"""
purge_tests.py — run: python -m coordination.purge_tests

"the data deleted must not get stored in the Db or any other place it must be
 deleted permanently"

The hard part of that sentence is 'permanently'. A DELETE in SQLite does not
wipe anything — the page keeps the bytes and the space is reused whenever the
database next happens to need it, so the text of a deleted message stays
legible in the file, and in every copy of the file, potentially for weeks.

So the test that matters here does not count rows. It writes a distinctive
phrase into a message, deletes it, and then reads the RAW BYTES of the database
file looking for that phrase. If it is still in there, the purge is a listing
change and not a deletion, and this fails.
"""
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

from . import board, purge as P

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
    con.executescript((Path(__file__).parent / "schema.sql").read_text(encoding="utf-8"))
    con.commit()
    con.close()
    board.DB = db
    board.ensure_schema()
    return db


def _w(q, args=()):
    with sqlite3.connect(board.DB) as c:
        c.execute(q, args)


def _n(q, args=()):
    c = sqlite3.connect(board.DB)
    try:
        return c.execute(q, args).fetchone()[0]
    finally:
        c.close()


OLD = int(time.time()) - 45 * 86400
NEW = int(time.time()) - 2 * 86400
SECRET = "ZZQX-a-very-distinctive-phrase-nobody-else-would-write-QXZZ"


def _all_bytes(db):
    """
    THE DATABASE IS NOT ONE FILE.

    board opens SQLite in WAL mode, so a freshly written row lives in
    agency.db-wal until a checkpoint moves it across. The first version of this
    test read agency.db alone, found the phrase was not in it, and therefore
    "proved" the purge had removed it — when the purge had not run yet. A test
    that passes before the code does anything is worse than no test.

    Everything the database consists of, then: the file, its write-ahead log
    and its shared-memory index.
    """
    db = Path(db)
    blob = b""
    for suffix in ("", "-wal", "-shm"):
        f = Path(str(db) + suffix)
        if f.exists():
            blob += f.read_bytes()
    return blob


def test_the_bytes_are_actually_gone():
    """
    The one that matters. Not 'the row does not come back in a SELECT' — the
    phrase is not in the file at all.
    """
    print("\nDeleted means the words are not in the file any more:")
    db = _fresh_db()
    _w("INSERT INTO requests(id,from_sector,to_sector,subject,status,created_at)"
       " VALUES('r1','SEO','Content','old thread','closed',?)", (OLD,))
    _w("INSERT INTO request_msgs(request_id,author,body,at)"
       " VALUES('r1','seo-strategist',?,?)", (SECRET, OLD))

    check("the phrase is in the database before the purge",
          SECRET.encode() in _all_bytes(db), True)

    r = P.run(days=30)
    check("the purge ran", r["ok"], True)
    check("and it says the file was rewritten", r["vacuumed"], True)
    check("the row is gone from the table",
          _n("SELECT count(*) FROM request_msgs"), 0)

    check("AND THE WORDS ARE NOWHERE IN IT — file, write-ahead log or index",
          SECRET.encode() in _all_bytes(db), False)


def test_a_plain_delete_would_not_have_been_enough():
    """
    Proving the point above is worth making: the same delete WITHOUT the vacuum
    leaves the text sitting in the file. This is why purge.run ends in VACUUM
    and why that is not an optimisation.
    """
    print("\n...and a delete on its own would not have been:")
    db = _fresh_db()
    _w("INSERT INTO requests(id,from_sector,to_sector,subject,status,created_at)"
       " VALUES('r1','SEO','Content','old','closed',?)", (OLD,))
    _w("INSERT INTO request_msgs(request_id,author,body,at)"
       " VALUES('r1','x',?,?)", (SECRET, OLD))
    _w("DELETE FROM request_msgs")
    check("the table is empty", _n("SELECT count(*) FROM request_msgs"), 0)
    check("and the words are still sitting in the database",
          SECRET.encode() in _all_bytes(db), True)


def test_only_what_is_old_and_finished():
    print("\nRecent work stays, and so does anything still open:")
    _fresh_db()
    _w("INSERT INTO tasks(id,sector,title,status,created_at)"
       " VALUES('t-old','SEO','done long ago','done',?)", (OLD,))
    _w("INSERT INTO tasks(id,sector,title,status,created_at)"
       " VALUES('t-open','SEO','nobody has done this','open',?)", (OLD,))
    _w("INSERT INTO tasks(id,sector,title,status,created_at)"
       " VALUES('t-new','SEO','done yesterday','done',?)", (NEW,))
    P.run(days=30)
    left = sorted(r[0] for r in sqlite3.connect(board.DB)
                  .execute("SELECT id FROM tasks"))
    check("the old finished one goes", "t-old" in left, False)
    check("the recent one stays", "t-new" in left, True)
    check("and an old one nobody finished STAYS — age is not done",
          "t-open" in left, True)


def test_the_scorecard_is_never_swept():
    """
    The handbook has the MD reading its own hit-rate every cycle and being
    confronted with its own track record. A scoreboard that forgets its losses
    is not a scoreboard.
    """
    print("\nThe MD's own record is kept, however old:")
    _fresh_db()
    _w("INSERT INTO decisions(cycle,reasoning,expectation,confidence,outcome,created_at)"
       " VALUES('md','a call that failed','x',0.6,'failed',?)", (OLD,))
    _w("INSERT INTO leads(company,stage,found_at) VALUES('Old Co','new',?)", (OLD,))
    P.run(days=30)
    check("the decision is still there", _n("SELECT count(*) FROM decisions"), 1)
    check("and so is the lead — otherwise the Hunter finds and mails it again",
          _n("SELECT count(*) FROM leads"), 1)


def test_a_message_whose_conversation_has_gone_goes_with_it():
    print("\nNo orphans left behind:")
    _fresh_db()
    _w("INSERT INTO requests(id,from_sector,to_sector,subject,status,created_at)"
       " VALUES('r1','SEO','Content','old','closed',?)", (OLD,))
    # a recent message on a conversation that is itself old and closed
    _w("INSERT INTO request_msgs(request_id,author,body,at)"
       " VALUES('r1','x','said yesterday',?)", (NEW,))
    P.run(days=30)
    check("the conversation went", _n("SELECT count(*) FROM requests"), 0)
    check("and the message went with it, though it was recent",
          _n("SELECT count(*) FROM request_msgs"), 0)


def test_the_dry_run_changes_nothing():
    print("\nLooking is not deleting:")
    _fresh_db()
    _w("INSERT INTO tasks(id,sector,title,status,created_at)"
       " VALUES('t','SEO','old','done',?)", (OLD,))
    r = P.plan(days=30)
    check("it says what would go", r["rows"], 1)
    check("naming it in words", r["tables"][0]["label"], "finished jobs")
    check("but nothing was touched", _n("SELECT count(*) FROM tasks"), 1)
    check("and it lists what it would never touch", "decisions" in r["kept"], True)


def test_an_empty_or_odd_cabinet_does_not_crash_it():
    print("\nNothing to do, and a cabinet missing a table:")
    _fresh_db()
    r = P.run(days=30)
    check("it succeeds", r["ok"], True)
    check("and says plainly that nothing was old enough",
          "nothing was older" in r["head"], True)

    # a table this knows about but an older cabinet does not have
    P.SWEEPS.append(("no_such_table", "at", "something imaginary", ""))
    try:
        r = P.run(days=30)
        check("a table that is not there is skipped, not fatal", r["ok"], True)
    finally:
        P.SWEEPS.pop()


def main():
    real = board.DB
    _fresh_db()
    try:
        test_the_bytes_are_actually_gone()
        test_a_plain_delete_would_not_have_been_enough()
        test_only_what_is_old_and_finished()
        test_the_scorecard_is_never_swept()
        test_a_message_whose_conversation_has_gone_goes_with_it()
        test_the_dry_run_changes_nothing()
        test_an_empty_or_odd_cabinet_does_not_crash_it()
    finally:
        board.DB = real

    print()
    if _fail:
        print(f"{len(_fail)} FAILED: {_fail}")
        sys.exit(1)
    print("all purge tests passed")


if __name__ == "__main__":
    main()
