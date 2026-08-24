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
    if desk is None:
        try:
            from . import desks as _desks
            desk, _ = _desks.best_desk({"title": title, "detail": detail},
                                       sector=sector)
        except Exception:
            desk = None
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
    not an error, and it must not take the whole pass down with it.
    """
    try:
        return post_task(sector, title, detail, risk=risk, created_by=created_by,
                         about=about, desk=desk)
    except SectorOff:
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


def next_open_task(sector, agent):
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
        return dispatch.next_task(sector, agent)
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
    """Only the owner may finish its own task."""
    with _conn() as c:
        cur = c.execute(
            "UPDATE tasks SET status=?, result=?, finished_at=?"
            " WHERE id=? AND owner=?",
            (status, json.dumps(result), _now(), task_id, agent),
        )
        if cur.rowcount != 1:
            raise PermissionError(f"{agent} does not own task {task_id}")


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
            alive = c.execute(
                "SELECT 1 FROM agent_runs WHERE agent=? AND started_at >= ?"
                " AND status IN ('running','ok') LIMIT 1",
                (owner, int(row["claimed_at"] or 0))).fetchone()
            if alive:
                c.execute("COMMIT;")
                return False, (f"{owner} has been recording work since it claimed "
                               "this — it is slow, not dead. Leave it alone.")

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
            alive = c.execute(
                "SELECT 1 FROM agent_runs WHERE agent=? AND started_at >= ?"
                " AND status IN ('running','ok') LIMIT 1",
                (r["owner"] or "", int(r["claimed_at"] or 0))).fetchone()
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
        raw = SCHEMA_PATH.read_text()
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
                "INSERT INTO agent_runs(agent,task_id,started_at,status,note)"
                " VALUES(?,?,?, 'running', ?)", (agent, task_id, _now(), note))
            run = _Run(cur.lastrowid)
    except sqlite3.Error:
        # no cabinet yet (python -m coordination.init_db). Let the pipeline run
        # and fail on its own terms; do not hijack the error with a trace problem.
        run = _Run(None)
    try:
        yield run
    except BaseException as e:
        # Record WHAT WENT WRONG, not just that something did. The note at the
        # point of the crash says what the agent was attempting; on its own that
        # reads like a status update and explains nothing. The office floor turns
        # this desk's bulb off and shows this line as the reason, so the reason
        # has to be the actual fault.
        _end_run(run.id, "error", f"{type(e).__name__}: {e}")
        raise
    _end_run(run.id, "ok")


def _end_run(run_id, status, error=None):
    if run_id is None:
        return
    try:
        with _conn() as c:
            _migrate(c)
            if error:
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
                    "INSERT INTO locks(resource,holder,acquired_at,expires_at)"
                    " VALUES(?,?,?,?)"
                    " ON CONFLICT(resource) DO UPDATE SET holder=excluded.holder,"
                    " acquired_at=excluded.acquired_at, expires_at=excluded.expires_at",
                    (resource, agent, _now(), _now() + ttl),
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
        c.execute("BEGIN IMMEDIATE;")
        cur = c.execute(
            "UPDATE requests SET status='claimed', handler=? WHERE id=? AND status='open'",
            (agent, request_id),
        )
        c.execute("COMMIT;")
        return cur.rowcount == 1


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


def lead_seen(email=None, phone=None, company=None):
    """
    True if we already have this lead — by email, phone, or company name.
    The Hunter calls this BEFORE storing anything. Emails re-appear constantly
    across directories, so this is what stops re-collecting and re-mailing them.
    """
    with _conn() as c:
        if email:
            if c.execute("SELECT 1 FROM leads WHERE lower(email)=?",
                         (_norm(email),)).fetchone():
                return True
        if phone:
            if c.execute("SELECT 1 FROM leads WHERE replace(phone,' ','')=?",
                         (phone.replace(" ", ""),)).fetchone():
                return True
        if company:
            if c.execute("SELECT 1 FROM leads WHERE lower(company)=?",
                         (company.strip().lower(),)).fetchone():
                return True
    return False


def add_lead_if_new(lead: dict):
    """
    Store a lead only if it's genuinely new. Returns the lead id, or None if it
    was a duplicate (and logs the skip so the Scanner can report 'N dupes skipped').
    """
    if lead_seen(lead.get("email"), lead.get("phone"), lead.get("company")):
        return None
    ev = lead.get("evidence")
    if ev is not None and not isinstance(ev, str):
        ev = json.dumps(ev)
    with _conn() as c:
        _migrate(c)
        cur = c.execute(
            "INSERT INTO leads(name,company,email,phone,socials,source,area,segment,"
            "found_at,website,whatsapp,address,contact_kind,evidence)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (lead.get("name"), lead.get("company"), _norm(lead.get("email")),
             lead.get("phone"), lead.get("socials"), lead.get("source"),
             lead.get("area"), lead.get("segment"), _now(),
             lead.get("website"), lead.get("whatsapp"), lead.get("address"),
             lead.get("contact_kind"), ev),
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
    rid = post_request(from_sector, to_sector, subject, body)
    if not blocking:
        return rid
    waited = 0
    while waited < timeout:
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
    return None


# ----------------------------------------------------------------------------
# SITE QUEUE — read-only mode: record a change instead of making it
# ----------------------------------------------------------------------------

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
    body = json.dumps(detail)[:2000]
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
                   audit=None, by="leads-outreach"):
    """Hold one drafted mail for the owner. Returns the row id.

    ASK ONCE: the same draft to the same address, still pending, is not queued
    again. The Hunter runs daily and would otherwise fill his list with copies.
    """
    with _conn() as c:
        _migrate(c)
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
    Has the owner ever approved a batch? Once he has, outreach runs on its own.

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
                "why": ("Approved. This one goes out on the next Leads pass, and "
                        "outreach is now armed — later drafts that pass Security "
                        "and the Auditor send without asking again."
                        if decision == "approved" and moved else
                        "Turned down. It will not be sent." if moved else "")}
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
