"""
leads_hunt.py — the Hunter. Goes and gets real UAE businesses off the map.

    python -m pipelines.leads_hunt --segment hotel --area "Dubai Marina"
    python -m pipelines.leads_hunt --dry-run          (see what it would collect)

HOW IT ACTUALLY WORKS, AND WHY THIS WAY

Two hops, because no single source has everything.

  1. THE MAP. Google Maps is where every UAE business is listed with its
     category, area, phone and website — the four things we need. We read it
     through the Places API rather than driving a browser at maps.google.com.
     That is not squeamishness: scraping the Maps front end breaks Google's
     terms and gets the IP blocked within days, usually mid-run, and you find
     out when the pipeline has been returning empty for a week. The API is the
     same data through the front door, it costs a few dollars per thousand
     places, and Google gives every account a monthly credit that covers far
     more than this business will use.

  2. THE COMPANY'S OWN WEBSITE. Maps has no email addresses — not hidden, not
     paywalled, simply not in there. The email comes off the company's own
     contact page, where they printed it precisely so people would write to
     them. That hop is where the working addresses come from, and it is why a
     listing without a website is worth much less than one with.

Everything a business publishes about itself is fair game. What the Hunter will
not do is invent an address it never saw: no pattern-guessing info@<domain>
because it looks likely. A guessed address bounces, and bounces are what get an
outreach domain blacklisted, which would cost every real lead behind it.

WHAT COMES OUT

One row per business, with the service it most likely needs already worked out
from its own listing (see services.py). A hotel comes back tagged for shower
glass and mirrors; a warehouse for aluminium doors. That tag is what lets the
mail talk about the right thing instead of reciting a catalogue.

Nothing here contacts anybody. Collection and outreach are separate jobs on
purpose, with Security's clearance in between.
"""
import argparse
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from coordination import board
from coordination import countries, deskwork, profile, uae
from . import harvest
from . import leads_sources as ls
from . import services

PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
FIELDS = ("places.displayName,places.formattedAddress,places.nationalPhoneNumber,"
          "places.internationalPhoneNumber,places.websiteUri,places.primaryTypeDisplayName,"
          "places.types,places.businessStatus,places.userRatingCount")

UA = "Mozilla/5.0 (compatible; glasswork-leads/1.0; +contact-page reader)"

# Pages a UAE business puts its address on, in the order worth trying.
CONTACT_PATHS = ("", "/contact", "/contact-us", "/contactus", "/about",
                 "/about-us", "/get-in-touch", "/enquiry")

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

# Addresses that belong to the website's builder, not the business.
JUNK_EMAIL = ("example.com", "sentry.io", "wordpress", "wixpress", "godaddy",
              "squarespace", "shopify", "yourdomain", "domain.com", "email.com",
              "sample", "test@", "noreply", "no-reply", "donotreply")
JUNK_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js")


# ---------------------------------------------------------------------------
# 1. the map
# ---------------------------------------------------------------------------

def _setting(name) -> str:
    """The environment first, then the saved settings (.env) — as everywhere else."""
    v = (os.environ.get(name) or "").strip()
    if v:
        return v
    try:
        from providers import integrations
        return (integrations.read_env().get(name) or "").strip()
    except Exception:
        return ""


def _is_tomtom(tool) -> bool:
    return "tomtom" in f"{tool.get('name', '')} {tool.get('base_url', '')}".lower()


def _places_key():
    """The Google Places key, from the shelf if it is on there, else the settings."""
    try:
        from providers import store
        for t in store.tools_on_shelf("maps"):
            k = (t.get("api_key") or "").strip()
            if k and not _is_tomtom(t):
                return k, t.get("name", "maps shelf")
    except Exception:
        pass
    k = _setting("GOOGLE_PLACES_API_KEY") or _setting("GOOGLE_MAPS_API_KEY")
    return (k, "GOOGLE_PLACES_API_KEY") if k else (None, None)


def _tomtom_key():
    """The TomTom key: a TomTom tool on the maps shelf, else TOMTOM_API_KEY."""
    try:
        from providers import store
        for t in store.tools_on_shelf("maps"):
            k = (t.get("api_key") or "").strip()
            if k and _is_tomtom(t):
                return k, t.get("name", "TomTom")
    except Exception:
        pass
    k = _setting("TOMTOM_API_KEY")
    return (k, "TOMTOM_API_KEY") if k else (None, None)


def _map_sources() -> list:
    """
    Every map the Hunter can read, in the order it tries them.

    "google places api says its unavailable ... add tom tom as alternate".
    Google first when it has a key — it carries the most UAE listings — and
    TomTom behind it: used on its own when there is no Google key, and taken
    over mid-pass the moment Google refuses, so one broken key never costs the
    day its leads. With neither, OpenStreetMap (its own step) still runs.
    """
    out = []
    k, where = _places_key()
    if k:
        out.append({"kind": "google", "key": k, "from": where, "label": "Google Places"})
    k, where = _tomtom_key()
    if k:
        out.append({"kind": "tomtom", "key": k, "from": where, "label": "TomTom"})
    return out


def _cc() -> str:
    """The open website's country, as the maps take it."""
    return profile.country("AE") or "AE"


def _country_name() -> str:
    return countries.name(_cc()) or "United Arab Emirates"


def search_places(query, area, key, limit=20, timeout=25):
    """
    One text search against the Places API. Returns (places, error).

    regionCode=AE keeps the results in the country even when a query is generic,
    which matters: 'hotel Marina' without it returns Marina del Rey.
    """
    body = json.dumps({
        "textQuery": f"{query} in {area}, {_country_name()}",
        "regionCode": _cc(),
        "maxResultCount": min(int(limit), 20),
    }).encode()
    req = urllib.request.Request(PLACES_URL, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "X-Goog-Api-Key": key,
        "X-Goog-FieldMask": FIELDS,
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace")).get("places", []), None
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:600]
        return [], places_refusal(e.code, detail)
    except Exception as e:
        return [], str(e)


def places_refusal(code, detail) -> str:
    """
    Google's refusal, in words he can act on.

    "the google places api says its unavailable". Google closed the OLD
    "Places API" to new projects, and in the Cloud console it now shows as
    unavailable — while this program uses the NEW one, "Places API (New)",
    which is a separate switch in the same library. A refusal that only said
    "HTTP 403" left him looking at the wrong switch.
    """
    d = (detail or "").lower()
    if "billing" in d:
        return ("Google refused the Places key: billing is not switched on for its "
                "Google Cloud project. Google Cloud → Billing → link a billing "
                "account to the project (Google gives a free monthly allowance). "
                f"HTTP {code}.")
    if any(w in d for w in ("service_disabled", "has not been used", "is disabled",
                            "api_key_service_blocked", "not been enabled")):
        return ("Google refused the Places key: \"Places API (New)\" is not switched "
                "on for its project. Google Cloud → APIs & Services → Library → search "
                "\"Places API (New)\" and press Enable. Not the old \"Places API\" — "
                "Google closed that one to new projects, which is why it shows as "
                f"unavailable. HTTP {code}.")
    if "api key not valid" in d or "api_key_invalid" in d:
        return ("Google says the Places key is not valid — copy it again from Google "
                f"Cloud → APIs & Services → Credentials. HTTP {code}.")
    if "referer" in d or "ip address" in d or "restricted" in d:
        return ("Google refused the Places key because of a restriction on it (a "
                "website or IP restriction). Google Cloud → Credentials → the key → "
                "allow the Places API (New) and no website restriction for a key used "
                f"from this computer. HTTP {code}.")
    return f"HTTP {code} from the Places API — {detail[:300]}"


# ---------------------------------------------------------------------------
# 1b. the other map — TomTom
# ---------------------------------------------------------------------------
#
# TomTom's Search API lists UAE businesses with a name, category, address, phone
# and often a website — the same four things Places gives, from a different
# survey, with a free daily allowance and no Google Cloud project behind it.
# Two calls: where the area is (once per area, kept for the pass), then the
# businesses of that kind around it.

TOMTOM_BASE = "https://api.tomtom.com/search/2"
TOMTOM_PAGE = 50          # results per search; TomTom allows up to 100
_TT_WHERE = {}            # area -> (lat, lon, radius metres), for this process


def _tomtom_get(path, params, timeout=25):
    url = f"{TOMTOM_BASE}/{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def tomtom_refusal(code, detail) -> str:
    """TomTom's refusal, in words he can act on."""
    d = (detail or "").lower()
    if code in (401, 403) or "forbidden" in d or "not authorized" in d:
        return ("TomTom refused the key — check it at developer.tomtom.com → "
                "Dashboard → Keys (copy it again), and that the Search API is "
                f"ticked for that key. HTTP {code}.")
    if code == 429 or "over the limit" in d or "quota" in d:
        return ("TomTom's free allowance is used up for now (too many searches "
                "today, or too many at once). It carries on by itself tomorrow; "
                f"OpenStreetMap keeps collecting meanwhile. HTTP {code}.")
    return f"HTTP {code} from TomTom — {(detail or '')[:300]}"


def _tomtom_where(area, key, timeout=25):
    """(lat, lon, radius) for an area name, asked once per area. None if unknown."""
    if area in _TT_WHERE:
        return _TT_WHERE[area]
    d = _tomtom_get("geocode/" + urllib.parse.quote(f"{area}, {_country_name()}",
                                                    safe="") + ".json",
                    {"key": key, "countrySet": _cc(), "limit": 1}, timeout)
    res = (d.get("results") or [])
    if not res or not (res[0].get("position") or {}).get("lat"):
        _TT_WHERE[area] = None
        return None
    pos = res[0]["position"]
    is_emirate = area.strip().lower() in {e.lower() for e in EMIRATES}
    radius = 30000 if is_emirate else 6000
    vp = res[0].get("viewport") or res[0].get("boundingBox") or {}
    try:
        tl, br = vp["topLeftPoint"], vp["btmRightPoint"]
        # half the diagonal of the area's own box, in metres, kept sensible
        dlat = abs(tl["lat"] - br["lat"]) * 111000
        dlon = abs(tl["lon"] - br["lon"]) * 101000
        radius = int(min(40000, max(3000, ((dlat ** 2 + dlon ** 2) ** 0.5) / 2)))
    except Exception:
        pass
    _TT_WHERE[area] = (pos["lat"], pos["lon"], radius)
    return _TT_WHERE[area]


def search_tomtom(query, area, key, limit=TOMTOM_PAGE, timeout=25):
    """
    One TomTom search for this kind of business around an area. Returns
    (results, error), results in TomTom's own shape.
    """
    try:
        where = _tomtom_where(area, key, timeout)
        params = {"key": key, "countrySet": _cc(), "language": profile.language() or "en-GB",
                  "limit": min(int(limit or TOMTOM_PAGE), 100)}
        if where:
            params.update({"lat": where[0], "lon": where[1], "radius": where[2]})
            q = query
        else:
            q = f"{query} {area}"          # an area TomTom could not place
        d = _tomtom_get("poiSearch/" + urllib.parse.quote(q, safe="") + ".json",
                        params, timeout)
        return [r for r in (d.get("results") or []) if r.get("poi")], None
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")[:600]
        except Exception:
            detail = ""
        return [], tomtom_refusal(e.code, detail)
    except Exception as e:
        return [], f"TomTom did not answer: {e}"


def _tomtom_to_candidate(r, segment, area):
    """Flatten one TomTom result into the shape the cabinet stores."""
    poi = r.get("poi") or {}
    addr = r.get("address") or {}
    name = (poi.get("name") or "").strip()
    site = (poi.get("url") or "").strip()
    if site and not re.match(r"^https?://", site, re.I):
        site = "http://" + site
    cats = [str(c) for c in (poi.get("categories") or []) if c]
    names = [n.get("name") for cl in (poi.get("classifications") or [])
             for n in (cl.get("names") or [])
             if n.get("name") and str(n.get("nameLocale", "")).lower().startswith("en")]
    cat = cats[0] if cats else (names[0] if names else "")
    svc, why = services.match_service(name, cat, " ".join(cats + names), segment=segment)
    return {
        "company": name,
        "name": "",
        "email": "",                     # comes from their own site, below
        "phone": (poi.get("phone") or "").strip(),
        "website": site,
        "address": (addr.get("freeformAddress") or "").strip(),
        "area": area,
        "segment": segment,
        "category": cat,
        "source": "tomtom (search api)",
        "country": _cc(),                 # the search was fenced to this country
        "service": svc,
        "service_label": services.label(svc),
        "service_reason": why,
        "reviews": 0,
        "status": "",                    # TomTom lists open businesses only
    }


def tomtom_check() -> dict:
    """
    One small real search, for the Test button on Setup: does TomTom answer
    with this key, and does it know UAE businesses?
    """
    key, where = _tomtom_key()
    if not key:
        return {"ok": False, "why": "No TomTom key saved yet — paste it in the TomTom "
                                    "box and press Save first."}
    # The owner's test is the one he knows ("glass near Dubai"); anyone else's
    # asks for the first kind of business their profile sells to.
    if profile.is_starter():
        what, near = "glass", "Dubai"
    else:
        first = next(iter(ls.SEGMENTS.values()), {})
        what = (list(first.get("queries") or []) or ["business"])[0]
        near = (uae.served() or [_country_name()])[0]
    found, err = search_tomtom(what, near, key, limit=5)
    if err:
        return {"ok": False, "why": err}
    names = [((r.get("poi") or {}).get("name") or "") for r in found][:5]
    return {"ok": True, "found": len(found), "sample": names,
            "why": (f"TomTom answered — {len(found)} {what} business(es) near {near}, e.g. "
                    + ", ".join(n for n in names[:3] if n) if found else
                    f"TomTom answered, but found nothing for '{what}' near {near} — the "
                    "key works; try a run and see what it collects.")}


def _place_to_candidate(p, segment, area):
    """Flatten one Places result into the shape the cabinet stores."""
    name = ((p.get("displayName") or {}).get("text") or "").strip()
    site = (p.get("websiteUri") or "").strip()
    cat = (p.get("primaryTypeDisplayName") or {}).get("text") or ""
    types = " ".join(p.get("types") or [])
    svc, why = services.match_service(name, cat, types, segment=segment)
    return {
        "company": name,
        "name": "",                      # a business, not a person, until proven otherwise
        "email": "",                     # comes from their own site, below
        "phone": (p.get("internationalPhoneNumber")
                  or p.get("nationalPhoneNumber") or "").strip(),
        "website": site,
        "address": (p.get("formattedAddress") or "").strip(),
        "area": area,
        "segment": segment,
        "category": cat,
        "source": "google maps (places api)",
        "country": _cc(),                 # the search was fenced to this country
        "service": svc,
        "service_label": services.label(svc),
        "service_reason": why,
        "reviews": p.get("userRatingCount") or 0,
        "status": p.get("businessStatus") or "",
    }


# ---------------------------------------------------------------------------
# 2. the company's own contact page
# ---------------------------------------------------------------------------

def _get(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ctype = (r.headers.get("Content-Type") or "").lower()
            if "html" not in ctype and "text" not in ctype:
                return ""
            return r.read(600_000).decode("utf-8", "replace")
    except Exception:
        return ""


def usable_email(addr: str) -> bool:
    a = (addr or "").strip().lower()
    if not a or len(a) > 120:
        return False
    if any(j in a for j in JUNK_EMAIL):
        return False
    if any(a.endswith(x) for x in JUNK_EXT):
        return False
    return "@" in a and "." in a.split("@")[-1]


FREE_MAILBOXES = ("gmail.", "hotmail.", "yahoo.", "outlook.", "live.", "icloud.")


def rank_emails(found, site_host=""):
    """
    Best address first. Their own domain before a free mailbox; then by who the
    address reaches — ls.address_tier: projects@ and owner@ (someone who buys)
    before info@ (a switchboard), before sales@ and careers@ (nobody who buys
    from us), and a named person after every role, per the PDPL rule.

    It also DROPS addresses belonging to someone else. Half the small-business
    sites in the UAE carry a "built by <web agency>" line in the footer with the
    agency's address next to it, and that address ranks perfectly well on every
    other test. Mailing it wastes the slot and tells the agency we scraped their
    client. Their own domain or a free mailbox they published: nothing else.
    """
    host = (site_host or "").lower().lstrip("www.")

    def theirs(a):
        dom = a.partition("@")[2].lower()
        if not host:
            return True                  # no site to compare against — keep it
        return host in dom or dom in host or any(f in dom for f in FREE_MAILBOXES)

    def score(a):
        a = a.lower()
        dom = a.partition("@")[2]
        return (0 if host and host in dom else 1,          # their own domain
                ls.address_tier(a),                         # who it reaches
                1 if any(g in dom for g in FREE_MAILBOXES) else 0,
                len(a))

    keep = set(a.lower() for a in found if usable_email(a))
    return sorted((a for a in keep if theirs(a)), key=score)


def emails_from_site(url, max_pages=3, timeout=12):
    """
    Read the company's own pages and take the addresses they published there.
    Returns (emails_best_first, pages_read).

    Nothing is guessed. If a company has not published an address, it comes back
    with none and stays a phone-only lead, which is an honest outcome.
    """
    if not url:
        return [], []
    try:
        parsed = urllib.parse.urlparse(url if "://" in url else "https://" + url)
    except Exception:
        return [], []
    base = f"{parsed.scheme}://{parsed.netloc}"
    host = parsed.netloc.lower().lstrip("www.")

    found, read = [], []
    for path in CONTACT_PATHS[:max_pages + 1]:
        html = _get(base + path, timeout)
        if not html:
            continue
        read.append(base + path)
        # mailto: links first — those are unambiguous, an address they chose to publish
        found += [m.split("?")[0] for m in
                  re.findall(r'mailto:([^"\'>\s]+)', html, re.I)]
        found += EMAIL_RE.findall(html)
        if rank_emails(found, host):
            break                        # got one worth having; stop reading
        time.sleep(0.4)                  # be polite to someone else's server
    return rank_emails(found, host), read


def enrich(candidate, timeout=12, deadline=None):
    """
    Read the business's own site for everything Maps does not hold, and read
    their own words for a better service match.

    THIS USED TO BE ITS OWN LITTLE CRAWLER AND IT WAS THE WEAK LINK.

    Raw urllib against eight guessed paths: no rendering, so a Wix or
    Squarespace site — which is most UAE small businesses — came back an empty
    shell and the lead was filed phone-only; no cache; no spend guard; and a
    guessed /contact was a 404 on any site that calls the page something else.
    The owner's own site calls it /contact.php, so the guesser would have missed
    the address on the business this agency belongs to.

    pipelines/harvest.py replaces it: the site's OWN links are followed, pages
    are read through the fetcher (address checked, plain HTTP first because it
    is nothing, renderer behind it, cached, metered), and every detail comes
    back with a note saying where it was found and how.
    """
    site = candidate.get("website")
    if not site:
        candidate["pages_read"] = []
        return candidate

    wait = max(timeout, 20)
    if deadline:
        # A site read near the end of the pass gets the time that is left,
        # shared across its pages, so the last read cannot carry the pass past
        # its own stopping point and lose the batch it was about to save.
        wait = max(5, min(wait, int((deadline - time.time()) / harvest.MAX_PAGES)))
    h = harvest.harvest(site, timeout=wait)
    pages = [p["url"] for p in h.get("pages") or []]
    candidate["pages_read"] = pages
    candidate["harvest"] = h.get("head", "")

    if h.get("emails"):
        candidate["email"] = h["emails"][0]["value"]
        candidate["other_emails"] = [e["value"] for e in h["emails"][1:4]]
    # Maps gives A number. The site often gives the one somebody answers.
    if h.get("phones") and not candidate.get("phone"):
        candidate["phone"] = h["phones"][0]["value"]
    # ONLY from a click-to-chat button. See the note in harvest.py: a business
    # that put a WhatsApp button on its own website has invited people to
    # message it there, and that is not the same as a mobile number we happened
    # to find. The maybes are carried separately and never used to contact.
    if h.get("whatsapp"):
        candidate["whatsapp"] = h["whatsapp"][0]["value"]
    if h.get("whatsapp_maybe"):
        candidate["whatsapp_maybe"] = [w["value"] for w in h["whatsapp_maybe"][:2]]
    if h.get("address"):
        candidate["address"] = h["address"]
    if h.get("people"):
        candidate["name"] = h["people"][0]["name"]
        candidate["name_role"] = h["people"][0].get("role", "")
    if h.get("socials"):
        candidate["socials"] = ", ".join(s["value"] for s in h["socials"][:4])
    # Where every detail came from, kept with the lead. A contact with no
    # provenance is a rumour, and in six months nobody can tell a real one from
    # a parsing accident.
    candidate["evidence"] = {
        k: [{kk: vv for kk, vv in row.items() if kk in ("value", "name", "how", "where")}
            for row in (h.get(k) or [])[:4]]
        for k in ("emails", "phones", "whatsapp", "people")}

    if pages and not (deadline and time.time() >= deadline):
        # their own site describes what they do better than a Maps category does
        blurb = _get(pages[0], timeout)[:20000]
        svc, why = services.match_service(candidate["company"], candidate.get("category"),
                                          blurb, segment=candidate.get("segment", ""))
        candidate["service"], candidate["service_label"] = svc, services.label(svc)
        candidate["service_reason"] = why
    return candidate


# ---------------------------------------------------------------------------
# 3. the run
# ---------------------------------------------------------------------------

def local_first(areas):
    """
    Nearest out. The owner's own base first, then the rest of that emirate, then
    the others.

    "Main target is locals" is not a preference, it is how this trade makes
    money: a Business Bay enquiry gets surveyed tomorrow and quoted the same
    week, while a Ras Al Khaimah one is two hours each way before anybody has
    measured anything. The search budget always runs out — it should run out at
    the far end.

    The base comes from the address the owner saved on /setup. With none saved
    the list is left exactly as written, which already starts in Dubai; guessing
    a home city from nothing would be inventing a fact.

    THE ORDER WITHIN THE HOME EMIRATE MATTERS TOO, and this used to get it
    backwards. It put the emirate itself first and its districts after, so the
    first search of every run was "hotel in Dubai" — which returns the same
    forty well-known names every time, most of them already in the cabinet as
    duplicates. "hotel in Al Quoz" returns the street. Districts first, the
    emirate as the catch-all behind them.
    """
    try:
        from coordination import site
        base = (site.status().get("url") or "")
    except Exception:
        base = ""
    # A domain has no spaces or hyphens in the middle of a place name, so the
    # comparison is made on a flattened copy of both.
    flat = base.lower().replace(" ", "").replace("-", "").replace("_", "")
    home = ""
    for place in sorted(set(uae.EMIRATES) | set(uae.CITIES), key=lambda s: -len(s)):
        if place.lower().replace(" ", "") in flat:
            home = uae.emirate_of(place) or place
            break
    if not home:
        return list(areas)

    def rank(a):
        where = uae.emirate_of(a)
        mine = where.lower() == home.lower()
        is_emirate = a.strip().lower() in {e.lower() for e in uae.EMIRATES}
        if mine and not is_emirate:
            return 0                      # a district or town of home — the street
        if mine:
            return 1                      # home emirate, searched wide
        if not is_emirate:
            return 2                      # somebody else's district
        return 3                          # somebody else's emirate

    return sorted(areas, key=rank)


def plan_targets(segment=None, area=None, per_segment=2, service=None):
    """
    Which searches to run.

    Three ways to aim it, and the third is the one the owner asked for:

      nothing named   the whole market. The Hunter is not restricted to a chosen
                      few, which is the point of the segment table.
      a segment       just that kind of business.
      A SERVICE       every kind of business that BUYS that service, and nobody
                      else. "Get me shower-glass work this week" now targets
                      hotels, gyms, villas and fit-out contractors — not a
                      warehouse that will never want one. The buyer list comes
                      off services.py, so it can never drift from what the mail
                      will end up writing about.

    Aiming by service also STAMPS the service onto every target, so a lead
    collected for shower glass is stored knowing that. Before, the service was
    guessed back out of the business's own listing afterwards — fine when you
    are sweeping the market, wrong when you went looking for one specific job.

    LOCALS FIRST. Areas are searched nearest-out: the owner's own districts
    before the far emirates. A glass firm in Dubai wins a Business Bay job it
    can survey tomorrow; a Ras Al Khaimah enquiry is two hours each way before
    anyone has quoted anything. When the search budget runs out — and it always
    does — it should run out on the far end, not the near one.

    The order is round-robin across segments, not segment by segment. It matters
    because callers cap the number of searches: nested loops would spend the
    whole budget on fit-out contractors in six districts and never reach a hotel,
    which is exactly the narrowness this sector was rebuilt to get away from.
    """
    svc = services.resolve_service(service) if service else ""
    if service and not svc:
        return []                      # an unknown service must find nobody

    if segment:
        segs = [segment]
    elif svc:
        # only the segments that actually buy this job
        segs = [s for s in services.buyers_of(svc) if s in ls.SEGMENTS]
    else:
        segs = list(ls.SEGMENTS)

    areas = [area] if area else local_first(ls.DISTRICTS)

    per_seg = []
    for s in segs:
        meta = ls.SEGMENTS.get(s)
        if not meta:
            continue
        per_seg.append([{"segment": s, "query": q, "area": a,
                         **({"for_service": svc} if svc else {})}
                        for q in list(meta["queries"])[:per_segment] for a in areas])

    out, i = [], 0
    while any(len(q) > i for q in per_seg):
        for q in per_seg:
            if len(q) > i:
                out.append(q[i])
        i += 1
    return out


def critic_check(candidates, searched, errors):
    """
    The Hunter's failure modes: collecting nothing and calling it a day,
    collecting the same firm twice, or collecting rows nobody can contact.
    """
    reasons = []
    if not searched:
        reasons.append("no searches ran — there is nothing to report")
    if searched and not candidates and not errors:
        reasons.append("every search came back empty and nothing errored — that is "
                       "not a real result, check the query and the key")
    names = [c["company"].strip().lower() for c in candidates if c.get("company")]
    if len(names) != len(set(names)):
        reasons.append("the same company appears twice in one run")
    blank = [c for c in candidates if not (c.get("email") or c.get("phone"))]
    if blank:
        reasons.append(f"{len(blank)} rows have no way to reach them at all")
    noservice = [c for c in candidates if not c.get("service")]
    if noservice:
        reasons.append(f"{len(noservice)} rows have no service attached — we would not "
                       "know what to write to them about")
    return (not reasons, reasons)


def run(agent="leads-hunter", segment=None, area=None, limit=20, per_segment=1,
        max_searches=6, dry_run=False, enrich_sites=True, service=None):
    """
    Collect leads — and, first, read the board to find out which leads.

    THE HUNTER USED TO IGNORE THE BOARD ENTIRELY. It ran the same sweep every
    day whatever the MD had posted, so "find 20 leads in Dubai" sat open while
    the desk that could do it ran past it. Now the job steers the pass: how
    many, which emirate, which service. Anything the job does not say keeps the
    caller's default, so a plain scheduled run behaves exactly as before.
    """
    with board.agent_run(agent) as run_row:
        # NO KEY, NO CLAIM.
        #
        # This desk used to claim the collection task first and THEN discover
        # there was no Places key — and job.cannot() closes a task BLOCKED. So
        # the step whose own message says "OpenStreetMap finds real UAE
        # businesses with no key at all, and it runs right after this step"
        # closed the one task OpenStreetMap needed, and used the sector's one
        # job per run doing it. The free path could never get a turn.
        #
        # A desk that cannot do the work without a key does not pick up the
        # work. It says why, as before, and leaves the task on the board for
        # the collector that can.
        if not dry_run and not _map_sources():
            out = _hunt(agent, segment, area, limit, per_segment, max_searches,
                        dry_run, enrich_sites, run_row, service)
            out["idle"] = True
            out["left_for"] = "Leads collects from OpenStreetMap"
            run_row.note(out.get("error") or "no maps key — left for OpenStreetMap")
            return out
        with deskwork.at_the_desk("Leads", agent, run_row) as job:
            wanted = None
            if job:
                wanted = job.how_many(None)
                limit, max_searches = _sized(wanted, limit, max_searches)
                area = job.first_mention(ls.DISTRICTS, area)
                # resolve_service already forgives our internal spelling —
                # "frameless shower" and "shower_glass" land on the same job
                service = services.resolve_service(job.text) or service
                # a job naming an emirate but no district still means that
                # emirate — search it wide rather than not at all
                if area is None:
                    area = job.first_mention(EMIRATES, None)
            out = _hunt(agent, segment, area, limit, per_segment, max_searches,
                        dry_run, enrich_sites, run_row, service, wanted=wanted)
            _answer(job, out, area, service)
        run_row.note(out.get("summary") or out.get("error") or "hunted")
        return out


# Google Places returns at most 20 results for one search, so a job asking for
# more than that is asking for more searches, not a bigger page.
PLACES_PAGE = 20
MOST_SEARCHES = 12

# How much of one page of search results survives to become a NEW stored lead.
#
# The old sizing assumed all of it — twenty results, twenty leads — and that is
# not what happens. Three filters run in series afterwards and they multiply:
# already in the cabinet, closed down, no way to contact them. On a segment
# that has been swept once already, most of a page is businesses we collected
# last week.
#
# A third is the honest figure from what the cabinet actually stores per pass,
# and it is deliberately a floor rather than an average: sizing a hunt on an
# optimistic yield is how you ask for twenty and stop after finding six.
FOUND_PER_PAGE = PLACES_PAGE / 3.0


def _sized(wanted, limit, max_searches):
    """
    Turn "find 20 leads" into a page size and a number of searches.

    ASKING FOR MORE USED TO GET YOU LESS. The old sum floored the search count
    at max_searches only while `wanted` fitted in a single page, and at ONE
    above it:

        ask for 20  ->  6 searches
        ask for 30  ->  2 searches
        ask for 40  ->  2 searches

    So "find 30 leads this week" ran a third of the hunt that "find 20" ran,
    and a Leads sector that looked lazy was doing exactly what it was told.
    Nothing in the output said so, because a hunt that runs two searches and
    finds four businesses reports four businesses, not a budget it never had.

    The floor is now the floor in both directions, and the target is divided by
    what a page really yields rather than by the page size. Still capped: a job
    typed as "500" is a typo, not a mandate to spend the month's Places budget
    in one pass.
    """
    if not wanted:
        return limit, max_searches
    wanted = max(1, int(wanted))
    page = min(PLACES_PAGE, wanted)
    needed = -(-wanted // int(FOUND_PER_PAGE))     # round up
    return page, min(MOST_SEARCHES, max(max_searches, needed))


EMIRATES = tuple(uae.EMIRATES)


def _answer(job, out, area, service):
    """
    Close the job the way the hunt actually went — and only the way it went.

    The distinction that matters to him: a pass that could not run (no key on
    the maps shelf) is BLOCKED with that sentence, because there is something
    to fix. A pass that ran and found only firms already in the cabinet is
    DONE, because the desk did its job and the answer was 'nobody new'. A pass
    that found nothing and cannot say why hands the job back rather than
    claiming either.
    """
    if not job:
        return
    where = f" in {area}" if area else ""
    what = f" for {services.label(service)}" if service else ""
    if not out.get("ok"):
        job.cannot(out.get("error") or "the hunt did not complete and gave no reason.")
        return
    if out.get("dry_run"):
        job.cannot("This was a planning pass only — nothing was collected, so "
                   "the job is not done.")
        return
    if out.get("critic") == "rejected":
        job.cannot("The Critic rejected the batch: "
                   + "; ".join(out.get("critic_reasons") or ["no reason given"]))
        return
    stored, dupes = out.get("stored", 0), out.get("duplicates", 0)
    clock = out.get("stopped") == "clock"
    if stored:
        job.did(f"Collected {stored} new lead(s){what}{where} from "
                f"{len(out.get('searches') or [])} searches"
                + (f"; {dupes} were already in the cabinet." if dupes else ".")
                + (" It stopped at its time limit and saved what it had."
                   if clock else ""),
                stored=stored, duplicates=dupes)
    elif clock and out.get("not_reached_this_pass"):
        # Out of time with sites still unread is not "nobody new" — left
        # unanswered, so the job is handed back and the next round reads them.
        return
    elif dupes:
        job.did(f"Searched{where}{what} and found {dupes} firm(s) — every one of "
                "them already in the cabinet, so nothing new was collected. "
                "This source is worked out; a different area or segment would "
                "be a better ask.", stored=0, duplicates=dupes)
    else:
        errs = [s.get("error") for s in (out.get("searches") or []) if s.get("error")]
        if errs:
            job.cannot("Every search failed: " + "; ".join(sorted(set(errs))[:3]))
        else:
            job.did(f"Searched{where}{what} across "
                    f"{len(out.get('searches') or [])} queries and found no "
                    "businesses at all matching them.", stored=0)


# "Leads collects — timed out after 420s", with nothing stored.
#
# The pass used to search every target, THEN read every website it had found,
# THEN store them all at the end. TomTom hands back fifty businesses a search,
# and reading fifty sites one polite request at a time is many minutes, so the
# day's stopwatch took the step away before the last line — and a pass killed
# before its last line stores nothing. It looked as though TomTom was not being
# used at all, when TomTom had answered every search.
#
# So, the same rule leads_osm follows: the pass takes most of its step's
# ceiling, read from the runner rather than copied; it skips firms already in
# the cabinet before reading anything; it saves after every BANK_EVERY sites;
# and it stops reading with WRITE_RESERVE left to save the last batch. Partial
# and saved beats complete and killed.
OF_THE_CEILING = 0.80
WRITE_RESERVE = 45       # screening, dedup and storing, with room for a bad day
HUNT_BUDGET = 330        # only if the runner's ceiling cannot be read
BANK_EVERY = 8           # sites read between saves — at most this many lost


def pass_budget() -> int:
    """Seconds this whole pass may take, from the day run's own step limit."""
    try:
        from . import day_run
        return max(90, int(day_run.step_timeout_for("pipelines.leads_hunt")
                           * OF_THE_CEILING))
    except Exception:
        return HUNT_BUDGET


def _refused(err) -> bool:
    """A map saying no to the key — not a slow line, which asking again can fix."""
    t = (err or "").lower()
    return any(w in t for w in ("refused", "http 401", "http 403", "http 429",
                                "not valid", "billing", "allowance is used up"))


def _search_map(src, t, limit):
    """One search on one map, as candidates. Returns (candidates, error)."""
    if src["kind"] == "tomtom":
        found, err = search_tomtom(t["query"], t["area"], src["key"],
                                   max(int(limit or 0), TOMTOM_PAGE))
        return [_tomtom_to_candidate(r, t["segment"], t["area"]) for r in found], err
    found, err = search_places(t["query"], t["area"], src["key"], limit)
    return [_place_to_candidate(p, t["segment"], t["area"]) for p in found], err


def _hunt(agent, segment, area, limit, per_segment, max_searches, dry_run,
          enrich_sites, run_row, service=None, wanted=None):
    sources = _map_sources()
    key = sources[0]["key"] if sources else None
    targets = plan_targets(segment, area, per_segment, service)[:max_searches]
    if service and not targets:
        return {"ok": False, "error":
                f"'{service}' is not a service this business sells, so there is "
                f"nobody to go and look for. One of: "
                + ", ".join(sorted(services.SERVICES))}

    if dry_run or not key:
        plan = {"ok": bool(targets), "dry_run": True, "searches_planned": len(targets),
                "targets": targets[:20],
                "segments_in_scope": len(ls.SEGMENTS),
                "areas_in_scope": len(ls.DISTRICTS),
                "summary": f"planned {len(targets)} searches across "
                           f"{len(set(t['segment'] for t in targets))} segments"}
        if not key:
            plan["ok"] = False
            plan["error"] = (
                "No maps key (Google Places or TomTom), so THIS pass collected nothing — "
                "but you do not need one to get leads. The free path is already in the day: "
                "'Leads collects from OpenStreetMap' (pipelines.leads_osm) finds real "
                "UAE businesses with no key at all, and it runs right after this step. "
                "If it also came back empty, the reason is on ITS line, not this one.\n\n"
                "Reading this as 'Leads is blocked until I buy something' is the wrong "
                "conclusion and it is this message's fault for not saying so. Places is "
                "an upgrade: it carries opening hours, ratings and categories OSM does "
                "not. To add it: Setup -> Shelf -> maps, or set GOOGLE_PLACES_API_KEY. "
                "Get it at console.cloud.google.com — enable 'Places API (New)', create "
                "a key, restrict it to that API. Google's monthly credit covers far more "
                "searching than this business will do.\n\n"
                "Or TomTom, with no Google account at all: Setup -> Integrations -> "
                "TomTom. A free key from developer.tomtom.com; its daily allowance "
                "covers this business's searching.")
        return plan

    # only one Hunter goes shopping at a time: two of them running the same
    # segment would spend twice and collect the same firms.
    budget = pass_budget()
    started = time.time()
    stop_looking = started + budget - WRITE_RESERVE
    with board.lock("leads_collection", agent, ttl=budget + 120):
        searched, errors, seen_here = [], [], set()
        at, switched = 0, []            # which map is in use, and any hand-over
        kept = []                       # every live business met, for the totals
        got = {"stored": [], "duplicates": [], "refused": [], "personal_data_held": []}
        n = {"already": 0, "unreachable": 0, "sites_read": 0, "left_unread": 0,
             "mailable": 0}
        rejected, new_ones = [], []      # new_ones: the candidates intake stored

        def have():
            # "20 leads" means 20 he can write to when sites are read — the
            # Leads room lists those. A pass that does not read sites can only
            # count what it stored.
            return n["mailable"] if enrich_sites else len(got["stored"])

        def bank(batch):
            """Read their sites, then STORE — now, not at the end of the pass."""
            ready = []
            for c in batch:
                if enrich_sites and c.get("website"):
                    if time.time() >= stop_looking:
                        # Not stored unread: a lead in the cabinet is skipped
                        # by every later search, so its site would never be
                        # read. It is found again next round and read then.
                        n["left_unread"] += 1
                        continue
                    try:
                        enrich(c, deadline=stop_looking)
                    except Exception:            # one bad site is not a lost batch
                        pass
                    n["sites_read"] += 1
                ready.append(c)
            # A listing with no phone and no published email cannot be
            # reached, so it is not a lead — left out here, and counted, rather
            # than letting one such row make the Critic throw away the batch.
            # TomTom lists more businesses without a phone than Google does.
            reach = [c for c in ready if c.get("email") or c.get("phone")]
            n["unreachable"] += len(ready) - len(reach)
            if not reach:
                return
            ok, reasons = critic_check(reach, searched, errors)
            if not ok:
                rejected.extend(r for r in reasons if r not in rejected)
                return
            # screen + dedup + store. intake() is the only door into the leads table.
            res = ls.intake(reach, agent=agent)
            for k in got:
                got[k].extend(res.get(k) or [])
            names = {(x.get("company") or "").strip().lower() for x in res.get("stored") or []}
            new_ones.extend(c for c in reach if c["company"].strip().lower() in names)
            n["mailable"] = sum(1 for x in got["stored"] if (x.get("email") or "").strip())

        stopped = "searched"
        for t in targets:
            if wanted and have() >= wanted:
                stopped = "target"
                break
            if time.time() >= stop_looking:
                stopped = "clock"
                break
            found, err = _search_map(sources[at], t, limit)
            # ONE MAP SAYING NO IS NOT THE DAY SAYING NO. Google refusing the
            # key (the "unavailable" API, billing off) hands the rest of the
            # pass to the next map — this search included.
            while err and _refused(err) and at + 1 < len(sources):
                switched.append(f"{sources[at]['label']} refused ({err[:120]}), so "
                                f"{sources[at + 1]['label']} took over")
                errors.append(err)
                at += 1
                found, err = _search_map(sources[at], t, limit)
            searched.append({**t, "found": len(found), "error": err,
                             "map": sources[at]["label"]})
            if err:
                errors.append(err)
                continue
            fresh = []
            for c in found:
                # Hunting FOR a service beats guessing one back out of the
                # listing: we already know what job we went looking for, and the
                # listing only ever hinted at it.
                if t.get("for_service"):
                    c["service"] = t["for_service"]
                    c["service_label"] = services.label(t["for_service"])
                    c["service_reason"] = ("collected specifically for "
                                           + services.label(t["for_service"]))
                nm = c["company"].strip().lower()
                if not nm or nm in seen_here:
                    continue
                if (c.get("status") or "OPERATIONAL") != "OPERATIONAL":
                    continue             # closed businesses are not leads
                seen_here.add(nm)
                kept.append(c)
                # Asked BEFORE a website is read: a business already in the
                # cabinet should cost nothing, not a site read that intake then
                # throws away as a duplicate. With TomTom's long lists, most of
                # a page on a swept area is last week's firms.
                if board.lead_seen(c.get("email"), c.get("phone"), c.get("company"),
                                   c.get("website")):
                    n["already"] += 1
                    continue
                fresh.append(c)
            # the ones with a website first: only they can give an email
            fresh.sort(key=lambda c: 0 if c.get("website") else 1)
            for i in range(0, len(fresh), BANK_EVERY):
                if wanted and have() >= wanted:
                    break
                bank(fresh[i:i + BANK_EVERY])
            time.sleep(0.5)
        if stopped == "searched" and wanted and have() >= wanted:
            stopped = "target"
        elif stopped == "searched" and n["left_unread"]:
            stopped = "clock"

        stored, dupes = len(got["stored"]), len(got["duplicates"]) + n["already"]
        # Nothing met at all is only a result when a search said why.
        ok, reasons = (True, []) if kept else critic_check([], searched, errors)
        if not ok or (rejected and not stored):
            why = (reasons or []) + rejected
            return {"ok": True, "critic": "rejected", "critic_reasons": why,
                    "searches": searched, "found": len(kept),
                    "stored": 0, "duplicates": dupes,
                    "summary": "critic rejected: " + "; ".join(why)}

        did = board.log_decision(
            "leads-hunt",
            reasoning=(f"Searched {len(searched)} segment/area combinations via "
                       f"{sources[at]['label']} ({sources[at]['from']}); "
                       f"{len(kept)} live businesses found, "
                       f"{stored} new, {dupes} already "
                       f"in the cabinet, {len(got['refused'])} screened out."),
            expectation="Each new lead carries the service it needs, so outreach writes "
                        "about that job rather than reciting the whole service list.",
            confidence=0.7,
            evidence={"lead_ids": [s["id"] for s in got["stored"]]})

        by_service = {}
        for c in new_ones:
            label = c.get("service_label") or services.label(c.get("service") or "")
            by_service[label] = by_service.get(label, 0) + 1
        mailable = n["mailable"]
        took = int(time.time() - started)
        tail = ""
        if stopped == "clock":
            tail = (f". It stopped at its time limit ({took}s) and saved what it had"
                    + (f"; {n['left_unread']} website(s) not yet read are picked "
                       "up next round" if n["left_unread"] else ""))
        elif stopped == "target":
            tail = f". That is the {wanted} asked for"
        return {"ok": True, "agent": agent, "critic": "passed",
                "map": sources[at]["label"], "switched": switched,
                "unreachable_left_out": n["unreachable"],
                "searches": searched, "found": len(kept),
                "stored": stored, "duplicates": dupes,
                "already_in_cabinet": n["already"],
                "refused": got["refused"],
                "with_email": mailable,
                "with_phone": sum(1 for c in new_ones if c.get("phone")),
                "sites_read": n["sites_read"],
                "not_reached_this_pass": n["left_unread"],
                "stopped": stopped, "seconds": took,
                "critic_notes": rejected,
                "by_service": by_service,
                "personal_data_held": got["personal_data_held"],
                "contacted": 0, "decision_id": did,
                "summary": (f"{stored} new leads from {len(searched)} "
                            f"{sources[at]['label']} searches "
                            f"({mailable} with a published email)"
                            + tail
                            + (". " + switched[0] if switched else ""))}


def main():
    ap = argparse.ArgumentParser(description="Collect real UAE business leads.")
    ap.add_argument("--segment", help="one segment (default: every segment)")
    ap.add_argument("--service", help="hunt for the businesses that BUY this "
                    "service, e.g. --service 'shower glass'")
    ap.add_argument("--area", help="one area (default: nearest first)")
    ap.add_argument("--limit", type=int, default=20, help="places per search (max 20)")
    ap.add_argument("--max-searches", type=int, default=6)
    ap.add_argument("--per-segment", type=int, default=1, help="search terms per segment")
    ap.add_argument("--dry-run", action="store_true", help="plan only, collect nothing")
    ap.add_argument("--no-enrich", action="store_true", help="skip reading their websites")
    ap.add_argument("--test-tomtom", action="store_true",
                    help="one small real TomTom search, to check the key (Setup's Test)")
    a = ap.parse_args()
    if a.test_tomtom:
        print(json.dumps(tomtom_check(), ensure_ascii=False))
        return
    print(json.dumps(run(segment=a.segment, area=a.area, limit=a.limit,
                         per_segment=a.per_segment, max_searches=a.max_searches,
                         dry_run=a.dry_run, enrich_sites=not a.no_enrich,
                         service=a.service), indent=2))


if __name__ == "__main__":
    main()
