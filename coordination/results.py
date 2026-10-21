"""
results.py — what each sector actually produced. Not what it is, what it made.

    python -m coordination.results            today, every sector
    python -m coordination.results --sector Leads
    python -m coordination.results --days 7
    python -m coordination.results --json

"there is no any section that shows the results of the sectors."

THE THING THAT WAS MISSING, PRECISELY

The office had three screens that each answered a different question and none
of them answered his.

  the floor      which desks are lit. That is who is working, not what came out.
  the run panel  which steps ran. 'SEO finds the gaps — finished' tells you the
                 program did not crash. It does not tell you it found nothing.
  the control    a per-sector sheet listing the last forty rows of all time. On
                 day ninety that is a wall of history with today buried in it.

He presses Run a Day and wants to know what came out of it. That is a fourth
question and nothing answered it.

AND THE HALF THAT MATTERS MORE: WHY NOT

A results screen that only shows what was produced is fine on a good day and
useless on the day he actually goes looking, which is the day something is
wrong. So every sector that produced nothing has to say why, in his words, from
evidence rather than from a shrug:

    the sector is switched off — he did that, and it says when and why
    its step sat out  — the step's own summary is the reason
    its step failed   — same, and it reads as a failure rather than a quiet zero
    the day never ran — no run today, so nothing was ever asked of it
    it ran and found nothing — which IS a result, and an honest one

That list is derived from the day_run rows and the sector switches, so it
cannot drift from what actually happened. There is no field anywhere that an
agent has to remember to fill in.
"""
import argparse
import json
import sqlite3
import sys
import time

from . import board
from .state import SECTORS, _day_start, _hhmm

# What "produced something" means for each sector, as the rows a person would
# actually want to read. Deliberately the same tables the sector's own work
# writes — there is no results table an agent has to remember to fill in, and
# so there is nothing that can silently stop being filled in.
#
# (table label, timestamp column, sql with :since, the column names to show)
MADE = {
    "Director": (
        "decisions made", "created_at",
        "SELECT cycle AS a, reasoning AS b, confidence AS c,"
        " COALESCE(outcome,'not scored yet') AS d, created_at AS at"
        " FROM decisions WHERE created_at>=? ORDER BY created_at DESC LIMIT 60",
        ("about", "the reasoning", "confidence", "did it work")),
    "IT": (
        "site tickets", "found_at",
        "SELECT kind AS a, detail AS b, severity AS c, status AS d,"
        " found_at AS at FROM site_issues WHERE found_at>=?"
        " ORDER BY found_at DESC LIMIT 60",
        ("kind", "what is wrong", "how bad", "where it got to")),
    # 'type', not 'service'. content_queue has never had a service column, and
    # the identical query in state._DETAIL has been selecting one since the day
    # it was written — swallowed by a bare except, so the control room's Content
    # sheet has silently shown nothing for as long as it has existed. Caught by
    # trying to insert a row into it. See test_every_query_actually_runs.
    "Content": (
        "pages written or briefed", "created_at",
        "SELECT title AS a, COALESCE(brief,'') AS b, status AS c,"
        " COALESCE(type,'') AS d, created_at AS at FROM content_queue"
        " WHERE created_at>=? ORDER BY created_at DESC LIMIT 60",
        ("page", "the brief", "where it got to", "kind")),
    "SEO": (
        "keywords found", "seen_at",
        "SELECT phrase AS a, evidence AS b, score AS c, sources AS d,"
        " seen_at AS at FROM keyword_intel WHERE seen_at>=?"
        " ORDER BY score DESC LIMIT 60",
        ("search", "why we think so", "score", "where from")),
    "Leads": (
        "businesses found", "found_at",
        "SELECT COALESCE(company,'') AS a, COALESCE(email,COALESCE(phone,'')) AS b,"
        " COALESCE(area,'') AS c, stage AS d, found_at AS at FROM leads"
        " WHERE found_at>=? ORDER BY found_at DESC LIMIT 60",
        ("business", "how to reach them", "area", "where it got to")),
    # health_score and severity, not health and verdict. Both of these were
    # wrong in state._DETAIL too — see the note on Content. Three of the seven
    # control-room sheets have been silently empty since they were written.
    "Report": (
        "reports", "created_at",
        "SELECT day AS a, summary AS b, COALESCE(health_score,'') AS c,"
        " COALESCE(spend_usd,'') AS d, created_at AS at FROM daily_reports"
        " WHERE created_at>=? ORDER BY created_at DESC LIMIT 60",
        ("day", "what happened", "health", "spend")),
    "Security": (
        "things screened", "at",
        "SELECT kind AS a, COALESCE(detail,'') AS b, COALESCE(action,'') AS c,"
        " COALESCE(source,'') AS d, at FROM security_events WHERE at>=?"
        " ORDER BY at DESC LIMIT 60",
        ("kind", "what it was", "what we did", "where from")),
}

# The headline count or two a person reads before anything else. One query each,
# and every one of them is a fact about TODAY rather than about all time — the
# number he wants after pressing Run is "what did that get me", not "what is on
# file".
HEADLINE = {
    "Director": [
        ("calls made", "SELECT count(*) FROM decisions WHERE created_at>=?"),
        ("tasks posted", "SELECT count(*) FROM tasks WHERE created_at>=?"
                         " AND created_by='MD'"),
    ],
    "IT": [
        ("tickets filed", "SELECT count(*) FROM site_issues WHERE found_at>=?"),
        ("changes to your site",
         "SELECT count(*) FROM site_changes WHERE at>=? AND source='agency'"
         " AND ok=1"),
        ("waiting on your yes",
         "SELECT count(*) FROM site_queue WHERE status='pending'"),
    ],
    "Content": [
        ("pages briefed", "SELECT count(*) FROM content_queue WHERE created_at>=?"),
        ("waiting to be written",
         "SELECT count(*) FROM content_queue WHERE status IN"
         " ('idea','briefed','approved')"),
    ],
    "SEO": [
        ("searches found", "SELECT count(*) FROM keyword_intel WHERE seen_at>=?"),
        ("gaps on file", "SELECT count(*) FROM keywords"),
    ],
    "Leads": [
        ("businesses found", "SELECT count(*) FROM leads WHERE found_at>=?"),
        ("with a way to reach them",
         "SELECT count(*) FROM leads WHERE found_at>=? AND"
         " (COALESCE(email,'')!='' OR COALESCE(phone,'')!='')"),
        ("written to", "SELECT count(*) FROM outreach_log WHERE at>=?"
                       " AND direction='out'"),
        ("replies", "SELECT count(*) FROM outreach_log WHERE at>=?"
                    " AND direction='reply'"),
    ],
    "Report": [
        ("reports written", "SELECT count(*) FROM daily_reports WHERE created_at>=?"),
        ("calls still unscored",
         "SELECT count(*) FROM decisions WHERE outcome IS NULL"),
    ],
    "Security": [
        ("things screened", "SELECT count(*) FROM security_events WHERE at>=?"),
        ("stopped", "SELECT count(*) FROM security_events WHERE at>=? AND"
                    " kind IN ('scam_lead','fraud','phishing')"),
    ],
}


def one_of(n, label) -> str:
    """
    '1 call made', not '1 calls made'.

    Small, and worth doing: he reads this screen every morning, and a program
    that cannot count to one in English reads as a program nobody finished.
    Only the FIRST word is touched, because that is the noun in every label
    here — 'calls made', 'tickets filed', 'searches found'. A label that does
    not start with a noun ('written to', 'waiting on your yes') is left exactly
    as it is rather than mangled by a rule that does not apply to it.
    """
    if n == 1 and label:
        first, _, rest = label.partition(" ")
        low = first.lower()
        if low.endswith("ies") and len(first) > 4:
            first = first[:-3] + "y"
        elif low.endswith(("sses", "ches", "shes", "xes")):
            first = first[:-2]
        elif low.endswith("s") and not low.endswith("ss"):
            first = first[:-1]
        else:
            return label
        return (first + " " + rest).strip()
    return label


def _one(c, q, args=(), default=0):
    try:
        r = c.execute(q, args).fetchone()
        return default if r is None or r[0] is None else r[0]
    except sqlite3.OperationalError:
        return default


def _rows(c, q, args=()):
    try:
        return [dict(r) for r in c.execute(q, args)]
    except sqlite3.OperationalError:
        return []


def _steps_of(c, sector, run_id):
    """What the last run asked this sector to do, and what came back."""
    if not run_id:
        return []
    every = _rows(c, "SELECT label, module, status, summary, started_at, ended_at"
                     " FROM day_steps WHERE run_id=? ORDER BY seq", (run_id,))
    return [s for s in every if _sector_of_module(s["module"]) == sector]


# Which sector a pipeline belongs to. Read off day_run.STEPS rather than kept as
# a second list here — a third copy of that mapping is a third thing to forget
# to update, and this file already learned that lesson from _ADDED_TABLES.
_MODULE_SECTOR = None


def _sector_of_module(module):
    global _MODULE_SECTOR
    if _MODULE_SECTOR is None:
        _MODULE_SECTOR = {}
        try:
            from pipelines import day_run
            for _label, mod, room, _lane in getattr(day_run, "STEPS", ()):
                _MODULE_SECTOR[mod] = room
        except Exception:
            _MODULE_SECTOR = {}
    return _MODULE_SECTOR.get(module, "")


def _has_a_step(sector) -> bool:
    """Does the daily run, as it stands today, ask this sector for anything?"""
    _sector_of_module("")                      # builds the map on first call
    return sector in set((_MODULE_SECTOR or {}).values())


def _why_nothing(c, key, switches, steps, run, made_n):
    """
    Why did this sector produce nothing? Answered from evidence, never guessed.

    Ordered by what is actually true first, because more than one can be — a
    switched-off sector also has no steps, and telling him "the day never ran"
    when he switched it off himself is the screen being unhelpful on purpose.
    """
    if made_n:
        return ""
    sw = switches.get(key) or {}
    if sw and not sw.get("enabled", True):
        note = (sw.get("note") or "").strip()
        return ("you switched this sector off"
                + (f" — \"{note}\"" if note else "")
                + ". Turn it back on and it works the next day you run.")
    if not run:
        return ("the agency has not been run yet, so nothing has been asked of "
                "this sector. Press Run the day.")
    bad = [s for s in steps if s["status"] == "failed"]
    if bad:
        return ("its work failed: "
                + "; ".join(f"{s['label']} — {s['summary'] or 'no detail'}"
                            for s in bad[:2]))
    cut = [s for s in steps if s["status"] == "stopped"]
    if cut:
        return "you stopped the day before this sector got to it."
    skipped = [s for s in steps if s["status"] == "skipped"]
    if skipped and not [s for s in steps if s["status"] in ("ok", "idle")]:
        return "its step was skipped — the sector is switched off."
    idle = [s for s in steps if s["status"] == "idle"]
    if idle:
        return ("it ran and had nothing to do: "
                + "; ".join(f"{s['label']} — {s['summary'] or 'no detail'}"
                            for s in idle[:2]))
    waiting = [s for s in steps if s["status"] in ("waiting", "running")]
    if waiting:
        return "it has not got to this yet — the day is still going."
    if not steps:
        # TWO DIFFERENT THINGS, AND ONLY ONE OF THEM IS A BUG.
        #
        # The first version read the LAST RUN's steps and, finding none for
        # this sector, announced "there is no step for this sector — that is a
        # gap in the run". That is a strong claim and it was wrong whenever the
        # last run happened to predate a step being added, which made a normal
        # upgrade look like a hole in the program.
        #
        # Whether a sector has a step is a question about the run list as it
        # stands now, so ask that instead.
        if not _has_a_step(key):
            return ("no step in the daily run belongs to this sector, so it is "
                    "never asked to do anything. That is a gap in the run, not "
                    "in the sector.")
        return ("the last run did not include this sector's step — it predates "
                "it. Run the day again and it will.")
    return ("it ran, finished cleanly, and produced nothing new. Sometimes that "
            "is the honest answer — there was nothing new to find.")


def _last_run(c):
    r = _rows(c, "SELECT id, started_at, ended_at, status, steps_total,"
                 " steps_done FROM day_runs ORDER BY started_at DESC LIMIT 1")
    return r[0] if r else None


def results(days=0, sector=None) -> dict:
    """
    Everything every sector produced, and why the quiet ones were quiet.

    days=0 means today. days=7 means the last seven days, which is the view
    worth having on a Monday morning — a single bad day looks like a broken
    agency and a week rarely does.
    """
    board.ensure_schema()
    c = sqlite3.connect(board.DB, timeout=30)
    c.row_factory = sqlite3.Row
    try:
        now = int(time.time())
        since = _day_start() if not days else now - int(days) * 86400
        run = _last_run(c)
        try:
            switches = board.sector_switches()
        except Exception:
            switches = {}

        out = []
        for key, name, _match, _sec in SECTORS:
            if sector and key.lower() != sector.lower():
                continue
            label, _ts, sql, cols = MADE.get(key, ("output", "", "", ()))
            items = []
            if sql:
                for r in _rows(c, sql, (since,)):
                    items.append({"cols": [r["a"], r["b"], r["c"], r["d"]],
                                  "at": _hhmm(r["at"]), "ts": r["at"]})
            counts = []
            for cap, q in HEADLINE.get(key, []):
                n = _one(c, q, (since,) * q.count("?"))
                counts.append({"label": one_of(n, cap), "n": n})
            steps = _steps_of(c, key, (run or {}).get("id"))
            sw = switches.get(key) or {}
            out.append({
                "key": key, "name": name,
                "enabled": bool(sw.get("enabled", True)) if sw else True,
                "made_label": label, "columns": list(cols),
                "counts": counts, "items": items, "made": len(items),
                "steps": [{"label": s["label"], "status": s["status"],
                           "summary": s["summary"] or ""} for s in steps],
                "nothing_because": _why_nothing(c, key, switches, steps, run,
                                                len(items)),
            })
    finally:
        c.close()

    total = sum(s["made"] for s in out)
    quiet = [s["name"] for s in out if not s["made"]]
    when = "today" if not days else f"the last {days} days"
    return {"ok": True, "since": since, "days": days, "when": when,
            "run": run, "sectors": out, "total": total,
            "head": (f"{total} thing(s) produced {when} across "
                     f"{len(out) - len(quiet)} of {len(out)} sectors"
                     + (f". Quiet: {', '.join(quiet)}." if quiet else ".")
                     if total else
                     f"Nothing was produced {when}."
                     + (" The agency has not been run yet." if not run else ""))}


def as_text(r) -> str:
    if not r.get("ok"):
        return "RESULTS — " + (r.get("error") or "could not read")
    out = ["RESULTS — " + r["head"], ""]
    for s in r["sectors"]:
        line = f"  {s['name']}"
        if s["counts"]:
            line += "   " + ", ".join(f"{c['n']} {c['label']}" for c in s["counts"])
        out.append(line)
        if s["nothing_because"]:
            out.append(f"      nothing, because {s['nothing_because']}")
        for it in s["items"][:5]:
            first = str(it["cols"][0] or "")[:60]
            second = str(it["cols"][1] or "")[:70]
            out.append(f"      {it['at']}  {first:<60} {second}")
        if s["made"] > 5:
            out.append(f"      ... and {s['made'] - 5} more")
        out.append("")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="What each sector produced.")
    ap.add_argument("--days", type=int, default=0, help="0 = today")
    ap.add_argument("--sector", default="", help="just this one")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    r = results(days=a.days, sector=a.sector or None)
    print(json.dumps(r, indent=2) if a.json else as_text(r))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
