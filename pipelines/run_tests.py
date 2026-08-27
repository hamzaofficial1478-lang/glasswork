"""
run_tests.py — run: python -m pipelines.run_tests   (python3 on macOS/Linux)

Covers the three pieces the Run button stands on:

  coordination/readiness.py   can each sector work RIGHT NOW, and does the
                              answer distinguish 'proven' from 'saved'
  coordination/activity.py    is the timeline honestly derived from the rows
                              the work itself wrote
  pipelines/day_run.py        does the check refuse a broken day, does one
                              press mean one day, and does a step that sat out
                              read as having sat out rather than as a success

Everything runs against a throwaway cabinet and a throwaway provider store. No
network, no real API call, and nothing touches the owner's real config.
"""
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from coordination import activity, board, readiness
from providers import store

_fail = []


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


def _fresh_db():
    d = Path(tempfile.mkdtemp())
    db = d / "agency.db"
    con = sqlite3.connect(db)
    con.executescript((Path(__file__).parents[1] / "coordination" / "schema.sql").read_text(encoding="utf-8"))
    con.commit()
    con.close()
    # One place says where the cabinet is. Every reader goes through board.DB,
    # so redirecting it here redirects all of them — that is the whole point of
    # having one definition, and it is what these tests are standing on.
    board.DB = db
    board.ensure_schema()
    return db


def _rows(q, args=()):
    """Read from the throwaway cabinet. Used by the assertions, not the code."""
    c = sqlite3.connect(board.DB)
    try:
        return c.execute(q, args).fetchall()
    finally:
        c.close()


def _now():
    return int(time.time())


# ---------------------------------------------------------------------------
# READINESS
# ---------------------------------------------------------------------------

def _readiness_tests():
    print("\nConfigured is not working:")

    saved = {"n": 1, "verified": 0, "tested": 0, "names": ["X · m"]}
    proven = {"n": 2, "verified": 2, "tested": 2, "names": ["X · m", "Y · n"]}
    lone = {"n": 1, "verified": 1, "tested": 1, "names": ["X · m"]}

    real = readiness._brains
    try:
        readiness._brains = lambda task: saved
        r = readiness.sector("report", "report", env={"NTFY_TOPIC": "t"}, shelf={})
        check("saved but never called -> unchecked, not ready", r["state"], "unchecked")

        readiness._brains = lambda task: proven
        r = readiness.sector("report", "report", env={"NTFY_TOPIC": "t"}, shelf={})
        check("two proven brains -> ready", r["state"], "ready")

        readiness._brains = lambda task: lone
        r = readiness.sector("report", "report", env={"NTFY_TOPIC": "t"}, shelf={})
        check("one proven brain, no fallback -> thin", r["state"], "thin")

        # MD and Security are the two that put hard calls to two models
        r = readiness.sector("md", "md", env={}, shelf={})
        check("MD on one brain -> thin (the cross-check stops silently)", r["state"], "thin")
        check("and it says why", any("cross-check" in t for t in r["thin"]), True)
        r = readiness.sector("security", "security", env={}, shelf={})
        check("Security on one brain -> thin", r["state"], "thin")

        readiness._brains = lambda task: {"n": 0, "verified": 0, "names": [],
                                          "error": "no routing"}
        r = readiness.sector("seo", "seo", env={},
                             shelf={"seo_data": {"items": 2, "working": 2},
                                    "crawler": {"items": 1, "working": 1}})
        check("no brain -> blocked whatever the shelf says", r["state"], "blocked")

        print("\nA required shelf outranks everything else:")
        readiness._brains = lambda task: proven
        # THIS USED TO ASSERT THAT AN EMPTY MAPS SHELF BLOCKS LEADS, and that
        # was right for as long as search_places was the only way the Hunter
        # found anybody. pipelines/leads_osm.py finds UAE businesses on
        # OpenStreetMap with no key, so a missing Places key is now a coverage
        # note rather than a dead room — and a lamp saying BLOCKED over a
        # sector that can work would be the lamp lying.
        r = readiness.sector("leads", "leads",
                             env={"SMTP_HOST": "h", "OUTREACH_DOMAIN": "d"},
                             shelf={"maps": {"items": 0, "working": 0},
                                    "email": {"items": 2, "working": 2}})
        check("no Places key is no longer a blackout", r["state"] != "blocked", True)
        check("and nothing claims the shelf stops it",
              any("maps" in b for b in r["blocking"]), False)
        # ...but a shelf that IS required still outranks everything, which is
        # what this section is actually here to protect. Assert it against the
        # declaration rather than one sector's current list, so the rule keeps
        # being tested after the next sector changes its mind.
        required = [(s, k) for s, sh in readiness.SHELVES.items()
                    for k, need in sh.items() if need]
        if required:
            sec, shelf_name = required[0]
            r = readiness.sector(sec, sec, env={},
                                 shelf={shelf_name: {"items": 0, "working": 0}})
            check(f"an empty REQUIRED shelf still blocks ({sec}/{shelf_name})",
                  r["state"], "blocked")
            check("and names it",
                  any(shelf_name in b for b in r["blocking"]), True)
        else:
            check("no shelf is required of any sector any more — noted", True, True)

        r = readiness.sector("content", "content", env={},
                             shelf={"stock_images": {"items": 0, "working": 0},
                                    "image_gen": {"items": 0, "working": 0}})
        check("an OPTIONAL shelf missing does not block Content", r["state"], "ready")

        print("\nA missing key is a blocker, not a warning:")
        r = readiness.sector("it", "it", env={}, shelf={})
        check("no SITE_URL -> IT blocked", r["state"], "blocked")
        r = readiness.sector("it", "it", env={"SITE_URL": "https://x.ae"}, shelf={})
        check("with SITE_URL -> IT ready", r["state"], "ready")

        print("\nThe worst state wins, never the average:")
        check("blocked beats ready", readiness._worst("ready", "blocked"), "blocked")
        check("thin beats unchecked", readiness._worst("unchecked", "thin"), "thin")
        check("unchecked beats ready", readiness._worst("ready", "unchecked"), "unchecked")
    finally:
        readiness._brains = real

    print("\nThe remembered check:")
    check("no check yet -> empty, not a fake pass", readiness.last_check(), {})
    readiness.record_check({"ready": False, "blocking": ["seo has no brain"]})
    lc = readiness.last_check()
    check("the check is remembered", lc["ready"], False)
    check("with the reason", lc["blocking"], ["seo has no brain"])


# ---------------------------------------------------------------------------
# ACTIVITY
# ---------------------------------------------------------------------------

def _activity_tests():
    now = _now()
    con = sqlite3.connect(board.DB)
    con.execute("INSERT INTO tasks(id,sector,title,risk,status,owner,created_by,"
                "created_at,claimed_at,finished_at) VALUES"
                "('t1','Leads','Collect Al Quoz firms','green','done','leads-hunter','MD',?,?,?)",
                (now - 300, now - 280, now - 200))
    con.execute("INSERT INTO agent_runs(agent,started_at,ended_at,status,note,usd_cost)"
                " VALUES('leads-hunter',?,?,'ok','added 34 leads',0.2)", (now - 280, now - 200))
    con.execute("INSERT INTO agent_runs(agent,started_at,ended_at,status,note)"
                " VALUES('content-writer',?,?,'error','image_gen returned 402')",
                (now - 150, now - 149))
    con.execute("INSERT INTO borrows(shelf,item,agent,sector,reason,status,granted_at)"
                " VALUES('maps','Places','leads-hunter','Leads','Al Quoz','active',?)",
                (now - 280,))
    con.execute("INSERT INTO security_events(kind,severity,source,detail,action,at)"
                " VALUES('injection','high','scraped page','ignore previous','refused',?)",
                (now - 100,))
    # 90 leads in one pass: the noise case
    for i in range(90):
        con.execute("INSERT INTO leads(company,email,email_valid,found_at)"
                    " VALUES(?,?,1,?)", (f"co{i}", f"a{i}@x.ae", now - 250))
    con.commit()
    con.close()

    ev = activity.since(now - 3600, limit=200)
    verbs = {(e["who"], e["verb"]) for e in ev}

    print("\nThe five verbs come off real rows:")
    check("a finished run is delivered", ("leads-hunter", "delivered") in verbs, True)
    check("a claimed task is grabbed", ("leads-hunter", "grabbed") in verbs, True)
    check("the MD posting a task is pushed", ("MD", "pushed") in verbs, True)
    check("a failed run is errored", ("content-writer", "errored") in verbs, True)

    print("\nSecurity catching a scam is Security WORKING:")
    sec = [e for e in ev if e["who"] == "security-screener"]
    check("a refused injection is 'got', not 'errored'",
          {e["verb"] for e in sec}, {"got"})
    check("but it still reads as bad", {e["level"] for e in sec}, {"bad"})

    print("\nNinety leads are one line, not ninety:")
    check("no per-lead rows on the timeline",
          sum(1 for e in ev if "co50" in e["what"]), 0)
    check("the run's own count carries it instead",
          any("added 34 leads" in e["what"] for e in ev), True)

    print("\nThe two agents that are not named sector-role still find their room:")
    con = sqlite3.connect(board.DB)
    con.execute("INSERT INTO agent_runs(agent,started_at,ended_at,status,note)"
                " VALUES('managing-director',?,?,'ok','posted 3 tasks')",
                (now - 90, now - 80))
    con.execute("INSERT INTO agent_runs(agent,started_at,ended_at,status,note)"
                " VALUES('reporter',?,?,'ok','scored the day')", (now - 70, now - 60))
    con.commit(); con.close()
    ev = activity.since(now - 3600, limit=200)
    check("the MD's line lands in the Director's Office",
          next(e["room"] for e in ev if e["who"] == "managing-director"), "Director")
    check("the Reporter's line lands in Reports",
          next(e["room"] for e in ev if e["who"] == "reporter"), "Report")

    print("\n...and their DESKS light up, which they never used to:")
    from coordination import state as _state
    rooms = {r["key"]: r for r in _state.build()["rooms"]}
    check("the Director's Office has a live agent",
          [a["s"] for a in rooms["Director"]["agents"] if a["n"] == "managing-director"],
          ["done"])
    check("and it is seated at the Director desk",
          [a["slot"] for a in rooms["Director"]["agents"] if a["n"] == "managing-director"],
          ["director"])
    check("Reports has one too",
          [a["s"] for a in rooms["Report"]["agents"] if a["n"] == "reporter"], ["done"])

    print("\nA stalled run is not a working one:")
    con = sqlite3.connect(board.DB)
    con.execute("INSERT INTO agent_runs(agent,started_at,status,note)"
                " VALUES('seo-strategist',?,'running','reading ranks')", (now - 4000,))
    con.commit(); con.close()
    ev = activity.since(now - 9000, limit=200)
    st = [e for e in ev if e["who"] == "seo-strategist"]
    check("started 66 minutes ago and never came back -> errored",
          st[0]["verb"], "errored")
    # The wording is anchored to when the run BEGAN, not recomputed against
    # now() on every read. "started 12h ago and never came back" was rebuilt
    # fresh each time the log was opened, so a desk that recovered hours
    # earlier still carried a report of being dead that looked brand new.
    check("and says so", "stopped responding" in st[0]["what"], True)
    check("naming when it began, not how long ago that was",
          "began at" in st[0]["what"], True)
    check("and what happens to it next",
          "Overseer" in (st[0]["detail"] or ""), True)

    print("\nA cabinet with none of these tables degrades, never crashes:")
    # An owner who built his cabinet before half these tables existed must get
    # an empty timeline, not a stack trace on the office floor.
    was = board.DB
    board.DB = Path(tempfile.mkdtemp()) / "bare.db"
    sqlite3.connect(board.DB).close()
    try:
        check("empty cabinet -> empty timeline", activity.since(now - 3600), [])
        check("and the feed still has a head",
              activity.feed()["head"], "nothing has run yet")
    finally:
        board.DB = was


# ---------------------------------------------------------------------------
# THE RUN ITSELF
# ---------------------------------------------------------------------------

def _approval_tests():
    """The owner's yes must actually land in the cabinet."""
    now = _now()
    con = sqlite3.connect(board.DB)
    con.execute("INSERT INTO tasks(id,sector,title,detail,risk,status,created_by,created_at)"
                " VALUES('amb1','IT','Ship the gallery fix','touches a core template',"
                "'amber','open','MD',?)", (now - 60,))
    con.execute("INSERT INTO tasks(id,sector,title,risk,status,created_by,created_at)"
                " VALUES('amb2','Content','Publish the case study','amber','open','MD',?)",
                (now - 60,))
    con.execute("INSERT INTO site_queue(agent,kind,detail,status,queued_at)"
                " VALUES('it-releaser','upsert_page','the pergolas page','pending',?)",
                (now - 60,))
    con.commit(); con.close()

    from coordination import state as _state
    apps = _state.build()["approvals"]
    print("\nEvery item waiting on the owner can be addressed:")
    check("all three are listed", len(apps), 3)
    check("each carries what it is", sorted({a["kind"] for a in apps}),
          ["site_change", "task"])
    check("and an id to decide against", all(a.get("id") is not None for a in apps), True)

    print("\nApproving one writes it down, rather than dimming a row:")
    board.decide_approval("task", "amb1", "approved")
    con = sqlite3.connect(board.DB)
    risk, detail = con.execute("SELECT risk, detail FROM tasks WHERE id='amb1'").fetchone()
    con.close()
    check("the task is no longer amber", risk, "green")
    check("and the approval is on the record", "owner approved this on" in detail, True)
    check("so it drops off the waiting list",
          [a["id"] for a in _state.build()["approvals"] if a["id"] == "amb1"], [])

    print("\nRefusing blocks it — it does not quietly proceed:")
    board.decide_approval("task", "amb2", "rejected")
    con = sqlite3.connect(board.DB)
    st = con.execute("SELECT status FROM tasks WHERE id='amb2'").fetchone()[0]
    con.close()
    check("blocked", st, "blocked")

    print("\nA queued site change goes through the same door:")
    board.decide_approval("site_change", 1, "approved")
    con = sqlite3.connect(board.DB)
    st = con.execute("SELECT status FROM site_queue WHERE id=1").fetchone()[0]
    con.close()
    check("approved in site_queue", st, "approved")
    check("nothing is left waiting", _state.build()["approvals"], [])

    print("\nNothing else may be decided this way:")
    for kind, ref in (("shelf", "seo_data"), ("", "x"), ("decision", "d1")):
        try:
            board.decide_approval(kind, ref, "approved")
            check(f"'{kind}' is refused", "no error", "ValueError")
        except ValueError:
            check(f"'{kind}' is refused", True, True)
    try:
        board.decide_approval("task", "amb1", "maybe")
        check("a decision must be yes or no", "no error", "ValueError")
    except ValueError:
        check("a decision must be yes or no", True, True)
    check("deciding a task that has gone is reported, not silent",
          board.decide_approval("task", "no-such-task", "approved")["ok"], False)


def _day_run_tests():
    from . import day_run

    print("\nA step that sat out is not a step that succeeded:")
    check("exit 0 and ok:false -> idle",
          day_run._verdict('{"ok": false, "error": "no task"}', 0), "idle")
    check("exit 0 and ok:true -> ok", day_run._verdict('{"ok": true, "posted": 3}', 0), "ok")
    check("non-zero exit -> failed", day_run._verdict("boom", 1), "failed")
    check("prose output -> ok", day_run._verdict("MD CYCLE — posted 1 task", 0), "ok")

    print("\nThe one-line summary reads the answer, not the closing brace:")
    check("a JSON object is read, not tailed",
          day_run._summarise('{\n "ok": true,\n "gaps_found": 69\n}', 0),
          "gaps found 69")
    check("ok:false leads with the reason",
          day_run._summarise('{"ok": false, "error": "No open IT task"}', 0),
          "No open IT task")
    check("prose leads with its headline, not its last line",
          day_run._summarise("MD CYCLE — posted 1\n\nPOSTED\n  + a thing", 0),
          "MD CYCLE — posted 1")
    check("a traceback shows the exception, not the header",
          day_run._summarise("Traceback (most recent call last):\n  File x\nKeyError: 'seo'", 1),
          "KeyError: 'seo'")
    check("no output at all still says something",
          day_run._summarise("", 3), "no output (exit 3)")

    print("\nA killed run does not stay 'running' for ever:")
    con = sqlite3.connect(board.DB)
    long_ago = _now() - (day_run.STEP_TIMEOUT + 3600)
    con.execute("INSERT INTO day_runs(id,started_at,status,started_by,steps_total)"
                " VALUES('dead',?,'running','owner',2)", (long_ago,))
    con.execute("INSERT INTO day_steps(run_id,seq,label,module,status,started_at)"
                " VALUES('dead',0,'The MD plans','pipelines.md_cycle','ok',?)", (long_ago,))
    con.execute("INSERT INTO day_steps(run_id,seq,label,module,status,started_at)"
                " VALUES('dead',1,'IT checks','pipelines.it_health','running',?)", (long_ago,))
    con.commit(); con.close()

    st = day_run.status("dead")
    check("no lock, no progress -> reaped", st["run"]["status"], "stalled")
    check("and the button is pressable again", st["run"]["running"], False)
    check("the step it died on is marked failed",
          [s["status"] for s in st["steps"]], ["ok", "failed"])
    check("and says what happened",
          "killed" in (st["steps"][1]["summary"] or ""), True)
    check("the head tells the owner to press Run again",
          "Press Run again" in st["head"], True)

    print("\n...but a run that is actually MOVING is left alone:")
    # THIS USED TO ASSERT THAT HOLDING THE LOCK PROTECTED A RUN, and the owner
    # paid for it: a run killed hours earlier still held the lock for its
    # three-hour lease, the reaper refused to reap because the lock was held,
    # and start() refused to start because the lock was held. Each waited for
    # the other and he was told "a day is already running" by a corpse.
    #
    # Progress is the signal now, and the lock is a thing to clean up. So the
    # fixture here has to be a run that is genuinely moving — because that is
    # the thing that must be protected, and a lock is not evidence of life.
    now = _now()
    con = sqlite3.connect(board.DB)
    con.execute("INSERT INTO day_runs(id,started_at,status,started_by,steps_total)"
                " VALUES('slow',?,'running','owner',1)", (now - 60,))
    con.execute("INSERT INTO day_steps(run_id,seq,label,module,status,started_at)"
                " VALUES('slow',0,'SEO watches','pipelines.competitors','running',?)",
                (now - 30,))
    con.commit(); con.close()
    board.acquire_lock(day_run.LOCK, day_run.LOCK_HOLDER, wait=False, ttl=600)
    try:
        st = day_run.status("slow")
        check("still running, because a step moved a moment ago",
              st["run"]["status"], "running")
        check("and it keeps its lock",
              bool(_rows("SELECT 1 FROM locks WHERE resource=?", (day_run.LOCK,))),
              True)
    finally:
        board.release_lock(day_run.LOCK, day_run.LOCK_HOLDER)

    print("\n...and a LIVE run on a long step survives him opening a screen:")
    # "one major bug i found when the program was working and i opened the full
    #  control room section and when i get back to office the progarm had
    #  stoped working"
    #
    # It had not stopped — this reaped it. Every screen that shows the run
    # calls status(), status() calls _reap, and _reap declared it dead on a
    # stopwatch alone. His own log says why the stopwatch ran out on a healthy
    # day: "SEO builds the keyword picture — timed out after 420s". A step is
    # ALLOWED to take seven minutes and writes nothing while it does.
    # Past the fuse for the LONGEST step there is, so the pid check is what
    # decides these and not the stopwatch.
    quiet = _now() - (day_run.longest_step() + 300)
    con = sqlite3.connect(board.DB)
    con.execute("INSERT INTO day_runs(id,started_at,status,started_by,steps_total,pid,host)"
                " VALUES('busy',?,'running','owner',1,?,?)",
                (quiet, os.getpid(), board._this_host()))
    con.execute("INSERT INTO day_steps(run_id,seq,label,module,status,started_at)"
                " VALUES('busy',0,'SEO builds the keyword picture',"
                "'pipelines.seo_intel','running',?)", (quiet,))
    con.commit(); con.close()
    st = day_run.status("busy")
    check("nothing has moved in a very long time, and it is still running",
          st["run"]["status"], "running")
    check("the step it is on is untouched", st["steps"][0]["status"], "running")

    print("\n...and asking twice does not change the answer:")
    check("still running", day_run.status("busy")["run"]["status"], "running")

    print("\n...while a pid that really is gone is still reaped:")
    corpse = subprocess.Popen([sys.executable, "-c", "pass"])
    corpse.wait()
    con = sqlite3.connect(board.DB)
    con.execute("INSERT INTO day_runs(id,started_at,status,started_by,steps_total,pid,host)"
                " VALUES('corpse',?,'running','owner',1,?,?)",
                (quiet, corpse.pid, board._this_host()))
    con.execute("INSERT INTO day_steps(run_id,seq,label,module,status,started_at)"
                " VALUES('corpse',0,'IT checks','pipelines.it_health','running',?)", (quiet,))
    con.commit(); con.close()
    st = day_run.status("corpse")
    check("the process is gone, so the books are closed", st["run"]["status"], "stalled")
    check("and the step it died on says so", st["steps"][0]["status"], "failed")

    print("\n...and a dead one lets go of the lock, so the button works again:")
    con = sqlite3.connect(board.DB)
    con.execute("INSERT INTO day_runs(id,started_at,status,started_by,steps_total)"
                " VALUES('gone',?,'running','owner',1)", (long_ago,))
    con.execute("INSERT INTO day_steps(run_id,seq,label,module,status,started_at)"
                " VALUES('gone',0,'SEO watches','pipelines.competitors','running',?)",
                (long_ago,))
    con.commit(); con.close()
    board.acquire_lock(day_run.LOCK, day_run.LOCK_HOLDER, wait=False, ttl=3 * 3600)
    st = day_run.status("gone")
    check("reaped despite the lease having hours left", st["run"]["status"], "stalled")
    check("and the lock is released with it",
          _rows("SELECT 1 FROM locks WHERE resource=?", (day_run.LOCK,)), [])

    print("\nThe same complaint from two checks is one problem:")
    check("deduplicated, order kept",
          day_run._once(["b has no brain", "B HAS NO BRAIN", "a is empty"]),
          ["b has no brain", "a is empty"])

    _lane_tests(day_run)


def _lane_tests(day_run):
    """
    The day is lanes, not a queue.

    The owner asked for a slow agent to be moved into another line so it stops
    holding everyone else up. Three things have to be true, and the second and
    third are what stop that turning into a mess: the lanes really do overlap,
    the dependencies are still respected, and no more than MAX_LANES processes
    are ever in flight.
    """
    import threading
    import types

    print("\nEvery step still belongs to exactly one lane:")
    plan = day_run.lanes()
    check("nothing is unassigned",
          sorted(i for v in plan.values() for i in v), list(range(len(day_run.STEPS))))
    check("no step is in two lanes",
          len([i for v in plan.values() for i in v]), len(day_run.STEPS))
    check("every lane a lane waits for exists",
          sorted({d for deps in day_run.LANE_AFTER.values() for d in deps} - set(plan)),
          [])
    check("every lane says what it waits for",
          sorted(set(plan) - set(day_run.LANE_AFTER)), [])

    print("\nAnd waiting cannot go round in a circle:")
    seen, order = set(), []

    def visit(lane, trail=()):
        if lane in trail:
            order.append("CYCLE:" + "->".join(trail + (lane,)))
            return
        if lane in seen:
            return
        for dep in day_run.LANE_AFTER.get(lane, ()):
            visit(dep, trail + (lane,))
        seen.add(lane)
        order.append(lane)
    for lane in plan:
        visit(lane)
    check("no lane waits, however indirectly, on itself",
          [o for o in order if o.startswith("CYCLE")], [])
    check("'open' runs before everything that follows it", order[0], "open")
    check("'close' runs last", order[-1], "close")

    print("\nLanes really do overlap, and one slow desk holds nobody up:")
    started, live, peak, lock = [], [0], [0], threading.Lock()
    slow = "pipelines.seo_intel"
    # Which OTHER lanes finished a step while the slow one was still going. This
    # is the owner's actual requirement — "if an agent is taking more time it
    # should be put into the other line so it doesn't hold up the others" — and
    # it is a fact about the interleaving, not about how fast the machine is.
    slow_running, during = [False], set()

    # A FAKE Popen, NOT A FAKE run(). The step runner used subprocess.run,
    # which blocks until the child is finished — and that is precisely what
    # the Stop button could not live with, so it now opens the child and waits
    # on it in short hops. The stub has to be the same shape, or the test
    # proves the lanes work in a way the code no longer runs.
    class _Fake:
        def __init__(self, mod, sink):
            self.mod, self.sink, self.returncode = mod, sink, None
            self.dur = 0.30 if mod == slow else 0.02
            self.t0 = time.time()
            with lock:
                started.append(mod)
                live[0] += 1
                peak[0] = max(peak[0], live[0])
                if mod == slow:
                    slow_running[0] = True

        def _finish(self, code=0):
            if self.returncode is not None:
                return self.returncode
            self.returncode = code
            try:
                self.sink.write(b'{"ok": true, "did": 1}')
            except Exception:
                pass
            with lock:
                live[0] -= 1
                if self.mod == slow:
                    slow_running[0] = False
                elif slow_running[0]:
                    during.add(self.mod)
            return code

        def wait(self, timeout=None):
            left = self.dur - (time.time() - self.t0)
            if left <= 0:
                return self._finish()
            if timeout is not None and timeout < left:
                time.sleep(timeout)
                raise subprocess.TimeoutExpired(self.mod, timeout)
            time.sleep(left)
            return self._finish()

        def terminate(self):
            self._finish(-15)

        def kill(self):
            self._finish(-9)

    def fake_popen(cmd, cwd=None, stdout=None, stderr=None, **kw):
        return _Fake(cmd[-1], stdout)

    real_sub, day_run.subprocess = day_run.subprocess, types.SimpleNamespace(
        Popen=fake_popen, STDOUT=subprocess.STDOUT,
        TimeoutExpired=subprocess.TimeoutExpired)
    try:
        rid = day_run._open_run("lane test", False, [])
        failed, was_stopped = day_run._run_lanes(rid)
    finally:
        day_run.subprocess = real_sub

    check("nothing failed", failed, 0)
    check("and nobody stopped it", was_stopped, False)
    check("more than one step ran at once", peak[0] > 1, True)
    check("and never more than the cap", peak[0] <= day_run.MAX_LANES, True)
    # DELIBERATELY NOT A STOPWATCH. The first version asserted the whole run
    # took under 0.6s, which is a claim about the machine rather than about the
    # code, and it duly failed once on a loaded box and passed on the retry. A
    # test that is sometimes right teaches you to re-run tests.
    #
    # The property that actually matters is the owner's: while one desk is slow,
    # the others get on with it. That is exactly what `during` records.
    mods = lambda lane: {day_run.STEPS[i][1] for i in plan[lane]}   # noqa: E731
    check("the whole IT lane got through while SEO's slow step ran",
          mods("it") <= during, True)
    check("so did the whole Leads lane", mods("leads") <= during, True)
    check("and nothing in SEO's own lane overtook it — a lane is still a queue",
          mods("seo") & during, set())

    print("\n...but a step never starts before what it depends on has finished:")
    at = {m: i for i, m in enumerate(started)}
    check("the MD plans before any sector works",
          at["pipelines.md_cycle"] < min(at["pipelines.it_health"],
                                         at["pipelines.leads_sources"],
                                         at["pipelines.seo_intel"]), True)
    check("Security screens after Leads has collected",
          at["pipelines.leads_hunt"] < at["pipelines.security_screen"], True)
    check("Content is briefed after the AEO audit exists",
          at["pipelines.seo_answers"] < at["pipelines.content_briefs"], True)
    check("Report scores last of all",
          at["pipelines.report_daily"], len(started) - 1)

    print("\nAnd the run panel does not name one lane and hide the others:")
    con = sqlite3.connect(board.DB)
    con.execute("UPDATE day_steps SET status='running' WHERE run_id=? AND seq IN (3,5,7)",
                (rid,))
    con.execute("UPDATE day_runs SET status='running' WHERE id=?", (rid,))
    con.commit()
    con.close()
    board.acquire_lock(day_run.LOCK, day_run.LOCK_HOLDER, wait=False, ttl=600)
    try:
        st = day_run.status(rid)
        check("all three are reported", len(st["doing_now"]), 3)
        check("and the headline says so", "at the same time" in st["head"], True)
    finally:
        board.release_lock(day_run.LOCK, day_run.LOCK_HOLDER)

    _stop_button_tests(day_run)

    # EVERYTHING BELOW HERE WAS DEAD CODE.
    #
    # A helper called real_timeout_error() was once dropped in at this point,
    # and the rest of these tests ended up inside it, after its `return`.
    # Python does not complain about that: the module imported, the suite
    # printed PASS, and roughly ninety assertions — one press means one day, a
    # broken day refusing to start, the owner's orders reaching the board
    # before the MD plans, an order for a sector that does not exist — had
    # simply not run for as long as the helper had existed.
    #
    # The helper itself is gone: the stub now uses the genuine
    # subprocess.TimeoutExpired directly, which is all it was ever for.

    print("\nOne press means one day:")
    check("the lock is free to start with",
          board.acquire_lock(day_run.LOCK, "someone-else", wait=False, ttl=60), True)
    r = day_run.start(force=True, skip_check=True)
    check("a second run is refused while the first holds the lock", r.get("busy"), True)
    check("and it says why in plain words", "already running" in r["why"], True)
    board.release_lock(day_run.LOCK, "someone-else")

    print("\nA broken day does not start itself:")
    real = day_run.preflight
    day_run.preflight = lambda quick=False: {
        "ready": False, "blocking": ["Leads: the email shelf is empty"], "warnings": []}
    try:
        r = day_run.start()
        check("refused", r.get("refused"), True)
        check("nothing ran", r.get("ran"), False)
        check("the reason is the owner's, not a stack trace",
              "email shelf is empty" in r["why"], True)

        print("\n...but the owner is the tie-breaker:")
        real_steps, day_run.STEPS = day_run.STEPS, []
        try:
            r = day_run.start(force=True)
            check("--force runs it anyway", r.get("ran"), True)
            con = sqlite3.connect(board.DB)
            forced = con.execute("SELECT forced FROM day_runs WHERE id=?",
                                 (r["run_id"],)).fetchone()[0]
            con.close()
            check("and it is recorded as forced, in writing", forced, 1)
        finally:
            day_run.STEPS = real_steps
    finally:
        day_run.preflight = real

    print("\nThe owner's orders reach the board BEFORE the MD plans:")
    from coordination import proposals
    seen = {}
    real_set, real_pre = proposals.set_orders, day_run.preflight
    real_steps, day_run.STEPS = day_run.STEPS, []   # no subprocesses here
    day_run.preflight = lambda quick=False: {"ready": True, "blocking": [], "warnings": []}
    proposals.set_orders = lambda sec, text, day=None: seen.setdefault(sec, text)
    try:
        day_run.start(orders={"Leads": "fitout only", "SEO": "  "})
        check("a real order is written", seen.get("Leads"), "fitout only")
        check("an empty box is not written as an order", "SEO" in seen, False)
    finally:
        proposals.set_orders, day_run.preflight = real_set, real_pre
        day_run.STEPS = real_steps

    print("\nAn order for a sector that does not exist is not silently binned:")
    check("'leads' is the Leads sector", proposals.canonical_sector("leads"), "Leads")
    check("'  SeO ' too", proposals.canonical_sector("  SeO "), "SEO")
    check("'Marketing' is not a sector", proposals.canonical_sector("Marketing"), None)
    try:
        proposals.set_orders("Marketing", "do some marketing")
        check("an unknown sector is refused", "no error raised", "ValueError")
    except ValueError as e:
        check("an unknown sector is refused", "there is no 'Marketing' sector" in str(e), True)
    # the board matches sector EXACTLY, so a lowercase order must land as 'Leads'
    proposals.set_orders("leads", "fitout only")
    check("and a lowercase one lands where the board will find it",
          list(proposals.orders()), ["Leads"])

    real_pre, day_run.preflight = day_run.preflight, (
        lambda quick=False: {"ready": True, "blocking": [], "warnings": []})
    real_steps, day_run.STEPS = day_run.STEPS, []
    try:
        r = day_run.start(orders={"Marketing": "sell more", "IT": "check the form"})
        check("the run still happens", r.get("ran"), True)
        check("the good order lands", r["orders"].get("IT"), "check the form")
        check("and the bad one is reported, not swallowed",
              "Marketing" in r["rejected_orders"], True)
        check("in the sentence the owner reads", "went nowhere" in r["why"], True)
    finally:
        day_run.preflight, day_run.STEPS = real_pre, real_steps


def _stop_button_tests(day_run):
    """
    THE STOP BUTTON.

    "there is no any stop button that makes the agents or overseer to
    understand that the task is stopped by the user."

    Stopping has to be provable at three levels and each one can fail on its
    own, so each is tested on its own:

      the flag   is a stop something a running process can actually SEE
      the run    does a run in flight put its tools down, and does what it
                 never got to say so instead of sitting on 'waiting' for ever
      the child  a step is a subprocess that could be seven minutes into a
                 seven-minute budget. Faking that proves nothing, so this
                 stops a REAL process and then asks the operating system
                 whether it is gone.
    """
    import os
    import threading
    import types

    print("\nA stop is something a running process can see:")
    board.clear_stop(day_run.LOCK)
    check("nothing to see when nobody pressed anything",
          board.stop_requested(day_run.LOCK), None)
    board.request_stop(day_run.LOCK, by="owner", why="going out")
    r = board.stop_requested(day_run.LOCK)
    check("pressed -> a live stop, with his reason", (r or {}).get("why"), "going out")
    board.request_stop(day_run.LOCK, by="owner", why="again")
    check("pressing twice is still one stop",
          len(_rows("SELECT scope FROM stop_requests")), 1)
    board.clear_stop(day_run.LOCK)
    check("cleared -> gone, and it is history not an instruction",
          board.stop_requested(day_run.LOCK), None)
    check("but the row is kept", len(_rows("SELECT scope FROM stop_requests")), 1)

    print("\nA real process, really stopped:")
    victim = subprocess.Popen([sys.executable, "-c",
                               "import time; time.sleep(120)"])
    try:
        check("it is running", board.process_alive(victim.pid), True)
        out = board.end_process(victim.pid)
        check("end_process says it stopped it", out["ok"], True)
        check("and the operating system agrees",
              board.process_alive(victim.pid), False)
    finally:
        try:
            victim.kill()
        except Exception:
            pass
        try:
            victim.wait(timeout=5)
        except Exception:
            pass
    check("it will not fire at a pid it cannot place",
          board.end_process(victim.pid, host="some-other-laptop")["ok"], False)
    check("nor at nothing at all", board.end_process(0)["ok"], False)
    check("nor at itself", board.end_process(os.getpid())["ok"], False)

    if os.name != "nt":
        print("\n...and the steps it had started go with it:")
        # A run killed on its own leaves its steps orphaned and still writing
        # to the cabinet after he has been told the day stopped. The office
        # starts a day detached — start_new_session, the same thing Node's
        # detached:true does — which makes it a process-group leader, and the
        # whole group is signalled. This is that shape: a leader with a child.
        leader = subprocess.Popen(
            [sys.executable, "-c",
             "import subprocess,sys,time;"
             "k=subprocess.Popen([sys.executable,'-c','import time;time.sleep(90)']);"
             "print(k.pid,flush=True);time.sleep(90)"],
            start_new_session=True, stdout=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace")
        try:
            kid = int((leader.stdout.readline() or "0").strip())
            check("both the run and its step are up",
                  board.process_alive(leader.pid) and board.process_alive(kid), True)
            out = board.end_process(leader.pid)
            check("ending the run says it took the group with it",
                  out.get("group"), True)
            check("the run is gone", board.process_alive(leader.pid), False)
            time.sleep(0.3)
            # An orphaned step that has exited is reparented, and whether it is
            # collected promptly is up to whatever inherits it — in a container
            # PID 1 often never bothers, and it sits there as a zombie. That is
            # a dead process, and process_alive has to say so: a liveness check
            # that counts zombies as working is the very thing that kept his
            # Overseer waiting seventeen hours.
            check("and so is the step it had started",
                  board.process_alive(kid), False)
        finally:
            for p in (leader,):
                try:
                    p.kill()
                    p.wait(timeout=5)
                except Exception:
                    pass

    print("\nThe day puts its tools down when he presses Stop:")
    # A step that would take a minute, so the stop lands DURING one rather
    # than in the gap between two — the gap is the easy case.
    long_step = "import time; time.sleep(60)"
    real_steps = day_run.STEPS
    real_after = day_run.LANE_AFTER
    day_run.STEPS = [("A long step", "pipelines._never", "IT", "it"),
                     ("Another one", "pipelines._never", "IT", "it"),
                     ("A third", "pipelines._never", "SEO", "seo")]
    day_run.LANE_AFTER = {"it": (), "seo": ()}
    kids = []

    def slow_popen(cmd, cwd=None, stdout=None, stderr=None, **kw):
        p = subprocess.Popen([sys.executable, "-c", long_step],
                             stdout=stdout, stderr=stderr)
        kids.append(p)
        return p

    real_sub = day_run.subprocess
    day_run.subprocess = types.SimpleNamespace(
        Popen=slow_popen, STDOUT=subprocess.STDOUT,
        TimeoutExpired=subprocess.TimeoutExpired)
    board.clear_stop(day_run.LOCK)
    try:
        rid = day_run._open_run("stop test", False, [])
        held = {}

        def press():
            time.sleep(1.0)
            board.request_stop(day_run.LOCK, by="owner", why="lunch")

        threading.Thread(target=press, daemon=True).start()
        t0 = time.time()
        with day_run._StopWatch(every=0.2) as watch:
            failed, stopped = day_run._run_lanes(rid, watch)
        took = time.time() - t0
    finally:
        day_run.subprocess = real_sub
        for p in kids:
            try:
                p.kill()
                p.wait(timeout=5)
            except Exception:
                pass
        day_run.STEPS, day_run.LANE_AFTER = real_steps, real_after
        board.clear_stop(day_run.LOCK)

    check("the run reports it was stopped", stopped, True)
    # The steps sleep for sixty seconds each. Anything under ten means the
    # children were actually killed rather than waited out — and the assertion
    # is about the code, not the clock, because the gap is that wide.
    check("and it did not sit through the step it was in", took < 10, True)
    st = day_run.status(rid)
    marks = [s["status"] for s in st["steps"]]
    check("no step is left claiming to be running", "running" in marks, False)
    check("nor left waiting for a run that is over", "waiting" in marks, False)
    check("the step that was cut off says so",
          any("cut off" in (s["summary"] or "") for s in st["steps"]), True)
    check("and one that never started says that instead",
          any("before this started" in (s["summary"] or "") for s in st["steps"]), True)
    check("his words are on the record",
          any("lunch" in (s["summary"] or "") for s in st["steps"]), True)

    print("\n...and the run reads as stopped, not as failed:")
    day_run._close_run(rid, "stopped")
    st = day_run.status(rid)
    check("the run is 'stopped'", st["run"]["status"], "stopped")
    check("the button is pressable again", st["run"]["running"], False)
    check("the head says he stopped it", "you stopped this run" in st["head"], True)
    check("the bar is not stuck part-way on a day that is over",
          st["progress"][0], st["progress"][1])
    # This is the part the Overseer reads. 'failed' would put a stopped day in
    # its sights as something to investigate; 'stopped' is a fact about him.
    check("and it is not recorded as a failure", st["run"]["status"] != "failed", True)

    print("\nStop when nothing is running clears up instead of erroring:")
    board.acquire_lock(day_run.LOCK, day_run.LOCK_HOLDER, wait=False, ttl=600)
    r = day_run.stop(by="owner")
    check("it does not fail", r["ok"], True)
    check("it says nothing was running", r["was_running"], False)
    check("and it lets go of the door the last run left held",
          _rows("SELECT 1 FROM locks WHERE resource=?", (day_run.LOCK,)), [])

    print("\nA stop left over from a closed program does not kill the next day:")
    board.request_stop(day_run.LOCK, by="owner", why="stale")
    real_pre, day_run.preflight = day_run.preflight, (
        lambda quick=False: {"ready": True, "blocking": [], "warnings": []})
    real_steps, day_run.STEPS = day_run.STEPS, []
    try:
        r = day_run.start()
        check("the next run starts anyway", r.get("ran"), True)
        check("and is not born stopped", r.get("stopped"), None)
        check("the stale flag is gone", board.stop_requested(day_run.LOCK), None)
    finally:
        day_run.preflight, day_run.STEPS = real_pre, real_steps


def _backend_tests():
    """
    The back office: the password, and the promise that a status line never
    prints a secret.
    """
    import backend

    print("\nThe admin password is a real gate, not decoration:")
    h = backend.hash_password("correct horse battery")
    check("stored as pbkdf2 with a salt", h.split("$")[0], "pbkdf2")
    check("and enough rounds to be worth something",
          int(h.split("$")[1]) >= 200_000, True)
    check("the right password opens it",
          backend.check_password("correct horse battery", h), True)
    check("a wrong one does not", backend.check_password("wrong", h), False)
    check("nor does a tampered hash",
          backend.check_password("correct horse battery", h[:-4] + "AAAA"), False)
    check("nor does junk in the stored field",
          backend.check_password("x", "not-a-hash"), False)
    check("two hashes of the same password differ (the salt is random)",
          backend.hash_password("x") == backend.hash_password("x"), False)
    check("the plain password is nowhere in what gets stored",
          "correct horse battery" in h, False)

    print("\nWriting one key leaves the rest of .env alone:")
    import tempfile
    from pathlib import Path as _P
    was = backend.ENV
    backend.ENV = _P(tempfile.mkdtemp()) / ".env"
    try:
        backend.ENV.write_text("# my notes\nSMTP_PASS=hunter2\nNTFY_TOPIC=abc\n", encoding="utf-8")
        backend.write_env("ADMIN_PASSWORD_HASH", h)
        text = backend.ENV.read_text(encoding="utf-8")
        check("the comment survives", "# my notes" in text, True)
        check("the other secrets survive", "SMTP_PASS=hunter2" in text, True)
        check("and the new key is there", "ADMIN_PASSWORD_HASH=" in text, True)

        backend.write_env("ADMIN_PASSWORD_HASH", None)
        text = backend.ENV.read_text(encoding="utf-8")
        check("removing it takes only that line", "ADMIN_PASSWORD_HASH" in text, False)
        check("everything else is still there",
              ("SMTP_PASS=hunter2" in text and "NTFY_TOPIC=abc" in text), True)
    finally:
        backend.ENV = was

    print("\nThe status line never prints a secret:")
    src = (Path(__file__).parents[1] / "backend.py").read_text(encoding="utf-8")
    body = src.split("def where(")[1].split("def main(")[0]
    for leaky in ("e.get(\"SMTP_PASS\")", "e['SMTP_PASS']", "e.get(\"SITE_API_TOKEN\")",
                  "e.get(\"DATAFORSEO_PASSWORD\")"):
        check(f"it does not echo {leaky.split(chr(34))[1] if chr(34) in leaky else leaky}",
              leaky in body, False)
    check("it reports set/not-set instead", '"set" if' in body, True)


def main():
    real_db, real_store = board.DB, store.STORE
    _fresh_db()
    store.STORE = Path(tempfile.mkdtemp()) / "providers.local.json"
    try:
        _readiness_tests()
        _activity_tests()
        _approval_tests()
        _day_run_tests()
        _backend_tests()
    finally:
        board.DB, store.STORE = real_db, real_store

    if _fail:
        print(f"\n{len(_fail)} FAILED: {_fail}")
        sys.exit(1)
    print("\nall run tests passed")


if __name__ == "__main__":
    main()
