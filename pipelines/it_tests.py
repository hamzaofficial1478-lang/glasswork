"""
it_tests.py — run: python -m pipelines.it_tests   (python3 on macOS/Linux)

IT has two failure modes and they pull in opposite directions:

  * a green tick on a site nobody actually checked
  * an agent that touches production

So the tests assert that a dead enquiry path is caught and ranked above
cosmetics, that a foreign phone number is not waved through as UAE, and that
the Watcher files tickets and queues changes without ever applying one.

Everything runs against a throwaway cabinet and local fixture HTML; no real
site is touched.
"""
import os
import sqlite3
import sys
import tempfile
import threading
from pathlib import Path

from coordination import board
from . import it_health as it

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
    con.executescript((Path(__file__).parents[1] / "coordination" / "schema.sql").read_text(encoding="utf-8"))
    con.commit()
    con.close()
    return db


GOOD = """<!DOCTYPE html><html><head><title>Glassworks Dubai</title>
<meta name="viewport" content="width=device-width, initial-scale=1"></head><body>
<a href="https://wa.me/971501234567">WhatsApp</a>
<a href="tel:+97142345678">04 234 5678</a>
<form action="/enquiry"><input type="email" name="email"></form>
<a href="mailto:hello@glassworks.ae">Email</a></body></html>"""

DEAD = """<!DOCTYPE html><html><head><title>Glassworks</title></head><body>
<h1>Glass &amp; aluminium</h1><p>Call us during office hours.</p></body></html>"""


def test_uae_phone():
    """
    The bug this replaced: a loose search matched a UAE-shaped run ANYWHERE in
    the string, so +44 20 7123 4567 passed because '0 7 1234567' reads as a UAE
    landline. It has to be a UAE number from its first digit.
    """
    print("UAE phone numbers:")
    for good in ("+971 50 123 4567", "+971 4 234 5678", "00971501234567",
                 "971 4 2345678", "050 123 4567", "04-234 5678"):
        check(f"UAE: {good}", it.looks_like_uae_number(good), True)
    for bad in ("+44 20 7123 4567", "+91 22 1234 5678", "+1 555 012 3456",
                "0800 123 456", "+971 1 2345678", "", "not a number"):
        check(f"not UAE: {bad or '(empty)'}", it.looks_like_uae_number(bad), False)


def test_enquiry_path():
    print("Enquiry path (the check that matters most):")
    findings, found = it.check_enquiry_path(GOOD)
    check("a complete page finds all four routes",
          sorted(k for k, _ in found), ["email", "form", "phone", "whatsapp"])
    check("and raises nothing", findings, [])

    findings, found = it.check_enquiry_path(DEAD)
    check("a page with no route at all is CRITICAL",
          [f["severity"] for f in findings], ["critical"])
    check("and says every visitor is lost",
          "Every visitor is lost" in findings[0]["detail"], True)
    check("no routes found", found, [])

    # missing WhatsApp is high for this business specifically
    no_wa = GOOD.replace('<a href="https://wa.me/971501234567">WhatsApp</a>', "")
    findings, _ = it.check_enquiry_path(no_wa)
    check("missing WhatsApp is high, not cosmetic",
          [(f["severity"], "WhatsApp" in f["detail"]) for f in findings], [("high", True)])

    # a foreign click-to-call number is worse than none: it looks legitimate
    foreign = GOOD.replace("tel:+97142345678", "tel:+442071234567")
    findings, _ = it.check_enquiry_path(foreign)
    check("a non-UAE click-to-call is flagged high",
          any(f["severity"] == "high" and "not look like a UAE number" in f["detail"]
              for f in findings), True)


def test_health_and_hygiene():
    print("Health and hygiene:")
    check("no response is critical",
          [f["severity"] for f in it.check_health(0, 0, "https://x.ae", "timed out")],
          ["critical"])
    check("500 is critical",
          any(f["severity"] == "critical"
              for f in it.check_health(500, 100, "https://x.ae", None)), True)
    check("404 is high",
          any(f["severity"] == "high"
              for f in it.check_health(404, 100, "https://x.ae", None)), True)
    check("plain http is flagged",
          any(f["kind"] == "ssl" for f in it.check_health(200, 100, "http://x.ae", None)), True)
    check("https is not flagged",
          it.check_health(200, 100, "https://x.ae", None), [])
    check("very slow is high",
          any(f["kind"] == "speed" and f["severity"] == "high"
              for f in it.check_health(200, 6000, "https://x.ae", None)), True)
    check("a fast https page is clean",
          it.check_health(200, 300, "https://x.ae", None), [])

    check("no viewport is high (phones are where enquiries start)",
          any(f["severity"] == "high" for f in it.check_hygiene(DEAD)), True)
    check("a proper page passes hygiene", it.check_hygiene(GOOD), [])


def test_critic():
    print("Critic (no green tick on an unchecked site):")
    check("a real report passes", it.critic_check([{"severity": "high"}], True, ["whatsapp"])[0], True)
    ok, why = it.critic_check([], False, [])
    check("nothing fetched -> refuse to report",
          (ok, any("never fetched" in r for r in why)), (False, True))
    ok, why = it.critic_check([], True, [])
    check("clean report with no enquiry path is rejected",
          (ok, any("cannot be right" in r for r in why)), (False, True))


def test_claim_is_atomic():
    print("Claim, don't grab (6 Watchers, 1 task):")
    real = board.DB
    board.DB = _fresh_db()
    try:
        tid = board.post_task("IT", "health baseline", "detail")
        winners, lock = [], threading.Lock()

        def go(n):
            if board.claim_task(tid, f"watcher-{n}"):
                with lock:
                    winners.append(n)

        ts = [threading.Thread(target=go, args=(i,)) for i in range(6)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        check("exactly one Watcher claims it", len(winners), 1)
    finally:
        board.DB = real


def test_run_files_but_never_fixes():
    """
    The red line: the Watcher observes. It may file tickets and queue changes
    for the owner, but nothing it does may reach the site.
    """
    print("Files tickets, applies nothing:")
    real = board.DB
    board.DB = _fresh_db()
    saved_fetch = it._fetch
    try:
        check("refuses with no task on the board", it.run(agent="t")["ok"], False)

        board.post_task("IT", "Read-only enquiry-path + health baseline", "detail")
        it._fetch = lambda url, timeout=20: (200, DEAD, 120, None)
        r = it.run(agent="it-watcher", url="https://glassworks.ae")

        check("it runs", r["ok"], True)
        check("critic passed", r["critic"], "passed")
        check("the dead enquiry path is the worst finding", r["worst_severity"], "critical")
        check("nothing was applied to the site", r["changes_applied"], 0)
        check("but changes were queued for the owner", r["changes_queued_for_owner"] > 0, True)

        con = sqlite3.connect(board.DB)
        con.row_factory = sqlite3.Row
        check("tickets were filed",
              con.execute("SELECT count(*) FROM site_issues").fetchone()[0], r["issues_filed"])
        check("every ticket is open and attributed",
              con.execute("SELECT count(*) FROM site_issues WHERE status='open'"
                          " AND found_by='it-watcher'").fetchone()[0], r["issues_filed"])
        q = con.execute("SELECT count(*) FROM site_queue WHERE status='pending'").fetchone()[0]
        check("queued changes are pending the owner, not applied", q,
              r["changes_queued_for_owner"])
        t = dict(con.execute("SELECT status,owner FROM tasks").fetchone())
        check("the task was finished", (t["status"], t["owner"]), ("done", "it-watcher"))
        con.close()
    finally:
        it._fetch = saved_fetch
        board.DB = real


def test_no_site_configured():
    """
    'No site configured' has to be SIMULATED, not assumed.

    This test used to just pass url="" and trust the machine had no website
    saved anywhere. That is true on a fresh checkout and false on every machine
    the agency is actually running on — site_url() falls back to the process
    environment, then .env, then config/site.json, so as soon as the owner saved
    his website this test started failing on his copy for no reason connected to
    the code. Every source is silenced here instead.
    """
    from coordination import site as _site
    real = board.DB
    board.DB = _fresh_db()
    saved_env = os.environ.pop("SITE_URL", None)
    saved_from_env, saved_load = _site.from_env_file, _site.load
    _site.from_env_file = lambda: ""
    _site.load = lambda: {}
    print("No site configured:")
    try:
        board.post_task("IT", "health baseline", "detail")
        r = it.run(agent="it-watcher", url="")
        check("it refuses rather than reporting a clean site", r["ok"], False)
        check("and says what to do", "SITE_URL" in r["error"], True)
        con = sqlite3.connect(board.DB)
        check("nothing was filed",
              con.execute("SELECT count(*) FROM site_issues").fetchone()[0], 0)
        con.close()
    finally:
        _site.from_env_file, _site.load = saved_from_env, saved_load
        if saved_env is not None:
            os.environ["SITE_URL"] = saved_env
        board.DB = real


def main():
    test_uae_phone()
    test_enquiry_path()
    test_health_and_hygiene()
    test_critic()
    test_claim_is_atomic()
    test_run_files_but_never_fixes()
    test_no_site_configured()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: {_fail}")
        sys.exit(1)
    print("all IT tests passed")


if __name__ == "__main__":
    main()
