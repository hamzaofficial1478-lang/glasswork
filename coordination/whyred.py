"""
whyred.py — why is that desk red?

    python -m coordination.whyred            every desk, worst first
    python -m coordination.whyred --all      including the ones that are fine
    python -m coordination.whyred seo        one sector
    python -m coordination.whyred --json

"in sector seo and ranking the agent seo staratgest(keywords gap) and other
 seo startgiest (links&guest posting) there two are blinking red check out why"

The floor already knew. A blinking red lamp is `agent_runs.status = 'error'` or
'stalled' on that desk's most recent run, and the reason the run wrote down is
sitting in the same row — the office puts it in the hover text. That is one
place, it needs a mouse, and it is gone the moment the page reloads. So the
answer to "why is it red" was a message he had to come and ask a person for.

This is that answer as a command. It reads the same rows the floor reads and
prints, per desk: what state it is in, what the run actually said, when, and
what to do about it. Nothing here is inferred — every line comes off a row.

WHY IT IS NOT PART OF --check

`run.py --check` asks "can this sector work" — config, keys, models. This asks
"what happened when it last tried". They fail independently and they need
different things done about them: a blocked check is something to configure, a
red desk is something that ran and broke.
"""
import argparse
import json
import re
import sqlite3
import sys
import time
from pathlib import Path

try:
    from . import board
except ImportError:  # pragma: no cover
    import board

# The same window the office floor uses. A desk older than this is not red, it
# is simply idle, and calling an idle desk broken is the sort of false alarm
# that teaches somebody to ignore the screen.
RECENT = 3600

# Long enough that a run still claiming to be working is not working. Matches
# coordination/state.STALL_SECS — one definition would be better, and this one
# reads it rather than copying it.
try:
    from . import state as _state
    STALL = getattr(_state, "STALL_SECS", 1200)
except Exception:                                            # pragma: no cover
    STALL = 1200

SECTORS = ["Director", "IT", "Content", "SEO", "Leads", "Report", "Security"]

# What to do about each state, in his language rather than the table's.
ADVICE = {
    "error": ("Run it yourself and watch it fail: the command is below. The "
              "note above is what the agent wrote as it went down."),
    "stalled": ("It started and never came back, so the Overseer closed the "
                "books on it. Usually the step ran out of its time budget — "
                "check whether the source it waits on is slow or unreachable."),
    "running": ("Still going. If it has been longer than a step is allowed, it "
                "is stuck rather than slow."),
}

# Which command re-runs THIS desk — not the four commands in its sector.
#
# Read off the day's own step list so it cannot drift from what actually runs,
# and matched to the desk by the agent name each module runs under. That name
# is in the source as `AGENT = "seo-backlinks"` or as the default of
# `run(agent="leads-hunter")`, so it is read from the file rather than kept in
# a second list here — a hand-written map is a map that is wrong by the time
# somebody renames a desk.
_AGENT_IN_SOURCE = re.compile(
    r'^AGENT\s*=\s*["\']([a-z0-9\-]+)["\']'
    r'|^def run\([^)]*?agent\s*=\s*["\']([a-z0-9\-]+)["\']',
    re.M | re.S)


def _commands():
    """{room: [cmd]} and {agent: [cmd]}, so a desk gets its own command."""
    by_room, by_agent = {}, {}
    try:
        from pipelines import day_run
        root = Path(__file__).resolve().parent.parent
    except Exception:                                        # pragma: no cover
        return {}, {}
    for _label, mod, room, _lane in day_run.STEPS:
        cmd = f"python -m {mod}"
        by_room.setdefault(room, []).append(cmd)
        path = root / (mod.replace(".", "/") + ".py")
        try:
            src = path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue
        for m in _AGENT_IN_SOURCE.finditer(src):
            name = m.group(1) or m.group(2)
            if name:
                by_agent.setdefault(name, [])
                if cmd not in by_agent[name]:
                    by_agent[name].append(cmd)
    return by_room, by_agent


def _rows(c, q, args=()):
    try:
        return c.execute(q, args).fetchall()
    except Exception:
        return []


def _ago(secs):
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60} min ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def _sector_of(agent):
    """
    Which room a desk belongs to. The two that do not follow the naming pattern
    are the two that were dark on the floor for months — see state.AGENT_ALIASES,
    which is where this reads its answer from rather than guessing again.
    """
    a = (agent or "").strip().lower()
    try:
        aliases = _state.AGENT_ALIASES
    except Exception:                                        # pragma: no cover
        aliases = {}
    for key, names in aliases.items():
        for n in names:
            if a == n or a.startswith(n + "-"):
                return key
    return a.split("-", 1)[0]


def look(sector="", include_ok=False, now=None) -> dict:
    """Every desk that has run recently, and what came of its last run."""
    now = int(now or time.time())
    board.ensure_schema()
    want = (sector or "").strip().lower()
    by_room, by_agent = _commands()

    desks, seen = [], set()
    try:
        con = sqlite3.connect(board.DB, timeout=30)
        con.row_factory = sqlite3.Row
    except Exception as e:                                   # pragma: no cover
        return {"ok": False, "error": str(e), "desks": []}
    try:
        rows = _rows(con,
                     "SELECT agent, status, started_at, ended_at, note, task_id"
                     " FROM agent_runs WHERE started_at >= ?"
                     " ORDER BY started_at DESC", (now - RECENT,))
        for r in rows:
            agent = r["agent"] or "?"
            if agent in seen:
                continue                     # one line per desk: its last run
            seen.add(agent)
            room = _sector_of(agent)
            if want and room != want and (agent or "").lower().split("-")[0] != want:
                continue

            status = (r["status"] or "").strip()
            age = now - (r["ended_at"] or r["started_at"] or now)
            red = status in ("error", "stalled") or (
                status == "running" and (now - (r["started_at"] or now)) > STALL)
            if not red and not include_ok:
                continue

            said = (r["note"] or "").strip()
            desks.append({
                "agent": agent, "sector": room, "status": status or "unknown",
                "red": red, "when": _ago(age), "at": r["ended_at"] or r["started_at"],
                "said": said or ("nothing was written down" if red else "finished"),
                "advice": ADVICE.get(status, "") if red else "",
                # this desk's own command first; the sector's only
                # when nothing in the day runs under that name.
                "try": by_agent.get(agent) or by_room.get(_room_name(room), []),
            })
    finally:
        con.close()

    desks.sort(key=lambda d: (not d["red"], d["sector"], d["agent"]))
    bad = [d for d in desks if d["red"]]
    return {"ok": True, "desks": desks, "red": len(bad),
            "head": (f"{len(bad)} desk(s) are red"
                     if bad else
                     "No desk is red. Every desk that ran in the last hour "
                     "finished cleanly.")}


def _room_name(key):
    """'seo' -> 'SEO', so the command lookup matches day_run's room labels."""
    k = (key or "").strip().lower()
    for s in SECTORS:
        if s.lower() == k:
            return s
    return {"md": "Director", "report": "Report"}.get(k, key)


def as_text(r) -> str:
    if not r.get("ok"):
        return "WHY RED — could not read the cabinet: " + str(r.get("error"))
    L = ["WHY RED — " + r["head"]]
    for d in r["desks"]:
        mark = "RED " if d["red"] else "ok  "
        L.append("")
        L.append(f"  {mark} {d['sector']} · {d['agent']}   [{d['status']}, {d['when']}]")
        L.append(f"       it said: {d['said'][:300]}")
        if d["advice"]:
            L.append(f"       {d['advice']}")
        for cmd in d["try"][:4]:
            L.append(f"       try:  {cmd}")
    if not r["desks"]:
        L.append("")
        L.append("  Nothing has run in the last hour, so there is nothing to "
                 "report on. Press Run the day, then come back.")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(
        description="Why is that desk red? Reads the last run of every desk.")
    ap.add_argument("sector", nargs="?", default="",
                    help="one sector only, e.g. seo")
    ap.add_argument("--all", action="store_true",
                    help="include the desks that are fine")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    r = look(sector=a.sector, include_ok=a.all)
    print(json.dumps(r, indent=2) if a.json else as_text(r))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
