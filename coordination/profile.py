"""
profile.py — what this business is, what it sells, who buys it and where:
one profile per website.

    python -m coordination.profile                 the open website's profile, in short
    python -m coordination.profile --json          all of it, for the office
    echo '{…}' | python -m coordination.profile --save

"now i want to build this program as the public use ... any small business,
 anywhere"

The agency was written for one business: glass and aluminium in the UAE. Its
services, the kinds of business it sells to, the emirates and districts it
searches, the country in every map query and the trade in every prompt were
written into the code. A plumber in Manchester would have been sold shower
glass in Dubai Marina.

So each website now carries its own profile, and the tables everything reads —
services (pipelines/services.py), customer types (pipelines/leads_sources.py)
and places (coordination/uae.py) — come from it.

THE STARTER. The owner's own website keeps exactly what it had: its profile
starts from the glass-and-aluminium-in-the-UAE catalogue those modules still
hold, and anything he changes on "Your business" is laid on top. That is what
a website with no profile gets before accounts exist, and what his website
gets when it is brought into his account. A NEW website starts blank — not
pretending to be a glazier — and the office says what it still needs.

Kept in the website's config folder (business.local.json), sealed with the
account's key like every other setting.
"""
import argparse
import json
import sys

from . import countries, workspace

FILE = "business.local.json"
STARTER = "glass_uae"

# What a stored profile may hold. Anything else is dropped on save.
FIELDS = ("starter", "business", "country", "place", "services", "service_order",
          "fallback_service", "customers", "local_customers", "language", "spelling",
          "currency", "outreach_confirmed")

_CACHE = {}


def _path():
    return workspace.config_path(FILE)


def stored() -> dict:
    """What this website has saved, or {} — never raises."""
    p = _path()
    key = (str(p), p.stat().st_mtime if p.exists() else 0)
    if key in _CACHE:
        return _CACHE[key]
    try:
        d = json.loads(workspace.read_secret(p, "") or "{}")
    except Exception:
        d = {}
    d = d if isinstance(d, dict) else {}
    _CACHE.clear()
    _CACHE[key] = d
    return d


def starter_name(s=None) -> str:
    """Which starter catalogue this website builds on: the glass one, or none."""
    s = stored() if s is None else s
    if "starter" in s:
        return s.get("starter") or ""
    # No profile saved: before accounts this is the owner's own website.
    return STARTER if workspace.legacy() else ""


def is_starter() -> bool:
    return starter_name() == STARTER


def _pick(field, starter_value, empty):
    """
    The website's own value when it has one, else the starter's when it builds
    on one. The modules that HOLD the starter catalogue pass their piece in
    (they are still being imported); everyone else gets it looked up.
    """
    s = stored()
    v = s.get(field)
    if v not in (None, "", [], {}):
        return v
    if starter_name(s) != STARTER:
        return empty
    if starter_value is None:
        starter_value = starter_profile().get(field)
    return starter_value if starter_value is not None else empty


# ---------------------------------------------------------------------------
# what the rest of the program reads
# ---------------------------------------------------------------------------

def services(starter=None) -> dict:
    """service key -> {label, names, signals, buyers, related}."""
    return _pick("services", starter, {})


def service_order(starter=None) -> list:
    order = _pick("service_order", starter, [])
    return list(order) or list(services())


def fallback_service(starter=None) -> str:
    return _pick("fallback_service", starter, "") or (service_order() or [""])[0]


def customers(starter=None) -> dict:
    """customer type -> {why, queries}."""
    return _pick("customers", starter, {})


def local_customers(starter=None) -> set:
    return set(_pick("local_customers", starter, []))


def country(starter="AE") -> str:
    return (_pick("country", starter, "") or "").upper()


def place(starter=None) -> dict:
    """
    {regions, region_word, cities, districts, parent, served, home} — where it
    works. Regions are the big divisions (emirates, states, counties); districts
    are where a map search finds the street; parent says which region each sits in.
    """
    p = _pick("place", starter, {}) or {}
    return {"regions": list(p.get("regions") or []), "region_word": p.get("region_word") or "area",
            "cities": list(p.get("cities") or []), "districts": list(p.get("districts") or []),
            "parent": dict(p.get("parent") or {}), "served": list(p.get("served") or []),
            "home": p.get("home") or ""}


def business(starter=None) -> dict:
    b = _pick("business", starter, {}) or {}
    return {k: b.get(k, "") for k in ("name", "trade", "trade_short", "tagline", "years",
                                      "description", "services_phrase")}


def language() -> str:
    return _pick("language", "en-GB", "") or countries.language(country())


def spelling() -> str:
    lang = language()
    named = _pick("spelling", "British English", "")
    if named:
        return named
    return {"en-US": "American English", "en-GB": "British English"}.get(
        lang, "the business's own language (" + lang + ")")


def outreach() -> dict:
    """{rule, law, confirmed, may_send} — whether this country lets the agency write first."""
    cc = country()
    r = countries.outreach_rule(cc)
    confirmed = bool(stored().get("outreach_confirmed"))
    may = r["rule"] == countries.ALLOWED or (r["rule"] == countries.CONFIRM and confirmed)
    return {**r, "country": cc, "confirmed": confirmed, "may_send": may}


def describe() -> str:
    """One line for a prompt: 'a glass and aluminium … business in the UAE'."""
    trade = business().get("trade") or "small"
    where = countries.in_a_sentence(country()) if country() else "its area"
    return f"a {trade} business in {where}"


def missing() -> list:
    """What a website still needs before every desk can work for it, in his words."""
    out = []
    if not business().get("trade"):
        out.append("what the business does")
    if not country():
        out.append("the country it works in")
    pl = place()
    if not (pl["regions"] or pl["districts"] or pl["cities"]):
        out.append("the areas it serves")
    if not services():
        out.append("what it sells")
    if not customers():
        out.append("which kinds of business buy it")
    return out


# ---------------------------------------------------------------------------
# the starter, whole — for the office to show and edit
# ---------------------------------------------------------------------------

def _glass_business() -> dict:
    return {"name": "", "trade": "glass and aluminium fabrication and installation",
            "trade_short": "glass & aluminium",
            "tagline": "Glass & aluminium, done properly", "years": "",
            "description": "Glass work, aluminium work, glass and wooden doors, pergolas "
                           "and related fabrication and installation.",
            "services_phrase": "glass work, aluminium, doors and pergolas"}


_STARTER = {}


def starter_profile() -> dict:
    """The glass-and-aluminium-in-the-UAE catalogue, as a profile."""
    if _STARTER:
        return _STARTER
    from pipelines import leads_sources, services as svc
    from . import uae
    _STARTER.update({"starter": STARTER, "business": _glass_business(), "country": "AE",
            "place": dict(uae.UAE_PLACE), "services": svc.GLASS_SERVICES,
            "service_order": list(svc.GLASS_ORDER), "fallback_service": "glass_repair",
            "customers": leads_sources.GLASS_SEGMENTS,
            "local_customers": sorted(leads_sources.GLASS_LOCAL),
            "language": "en-GB", "spelling": "British English", "currency": "AED"})
    return _STARTER


def current() -> dict:
    """The whole profile as the program uses it: saved values over the starter."""
    s = stored()
    base = starter_profile() if starter_name(s) == STARTER else {}
    out = {f: (s[f] if s.get(f) not in (None, "", [], {}) else base.get(f))
           for f in FIELDS if f != "starter"}
    out["starter"] = starter_name(s)
    out["outreach"] = outreach()
    out["missing"] = missing()
    out["describe"] = describe()
    return json.loads(json.dumps(out, default=list))


def save(data: dict) -> dict:
    """Save what the 'Your business' page sent, keeping only known fields."""
    if not isinstance(data, dict):
        return {"ok": False, "error": "nothing to save"}
    clean = {k: v for k, v in data.items() if k in FIELDS}
    if "country" in clean:
        cc = str(clean["country"] or "").upper()
        if cc and not countries.name(cc):
            return {"ok": False, "error": f"'{cc}' is not a country code the program knows"}
        clean["country"] = cc
    merged = {**stored(), **clean}
    workspace.write_secret(_path(), json.dumps(merged, indent=2, ensure_ascii=False, default=list))
    _CACHE.clear()
    return {"ok": True, "missing": missing()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="This website's business profile.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--save", action="store_true", help="read a profile as JSON on stdin")
    ap.add_argument("--countries", action="store_true")
    a = ap.parse_args(argv)
    if a.save:
        print(json.dumps(save(json.loads(sys.stdin.read() or "{}"))))
        return 0
    if a.countries:
        print(json.dumps([{"code": c, "name": n} for c, n in countries.all_countries()]))
        return 0
    if a.json:
        print(json.dumps(current(), ensure_ascii=False))
        return 0
    m = missing()
    print(describe()[0].upper() + describe()[1:] + "."
          + (" Still needed: " + ", ".join(m) + "." if m else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
