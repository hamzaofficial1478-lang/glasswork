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


# ----------------------------------------------------------------------------
# TASKS — claim, don't grab
# ----------------------------------------------------------------------------

def post_task(sector, title, detail, risk="green", created_by="MD"):
    """MD (or a lead) posts work. Starts life as 'open' and unowned."""
    tid = uuid.uuid4().hex[:12]
    with _conn() as c:
        c.execute(
            "INSERT INTO tasks(id,sector,title,detail,risk,status,owner,created_by,created_at)"
            " VALUES(?,?,?,?,?, 'open', NULL, ?, ?)",
            (tid, sector, title, detail, risk, created_by, _now()),
        )
    return tid


def claim_task(task_id, agent):
    """
    Atomically claim an open task. Returns True only if THIS agent got it.

    The UPDATE ... WHERE status='open' is the whole trick: SQLite serialises it,
    so if two agents race, exactly one UPDATE affects a row. The loser gets False
    and moves on. No task is ever worked by two agents.
    """
    with _conn() as c:
        c.execute("BEGIN IMMEDIATE;")
        cur = c.execute(
            "UPDATE tasks SET status='in_progress', owner=?, claimed_at=?"
            " WHERE id=? AND status='open'",
            (agent, _now(), task_id),
        )
        c.execute("COMMIT;")
        return cur.rowcount == 1


def next_open_task(sector, agent):
    """Claim the oldest open task for a sector, if any. Returns the task or None."""
    with _conn() as c:
        rows = c.execute(
            "SELECT id FROM tasks WHERE sector=? AND status='open' ORDER BY created_at",
            (sector,),
        ).fetchall()
    for r in rows:
        if claim_task(r["id"], agent):
            return get_task(r["id"])
    return None


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


def _migrate(c):
    """agent_runs gained its 'note' column after the first cabinets were built."""
    key = str(DB)
    if key in _MIGRATED:
        return
    try:
        cols = {r[1] for r in c.execute("PRAGMA table_info(agent_runs)")}
        if cols and "note" not in cols:
            c.execute("ALTER TABLE agent_runs ADD COLUMN note TEXT")
    except sqlite3.OperationalError:
        pass
    _MIGRATED.add(key)


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

def post_request(from_sector, to_sector, subject, body):
    """One sector asks another for something and waits. Never reaches in directly."""
    rid = uuid.uuid4().hex[:12]
    with _conn() as c:
        c.execute(
            "INSERT INTO requests(id,from_sector,to_sector,subject,status,created_at)"
            " VALUES(?,?,?,?, 'open', ?)",
            (rid, from_sector, to_sector, subject, _now()),
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


def open_requests_for(sector):
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM requests WHERE to_sector=? AND status='open' ORDER BY created_at",
            (sector,),
        ).fetchall()
        return [dict(r) for r in rows]


# ----------------------------------------------------------------------------
# DECISION LOG — the MD's scorecard (learns over time)
# ----------------------------------------------------------------------------

def log_decision(cycle, reasoning, expectation, confidence):
    did = uuid.uuid4().hex[:12]
    with _conn() as c:
        c.execute(
            "INSERT INTO decisions(id,cycle,reasoning,expectation,confidence,outcome,created_at)"
            " VALUES(?,?,?,?,?, NULL, ?)",
            (did, cycle, reasoning, expectation, confidence, _now()),
        )
    return did


def score_decision(decision_id, outcome):
    """Report sector calls this later: 'worked' / 'failed' / 'partial'."""
    with _conn() as c:
        c.execute("UPDATE decisions SET outcome=?, scored_at=? WHERE id=?",
                  (outcome, _now(), decision_id))


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
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO leads(name,company,email,phone,socials,source,area,segment,found_at)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (lead.get("name"), lead.get("company"), _norm(lead.get("email")),
             lead.get("phone"), lead.get("socials"), lead.get("source"),
             lead.get("area"), lead.get("segment"), _now()),
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
    """
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO site_queue(agent,kind,detail,status,queued_at)"
            " VALUES(?,?,?, 'pending', ?)",
            (agent, kind, json.dumps(detail)[:2000], _now()),
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
