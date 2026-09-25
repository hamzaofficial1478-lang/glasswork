"""
leads_osm.py — find businesses without a Google Places key.

    python -m pipelines.leads_osm --probe            can this machine reach OSM?
    python -m pipelines.leads_osm --find food_stall --area Dubai
    python -m pipelines.leads_osm                    one pass, as the day runs it

WHY THIS EXISTS, AND WHAT IT IS NOT

The owner asked what had been built to replace Google Maps. The honest answer
at the time was: nothing. pipelines/harvest.py replaced the CONTACT hop —
reading a business's own website for its address, phone and WhatsApp — and that
is a different job. Maps is what FINDS the business in the first place, and
`search_places` was the only discovery there was, so an empty maps shelf meant
the Leads room went dark and the readiness lamp was telling the truth.

This is the discovery half, on OpenStreetMap. No key, no account, no per-call
charge. OSM carries UAE businesses as tagged points: a name, a category, an
address, frequently a phone and a website. Feed the website into harvest.py and
you have the same lead Places would have produced, for nothing.

WHAT IT IS HONEST ABOUT

OSM is not as complete as Google in the UAE and pretending otherwise would set
the owner up to think the Hunter is broken when it is simply the data. A café
on a main road is usually there; a one-room tailor in a back street often is
not. So this does not REPLACE the Places path — both are collectors feeding the
same gate, Places is used when a key is present, and this runs either way. Two
partial lists deduplicated by the Scanner beat one partial list.

BEING A GOOD CITIZEN ON A FREE SERVICE

Overpass is donated infrastructure with a fair-use policy, and a scraper that
hammers it gets the whole project blocked, not just us. So: a real User-Agent
that says who we are, one modest bounded query at a time, a hard cap on results,
a pause between calls, and mirrors tried in order rather than one hit
repeatedly. If every mirror refuses, that is reported as a fact — never retried
in a tight loop.
"""
import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from coordination import board, deskwork, dispatch, uae
from . import find_site, harvest, leads_sources as ls, services

AGENT = "leads-hunter"
SECTOR = "Leads"

# Who we are. A free service is entitled to know who is calling it, and an
# anonymous scraper is the first thing an operator blocks.
UA = ("glasswork-leads/1.0 (UAE glass and aluminium; contact via "
      "worlddealtechnicalservices.com)")

# Tried in order. The main instance is busiest and refuses first under load;
# the mirrors exist precisely so nobody hammers one host.
MIRRORS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
)

TIMEOUT = 60
PAUSE = 2.0          # between calls, deliberately generous
MAX_RESULTS = 60     # per query; a bigger number is a heavier load on a gift

# How many business websites one pass will read looking for an email address.
# Each one is up to four page fetches through the metered fetcher, so this is
# the difference between a lead run and an afternoon. What it does not get to
# is reported as "left for the next pass", never silently dropped.
ENRICH_MAX = 12

# Where to look. Emirate-level boxes, because they are well known and stable —
# a district box would be a guess, and the district is read off the address the
# POI carries rather than inferred from where we searched.
BBOX = {
    "Dubai":            "24.79,54.89,25.36,55.57",
    "Abu Dhabi":        "24.25,54.28,24.60,54.75",
    "Sharjah":          "25.27,55.35,25.45,55.65",
    "Ajman":            "25.38,55.42,25.48,55.58",
    "Ras Al Khaimah":   "25.65,55.85,25.90,56.10",
    # UMM AL QUWAIN WAS MISSING, and it is an emirate he works in. Every list
    # in this program now reads coordination/uae.py, and that one has all seven
    # — so an emirate absent from here is an emirate the map search silently
    # skips while the rest of the agency believes it is covered.
    "Umm Al Quwain":    "25.50,55.53,25.62,55.75",
    "Al Ain":           "24.13,55.66,24.30,55.86",
    "Fujairah":         "25.08,56.28,25.20,56.40",
}
# THE PASS WORKS TO A CLOCK.
#
# A full day run handed this collector its task for the first time — once the
# map step stopped taking it — and it ran for over fifteen minutes and stored
# nothing. Every business on the map, then a website looked up for each one
# that had none (five domains tried at twelve seconds apiece, twenty-five
# businesses), then every site read — and the leads written only at the very
# END. The day kills this step at seven minutes. Killed, it stores nothing and
# leaves the task held.
#
# So the pass takes most of its step's ceiling, read from the runner rather
# than copied, stops looking and reading when that is spent, and always keeps
# enough back to store what it has. The same rule seo_intel follows, for the
# same reason: partial and honest beats complete and killed.
OF_THE_CEILING = 0.80
WRITE_RESERVE = 45       # screening, dedup and storing, with room for a bad day
OSM_BUDGET = 330         # only if the runner's ceiling cannot be read


def pass_budget() -> int:
    """Seconds this whole pass may take, from the day run's own step limit."""
    try:
        from . import day_run
        return max(90, int(day_run.step_timeout_for("pipelines.leads_osm")
                           * OF_THE_CEILING))
    except Exception:
        return OSM_BUDGET


DEFAULT_AREAS = ("Dubai", "Sharjah", "Abu Dhabi")

# Our segments, in OSM's vocabulary. Only tags that genuinely exist in the UAE
# extract — inventing a tag produces a query that always returns nothing, which
# looks exactly like "OSM has no data" and is much harder to spot.
OSM_TAGS = {
    "food_stall":    [("amenity", "cafe"), ("amenity", "fast_food"),
                      ("shop", "bakery"), ("shop", "butcher"),
                      ("shop", "confectionery")],
    "restaurant":    [("amenity", "restaurant")],
    "salon":         [("shop", "hairdresser"), ("shop", "beauty")],
    "spa":           [("leisure", "spa"), ("shop", "massage")],
    "gym":           [("leisure", "fitness_centre")],
    "supermarket":   [("shop", "supermarket"), ("shop", "convenience")],
    "laundry":       [("shop", "laundry"), ("shop", "dry_cleaning"),
                      ("shop", "tailor")],
    # NOT under laundry, where it used to be. A Sharjah laundry search came
    # back fourteen car workshops out of twenty-six — good leads, wrong
    # segment, and the mail would have offered a garage a mirror wall.
    "garage":        [("shop", "car_repair"), ("shop", "tyres"),
                      ("shop", "car_parts"), ("amenity", "car_wash")],
    "clinic_small":  [("amenity", "clinic"), ("amenity", "dentist"),
                      ("amenity", "doctors"), ("shop", "optician"),
                      ("amenity", "veterinary")],
    "nursery_local": [("amenity", "kindergarten"), ("amenity", "childcare")],
    "school":        [("amenity", "school"), ("amenity", "college")],
    "hospital":      [("amenity", "hospital")],
    # Hotel apartments are half the Dubai market and they are tagged
    # tourism=apartment, not tourism=hotel. Asking only for hotels missed the
    # segment that refurbishes most often.
    "hotel":         [("tourism", "hotel"), ("tourism", "guest_house"),
                      ("tourism", "hostel"), ("tourism", "motel"),
                      ("tourism", "resort")],
    # The nearest thing to "rental owners" that exists as a business on a map:
    # holiday-home and short-let operators. They turn units over constantly,
    # which means shower glass, mirrors and balcony railings on a short cycle.
    "holiday_let":   [("tourism", "apartment"), ("tourism", "chalet")],
    # Who actually holds the keys to other people's villas.
    "property_management": [("office", "property_management"),
                            ("office", "estate_agent"),
                            ("shop", "estate_agent")],
    "retail":        [("shop", "clothes"), ("shop", "furniture"),
                      ("shop", "mobile_phone"), ("shop", "jewelry")],
    "showroom":      [("shop", "car"), ("shop", "furniture")],
    "mall":          [("shop", "mall")],
    "local_market":  [("amenity", "marketplace")],
    "bank":          [("amenity", "bank"), ("amenity", "bureau_de_change")],
    "warehouse":     [("building", "warehouse"), ("industrial", "warehouse")],
    "office":        [("office", "company"), ("office", "estate_agent")],
    "coworking":     [("amenity", "coworking_space"), ("office", "coworking")],
    "real_estate_agency": [("office", "estate_agent"),
                           ("shop", "estate_agent"),
                           ("office", "property_management")],
    # The billboard and display trade he asked for. office=advertising_agency
    # is the one that finds the companies that put up hoardings — they
    # subcontract the aluminium framing and the acrylic, which is us.
    "signage":       [("craft", "signmaker"), ("shop", "copyshop"),
                      ("office", "advertising_agency"),
                      ("shop", "printing")],
    "builder":       [("craft", "builder"), ("craft", "carpenter"),
                      ("shop", "doityourself"), ("shop", "hardware")],
}


def segments_covered():
    """
    Which of the agency's segments this collector can actually find — in the
    agency's order of value, not the alphabet's.

    This was sorted(), so the collector worked BANKS first: "bank" sorts before
    "builder" and everything else. The first time a day run let it collect,
    every one of its fifteen leads was a bank — chains that buy centrally, with
    one phone number between them. ls.SEGMENTS is ordered on purpose, fit-out
    contractors and interior designers at the top, and the Places hunter
    already follows it; now both collectors do.
    """
    return [k for k in ls.SEGMENTS if k in OSM_TAGS]


# ---------------------------------------------------------------------------
# CHAINS ARE NOT LOCALS
# ---------------------------------------------------------------------------
#
# The first real run in Dubai came back with Krispy Kreme, McDonald's, Burger
# King, Subway, Costa Coffee, KFC, Starbucks, Five Guys and Papa John's. Every
# one is a franchise with procurement in another country, and the owner had
# already said, about Google: "most of the data is related to agencies,
# businesses and majors, while what I want is the locals."
#
# Swapping Google for OSM and keeping the chains would have reproduced his
# complaint with a different data source. A shopfront job is won by walking
# into a cafeteria and talking to the man who owns it; nobody wins one by
# emailing a global brand's info@ address.
#
# OSM makes this checkable rather than guessable. Mappers tag a recognised
# brand with `brand:wikidata` — a curated identifier for a specific company —
# and the same for `operator:wikidata`. A one-room cafeteria in Al Quoz has
# neither. That is evidence in the data, not a list of names somebody has to
# maintain and will forget to update.

# ---------------------------------------------------------------------------
#
# THE SEGMENTS A MAP CANNOT HOLD, AND WHAT TO DO INSTEAD
#
# The owner asked to test villa owners, house owners and rental owners. There
# is no honest way to do that here and pretending otherwise would waste his
# week. OpenStreetMap maps *places*: a villa is a building outline with no
# owner, no name and no telephone number, because the person who lives in it
# never published one. The only way to get a householder's number is to buy a
# list somebody else compiled without asking them — which is how you end up
# blacklisted by every mail provider, and in the UAE it is also illegal.
#
# But every villa has people who already have the owner's number and are paid
# to look after the building. Those ARE on the map, they ARE businesses, and
# they order glass work by the dozen rather than one door at a time. So the
# answer to "find me villa owners" is not "no" — it is "here is the door".
NOT_ON_A_MAP = {
    "villa_compound": (
        "A compound is a residential area, so OSM holds its outline and not "
        "one contact for anybody in it.",
        "property_management and facility_management — a compound is run by "
        "one office that decides for every villa on the plot, which is a "
        "better job than one villa anyway."),
    "landlord": (
        "Landlords are private people. OSM has no owner field and no "
        "telephone number for a home, and the lists that claim to were "
        "compiled without asking anybody.",
        "real_estate_agency and property_management — they hold the keys, "
        "they see every unit before it is let, and they are the ones who "
        "authorise the work."),
    "private_individual": (
        "A home is not a business and has no published contact route.",
        "builder and real_estate_agency for the work, and the website and "
        "Google reviews for the householders who come looking for you."),
    "facility_management": (
        "OSM has no tag for facilities management, so there is nothing to "
        "ask it for.",
        "property_management, which OSM does hold, and office — the FM "
        "companies are tagged office=company when they are mapped at all."),
    "startup": (
        "A young company looks exactly like an old one on a map.",
        "coworking and office — that is where they sit, and the coworking "
        "operator re-partitions for every one that moves in."),
}

CHAIN_TAGS = ("brand:wikidata", "operator:wikidata", "brand:wikipedia")

# How many times one name has to appear in a single result set before it is a
# chain whatever its tags say. Two branches is a local success story; four is
# a franchise.
CHAIN_REPEATS = 3

# The wikidata check misses the regional groups. A Dubai clinic search came
# back with Medcare, Magrabi and Prime Medical — multi-branch outfits with a
# head office and a procurement department, which is exactly what the owner
# said he did not want, but no curated brand identifier and no two branches
# spelled the same ("Medcare Hospital Al Safa", "Medcare Medical Centre
# Jumeirah"), so neither rule above caught them.
#
# What DOES catch them is the mapper's own words: both pins carry
# operator=Medcare. Two pins the mapper says are run by one company is that
# company saying it has branches. Lower than CHAIN_REPEATS on purpose — a
# repeated NAME might be two shops that happen to share one, but a repeated
# operator tag is a statement of fact about who owns them.
GROUP_REPEATS = 2


def chain_verdict(tags) -> tuple:
    """(is_a_chain, why). Read off the tags, never off a list of brand names."""
    for t in CHAIN_TAGS:
        if (tags.get(t) or "").strip():
            brand = (tags.get("brand") or tags.get("operator") or "").strip()
            return True, (f"a recognised brand ({brand})" if brand
                          else f"tagged {t}")
    return False, ""


def group_of(tags) -> str:
    """
    The parent company the mapper named, if they named one. "" otherwise.

    Ignored when it is just the shop's own name written twice, which is how
    plenty of independents are tagged and is not evidence of anything.
    """
    name = _first(tags, "name:en", "name").strip().lower()
    for k in ("operator", "brand", "operator:en", "brand:en"):
        v = (tags.get(k) or "").strip()
        if v and v.lower() != name:
            return v
    return ""


def drop_chains(cands, elements=None, repeats=CHAIN_REPEATS,
                group_repeats=GROUP_REPEATS):
    """
    (locals, dropped). `dropped` carries the reason for each, because a
    collector that silently halves its own output is indistinguishable from a
    broken one.
    """
    counts, groups = {}, {}
    for c in cands:
        counts[c["company"].strip().lower()] = \
            counts.get(c["company"].strip().lower(), 0) + 1
        g = (c.get("_group") or "").strip().lower()
        if g:
            groups[g] = groups.get(g, 0) + 1
    keep, dropped = [], []
    for c in cands:
        why = c.pop("_chain_why", "")
        g = (c.pop("_group", "") or "").strip()
        n = counts.get(c["company"].strip().lower(), 0)
        if not why and n >= repeats:
            why = f"{n} branches in one search"
        if not why and g and groups.get(g.lower(), 0) >= group_repeats:
            why = (f"{groups[g.lower()]} branches of {g} in one search — "
                   "a group with a head office, not a local")
        if why:
            dropped.append({"company": c["company"], "why": why})
        else:
            keep.append(c)
    return keep, dropped


# ---------------------------------------------------------------------------
# THE QUERY
# ---------------------------------------------------------------------------

# Ask Overpass for POIs that carry ANY key matching this, with any value.
# The point of the whole thing: a signage search in Dubai returned 60 shops of
# which 7 had a way in. The other 53 were not a failure of the search, they
# were the search spending its whole budget on businesses nobody mapped a
# telephone number for. Overpass can filter on the presence of a key, so we
# can ask for the 7 directly and use the budget on more of them.
HAS_CONTACT = "^(contact:)?(website|phone|mobile|email)$"


def build_query(tags, bbox, limit=MAX_RESULTS, timeout=25,
                contactable_only=False) -> str:
    """
    Overpass QL for a set of tags inside one box.

    `nwr` rather than `node`: a supermarket is usually mapped as a building
    (a way), not a pin, and asking only for nodes quietly misses most of the
    bigger businesses — which are the ones worth having.

    `contactable_only` adds a has-any-of-these-keys filter, which turns the
    result cap from a limiter into a sieve: the same 60 slots, filled with
    businesses that can actually be contacted instead of an arbitrary slice.
    """
    filt = f'[~"{HAS_CONTACT}"~"."]' if contactable_only else ""
    parts = "".join(f'nwr["{k}"="{v}"]{filt}({bbox});' for k, v in tags)
    return (f"[out:json][timeout:{int(timeout)}];({parts});"
            f"out center {int(limit)};")


def _post(url, query, timeout=TIMEOUT):
    req = urllib.request.Request(
        url, data=urllib.parse.urlencode({"data": query}).encode(),
        headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


# Not worth starting a mirror with less than this left on the pass's clock.
LEAST_WAIT = 10


def ask(query, timeout=TIMEOUT, deadline=None) -> dict:
    """
    Run one query against the first mirror that answers.

    Returns {ok, elements, mirror, tried}. Every mirror that refused is named,
    because "OSM did not answer" and "OSM has nothing there" are completely
    different facts and the difference decides whether the owner needs to do
    anything about it.

    `deadline` bounds the whole thing, not each mirror. Four mirrors at sixty
    seconds is four minutes for one search: a real hunt on a bad day spent 328
    seconds on two searches, and a third would have run past the day's kill.
    """
    tried = []
    for url in MIRRORS:
        host = urllib.parse.urlparse(url).netloc
        wait = timeout
        if deadline:
            wait = min(timeout, deadline - time.time())
            if wait < LEAST_WAIT:
                return {"ok": False, "elements": [], "mirror": "", "tried": tried,
                        "out_of_time": True,
                        "error": "the pass's clock ran out before a mirror answered"
                                 + (" — " + "; ".join(tried) if tried else "")}
        try:
            d = _post(url, query, wait)
            return {"ok": True, "elements": d.get("elements") or [],
                    "mirror": host, "tried": tried}
        except urllib.error.HTTPError as e:
            tried.append(f"{host}: HTTP {e.code}")
        except Exception as e:
            tried.append(f"{host}: {type(e).__name__}")
        time.sleep(PAUSE)
    return {"ok": False, "elements": [], "mirror": "",
            "tried": tried,
            "error": "no Overpass mirror answered — " + "; ".join(tried)}


# ---------------------------------------------------------------------------
# ONE POI -> ONE CANDIDATE LEAD
# ---------------------------------------------------------------------------

def _first(tags, *keys):
    for k in keys:
        v = (tags.get(k) or "").strip()
        if v:
            return v
    return ""


def _address(tags):
    bits = [_first(tags, "addr:housenumber"),
            _first(tags, "addr:street:en", "addr:street"),
            _first(tags, "addr:district:en", "addr:district",
                   "addr:suburb:en", "addr:suburb",
                   "addr:neighbourhood:en", "addr:neighbourhood"),
            _english_area(_first(tags, "addr:city:en", "addr:city"))]
    return ", ".join(b for b in bits if b)


# Half the UAE is mapped in Arabic. A row filed as "دبي" and a row filed as
# "Dubai" are the same city, but the cabinet groups by string, so they land in
# two piles and the emirate counts come out wrong. Only the emirates and the
# few big cities are translated here — those are the names we sort on. A
# district we do not know is left exactly as the mapper wrote it, because a
# half-guessed transliteration is worse than honest Arabic.
_ARABIC_AREAS = {
    "دبي": "Dubai",
    "الشارقة": "Sharjah",
    "ابو ظبي": "Abu Dhabi",
    "ابوظبي": "Abu Dhabi",
    "عجمان": "Ajman",
    "راس الخيمة": "Ras Al Khaimah",
    "ام القيوين": "Umm Al Quwain",
    "الفجيرة": "Fujairah",
    "العين": "Al Ain",
    "خورفكان": "Khor Fakkan",
    "دبا الحصن": "Dibba Al Hisn",
    "الامارات العربية المتحدة": "United Arab Emirates",
}

# Arabic writes the same letter several ways. Normalise before looking up, or
# "أبو ظبي" and "ابو ظبي" miss each other by one hamza.
_ARABIC_FOLD = str.maketrans({
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    "ى": "ي", "ئ": "ي", "ة": "ه", "ؤ": "و",
    "ـ": "",  # tatweel, the decorative stretch character
})
_HARAKAT = re.compile(r"[ً-ْٰ]")  # vowel marks


def _fold(s: str) -> str:
    return " ".join(_HARAKAT.sub("", s).translate(_ARABIC_FOLD).split())


# The table above is written the way a mapper would write it; the lookup is
# folded once, here, so both sides of the comparison went through the same mill.
#
# The English spellings go in the same table, because mappers type them however
# they feel: a real Dubai run filed "sharjah" in lower case, which then failed
# to match the emirate list and was counted as a district. Same city, same
# lesson as the Arabic — compare on a folded key, print the proper spelling.
_ARABIC_LOOKUP = {_fold(k): v for k, v in _ARABIC_AREAS.items()}
_ARABIC_LOOKUP.update({v.lower(): v for v in _ARABIC_AREAS.values()})
_ARABIC_LOOKUP.update({k.lower(): k for k in BBOX})
_ARABIC_LOOKUP.update({
    "uae": "United Arab Emirates",
    "u.a.e.": "United Arab Emirates",
    "abudhabi": "Abu Dhabi",
    "abu dabi": "Abu Dhabi",
    "shajah": "Sharjah",           # a real and common misspelling on OSM
    "rak": "Ras Al Khaimah",
    "umm al quwain": "Umm Al Quwain",
    "umm al qaiwain": "Umm Al Quwain",
})


def _english_area(name: str) -> str:
    """
    'دبي' -> 'Dubai'. 'sharjah' -> 'Sharjah'. Anything we do not know comes
    back exactly as the mapper wrote it.
    """
    if not name:
        return ""
    folded = _fold(name)
    return _ARABIC_LOOKUP.get(folded, _ARABIC_LOOKUP.get(folded.lower(),
                                                         name.strip()))


def _area_of(tags, fallback=""):
    return _english_area(
        _first(tags, "addr:district:en", "addr:district",
               "addr:suburb:en", "addr:suburb",
               "addr:neighbourhood:en", "addr:neighbourhood",
               "addr:city:en", "addr:city")) or fallback


def _emirate_of(tags) -> str:
    """
    Which emirate the mapper says this is in, or "" if they never said.

    Separate from _area_of on purpose. _area_of answers "where in the city",
    and prefers the district, so most rows come back "Al Quoz" or "Al Nahda"
    and never name an emirate at all. This answers the other question, and it
    is the one that catches the box overshooting its border.
    """
    city = _english_area(_first(tags, "addr:city:en", "addr:city"))
    return city if city in BBOX else ""


def to_candidate(el, segment, area="") -> dict:
    """
    An OSM element as the same shape leads_sources.intake() already takes.

    Returns {} for anything with no name — an unnamed pin is a dot on a map,
    not a business anybody can write to, and filing it would put a row in the
    cabinet that can never become a lead.
    """
    tags = (el or {}).get("tags") or {}
    name = _first(tags, "name:en", "name", "operator", "brand")
    if not name:
        return {}
    # One number, one shape. OSM carries '+971 4 385 1122', '04 555 9000' and
    # '00971...' for the same line depending on who mapped it, and a cabinet
    # holding all three rings the same business three times.
    raw_phone = _first(tags, "contact:phone", "phone", "contact:mobile")
    phone = harvest.normalise_phone(raw_phone) or raw_phone.strip()
    site = _first(tags, "contact:website", "website", "url", "contact:facebook")
    if site and "://" not in site:
        site = "https://" + site.lstrip("/")
    svc, why = services.match_service(name, _first(tags, "cuisine", "shop", "amenity"),
                                      segment=segment)
    is_chain, chain_why = chain_verdict(tags)
    return {
        # Carried, not acted on here — drop_chains() decides, so a caller that
        # genuinely wants the chains can have them.
        "_chain_why": chain_why if is_chain else "",
        "_group": group_of(tags),
        # Same idea: find() counts these, nothing downstream reads them.
        "_emirate": _emirate_of(tags),
        "company": name[:120],
        "phone": phone[:40],
        "website": site[:200],
        # OSM DOES carry contact:email, and we were throwing it away. This line
        # read `"email": ""` with a comment saying harvest would get it — so
        # every business that had published an address right there in the map
        # data was filed unmailable and then had its website fetched to look
        # for the address we already had.
        "email": _first(tags, "contact:email", "email")[:120].strip().lower(),
        "area": _area_of(tags, area)[:80],
        "address": _address(tags)[:200],
        "segment": segment,
        "category": _first(tags, "shop", "amenity", "office", "craft", "leisure"),
        "service": svc,
        "service_label": services.label(svc),
        "service_reason": why,
        "source": f"openstreetmap:{el.get('type', '?')}/{el.get('id', '?')}",
    }


def find(segment, area="Dubai", limit=MAX_RESULTS, chains=False,
         contactable_only=False, deadline=None) -> dict:
    """
    Every named LOCAL business of one segment in one emirate. Reads only.

    Two things are filtered and both are counted out loud, because the gap
    between "57 found" and what is actually worth having is the whole story:

      chains        franchises with procurement abroad. See chain_verdict.
      no way in     OSM knows the shop exists but carries no phone, no
                    website and no address. There is nothing to do with that
                    but count it — screen_lead would refuse it downstream
                    anyway, and filing it would pad the lead list with rows
                    nobody can ever contact.

    `contactable_only` asks Overpass for the ones with a phone, site or email
    tag and nothing else. Same 60 slots, spent on businesses we can write to.
    """
    tags = OSM_TAGS.get(segment)
    if not tags:
        if segment in NOT_ON_A_MAP:
            why, instead = NOT_ON_A_MAP[segment]
            return {"ok": False, "unmappable": True, "reach_instead": instead,
                    "error": (f"'{segment}' is not something a map holds. {why} "
                              f"The way to reach them is: {instead}")}
        return {"ok": False, "error": f"no OSM tags mapped for '{segment}'. "
                                      f"Mapped: {', '.join(segments_covered())}"}
    bbox = BBOX.get(area)
    if not bbox:
        return {"ok": False, "error": f"no box for '{area}'. "
                                      f"Known: {', '.join(BBOX)}"}
    q = build_query(tags, bbox, limit, contactable_only=contactable_only)
    r = ask(q, deadline=deadline) if deadline else ask(q)
    if not r["ok"]:
        return {"ok": False, "error": r["error"], "tried": r["tried"],
                "out_of_time": bool(r.get("out_of_time"))}
    # Exactly `limit` back means Overpass stopped counting, not that Dubai has
    # exactly that many. Both his searches returned exactly 60 and neither said
    # so, which reads as "that is all there is" when it is "that is all we
    # asked for" — a completely different fact, and the one that decides
    # whether it is worth searching again.
    truncated = len(r["elements"]) >= int(limit)
    cands, unnamed = [], 0
    for el in r["elements"]:
        c = to_candidate(el, segment, area)
        if c:
            cands.append(c)
        else:
            unnamed += 1

    named = len(cands)
    if chains:
        for c in cands:
            c.pop("_chain_why", None)
            c.pop("_group", None)
        dropped = []
    else:
        cands, dropped = drop_chains(cands)

    # A business with no phone, no site and no address cannot be reached by
    # anyone, today or later. Counted, not filed.
    reachable = [c for c in cands
                 if c["phone"] or c["website"] or c["address"]]
    no_way_in = len(cands) - len(reachable)

    # A bounding box is a rectangle and an emirate border is not, so a Dubai
    # search picks up the near edge of Sharjah. These are still real UAE leads
    # and dropping them would throw away good work, but they are counted and
    # said out loud so "in Dubai" does not quietly mean "and a bit of Sharjah".
    spill = sorted({c["_emirate"] for c in reachable
                    if c.get("_emirate") and c["_emirate"] != area})
    over_the_border = sum(1 for c in reachable
                          if c.get("_emirate") and c["_emirate"] != area)
    for c in reachable:
        c.pop("_emirate", None)

    head = (f"{named} on the map, {len(dropped)} chain(s) dropped, "
            f"{no_way_in} with no way to reach them, "
            f"{len(reachable)} worth having")
    if over_the_border:
        head += (f" — {over_the_border} of them actually in "
                 + ", ".join(spill))
    if truncated:
        head += (f" (capped at {int(limit)} — there are more, "
                 + ("raise --limit" if contactable_only else
                    "try --contactable to spend the cap on the ones with a "
                    "phone or a site") + ")")
    return {"ok": True, "segment": segment, "area": area, "mirror": r["mirror"],
            "on_the_map": named, "unnamed_skipped": unnamed,
            "chains_dropped": dropped, "no_contact_route": no_way_in,
            "found": len(reachable), "truncated": truncated,
            "contactable_only": contactable_only, "limit": int(limit),
            "with_phone": sum(1 for c in reachable if c["phone"]),
            "with_website": sum(1 for c in reachable if c["website"]),
            "with_email_from_the_map": sum(1 for c in reachable if c["email"]),
            "over_the_border": over_the_border, "spilled_into": spill,
            "candidates": reachable,
            "reach": reach_of(reachable),
            "head": head}


# ---------------------------------------------------------------------------
#
# FROM A PIN ON A MAP TO AN ADDRESS YOU CAN WRITE TO
#
# The owner read a clinic run and asked the question that matters: "as we are
# getting the leads are we also getting the mails?" The answer was no, and the
# reason was a comment in this file promising something no code did. A
# candidate was filed with email="" and the note "harvest gets it" — and
# nothing ever called harvest. Every OSM lead in the cabinet was unmailable and
# the run said nothing about it.
#
# OSM does not hold email addresses. It very occasionally has contact:email,
# and almost always has nothing. What it does have, for some of them, is the
# business's own website — and a business publishes its email on its own site,
# because it wants to be written to. pipelines/harvest.py already reads a site
# properly. This is the missing wire between the two.
#
# And the count has to be honest, because it decides what he does with his
# morning. Of twenty clinics in his last Dubai run, four had a website. Four is
# what the mail runner can work with; the rest are a phone to message by hand
# or a door to walk through, and calling all twenty "leads" would have him
# waiting on emails that were never going to exist.
# ---------------------------------------------------------------------------

def reach_of(cands) -> dict:
    """
    How each of these can ACTUALLY be reached, counted honestly.

    Four buckets, in descending order of how little work they cost him:

      mailable    an email address. The mail runner can write to these today.
      harvestable a website but no email yet — enrich() reads the site.
      message     a phone and nothing else. He said he would do WhatsApp by
                  hand, so these are his list, not the agency's.
      walk_in     an address only. Which for this trade is a real lead: you
                  drive to Al Quoz and knock.
    """
    out = {"mailable": 0, "harvestable": 0, "message": 0, "walk_in": 0}
    for c in cands:
        if (c.get("email") or "").strip():
            out["mailable"] += 1
        elif (c.get("website") or "").strip():
            out["harvestable"] += 1
        elif (c.get("phone") or "").strip():
            out["message"] += 1
        elif (c.get("address") or "").strip():
            out["walk_in"] += 1
    return out


def reach_line(reach) -> str:
    return (f"{reach['mailable']} mailable now, "
            f"{reach['harvestable']} with a site to read, "
            f"{reach['message']} phone-only (WhatsApp by hand), "
            f"{reach['walk_in']} walk-in only")


def enrich(cands, limit=ENRICH_MAX, timeout=20, allow_paid=True,
           deadline=None) -> dict:
    """
    Read the websites we found and take the email the business published.

    Only candidates that HAVE a website and do NOT already have an email — a
    site read is somebody else's bandwidth and possibly our money, and a lead
    that is already mailable has nothing to gain from it.

    `limit` is a hard stop on how many sites one pass will read, because a
    segment with sixty websites would otherwise turn a lead run into an
    afternoon. Everything is reported: read, found, failed.
    """
    todo = [c for c in cands
            if (c.get("website") or "").strip()
            and not (c.get("email") or "").strip()]
    attempting = todo[:max(0, int(limit))]
    read, gained, failed, spend, tried = 0, 0, [], 0.0, 0
    for c in attempting:
        # The pass's clock — see OSM_BUDGET. A site not read today is still
        # worth reading tomorrow; a pass killed mid-read stores nothing at all.
        if deadline and time.time() >= deadline:
            break
        tried += 1
        try:
            h = harvest.harvest(c["website"], timeout=timeout,
                                allow_paid=allow_paid)
        except Exception as e:                       # a bad site is not a crash
            failed.append({"company": c.get("company"), "why": f"{type(e).__name__}: {e}"})
            continue
        read += 1
        spend += h.get("spend", 0.0) or 0.0
        if not h.get("ok"):
            failed.append({"company": c.get("company"),
                           "why": h.get("error") or "the site did not read"})
            continue
        if h.get("emails"):
            c["email"] = h["emails"][0]["value"]
            c["email_where"] = h["emails"][0].get("where", "")
            gained += 1
        else:
            failed.append({"company": c.get("company"),
                           "why": "site read, no email published on it"})
        # Everything else the site gave us that the map did not. The map's
        # phone stands: it is the one the business registered.
        if h.get("phones") and not (c.get("phone") or "").strip():
            c["phone"] = h["phones"][0]["value"]
        if h.get("whatsapp") and not (c.get("whatsapp") or "").strip():
            c["whatsapp"] = h["whatsapp"][0]["value"]
        if h.get("address") and not (c.get("address") or "").strip():
            c["address"] = h["address"]
        if h.get("socials") and not (c.get("socials") or "").strip():
            c["socials"] = ", ".join(s["value"] for s in h["socials"][:4])
    # What the cap kept us from trying — NOT the ones we tried and lost. A site
    # that was read and had no email is a closed question; one we never got to
    # is still worth a pass tomorrow, and they must not be counted together.
    skipped = len(todo) - tried
    return {"sites_read": read, "emails_found": gained,
            "no_email_on_site": failed, "not_reached_this_pass": skipped,
            "spend": round(spend, 4),
            "head": (f"read {read} site(s), got {gained} email address(es)"
                     + (f", {skipped} left for the next pass" if skipped else ""))}


# ---------------------------------------------------------------------------
#
# THE HUNT — ROUNDS UNTIL THE NUMBER HE ASKED FOR
#
# The lead-generation repo the owner pointed at (kashif-developer9053/
# our-company) is, underneath the dashboard, one idea done properly:
# backend/agent1/lead_harvester.py searches in ROUNDS until a target number of
# qualified leads is in hand, and when it falls short it knows why. This pass
# used to do one sweep — a fixed list of segments times three emirates — and
# stop wherever that left it, whether that was four leads or sixty.
#
# What comes across, in its terms and ours:
#
#   Work to the number     it stops the moment the target is met. So does this:
#                          the job's count, or 20 when the job gives none.
#   A new search a round   there, a new phrasing of the same query. OSM answers
#                          the same question with the same sixty, so here a
#                          round is the next (segment, emirate) — most valuable
#                          segment first, across the emirates, then the next.
#   Skip what we hold      it drops keys already seen before enriching. Here the
#                          cabinet is asked before a website is looked up or
#                          read, so a business already on file costs nothing.
#   Barren rounds end it   N searches in a row with nothing new means the
#                          ground is worked out — "and be far more stubborn
#                          while the haul is still thin".
#   Short? Widen           it re-searches the surrounding area. Here, the other
#                          emirates — but only when the job did not name a
#                          place. If he said Dubai, it stays in Dubai and says
#                          which neighbours to name to go further.
#   Say what ran out       the clock, the map, the free service, or the list of
#                          searches — so he knows what to change.
#
# Two things are ours. Every round is STORED when it finishes: the repo gathers
# everything and returns it at the end, and a pass here that did that was
# killed at the step's ceiling with nothing stored. And a round may only fill
# a share of the target when the pass chose the segments itself, so twenty
# leads are not twenty of one kind — what a round had over its share is kept
# and used before the pass gives up or goes further afield.
#
# NOT TAKEN: its social search (scraping search engines for Instagram and
# Facebook pages — platform terms, and a poor fit for businesses that buy
# glass), and its site-audit gate, which qualifies prospects for a WEB agency.
# Ours is qualified by having a way in; ls.intake already refuses the rest.
# ---------------------------------------------------------------------------

TARGET_DEFAULT = 20       # a job with no number: the repo's "not really answered" floor
PER_ROUND_SHARE = 3       # a round may fill a third of the target...
PER_ROUND_FLOOR = 5       # ...and never fewer than five
BARREN_LIMIT = 4          # searches in a row with nothing new, once the haul is decent
BARREN_LIMIT_THIN = 8     # and while it is under half the target
REFUSALS_LIMIT = 2        # rounds in a row where every mirror refused: stop, never hammer
WIDEN_BEFORE = 0.75       # only go further afield with a quarter of the clock left

# The other emirates, nearest first, for a pass that was not told where.
WIDER_AREAS = ("Ajman", "Umm Al Quwain", "Al Ain", "Ras Al Khaimah", "Fujairah")

# Who to name when a named emirate runs out. Said, not done: he picked the place.
NEIGHBOURS = {
    "Dubai": ("Sharjah", "Ajman"),
    "Sharjah": ("Dubai", "Ajman", "Umm Al Quwain"),
    "Abu Dhabi": ("Al Ain", "Dubai"),
    "Ajman": ("Sharjah", "Umm Al Quwain"),
    "Umm Al Quwain": ("Ajman", "Ras Al Khaimah"),
    "Ras Al Khaimah": ("Umm Al Quwain", "Fujairah"),
    "Al Ain": ("Abu Dhabi",),
    "Fujairah": ("Ras Al Khaimah", "Sharjah"),
}


def per_round_cap(target) -> int:
    return max(PER_ROUND_FLOOR, -(-int(target) // PER_ROUND_SHARE))


def _pool_key(c) -> str:
    """One business, however many segments found it: the OSM element id."""
    return c.get("source") or "|".join(str(c.get(k) or "").lower()
                                       for k in ("company", "phone", "website"))


def _reach_rank(c) -> int:
    """The easiest to reach first, when a round has more than its share."""
    for i, k in enumerate(("email", "website", "phone")):
        if (c.get(k) or "").strip():
            return i
    return 3


def _tally(total: dict, part: dict) -> dict:
    """Add one round's counts from fill_in / enrich into the pass's totals."""
    for k, v in (part or {}).items():
        if k == "head" or isinstance(v, bool):
            continue
        if isinstance(v, (int, float)):
            total[k] = round(total.get(k, 0) + v, 4)
        elif isinstance(v, list):
            total.setdefault(k, []).extend(v)
    return total


def why_it_stopped(why, stored, target, *, area=None, rounds_run=0,
                   untried=0, examined=0, already=0, budget=0,
                   dry_run=False) -> str:
    """One plain sentence on how far the pass got and which constraint ran out."""
    got = (f"Found {stored} new of the {target} asked for (dry run, nothing stored)."
           if dry_run else f"Stored {stored} of the {target} asked for.")
    if why == "target":
        return got
    further = (f" {area} looks worked out on the map — name "
               + " or ".join(NEIGHBOURS.get(area, ())) + " in the order to go further."
               if area and NEIGHBOURS.get(area) else
               " OpenStreetMap is thinner than Google in the UAE; a Places key is "
               "what finds more.")
    if why == "barren":
        return (got + f" I ran out of new businesses: search after search turned up "
                f"only ones we already hold ({already} already on file this pass)."
                + further)
    if why == "clock":
        return (got + f" The pass used its {max(1, budget // 60)} minutes of the "
                f"day's step with {untried} search(es) still untried. What it found "
                f"is stored, and the next pass carries on.")
    if why == "refused":
        return (got + f" OpenStreetMap refused {REFUSALS_LIMIT} searches in a row, "
                "so I stopped rather than hammer a free service. "
                "`python -m pipelines.leads_osm --probe` says whether it is this "
                "machine or them.")
    return (got + f" I tried every search I have for this ({rounds_run}) and the map "
            f"holds only {examined} reachable business(es) there we did not already "
            f"have." + further)


# ---------------------------------------------------------------------------
# THE PASS
# ---------------------------------------------------------------------------

def run(agent=AGENT, segment=None, area=None, per_segment=1, dry_run=False,
        chains=False, read_sites=True, contactable_only=True,
        guess_sites=True, target=None):
    with board.agent_run(agent) as row:
        out = _run(agent, segment, area, per_segment, dry_run, chains,
                   read_sites, contactable_only, guess_sites, target)
        row.note(out.get("head") or out.get("error") or "nothing collected")
        return out


def _run(agent, segment, area, per_segment, dry_run, chains=False,
         read_sites=True, contactable_only=False, guess_sites=True, target=None):
    """
    `per_segment` is kept for the command line's sake and no longer limits
    anything: the target decides how far the pass goes, not a segment count.
    """
    if not board.sector_enabled(SECTOR):
        return {"ok": False, "error": "Leads is switched off."}
    task = board.next_open_task(SECTOR, agent)
    if not task:
        return dispatch.nothing_here(SECTOR, agent)

    # READ THE JOB. This claimed the task and then searched its own default
    # three areas regardless — "Find 20 leads in Al Quoz" was claimed, and the
    # collector went and searched Dubai, Sharjah and Abu Dhabi. The Places
    # hunter learned this already (see leads_hunt.run); this is the same repair
    # one collector over, so both doors into the Leads sector obey the ask.
    asked = deskwork.Job(task, SECTOR, agent)
    if area is None:
        # A district first, because a map search for a district returns the
        # street and one for an emirate returns the same well-known forty.
        area = asked.first_mention(list(BBOX), None) or \
            asked.first_mention(uae.search_areas(), None)
        if area and area not in BBOX:
            # He named somewhere real that has no bounding box of its own —
            # Al Quoz, Mussafah. Search the emirate it sits in rather than
            # dropping the instruction on the floor.
            area = uae.emirate_of(area) or None
    if segment is None:
        named = asked.first_mention(list(segments_covered()), None)
        if named:
            segment = named
    target = max(1, int(target or asked.how_many(None) or TARGET_DEFAULT))

    # The rounds. Most valuable segment first, across the emirates, then the
    # next — the agency's order, never the alphabet's (see segments_covered).
    segs = [segment] if segment else segments_covered()
    if area:
        core, wider = [(s, area) for s in segs], []      # he named the place
    else:
        core = [(s, a) for s in segs for a in DEFAULT_AREAS]
        wider = [(s, a) for s in segs for a in WIDER_AREAS]
    # A share per round only when the pass chose the segments. "Twenty signage
    # firms in Dubai" is exactly one search and must not be rationed.
    cap = per_round_cap(target) if not (segment and area) else 0

    budget = pass_budget()
    started = time.time()
    stop_looking = started + budget - WRITE_RESERVE
    widen_by = started + (budget - WRITE_RESERVE) * WIDEN_BEFORE

    seen, collected, spare, errors, widened_to = set(), [], [], [], []
    got = {"stored": [], "duplicates": [], "refused": [], "personal_data_held": []}
    guessed = {"looked_for": 0, "sites_found": 0, "too_generic_to_guess": [],
               "no_site_found": [], "not_reached_this_pass": 0}
    enriched = {"sites_read": 0, "emails_found": 0, "no_email_on_site": [],
                "not_reached_this_pass": 0, "spend": 0.0}
    n = {"rounds": 0, "examined": 0, "already": 0, "stored": 0}

    def search(seg, a):
        """One round's new businesses — or "refused" / "clock" if none came back."""
        r = find(seg, a, chains=chains, contactable_only=contactable_only,
                 deadline=stop_looking)
        if not r.get("ok"):
            errors.append(f"{seg}/{a}: {r.get('error')}")
            return "clock" if r.get("out_of_time") else "refused"
        time.sleep(PAUSE)
        fresh = []
        for c in r["candidates"]:
            k = _pool_key(c)
            if k in seen:
                continue                     # met this pass already
            seen.add(k)
            # Asked BEFORE a website is looked up or read: a business already
            # in the cabinet should cost nothing at all, not a site read that
            # intake then throws away as a duplicate.
            if board.lead_seen(c.get("email"), c.get("phone"), c.get("company"),
                               c.get("website")):
                n["already"] += 1
                continue
            fresh.append(c)
        n["examined"] += len(fresh)
        return fresh

    def file(batch):
        """Look up missing sites, read them, and STORE — this round, not at the end."""
        if not batch:
            return
        collected.extend(batch)
        if dry_run:
            n["stored"] += len(batch)
            return
        if guess_sites:
            _tally(guessed, find_site.fill_in(batch, deadline=stop_looking))
        if read_sites:
            _tally(enriched, enrich(batch, deadline=stop_looking))
        res = ls.intake(batch, agent=agent, sector=SECTOR)
        for k in got:
            got[k].extend(res.get(k) or [])
        n["stored"] = len(got["stored"])

    def use_spares():
        """What earlier rounds had over their share, before giving up or going on."""
        room = target - n["stored"]
        if room > 0 and spare and time.time() < stop_looking:
            batch = spare[:room]
            del spare[:room]
            file(batch)

    def hunt(rounds, widening=False):
        barren = refusals = 0
        for seg, a in rounds:
            if n["stored"] >= target:
                return "target"
            if time.time() >= stop_looking:
                return "clock"
            n["rounds"] += 1
            fresh = search(seg, a)
            if fresh == "clock":
                return "clock"
            if fresh == "refused":
                refusals += 1
                if refusals >= REFUSALS_LIMIT:
                    return "refused"
                continue
            refusals = 0
            if not fresh:
                barren += 1
                thin = n["stored"] < target / 2
                if barren >= (BARREN_LIMIT_THIN if thin else BARREN_LIMIT):
                    return "barren"
                continue
            barren = 0
            if widening and a not in widened_to:
                widened_to.append(a)
            fresh.sort(key=_reach_rank)
            room = target - n["stored"]
            take = fresh[:min(cap, room)] if cap else fresh[:room]
            spare.extend(fresh[len(take):])
            file(take)
        return "target" if n["stored"] >= target else "rounds"

    why = hunt(core)
    if why in ("barren", "rounds"):
        use_spares()
        if n["stored"] >= target:
            why = "target"
        elif wider and time.time() < widen_by:
            why = hunt(wider, widening=True)
            if why in ("barren", "rounds"):
                use_spares()
                if n["stored"] >= target:
                    why = "target"
    # The wider emirates only count once the pass actually went there — a real
    # run cut off in its second search said "206 still untried", which was
    # true of every search it might conceivably make and useless to him.
    planned = len(core) + (len(wider) if widened_to else 0)
    stop_line = why_it_stopped(why, n["stored"], target, area=area,
                               rounds_run=n["rounds"],
                               untried=max(0, planned - n["rounds"]),
                               examined=n["examined"], already=n["already"],
                               budget=budget, dry_run=dry_run)
    if widened_to:
        stop_line += (f" {', '.join(DEFAULT_AREAS[:-1])} and {DEFAULT_AREAS[-1]} "
                      f"ran short, so it widened to {', '.join(widened_to)}.")

    if not collected and not n["already"]:
        board.finish_task(task["id"], agent, {"found": 0, "stopped_because": why},
                          status="done")
        return {"ok": False, "agent": agent, "task": task["id"], "errors": errors,
                "target": target, "rounds": n["rounds"], "stopped_because": why,
                "error": (f"OpenStreetMap returned nothing new in {n['rounds']} "
                          f"search(es), starting with {core[0][0]} in {core[0][1]}"
                          + (". " + errors[0] if errors else
                             ". That is the data, not a fault — OSM is thinner "
                             "than Google in the UAE."))}

    reach = reach_of(collected)
    if dry_run:
        board.finish_task(task["id"], agent, {"found": len(collected),
                                              "dry_run": True}, status="done")
        return {"ok": True, "agent": agent, "task": task["id"], "dry_run": True,
                "candidates": collected[:40], "errors": errors, "reach": reach,
                "target": target, "rounds": n["rounds"], "stopped_because": why,
                "message": stop_line,
                "head": f"{len(collected)} found, nothing stored (dry run). {stop_line}"}

    guessed["head"] = (f"looked for {guessed['looked_for']} missing site(s), found "
                       f"{guessed['sites_found']}" if guess_sites else
                       "missing sites not looked for this pass")
    enriched["head"] = (f"read {enriched['sites_read']} site(s), got "
                        f"{enriched['emails_found']} email address(es)"
                        + (f", {enriched['not_reached_this_pass']} left for the "
                           f"next pass" if enriched["not_reached_this_pass"] else "")
                        if read_sites else "sites not read this pass")
    on_file = n["already"] + len(got["duplicates"])
    head = (f"{len(got['stored'])} new lead(s) from OpenStreetMap, "
            f"{on_file} already on file, {len(got['refused'])} refused — "
            + reach_line(reach) + ". " + stop_line)
    board.finish_task(task["id"], agent, {"stored": len(got["stored"]),
                                          "mailable": reach["mailable"],
                                          "target": target,
                                          "stopped_because": why,
                                          "head": head}, status="done")
    board.log_decision(
        "leads-osm",
        reasoning=(f"Hunted in {n['rounds']} round(s) for {target} lead(s), most "
                   f"valuable segment first"
                   + (f", widening to {', '.join(widened_to)}" if widened_to else "")
                   + f". {n['already']} business(es) were already on file and cost "
                   f"nothing. Found {guessed['sites_found']} website(s) the map did "
                   f"not have, and read {enriched['sites_read']} for a published "
                   f"email. {stop_line}"),
        expectation=(f"{reach['mailable']} can be written to by the mail runner; "
                     f"{reach['message']} are a phone only and are the owner's "
                     f"to message by hand."),
        confidence=0.6,
        evidence={"stored": [s["id"] for s in got["stored"]],
                  "target": target, "stopped_because": why,
                  "emails_found": enriched["emails_found"],
                  "sites_guessed": guessed["sites_found"]})
    return {"ok": True, "agent": agent, "task": task["id"], "head": head,
            "errors": errors, "reach": reach, "enriched": enriched,
            "guessed_sites": guessed, "target": target,
            "complete": n["stored"] >= target, "rounds": n["rounds"],
            "examined": n["examined"], "already_on_file": n["already"],
            "widened_to": widened_to, "stopped_because": why,
            "message": stop_line, **got, "contacted": 0, "sector": SECTOR}


# ---------------------------------------------------------------------------
# THE PROBE — one command, no quoting, no cabinet
# ---------------------------------------------------------------------------

def probe() -> dict:
    """
    Can this machine reach OpenStreetMap, and is there UAE data in it?

    Exists because the owner's machine is the only one that can answer it: the
    container this was written in cannot reach any Overpass mirror, so the
    network hop is the one part that could not be tested here. Reads only,
    writes nothing, spends nothing.
    """
    q = build_query([("amenity", "cafe")], BBOX["Dubai"], limit=20, timeout=25)
    r = ask(q, timeout=45)
    if not r["ok"]:
        return {"ok": False, "error": r["error"], "tried": r["tried"]}
    named = [to_candidate(e, "food_stall", "Dubai") for e in r["elements"]]
    named = [c for c in named if c]
    return {"ok": True, "mirror": r["mirror"], "raw": len(r["elements"]),
            "named": len(named),
            "with_phone": sum(1 for c in named if c["phone"]),
            "with_website": sum(1 for c in named if c["website"]),
            "sample": named[:8], "tried": r["tried"]}


def _print_probe(p):
    if not p.get("ok"):
        print("OPENSTREETMAP — could not reach it")
        for t in p.get("tried", []):
            print("   " + t)
        print("\nThat is the answer I needed. Send this output back.")
        return
    print(f"OPENSTREETMAP — reachable via {p['mirror']}")
    print(f"   {p['raw']} point(s) in a slice of Dubai, {p['named']} with a name")
    print(f"   {p['with_phone']} with a phone, {p['with_website']} with a website")
    for c in p["sample"]:
        print(f"     {c['company'][:34]:36} {c['phone'][:16]:17} {c['website'][:34]}")
    print("\nThat is the answer I needed. Send this output back.")


def main():
    ap = argparse.ArgumentParser(
        description="Find UAE businesses on OpenStreetMap — no Places key.")
    ap.add_argument("--probe", action="store_true",
                    help="can this machine reach OSM? reads only, stores nothing")
    ap.add_argument("--find", metavar="SEGMENT", help="one segment, printed")
    ap.add_argument("--segments", action="store_true",
                    help="list what --find and --area will accept")
    ap.add_argument("--area", default="Dubai")
    ap.add_argument("--chains", action="store_true",
                    help="keep the franchises too (dropped by default: they buy "
                         "centrally, usually abroad)")
    # CONTACTABLE IS THE DEFAULT NOW. The day run calls this with no flags,
    # so the default is what actually happens every day — and it was "every
    # business on the map". The first time Leads was allowed to collect, it
    # stored fifteen businesses and one had any way in. The same collector
    # asked for contactable ones only gave twenty-two mailable out of
    # twenty-five. A lead nobody can phone, message or write to is not a lead.
    # --everyone is there for a hand-run survey of a whole area.
    ap.add_argument("--contactable", action="store_true", default=True,
                    help="(the default) ask OSM only for businesses with a "
                         "phone, site or email tag")
    ap.add_argument("--everyone", dest="contactable", action="store_false",
                    help="include businesses the map gives no way to reach")
    ap.add_argument("--no-read-sites", dest="read_sites", action="store_false",
                    help="skip reading the businesses' websites for an email "
                         "address (faster, but the leads stay unmailable)")
    ap.add_argument("--no-guess-sites", dest="guess_sites", action="store_false",
                    help="skip looking for the website of a business the map "
                         "gave no contact for at all")
    ap.add_argument("--limit", type=int, default=MAX_RESULTS,
                    help=f"how many POIs to ask OSM for (default {MAX_RESULTS})")
    ap.add_argument("--per-segment", type=int, default=1,
                    help="no longer limits anything — see --target")
    ap.add_argument("--target", type=int, default=None,
                    help=f"how many new leads to hunt for (default: the job's "
                         f"number, or {TARGET_DEFAULT})")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.segments:
        segs = segments_covered()
        if a.json:
            print(json.dumps({"segments": segs, "areas": list(BBOX)}, indent=2))
        else:
            print(f"{len(segs)} segments findable on OpenStreetMap:\n")
            for s in segs:
                why = (ls.SEGMENTS.get(s) or {}).get("why", "")
                print(f"   {s:20} {why[:88]}")
            print("\nAreas: " + ", ".join(BBOX))
            print("\n   python -m pipelines.leads_osm --find garage --area Sharjah")
        return 0

    if a.probe:
        p = probe()
        if a.json:
            print(json.dumps(p, indent=2))
        else:
            _print_probe(p)
        return 0 if p.get("ok") else 1

    if a.find:
        r = find(a.find, a.area, limit=a.limit, chains=a.chains,
                 contactable_only=a.contactable)
        if r.get("ok") and a.guess_sites:
            r["guessed_sites"] = find_site.fill_in(r["candidates"])
        if r.get("ok") and a.read_sites:
            r["enriched"] = enrich(r["candidates"])
        if r.get("ok"):
            r["reach"] = reach_of(r["candidates"])
        if a.json:
            print(json.dumps(r, indent=2))
        elif not r.get("ok"):
            print("could not: " + str(r.get("error")))
        else:
            print(f"{a.find} in {a.area} via {r['mirror']} — {r['head']}")
            print(f"   {r['with_phone']} with a phone, {r['with_website']} "
                  f"with a site, {r['with_email_from_the_map']} with an email "
                  f"already on the map")
            if r.get("guessed_sites"):
                print(f"   {r['guessed_sites']['head']}")
            if r.get("enriched"):
                print(f"   {r['enriched']['head']}")
            print(f"   HOW WE REACH THEM: {reach_line(r['reach'])}")
            if r["chains_dropped"]:
                names = ", ".join(d["company"] for d in r["chains_dropped"][:6])
                print(f"   dropped as chains: {names}"
                      + (" ..." if len(r["chains_dropped"]) > 6 else ""))
            print()
            for c in r["candidates"][:25]:
                print(f"   {c['company'][:30]:32} {c['phone'][:15]:16} "
                      f"{(c.get('email') or '')[:30]:32} {c['area'][:14]:16} "
                      f"{c['website'][:28]}")
        return deskwork.exit_code(r)

    r = run(segment=None, area=a.area if a.area != "Dubai" else None,
            per_segment=a.per_segment, dry_run=a.dry_run,
            read_sites=a.read_sites, contactable_only=a.contactable,
            guess_sites=a.guess_sites, target=a.target)
    print(json.dumps(r, indent=2) if a.json else
          (r.get("head") or r.get("error") or "nothing to do"))
    return deskwork.exit_code(r)


if __name__ == "__main__":
    sys.exit(main())
