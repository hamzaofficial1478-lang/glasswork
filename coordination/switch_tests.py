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
        con.executescript((ROOT / "coordination" / "schema.sql").read_text(encoding="utf-8"))
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
    """
    This used to switch IT off to make its point. IT is essential now and cannot
    be switched off at all, so the test says the same thing with a sector that
    genuinely can be: a fault ticket is ABOUT a sector, not work FOR it, and it
    has to be filed even when that sector is stopped — otherwise switching a
    sector off would also switch off the agency's ability to notice it broke.
    """
    print("\nA fault ticket is ABOUT a sector, not work for it:")
    with _Cabinet() as cab:
        board.set_sector("Content", False, "paused")
        tid = board.post_task("Content", "Fix: content-writer stalled", "d",
                              created_by="Overseer", force=True)
        check("the Overseer's ticket is still filed", bool(tid), True)
        # ...but ordinary work still is not.
        try:
            board.post_task("Content", "Ordinary content job", "d")
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
        skipped = [room for (_l, _m, room, _lane) in day_run.STEPS
                   if not board.sector_enabled(room)]
        ran = [room for (_l, _m, room, _lane) in day_run.STEPS
               if board.sector_enabled(room)]
        check("Content's step is skipped", "Content" in skipped, True)
        check("and nothing else is", set(skipped), {"Content"})
        check("the rest of the day still runs", "SEO" in ran and "IT" in ran, True)


def test_the_four_the_agency_runs_on_cannot_be_switched_off():
    print("\nFour sectors have no off switch, because the agency runs on them:")
    with _Cabinet():
        for key in ("Director", "IT", "Report", "Security"):
            try:
                board.set_sector(key, False, "trying it on")
                check(f"{key} is refused", "it was allowed", "refused")
            except board.SectorEssential as e:
                check(f"{key} is refused", key in str(e), True)
                check(f"  and says why {key} matters", len(str(e)) > 40, True)
            check(f"  {key} is still working", board.sector_enabled(key), True)

        for key in ("Content", "SEO", "Leads"):
            board.set_sector(key, False)
            check(f"{key} CAN be switched off", board.sector_enabled(key), False)
            board.set_sector(key, True)


def test_an_essential_sector_stays_on_even_if_the_row_says_otherwise():
    """
    Belt and braces. A stale 'off' row for IT — an older build, a hand-edited
    cabinet, a restore from a backup taken mid-experiment — would disable the
    one sector that can fix the problem, and nothing would be able to turn it
    back on. The code decides, not the row.
    """
    print("\nA stale 'off' row cannot disable an essential sector:")
    with _Cabinet() as cab:
        c = sqlite3.connect(board.DB)
        c.execute("INSERT INTO sector_switches (sector, enabled, note, changed_at)"
                  " VALUES ('IT', 0, 'planted by hand', 0)")
        c.commit(); c.close()
        check("the row really is there",
              cab.sql("SELECT enabled FROM sector_switches WHERE sector='IT'")[0]["enabled"], 0)
        check("but IT still works", board.sector_enabled("IT"), True)
        check("and can still be given work", bool(board.post_task("IT", "fix it", "d")), True)


def test_a_stopped_sector_is_marked_off_for_the_screens():
    print("\nEvery screen is told, so the office floor can put the lights out:")
    with _Cabinet():
        # Real runs first, or the rooms come back with no desks at all and the
        # assertions below pass vacuously against empty lists.
        now = int(time.time())
        c = sqlite3.connect(board.DB)
        for agent, sector in (("content-writer", "Content"), ("seo-strategist", "SEO")):
            c.execute("INSERT INTO agent_runs(agent,started_at,ended_at,status,note)"
                      " VALUES(?,?,?, 'ok','did a thing')", (agent, now - 300, now - 240))
        c.commit(); c.close()

        board.set_sector("Content", False, "rewriting the service pages myself")
        rooms = {r["key"]: r for r in st.build()["rooms"]}
        check("the stopped room really has desks to mark",
              len(rooms["Content"]["agents"]) > 0, True)
        check("the room says it is off", rooms["Content"]["enabled"], False)
        check("with the owner's own reason", rooms["Content"]["off_note"],
              "rewriting the service pages myself")
        check("and every desk in it reads 'off', not idle",
              sorted({a["s"] for a in rooms["Content"]["agents"]}), ["off"])
        check("no other room is touched",
              all(rooms[k]["enabled"] for k in rooms if k != "Content"), True)
        check("and their desks are not marked off",
              any(a["s"] != "off" for k in rooms if k != "Content"
                  for a in rooms[k]["agents"]), True)
        check("essential rooms are flagged so no switch is drawn for them",
              sorted(k for k, r in rooms.items() if r["essential"]),
              ["Director", "IT", "Report", "Security"])


def test_nothing_can_claim_work_for_a_stopped_sector():
    """
    Not just the tidy route. An agent reaching past next_open_task and claiming
    by id must be refused too, or the switch is a suggestion.
    """
    print("\nEvery route to picking work up is closed, not just the tidy one:")
    with _Cabinet() as cab:
        tid = board.post_task("Leads", "Mail the cleared firms", "d")
        board.set_sector("Leads", False)
        check("next_open_task finds nothing",
              board.next_open_task("Leads", "leads-outreach"), None)
        check("claiming by id is refused too",
              board.claim_task(tid, "leads-hunter"), False)
        check("and the job is still there, untouched, for when he turns it back on",
              cab.sql("SELECT status,owner FROM tasks WHERE id=?", (tid,))[0],
              {"status": "open", "owner": None})


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


def _old_cabinet(*without):
    """
    A cabinet built by an OLDER version: the schema with some tables missing.

    This is what the owner actually had, and what nothing was testing.
    """
    import re
    schema = (ROOT / "coordination" / "schema.sql").read_text(encoding="utf-8")
    for t in without:
        schema = re.sub(r"CREATE TABLE IF NOT EXISTS %s \(.*?\);" % t, "",
                        schema, flags=re.S)
        schema = re.sub(r"CREATE INDEX IF NOT EXISTS %s\w*[^;]*;" % t, "", schema)
    d = Path(tempfile.mkdtemp())
    db = d / "agency.db"
    con = sqlite3.connect(db)
    con.executescript(schema)
    con.commit()
    con.close()
    return db


def test_an_older_cabinet_gets_every_new_table():
    """
    THE BUG THIS PINS.

    Tables were declared in schema.sql and, separately, hand-copied into a list
    in board.py that the migration actually read. The two drifted three times.
    On the owner's cabinet — built before any of them existed — the result was:

      sector_switches   "no such table" the moment he switched a sector off
      read_marks        every write swallowed, so his activity badge reset on
                        every poll and he reported it three times
      faults            the Overseer's pass died on its first write

    The first crashed loudly and got fixed. The other two failed silently and
    were mistaken for other problems for days. So this does not test three
    table names — it tests that EVERY table in schema.sql is reachable after
    migrating a cabinet that has none of them, which is the check that keeps
    working when the next table is added.
    """
    import re
    print("\nA cabinet built before these tables existed gets them all:")
    schema = (ROOT / "coordination" / "schema.sql").read_text(encoding="utf-8")
    want = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", schema, re.I))
    newest = ["faults", "read_marks", "sector_switches"]

    real = board.DB
    db = _old_cabinet(*newest)
    board.DB = str(db)
    board._MIGRATED.discard(str(db))
    try:
        con = sqlite3.connect(db)
        before = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        con.close()
        check("the old cabinet really is missing them",
              sorted(set(newest) - before), sorted(newest))

        # Any ordinary board call must heal it — the owner should never have to
        # rebuild his cabinet or run a command to get a working feature.
        board.sector_enabled("Leads")

        con = sqlite3.connect(db)
        after = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        con.close()
        missing = sorted(want - after)
        check("every table in schema.sql now exists", missing, [])
        check("including the three that were missed", sorted(set(newest) - after), [])
    finally:
        board.DB = real

    # And the features themselves work, not just the tables.
    db = _old_cabinet(*newest)
    board.DB = str(db)
    board._MIGRATED.discard(str(db))
    try:
        board.set_sector("Content", False, "has apis, testing")
        check("switching a sector off no longer crashes",
              board.sector_enabled("Content"), False)

        from . import activity
        now = int(time.time())
        con = sqlite3.connect(board.DB)
        con.execute("INSERT INTO agent_runs(agent,started_at,status,note)"
                    " VALUES('seo-strategist',?, 'ok','a run')", (now - 60,))
        con.commit(); con.close()
        activity.mark_read()
        check("the activity read mark actually persists",
              activity.feed()["unread"], 0)

        from . import faults
        con = sqlite3.connect(board.DB)
        con.execute("INSERT INTO agent_runs(agent,started_at,status,note)"
                    " VALUES('leads-hunter',?, 'running','x')",
                    (now - faults.STALL_SECS - 300,))
        con.commit(); con.close()
        check("and the Overseer's pass runs instead of dying",
              faults.scan()["filed"], 1)
    finally:
        board.DB = real


def main():
    for t in (test_an_older_cabinet_gets_every_new_table,
              test_a_sector_nobody_has_touched_is_on,
              test_switching_off_stops_work_reaching_it,
              test_work_already_on_the_board_cannot_be_claimed,
              test_the_overseer_can_still_file_against_a_stopped_sector,
              test_try_post_returns_none_instead_of_raising,
              test_a_stopped_sector_is_not_scored_as_failing,
              test_the_day_skips_a_stopped_sector,
              test_the_four_the_agency_runs_on_cannot_be_switched_off,
              test_an_essential_sector_stays_on_even_if_the_row_says_otherwise,
              test_a_stopped_sector_is_marked_off_for_the_screens,
              test_nothing_can_claim_work_for_a_stopped_sector,
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
