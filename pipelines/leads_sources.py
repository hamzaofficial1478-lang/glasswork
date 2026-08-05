"""
leads_sources.py — the Leads sector's first real task, plus the gate that will
guard every lead the agency ever collects.

There is a task already sitting on the board, in the MD's own words:

    "B2B target-source list for the buy-intent districts (no outreach). Sources
     only — store NO lead records yet, no contact. Sending domain is missing, so
     this is pipeline-prep. Real leads later go through add_lead_if_new +
     Security clearance. Private villa owners excluded (privacy/PDPL)."

So this module does exactly that and nothing more. It maps where B2B buyers of
glass and aluminium work can be found, and it stores not one lead and contacts
not one person. Those are not aspirations — `run()` asserts both at the end and
fails loudly if either is untrue.

The second half is the machinery that makes real collection safe later:

  screen_lead()  the PDPL gate. Private individuals are refused outright. A
                 villa owner's details on a classifieds site are personal data;
                 a licensed contractor's switchboard is not.
  intake()       dedup + screen + Security clearance. It stores only leads that
                 are new AND business AND in the UAE AND relevant, always at
                 stage 'new', and it never writes to outreach_log. Nothing in
                 here can contact anybody, by construction.

Re-contacting someone you have already approached burns the relationship and
the domain's reputation, which is why the dedup check runs before the store and
not after.

    python -m pipelines.leads_sources        (python3 on macOS/Linux)
"""
import json
import os
import re
import sqlite3

from coordination import board

# The districts the MD named — the buy-intent areas from the keyword work.
DISTRICTS = ["Business Bay", "Al Quoz", "Al Barsha", "Jumeirah Village Circle",
             "Dubai Marina", "Downtown Dubai"]

# Who actually buys this trade in volume, and why they are worth the approach.
SEGMENTS = {
    "fitout": "Fit-out contractors — partitions, shopfronts and doors on every job.",
    "interior_design": "Interior-design studios — they specify the glazing before tender.",
    "developer": "Property developers — balustrades, façades and windows at scale.",
    "fm": "Facility-management firms — repeat repair and replacement work.",
    "handover": "Real-estate / handover agents — snagging and pre-handover fixes.",
}

# Public, B2B places those firms are listed. 'b2b_only' is the PDPL-critical
# flag: a source that yields private individuals cannot be used, no matter how
# many names it holds.
SOURCES = [
    {"name": "Dubai Chamber of Commerce member directory", "kind": "registry",
     "segments": ["fitout", "interior_design", "fm"], "b2b_only": True,
     "access": "public search", "why": "Licensed firms with a trade category — the cleanest B2B list in Dubai."},
    {"name": "DED trade-licence listings", "kind": "registry",
     "segments": ["fitout", "interior_design", "fm"], "b2b_only": True,
     "access": "public search", "why": "Confirms a firm is real and still trading before anyone approaches it."},
    {"name": "Google Business Profiles by category + district", "kind": "maps",
     "segments": ["fitout", "interior_design", "fm", "handover"], "b2b_only": True,
     "access": "public", "why": "Gives the district split the keyword work asked for, plus review counts."},
    {"name": "LinkedIn company search (industry + emirate)", "kind": "social",
     "segments": ["fitout", "interior_design", "developer", "fm"], "b2b_only": True,
     "access": "public company pages", "why": "Company pages only — never scrape individual profiles."},
    {"name": "BNC Network / Zawya project boards", "kind": "tender",
     "segments": ["fitout", "developer"], "b2b_only": True,
     "access": "subscription", "why": "Names the contractor on a live project before the glazing is bought."},
    {"name": "Big 5 / INDEX exhibitor lists", "kind": "trade_show",
     "segments": ["fitout", "interior_design", "developer"], "b2b_only": True,
     "access": "public", "why": "Self-selected buyers in exactly this trade, refreshed yearly."},
    {"name": "MEFMA member list (facility management)", "kind": "association",
     "segments": ["fm"], "b2b_only": True,
     "access": "public", "why": "FM firms carry the repeat maintenance spend, not one-off jobs."},
    {"name": "Bayut / Property Finder AGENCY pages", "kind": "portal",
     "segments": ["handover"], "b2b_only": True,
     "access": "public", "why": "The agency, not the listing. Agencies handle snagging across many units."},
    {"name": "Dubai Land Department developer register", "kind": "registry",
     "segments": ["developer"], "b2b_only": True,
     "access": "public", "why": "Developers with active projects in the named districts."},
    # Deliberately included so the screen has something real to refuse.
    {"name": "Classifieds villa-owner listings", "kind": "classifieds",
     "segments": ["homeowner"], "b2b_only": False,
     "access": "public", "why": "Private individuals — named here only to record that it is refused."},
]

# Anything that smells of a private individual. The MD excluded villa owners on
# privacy/PDPL grounds, and that has to be enforced in code, not in a comment.
_PRIVATE = ("villa owner", "home owner", "homeowner", "resident", "tenant",
            "private individual", "household", "consumer", "personal")
_PRIVATE_RE = re.compile("|".join(re.escape(p) for p in _PRIVATE), re.I)

UAE_HINTS = ("dubai", "abu dhabi", "sharjah", "ajman", "fujairah",
             "ras al khaimah", "umm al quwain", "uae", "u.a.e", "emirates",
             # districts, because an area field often says only "Business Bay"
             "business bay", "al quoz", "al barsha", "jumeirah", "marina",
             "downtown", "deira", "jlt", "jvc", "silicon oasis", "mirdif")

RELEVANT = ("glass", "alumin", "fit out", "fit-out", "fitout", "interior",
            "contract", "develop", "facilit", "property", "real estate",
            "joinery", "façade", "facade", "constru", "engineer", "maintenance")

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
    """A source is usable only if it yields businesses."""
    reasons = []
    if not src.get("b2b_only"):
        reasons.append("yields private individuals — refused under PDPL")
    if is_private_individual(src.get("name"), " ".join(src.get("segments", []))):
        reasons.append("named segment is a private individual")
    if not src.get("why"):
        reasons.append("no stated reason for using it")
    return (not reasons, reasons)


def screen_lead(c: dict) -> tuple:
    """
    The gate every candidate passes before it can be stored. Order matters:
    privacy first, because a private individual is refused even if they are a
    perfect commercial fit.
    """
    blob = " ".join(str(c.get(k) or "") for k in ("name", "company", "segment", "source", "socials"))
    if is_private_individual(blob) or c.get("segment") == "homeowner":
        return (False, "private individual — refused under PDPL")
    if not (c.get("company") or "").strip():
        return (False, "no company name — cannot confirm it is a business")
    # Geography comes from WHERE THE FIRM IS, never from where we found it. A
    # London firm listed in a Dubai directory is still in London, and letting the
    # source name into this check would wave it straight through.
    geo = (str(c.get("area") or "").strip()
           or f'{c.get("company") or ""} {c.get("name") or ""}').lower()
    if not any(h in geo for h in UAE_HINTS):
        return (False, "outside the UAE — not a market this business serves")
    if not any(r in blob.lower() for r in RELEVANT):
        return (False, "no connection to glass, aluminium or the trades that buy it")
    if not ((c.get("email") or "").strip() or (c.get("phone") or "").strip()):
        return (False, "no business contact route recorded")
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
        if contact_kind(c) == "personal":
            # counted separately so you can always see how much personal data the
            # cabinet is holding, rather than discovering it during an audit
            personal.append(c.get("company"))
        # dedup BEFORE the store — re-approaching a firm burns the relationship
        if board.lead_seen(c.get("email"), c.get("phone"), c.get("company")):
            dupes.append(c.get("company"))
            continue
        lid = board.add_lead_if_new({**c, "source": c.get("source", "leads-hunter")})
        if lid is None:
            dupes.append(c.get("company"))
        else:
            stored.append({"id": lid, "company": c.get("company")})
    return {"stored": stored, "duplicates": dupes, "refused": refused,
            "personal_data_held": personal, "contacted": 0,
            "agent": agent, "sector": sector}


# ---- today's task: the source map -------------------------------------------

def build_source_map():
    """Usable sources, grouped by segment, with the refused ones kept visible."""
    usable, refused = [], []
    for s in SOURCES:
        ok, why = screen_source(s)
        (usable if ok else refused).append({**s, "refused_because": why})
    by_segment = {}
    for seg, why in SEGMENTS.items():
        hits = [s["name"] for s in usable if seg in s["segments"]]
        by_segment[seg] = {"why_they_buy": why, "districts": DISTRICTS,
                           "sources": hits, "source_count": len(hits)}
    return {"segments": by_segment, "usable_sources": usable,
            "refused_sources": [{"name": r["name"], "because": r["refused_because"]}
                                for r in refused]}


def critic_check(smap, env_ready):
    """
    The Critic's gate. Leads' failure mode is contacting the wrong person, or
    the same person twice, so the checks aim straight at that.
    """
    reasons = []
    if not smap["usable_sources"]:
        reasons.append("no usable sources — nothing for the Hunter to work from")
    thin = [k for k, v in smap["segments"].items() if v["source_count"] == 0]
    if thin:
        reasons.append(f"no source covers: {', '.join(thin)}")
    # the PDPL rule must have actually bitten, not just been declared
    if not smap["refused_sources"]:
        reasons.append("nothing was refused — the privacy screen cannot be working")
    for s in smap["usable_sources"]:
        if not s.get("b2b_only"):
            reasons.append(f"'{s['name']}' passed but is not B2B-only")
    if env_ready:
        reasons.append("outreach domain is configured — this task was scoped as "
                       "pipeline-prep with no contact; re-plan before collecting")
    return (not reasons, reasons)


def run(agent="leads-hunter"):
    con = sqlite3.connect(board.DB)
    con.row_factory = sqlite3.Row
    try:
        leads_before = con.execute("SELECT count(*) FROM leads").fetchone()[0]
        log_before = con.execute("SELECT count(*) FROM outreach_log").fetchone()[0]

        # 1. claim the MD's task (atomic — two Hunters cannot both take it)
        task = board.next_open_task("Leads", agent)
        if not task:
            return {"ok": False, "error":
                    "No open Leads task on the board to claim. The Hunter works from the "
                    "board, never on its own initiative."}

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
            "Mapped " + str(len(smap["usable_sources"])) + " B2B sources across "
            + ", ".join(DISTRICTS) + ".\n\nUsable:\n- "
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
            reasoning=(f"Mapped {len(smap['usable_sources'])} B2B sources covering all "
                       f"{len(SEGMENTS)} buying segments across {len(DISTRICTS)} districts; "
                       f"refused {len(smap['refused_sources'])} on PDPL grounds."),
            expectation="The Hunter can collect verified B2B leads the day outreach is approved.",
            confidence=0.75)

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
