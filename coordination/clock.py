"""
clock.py — the agency's own clock, which only ticks while the agency is open.

THE OWNER'S POINT

    "when user open program and user see that any sector not responded from
     last 2 days or many hours the stop button will stop all the sector or
     workers and when user will press run day then from that time the sector
     and agents will start to count time"

He is describing a bug, and he is right about it. His control room said:

    Content has not answered SEO in 26 days: Keyword-gap list ready
    5 task(s) on the Content board have been open up to 6 days

His laptop was not on for 26 days. It ran a day here and a day there, and the
wall clock counted every night, every weekend and every week the program was
shut as time Content spent ignoring SEO. So the Overseer filed a fault, then
filed it again, and the screen filled with alarms measuring nothing but how
long the machine had been switched off.

Two things follow from that, and only the second one is subtle:

  A REAL AGE IS RUNNING TIME. If the agency was open for four hours across
  those 26 days, the request went unanswered for four hours. That is the
  number that says whether anything is actually wrong, and it is the only one
  worth putting a threshold on.

  THE CALENDAR STILL MATTERS TO HIM. "Four hours of agency time" is the right
  measure and a useless thing to read on its own — he wants to know it has
  been sitting there since Tuesday. So every phrase here carries both, and
  says which is which.

HOW IT IS MEASURED

From day_runs, which already records every day the agency opened, when it
closed, and whether the process that owned it is still alive. Nothing new is
written and nothing has to be kept up to date: the clock is derived from the
same rows the run panel draws.

A day that is running right now counts up to this moment. A day whose process
is gone counts up to when it was last seen — see board.day_is_open for the same
liveness test, asked for the same reason.

WHAT THIS IS NOT

It is not a way of hiding a real problem. A sector that ignores a request for
four hours of running time is exactly as broken as before and is reported
exactly as loudly. This only stops the agency being blamed for the hours it
was not asked to work.

It is also not retroactive honesty about a cabinet with no history: if no day
run was ever recorded over the period, there is no evidence the agency was
open, and running time is zero rather than the whole wall-clock span. That is
the safe direction — it under-reports staleness rather than inventing it.
"""
import sqlite3
import time

# A day that opened and never recorded an end, whose process is long gone. We
# cannot know when it really stopped, so it is credited with a bounded stretch
# rather than "until now" (which would count every hour since) or zero (which
# would throw away a day's real work). One working day is the honest guess.
UNCLOSED_DAY_CREDIT = 8 * 3600


def _db():
    from . import board
    return board.DB


def _rows(a, b):
    """Every day run overlapping [a, b], as (started, ended) pairs."""
    from . import board
    out = []
    try:
        c = sqlite3.connect(_db(), timeout=10)
        c.row_factory = sqlite3.Row
    except Exception:                                        # pragma: no cover
        return out
    try:
        for r in c.execute(
                "SELECT started_at, ended_at, status, pid, host FROM day_runs"
                " WHERE COALESCE(ended_at, started_at + ?) >= ? AND started_at <= ?"
                " ORDER BY started_at", (UNCLOSED_DAY_CREDIT, a, b)).fetchall():
            start = int(r["started_at"] or 0)
            if not start:
                continue
            end = r["ended_at"]
            if end:
                end = int(end)
            elif r["status"] == "running" and board.process_alive(r["pid"], r["host"] or ""):
                end = int(time.time())          # still open, still counting
            else:
                end = start + UNCLOSED_DAY_CREDIT
            if end > start:
                out.append((start, end))
    except Exception:                                        # pragma: no cover
        return []
    finally:
        c.close()
    return out


def _merge(spans):
    """Overlapping runs are one stretch of open time, not two."""
    out = []
    for s, e in sorted(spans):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return out


def open_seconds(a, b=None) -> int:
    """How much of the window [a, b] the agency was actually open for."""
    b = int(b if b is not None else time.time())
    a = int(a or 0)
    if b <= a:
        return 0
    total = 0
    for s, e in _merge(_rows(a, b)):
        total += max(0, min(e, b) - max(s, a))
    return int(total)


def age(since, now=None) -> int:
    """
    How long something has been waiting, in agency time.

    This is the number to put a threshold on. `since` is a wall-clock stamp out
    of the cabinet like every other timestamp; the translation happens here so
    callers do not each have to remember to do it.
    """
    return open_seconds(since, now)


def _phrase(secs) -> str:
    secs = max(0, int(secs))
    if secs < 90:
        return f"{secs} second(s)"
    if secs < 5400:
        return f"{round(secs / 60)} minute(s)"
    if secs < 3 * 86400:
        return f"{round(secs / 3600, 1):g} hour(s)"
    return f"{round(secs / 86400, 1):g} day(s)"


def ago(since, now=None) -> str:
    """
    Both clocks in one phrase, with the honest one first.

    "3.5 hour(s) of agency time (26 days ago on the calendar)" — the number
    that decides whether this is a problem, and the date he actually recognises.
    The two are only both shown when they disagree enough to matter; on a day
    the program has been running throughout, one number is the whole truth and
    two would be noise.
    """
    now = int(now if now is not None else time.time())
    since = int(since or now)
    wall = max(0, now - since)
    running = age(since, now)
    if wall - running < max(3600, wall * 0.2):
        return _phrase(running)
    return (f"{_phrase(running)} of agency time "
            f"({_phrase(wall)} ago on the calendar — the program was shut for "
            f"most of it)")


def state(now=None) -> dict:
    """Is the agency open, and how much has it been open today. For the office."""
    from . import board
    now = int(now if now is not None else time.time())
    day0 = now - (now % 86400)
    d = board.day_is_open(now)
    return {"open": bool(d.get("open")), "why": d.get("why", ""),
            "open_today_secs": open_seconds(day0, now),
            "open_today": _phrase(open_seconds(day0, now)),
            "run_id": d.get("run_id")}


if __name__ == "__main__":
    import json
    print(json.dumps(state(), indent=2))
