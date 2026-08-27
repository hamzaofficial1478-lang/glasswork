"""
release_tests.py — run: python -m pipelines.release_tests

The IT Releaser: the one desk allowed to write to the owner's website.

"if it cannot make changes to site then what was full acess of website and the
 files uploaded into the website?"

His approval used to go into a table nothing read. This is the desk that reads
it — which makes it the single most dangerous piece of code in the agency, so
the tests are mostly about what it REFUSES to do:

  * it never touches a change he has not approved
  * it never writes on a red test suite
  * it never runs a routine that is not on the allow-list, however the row asks
  * it never leaves a change half-applied — if the page will not confirm it, it
    goes straight back
  * and every outcome, including every failure, ends up somewhere he can see

Everything runs against a throwaway cabinet with the site stubbed out. No real
website is contacted.
"""
import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

from coordination import board, changelog
from . import it_release as R, site_api

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
    con.executescript((Path(__file__).parents[1] / "coordination" / "schema.sql")
                      .read_text(encoding="utf-8"))
    con.commit()
    con.close()
    board.DB = db
    board.ensure_schema()
    return db


def _sql(q, args=()):
    c = sqlite3.connect(board.DB)
    c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute(q, args)]
    finally:
        c.close()


VIEWPORT = '<meta name="viewport" content="width=device-width, initial-scale=1">'


def _queue(detail, status="approved"):
    """One row in his approval queue, in the shape the Fixer writes."""
    cid = board.queue_site_change("it-fixer", "fix_bug", detail)
    if status != "pending":
        board.decide_site_change(cid, status)
    return cid


def _ticket(detail="No viewport meta tag"):
    with sqlite3.connect(board.DB) as c:
        return c.execute(
            "INSERT INTO site_issues(kind,severity,detail,url,status,found_by,found_at)"
            " VALUES('bug','high',?,'https://x/','in_review','it-watcher',?)",
            (detail, int(time.time()))).lastrowid


FULL = {"mode": "full", "base": "https://x.test/agency-api/index.php",
        "token": "t" * 40, "site": "https://x.test"}


class _Stub:
    """
    His website, as a dict, stubbed at site_api.call and NOT any higher.

    THE FIRST VERSION REPLACED put_head_block ITSELF, and that quietly stopped
    testing the thing that matters most about a release: put_head_block is
    where the change log is written. With it stubbed out, the happy-path test
    asserted a log line that the real code writes and the test could never have
    produced — it failed for the right reason and for entirely the wrong cause.

    Stubbing one level down means everything above the HTTP call is the real
    code: the write, the log line, the undo command, the fingerprint refresh.
    """

    def __init__(self, pages=None, accept=True, echo=True):
        self.pages = dict(pages or {"includes/header.php": "<head></head>"})
        self.writes, self.undos = [], []
        self.accept, self.echo = accept, echo

    def __enter__(self):
        self.real = (site_api.call, site_api.settings, site_api.why_not_ready,
                     R.tests_pass)
        site_api.call = self._call
        site_api.settings = lambda env=None: dict(FULL)
        site_api.why_not_ready = lambda s=None: ""
        R.tests_pass = lambda timeout=None: {"ok": True, "took_s": 1,
                                             "why": "all suites green"}
        return self

    def __exit__(self, *a):
        (site_api.call, site_api.settings, site_api.why_not_ready,
         R.tests_pass) = self.real

    def _call(self, path, method="GET", body=None, timeout=25, s=None):
        body = body or {}
        if path == "/pages" and method == "GET":
            return {"ok": True, "pages": [{"page": p, "bytes": len(h),
                                           "changed": "t"}
                                          for p, h in self.pages.items()]}
        if path.startswith("/pages/") and method == "GET":
            name = path.split("/", 2)[2]
            if name not in self.pages:
                return {"ok": False, "error": "no such page", "status": 404}
            return {"ok": True, "page": name, "html": self.pages[name]}
        if path == "/head" and method == "POST":
            self.writes.append((body["page"], body["id"], "it-releaser"))
            if not self.accept:
                return {"ok": False, "error": "the server said no", "status": 400}
            if self.echo:
                self.pages[body["page"]] = (
                    f"<!-- agency:{body['id']} -->{body['html']}"
                    f"<!-- /agency:{body['id']} -->")
            return {"ok": True, "page": body["page"], "backup": "header.bak"}
        if path == "/pages" and method == "POST":
            self.pages[body["page"]] = body["html"]
            return {"ok": True, "page": body["page"], "backup": "page.bak"}
        if path == "/rollback" and method == "POST":
            self.undos.append(body.get("page", ""))
            self.pages[body.get("page", "")] = "<head></head>"
            return {"ok": True, "page": body.get("page", "")}
        return {"ok": False, "error": f"the stub has no {method} {path}"}


# ---------------------------------------------------------------------------

def test_nothing_he_has_not_approved_is_ever_touched():
    """
    The first guard, and the one the handbook is loudest about. The Fixer
    queues, HE decides, this applies. No desk does two of those jobs.
    """
    print("\nA change he has not answered is not a change:")
    _fresh_db()
    _queue({"issue_id": 1, "change": {"page": "includes/header.php",
                                      "block": "viewport", "html": VIEWPORT}},
           status="pending")
    with _Stub() as site:
        r = R.run()
    check("it refuses to run", r["ok"], False)
    check("nothing was written", site.writes, [])
    check("and it tells him it is waiting on HIM", r["waiting_on_you"], 1)
    check("in words, not a code",
          "waiting for your yes or no" in r["error"], True)
    check("the row is untouched",
          _sql("SELECT status FROM site_queue")[0]["status"], "pending")

    print("\n...and a change he turned down stays turned down:")
    _fresh_db()
    _queue({"issue_id": 1, "change": {"page": "includes/header.php",
                                      "block": "viewport", "html": VIEWPORT}},
           status="rejected")
    with _Stub() as site:
        r = R.run()
    check("still nothing written", site.writes, [])
    check("and it is not counted as waiting on him", r["waiting_on_you"], 0)


def test_a_red_suite_stops_everything():
    """
    Handbook: a change goes out after the tests pass, not before. Asserted here
    rather than trusted, because this is the one desk where trusting it means
    writing untested code onto the website that brings him work.
    """
    print("\nNothing goes on the website while the tests are red:")
    _fresh_db()
    cid = _queue({"issue_id": 1, "change": {"page": "includes/header.php",
                                            "block": "viewport", "html": VIEWPORT}})
    with _Stub() as site:
        R.tests_pass = lambda timeout=None: {
            "ok": False, "took_s": 12, "failing": ["FAIL  pipelines.leads_tests"],
            "why": "the test suite is red — nothing goes on the website until "
                   "it is green again: FAIL  pipelines.leads_tests"}
        r = R.run()
    check("it refuses", r["ok"], False)
    check("nothing was written", site.writes, [])
    check("it names the suite that failed", "leads_tests" in r["error"], True)
    row = _sql("SELECT status, note FROM site_queue WHERE id=?", (cid,))[0]
    check("the change is marked failed, not left as approved", row["status"], "failed")
    check("with the reason he can read", "test suite is red" in row["note"], True)
    # AND IT HAS TO BE SOMEWHERE HE LOOKS. The approval queue lists 'pending',
    # so a row going to 'failed' disappears off the screen he was watching — he
    # pressed Approve, the item vanished, and he would assume it had gone on.
    log = changelog.recent()["changes"]
    check("the failure is in the change log, not just the row", len(log), 1)
    check("marked as not having happened", log[0]["ok"], 0)
    check("in words he can act on",
          "could not be carried out" in log[0]["summary"], True)

    print("\n...but he is the tie-breaker, and using that is on the record:")
    _fresh_db()
    cid = _queue({"issue_id": 1, "change": {"page": "includes/header.php",
                                            "block": "viewport", "html": VIEWPORT}})
    with _Stub() as site:
        R.tests_pass = lambda timeout=None: (_ for _ in ()).throw(
            AssertionError("the suite must not run when it is skipped"))
        r = R.run(skip_tests=True)
    check("it goes", r["applied"], 1)
    check("and the record says the tests were skipped",
          "tests were skipped at your request"
          in _sql("SELECT note FROM site_queue WHERE id=?", (cid,))[0]["note"], True)


def test_only_the_named_routines_may_be_run():
    """
    A queue row can ask for a routine by name. getattr on whatever string is in
    the row would happily run undo_schema — which strips the agency's markup
    off every page — because a row said so. The allow-list is the difference
    between a feature and a hole.
    """
    print("\nA row cannot name any routine it likes:")
    _fresh_db()
    cid = _queue({"issue_id": 1, "change": {"run": "undo_schema"}})
    called = []
    real = site_api.undo_schema
    site_api.undo_schema = lambda *a, **k: called.append(1) or {"ok": True}
    try:
        with _Stub():
            r = R.run()
    finally:
        site_api.undo_schema = real
    check("it was not run", called, [])
    check("it is counted as refused", r["refused"], 1)
    row = _sql("SELECT status, note FROM site_queue WHERE id=?", (cid,))[0]
    check("and marked failed rather than quietly dropped", row["status"], "failed")
    check("and he can see it happened", changelog.recent()["total"], 1)
    check("naming what it would not do", "undo_schema" in row["note"], True)

    print("\n...and one that IS on the list runs:")
    _fresh_db()
    _queue({"issue_id": 1, "change": {"run": "fix_canonical", "pages": ["index.php"]}})
    ran = []
    real = site_api.fix_canonical
    site_api.fix_canonical = lambda agent=None, s=None, dry_run=False: (
        ran.append(agent) or {"ok": True, "head": "pointed 8 canonical tag(s) home"})
    try:
        with _Stub():
            r = R.run()
    finally:
        site_api.fix_canonical = real
    check("it ran", len(ran), 1)
    check("as the Releaser and nobody else", ran[0], "it-releaser")
    check("and reported as applied", r["applied"], 1)


def test_the_happy_path_end_to_end():
    print("\nHe approved it, so it goes on — and everything says so:")
    _fresh_db()
    iid = _ticket()
    cid = _queue({"issue_id": iid, "why": "phones render it desktop-sized",
                  "change": {"page": "includes/header.php", "block": "viewport",
                             "html": VIEWPORT}})
    with _Stub() as site:
        r = R.run()
    check("one change written", r["applied"], 1)
    check("to the right page and block",
          site.writes, [("includes/header.php", "viewport", "it-releaser")])
    check("and it is really on the page", VIEWPORT in site.pages["includes/header.php"], True)

    row = _sql("SELECT status, note, applied_at FROM site_queue WHERE id=?", (cid,))[0]
    check("the queue row is applied", row["status"], "applied")
    check("with when", bool(row["applied_at"]), True)
    check("and confirmation, not just 'ok'", "confirmed on the page" in row["note"], True)
    check("the ticket that started it is closed off",
          _sql("SELECT status FROM site_issues WHERE id=?", (iid,))[0]["status"],
          "deployed")

    log = changelog.recent()["changes"]
    check("it is in the change log", len(log), 1)
    check("attributed to the Releaser", log[0]["agent"], "it-releaser")
    check("with the way back", "--remove-block viewport" in log[0]["undo_cmd"], True)

    print("\n...and there is nothing left to do on the next pass:")
    with _Stub() as site:
        r2 = R.run()
    check("it says so", r2["ok"], False)
    check("and writes nothing again", site.writes, [])


def test_a_change_the_page_will_not_confirm_goes_straight_back():
    """
    'The server said ok' and 'the change is on the page' are different claims.
    A write that cannot be confirmed is undone at once: a half-applied change
    nobody knows the shape of is worse than no change at all.
    """
    print("\nWritten, not there, put back:")
    _fresh_db()
    iid = _ticket()
    cid = _queue({"issue_id": iid, "change": {"page": "includes/header.php",
                                              "block": "viewport", "html": VIEWPORT}})
    # accepts the write and does not actually change the page
    with _Stub(echo=False) as site:
        r = R.run()
    check("not counted as applied", r["applied"], 0)
    check("it was undone immediately", site.undos, ["includes/header.php"])
    row = _sql("SELECT status, note FROM site_queue WHERE id=?", (cid,))[0]
    check("marked failed", row["status"], "failed")
    check("saying nothing was left half-done",
          "Nothing has been left half-done" in row["note"], True)
    check("the ticket is NOT closed off",
          _sql("SELECT status FROM site_issues WHERE id=?", (iid,))[0]["status"],
          "in_review")
    check("and the log records the attempt rather than hiding it",
          changelog.recent()["changes"][0]["ok"], 0)

    print("\n...and a write the server simply refuses is recorded too:")
    _fresh_db()
    cid = _queue({"issue_id": 1, "change": {"page": "includes/header.php",
                                            "block": "viewport", "html": VIEWPORT}})
    with _Stub(accept=False) as site:
        r = R.run()
    check("not applied", r["applied"], 0)
    check("nothing to undo, because nothing was written", site.undos, [])
    check("marked failed with what the server said",
          "the server said no" in
          _sql("SELECT note FROM site_queue WHERE id=?", (cid,))[0]["note"], True)


def test_advice_he_approved_is_not_pretended_at():
    """
    Some queued items are advice — hosting changes, wording he has to decide.
    Approving one means 'noted', not 'do it'. There is nothing to write and the
    Releaser must not invent something adjacent that looks similar.
    """
    print("\nApproving advice does not make it a change:")
    _fresh_db()
    cid = _queue({"issue_id": 1, "verdict": "This one needs you",
                  "why": "not on HTTPS",
                  "what_to_do": ["Turn on the certificate in your hosting panel"],
                  "needs": "a change in your hosting panel"})
    with _Stub() as site:
        r = R.run()
    check("nothing was written", site.writes, [])
    check("it is counted as noted, not applied to the site", r["noted"], 1)
    row = _sql("SELECT status, note FROM site_queue WHERE id=?", (cid,))[0]
    check("it leaves his queue", row["status"], "applied")
    check("saying plainly that it was for him",
          "always something for you to do" in row["note"], True)


def test_a_site_it_cannot_reach_fails_loudly():
    print("\nNo way to reach the site is a failure he is told about:")
    _fresh_db()
    cid = _queue({"issue_id": 1, "change": {"page": "includes/header.php",
                                            "block": "viewport", "html": VIEWPORT}})
    real = site_api.why_not_ready
    site_api.why_not_ready = lambda s=None: "there is no Admin API token"
    try:
        r = R.run()
    finally:
        site_api.why_not_ready = real
    check("it refuses", r["ok"], False)
    check("and says which of the two things is missing",
          "no Admin API token" in r["error"], True)
    check("the change is marked failed, not silently left approved",
          _sql("SELECT status FROM site_queue WHERE id=?", (cid,))[0]["status"],
          "failed")
    check("and it shows up in the change log rather than vanishing",
          changelog.recent()["changes"][0]["error"], "could not reach your "
          "website: there is no Admin API token")


def test_two_releases_do_not_overlap():
    print("\nOne desk at a time, even if two are started:")
    _fresh_db()
    _queue({"issue_id": 1, "change": {"page": "includes/header.php",
                                      "block": "viewport", "html": VIEWPORT}})
    board.acquire_lock(R.LOCK, "another-releaser", wait=False, ttl=300)
    try:
        with _Stub() as site:
            r = R.run()
        check("the second one stands down", r["ok"], False)
        check("without writing anything", site.writes, [])
        check("and says why", "already going" in r["error"], True)
    finally:
        board.release_lock(R.LOCK, "another-releaser")

    print("\n...and once the first finishes, the second goes:")
    with _Stub() as site:
        r = R.run()
    check("written", r["applied"], 1)


def test_the_dry_run_writes_nothing():
    print("\n--dry-run works it all out and touches nothing:")
    _fresh_db()
    cid = _queue({"issue_id": 1, "change": {"page": "includes/header.php",
                                            "block": "viewport", "html": VIEWPORT}})
    with _Stub() as site:
        r = R.run(dry_run=True)
    check("it reports what it would do", len(r["changes"]), 1)
    check("naming the page", r["changes"][0]["page"], "includes/header.php")
    check("but wrote nothing", site.writes, [])
    check("and left the row approved, ready for the real pass",
          _sql("SELECT status FROM site_queue WHERE id=?", (cid,))[0]["status"],
          "approved")


def main():
    real = board.DB
    _fresh_db()
    try:
        test_nothing_he_has_not_approved_is_ever_touched()
        test_a_red_suite_stops_everything()
        test_only_the_named_routines_may_be_run()
        test_the_happy_path_end_to_end()
        test_a_change_the_page_will_not_confirm_goes_straight_back()
        test_advice_he_approved_is_not_pretended_at()
        test_a_site_it_cannot_reach_fails_loudly()
        test_two_releases_do_not_overlap()
        test_the_dry_run_writes_nothing()
    finally:
        board.DB = real

    print()
    if _fail:
        print(f"{len(_fail)} FAILED: {_fail}")
        sys.exit(1)
    print("all releaser tests passed")


if __name__ == "__main__":
    main()
