"""
services.py — what the business actually sells, in one place.

Everything downstream reads this file. The Hunter uses it to decide which
service a business it just found is likely to need; the mail composer uses it to
write about THAT service and to mention the related ones underneath. Keeping it
in one module is the point: add a service here and both the targeting and the
email pick it up, with no second list to forget.

Each service carries five things:

  label      what we call it in writing, in the owner's words
  names      what the service is CALLED — the words a website uses for it
  signals    words that, in a business's name/category/site, say they NEED it
  buyers     the segments that buy it most (see SEGMENTS in leads_sources)
  related    what to mention underneath in the same email

'names' and 'signals' answer different questions and must not be confused. A
hotel is a *signal* for shower glass — it tells you who needs the job. "Shower
enclosure" is a *name* — it tells you what the job is. Reading our own website
needs names; reading a prospect's listing needs signals. Trying to do the first
with the second is exactly how the site reader failed to recognise "Frameless
Shower Enclosures" as a service we sell.

'related' is doing real work. A hotel that needs shower glass almost always has
mirrors, balustrades and aluminium windows too, and a mail that names the one
thing and lists the rest wins more of the job than a mail that pitches a
catalogue. It is also how the agency avoids the classic mistake of sending a
restaurant a pergola pitch and nothing else.
"""

import re
from functools import lru_cache

SERVICES = {
    "glass_partition": {
        "label": "office glass partitions",
        "names": ("glass partition",
                  "office partition",
                  "glazed partition",
                  "partition"),
        "signals": ("office", "coworking", "co-working", "business centre", "business center",
                    "corporate", "consultanc", "law firm", "accounting", "clinic", "bank",
                    "insurance", "agency", "studio", "workspace", "fit out", "fit-out",
                    "interior design", "design studio", "designer"),
        "buyers": ("office", "coworking", "clinic", "bank", "real_estate_agency",
                   "fitout_contractor", "interior_design", "startup"),
        "related": ("glass_door", "aluminium_doors", "mirror", "glass_repair"),
    },
    "shopfront": {
        "label": "glass shopfronts and storefronts",
        "names": ("shopfront",
                  "shop front",
                  "storefront",
                  "store front",
                  "shop fitting",
                  "shopfitting"),
        "signals": ("shop", "store", "retail", "boutique", "showroom", "outlet",
                    "pharmacy", "supermarket", "mall", "kiosk", "bakery"),
        "buyers": ("retail", "showroom", "mall", "restaurant", "salon", "pharmacy"),
        "related": ("glass_door", "aluminium_doors", "glass_repair", "cladding"),
    },
    "shower_glass": {
        "label": "frameless shower glass",
        "names": ("shower glass",
                  "shower enclosure",
                  "shower screen",
                  "shower cabin",
                  "shower cubicle",
                  "shower"),
        "signals": ("hotel", "resort", "spa", "hostel", "apartment", "residence",
                    "villa", "bathroom", "sanitary", "wellness", "hospital"),
        "buyers": ("hotel", "spa", "hospital", "property_management", "developer",
                   "fitout_contractor"),
        "related": ("mirror", "glass_door", "glass_repair", "aluminium_windows"),
    },
    "glass_railing": {
        "label": "glass railings and balustrades",
        "names": ("glass railing",
                  "balustrade",
                  "handrail",
                  "glass railings",
                  "banister",
                  "railing"),
        "signals": ("villa", "tower", "residence", "staircase", "balcony", "terrace",
                    "developer", "landscap", "resort", "restaurant", "rooftop"),
        "buyers": ("developer", "property_management", "hotel", "restaurant",
                   "construction", "villa_compound"),
        "related": ("pergola", "glass_partition", "aluminium_windows", "glass_repair"),
    },
    "glass_door": {
        "label": "glass doors, sliding and automatic",
        "names": ("glass door",
                  "sliding door",
                  "automatic door",
                  "frameless door",
                  "swing door",
                  "patch fitting"),
        "signals": ("entrance", "lobby", "reception", "clinic", "hospital", "bank",
                    "office", "hotel", "supermarket", "showroom", "airport"),
        "buyers": ("office", "hotel", "hospital", "clinic", "retail", "bank", "mall"),
        "related": ("shopfront", "glass_partition", "aluminium_doors", "glass_repair"),
    },
    "mirror": {
        "label": "mirrors and mirror walls",
        "names": ("mirror wall",
                  "mirror"),
        "signals": ("salon", "barber", "gym", "fitness", "spa", "dance", "yoga",
                    "beauty", "hotel", "boutique", "studio"),
        "buyers": ("salon", "gym", "spa", "hotel", "retail", "school"),
        "related": ("shower_glass", "glass_partition", "glass_repair", "glass_door"),
    },
    "aluminium_windows": {
        "label": "aluminium windows",
        "names": ("aluminium window",
                  "aluminum window",
                  "upvc window",
                  "sliding window",
                  "window"),
        "signals": ("villa", "apartment", "building", "residence", "tower", "compound",
                    "developer", "contracting", "school", "warehouse", "labour camp"),
        "buyers": ("developer", "property_management", "construction", "school",
                   "villa_compound", "warehouse"),
        "related": ("aluminium_doors", "glass_repair", "cladding", "skylight"),
    },
    "aluminium_doors": {
        "label": "aluminium doors",
        "names": ("aluminium door",
                  "aluminum door",
                  "upvc door"),
        "signals": ("warehouse", "factory", "workshop", "garage", "storage", "logistics",
                    "building", "school", "clinic", "office", "compound"),
        "buyers": ("warehouse", "construction", "school", "office", "facility_management",
                   "property_management"),
        "related": ("aluminium_windows", "glass_door", "glass_repair", "cladding"),
    },
    "cladding": {
        "label": "aluminium cladding and façade panels",
        "names": ("cladding",
                  "acp",
                  "aluminium composite",
                  "composite panel",
                  "facade panel",
                  "façade panel"),
        "signals": ("tower", "building", "façade", "facade", "developer", "contracting",
                    "hotel", "mall", "showroom", "petrol", "signage"),
        "buyers": ("developer", "construction", "mall", "hotel", "facility_management"),
        "related": ("curtain_wall", "aluminium_windows", "skylight", "glass_repair"),
    },
    "curtain_wall": {
        "label": "curtain walling and structural glazing",
        "names": ("curtain wall",
                  "curtain walling",
                  "structural glazing",
                  "spider glazing",
                  "unitised"),
        "signals": ("tower", "high rise", "high-rise", "corporate", "headquarters",
                    "developer", "contracting", "consultant", "architect"),
        "buyers": ("developer", "construction", "architect", "office"),
        "related": ("cladding", "aluminium_windows", "skylight", "glass_repair"),
    },
    "wooden_doors": {
        "label": "wooden doors and joinery",
        "names": ("wooden door",
                  "wood door",
                  "timber door",
                  "joinery",
                  "carpentry"),
        "signals": ("hotel", "apartment", "villa", "residence", "school", "clinic",
                    "interior", "joinery", "furniture", "fit out", "fit-out"),
        "buyers": ("hotel", "fitout_contractor", "interior_design", "developer",
                   "property_management", "school"),
        "related": ("glass_door", "glass_partition", "mirror", "aluminium_doors"),
    },
    "pergola": {
        "label": "pergolas, shades and canopies",
        "names": ("pergola",
                  "gazebo",
                  "shade structure",
                  "car park shade",
                  "awning",
                  "canopy",
                  "shade"),
        "signals": ("restaurant", "cafe", "café", "resort", "villa", "garden", "landscap",
                    "pool", "terrace", "rooftop", "nursery", "school", "compound"),
        "buyers": ("restaurant", "hotel", "school", "villa_compound", "property_management",
                   "developer"),
        "related": ("glass_railing", "skylight", "aluminium_windows", "cladding"),
    },
    "skylight": {
        "label": "skylights and glass roofs",
        "names": ("skylight",
                  "glass roof",
                  "atrium",
                  "roof glazing"),
        "signals": ("mall", "atrium", "showroom", "villa", "resort", "museum",
                    "airport", "warehouse", "clinic"),
        "buyers": ("mall", "developer", "hotel", "showroom", "construction"),
        "related": ("curtain_wall", "pergola", "cladding", "glass_repair"),
    },
    "glass_repair": {
        "label": "glass and aluminium repair and replacement",
        "names": ("glass repair",
                  "glass replacement",
                  "glass fixing",
                  "glazing repair",
                  "maintenance",
                  "repair"),
        "signals": ("facility", "maintenance", "management", "property", "handover",
                    "snagging", "building services", "hospital", "school", "hotel"),
        "buyers": ("facility_management", "property_management", "real_estate_agency",
                   "hotel", "hospital", "school", "mall"),
        "related": ("glass_partition", "aluminium_windows", "glass_door", "shopfront"),
    },
}

# What the whole list is called on a website and in an email footer. Ordered the
# way the owner would say them, not alphabetically.
ALL_SERVICE_LABELS = [SERVICES[k]["label"] for k in
                      ("glass_partition", "shopfront", "glass_door", "shower_glass",
                       "glass_railing", "mirror", "aluminium_windows", "aluminium_doors",
                       "cladding", "curtain_wall", "wooden_doors", "pergola",
                       "skylight", "glass_repair")]


@lru_cache(maxsize=None)
def _signal_re(signal: str):
    """
    Match a signal at a word BOUNDARY, not anywhere in the string.

    A plain substring test reads "small business" as a shopping mall and
    "restore" as a retail store — the same trap the SEO scope check fell into
    with "uk" inside "sukkari". The start is anchored and the end deliberately
    is not, so prefix signals like "landscap" still catch "landscaping".
    """
    return re.compile(r"\b" + re.escape(signal))


def label(service_key: str) -> str:
    s = SERVICES.get(service_key)
    return s["label"] if s else (service_key or "").replace("_", " ")


def related_labels(service_key: str, limit: int = 4) -> list:
    """The services to name underneath the main one, in an email."""
    s = SERVICES.get(service_key)
    if not s:
        return ALL_SERVICE_LABELS[:limit]
    return [label(k) for k in s["related"][:limit]]


def match_by_name(text) -> tuple:
    """
    Which service is this phrase the NAME of? (key, matched_name) or (None, "").

    Used when reading a site that lists what it sells, rather than guessing what
    a prospect needs. The longest matching name wins, so "aluminium door" beats
    the bare "door" and "shower enclosure" beats "shower" — the specific name is
    always the more informative one.
    """
    t = (text or "").lower()
    best = (0, None, "")
    for key, s in SERVICES.items():
        for n in s.get("names", ()):
            if len(n) > best[0] and _signal_re(n).search(t):
                best = (len(n), key, n)
    return (best[1], best[2])


def services_for_segment(segment: str) -> list:
    """Which services this kind of business buys, most likely first."""
    seg = (segment or "").strip().lower()
    return [k for k, s in SERVICES.items() if seg in s["buyers"]]


def match_service(*texts, segment: str = "") -> tuple:
    """
    Work out which service a business most likely needs, from whatever we know
    about it: its name, its category on the map, the words on its site.

    Returns (service_key, why). The segment is used as the tie-breaker rather
    than the primary signal, because a company's own words beat our guess about
    its category: "Al Noor Interiors — shopfitting and joinery" tells us more
    than the fact that Maps filed it under 'general contractor'.
    """
    blob = " ".join(str(t or "") for t in texts).lower()
    scored = []
    for key, s in SERVICES.items():
        hits = [w for w in s["signals"] if _signal_re(w).search(blob)]
        if not hits:
            continue
        score = len(hits) + (1.5 if (segment or "").lower() in s["buyers"] else 0)
        scored.append((score, key, hits))
    if scored:
        # Ties are common and they matter. The word "hotel" appears in the
        # signals for shower glass, mirrors, doors, cladding and half a dozen
        # others, so a hotel scores the same on all of them — and sorting the
        # tie alphabetically pitched a hotel on aluminium cladding, which is
        # not the job it is most likely buying. Break ties on the order services
        # are declared above, which is the order the owner would name them.
        order = list(SERVICES)
        scored.sort(key=lambda x: (-x[0], order.index(x[1])))
        _, key, hits = scored[0]
        return key, f"their listing mentions {', '.join(hits[:3])}"

    by_segment = services_for_segment(segment)
    if by_segment:
        return by_segment[0], f"typical for a {segment.replace('_', ' ')}"
    return "glass_repair", "no strong signal — defaulted to repair and replacement, "\
                           "which every building needs eventually"
