"""
targets_tests.py — run: python -m coordination.targets_tests

Two things are being protected here.

The first is that progress is MEASURED. The whole point of a target is that the
agency cannot mark its own homework, so every test below puts real rows in a
throwaway cabinet and checks the count comes back from those rows rather than
from anything an agent claimed.

The second is the outreach ceiling. It is the one number in this file that can
do permanent damage — volume from a young domain gets it filed as spam for good
— so there are tests that it warns, tests that it still lets the owner through
because he is the tie-breaker, and a test that the warning survives all the way
into the MD's briefing rather than being swallowed at the UI.
"""
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

from . import targets as tg

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


def _job(s, name):
    return next(j for j in s["jobs"] if j["name"] == name)


def test_nothing_set_says_so():
    print("An unset agency admits it:")
    real = tg.DB
    tg.DB = _fresh_db()
    try:
        s = tg.status()
        check("it reads clean", s["ok"], True)
        check("no target is set", s["any_target_set"], False)
        check("every job is at zero", [j["per_day"] for j in s["jobs"]], [0, 0, 0, 0])
        check("and it suggests a starting point",
              [j["suggested"] for j in s["jobs"]], [20, 10, 1, 2])
        check("the text tells you what to do",
              "No targets set" in tg.as_text(s), True)
    finally:
        tg.DB = real


def test_progress_is_measured_not_claimed():
    """
    The property that makes a target worth anything: the number comes from rows
    that only exist because the work happened.
    """
    print("Progress is counted from the cabinet:")
    real = tg.DB
    tg.DB = _fresh_db()
    try:
        tg.set_target("leads", 3)
        check("nothing done yet", _job(tg.status(), "leads")["done_today"], 0)

        now = int(time.time())
        con = sqlite3.connect(tg.DB)
        for n in ("A Co", "B Co"):
            con.execute("INSERT INTO leads(company,found_at) VALUES(?,?)", (n, now))
        con.commit()
        con.close()

        s = tg.status()
        check("two real leads counted", _job(s, "leads")["done_today"], 2)
        check("one to go", _job(s, "leads")["remaining"], 1)
        check("not hit yet", _job(s, "leads")["hit"], False)
        check("percentage is honest", _job(s, "leads")["pct"], 67)

        con = sqlite3.connect(tg.DB)
        con.execute("INSERT INTO leads(company,found_at) VALUES('C Co',?)", (now,))
        con.commit()
        con.close()
        check("target hit", _job(tg.status(), "leads")["hit"], True)
    finally:
        tg.DB = real


def test_yesterdays_work_is_not_todays():
    print("Yesterday does not count towards today:")
    real = tg.DB
    tg.DB = _fresh_db()
    try:
        tg.set_target("leads", 5)
        yesterday = tg._day_start() - 3600
        con = sqlite3.connect(tg.DB)
        con.execute("INSERT INTO leads(company,found_at) VALUES('Old Co',?)", (yesterday,))
        con.commit()
        con.close()
        check("a lead from before midnight is not counted",
              _job(tg.status(), "leads")["done_today"], 0)
    finally:
        tg.DB = real


def test_each_job_counts_the_right_thing():
    print("Each job counts its own evidence:")
    real = tg.DB
    tg.DB = _fresh_db()
    try:
        for k in ("leads", "mails", "posts", "site_checks"):
            tg.set_target(k, 1)
        now = int(time.time())
        con = sqlite3.connect(tg.DB)
        con.execute("INSERT INTO leads(company,found_at) VALUES('L',?)", (now,))
        con.execute("INSERT INTO outreach_log(direction,at) VALUES('sent',?)", (now,))
        # a reply is not something we did — it must not count towards mails
        con.execute("INSERT INTO outreach_log(direction,at) VALUES('reply',?)", (now,))
        con.execute("INSERT INTO content_queue(title,status,created_at)"
                    " VALUES('P','published',?)", (now,))
        # a brief that never got published is not a post
        con.execute("INSERT INTO content_queue(title,status,created_at)"
                    " VALUES('Q','briefed',?)", (now,))
        con.execute("INSERT INTO agent_runs(agent,status,started_at)"
                    " VALUES('it-watcher','ok',?)", (now,))
        # a failed run is not a completed check
        con.execute("INSERT INTO agent_runs(agent,status,started_at)"
                    " VALUES('it-watcher','error',?)", (now,))
        # nor is another sector's run
        con.execute("INSERT INTO agent_runs(agent,status,started_at)"
                    " VALUES('leads-hunter','ok',?)", (now,))
        con.commit()
        con.close()

        s = tg.status()
        check("leads counts leads", _job(s, "leads")["done_today"], 1)
        check("a reply is not an outreach mail", _job(s, "mails")["done_today"], 1)
        check("an unpublished brief is not a post", _job(s, "posts")["done_today"], 1)
        check("a failed run is not a completed check",
              _job(s, "site_checks")["done_today"], 1)
        check("all four hit", s["all_hit"], True)
    finally:
        tg.DB = real


def test_the_outreach_ceiling():
    """
    The owner can go over it — the handbook makes him the tie-breaker — but it
    is never silent, and the warning has to reach the MD, not stop at the UI.
    """
    print("The outreach ceiling warns but does not block:")
    real = tg.DB
    tg.DB = _fresh_db()
    try:
        ok, why = tg.check_ceiling("mails", 50)
        check("at the ceiling is fine", (ok, why), (True, ""))
        ok, why = tg.check_ceiling("mails", 400)
        check("over it is flagged", ok, False)
        check("and the warning explains the real cost",
              "spam" in why and "400" in why, True)

        r = tg.set_target("mails", 400)
        check("it is still saved — the owner decides", r["per_day"], 400)
        s = tg.status()
        check("and the status carries the warning", len(s["warnings"]), 1)
        check("the text output shows it", "WARNING" in tg.as_text(s), True)

        check("a sane number clears it", tg.set_target("mails", 20)["warning"], "")
        check("and the status is clean again", tg.status()["warnings"], [])
    finally:
        tg.DB = real


def test_zero_means_off_not_unset():
    print("Zero is a decision, not an absence:")
    real = tg.DB
    tg.DB = _fresh_db()
    try:
        tg.set_target("posts", 0)
        s = tg.status()
        check("the agency knows a target was set", s["any_target_set"], True)
        check("but posts is off", _job(s, "posts")["per_day"], 0)
        check("and it is not counted as hit", _job(s, "posts")["hit"], False)
        check("the text says 'not asked for'", "not asked for today" in tg.as_text(s), True)
    finally:
        tg.DB = real


def test_orders_round_trip():
    print("The owner's own words survive:")
    real = tg.DB
    tg.DB = _fresh_db()
    try:
        text = 'Sharjah hotels. No outreach — I\'m on site. "Aluminium first."'
        tg.set_orders(text)
        check("stored verbatim, quotes and all", tg.get_orders(), text)
        check("and it reaches the status", tg.status()["orders"], text)
        check("and the readable version", text in tg.as_text(), True)

        tg.set_orders("changed my mind")
        check("re-saving replaces, not appends", tg.get_orders(), "changed my mind")
    finally:
        tg.DB = real


def test_a_bad_job_name_is_refused():
    print("An unknown job is refused:")
    real = tg.DB
    tg.DB = _fresh_db()
    try:
        raised = None
        try:
            tg.set_target("tiktok_dances", 5)
        except KeyError as e:
            raised = str(e)
        check("it says what it does not know", "tiktok_dances" in (raised or ""), True)
        check("and lists what it does", "leads" in (raised or ""), True)
        check("nothing was stored", tg.status()["any_target_set"], False)
    finally:
        tg.DB = real


def test_negative_targets_cannot_be_stored():
    print("A negative target is meaningless:")
    real = tg.DB
    tg.DB = _fresh_db()
    try:
        tg.set_target("leads", -10)
        check("clamped to zero", _job(tg.status(), "leads")["per_day"], 0)
    finally:
        tg.DB = real


def test_an_unbuilt_cabinet_does_not_crash():
    print("No cabinet yet:")
    real = tg.DB
    tg.DB = Path(tempfile.mkdtemp()) / "nothing.db"
    try:
        s = tg.status()
        check("it answers rather than throwing", s["ok"], True)
        check("with everything at zero", [j["done_today"] for j in s["jobs"]], [0, 0, 0, 0])
    finally:
        tg.DB = real


def test_the_briefing_leads_with_the_orders():
    """
    An agent that reads the capability list but not the orders does what it CAN
    rather than what it was ASKED. The orders have to be in the briefing.
    """
    print("The briefing carries today's orders:")
    from . import briefing
    real = tg.DB
    tg.DB = _fresh_db()
    try:
        tg.set_target("leads", 15)
        tg.set_orders("Sharjah hotels only this week")
        b = briefing.build()
        o = b.get("todays_orders") or {}
        check("the briefing includes them", o.get("ok"), True)
        check("with the number", _job(o, "leads")["per_day"], 15)
        text = briefing.as_text(b)
        check("and they are visible in the readable version",
              "WHAT THE OWNER HAS ASKED FOR TODAY" in text, True)
        check("including his own words", "Sharjah hotels only this week" in text, True)
    finally:
        tg.DB = real


def main():
    test_nothing_set_says_so()
    test_progress_is_measured_not_claimed()
    test_yesterdays_work_is_not_todays()
    test_each_job_counts_the_right_thing()
    test_the_outreach_ceiling()
    test_zero_means_off_not_unset()
    test_orders_round_trip()
    test_a_bad_job_name_is_refused()
    test_negative_targets_cannot_be_stored()
    test_an_unbuilt_cabinet_does_not_crash()
    test_the_briefing_leads_with_the_orders()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: {_fail}")
        sys.exit(1)
    print("all targets tests passed")


if __name__ == "__main__":
    main()
