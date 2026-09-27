"""
board.py — the coordination layer that stops agents overriding each other.

Three mechanisms, all backed by one SQLite file (swap for Postgres in production):

  1. Tasks     — an agent must CLAIM a task atomically before working it.
  2. Locks     — shared resources have a single holder; others queue and wait.
  3. Requests  — sector-to-sector messages are append-only; never overwritten.

The whole point: claim, don't grab. Request, don't reach. Append, don't overwrite.

SQLite is used with WAL + IMMEDIATE transactions so a claim is genuinely atomic
even with several agents running at once. Every agent imports this module and
goes through it — no agent writes to the tables directly.
"""

import os
import re
import socket
import sqlite3
import time
import uuid
import json
from contextlib import contextmanager
from pathlib import Path

DB = Path(__file__).parent / "agency.db"


@contextmanager
def _conn():
    con = sqlite3.connect(DB, timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA busy_timeout=30000;")
    try:
        yield con
    finally:
        con.close()


def _this_host() -> str:
    try:
        return socket.gethostname()[:60]
    except Exception:
        return ""


def process_alive(pid, host="") -> bool:
    """
    Is that process still running on THIS machine?

    Unknown counts as alive, on purpose and in three separate ways:

      no pid        a row written before this column existed. Leave it to the
                    time-based reaper rather than declaring it dead now.
      other machine we cannot see another box's process table. Saying "not
                    alive" would let one machine close another's live work.
      cannot tell   a permissions error asking about the pid means the process
                    exists and belongs to somebody else.

    Getting this backwards kills running work, which is far worse than being
    slow to tidy up after a dead one.
    """
    if not pid:
        return True
    if host and host != _this_host():
        return True
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return True
    if pid <= 0:
        return True
    if os.name == "nt":
        try:
            import ctypes
            # 0x1000 = PROCESS_QUERY_LIMITED_INFORMATION. Works without the
            # rights that the older PROCESS_QUERY_INFORMATION demands, which
            # matters because the owner runs this as an ordinary user.
            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
            if not h:
                return False
            code = ctypes.c_ulong()
            ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
            ctypes.windll.kernel32.CloseHandle(h)
            # 259 is STILL_ACTIVE. A handle can outlive the process, so the
            # handle opening is not on its own proof that it is running.
            return bool(ok) and code.value == 259
        except Exception:
            return True
    # A ZOMBIE IS NOT WORKING.
    #
    # A process that has exited stays in the table until whoever started it
    # collects its exit code. os.kill(pid, 0) says "yes, that pid exists" for
    # one of those exactly as it does for a process doing work — so an agent
    # whose parent went away can sit there as a zombie and keep its run row
    # saying 'running' for ever, which is the whole bug this column was added
    # to kill.
    #
    # Linux will tell us plainly if we ask. Nothing else here does, and that is
    # fine: on Windows there is no such state, and on Mac launchd collects them
    # within moments. Only a positive read of 'Z' counts — anything unreadable
    # falls through to the rule above, that unknown means alive.
    try:
        with open(f"/proc/{pid}/stat", "rb") as fh:
            # comm is in brackets and may itself contain spaces or brackets,
            # so the state is the field after the LAST ')'.
            after = fh.read().rsplit(b")", 1)[-1].split()
        if after and after[0] in (b"Z", b"X", b"x"):
            return False
    except Exception:
        pass

    try:
        os.kill(pid, 0)            # signal 0 asks "are you there", sends nothing
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True                # exists, owned by someone else
    except Exception:
        return True


def _really_gone(pid, host="") -> bool:
    """
    Has it actually gone — including the case where it is our own child?

    A killed child does not leave the process table. It sits there as a zombie
    until its parent collects its exit code, and os.kill(pid, 0) answers "yes,
    that pid exists" for a zombie exactly as it does for a process doing work.
    So end_process would TERM a child, KILL it, and then honestly report that it
    was still running.

    waitpid with WNOHANG collects it if it is ours, and raises ChildProcessError
    if it is not — which is the answer we want either way, and the only way to
    tell the two apart without reading /proc.
    """
    if os.name != "nt":
        try:
            got, _status = os.waitpid(int(pid), os.WNOHANG)
            if got:
                return True
        except ChildProcessError:
            pass                       # not our child; nothing to collect
        except Exception:
            pass
    return not process_alive(pid, host)


def end_process(pid, host="") -> dict:
    """
    Ask a process to finish, then insist. Returns what actually happened.

    Only ever called by day_run.stop(), and only after the run has been asked
    politely and given its grace period. Sending a signal is not like reading a
    row: get the pid wrong and you kill something that was minding its own
    business. So the same three "unknown means leave it alone" rules from
    process_alive apply here in reverse — no pid, another machine, or a process
    we cannot see means we do not fire.

    TERM first, because a Python process handling SIGTERM can still tidy up.
    KILL after two seconds, because a wedged one cannot.
    """
    try:
        pid = int(pid or 0)
    except (TypeError, ValueError):
        return {"ok": False, "why": "no process id was recorded for that run"}
    if pid <= 0:
        return {"ok": False, "why": "no process id was recorded for that run"}
    if host and host != _this_host():
        return {"ok": False, "why": f"that run is on {host}, not this machine — "
                                    "stop it there"}
    if pid == os.getpid():
        return {"ok": False, "why": "that is this process; it will not kill itself"}
    if not process_alive(pid, host):
        return {"ok": True, "already_gone": True,
                "why": "the process had already gone"}

    if os.name == "nt":
        try:
            import ctypes
            # 1 = PROCESS_TERMINATE. Windows has no polite signal to send, so
            # this is the only knock on the door there is.
            h = ctypes.windll.kernel32.OpenProcess(1, False, pid)
            if not h:
                return {"ok": False, "why": "the machine would not let us stop it"}
            ok = ctypes.windll.kernel32.TerminateProcess(h, 1)
            ctypes.windll.kernel32.CloseHandle(h)
            return {"ok": bool(ok), "killed": bool(ok),
                    "why": "the process was ended" if ok
                           else "Windows would not let us end it"}
        except Exception as e:
            return {"ok": False, "why": f"could not end the process: {e}"}

    import signal

    # THE STEPS IT SPAWNED HAVE TO GO WITH IT.
    #
    # A run kills nothing on the way out when it is killed itself, so its steps
    # — each its own `python -m pipelines.x` — would be orphaned and carry on
    # writing to the cabinet after he had been told the day was stopped.
    #
    # Signalling the whole process GROUP takes them too. That is only safe when
    # the run is the group's leader, which is exactly the case that matters:
    # the office starts a day detached, and detached means setsid, which makes
    # it one. Started from a terminal it shares the shell's group, and
    # signalling that would kill his shell — so there, one process only.
    group = 0
    try:
        if os.getpgid(pid) == pid:
            group = pid
    except Exception:
        group = 0

    def _signal(sig):
        if group:
            os.killpg(group, sig)
        else:
            os.kill(pid, sig)

    try:
        _signal(signal.SIGTERM)
    except ProcessLookupError:
        return {"ok": True, "already_gone": True,
                "why": "the process had already gone"}
    except Exception as e:
        return {"ok": False, "why": f"could not end the process: {e}"}
    for _ in range(20):
        time.sleep(0.1)
        if _really_gone(pid, host):
            return {"ok": True, "killed": True, "group": bool(group),
                    "why": "the process ended on the first signal"
                           + (" — and the steps it had started with it"
                              if group else "")}
    try:
        _signal(signal.SIGKILL)
    except Exception:
        pass
    time.sleep(0.2)
    gone = _really_gone(pid, host)
    return {"ok": gone, "killed": gone, "forced": True, "group": bool(group),
            "why": ("the process would not go, so it was killed" if gone
                    else "the process is still there — end it from Task Manager")}


# ---------------------------------------------------------------------------
# THE STOP BUTTON
# ---------------------------------------------------------------------------
#
# "there is no any stop button that makes the agents or overseer to understand
# that the task is stopped by the user."
#
# Closing the window was never stopping — sweep_dead_processes() further down
# is what clears up after that. This is the deliberate version: a written
# request that the thing which is running should put its tools down, readable
# by a process that is still alive to read it.
#
# It is a REQUEST and not an order carried out. Nothing here reaches into
# another process. The run polls for the row, finishes tidily and clears it;
# day_run.stop() is what loses patience if it does not.
STOP_SCOPE = "day_run"


def request_stop(scope=STOP_SCOPE, by="owner", why="") -> dict:
    """Ask whatever is running under `scope` to stop. Pressing twice is one stop."""
    now = _now()
    with _conn() as c:
        _migrate(c)
        c.execute("""INSERT INTO stop_requests (scope, asked_at, asked_by, why, cleared_at)
                     VALUES (?,?,?,?,NULL)
                     ON CONFLICT(scope) DO UPDATE SET
                       asked_at=excluded.asked_at, asked_by=excluded.asked_by,
                       why=excluded.why, cleared_at=NULL""",
                  (scope, now, (by or "owner")[:40], (why or "")[:300]))
    return {"scope": scope, "asked_at": now, "asked_by": by, "why": why}


def stop_requested(scope=STOP_SCOPE):
    """The live stop, or None. A cleared row is history, not an instruction."""
    with _conn() as c:
        _migrate(c)
        try:
            r = c.execute("SELECT scope, asked_at, asked_by, why FROM stop_requests"
                          " WHERE scope=? AND cleared_at IS NULL", (scope,)).fetchone()
        except sqlite3.OperationalError:
            return None
        return dict(r) if r else None


def clear_stop(scope=STOP_SCOPE) -> None:
    """
    The stop has been carried out. Clear it.

    Called when a run actually stops, and again at the start of every new run:
    a flag left standing by a program that was closed mid-stop would otherwise
    kill the next day the moment it began, and he would press Run four times
    and watch nothing happen four times.
    """
    with _conn() as c:
        _migrate(c)
        try:
            c.execute("UPDATE stop_requests SET cleared_at=?"
                      " WHERE scope=? AND cleared_at IS NULL", (_now(), scope))
        except sqlite3.OperationalError:
            pass


def _now():
    return int(time.time())


# How long before an unanswered question may be nudged again. Four hours: long
# enough that a pipeline on a short timer cannot spam a thread, short enough
# that a day's silence is visibly a day's silence.
REASK_COOLDOWN = 4 * 3600


def _ago(secs):
    secs = max(0, int(secs))
    if secs < 3600:
        return f"{secs // 60} minutes"
    if secs < 86400:
        return f"{secs // 3600} hours"
    return f"{secs // 86400} days"


# ----------------------------------------------------------------------------
# TASKS — claim, don't grab
# ----------------------------------------------------------------------------

# ----------------------------------------------------------------------------
# SECTOR SWITCHES — the owner's off button
# ----------------------------------------------------------------------------
#
# His agency, his call. He may not want Leads sending cold outreach this month,
# or Content publishing while he rewrites the service pages. The honest way to
# say that is a switch: the sector keeps its agents, its history and its score,
# and simply does no work until he turns it back on.
#
# A MISSING ROW MEANS ON. This is the important rule and it is deliberate — a
# cabinet built before this table existed, or a sector added in a later version,
# must default to working. If absence meant "off", one upgrade would silently
# stop the entire agency and nothing on any screen would explain why.
#
# Enforced in three places, because one is not enough:
#   post_task    the MD cannot pile work onto a sector that will not do it
#   claim_task   nothing already on the board gets picked up by a stopped sector
#   day_run      the sector's step is skipped, and the run panel says so
# The first two matter most: without them a switched-off sector accumulates a
# backlog it is forbidden to clear, and then gets marked down for the backlog.

# SECTORS THAT CANNOT BE SWITCHED OFF.
#
# These four are not services the agency offers, they are how it runs at all:
#
#   Director  plans the day and posts the work. Off, the board never fills and
#             every other sector correctly refuses to invent work for itself.
#   IT        holds the only door to production and the only agents that can
#             change code — including the fix for whatever broke.
#   Report    scores what happened and marks the MD's decisions. Off, the agency
#             stops learning and every score freezes where it stands.
#   Security  screens everything arriving from outside before another agent acts
#             on it. Off is not "less security", it is scraped text and unchecked
#             leads reaching agents that trust them.
#
# Content, SEO and Leads are the ones with an honest off switch: they are work
# the owner may not want done this month, and stopping them costs him nothing
# but that work. Enforced here rather than by hiding the button, because a rule
# that only exists in the interface is not a rule.
ESSENTIAL_SECTORS = {"director", "it", "report", "security"}

WHY_ESSENTIAL = {
    "director": "the MD plans the day and posts the work — with it off the board "
                "never fills and every other sector has nothing to claim",
    "it": "IT holds the only door to production and the only agents that can "
          "change code, including the fix for whatever breaks next",
    "report": "Report scores the day and marks the MD's decisions worked or "
              "failed — with it off the agency stops learning",
    "security": "Security screens everything coming in from outside before "
                "another agent acts on it; off is not less security, it is "
                "scraped pages and unchecked leads reaching agents that trust them",
}


def sector_essential(sector) -> bool:
    return str(sector or "").strip().lower() in ESSENTIAL_SECTORS


def sector_enabled(sector) -> bool:
    """
    Is this sector allowed to work? Unknown or unset means yes.

    An essential sector is always on, whatever a row says. Belt and braces: if a
    stale 'off' row for IT ever reached the table — an older build, a hand-edited
    cabinet, a restore from backup — it would silently disable the only sector
    that can fix the problem, and nothing would be able to turn it back on.
    """
    if not sector:
        return True
    if sector_essential(sector):
        return True
    with _conn() as c:
        _migrate(c)
        try:
            r = c.execute("SELECT enabled FROM sector_switches WHERE lower(sector)=?",
                          (str(sector).strip().lower(),)).fetchone()
        except sqlite3.OperationalError:
            return True
        return True if r is None else bool(r["enabled"])


def sector_switches() -> dict:
    """Every switch the owner has actually set. Sectors he has not touched are absent."""
    with _conn() as c:
        _migrate(c)
        try:
            rows = c.execute("SELECT sector, enabled, note, changed_at"
                             " FROM sector_switches").fetchall()
        except sqlite3.OperationalError:
            return {}
        return {r["sector"]: {"enabled": bool(r["enabled"]), "note": r["note"] or "",
                              "changed_at": r["changed_at"]} for r in rows}


class SectorEssential(Exception):
    """Raised when something tries to switch off a sector the agency runs on."""


def set_sector(sector, enabled, note=""):
    """Turn a sector on or off. Returns the new state."""
    sector = str(sector or "").strip()
    if not sector:
        raise ValueError("which sector?")
    if not enabled and sector_essential(sector):
        raise SectorEssential(
            f"{sector} cannot be switched off — " +
            WHY_ESSENTIAL.get(sector.lower(), "the agency runs on it") + ".")
    with _conn() as c:
        _migrate(c)
        c.execute("""INSERT INTO sector_switches (sector, enabled, note, changed_at)
                     VALUES (?,?,?,?)
                     ON CONFLICT(sector) DO UPDATE SET
                       enabled=excluded.enabled, note=excluded.note,
                       changed_at=excluded.changed_at""",
                  (sector, 1 if enabled else 0, (note or "")[:300], _now()))
    return {"sector": sector, "enabled": bool(enabled), "note": note}


# How many unclaimed jobs a sector may be holding before it stops being given
# more. Eight is roughly a day's work for a sector that is actually claiming;
# for one that is not, it is the point at which adding more is plainly futile.
BOARD_FULL = 8


# ----------------------------------------------------------------------------
# THE DAY GATE — nothing starts until the owner starts the day
# ----------------------------------------------------------------------------
#
# "until i did not start the day they should not start the work if they had
#  some task left behind those agents will complete their previous recorded
#  task first"
#
# Two rules in one sentence, and they pull in opposite directions, which is
# what makes this worth writing down rather than guessing at:
#
#   NEW work waits for him. A desk does not go looking for something to do
#   because the clock says so. He opens the agency; then it works.
#
#   HELD work does not wait for anybody. A task this desk claimed and did not
#   finish is already its own, already recorded, and finishing it is not
#   "starting work" — it is stopping being half-done. Gating that would leave
#   a claim aging on the board until the Overseer filed it as abandoned, which
#   is the ninety-hour deadlock all over again.
#
# The gate lives in coordination/dispatch.py, at the one funnel every desk goes
# through to pick something up. This function is only the question.
#
# ENV OVERRIDE. He runs pipelines from PowerShell by hand — that IS him
# starting the work, and refusing him at his own terminal would be the program
# arguing with its owner. WORK_OUTSIDE_THE_DAY=1 says so out loud, and every
# refusal below names it.

def day_is_open(now=None) -> dict:
    """
    Is the agency open for business right now, and is the gate even in force?

    Returns {open, enforced, why, run_id}.

    `enforced` is False for a cabinet that has never run a day at all. That is
    not a loophole and it is not a convenience for the tests — it is the honest
    reading of the rule. "Do not start before I start the day" is a rule about
    an agency in service. A cabinet with no day in its history has never been
    in service, has nothing scheduled against it, and the only thing that ever
    reaches it is somebody typing a command, which is a person starting work.
    The moment he runs his first day, the gate is live and stays live.
    """
    now = int(now if now is not None else _now())
    try:
        with _conn() as c:
            _migrate(c)
            ever = c.execute("SELECT count(*) FROM day_runs").fetchone()[0]
            row = c.execute(
                "SELECT id, pid, host, started_at FROM day_runs"
                " WHERE status='running' ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
    except Exception:
        # A cabinet that cannot be read is not a reason to stop work; it is a
        # different problem, and one the caller will hit anyway a line later.
        return {"open": True, "enforced": False, "run_id": None,
                "why": "the cabinet could not be read, so the gate stood aside"}

    if str(os.environ.get("WORK_OUTSIDE_THE_DAY", "")).strip().lower() in (
            "1", "true", "yes", "on"):
        return {"open": True, "enforced": False, "run_id": None,
                "why": "WORK_OUTSIDE_THE_DAY is set, so the gate is standing open"}

    if not ever:
        return {"open": False, "enforced": False, "run_id": None,
                "why": ("this cabinet has never run a day, so there is no "
                        "rhythm to be out of step with yet")}

    if row is None:
        return {"open": False, "enforced": True, "run_id": None,
                "why": "no day is running"}

    # A row saying 'running' left behind by a program he closed is not an open
    # day. This is the same liveness test sweep_dead_processes uses, asked here
    # so the gate is right even before the Overseer's next pass.
    if not process_alive(row["pid"], row["host"] or ""):
        return {"open": False, "enforced": True, "run_id": row["id"],
                "why": ("the last day run belongs to a program that is no "
                        "longer running")}

    return {"open": True, "enforced": True, "run_id": row["id"],
            "why": f"the day started {_ago(now - int(row['started_at'] or now))} ago"}


class DeskBusy(Exception):
    """Raised when the desk this job is for is mid-run with work already queued."""


class BoardFull(Exception):
    """Raised when a sector's board is backed up and more work would be futile."""


def _waiting_for_desk(desk) -> int:
    """Open tasks already earmarked for this desk."""
    try:
        with _conn() as c:
            r = c.execute("SELECT count(*) FROM tasks WHERE status='open'"
                          " AND lower(COALESCE(desk,''))=?",
                          ((desk or "").strip().lower(),)).fetchone()
        return int(r[0] or 0)
    except Exception:
        return 0


def open_task_count(sector) -> int:
    """Jobs on this sector's board that nobody has picked up."""
    with _conn() as c:
        _migrate(c)
        try:
            return c.execute(
                "SELECT count(*) FROM tasks WHERE lower(sector)=? AND status='open'",
                (str(sector or "").strip().lower(),)).fetchone()[0]
        except sqlite3.OperationalError:
            return 0


class SectorOff(Exception):
    """Raised when work is aimed at a sector the owner has switched off."""


def post_task(sector, title, detail, risk="green", created_by="MD", force=False,
              about="", desk=None):
    """
    MD (or a lead) posts work. Starts life as 'open' and unowned.

    Refuses a sector the owner has switched off. Posting anyway would leave the
    board filling with work that is forbidden to be done — and the sector would
    then be scored down for the backlog it is not allowed to clear. `force` is
    for the Overseer, whose fault tickets are ABOUT a sector rather than work
    for it, and which must still be filed when a sector is stopped.

    TWO FIELDS THAT DECIDE HOW THE TASK IS LATER HANDED OUT

    `about` names the row this task exists because of — 'site_issues:7',
    'leads:12', 'requests:88'. coordination/dispatch.py reads that row to work
    out how urgent the task really is, instead of taking the poster's word for
    it or, as before, ignoring the question entirely and going by arrival time.

    `desk` names the desk it is for. Pass one to decide it here; pass None and
    the roster in coordination/desks.py decides, which is better than leaving
    it blank — the choice is made once at posting time rather than re-derived
    by every idle desk that walks past. Pass "" to deliberately leave it open
    to the whole sector.
    """
    if not force and not sector_enabled(sector):
        raise SectorOff(
            f"{sector} is switched off, so it was not given: {title!r}. "
            "Turn the sector back on to let work reach it.")

    # A BOARD THAT IS ALREADY BACKED UP DOES NOT GET MORE WORK.
    #
    # He opened the control room and found 24 tasks on the Director's board, 9
    # on IT's, 4 on Content's, 3 on SEO's — none of them claimed, some five
    # days old — and every sector scored down for the backlog.
    #
    # The cause is not the board. Eleven of the seventeen pipelines that run in
    # a day never call next_open_task at all: they do their work and go home
    # without ever looking at what they were asked for. So the MD posted work
    # every morning onto boards where most desks never look, the pile grew by a
    # few every day, the Overseer filed 'nobody is picking this up', and the MD
    # was told and posted more.
    #
    # Making eleven pipelines claim is the real repair and it is not one
    # change. This is the thing that stops the pile growing while that happens,
    # and it is the handbook's own rule turned on the MD: no decision without
    # evidence. Posting a twenty-fifth task onto a board holding twenty-four
    # nobody has touched is a decision with the evidence against it.
    #
    # force still goes through — the Overseer's fault tickets are ABOUT a
    # sector rather than work for it, and must be filed regardless.
    if not force:
        waiting = open_task_count(sector)
        if waiting >= BOARD_FULL:
            raise BoardFull(
                f"{sector} already has {waiting} job(s) on its board that "
                f"nobody has picked up, so {title!r} was not added. Clearing "
                "the backlog comes before adding to it.")
    if desk is None:
        try:
            from . import desks as _desks
            desk, _ = _desks.best_desk({"title": title, "detail": detail},
                                       sector=sector)
        except Exception:
            desk = None

    # A DESK THAT IS MID-RUN IS NOT ASKED FOR SOMETHING ELSE.
    #
    # "if an agent is working let it work do not put or ask after each secod
    #  the same question to put more load on that agent let it work"
    #
    # He is describing the shape rather than one bug: the day posts, the
    # Overseer notices nothing has been picked up, the MD posts again, and the
    # one desk that could do any of it is out doing the first one. Nothing gets
    # done faster — a desk works one job at a time — and every extra row is
    # something the Overseer then files a complaint about.
    #
    # ONLY when it already has work waiting. A busy desk with an empty queue
    # still gets the job: it will pick it up the moment it finishes, and
    # holding it back would just mean the desk goes idle for a day. This is
    # about the SECOND thing in the queue, not the first.
    if not force and desk:
        try:
            if desk_at_work(desk) and _waiting_for_desk(desk) >= 1:
                raise DeskBusy(
                    f"{desk} is mid-run and already has work waiting, so "
                    f"{title!r} was not added. It works one job at a time; a "
                    "second one in the queue only makes the board look busier.")
        except DeskBusy:
            raise
        except Exception:
            pass                      # never let the courtesy check block work
    tid = uuid.uuid4().hex[:12]
    with _conn() as c:
        _migrate(c)
        # ASK ONCE. The request board learned this after the owner found
        # eighteen copies of one question on it; the task board had the same
        # hole and four duplicate pairs sitting in it — two identical Overseer
        # fault tickets, two identical listing jobs. A task nobody has picked
        # up yet is not made more likely to be done by posting it again; it
        # just pushes something else down the board.
        #
        # Only OPEN tasks count. Once one has been claimed and finished, the
        # same title tomorrow is tomorrow's work, not a duplicate.
        dup = c.execute(
            "SELECT id FROM tasks WHERE sector=? AND lower(trim(title))=?"
            " AND status='open' LIMIT 1",
            (sector, (title or "").strip().lower())).fetchone()
        if dup:
            return dup["id"]
        c.execute(
            "INSERT INTO tasks(id,sector,title,detail,risk,status,owner,"
            "created_by,created_at,about,desk)"
            " VALUES(?,?,?,?,?, 'open', NULL, ?,?,?,?)",
            (tid, sector, title, detail, risk, created_by, _now(),
             (about or "").strip(), (desk or "").strip().lower()),
        )
    return tid


def try_post_task(sector, title, detail, risk="green", created_by="MD",
                  about="", desk=None):
    """
    post_task, but a switched-off sector returns None instead of raising.

    For a pipeline posting a follow-up to its OWN sector as a side effect of
    finishing: the owner switching that sector off mid-run is an instruction,
    not an error, and it must not take the whole pass down with it. A board
    that is already backed up is the same shape of answer — no, and not a
    crash.
    """
    try:
        return post_task(sector, title, detail, risk=risk, created_by=created_by,
                         about=about, desk=desk)
    except (SectorOff, BoardFull, DeskBusy):
        return None


def claim_task(task_id, agent):
    """
    Atomically claim an open task. Returns True only if THIS agent got it.

    The UPDATE ... WHERE status='open' is the whole trick: SQLite serialises it,
    so if two agents race, exactly one UPDATE affects a row. The loser gets False
    and moves on. No task is ever worked by two agents.

    A task belonging to a switched-off sector cannot be claimed at all. The
    check is in the same WHERE clause as the race, not before it, so a sector
    switched off mid-claim loses the race rather than half-winning it.

    NOR CAN AN AMBER OR RED ONE, AND THAT WAS MISSING.

    Amber means "this needs the owner's yes before the desk can start" — it is
    what the floor draws amber for and what 'Awaiting you' lists. Nothing
    enforced it. Any agent could claim an unapproved amber task and get on with
    it, which on the Leads board means the one task titled "Mail the 9 cleared
    firms" could have been picked up and acted on without him ever saying yes.
    An approval that only changes a colour is not an approval.

    Approving is what clears it: decide_approval drops the risk to 'green',
    which is the moment the desk may take it. Refusing sets it 'blocked', so it
    is not open either. `force_claim` exists for the one caller that legitimately
    needs to bypass this — the owner acting through the office.
    """
    return _claim(task_id, agent, allow_unapproved=False)


def force_claim(task_id, agent):
    """Claim regardless of the approval gate. For the owner, not for agents."""
    return _claim(task_id, agent, allow_unapproved=True)


def _claim(task_id, agent, allow_unapproved):
    gate = "" if allow_unapproved else \
        " AND COALESCE(lower(risk),'green') NOT IN ('amber','red')"
    with _conn() as c:
        _migrate(c)
        c.execute("BEGIN IMMEDIATE;")
        cur = c.execute(
            "UPDATE tasks SET status='in_progress', owner=?, claimed_at=?"
            " WHERE id=? AND status='open'"
            "   AND lower(sector) NOT IN ("
            "     SELECT lower(sector) FROM sector_switches WHERE enabled=0)"
            + gate,
            (agent, _now(), task_id),
        )
        c.execute("COMMIT;")
        return cur.rowcount == 1


def next_open_task(sector, agent, skip=()):
    """
    Claim the task this desk should be doing next, and return it. None if there
    is nothing on the board it can take.

    THIS USED TO BE `ORDER BY created_at`, AND THAT WAS THE BUG.

    Arrival order, handed to whoever asked first. Two consequences the owner
    caught:

      an urgent task queued behind a trivial one, for as long as both existed;
      and any desk could claim any task in its sector, so it-watcher — which
      files tickets and fixes nothing — could take "Fix the contact form",
      own it, and leave it half-done where the Fixer could not see it.

    Both are fixed one level up: coordination/dispatch.py orders the board by
    what each task is worth (read off the row it is about) plus how long it has
    waited, and coordination/desks.py says whether this desk may take it. The
    behaviour here is unchanged in every other respect — one atomic claim, the
    task returned, None when there is nothing.

    The import is deliberately inside the function: dispatch imports board, and
    at module level that is a cycle.
    """
    try:
        from . import dispatch
    except Exception:
        dispatch = None
    if dispatch is not None:
        return dispatch.next_task(sector, agent, skip=skip)
    # Fallback, if dispatch cannot be imported for any reason: the old
    # behaviour, which is worse but is not nothing.
    with _conn() as c:                                       # pragma: no cover
        rows = c.execute(
            "SELECT id FROM tasks WHERE sector=? AND status='open' ORDER BY created_at",
            (sector,),
        ).fetchall()
    for r in rows:                                           # pragma: no cover
        if claim_task(r["id"], agent):
            return get_task(r["id"])
    return None                                              # pragma: no cover


def finish_task(task_id, agent, result, status="done"):
    """
    Only the owner may finish its own task.

    AND THE ASKER IS TOLD. A task filed from a request carries
    about='requests:<id>', and inbox.answer_when_done exists to close that
    thread — but nothing called it, so every request a sector filed as work
    stayed 'claimed' for ever, however completely the work was done.

    That was not cosmetic. state.py scores a sector on

        SELECT count(*) FROM requests WHERE to_sector=? AND status IN ('open','claimed')

    so each ask became a permanent -5 no amount of work could clear, the
    Overseer chased something that could never become true, and one ask was
    charged twice — once as an unanswered thread and again as a board item.
    Sectors sat at 2/100 while running cleanly.

    Best-effort on purpose: telling the asker is a courtesy owed after the work,
    and a failure here must never make a finished task look unfinished.
    """
    with _conn() as c:
        cur = c.execute(
            "UPDATE tasks SET status=?, result=?, finished_at=?"
            " WHERE id=? AND owner=?",
            (status, json.dumps(result), _now(), task_id, agent),
        )
        if cur.rowcount != 1:
            raise PermissionError(f"{agent} does not own task {task_id}")

    if status == "done":
        try:
            from . import inbox                    # local: inbox imports board
            inbox.answer_when_done(
                task_id, agent,
                f"Done — {agent} finished the work this was filed as. "
                f"Closing the thread.")
        except Exception:
            pass


def withdraw_task(task_id, by, reason):
    """
    Take back a task you posted, because the reason for it has gone.

    ONLY WHILE IT IS STILL OPEN. Once an agent has claimed it, the work belongs
    to that agent and nobody — not the poster, not the Overseer — may reach in
    and close it; that is the whole point of claiming. A claimed task gets a
    note appended instead and its owner decides.

    Returns 'withdrawn', 'noted' (claimed, so left alone) or 'gone'.
    """
    with _conn() as c:
        c.execute("BEGIN IMMEDIATE;")
        row = c.execute("SELECT status, result FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            c.execute("COMMIT;")
            return "gone"
        if row["status"] == "open":
            c.execute("UPDATE tasks SET status='done', result=?, finished_at=?"
                      " WHERE id=? AND status='open'",
                      (json.dumps({"withdrawn_by": by, "reason": reason}), _now(), task_id))
            c.execute("COMMIT;")
            return "withdrawn"
        # Claimed or already finished: append, never overwrite.
        prev = row["result"]
        try:
            prev = json.loads(prev) if prev else {}
        except Exception:
            prev = {"note": str(prev)}
        if not isinstance(prev, dict):
            prev = {"note": prev}
        prev.setdefault("notes", []).append({"by": by, "at": _now(), "note": reason})
        c.execute("UPDATE tasks SET result=? WHERE id=?", (json.dumps(prev), task_id))
        c.execute("COMMIT;")
        return "noted"


# How long a claim with nothing running behind it stays a claim. Kept the same
# as faults.ABANDONED_SECS on purpose: the Overseer must not be able to call a
# claim dead while this function still calls it alive, or the ticket and the fix
# disagree and the owner is told to do something that then gets refused.
DEAD_CLAIM_SECS = 3600


def release_claim(task_id, by, reason="", force=False):
    """
    Put a dead claim back on the board. Returns (ok, why).

    THE GAP THIS FILLS

    Claiming is exclusive, and that is the whole point — while a claim is held
    nobody else may touch the work. The consequence nobody had handled: if the
    holder never comes back, the claim is held FOR EVER. There was no way to
    let go. withdraw_task refuses a claimed task by design, finish_task only
    accepts the owner, and if that owner is a desk with no pipeline behind it
    any more, the task is gone — not failed, not open, just permanently held by
    a ghost. Two of them were sitting on the owner's live board, one for a
    week, both drawn as a red desk he could do nothing about.

    The Overseer has been filing these as 'abandoned' faults since it was
    written, with the advice "either finish it or release the claim". There was
    no release. It was telling him to do something the system could not do.

    WHAT THIS DOES NOT DO

    It does not make claims soft. A LIVE claim is still untouchable, and that
    is enforced here rather than trusted:

      the owner giving it back      always allowed. It is theirs to hand over.
      a dead claim                  held longer than DEAD_CLAIM_SECS with no
                                    run recording work against it since. Not
                                    "the agent looks quiet" — no row.
      anything else                 refused, with the reason.

    `force` is the human override, for the owner alone, and it is recorded as
    an override rather than passed off as an ordinary release.

    The task returns to 'open' with its history intact — append, never
    overwrite — so the next desk that can do the work picks it up in the normal
    way, through dispatch, and the reason it came back is on the record.
    """
    with _conn() as c:
        _migrate(c)
        c.execute("BEGIN IMMEDIATE;")
        row = c.execute("SELECT status, owner, claimed_at, result, title"
                        " FROM tasks WHERE id=?", (task_id,)).fetchone()
        if not row:
            c.execute("COMMIT;")
            return False, "there is no such task"
        if row["status"] != "in_progress":
            c.execute("COMMIT;")
            return False, f"that task is '{row['status']}', not claimed by anybody"

        owner = row["owner"] or ""
        held = _now() - int(row["claimed_at"] or _now())
        giving_back = (by or "").strip().lower() == owner.strip().lower()

        if not giving_back and not force:
            if held < DEAD_CLAIM_SECS:
                c.execute("COMMIT;")
                return False, (
                    f"{owner} claimed this {held // 60} minutes ago and a claim "
                    "is exclusive while it is live. Wait, or have the owner "
                    "force it.")
            # Held long enough. Now the actual test: is anything running?
            # WORK ON *THIS TASK*, NOT ANY WORK AT ALL. This asked "has this
            # agent recorded a run since it claimed", and that is a different
            # question with the same shape — which made the claim permanently
            # unreleasable and put a flat contradiction on his screen:
            #
            #   the card    "holding '...' since 90h ago with nothing running"
            #   the button  "seo-strategist has been recording work since it
            #                claimed this — it is slow, not dead."
            #
            # Both were reading the truth. seo-strategist runs EVERY DAY — the
            # keyword pass, the gap pass — so ninety hours after claiming one
            # task it had four clean runs behind it, none of them about that
            # task. The clause matched, the release was refused, and the only
            # desk that could do the work was locked out of it for ever by its
            # own unrelated diligence.
            #
            # Two things genuinely mean "leave it alone", and neither of them
            # is "this agent has been busy elsewhere":
            #   a run stamped with THIS task id — it really is working it
            #   a run in flight that names no task — it MIGHT be this, and
            #     killing a live claim is worse than waiting one more pass
            #
            # A run stamped with a DIFFERENT task is not this one, live or not.
            # Without that last word the same false positive comes back one
            # size smaller: seo-strategist mid-run on the gap pass would hold
            # an unrelated claim open exactly as its finished runs used to.
            alive = c.execute(
                "SELECT 1 FROM agent_runs"
                " WHERE agent=? AND started_at >= ?"
                "   AND (task_id = ?"
                "        OR (status = 'running' AND task_id IS NULL))"
                "   AND status IN ('running','ok') LIMIT 1",
                (owner, int(row["claimed_at"] or 0), task_id)).fetchone()
            if alive:
                c.execute("COMMIT;")
                return False, (f"{owner} is recording work against this task — it "
                               "is slow, not dead. Leave it alone.")

        prev = row["result"]
        try:
            prev = json.loads(prev) if prev else {}
        except Exception:
            prev = {"note": str(prev)}
        if not isinstance(prev, dict):
            prev = {"note": prev}
        prev.setdefault("notes", []).append({
            "by": by, "at": _now(),
            "note": (f"claim released after {held // 3600}h — "
                     + (reason or ("handed back by its owner" if giving_back
                                   else "nothing was running behind it"))
                     + (" [forced by the owner]" if force and not giving_back else ""))})
        c.execute("UPDATE tasks SET status='open', owner=NULL, claimed_at=NULL,"
                  " result=? WHERE id=? AND status='in_progress'",
                  (json.dumps(prev), task_id))
        c.execute("COMMIT;")
    return True, (f"{owner}'s claim on {row['title'][:60]!r} was released after "
                  f"{held // 3600}h; it is back on the board")


# How long a job may sit on a board with nobody picking it up before the agency
# admits nobody is going to. Three days is long enough to survive a weekend and
# short enough that the board still describes this week.
# How long a run may be marked 'running' and still count as a desk that is
# genuinely at work. Past this it is a corpse, not a busy agent — the longest
# step in the day gets 20 minutes, so 25 clears the slowest honest run with
# room to spare. See board.desk_at_work.
STILL_WORKING = 1500

UNCLAIMED_DAYS = 3


def expire_unclaimed(older_than_days=UNCLAIMED_DAYS) -> list:
    """
    Take work off the board that nobody has picked up, and say so.

    His board had jobs on it five days old. Nobody was going to claim them —
    eleven of the seventeen pipelines never look at the board at all — and
    every day they sat there the sector was scored down for a backlog it was
    never going to clear, the Overseer filed another 'nobody is picking this
    up', and the pile grew.

    A job nobody has claimed in three days is not waiting, it is abandoned.
    Withdrawing it is not losing it: the row stays, with the reason on it, and
    whatever caused it — a site ticket, a request, a gap in the keyword list —
    is still in the cabinet and will raise it again if it still matters. What
    changes is that the board stops lying about what is going to happen.
    """
    cut = _now() - int(older_than_days) * 86400
    out = []
    with _conn() as c:
        _migrate(c)
        # Never the amber and red ones. Those are waiting on HIM, not on a
        # desk, and withdrawing his pending decisions after three days would
        # make them vanish from "Waiting on you" without his ever saying no.
        rows = c.execute(
            "SELECT id, sector, title, created_at FROM tasks"
            "  WHERE status='open' AND created_at < ?"
            "    AND COALESCE(risk,'green') NOT IN ('amber','red')", (cut,)).fetchall()
        for r in rows:
            days = (_now() - int(r["created_at"] or _now())) // 86400
            # finished_at, not ended_at: tasks has no ended_at column. This
            # line named the wrong one from the day it was written, so every
            # call raised, the Overseer's pass swallowed the error, and not one
            # abandoned job was ever withdrawn — the board grew for ever.
            c.execute(
                "UPDATE tasks SET status='withdrawn', result=?, finished_at=?"
                "  WHERE id=? AND status='open'",
                (f"Nobody picked this up in {days} day(s), so it was taken off "
                 "the board. It was not done. If it still matters it will be "
                 "raised again by whatever asked for it.", _now(), r["id"]))
            out.append({"id": r["id"], "sector": r["sector"],
                        "title": r["title"], "days": days})
    return out


def desk_at_work(agent, now=None):
    """
    Is this desk mid-run right now? Returns the run row, or None.

    "if an agent is working let it work do not put or ask after each secod the
     same question to put more load on that agent"

    A live run means a process is out there doing the thing. Piling a second
    job on the same desk in that moment does not get the second job done any
    sooner — the desk works one at a time — it just makes the board look busier
    and gives the Overseer something else to complain about.

    Deliberately only counts a run that is BOTH still marked running and young
    enough to plausibly be running. A row left at 'running' by a killed process
    is not a working desk, and treating it as one would be the opposite bug:
    work held back for ever because of a corpse.
    """
    now = int(now or _now())
    try:
        with _conn() as c:
            r = c.execute(
                "SELECT id, agent, started_at, note FROM agent_runs"
                " WHERE agent=? AND status='running' AND started_at >= ?"
                " ORDER BY started_at DESC LIMIT 1",
                (agent or "", now - STILL_WORKING)).fetchone()
        return dict(r) if r else None
    except Exception:
        return None


def dead_claims(now=None):
    """
    Every claim with nothing behind it, and how long it has been held.

    Read-only, so any screen can ask. This is what turns "a desk is red and I
    cannot do anything about it" into a list with a reason and an action.
    """
    now = int(now or _now())
    out = []
    with _conn() as c:
        _migrate(c)
        try:
            rows = c.execute(
                "SELECT id, sector, title, owner, claimed_at FROM tasks"
                " WHERE status='in_progress' AND claimed_at < ?"
                " ORDER BY claimed_at", (now - DEAD_CLAIM_SECS,)).fetchall()
        except Exception:
            return out
        for r in rows:
            # Same rule as release_claim, and it has to be the same or the two
            # screens disagree: work against THIS task, or a run in flight.
            # "this agent ran yesterday" is not evidence about this claim.
            alive = c.execute(
                "SELECT 1 FROM agent_runs WHERE agent=? AND started_at >= ?"
                "   AND (task_id = ?"
                "        OR (status = 'running' AND task_id IS NULL))"
                "   AND status IN ('running','ok') LIMIT 1",
                (r["owner"] or "", int(r["claimed_at"] or 0), r["id"])).fetchone()
            if alive:
                continue                 # slow, not dead
            out.append({"id": r["id"], "sector": r["sector"], "title": r["title"],
                        "owner": r["owner"], "claimed_at": r["claimed_at"],
                        "held_secs": now - int(r["claimed_at"] or now)})
    return out


def get_task(task_id):
    with _conn() as c:
        r = c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        return dict(r) if r else None


# ----------------------------------------------------------------------------
# AGENT RUNS — proof that an agent actually did something
# ----------------------------------------------------------------------------
#
# Why this exists: a task is claimed and finished inside the same second, so
# "who holds a claimed task right now" is almost always nobody. Read the board
# alone and a sector that filed nine tickets two minutes ago looks exactly like
# one that has never run in its life — both dark. The Overseer needs the same
# trace to spot a stall or a loop, and the cost governor needs somewhere to put
# spend. So every pipeline records a run: when it started, what it was working,
# how it ended, and one plain line about what came of it.

_MIGRATED = set()


_ADDED_COLUMNS = (
    # table          column          type
    ("agent_runs",   "note",         "TEXT"),
    # A decision has to say which rows it is a claim ABOUT, or nothing can ever
    # check it honestly later. Without this, Report matched evidence to a
    # decision by wall-clock proximity, so an unrelated content brief scored an
    # SEO gap list as 'worked' and one brief scored every pending gap list at
    # once. See pipelines/report_daily.py.
    ("decisions",    "evidence",     "TEXT"),
    ("decisions",    "outcome_note", "TEXT"),
    # 'Clear' on the activity panel hides what has been read. Added after
    # read_marks shipped, so cabinets built in between need the column.
    ("read_marks",   "cleared_to",   "INTEGER DEFAULT 0"),
    # A request's place in the queue. See coordination/triage.py — "whoever
    # asked first" left an urgent request behind a trivial one, and a trivial
    # one waiting for ever.
    ("requests",     "blocking",     "INTEGER DEFAULT 0"),
    ("requests",     "urgency",      "TEXT"),
    # What row this request is ABOUT, e.g. 'outreach_log:42', 'site_issues:7'.
    # triage.py reads that row to decide weight, instead of guessing from words
    # in the subject line — the first version matched "lead" inside the agent
    # name "leads-outreach" and ranked a stalled-agent notice as a customer
    # waiting for a reply.
    ("requests",     "about",        "TEXT"),
    # WHEN it was claimed. Without this a request could be claimed and never
    # answered, and nothing could tell the difference between "an agent is
    # working on it right now" and "an agent took it three weeks ago and
    # died". The sector's own inbox only lists status='open', so a stuck claim
    # made the request invisible to the very sector being blamed for ignoring
    # it — the Overseer counted it as unanswered for nineteen days while
    # Content, asked to answer it, correctly reported there was nothing to
    # claim. Tasks were given a way out of this; requests never were.
    ("requests",     "claimed_at",   "INTEGER"),
    # WHICH PROCESS IS DOING THIS WORK.
    #
    # Every reaper in the agency was time-based: a run is dead once it has been
    # 'running' for twenty minutes, a lock is stale once its three-hour lease
    # expires. That is right for an agent stuck on a slow call, and completely
    # wrong for the thing that actually happens — the owner closes the program.
    # The process is gone in an instant and the cabinet says it is still
    # working for hours. He watched the Overseer wait seventeen hours for a
    # reply from a process that no longer existed.
    #
    # A process id turns "how long has this been going" into "is this still
    # running at all", which is a question with a real answer.
    ("agent_runs",   "pid",          "INTEGER"),
    ("agent_runs",   "host",         "TEXT"),
    ("day_runs",     "pid",          "INTEGER"),
    # And the machine that pid is on, for the one case where it is about to be
    # acted on rather than merely read: Stop kills the recorded process if it
    # will not put its tools down. Pid 8814 on his laptop is a different
    # program on any other box, so a stop that cannot place the pid does not
    # get to send a signal to it.
    ("day_runs",     "host",         "TEXT"),
    # What happened after he said yes. There was nowhere to record it, because
    # nothing ever acted on an approval — the row reached 'approved' and stayed
    # there. See pipelines/it_release.py.
    ("site_queue",   "applied_at",   "INTEGER"),
    ("site_queue",   "note",         "TEXT"),
    # THE SAME FIRM, WRITTEN THREE WAYS, COLLECTED THREE TIMES.
    #
    # "the main thing from leads is the one lead that had already scrapped that
    #  will not get scrapped again."
    #
    # lead_seen compared the raw columns. That catches a directory that spells
    # everything identically and nothing else. In practice the same business
    # comes back as:
    #
    #   +971 4 123 4567   /   04-123 4567   /   00971 4 1234567
    #   Al Quoz Fitout LLC  /  AL QUOZ FIT OUT L.L.C.  /  Al Quoz Fitout
    #   and often with no email at all on one of the two listings
    #
    # Every one of those was a fresh row, and a fresh row is a second email to
    # somebody who already had one — which is the thing that burns the
    # relationship and the sending domain together.
    #
    # So the comparison is on NORMALISED keys, written beside the raw values
    # rather than instead of them: the last nine digits of the phone, the
    # registered domain of the website, and the company name with its legal
    # suffixes and punctuation stripped. Existing rows are backfilled once, so
    # a cabinet that already holds duplicates stops adding to them.
    # A rival the SEO desk has SEEN people search for by name, waiting for him
    # to say whether it is actually a competitor. See pipelines/competitors.py:
    # "who actually bids against you is your call" was right and left the whole
    # step dead until he typed a command, which he never had reason to do.
    # NULL means confirmed — every row that existed before this was one he
    # added by hand.
    # What actually changed on a page, and the snapshot that lets the next
    # sweep work it out. "there is section about website changes but it did not
    # gave the detailed berief about that".
    ("site_changes",   "detail",     "TEXT"),
    ("site_fingerprints", "snapshot", "TEXT"),
    ("competitors",  "status",       "TEXT"),
    ("competitors",  "evidence",     "TEXT"),
    ("competitors",  "first_seen",   "INTEGER"),
    ("leads",        "phone_key",    "TEXT"),
    ("leads",        "domain_key",   "TEXT"),
    ("leads",        "company_key",  "TEXT"),
    ("locks",        "pid",          "INTEGER"),
    # The same two ideas, one table over. Tasks were handed out in arrival
    # order with no notion of what they were about or who they were for, so an
    # urgent one queued behind a trivial one and any desk could claim any task
    # in its sector. See coordination/dispatch.py and coordination/desks.py.
    ("tasks",        "about",        "TEXT"),
    ("tasks",        "desk",         "TEXT"),
    # Which lane of the day ran this step. The day used to be one queue, so
    # there was nothing to record; it now runs independent sectors side by side
    # (see pipelines/day_run.py) and "what was running at the same time as
    # this" is a question the run panel has to be able to answer.
    ("day_steps",    "lane",         "TEXT"),
    # "sometimes i will only run content sector with MD and IT and Security".
    # The sectors picked for one run, without touching the permanent switches.
    ("day_runs",     "only_sectors", "TEXT"),
    # "start from where you left" — the run this one picks up from, and what
    # the clear-up at its start put right. See coordination/leftovers.py.
    ("day_runs",     "resume_of",    "TEXT"),
    ("day_runs",     "tidied",       "TEXT"),
    # One campaign mail for every lead (pipelines/campaign.py): which campaign
    # a queued mail is, and the designed version that goes with the text.
    ("outreach_queue", "campaign_id", "INTEGER"),
    ("outreach_queue", "html",        "TEXT"),
    # A mail that came back (a bounce report in his inbox, or refused outright).
    ("outreach_queue", "bounced_at",   "INTEGER"),
    ("outreach_queue", "bounce_reason", "TEXT"),
    # What the Harvester read off the business's own website — the details Maps
    # does not hold. `whatsapp` is filled ONLY from a click-to-chat button the
    # business published itself; `evidence` records where every detail came
    # from, so a number can be argued with six months later.
    # See pipelines/harvest.py.
    ("leads",        "website",      "TEXT"),
    ("leads",        "whatsapp",     "TEXT"),
    ("leads",        "address",      "TEXT"),
    ("leads",        "contact_kind", "TEXT"),
    ("leads",        "evidence",     "TEXT"),
)

# WHY THIS LIST NO LONGER DECIDES ANYTHING
#
# This used to be THE list of tables a migration would create, hand-copied from
# schema.sql. Two files describing the same tables is two files that drift, and
# they did — three times. `faults`, `read_marks` and `sector_switches` were all
# added to schema.sql and never added here, so on any cabinet built before them
# the tables simply never appeared:
#
#   sector_switches   turning a sector off crashed outright, which is how it
#                     was finally caught — "no such table: sector_switches"
#   read_marks        every write was swallowed by a defensive except, so the
#                     owner's activity count reset on every poll and he
#                     reported the same stuck badge three times
#   faults            the whole Overseer pass died on its first write
#
# The last two are the worse kind of bug: they failed silently and looked like
# something else entirely. _migrate now applies schema.sql itself — every
# statement in it is CREATE ... IF NOT EXISTS, so it is idempotent and cheap —
# and this tuple is only a fallback for the case where schema.sql cannot be read
# at all. Adding a table means editing ONE file now.
_ADDED_TABLES = (
    """CREATE TABLE IF NOT EXISTS proposals (
         id TEXT PRIMARY KEY, sector TEXT NOT NULL, title TEXT NOT NULL,
         detail TEXT, evidence TEXT, needs TEXT, urgency INTEGER DEFAULT 5,
         status TEXT DEFAULT 'open', task_id TEXT, why TEXT,
         created_at INTEGER, decided_at INTEGER)""",
    """CREATE TABLE IF NOT EXISTS task_objections (
         id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
         sector TEXT NOT NULL, reason TEXT NOT NULL, detail TEXT, at INTEGER)""",
    """CREATE TABLE IF NOT EXISTS sector_orders (
         day TEXT NOT NULL, sector TEXT NOT NULL, orders TEXT,
         updated_at INTEGER, PRIMARY KEY (day, sector))""",
    # The remembered preflight. A saved API key is not a working API key, and
    # proving one costs a call — so the sweep runs when asked and the office
    # lights read the answer from here instead of re-testing on every poll.
    """CREATE TABLE IF NOT EXISTS preflight_runs (
         id INTEGER PRIMARY KEY AUTOINCREMENT, at INTEGER, ready INTEGER,
         blocking TEXT, payload TEXT)""",
    # One press of Run. Kept because a day that half-ran and stopped is the
    # thing you most need to see afterwards, and stdout has scrolled away.
    """CREATE TABLE IF NOT EXISTS day_runs (
         id TEXT PRIMARY KEY, started_at INTEGER, ended_at INTEGER,
         status TEXT, started_by TEXT, note TEXT, forced INTEGER DEFAULT 0,
         blocking TEXT, steps_total INTEGER DEFAULT 0, steps_done INTEGER DEFAULT 0)""",
    """CREATE TABLE IF NOT EXISTS day_steps (
         id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, seq INTEGER,
         label TEXT, module TEXT, status TEXT, started_at INTEGER,
         ended_at INTEGER, code INTEGER, summary TEXT)""",
    # Where a link to us could come from, and what happened to that idea. One
    # row per opportunity, never one per link acquired — the difference matters,
    # because a link we asked for and did not get is the thing worth reviewing.
    """CREATE TABLE IF NOT EXISTS backlinks (
         id INTEGER PRIMARY KEY AUTOINCREMENT,
         host TEXT NOT NULL, url TEXT, name TEXT,
         kind TEXT,            -- citation | supplier | association | guest_post | rival_gap
         why TEXT,             -- why this one is worth having, in plain words
         local INTEGER DEFAULT 0,   -- 1 = a UAE source, which is the point
         effort TEXT,          -- free | form | pitch | paid
         status TEXT DEFAULT 'idea',  -- idea | pitched | live | rejected | refused
         note TEXT, found_at INTEGER, decided_at INTEGER,
         UNIQUE(host, kind))""",
    # Third-party API usage. Counts are facts, usd is the owner's own estimated
    # rate — see coordination/usage.py for why the two are kept apart. day and
    # month are stored as text so a quota that resets monthly can be summed
    # without doing timezone arithmetic in SQL.
    """CREATE TABLE IF NOT EXISTS api_usage (
         id INTEGER PRIMARY KEY AUTOINCREMENT,
         at INTEGER, day TEXT, month TEXT,
         provider TEXT NOT NULL, kind TEXT NOT NULL,
         n INTEGER DEFAULT 0, usd REAL DEFAULT 0, note TEXT)""",
    """CREATE INDEX IF NOT EXISTS idx_api_usage_day
         ON api_usage(provider, kind, day)""",
    """CREATE INDEX IF NOT EXISTS idx_api_usage_month
         ON api_usage(provider, kind, month)""",
    # Keyword intelligence built from FREE sources — Search Console,
    # autocomplete, the owner's own enquiries, rival sitemaps. Deliberately has
    # no "volume" column: nothing free can supply one, and a column named volume
    # would be filled with a guess within a week. See pipelines/seo_intel.py.
    """CREATE TABLE IF NOT EXISTS keyword_intel (
         id INTEGER PRIMARY KEY AUTOINCREMENT,
         phrase TEXT NOT NULL UNIQUE, area TEXT, intent TEXT,
         impressions INTEGER DEFAULT 0, clicks INTEGER DEFAULT 0,
         position REAL DEFAULT 0, striking INTEGER DEFAULT 0,
         suggest_breadth INTEGER DEFAULT 0, rival_pages INTEGER DEFAULT 0,
         in_enquiries INTEGER DEFAULT 0, we_have_page INTEGER DEFAULT 0,
         score REAL DEFAULT 0, evidence TEXT, sources TEXT, seen_at INTEGER)""",
    # What has already been put on the owner's phone. The unique key is what
    # stops the same pending approval buzzing him hourly until he answers it —
    # see coordination/notify.py on why that matters more than sending.
    """CREATE TABLE IF NOT EXISTS notifications (
         id INTEGER PRIMARY KEY AUTOINCREMENT,
         key TEXT NOT NULL UNIQUE, urgency TEXT, title TEXT, body TEXT,
         first_seen INTEGER, sent_at INTEGER, route TEXT, detail TEXT)""",
    # What the answer engines and the generative engines need from a page, and
    # whether it is there. One row per page per check — AEO and GEO are audits
    # of our OWN pages, so the fix always belongs to Content or IT, never here.
    """CREATE TABLE IF NOT EXISTS aeo_findings (
         id INTEGER PRIMARY KEY AUTOINCREMENT,
         url TEXT NOT NULL, family TEXT,   -- aeo | geo
         check_id TEXT NOT NULL,
         passed INTEGER DEFAULT 0,
         weight INTEGER DEFAULT 1,
         detail TEXT, fix TEXT, owner_sector TEXT,
         found_at INTEGER,
         UNIQUE(url, check_id))""",
)


SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def _schema_statements():
    """
    Every CREATE in schema.sql, one statement at a time.

    Not executescript(): that issues an implicit COMMIT first, and _migrate is
    called from inside functions that may already have work in flight. Executed
    one by one, a migration can never end someone else's transaction early.

    Only CREATE statements are taken. schema.sql contains nothing else today,
    and this makes sure that stays true — a stray DELETE in the schema file
    should not quietly run against every existing cabinet on the next upgrade.
    """
    try:
        raw = SCHEMA_PATH.read_text(encoding="utf-8")
    except Exception:
        return []
    body = "\n".join(l.split("--", 1)[0] for l in raw.splitlines())
    out = []
    for stmt in body.split(";"):
        s = stmt.strip()
        if s.upper().startswith("CREATE "):
            out.append(s)
    return out


def _migrate(c):
    """
    Bring a cabinet built by an older version up to date. Safe to run repeatedly.

    Applies schema.sql — every statement in it is CREATE ... IF NOT EXISTS —
    then the column additions, which SQL cannot express idempotently and so have
    to be checked by hand. See the note on _ADDED_TABLES for why the schema file
    is the source of truth rather than a list kept in this module.
    """
    key = str(DB)
    if key in _MIGRATED:
        return
    stmts = _schema_statements()
    for ddl in (stmts or _ADDED_TABLES):
        try:
            c.execute(ddl)
        except sqlite3.OperationalError:
            pass
    # Belt and braces: if schema.sql was readable we have already run these, and
    # IF NOT EXISTS makes the second pass free. If it was NOT readable, this is
    # the only thing standing between an old cabinet and a crash.
    if stmts:
        for ddl in _ADDED_TABLES:
            try:
                c.execute(ddl)
            except sqlite3.OperationalError:
                pass
    for table, col, coltype in _ADDED_COLUMNS:
        try:
            cols = {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
            if cols and col not in cols:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
        except sqlite3.OperationalError:
            pass
    try:
        c.commit()
    except Exception:
        pass
    _MIGRATED.add(key)
    # Once per cabinet, and AFTER the flag is set — backfill_lead_keys opens
    # its own connection and would recurse straight back into here otherwise.
    try:
        _backfill_keys(c)
    except Exception:
        pass


def _backfill_keys(c):
    """Write the normalised lead keys onto rows that predate the columns."""
    try:
        rows = c.execute(
            "SELECT id, phone, whatsapp, website, company FROM leads"
            " WHERE company_key IS NULL OR company_key=''").fetchall()
    except sqlite3.OperationalError:
        return
    for r in rows:
        pk, dk, ck = _keys_of({"phone": r["phone"] or r["whatsapp"],
                               "website": r["website"], "company": r["company"]})
        c.execute("UPDATE leads SET phone_key=?, domain_key=?, company_key=?"
                  " WHERE id=?", (pk, dk, ck, r["id"]))
    if rows:
        try:
            c.commit()
        except Exception:
            pass


def ensure_schema(con=None):
    """
    Bring a cabinet up to date. Public because the Report sector opens its own
    connection to score decisions, and a migration that only runs on board's
    own connections is a migration that silently doesn't run.
    """
    if con is not None:
        _MIGRATED.discard(str(DB))
        _migrate(con)
        con.commit()
        return
    with _conn() as c:
        _MIGRATED.discard(str(DB))
        _migrate(c)


class _Run:
    """
    Handle on an open run. The pipeline tells it what it ended up doing.

    Every write here is best-effort. Bookkeeping must never be the thing that
    breaks the work: on a cabinet that has not been built yet the whole table is
    missing, and a pipeline should fail with its own clear message about the
    board rather than a raw sqlite error about a table nobody has heard of.
    A run with id None is a no-op handle — the work still runs, untraced.
    """

    def __init__(self, run_id):
        self.id = run_id
        self.task_id = None
        self._note = None

    def _write(self, sql, args):
        if self.id is None:
            return self
        try:
            with _conn() as c:
                _migrate(c)
                c.execute(sql, args)
        except sqlite3.Error:
            pass
        return self

    def task(self, task_id):
        """Attach the task once it has actually been claimed."""
        self.task_id = task_id
        return self._write("UPDATE agent_runs SET task_id=? WHERE id=?", (task_id, self.id))

    def note(self, text):
        """One sentence, in plain words. This is what the office floor shows."""
        self._note = (text or "")[:200]
        return self._write("UPDATE agent_runs SET note=? WHERE id=?", (self._note, self.id))

    def spent(self, usd, tokens_in=0, tokens_out=0):
        return self._write(
            "UPDATE agent_runs SET usd_cost=usd_cost+?, tokens_in=tokens_in+?,"
            " tokens_out=tokens_out+? WHERE id=?",
            (float(usd), int(tokens_in), int(tokens_out), self.id))


@contextmanager
def agent_run(agent, task_id=None, note=None):
    """
    Wrap a pipeline's work so it leaves a trace:

        with board.agent_run("it-watcher") as run:
            task = board.next_open_task("IT", "it-watcher")
            run.task(task["id"])
            ...
            run.note("filed 3 tickets on glassworks.ae")

    A crash marks the run 'error' and re-raises — a stalled agent must never
    look like a clean finish.
    """
    try:
        with _conn() as c:
            _migrate(c)
            cur = c.execute(
                "INSERT INTO agent_runs(agent,task_id,started_at,status,note,"
                "pid,host) VALUES(?,?,?, 'running', ?,?,?)",
                (agent, task_id, _now(), note, os.getpid(), _this_host()))
            run = _Run(cur.lastrowid)
    except sqlite3.Error:
        # no cabinet yet (python -m coordination.init_db). Let the pipeline run
        # and fail on its own terms; do not hijack the error with a trace problem.
        run = _Run(None)
    try:
        yield run
    except KeyboardInterrupt:
        # Not a fault in the desk. Windows hands Ctrl+C to every program in
        # the window it was pressed in (see console.background_flags), so this
        # is somebody stopping a run, or copying text off the screen — and the
        # floor calling it "crashed" sent IT off to ask the MD about a bug that
        # does not exist.
        _end_run(run.id, "stopped", note=(
            "stopped part-way: Ctrl+C reached it from the window it was started "
            "in. Nothing is wrong with the code; it runs again next time."))
        raise
    except BaseException as e:
        # Record WHAT WENT WRONG, not just that something did. The note at the
        # point of the crash says what the agent was attempting; on its own that
        # reads like a status update and explains nothing. The office floor turns
        # this desk's bulb off and shows this line as the reason, so the reason
        # has to be the actual fault.
        _end_run(run.id, "error", f"{type(e).__name__}: {e}")
        raise
    _end_run(run.id, "ok")


def _end_run(run_id, status, error=None, note=None):
    if run_id is None:
        return
    try:
        with _conn() as c:
            _migrate(c)
            if note:
                c.execute(
                    "UPDATE agent_runs SET status=?, ended_at=?,"
                    " note = CASE WHEN note IS NULL OR note='' THEN ?"
                    "             ELSE note || ' — ' || ? END"
                    " WHERE id=?", (status, _now(), note, note, run_id))
            elif error:
                # keep what it was doing, then say how it broke
                c.execute(
                    "UPDATE agent_runs SET status=?, ended_at=?,"
                    " note = CASE WHEN note IS NULL OR note='' THEN ?"
                    "             ELSE note || ' — FAILED: ' || ? END"
                    " WHERE id=?",
                    (status, _now(), "FAILED: " + error[:200], error[:200], run_id))
            else:
                c.execute("UPDATE agent_runs SET status=?, ended_at=? WHERE id=?",
                          (status, _now(), run_id))
    except sqlite3.Error:
        pass


def recent_runs(since, limit=60):
    """Runs started at or after `since` — newest first. Used by the office floor."""
    with _conn() as c:
        _migrate(c)
        try:
            rows = c.execute(
                "SELECT r.id, r.agent, r.task_id, r.started_at, r.ended_at, r.status,"
                "       r.note, t.title AS task_title"
                " FROM agent_runs r LEFT JOIN tasks t ON t.id = r.task_id"
                " WHERE r.started_at >= ? ORDER BY r.started_at DESC LIMIT ?",
                (int(since), limit)).fetchall()
        except sqlite3.OperationalError:
            return []
        return [dict(r) for r in rows]


# ----------------------------------------------------------------------------
# LOCKS — single holder, everyone else queues (this is what stops overriding)
# ----------------------------------------------------------------------------

def acquire_lock(resource, agent, wait=True, ttl=600):
    """
    Take a lock on a shared resource (e.g. 'live_site', 'publish_pipeline').
    Only one agent holds it at a time. If wait=True, block politely until free.
    Locks expire after ttl seconds so a crashed agent can't hold one forever.
    """
    while True:
        with _conn() as c:
            c.execute("BEGIN IMMEDIATE;")
            row = c.execute("SELECT holder,expires_at FROM locks WHERE resource=?",
                            (resource,)).fetchone()
            free = (row is None) or (row["expires_at"] < _now())
            if free:
                c.execute(
                    "INSERT INTO locks(resource,holder,acquired_at,expires_at,pid)"
                    " VALUES(?,?,?,?,?)"
                    " ON CONFLICT(resource) DO UPDATE SET holder=excluded.holder,"
                    " acquired_at=excluded.acquired_at, expires_at=excluded.expires_at,"
                    " pid=excluded.pid",
                    (resource, agent, _now(), _now() + ttl, os.getpid()),
                )
                c.execute("COMMIT;")
                return True
            c.execute("COMMIT;")
        if not wait:
            return False
        time.sleep(2)  # someone else has it — wait your turn, don't override


def release_lock(resource, agent):
    with _conn() as c:
        c.execute("DELETE FROM locks WHERE resource=? AND holder=?", (resource, agent))


@contextmanager
def lock(resource, agent, ttl=600):
    """Use as: `with lock('live_site', 'it-releaser'): ...`"""
    acquire_lock(resource, agent, wait=True, ttl=ttl)
    try:
        yield
    finally:
        release_lock(resource, agent)


# ----------------------------------------------------------------------------
# REQUEST BOARD — sector-to-sector, append-only, turn-taking
# ----------------------------------------------------------------------------

def post_request(from_sector, to_sector, subject, body,
                 blocking=False, urgency="", about=""):
    """
    One sector asks another for something and waits. Never reaches in directly.

    `blocking`  the asker cannot continue without an answer. The one thing the
                asker genuinely knows better than the receiver, so it is taken
                at face value — but it is one factor in the queue, not the whole
                score, or every sender would set it. See coordination/triage.py.
    `about`     the row this is about: 'outreach_log:42', 'site_issues:7'. Triage
                reads that row rather than guessing from words in the subject.
    `urgency`   the asker's own read. Context, not authority.
    """
    # ADDRESSED TO A SECTOR THAT EXISTS, OR NOT POSTED AT ALL.
    #
    # pipelines/mail.py asked sector "MD" to scan every outreach draft before
    # sending, blocking for two minutes on the answer. The Managing Director's
    # sector is called "Director". Nothing reads "MD", so the request went into
    # an inbox with no owner, the wait always timed out, and every mail was
    # held unsent with "MD did not approve (or did not answer)" — which read
    # like the MD refusing, when the MD had never been asked.
    #
    # A typo in an address is not a small bug here: it is silence that looks
    # like a decision. Normalising known aliases and rejecting the rest costs
    # one function call and makes that whole class of fault impossible.
    from . import inbox as _inbox
    from_sector = _inbox.to_sector(from_sector, strict=False)
    to_sector = _inbox.to_sector(to_sector)

    now = _now()
    with _conn() as c:
        _migrate(c)

        # ASK ONCE. THEN NUDGE.
        #
        # Every pipeline that raises a request runs daily, and none of them
        # checked whether they had already asked. The owner's board had the same
        # question from SEO to Content eighteen times, and to IT eighteen times
        # — one for each run since the day it first went unanswered. Eighteen
        # rows is not eighteen problems, it is one problem and seventeen copies,
        # and it buries everything else in the receiving sector's inbox.
        #
        # An identical open question is therefore not posted again. The existing
        # thread gets a nudge instead, and only after a cooldown — a pipeline
        # running four times an hour must not append four times an hour either.
        # The reader sees one conversation that says "asked again, still
        # waiting", which is the truth, instead of a wall of duplicates.
        dup = c.execute(
            "SELECT id, created_at FROM requests WHERE from_sector=? AND to_sector=?"
            "  AND subject=? AND status IN ('open','claimed')"
            " ORDER BY created_at LIMIT 1",
            (from_sector, to_sector, subject)).fetchone()
        if dup:
            last = c.execute("SELECT MAX(at) FROM request_msgs WHERE request_id=?",
                             (dup["id"],)).fetchone()[0] or dup["created_at"] or 0
            if now - int(last) >= REASK_COOLDOWN:
                n = c.execute("SELECT count(*) FROM request_msgs WHERE request_id=?",
                              (dup["id"],)).fetchone()[0]
                _append_msg(c, dup["id"], from_sector,
                            f"Still waiting — asked again ({n + 1} times now), "
                            f"{_ago(now - int(dup['created_at'] or now))} after the "
                            "first ask. Nothing has changed at this end.")
                if blocking:
                    c.execute("UPDATE requests SET blocking=1 WHERE id=?", (dup["id"],))
            return dup["id"]

        rid = uuid.uuid4().hex[:12]
        c.execute(
            "INSERT INTO requests(id,from_sector,to_sector,subject,status,created_at,"
            "blocking,urgency,about) VALUES(?,?,?,?, 'open', ?,?,?,?)",
            (rid, from_sector, to_sector, subject, now,
             1 if blocking else 0, (urgency or "").strip()[:20], (about or "").strip()[:60]),
        )
        _append_msg(c, rid, from_sector, body)
    return rid


def claim_request(request_id, agent):
    """The receiving sector claims the request so two agents don't both answer."""
    with _conn() as c:
        _migrate(c)
        c.execute("BEGIN IMMEDIATE;")
        cur = c.execute(
            "UPDATE requests SET status='claimed', handler=?, claimed_at=?"
            " WHERE id=? AND status='open'",
            (agent, _now(), request_id),
        )
        c.execute("COMMIT;")
        return cur.rowcount == 1


# How long a claimed-but-unanswered request may sit before the claim is treated
# as dead and the request goes back in the sector's inbox. Generous: a real
# agent answers in seconds, so an hour is already far past "still working".
DEAD_REQUEST_CLAIM_SECS = 3600


def sweep_dead_processes() -> dict:
    """
    Close everything owned by a process that is no longer running.

    THE SEVENTEEN-HOUR WAIT. He closed the program and the Overseer went on
    reporting that seo-answers had been working for hours, that a lock was
    held, that the Director had not replied. All true in the cabinet and none
    of it true in the world: the process had gone the instant he shut the
    window, and nothing recorded that.

    Every reaper here was time-based — twenty minutes for a run, three hours
    for a lock lease. Those thresholds are right for an agent stuck on a slow
    call and useless for a process that no longer exists, and the second case
    is the one that actually happens.

    Called at startup and on every Overseer pass, so the first thing a new run
    does is stop lying about the last one. Time-based reaping stays exactly as
    it was, for the genuinely-stuck-but-alive case this cannot see.
    """
    now = _now()
    runs, locks_freed, days = [], [], []
    with _conn() as c:
        _migrate(c)
        for r in c.execute("SELECT id, agent, pid, host, started_at, note"
                           "  FROM agent_runs WHERE status='running'").fetchall():
            if process_alive(r["pid"], r["host"] or ""):
                continue
            c.execute("UPDATE agent_runs SET status='stopped', ended_at=?,"
                      " note=? WHERE id=? AND status='running'",
                      (now, ((r["note"] or "") +
                             " — the program was closed while this was running")
                       [:400], r["id"]))
            runs.append({"id": r["id"], "agent": r["agent"],
                         "ran_for": now - int(r["started_at"] or now)})
        for l in c.execute("SELECT resource, holder, pid FROM locks").fetchall():
            if process_alive(l["pid"], ""):
                continue
            c.execute("DELETE FROM locks WHERE resource=?", (l["resource"],))
            locks_freed.append({"resource": l["resource"], "was": l["holder"]})
        for d in c.execute("SELECT id, pid, started_at FROM day_runs"
                           " WHERE status='running'").fetchall():
            if process_alive(d["pid"], ""):
                continue
            c.execute("UPDATE day_runs SET status='stopped', ended_at=?"
                      " WHERE id=? AND status='running'", (now, d["id"]))
            c.execute("UPDATE day_steps SET status='stopped', ended_at=?,"
                      " summary=COALESCE(summary,'the program was closed before"
                      " this step finished') WHERE run_id=? AND status='running'",
                      (now, d["id"]))
            days.append(d["id"])
    # A stop that was in flight when the program went away has nothing left to
    # stop. Leaving the flag standing would kill the next run the moment it
    # started, which looks exactly like the Run button not working.
    if days:
        clear_stop(STOP_SCOPE)
    return {"runs_closed": runs, "locks_freed": locks_freed,
            "days_closed": days,
            "head": (f"{len(runs)} run(s), {len(locks_freed)} lock(s) and "
                     f"{len(days)} day(s) belonged to a process that is gone"
                     if (runs or locks_freed or days) else
                     "nothing left over from a closed program")}


def release_dead_request_claims(older_than=DEAD_REQUEST_CLAIM_SECS) -> list:
    """
    Put back requests an agent claimed and then never answered.

    THE NINETEEN-DAY BUG. claim_request moves a request to 'claimed'. The
    sector's own inbox, open_requests_for, lists status='open' only. The
    Overseer's unanswered check counts 'open' AND 'claimed'. So the moment an
    agent claimed a request and died before replying, the request became
    invisible to the sector that owed the answer, and permanently visible to
    the Overseer as that sector ignoring it.

    The owner watched the result for nineteen days: "Content has not answered
    SEO" on his phone every morning, and content_briefs reporting — correctly,
    from what it could see — that there was nothing to claim.

    Only claims with NO reply on the thread are released. A handler that
    answered and left the thread open is finished, not stuck, and yanking that
    back would hand the same work to a second agent.
    """
    now = _now()
    out = []
    with _conn() as c:
        _migrate(c)
        rows = c.execute(
            "SELECT r.id, r.to_sector, r.from_sector, r.handler, r.subject,"
            "       r.claimed_at, r.created_at,"
            "       (SELECT count(*) FROM request_msgs m"
            "          WHERE m.request_id = r.id AND m.author = r.handler) AS replies"
            "  FROM requests r WHERE r.status='claimed'").fetchall()
        for r in rows:
            if r["replies"]:
                continue                       # answered, just not closed
            # An old cabinet has claims made before claimed_at existed. Fall
            # back to created_at rather than skipping them for ever, which
            # would leave exactly the rows this was written to rescue stuck.
            since = int(r["claimed_at"] or r["created_at"] or now)
            if now - since < older_than:
                continue                       # genuinely still working
            c.execute("UPDATE requests SET status='open', handler=NULL,"
                      " claimed_at=NULL WHERE id=? AND status='claimed'",
                      (r["id"],))
            out.append({"id": r["id"], "sector": r["to_sector"],
                        "from": r["from_sector"], "was": r["handler"],
                        "subject": r["subject"], "stuck_for": now - since})
    return out


def reply_request(request_id, agent, body, close=False):
    """Append a reply. The thread grows; nothing is ever overwritten."""
    with _conn() as c:
        _append_msg(c, request_id, agent, body)
        if close:
            c.execute("UPDATE requests SET status='closed' WHERE id=?", (request_id,))


def _append_msg(c, request_id, author, body):
    c.execute(
        "INSERT INTO request_msgs(request_id,author,body,at) VALUES(?,?,?,?)",
        (request_id, author, body, _now()),
    )


def open_requests_for(sector, triaged=True):
    """
    This sector's inbox, worst first.

    It used to be ORDER BY created_at — whoever asked first. That put an urgent
    request behind a trivial one that happened to arrive earlier, which is the
    question the owner asked and the answer he would not have liked. Ordering is
    coordination/triage.py's job now: weight from evidence the receiver can
    check, plus an ageing bonus so nothing at the back starves.

    `triaged=False` returns plain arrival order, for anything that genuinely
    wants the thread in the order it happened rather than in the order it
    matters.
    """
    with _conn() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM requests WHERE to_sector=? AND status='open'"
            " ORDER BY created_at", (sector,))]
    if not triaged or len(rows) < 2:
        return rows
    try:
        from . import triage as _triage
    except Exception:
        return rows
    try:
        order = {}
        q = _triage.queue(sector, limit=len(rows))
        for r in q["next"] + q["waiting"]:
            for rid in r["ids"]:
                order[rid] = r["position"]
        rows.sort(key=lambda r: order.get(r["id"], 10_000))
    except Exception:
        pass          # a broken triage must not stop a sector reading its post
    return rows


# ----------------------------------------------------------------------------
# DECISION LOG — the MD's scorecard (learns over time)
# ----------------------------------------------------------------------------

def log_decision(cycle, reasoning, expectation, confidence, evidence=None):
    """
    Record a call and what it expects to happen.

    `evidence` names the rows this decision is a claim ABOUT — the site issues it
    just filed, the leads it stored, the keywords it added. Report needs that to
    check the expectation against the right rows later. A decision logged without
    it cannot be scored honestly and will be left unscored rather than guessed,
    so pass it whenever the pipeline knows what it produced.
    """
    did = uuid.uuid4().hex[:12]
    with _conn() as c:
        _migrate(c)
        c.execute(
            "INSERT INTO decisions(id,cycle,reasoning,expectation,confidence,outcome,"
            "created_at,evidence) VALUES(?,?,?,?,?, NULL, ?,?)",
            (did, cycle, reasoning, expectation, confidence, _now(),
             json.dumps(evidence) if evidence else None),
        )
    return did


def score_decision(decision_id, outcome, note=None):
    """
    Report sector calls this later: 'worked' / 'failed' / 'partial'.

    The note is the evidence behind the verdict. A score with no stated reason is
    just an opinion, and the MD is supposed to be able to read back WHY a line of
    reasoning kept losing.
    """
    with _conn() as c:
        _migrate(c)
        c.execute("UPDATE decisions SET outcome=?, scored_at=?, outcome_note=? WHERE id=?",
                  (outcome, _now(), note, decision_id))


def recent_hit_rate(limit=30):
    with _conn() as c:
        rows = c.execute(
            "SELECT outcome FROM decisions WHERE outcome IS NOT NULL"
            " ORDER BY scored_at DESC LIMIT ?", (limit,)).fetchall()
    if not rows:
        return None
    wins = sum(1 for r in rows if r["outcome"] == "worked")
    return round(wins / len(rows), 2)


# ----------------------------------------------------------------------------
# LEAD SCANNER — never collect the same lead twice
# ----------------------------------------------------------------------------

def _norm(email):
    return (email or "").strip().lower()


# Legal and trading suffixes that appear on one listing and not the next. They
# carry no identity — "Al Quoz Fitout" and "Al Quoz Fitout L.L.C." are one firm.
_SUFFIXES = (
    "llc", "l l c", "fzc", "fzco", "fze", "fz llc", "dmcc", "pjsc", "psc",
    "wll", "ltd", "limited", "co", "company", "est", "establishment",
    "trading", "general trading", "contracting", "group", "holding",
    "international", "uae", "dubai branch", "branch",
)
# LONGEST FIRST, or a shorter one eats the tail of a longer one: "general
# trading" lost its "trading" to the shorter entry and left "general" behind,
# which no rule then matched. Sorted once, here, rather than trusted to
# whoever adds the next word to the tuple above.
_SUFFIXES_LONGEST_FIRST = tuple(sorted(_SUFFIXES, key=len, reverse=True))


def phone_key(phone) -> str:
    """
    A UAE phone reduced to the only part that identifies it: the national
    significant number, with the country code and the trunk zero taken off.

      +971 4 123 4567   ->  41234567
      04-123 4567       ->  41234567
      00971 4 1234567   ->  41234567
      +971 50 123 4567  ->  501234567
      050 123 4567      ->  501234567

    "Strip the spaces and compare" — what this used to do — matched none of
    those to each other, and "take the last nine digits" gets the landline
    forms wrong because one of them carries a trunk zero the other does not.
    The prefixes have to come off the front, in order.
    """
    digits = re.sub(r"\D", "", str(phone or ""))
    if not digits:
        return ""
    for prefix in ("00971", "971"):
        if digits.startswith(prefix) and len(digits) > len(prefix):
            digits = digits[len(prefix):]
            break
    digits = digits.lstrip("0")
    # Anything left that is still longer than a UAE number is a foreign one
    # written in full; keep its own significant tail rather than mangling it.
    return digits[-10:] if len(digits) > 10 else digits


def domain_key(url) -> str:
    """
    The registered domain of a website, with the www and the path gone.

    A business's own domain is the strongest identity signal there is: two
    listings with different names and no email are the same firm if they point
    at the same site.
    """
    s = str(url or "").strip().lower()
    if not s:
        return ""
    s = re.sub(r"^[a-z][a-z0-9+.\-]*://", "", s)
    s = s.split("/")[0].split("?")[0].split("#")[0]
    s = s.split("@")[-1].split(":")[0]
    if s.startswith("www."):
        s = s[4:]
    return s if "." in s else ""


def company_key(name) -> str:
    """
    A company name with the punctuation, the legal suffixes AND the spaces
    taken off.

    The spaces matter more than they look. "Al Quoz Fitout" and "Al Quoz Fit
    Out" are one firm on two directories, and any comparison that keeps the
    spacing treats them as two. What is left is not meant to be readable — it
    is an identity, and the readable name is in the column next to it.
    """
    s = re.sub(r"[^a-z0-9 ]+", " ", str(name or "").lower())
    s = re.sub(r"\s+", " ", s).strip()
    if not s:
        return ""
    changed = True
    while changed and s:
        changed = False
        for suf in _SUFFIXES_LONGEST_FIRST:
            if s.endswith(" " + suf):
                s = s[: -(len(suf) + 1)].strip()
                changed = True
    return s.replace(" ", "")


def _keys_of(lead: dict) -> tuple:
    return (phone_key(lead.get("phone") or lead.get("whatsapp")),
            domain_key(lead.get("website")),
            company_key(lead.get("company")))


def lead_seen(email=None, phone=None, company=None, website=None):
    """
    True if we already have this lead. The Hunter calls this BEFORE storing
    anything — a lead already in the cabinet is skipped, logged as a duplicate,
    and never re-mailed.

    FOUR KEYS, AND THREE OF THEM ARE NORMALISED. Comparing the raw columns only
    caught a directory that spelled everything exactly as the last one did,
    which is not how directories work. See phone_key / domain_key /
    company_key above for what each of them survives.
    """
    pk, dk, ck = _keys_of({"phone": phone, "website": website, "company": company})
    with _conn() as c:
        _migrate(c)
        if email:
            if c.execute("SELECT 1 FROM leads WHERE lower(email)=?",
                         (_norm(email),)).fetchone():
                return True
        if pk:
            if c.execute("SELECT 1 FROM leads WHERE phone_key=?", (pk,)).fetchone():
                return True
            # An older row written before the key column existed and not yet
            # backfilled. Cheap belt and braces on a table this size.
            if c.execute("SELECT 1 FROM leads WHERE replace(replace(replace("
                         "COALESCE(phone,''),' ',''),'-',''),'+','') LIKE ?",
                         ("%" + pk,)).fetchone():
                return True
        if dk:
            if c.execute("SELECT 1 FROM leads WHERE domain_key=?", (dk,)).fetchone():
                return True
        if ck:
            if c.execute("SELECT 1 FROM leads WHERE company_key=?", (ck,)).fetchone():
                return True
            if c.execute("SELECT 1 FROM leads WHERE lower(trim(company))=?",
                         ((company or "").strip().lower(),)).fetchone():
                return True
    return False


def add_lead_if_new(lead: dict):
    """
    Store a lead only if it's genuinely new. Returns the lead id, or None if it
    was a duplicate (and logs the skip so the Scanner can report 'N dupes skipped').
    """
    if lead_seen(lead.get("email"), lead.get("phone"), lead.get("company"),
                 lead.get("website")):
        return None
    ev = lead.get("evidence")
    if ev is not None and not isinstance(ev, str):
        ev = json.dumps(ev)
    pk, dk, ck = _keys_of(lead)
    with _conn() as c:
        _migrate(c)
        cur = c.execute(
            "INSERT INTO leads(name,company,email,phone,socials,source,area,segment,"
            "found_at,website,whatsapp,address,contact_kind,evidence,"
            "phone_key,domain_key,company_key)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (lead.get("name"), lead.get("company"), _norm(lead.get("email")),
             lead.get("phone"), lead.get("socials"), lead.get("source"),
             lead.get("area"), lead.get("segment"), _now(),
             lead.get("website"), lead.get("whatsapp"), lead.get("address"),
             lead.get("contact_kind"), ev, pk, dk, ck),
        )
        return cur.lastrowid


# ----------------------------------------------------------------------------
# VERIFICATION — one sector asks another to check something, and waits
# ----------------------------------------------------------------------------

def request_verification(from_sector, to_sector, subject, body, blocking=True, timeout=120):
    """
    The clean way sectors verify with each other. Leads asks Security 'is this
    lead real?', Content asks SEO 'is this keyword worth it?'. It posts a request
    and, if blocking, waits for the answer instead of acting on its own guess.

    Returns the answer text once the other sector replies, or None on timeout.
    The asker does NOT proceed past a blocking verification without a reply — that
    is the whole point: no sector overrides another's judgement on its own turf.
    """
    # Normalised HERE as well as inside post_request, because everything below
    # this line reasons about the sector by name: which desks belong to it,
    # whether any of them is at work, and what the thread note calls it. Passed
    # "MD", the grace check below looked for desks in a sector called MD, found
    # none, and fell back to the full two-minute wait — the exact behaviour it
    # was written to remove. Caught by running it, not by reading it.
    from . import inbox as _inbox
    from_sector = _inbox.to_sector(from_sector, strict=False)
    to_sector = _inbox.to_sector(to_sector)

    rid = post_request(from_sector, to_sector, subject, body)
    if not blocking:
        return rid

    # WAITING FOR A ROOM THAT WENT HOME.
    #
    # The day runs in lanes: the Director's lane finishes, then IT / Leads /
    # SEO run side by side, then Security and Content, then close. So when
    # Leads blocks on the Director mid-outreach, the Director's step finished
    # minutes ago. There is nobody in the building to answer, and the polling
    # loop below happily spent the full two minutes finding that out — two
    # minutes per mail, on every mail, before holding each one unsent.
    #
    # A wait is only worth making when somebody could plausibly answer it:
    # a desk in that sector is mid-run now, or one is running concurrently in
    # this day's lane. If not, this returns straight away with the answer
    # already known — nobody is there — and leaves the request open so the
    # sector's reader picks it up on its next pass. The asker gets the same
    # None it would have got in two minutes' time, immediately, and the thread
    # says why rather than looking like a refusal.
    grace = _answerable_grace(to_sector, timeout)
    if grace <= 0:
        reply_request(
            rid, from_sector,
            f"(not waited on) No desk in {to_sector} is running, so a blocking "
            f"wait here would time out without anyone having seen the question. "
            f"{from_sector} has taken the safe path for an unanswered ask and "
            f"carried on. This is still open: {to_sector} answers it on its next "
            f"pass, and the asker retries then.")
        return None

    waited = 0
    while waited < grace:
        with _conn() as c:
            msgs = c.execute(
                "SELECT author,body FROM request_msgs WHERE request_id=? ORDER BY id",
                (rid,)).fetchall()
        # an answer is any message from the other sector after ours
        for m in msgs[1:]:
            if m["author"] != from_sector:
                return m["body"]
        time.sleep(2)
        waited += 2
    reply_request(
        rid, from_sector,
        f"(waited {grace}s) {to_sector} was running but did not answer in time. "
        f"{from_sector} took the safe path for an unanswered ask. Still open.")
    return None


def _answerable_grace(sector, timeout) -> int:
    """
    How long it is worth blocking for an answer from `sector`.

    `timeout` if a desk in that sector is at work right now — it may well be
    mid-pass and about to read its post. 0 if the sector is dark, because
    waiting cannot change that. Any error here returns the full timeout: the
    old behaviour, which is slow but never wrong.
    """
    try:
        from . import desks as _desks, inbox as _inbox
        # Normalised here too, not only in the caller. A helper that silently
        # returns "wait the full two minutes" for a name it does not recognise
        # is exactly the failure this function exists to prevent, and it should
        # not depend on every caller having spelled the sector correctly.
        sector = _inbox.to_sector(sector, strict=False)
        agents = [d for d, spec in _desks.DESKS.items()
                  if (spec.get("sector") or "").lower() == (sector or "").lower()]
        if not agents:
            return int(timeout)
        return int(timeout) if any(desk_at_work(a) for a in agents) else 0
    except Exception:
        return int(timeout)


# ----------------------------------------------------------------------------
# SITE QUEUE — read-only mode: record a change instead of making it
# ----------------------------------------------------------------------------

# How big one queued change may be. A service page is 5-20KB of markup; this
# is generous enough for any real page and small enough that a runaway caller
# cannot fill his cabinet with one row.
MAX_CHANGE_BYTES = 200_000


# ---- site issues: one ticket per problem, however often it is seen ----------
#
# The owner's IT panel listed "The email address on the site is info@... and
# that domain has no DNS record" TWELVE times, and "Home page took 3263ms"
# beside "took 2983ms" beside "took 2170ms". The health check files what it
# finds on every run and nothing asked whether the same problem was already on
# the list, so one broken address became twelve tickets, the Fixer worked each
# one, and his panel read like twelve emergencies.
#
# The same problem is the same ticket while it is still live. Numbers are
# ignored when comparing ("took 3263ms" and "took 2983ms" are one slow page),
# and the live ticket is refreshed with the latest wording, so it always shows
# the newest measurement rather than the first.
LIVE_ISSUE = ("open", "fixing", "in_review")


def _issue_shape(kind, detail) -> str:
    return (str(kind or "").strip().lower() + "|"
            + re.sub(r"\d+(?:[.,]\d+)?", "#", str(detail or "").strip().lower())[:400])


def file_site_issue(kind, severity, detail, url="", found_by="", con=None) -> tuple:
    """(id, is_new): file one site issue unless the same one is already live."""
    now = _now()
    own = con is None
    c = con or sqlite3.connect(DB, timeout=30)
    try:
        shape = _issue_shape(kind, detail)
        for r in c.execute("SELECT id, kind, detail FROM site_issues WHERE lower(kind)=?"
                           " AND status IN (%s)" % ",".join("?" * len(LIVE_ISSUE)),
                           (str(kind or "").strip().lower(), *LIVE_ISSUE)):
            if _issue_shape(r[1], r[2]) == shape:
                c.execute("UPDATE site_issues SET detail=?, severity=? WHERE id=?",
                          (detail, severity, r[0]))
                if own:
                    c.commit()
                return r[0], False
        cur = c.execute(
            "INSERT INTO site_issues(kind,severity,detail,url,status,found_by,found_at)"
            " VALUES(?,?,?,?, 'open', ?,?)",
            (kind, severity, detail, url, found_by, now))
        if own:
            c.commit()
        return cur.lastrowid, True
    finally:
        if own:
            c.close()


def merge_duplicate_issues() -> int:
    """
    Fold the copies already on his list into one ticket each — the oldest,
    which carries the history. The rest are marked 'duplicate', not 'closed':
    a duplicate was never fixed, and counting it as fixed would flatter the
    Report's numbers. Returns how many were folded.
    """
    folded = 0
    with _conn() as c:
        seen = {}
        for r in c.execute("SELECT id, kind, detail FROM site_issues WHERE status IN (%s)"
                           " ORDER BY id" % ",".join("?" * len(LIVE_ISSUE)), LIVE_ISSUE):
            shape = _issue_shape(r["kind"], r["detail"])
            if shape in seen:
                c.execute("UPDATE site_issues SET status='duplicate' WHERE id=?", (r["id"],))
                folded += 1
            else:
                seen[shape] = r["id"]
    return folded


def queue_site_change(agent, kind, detail):
    """
    In read-only mode a site-touching agent records what it WOULD change here,
    for the owner to approve, rather than reaching the site. Returns the row id.

    ASK ONCE. The same daily-pipeline problem as post_request: the agents run
    every day, none of them checked whether the identical change was already
    waiting, and the owner's approval queue filled with ten copies of
    'catalogue_services' — one per run. Ten identical rows is not ten decisions,
    it is one decision he now has to make ten times, and it pushes the change
    that actually matters off the bottom of his list.

    An identical PENDING change is therefore not queued again; its row id comes
    back instead, so the caller still has something to log against. Once he has
    decided it — approved or refused — a later identical change is a genuinely
    new proposal and does queue.
    """
    body = json.dumps(detail)
    if len(body) > MAX_CHANGE_BYTES:
        # REFUSE, DO NOT TRUNCATE. This used to be `json.dumps(detail)[:2000]`,
        # which was fine for as long as every queued change was a sentence and
        # a page name. The moment Content could queue a whole page it became a
        # trap: 2000 characters cuts a page off mid-tag, the cut leaves invalid
        # JSON, plan_of cannot parse it and refuses — so the page would simply
        # never publish and the reason would read as the proposal being
        # malformed rather than as this line having eaten it.
        #
        # An oversized payload is a bug in the caller. Saying so is recoverable;
        # writing three quarters of a page to his website is not.
        raise ValueError(
            f"that change is {len(body)} characters and the queue holds "
            f"{MAX_CHANGE_BYTES}. Nothing was queued. A change this size is "
            "not one decision — split it, or write it to a file and queue the "
            "path.")
    with _conn() as c:
        dup = c.execute(
            "SELECT id FROM site_queue WHERE agent=? AND kind=? AND detail=?"
            "  AND status='pending' ORDER BY queued_at LIMIT 1",
            (agent, kind, body)).fetchone()
        if dup:
            return dup["id"]
        cur = c.execute(
            "INSERT INTO site_queue(agent,kind,detail,status,queued_at)"
            " VALUES(?,?,?, 'pending', ?)",
            (agent, kind, body, _now()),
        )
        return cur.lastrowid


def pending_site_changes():
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM site_queue WHERE status='pending' ORDER BY queued_at"
        ).fetchall()
        return [dict(r) for r in rows]


def decide_site_change(change_id, decision):
    """Owner approves/rejects a queued change. decision: 'approved' | 'rejected'."""
    with _conn() as c:
        c.execute("UPDATE site_queue SET status=?, decided_at=? WHERE id=?",
                  (decision, _now(), change_id))


def approved_site_changes(limit=25):
    """
    What he has said yes to and nobody has acted on yet.

    THE OTHER HALF OF HIS QUESTION. "if it cannot make changes to site then
    what was full access of website and the files uploaded into the website?"
    The answer was that his approval was written to a row that nothing ever
    read: pending → approved → and there it sat, for ever. This is the query
    the Releaser runs, and until it existed the queue was a suggestion box.

    Oldest first, because a decision he made on Monday should not be overtaken
    by one he made this morning.
    """
    with _conn() as c:
        _migrate(c)
        rows = c.execute(
            "SELECT * FROM site_queue WHERE status='approved'"
            " ORDER BY decided_at, id LIMIT ?", (int(limit),)).fetchall()
        return [dict(r) for r in rows]


def mark_site_change(change_id, status, note=""):
    """
    Record what became of an approved change. 'applied' or 'failed'.

    Never silently: a change that could not be written has to end up somewhere
    he can see, or it disappears from the queue and he assumes it went on.
    """
    if status not in ("applied", "failed"):
        raise ValueError(f"not an outcome: {status}")
    with _conn() as c:
        _migrate(c)
        c.execute("UPDATE site_queue SET status=?, applied_at=?, note=?"
                  " WHERE id=?", (status, _now(), (note or "")[:500], change_id))
    return {"id": change_id, "status": status, "note": note}


def note_site_change(change_id, note=""):
    """An approved change that has not gone yet, and why — its approval kept."""
    with _conn() as c:
        _migrate(c)
        c.execute("UPDATE site_queue SET note=? WHERE id=? AND status='approved'",
                  ((note or "")[:500], change_id))


def reopen_site_changes(pattern_words, days=7):
    """
    Put back to 'approved' the changes marked failed only because his site did
    not answer — his yes was never about the site being reachable. Returns ids.
    """
    since = _now() - days * 86400
    out = []
    with _conn() as c:
        _migrate(c)
        for r in c.execute("SELECT id, note FROM site_queue WHERE status='failed'"
                           " AND COALESCE(applied_at, decided_at, queued_at, 0)>=?",
                           (since,)).fetchall():
            note = (r["note"] or "").lower()
            if any(w in note for w in pattern_words):
                c.execute("UPDATE site_queue SET status='approved', applied_at=NULL,"
                          " note=? WHERE id=?",
                          ("waiting — put back after your website did not answer", r["id"]))
                out.append(r["id"])
    return out


# ---------------------------------------------------------------------------
# OUTREACH — drafted mail waiting on the owner
# ---------------------------------------------------------------------------
#
# The agency does not start cold-mailing real businesses on its own say-so. The
# first batch is queued here for him to read; approving it ARMS outreach, and
# from then on drafts that pass Security and the Auditor go straight out inside
# the daily cap. One confirmation, then autonomous — which is the handbook's
# own rule about the owner being the tie-breaker on anything irreversible,
# rather than a permission prompt he has to answer every morning.

def queue_outreach(lead_id, company, to_email, subject, body, service="",
                   audit=None, by="leads-outreach", held=False):
    """Hold one drafted mail for the owner. Returns the row id.

    ASK ONCE: the same draft to the same address, still pending, is not queued
    again. The Hunter runs daily and would otherwise fill his list with copies.

    `held`: the Auditor refused it. It is kept — status 'held', with the
    reasons in `audit` — so the Mail room can show him what was written and
    why it cannot go, rather than the draft vanishing and the room staying
    empty after a run that reported writing seven. A held draft can never be
    sent; only a newer draft for the same lead replaces it.
    """
    if held:
        with _conn() as c:
            _migrate(c)
            c.execute("UPDATE outreach_queue SET status='replaced' WHERE lead_id=?"
                      " AND status='held'", (lead_id,))
            cur = c.execute(
                "INSERT INTO outreach_queue(lead_id,company,to_email,subject,body,"
                "service,audit,status,queued_by,queued_at)"
                " VALUES(?,?,?,?,?,?,?, 'held', ?, ?)",
                (lead_id, company, (to_email or "").strip().lower(), subject, body,
                 service, json.dumps(audit or {}), by, _now()))
            return cur.lastrowid
    with _conn() as c:
        _migrate(c)
        # A fresh draft for this lead replaces any held one.
        c.execute("UPDATE outreach_queue SET status='replaced' WHERE lead_id=?"
                  " AND status='held'", (lead_id,))
        dup = c.execute(
            "SELECT id FROM outreach_queue WHERE status='pending'"
            " AND to_email=? AND subject=? LIMIT 1",
            ((to_email or "").strip().lower(), subject)).fetchone()
        if dup:
            return dup["id"]
        cur = c.execute(
            "INSERT INTO outreach_queue(lead_id,company,to_email,subject,body,"
            "service,audit,status,queued_by,queued_at)"
            " VALUES(?,?,?,?,?,?,?, 'pending', ?, ?)",
            (lead_id, company, (to_email or "").strip().lower(), subject, body,
             service, json.dumps(audit or {}), by, _now()))
        return cur.lastrowid


def pending_outreach(limit=50):
    with _conn() as c:
        _migrate(c)
        return [dict(r) for r in c.execute(
            "SELECT * FROM outreach_queue WHERE status='pending'"
            " ORDER BY queued_at LIMIT ?", (int(limit),))]


def decide_outreach(qid, decision, note=""):
    """Owner's yes or no on one drafted mail. 'approved' | 'rejected'."""
    if decision not in ("approved", "rejected"):
        raise ValueError(f"not a decision: {decision}")
    with _conn() as c:
        _migrate(c)
        c.execute("UPDATE outreach_queue SET status=?, decided_at=?, note=?"
                  " WHERE id=? AND status='pending'",
                  (decision, _now(), note[:300], qid))
        return c.total_changes > 0


def outreach_armed() -> bool:
    """
    Has the owner ever approved a mail?

    This used to switch outreach to sending on its own after his first yes. It
    no longer does — every draft waits for his tick (pipelines/leads_outreach,
    EVERY MAIL IS HIS CALL) — and this is kept only as a fact the screens can
    report.

    Read from the record rather than a config flag on purpose: the thing that
    arms it is him actually approving mail, which is a row, not a setting
    somebody could flip without meaning to.
    """
    with _conn() as c:
        _migrate(c)
        try:
            r = c.execute("SELECT 1 FROM outreach_queue"
                          " WHERE status IN ('approved','sent') LIMIT 1").fetchone()
            return bool(r)
        except Exception:
            return False


def mark_outreach_sent(qid, ok=True, note=""):
    with _conn() as c:
        _migrate(c)
        c.execute("UPDATE outreach_queue SET status=?, sent_at=?, note=?"
                  " WHERE id=?",
                  ("sent" if ok else "rejected", _now(), note[:300], qid))


def mark_outreach_stale(qid, note=""):
    """Approved once, but what it would send is no longer what he approved."""
    with _conn() as c:
        _migrate(c)
        c.execute("UPDATE outreach_queue SET status='stale', decided_at=?, note=?"
                  " WHERE id=? AND status='approved'", (_now(), note[:300], qid))


def decide_approval(kind, ref, decision):
    """
    The owner's yes or no on one amber item, actually written down.

    The Control Room's Approve button used to set an opacity and print
    "approved — sent to the agent". Nothing was sent to anything: the row was
    untouched and the next poll brought the item straight back. On the one
    control the handbook makes the owner the tie-breaker for, that is the worst
    possible failure — he believes he has decided, the agency never hears it,
    and both sides think they are waiting on the other.

    Two kinds, because there are two things that queue for him:
      site_change  a change the agents want to make to the live site
      task         a task the MD marked amber or red

    Approving a task drops it to green and stamps who said so, which is what
    takes it off this list; refusing blocks it. The reason is appended to the
    task rather than replacing anything — append, don't overwrite.
    """
    if decision not in ("approved", "rejected"):
        raise ValueError(f"not a decision: {decision}")
    when = time.strftime("%Y-%m-%d %H:%M", time.localtime(_now()))
    if kind == "site_change":
        decide_site_change(int(ref), decision)
        return {"ok": True, "kind": kind, "ref": ref, "decision": decision}
    if kind == "outreach":
        moved = decide_outreach(int(ref), decision, note=f"owner {decision} {when}")
        return {"ok": bool(moved), "kind": kind, "ref": ref, "decision": decision,
                "error": "" if moved else "that draft has already been decided",
                "why": ("Approved. It goes out through your email on the next "
                        "Leads pass, inside the daily cap — or press Send in the "
                        "Mail room to send it now. Every other draft still "
                        "waits for your own yes."
                        if decision == "approved" and moved else
                        "Turned down. It will not be sent." if moved else "")}
    if kind == "site_resting":
        # "It opens on this computer again" — let the agency read it now.
        if decision == "approved":
            from . import sitepace
            sitepace.clear()
        return {"ok": True, "kind": kind, "ref": ref, "decision": decision,
                "why": ("The agency reads your site again from now, one request at "
                        "a time." if decision == "approved" else
                        "Left alone until the pause runs out.")}
    if kind != "task":
        raise ValueError(f"nothing of kind '{kind}' queues for approval")
    with _conn() as c:
        row = c.execute("SELECT status FROM tasks WHERE id=?", (ref,)).fetchone()
        if not row:
            return {"ok": False, "error": "that task is no longer on the board"}
        note = f"\n\n[owner {decision} this on {when}]"
        if decision == "approved":
            c.execute("UPDATE tasks SET risk='green', detail=COALESCE(detail,'')||?"
                      " WHERE id=?", (note, ref))
        else:
            c.execute("UPDATE tasks SET status='blocked', detail=COALESCE(detail,'')||?"
                      " WHERE id=?", (note, ref))
    return {"ok": True, "kind": kind, "ref": ref, "decision": decision}


if __name__ == "__main__":
    # tiny smoke test
    from init_db import init
    init()
    t = post_task("IT", "Fix contact form", "Daily form test failed at 07:00", risk="amber")
    print("claim A:", claim_task(t, "it-fixer"))   # True
    print("claim B:", claim_task(t, "it-watcher"))  # False — already owned
    with lock("live_site", "it-releaser"):
        print("it-releaser holds live_site lock")
    print("hit rate:", recent_hit_rate())
