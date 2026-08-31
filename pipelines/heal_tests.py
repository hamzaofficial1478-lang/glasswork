"""
heal_tests.py — run: python -m pipelines.heal_tests

"there were 2 agents in Seo Sector blinking red the IT sector should at leat
 fix that or ask to MD for it but all they sitting blindly and quietly just
 showing the error why not fixing while the fixer is there"

and

"if an agent is working let it work do not put or ask after each secod the same
 question to put more load on that agent"

Two halves of one idea: act on what is broken, and leave alone what is working.

What has to hold:

    a passing failure (timeout, 429)      cleared, lamp goes out
    a missing key                         put to HIM, naming the setting
    a real code bug                       put to the MD, with the error
    a lock nobody holds                   released
    a claim nobody is working             handed back
    a claim somebody IS working           left completely alone
    a desk mid-run with work queued       not given a third job

Throwaway cabinet, no network.
"""
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

from coordination import board, faults
from . import it_selfheal as heal

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
    con.executescript((Path(__file__).parents[1] / "coordination"
                       / "schema.sql").read_text(encoding="utf-8"))
    con.commit()
    con.close()
    board.DB = db
    board.ensure_schema()
    return db


def _crash(db, agent, note, ago=600):
    now = int(time.time())
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO agent_runs(agent,status,note,started_at,ended_at)"
                  " VALUES(?,'error',?,?,?)", (agent, note, now - ago, now - ago + 5))


def _q(db, sql, args=()):
    with sqlite3.connect(db) as c:
        return c.execute(sql, args).fetchall()


# ---------------------------------------------------------------------------

def test_it_reads_a_crash_and_decides_what_kind_it_is():
    print("\nThree crashes, three different answers:")
    db = _fresh_db()
    _crash(db, "seo-strategist", "read timed out after 120s")
    _crash(db, "seo-backlinks", "GOOGLE_PLACES_API_KEY is not set")
    _crash(db, "content-writer", "KeyError: primary_keyword in make_brief")
    faults.scan()

    con = heal._conn()
    try:
        plans = {f["agent"]: heal.plan_for(f, con) for f in heal.open_faults(con)}
    finally:
        con.close()
    check("a timeout is something to retry, not to fix",
          plans["seo-strategist"]["do"], "retry")
    check("a missing key goes to him", plans["seo-backlinks"]["do"], "ask_owner")
    check("and it names the setting by name",
          plans["seo-backlinks"]["setting"], "GOOGLE_PLACES_API_KEY")
    check("a real bug goes to the MD", plans["content-writer"]["do"], "ask_md")


def test_it_actually_does_it_rather_than_reporting():
    """
    The whole complaint. The Fixer had the ticket and every recipe it owned was
    a website recipe, so it said 'no recipe' and the lamp stayed red.
    """
    print("\nAnd it carries them out:")
    db = _fresh_db()
    _crash(db, "seo-strategist", "read timed out after 120s")
    _crash(db, "seo-backlinks", "SMTP_HOST is not set")
    _crash(db, "content-writer", "TypeError: NoneType is not subscriptable")
    faults.scan()

    r = heal.run()
    check("it repaired what it could", r["repaired"], 1)
    check("and passed on what it could not", r["escalated"], 2)

    check("the transient fault is closed",
          _q(db, "SELECT count(*) FROM faults WHERE status='resolved'")[0][0], 1)
    check("he is asked for the setting, in amber",
          [t[0] for t in _q(db, "SELECT title FROM tasks WHERE risk='amber'")],
          ["You need to set SMTP_HOST"])
    asked = [x[0] for x in _q(db, "SELECT subject FROM requests"
                                  " WHERE to_sector='Director'")]
    check("and the MD is given the one nobody else can judge", len(asked), 1)
    check("with the error in the subject", "TypeError" in asked[0], True)


def test_a_lock_nobody_holds_is_released():
    print("\nA shelf locked by a run that is long gone:")
    db = _fresh_db()
    now = int(time.time())
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO locks(resource,holder,acquired_at,expires_at,pid)"
                  " VALUES('shelf:seo_data','seo-strategist',?,?,?)",
                  (now - 9000, now - 100, 999999))
    plan = {"do": "release_lock", "resource": "shelf:seo_data",
            "holder": "seo-strategist", "why": "test"}
    got = heal.carry_out(plan, {"what": "lock"}, dry_run=False)
    check("it is released", got["done"], True)
    check("and the shelf is free",
          _q(db, "SELECT count(*) FROM locks WHERE resource='shelf:seo_data'")[0][0], 0)

    print("\n...and a lock the fault cannot name is left alone:")
    got = heal.carry_out({"do": "release_lock", "resource": "", "why": "x"},
                         {"what": "lock"})
    check("nothing guessed", got["done"], False)
    check("and it says why", "guessed lock" in got["said"], True)


def test_a_desk_that_is_working_is_left_completely_alone():
    """
    The dangerous one, and the reason the liveness check had to go in before
    the repair did: the fix for 'abandoned' is to TAKE THE CLAIM AWAY. Doing
    that to a desk running the test suite would be the agency breaking itself.
    """
    print("\nA long job in progress is not an abandoned one:")
    db = _fresh_db()
    now = int(time.time())
    tid = board.post_task("IT", "Deploy what I approved", "", created_by="MD", force=True)
    board.claim_task(tid, "it-releaser")
    with sqlite3.connect(db) as c:
        c.execute("UPDATE tasks SET claimed_at=? WHERE id=?", (now - 4 * 86400, tid))
        # it IS working — a live run started after it took the claim
        c.execute("INSERT INTO agent_runs(agent,status,note,started_at)"
                  " VALUES('it-releaser','running','running the test suite',?)",
                  (now - 200,))
    faults.scan()
    filed = [f[0] for f in _q(db, "SELECT kind FROM faults")]
    check("no abandoned fault is filed against it", "abandoned" in filed, False)
    check("and the claim is untouched",
          _q(db, "SELECT status FROM tasks WHERE id=?", (tid,))[0][0], "in_progress")

    print("\n...but one with nothing running behind it is handed back:")
    db = _fresh_db()
    tid = board.post_task("SEO", "Claim 4 free listings", "", created_by="MD", force=True)
    board.claim_task(tid, "seo-backlinks")
    with sqlite3.connect(db) as c:
        c.execute("UPDATE tasks SET claimed_at=? WHERE id=?", (now - 4 * 86400, tid))
    faults.scan()
    check("that one IS filed",
          "abandoned" in [f[0] for f in _q(db, "SELECT kind FROM faults")], True)
    heal.run()
    check("and it goes back on the board",
          _q(db, "SELECT status FROM tasks WHERE id=?", (tid,))[0][0], "open")


def test_a_busy_desk_is_not_handed_a_third_job():
    print("\nOne job at a time:")
    db = _fresh_db()
    now = int(time.time())
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO agent_runs(agent,status,note,started_at)"
                  " VALUES('leads-hunter','running','collecting',?)", (now - 60,))
    check("the desk reads as working",
          bool(board.desk_at_work("leads-hunter")), True)

    first = board.post_task("Leads", "Find 20 leads in Al Quoz", "",
                            created_by="MD", desk="leads-hunter")
    check("the first job still goes on — it will pick it up when it finishes",
          bool(first), True)
    try:
        board.post_task("Leads", "Find 20 leads in Deira", "",
                        created_by="MD", desk="leads-hunter")
        check("the second is held back", False, True)
    except board.DeskBusy as e:
        check("the second is held back", True, True)
        check("and says why", "mid-run" in str(e), True)

    print("\n...and the Overseer's fault tickets still get through:")
    forced = board.post_task("Leads", "Fix: leads-hunter crashed", "",
                             created_by="Overseer", desk="leads-hunter", force=True)
    check("force is not blocked by a busy desk", bool(forced), True)

    print("\n...and a desk that has finished takes work again:")
    with sqlite3.connect(db) as c:
        c.execute("UPDATE agent_runs SET status='ok', ended_at=?", (now,))
    check("no longer at work", board.desk_at_work("leads-hunter"), None)
    check("so it is given the job",
          bool(board.post_task("Leads", "Find 20 leads in Deira", "",
                               created_by="MD", desk="leads-hunter")), True)


def test_a_corpse_is_not_mistaken_for_a_busy_desk():
    """
    The opposite failure: a row left at 'running' by a killed process would
    hold work back for ever if it counted as a working desk.
    """
    print("\nA run left 'running' by a killed process is not a busy desk:")
    db = _fresh_db()
    old = int(time.time()) - board.STILL_WORKING - 60
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO agent_runs(agent,status,note,started_at)"
                  " VALUES('leads-hunter','running','died here',?)", (old,))
    check("it does not count as at work", board.desk_at_work("leads-hunter"), None)
    check("so work reaches the desk",
          bool(board.post_task("Leads", "Find leads", "", created_by="MD",
                               desk="leads-hunter")), True)


def test_nothing_broken_is_not_an_error():
    print("\nA healthy agency:")
    _fresh_db()
    r = heal.run()
    check("it does not fail", r.get("idle"), True)
    check("and says what this desk is for",
          "repairs the agency itself" in r["error"], True)


def main():
    real = board.DB
    _fresh_db()
    try:
        test_it_reads_a_crash_and_decides_what_kind_it_is()
        test_it_actually_does_it_rather_than_reporting()
        test_a_lock_nobody_holds_is_released()
        test_a_desk_that_is_working_is_left_completely_alone()
        test_a_busy_desk_is_not_handed_a_third_job()
        test_a_corpse_is_not_mistaken_for_a_busy_desk()
        test_nothing_broken_is_not_an_error()
    finally:
        board.DB = real

    print()
    if _fail:
        print(f"{len(_fail)} FAILED: {_fail}")
        sys.exit(1)
    print("all self-repair tests passed")


if __name__ == "__main__":
    main()
