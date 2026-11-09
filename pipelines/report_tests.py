"""
report_tests.py — run: python -m pipelines.report_tests   (python3 on macOS/Linux)

Report's one job that can go wrong quietly is scoring a decision it has no real
evidence for. So these tests pin down, per cycle: worked when the evidence is
there and belongs to THAT decision, failed only once the grace window has
actually passed, and left unscored — never guessed — while still inside it.

The hard part is attribution. Scoring used to match evidence to a decision by
wall-clock proximity, which meant an unrelated brief scored an SEO gap list, one
brief scored every pending gap list at once, and a health check that could not
find its own tickets read that as "nothing was wrong" and scored an open
critical issue as worked. Every decision now records WHICH rows it is a claim
about, and these tests hold that line: evidence belonging to somebody else must
never move the score.

Everything runs against a throwaway cabinet; no real Telegram message is
ever sent (send_message is monkeypatched to prove nothing goes out).
"""
import json
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
    con.executescript((Path(__file__).parents[1] / "coordination" / "schema.sql").read_text(encoding="utf-8"))
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


def _mk_decision(cycle, created_at, con, evidence=None):
    did = board.log_decision(cycle, reasoning="test", expectation="test",
                             confidence=0.7, evidence=evidence)
    con.execute("UPDATE decisions SET created_at=? WHERE id=?", (created_at, did))
    con.commit()
    return did


def _issue(con, severity, status, at, url="https://x.ae"):
    cur = con.execute(
        "INSERT INTO site_issues(kind,severity,detail,url,status,found_by,found_at)"
        " VALUES('form_dead',?,'no whatsapp',?,?,'it-watcher',?)",
        (severity, url, status, at))
    con.commit()
    return cur.lastrowid


def _lead(con, company, segment, at):
    cur = con.execute("INSERT INTO leads(name,company,segment,found_at) VALUES(?,?,?,?)",
                      (company, company, segment, at))
    con.commit()
    return cur.lastrowid


def _brief(con, title, status, at):
    cur = con.execute(
        "INSERT INTO content_queue(title,type,status,created_at) VALUES(?,?,?,?)",
        (title, "service_page", status, at))
    con.commit()
    return cur.lastrowid


def _outcome(con, did):
    r = con.execute("SELECT outcome FROM decisions WHERE id=?", (did,)).fetchone()
    return r[0] if r else None


def _scoring_tests():
    con = sqlite3.connect(board.DB)

    print("Nothing is marked 'worked' without being checked:")
    for cycle in ("security-screen", "site-services", "leads-sources"):
        did = _mk_decision(cycle, _now(), con)
        scored, waiting = report.score_pending_decisions(con)
        con.commit()
        check(f"{cycle} is not waved through", _outcome(con, did), None)
        check(f"{cycle} says why it is unscored",
              bool(next((w["why"] for w in waiting if w["decision_id"] == did), None)), True)
    con.execute("DELETE FROM decisions"); con.commit()

    print("A decision with no recorded evidence cannot be scored at all:")
    did = _mk_decision("seo-gap", _now() - report.GRACE["seo-gap"] - 10, con)  # no evidence
    scored, waiting = report.score_pending_decisions(con)
    check("old evidence-less decision stays unscored", _outcome(con, did), None)
    check("and is not claimed as scored", did in [x["decision_id"] for x in scored], False)
    con.execute("DELETE FROM decisions"); con.commit()

    print("seo-gap scores only on a brief about ITS OWN phrases:")
    ev = {"phrases": ["frameless shower screens dubai"]}
    did = _mk_decision("seo-gap", _now() - 1000, con, ev)
    _brief(con, "Aluminium Windows Abu Dhabi", "briefed", _now())
    report.score_pending_decisions(con)
    check("a brief about something else does not score it", _outcome(con, did), None)

    _brief(con, "Frameless Shower Screens Dubai - service page", "briefed", _now())
    report.score_pending_decisions(con)
    check("a brief about its own phrase does", _outcome(con, did), "worked")

    con.execute("DELETE FROM decisions"); con.execute("DELETE FROM content_queue"); con.commit()
    did = _mk_decision("seo-gap", _now() - report.GRACE["seo-gap"] - 10, con, ev)
    report.score_pending_decisions(con)
    check("nothing briefed past the grace window -> failed", _outcome(con, did), "failed")
    con.execute("DELETE FROM decisions"); con.commit()

    print("seo-gap is judged by the PAGE its phrases belong to, as the briefs desk is:")
    def _page(url, at):
        con.execute("INSERT INTO content_queue(title,type,brief,status,created_at)"
                    " VALUES(?,?,?,?,?)",
                    (url, "service_page", json.dumps({"url": url}), "briefed", at))
        con.commit()
    ev = {"phrases": ["shower screen repair dubai marina"]}
    did = _mk_decision("seo-gap", _now() - 1000, con, ev)
    _page("/shower-glass-dubai-marina", _now())
    report.score_pending_decisions(con)
    check("its page briefed after the list, without the exact words -> worked",
          _outcome(con, did), "worked")
    con.execute("DELETE FROM decisions"); con.execute("DELETE FROM content_queue"); con.commit()

    _page("/shower-glass-dubai-marina", _now() - report.GRACE["seo-gap"] - 500)
    did = _mk_decision("seo-gap", _now() - report.GRACE["seo-gap"] - 10, con, ev)
    scored, waiting = report.score_pending_decisions(con)
    check("its page was already briefed before the list -> not a failure",
          _outcome(con, did), None)
    check("and it says why",
          "already briefed" in next(w["why"] for w in waiting if w["decision_id"] == did),
          True)
    con.execute("DELETE FROM decisions"); con.execute("DELETE FROM content_queue"); con.commit()

    print("content-briefs follows the pages it actually created:")
    b1 = _brief(con, "Page one", "briefed", _now())
    did = _mk_decision("content-briefs", _now() - 100, con, {"content_queue_ids": [b1]})
    _brief(con, "A page this run never briefed", "drafted", _now())
    report.score_pending_decisions(con)
    check("somebody else's page moving does not score it", _outcome(con, did), None)

    con.execute("UPDATE content_queue SET status='drafted' WHERE id=?", (b1,)); con.commit()
    report.score_pending_decisions(con)
    check("its own page moving does", _outcome(con, did), "worked")
    con.execute("DELETE FROM decisions"); con.execute("DELETE FROM content_queue"); con.commit()

    print("it-health scores the tickets THIS check filed, and no others:")
    did = _mk_decision("it-health", _now(), con, {"site_issue_ids": []})
    report.score_pending_decisions(con)
    check("a check that filed nothing -> worked", _outcome(con, did), "worked")

    # the bug that mattered: the ticket is real, open, critical, and was filed
    # further from the decision than any time window would allow
    con.execute("DELETE FROM decisions"); con.commit()
    filed = _now() - 3600
    iid = _issue(con, "critical", "open", filed)
    did = _mk_decision("it-health", filed + 30, con, {"site_issue_ids": [iid]})
    report.score_pending_decisions(con)
    check("an open critical ticket is NEVER scored worked", _outcome(con, did), None)

    con.execute("UPDATE site_issues SET status='closed' WHERE id=?", (iid,)); con.commit()
    report.score_pending_decisions(con)
    check("once resolved it scores worked", _outcome(con, did), "worked")

    con.execute("DELETE FROM decisions"); con.execute("DELETE FROM site_issues"); con.commit()
    old = _now() - report.GRACE["it-health"] - 10
    iid = _issue(con, "critical", "open", old)
    did = _mk_decision("it-health", old, con, {"site_issue_ids": [iid]})
    report.score_pending_decisions(con)
    check("still open past the grace window -> failed", _outcome(con, did), "failed")

    con.execute("DELETE FROM decisions"); con.execute("DELETE FROM site_issues"); con.commit()
    iid = _issue(con, "low", "open", _now())
    did = _mk_decision("it-health", _now(), con, {"site_issue_ids": [iid]})
    report.score_pending_decisions(con)
    check("only low-severity tickets -> worked", _outcome(con, did), "worked")
    con.execute("DELETE FROM decisions"); con.execute("DELETE FROM site_issues"); con.commit()

    print("leads-hunt judges the leads THIS run stored:")
    t = _now()
    a, b = _lead(con, "A Co", "hotel", t), _lead(con, "B Co", "hotel", t)
    did = _mk_decision("leads-hunt", t, con, {"lead_ids": [a, b]})
    _lead(con, "Untagged from some other run", "", t)      # must not drag it down
    report.score_pending_decisions(con)
    check("another run's untagged lead does not fail it", _outcome(con, did), "worked")

    con.execute("DELETE FROM decisions"); con.execute("DELETE FROM leads"); con.commit()
    t = _now()
    c = _lead(con, "C Co", "", t)
    did = _mk_decision("leads-hunt", t, con, {"lead_ids": [c]})
    report.score_pending_decisions(con)
    check("none of its own tagged -> failed", _outcome(con, did), "failed")

    con.execute("DELETE FROM decisions"); con.execute("DELETE FROM leads"); con.commit()
    t = _now()
    d, e = _lead(con, "D Co", "hotel", t), _lead(con, "E Co", "", t)
    did = _mk_decision("leads-hunt", t, con, {"lead_ids": [d, e]})
    report.score_pending_decisions(con)
    check("half tagged -> partial", _outcome(con, did), "partial")
    con.execute("DELETE FROM decisions"); con.execute("DELETE FROM leads"); con.commit()

    print("The verdict's reason is written down, not just returned:")
    iid = _issue(con, "high", "closed", _now())
    did = _mk_decision("it-health", _now(), con, {"site_issue_ids": [iid]})
    report.score_pending_decisions(con)
    note = con.execute("SELECT outcome_note FROM decisions WHERE id=?", (did,)).fetchone()[0]
    check("the scorecard records WHY", bool(note and note.strip()), True)
    con.execute("DELETE FROM decisions"); con.execute("DELETE FROM site_issues"); con.commit()

    print("security-screen is judged on whether the gate actually held:")
    t = _now()
    did = _mk_decision("security-screen", t, con, {"request_ids": ["r1"]})
    report.score_pending_decisions(con)
    check("nothing sent yet -> no verdict, the gate was not tested",
          _outcome(con, did), None)

    # a mail goes out, and Security had nothing waiting
    con.execute("INSERT INTO outreach_log(lead_id,channel,direction,body,at)"
                " VALUES(1,'email','sent','hello',?)", (t + 5,))
    con.commit()
    report.score_pending_decisions(con)
    check("outreach with a clear Security board -> worked", _outcome(con, did), "worked")

    # now the failing shape: outreach while a Security request sits unanswered
    con.execute("DELETE FROM decisions"); con.execute("DELETE FROM outreach_log")
    con.execute("DELETE FROM requests"); con.commit()
    t = _now()
    did = _mk_decision("security-screen", t, con, {"request_ids": ["r2"]})
    con.execute("INSERT INTO requests(id,from_sector,to_sector,subject,status,created_at)"
                " VALUES('q1','Leads','Security','clear this lead','open',?)", (t + 1,))
    con.execute("INSERT INTO outreach_log(lead_id,channel,direction,body,at)"
                " VALUES(2,'email','sent','hello',?)", (t + 9,))
    con.commit()
    report.score_pending_decisions(con)
    check("a sector that did not wait -> failed", _outcome(con, did), "failed")
    note = con.execute("SELECT outcome_note FROM decisions WHERE id=?", (did,)).fetchone()[0]
    check("and the note names the jumped gate", "did not wait" in note, True)

    # never tested and out of time -> still no verdict, never a guess
    con.execute("DELETE FROM decisions"); con.execute("DELETE FROM outreach_log")
    con.execute("DELETE FROM requests"); con.commit()
    old_t = _now() - report.GRACE["security-screen"] - 10
    did = _mk_decision("security-screen", old_t, con, {"request_ids": ["r3"]})
    report.score_pending_decisions(con)
    check("never tested, past the window -> still unscored", _outcome(con, did), None)
    con.execute("DELETE FROM decisions"); con.commit()

    print("Unknown cycle is left alone, never guessed:")
    did = _mk_decision("some-future-pipeline", _now(), con, {"anything": 1})
    scored, waiting = report.score_pending_decisions(con)
    check("unknown cycle stays unscored", _outcome(con, did), None)
    check("and is not claimed as scored", did in [x["decision_id"] for x in scored], False)
    check("but it is reported as waiting, not silently dropped",
          did in [w["decision_id"] for w in waiting], True)

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
    # The point is that an empty rival list never becomes a claim about rivals.
    # Asserted on meaning rather than the exact sentence: the wording moved once
    # the watch was built, and a test pinned to a phrase fails on a rewrite that
    # changed nothing it was actually guarding.
    check("no competitors invented", "no rivals named yet" in out["summary"], True)
    check("and it never claims to have checked any",
          any(w in out["summary"].lower() for w in ("published", "no change since")), False)
    check("nothing urgent -> says so plainly", out["actions"], ["Nothing urgent today - the board is clear."])

    con = sqlite3.connect(board.DB)
    n = con.execute("SELECT count(*) FROM daily_reports").fetchone()[0]
    check("exactly one digest row written", n, 1)
    con.close()

    print("Running twice in a day leaves one digest, not two:")
    report.run()
    con = sqlite3.connect(board.DB)
    day = time.strftime("%Y-%m-%d", time.localtime())
    n = con.execute("SELECT count(*) FROM daily_reports WHERE day=?", (day,)).fetchone()[0]
    con.close()
    check("still exactly one row for today", n, 1)

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
