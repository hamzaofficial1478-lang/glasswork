"""
activity.py — one timeline of what every agent got, grabbed, pushed, delivered,
or broke on.

WHY THIS IS DERIVED AND NOT LOGGED

The obvious build is an `activity` table every agent writes a line to. That was
rejected: a log an agent has to remember to write is a log that drifts from what
actually happened, and the first time an agent forgets a line the timeline
becomes a nicer story than the truth. Every event below is read back out of the
row the work itself created — a claimed task, a granted borrow, a finished run,
a sent mail. If it is on this timeline, the work really happened; if the work
happened, it is on this timeline. There is no third case, and nothing to keep
in sync.

THE FIVE VERBS

  got        something arrived for this sector to deal with — a request from
             another sector, the owner's order for the day
  grabbed    it took something that is now exclusively its own — claimed a task
             off the board, took a shelf. Grabbing is the moment nobody else can
             have the thing, which is why it gets its own verb
  pushed     it put work into the building — posted a task, filed a ticket,
             proposed something to the MD
  delivered  it finished — a run ended clean, a task closed, a mail went out, a
             page published
  errored    it did not — a run raised, a run stalled, Security flagged something

WHAT IS DELIBERATELY NOT HERE

Individual leads. The Hunter can add ninety in one pass and ninety rows would
bury everything else on the floor; the run's own line already says "added 90
leads", which is the honest summary. Same reasoning for rankings and snapshots.
The rule: one line per DECISION or DELIVERY, not one line per row written.
"""
import argparse
import json
import sqlite3
import time

try:
    from . import board
except ImportError:  # pragma: no cover
    import board

# Where the cabinet lives is board's to say — see the note in state.py for what
# happens when a module works it out for itself.
def _db():
    return board.DB

GOT, GRABBED, PUSHED, DELIVERED, ERRORED = (
    "got", "grabbed", "pushed", "delivered", "errored")

# Which room a name belongs to. Most agents are named sector-role, so the first
# segment is the room — but two are not: the MD runs as 'managing-director' and
# Report runs as 'reporter'. Both are listed in full, or every line either of
# them writes lands in no room at all. (Same trap as state.AGENT_ALIASES.)
ROOM = {"md": "Director", "director": "Director", "managing": "Director",
        "managing-director": "Director", "critic": "Director",
        "it": "IT", "content": "Content", "seo": "SEO", "leads": "Leads",
        "report": "Report", "reporter": "Report",
        "security": "Security", "owner": "Director"}

# Whose call a logged decision is, from the cycle that logged it. Anything not
# listed is named after its own cycle, never quietly credited to the MD.
DECISION_OWNER = {
    "md-cycle": ("the MD's day plan", "managing-director", "Director"),
    "seo-gap": ("SEO's keyword-gap list", "seo-strategist", "SEO"),
    "competitor-watch": ("SEO's rival watch", "seo-strategist", "SEO"),
    "content-briefs": ("Content's page briefs", "content-writer", "Content"),
    "it-health": ("IT's health check", "it-watcher", "IT"),
    "site-services": ("IT's services check", "it-watcher", "IT"),
    "leads-hunt": ("the Leads hunt", "leads-hunter", "Leads"),
    "leads-sources": ("Leads' source map", "leads-hunter", "Leads"),
    "security-screen": ("Security's screening", "security", "Security"),
}


def decision_owner(cycle) -> tuple:
    """(whose call, the desk, the room) for a decision's cycle."""
    cycle = (cycle or "").strip()
    if cycle in DECISION_OWNER:
        return DECISION_OWNER[cycle]
    room = _room_of(cycle) or "Report"
    return f"{cycle or 'an unnamed'} call", cycle or "report-reporter", room


WINDOW = 24 * 3600      # how far back the floor looks by default
PER_SOURCE = 40         # cap per source so one chatty table cannot drown the rest


def _conn():
    c = sqlite3.connect(_db(), timeout=30)
    c.row_factory = sqlite3.Row
    return c


def _rows(c, q, args=()):
    try:
        return c.execute(q, args).fetchall()
    except Exception:
        return []       # a cabinet built before a table existed is not an error


def _room_of(name):
    n = (name or "").strip().lower()
    return ROOM.get(n) or ROOM.get(n.split("-", 1)[0]) or ""


def _ago(secs):
    secs = max(0, int(secs))
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60}m ago"
    if secs < 86400:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400}d ago"


def _ev(at, verb, who, what, detail="", room=None, level="normal"):
    return {"at": int(at or 0), "verb": verb, "who": who or "—",
            "room": room if room is not None else _room_of(who),
            "what": what, "detail": (detail or "")[:220], "level": level}


def since(ts=None, limit=60) -> list:
    """Everything that happened after `ts`, newest first."""
    now = int(time.time())
    ts = int(ts if ts is not None else now - WINDOW)
    c = _conn()
    ev = []

    # ---- runs: the spine of the timeline --------------------------------
    for r in _rows(c, "SELECT r.agent, r.status, r.started_at, r.ended_at, r.note,"
                      " r.usd_cost, t.title FROM agent_runs r"
                      " LEFT JOIN tasks t ON t.id=r.task_id"
                      " WHERE r.started_at>=? ORDER BY r.started_at DESC LIMIT ?",
                   (ts, PER_SOURCE)):
        line = r["note"] or r["title"] or "a run"
        if r["status"] == "ok":
            ev.append(_ev(r["ended_at"] or r["started_at"], DELIVERED, r["agent"],
                          line, f"${r['usd_cost']:.2f}" if r["usd_cost"] else ""))
        elif r["status"] == "error":
            ev.append(_ev(r["ended_at"] or r["started_at"], ERRORED, r["agent"],
                          line, "the run raised and stopped", level="bad"))
        elif r["status"] == "stopped":
            ev.append(_ev(r["ended_at"] or r["started_at"], GOT, r["agent"],
                          "stopped part-way", r["note"] or "", level="normal"))
        elif r["status"] == "stalled":
            # Reaped by the Overseer. Past tense, and anchored to when it was
            # declared dead rather than to now — see the note below.
            ev.append(_ev(r["ended_at"] or r["started_at"], ERRORED, r["agent"],
                          "stopped responding and was declared dead",
                          r["note"] or "", level="bad"))
        elif r["status"] == "running":
            # A run still claiming to be running past the line is either
            # genuinely stuck or a corpse nobody has swept up. Say the second
            # thing, because the owner can act on it: the Overseer marks it
            # 'stalled' on its next pass and this line stops moving.
            #
            # It used to read "started 12h ago and never came back", recomputed
            # from now() on every read, which meant a desk that had recovered
            # hours ago still had a fresh-looking report of being dead sitting
            # at the top of the log. The age is anchored to the run's own start
            # now, and the line says what will happen to it.
            stalled = (now - r["started_at"]) > 1200
            ev.append(_ev(r["started_at"], ERRORED if stalled else GRABBED, r["agent"],
                          ("stopped responding — began at "
                           + time.strftime("%H:%M", time.localtime(r["started_at"]))
                           + " and never reported back") if stalled else (line + " …"),
                          ("the Overseer closes this off on its next pass"
                           if stalled else "working now"),
                          level="bad" if stalled else "live"))

    # ---- the board ------------------------------------------------------
    for t in _rows(c, "SELECT id,sector,title,owner,created_by,risk,status,"
                      " created_at,claimed_at,finished_at FROM tasks"
                      " WHERE COALESCE(finished_at,claimed_at,created_at)>=?"
                      " ORDER BY COALESCE(finished_at,claimed_at,created_at) DESC"
                      " LIMIT ?", (ts, PER_SOURCE)):
        if (t["created_at"] or 0) >= ts:
            ev.append(_ev(t["created_at"], PUSHED, t["created_by"] or "MD",
                          f"posted to {t['sector']}: {t['title']}",
                          "needs your nod" if t["risk"] in ("amber", "red") else "",
                          level="amber" if t["risk"] in ("amber", "red") else "normal"))
        if (t["claimed_at"] or 0) >= ts and t["owner"]:
            ev.append(_ev(t["claimed_at"], GRABBED, t["owner"],
                          f"claimed: {t['title']}",
                          "nobody else can take this now"))
        if (t["finished_at"] or 0) >= ts and t["status"] in ("done", "failed"):
            ev.append(_ev(t["finished_at"],
                          DELIVERED if t["status"] == "done" else ERRORED,
                          t["owner"] or t["sector"],
                          f"{'finished' if t['status'] == 'done' else 'failed'}: {t['title']}",
                          level="normal" if t["status"] == "done" else "bad"))

    # ---- one sector asking another --------------------------------------
    for r in _rows(c, "SELECT id,from_sector,to_sector,subject,status,handler,"
                      " created_at FROM requests WHERE created_at>=?"
                      " ORDER BY created_at DESC LIMIT ?", (ts, PER_SOURCE)):
        ev.append(_ev(r["created_at"], PUSHED, r["from_sector"],
                      f"asked {r['to_sector']}: {r['subject']}",
                      "waiting on an answer" if r["status"] == "open" else r["status"]))
        ev.append(_ev(r["created_at"], GOT, r["to_sector"],
                      f"a question from {r['from_sector']}: {r['subject']}",
                      "blocking them until answered" if r["status"] == "open" else ""))
    for m in _rows(c, "SELECT m.author, m.body, m.at, r.from_sector, r.subject"
                      " FROM request_msgs m LEFT JOIN requests r ON r.id=m.request_id"
                      " WHERE m.at>=? ORDER BY m.at DESC LIMIT ?", (ts, PER_SOURCE)):
        ev.append(_ev(m["at"], DELIVERED, m["author"],
                      f"answered {m['from_sector'] or 'a request'}: {m['subject'] or ''}",
                      (m["body"] or "")[:180]))

    # ---- the shelf: taking a shared API out of everyone else's reach ----
    for b in _rows(c, "SELECT shelf,item,agent,sector,reason,status,usd_cost,"
                      " granted_at,released_at FROM borrows WHERE granted_at>=?"
                      " ORDER BY granted_at DESC LIMIT ?", (ts, PER_SOURCE)):
        ev.append(_ev(b["granted_at"], GRABBED, b["agent"],
                      f"took the {b['shelf']} shelf" + (f" ({b['item']})" if b["item"] else ""),
                      b["reason"] or "", room=_room_of(b["sector"] or b["agent"]),
                      level="live" if b["status"] == "active" else "normal"))
        if (b["released_at"] or 0) >= ts:
            ev.append(_ev(b["released_at"], DELIVERED, b["agent"],
                          f"put the {b['shelf']} shelf back",
                          f"${b['usd_cost']:.2f}" if b["usd_cost"] else "",
                          room=_room_of(b["sector"] or b["agent"])))
    for q in _rows(c, "SELECT shelf,agent,sector,status,enqueued_at FROM shelf_queue"
                      " WHERE enqueued_at>=? AND status='waiting'"
                      " ORDER BY enqueued_at DESC LIMIT ?", (ts, PER_SOURCE)):
        ev.append(_ev(q["enqueued_at"], GOT, q["agent"],
                      f"queued for the {q['shelf']} shelf — somebody else has it",
                      "waiting its turn", room=_room_of(q["sector"] or q["agent"]),
                      level="amber"))

    # ---- what the sectors produced --------------------------------------
    for s in _rows(c, "SELECT kind,severity,detail,url,found_by,found_at FROM site_issues"
                      " WHERE found_at>=? ORDER BY found_at DESC LIMIT ?", (ts, PER_SOURCE)):
        ev.append(_ev(s["found_at"], PUSHED, s["found_by"] or "it-watcher",
                      f"filed a {s['severity'] or ''} {s['kind']} ticket".replace("  ", " "),
                      (s["detail"] or s["url"] or "")[:180], room="IT",
                      level="bad" if (s["severity"] or "").lower() in ("critical", "high") else "amber"))
    for o in _rows(c, "SELECT l.company, o.channel, o.direction, o.reply_class,"
                      " o.needs_owner, o.at FROM outreach_log o"
                      " LEFT JOIN leads l ON l.id=o.lead_id"
                      " WHERE o.at>=? ORDER BY o.at DESC LIMIT ?", (ts, PER_SOURCE)):
        who = o["company"] or "a lead"
        if o["direction"] == "sent":
            ev.append(_ev(o["at"], DELIVERED, "leads-outreach",
                          f"{o['channel'] or 'email'} sent to {who}", room="Leads"))
        else:
            ev.append(_ev(o["at"], GOT, "leads-responder",
                          f"{who} replied ({o['reply_class'] or 'unclassified'})",
                          "needs you" if o["needs_owner"] else "",
                          room="Leads", level="amber" if o["needs_owner"] else "good"))
    for p in _rows(c, "SELECT title,type,status,created_at FROM content_queue"
                      " WHERE created_at>=? ORDER BY created_at DESC LIMIT ?",
                   (ts, PER_SOURCE)):
        held = (p["status"] or "").startswith("blocked_on_")
        ev.append(_ev(p["created_at"],
                      DELIVERED if p["status"] == "published" else PUSHED,
                      "content-writer",
                      f"{p['status']}: {p['title']}",
                      "held for " + p["status"].replace("blocked_on_", "") if held else "",
                      room="Content", level="amber" if held else "normal"))
    # Security catching a scam is Security WORKING, not Security failing. The
    # verb stays 'got' — something bad arrived from outside — and the severity
    # goes into the level, so it still shows red without accusing the screener
    # of having broken. 'errored' is reserved for our own agents falling over.
    for s in _rows(c, "SELECT kind,severity,detail,action,at FROM security_events"
                      " WHERE at>=? ORDER BY at DESC LIMIT ?", (ts, PER_SOURCE)):
        sev = (s["severity"] or "").lower()
        ev.append(_ev(s["at"], GOT, "security-screener",
                      f"{(s['kind'] or 'event').replace('_', ' ')} — {s['action'] or 'logged'}",
                      (s["detail"] or "")[:180], room="Security",
                      level="bad" if sev in ("critical", "high") else "amber"))

    # ---- the proposal queue and the owner's orders ----------------------
    for p in _rows(c, "SELECT sector,title,status,why,created_at,decided_at"
                      " FROM proposals WHERE COALESCE(decided_at,created_at)>=?"
                      " ORDER BY COALESCE(decided_at,created_at) DESC LIMIT ?",
                   (ts, PER_SOURCE)):
        if (p["created_at"] or 0) >= ts:
            ev.append(_ev(p["created_at"], PUSHED, p["sector"],
                          f"proposed: {p['title']}", "waiting on the MD"))
        if (p["decided_at"] or 0) >= ts:
            ev.append(_ev(p["decided_at"], GOT, p["sector"],
                          f"MD {p['status']} it: {p['title']}", p["why"] or "",
                          level="normal" if p["status"] == "accepted" else "amber"))
    for o in _rows(c, "SELECT sector,orders,updated_at FROM sector_orders"
                      " WHERE updated_at>=? ORDER BY updated_at DESC LIMIT ?",
                   (ts, PER_SOURCE)):
        ev.append(_ev(o["updated_at"], GOT, o["sector"],
                      "the owner's order for today", o["orders"] or "", level="amber"))

    # ---- every desk's logged calls, and how Report scored them -----------
    #
    # "what heppens to the MD all the sudden it failed why?" — it had not. This
    # read "scored the MD's call: failed" for EVERY decision in the table, and
    # most of them are not the MD's: SEO's keyword-gap lists, IT's health
    # checks, the Leads hunt. Each is filed under the desk that made it, and the
    # line names whose call it was and why Report scored it the way it did.
    for d in _rows(c, "SELECT cycle,reasoning,confidence,outcome,outcome_note,"
                      "created_at,scored_at"
                      " FROM decisions WHERE COALESCE(scored_at,created_at)>=?"
                      " ORDER BY COALESCE(scored_at,created_at) DESC LIMIT ?",
                   (ts, PER_SOURCE)):
        whose, who, room = decision_owner(d["cycle"])
        if (d["created_at"] or 0) >= ts:
            ev.append(_ev(d["created_at"], PUSHED, who,
                          f"logged a call: {whose}", (d["reasoning"] or "")[:180],
                          room=room))
        if (d["scored_at"] or 0) >= ts and d["outcome"]:
            why = d["outcome_note"] or ""
            ev.append(_ev(d["scored_at"], DELIVERED if d["outcome"] == "worked" else ERRORED,
                          "report-reporter",
                          f"scored {whose}: {d['outcome']}",
                          (f"why: {why} — " if why else "") + (d["reasoning"] or ""),
                          room=room,
                          level="good" if d["outcome"] == "worked" else "amber"))

    c.close()
    ev.sort(key=lambda e: e["at"], reverse=True)
    for e in ev:
        e["ago"] = _ago(now - e["at"])
        e["hhmm"] = time.strftime("%H:%M", time.localtime(e["at"])) if e["at"] else ""
    return ev[:max(1, int(limit))]


def read_mark(what="activity") -> int:
    """How far the owner has read. 0 if he never has."""
    c = _conn()
    try:
        board._migrate(c)
        r = _rows(c, "SELECT seen_to FROM read_marks WHERE what=?", (what,))
        return int(r[0]["seen_to"]) if r else 0
    finally:
        c.close()


def clear_mark(what="activity") -> int:
    """How far he has CLEARED. Everything at or before is hidden from the list."""
    c = _conn()
    try:
        board._migrate(c)
        r = _rows(c, "SELECT cleared_to FROM read_marks WHERE what=?", (what,))
        return int(r[0]["cleared_to"] or 0) if r else 0
    finally:
        c.close()


def clear_to(cleared_to=None, what="activity") -> int:
    """
    Hide everything up to `cleared_to` from the list.

    This is not a delete and must never become one. Every line in the activity
    log is derived from a row the work itself wrote — a claimed task, a granted
    borrow, a finished run — so 'deleting an event' would mean deleting the
    record that the work happened. The owner wanted a button that empties the
    screen; this empties the screen. `--show-all` puts it back, and the history
    was never gone.
    """
    now = int(time.time())
    ts = int(cleared_to or now)
    if ts > now + 300:
        ts = now
    c = _conn()
    try:
        board._migrate(c)
        c.execute("""INSERT INTO read_marks (what, seen_to, cleared_to, at)
                     VALUES (?,?,?,?)
                     ON CONFLICT(what) DO UPDATE SET
                       seen_to=MAX(read_marks.seen_to, excluded.seen_to),
                       cleared_to=MAX(COALESCE(read_marks.cleared_to,0), excluded.cleared_to),
                       at=excluded.at""", (what, ts, ts, now))
        c.commit()
        return ts
    finally:
        c.close()


def show_all(what="activity") -> bool:
    """Undo a Clear. The events were never gone, only hidden."""
    c = _conn()
    try:
        board._migrate(c)
        c.execute("UPDATE read_marks SET cleared_to=0 WHERE what=?", (what,))
        c.commit()
        return True
    except Exception:
        return False
    finally:
        c.close()


def mark_read(seen_to=None, what="activity") -> int:
    """
    Record that everything up to `seen_to` has been read, and return the mark.

    It only ever moves FORWARD. Two tabs open, one of them showing a poll from a
    minute ago, and the older one would otherwise drag the mark backwards and
    resurrect a count he had just cleared — which is what he kept seeing.
    """
    now = int(time.time())
    ts = int(seen_to or now)
    if ts > now + 300:      # a browser with a wrong clock cannot mark the future read
        ts = now
    c = _conn()
    try:
        # Migrate FIRST. A cabinet built before read_marks existed has no table
        # to write to, and the except below used to turn that into a silent
        # success — the owner pressed the button, was told it worked, and the
        # count came back on the very next poll. Three times.
        board._migrate(c)
        c.execute("""INSERT INTO read_marks (what, seen_to, at) VALUES (?,?,?)
                     ON CONFLICT(what) DO UPDATE SET
                       seen_to=MAX(read_marks.seen_to, excluded.seen_to),
                       at=excluded.at""", (what, ts, now))
        c.commit()
        r = _rows(c, "SELECT seen_to FROM read_marks WHERE what=?", (what,))
        return int(r[0]["seen_to"]) if r else ts
    finally:
        c.close()


def feed(ts=None, limit=60) -> dict:
    """
    The window's events, plus how many of them the owner has not seen.

    The read mark is when he last OPENED the panel, not a window cutoff — the
    badge has to keep counting while he is away, the way an unread count does,
    and the panel still shows the whole day when he opens it. Two different
    questions, one call, because a badge that disagrees with the list it opens
    is worse than no badge.

    Where the mark is kept matters more than it sounds. It used to live in the
    browser, and localStorage is per-origin: the office on localhost and the
    control room on 127.0.0.1 kept separate marks, so clearing one left the
    other showing the same stubborn number. It is read from the cabinet now, so
    every screen agrees. A caller may still pass `ts` to ask a hypothetical.
    """
    everything = since(None, limit)               # the full window, always
    seen_to = int(ts) if ts else read_mark()
    unread = (sum(1 for e in everything if e["at"] > seen_to) if seen_to
              else len(everything))

    # EVERY NUMBER BELOW IS COUNTED FROM THE FULL WINDOW, not from the visible
    # list. 'Clear' is a preference about what he wants to look at; whether two
    # agents are running or three things are broken is a fact about the agency.
    # Counting the visible list instead would mean pressing Clear made a live
    # problem disappear from the ticker and the floor, which is the single worst
    # thing a status screen can do.
    counts = {}
    for e in everything:
        counts[e["verb"]] = counts.get(e["verb"], 0) + 1
    live = sum(1 for e in everything if e["level"] == "live")
    bad = sum(1 for e in everything if e["level"] == "bad")
    newest_at = max([e["at"] for e in everything], default=int(time.time()))

    hidden_to = clear_mark()
    ev = [e for e in everything if e["at"] > hidden_to] if hidden_to else everything
    hidden = len(everything) - len(ev)

    head = (f"{live} happening now" if live else
            f"{bad} problem{'s' if bad != 1 else ''}" if bad else
            f"{len(everything)} things today" if everything else "nothing has run yet")
    if hidden:
        head += f" · {hidden} cleared"

    # newest_at is what a screen should mark as read when it shows this feed:
    # the newest thing it actually put in front of him, not the clock. Marking
    # the clock on a browser running a minute fast reads the future.
    return {"ok": True, "at": int(time.time()), "events": ev,
            "newest_at": newest_at,
            "unread": unread, "seen_to": seen_to,
            "cleared_to": hidden_to, "hidden": hidden, "total": len(everything),
            "counts": counts, "live": live, "errors": bad, "head": head}


def as_text(limit=40) -> str:
    d = feed(limit=limit)
    L = [f"ACTIVITY — {d['head']}"]
    for e in d["events"]:
        L.append(f"  {e['hhmm']}  {e['verb']:9} {(e['room'] or '—'):9} {e['who']:20} {e['what']}")
    if not d["events"]:
        L.append("  (nothing — no pipeline has run in the last 24 hours)")
    return "\n".join(L)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="What every agent has been doing.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--since", type=int, default=None)
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--mark-read", type=int, nargs="?", const=0, default=None,
                    metavar="TS",
                    help="record that everything up to TS has been read "
                         "(no TS = now), then print the feed")
    ap.add_argument("--clear", type=int, nargs="?", const=0, default=None,
                    metavar="TS",
                    help="hide everything up to TS from the list (no TS = now). "
                         "Hides only — nothing is deleted, --show-all puts it back")
    ap.add_argument("--show-all", action="store_true",
                    help="undo a --clear and show the whole window again")
    a = ap.parse_args()
    if a.show_all:
        show_all()
        mark = 0
    elif a.clear is not None:
        mark = clear_to(a.clear or None)
    elif a.mark_read is not None:
        mark = mark_read(a.mark_read or None)
    else:
        mark = None

    if mark is None:
        print(json.dumps(feed(a.since, a.limit)) if a.json else as_text(a.limit))
    elif a.json:
        d = feed(a.since, a.limit)
        d["marked_to"] = mark
        print(json.dumps(d))
    elif a.show_all:
        print("showing everything again")
    elif a.clear is not None:
        print(f"cleared up to {time.strftime('%H:%M:%S', time.localtime(mark))} "
              "— hidden, not deleted")
    else:
        print(f"read up to {time.strftime('%H:%M:%S', time.localtime(mark))}")
