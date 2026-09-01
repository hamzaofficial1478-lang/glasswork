"""
targets.py — the owner's daily orders, and how the day is actually going.

    python -m coordination.targets                 (see today)
    python -m coordination.targets --set leads=40 --set mails=25
    python -m coordination.targets --orders "focus on Sharjah hotels this week"

WHY THIS EXISTS

Until now the agency decided its own workload, which sounds autonomous and is
actually the problem: an MD with no number to hit either does nothing or does
too much, and neither is visible until the damage is done. The owner setting
"40 leads, 25 mails, 1 post, 1 site check" turns a vague instruction into
something every sector can plan against and the Report can score.

Progress is MEASURED, never self-reported. Each job counts rows in the cabinet
that only exist because the work really happened — leads found today, mails
actually logged as delivered, pages that reached 'published', IT runs that
finished clean. An agent cannot tick its own box.

THE CAPS ARE NOT SUGGESTIONS

Outreach volume is the one number here that can do lasting damage. Push 400
cold mails a day out of a young domain and it lands in spam permanently, taking
every future lead with it — you do not get that reputation back by slowing down
later. So each job carries a ceiling. The owner can still go above it, because
the handbook makes him the tie-breaker, but it is flagged loudly rather than
accepted quietly, and the flag travels with the target into the MD's plan.
"""
import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

DB = Path(__file__).parent / "agency.db"

# Each job: what it is, how progress is counted, and the point past which the
# number stops being ambition and starts being a liability.
JOBS = {
    "leads": {
        "label": "New leads collected",
        "unit": "leads",
        "ceiling": 200,
        "why_ceiling": "Collecting is cheap; the limit is how many you can actually "
                       "follow up. A pile nobody contacts is not an asset.",
        "sql": "SELECT count(*) FROM leads WHERE found_at>=?",
        "does_it": "python -m pipelines.leads_hunt",
    },
    "mails": {
        "label": "Outreach emails delivered",
        "unit": "mails",
        "ceiling": 50,
        "why_ceiling": "The one number that can do permanent damage. A young domain "
                       "pushing volume gets filed as spam for good, and that takes "
                       "every future lead with it. Ramp up slowly or not at all.",
        "sql": "SELECT count(*) FROM outreach_log WHERE direction='sent' AND at>=?",
        "does_it": "outreach — not switched on until you approve a route",
    },
    "posts": {
        "label": "Pages or posts published",
        "unit": "posts",
        "ceiling": 3,
        "why_ceiling": "This business wins on real finished jobs, not volume. Three "
                       "thin pages a day compete with each other and win nothing.",
        # content_queue has no published_at, so this counts pages CREATED today
        # that reached 'published'. Honest but approximate — noted in the output
        # rather than quietly passed off as exact.
        "sql": "SELECT count(*) FROM content_queue WHERE status='published' AND created_at>=?",
        "approximate": "counted by creation date — the queue has no separate publish date yet",
        "does_it": "python -m pipelines.content_briefs",
    },
    "site_checks": {
        "label": "Website diagnostics run",
        "unit": "checks",
        "ceiling": 24,
        "why_ceiling": "Hourly is plenty. More than that is load on your own site for "
                       "no extra information.",
        "sql": ("SELECT count(*) FROM agent_runs WHERE agent LIKE 'it-%'"
                " AND status='ok' AND started_at>=?"),
        "does_it": "python -m pipelines.it_health",
    },
}

# What a sensible first week looks like. Used only when nothing is set — never
# written to the cabinet behind the owner's back, so "unset" stays visible.
SUGGESTED = {"leads": 20, "mails": 10, "posts": 1, "site_checks": 2}


def _conn():
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def _ensure(c):
    """The tables arrived after the first cabinets were built."""
    c.execute("CREATE TABLE IF NOT EXISTS targets ("
              "name TEXT PRIMARY KEY, per_day INTEGER DEFAULT 0, note TEXT,"
              " updated_at INTEGER)")
    c.execute("CREATE TABLE IF NOT EXISTS daily_orders ("
              "day TEXT PRIMARY KEY, orders TEXT, updated_at INTEGER)")


def _day_start(ts=None):
    lt = time.localtime(ts)
    return int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)))


def today() -> str:
    return time.strftime("%Y-%m-%d", time.localtime())


def check_ceiling(name, per_day):
    """(ok, warning). Over the ceiling is allowed but never silent."""
    job = JOBS.get(name)
    if not job or per_day <= job["ceiling"]:
        return True, ""
    return False, (f"{per_day} {job['unit']}/day is above the safe ceiling of "
                   f"{job['ceiling']}. {job['why_ceiling']} Set it if you mean it — "
                   f"it is your call — but the MD will carry this warning into the plan.")


def set_target(name, per_day, note=""):
    if name not in JOBS:
        raise KeyError(f"unknown job '{name}' — one of {', '.join(JOBS)}")
    per_day = max(0, int(per_day))
    ok, warning = check_ceiling(name, per_day)
    with _conn() as c:
        _ensure(c)
        c.execute("INSERT INTO targets(name,per_day,note,updated_at) VALUES(?,?,?,?)"
                  " ON CONFLICT(name) DO UPDATE SET per_day=excluded.per_day,"
                  " note=excluded.note, updated_at=excluded.updated_at",
                  (name, per_day, note or None, int(time.time())))
        c.commit()
    return {"ok": True, "name": name, "per_day": per_day,
            "within_safe_ceiling": ok, "warning": warning}


# WORDS THAT NAME A NUMBER ARE A TARGET, NOT JUST A SENTENCE.
#
# "i asked for 20 leads and 1 content post but in images you can see it's
#  saying leads sector had nothing to run why?"
#
# Because he typed it and the MD reads figures. Orders and targets were two
# separate things that nobody had joined up: an order became a task titled
# "Owner's order: collect 20 leads", which is a real task and does get claimed
# — but the MD posts the actual COLLECT TODAY'S LEADS task only when
# targets.status() reports a leads target with something still to do. No
# target, no collection task, and the Hunter came back to an empty board.
#
# He should not have to know that his own program keeps numbers in one place
# and sentences in another. Writing "20 leads" sets the target.
#
# Only patterns where the number is unambiguous — a bare figure next to the
# thing being counted. Anything vaguer is left alone rather than guessed at,
# because a target invented from a misread sentence is worse than none: it
# makes the MD plan work he never asked for.
_NUMBER_WORDS = [
    ("leads", r"(\d{1,3})\s*(?:new\s+)?(?:leads?|prospects?|businesses|firms|companies)"),
    ("mails", r"(\d{1,3})\s*(?:outreach\s+)?(?:e-?mails?|mails?)\b"),
    ("posts", r"(\d{1,2})\s*(?:content\s+|blog\s+|service\s+)?(?:posts?|pages?|articles?)"),
    ("site_checks", r"(\d{1,2})\s*(?:site\s+|website\s+)?(?:checks?|diagnostics?|audits?)"),
]


def from_words(text, apply=True) -> list:
    """
    The daily targets an order in plain English is asking for.

    Returns [{name, per_day, phrase, ...}] — what it found and, when `apply`,
    what it set. `apply=False` is the dry run the office uses to show him what
    it understood before it acts on it.
    """
    import re
    said = (text or "").lower()
    found = []
    for name, pattern in _NUMBER_WORDS:
        m = re.search(pattern, said)
        if not m:
            continue
        n = int(m.group(1))
        if n <= 0:
            continue
        row = {"name": name, "per_day": n, "phrase": m.group(0).strip(),
               "label": JOBS[name]["label"]}
        if apply:
            try:
                row.update(set_target(name, n, note="from the owner's own words"))
            except Exception as e:                           # pragma: no cover
                row["error"] = str(e)
        else:
            ok, warning = check_ceiling(name, n)
            row.update({"within_safe_ceiling": ok, "warning": warning})
        found.append(row)
    return found


def set_orders(text, day=None):
    """
    The owner's orders for a day, in his own words: "focus on Sharjah hotels",
    "no outreach today, I'm on site". The MD reads these before planning and is
    expected to follow them over its own preference.
    """
    d = day or today()
    with _conn() as c:
        _ensure(c)
        c.execute("INSERT INTO daily_orders(day,orders,updated_at) VALUES(?,?,?)"
                  " ON CONFLICT(day) DO UPDATE SET orders=excluded.orders,"
                  " updated_at=excluded.updated_at",
                  (d, (text or "").strip()[:4000], int(time.time())))
        c.commit()
    # A number in the order is a target. See from_words: the MD plans from
    # figures, the owner writes sentences, and nobody had joined the two up —
    # which is why "20 leads" produced a task saying "collect 20 leads" and no
    # actual collection.
    return {"ok": True, "day": d, "targets_set": from_words(text)}


def get_orders(day=None):
    d = day or today()
    try:
        with _conn() as c:
            _ensure(c)
            r = c.execute("SELECT orders FROM daily_orders WHERE day=?", (d,)).fetchone()
            return (r["orders"] if r else "") or ""
    except sqlite3.Error:
        return ""


def _done_today(c, job, day0):
    try:
        r = c.execute(job["sql"], (day0,)).fetchone()
        return int(r[0] or 0)
    except sqlite3.Error:
        return 0


def _running_position(c, job, day0):
    """
    (yesterday, all time). The two numbers a sector needs to know where it is.

    "merge them into 1 task just on the base when the user will ask to find the
     next leads and also the previous day's leads"

    A job's own SQL counts everything since a moment, so yesterday is the
    difference between two of those counts and the all-time figure is the same
    count from zero. No new queries to write and no chance of the two
    disagreeing with what the target screen already shows.
    """
    since_yesterday = _done_today(c, job, day0 - 86400)
    today = _done_today(c, job, day0)
    return max(0, since_yesterday - today), _done_today(c, job, 0)


def status(day0=None):
    """
    Where the day stands: every job, its target, what has actually happened, and
    whether anything is over a ceiling. Read-only.
    """
    day0 = day0 if day0 is not None else _day_start()
    out, set_any = [], False
    try:
        with _conn() as c:
            _ensure(c)
            rows = {r["name"]: r for r in c.execute("SELECT * FROM targets")}
            for name, job in JOBS.items():
                row = rows.get(name)
                per_day = int(row["per_day"]) if row else 0
                if row:
                    set_any = True
                done = _done_today(c, job, day0)
                yesterday, on_file = _running_position(c, job, day0)
                ok, warning = check_ceiling(name, per_day)
                entry = {
                    "name": name, "label": job["label"], "unit": job["unit"],
                    "per_day": per_day, "done_today": done,
                    "done_yesterday": yesterday, "on_file": on_file,
                    "remaining": max(0, per_day - done),
                    "pct": (min(100, round(done / per_day * 100)) if per_day else None),
                    "hit": bool(per_day) and done >= per_day,
                    "set_by_owner": bool(row),
                    "suggested": SUGGESTED.get(name, 0),
                    "note": (row["note"] if row else "") or "",
                    "does_it": job["does_it"],
                    "ceiling": job["ceiling"],
                    "over_ceiling": not ok, "warning": warning,
                }
                if job.get("approximate"):
                    entry["approximate"] = job["approximate"]
                out.append(entry)
    except sqlite3.Error as e:
        return {"ok": False, "error": f"cabinet not readable: {e}", "jobs": []}

    return {"ok": True, "day": today(), "jobs": out,
            "any_target_set": set_any,
            "orders": get_orders(),
            "all_hit": bool(set_any) and all(j["hit"] for j in out if j["per_day"]),
            "warnings": [j["warning"] for j in out if j["warning"]]}


def as_text(s=None) -> str:
    """The day on one screen, for a person or for the MD's context."""
    s = s or status()
    if not s.get("ok"):
        return s.get("error", "unavailable")
    L = [f"TODAY — {s['day']}"]
    if not s["any_target_set"]:
        L.append("  No targets set. The agency has no number to hit, so the MD is")
        L.append("  guessing at workload. Set them on /setup, or:")
        L.append("    python -m coordination.targets --set leads=20 --set mails=10")
        L.append("")
        L.append("  Suggested first week:")
        for j in s["jobs"]:
            L.append(f"    {str(j['suggested']).rjust(4)}  {j['label'].lower()}")
    else:
        for j in s["jobs"]:
            if not j["per_day"]:
                L.append(f"  [ off ] {j['label']} — not asked for today")
                continue
            filled = round((j["pct"] or 0) / 10)
            bar = "#" * filled + "." * (10 - filled)
            mark = "done" if j["hit"] else f"{j['remaining']} to go"
            L.append(f"  [{bar}] {j['done_today']}/{j['per_day']} "
                     f"{j['label'].lower()} — {mark}")
            if j.get("approximate"):
                L.append(f"           note: {j['approximate']}")
            if j["warning"]:
                L.append(f"           WARNING: {j['warning']}")

    # Always last, and always shown. This used to sit after an early return, so
    # writing orders without also setting a number made the owner's own words
    # invisible — the one thing in here that must never be dropped.
    if s["orders"]:
        L += ["", "OWNER'S ORDERS FOR TODAY", "  " + s["orders"].replace("\n", "\n  ")]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="The owner's daily targets.")
    ap.add_argument("--set", action="append", default=[], metavar="job=n",
                    help="e.g. --set leads=40 --set mails=25")
    ap.add_argument("--orders", help="orders for today, in your own words")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    for pair in a.set:
        if "=" not in pair:
            raise SystemExit(f"--set wants job=number, got '{pair}'")
        k, v = pair.split("=", 1)
        r = set_target(k.strip(), int(v))
        if r["warning"]:
            # stderr, not stdout: --json means stdout is machine-readable, and a
            # warning printed above the JSON made the whole reply unparseable —
            # so setting an over-ceiling target through the UI looked like a
            # save failure when it had in fact saved.
            print(f"! {r['warning']}\n", file=sys.stderr)
    if a.orders is not None:
        set_orders(a.orders)

    print(json.dumps(status(), indent=2) if a.json else as_text())


if __name__ == "__main__":
    main()
