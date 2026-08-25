"""
leads_sources.py — WHO the agency sells to and WHERE to find them, plus the gate
every lead passes on its way into the cabinet.

    python -m pipelines.leads_sources        (python3 on macOS/Linux)

This file used to describe five contractor segments, on the reasoning that
contractors buy the most glass per job. They do — and they also squeeze hardest
and pay slowest, and meanwhile every hotel, clinic, salon, restaurant, school,
showroom, mall and warehouse in the UAE was out of scope for a firm that fits
glass into all of them. That was not a strategy anybody chose; it was the first
list written down, left in place. The list below is the market.

Each segment carries three things: why they buy, the terms that find them on a
map, and the services they actually need — so a lead never arrives without an
answer to "what do we write to them about". That is what services.py is for.

The gates, in the order they run:

  screen_source()  is this somewhere we should be looking at all?
  screen_lead()    is this a real, reachable UAE business we can help?
  intake()         dedup, then store — and never contact.

Re-contacting a firm you have already approached burns the relationship and the
sending domain, which is why the dedup check runs BEFORE the store, not after.
Nothing in this module can email anybody, by construction; the collector that
goes and gets the leads is leads_hunt.py, and outreach is a separate job again,
behind Security's clearance.
"""
import json
import os
import re
import sqlite3

from coordination import board, dispatch
from . import services

# Where to look. The keyword work named the first six; the rest are simply where
# the money is in the UAE, and leaving them out was costing us reach for no
# reason. Abu Dhabi and Sharjah are in because the business serves all three
# emirates, and a target list that only knows Dubai is half a target list.
DISTRICTS = ["Business Bay", "Al Quoz", "Al Barsha", "Jumeirah Village Circle",
             "Dubai Marina", "Downtown Dubai", "Deira", "Jumeirah Lake Towers",
             "Dubai Investment Park", "Dubai Silicon Oasis", "Al Qusais", "Mirdif",
             "Abu Dhabi", "Al Ain", "Sharjah", "Ajman", "Ras Al Khaimah"]

# WHO WE SELL TO
#
# This list used to hold five contractor-ish segments, on the theory that
# contractors buy the most glass. They do buy the most per job — and they also
# squeeze the price hardest and pay slowest. The firm's actual work comes from
# every kind of building in the UAE: hotels, clinics, salons, restaurants,
# schools, offices, showrooms, malls, warehouses. Narrowing to five was a
# strategy nobody chose; it was just the first list written down.
#
# Each segment carries the search terms that find it on a map, because the
# Hunter needs a real query, not a category label.
SEGMENTS = {
    # --- the trade, who buy in volume ---------------------------------------
    "fitout_contractor": {
        "why": "Fit-out contractors — partitions, shopfronts and doors on every single job.",
        "queries": ("fit out companies", "interior fit out contractor", "shopfitting company"),
    },
    "interior_design": {
        "why": "Interior-design studios — they specify the glazing before the job is tendered.",
        "queries": ("interior design company", "interior designer", "design studio"),
    },
    "developer": {
        "why": "Developers — balustrades, façades, windows and skylights at scale.",
        "queries": ("property developer", "real estate developer"),
    },
    "construction": {
        "why": "Main contractors — curtain walling, cladding and aluminium packages.",
        "queries": ("construction company", "building contractor", "general contracting"),
    },
    "architect": {
        "why": "Architecture and engineering consultants — they draw the glazing spec.",
        "queries": ("architecture firm", "engineering consultant"),
    },
    "facility_management": {
        "why": "FM firms — repeat repair and replacement across whole portfolios.",
        "queries": ("facility management company", "building maintenance company"),
    },
    "property_management": {
        "why": "Property managers — snagging, handover fixes and tenant-change works.",
        "queries": ("property management company", "owners association management"),
    },
    "real_estate_agency": {
        "why": "Estate agencies — they see every unit that needs work before it is let.",
        "queries": ("real estate agency", "real estate brokers"),
    },

    # --- buildings that buy for themselves ----------------------------------
    "hotel": {
        "why": "Hotels and resorts — shower glass, mirrors, balustrades, doors, refurb cycles.",
        "queries": ("hotel", "resort", "hotel apartments"),
    },
    "hospital": {
        "why": "Hospitals and medical centres — partitions, automatic doors, hygiene glass.",
        "queries": ("hospital", "medical centre", "dental clinic", "polyclinic"),
    },
    "school": {
        "why": "Schools and nurseries — windows, safety glass, shades and canopies.",
        "queries": ("private school", "nursery school", "training institute"),
    },
    "office": {
        "why": "Offices and corporates — partitions are the single biggest repeat line.",
        "queries": ("office building", "corporate office", "law firm", "accounting firm"),
    },
    "coworking": {
        "why": "Co-working operators — they re-partition constantly as tenants change.",
        "queries": ("coworking space", "business centre"),
    },
    "retail": {
        "why": "Shops and boutiques — shopfronts, doors and display glass.",
        "queries": ("retail shop", "boutique", "supermarket", "pharmacy"),
    },
    "showroom": {
        "why": "Showrooms — big spans of glass, and they replace it when the brand changes.",
        "queries": ("car showroom", "furniture showroom", "showroom"),
    },
    "mall": {
        "why": "Malls — skylights, atria, shopfront churn as units turn over.",
        "queries": ("shopping mall", "shopping centre"),
    },
    "restaurant": {
        "why": "Restaurants and cafés — pergolas and shopfronts, and they refit often.",
        "queries": ("restaurant", "cafe", "coffee shop", "cloud kitchen"),
    },
    "salon": {
        "why": "Salons and barbers — mirror walls and glass frontage, on a short refit cycle.",
        "queries": ("beauty salon", "barber shop", "nail salon"),
    },
    "gym": {
        "why": "Gyms and studios — mirror walls, glass partitions, heavy wear.",
        "queries": ("gym", "fitness centre", "yoga studio", "padel club"),
    },
    "spa": {
        "why": "Spas and wellness centres — shower glass and mirrors, replaced often.",
        "queries": ("spa", "wellness centre", "massage centre"),
    },
    "bank": {
        "why": "Banks and exchanges — security glass, automatic doors, branch refits.",
        "queries": ("bank branch", "exchange house"),
    },
    "warehouse": {
        "why": "Warehouses and light industry — aluminium doors, windows, partitioned offices.",
        "queries": ("warehouse", "logistics company", "factory"),
    },
    "villa_compound": {
        "why": "Villa compounds and staff accommodation — windows, railings, shades at volume.",
        "queries": ("villa compound", "staff accommodation", "residential compound"),
    },
    "startup": {
        "why": "Studios, agencies and startups — first fit-out of a new office.",
        "queries": ("marketing agency", "photography studio", "software company"),
    },

    # --- LOCAL AND STREET-LEVEL ---------------------------------------------
    #
    # The owner's correction, and it was a fair one: "on maps most of the data
    # is related to agencies, businesses and majors, while what I want is to
    # target the locals". The segments above are mostly buildings and firms
    # with a procurement process. These are the ones where the person who
    # decides is the person standing behind the counter, and they buy the
    # smaller, faster, better-margin jobs — a shopfront, a shower screen, a
    # sign board, a pergola over the terrace.
    "supermarket": {
        "why": "Grocers and supermarkets — shopfronts, cold-room glass, sign boards, "
               "and they refit a unit at a time rather than tendering it.",
        "queries": ("supermarket", "grocery store", "hypermarket", "mini mart",
                    "baqala"),
    },
    "local_market": {
        "why": "Souks, community markets and small malls — dozens of small units "
               "behind one landlord, each with its own frontage.",
        "queries": ("souk", "local market", "community mall", "market place",
                    "fish market", "vegetable market"),
    },
    "signage": {
        "why": "Sign and advertising companies — they SUBCONTRACT the aluminium "
               "framing, acrylic and display glass they cannot make themselves. "
               "One relationship here is repeat work every month.",
        "queries": ("signage company", "sign board maker", "advertising company",
                    "billboard company", "printing press", "branding company"),
    },
    "holiday_let": {
        "why": "Holiday-home and short-let operators — they turn units over "
               "constantly and fix what the last guest broke, out of their own "
               "pocket, this week.",
        "queries": ("holiday homes", "short term rental", "vacation homes",
                    "serviced apartments", "guest house"),
    },
    "landlord": {
        "why": "Small landlords and building owners — the ones who own two or "
               "three buildings and manage them themselves, so there is nobody "
               "to get past.",
        "queries": ("building owner", "landlord", "rental apartments",
                    "flats for rent", "labour accommodation"),
    },
    "builder": {
        "why": "Small builders and villa contractors — the ones who build one "
               "villa at a time and buy the glazing themselves rather than "
               "putting it out to tender.",
        "queries": ("villa contractor", "building renovation", "home renovation",
                    "villa construction", "swimming pool contractor",
                    "landscaping company"),
    },
    "laundry": {
        "why": "Laundries, tailors, key-cutters and the rest of the parade — "
               "small shopfronts, cheap to win, and they talk to each other.",
        "queries": ("laundry", "dry cleaners", "tailor shop", "mobile repair shop",
                    "key cutting"),
    },
    # ITS OWN SEGMENT, because it was inside 'laundry' and should not have been.
    # A Sharjah search for laundries came back Wheelocity Garage, Alrehan
    # Garage, Al Jouda Auto Repairing and eleven more workshops — fourteen of
    # twenty-six results. They are perfectly good local leads, and lumping them
    # in with laundries meant the mail would have offered a garage a mirror
    # wall. What a garage actually buys is the roller-shutter frontage, the
    # office partition and the toughened glass in the door.
    "garage": {
        "why": "Car workshops, spare-part shops and car washes — big aluminium "
               "frontages, roller doors, and a partitioned office at the front. "
               "The owner is on site and decides on the spot.",
        "queries": ("car repair garage", "auto repair", "car workshop",
                    "spare parts shop", "car wash", "tyre shop",
                    "auto electrical repair"),
    },
    "clinic_small": {
        "why": "Single-branch clinics — physio, optical, veterinary, dental — "
               "who need partitions and hygiene glass without a procurement "
               "department in the way.",
        "queries": ("physiotherapy clinic", "optical shop", "veterinary clinic",
                    "dermatology clinic", "homeopathy clinic"),
    },
    "nursery_local": {
        "why": "Home nurseries and small learning centres — safety glass, "
               "railings and shades, and a very short decision chain.",
        "queries": ("home nursery", "day care", "tuition centre", "learning centre"),
    },
    "food_stall": {
        "why": "Cafeterias, juice shops, bakeries and shawarma counters — the "
               "densest shopfront segment in the UAE and the one nobody else "
               "bothers to call.",
        "queries": ("cafeteria", "juice shop", "bakery", "shawarma", "sweets shop",
                    "butchery", "catering kitchen"),
    },
}

# WHERE WE FIND THEM
#
# The workhorse is the first entry. Google Maps is where every UAE business is
# listed with its category, area, phone and website, which is exactly the four
# things the Hunter needs — and the Places API is that same data through the
# front door. Point a headless browser at maps.google.com instead and you are
# breaking Google's terms and getting the IP blocked inside a week; the API
# costs a few dollars a thousand and does not go down. Same data, no ban.
#
# Maps does not publish email addresses. Nobody's does. The email comes from the
# business's OWN website, off the contact page where they printed it so people
# would write to them. That second hop is where real, working addresses come
# from, and it is why the two entries below run as a pair.
SOURCES = [
    {"name": "Google Maps (Places API) by category + area", "kind": "maps",
     "segments": list(SEGMENTS), "business_data": True, "access": "api key",
     "why": "Every UAE business, with category, area, phone and website. The Hunter's "
            "main source — it covers every segment we sell to, not a chosen few."},
    {"name": "The business's own website contact page", "kind": "website",
     "segments": list(SEGMENTS), "business_data": True, "access": "public",
     "why": "Where the company itself publishes info@/sales@ for people to write to. "
            "Maps has no email; this is the hop that turns a listing into a lead."},
    {"name": "Dubai Chamber of Commerce member directory", "kind": "registry",
     "segments": ["fitout_contractor", "interior_design", "facility_management",
                  "construction", "warehouse"], "business_data": True,
     "access": "public search", "why": "Licensed firms with a trade category — the cleanest list in Dubai."},
    {"name": "DED trade-licence listings", "kind": "registry",
     "segments": ["fitout_contractor", "interior_design", "facility_management",
                  "retail", "restaurant"], "business_data": True,
     "access": "public search", "why": "Confirms a firm is real and still trading before anyone approaches it."},
    {"name": "LinkedIn company search (industry + emirate)", "kind": "social",
     "segments": ["fitout_contractor", "interior_design", "developer",
                  "facility_management", "office", "startup"], "business_data": True,
     "access": "public company pages", "why": "Company pages only — never scrape individual profiles."},
    {"name": "BNC Network / Zawya project boards", "kind": "tender",
     "segments": ["fitout_contractor", "developer", "construction", "architect"],
     "business_data": True,
     "access": "subscription", "why": "Names the contractor on a live project before the glazing is bought."},
    {"name": "Big 5 / INDEX exhibitor lists", "kind": "trade_show",
     "segments": ["fitout_contractor", "interior_design", "developer", "construction"],
     "business_data": True,
     "access": "public", "why": "Self-selected buyers in exactly this trade, refreshed yearly."},
    {"name": "MEFMA member list (facility management)", "kind": "association",
     "segments": ["facility_management", "property_management"], "business_data": True,
     "access": "public", "why": "FM firms carry the repeat maintenance spend, not one-off jobs."},
    {"name": "Bayut / Property Finder AGENCY pages", "kind": "portal",
     "segments": ["real_estate_agency", "property_management"], "business_data": True,
     "access": "public", "why": "The agency, not the listing. Agencies handle snagging across many units."},
    {"name": "Dubai Land Department developer register", "kind": "registry",
     "segments": ["developer", "property_management"], "business_data": True,
     "access": "public", "why": "Developers with active projects across the emirate."},
    {"name": "Hotel and hospital group corporate sites", "kind": "website",
     "segments": ["hotel", "hospital", "mall", "school"], "business_data": True,
     "access": "public", "why": "Groups buy centrally — one procurement contact covers twenty buildings."},
    # ---- WHERE THE LOCALS ARE, WHICH IS NOT WHERE THE FIRMS ARE ------------
    #
    # The owner's point: "on maps most of the data is related to agencies,
    # businesses and majors, while what I want is the locals." Maps has the
    # small shops too — a cafeteria in Al Quoz is on Maps — but you only reach
    # them by searching for what they ARE, area by area, which is what the
    # local segments above now do. These are the places that hold what Maps
    # does not.
    {"name": "Instagram business profiles by UAE location tag", "kind": "social",
     "segments": ["salon", "spa", "gym", "restaurant", "food_stall", "retail",
                  "supermarket", "laundry", "clinic_small", "nursery_local",
                  "holiday_let", "signage"],
     "business_data": True, "access": "public business profiles",
     "why": "Where a UAE salon, café or gym actually lives. Business profiles "
            "publish a phone or a WhatsApp button in the bio, and a location "
            "tag puts them in an area. Business profiles only — never a "
            "personal account."},
    {"name": "Food-delivery platform listings (Talabat, Deliveroo, Zomato)",
     "kind": "portal",
     "segments": ["restaurant", "food_stall", "supermarket", "laundry"],
     "business_data": True, "access": "public listing pages",
     "why": "Every cafeteria, juice shop and grocer in the country, with its "
            "area and trading name. The densest shopfront list there is."},
    {"name": "UAE local directories (Yellow Pages, Connect.ae, Dubai listings)",
     "kind": "directory",
     "segments": ["supermarket", "local_market", "laundry", "food_stall",
                  "signage", "builder", "clinic_small", "nursery_local", "retail"],
     "business_data": True, "access": "public",
     "why": "The small businesses that never bothered with a website but did "
            "get themselves listed. Often a landline and nothing else — which "
            "is still a way to reach them."},
    {"name": "Holiday-let operator pages (Airbnb/Booking host listings)",
     "kind": "portal", "segments": ["holiday_let", "landlord"],
     "business_data": True, "access": "public",
     "why": "Operators managing several units, who fix what the last guest "
            "broke out of their own pocket and this week. The OPERATOR's own "
            "site or company page — not a guest, and not a private host."},
    {"name": "Signage and print companies' client and portfolio pages",
     "kind": "website", "segments": ["signage"], "business_data": True,
     "access": "public",
     "why": "They subcontract the aluminium framing and acrylic they cannot "
            "make. One relationship here is repeat work every month, and their "
            "portfolio page names the shops they fitted out — who are leads too."},
    {"name": "Owners' association and building manager listings (Mollak/DLD)",
     "kind": "registry", "segments": ["landlord", "property_management",
                                      "villa_compound"],
     "business_data": True, "access": "public search",
     "why": "The registered manager of a building is the person who signs off "
            "railings, windows and lobby glass across every unit in it."},

    # Kept in the list on purpose so the screen has something real to refuse and
    # you can see the rule biting rather than take it on trust.
    {"name": "Classifieds villa-owner listings", "kind": "classifieds",
     "segments": ["private_individual"], "business_data": False,
     "access": "public", "why": "Private individuals at home — see the note on ALLOW_INDIVIDUALS."},
]

# WHICH SEGMENTS ARE "THE LOCALS"
#
# Not a category of business, a category of DECISION. On this list the person
# who decides is the person behind the counter or the one who owns the villa:
# no procurement, no tender, no three quotes, and the job is wanted this month.
# The rest have a process to get through. The owner asked to be pointed at the
# first group, and this is what lets the source map answer him rather than
# leaving him to count the list himself.
LOCAL_SEGMENTS = {
    "supermarket", "local_market", "signage", "holiday_let", "landlord",
    "builder", "laundry", "garage", "clinic_small", "nursery_local",
    "food_stall",
    "salon", "gym", "spa", "restaurant", "retail", "startup", "coworking",
    "villa_compound",
}


def reaches_locals(source) -> bool:
    """Does this source find street-level buyers, or only firms with a process?"""
    segs = set(source.get("segments") or [])
    return bool(segs & LOCAL_SEGMENTS)

# PRIVATE INDIVIDUALS — the one limit left, and it is the owner's to lift
#
# The firm serves homes as well as businesses, and nothing here says otherwise:
# villas are reached through the people who manage them, which is why villa
# compounds, owners' associations, property managers and estate agencies are all
# segments above. What this guard stops is one specific thing — cold-mailing a
# named private person at home, scraped off a classifieds ad. In the UAE that is
# the case where the PDPL and the TDRA rules actually have teeth, and it is also
# the fastest way to get an outreach domain marked as spam, which would cost us
# every other segment on the list.
#
# It is a switch, not a wall. Set LEADS_ALLOW_INDIVIDUALS=1 and the Hunter will
# collect them; the decision belongs to the owner, not to this file.
def allow_individuals() -> bool:
    return (os.environ.get("LEADS_ALLOW_INDIVIDUALS") or "").strip().lower() in ("1", "true", "yes")


_PRIVATE = ("villa owner", "home owner", "homeowner", "private individual",
            "household", "private residence")
_PRIVATE_RE = re.compile("|".join(re.escape(p) for p in _PRIVATE), re.I)

UAE_HINTS = ("dubai", "abu dhabi", "sharjah", "ajman", "fujairah",
             "ras al khaimah", "umm al quwain", "uae", "u.a.e", "emirates",
             # districts, because an area field often says only "Business Bay"
             "business bay", "al quoz", "al barsha", "jumeirah", "marina",
             "downtown", "deira", "jlt", "jvc", "silicon oasis", "mirdif")

# RELEVANCE — deliberately broad, because the old list was the real limitation
#
# This used to be a contractor keyword list, so a hotel, a hospital, a salon and
# a restaurant all failed it and were thrown away. Every one of those occupies a
# building with glass and aluminium in it. The test that matters is "is this a
# business that occupies or works on premises", and in the UAE the honest answer
# for almost every registered company is yes. So the list below is wide on
# purpose; the narrowing happens later, in which SERVICE we pitch them, not in
# whether we are allowed to talk to them at all.
RELEVANT = (
    # the trade
    "glass", "alumin", "fit out", "fit-out", "fitout", "interior", "contract",
    "develop", "facilit", "property", "real estate", "joinery", "façade",
    "facade", "constru", "engineer", "maintenance", "architect", "design",
    # hospitality and food
    "hotel", "resort", "restaurant", "cafe", "café", "catering", "kitchen",
    "lounge", "hospitality", "bakery",
    # health, care and education
    "hospital", "clinic", "medical", "dental", "pharmac", "health", "wellness",
    "school", "nursery", "academy", "institute", "university", "training",
    # retail and leisure
    "shop", "store", "retail", "boutique", "showroom", "mall", "market",
    "supermarket", "salon", "barber", "beauty", "spa", "gym", "fitness",
    "studio", "cinema", "club",
    # offices, services and industry
    "office", "coworking", "co-working", "business centre", "business center",
    "bank", "exchange", "insurance", "consult", "law", "legal", "accounting",
    "agency", "marketing", "logistics", "warehouse", "factory", "industr",
    "trading", "group", "holding", "services", "company", "llc", "l.l.c",
    "compound", "accommodation", "tower", "building", "centre", "center",
)

# A company switchboard is business data. A named employee's work address is
# PERSONAL data about an identifiable person, even at a company — UAE PDPL does
# not stop caring because the domain is corporate. So we prefer the role inbox,
# and anything personal is flagged rather than filed away as ordinary business
# detail. It is also simply better outreach: info@ survives the person leaving.
ROLE_INBOXES = ("info", "sales", "enquiry", "enquiries", "contact", "admin",
                "office", "projects", "procurement", "purchasing", "tenders",
                "hello", "team", "support", "estimation", "estimating")


def contact_kind(lead: dict) -> str:
    """'role' | 'personal' | 'unknown' — which kind of address is this?"""
    email = (lead.get("email") or "").strip().lower()
    local = email.split("@")[0] if "@" in email else ""
    if local and any(local == r or local.startswith(r + ".") or local.startswith(r + "-")
                     for r in ROLE_INBOXES):
        return "role"
    if (lead.get("name") or "").strip():
        return "personal"          # we hold a person's name against this record
    if local and ("." in local or "_" in local):
        return "personal"          # firstname.lastname@ is a person
    return "unknown"


# ---- the gate ---------------------------------------------------------------

def is_private_individual(*fields) -> bool:
    """True if anything here points at a person rather than a business."""
    return bool(_PRIVATE_RE.search(" ".join(str(f or "") for f in fields)))


def screen_source(src) -> tuple:
    """A source is usable if it yields businesses — or if the owner has opened
    the door to individuals, in which case it is usable too."""
    reasons = []
    if not src.get("business_data") and not allow_individuals():
        reasons.append("yields private individuals at home — off unless the owner "
                       "sets LEADS_ALLOW_INDIVIDUALS=1")
    if not src.get("why"):
        reasons.append("no stated reason for using it")
    return (not reasons, reasons)


def screen_lead(c: dict) -> tuple:
    """
    The gate every candidate passes before it can be stored. Order matters:
    the privacy question comes first, because it is the only one that can make a
    perfectly good commercial fit the wrong thing to do.
    """
    blob = " ".join(str(c.get(k) or "") for k in ("name", "company", "segment", "source", "socials"))
    if (is_private_individual(blob) or c.get("segment") == "private_individual") \
            and not allow_individuals():
        return (False, "a private person at home — off unless the owner sets "
                       "LEADS_ALLOW_INDIVIDUALS=1")
    if not (c.get("company") or "").strip():
        return (False, "no company name — cannot confirm who this actually is")
    # Geography comes from WHERE THE FIRM IS, never from where we found it. A
    # London firm listed in a Dubai directory is still in London, and letting the
    # source name into this check would wave it straight through.
    geo = (str(c.get("area") or "").strip()
           or f'{c.get("company") or ""} {c.get("name") or ""}').lower()
    if not any(h in geo for h in UAE_HINTS):
        return (False, "outside the UAE — not a market this business serves")
    if not any(r in blob.lower() for r in RELEVANT):
        return (False, "no connection to glass, aluminium or the trades that buy it")
    # A STREET ADDRESS IS A CONTACT ROUTE FOR THIS BUSINESS.
    #
    # This used to demand an email or a phone, which is right for an agency
    # that only ever writes. It is wrong for a glass and aluminium workshop in
    # Dubai: a shopfront job is won by walking into the cafeteria and talking
    # to the man behind the counter, and "Al Hunoof Public Kitchen, Al Quoz 3"
    # is everything the owner needs to do that.
    #
    # It mattered the moment the OSM collector arrived. Most small UAE
    # businesses on the map carry a name and an address and no phone at all, so
    # this line would have thrown away the majority of exactly the local leads
    # he asked for. Nothing downstream is loosened: outreach still refuses a
    # lead with no address to write to, and a visit-only lead simply is not
    # mailed.
    if not ((c.get("email") or "").strip() or (c.get("phone") or "").strip()
            or (c.get("address") or "").strip()):
        return (False, "no way to reach them at all — no email, no phone, "
                       "not even a street address")
    kind = contact_kind(c)
    if kind == "personal":
        # Allowed, but it is personal data and must not be filed as if it were
        # just a company detail. Prefer the switchboard where one exists.
        return (True, "business, but a NAMED PERSON's address — personal data under "
                      "PDPL; prefer the company's role inbox and keep a lawful basis")
    return (True, f"business, in the UAE, relevant, contactable ({kind} inbox)")


def intake(candidates, agent="leads-hunter", sector="Leads") -> dict:
    """
    Screen, dedup, store. Never contacts anybody.

    Every candidate goes through screen_lead() and then add_lead_if_new(), which
    is what stops the same firm being collected twice from two directories. A
    stored lead sits at stage 'new' and stays there: moving it to 'contacted'
    needs Security clearance and an owner-approved route, neither of which lives
    in this function.
    """
    stored, dupes, refused, personal = [], [], [], []
    for c in candidates:
        ok, why = screen_lead(c)
        if not ok:
            refused.append({"company": c.get("company") or c.get("name"), "why": why})
            continue
        kind = contact_kind(c)
        if kind == "personal":
            # counted separately so you can always see how much personal data the
            # cabinet is holding, rather than discovering it during an audit
            personal.append(c.get("company"))
        # dedup BEFORE the store — re-approaching a firm burns the relationship
        if board.lead_seen(c.get("email"), c.get("phone"), c.get("company")):
            dupes.append(c.get("company"))
            continue
        # Stored WITH how we can reach them, so nobody has to re-derive it later
        # and get a different answer. See pipelines/harvest.py.
        lid = board.add_lead_if_new({**c, "source": c.get("source", "leads-hunter"),
                                     "contact_kind": kind})
        if lid is None:
            dupes.append(c.get("company"))
        else:
            stored.append({"id": lid, "company": c.get("company")})
    return {"stored": stored, "duplicates": dupes, "refused": refused,
            "personal_data_held": personal, "contacted": 0,
            "agent": agent, "sector": sector}


# ---- today's task: the source map -------------------------------------------

def build_source_map():
    """
    Usable sources, grouped by segment, with the refused ones kept visible and
    the service each segment actually buys attached to it.

    The services matter here, not just in the mail. A target list that says
    "hotels" is a list; a target list that says "hotels — shower glass, mirrors,
    balustrades, wooden doors" is a plan, and it is what stops the Hunter
    collecting names nobody knows what to do with.
    """
    usable, refused = [], []
    for s in SOURCES:
        ok, why = screen_source(s)
        (usable if ok else refused).append({**s, "refused_because": why})
    by_segment = {}
    for seg, meta in SEGMENTS.items():
        hits = [s["name"] for s in usable if seg in s["segments"]]
        svc = services.services_for_segment(seg)
        by_segment[seg] = {"why_they_buy": meta["why"],
                           "search_terms": list(meta["queries"]),
                           "services_they_need": [services.label(k) for k in svc],
                           "sources": hits, "source_count": len(hits),
                           # street-level buyer, or a firm with a process
                           "local": seg in LOCAL_SEGMENTS}
    local_srcs = [s["name"] for s in usable if reaches_locals(s)]
    return {"segments": by_segment, "usable_sources": usable, "districts": DISTRICTS,
            "local_segments": sorted(s for s in by_segment if by_segment[s]["local"]),
            "local_sources": local_srcs,
            "refused_sources": [{"name": r["name"], "because": r["refused_because"]}
                                for r in refused]}


def critic_check(smap, env_ready):
    """
    The Critic's gate. Leads' failure mode is contacting the wrong person, or
    the same person twice, so the checks aim straight at that — plus the newer
    failure this sector actually had, which was a target list so narrow it threw
    away most of the market.
    """
    reasons = []
    if not smap["usable_sources"]:
        reasons.append("no usable sources — nothing for the Hunter to work from")
    thin = [k for k, v in smap["segments"].items() if v["source_count"] == 0]
    if thin:
        reasons.append(f"no source covers: {', '.join(thin)}")
    noservice = [k for k, v in smap["segments"].items() if not v["services_they_need"]]
    if noservice:
        reasons.append("no service is mapped to: " + ", ".join(noservice)
                       + " — we would be writing to them with nothing to sell")
    if len(smap["segments"]) < 12:
        reasons.append(f"only {len(smap['segments'])} segments — this business sells to "
                       "every kind of building in the UAE, and a short list is a "
                       "decision nobody made")
    if not allow_individuals() and not smap["refused_sources"]:
        reasons.append("nothing was refused — the privacy screen cannot be working")
    if env_ready:
        reasons.append("outreach domain is configured — this task was scoped as "
                       "pipeline-prep with no contact; re-plan before collecting")
    return (not reasons, reasons)


def run(agent="leads-hunter"):
    with board.agent_run(agent) as run_row:
        out = _hunt(agent, run_row)
        run_row.note(f"mapped {out.get('usable_sources', 0)} usable source(s) across "
                     f"{out.get('segments_covered', 0)} segment(s)"
                     if out.get("ok") else out.get("error", "nothing to hunt"))
        return out


def _hunt(agent, run_row):
    con = sqlite3.connect(board.DB)
    con.row_factory = sqlite3.Row
    try:
        leads_before = con.execute("SELECT count(*) FROM leads").fetchone()[0]
        log_before = con.execute("SELECT count(*) FROM outreach_log").fetchone()[0]

        # 1. claim the MD's task (atomic — two Hunters cannot both take it)
        task = board.next_open_task("Leads", agent)
        if not task:
            return {"ok": False, "error": dispatch.why_nothing("Leads", agent)}
        run_row.task(task["id"])

        # 2. the MD scoped this as prep because there is no outreach route yet.
        #    Check that rather than assuming it.
        env_ready = bool((os.environ.get("OUTREACH_DOMAIN") or "").strip())

        # 3. build the map, with the privacy screen actually applied
        smap = build_source_map()

        ok, reasons = critic_check(smap, env_ready)
        if not ok:
            board.finish_task(task["id"], agent,
                              "Critic rejected: " + "; ".join(reasons), status="blocked")
            return {"ok": True, "claimed_task": task["id"], "critic": "rejected",
                    "critic_reasons": reasons, "stored_leads": 0}

        # 4. Security's call, not ours: it owns judgement on who is safe to approach.
        #    Non-blocking — nothing is being contacted today, so there is nothing to
        #    stall. Actual contact stays gated behind a clearance this does not grant.
        ask = board.request_verification(
            "Leads", "Security", "Clear these lead sources before any collection",
            "Mapped " + str(len(smap["usable_sources"])) + " sources covering "
            + str(len(SEGMENTS)) + " segments across " + ", ".join(DISTRICTS) + ".\n\nUsable:\n- "
            + "\n- ".join(f'{s["name"]} ({s["kind"]}, {s["access"]})'
                          for s in smap["usable_sources"])
            + "\n\nRefused on privacy grounds:\n- "
            + "\n- ".join(f'{r["name"]}: {"; ".join(r["because"])}'
                          for r in smap["refused_sources"])
            + "\n\nNo leads were stored and nobody was contacted. Please flag any source "
              "you consider unsafe, injection-prone or likely to carry personal data "
              "before the Hunter collects from it.",
            blocking=False)

        # 5. the deliverable IS the map — it goes on the task, not into the leads table.
        #    Pass the dict: finish_task json-encodes it, and handing it a string
        #    would store the map double-encoded and unreadable to anything downstream.
        board.finish_task(task["id"], agent, smap, status="done")
        did = board.log_decision(
            "leads-sources",
            reasoning=(f"Mapped {len(smap['usable_sources'])} sources covering "
                       f"{len(SEGMENTS)} buying segments across {len(DISTRICTS)} areas, each "
                       f"tied to the services it actually buys; refused "
                       f"{len(smap['refused_sources'])}."),
            expectation="The Hunter collects across the whole market, not five contractor "
                        "types, and every lead arrives with the service to pitch attached.",
            confidence=0.75,
            # the claim is about leads that do not exist yet, so this records the
            # baseline Report must measure the Hunter's later work against
            evidence={"leads_before": leads_before,
                      "segments_mapped": len(SEGMENTS)})

        # 6. prove the two promises rather than asserting them in prose
        leads_after = con.execute("SELECT count(*) FROM leads").fetchone()[0]
        log_after = con.execute("SELECT count(*) FROM outreach_log").fetchone()[0]
        if leads_after != leads_before or log_after != log_before:
            raise AssertionError(
                f"contract broken: leads {leads_before}->{leads_after}, "
                f"outreach {log_before}->{log_after} (this task stores and contacts nothing)")

        return {"ok": True, "agent": agent, "claimed_task": task["id"],
                "critic": "passed", "outreach_domain_set": env_ready,
                "segments_covered": len(smap["segments"]),
                "usable_sources": len(smap["usable_sources"]),
                "refused_sources": [r["name"] for r in smap["refused_sources"]],
                "districts": DISTRICTS,
                "security_request": ask, "decision_id": did,
                "stored_leads": 0, "contacted": 0,
                "map": smap["segments"]}
    finally:
        con.close()


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
