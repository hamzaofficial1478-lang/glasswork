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
    con.executescript((ROOT / "coordination" / "schema.sql").read_text())
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
        cab.write("UPDATE agent_runs SET status='ok', ended_at=? WHERE status='running'",
                  (int(time.time()),))
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
        cab.write("UPDATE agent_runs SET status='ok', ended_at=? WHERE status='running'",
                  (int(time.time()),))
        F.scan()
        t = cab.sql("SELECT status, owner, result FROM tasks WHERE id=?", (f["task_id"],))[0]
        check("the claim stands", (t["status"], t["owner"]), ("in_progress", "it-fixer"))
        check("but IT is told the reason has gone", "cleared" in (t["result"] or ""), True)


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
              test_a_sector_falling_apart_is_raised_once,
              test_a_fault_that_fixes_itself_closes_what_it_opened,
              test_a_claimed_ticket_is_never_yanked,
              test_a_fault_that_comes_back_is_reported_again,
              test_an_abandoned_task_goes_to_its_own_sector_not_it,
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
