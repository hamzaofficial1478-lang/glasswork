"""
faults.py — what happens when an AGENT breaks, rather than the website.

THE HOLE THIS FILLS

The owner watched `seo-strategist` sit dead for forty-one minutes. The control
room drew it in red, correctly, and that was the entire consequence: no ticket,
no sector told, the MD planning the next day around a desk that was not working,
and IT — the only sector with hands that can fix code — never hearing about it.
The stall was DISPLAYED, not HANDLED. Everything below is the handling.

WHO IS RESPONSIBLE FOR WHAT

The handbook is clear and it is worth restating, because the obvious answer is
the wrong one:

  Overseer   internal affairs. Watches the AGENTS — stalls, loops, token burn,
             broken contracts. It is the one that notices. This module is its
             eyes; the Overseer agent is what reads it.
  the sector The desk belongs to it. SEO must know its strategist is down, or it
             will keep planning work for an agent that cannot do it. It is told
             every time, whether or not it can fix the cause.
  IT         has the only agents allowed to open a file and change code
             (it-fixer, it-releaser). A stall or a crash is a code or process
             fault, so IT gets the ticket to actually fix it. This is the answer
             to "why is IT not noticing" — nothing was telling it.
  MD         is told when it is no longer one desk having a bad afternoon:
             something blocking, or the same fault surviving several passes, or
             a sector with more than one thing broken at once. The MD plans
             around a sector that is down; it cannot do that if nobody says so.
  owner      hears through notify.py, at the urgency the fault actually
             deserves, and not more often than once a day.

WHY IT IS A TABLE AND NOT A MESSAGE

Because a fault has to be closed. A message goes out once and is either acted
on or forgotten; a row can be re-checked next pass and marked resolved when the
underlying condition clears, so the agency stops chasing something that has
already fixed itself. Every fault carries a stable `key`, so a stall that is
still true at the next scan raises seen_count instead of filing a second ticket
and buzzing the owner twice for one problem.

NOTHING HERE INVENTS A FAULT. Every row below is read out of a row the work
itself wrote — a run that never ended, a task claimed and abandoned, a request
with no answer. Same rule as activity.py: if it is on this list it happened.
"""
import argparse
import json
import sqlite3
import time

try:
    from . import board
except ImportError:  # pragma: no cover
    import board

# ---- when patience runs out -------------------------------------------------
# Nothing in this agency legitimately takes twenty minutes. The site check times
# out in under a minute; the Hunter is capped well below it. Kept the same as
# state.STALL_SECS on purpose — a desk drawn as stalled on the floor and a desk
# with a fault filed against it must be the same desk, or the screens and the
# tickets tell two different stories.
STALL_SECS = 1200
# A task claimed and then sat on. Longer than a stall: the claim is held across
# a whole pipeline, not one call.
ABANDONED_SECS = 3600
# A sector asked another sector for something and got nothing back. Requests are
# how the sectors work at all, so an unanswered one stops real work.
UNANSWERED_SECS = 3 * 3600
# A shared resource — the live site, the publishing pipeline — held past its
# lease. Everyone else queues behind it, so this is blocking by definition.
LOCK_GRACE = 600
# Survive this many scans and it is not a blip, it is a fault the sector is not
# dealing with. The MD hears about it.
ESCALATE_AFTER = 3
# More than this many open faults in one sector and the sector itself is the
# problem, whatever the individual causes are.
SECTOR_ALARM = 3

BLOCKING, DEGRADED, NOTED = "blocking", "degraded", "noted"

# Which sector owns a desk. Same two exceptions as everywhere else: the MD runs
# as 'managing-director' and Report runs as 'reporter', so neither is found by
# splitting on the first hyphen.
SECTOR_OF = {
    "managing-director": "Director", "md": "Director", "critic": "Director",
    "reporter": "Report", "report": "Report", "overseer": "Director",
    "it": "IT", "content": "Content", "seo": "SEO", "leads": "Leads",
    "security": "Security",
}

# Who is asked to FIX each kind of fault, as opposed to who is told about it.
# A stall or a crash is code or process, and IT owns code. An abandoned task or
# an unanswered request is a sector not doing its own job — the fix is that
# sector picking the work back up, and handing that to IT would be nonsense.
FIXER_OF = {"stall": "IT", "crash": "IT", "lock": "IT",
            "abandoned": None, "unanswered": None}

OVERSEER = "Overseer"


def _conn():
    c = sqlite3.connect(board.DB, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def _rows(c, q, args=()):
    try:
        return [dict(r) for r in c.execute(q, args).fetchall()]
    except Exception:
        return []       # a cabinet built before a table existed is not an error


def _one(c, q, args=()):
    try:
        r = c.execute(q, args).fetchone()
        return r[0] if r else 0
    except Exception:
        return 0


def sector_of(agent):
    n = (agent or "").strip().lower()
    return SECTOR_OF.get(n) or SECTOR_OF.get(n.split("-", 1)[0]) or ""


def _ago(secs):
    secs = max(0, int(secs))
    if secs < 60:
        return "under a minute"
    if secs < 3600:
        return f"{secs // 60} minutes"
    if secs < 86400:
        return f"{secs // 3600} hours"
    return f"{secs // 86400} days"


# ----------------------------------------------------------------------------
# DETECT — read the rows, say what is broken. No writes, so it is safe to call
# from anywhere, including a screen.
# ----------------------------------------------------------------------------

def detect(now=None) -> list:
    """Every fault currently true, as plain dicts. Reads only."""
    now = int(now or time.time())
    out = []
    c = _conn()
    try:
        board._migrate(c)

        # 1. A RUN THAT NEVER CAME BACK. The one the owner actually hit.
        #    'stalled' is included: once reaped the row is no longer claiming to
        #    be running, but the fault is not fixed just because the Overseer
        #    stopped believing the claim. It stays open until the cause is dealt
        #    with, or resolve() closes it.
        #
        #    THE DESK IS BETTER WHEN IT RUNS AGAIN. Not when the dead run
        #    changes — it never will, that is what dead means. The NOT EXISTS
        #    below is the recovery test: the same agent has since started a run
        #    that finished cleanly. Without it a stall stayed on the books for
        #    ever, and the owner sat looking at a desk that was plainly working
        #    while the screen still reported it dead.
        for r in _rows(c, "SELECT r.id, r.agent, r.started_at, r.task_id, r.note,"
                          " r.status, t.title FROM agent_runs r LEFT JOIN tasks t"
                          " ON t.id=r.task_id"
                          " WHERE r.status IN ('running','stalled') AND r.started_at < ?"
                          "   AND NOT EXISTS (SELECT 1 FROM agent_runs b"
                          "     WHERE b.agent=r.agent AND b.status='ok'"
                          "       AND b.started_at > r.started_at)"
                          " ORDER BY r.started_at", (now - STALL_SECS,)):
            held = now - int(r["started_at"] or now)
            out.append({
                "key": f"stall:{r['agent']}:{r['started_at']}",
                "kind": "stall", "agent": r["agent"], "sector": sector_of(r["agent"]),
                "severity": BLOCKING if held > 4 * STALL_SECS else DEGRADED,
                "what": f"{r['agent']} started a run {_ago(held)} ago and never "
                        "reported back",
                "detail": (f"It was working on: {r['title'] or r['note'] or 'no task recorded'}. "
                           "A run in this state is not slow, it is gone — the process was "
                           "killed, or it is waiting on something that will never answer. "
                           "Nothing it was doing was half-written; the run is open, not "
                           "finished, so no other agent has picked the work up either."),
                "evidence": f"agent_runs#{r['id']}",
            })

        # 2. A RUN THAT RAISED. Already over, but it left work undone and it will
        #    almost certainly raise again the next time it is asked.
        day0 = now - 24 * 3600
        for r in _rows(c, "SELECT r.id, r.agent, r.ended_at, r.note, t.title"
                          " FROM agent_runs r LEFT JOIN tasks t ON t.id=r.task_id"
                          " WHERE r.status='error' AND r.ended_at >= ?"
                          " ORDER BY r.ended_at DESC LIMIT 40", (day0,)):
            out.append({
                "key": f"crash:{r['id']}",
                "kind": "crash", "agent": r["agent"], "sector": sector_of(r["agent"]),
                "severity": DEGRADED,
                "what": f"{r['agent']} crashed: {(r['note'] or 'no error recorded')[:120]}",
                "detail": (f"The run raised and stopped{(', while working on ' + r['title']) if r['title'] else ''}. "
                           "It will raise again the next time this agent is asked to do "
                           "the same thing, so this is worth fixing rather than re-running."),
                "evidence": f"agent_runs#{r['id']}",
            })

        # 3. A TASK CLAIMED AND THEN SAT ON. Claiming is exclusive — while this
        #    row is held, no other agent may touch the work. An abandoned claim
        #    is worse than an untouched task: it looks handled.
        for r in _rows(c, "SELECT id, sector, title, owner, claimed_at FROM tasks"
                          " WHERE status='in_progress' AND claimed_at < ?"
                          " ORDER BY claimed_at", (now - ABANDONED_SECS,)):
            held = now - int(r["claimed_at"] or now)
            out.append({
                "key": f"abandoned:{r['id']}:{r['claimed_at']}",
                "kind": "abandoned", "agent": r["owner"] or "", "sector": r["sector"],
                "severity": DEGRADED,
                "what": f"{r['owner'] or 'someone'} claimed \"{(r['title'] or '')[:70]}\" "
                        f"{_ago(held)} ago and has not finished it",
                "detail": ("A claimed task belongs to one agent and nobody else may "
                           "touch it. While this claim is held the work cannot move, "
                           "and the board reads as though it is being dealt with. "
                           "Either finish it or release the claim."),
                "evidence": f"tasks#{r['id']}",
            })

        # 4. ONE SECTOR ASKED ANOTHER AND GOT NOTHING BACK. Requests are how the
        #    sectors work at all — the asker is stopped until it hears.
        #
        #    GROUPED, not one fault per row. A pipeline that runs daily asks the
        #    same unanswered question again every day, and filing each copy
        #    separately turned one silent sector into fourteen tickets. The same
        #    question from the same sector to the same sector is ONE fault, and
        #    how many times it has been asked is part of what makes it bad.
        asked = {}
        for r in _rows(c, "SELECT id, from_sector, to_sector, subject, created_at,"
                          " status FROM requests"
                          " WHERE status IN ('open','claimed') AND created_at < ?"
                          " ORDER BY created_at", (now - UNANSWERED_SECS,)):
            k = (r["to_sector"], r["from_sector"], (r["subject"] or "")[:80])
            g = asked.setdefault(k, {"n": 0, "first": r["created_at"], "ids": [],
                                     "claimed": 0})
            g["n"] += 1
            g["ids"].append(str(r["id"]))
            g["first"] = min(g["first"] or now, r["created_at"] or now)
            g["claimed"] += 1 if r["status"] == "claimed" else 0
        for (to_s, from_s, subj), g in asked.items():
            waited = now - int(g["first"] or now)
            again = (f" It has now been asked {g['n']} times and answered none of "
                     "them." if g["n"] > 1 else "")
            out.append({
                "key": f"unanswered:{to_s}:{from_s}:{subj}".lower(),
                "kind": "unanswered", "agent": "", "sector": to_s,
                # Asked repeatedly and still ignored is not a slow reply any more,
                # it is a sector that is not listening.
                "severity": BLOCKING if g["n"] >= 3 else DEGRADED,
                "what": f"{to_s} has not answered {from_s} in {_ago(waited)}: {subj[:70]}"
                        + (f" (×{g['n']})" if g["n"] > 1 else ""),
                "detail": (f"{from_s} asked and is waiting. A blocking verification "
                           "means the asker stops until it hears back, so this is not "
                           "one sector's problem — it is two sectors idle." + again
                           + ("" if not g["claimed"] else
                              f" {g['claimed']} of them were claimed, which means an "
                              "agent took the question and then did not reply.")),
                "evidence": "requests#" + ",".join(g["ids"][:8]),
            })

        # 5. A SHARED RESOURCE HELD PAST ITS LEASE. Everyone else is queued
        #    behind it by design, so this one is blocking whatever it looks like.
        for r in _rows(c, "SELECT resource, holder, acquired_at, expires_at"
                          " FROM locks WHERE expires_at < ?", (now - LOCK_GRACE,)):
            over = now - int(r["expires_at"] or now)
            out.append({
                "key": f"lock:{r['resource']}:{r['acquired_at']}",
                "kind": "lock", "agent": r["holder"], "sector": sector_of(r["holder"]),
                "severity": BLOCKING,
                "what": f"{r['holder']} has held {r['resource']} for {_ago(over)} "
                        "past its lease",
                "detail": ("A resource lock has one holder and everyone else waits "
                           "their turn. This one expired and was never released, so "
                           "every agent that needs " + str(r["resource"]) +
                           " is queued behind an agent that has gone."),
                "evidence": f"locks:{r['resource']}",
            })
    finally:
        c.close()
    return out


# ----------------------------------------------------------------------------
# FILE — write what detect() found, once per distinct fault.
# ----------------------------------------------------------------------------

def _upsert(c, f, now):
    """
    Insert or refresh one fault. Returns (row, is_new).

    A fault that was closed and has come back REOPENS. The first version just
    bumped last_seen on the resolved row, so a sector that answered a question
    once and then ignored the next three was never reported again — the key was
    already on file and the row said 'resolved'. Reopening clears the thread and
    ticket ids too, or it would point at conversations that were closed off.
    """
    row = c.execute("SELECT * FROM faults WHERE key=?", (f["key"],)).fetchone()
    if row:
        if row["status"] == "resolved":
            c.execute("""UPDATE faults SET status='open', resolved_at=NULL,
                         resolution=?, request_id=NULL, task_id=NULL,
                         escalated_at=NULL, last_seen=?, seen_count=seen_count+1,
                         severity=?, what=?, detail=? WHERE key=?""",
                      (f"came back after being closed at "
                       f"{time.strftime('%H:%M %d %b', time.localtime(row['resolved_at'] or now))}",
                       now, f["severity"], f["what"], f["detail"], f["key"]))
            return dict(c.execute("SELECT * FROM faults WHERE key=?", (f["key"],)).fetchone()), True
        c.execute("UPDATE faults SET last_seen=?, seen_count=seen_count+1,"
                  " severity=?, what=?, detail=? WHERE key=?",
                  (now, f["severity"], f["what"], f["detail"], f["key"]))
        return dict(c.execute("SELECT * FROM faults WHERE key=?", (f["key"],)).fetchone()), False
    c.execute("""INSERT INTO faults
                 (key, kind, agent, sector, severity, what, detail, evidence,
                  first_seen, last_seen, seen_count, status)
                 VALUES (?,?,?,?,?,?,?,?,?,?,1,'open')""",
              (f["key"], f["kind"], f.get("agent") or "", f.get("sector") or "",
               f["severity"], f["what"], f["detail"], f.get("evidence") or "",
               now, now))
    return dict(c.execute("SELECT * FROM faults WHERE key=?", (f["key"],)).fetchone()), True


def _still_true(c, f, now):
    """Has the underlying condition cleared? Read the same row detect() read."""
    kind, ev = f["kind"], (f["evidence"] or "")
    try:
        if kind in ("stall", "crash") and ev.startswith("agent_runs#"):
            rid = int(ev.split("#", 1)[1])
            r = c.execute("SELECT status, started_at FROM agent_runs WHERE id=?",
                          (rid,)).fetchone()
            if not r:
                return False
            if kind == "stall":
                # WHEN IS A DEAD DESK BETTER?
                #
                # Not when the dead run changes — it never will; that is what
                # dead means, and the Overseer's own 'stalled' mark is a
                # retraction of the claim, not a recovery. The honest signal is
                # THE SAME AGENT RUNNING AGAIN AND FINISHING. That is what the
                # owner was looking at when he said the desk was live again
                # while the log still called it dead.
                if r["status"] not in ("running", "stalled"):
                    return False        # the run itself reached a real ending
                if (now - int(r["started_at"] or now)) <= STALL_SECS:
                    return False
                back = c.execute(
                    "SELECT 1 FROM agent_runs WHERE agent=? AND status='ok'"
                    " AND started_at > ? LIMIT 1",
                    (f["agent"], int(r["started_at"] or 0))).fetchone()
                return not back
            return r["status"] == "error"
        if kind == "abandoned" and ev.startswith("tasks#"):
            tid = ev.split("#", 1)[1]
            r = c.execute("SELECT status, claimed_at FROM tasks WHERE id=?", (tid,)).fetchone()
            return bool(r) and r["status"] == "in_progress" \
                and (now - int(r["claimed_at"] or now)) > ABANDONED_SECS
        if kind == "unanswered" and ev.startswith("requests#"):
            # Grouped: still true while ANY of the copies is unanswered.
            ids = [i for i in ev.split("#", 1)[1].split(",") if i]
            if not ids:
                return False
            q = ("SELECT count(*) FROM requests WHERE status IN ('open','claimed')"
                 " AND id IN (%s)" % ",".join("?" * len(ids)))
            return bool(c.execute(q, ids).fetchone()[0])
        if kind == "lock" and ev.startswith("locks:"):
            res = ev.split(":", 1)[1]
            r = c.execute("SELECT expires_at FROM locks WHERE resource=?", (res,)).fetchone()
            return bool(r) and int(r["expires_at"] or 0) < now - LOCK_GRACE
    except Exception:
        pass
    return True     # cannot tell: leave it open rather than closing it blind


def _reap_run(c, row, now):
    """
    Retract a claim the agent can no longer make.

    A run row says status='running'. That is a statement about RIGHT NOW, and
    the agent that wrote it is gone, so nobody will ever correct it. Every
    screen that reads the row re-derives "stalled — started 12h ago and never
    came back", forever, including long after the desk recovered and was working
    again — the owner watched a live green agent sit underneath a permanent
    report that it was dead.

    Marking it 'stalled' with an ended_at turns an eternal present tense into
    something that happened, at a time, once. The FAULT stays open — the
    Overseer disbelieving the claim is not the same as the problem being fixed —
    which is why _still_true() counts 'stalled' as ongoing.

    Only reaped after the fault has been filed and routed. Reaping first would
    erase the evidence before anyone was told.
    """
    if not (row["request_id"] or row["task_id"]):
        return False
    ev = row["evidence"] or ""
    if row["kind"] != "stall" or not ev.startswith("agent_runs#"):
        return False
    try:
        rid = int(ev.split("#", 1)[1])
    except ValueError:
        return False
    r = c.execute("SELECT status, started_at, note FROM agent_runs WHERE id=?",
                  (rid,)).fetchone()
    if not r or r["status"] != "running":
        return False
    held = now - int(r["started_at"] or now)
    c.execute("UPDATE agent_runs SET status='stalled', ended_at=?, note=?"
              " WHERE id=? AND status='running'",
              (now, (f"stopped responding after {_ago(held)} — "
                     f"declared dead by the Overseer" +
                     (f" (was: {r['note']})" if r["note"] else ""))[:400], rid))
    c.commit()
    return True


def _close_out(c, row, note, status):
    """
    Tidy up everything a fault opened: the sector's thread and the fixer's
    ticket. A resolved fault that leaves IT holding "Fix: seo-strategist
    stalled" for a stall that ended is how a board fills with work nobody needs
    to do — and the sector never hears that it is off the hook.

    The failures here used to be swallowed by a bare `except: pass`, which hid a
    database lock and silently left every thread open. If this cannot finish, it
    says so on the fault rather than pretending.
    """
    problems = []
    if row["request_id"]:
        try:
            board.reply_request(row["request_id"], OVERSEER, note, close=True)
        except Exception as e:
            problems.append(f"could not close the thread with "
                            f"{row['sector']}: {e}")
    if row["task_id"]:
        try:
            # Withdraw, not finish. If IT has already claimed this the work is
            # IT's and the Overseer may not close it — the ticket gets a note
            # and IT decides. Claim, don't grab, applies to unclaiming too.
            board.withdraw_task(row["task_id"], OVERSEER, note)
        except Exception as e:
            problems.append(f"could not withdraw the ticket with "
                            f"{row['routed_to']}: {e}")
    if problems:
        c.execute("UPDATE faults SET resolution=? WHERE id=?",
                  ((row["resolution"] or note) + " — but " + "; ".join(problems),
                   row["id"]))
        c.commit()
    return not problems


def _route(c, row, now):
    """
    Tell the people who need to know, exactly once per fault.

    Two messages, and they are different messages. The owning sector is told
    because its desk is down and it must stop planning around it. IT is given a
    TICKET, because it is the only sector with an agent that can open a file —
    but only for the faults that are actually code. A sector sitting on its own
    claimed task does not need IT; it needs to do the work.
    """
    sector = (row["sector"] or "").strip()
    body = (f"{row['what']}.\n\n{row['detail']}\n\n"
            f"Evidence: {row['evidence']}. First seen "
            f"{time.strftime('%H:%M', time.localtime(row['first_seen']))}.")

    # Name the fault for what it is. "Your desk is down" is true of a stall and
    # nonsense for a sector that is up and simply has not replied to anyone.
    subject = {
        "stall": "Your desk is down",
        "crash": "One of your agents is crashing",
        "abandoned": "You are sitting on a claimed task",
        "unanswered": "You have not answered",
        "lock": "You are holding a lock nobody can take",
    }.get(row["kind"], "Fault on your desk")

    rid = row["request_id"]
    if sector and not rid:
        # Commit first: board opens its own connection, and an uncommitted write
        # on this one holds the database lock against it.
        c.commit()
        rid = board.post_request(
            OVERSEER, sector, f"{subject}: {row['what'][:80]}", body)

    tid = row["task_id"]
    fixer = FIXER_OF.get(row["kind"])
    if fixer and not tid:
        c.commit()
        # force=True: a fault ticket is ABOUT a sector, not work FOR it, and it
        # has to be filed even when the owner has stopped the sector that broke.
        # (The fixer here is IT, which he can also switch off — if he has, he
        # has said he does not want IT working, and this ticket waits with
        # everything else rather than vanishing.)
        tid = board.post_task(
            fixer,
            f"Fix: {row['what'][:90]}",
            body + "\n\nThis is an agency fault, not a website fault. The desk "
                   "that broke belongs to " + (sector or "an unknown sector") +
                   "; you have been given it because you are the sector that can "
                   "change code. Reproduce it from the command line first — the "
                   "run is in agent_runs and the error, if there was one, is in "
                   "its note.",
            risk="green", created_by=OVERSEER, force=True)

    c.execute("UPDATE faults SET request_id=?, task_id=?, routed_to=?,"
              " status=CASE WHEN status='open' THEN 'routed' ELSE status END"
              " WHERE id=?",
              (rid, tid, fixer or sector, row["id"]))
    return rid, tid


def _escalate(c, row, now):
    """
    Tell the MD — but only when it has stopped being one desk's bad afternoon.

    Two triggers, both evidence rather than mood: it is blocking, so work is
    stopped behind it; or it has survived enough passes that whoever it was
    routed to is plainly not dealing with it.

    A THIRD trigger used to live here — "this sector has several faults open" —
    and it was a mistake that showed up the first time this ran for real: it
    fired on every fault in that sector, so one quiet sector put forty-six
    separate requests in the MD's inbox. An escalation nobody can read is not an
    escalation. The sector-wide pattern is a real signal, but it is ONE signal,
    so it is raised once for the sector in _sector_alarm() below.
    """
    if row["escalated_at"]:
        return False
    why = None
    if row["severity"] == BLOCKING:
        why = "it is blocking — work is stopped behind it, not merely slower"
    elif int(row["seen_count"] or 0) >= ESCALATE_AFTER:
        why = (f"it has survived {row['seen_count']} passes. It was routed to "
               f"{row['routed_to'] or 'its sector'} and is still true, so the "
               "routing is not working")
    if not why:
        return False

    c.commit()      # board writes on its own connection; do not hold the lock
    board.post_request(
        OVERSEER, "Director",
        f"Escalating: {row['what'][:80]}",
        f"{row['what']}.\n\n{row['detail']}\n\n"
        f"Escalated because {why}.\n\n"
        f"Routed to: {row['routed_to'] or 'nobody yet'}. "
        f"Seen {row['seen_count']} time(s) since "
        f"{time.strftime('%H:%M', time.localtime(row['first_seen']))}. "
        f"Evidence: {row['evidence']}.\n\n"
        "Plan around this sector until it clears. Do not post it work that "
        "depends on the desk that is down.",
        # Name the row, so the MD routes from the fault's own sector rather than
        # hunting for a sector name in the subject line.
        blocking=(row["severity"] == BLOCKING), about=f"faults:{row['id']}")
    c.execute("UPDATE faults SET escalated_at=?, status='escalated' WHERE id=?",
              (now, row["id"]))
    return True


def _sector_alarm(c, now, counts):
    """
    A sector with several things broken at once is a sector problem, whatever
    the individual causes are — and it is worth exactly one message, naming all
    of them. Once a day per sector: the point is that the MD plans around it
    tomorrow, not that it is reminded every twenty minutes today.
    """
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    raised = 0
    for sector, n in counts.items():
        if n < SECTOR_ALARM or not sector:
            continue
        key = f"alarm:{sector}:{day}"
        if c.execute("SELECT 1 FROM faults WHERE key=?", (key,)).fetchone():
            continue
        rows = _rows(c, "SELECT what, kind, routed_to FROM faults"
                        " WHERE status!='resolved' AND lower(sector)=?"
                        " ORDER BY last_seen DESC LIMIT 12", (sector,))
        name = next((r["what"] for r in rows), sector)
        listing = "\n".join(f"  · [{r['kind']}] {r['what']}" for r in rows)
        c.commit()      # board writes on its own connection
        board.post_request(
            OVERSEER, "Director",
            f"{sector.title()} has {n} faults open at once",
            f"{sector.title()} is not having one bad run, it is not working:\n\n"
            f"{listing}\n\n"
            "Each of these has been routed on its own. This message is about the "
            "pattern — plan tomorrow assuming this sector is unreliable until it "
            "clears, and do not stack work on it that something else depends on.",
            about=f"sector:{sector}")
        # Recorded as a fault of its own so it is filed once and shows on the
        # board with everything else, rather than being an invisible side effect.
        c.execute("""INSERT INTO faults
                     (key, kind, agent, sector, routed_to, severity, what, detail,
                      evidence, first_seen, last_seen, seen_count, escalated_at, status)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,1,?,'escalated')""",
                  (key, "sector", "", sector.title(), "Director", DEGRADED,
                   f"{sector.title()} has {n} faults open at once",
                   f"Raised once for the sector rather than once per fault. "
                   f"Most recent: {name}", f"faults:{sector}", now, now, now))
        c.commit()
        raised += 1
    return raised


def scan(now=None) -> dict:
    """
    One pass: find what is broken, file it, route it, escalate what deserves it,
    and close what has fixed itself. Idempotent — running it twice in a minute
    files nothing twice.
    """
    now = int(now or time.time())
    found = detect(now)
    keys = {f["key"] for f in found}
    filed = routed = escalated = resolved = reaped = 0
    c = _conn()
    try:
        board._migrate(c)

        # Close first, so escalation counts are not inflated by faults that have
        # already cleared this pass.
        for row in _rows(c, "SELECT * FROM faults WHERE status!='resolved'"):
            if row["key"] in keys:
                continue
            if _still_true(c, row, now):
                continue
            note = ("the desk is working again — "
                    f"{row['agent']} has since completed a run"
                    if row["kind"] == "stall" and row["agent"]
                    else "cleared on its own — the condition is no longer true")
            c.execute("UPDATE faults SET status='resolved', resolved_at=?,"
                      " resolution=? WHERE id=?", (now, note, row["id"]))
            c.commit()      # board writes on its own connection; free the lock
            _close_out(c, row, f"Closing this: {note}. " + row["what"],
                       "resolved-itself")
            resolved += 1
        c.commit()

        rows = []
        for f in found:
            row, is_new = _upsert(c, f, now)
            filed += 1 if is_new else 0
            rows.append(row)
        c.commit()

        for row in rows:
            fresh = dict(c.execute("SELECT * FROM faults WHERE id=?", (row["id"],)).fetchone())
            if not fresh["request_id"] and not fresh["task_id"]:
                _route(c, fresh, now)
                routed += 1
                c.commit()
                fresh = dict(c.execute("SELECT * FROM faults WHERE id=?", (row["id"],)).fetchone())
            if _escalate(c, fresh, now):
                escalated += 1
                c.commit()
            # Somebody has been told; now retract the dead run's claim to be
            # working, so the screens stop reporting it in the present tense.
            if _reap_run(c, fresh, now):
                reaped += 1

        counts = {}
        for row in _rows(c, "SELECT lower(sector) s, count(*) n FROM faults"
                            " WHERE status!='resolved' AND kind!='sector'"
                            " GROUP BY lower(sector)"):
            counts[row["s"] or ""] = row["n"]
        alarms = _sector_alarm(c, now, counts)
        c.commit()
    finally:
        c.close()
    return {"ok": True, "at": now, "found": len(found), "filed": filed,
            "routed": routed, "escalated": escalated, "resolved": resolved,
            "reaped": reaped, "sector_alarms": alarms}


# ----------------------------------------------------------------------------
# READ — for the screens, the MD and the Overseer.
# ----------------------------------------------------------------------------

def open_faults(sector=None, limit=50) -> list:
    c = _conn()
    try:
        board._migrate(c)
        q = ("SELECT * FROM faults WHERE status!='resolved'")
        args = []
        if sector:
            q += " AND lower(sector)=?"
            args.append(str(sector).lower())
        q += " ORDER BY CASE severity WHEN 'blocking' THEN 0 WHEN 'degraded'" \
             " THEN 1 ELSE 2 END, last_seen DESC LIMIT ?"
        args.append(int(limit))
        return _rows(c, q, tuple(args))
    finally:
        c.close()


def summary() -> dict:
    """One line per sector plus the totals, for the floor and the MD's sense pass."""
    c = _conn()
    try:
        board._migrate(c)
        rows = _rows(c, "SELECT * FROM faults WHERE status!='resolved'")
    finally:
        c.close()
    by_sector, by_kind = {}, {}
    for r in rows:
        by_sector[r["sector"] or "—"] = by_sector.get(r["sector"] or "—", 0) + 1
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
    blocking = [r for r in rows if r["severity"] == BLOCKING]
    unrouted = [r for r in rows if not r["request_id"] and not r["task_id"]]
    return {"ok": True, "open": len(rows), "blocking": len(blocking),
            "escalated": len([r for r in rows if r["escalated_at"]]),
            "unrouted": len(unrouted),
            "by_sector": by_sector, "by_kind": by_kind,
            "worst": (sorted(rows, key=lambda r: (r["severity"] != BLOCKING,
                                                  -int(r["seen_count"] or 0)))
                      [:5]),
            "head": ("nothing broken" if not rows else
                     f"{len(blocking)} blocking, {len(rows)} open" if blocking else
                     f"{len(rows)} open")}


def resolve(fault_id, note="fixed"):
    """Close a fault by hand — the fixer saying it is done."""
    now = int(time.time())
    c = _conn()
    try:
        row = c.execute("SELECT * FROM faults WHERE id=?", (fault_id,)).fetchone()
        if not row:
            return {"ok": False, "error": f"no fault #{fault_id}"}
        c.execute("UPDATE faults SET status='resolved', resolved_at=?, resolution=?"
                  " WHERE id=?", (now, note[:400], fault_id))
        c.commit()
        clean = _close_out(c, dict(row), f"Resolved: {note}", "resolved-by-hand")
        return {"ok": True, "id": fault_id, "note": note, "threads_closed": clean}
    finally:
        c.close()


def as_text() -> str:
    s = summary()
    L = [f"FAULTS — {s['head']}"]
    for r in open_faults():
        flag = {"blocking": "!!", "degraded": " *", "noted": "  "}.get(r["severity"], "  ")
        where = r["routed_to"] or "not routed"
        L.append(f" {flag} [{r['id']:>3}] {r['kind']:<10} {r['what'][:88]}")
        L.append(f"      → {where}"
                 + (f", MD told" if r["escalated_at"] else "")
                 + f", seen {r['seen_count']}x")
    if s["open"] == 0:
        L.append("  (nothing — no agent is stalled, crashed or sitting on work)")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(
        description="Faults in the agency itself — stalled, crashed and abandoned agents.")
    # Running this module DOES the work, the same as every other pipeline step:
    # the day runner calls `python -m coordination.faults` with no arguments and
    # expects the pass to happen. Looking without touching is --list/--detect.
    ap.add_argument("--scan", action="store_true",
                    help="the default: find, file, route and escalate; close what has cleared")
    ap.add_argument("--list", action="store_true",
                    help="show the open faults without scanning")
    ap.add_argument("--detect", action="store_true",
                    help="what is broken right now, without writing anything")
    ap.add_argument("--resolve", type=int, metavar="ID", help="close a fault by hand")
    ap.add_argument("--note", default="fixed")
    ap.add_argument("--sector")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    a.scan = a.scan or not (a.list or a.detect or a.resolve)

    if a.resolve:
        out = resolve(a.resolve, a.note)
    elif a.detect:
        out = {"ok": True, "faults": detect()}
    elif a.scan:
        out = scan()
        out["summary"] = summary()
    else:
        out = {"ok": True, "summary": summary(),
               "faults": open_faults(a.sector)}

    if a.json:
        print(json.dumps(out, default=str))
    elif a.detect:
        for f in out["faults"]:
            print(f"  {f['severity']:<9} {f['kind']:<10} {f['what']}")
        if not out["faults"]:
            print("  (nothing broken)")
    elif a.scan:
        print(f"scanned: {out['found']} true, {out['filed']} newly filed, "
              f"{out['routed']} routed, {out['escalated']} escalated to the MD, "
              f"{out['resolved']} closed")
        print(as_text())
    elif a.resolve:
        print(out.get("note") or out.get("error"))
    else:
        print(as_text())


if __name__ == "__main__":
    main()
