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
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from coordination import board, dispatch
from . import harvest, leads_sources as ls, services

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

# Where to look. Emirate-level boxes, because they are well known and stable —
# a district box would be a guess, and the district is read off the address the
# POI carries rather than inferred from where we searched.
BBOX = {
    "Dubai":            "24.79,54.89,25.36,55.57",
    "Abu Dhabi":        "24.25,54.28,24.60,54.75",
    "Sharjah":          "25.27,55.35,25.45,55.65",
    "Ajman":            "25.38,55.42,25.48,55.58",
    "Ras Al Khaimah":   "25.65,55.85,25.90,56.10",
    "Al Ain":           "24.13,55.66,24.30,55.86",
    "Fujairah":         "25.08,56.28,25.20,56.40",
}
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
                      ("shop", "tailor"), ("shop", "car_repair")],
    "clinic_small":  [("amenity", "clinic"), ("amenity", "dentist"),
                      ("amenity", "doctors"), ("shop", "optician"),
                      ("amenity", "veterinary")],
    "nursery_local": [("amenity", "kindergarten"), ("amenity", "childcare")],
    "school":        [("amenity", "school"), ("amenity", "college")],
    "hospital":      [("amenity", "hospital")],
    "hotel":         [("tourism", "hotel"), ("tourism", "guest_house")],
    "retail":        [("shop", "clothes"), ("shop", "furniture"),
                      ("shop", "mobile_phone"), ("shop", "jewelry")],
    "showroom":      [("shop", "car"), ("shop", "furniture")],
    "mall":          [("shop", "mall")],
    "local_market":  [("amenity", "marketplace")],
    "bank":          [("amenity", "bank"), ("amenity", "bureau_de_change")],
    "warehouse":     [("building", "warehouse"), ("industrial", "warehouse")],
    "office":        [("office", "company"), ("office", "estate_agent")],
    "coworking":     [("amenity", "coworking_space"), ("office", "coworking")],
    "real_estate_agency": [("office", "estate_agent")],
    "signage":       [("craft", "signmaker"), ("shop", "copyshop")],
    "builder":       [("craft", "builder"), ("craft", "carpenter"),
                      ("shop", "doityourself"), ("shop", "hardware")],
}


def segments_covered():
    """Which of the agency's segments this collector can actually find."""
    return sorted(k for k in OSM_TAGS if k in ls.SEGMENTS)


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

CHAIN_TAGS = ("brand:wikidata", "operator:wikidata", "brand:wikipedia")

# How many times one name has to appear in a single result set before it is a
# chain whatever its tags say. Two branches is a local success story; four is
# a franchise.
CHAIN_REPEATS = 3


def chain_verdict(tags) -> tuple:
    """(is_a_chain, why). Read off the tags, never off a list of brand names."""
    for t in CHAIN_TAGS:
        if (tags.get(t) or "").strip():
            brand = (tags.get("brand") or tags.get("operator") or "").strip()
            return True, (f"a recognised brand ({brand})" if brand
                          else f"tagged {t}")
    return False, ""


def drop_chains(cands, elements=None, repeats=CHAIN_REPEATS):
    """
    (locals, dropped). `dropped` carries the reason for each, because a
    collector that silently halves its own output is indistinguishable from a
    broken one.
    """
    counts = {}
    for c in cands:
        counts[c["company"].strip().lower()] = \
            counts.get(c["company"].strip().lower(), 0) + 1
    keep, dropped = [], []
    for c in cands:
        why = c.pop("_chain_why", "")
        if not why and counts.get(c["company"].strip().lower(), 0) >= repeats:
            why = f"{counts[c['company'].strip().lower()]} branches in one search"
        if why:
            dropped.append({"company": c["company"], "why": why})
        else:
            keep.append(c)
    return keep, dropped


# ---------------------------------------------------------------------------
# THE QUERY
# ---------------------------------------------------------------------------

def build_query(tags, bbox, limit=MAX_RESULTS, timeout=25) -> str:
    """
    Overpass QL for a set of tags inside one box.

    `nwr` rather than `node`: a supermarket is usually mapped as a building
    (a way), not a pin, and asking only for nodes quietly misses most of the
    bigger businesses — which are the ones worth having.
    """
    parts = "".join(f'nwr["{k}"="{v}"]({bbox});' for k, v in tags)
    return (f"[out:json][timeout:{int(timeout)}];({parts});"
            f"out center {int(limit)};")


def _post(url, query, timeout=TIMEOUT):
    req = urllib.request.Request(
        url, data=urllib.parse.urlencode({"data": query}).encode(),
        headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def ask(query, timeout=TIMEOUT) -> dict:
    """
    Run one query against the first mirror that answers.

    Returns {ok, elements, mirror, tried}. Every mirror that refused is named,
    because "OSM did not answer" and "OSM has nothing there" are completely
    different facts and the difference decides whether the owner needs to do
    anything about it.
    """
    tried = []
    for url in MIRRORS:
        host = urllib.parse.urlparse(url).netloc
        try:
            d = _post(url, query, timeout)
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
    bits = [_first(tags, "addr:housenumber"), _first(tags, "addr:street"),
            _first(tags, "addr:district", "addr:suburb", "addr:neighbourhood"),
            _first(tags, "addr:city")]
    return ", ".join(b for b in bits if b)


def _area_of(tags, fallback=""):
    return _first(tags, "addr:district", "addr:suburb", "addr:neighbourhood",
                  "addr:city") or fallback


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
        "company": name[:120],
        "phone": phone[:40],
        "website": site[:200],
        "email": "",                       # OSM rarely has one; harvest gets it
        "area": _area_of(tags, area)[:80],
        "address": _address(tags)[:200],
        "segment": segment,
        "category": _first(tags, "shop", "amenity", "office", "craft", "leisure"),
        "service": svc,
        "service_label": services.label(svc),
        "service_reason": why,
        "source": f"openstreetmap:{el.get('type', '?')}/{el.get('id', '?')}",
    }


def find(segment, area="Dubai", limit=MAX_RESULTS, chains=False) -> dict:
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
    """
    tags = OSM_TAGS.get(segment)
    if not tags:
        return {"ok": False, "error": f"no OSM tags mapped for '{segment}'. "
                                      f"Mapped: {', '.join(segments_covered())}"}
    bbox = BBOX.get(area)
    if not bbox:
        return {"ok": False, "error": f"no box for '{area}'. "
                                      f"Known: {', '.join(BBOX)}"}
    r = ask(build_query(tags, bbox, limit))
    if not r["ok"]:
        return {"ok": False, "error": r["error"], "tried": r["tried"]}
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
        dropped = []
    else:
        cands, dropped = drop_chains(cands)

    # A business with no phone, no site and no address cannot be reached by
    # anyone, today or later. Counted, not filed.
    reachable = [c for c in cands
                 if c["phone"] or c["website"] or c["address"]]
    no_way_in = len(cands) - len(reachable)

    return {"ok": True, "segment": segment, "area": area, "mirror": r["mirror"],
            "on_the_map": named, "unnamed_skipped": unnamed,
            "chains_dropped": dropped, "no_contact_route": no_way_in,
            "found": len(reachable),
            "with_phone": sum(1 for c in reachable if c["phone"]),
            "with_website": sum(1 for c in reachable if c["website"]),
            "candidates": reachable,
            "head": (f"{named} on the map, {len(dropped)} chain(s) dropped, "
                     f"{no_way_in} with no way to reach them, "
                     f"{len(reachable)} worth having")}


# ---------------------------------------------------------------------------
# THE PASS
# ---------------------------------------------------------------------------

def run(agent=AGENT, segment=None, area=None, per_segment=1, dry_run=False,
        chains=False):
    with board.agent_run(agent) as row:
        out = _run(agent, segment, area, per_segment, dry_run, chains)
        row.note(out.get("head") or out.get("error") or "nothing collected")
        return out


def _run(agent, segment, area, per_segment, dry_run, chains=False):
    if not board.sector_enabled(SECTOR):
        return {"ok": False, "error": "Leads is switched off."}
    task = board.next_open_task(SECTOR, agent)
    if not task:
        return {"ok": False, "error": dispatch.why_nothing(SECTOR, agent)}

    segs = [segment] if segment else segments_covered()[:max(1, int(per_segment))]
    areas = [area] if area else list(DEFAULT_AREAS)
    collected, errors = [], []
    for s in segs:
        for a in areas:
            r = find(s, a, chains=chains)
            if not r.get("ok"):
                errors.append(f"{s}/{a}: {r.get('error')}")
                continue
            collected += r["candidates"]
            time.sleep(PAUSE)

    if not collected:
        board.finish_task(task["id"], agent, {"found": 0}, status="done")
        return {"ok": False, "agent": agent, "task": task["id"], "errors": errors,
                "error": ("OpenStreetMap returned nothing for "
                          + ", ".join(segs) + " in " + ", ".join(areas)
                          + (". " + errors[0] if errors else
                             ". That is the data, not a fault — OSM is thinner "
                             "than Google in the UAE."))}

    if dry_run:
        board.finish_task(task["id"], agent, {"found": len(collected),
                                              "dry_run": True}, status="done")
        return {"ok": True, "agent": agent, "task": task["id"], "dry_run": True,
                "candidates": collected[:40], "errors": errors,
                "head": f"{len(collected)} found, nothing stored (dry run)"}

    # Same gate as every other collector: screened, deduped, never contacted.
    res = ls.intake(collected, agent=agent, sector=SECTOR)
    head = (f"{len(res['stored'])} new lead(s) from OpenStreetMap, "
            f"{len(res['duplicates'])} already on file, "
            f"{len(res['refused'])} refused")
    board.finish_task(task["id"], agent, {"stored": len(res["stored"]),
                                          "head": head}, status="done")
    board.log_decision(
        "leads-osm",
        reasoning=(f"Collected {len(collected)} POI(s) from OpenStreetMap across "
                   + ", ".join(segs) + " in " + ", ".join(areas)
                   + " — no Places key needed."),
        expectation="These enrich to contactable leads once harvest reads their sites.",
        confidence=0.6,
        evidence={"stored": [s["id"] for s in res["stored"]]})
    return {"ok": True, "agent": agent, "task": task["id"], "head": head,
            "errors": errors, **res}


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
    ap.add_argument("--area", default="Dubai")
    ap.add_argument("--chains", action="store_true",
                    help="keep the franchises too (dropped by default: they buy "
                         "centrally, usually abroad)")
    ap.add_argument("--per-segment", type=int, default=1)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.probe:
        p = probe()
        if a.json:
            print(json.dumps(p, indent=2))
        else:
            _print_probe(p)
        return 0 if p.get("ok") else 1

    if a.find:
        r = find(a.find, a.area, chains=a.chains)
        if a.json:
            print(json.dumps(r, indent=2))
        elif not r.get("ok"):
            print("could not: " + str(r.get("error")))
        else:
            print(f"{a.find} in {a.area} via {r['mirror']} — {r['head']}")
            print(f"   {r['with_phone']} with a phone, {r['with_website']} with a site")
            if r["chains_dropped"]:
                names = ", ".join(d["company"] for d in r["chains_dropped"][:6])
                print(f"   dropped as chains: {names}"
                      + (" ..." if len(r["chains_dropped"]) > 6 else ""))
            print()
            for c in r["candidates"][:25]:
                print(f"   {c['company'][:34]:36} {c['phone'][:16]:17} "
                      f"{c['area'][:18]:20} {c['website'][:30]}")
        return 0 if r.get("ok") else 1

    r = run(segment=None, area=a.area if a.area != "Dubai" else None,
            per_segment=a.per_segment, dry_run=a.dry_run)
    print(json.dumps(r, indent=2) if a.json else
          (r.get("head") or r.get("error") or "nothing to do"))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
