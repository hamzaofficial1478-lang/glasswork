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
                   "fitout_contractor", "interior_design", "startup",
                   "clinic_small", "signage", "garage"),
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
        "buyers": ("retail", "showroom", "mall", "restaurant", "salon", "pharmacy",
                   "supermarket", "local_market", "laundry", "food_stall", "signage", "garage"),
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
                   "fitout_contractor",
                   "holiday_let", "landlord", "builder", "clinic_small"),
        "related": ("mirror", "glass_door", "glass_repair", "aluminium_windows"),
    },
    "glass_railing": {
        "label": "glass railings and balustrades",
        "names": ("glass railing",
                  "balustrade",
                  "handrail",
                  "glass railings",
                  "banister",
                  "railing",
                  # what a staircase balustrade is actually called on UAE sites
                  "staircase glass",
                  "staircase glasses",
                  "stair glass"),
        "signals": ("villa", "tower", "residence", "staircase", "balcony", "terrace",
                    "developer", "landscap", "resort", "restaurant", "rooftop"),
        "buyers": ("developer", "property_management", "hotel", "restaurant",
                   "construction", "villa_compound",
                   "builder", "landlord", "holiday_let"),
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
        "buyers": ("office", "hotel", "hospital", "clinic", "retail", "bank", "mall",
                   "supermarket", "clinic_small", "food_stall", "laundry"),
        "related": ("shopfront", "glass_partition", "aluminium_doors", "glass_repair"),
    },
    "mirror": {
        "label": "mirrors and mirror walls",
        "names": ("mirror wall",
                  "mirror"),
        "signals": ("salon", "barber", "gym", "fitness", "spa", "dance", "yoga",
                    "beauty", "hotel", "boutique", "studio"),
        "buyers": ("salon", "gym", "spa", "hotel", "retail", "school",
                   "clinic_small", "laundry", "nursery_local"),
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
                   "villa_compound", "warehouse",
                   "builder", "landlord", "holiday_let", "nursery_local", "garage"),
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
                   "property_management",
                   "builder", "signage", "garage"),
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
        "buyers": ("developer", "construction", "mall", "hotel", "facility_management",
                   "builder"),
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
                  "carpentry",
                  "wooden work",
                  "wooden ceiling",
                  "wooden interior",
                  "wooden flooring"),
        "signals": ("hotel", "apartment", "villa", "residence", "school", "clinic",
                    "interior", "joinery", "furniture", "fit out", "fit-out"),
        "buyers": ("hotel", "fitout_contractor", "interior_design", "developer",
                   "property_management", "school",
                   "builder", "holiday_let", "landlord"),
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
                   "developer",
                   "food_stall", "builder", "landlord", "nursery_local"),
        "related": ("glass_railing", "skylight", "aluminium_windows", "cladding"),
    },
    # ---- FOUR THE CATALOGUE DID NOT KNOW, AND THE BUSINESS PLAINLY SELLS ---
    #
    # Read straight off worlddealtechnicalservices.com. The site's own headings
    # say Folding Door, Lamination Glass, Sign Board and Glass Installation, in
    # three variants each, and every one came back "uncatalogued" — so the
    # agency could not target them, write about them, or pitch them to a lead.
    # 'Sign Board' is in the site's own title tag, and there is a whole Leads
    # segment for signage firms, which made its absence here the odd one out.
    "folding_doors": {
        "label": "folding and bi-fold doors",
        "names": ("folding door",
                  "bifold door",
                  "bi-fold door",
                  "sliding folding door",
                  "accordion door",
                  "folding partition",
                  "concertina door",
                  "aluminium folding",
                  "aluminum folding",
                  "wooden folding",
                  "steel folding",
                  "glass folding"),
        "signals": ("villa", "balcony", "terrace", "restaurant", "cafe", "hotel",
                    "office", "meeting room", "showroom", "patio", "garden"),
        "buyers": ("restaurant", "hotel", "office", "coworking", "villa_compound",
                   "builder", "landlord", "holiday_let", "retail", "food_stall"),
        "related": ("aluminium_doors", "glass_partition", "glass_door", "pergola"),
    },
    "laminated_glass": {
        "label": "laminated and safety glass",
        "names": ("laminated glass",
                  "lamination glass",
                  "glass lamination",
                  "safety glass",
                  "toughened glass",
                  "tempered glass",
                  "double glazing",
                  "insulated glass",
                  "acoustic glass",
                  "privacy glass",
                  "frosted glass",
                  "tinted glass",
                  "obscure glass"),
        "signals": ("school", "nursery", "hospital", "clinic", "bank", "villa",
                    "balcony", "staircase", "shopfront", "security", "acoustic"),
        "buyers": ("school", "nursery_local", "hospital", "clinic_small", "bank",
                   "retail", "hotel", "builder", "developer", "landlord"),
        "related": ("glass_partition", "glass_railing", "shopfront", "glass_repair"),
    },
    "sign_board": {
        "label": "sign boards and display boards",
        "names": ("sign board",
                  "signboard",
                  "signage",
                  "display board",
                  "acrylic letter",
                  "3d letter",
                  "light box",
                  "name board",
                  "shop sign",
                  "billboard",
                  "outdoor billboard",
                  "hoarding"),
        "signals": ("shop", "retail", "restaurant", "cafe", "salon", "clinic",
                    "showroom", "office", "mall", "branding", "advertising"),
        "buyers": ("signage", "retail", "restaurant", "food_stall", "salon",
                   "supermarket", "clinic_small", "gym", "showroom", "startup",
                   "local_market", "laundry", "garage"),
        "related": ("shopfront", "cladding", "glass_repair"),
    },
    "glass_installation": {
        "label": "glass and aluminium installation",
        "names": ("glass installation",
                  "aluminium installation",
                  "aluminum installation",
                  "glass fixing",
                  "glass fitting",
                  "glazing installation",
                  "office glass",
                  "office glasses",
                  "bath glass",
                  "bath glasses"),
        "signals": ("install", "fit", "fix", "supply and fix", "office", "villa",
                    "staircase", "bathroom", "apartment", "building"),
        "buyers": ("office", "coworking", "builder", "landlord", "developer",
                   "property_management", "holiday_let", "villa_compound",
                   "clinic_small", "startup"),
        "related": ("glass_partition", "glass_railing", "shower_glass",
                    "glass_repair"),
    },
    # Off his own site, which sold it under two names the agency did not know:
    # "Aluminum Interior Designs" and "Aluminum Interior". Until a service is
    # catalogued the agency cannot target a segment with it or write about it,
    # so a line he actually sells was invisible to every sector.
    "aluminium_interior": {
        "label": "aluminium interior fit-out and cladding",
        "names": ("aluminium interior",
                  "aluminum interior",
                  "aluminium interior design",
                  "aluminum interior design",
                  "aluminium interior designs",
                  "aluminum interior designs",
                  "aluminium cladding",
                  "aluminum cladding",
                  "aluminium false ceiling",
                  "aluminum false ceiling",
                  "aluminium interior work",
                  "aluminum interior work"),
        "signals": ("interior", "cladding", "ceiling", "fit-out", "fitout",
                    "office", "villa", "lobby", "reception", "refurb"),
        "buyers": ("office", "coworking", "hotel", "retail", "showroom",
                   "mall", "restaurant", "clinic_small", "bank", "developer",
                   "fitout_contractor", "interior_design", "property_management",
                   "villa_compound", "landlord", "startup"),
        "related": ("glass_partition", "aluminium_windows", "glass_installation",
                    "wooden_doors"),
    },
    "skylight": {
        "label": "skylights and glass roofs",
        "names": ("skylight",
                  "glass roof",
                  "atrium",
                  "roof glazing",
                  "roof glass"),
        "signals": ("mall", "atrium", "showroom", "villa", "resort", "museum",
                    "airport", "warehouse", "clinic"),
        "buyers": ("mall", "developer", "hotel", "showroom", "construction",
                   "local_market", "supermarket", "builder"),
        "related": ("curtain_wall", "pergola", "cladding", "glass_repair"),
    },
    # THE SEO DESK TURNED DOWN A REAL SERVICE BECAUSE THIS CATALOGUE HAD NOT
    # HEARD OF IT.
    #
    #   "'swimming pools' is not a service this business sells, so there is
    #    nobody to go and look for."
    #   "the swimming pool is in our service that swimming pool safety glass"
    #
    # The keyword research was right and the catalogue was wrong. Pool fencing
    # and infinity-edge walls are laminated safety glass with a specific code
    # requirement behind them, they are bought by exactly the buyers already
    # listed elsewhere here, and they are among the better-paying glass jobs in
    # this country. A phrase naming a real service must never come back as "we
    # do not sell that".
    "pool_glass": {
        "label": "swimming pool safety glass and fencing",
        "names": ("swimming pool glass",
                  "pool glass",
                  "pool fencing",
                  "pool fence",
                  "swimming pool fence",
                  "glass pool fence",
                  "pool safety glass",
                  "swimming pool safety glass",
                  "infinity pool glass",
                  "pool balustrade",
                  "swimming pool",
                  "frameless pool fence"),
        "signals": ("pool", "swimming", "villa", "resort", "hotel", "spa",
                    "compound", "leisure", "club", "landscap", "gym",
                    "holiday home", "chalet"),
        "buyers": ("hotel", "developer", "villa_compound", "property_management",
                   "landlord", "holiday_let", "gym", "construction", "builder",
                   "landscaping"),
        "related": ("glass_railing", "laminated_glass", "pergola", "glass_repair"),
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
                   "hotel", "hospital", "school", "mall",
                   "holiday_let", "landlord", "supermarket", "food_stall", "laundry", "local_market", "clinic_small", "nursery_local", "signage", "builder", "garage"),
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


# Words that turn a glazing term into a different product: a film, a sticker or
# a wrap is applied to somebody's existing glass, not fabricated and fitted.
_TREATMENT_RE = re.compile(r"\b(film|films|sticker|stickers|vinyl|wrap|wraps|"
                           r"decal|decals|tint(?:ing)? film)\b", re.I)


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
    # A SURFACE TREATMENT IS NOT THE GLAZING. "Frosted glass" is a panel this
    # business makes; "frosted glass film" is a vinyl somebody sticks on an
    # existing pane, and they are different jobs with different prices. Adding
    # 'frosted glass' to the catalogue made the matcher start swallowing the
    # film too, which would have had the agency offering a pane to somebody
    # asking for a sticker. Unless a service names one of these outright, a
    # phrase carrying one is not ours.
    if _TREATMENT_RE.search(t) and not any(
            _TREATMENT_RE.search(n) for s in SERVICES.values()
            for n in s.get("names", ())):
        return (None, "")
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


def buyers_of(service_key: str) -> list:
    """
    The reverse question, and the one the owner asked for: given a service we
    want work on, WHICH KIND OF BUSINESS buys it.

    services_for_segment answers "I found a hotel, what does it need?" — useful
    once you already have the lead. This answers "we want shower-glass jobs, who
    do we go and look for?", which is how you actually fill a week. Same table,
    read the other way, so the two can never drift apart.
    """
    s = SERVICES.get((service_key or "").strip().lower())
    return list(s["buyers"]) if s else []


def resolve_service(text: str) -> str:
    """
    A service key from whatever the owner typed. 'shower glass', 'Frameless
    Shower', 'shower_glass' all land on the same job.

    Exact key first, then the service's own names, then a loose contains. He
    should not have to learn our internal spelling to ask for a week of
    shower-glass work.
    """
    t = (text or "").strip().lower().replace(" ", "_")
    if t in SERVICES:
        return t
    plain = (text or "").strip().lower()
    for k, s in SERVICES.items():
        if plain == s["label"].lower() or plain in [n.lower() for n in s["names"]]:
            return k
    for k, s in SERVICES.items():
        if plain and (plain in s["label"].lower()
                      or any(plain in n.lower() or n.lower() in plain for n in s["names"])):
            return k
    return ""


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
