"""
faults_tests.py — run: python -m coordination.faults_tests

What is being protected here is a promise, not a function: when an agent breaks,
somebody is told and somebody is given it to fix. The owner watched a stalled
agent sit for forty-one minutes while the control room drew it in red and did
nothing else, so every test below checks the CONSEQUENCE — a request in the
sector's inbox, a ticket on IT's board, a line in the MD's — and not merely that
the fault was noticed.

The other half is restraint. A detector that files a ticket every twenty minutes
for the same stall is worse than no detector: the owner mutes it, and the next
one goes unread too. So there are tests that a second pass files nothing, that
one silent sector produces one message and not fourteen, and that a fault which
fixes itself closes the conversations it opened.

Everything runs against a throwaway cabinet with real rows in it.
"""
import sqlite3
import tempfile
import time
from pathlib import Path

from . import board
from . import faults as F

_fail = []
ROOT = Path(__file__).parents[1]


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
    con.executescript((ROOT / "coordination" / "schema.sql").read_text(encoding="utf-8"))
    con.commit()
    con.close()
    return db


class _Cabinet:
    """Point both board and faults at a throwaway cabinet, then put it back."""

    def __enter__(self):
        self.real = board.DB
        board.DB = str(_fresh_db())
        return self

    def __exit__(self, *a):
        board.DB = self.real

    def sql(self, q, args=()):
        c = sqlite3.connect(board.DB)
        c.row_factory = sqlite3.Row
        try:
            rows = [dict(r) for r in c.execute(q, args)]
            c.commit()
            return rows
        finally:
            c.close()

    def write(self, q, args=()):
        c = sqlite3.connect(board.DB)
        try:
            c.execute(q, args)
            c.commit()
        finally:
            c.close()


def _recovered(cab, agent="seo-strategist"):
    """
    The desk comes back the only way a desk actually comes back: the agent runs
    again and finishes. The dead run stays dead — nothing ever reaches back and
    edits it, which is why the fault cannot be cleared by touching that row.
    """
    now = int(time.time())
    cab.write("INSERT INTO agent_runs(agent, started_at, ended_at, status, note)"
              " VALUES(?,?,?, 'ok', 'ran clean')", (agent, now - 60, now))


def _stall(cab, agent="seo-strategist", ago=None):
    """A run that started and never came back — the owner's actual fault."""
    started = int(time.time()) - (ago if ago is not None else F.STALL_SECS + 300)
    cab.write("INSERT INTO agent_runs(agent, started_at, status, note)"
              " VALUES(?,?, 'running', 'looking for page-two wins')", (agent, started))
    return started


# ---------------------------------------------------------------------------

def test_a_stall_is_found_and_given_to_somebody():
    print("\nA stalled agent is noticed, and somebody is given it:")
    with _Cabinet() as cab:
        _stall(cab)
        found = F.detect()
        check("the stall is found", [f["kind"] for f in found], ["stall"])
        check("and it names the desk", found[0]["agent"], "seo-strategist")
        check("and knows whose sector that is", found[0]["sector"], "SEO")

        out = F.scan()
        check("it is filed once", out["filed"], 1)

        inbox = cab.sql("SELECT to_sector, subject FROM requests WHERE from_sector='Overseer'")
        check("SEO is told its own desk is down",
              any(r["to_sector"] == "SEO" and "desk is down" in r["subject"] for r in inbox), True)

        tickets = cab.sql("SELECT sector, status FROM tasks WHERE created_by='Overseer'")
        check("IT is given the ticket, because IT is the sector that can fix code",
              [(t["sector"], t["status"]) for t in tickets], [("IT", "open")])


def test_a_desk_that_is_merely_slow_is_left_alone():
    print("\nA long run is not a stall:")
    with _Cabinet() as cab:
        _stall(cab, ago=F.STALL_SECS - 120)      # under the line
        check("nothing is filed", F.detect(), [])
        F.scan()
        check("and nobody is bothered",
              cab.sql("SELECT count(*) n FROM requests")[0]["n"], 0)


def test_the_same_fault_is_not_filed_twice():
    print("\nThe same stall on the next pass is the same stall:")
    with _Cabinet() as cab:
        _stall(cab)
        F.scan()
        before = cab.sql("SELECT count(*) n FROM requests")[0]["n"]
        second = F.scan()
        after = cab.sql("SELECT count(*) n FROM requests")[0]["n"]
        check("nothing new is filed", second["filed"], 0)
        check("and nobody is messaged again", after, before)
        row = cab.sql("SELECT seen_count FROM faults")[0]
        check("but the agency knows it is still true", row["seen_count"], 2)


def test_a_blocking_fault_reaches_the_md():
    print("\nBlocking means the MD hears about it:")
    with _Cabinet() as cab:
        _stall(cab, ago=5 * F.STALL_SECS)        # long enough to be blocking
        F.scan()
        md = cab.sql("SELECT subject FROM requests WHERE to_sector='Director'")
        check("the MD is told once", len(md), 1)
        check("and told what it is", "Escalating" in md[0]["subject"], True)


def test_a_degraded_fault_waits_before_escalating():
    print("\nOne bad afternoon is not an escalation:")
    with _Cabinet() as cab:
        _stall(cab)                              # degraded, not blocking
        F.scan()
        check("the MD is not woken on the first pass",
              cab.sql("SELECT count(*) n FROM requests WHERE to_sector='Director'")[0]["n"], 0)
        for _ in range(F.ESCALATE_AFTER - 1):
            F.scan()
        check(f"but after {F.ESCALATE_AFTER} passes with nobody fixing it, the MD is told",
              cab.sql("SELECT count(*) n FROM requests WHERE to_sector='Director'")[0]["n"], 1)


def test_one_silent_sector_is_one_message():
    print("\nA sector ignoring the same question is one fault, not fourteen:")
    with _Cabinet() as cab:
        old = int(time.time()) - F.UNANSWERED_SECS - 600
        for i in range(14):
            rid = f"r{i:02d}"
            cab.write("INSERT INTO requests(id,from_sector,to_sector,subject,status,created_at)"
                      " VALUES(?,'SEO','Content','AEO fixes for Content','open',?)",
                      (rid, old + i))
        found = F.detect()
        check("fourteen copies of one question are one fault", len(found), 1)
        check("and the count is part of what makes it bad", "×14" in found[0]["what"], True)
        check("asked repeatedly and ignored is blocking", found[0]["severity"], F.BLOCKING)
        F.scan()
        told = cab.sql("SELECT to_sector FROM requests WHERE from_sector='Overseer'")
        check("Content is asked once", sum(1 for t in told if t["to_sector"] == "Content"), 1)


def test_an_unanswered_request_says_which_command_answers_it():
    """
    He watched "Content has not answered SEO in 19 days" arrive on his phone
    for nineteen days. Every word of it was true. None of it told him what to
    do — and the answer was one command, which nothing anywhere named.

    A fault the owner cannot act on is not a warning, it is nagging.
    """
    print("\nThe fault names the command that clears it:")
    with _Cabinet() as cab:
        old = int(time.time()) - F.UNANSWERED_SECS - 600
        cab.write("INSERT INTO requests(id,from_sector,to_sector,subject,status,"
                  "created_at) VALUES('rq1','SEO','Content',"
                  "'Keyword-gap list ready','open',?)", (old,))
        found = [f for f in F.detect() if f["kind"] == "unanswered"]
        check("the fault is there", len(found), 1)
        check("and it carries the command, not just the complaint",
              "python -m pipelines.content_briefs" in found[0]["detail"], True)
        check("introduced as something to run",
              "To answer it now, run:" in found[0]["detail"], True)

    print("\n...and the command comes from the daily run, not a second list:")
    check("Content", F.how_to_run("Content"),
          "python -m pipelines.content_briefs")
    check("Security", F.how_to_run("Security"),
          "python -m pipelines.security_screen")
    check("a sector with several steps names them in order",
          F.how_to_run("SEO").startswith("python -m pipelines.seo_intel"), True)
    check("case does not matter", F.how_to_run("content"),
          "python -m pipelines.content_briefs")
    print("  ...and a sector nobody runs says nothing rather than guessing:")
    check("no invented command", F.how_to_run("Accounts"), "")
    check("nor for an empty one", F.how_to_run(""), "")


def test_a_request_claimed_and_never_answered_goes_back_in_the_inbox():
    """
    THE NINETEEN-DAY BUG, and it was worse than a slow sector.

    claim_request moves a request to 'claimed'. The receiving sector's own
    inbox, open_requests_for, lists status='open' only. The unanswered check
    counts 'open' AND 'claimed'. So the moment an agent claimed a request and
    died before replying, the request became INVISIBLE to the sector that owed
    the answer and permanently visible to the Overseer as that sector ignoring
    it.

    The owner watched both halves at once: "Content has not answered SEO in 19
    days" on his phone every morning, and content_briefs reporting — correctly,
    from what it could see — that there was nothing to claim. Neither was
    lying. Tasks were given a way out of this long ago; requests never were.
    """
    print("\nA request stuck at 'claimed' is invisible to the sector that owes it:")
    with _Cabinet() as cab:
        old = int(time.time()) - 19 * 86400
        cab.write("INSERT INTO requests(id,from_sector,to_sector,subject,status,"
                  "handler,created_at) VALUES('rq1','SEO','Content',"
                  "'Keyword-gap list ready','claimed','content-writer',?)", (old,))
        check("Content's inbox is empty",
              [r["id"] for r in board.open_requests_for("Content")], [])
        check("while the Overseer counts it against Content",
              any(f["kind"] == "unanswered" and f["sector"] == "Content"
                  for f in F.detect()), True)

        print("  ...so the Overseer puts it back where the sector can see it:")
        freed = board.release_dead_request_claims()
        check("one released", len(freed), 1)
        check("named", freed[0]["id"], "rq1")
        check("with how long it sat", freed[0]["stuck_for"] >= 19 * 86400, True)
        check("and Content can finally see it",
              [r["id"] for r in board.open_requests_for("Content")], ["rq1"])

    print("\n...but a handler that ANSWERED and left the thread open is finished:")
    with _Cabinet() as cab:
        old = int(time.time()) - 19 * 86400
        cab.write("INSERT INTO requests(id,from_sector,to_sector,subject,status,"
                  "handler,created_at) VALUES('rq2','SEO','Content','Done one',"
                  "'claimed','content-writer',?)", (old,))
        cab.write("INSERT INTO request_msgs(request_id,author,body,at)"
                  " VALUES('rq2','content-writer','here you go',?)",
                  (int(time.time()),))
        check("not yanked back off him", board.release_dead_request_claims(), [])

    print("\n...and an agent genuinely working right now is left alone:")
    with _Cabinet() as cab:
        cab.write("INSERT INTO requests(id,from_sector,to_sector,subject,status,"
                  "created_at) VALUES('rq3','SEO','Content','Fresh','open',?)",
                  (int(time.time()),))
        check("claimed just now", board.claim_request("rq3", "content-writer"), True)
        check("nothing released", board.release_dead_request_claims(), [])
        check("and it is still theirs",
              cab.sql("SELECT handler FROM requests WHERE id='rq3'")[0]["handler"],
              "content-writer")

    print("\n...the claim is timestamped, which is what makes any of this possible:")
    with _Cabinet() as cab:
        cab.write("INSERT INTO requests(id,from_sector,to_sector,subject,status,"
                  "created_at) VALUES('rq4','SEO','Content','X','open',?)",
                  (int(time.time()),))
        board.claim_request("rq4", "content-writer")
        row = cab.sql("SELECT claimed_at FROM requests WHERE id='rq4'")[0]
        check("recorded", bool(row["claimed_at"]), True)

    print("\n...and a cabinet older than the column still gets its rows back:")
    with _Cabinet() as cab:
        old = int(time.time()) - 19 * 86400
        # No claimed_at at all — which is exactly what every row claimed
        # before the column existed looks like after the migration.
        cab.write("INSERT INTO requests(id,from_sector,to_sector,subject,status,"
                  "handler,created_at) VALUES('rq5','SEO','Content',"
                  "'Old cabinet','claimed','content-writer',?)", (old,))
        check("falls back to when it was created",
              len(board.release_dead_request_claims()), 1)


def test_a_sector_falling_apart_is_raised_once():
    print("\nA sector with several faults gets ONE message about the pattern:")
    with _Cabinet() as cab:
        for a in ("seo-strategist", "seo-answers", "seo-lead"):
            _stall(cab, agent=a)
        F.scan()
        alarms = cab.sql("SELECT subject FROM requests WHERE to_sector='Director'"
                         " AND subject LIKE '%faults open at once%'")
        check("the pattern is raised once, not once per fault", len(alarms), 1)
        check("and it names the sector", "Seo" in alarms[0]["subject"], True)
        # This is the bug that shipped first: forty-six escalations for one
        # quiet sector. The MD's inbox has to stay readable.
        allmd = cab.sql("SELECT count(*) n FROM requests WHERE to_sector='Director'")[0]["n"]
        check("and the MD's inbox stays readable", allmd <= 4, True)


def test_a_fault_that_fixes_itself_closes_what_it_opened():
    print("\nWhen the agent comes back, the paperwork closes:")
    with _Cabinet() as cab:
        _stall(cab)
        F.scan()
        f = cab.sql("SELECT * FROM faults")[0]
        _recovered(cab)
        out = F.scan()
        check("the fault is closed", out["resolved"], 1)
        check("the sector's thread is closed too",
              cab.sql("SELECT status FROM requests WHERE id=?", (f["request_id"],))[0]["status"],
              "closed")
        check("and IT's ticket is withdrawn rather than left on its board",
              cab.sql("SELECT status FROM tasks WHERE id=?", (f["task_id"],))[0]["status"],
              "done")


def test_a_claimed_ticket_is_never_yanked():
    print("\nOnce IT has claimed it, the work is IT's:")
    with _Cabinet() as cab:
        _stall(cab)
        F.scan()
        f = cab.sql("SELECT * FROM faults")[0]
        board.claim_task(f["task_id"], "it-fixer")
        _recovered(cab)
        F.scan()
        t = cab.sql("SELECT status, owner, result FROM tasks WHERE id=?", (f["task_id"],))[0]
        check("the claim stands", (t["status"], t["owner"]), ("in_progress", "it-fixer"))
        check("but IT is told the reason has gone",
              "working again" in (t["result"] or ""), True)


def test_a_fault_that_comes_back_is_reported_again():
    print("\nA sector that goes quiet again is reported again:")
    with _Cabinet() as cab:
        old = int(time.time()) - F.UNANSWERED_SECS - 600
        cab.write("INSERT INTO requests(id,from_sector,to_sector,subject,status,created_at)"
                  " VALUES('q1','SEO','Content','Worth writing?','open',?)", (old,))
        F.scan()
        cab.write("UPDATE requests SET status='closed' WHERE id='q1'")
        F.scan()
        check("closed once answered",
              cab.sql("SELECT status FROM faults")[0]["status"], "resolved")
        # Asked again, ignored again — the resolved row must not swallow it.
        cab.write("INSERT INTO requests(id,from_sector,to_sector,subject,status,created_at)"
                  " VALUES('q2','SEO','Content','Worth writing?','open',?)", (old,))
        out = F.scan()
        check("it reopens rather than being swallowed by the closed one", out["filed"], 1)
        row = cab.sql("SELECT status, resolution FROM faults")[0]
        check("and it is open again", row["status"] in ("open", "routed", "escalated"), True)
        check("and says it came back", "came back" in (row["resolution"] or ""), True)


def test_an_abandoned_task_goes_to_its_own_sector_not_it():
    print("\nA sector sitting on its own claimed task does not need IT:")
    with _Cabinet() as cab:
        old = int(time.time()) - F.ABANDONED_SECS - 600
        cab.write("INSERT INTO tasks(id,sector,title,status,owner,created_at,claimed_at)"
                  " VALUES('t1','Leads','Mail the cleared firms','in_progress',"
                  "'leads-outreach',?,?)", (old, old))
        F.scan()
        check("Leads is told", cab.sql("SELECT to_sector FROM requests"
                                       " WHERE from_sector='Overseer'")[0]["to_sector"], "Leads")
        check("and IT is given nothing, because there is no code to fix",
              cab.sql("SELECT count(*) n FROM tasks WHERE created_by='Overseer'")[0]["n"], 0)


def test_a_dead_claim_is_put_back_and_a_live_one_is_not():
    """
    The hole the abandoned fault has been reporting since it was written.

    A claimed task belongs to one agent and nobody else may touch it — that is
    the rule that stops two agents doing one job, and it must not weaken. But
    if the holder never comes back the task was held FOR EVER: withdraw_task
    refuses a claimed task, finish_task only accepts the owner, and a desk with
    no pipeline behind it will never be either. Two were on the owner's live
    board, one for a week.

    So: a dead claim goes back on the board. A LIVE one is still untouchable,
    and that is what most of this test is about.
    """
    print("\nA claim nobody is honouring goes back on the board:")
    with _Cabinet() as cab:
        old = int(time.time()) - F.ABANDONED_SECS - 600
        cab.write("INSERT INTO tasks(id,sector,title,status,owner,created_at,claimed_at)"
                  " VALUES('d1','Leads','Mail the cleared firms','in_progress',"
                  "'leads-outreach',?,?)", (old, old))
        F.scan()
        row = cab.sql("SELECT status, owner FROM tasks WHERE id='d1'")[0]
        check("it is open again", row["status"], "open")
        check("and unowned", row["owner"], None)
        check("with the reason written on it, not overwritten",
              "claim released" in (cab.sql("SELECT result FROM tasks WHERE id='d1'")[0]
                                   ["result"] or ""), True)
        check("the sector was still told first",
              bool(cab.sql("SELECT 1 FROM requests WHERE to_sector='Leads'")), True)

    print("\n...but a desk that is merely slow keeps its claim:")
    with _Cabinet() as cab:
        old = int(time.time()) - F.ABANDONED_SECS - 600
        cab.write("INSERT INTO tasks(id,sector,title,status,owner,created_at,claimed_at)"
                  " VALUES('d2','SEO','A long audit','in_progress','seo-answers',?,?)",
                  (old, old))
        # it HAS been recording work since it claimed — slow, not dead
        cab.write("INSERT INTO agent_runs(agent,task_id,started_at,ended_at,status)"
                  " VALUES('seo-answers','d2',?,?,'ok')", (old + 300, old + 400))
        F.scan()
        check("the claim is left alone",
              cab.sql("SELECT status FROM tasks WHERE id='d2'")[0]["status"],
              "in_progress")
        ok, why = board.release_claim("d2", "somebody-else")
        check("and reaching in is refused", ok, False)
        check("with the reason", "slow, not dead" in why, True)

    print("\n...and a fresh claim is nobody else's business at all:")
    with _Cabinet() as cab:
        now = int(time.time())
        cab.write("INSERT INTO tasks(id,sector,title,status,owner,created_at,claimed_at)"
                  " VALUES('d3','IT','Check the site','in_progress','it-watcher',?,?)",
                  (now, now))
        ok, why = board.release_claim("d3", "it-fixer")
        check("refused", ok, False)
        check("because a live claim is exclusive", "exclusive while it is live" in why, True)
        ok, _ = board.release_claim("d3", "it-watcher")
        check("but its own desk may always hand it back", ok, True)
        check("and then anyone can take it",
              board.claim_task("d3", "it-search"), True)

    print("\n...and the owner can force one, on the record:")
    with _Cabinet() as cab:
        now = int(time.time())
        cab.write("INSERT INTO tasks(id,sector,title,status,owner,created_at,claimed_at)"
                  " VALUES('d4','IT','Check the site','in_progress','it-watcher',?,?)",
                  (now, now))
        ok, _ = board.release_claim("d4", "owner", reason="I need this back", force=True)
        check("forced through", ok, True)
        check("and recorded as forced, not passed off as ordinary",
              "forced by the owner" in
              (cab.sql("SELECT result FROM tasks WHERE id='d4'")[0]["result"] or ""),
              True)


def test_work_nobody_picks_up_is_a_fault_too():
    """
    The failure that routing work to a named desk introduces.

    Sending a fix to it-fixer is right — the Watcher cannot write one. But if
    no pipeline runs as it-fixer, that task waits for ever and the board looks
    calm rather than stuck, which is the exact shape of the problem the owner
    kept catching by eye. It has to raise a fault on its own.
    """
    print("\nA task nobody ever claims is not a quiet board, it is a stuck one:")
    with _Cabinet() as cab:
        old = int(time.time()) - F.UNCLAIMED_SECS - 600
        cab.write("INSERT INTO tasks(id,sector,title,status,desk,created_at)"
                  " VALUES('u1','IT','Fix the contact form','open','it-fixer',?)",
                  (old,))
        found = [f for f in F.detect() if f["kind"] == "unclaimed"]
        check("the Overseer sees it", len(found), 1)
        check("and names the desk it is waiting on", "it-fixer" in found[0]["detail"], True)
        F.scan()
        check("the sector that owns the board is told",
              cab.sql("SELECT to_sector FROM requests WHERE from_sector='Overseer'"
                      " AND subject LIKE '%picking up%'")[0]["to_sector"], "IT")

        print("  ...and a whole stuck board is ONE ticket, not one per task:")
        cab.write("INSERT INTO tasks(id,sector,title,status,desk,created_at)"
                  " VALUES('u1b','IT','Fix the pricing table','open','it-fixer',?)",
                  (old,))
        found = [f for f in F.detect() if f["kind"] == "unclaimed"]
        check("still one fault", len(found), 1)
        check("but it says how much is stuck", "2 task(s)" in found[0]["what"], True)

        print("  ...and it closes the moment the board is being worked again:")
        cab.write("UPDATE tasks SET status='in_progress', owner='it-fixer'"
                  " WHERE id IN ('u1','u1b')")
        F.scan()
        check("resolved without the work having to be finished",
              cab.sql("SELECT status FROM faults WHERE kind='unclaimed'")[0]["status"],
              "resolved")

    print("\n...but a task posted this morning is just a task:")
    with _Cabinet() as cab:
        cab.write("INSERT INTO tasks(id,sector,title,status,created_at)"
                  " VALUES('u2','IT','Check the site','open',?)",
                  (int(time.time()) - 3600,))
        check("no fault for an hour-old task",
              [f for f in F.detect() if f["kind"] == "unclaimed"], [])


def test_a_dead_run_stops_claiming_to_be_running():
    print("\nA dead run's claim to be working is retracted, not left standing:")
    from . import activity
    with _Cabinet() as cab:
        _stall(cab)
        said = [e["what"] for e in activity.feed()["events"] if "respond" in e["what"]]
        check("before the sweep it reads as an open problem", bool(said), True)

        F.scan()
        run = cab.sql("SELECT status, ended_at, note FROM agent_runs")[0]
        check("the Overseer marks the run dead", run["status"], "stalled")
        check("with an ending time, so its age stops growing", bool(run["ended_at"]), True)
        check("and says who declared it", "Overseer" in (run["note"] or ""), True)

        # This is the bit the owner watched go wrong: the log kept saying "never
        # came back" in the present tense, recomputed from now(), for a desk
        # that had recovered hours earlier.
        ev = [e for e in activity.feed()["events"] if e["who"] == "seo-strategist"]
        check("the log now reports it in the past tense",
              any("was declared dead" in e["what"] for e in ev), True)
        check("and no longer says it is still running",
              any("never came back" in e["what"] for e in ev), False)

        # Reaping is the Overseer disbelieving the claim, NOT the fault being
        # fixed. Nobody has touched the cause.
        check("the fault stays open",
              cab.sql("SELECT status FROM faults")[0]["status"] != "resolved", True)
        check("and a second sweep does not re-file it", F.scan()["filed"], 0)


def test_a_run_is_only_reaped_after_somebody_is_told():
    print("\nNothing is swept up before it has been reported:")
    with _Cabinet() as cab:
        _stall(cab)
        # detect() alone must not touch the row — it is the read-only pass, and
        # erasing the evidence before anyone is told would be the worst order.
        F.detect()
        check("detect() changes nothing",
              cab.sql("SELECT status FROM agent_runs")[0]["status"], "running")
        F.scan()
        check("scan() reaps it only after routing",
              cab.sql("SELECT status FROM agent_runs")[0]["status"], "stalled")
        check("and the sector was told first",
              bool(cab.sql("SELECT 1 FROM requests WHERE to_sector='SEO'")), True)


def test_nothing_broken_says_nothing():
    print("\nA healthy agency is quiet:")
    with _Cabinet() as cab:
        out = F.scan()
        check("no faults", out["found"], 0)
        check("no messages", cab.sql("SELECT count(*) n FROM requests")[0]["n"], 0)
        check("and it says so plainly", F.summary()["head"], "nothing broken")


def main():
    for t in (test_a_stall_is_found_and_given_to_somebody,
              test_a_desk_that_is_merely_slow_is_left_alone,
              test_the_same_fault_is_not_filed_twice,
              test_a_blocking_fault_reaches_the_md,
              test_a_degraded_fault_waits_before_escalating,
              test_one_silent_sector_is_one_message,
              test_an_unanswered_request_says_which_command_answers_it,
              test_a_request_claimed_and_never_answered_goes_back_in_the_inbox,
              test_a_sector_falling_apart_is_raised_once,
              test_a_fault_that_fixes_itself_closes_what_it_opened,
              test_a_claimed_ticket_is_never_yanked,
              test_a_fault_that_comes_back_is_reported_again,
              test_an_abandoned_task_goes_to_its_own_sector_not_it,
              test_a_dead_claim_is_put_back_and_a_live_one_is_not,
              test_work_nobody_picks_up_is_a_fault_too,
              test_a_dead_run_stops_claiming_to_be_running,
              test_a_run_is_only_reaped_after_somebody_is_told,
              test_nothing_broken_says_nothing):
        t()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
    else:
        print("all passed")
    return not _fail


if __name__ == "__main__":
    import sys
    sys.exit(0 if main() else 1)
