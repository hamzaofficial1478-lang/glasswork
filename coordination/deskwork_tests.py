"""
deskwork_tests.py — run: python -m coordination.deskwork_tests

"in full room control there is still showing Director · 24 tasks on the board.
 each sector is still holding i had listed all the unassigned tasks"

Eleven of seventeen daily pipelines never called the board at all. The obvious
repair — bolt claiming onto all eleven — recreates a worse bug he already
caught once: a desk that claims a job, runs its usual pass and marks it done
without reading a word of it.

So the tests that matter here are not "does it claim". They are:

    does a pass that produced nothing get away with saying done?     no
    does a crash halfway through leave the job looking finished?     no
    does a job that cannot be done say the ACTUAL reason?            yes
    does the desk read what was asked — how many, where, what?       yes
    does a clean pass that genuinely found nothing close honestly?   yes
    can the same job be handed round for ever?                       no

Throwaway cabinet, no network.
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

from . import board, deskwork

_fail = []

SCHEMA = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


def check_in(name, needle, hay):
    if needle.lower() in (hay or "").lower():
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        {needle!r} not in {hay!r}")
        _fail.append(name)


def _fresh_db():
    d = Path(tempfile.mkdtemp())
    db = d / "agency.db"
    con = sqlite3.connect(db)
    con.executescript(SCHEMA)
    con.commit()
    con.close()
    board.DB = db
    board.ensure_schema()
    return db


def _post(sector, title, detail=""):
    return board.post_task(sector, title, detail, created_by="MD", force=True)


def _row(task_id):
    with sqlite3.connect(board.DB) as c:
        c.row_factory = sqlite3.Row
        return dict(c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone())


# ---------------------------------------------------------------------------

def test_a_pass_that_did_nothing_does_not_get_to_say_done():
    """
    The bug he caught, made impossible. A desk claims, runs, and never records
    an answer — through a branch nobody thought about, or plain forgetfulness.
    The job must come back to the board, not close green.
    """
    print("\nClaimed, ran, answered nothing:")
    _fresh_db()
    tid = _post("Leads", "Find 20 leads in Dubai")

    with deskwork.at_the_desk("Leads", "leads-hunter") as job:
        check("the desk got the job", job.id, tid)
        pass                                   # the whole point: no job.did()

    r = _row(tid)
    check("it is open again, not done", r["status"], "open")
    check("and nobody owns it", r["owner"], None)
    check_in("the row says what happened", "produced nothing", r["result"])
    check_in("and names the desk", "leads-hunter", r["result"])


def test_a_crash_halfway_through_is_not_a_finished_job():
    print("\nThe pass blows up mid-way:")
    _fresh_db()
    tid = _post("SEO", "Find the gaps for pergolas")

    try:
        with deskwork.at_the_desk("SEO", "seo-strategist") as job:
            job.did("about to claim success")   # even having said done
            raise RuntimeError("the shelf key expired")
    except RuntimeError:
        pass

    r = _row(tid)
    check("the exception wins over the optimism", r["status"], "open")
    check_in("and the real error is on the row", "shelf key expired", r["result"])
    check_in("named as what it was", "RuntimeError", r["result"])


def test_a_job_that_cannot_be_done_says_why_in_words():
    """
    His words: "FOR EACH SECTOR DETAILS THAT IS FAILED IT SHOWS SOMETHING IS
    HOLDING IT BACK. while the program should show the actaull error."
    """
    print("\nBlocked, with the actual reason:")
    _fresh_db()
    tid = _post("Leads", "Find 20 leads in Dubai")

    with deskwork.at_the_desk("Leads", "leads-hunter") as job:
        job.cannot("No Google Places key on the maps shelf, so nothing could "
                   "be searched for.")

    r = _row(tid)
    check("it is blocked, not open and not done", r["status"], "blocked")
    check_in("and it names the actual obstacle", "Places key", r["result"])
    check_in("in plain words", "maps shelf", r["result"])


def test_the_desk_reads_what_was_actually_asked_for():
    print("\nHow many, where, and about what:")
    _fresh_db()
    _post("Leads", "Find 20 leads in Al Quoz",
          "Fit-out contractors who buy shower glass. Reply within 24 hours.")

    with deskwork.at_the_desk("Leads", "leads-hunter") as job:
        check("the count comes off the job", job.how_many(default=6), 20)
        check("'24 hours' is a deadline, not a count",
              20 in [job.how_many(default=6)], True)
        check("the area comes off the job",
              job.first_mention(["Al Quoz", "Deira", "Dubai Marina"]), "Al Quoz")
        check("a word it does not name is not invented",
              job.first_mention(["Sharjah", "Ajman"]), None)
        check("whole words only — 'add' must not match 'address'",
              job.wants("add"), False)
        check("and a word it does name is found", job.wants("contractors"), True)
        check("the subject words come back without the filler",
              [w for w in job.words() if w in ("leads", "quoz", "contractors",
                                               "shower", "glass")],
              ["leads", "quoz", "contractors", "shower", "glass"])
        job.did("read it")

    print("\n...and a counted job is read for what is LEFT, not what is done:")
    _fresh_db()
    _post("Leads", "Collect today's leads across the mapped segments",
          "Work the source map.\n\nWhere this stands: 5 done today of 20 asked "
          "for, so 15 still to go. Yesterday: 3. On file altogether: 40.")
    with deskwork.at_the_desk("Leads", "leads-hunter") as job:
        check("fifteen still to go, not the five already done",
              job.how_many(default=6), 15)
        job.did("read it")


def test_a_clean_pass_that_found_nothing_closes_honestly():
    """
    Not every empty pass is a failure. "Go and look" answered by "I looked and
    there was nothing" IS the answer — and leaving it open for ever is how the
    board filled up. But it is never dressed up as a win.
    """
    print("\nRan, looked, found nothing:")
    _fresh_db()
    tid = _post("SEO", "Check for new link opportunities")

    with deskwork.at_the_desk("SEO", "seo-backlinks") as job:
        deskwork.settle(job, {"ok": True, "summary": "no new hosts since Tuesday"},
                        made=0, what="link opportunity")

    r = _row(tid)
    check("closed, because the looking was the job", r["status"], "done")
    check_in("and it says plainly that nothing came of it",
             "produced no link opportunities", r["result"])
    check_in("with the pass's own words kept", "since Tuesday", r["result"])


def test_settle_puts_a_real_error_through_untouched():
    print("\nA failed pass carries its own error, not a euphemism:")
    _fresh_db()
    tid = _post("IT", "Deploy the approved domain fix")

    with deskwork.at_the_desk("IT", "it-releaser") as job:
        deskwork.settle(job, {"ok": False, "error":
                              "Nothing to put on the site — 3 change(s) are "
                              "still waiting for your yes or no in the office."})

    r = _row(tid)
    check("blocked", r["status"], "blocked")
    check_in("and the sentence survives whole", "waiting for your yes or no",
             r["result"])


def test_a_job_is_not_handed_round_for_ever():
    print("\nThree passes with the same result is not new information:")
    _fresh_db()
    tid = _post("Report", "Score yesterday's decisions")

    for i in range(deskwork.GIVE_UP_AFTER):
        with deskwork.at_the_desk("Report", "reporter"):
            pass
        r = _row(tid)
        if i < deskwork.GIVE_UP_AFTER - 1:
            check(f"pass {i + 1}: back on the board", r["status"], "open")

    r = _row(tid)
    check(f"pass {deskwork.GIVE_UP_AFTER}: it stops going round", r["status"],
          "blocked")
    check_in("and says so", "needs a person", r["result"])


def test_a_desk_hands_back_work_that_is_not_its_pass():
    """
    Two pipelines run as it-watcher. Routing cannot tell them apart, so the
    change-watcher could claim "check the contact form" and close it having
    diffed some fingerprints. `takes` is the guard, and a routing miss must not
    count against the job.
    """
    print("\nOne desk name, two passes:")
    _fresh_db()
    tid = _post("IT", "Check the contact form still works")

    with deskwork.at_the_desk("IT", "it-watcher",
                              takes=lambda j: j.wants("changed", "watch")) as job:
        check("the change-watcher does not take a health job",
              bool(job), False)

    r = _row(tid)
    check("it is straight back on the board", r["status"], "open")
    check("still nobody's", r["owner"], None)
    check("and it did not count as a failed attempt",
          str(r["result"] or "").count("handed back"), 0)

    print("\n...and the pass that DOES do it takes it:")
    tid2 = _post("IT", "Tell me what changed on the site")
    with deskwork.at_the_desk("IT", "it-watcher",
                              takes=lambda j: j.wants("changed", "watch")) as job:
        check("this one is its own", job.id, tid2)
        job.did("nothing moved since the last look")
    check("and closes", _row(tid2)["status"], "done")


def test_an_empty_board_is_not_an_error():
    print("\nNothing to claim:")
    _fresh_db()
    with deskwork.at_the_desk("Content", "content-writer") as job:
        check("falsy, so a pass can carry on", bool(job), False)
        check("reading it is safe", job.how_many(default=3), 3)
        check("as is asking what it wants", job.wants("anything"), False)
        check_in("and it says why, in a sentence",
                 "board", job.why_nothing)
        deskwork.settle(job, {"ok": True}, made=0, what="brief")   # no-op


def test_the_eleven_pipelines_actually_call_it():
    """
    The list is the point of the change. A pipeline quietly dropping its claim
    again is the regression this file exists to catch.
    """
    print("\nEvery desk that runs in a day now works from the board:")
    root = Path(__file__).parent.parent / "pipelines"
    should = ["it_release", "site_search", "site_watch", "leads_hunt",
              "seo_intel", "seo_gap", "seo_backlinks", "seo_answers",
              "security_screen", "content_briefs", "report_daily"]
    for name in should:
        src = (root / f"{name}.py").read_text(encoding="utf-8")
        check(f"{name} claims", "at_the_desk(" in src, True)
    # and the six that already did are untouched
    for name in ["it_health", "it_fix", "leads_osm", "leads_outreach",
                 "leads_sources", "competitors"]:
        src = (root / f"{name}.py").read_text(encoding="utf-8")
        check(f"{name} still claims", "next_open_task(" in src, True)


def main():
    real = board.DB
    _fresh_db()
    try:
        test_a_pass_that_did_nothing_does_not_get_to_say_done()
        test_a_crash_halfway_through_is_not_a_finished_job()
        test_a_job_that_cannot_be_done_says_why_in_words()
        test_the_desk_reads_what_was_actually_asked_for()
        test_a_clean_pass_that_found_nothing_closes_honestly()
        test_settle_puts_a_real_error_through_untouched()
        test_a_job_is_not_handed_round_for_ever()
        test_a_desk_hands_back_work_that_is_not_its_pass()
        test_an_empty_board_is_not_an_error()
        test_the_eleven_pipelines_actually_call_it()
    finally:
        board.DB = real

    print()
    if _fail:
        print(f"{len(_fail)} FAILED: {_fail}")
        sys.exit(1)
    print("all deskwork tests passed")


if __name__ == "__main__":
    main()
