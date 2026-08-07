"""
report_tests.py — run: python -m pipelines.report_tests   (python3 on macOS/Linux)

Report's one job that can go wrong quietly is scoring a decision it has no
real evidence for. So these tests pin down, per cycle: worked when the real
evidence is there, failed only once the grace window has actually passed,
and left unscored — never guessed — while still inside it. Self-verifying
cycles (security-screen, site-services, leads-sources) score worked
immediately, because their own pipeline already gated the decision behind a
passed critic before it was ever logged.

Everything runs against a throwaway cabinet; no real Telegram message is
ever sent (send_message is monkeypatched to prove nothing goes out).
"""
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

from coordination import board
from providers import integrations
from . import report_daily as report

_fail = []
_sent = []


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
    con.executescript((Path(__file__).parents[1] / "coordination" / "schema.sql").read_text())
    con.commit()
    con.close()
    return db


def _now():
    return int(time.time())


def main():
    real_db = board.DB
    board.DB = _fresh_db()
    real_send = integrations.send_message
    integrations.send_message = lambda text, **kw: (_sent.append(text) or {"ok": True, "detail": "faked"})
    try:
        _scoring_tests()
        _digest_tests()
    finally:
        board.DB = real_db
        integrations.send_message = real_send

    if _fail:
        print(f"\n{len(_fail)} FAILED: {_fail}")
        sys.exit(1)
    print("\nall report tests passed")


def _mk_decision(cycle, created_at, con):
    did = board.log_decision(cycle, reasoning="test", expectation="test", confidence=0.7)
    con.execute("UPDATE decisions SET created_at=? WHERE id=?", (created_at, did))
    con.commit()
    return did


def _outcome(con, did):
    r = con.execute("SELECT outcome FROM decisions WHERE id=?", (did,)).fetchone()
    return r[0] if r else None


def _scoring_tests():
    con = sqlite3.connect(board.DB)

    print("Self-verifying cycles score worked immediately:")
    for cycle in ("security-screen", "site-services", "leads-sources"):
        did = _mk_decision(cycle, _now(), con)
        scored = report.score_pending_decisions(con)
        con.commit()
        row = next((s for s in scored if s["decision_id"] == did), None)
        check(f"{cycle} scored", row and row["outcome"], "worked")
        check(f"{cycle} outcome persisted", _outcome(con, did), "worked")

    print("seo-gap waits for Content, then scores on real evidence:")
    did_pending = _mk_decision("seo-gap", _now(), con)
    scored = report.score_pending_decisions(con)
    check("fresh seo-gap decision stays unscored", _outcome(con, did_pending), None)
    check("and is not in the scored list", did_pending in [s["decision_id"] for s in scored], False)

    # failed case first, while content_queue is still empty — no cross-contamination
    did_failed = _mk_decision("seo-gap", _now() - report.GRACE["seo-gap"] - 10, con)
    report.score_pending_decisions(con)
    check("seo-gap fails after the grace window with nothing briefed",
          _outcome(con, did_failed), "failed")

    did_worked = _mk_decision("seo-gap", _now() - 1000, con)
    con.execute("INSERT INTO content_queue(title,type,status,created_at) VALUES(?,?,?,?)",
                ("Glass partition Dubai", "service_page", "briefed", _now()))
    con.commit()
    report.score_pending_decisions(con)
    check("seo-gap worked once Content briefed something after it", _outcome(con, did_worked), "worked")

    print("it-health: clean check with nothing urgent scores worked outright:")
    did = _mk_decision("it-health", _now(), con)
    report.score_pending_decisions(con)
    check("no urgent issues -> worked", _outcome(con, did), "worked")

    print("it-health: urgent issue resolved scores worked:")
    t = _now()
    did = _mk_decision("it-health", t, con)
    con.execute("INSERT INTO site_issues(kind,severity,detail,url,status,found_by,found_at)"
                " VALUES('form_dead','critical','no whatsapp','https://x.ae','closed','it-watcher',?)", (t,))
    con.commit()
    report.score_pending_decisions(con)
    check("resolved urgent issue -> worked", _outcome(con, did), "worked")

    print("it-health: urgent issue still open, inside grace -> unscored:")
    t = _now()
    did = _mk_decision("it-health", t, con)
    con.execute("INSERT INTO site_issues(kind,severity,detail,url,status,found_by,found_at)"
                " VALUES('form_dead','critical','no whatsapp','https://x.ae','open','it-watcher',?)", (t,))
    con.commit()
    report.score_pending_decisions(con)
    check("still open, too soon -> unscored", _outcome(con, did), None)

    print("it-health: urgent issue still open past grace -> failed:")
    t = _now() - report.GRACE["it-health"] - 10
    did = _mk_decision("it-health", t, con)
    con.execute("INSERT INTO site_issues(kind,severity,detail,url,status,found_by,found_at)"
                " VALUES('form_dead','critical','no whatsapp','https://x.ae','open','it-watcher',?)", (t,))
    con.commit()
    report.score_pending_decisions(con)
    check("still open past grace -> failed", _outcome(con, did), "failed")

    print("leads-hunt: tagging evidence checked immediately, no waiting:")
    # each sub-case starts from an empty leads table — the +/-5s evidence window
    # is real wall-clock time, and these sub-cases run within the same second.
    con.execute("DELETE FROM leads"); con.commit()
    t = _now()
    did = _mk_decision("leads-hunt", t, con)
    con.execute("INSERT INTO leads(name,company,segment,found_at) VALUES('A','A Co','hotel',?)", (t,))
    con.execute("INSERT INTO leads(name,company,segment,found_at) VALUES('B','B Co','hotel',?)", (t,))
    con.commit()
    report.score_pending_decisions(con)
    check("all leads tagged -> worked", _outcome(con, did), "worked")

    con.execute("DELETE FROM leads"); con.commit()
    t = _now()
    did = _mk_decision("leads-hunt", t, con)
    con.execute("INSERT INTO leads(name,company,segment,found_at) VALUES('C','C Co','',?)", (t,))
    con.commit()
    report.score_pending_decisions(con)
    check("no leads tagged -> failed", _outcome(con, did), "failed")

    con.execute("DELETE FROM leads"); con.commit()
    t = _now()
    did = _mk_decision("leads-hunt", t, con)
    con.execute("INSERT INTO leads(name,company,segment,found_at) VALUES('D','D Co','hotel',?)", (t,))
    con.execute("INSERT INTO leads(name,company,segment,found_at) VALUES('E','E Co','',?)", (t,))
    con.commit()
    report.score_pending_decisions(con)
    check("mixed tagging -> partial", _outcome(con, did), "partial")

    print("Unknown cycle is left alone, never guessed:")
    did = _mk_decision("some-future-pipeline", _now(), con)
    scored = report.score_pending_decisions(con)
    check("unknown cycle stays unscored", _outcome(con, did), None)
    check("and is not claimed as scored", did in [s["decision_id"] for s in scored], False)

    con.close()


def _digest_tests():
    print("The digest runs clean on an empty cabinet, no invented numbers:")
    con = sqlite3.connect(board.DB)
    con.execute("DELETE FROM decisions"); con.execute("DELETE FROM leads")
    con.execute("DELETE FROM site_issues"); con.execute("DELETE FROM daily_reports")
    con.commit(); con.close()

    out = report.run()
    check("it runs", out["ok"], True)
    check("critic passed", out.get("critic"), "passed")
    check("no competitors invented", "0 competitors on file" in out["summary"], True)
    check("nothing urgent -> says so plainly", out["actions"], ["Nothing urgent today - the board is clear."])

    con = sqlite3.connect(board.DB)
    n = con.execute("SELECT count(*) FROM daily_reports").fetchone()[0]
    check("exactly one digest row written", n, 1)
    con.close()

    print("A real critical issue becomes the top action, not buried:")
    con = sqlite3.connect(board.DB)
    con.execute("INSERT INTO site_issues(kind,severity,detail,url,status,found_by,found_at)"
                " VALUES('form_dead','critical','no whatsapp','https://x.ae','open','it-watcher',?)", (_now(),))
    con.commit(); con.close()
    out = report.run()
    check("critical issue leads the actions", "critical site issue" in out["actions"][0], True)

    print("Nothing is ever sent without a real Telegram config:")
    check("digest attempted a send (faked, nothing real left the building)", len(_sent) >= 1, True)


if __name__ == "__main__":
    main()
