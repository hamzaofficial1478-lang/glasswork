"""
fix_tests.py — run: python -m pipelines.fix_tests   (python3 on macOS/Linux)

The IT Fixer. Its two failure modes pull in opposite directions and both have
already happened once in this agency, one desk over:

  * a desk that does not exist, so work routed to it waits for ever while the
    board looks calm. That is the bug this pipeline was written for.
  * a desk that answers everything — patching what it should have asked about,
    and reporting a full bench as a clean one.

So the tests assert that real tickets get real changes, that a judgement call
is handed back rather than guessed at, that a ticket with no recipe is said
out loud instead of skipped, and that nothing in the whole path ever writes to
the website.

Everything runs against a throwaway cabinet. No site is contacted: site_api's
readers are replaced, so a machine with no network and a machine with a
misconfigured site behave identically here.
"""
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

from coordination import board
from . import it_fix, site_api

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


def _file(kind, severity, detail, url="https://example.ae/"):
    with sqlite3.connect(board.DB) as c:
        cur = c.execute(
            "INSERT INTO site_issues(kind,severity,detail,url,status,found_by,found_at)"
            " VALUES(?,?,?,?, 'open', 'it-watcher', ?)",
            (kind, severity, detail, url, int(time.time())))
        return cur.lastrowid


# ---------------------------------------------------------------------------

def test_the_desk_exists_at_all():
    """
    The bug, stated as a test.

    coordination/desks.py routes anything with the word 'fix' in it to
    it-fixer, and until this pipeline was written nothing ever ran under that
    name — so the task was claimed by nobody, for ever, and the Overseer's
    'unclaimed' fault was the only thing that noticed. This asserts the desk is
    now a desk: a module that runs as it-fixer and can take a task off the
    board.
    """
    print("\nThere is now something sitting at the it-fixer desk:")
    from coordination import desks
    check("the desk is still declared", desks.DESKS["it-fixer"]["sector"], "IT")
    check("and a pipeline runs under its name", it_fix.AGENT, "it-fixer")

    board.post_task("IT", "Fix the contact form", "it is not submitting",
                    created_by="MD", desk="it-fixer")
    got = board.next_open_task("IT", "it-fixer")
    check("which means the task is claimable", bool(got), True)
    check("and it is the right one", got["title"], "Fix the contact form")
    # put it back so the pass below can claim it for real
    board.release_claim(got["id"], "owner", reason="test setup", force=True)


def test_a_fact_is_patched_and_a_judgement_is_not():
    """
    The line the Fixer must hold.

    A viewport tag is the same on every site there has ever been — patch it. A
    page title is a claim about the business — do not invent one. Getting this
    backwards in either direction is the whole risk of an agent with edit
    rights, so it is asserted on both sides rather than only on the safe one.
    """
    print("\nA fact gets a patch; a judgement gets a question:")
    ctx = {"env": {}, "business_name": "World Deal Technical Services",
           "phone": "", "emirate": "Dubai", "head_page": "includes/header.php"}

    v = it_fix.plan_for({"id": 1, "kind": "bug", "severity": "high", "url": "",
                         "detail": "No viewport meta tag — the site will render "
                                   "desktop-sized on a phone."}, ctx)
    check("the viewport tag is patched", v["how"], "patch")
    check("into the file that actually holds the head", v["page"], "includes/header.php")
    check("with the one correct value", 'width=device-width' in v["html"], True)
    check("and a way to take it out again", "remove-block" in v["undo"], True)

    t = it_fix.plan_for({"id": 2, "kind": "bug", "severity": "medium", "url": "",
                         "detail": "The page has no title."}, ctx)
    check("a missing title is NOT patched", t["how"], "advice")
    check("but a wording is drafted from what we know",
          "World Deal Technical Services" in t["steps"][0], True)
    check("and it says whose call it is", "your say-so" in t["needs"], True)

    print("\n  ...and with no head file to write to, even the fact is a question:")
    v2 = it_fix.plan_for({"id": 3, "kind": "bug", "severity": "high", "url": "",
                          "detail": "No viewport meta tag"},
                         dict(ctx, head_page=""))
    check("it does not invent a page to write to", v2["how"], "advice")
    check("and hands over the exact tag anyway",
          "width=device-width" in v2["steps"][0], True)


def test_the_number_customers_ring_is_never_guessed():
    """
    A wrong phone number is worse than a missing link: a missing link is
    obviously missing, and a wrong one looks like it works while quietly
    sending every enquiry into the void.
    """
    print("\nThe Fixer will not put a number on the site on its own say-so:")
    ctx = {"env": {}, "business_name": "", "phone": "+971 50 123 4567",
           "emirate": "", "head_page": "includes/header.php"}
    w = it_fix.plan_for({"id": 4, "kind": "form_dead", "severity": "critical",
                         "url": "", "detail": "No WhatsApp link — the main "
                                              "enquiry route for this business"}, ctx)
    check("even with the number saved, it asks first", w["how"], "advice")
    check("but the exact link is written out, ready",
          "wa.me/971501234567" in w["steps"][0], True)
    check("and it says why it is asking",
          "looks like it works" in w["needs"], True)

    print("\n  ...and with no number saved it asks for the number, not for a yes:")
    w2 = it_fix.plan_for({"id": 5, "kind": "form_dead", "severity": "critical",
                          "url": "", "detail": "No WhatsApp link"},
                         dict(ctx, phone=""))
    check("it asks for the number", w2["needs"], "your WhatsApp number")
    check("and does not pretend it has a link", "wa.me/" in str(w2["steps"]), False)


def test_hosting_problems_are_not_dressed_up_as_patches():
    print("\nSomething only the hosting company can fix says so:")
    ctx = {"env": {}, "business_name": "", "phone": "", "emirate": "", "head_page": "p"}
    s = it_fix.plan_for({"id": 6, "kind": "ssl", "severity": "high", "url": "",
                         "detail": "Site is not on HTTPS."}, ctx)
    check("no certificate is patched into a page", s["how"], "advice")
    check("and it names where the work is", "hosting panel" in s["needs"], True)

    d = it_fix.plan_for({"id": 7, "kind": "bug", "severity": "critical", "url": "",
                         "detail": "Site did not respond: timed out"}, ctx)
    check("a site that is down is not a page edit either", d["how"], "advice")
    check("and it is urgent in the owner's words",
          "urgently" in d["needs"], True)

    sp = it_fix.plan_for({"id": 8, "kind": "speed", "severity": "high", "url": "",
                          "detail": "Home page took 5400ms."}, ctx)
    check("speed is advice, ordered by what usually pays", sp["how"], "advice")
    check("and it quotes the measurement back", "5400ms" in sp["why"], True)


def test_a_ticket_with_no_recipe_is_said_out_loud():
    """
    The failure this whole pipeline exists to stop, one level down.

    A Fixer that silently skips what it does not understand rebuilds exactly
    the hole it was written to fill — work stops moving and nothing says so.
    """
    print("\nA ticket the Fixer does not understand goes UP, not into a corner:")
    _fresh_db()
    _file("broken_link", "medium", "Something the recipes have never seen")
    r = it_fix.run()
    check("the pass still succeeds", r["ok"], True)
    check("but it counts what it could not do", r["no_recipe"], 1)
    check("and nothing was queued for it", r["queued_for_you"], 0)
    # The thread body is append-only in request_msgs, never a column on the
    # request itself — see the handbook: you add to the log, you never
    # overwrite. So the assertion reads the first message, which is what a
    # person opening the thread would see.
    said = _sql("SELECT id, subject FROM requests WHERE from_sector='IT'")
    check("IT is told, once", len(said), 1)
    body = _sql("SELECT body FROM request_msgs WHERE request_id=? ORDER BY id",
                (said[0]["id"],))[0]["body"]
    check("with the ticket named", "#1" in body, True)
    check("and no pretence it is being worked", "not being worked" in body, True)


def test_the_critic_stops_a_bench_that_only_looks_worked():
    print("\nThe Critic will not let 'mostly no recipe' read as a working desk:")
    worked = [{"issue": {"id": 1}, "plan": {"how": "patch", "html": "<x>"}}]
    many = [{"id": i} for i in range(2, 8)]
    ok, why = it_fix.critic_check(worked, many)
    check("rejected", ok, False)
    check("and it says so plainly", "would be a lie" in why[0], True)

    ok2, why2 = it_fix.critic_check(
        [{"issue": {"id": 1}, "plan": {"how": "patch", "html": "   "}}], [])
    check("an empty patch is rejected too", ok2, False)
    check("naming the ticket", "issue 1" in why2[0], True)

    ok3, _ = it_fix.critic_check(worked, [{"id": 2}])
    check("but a mostly-worked bench passes", ok3, True)


def test_a_whole_pass_queues_and_never_writes():
    """
    The red line, asserted rather than trusted.

    The Fixer holds no write rights: site_api refuses it by name. This runs a
    complete pass over real tickets and then checks the two things that matter
    — the owner has decisions waiting, and the site was never called.
    """
    print("\nA full pass: the owner gets decisions, the site gets nothing:")
    _fresh_db()
    calls = []
    real_call = site_api.call
    site_api.call = lambda *a, **k: calls.append(a) or {"ok": False, "error": "no"}
    real_ready = site_api.why_not_ready
    site_api.why_not_ready = lambda s=None: "read-only in the test"
    try:
        v = _file("bug", "high", "No viewport meta tag on the home page")
        s = _file("ssl", "high", "Site is not on HTTPS.")
        _file("speed", "medium", "Home page took 2900ms.")
        board.post_task("IT", "Fix the site issues IT filed", "worst first",
                        created_by="MD", desk="it-fixer")

        r = it_fix.run()
        check("the pass ran", r["ok"], True)
        check("it claimed the board task", bool(r["claimed_task"]), True)
        # Four, not three: the task he posted is now worked as a ticket in its
        # own right. It used to be claimed and its words never read.
        check("three tickets plus the job he posted", r["tickets_read"], 4)
        check("and a change queued for each ticket", r["queued_for_you"], 3)

        q = _sql("SELECT agent, kind, detail FROM site_queue WHERE status='pending'")
        check("all queued in the Fixer's name", {x["agent"] for x in q}, {"it-fixer"})
        check("the owner sees a verdict, not a diff",
              all("verdict" in x["detail"] for x in q), True)
        check("and every one cites the ticket it came from",
              all("issue_id" in x["detail"] for x in q), True)

        check("THE SITE WAS NEVER CALLED", calls, [])

        moved = _sql("SELECT status FROM site_issues WHERE id IN (?,?)", (v, s))
        check("worked tickets move to in_review",
              {m["status"] for m in moved}, {"in_review"})

        print("\n  ...and running it again does not queue the same thing twice:")
        r2 = it_fix.run()
        check("nothing left on the bench", r2["ok"], False)
        check("and it says why in his words",
              "waiting on you" in r2["error"], True)
        check("the queue did not grow",
              len(_sql("SELECT id FROM site_queue")), 3)
    finally:
        site_api.call = real_call
        site_api.why_not_ready = real_ready


def test_a_ticket_the_world_already_fixed_is_closed_not_queued():
    print("\nA ticket overtaken by reality is closed, not put to the owner:")
    _fresh_db()
    real = site_api.canonical_plan
    site_api.canonical_plan = lambda s=None: {"ok": True, "want": "x.ae", "jobs": []}
    real_ready = site_api.why_not_ready
    site_api.why_not_ready = lambda s=None: "read-only in the test"
    try:
        i = _file("bug", "medium", "canonical tag names the wrong domain")
        r = it_fix.run()
        check("it is counted as overtaken", r["overtaken"], 1)
        check("nothing is queued", r["queued_for_you"], 0)
        check("and the ticket is closed",
              _sql("SELECT status FROM site_issues WHERE id=?", (i,))[0]["status"],
              "closed")
    finally:
        site_api.canonical_plan = real
        site_api.why_not_ready = real_ready


def test_two_fixers_do_not_patch_the_same_page():
    print("\nA page somebody else is holding is left alone, not fought over:")
    _fresh_db()
    real_ready = site_api.why_not_ready
    site_api.why_not_ready = lambda s=None: "read-only in the test"
    try:
        _file("bug", "high", "No viewport meta tag")
        board.acquire_lock("page:site", "someone-else", wait=False, ttl=60)
        try:
            r = it_fix.run()
            check("it waits its turn rather than grabbing",
                  r["held_by_someone_else"], 1)
            check("and queues nothing behind their back", r["queued_for_you"], 0)
            check("the ticket stays open for the next pass",
                  _sql("SELECT status FROM site_issues")[0]["status"], "open")
        finally:
            board.release_lock("page:site", "someone-else")

        print("\n  ...and once they let go, it goes through:")
        r2 = it_fix.run()
        check("queued this time", r2["queued_for_you"], 1)
    finally:
        site_api.why_not_ready = real_ready


def test_an_empty_bench_says_what_to_run():
    print("\nAn empty bench is not an error, and it says what to do next:")
    _fresh_db()
    r = it_fix.run()
    check("not an error, just nothing to do", r["ok"], False)
    check("and it names the command that fills the bench",
          "pipelines.it_health" in r["error"], True)


def test_worst_first():
    print("\nThe worst ticket is worked first, not the oldest:")
    _fresh_db()
    low = _file("speed", "low", "slightly slow")
    time.sleep(0.01)
    crit = _file("bug", "critical", "Site did not respond: refused")
    con = sqlite3.connect(board.DB)
    con.row_factory = sqlite3.Row
    try:
        order = [i["id"] for i in it_fix.open_issues(con)]
    finally:
        con.close()
    check("critical before low, whatever the order they arrived in",
          order, [crit, low])


def main():
    real = board.DB
    _fresh_db()
    try:
        test_the_desk_exists_at_all()
        test_a_fact_is_patched_and_a_judgement_is_not()
        test_the_number_customers_ring_is_never_guessed()
        test_hosting_problems_are_not_dressed_up_as_patches()
        test_a_ticket_with_no_recipe_is_said_out_loud()
        test_the_critic_stops_a_bench_that_only_looks_worked()
        test_a_whole_pass_queues_and_never_writes()
        test_a_ticket_the_world_already_fixed_is_closed_not_queued()
        test_two_fixers_do_not_patch_the_same_page()
        test_an_empty_bench_says_what_to_run()
        test_worst_first()
    finally:
        board.DB = real

    if _fail:
        print(f"\n{len(_fail)} FAILED: {_fail}")
        sys.exit(1)
    print("\nall fixer tests passed")


if __name__ == "__main__":
    main()
