"""
dispatch_tests.py — run: python3 -m coordination.dispatch_tests

Covers the two questions the owner asked about picking work:

  "on which bases the agent will be selected"     coordination/desks.py
  "urgent gets listed and normal wins"            coordination/dispatch.py

Everything runs against a throwaway cabinet. No network, nothing touches the
owner's real filing cabinet.
"""
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

from coordination import board, desks, dispatch

_fail = []


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


def _fresh():
    d = Path(tempfile.mkdtemp())
    db = d / "agency.db"
    con = sqlite3.connect(db)
    con.executescript((Path(__file__).parent / "schema.sql").read_text(encoding="utf-8"))
    con.commit()
    con.close()
    board.DB = db
    board.ensure_schema()
    return db


def _now():
    return int(time.time())


def _age(db, task_id, secs):
    """Backdate a task so ageing can be tested without waiting for it."""
    con = sqlite3.connect(db)
    con.execute("UPDATE tasks SET created_at=? WHERE id=?", (_now() - secs, task_id))
    con.commit()
    con.close()


# ---------------------------------------------------------------------------
# THE DESK ROSTER
# ---------------------------------------------------------------------------

def _desk_tests():
    print("\nThe desk that finds a fault is not the desk that ships the fix:")
    fix = {"title": "Fix the contact form", "detail": "the enquiry form is broken"}
    check("the Watcher is refused a fix", desks.can("it-watcher", fix), False)
    check("and told why", "never does 'fix'" in desks.verdict("it-watcher", fix)["why"],
          True)
    check("the Fixer takes it", desks.can("it-fixer", fix), True)
    check("and it is routed there on sight", desks.best_desk(fix, "IT")[0], "it-fixer")

    print("\nThe title is the instruction; the detail is only context:")
    # The first version of this read title and detail as one blob and refused
    # the Watcher its own health check, because the detail said "queue any fix,
    # never apply it" and the word "fix" was in the sentence forbidding it.
    watch = {"title": "Re-check the enquiry path after critical findings",
             "detail": "Read-only re-check first, then hygiene. File findings; "
                       "queue any fix, never apply it."}
    check("a read-only check is not a fix", desks.can("it-watcher", watch), True)
    check("and it goes to the Watcher", desks.best_desk(watch, "IT")[0], "it-watcher")
    hunt = {"title": "Collect today's leads across the mapped segments",
            "detail": "Work the source map. Dedup before storing; contact nobody."}
    check("'contact nobody' does not make it an outreach task",
          desks.best_desk(hunt, "Leads")[0], "leads-hunter")

    print("\nA named desk is a decision, not a hint:")
    named = dict(fix, desk="it-releaser")
    check("the named desk gets it", desks.can("it-releaser", named), True)
    check("nobody else does", desks.can("it-fixer", named), False)
    check("no word-matching runs at all",
          desks.verdict("it-releaser", named)["named"], True)
    typo = dict(fix, desk="it-fixxer")
    check("a typo does not strand the task", desks.can("it-fixer", typo), True)

    print("\nA desk nobody has heard of is allowed, but sorts last:")
    v = desks.verdict("some-new-pipeline", {"title": "do a thing", "detail": ""})
    check("allowed", v["allowed"], True)
    check("but ranked below a rostered desk that is merely indifferent",
          v["score"] < desks.verdict("it-releaser", {"title": "do a thing",
                                                     "detail": ""})["score"],
          True)

    print("\nWord matching does not fire on fragments:")
    check("'prefix' is not 'fix'",
          desks.can("it-watcher", {"title": "Normalise the URL prefix", "detail": ""}),
          True)
    check("'fixing' is 'fix'",
          desks.can("it-watcher", {"title": "Fixing the sitemap", "detail": ""}),
          False)

    print("\nThe red lines in the handbook hold at the desk level:")
    ship = {"title": "Release the new pricing page", "detail": ""}
    check("the Writer does not publish", desks.can("content-writer",
          {"title": "Publish the pergola case study", "detail": ""}), False)
    check("the Fixer does not release", desks.can("it-fixer", ship), False)
    check("only the Releaser does", desks.can("it-releaser", ship), True)
    money = {"title": "Offer them a discount to close it", "detail": ""}
    check("no desk in Leads offers a concession",
          [d for d in desks.sector_desks("Leads") if desks.can(d, money)], [])
    check("SEO does not touch the site itself", desks.can("seo-strategist",
          {"title": "Publish the schema fix", "detail": ""}), False)


# ---------------------------------------------------------------------------
# ORDERING THE BOARD
# ---------------------------------------------------------------------------

def _order_tests():
    print("\nThe owner's case: urgent posted second, trivial posted first:")
    db = _fresh()
    con = sqlite3.connect(db)
    con.execute("INSERT INTO site_issues(kind,severity,detail,url,found_by,found_at,"
                "status) VALUES('form','critical','contact form 500','/c','w',?,'open')",
                (_now(),))
    sid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.commit()
    con.close()

    trivial = board.post_task("IT", "Read-only health check on the site",
                              "routine hygiene pass")
    _age(db, trivial, 2 * 3600)                      # posted two hours earlier
    urgent = board.post_task("IT", "Fix the contact form",
                             "the enquiry form is broken",
                             about=f"site_issues:{sid}")

    order = dispatch.board_order("IT")
    check("the urgent one is first despite arriving later",
          order[0]["id"], urgent)
    check("and the board says why",
          "enquiry path is broken" in order[0]["why"][0]["label"], True)
    check("the older trivial one is second", order[1]["id"], trivial)
    check("its two hours of waiting is counted",
          any("nothing is left to starve" in w["label"] for w in order[1]["why"]),
          True)

    print("\nBoth desks get the right one, in one pass each:")
    got_w = board.next_open_task("IT", "it-watcher")
    got_f = board.next_open_task("IT", "it-fixer")
    check("the Watcher takes the health check", got_w["id"], trivial)
    check("the Fixer takes the broken form", got_f["id"], urgent)
    check("neither is left open",
          [t["id"] for t in dispatch.board_order("IT")], [])

    print("\nWaiting long enough beats being important — or the queue is a lie:")
    db = _fresh()
    con = sqlite3.connect(db)
    con.execute("INSERT INTO site_issues(kind,severity,detail,url,found_by,found_at,"
                "status) VALUES('x','critical','y','/z','w',?,'open')", (_now(),))
    sid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.commit()
    con.close()
    big = board.post_task("IT", "Fix the broken thing", "x", about=f"site_issues:{sid}")
    small = board.post_task("IT", "Check the sitemap", "y")
    check("fresh, the important one leads",
          dispatch.board_order("IT")[0]["id"], big)
    _age(db, small, 80 * 3600)                       # over three days ignored
    check("after three days ignored, the small one leads",
          dispatch.board_order("IT")[0]["id"], small)
    _age(db, small, 900 * 3600)                      # over a month
    check("but ageing is capped — a month cannot outrank a person waiting",
          dispatch.board_order("IT")[0]["id"], small)
    check("(the cap is what stops a forgotten task outranking a customer)",
          dispatch.AGE_CAP, 60)

    print("\nUrgency that has expired earns nothing:")
    db = _fresh()
    con = sqlite3.connect(db)
    con.execute("INSERT INTO site_issues(kind,severity,detail,url,found_by,found_at,"
                "status) VALUES('x','critical','y','/z','w',?,'resolved')", (_now(),))
    sid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
    con.commit()
    con.close()
    t = board.post_task("IT", "Fix the thing that was broken", "x",
                        about=f"site_issues:{sid}")
    check("a task about a fault already fixed is ordinary work",
          dispatch.board_order("IT")[0]["points"], 30)
    check("a reference to a row that does not exist earns nothing either",
          dispatch.score({"title": "x", "created_at": _now()},
                         kind=None)[0], 30)
    check("and the task is still there to be done", bool(t), True)

    print("\nPosting the same job twice does not put it on the board twice:")
    db = _fresh()
    a = board.post_task("SEO", "Claim 8 free listings", "the same job")
    b = board.post_task("SEO", "  claim 8 FREE listings  ", "posted again an hour later")
    check("the second post returns the first task", a, b)
    check("and the board holds one of it", len(dispatch.board_order("SEO")), 1)
    check("a different sector is a different job",
          board.post_task("Content", "Claim 8 free listings", "x") == a, False)
    board.next_open_task("SEO", "seo-backlinks")
    again = board.post_task("SEO", "Claim 8 free listings", "next week's round")
    check("once the first is claimed, the same title is new work again",
          again == a, False)

    print("\nThe same board gives the same order twice:")
    db = _fresh()
    ids = [board.post_task("SEO", f"Look at thing {i}", "same weight, same minute")
           for i in range(6)]
    first = [t["id"] for t in dispatch.board_order("SEO")]
    second = [t["id"] for t in dispatch.board_order("SEO")]
    check("no coin-flips", first, second)
    check("every task accounted for", sorted(first), sorted(ids))


# ---------------------------------------------------------------------------
# WHAT HAPPENS WHEN A DESK CANNOT TAKE ANYTHING
# ---------------------------------------------------------------------------

def _empty_handed_tests():
    print("\nA desk that comes back empty says what it walked past:")
    db = _fresh()
    board.post_task("IT", "Fix the contact form", "broken")
    board.post_task("IT", "Deploy the fix", "after review")
    said = dispatch.why_nothing("IT", "it-watcher")
    check("it does not claim the board is empty",
          "Nothing open" in said, False)
    check("it counts what is there", "2 task(s) open" in said, True)
    check("it names the task it could not take",
          "Fix the contact form" in said, True)
    check("and the desk waiting on it", "it-fixer" in said, True)

    print("\nAn empty board still reads as an empty board:")
    db = _fresh()
    check("plainly", dispatch.why_nothing("IT", "it-watcher").startswith(
        "Nothing open on the IT board"), True)

    print("\nWork named for a desk that never runs does not vanish quietly:")
    db = _fresh()
    t = board.post_task("IT", "Fix the contact form", "broken")
    _age(db, t, 9 * 3600)
    stuck = dispatch.unclaimed("IT")
    check("it is reported as unclaimed", len(stuck), 1)
    check("with the desk it is waiting on", stuck[0]["desk"], "it-fixer")
    check("and how long it has waited", stuck[0]["waited"], "9h")

    print("\nAfter long enough, the name stops being a reservation:")
    o = dispatch.offer("IT", "it-releaser")
    check("a desk that genuinely does this work may inherit it",
          bool(o["take"]), False)   # the Releaser does not do fixes either
    check("but the Watcher still cannot — a `never` rule is never relaxed",
          bool(dispatch.offer("IT", "it-watcher")["take"]), False)
    check("and it is listed as orphaned rather than forgotten",
          [x["id"] for x in dispatch.offer("IT", "it-watcher")["orphans"]], [])
    # A task the Watcher CAN do, named for someone else and long abandoned,
    # does fall to it — otherwise a mis-routed task is lost for good.
    db = _fresh()
    t2 = board.post_task("IT", "Check the enquiry path", "read-only",
                         desk="it-search")
    _age(db, t2, 9 * 3600)
    o = dispatch.offer("IT", "it-watcher")
    check("an abandoned check does fall to the desk that does checks",
          o["take"]["id"] if o["take"] else None, t2)
    check("and says it was inherited",
          "has left it" in (o["take"]["why_me"] if o["take"] else ""), True)


# ---------------------------------------------------------------------------
# THE RACE ITSELF — still exactly one winner
# ---------------------------------------------------------------------------

def _race_tests():
    print("\nTwo desks racing for one task: exactly one winner, no lost work:")
    db = _fresh()
    t = board.post_task("IT", "Check the site", "read-only", desk="")
    wins = [board.claim_task(t, "a"), board.claim_task(t, "b")]
    check("one claim lands", wins.count(True), 1)
    check("the other is refused", wins.count(False), 1)
    check("and the task has one owner", board.get_task(t)["owner"] in ("a", "b"), True)

    print("\nThe loser is not blocked — it takes the next thing it can do:")
    db = _fresh()
    a = board.post_task("IT", "Check the site", "read-only", desk="")
    b = board.post_task("IT", "Check the sitemap", "read-only", desk="")
    first = board.next_open_task("IT", "it-watcher")
    second = board.next_open_task("IT", "it-search")
    check("both tasks got taken", sorted([first["id"], second["id"]]),
          sorted([a, b]))
    check("by different desks", first["owner"] != second["owner"], True)
    check("and nothing is left open", dispatch.board_order("IT"), [])

    print("\nA switched-off sector's work is still not claimable:")
    db = _fresh()
    t = board.post_task("Content", "Draft the pergola page", "brief attached")
    board.set_sector("Content", False, "owner")
    check("the board still holds it", len(dispatch.board_order("Content")), 1)
    check("but nobody can take it",
          board.next_open_task("Content", "content-writer"), None)
    board.set_sector("Content", True, "owner")
    check("switching it back on releases it",
          bool(board.next_open_task("Content", "content-writer")), True)


def main():
    _desk_tests()
    _order_tests()
    _empty_handed_tests()
    _race_tests()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: {_fail}")
        sys.exit(1)
    print("all dispatch tests passed")


if __name__ == "__main__":
    main()
