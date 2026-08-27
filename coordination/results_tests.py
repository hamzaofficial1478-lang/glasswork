"""
results_tests.py — run: python -m coordination.results_tests

"there is no any section that shows the results of the sectors."

The screen is only worth having if it is right on a bad day, so most of these
tests are about a sector that produced NOTHING. There are six different reasons
for that and they call for six different things from him:

    switched off        he did that. Nothing to do.
    the day never ran   press the button.
    the step failed     something is broken and IT needs to know.
    you stopped it      expected.
    it sat out          it ran and had nothing to do. Fine.
    it ran clean, zero  also fine, and the hardest one to say honestly.

Telling him the wrong one of those six sends him looking in the wrong place, so
each is derived from the rows the run itself wrote and each is asserted here.

Throwaway cabinet, no network.
"""
import re
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

from . import board, results as R

_fail = []

SCHEMA = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


def _fresh_db(without=()):
    """
    A throwaway cabinet. `without` builds one MISSING those tables, which is
    how an older copy is simulated — the statements are filtered out before the
    schema is applied rather than removed afterwards, so nothing here ever has
    to destroy a table to test what happens when one is absent.
    """
    sql = SCHEMA
    for name in without:
        sql = re.sub(r"CREATE TABLE IF NOT EXISTS " + name + r"\s*\(.*?\);",
                     "", sql, flags=re.S)
        sql = re.sub(r"CREATE INDEX IF NOT EXISTS \S+ ON " + name + r"\(.*?\);",
                     "", sql, flags=re.S)
    d = Path(tempfile.mkdtemp())
    db = d / "agency.db"
    con = sqlite3.connect(db)
    con.executescript(sql)
    con.commit()
    con.close()
    board.DB = db
    if not without:
        board.ensure_schema()
    return db


def _write(q, args=()):
    with sqlite3.connect(board.DB) as c:
        c.execute(q, args)


def _run(steps, status="done"):
    """A day run whose steps ended however the test needs them to."""
    now = int(time.time())
    _write("INSERT INTO day_runs(id,started_at,ended_at,status,started_by,steps_total)"
           " VALUES('r1',?,?,?,'owner',?)", (now - 600, now, status, len(steps)))
    for i, (module, st, summary) in enumerate(steps):
        _write("INSERT INTO day_steps(run_id,seq,label,module,status,summary,"
               " started_at,ended_at) VALUES('r1',?,?,?,?,?,?,?)",
               (i, f"step {i}", module, st, summary, now - 500, now - 400))


def _of(r, key):
    return next(s for s in r["sectors"] if s["key"] == key)


# ---------------------------------------------------------------------------

def test_what_a_sector_made_today_and_not_all_time():
    """
    The control room already listed the last forty rows of ALL TIME. On day
    ninety that is a wall of history with today buried in it, and he presses
    Run and wants to know what THAT got him.
    """
    print("\nToday's work, not the whole filing cabinet:")
    _fresh_db()
    now = int(time.time())
    _write("INSERT INTO leads(company,email,area,stage,found_at)"
           " VALUES('Al Quoz Fitout','a@b.ae','Dubai','new',?)", (now,))
    _write("INSERT INTO leads(company,email,area,stage,found_at)"
           " VALUES('Old One','c@d.ae','Sharjah','new',?)", (now - 8 * 86400,))
    r = R.results()
    leads = _of(r, "Leads")
    check("only today's lead is shown", leads["made"], 1)
    check("and it is the right one", leads["items"][0]["cols"][0], "Al Quoz Fitout")
    check("the count agrees", leads["counts"][0]["n"], 1)
    check("and the columns are named in his language",
          leads["columns"][1], "how to reach them")

    print("\n...and a week is a week:")
    check("still one — the other is eight days old",
          _of(R.results(days=7), "Leads")["made"], 1)
    check("a month picks up both",
          _of(R.results(days=30), "Leads")["made"], 2)


def test_the_six_reasons_a_sector_produced_nothing():
    print("\nSwitched off says so, and quotes his reason back:")
    _fresh_db()
    board.set_sector("Leads", False, note="not this month")
    why = _of(R.results(), "Leads")["nothing_because"]
    check("it is his own doing, not a fault", "you switched this sector off" in why, True)
    check("and his reason is quoted back", "not this month" in why, True)
    check("with what to do about it", "Turn it back on" in why, True)

    print("\nNo run at all says press the button:")
    _fresh_db()
    r = R.results()
    check("it does not read as a failure",
          "has not been run yet" in _of(r, "SEO")["nothing_because"], True)
    check("and the headline says so too", "has not been run yet" in r["head"], True)

    print("\nA step that FAILED reads as broken, not as quiet:")
    _fresh_db()
    _run([("pipelines.seo_gap", "failed", "KeyError: 'phrase'")])
    why = _of(R.results(), "SEO")["nothing_because"]
    check("it says the work failed", why.startswith("its work failed"), True)
    check("and carries the actual error", "KeyError" in why, True)

    print("\nA step he STOPPED is expected, not broken:")
    _fresh_db()
    _run([("pipelines.seo_gap", "stopped", "stopped by you — lunch")], status="stopped")
    check("it says he stopped it",
          "you stopped the day" in _of(R.results(), "SEO")["nothing_because"], True)

    print("\nA step that SAT OUT ran fine and had nothing to do:")
    _fresh_db()
    _run([("pipelines.seo_gap", "idle", "No open SEO task on the board")])
    why = _of(R.results(), "SEO")["nothing_because"]
    check("it says it had nothing to do", "nothing to do" in why, True)
    check("and quotes the step's own reason", "No open SEO task" in why, True)

    print("\nAnd a step that ran clean and still made nothing says exactly that:")
    _fresh_db()
    _run([("pipelines.seo_gap", "ok", "checked 40 phrases, nothing new")])
    why = _of(R.results(), "SEO")["nothing_because"]
    check("no fault is invented", "failed" in why, False)
    check("it is allowed to be an honest zero", "produced nothing new" in why, True)


def test_a_sector_that_produced_something_is_not_explained_away():
    print("\nA sector with output does not get an excuse attached:")
    _fresh_db()
    _run([("pipelines.leads_hunt", "ok", "found 3")])
    _write("INSERT INTO leads(company,email,stage,found_at) VALUES('X','x@y.ae','new',?)",
           (int(time.time()),))
    leads = _of(R.results(), "Leads")
    check("it made something", leads["made"], 1)
    check("so there is nothing to explain", leads["nothing_because"], "")


def test_the_last_run_predating_a_step_is_not_called_a_gap():
    """
    The first version read the last run's steps and, finding none for a sector,
    announced 'no step belongs to this sector — that is a gap in the run'. That
    is a strong claim, and it was wrong every time a run predated a step being
    added, which made an ordinary upgrade look like a hole in the program.
    """
    print("\nAn old run that predates a step is not a hole in the program:")
    _fresh_db()
    _run([("pipelines.nothing_real", "ok", "")])
    why = _of(R.results(), "Leads")["nothing_because"]
    check("it does not claim Leads has no step", "gap in the run" in why, False)
    check("it says the run predates it", "predates it" in why, True)
    check("and Leads really does have a step today", R._has_a_step("Leads"), True)


def test_the_steps_of_the_last_run_travel_with_the_sector():
    print("\nEach sector carries what the run asked of it:")
    _fresh_db()
    _run([("pipelines.it_health", "ok", "checked the site"),
          ("pipelines.leads_hunt", "idle", "nothing on the board"),
          ("pipelines.seo_gap", "failed", "boom")])
    r = R.results()
    check("IT gets its own step", [s["status"] for s in _of(r, "IT")["steps"]], ["ok"])
    check("Leads gets its own", [s["status"] for s in _of(r, "Leads")["steps"]], ["idle"])
    check("SEO gets its own", [s["status"] for s in _of(r, "SEO")["steps"]], ["failed"])
    check("and nobody gets somebody else's", _of(r, "Content")["steps"], [])


def test_one_sector_can_be_asked_for_on_its_own():
    print("\nHe can ask about one sector:")
    _fresh_db()
    check("just the one", [s["key"] for s in R.results(sector="Leads")["sectors"]],
          ["Leads"])
    check("an unknown name is empty rather than an error",
          R.results(sector="Marketing")["sectors"], [])


def test_the_headline_is_readable_before_anything_else():
    print("\nThe headline says what happened without opening anything:")
    _fresh_db()
    _run([("pipelines.leads_hunt", "ok", "")])
    now = int(time.time())
    for i in range(3):
        _write("INSERT INTO leads(company,stage,found_at) VALUES(?,'new',?)",
               (f"Co {i}", now))
    r = R.results()
    check("it counts the lot", r["total"], 3)
    check("and names the quiet ones", "Quiet:" in r["head"], True)
    check("saying which period it is about", "today" in r["head"], True)


def test_an_older_cabinet_still_gets_a_screen():
    """
    A copy built before some of these tables existed is missing them. A results
    screen that falls over on the day something is odd is a results screen he
    cannot use on the day he needs it.
    """
    print("\nAn older cabinet still gets a screen:")
    _fresh_db(without=("keyword_intel", "site_changes"))
    r = R.results()
    check("it still answers", r["ok"], True)
    check("with every sector present", len(r["sectors"]), 7)
    check("and the one whose table is missing reads as empty, not broken",
          _of(r, "SEO")["made"], 0)
    check("its counts fall back to zero rather than blowing up",
          _of(r, "SEO")["counts"][0]["n"], 0)


def test_it_can_count_to_one_in_english():
    print("\n'1 call made', not '1 calls made':")
    check("plain s", R.one_of(1, "calls made"), "call made")
    check("ies -> y", R.one_of(1, "replies"), "reply")
    check("es after ch", R.one_of(1, "searches found"), "search found")
    check("es after ss", R.one_of(1, "businesses found"), "business found")
    check("more than one is left alone", R.one_of(3, "calls made"), "calls made")
    check("none is left alone too", R.one_of(0, "calls made"), "calls made")
    # A label that does not start with a noun must not be mangled by a rule
    # that does not apply to it.
    check("'written to' survives", R.one_of(1, "written to"), "written to")
    check("'waiting on your yes' survives",
          R.one_of(1, "waiting on your yes"), "waiting on your yes")
    check("and 'stopped' is not turned into 'stoppe'",
          R.one_of(1, "stopped"), "stopped")

    print("\n...and it reaches the screen:")
    _fresh_db()
    _write("INSERT INTO leads(company,stage,found_at) VALUES('One','new',?)",
           (int(time.time()),))
    check("through the real reader",
          _of(R.results(), "Leads")["counts"][0]["label"], "business found")


def test_every_query_actually_runs():
    """
    THE BUG THIS EXISTS TO STOP, WHICH HAD ALREADY HAPPENED.

    Every reader here wraps its query in a try/except so an older cabinet gets
    a screen instead of a stack trace. The cost of that kindness is that a
    TYPO is indistinguishable from an old cabinet: the query raises, the except
    swallows it, and the sector shows "nothing yet" for ever.

    It had already happened. state._DETAIL's Content query selected a column
    called 'service' that content_queue has never had, so the control room's
    Content sheet showed nothing from the day it was written, however much
    Content produced. Nobody noticed because "nothing yet" is a plausible
    answer.

    So every query gets run against a fresh schema, and a broken one fails HERE
    rather than showing him a lie for six months.
    """
    print("\nEvery query runs against the real schema — a typo cannot hide:")
    db = _fresh_db()
    c = sqlite3.connect(db)
    try:
        for key, (_label, _ts, sql, cols) in R.MADE.items():
            try:
                c.execute(sql, (0,)).fetchall()
                ok, err = True, ""
            except sqlite3.OperationalError as e:
                ok, err = False, str(e)
            check(f"{key}: the 'what it made' query runs", ok, True)
            if not ok:
                print(f"        {err}")
            check(f"{key}: it names four columns", len(cols), 4)
        for key, pairs in R.HEADLINE.items():
            for label, q in pairs:
                try:
                    c.execute(q, (0,) * q.count("?")).fetchall()
                    ok, err = True, ""
                except sqlite3.OperationalError as e:
                    ok, err = False, str(e)
                check(f"{key}: '{label}' counts something real", ok, True)
                if not ok:
                    print(f"        {err}")
    finally:
        c.close()

    print("\n...and so does the control room's, which is where it went wrong:")
    from . import state
    c = sqlite3.connect(db)
    try:
        for key, (_label, sql, cols) in state._DETAIL.items():
            try:
                c.execute(sql).fetchall()
                ok, err = True, ""
            except sqlite3.OperationalError as e:
                ok, err = False, str(e)
            check(f"{key}: the control room query runs", ok, True)
            if not ok:
                print(f"        {err}")
    finally:
        c.close()


def main():
    real = board.DB
    _fresh_db()
    try:
        test_what_a_sector_made_today_and_not_all_time()
        test_the_six_reasons_a_sector_produced_nothing()
        test_a_sector_that_produced_something_is_not_explained_away()
        test_the_last_run_predating_a_step_is_not_called_a_gap()
        test_the_steps_of_the_last_run_travel_with_the_sector()
        test_one_sector_can_be_asked_for_on_its_own()
        test_the_headline_is_readable_before_anything_else()
        test_an_older_cabinet_still_gets_a_screen()
        test_it_can_count_to_one_in_english()
        test_every_query_actually_runs()
    finally:
        board.DB = real

    print()
    if _fail:
        print(f"{len(_fail)} FAILED: {_fail}")
        sys.exit(1)
    print("all results tests passed")


if __name__ == "__main__":
    main()
