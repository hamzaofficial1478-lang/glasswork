"""
uae.py — where this business actually works. One list, read by everyone.

"there you mentioned only 3 cities of UAE while we are covering the whole UAE
 and you can collect the leads from any where in the UAE"

Fourteen files had their own copy of ["Dubai", "Abu Dhabi", "Sharjah"]. Not one
of them was wrong on purpose — the first one was written when those were the
three areas the keyword work had named, and every file since copied the line
next to it. The result is a business that serves seven emirates and an agency
that could only see three of them:

  the lead hunter searched three emirates and skipped Ajman entirely;
  the SEO strategist scored "aluminium work ajman" as a general-interest
    phrase rather than a local one, because Ajman was not in its area list;
  the schema markup on his own pages told Google he serves three emirates;
  the outreach mail said three;
  the offer mail he read said three.

So this is the list, and nothing else keeps its own. Adding an emirate here
adds it to every one of those at once, which is the only way a list like this
stays true a year from now.

WHAT HE ACTUALLY SETS

BUSINESS_AREAS in .env overrides everything, because it is his business and
some weeks he only wants Dubai. Unset — the normal case — means the whole
country, which is what he said and what the trade licence allows.

EMIRATES vs CITIES vs DISTRICTS

Three different jobs, so three lists rather than one:

  EMIRATES    the seven. What "areaServed" means, what an address is checked
              against, and the coarsest thing worth writing a page about.
  CITIES      towns that are searched for by name and are not their emirate —
              Al Ain, Khor Fakkan, Ruwais. A person in Al Ain does not search
              "Abu Dhabi".
  DISTRICTS   where a map search actually finds firms. "Business Bay" returns
              a street of offices; "Dubai" returns the whole emirate and the
              same forty names every time.
"""

import os
import re

EMIRATES = (
    "Dubai",
    "Abu Dhabi",
    "Sharjah",
    "Ajman",
    "Umm Al Quwain",
    "Ras Al Khaimah",
    "Fujairah",
)

# Towns people search for by name rather than by emirate. Kept short and real:
# every one of these is somewhere a glazier gets called out to.
CITIES = (
    "Al Ain",           # Abu Dhabi emirate, and nobody there searches "Abu Dhabi"
    "Ruwais",
    "Madinat Zayed",
    "Khor Fakkan",      # Sharjah's east coast, physically separate
    "Kalba",
    "Dibba",
    "Hatta",            # Dubai's mountain enclave
    "Jebel Ali",
)

# Where a MAP search finds businesses. Searching an emirate returns the same
# forty well-known names; searching a district returns the street.
DISTRICTS = (
    # Dubai
    "Business Bay", "Al Quoz", "Al Barsha", "Jumeirah Village Circle",
    "Dubai Marina", "Downtown Dubai", "Deira", "Jumeirah Lake Towers",
    "Dubai Investment Park", "Dubai Silicon Oasis", "Al Qusais", "Mirdif",
    "Dubai South", "Al Ras", "Bur Dubai", "Ras Al Khor", "Umm Ramool",
    # Abu Dhabi
    "Mussafah", "Khalifa City", "Al Reem Island", "Yas Island",
    "Mohammed Bin Zayed City", "Al Mafraq",
    # Sharjah
    "Al Qasimia", "Industrial Area Sharjah", "Muwaileh", "Al Nahda Sharjah",
    "Sharjah Airport Free Zone",
    # the northern emirates, where the industrial estates are
    "Ajman Industrial Area", "Al Jurf Ajman",
    "Al Jazeera Al Hamra", "RAK Industrial Zone",
    "Fujairah Free Zone",
)


def _env(name, default=""):
    """Environment first, then .env. Same precedence as everywhere else."""
    v = os.environ.get(name)
    if v:
        return v
    try:
        from . import settings
        return settings.get(name, default) or default
    except Exception:
        pass
    try:
        from pathlib import Path
        from . import workspace
        p = workspace.env_path_for(name)
        if p.exists():
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, val = line.partition("=")
                if k.strip() == name:
                    return val.strip().strip('"').strip("'") or default
    except Exception:
        pass
    return default


def served() -> list:
    """
    The areas this business works in, as he has set them — the whole country
    unless he has said otherwise.

    Used for what the agency SAYS: areaServed in the schema, the line in an
    email, the areas a page is written for. Not for map searching; use
    search_areas() for that.
    """
    raw = _env("BUSINESS_AREAS", "")
    picked = [a.strip() for a in raw.split(",") if a.strip()]
    return picked or list(EMIRATES)


def served_phrase(joiner="and") -> str:
    """The areas as a person would say them: 'Dubai, Sharjah and Ajman'."""
    areas = served()
    if len(areas) >= len(EMIRATES):
        return "the UAE"
    if len(areas) == 1:
        return areas[0]
    return ", ".join(areas[:-1]) + f" {joiner} " + areas[-1]


def search_areas(limit=None) -> list:
    """
    Where to actually go looking, nearest-value first.

    Districts before emirates, deliberately: a map search for "Dubai" returns
    the same well-known forty every time, and a search for "Al Quoz" returns
    the workshops. The emirates and named towns come after, so a run that gets
    through the districts still widens rather than stopping.
    """
    out = list(DISTRICTS) + [e for e in EMIRATES] + list(CITIES)
    seen, uniq = set(), []
    for a in out:
        k = a.lower()
        if k not in seen:
            seen.add(k)
            uniq.append(a)
    return uniq[:limit] if limit else uniq


def everywhere() -> list:
    """Every place name this file knows, longest first — for matching text."""
    return sorted(set(EMIRATES) | set(CITIES) | set(DISTRICTS),
                  key=lambda s: -len(s))


_WORD = {}


def _re_for(place):
    r = _WORD.get(place)
    if r is None:
        r = re.compile(r"(?<![a-z0-9])" + re.escape(place.lower()) + r"(?![a-z0-9])")
        _WORD[place] = r
    return r


def place_in(text) -> str:
    """The most specific place named in this text, or "" if none is."""
    t = (text or "").lower()
    for place in everywhere():
        if _re_for(place).search(t):
            return place
    return ""


def emirate_in(text) -> str:
    """
    Which emirate this text is about — including via a district or town in it.

    "glass partition mussafah" is Abu Dhabi work and used to read as no area at
    all, which is how a perfectly good local phrase got scored as general
    interest and dropped.
    """
    t = (text or "").lower()
    for e in EMIRATES:
        if _re_for(e).search(t):
            return e
    place = place_in(t)
    return _EMIRATE_OF.get(place, "") if place else ""


def emirate_of(place) -> str:
    """Which emirate a known district or town sits in. "" if we do not know."""
    p = (place or "").strip()
    for e in EMIRATES:
        if p.lower() == e.lower():
            return e
    return _EMIRATE_OF.get(p, "")


def is_uae(text) -> bool:
    """Does this text name somewhere in the country at all?"""
    t = (text or "").lower()
    if re.search(r"(?<![a-z])(uae|u\.a\.e|emirates)(?![a-z])", t):
        return True
    return bool(place_in(t))


# Which emirate each district and town belongs to. Only needed for the ones
# whose name does not contain their emirate.
_EMIRATE_OF = {
    "Business Bay": "Dubai", "Al Quoz": "Dubai", "Al Barsha": "Dubai",
    "Jumeirah Village Circle": "Dubai", "Dubai Marina": "Dubai",
    "Downtown Dubai": "Dubai", "Deira": "Dubai",
    "Jumeirah Lake Towers": "Dubai", "Dubai Investment Park": "Dubai",
    "Dubai Silicon Oasis": "Dubai", "Al Qusais": "Dubai", "Mirdif": "Dubai",
    "Dubai South": "Dubai", "Al Ras": "Dubai", "Bur Dubai": "Dubai",
    "Ras Al Khor": "Dubai", "Umm Ramool": "Dubai", "Jebel Ali": "Dubai",
    "Hatta": "Dubai",
    "Mussafah": "Abu Dhabi", "Khalifa City": "Abu Dhabi",
    "Al Reem Island": "Abu Dhabi", "Yas Island": "Abu Dhabi",
    "Mohammed Bin Zayed City": "Abu Dhabi", "Al Mafraq": "Abu Dhabi",
    "Al Ain": "Abu Dhabi", "Ruwais": "Abu Dhabi", "Madinat Zayed": "Abu Dhabi",
    "Al Qasimia": "Sharjah", "Industrial Area Sharjah": "Sharjah",
    "Muwaileh": "Sharjah", "Al Nahda Sharjah": "Sharjah",
    "Sharjah Airport Free Zone": "Sharjah", "Khor Fakkan": "Sharjah",
    "Kalba": "Sharjah",
    "Ajman Industrial Area": "Ajman", "Al Jurf Ajman": "Ajman",
    "Al Jazeera Al Hamra": "Ras Al Khaimah",
    "RAK Industrial Zone": "Ras Al Khaimah",
    "Fujairah Free Zone": "Fujairah", "Dibba": "Fujairah",
}
