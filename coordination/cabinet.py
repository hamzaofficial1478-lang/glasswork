"""
cabinet.py — the shared API shelf (the "cabinet" the owner described).

The agency does NOT give every agent its own key. Each sector has one brain
(routed in providers/), and every other capability — SEO data, stock graphics,
email delivery, the crawler, image/embedding models — lives on a shared shelf.
An agent BORROWS a shelf when it needs that capability.

This is built entirely on the primitives already in board.py, so it obeys the
same law as the rest of the agency ("claim, don't grab; request, don't reach"):

  * one holder per shelf   -> a board resource lock, resource = 'shelf:<name>'.
    Different shelves run in parallel; two agents wanting the SAME shelf take
    turns. This is the fix for "everything queues behind everything".
  * who is waiting          -> the shelf_queue table (recorded before the wait,
    so the live board can show the queue).
  * every checkout logged   -> the borrows table (agent, item, reason, cost),
    feeding the Overseer's spend cap and the daily report.
  * TTL locks               -> a crashed agent can't hold a shelf forever.

Approval is two-tier, matching the handbook: routine borrows (a shelf item that
already exists, internal data, under the cost ceiling) are auto-granted and
logged; exceptional ones (external/untrusted data, or paid over the ceiling) are
flagged 'escalated' so the caller stops and asks the MD (and Security, for
outside data) via board.request_verification. The cabinet does not itself block
on approval — it enforces turn-taking and flags what must be escalated.
"""
import sqlite3
import time
from contextlib import contextmanager

# board is a sibling — importable both as a package (coordination.cabinet) and,
# defensively, as a bare script on the same path.
try:
    from . import board
except ImportError:  # pragma: no cover - script/path fallback
    import board


def _now():
    return int(time.time())


@contextmanager
def _conn():
    # same DB and pragmas as the board, so locks and the cabinet agree
    con = sqlite3.connect(board.DB, timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA busy_timeout=30000;")
    try:
        yield con
    finally:
        con.close()


def _resource(shelf: str) -> str:
    return "shelf:" + (shelf or "").strip().lower()


# ---------------------------------------------------------------------------
# APPROVAL POLICY — the MD sets it once; the cabinet applies it every borrow
# ---------------------------------------------------------------------------

def classify(data_class: str = "internal", est_cost: float = 0.0,
             cost_ceiling: float = 0.0) -> str:
    """
    Decide whether a borrow is routine or must be escalated. Kept tiny and pure
    so it is trivial to reason about and to test.

      external data           -> escalate  (Security must screen what's coming in)
      paid over the ceiling   -> escalate  (owner/MD owns spend above policy)
      otherwise               -> auto
    """
    if (data_class or "internal").strip().lower() == "external":
        return "escalate"
    if cost_ceiling and est_cost and float(est_cost) > float(cost_ceiling):
        return "escalate"
    return "auto"


# ---------------------------------------------------------------------------
# BORROW / RELEASE — take a shelf, do the work, give it back
# ---------------------------------------------------------------------------

def borrow(shelf, agent, sector=None, reason="", data_class="internal",
           est_cost=0.0, cost_ceiling=0.0, ttl=300, wait=True, timeout=120):
    """
    Take the shelf lock so this agent is the sole user of that capability.

    Records the agent in the shelf's waiting line first (so the live board shows
    it), then acquires the single-holder lock, then opens a borrow row. Returns:

      {ok: True, borrow_id, queue_id, shelf, approval}      on success
      {ok: False, error, queue_id}                          if busy / timed out

    `approval` is 'auto' or 'escalate' (see classify): the caller must go get MD
    /Security sign-off before acting when it is 'escalate' — the cabinet flags
    it, it does not silently proceed.
    """
    shelf = (shelf or "").strip().lower()
    if not shelf:
        return {"ok": False, "error": "no shelf named"}
    if not (reason or "").strip():
        # the owner's rule: no borrow without a strong reason
        return {"ok": False, "error": "a borrow needs a reason"}

    resource = _resource(shelf)
    approval = classify(data_class, est_cost, cost_ceiling)

    with _conn() as c:
        cur = c.execute(
            "INSERT INTO shelf_queue(shelf,agent,sector,reason,data_class,status,enqueued_at)"
            " VALUES(?,?,?,?,?, 'waiting', ?)",
            (shelf, agent, sector, reason, data_class, _now()),
        )
        qid = cur.lastrowid

    waited = 0
    while True:
        # non-blocking attempt so WE own the wait loop and keep the queue row honest
        if board.acquire_lock(resource, agent, wait=False, ttl=ttl):
            break
        if not wait or waited >= timeout:
            with _conn() as c:
                c.execute("UPDATE shelf_queue SET status='cancelled' WHERE id=?", (qid,))
            return {"ok": False,
                    "error": "shelf busy" if not wait else "timed out waiting for shelf",
                    "queue_id": qid, "shelf": shelf}
        time.sleep(2)
        waited += 2

    with _conn() as c:
        c.execute("UPDATE shelf_queue SET status='granted', granted_at=? WHERE id=?",
                  (_now(), qid))
        cur = c.execute(
            "INSERT INTO borrows(shelf,agent,sector,reason,data_class,approval,status,granted_at)"
            " VALUES(?,?,?,?,?,?, 'active', ?)",
            (shelf, agent, sector, reason, data_class, approval, _now()),
        )
        bid = cur.lastrowid

    return {"ok": True, "borrow_id": bid, "queue_id": qid,
            "shelf": shelf, "approval": approval}


def release(shelf, agent, borrow_id=None, item=None, usd_cost=0.0, denied=False):
    """
    Give the shelf back and close the borrow row (with what it used and cost).
    Only ever releases a lock this agent holds (board.release_lock checks holder).
    """
    shelf = (shelf or "").strip().lower()
    board.release_lock(_resource(shelf), agent)
    status = "denied" if denied else "done"
    with _conn() as c:
        if borrow_id is not None:
            c.execute(
                "UPDATE borrows SET status=?, released_at=?, usd_cost=?,"
                " item=COALESCE(?, item) WHERE id=?",
                (status, _now(), float(usd_cost or 0), item, borrow_id),
            )
        else:  # close this agent's most recent active borrow on the shelf
            row = c.execute(
                "SELECT id FROM borrows WHERE shelf=? AND agent=? AND status='active'"
                " ORDER BY granted_at DESC LIMIT 1", (shelf, agent)).fetchone()
            if row:
                c.execute(
                    "UPDATE borrows SET status=?, released_at=?, usd_cost=?,"
                    " item=COALESCE(?, item) WHERE id=?",
                    (status, _now(), float(usd_cost or 0), item, row["id"]),
                )
    return {"ok": True}


@contextmanager
def borrowed(shelf, agent, sector=None, reason="", data_class="internal",
             est_cost=0.0, cost_ceiling=0.0, ttl=300, timeout=120):
    """
    `with borrowed('stock_images', 'content-writer', reason='header for case study'):`
    Acquires the shelf and always releases it, even on error. Raises if it could
    not get the shelf so the caller never proceeds thinking it holds it.
    """
    r = borrow(shelf, agent, sector, reason, data_class, est_cost, cost_ceiling,
               ttl=ttl, wait=True, timeout=timeout)
    if not r["ok"]:
        raise TimeoutError(r["error"])
    try:
        yield r
    finally:
        release(shelf, agent, borrow_id=r.get("borrow_id"))


# ---------------------------------------------------------------------------
# LIVE STATE — what the shelf board renders (holder / queue / usage)
# ---------------------------------------------------------------------------

def _day_start():
    lt = time.localtime()
    return int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)))


def shelf_state(shelf_names=None, recent=5):
    """
    A live snapshot per shelf for the dashboard:
      holder      — who holds it now (+ how long, + seconds left on the lock)
      queue       — who is waiting, in order (the owner asked to see this)
      today_spend — USD borrowed on this shelf since midnight (Overseer's view)
      recent      — the last few checkouts (agent, item, approval, status, cost)

    Includes any shelf named in `shelf_names` plus any that appears live in the
    DB, so nothing in flight is ever hidden.
    """
    names = set((s or "").strip().lower() for s in (shelf_names or []) if s)
    now = _now()
    day0 = _day_start()
    with _conn() as c:
        # A cabinet created before the shelf tables existed is still perfectly
        # valid — report the shelves as idle rather than crashing the board.
        # (init_db.py is idempotent and will add the tables on the next run.)
        def _rows(sql, args=()):
            try:
                return c.execute(sql, args).fetchall()
            except sqlite3.OperationalError:
                return []

        for tbl, col in (("shelf_queue", "shelf"), ("borrows", "shelf")):
            for r in _rows(f"SELECT DISTINCT {col} AS s FROM {tbl}"):
                if r["s"]:
                    names.add(r["s"])
        for r in _rows("SELECT resource FROM locks WHERE resource LIKE 'shelf:%'"):
            names.add(r["resource"].split("shelf:", 1)[1])

        out = {}
        for shelf in sorted(names):
            _lk = _rows("SELECT holder,acquired_at,expires_at FROM locks WHERE resource=?",
                        (_resource(shelf),))
            lk = _lk[0] if _lk else None
            holder = None
            if lk and lk["expires_at"] and lk["expires_at"] >= now:
                holder = {"agent": lk["holder"],
                          "held_for_s": now - (lk["acquired_at"] or now),
                          "expires_in_s": lk["expires_at"] - now}
            queue = [
                {"agent": q["agent"], "sector": q["sector"], "reason": q["reason"],
                 "data_class": q["data_class"], "waited_s": now - (q["enqueued_at"] or now)}
                for q in _rows(
                    "SELECT * FROM shelf_queue WHERE shelf=? AND status='waiting'"
                    " ORDER BY enqueued_at", (shelf,))
            ]
            _sp = _rows("SELECT COALESCE(SUM(usd_cost),0) AS s FROM borrows"
                        " WHERE shelf=? AND granted_at>=?", (shelf, day0))
            spend = _sp[0]["s"] if _sp else 0
            hist = [
                {"agent": b["agent"], "sector": b["sector"], "item": b["item"],
                 "approval": b["approval"], "status": b["status"],
                 "usd_cost": b["usd_cost"], "reason": b["reason"],
                 "granted_at": b["granted_at"], "released_at": b["released_at"]}
                for b in _rows(
                    "SELECT * FROM borrows WHERE shelf=? ORDER BY granted_at DESC LIMIT ?",
                    (shelf, recent))
            ]
            out[shelf] = {"holder": holder, "queue": queue,
                          "today_spend": round(spend or 0, 4), "recent": hist}
    return out


if __name__ == "__main__":  # tiny smoke test
    try:
        from .init_db import init
    except ImportError:
        from init_db import init
    init()
    a = borrow("stock_images", "content-writer", "Content",
               reason="header image for a case study")
    print("borrow A:", a)
    b = borrow("stock_images", "seo-strategist", "SEO",
               reason="og image", wait=False)
    print("borrow B (should be busy):", b)
    print("state:", shelf_state(["stock_images", "email"]))
    release("stock_images", "content-writer", borrow_id=a["borrow_id"],
            item="pexels:glass-balustrade", usd_cost=0.0)
    print("after release:", shelf_state(["stock_images"]))
