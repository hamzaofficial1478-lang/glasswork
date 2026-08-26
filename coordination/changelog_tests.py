"""
changelog_tests.py — run: python -m coordination.changelog_tests

The website change log. "there should be a section in program where i can see
whats changed in website... that's totally blind."

Three things have to be true or the panel is decoration:

  * a change the agency makes is written down at the moment it is made, with
    enough detail to reverse it
  * a change SOMEBODY ELSE makes is noticed — that is the half he asked for,
    and the half that catches his own careful fix being overwritten
  * the agency's own edit is never reported back to him as tampering

Everything runs against a throwaway cabinet. No site is contacted.
"""
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

from . import board, changelog as CL

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


def test_a_change_is_written_down_with_a_way_back():
    print("\nA change the agency makes is recorded, and it can be undone:")
    _fresh_db()
    CL.record("head_block", page="includes/header.php", agent="it-releaser",
              block_id="viewport",
              summary="added the viewport block to the top of includes/header.php",
              bytes_after=4120, backup="header.php.20260826-101500-a1b2c3.bak",
              undo_cmd="python -m pipelines.site_api --remove-block viewport "
                       "--page includes/header.php")
    r = CL.recent()
    check("it is in the log", len(r["changes"]), 1)
    c = r["changes"][0]
    check("with a line he can read",
          c["summary"].startswith("added the viewport block"), True)
    check("attributed to the agency, by name", c["by"], "the agency — it-releaser")
    check("with the server's own copy of what was there",
          c["backup"].endswith(".bak"), True)
    check("and the exact command that puts it back",
          "--remove-block viewport" in c["undo_cmd"], True)
    check("and a time in words, not a number", c["when"], "just now")


def test_a_refused_write_is_recorded_too():
    """
    'The Fixer tried to change the header and was not allowed' is invisible
    until you go looking for why nothing happened. It belongs in the log.
    """
    print("\nA write that was refused is still something that happened:")
    _fresh_db()
    CL.record("page", page="index.php", agent="it-fixer", ok=False,
              error="'it-fixer' may not change the website")
    c = CL.recent()["changes"][0]
    check("recorded", c["action"], "page")
    check("marked as not having happened", c["ok"], 0)
    check("with the reason kept", "may not change" in c["error"], True)
    check("and no undo offered for a thing that never happened", c["undo_cmd"], "")


def test_somebody_else_editing_the_site_is_noticed():
    """
    The half he actually asked for. He edits a page in the file manager, a
    designer touches a template, the hosting company restores last Tuesday.
    None of it goes through the agency and all of it is his site changing.
    """
    print("\nThe first look is a baseline, not a wall of false alarms:")
    _fresh_db()
    first = CL.sweep([{"page": "index.php", "bytes": 100, "changed": "t1",
                       "html": "<html>one</html>"},
                      {"page": "about.php", "bytes": 50, "changed": "t1",
                       "html": "<html>two</html>"}])
    check("both pages fingerprinted", first["pages_seen"], 2)
    check("and nothing reported as changed", first["edited"], [])
    check("it says it is the first look", first["first_look"], True)
    check("nothing at all in the log yet", CL.recent()["total"], 0)

    print("\n...then an edit nobody in the agency made is reported:")
    second = CL.sweep([{"page": "index.php", "bytes": 100, "changed": "t1",
                        "html": "<html>ONE, rewritten</html>"},
                       {"page": "about.php", "bytes": 50, "changed": "t1",
                        "html": "<html>two</html>"}])
    check("the changed page is named", second["edited"], ["index.php"])
    check("the untouched one is not", "about.php" in second["edited"], False)
    c = CL.recent()["changes"][0]
    check("logged as somebody else's", c["by"], "somebody else")
    check("in plain words",
          "changed by somebody other than the agency" in c["summary"], True)
    check("with the size difference", "bytes bigger" in c["summary"], True)

    print("\n...and a rewrite that happens to be the same length is caught:")
    CL.sweep([{"page": "index.php", "bytes": 100, "changed": "t1",
               "html": "<html>ONE, rewritten</html>"},
              {"page": "about.php", "bytes": 50, "changed": "t1",
               "html": "<html>TWO</html>"}])
    # The old comparison was bytes and mtime. 'two' and 'TWO' are the same
    # length and the same timestamp, so that comparison would have called this
    # unchanged — which is exactly how a defacement goes unnoticed.
    c = CL.recent()["changes"][0]
    check("content is what is compared, not size", c["page"], "about.php")
    check("and it says so", "same size but different content" in c["summary"], True)


def test_a_page_appearing_and_vanishing():
    print("\nA new page, and a page that is gone:")
    _fresh_db()
    CL.sweep([{"page": "index.php", "bytes": 10, "html": "a"}])
    r = CL.sweep([{"page": "index.php", "bytes": 10, "html": "a"},
                  {"page": "pergolas.php", "bytes": 20, "html": "bb"}])
    check("the new page is reported", r["added"], ["pergolas.php"])
    check("and logged in his words",
          "appeared on the site" in CL.recent()["changes"][0]["summary"], True)

    r = CL.sweep([{"page": "index.php", "bytes": 10, "html": "a"}])
    check("the missing page is reported", r["gone"], ["pergolas.php"])
    check("and logged",
          "no longer on the site" in CL.recent()["changes"][0]["summary"], True)

    print("\n...and if it comes back it reads as back, not as brand new:")
    r = CL.sweep([{"page": "index.php", "bytes": 10, "html": "a"},
                  {"page": "pergolas.php", "bytes": 20, "html": "bb"}])
    check("not reported as a page that never existed", r["added"], [])
    check("reported as changed instead", r["edited"], ["pergolas.php"])


def test_the_agencys_own_edit_is_not_reported_as_tampering():
    """
    The failure that would make this whole panel useless: the agency writes a
    page, the next sweep sees it moved, and he is told somebody is tampering
    with his site — by the thing that did it.
    """
    print("\nThe agency's own change is not reported back as somebody else's:")
    _fresh_db()
    CL.sweep([{"page": "index.php", "bytes": 10, "html": "before"}])

    # the write path: record it, and remember what we left behind
    CL.record("page", page="index.php", agent="it-releaser",
              summary="rewrote index.php")
    CL.note_page("index.php", html="after, quite different")

    r = CL.sweep([{"page": "index.php", "bytes": 21, "html": "after, quite different"}])
    check("nothing reported as changed", r["edited"], [])
    check("because the fingerprint was refreshed on the write", r["was_us"], [])
    check("and the log has exactly one entry for it", CL.recent()["total"], 1)

    print("\n...and it still holds if the process died before refreshing:")
    # The real sequence, not an approximation of it: the page is already
    # fingerprinted from an earlier look, the agency writes, and the process
    # dies before note_page runs. The next sweep sees content that does not
    # match the OLD fingerprint and must put it down to us.
    CL.sweep([{"page": "about.php", "bytes": 5, "html": "start"}])
    with sqlite3.connect(board.DB) as c:          # the fingerprint is a minute old
        c.execute("UPDATE site_fingerprints SET seen_at=? WHERE page='about.php'",
                  (int(time.time()) - 60,))
    CL.record("page", page="about.php", agent="it-releaser",
              summary="rewrote about.php")        # note_page never ran
    r = CL.sweep([{"page": "about.php", "bytes": 9, "html": "different"}])
    check("put down to us, not to a stranger", r["was_us"], ["about.php"])
    check("and not reported as an outside change", r["edited"], [])

    print("\n...but an edit long after ours is a stranger's again:")
    with sqlite3.connect(board.DB) as c:
        c.execute("UPDATE site_changes SET at=? WHERE page='about.php'",
                  (int(time.time()) - CL.OURS_WINDOW - 60,))
    r = CL.sweep([{"page": "about.php", "bytes": 12, "html": "different now"}])
    check("reported", r["edited"], ["about.php"])


def test_our_own_fix_being_overwritten_is_the_thing_it_must_catch():
    """
    THE EVENT THIS PANEL EXISTS FOR, and the first version swallowed it.

    The rule used to be "if the agency wrote this page in the last few minutes,
    any difference is ours". So: the Releaser fixes the header at 09:00,
    something overwrites the whole file at 09:05, and the sweep at 09:06 says
    "that was us" and tells him nothing. The one change he most needs to know
    about was the one change that could not be reported.
    """
    print("\nThe agency's fix being wiped minutes later is reported, not swallowed:")
    _fresh_db()
    CL.sweep([{"page": "includes/header.php", "bytes": 40,
               "html": "<head><title>x</title></head>"}])

    # 09:00 — the Releaser writes, and records both halves properly
    CL.record("head_block", page="includes/header.php", agent="it-releaser",
              block_id="viewport", summary="added the viewport block")
    CL.note_page("includes/header.php",
                 html="<head><title>x</title><meta name=viewport></head>")

    r = CL.sweep([{"page": "includes/header.php", "bytes": 60,
                   "html": "<head><title>x</title><meta name=viewport></head>"}])
    check("our own write is not reported back at us", r["edited"], [])

    # 09:05 — something else flattens the file
    r = CL.sweep([{"page": "includes/header.php", "bytes": 20,
                   "html": "<head><title>Oops</title></head>"}])
    check("but the overwrite IS reported", r["edited"], ["includes/header.php"])
    check("and not put down to us", r["was_us"], [])
    c = CL.recent()["changes"][0]
    check("as somebody else's", c["by"], "somebody else")
    check("with the block we added now gone from the log's story",
          "changed by somebody other than the agency" in c["summary"], True)


def test_the_headline_says_something_useful_when_there_is_nothing():
    print("\nAn empty log says why it is empty, not just '0':")
    _fresh_db()
    r = CL.recent()
    check("nothing watched yet is its own message",
          "Nothing is being watched yet" in r["head"], True)
    check("and it says how to start", "/setup" in r["head"], True)

    CL.sweep([{"page": "index.php", "bytes": 10, "html": "a"}])
    r = CL.recent()
    check("watched but quiet reads as quiet, not as broken",
          "Nothing has changed since the agency started looking" in r["head"], True)


def test_the_log_can_be_narrowed():
    print("\nHe can ask for one page, or only the changes he did not make:")
    _fresh_db()
    CL.record("page", page="index.php", agent="it-releaser", summary="ours")
    CL.record("edited", page="about.php", source=CL.OUTSIDE, summary="theirs")
    check("one page", [c["page"] for c in CL.recent(page="about.php")["changes"]],
          ["about.php"])
    check("only what the agency did not do",
          [c["summary"] for c in CL.recent(source=CL.OUTSIDE)["changes"]],
          ["theirs"])
    check("and the whole list is both", CL.recent()["total"], 2)


def test_recording_never_breaks_the_work():
    """
    A change that happened and was not recorded is bad. A change REFUSED
    because the log was unwritable is worse. Every failure here is swallowed.
    """
    print("\nAn unwritable log does not stop the site being fixed:")
    was = board.DB
    board.DB = Path("/nowhere/at/all/agency.db")
    try:
        check("record returns rather than raising",
              CL.record("page", page="x.php"), 0)
        CL.note_page("x.php", html="a")          # must not raise either
        check("and reading says so instead of exploding",
              CL.recent()["ok"], False)
    finally:
        board.DB = was


def main():
    real = board.DB
    _fresh_db()
    try:
        test_a_change_is_written_down_with_a_way_back()
        test_a_refused_write_is_recorded_too()
        test_somebody_else_editing_the_site_is_noticed()
        test_a_page_appearing_and_vanishing()
        test_the_agencys_own_edit_is_not_reported_as_tampering()
        test_our_own_fix_being_overwritten_is_the_thing_it_must_catch()
        test_the_headline_says_something_useful_when_there_is_nothing()
        test_the_log_can_be_narrowed()
        test_recording_never_breaks_the_work()
    finally:
        board.DB = real

    print()
    if _fail:
        print(f"{len(_fail)} FAILED: {_fail}")
        sys.exit(1)
    print("all changelog tests passed")


if __name__ == "__main__":
    main()
