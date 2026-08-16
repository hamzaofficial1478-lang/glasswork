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

from coordination import board, briefing, state as _state, targets

MAX_TASKS_PER_CYCLE = 6      # a plan nobody can finish is not a plan


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
        facts["recent_failures"] = [
            r[0] for r in con.execute(
                "SELECT cycle FROM decisions WHERE outcome='failed'"
                " ORDER BY scored_at DESC LIMIT 8") if r[0]]
    finally:
        con.close()
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

    crit = site.get("critical", 0)
    if crit:
        out.append({
            "sector": "IT", "risk": "green",
            "title": "Re-check the enquiry path after critical findings",
            "detail": ("Read-only re-check of the enquiry path first, then hygiene. "
                       "File findings; queue any fix, never apply it."),
            "evidence": f"{crit} critical site issue(s) open in site_issues"})

    high = site.get("high", 0)
    if high and not crit:
        out.append({
            "sector": "IT", "risk": "green",
            "title": "Read-only health check on the site",
            "detail": "Enquiry path first, then hygiene. File findings only.",
            "evidence": f"{high} high-severity site issue(s) open in site_issues"})

    checks = _target(facts, "site_checks")
    if checks.get("per_day") and checks.get("remaining"):
        out.append({
            "sector": "IT", "risk": "green",
            "title": "Daily website diagnostic",
            "detail": "The owner's daily site check. Read-only; file what is wrong.",
            "evidence": (f"owner asked for {checks['per_day']} site check(s) today, "
                         f"{checks['done_today']} done")})

    if not facts.get("source_map_done"):
        out.append({
            "sector": "Leads", "risk": "green",
            "title": "Map the lead sources across every buying segment",
            "detail": ("Build the source map: which directories and segments to work, "
                       "each tied to the service that segment actually buys. "
                       "Collect nothing yet."),
            "evidence": "no leads-sources decision on file — the map has never been built"})

    lead_t = _target(facts, "leads")
    if lead_t.get("per_day") and lead_t.get("remaining"):
        out.append({
            "sector": "Leads", "risk": "green",
            "title": "Collect today's leads across the mapped segments",
            "detail": ("Work the source map. Every lead carries the service that "
                       "business needs. Dedup before storing; contact nobody."),
            "evidence": (f"owner asked for {lead_t['per_day']} lead(s) today, "
                         f"{lead_t['done_today']} collected")})

    if facts.get("approvals"):
        out.append({
            "sector": "Report", "risk": "green",
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
        if key in seen:
            rejected.append({**c, "why": "duplicated inside this same plan"})
            continue
        need = NEEDS.get(c["sector"])
        if need and need in missing:
            rejected.append({**c, "why": f"the {need} shelf is empty — this would fail "
                                         "the moment it ran"})
            continue
        if failures.count(c.get("cycle", "")) >= 3:
            rejected.append({**c, "why": "this line of reasoning has failed three times"})
            continue
        if c.get("risk", "green") != "green":
            rejected.append({**c, "why": "not green — goes to the owner, not the board"})
            continue

        seen.add(key)
        kept.append(c)

    if len(kept) > MAX_TASKS_PER_CYCLE:
        for c in kept[MAX_TASKS_PER_CYCLE:]:
            rejected.append({**c, "why": "over the day's task ceiling — kept for tomorrow"})
        kept = kept[:MAX_TASKS_PER_CYCLE]
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

    hit = facts.get("hit_rate")
    scoreline = ("no scored decisions yet — no track record to reason from"
                 if hit is None else f"recent hit-rate {hit:.0%}")

    if not kept:
        summary = (f"Nothing to post. {len(rejected)} candidate(s) rejected; {scoreline}.")
        return {"ok": True, "agent": agent, "posted": [], "rejected": rejected,
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

    summary = f"posted {len(posted)} task(s), Critic rejected {len(rejected)}"
    return {"ok": True, "agent": agent, "posted": posted, "rejected": rejected,
            "decision_id": did, "facts": facts, "summary": summary}


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
