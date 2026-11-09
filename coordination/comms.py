"""
comms.py — the sectors talking to each other, for real.

WHAT WAS WRONG

The office floor drew beams between the rooms with labels on them: "verify
lead?", "✓ cleared", "keyword worth it?". They looked exactly like the agency
working. They were eight hardcoded strings picked at random every 2.2 seconds,
and they fired identically on a cabinet with nothing in it. A screen that
animates whether or not anything happened is not a view of the business, it is
a screensaver — and worse, it is a screensaver that tells the owner his agency
is busy on a morning when nothing ran.

WHAT IT IS NOW

Every line below comes out of the `requests` / `request_msgs` tables — the real
request board, the one the handbook makes the sectors use ("Request, don't
reach"). If two sectors have not spoken, the floor is quiet, and that quiet is
information.

WHY THE REQUEST BOARD AND NOT A CHAT TABLE

Because it already exists and it is already the law. Every cross-sector exchange
in this agency — Leads asking Security to clear a lead, Content asking SEO
whether a keyword is worth writing for, anyone asking IT for a live-site change
— goes through board.post_request. Adding a second, prettier channel would mean
either duplicating those rows or having two places where a sector can be asked
something, which is exactly the overriding the board exists to prevent. So this
module reads; board.py stays the only thing that writes.

STATES, AND WHAT THEY MEAN ON THE FLOOR

  open      asked, nobody has picked it up. Amber past the waiting line: the
            asker may be blocked on it.
  claimed   an agent took it and owes an answer.
  answered  there is a reply in the thread.
  closed    done with.
"""
import argparse
import json
import sqlite3
import time

try:
    from . import board
except ImportError:  # pragma: no cover
    import board

# How long a sector may sit on a question before the floor stops drawing it as
# ordinary traffic and starts drawing it as a problem. Deliberately the same
# number the fault detector uses, so a beam that has gone amber and a fault
# filed against that sector are the same event, not two opinions.
try:
    from . import faults as _faults
    WAITING_LINE = _faults.UNANSWERED_SECS
except Exception:  # pragma: no cover
    WAITING_LINE = 3 * 3600

# The rooms the floor knows how to draw. A request addressed to something else
# is real and still listed, it just has nowhere to draw a beam to.
ROOMS = {"director", "it", "content", "seo", "leads", "report", "security"}

# Which ROOM a sender sits in. The Overseer and the Critic are not rooms on the
# floor — they work out of the Director's office — so a message from either is
# drawn from Director while still being LABELLED as who actually sent it. The
# floor is a map of the building; the text is a record of who spoke.
ROOM_OF = {"overseer": "Director", "critic": "Director", "md": "Director",
           "managing-director": "Director", "owner": "Director",
           "reporter": "Report"}


def room_of(sector):
    s = (sector or "").strip()
    return ROOM_OF.get(s.lower(), s)


def _conn():
    c = sqlite3.connect(board.DB, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def _rows(c, q, args=()):
    try:
        return [dict(r) for r in c.execute(q, args)]
    except Exception:
        return []


def _when(ts, now=None):
    """
    A time somebody can place. Clock time for today, a weekday name inside the
    last week, a date beyond that — the same way a person writes it down.
    """
    ts = int(ts or 0)
    if not ts:
        return ""
    now = int(now or time.time())
    lt, nt = time.localtime(ts), time.localtime(now)
    hhmm = time.strftime("%H:%M", lt)
    if (lt.tm_year, lt.tm_yday) == (nt.tm_year, nt.tm_yday):
        return hhmm
    days = (now - ts) // 86400
    if days < 1:
        return "yesterday " + hhmm
    if days < 7:
        return time.strftime("%a ", lt) + hhmm
    return time.strftime("%-d %b ", lt) + hhmm if hasattr(time, "strftime") else hhmm


def _ago(secs):
    secs = max(0, int(secs))
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def _short(subject, n=22):
    """A beam label has room for a few words, so shorten honestly."""
    s = " ".join((subject or "").split())
    return s if len(s) <= n else s[:n - 1].rstrip() + "…"


def threads(limit=40, since=None) -> list:
    """
    Real cross-sector exchanges, newest activity first, with their replies.

    `since` limits it to threads with activity after that time — what the floor
    wants, because a beam should be drawn for something that actually just
    happened, not for a question from last Tuesday.
    """
    now = int(time.time())
    c = _conn()
    try:
        board._migrate(c)
        args = []
        q = ("SELECT r.id, r.from_sector, r.to_sector, r.subject, r.status,"
             " r.handler, r.created_at,"
             " (SELECT max(at) FROM request_msgs m WHERE m.request_id=r.id) last_at,"
             " (SELECT count(*) FROM request_msgs m WHERE m.request_id=r.id) n_msgs"
             " FROM requests r")
        if since:
            q += (" WHERE COALESCE((SELECT max(at) FROM request_msgs m"
                  " WHERE m.request_id=r.id), r.created_at) >= ?")
            args.append(int(since))
        q += " ORDER BY COALESCE(last_at, r.created_at) DESC LIMIT ?"
        args.append(int(limit))

        out = []
        for r in _rows(c, q, tuple(args)):
            msgs = _rows(c, "SELECT author, body, at FROM request_msgs"
                            " WHERE request_id=? ORDER BY at", (r["id"],))
            last = int(r["last_at"] or r["created_at"] or now)
            waited = now - int(r["created_at"] or now)
            # Answered means somebody other than the asker has spoken in it.
            replies = [m for m in msgs
                       if (m["author"] or "").lower() != (r["from_sector"] or "").lower()]
            state = r["status"]
            if state in ("open", "claimed") and replies:
                state = "answered"
            waiting = state in ("open", "claimed")
            f_room, t_room = room_of(r["from_sector"]), room_of(r["to_sector"])
            out.append({
                "id": r["id"],
                "from": r["from_sector"], "to": r["to_sector"],
                # Where to draw it from and to, which is not always who sent it.
                "from_room": f_room, "to_room": t_room,
                "subject": r["subject"] or "(no subject)",
                "label": _short(r["subject"]),
                "state": state, "handler": r["handler"] or "",
                "at": int(r["created_at"] or now), "last_at": last,
                "ago": _ago(now - last), "waited": waited,
                "n_msgs": r["n_msgs"] or 0,
                # What the floor paints it. Amber is not decoration: it means a
                # sector is waiting on another sector and has been for a while.
                "tone": ("amber" if (waiting and waited > WAITING_LINE)
                         else "glass" if not waiting else "live"),
                "waiting": waiting,
                # Drawable needs two DIFFERENT rooms on the map. A message the
                # Director's office sent to itself is real and belongs in the
                # list, but there is no beam to draw for it.
                "drawable": (f_room.lower() in ROOMS and t_room.lower() in ROOMS
                             and f_room.lower() != t_room.lower()),
                # WHEN, NOT JUST WHAT TIME.
                #
                # This was the clock time and nothing else, so a thread whose
                # four messages were sent on four different days read as
                # 21:51, 02:47, 23:11, 16:23 — out of order to the eye, with
                # no way to tell which came first. He said he could not work
                # out which message came at which time, and he was right: the
                # information was not on the screen.
                #
                # 'when' carries the day when it is not today, so the same four
                # read as 21:51, "Sun 02:47", "Mon 23:11", "23 Aug 16:23".
                "messages": [{"author": m["author"], "body": m["body"],
                              "at": m["at"],
                              "hhmm": time.strftime("%H:%M", time.localtime(m["at"] or 0)),
                              "when": _when(m["at"] or 0, now),
                              "ago": _ago(now - int(m["at"] or now))} for m in msgs],
            })
        return out
    finally:
        c.close()


def feed(limit=40, window=24 * 3600) -> dict:
    """
    What the office floor draws, and what the panel lists.

    The floor gets `beams`: recent exchanges between two rooms it can actually
    draw. If that list is empty the floor stays quiet — which is the whole
    point. Nothing here is invented to fill the silence.
    """
    now = int(time.time())
    all_threads = threads(limit=limit)
    recent = [t for t in all_threads if t["last_at"] >= now - window]
    beams = [t for t in recent if t["drawable"]]
    waiting = [t for t in all_threads if t["waiting"]]
    stuck = [t for t in waiting if t["waited"] > WAITING_LINE]

    if not all_threads:
        head = "the sectors have not spoken yet"
    elif not recent:
        head = f"nothing said today · {len(all_threads)} older exchange(s)"
    else:
        head = f"{len(recent)} exchange(s) today"
        if stuck:
            head += f" · {len(stuck)} waiting on an answer"

    return {"ok": True, "at": now, "head": head,
            "beams": beams, "threads": all_threads,
            "today": len(recent), "waiting": len(waiting), "stuck": len(stuck)}


def thread(request_id) -> dict:
    """One conversation in full, for reading it."""
    for t in threads(limit=500):
        if t["id"] == request_id:
            return {"ok": True, "thread": t}
    return {"ok": False, "error": f"no exchange {request_id!r}"}


def as_text(limit=20) -> str:
    d = feed(limit=limit)
    L = [f"SECTOR TRAFFIC — {d['head']}"]
    for t in d["threads"][:limit]:
        flag = "!" if t["tone"] == "amber" else " "
        L.append(f" {flag} {t['from']:>8} → {t['to']:<8} {t['state']:<8} "
                 f"{t['ago']:>8}  {t['subject'][:56]}")
        for m in t["messages"][-2:]:
            L.append(f"        {m['author']}: {(m['body'] or '')[:70]}")
    if not d["threads"]:
        L.append("  (nothing — no sector has asked another for anything)")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(
        description="What the sectors have actually said to each other.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--thread", help="one exchange in full, by id")
    a = ap.parse_args()
    if a.thread:
        out = thread(a.thread)
        print(json.dumps(out) if a.json else
              "\n".join([f"{out['thread']['from']} → {out['thread']['to']}: "
                         f"{out['thread']['subject']}"] +
                        [f"  {m['hhmm']} {m['author']}: {m['body']}"
                         for m in out["thread"]["messages"]])
              if out["ok"] else out["error"])
        return
    print(json.dumps(feed(a.limit)) if a.json else as_text(a.limit))


if __name__ == "__main__":
    main()
