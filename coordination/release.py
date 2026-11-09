"""
release.py — put a stuck job back on the board, from the office.

    python3 -m coordination.release                       what is stuck
    echo '{"id":"abc123"}' | python3 -m coordination.release --stdin
    echo '{"id":"abc123","force":true}' | python3 -m coordination.release --stdin

A thin front door, the same shape as approve.py, so the server has one command
to shell into. The judgement lives in board.release_claim; this reads the
request and reports what happened.

WHAT IT IS FOR

A claimed task belongs to one agent and nobody else may touch it. That rule is
what stops two agents doing one job, and it is not negotiable. Its consequence
was: if the holder never comes back, the job is held for ever, drawn on the
floor as a red desk the owner can do nothing about.

The Overseer clears these on its own pass now. This is the manual door, for
when the owner is looking at the desk and does not want to wait for the next
pass — or when the desk is only slow and he has decided he wants the job back
anyway, which is `force` and is recorded as his decision rather than passed off
as an ordinary release.
"""
import json
import sys
import time

try:
    from . import board
except ImportError:  # pragma: no cover
    import board


def _ago(secs):
    secs = max(0, int(secs))
    if secs < 3600:
        return f"{secs // 60}m"
    if secs < 86400:
        return f"{secs // 3600}h"
    return f"{secs // 86400}d"


def stuck() -> dict:
    """Every claim with nothing behind it — for the screens."""
    rows = board.dead_claims()
    return {"ok": True, "at": int(time.time()), "stuck": [
        {**r, "held": _ago(r["held_secs"])} for r in rows],
        "head": (f"{len(rows)} job(s) held by a desk that is not working on them"
                 if rows else "nothing is stuck")}


def main():
    if "--stdin" not in sys.argv:
        r = stuck()
        if "--json" in sys.argv:
            print(json.dumps(r))
        else:
            print(r["head"].upper())
            for s in r["stuck"]:
                print(f"  {s['sector']:8} {s['owner']:16} held {s['held']:>4}  "
                      f"{s['title'][:56]}")
        return 0

    try:
        req = json.loads(sys.stdin.read() or "{}")
    except Exception as e:
        print(json.dumps({"ok": False, "error": f"bad input: {e}"}))
        return 2
    tid = str(req.get("id") or "").strip()
    if not tid:
        print(json.dumps({"ok": False, "error": "no task id given"}))
        return 1
    try:
        ok, why = board.release_claim(
            tid, str(req.get("by") or "owner"),
            reason=str(req.get("reason") or "")[:300],
            force=bool(req.get("force")))
    except Exception as e:                                   # pragma: no cover
        print(json.dumps({"ok": False, "error": f"could not release it: {e}"}))
        return 1
    print(json.dumps({"ok": bool(ok), "id": tid, "why": why,
                      "error": "" if ok else why}))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
