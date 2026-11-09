"""
clock_tests.py — run: python -m coordination.clock_tests

The promise: an age is time the agency was open, not time the laptop was shut.

    Content has not answered SEO in 26 days: Keyword-gap list ready

He had not left it 26 days. He had run the program on three of them. The alarm
was measuring how long his machine had been off and calling it neglect, and
because the Overseer re-checks every pass, it filed the same false alarm again
and again until the control room was nothing but false alarms.

The other half matters just as much and is the easier one to get wrong: this
must not become a way of hiding real staleness. A sector that ignores a request
for four hours of running time is exactly as broken as it was before.
"""
import os
import sqlite3
import tempfile
import time
from pathlib import Path

from . import board, clock, faults

_fail = []
ROOT = Path(__file__).parents[1]
DAY = 86400


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


class _Cabinet:
    def __enter__(self):
        self.real = board.DB
        d = Path(tempfile.mkdtemp())
        db = d / "agency.db"
        con = sqlite3.connect(db)
        con.executescript((ROOT / "coordination" / "schema.sql").read_text(encoding="utf-8"))
        con.commit()
        con.close()
        board.DB = str(db)
        return self

    def __exit__(self, *a):
        board.DB = self.real

    @staticmethod
    def ran(start, hours, status="ok", pid=None):
        with sqlite3.connect(board.DB) as c:
            c.execute("INSERT INTO day_runs(id,started_at,ended_at,status,"
                      "started_by,steps_total,steps_done,pid,host)"
                      " VALUES(?,?,?,?, 'owner',1,1,?,?)",
                      (f"r{start}", int(start),
                       int(start + hours * 3600) if hours else None,
                       status, pid if pid is not None else os.getpid(),
                       board._this_host()))


def test_a_shut_program_does_not_age_anything():
    print("\nHis 26 days:")
    with _Cabinet() as cab:
        now = int(time.time())
        for days_ago in (26, 14, 1):
            cab.ran(now - days_ago * DAY, 2)
        secs = clock.open_seconds(now - 26 * DAY, now)
        check("six hours of agency time, not 26 days", round(secs / 3600), 6)
        said = clock.ago(now - 26 * DAY, now)
        check("the phrase leads with the honest number",
              said.startswith("6 hour"), True)
        check("and still tells him the calendar date",
              "26 day(s) ago on the calendar" in said, True)


def test_a_day_that_never_stopped_counts_to_now():
    print("\nA day running right now is still counting:")
    with _Cabinet() as cab:
        now = int(time.time())
        cab.ran(now - 7200, 0, status="running")       # open, this process
        check("two hours so far", round(clock.open_seconds(now - 7200, now) / 3600), 2)


def test_a_day_whose_program_died_is_credited_not_left_running():
    print("\nAnd one whose program is gone is not still open:")
    with _Cabinet() as cab:
        now = int(time.time())
        cab.ran(now - 20 * DAY, 0, status="running", pid=999_999)
        secs = clock.open_seconds(now - 20 * DAY, now)
        check("credited a working day, not twenty",
              secs, clock.UNCLOSED_DAY_CREDIT)


def test_overlapping_runs_are_not_counted_twice():
    print("\nTwo runs over the same hour is one hour of agency time:")
    with _Cabinet() as cab:
        now = int(time.time())
        cab.ran(now - 4 * 3600, 3)
        cab.ran(now - 3 * 3600, 3)      # overlaps the first by two hours
        check("four hours, not six",
              round(clock.open_seconds(now - 5 * 3600, now) / 3600), 4)


def test_a_cabinet_with_no_days_has_no_agency_time():
    print("\nNo day ever recorded is no evidence the agency was open:")
    with _Cabinet():
        now = int(time.time())
        check("zero, not the whole span",
              clock.open_seconds(now - 30 * DAY, now), 0)


def test_real_staleness_is_still_reported_in_full():
    print("\nThis must not become a way of hiding a real problem:")
    with _Cabinet() as cab:
        now = int(time.time())
        # The program has been on the whole time. Nothing is being excused.
        cab.ran(now - 3 * DAY, 0, status="running")
        secs = clock.open_seconds(now - 3 * DAY, now)
        check("all three days count", round(secs / DAY), 3)
        check("and the phrase does not bother him with two numbers",
              "on the calendar" in clock.ago(now - 3 * DAY, now), False)


def test_the_overseer_files_the_honest_number():
    print("\nWhat the control room actually shows now:")
    with _Cabinet() as cab:
        now = int(time.time())
        rid = board.post_request("SEO", "Content", "Keyword-gap list ready", "the list")
        with sqlite3.connect(board.DB) as c:
            c.execute("UPDATE requests SET created_at=? WHERE id=?", (now - 26 * DAY, rid))
            c.execute("UPDATE request_msgs SET at=? WHERE request_id=?", (now - 26 * DAY, rid))
        for days_ago in (26, 14, 1):
            cab.ran(now - days_ago * DAY, 2)

        said = [f["what"] for f in faults.detect(now) if "not answered" in f["what"]]
        check("one fault, not none", len(said), 1)
        check("measured in agency time", "of agency time" in said[0], True)
        check("and it does not claim 26 days", "26 day" in said[0], False)


def test_a_genuinely_ignored_request_still_raises():
    print("\nAnd a sector that really is ignoring somebody is still caught:")
    with _Cabinet() as cab:
        now = int(time.time())
        rid = board.post_request("SEO", "Content", "Please answer this", "b")
        with sqlite3.connect(board.DB) as c:
            c.execute("UPDATE requests SET created_at=? WHERE id=?", (now - 2 * DAY, rid))
            c.execute("UPDATE request_msgs SET at=? WHERE request_id=?", (now - 2 * DAY, rid))
        cab.ran(now - 2 * DAY, 0, status="running")     # open the whole time
        said = [f["what"] for f in faults.detect(now) if "not answered" in f["what"]]
        check("still filed", len(said), 1)
        # Two full days of running time reads as 48 hours — _phrase only
        # switches to days past three, which is right: "48 hours" is a sharper
        # number to act on than "2 days".
        check("and in full", "48 hour(s) of agency time" in said[0], True)


def test_the_office_can_show_how_long_it_has_been_open():
    print("\nAnd he can see the clock itself:")
    with _Cabinet() as cab:
        now = int(time.time())
        cab.ran(now - 3600, 0, status="running")
        s = clock.state(now)
        check("open", s["open"], True)
        check("and says how long", s["open_today_secs"] > 0, True)


def main():
    for fn in list(globals().values()):
        if callable(fn) and getattr(fn, "__name__", "").startswith("test_"):
            fn()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        raise SystemExit(1)
    print("clock: all good — an age is time the agency was open.")


if __name__ == "__main__":
    main()
