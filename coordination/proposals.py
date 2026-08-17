"""
proposals.py — the sector proposal queue, and the right of reply.

HOW THE WORK NOW FLOWS

    owner ──▶ sector_orders        what he wants a sector doing today
    sector ─▶ propose()            what the sector thinks it needs
                   │
                   ▼
              THE MD ranks and allocates ── accepted ──▶ tasks ──▶ claimed
                   │                                        │
              rejected, WITH A REASON                       │
                                                            ▼
                                                       object_to_task()
                                                    "this isn't our job"
                                                            │
                                                            ▼
                                             back to the MD, and its DIRECTION
                                                    score feels the miss

WHY A PROPOSAL AND NOT JUST A TASK

A sector that could post its own task would be marking its own homework: it
would decide the work, do the work, and report on the work, with nobody ranking
it against anything else the business needs. So a proposal is deliberately not
claimable. Only the MD turns one into a task, which is what keeps allocation in
one pair of hands while the expertise stays in the sector's.

WHY THE MD CANNOT SILENTLY BIN ONE

Every decision carries a reason, accepted or rejected. A sector that keeps being
turned down can read exactly why, and so can the owner — a proposal queue where
things quietly disappear teaches the sector nothing and hides a bad MD.

THE RIGHT OF REPLY

The MD allocates, and the MD can allocate wrongly — Content work sent to Leads,
or a task needing a key nobody has. Before this, a sector could only do it badly
or leave it rotting on the board. Now it objects, the task is reopened for
re-routing, and the objection is recorded against the MD's direction score. That
is the missing half of turn-taking: the board could always say "claim, don't
grab", but it had no way to say "you have handed me the wrong thing".
"""
import argparse
import json
import sqlite3
import time
import uuid

try:
    from . import board
except ImportError:  # pragma: no cover
    import board

# What a sector may give as grounds for refusing work. A free-text-only refusal
# is impossible to count, and the MD needs to know WHICH way it went wrong.
OBJECTIONS = {
    "wrong_sector": "this is not our work — it belongs to another sector",
    "not_our_call": "this needs a judgement that is not ours to make",
    "missing_capability": "we cannot run this without a key we do not have",
    "unclear": "we cannot tell what is being asked for",
}


def _now():
    return int(time.time())


def _today():
    return time.strftime("%Y-%m-%d", time.localtime())


# ---------------------------------------------------------------------------
# SECTORS PROPOSE
# ---------------------------------------------------------------------------

def propose(sector, title, detail="", evidence="", needs="", urgency=5):
    """
    A sector asks for work. Returns the proposal id, or None if it was refused.

    Refused here, at the door, for the same reason the Critic refuses the MD:
    a proposal with no evidence is a hunch, and a queue full of hunches is worse
    than an empty one because somebody has to read them all.
    """
    if not (sector or "").strip() or not (title or "").strip():
        return None
    if not (evidence or "").strip():
        return None
    board.ensure_schema()
    pid = uuid.uuid4().hex[:12]
    with sqlite3.connect(board.DB, timeout=30) as c:
        # the same sector asking twice for the same thing is one ask
        dup = c.execute(
            "SELECT id FROM proposals WHERE sector=? AND lower(title)=? AND status='open'",
            (sector, title.strip().lower())).fetchone()
        if dup:
            return dup[0]
        c.execute(
            "INSERT INTO proposals(id,sector,title,detail,evidence,needs,urgency,"
            "status,created_at) VALUES(?,?,?,?,?,?,?, 'open', ?)",
            (pid, sector, title.strip(), detail, evidence, needs or "",
             max(1, min(9, int(urgency or 5))), _now()))
    return pid


def open_proposals(sector=None):
    """What is waiting on the MD, most urgent first, then oldest."""
    board.ensure_schema()
    q = ("SELECT id,sector,title,detail,evidence,needs,urgency,created_at"
         " FROM proposals WHERE status='open'")
    args = ()
    if sector:
        q += " AND sector=?"
        args = (sector,)
    q += " ORDER BY urgency ASC, created_at ASC"
    with sqlite3.connect(board.DB, timeout=30) as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute(q, args)]


def decide(proposal_id, status, why, task_id=None):
    """
    The MD's answer. `why` is not optional — a queue that swallows proposals
    without saying why teaches the sector nothing and hides a bad MD.
    """
    if status not in ("accepted", "rejected", "superseded"):
        raise ValueError(f"not a decision: {status}")
    if not (why or "").strip():
        raise ValueError("a decision needs a reason")
    board.ensure_schema()
    with sqlite3.connect(board.DB, timeout=30) as c:
        c.execute("UPDATE proposals SET status=?, why=?, task_id=?, decided_at=?"
                  " WHERE id=? AND status='open'",
                  (status, why, task_id, _now(), proposal_id))


# ---------------------------------------------------------------------------
# SECTORS OBJECT
# ---------------------------------------------------------------------------

def object_to_task(task_id, sector, reason, detail=""):
    """
    "You have handed me the wrong thing."

    The task is put back to 'open' and unowned so the MD can re-route it, and
    the objection is kept. Deliberately NOT deleted: a task that vanishes when
    refused lets a bad allocation disappear without ever showing up in the MD's
    direction score.
    """
    if reason not in OBJECTIONS:
        raise ValueError(f"not a recognised objection: {reason}")
    board.ensure_schema()
    with sqlite3.connect(board.DB, timeout=30) as c:
        row = c.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            return False
        if row[0] == "done":
            return False          # too late to refuse work already finished
        c.execute("INSERT INTO task_objections(task_id,sector,reason,detail,at)"
                  " VALUES(?,?,?,?,?)", (task_id, sector, reason, detail, _now()))
        # hand it back, unowned, with the refusal visible on the task itself
        c.execute(
            "UPDATE tasks SET status='open', owner=NULL, claimed_at=NULL,"
            " detail = COALESCE(detail,'') || ?"
            " WHERE id=?",
            (f"\n\n[{sector} objected: {OBJECTIONS[reason]}"
             + (f" — {detail}" if detail else "") + "]", task_id))
    return True


def objections(since=0):
    board.ensure_schema()
    with sqlite3.connect(board.DB, timeout=30) as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute(
            "SELECT * FROM task_objections WHERE at>=? ORDER BY at DESC", (since,))]


def objected_task_ids():
    """Tasks a sector has pushed back on — the MD must not simply re-post these."""
    board.ensure_schema()
    with sqlite3.connect(board.DB, timeout=30) as c:
        return {r[0] for r in c.execute("SELECT DISTINCT task_id FROM task_objections")}


# ---------------------------------------------------------------------------
# THE OWNER'S ORDERS, PER SECTOR
# ---------------------------------------------------------------------------

def set_orders(sector, text, day=None):
    """What the owner wants this sector doing today, in his own words."""
    board.ensure_schema()
    day = day or _today()
    with sqlite3.connect(board.DB, timeout=30) as c:
        c.execute("INSERT INTO sector_orders(day,sector,orders,updated_at)"
                  " VALUES(?,?,?,?) ON CONFLICT(day,sector) DO UPDATE SET"
                  " orders=excluded.orders, updated_at=excluded.updated_at",
                  (day, sector, (text or "").strip(), _now()))
    return {"day": day, "sector": sector, "orders": (text or "").strip()}


def orders(day=None) -> dict:
    """{sector: what the owner said}. The MD plans around this, not over it."""
    board.ensure_schema()
    day = day or _today()
    with sqlite3.connect(board.DB, timeout=30) as c:
        return {r[0]: r[1] for r in c.execute(
            "SELECT sector, orders FROM sector_orders WHERE day=? AND orders!=''",
            (day,))}


def as_text(day=None) -> str:
    L = ["THE QUEUE"]
    props = open_proposals()
    if not props:
        L.append("  (nothing proposed — sectors are not asking for anything)")
    for p in props:
        L.append(f"  [{p['urgency']}] {p['sector']}: {p['title']}")
        L.append(f"        because: {p['evidence']}")
        if p["needs"]:
            L.append(f"        needs the {p['needs']} shelf")
    o = orders(day)
    L += ["", "THE OWNER'S ORDERS TODAY"]
    if not o:
        L.append("  (none set)")
    for sec, txt in sorted(o.items()):
        L.append(f"  {sec}: {txt}")
    return "\n".join(L)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="The sector proposal queue.")
    ap.add_argument("--order", nargs=2, metavar=("SECTOR", "TEXT"),
                    help="set the owner's order for a sector today")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.order:
        print(json.dumps(set_orders(a.order[0], a.order[1]), indent=2))
    else:
        print(json.dumps({"proposals": open_proposals(), "orders": orders()}, indent=2)
              if a.json else as_text())
