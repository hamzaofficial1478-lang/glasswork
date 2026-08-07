"""
report_daily.py - the Report sector. Reads every sector's real numbers, scores
yesterday's decisions against actual evidence in the cabinet, writes the one
daily digest, and sends it to the owner on Telegram. Feeds the same numbers
back for the MD's next SENSE step.

    python -m pipelines.report_daily

TWO KINDS OF DECISION, SCORED HONESTLY

  SELF-VERIFYING   the decision's own pipeline already gates log_decision()
                    behind its own critic_check passing (security-screen,
                    site-services, leads-sources, leads-hunt) so the
                    expectation is already true the moment Report sees it.
                    leads-hunt still gets a real check (are the new leads
                    actually tagged?) because that is cheap evidence sitting
                    right there; the other three are scored 'worked' outright.

  DOWNSTREAM        the expectation depends on ANOTHER sector acting later
                    (seo-gap -> Content briefs it; content-briefs -> the
                    Writer drafts one; it-health -> the fix lands). These wait
                    for real evidence in the cabinet, inside a grace window
                    per cycle. Inside the window with no evidence yet -> left
                    UNSCORED. Never guessed, never forced to a verdict early.

This is the same rule the rest of the agency lives by: no decision without
evidence. Report does not get an exception for its own scorecard.
"""
import json
import sqlite3
import time

from coordination import board, state as _state
from providers import integrations

GRACE = {
    "seo-gap": 5 * 86400,
    "content-briefs": 7 * 86400,
    "it-health": 3 * 86400,
}
SELF_VERIFYING = ("security-screen", "site-services", "leads-sources")


def _now():
    return int(time.time())


def _one(con, q, args=(), default=0):
    r = con.execute(q, args).fetchone()
    return r[0] if r and r[0] is not None else default


def _day_start():
    lt = time.localtime()
    return int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)))


def _score_seo_gap(con, created_at):
    n = _one(con, "SELECT count(*) FROM content_queue WHERE created_at>?", (created_at,))
    if n > 0:
        return "worked", f"{n} content brief(s) created since"
    if _now() - created_at > GRACE["seo-gap"]:
        return "failed", "no content briefed from this gap list after 5 days"
    return None, "still within the 5-day grace window"


def _score_content_briefs(con, created_at):
    n = _one(con, "SELECT count(*) FROM content_queue WHERE created_at>? AND"
                  " status NOT IN ('idea','briefed')", (created_at,))
    if n > 0:
        return "worked", f"{n} brief(s) moved past 'briefed'"
    if _now() - created_at > GRACE["content-briefs"]:
        return "failed", "no brief was drafted from the template page after 7 days"
    return None, "still within the 7-day grace window"


def _score_it_health(con, created_at):
    rows = con.execute(
        "SELECT status, severity FROM site_issues WHERE found_at BETWEEN ? AND ?",
        (created_at - 5, created_at + 5)).fetchall()
    urgent = [r for r in rows if r[1] in ("critical", "high")]
    if not urgent:
        return "worked", "nothing critical or high was filed on this check"
    if all(r[0] in ("closed", "deployed") for r in urgent):
        return "worked", f"all {len(urgent)} urgent issue(s) from this check are resolved"
    if _now() - created_at > GRACE["it-health"]:
        still_open = sum(1 for r in urgent if r[0] not in ("closed", "deployed"))
        return "failed", f"{still_open} urgent issue(s) from this check still open after 3 days"
    return None, "still within the 3-day grace window"


def _score_leads_hunt(con, created_at):
    rows = con.execute(
        "SELECT segment FROM leads WHERE found_at BETWEEN ? AND ?",
        (created_at - 5, created_at + 5)).fetchall()
    if not rows:
        return "worked", "no leads were stored on this run to mis-tag"
    tagged = sum(1 for r in rows if (r[0] or "").strip())
    if tagged == len(rows):
        return "worked", f"all {len(rows)} new lead(s) carry a service tag"
    if tagged == 0:
        return "failed", f"none of {len(rows)} new lead(s) carry a service tag"
    return "partial", f"{tagged}/{len(rows)} new lead(s) carry a service tag"


def score_pending_decisions(con):
    """Score every decision still waiting on an outcome. Returns what it scored."""
    rows = con.execute("SELECT id, cycle, created_at FROM decisions"
                       " WHERE outcome IS NULL ORDER BY created_at").fetchall()
    scored = []
    for did, cycle, created_at in rows:
        if cycle in SELF_VERIFYING:
            outcome, why = "worked", "self-verifying: this sector's own critic already confirmed it"
        elif cycle == "leads-hunt":
            outcome, why = _score_leads_hunt(con, created_at)
        elif cycle == "seo-gap":
            outcome, why = _score_seo_gap(con, created_at)
        elif cycle == "content-briefs":
            outcome, why = _score_content_briefs(con, created_at)
        elif cycle == "it-health":
            outcome, why = _score_it_health(con, created_at)
        else:
            outcome, why = None, f"unknown cycle '{cycle}' - no scoring rule written for it"
        if outcome:
            board.score_decision(did, outcome)
            scored.append({"decision_id": did, "cycle": cycle, "outcome": outcome, "why": why})
    return scored


def _leads_summary(con, day0):
    by_stage = {r[0]: r[1] for r in con.execute("SELECT stage, count(*) FROM leads GROUP BY stage")}
    today = _one(con, "SELECT count(*) FROM leads WHERE found_at>=?", (day0,))
    return today, by_stage


def _site_severity(con):
    return {r[0]: r[1] for r in con.execute(
        "SELECT severity, count(*) FROM site_issues"
        " WHERE status IN ('open','fixing','in_review') GROUP BY severity")}


def _competitor_moves(con):
    """Honest, not invented: no competitor pipeline is built yet, so say so."""
    n = _one(con, "SELECT count(*) FROM competitors")
    if n == 0:
        return "no competitor tracking pipeline is built yet - 0 competitors on file"
    notes = [r[0] for r in con.execute(
        "SELECT notes FROM competitor_snapshots WHERE notes IS NOT NULL AND notes!=''"
        " ORDER BY taken_at DESC LIMIT 3")]
    return "; ".join(notes) if notes else f"{n} competitor(s) on file, no recent snapshot notes"


def _prioritised_actions(site_sev, leads_today, approvals_pending):
    """Three real things to do today, ranked by what actually costs the business money."""
    actions = []
    if site_sev.get("critical"):
        actions.append(f"Fix {site_sev['critical']} critical site issue(s) - "
                        "the enquiry path may be broken right now.")
    if approvals_pending:
        actions.append(f"Approve {approvals_pending} item(s) waiting on your Telegram nod.")
    if site_sev.get("high"):
        actions.append(f"Fix {site_sev['high']} high-severity site issue(s).")
    if leads_today:
        actions.append(f"{leads_today} new lead(s) came in today - check they're "
                        "being verified and cleared before outreach.")
    if not actions:
        actions.append("Nothing urgent today - the board is clear.")
    return actions[:3]


def critic_check(state, summary):
    """Report's own gate: a digest may not ship on a cabinet it could not read."""
    reasons = []
    if not state or not state.get("ok"):
        reasons.append("could not read the cabinet state - nothing to report on")
    if not (summary or "").strip():
        reasons.append("empty summary - nothing was actually said")
    return (len(reasons) == 0, reasons)


def run(agent="reporter"):
    with board.agent_run(agent) as run_row:
        out = _report(agent)
        run_row.note(out.get("summary") or out.get("error") or "produced the daily digest")
        return out


def _report(agent):
    con = sqlite3.connect(board.DB)
    try:
        scored = score_pending_decisions(con)
        con.commit()

        state = _state.build()  # the exact numbers the dashboard already shows
        day0 = _day_start()
        leads_today, by_stage = _leads_summary(con, day0)
        site_sev = _site_severity(con)
        competitor_notes = _competitor_moves(con)
        try:
            spend = float(str(state["metrics"]["spend"]).lstrip("$") or 0)
        except ValueError:
            spend = 0.0
        approvals_pending = len(state.get("approvals", []))
        actions = _prioritised_actions(site_sev, leads_today, approvals_pending)
        open_issues = sum(site_sev.values())

        summary = (f"Health {state['metrics']['health']}/100. "
                   f"{leads_today} lead(s) today, {sum(by_stage.values())} total in the cabinet. "
                   f"{open_issues} open site issue(s) "
                   f"({site_sev.get('critical', 0)} critical, {site_sev.get('high', 0)} high). "
                   f"${spend:.2f} spent today. Competitors: {competitor_notes}.")

        ok, reasons = critic_check(state, summary)
        if not ok:
            return {"ok": True, "agent": agent, "critic": "rejected",
                    "critic_reasons": reasons, "decisions_scored": scored}

        day = time.strftime("%Y-%m-%d", time.localtime())
        sector_scores = {r["key"]: r["score"] for r in state["rooms"]}
        con.execute(
            "INSERT INTO daily_reports(day,health_score,sector_scores,summary,actions,"
            "spend_usd,created_at) VALUES(?,?,?,?,?,?,?)",
            (day, state["metrics"]["health"], json.dumps(sector_scores), summary,
             json.dumps(actions), spend, _now()))
        con.commit()

        telegram_text = (
            f"Daily digest - {day}\n\n"
            f"Health: {state['metrics']['health']}/100\n" + summary + "\n\n"
            "Top 3 today:\n" + "\n".join(f"- {a}" for a in actions) + "\n\n"
            f"Decisions scored: {len(scored)}"
            + (f" ({sum(1 for s in scored if s['outcome']=='worked')} worked, "
               f"{sum(1 for s in scored if s['outcome']=='failed')} failed, "
               f"{sum(1 for s in scored if s['outcome']=='partial')} partial)" if scored else "")
        )
        tg = integrations.send_message(telegram_text)

        return {"ok": True, "agent": agent, "critic": "passed", "day": day,
                "health_score": state["metrics"]["health"], "sector_scores": sector_scores,
                "decisions_scored": scored, "summary": summary, "actions": actions,
                "spend_usd": spend, "telegram_sent": tg.get("ok"), "telegram_detail": tg.get("detail")}
    finally:
        con.close()


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
