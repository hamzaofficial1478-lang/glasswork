"""
md_cycle.py — the Managing Director's daily cycle. The brain that starts the day.

    python -m pipelines.md_cycle            plan, challenge, post
    python -m pipelines.md_cycle --dry-run  show the plan, post nothing

WHY THIS EXISTS

Every sector was built to work from the board: the IT Watcher claims an IT task,
the Leads sector claims a Leads task, Security answers requests other sectors
raise. All of that worked. Nothing put anything on the board. The only post_task
call in the whole codebase was inside board.py's own smoke test, so every
pipeline stopped at "No open task to claim" and the agency could not start
itself. This is that missing piece.

THE FOUR RULES FROM THE HANDBOOK, ENFORCED IN CODE

1. THE BRAIN NEVER TOUCHES THE HANDS. This module imports no pipeline and calls
   no run(). It reads the cabinet and posts tasks. That is the whole surface.
   md_tests.py asserts it, because a comment promising it is not enforcement.

2. NO DECISION WITHOUT EVIDENCE. Every candidate carries `evidence` — a fact or
   number already in the cabinet. The Critic drops anything that cannot point to
   one. A plan built on a hunch does not reach the board.

3. EVERY PLAN IS CHALLENGED. critic_check() runs before a single task is posted
   and can empty the plan entirely. It rejects the ungrounded, the duplicated,
   the impossible (work needing a capability the agency does not have), and the
   repeatedly-failed.

4. IT KEEPS SCORE. The cycle reads its own recent hit-rate first and says so in
   its reasoning, then logs the decision with the task ids it created so Report
   can mark it later against real rows.

TWO THINGS IT DELIBERATELY WILL NOT DO

Amber work is not posted. Anything risky goes to the owner as a message and
waits; the MD does not get to authorise itself.

It does not flood the board. Re-running it must not double the work, so a
candidate matching an already-open task for that sector is dropped rather than
posted again — the cycle is safe to run on a timer.
"""
import argparse
import json
import sqlite3

from coordination import board, briefing, proposals as _props, state as _state, targets

MAX_TASKS_PER_CYCLE = 6      # a plan nobody can finish is not a plan


# ---------------------------------------------------------------------------
# TWO MINDS ON THE ALLOCATION
# ---------------------------------------------------------------------------
#
# The MD's own reasoning is deterministic and stays that way — the rules above
# are auditable and free, and replacing them with a model would make the plan
# harder to check, not better.
#
# What the models are for is the ONE judgement the rules genuinely cannot make:
# when more work is grounded than the day can hold, which of it matters most.
# Getting that wrong costs a real day, so it is asked of both models and a
# disagreement is treated as information rather than noise.
#
# The safe default matters here. If the models split, or none is configured, the
# MD keeps its own deterministic ranking and says so — it does not stall, and it
# does not pretend a coin-flip was a decision.

def _ask_both_which_matters(candidates, facts, deliberate=None):
    """
    Returns (ordered_titles, note). On any doubt the MD's own order is kept.
    """
    if len(candidates) <= MAX_TASKS_PER_CYCLE:
        return None, ""          # nothing to choose between; do not spend
    if deliberate is None:
        try:
            from providers import deliberate as _d
            deliberate = _d
        except Exception:
            return None, "no provider layer — kept my own ranking"

    listing = "\n".join(f"- [{c['sector']}] {c['title']}  (because: {c['evidence']})"
                         for c in candidates)
    orders = "; ".join(f"{k}: {v}" for k, v in (facts.get("sector_orders") or {}).items())
    r = deliberate(
        "md",
        [{"role": "system", "content":
          "You rank a day's work for a UAE glass and aluminium business. Reply with "
          "the titles only, most important first, one per line, nothing else. A dead "
          "enquiry path outranks everything; the owner's own orders come next."},
         {"role": "user", "content":
          f"The owner's orders today: {orders or 'none'}\n\nCandidates:\n{listing}"}],
        max_tokens=400,
        verdict_of=lambda t: "|".join(l.strip().lstrip("-0123456789. ").lower()
                                      for l in (t or "").splitlines() if l.strip()))
    if not r.get("ok"):
        return None, f"kept my own ranking ({r.get('error')})"
    if r.get("consulted", 0) < 2:
        return None, "kept my own ranking — only one model, so no cross-check"
    if not r.get("agreed"):
        # A split on what matters most is worth the owner knowing about; it is
        # not worth stalling the day over, so the deterministic order stands.
        return None, ("the two models disagreed on what matters most today, "
                      "so I kept my own ranking — worth a look")
    return [t for t in (r.get("verdict") or "").split("|") if t], \
           "both models agreed on the order"


# ---------------------------------------------------------------------------
# SENSE — read the live state of the business. No judgement here, just facts.
# ---------------------------------------------------------------------------

def sense() -> dict:
    """Everything the MD is allowed to reason from. Rows, not impressions."""
    facts = {"hit_rate": None, "targets": {}, "metrics": {}, "approvals": 0,
             "open_tasks": {}, "site": {}, "leads": {}, "missing": []}
    try:
        facts["hit_rate"] = board.recent_hit_rate()
    except Exception:
        pass
    try:
        facts["targets"] = targets.status()
    except Exception:
        facts["targets"] = {"jobs": [], "orders": "", "any_target_set": False}
    try:
        st = _state.build()
        facts["metrics"] = st.get("metrics", {})
        facts["approvals"] = len(st.get("approvals", []))
    except Exception:
        pass
    try:
        b = briefing.build()
        facts["missing"] = [s.get("shelf") or s.get("would_let_us")
                            for s in b.get("cannot_do_yet", [])]
    except Exception:
        pass

    con = sqlite3.connect(board.DB)
    try:
        facts["open_tasks"] = {
            r[0]: r[1] for r in con.execute(
                "SELECT sector, count(*) FROM tasks WHERE status IN ('open','in_progress')"
                " GROUP BY sector")}
        facts["open_titles"] = {
            (r[0], (r[1] or "").strip().lower()) for r in con.execute(
                "SELECT sector, title FROM tasks WHERE status IN ('open','in_progress')")}
        facts["site"] = {
            r[0]: r[1] for r in con.execute(
                "SELECT severity, count(*) FROM site_issues"
                " WHERE status IN ('open','fixing','in_review') GROUP BY severity")}
        facts["leads"] = {"total": con.execute(
            "SELECT count(*) FROM leads").fetchone()[0]}
        facts["source_map_done"] = con.execute(
            "SELECT count(*) FROM decisions WHERE cycle='leads-sources'").fetchone()[0] > 0
        facts["objected_titles"] = {
            (r[0] or "").strip().lower() for r in con.execute(
                "SELECT t.title FROM task_objections o JOIN tasks t ON t.id=o.task_id")}
        facts["recent_failures"] = [
            r[0] for r in con.execute(
                "SELECT cycle FROM decisions WHERE outcome='failed'"
                " ORDER BY scored_at DESC LIMIT 8") if r[0]]
    finally:
        con.close()

    # What the sectors are asking for, and what the owner told each of them.
    # These outrank the MD's own hardcoded rules: the sector knows its domain
    # and the owner outranks everyone.
    try:
        facts["proposals"] = _props.open_proposals()
    except Exception:
        facts["proposals"] = []
    try:
        facts["sector_orders"] = _props.orders()
    except Exception:
        facts["sector_orders"] = {}
    return facts


# ---------------------------------------------------------------------------
# REASON — turn facts into candidate work. Each carries the fact behind it.
# ---------------------------------------------------------------------------

def _target(facts, name):
    for j in (facts.get("targets") or {}).get("jobs") or []:
        if j["name"] == name:
            return j
    return {}


def plan(facts: dict) -> list:
    """
    Candidate tasks, each citing the row that justifies it.

    Ordered by what actually costs the business money: a dead enquiry path
    outranks a keyword gap every time, because the site keeps attracting people
    and quietly dropping every one of them.
    """
    out = []
    site = facts.get("site") or {}

    # 1. WHAT THE SECTORS ASKED FOR. They know their own domain; the MD's job is
    #    to rank, not to second-guess the expertise. Their urgency is respected
    #    but not obeyed — a sector cannot promote itself past everything else.
    for p in facts.get("proposals") or []:
        out.append({
            "sector": p["sector"], "risk": "green",
            "title": p["title"],
            "detail": p.get("detail") or "",
            "evidence": p.get("evidence") or "",
            "needs": p.get("needs") or "",
            "urgency": max(2, int(p.get("urgency") or 5)),
            "proposal_id": p["id"]})

    # 2. WHAT THE OWNER TOLD A SECTOR TO DO. He outranks the MD outright, so this
    #    is urgency 1 and the Critic will not let it be dropped for the ceiling.
    for sector, text in (facts.get("sector_orders") or {}).items():
        out.append({
            "sector": sector, "risk": "green", "urgency": 1,
            "title": f"Owner's order: {text[:60]}",
            "detail": text,
            "evidence": f"the owner set this order for {sector} today"})

    crit = site.get("critical", 0)
    if crit:
        out.append({
            "sector": "IT", "risk": "green", "urgency": 3,
            "title": "Re-check the enquiry path after critical findings",
            "detail": ("Read-only re-check of the enquiry path first, then hygiene. "
                       "File findings; queue any fix, never apply it."),
            "evidence": f"{crit} critical site issue(s) open in site_issues"})

    high = site.get("high", 0)
    if high and not crit:
        out.append({
            "sector": "IT", "risk": "green", "urgency": 4,
            "title": "Read-only health check on the site",
            "detail": "Enquiry path first, then hygiene. File findings only.",
            "evidence": f"{high} high-severity site issue(s) open in site_issues"})

    checks = _target(facts, "site_checks")
    if checks.get("per_day") and checks.get("remaining"):
        out.append({
            "sector": "IT", "risk": "green", "urgency": 4,
            "title": "Daily website diagnostic",
            "detail": "The owner's daily site check. Read-only; file what is wrong.",
            "evidence": (f"owner asked for {checks['per_day']} site check(s) today, "
                         f"{checks['done_today']} done")})

    if not facts.get("source_map_done"):
        out.append({
            "sector": "Leads", "risk": "green", "urgency": 4,
            "title": "Map the lead sources across every buying segment",
            "detail": ("Build the source map: which directories and segments to work, "
                       "each tied to the service that segment actually buys. "
                       "Collect nothing yet."),
            "evidence": "no leads-sources decision on file — the map has never been built"})

    lead_t = _target(facts, "leads")
    if lead_t.get("per_day") and lead_t.get("remaining"):
        out.append({
            "sector": "Leads", "risk": "green", "urgency": 4,
            "title": "Collect today's leads across the mapped segments",
            "detail": ("Work the source map. Every lead carries the service that "
                       "business needs. Dedup before storing; contact nobody."),
            "evidence": (f"owner asked for {lead_t['per_day']} lead(s) today, "
                         f"{lead_t['done_today']} collected")})

    if facts.get("approvals"):
        out.append({
            "sector": "Report", "risk": "green", "urgency": 6,
            "title": "Chase the items waiting on the owner",
            "detail": "Summarise what is queued for approval and why each is blocking.",
            "evidence": f"{facts['approvals']} item(s) sitting in the approval queue"})

    return out


# ---------------------------------------------------------------------------
# CHALLENGE — the Critic. Nothing reaches the board until this passes.
# ---------------------------------------------------------------------------

# What each sector needs on the shelf before its work can possibly succeed.
NEEDS = {"Leads": "maps"}


def critic_check(candidates: list, facts: dict) -> tuple:
    """
    Challenge the plan. Returns (kept, rejected) — each rejection says why.

    This is the guard against a confident MD steering everyone wrong, so it is
    deliberately harsh: it would rather post nothing than post work that is
    ungrounded, already on the board, doomed for want of a key, or a repeat of
    something that has been failing.
    """
    kept, rejected = [], []
    missing = set(facts.get("missing") or [])
    open_titles = facts.get("open_titles") or set()
    failures = facts.get("recent_failures") or []
    seen = set()

    for c in candidates:
        key = (c["sector"], c["title"].strip().lower())

        if not (c.get("evidence") or "").strip():
            rejected.append({**c, "why": "no evidence cited — a hunch, not a plan"})
            continue
        if key in open_titles:
            rejected.append({**c, "why": "already on the board, unclaimed or in progress"})
            continue
        if c.get("title", "").strip().lower() in facts.get("objected_titles", set()):
            # a sector has already said this is not its work. Re-posting it
            # unchanged is the MD ignoring the only feedback it gets.
            rejected.append({**c, "why": "a sector objected to this and it has not "
                                         "been re-routed — needs a different sector"})
            continue
        if key in seen:
            rejected.append({**c, "why": "duplicated inside this same plan"})
            continue
        # a proposal names the shelf it cannot run without; otherwise fall back
        # to what we know the sector needs in general
        need = (c.get("needs") or "").strip() or NEEDS.get(c["sector"])
        if need and need in missing:
            # An OWNER'S order that cannot run is not a rejection, it is news he
            # needs. Dropping it into the reject pile with everything else is how
            # an instruction gets quietly binned and nobody finds out for a week.
            owner_order = int(c.get("urgency") or 5) <= 1
            rejected.append({**c, "owner_blocked": owner_order,
                             "why": (f"YOU ASKED FOR THIS AND IT CANNOT RUN: the {need} "
                                     f"shelf is empty. Add the key and it goes straight out."
                                     if owner_order else
                                     f"the {need} shelf is empty — this would fail "
                                     "the moment it ran")})
            continue
        if failures.count(c.get("cycle", "")) >= 3:
            rejected.append({**c, "why": "this line of reasoning has failed three times"})
            continue
        if c.get("risk", "green") != "green":
            rejected.append({**c, "why": "not green — goes to the owner, not the board"})
            continue

        seen.add(key)
        kept.append(c)

    # Rank before the ceiling bites, or the ceiling drops whatever happened to be
    # last in the list rather than whatever matters least. The owner's own orders
    # are urgency 1 and are never dropped: if they alone exceed the ceiling, the
    # ceiling yields, because a limit that silently bins the owner's instruction
    # is worse than a long day.
    kept.sort(key=lambda c: (int(c.get("urgency") or 5), c["sector"]))
    order, note = _ask_both_which_matters(kept, facts)
    if order:
        rank = {t: i for i, t in enumerate(order)}
        # the owner's orders stay pinned to the front whatever the models think
        kept.sort(key=lambda c: (int(c.get("urgency") or 5) > 1,
                                 rank.get(c["title"].strip().lower(), 999)))
    if note:
        facts["ranking_note"] = note
    if len(kept) > MAX_TASKS_PER_CYCLE:
        overflow = [c for c in kept[MAX_TASKS_PER_CYCLE:] if int(c.get("urgency") or 5) > 1]
        owner_work = [c for c in kept[MAX_TASKS_PER_CYCLE:] if int(c.get("urgency") or 5) <= 1]
        for c in overflow:
            rejected.append({**c, "why": "over the day's task ceiling — kept for tomorrow"})
        kept = kept[:MAX_TASKS_PER_CYCLE] + owner_work
    return kept, rejected


# ---------------------------------------------------------------------------
# DELEGATE — post the surviving plan. The only thing the MD ever writes.
# ---------------------------------------------------------------------------

def run(agent="managing-director", dry_run=False) -> dict:
    with board.agent_run(agent) as run_row:
        out = _cycle(agent, dry_run)
        run_row.note(out.get("summary") or out.get("error") or "planned the day")
        return out


def _cycle(agent, dry_run):
    facts = sense()
    candidates = plan(facts)
    kept, rejected = critic_check(candidates, facts)

    # Answer every proposal FIRST. This used to sit after the "nothing to post"
    # early return, so on a day when the Critic rejected the whole plan not one
    # sector was told why — the precise silent-binning this queue exists to stop.
    answered = 0
    for c in rejected:
        if c.get("proposal_id"):
            try:
                _props.decide(c["proposal_id"], "rejected", c["why"])
                answered += 1
            except Exception:
                pass

    hit = facts.get("hit_rate")
    scoreline = ("no scored decisions yet — no track record to reason from"
                 if hit is None else f"recent hit-rate {hit:.0%}")

    if not kept:
        stuck = [r for r in rejected if r.get("owner_blocked")]
        summary = (f"Nothing to post. {len(rejected)} candidate(s) rejected; {scoreline}."
                   + (f" {len(stuck)} OF YOUR OWN ORDER(S) CANNOT RUN." if stuck else ""))
        return {"ok": True, "agent": agent, "posted": [], "rejected": rejected,
                "owner_blocked": stuck, "answered": answered,
                "facts": facts, "summary": summary}

    if dry_run:
        return {"ok": True, "agent": agent, "dry_run": True, "would_post": kept,
                "rejected": rejected, "facts": facts,
                "summary": f"{len(kept)} task(s) would be posted; nothing written"}

    posted = []
    for c in kept:
        tid = board.post_task(c["sector"], c["title"],
                              c["detail"] + f"\n\n[MD evidence] {c['evidence']}",
                              risk=c.get("risk", "green"), created_by="MD")
        posted.append({"task_id": tid, "sector": c["sector"], "title": c["title"],
                       "evidence": c["evidence"]})
        if c.get("proposal_id"):
            try:
                _props.decide(c["proposal_id"], "accepted",
                              f"posted to {c['sector']} as task {tid}", task_id=tid)
                answered += 1
            except Exception:
                pass

    did = board.log_decision(
        "md-cycle",
        reasoning=(f"Planned from the cabinet: {scoreline}; "
                   f"{facts.get('site', {}).get('critical', 0)} critical and "
                   f"{facts.get('site', {}).get('high', 0)} high site issue(s) open; "
                   f"{facts.get('leads', {}).get('total', 0)} lead(s) on file. "
                   f"{len(kept)} task(s) posted, {len(rejected)} rejected by the Critic."),
        expectation="Each sector claims its task and the day's work actually starts.",
        confidence=0.7,
        # proposed/rejected/sectors are what the MD is MANAGED on, not just what
        # it produced: how much of the plan was guesswork the Critic had to throw
        # out, and which sectors it actually gave work to. The management card in
        # coordination/ reads these. The MD records them and never grades itself.
        evidence={"task_ids": [p["task_id"] for p in posted],
                  "proposed": len(candidates),
                  "rejected": len(rejected),
                  "sectors": sorted({p["sector"] for p in posted}),
                  # sectors it was not free to direct at all, so the scorecard
                  # does not mark it down for neglecting them
                  "blocked_sectors": sorted({r["sector"] for r in rejected
                                             if "shelf is empty" in r["why"]})})

    stuck = [r for r in rejected if r.get("owner_blocked")]
    note = facts.get("ranking_note") or ""
    summary = (f"posted {len(posted)} task(s), Critic rejected {len(rejected)}"
               + (f", answered {answered} proposal(s)" if answered else "")
               + (f" — {len(stuck)} OF YOUR OWN ORDER(S) CANNOT RUN" if stuck else "")
               + (f". {note}" if note else ""))
    return {"ok": True, "agent": agent, "posted": posted, "rejected": rejected,
            "owner_blocked": stuck, "decision_id": did, "facts": facts,
            "summary": summary}


def as_text(out) -> str:
    L = [f"MD CYCLE — {out.get('summary', '')}", ""]
    if out.get("posted") or out.get("would_post"):
        L.append("POSTED TO THE BOARD" if out.get("posted") else "WOULD POST")
        for p in out.get("posted") or out.get("would_post"):
            L.append(f"  + [{p['sector']}] {p['title']}")
            L.append(f"      because: {p['evidence']}")
    if out.get("rejected"):
        L += ["", "REJECTED BY THE CRITIC"]
        for r in out["rejected"]:
            L.append(f"  - [{r['sector']}] {r['title']}")
            L.append(f"      {r['why']}")
    return "\n".join(L)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="The MD's daily planning cycle.")
    ap.add_argument("--dry-run", action="store_true", help="show the plan, post nothing")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    res = run(dry_run=a.dry_run)
    print(json.dumps(res, indent=2, default=str) if a.json else as_text(res))
