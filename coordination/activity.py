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
        elif r["status"] == "running":
            stalled = (now - r["started_at"]) > 1200
            ev.append(_ev(r["started_at"], ERRORED if stalled else GRABBED, r["agent"],
                          ("stalled — started " + _ago(now - r["started_at"])
                           + " and never came back") if stalled else (line + " …"),
                          "" if stalled else "working now",
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

    # ---- the MD's own calls ---------------------------------------------
    for d in _rows(c, "SELECT cycle,reasoning,confidence,outcome,created_at,scored_at"
                      " FROM decisions WHERE COALESCE(scored_at,created_at)>=?"
                      " ORDER BY COALESCE(scored_at,created_at) DESC LIMIT ?",
                   (ts, PER_SOURCE)):
        if (d["created_at"] or 0) >= ts:
            ev.append(_ev(d["created_at"], PUSHED, "md",
                          f"logged a call ({d['cycle']})", (d["reasoning"] or "")[:180],
                          room="Director"))
        if (d["scored_at"] or 0) >= ts and d["outcome"]:
            ev.append(_ev(d["scored_at"], DELIVERED if d["outcome"] == "worked" else ERRORED,
                          "report-reporter",
                          f"scored the MD's call: {d['outcome']}",
                          (d["reasoning"] or "")[:180], room="Report",
                          level="good" if d["outcome"] == "worked" else "amber"))

    c.close()
    ev.sort(key=lambda e: e["at"], reverse=True)
    for e in ev:
        e["ago"] = _ago(now - e["at"])
        e["hhmm"] = time.strftime("%H:%M", time.localtime(e["at"])) if e["at"] else ""
    return ev[:max(1, int(limit))]


def feed(ts=None, limit=60) -> dict:
    """
    The window's events, plus how many of them the owner has not seen.

    `ts` is when he last OPENED the panel, not a window cutoff — the badge has
    to keep counting while he is away, the way an unread count does, and the
    panel still shows the whole day when he opens it. Two different questions,
    one call, because a badge that disagrees with the list it opens is worse
    than no badge.
    """
    ev = since(None, limit)                       # the full window, always
    seen_to = int(ts or 0)
    unread = sum(1 for e in ev if e["at"] > seen_to) if seen_to else len(ev)
    counts = {}
    for e in ev:
        counts[e["verb"]] = counts.get(e["verb"], 0) + 1
    live = sum(1 for e in ev if e["level"] == "live")
    bad = sum(1 for e in ev if e["level"] == "bad")
    return {"ok": True, "at": int(time.time()), "events": ev,
            "unread": unread, "seen_to": seen_to,
            "counts": counts, "live": live, "errors": bad,
            "head": (f"{live} happening now" if live else
                     f"{bad} problem{'s' if bad != 1 else ''}" if bad else
                     f"{len(ev)} things today" if ev else "nothing has run yet")}


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
    a = ap.parse_args()
    print(json.dumps(feed(a.since, a.limit)) if a.json else as_text(a.limit))
