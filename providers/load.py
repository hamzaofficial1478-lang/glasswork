"""
load.py — when the first model is already busy, use the second one.

THE OWNER'S QUESTION

"If any sector or the MD gets more load it can use its fallback API — divide
the tasks between both APIs for fast responses and fast deliverables."

WHAT WAS ACTUALLY HAPPENING

routing.call_task is a cascade: try the primary, and only if it FAILS try the
next one. That is the right rule for reliability and the wrong one for load. A
primary that is merely busy has not failed — so a second agent asking the same
model at the same time joined a queue behind the first, waited out the whole
timeout, and only then tried the fallback that had been sitting idle the entire
time. Two models configured, one of them doing all the work.

That did not matter much while the day ran one step at a time. It matters now:
pipelines/day_run.py runs IT, Leads and SEO side by side, so three sectors
reach for a model in the same second by design.

WHAT THIS DOES

A tiny ledger in the cabinet — one row per call in flight, written before the
call and deleted after. Any process can read it, because it is a table rather
than something in one process's memory, and that is the whole point: the three
lanes of the day are three separate processes.

Then the candidate list is REORDERED, never changed:

    both free            primary, then fallback.  Unchanged.
    primary busy         fallback first, primary behind it.
    both busy            primary first — nothing to gain by reordering, and the
                         cascade still tries both.

So it never reaches for a provider the route did not already allow, never
touches the cost-tier guard, and with one model configured it does nothing at
all. It is an ordering, not a new permission.

WHAT IT DELIBERATELY IS NOT

Not a semaphore. Nothing blocks and nothing queues — a busy primary is a
preference to go elsewhere, not a locked door. If both are busy, both get used,
because two slow answers still beat one answer and one agent sitting on its
hands.

Not a cost decision either. The route's cost-tier guard has already decided
what may be spent; this only picks between things already approved.

STALE ROWS

A process killed mid-call leaves its row behind, and a row that never clears
would make a perfectly healthy model look permanently busy. Every read purges
anything past its expires_at first, so the worst case is a model that looks
busy for as long as one call is allowed to take.
"""
import sqlite3
import time
import uuid

_DEFAULT_TTL = 300


def _db():
    """Where the cabinet is. Read through board so the tests can move it."""
    try:
        from coordination import board
        return board.DB
    except Exception:                                        # pragma: no cover
        return None


def _conn():
    db = _db()
    if not db:
        return None
    try:
        c = sqlite3.connect(db, timeout=10, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=10000")
        return c
    except Exception:                                        # pragma: no cover
        return None


def _ensure(c):
    c.execute("CREATE TABLE IF NOT EXISTS model_inflight ("
              "id INTEGER PRIMARY KEY AUTOINCREMENT, model_id TEXT NOT NULL,"
              " credential_id TEXT, task TEXT, holder TEXT,"
              " started_at INTEGER, expires_at INTEGER)")


def _purge(c, now):
    c.execute("DELETE FROM model_inflight WHERE expires_at IS NOT NULL"
              " AND expires_at < ?", (now,))


def begin(model_id, credential_id="", task="", holder="", ttl=_DEFAULT_TTL):
    """
    Say a call on this model is starting. Returns a token for end(), or None if
    the ledger is unavailable — in which case everything still works, it just
    stops being load-aware.
    """
    c = _conn()
    if c is None:
        return None
    now = int(time.time())
    tok = uuid.uuid4().hex[:12]
    try:
        _ensure(c)
        _purge(c, now)
        c.execute("INSERT INTO model_inflight(model_id,credential_id,task,holder,"
                  "started_at,expires_at) VALUES(?,?,?,?,?,?)",
                  (str(model_id), str(credential_id), str(task),
                   f"{holder or 'agent'}:{tok}", now, now + max(1, int(ttl))))
        return c.execute("SELECT last_insert_rowid()").fetchone()[0]
    except Exception:
        return None
    finally:
        c.close()


def end(token):
    """Say the call is over. Safe to call with None."""
    if token is None:
        return
    c = _conn()
    if c is None:                                            # pragma: no cover
        return
    try:
        c.execute("DELETE FROM model_inflight WHERE id=?", (token,))
    except Exception:                                        # pragma: no cover
        pass
    finally:
        c.close()


def busy(now=None) -> dict:
    """{model_id: how many calls are in flight}. Empty when nothing is."""
    c = _conn()
    if c is None:
        return {}
    now = int(now or time.time())
    try:
        _ensure(c)
        _purge(c, now)
        return {r["model_id"]: r["n"] for r in c.execute(
            "SELECT model_id, count(*) AS n FROM model_inflight GROUP BY model_id")}
    except Exception:
        return {}
    finally:
        c.close()


def order(candidates, load=None) -> tuple:
    """
    Reorder a route's candidates so a busy provider yields to a free one.

    Returns (ordered, note). `note` is the plain sentence explaining a change,
    or "" when nothing moved — an ordering nobody can see is an ordering nobody
    can debug, and this one changes which model answers.

    Stable by construction: sorting on (in-flight count, original position)
    means equal load leaves the route's own order alone, so the primary stays
    the primary until it is genuinely busier than the alternative.
    """
    cands = list(candidates or [])
    if len(cands) < 2:
        return cands, ""
    load = busy() if load is None else load
    if not load:
        return cands, ""
    keyed = [(load.get(c.get("model_id"), 0), i, c) for i, c in enumerate(cands)]
    keyed.sort(key=lambda k: (k[0], k[1]))
    ordered = [c for _n, _i, c in keyed]
    if ordered[0] is cands[0]:
        return ordered, ""
    was, now_first = cands[0], ordered[0]
    return ordered, (
        f"{was.get('api_model_id') or was.get('model_id')} has "
        f"{load.get(was.get('model_id'), 0)} call(s) in flight, so "
        f"{now_first.get('api_model_id') or now_first.get('model_id')} was "
        "asked first")


def as_text() -> str:
    b = busy()
    if not b:
        return "no model calls in flight"
    return "\n".join(f"  {m}  {n} in flight" for m, n in sorted(b.items()))


if __name__ == "__main__":
    print(as_text())
