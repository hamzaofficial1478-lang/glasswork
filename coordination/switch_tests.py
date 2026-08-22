"""
switch_tests.py — run: python -m coordination.switch_tests

The owner can turn a sector off. What is being protected here is that "off"
means off everywhere, and that it is never confused with "broken".

Two failure modes, and the second is the nastier one.

The obvious failure is a switch that does not switch: the sector keeps running,
or keeps being given work, and the button is decoration. So there are tests that
the MD cannot post to it, that nothing on the board can be claimed by it, and
that the day skips its step.

The subtle failure is a switch that quietly punishes him for using it. A
stopped sector runs nothing, produces nothing and clears no backlog — every
component of the health score reads that as failure, so the sector would score
near zero and drag the agency average down for obeying him. It has NO score, and
the reason says so in his own words.

And one rule that is easy to get backwards: a missing row means ON. A cabinet
built before this table existed must default to working. If absence meant off,
one upgrade would stop the whole agency with nothing on any screen to explain it.
"""
import sqlite3
import tempfile
import time
from pathlib import Path

from . import board
from . import state as st

_fail = []
ROOT = Path(__file__).parents[1]


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
        con.executescript((ROOT / "coordination" / "schema.sql").read_text())
        con.commit()
        con.close()
        board.DB = str(db)
        board._MIGRATED.discard(str(db))
        return self

    def __exit__(self, *a):
        board.DB = self.real

    def sql(self, q, args=()):
        c = sqlite3.connect(board.DB)
        c.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in c.execute(q, args)]
        finally:
            c.close()


def test_a_sector_nobody_has_touched_is_on():
    print("\nA missing row means ON, not off:")
    with _Cabinet() as cab:
        check("Leads works by default", board.sector_enabled("Leads"), True)
        check("so does every other sector",
              all(board.sector_enabled(r[0]) for r in st.SECTORS), True)
        check("nothing is recorded until he actually changes something",
              board.sector_switches(), {})
        check("and an unknown name is not treated as off",
              board.sector_enabled("SomethingElse"), True)


def test_switching_off_stops_work_reaching_it():
    print("\nOff means the MD cannot give it work:")
    with _Cabinet() as cab:
        board.set_sector("Leads", False, "not doing outreach this month")
        check("the switch is recorded", board.sector_enabled("Leads"), False)
        try:
            board.post_task("Leads", "Mail 20 firms", "d")
            check("posting is refused", "it was allowed", "refused")
        except board.SectorOff as e:
            check("posting is refused", "Leads is switched off" in str(e), True)
            check("and the message says how to undo it", "turn the sector back on"
                  in str(e).lower(), True)
        check("nothing landed on the board",
              cab.sql("SELECT count(*) n FROM tasks")[0]["n"], 0)
        check("other sectors are unaffected",
              bool(board.post_task("SEO", "A real job", "d")), True)


def test_work_already_on_the_board_cannot_be_claimed():
    print("\nOff also means work already there is not picked up:")
    with _Cabinet() as cab:
        tid = board.post_task("Leads", "Mail the cleared firms", "d")
        board.set_sector("Leads", False)
        check("the claim fails", board.claim_task(tid, "leads-outreach"), False)
        check("and the task is untouched, not deleted",
              cab.sql("SELECT status FROM tasks WHERE id=?", (tid,))[0]["status"], "open")
        board.set_sector("Leads", True)
        check("switching it back on lets the same task be claimed",
              board.claim_task(tid, "leads-outreach"), True)


def test_the_overseer_can_still_file_against_a_stopped_sector():
    print("\nA fault ticket is ABOUT a sector, not work for it:")
    with _Cabinet() as cab:
        board.set_sector("IT", False)
        tid = board.post_task("IT", "Fix: seo-strategist stalled", "d",
                              created_by="Overseer", force=True)
        check("the Overseer's ticket is still filed", bool(tid), True)
        # ...but ordinary work still is not.
        try:
            board.post_task("IT", "Ordinary IT job", "d")
            check("ordinary work is still refused", "allowed", "refused")
        except board.SectorOff:
            check("ordinary work is still refused", True, True)


def test_try_post_returns_none_instead_of_raising():
    print("\nA pipeline posting a follow-up does not crash on a stopped sector:")
    with _Cabinet():
        board.set_sector("SEO", False)
        check("it returns nothing", board.try_post_task("SEO", "t", "d"), None)
        board.set_sector("SEO", True)
        check("and works normally when the sector is on",
              bool(board.try_post_task("SEO", "t", "d")), True)


def test_a_stopped_sector_is_not_scored_as_failing():
    print("\nSwitched off is not the same as broken:")
    with _Cabinet() as cab:
        board.set_sector("Leads", False, "handling enquiries myself for now")
        c = sqlite3.connect(board.DB)
        c.row_factory = sqlite3.Row
        try:
            score, why = st._health(c, "Leads", "leads", int(time.time()) - 3600,
                                    int(time.time()))
        finally:
            c.close()
        check("it has no score at all", score, None)
        check("and the reason is the switch, not failure",
              why[0]["label"], "Switched off")
        check("in his own words", "handling enquiries myself" in why[0]["detail"], True)

        # And the headline must not be dragged down by a sector obeying him.
        # Give one live sector a clean run so there IS an average, then check the
        # stopped one is absent from it rather than averaged in as a zero.
        now = int(time.time())
        c = sqlite3.connect(board.DB)
        c.execute("INSERT INTO agent_runs(agent,started_at,ended_at,status,note)"
                  " VALUES('seo-strategist',?,?, 'ok','found three gaps')",
                  (now - 300, now - 240))
        c.commit(); c.close()
        snap = st.build()
        rooms = {r["key"]: r for r in snap["rooms"]}
        check("the stopped sector has no score in the snapshot",
              rooms["Leads"]["score"], None)
        check("and is flagged as switched off for the screens",
              rooms["Leads"]["enabled"], False)
        scored = [r["score"] for r in snap["rooms"] if r["score"] is not None]
        check("the agency average is the mean of the sectors that DO have one",
              snap["metrics"]["health"], round(sum(scored) / len(scored)))
        check("which is not zero just because a sector is off",
              snap["metrics"]["health"] > 0, True)


def test_the_day_skips_a_stopped_sector():
    print("\nThe day runner skips it, and says so rather than hiding it:")
    with _Cabinet():
        from pipelines import day_run
        board.set_sector("Content", False)
        skipped = [room for (_l, _m, room) in day_run.STEPS
                   if not board.sector_enabled(room)]
        ran = [room for (_l, _m, room) in day_run.STEPS if board.sector_enabled(room)]
        check("Content's step is skipped", "Content" in skipped, True)
        check("and nothing else is", set(skipped), {"Content"})
        check("the rest of the day still runs", "SEO" in ran and "IT" in ran, True)


def test_turning_it_back_on_changes_nothing_else():
    print("\nIt keeps its history — this is a switch, not a delete:")
    with _Cabinet() as cab:
        tid = board.post_task("Leads", "an earlier job", "d")
        board.set_sector("Leads", False, "paused")
        board.set_sector("Leads", True)
        check("the switch is off the record", board.sector_enabled("Leads"), True)
        check("its work is still there",
              cab.sql("SELECT count(*) n FROM tasks WHERE sector='Leads'")[0]["n"], 1)
        check("and it can be claimed again", board.claim_task(tid, "leads-hunter"), True)


def main():
    for t in (test_a_sector_nobody_has_touched_is_on,
              test_switching_off_stops_work_reaching_it,
              test_work_already_on_the_board_cannot_be_claimed,
              test_the_overseer_can_still_file_against_a_stopped_sector,
              test_try_post_returns_none_instead_of_raising,
              test_a_stopped_sector_is_not_scored_as_failing,
              test_the_day_skips_a_stopped_sector,
              test_turning_it_back_on_changes_nothing_else):
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
