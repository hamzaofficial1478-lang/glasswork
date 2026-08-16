"""
report_daily.py - the Report sector. Reads every sector's real numbers, scores
yesterday's decisions against actual evidence in the cabinet, writes the one
daily digest, and sends it to the owner on Telegram. Feeds the same numbers
back for the MD's next SENSE step.

    python -m pipelines.report_daily

SCORED AGAINST THE ROWS THE DECISION WAS ACTUALLY ABOUT

Every decision records, at the moment it is logged, WHICH rows it is a claim
about — the tickets that check filed, the leads that run stored, the keywords
that gap list added (board.log_decision(evidence=...)). Scoring reads those ids
back and asks what became of those specific rows.

That link is the whole point, and this file used to be built without it. It
matched evidence to a decision by wall-clock proximity instead, which was wrong
in three separate ways, all of them flattering:

  - an seo-gap list scored 'worked' off ANY content brief created afterwards,
    including a page the owner asked for by hand;
  - one brief scored EVERY pending gap list at once, because they all matched
    the same "created after" test;
  - a health check looked for its tickets in a +/-5 second window around the
    decision, and read "I could not find them" as "nothing was wrong" — so an
    open, critical, dead enquiry form scored 'worked'.

Three cycles were also scored 'worked' outright, unchecked, on the grounds of
being "self-verifying". They are not: 'no sector acts on unscreened text' and
'the Hunter collects across the whole market' are claims about what happens
NEXT. Nothing that has not been checked is marked as having worked.

THE RULE: a decision is scored only when there is real evidence about its own
rows. Otherwise it waits inside a grace window, and after that it either failed
or is left permanently unscored with the reason recorded. Unscored is a fine
answer. A guessed 'worked' is not — a scorecard that cannot return a loss
teaches the MD nothing, which is worse than having no scorecard at all.
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
    "leads-sources": 7 * 86400,
    "site-services": 7 * 86400,
}

GRACE["security-screen"] = 7 * 86400
GRACE["md-cycle"] = 2 * 86400

# Cycles with no honest check yet. Empty now: security-screen used to sit here
# because proving "no sector acts on unscreened text" needs an outreach trail,
# and nothing wrote one. mail.py records every send to outreach_log now, so the
# claim finally has something real to be checked against.
NO_RULE_YET = {}


def _now():
    return int(time.time())


def _one(con, q, args=(), default=0):
    r = con.execute(q, args).fetchone()
    return r[0] if r and r[0] is not None else default


def _day_start():
    lt = time.localtime()
    return int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)))


def _expired(cycle, created_at):
    """Has this decision waited longer than its cycle's grace window?"""
    return _now() - created_at > GRACE.get(cycle, 7 * 86400)


def _ids(ev, key):
    v = (ev or {}).get(key) or []
    return [x for x in v if x is not None]


def _in(col, ids):
    """A safe IN clause for a list of integer row ids."""
    return f"{col} IN ({','.join('?' * len(ids))})"


def _score_seo_gap(con, created_at, ev):
    """
    Did Content brief a page from THESE phrases? A brief written about something
    else is not evidence that this gap list was any good.
    """
    phrases = [p for p in (ev.get("phrases") or []) if p]
    if not phrases:
        return None, "no phrases recorded on this decision — cannot tell what it claimed"
    rows = con.execute("SELECT brief, title FROM content_queue WHERE created_at>?",
                       (created_at,)).fetchall()
    hits = {p for p in phrases
            for r in rows if p.lower() in ((r[0] or "") + " " + (r[1] or "")).lower()}
    if hits:
        return "worked", (f"{len(hits)} of {len(phrases)} gap phrase(s) were briefed, "
                          f"e.g. {sorted(hits)[0]!r}")
    if _expired("seo-gap", created_at):
        return "failed", f"none of the {len(phrases)} gap phrases was briefed within 5 days"
    return None, "still within the 5-day grace window"


def _score_content_briefs(con, created_at, ev):
    """Did any of the pages THIS run briefed actually move past 'briefed'?"""
    ids = _ids(ev, "content_queue_ids")
    if not ids:
        return None, "no brief ids recorded on this decision"
    rows = con.execute(
        f"SELECT status FROM content_queue WHERE {_in('id', ids)}", ids).fetchall()
    if not rows:
        return "failed", f"all {len(ids)} briefed page(s) have been deleted from the queue"
    moved = [r[0] for r in rows if r[0] not in ("idea", "briefed", "blocked_on_seo")]
    if moved:
        return "worked", f"{len(moved)} of {len(rows)} briefed page(s) moved past 'briefed'"
    if _expired("content-briefs", created_at):
        return "failed", f"none of the {len(rows)} briefed page(s) was drafted within 7 days"
    return None, "still within the 7-day grace window"


def _score_it_health(con, created_at, ev):
    """
    Were the urgent tickets THIS check filed ever resolved?

    'No urgent tickets' only counts as success when the check genuinely filed
    none. It must never be inferred from a lookup that came back empty, which is
    how an open critical issue used to score 'worked'.
    """
    if "site_issue_ids" not in (ev or {}):
        return None, "no ticket ids recorded on this decision — cannot tell what it filed"
    ids = _ids(ev, "site_issue_ids")
    if not ids:
        return "worked", "this check filed no tickets at all — the site was clean"
    rows = con.execute(
        f"SELECT status, severity FROM site_issues WHERE {_in('id', ids)}", ids).fetchall()
    urgent = [r for r in rows if r[1] in ("critical", "high")]
    if not urgent:
        return "worked", f"{len(ids)} ticket(s) filed, none of them critical or high"
    done = [r for r in urgent if r[0] in ("closed", "deployed")]
    if len(done) == len(urgent):
        return "worked", f"all {len(urgent)} urgent ticket(s) from this check are resolved"
    if _expired("it-health", created_at):
        return "failed", (f"{len(urgent) - len(done)} of {len(urgent)} urgent ticket(s) "
                          "from this check still open after 3 days")
    return None, f"{len(urgent) - len(done)} urgent ticket(s) still open, inside the 3-day window"


def _score_leads_hunt(con, created_at, ev):
    """Do the leads THIS run stored carry the service tag it promised?"""
    if "lead_ids" not in (ev or {}):
        return None, "no lead ids recorded on this decision"
    ids = _ids(ev, "lead_ids")
    if not ids:
        return None, "this run stored no leads, so the tagging claim was never tested"
    rows = con.execute(f"SELECT segment FROM leads WHERE {_in('id', ids)}", ids).fetchall()
    if not rows:
        return "failed", f"all {len(ids)} lead(s) from this run have gone from the cabinet"
    tagged = sum(1 for r in rows if (r[0] or "").strip())
    if tagged == len(rows):
        return "worked", f"all {len(rows)} lead(s) from this run carry a service tag"
    if tagged == 0:
        return "failed", f"none of {len(rows)} lead(s) from this run carry a service tag"
    return "partial", f"{tagged}/{len(rows)} lead(s) from this run carry a service tag"


def _score_leads_sources(con, created_at, ev):
    """
    The claim was that the Hunter would then collect across the whole market.
    Measured against the baseline the map recorded, not against nothing.
    """
    before = (ev or {}).get("leads_before")
    if before is None:
        return None, "no lead baseline recorded on this decision"
    rows = con.execute("SELECT DISTINCT segment FROM leads WHERE found_at>?",
                       (created_at,)).fetchall()
    segs = {(r[0] or "").strip() for r in rows if (r[0] or "").strip()}
    if len(segs) > 1:
        return "worked", f"leads have since arrived across {len(segs)} different segments"
    if _expired("leads-sources", created_at):
        if not segs:
            return "failed", "no leads at all were collected from this source map within 7 days"
        return "partial", f"7 days on, leads came from only {len(segs)} segment"
    return None, "still within the 7-day grace window, waiting on the Hunter"


def _score_site_services(con, created_at, ev):
    """The claim is the catalogue matches the site. Did the mismatches get closed?"""
    if ev is None or "uncatalogued" not in ev:
        return None, "no reconciliation recorded on this decision"
    gaps = len(ev.get("uncatalogued") or []) + len(ev.get("missing_from_site") or [])
    if gaps == 0:
        return "worked", "the catalogue already matched the site exactly"
    if _expired("site-services", created_at):
        return "failed", f"{gaps} service mismatch(es) were still unreconciled after 7 days"
    return None, f"{gaps} mismatch(es) waiting on the owner, inside the 7-day window"


def _score_security_screen(con, created_at, ev):
    """
    The claim: no sector acts on outside text that has not been through this gate.

    That is a claim about what happens NEXT, so it is checked against what
    actually went out afterwards. It became checkable only when mail.py started
    writing outreach_log — before that there was no record of any sector acting
    on anything, so this cycle was left permanently unscored, which was honest
    but useless.

    The test is whether outreach left the building while Security still had
    unanswered requests sitting on the board. If it did, some sector went ahead
    without waiting for its clearance, and the gate did not hold.
    """
    sent = _one(con, "SELECT count(*) FROM outreach_log"
                     " WHERE direction='sent' AND at>?", (created_at,))
    if not sent:
        if _expired("security-screen", created_at):
            return None, ("nothing was sent in 7 days, so this gate was never "
                          "actually tested — no verdict either way")
        return None, "waiting: nothing has gone out since the screen ran"

    last_send = _one(con, "SELECT max(at) FROM outreach_log WHERE direction='sent'")
    jumped = _one(con, "SELECT count(*) FROM requests WHERE to_sector='Security'"
                       " AND status IN ('open','claimed') AND created_at<?", (last_send,))
    if jumped:
        return "failed", (f"{sent} mail(s) went out while {jumped} Security "
                          "request(s) were still unanswered — a sector did not wait")
    return "worked", (f"{sent} mail(s) went out and Security had nothing "
                      "unanswered at the time")


def _score_md_cycle(con, created_at, ev):
    """
    Did the day's plan actually turn into work? The MD's claim is that each
    sector claims its task and the day starts, so the test is what became of
    the exact tasks it posted - not whether the board looks busy in general.
    """
    ids = [i for i in (ev or {}).get("task_ids") or [] if i]
    if not ids:
        return None, "no task ids recorded on this decision"
    rows = con.execute(
        f"SELECT status FROM tasks WHERE {_in('id', ids)}", ids).fetchall()
    if not rows:
        return "failed", f"all {len(ids)} task(s) the MD posted have vanished"
    done = [r[0] for r in rows if r[0] == "done"]
    touched = [r[0] for r in rows if r[0] in ("in_progress", "done", "blocked")]
    if len(done) == len(rows):
        return "worked", f"all {len(rows)} task(s) the MD posted were completed"
    if _expired("md-cycle", created_at):
        if not touched:
            return "failed", (f"none of the {len(rows)} task(s) was even claimed in 2 days "
                              "- the MD planned work nobody could do")
        return "partial", f"{len(done)}/{len(rows)} of the MD's task(s) finished in 2 days"
    return None, f"{len(done)}/{len(rows)} done, inside the 2-day window"


SCORERS = {
    "md-cycle": _score_md_cycle,
    "security-screen": _score_security_screen,
    "seo-gap": _score_seo_gap,
    "content-briefs": _score_content_briefs,
    "it-health": _score_it_health,
    "leads-hunt": _score_leads_hunt,
    "leads-sources": _score_leads_sources,
    "site-services": _score_site_services,
}


def score_pending_decisions(con):
    """
    Score every decision still waiting on an outcome, against its own recorded
    evidence. Returns only what it actually scored — anything left waiting is
    reported separately rather than being forced to a verdict.
    """
    board.ensure_schema(con)
    rows = con.execute("SELECT id, cycle, created_at, evidence FROM decisions"
                       " WHERE outcome IS NULL ORDER BY created_at").fetchall()
    scored, waiting = [], []
    for did, cycle, created_at, raw in rows:
        try:
            ev = json.loads(raw) if raw else None
        except (TypeError, ValueError):
            ev = None

        if cycle in NO_RULE_YET:
            outcome, why = None, NO_RULE_YET[cycle]
        elif cycle not in SCORERS:
            outcome, why = None, f"unknown cycle {cycle!r} — no scoring rule written for it"
        elif ev is None:
            # Logged before decisions carried their evidence. There is no honest
            # way to score it now, and inventing one is how the scorecard lied.
            outcome, why = None, "logged before decisions recorded their evidence — unscorable"
        else:
            outcome, why = SCORERS[cycle](con, created_at, ev)

        entry = {"decision_id": did, "cycle": cycle, "outcome": outcome, "why": why}
        if outcome:
            board.score_decision(did, outcome, note=why)
            scored.append(entry)
        else:
            waiting.append(entry)
    return scored, waiting


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


def critic_check(state, summary=None):
    """
    Report's own gate: a digest may not ship on a cabinet it could not read.

    This runs BEFORE the digest is built, on the raw state, because a guard that
    fires after the numbers have already been pulled out of `state` is not a
    guard — the missing key crashes first. It also has to check the keys it
    actually needs rather than a general 'ok' flag: state.build() never returns
    ok=False (it raises instead), so testing that flag could never reject
    anything and the gate was decorative.
    """
    reasons = []
    if not isinstance(state, dict):
        reasons.append("could not read the cabinet state - nothing to report on")
    else:
        if not state.get("ok"):
            reasons.append("the cabinet state came back marked not-ok")
        metrics = state.get("metrics")
        if not isinstance(metrics, dict):
            reasons.append("no metrics in the cabinet state - nothing to report on")
        else:
            for key in ("health", "spend"):
                if metrics.get(key) is None:
                    reasons.append(f"the state has no '{key}' figure to report")
        if not isinstance(state.get("rooms"), list):
            reasons.append("no per-sector rooms in the state - cannot score the sectors")
    if summary is not None and not str(summary).strip():
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
        scored, waiting = score_pending_decisions(con)
        con.commit()

        # The critic goes first, on the raw state, before a single figure is
        # pulled out of it. Reading the numbers and then asking whether they
        # were readable is the wrong way round.
        state = _state.build()  # the exact numbers the dashboard already shows
        ok, reasons = critic_check(state)
        if not ok:
            return {"ok": True, "agent": agent, "critic": "rejected",
                    "critic_reasons": reasons,
                    "decisions_scored": scored, "decisions_waiting": waiting}

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
                    "critic_reasons": reasons,
                    "decisions_scored": scored, "decisions_waiting": waiting}

        day = time.strftime("%Y-%m-%d", time.localtime())
        sector_scores = {r["key"]: r["score"] for r in state["rooms"]}
        # One digest per day. Running Report twice used to leave two rows for the
        # same date and no way to tell which one the owner actually read.
        con.execute("DELETE FROM daily_reports WHERE day=?", (day,))
        con.execute(
            "INSERT INTO daily_reports(day,health_score,sector_scores,summary,actions,"
            "spend_usd,created_at) VALUES(?,?,?,?,?,?,?)",
            (day, state["metrics"]["health"], json.dumps(sector_scores), summary,
             json.dumps(actions), spend, _now()))
        con.commit()

        tally = ""
        if scored:
            tally = (f" ({sum(1 for s in scored if s['outcome'] == 'worked')} worked, "
                     f"{sum(1 for s in scored if s['outcome'] == 'failed')} failed, "
                     f"{sum(1 for s in scored if s['outcome'] == 'partial')} partial)")
        telegram_text = (
            f"Daily digest - {day}\n\n"
            f"Health: {state['metrics']['health']}/100\n" + summary + "\n\n"
            "Top 3 today:\n" + "\n".join(f"- {a}" for a in actions) + "\n\n"
            f"Decisions scored: {len(scored)}" + tally
            # An unscored decision is a real state, not a rounding error. Saying
            # so keeps the owner from reading "3 scored, all worked" as "all is
            # well" when nine others could not be checked at all.
            + (f"\nStill unscored: {len(waiting)}" if waiting else "")
        )
        tg = integrations.send_message(telegram_text)

        return {"ok": True, "agent": agent, "critic": "passed", "day": day,
                "health_score": state["metrics"]["health"], "sector_scores": sector_scores,
                "decisions_scored": scored, "decisions_waiting": waiting,
                "summary": summary, "actions": actions,
                "spend_usd": spend, "telegram_sent": tg.get("ok"), "telegram_detail": tg.get("detail")}
    finally:
        con.close()


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
