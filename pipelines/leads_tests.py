"""
leads_tests.py — run: python -m pipelines.leads_tests   (python3 on macOS/Linux)

Leads is the sector where a bug reaches a real person. The failure modes are
specific, so the tests are too:

  * approaching a private individual        (PDPL — refused outright)
  * collecting the same firm twice          (burns the relationship and the domain)
  * contacting anyone before Security clears them
  * quietly widening scope past what the MD authorised

Everything runs against a throwaway cabinet; the real one is never touched.
"""
import sqlite3
import sys
import tempfile
import threading
from pathlib import Path

from coordination import board
from . import leads_sources as ls

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
    con.executescript((Path(__file__).parents[1] / "coordination" / "schema.sql").read_text())
    con.commit()
    con.close()
    return db


BIZ = {"company": "Marina Fitout LLC", "segment": "fitout", "area": "Dubai Marina",
       "email": "info@marinafitout.ae", "source": "Dubai Chamber"}


def test_privacy_gate():
    """The MD excluded private individuals. That has to bite in code."""
    print("PDPL gate (private individuals refused):")
    ok, why = ls.screen_lead({**BIZ, "segment": "homeowner"})
    check("homeowner segment refused", (ok, "PDPL" in why), (False, True))

    ok, why = ls.screen_lead({**BIZ, "company": "", "name": "Ahmed (villa owner)",
                              "segment": "fitout"})
    check("a named villa owner refused", ok, False)

    check("detector reads any field", ls.is_private_individual("", "private individual"), True)
    check("a real firm is not flagged",
          ls.is_private_individual("Marina Fitout LLC", "fitout contractor"), False)
    # the word must not be over-eager: 'Resident' inside a company name is common
    ok, _ = ls.screen_lead({**BIZ, "company": "Al Quoz Aluminium Works"})
    check("an ordinary contractor still passes", ok, True)


def test_other_screens():
    print("Scope screens:")
    ok, why = ls.screen_lead({**BIZ, "area": "London", "company": "London Glass Ltd",
                              "email": "a@b.co.uk"})
    check("outside the UAE refused", (ok, "UAE" in why), (False, True))

    ok, why = ls.screen_lead({"company": "Gulf Catering Co", "area": "Dubai",
                              "segment": "food", "email": "a@b.ae"})
    check("irrelevant trade refused", (ok, "glass" in why), (False, True))

    ok, why = ls.screen_lead({**BIZ, "email": "", "phone": ""})
    check("no contact route refused", (ok, "contact route" in why), (False, True))

    ok, _ = ls.screen_lead({**BIZ, "company": ""})
    check("no company name refused", ok, False)

    ok, _ = ls.screen_lead(BIZ)
    check("a clean B2B lead passes", ok, True)


def test_never_collected_twice():
    """
    The handbook is blunt about this: a lead already in the cabinet is skipped
    and never re-mailed. Directories overlap constantly, so this must hold when
    the same firm arrives from two different sources.
    """
    print("Never collected twice:")
    real = board.DB
    board.DB = _fresh_db()
    try:
        first = ls.intake([BIZ])
        check("first sighting is stored", len(first["stored"]), 1)

        again = ls.intake([{**BIZ, "source": "Google Maps"}])
        check("same firm from another source is a duplicate", len(again["duplicates"]), 1)
        check("and is NOT stored twice", len(again["stored"]), 0)

        # a different firm sharing nothing still gets in
        other = ls.intake([{**BIZ, "company": "Barsha Glass Co",
                            "email": "hi@barshaglass.ae"}])
        check("a genuinely new firm still gets in", len(other["stored"]), 1)

        con = sqlite3.connect(board.DB)
        check("exactly two rows in the cabinet",
              con.execute("SELECT count(*) FROM leads").fetchone()[0], 2)
        con.close()
    finally:
        board.DB = real


def test_intake_cannot_contact():
    """
    Nothing in intake() may reach a person. A stored lead sits at 'new' until
    Security clears it and the owner approves a route.
    """
    print("Intake contacts nobody:")
    real = board.DB
    board.DB = _fresh_db()
    try:
        res = ls.intake([BIZ, {**BIZ, "company": "X Co", "email": "x@y.ae"},
                         {**BIZ, "company": "Villa owner Ali", "segment": "homeowner"}])
        check("the private one was refused", len(res["refused"]), 1)
        check("two businesses stored", len(res["stored"]), 2)
        check("nothing was contacted", res["contacted"], 0)

        con = sqlite3.connect(board.DB)
        check("outreach log untouched",
              con.execute("SELECT count(*) FROM outreach_log").fetchone()[0], 0)
        stages = [r[0] for r in con.execute("SELECT DISTINCT stage FROM leads")]
        check("every lead sits at 'new', uncleared", stages, ["new"])
        con.close()
    finally:
        board.DB = real


def test_critic():
    print("Critic (scope and screen integrity):")
    good = ls.build_source_map()
    check("the real map passes", ls.critic_check(good, env_ready=False)[0], True)

    # if nothing was refused, the privacy screen is not actually running
    blind = {**good, "refused_sources": []}
    ok, why = ls.critic_check(blind, env_ready=False)
    check("a screen that refuses nothing is rejected",
          (ok, any("cannot be working" in r for r in why)), (False, True))

    # the MD scoped this as prep precisely because there is no route out yet
    ok, why = ls.critic_check(good, env_ready=True)
    check("scope creep is caught if outreach is suddenly live",
          (ok, any("re-plan" in r for r in why)), (False, True))

    empty = {"usable_sources": [], "segments": {}, "refused_sources": [{"name": "x"}]}
    check("no usable sources is rejected", ls.critic_check(empty, False)[0], False)


def test_source_map_is_covered():
    print("Source map:")
    m = ls.build_source_map()
    check("every buying segment has a source",
          [k for k, v in m["segments"].items() if not v["source_count"]], [])
    check("the classifieds source is refused",
          [r["name"] for r in m["refused_sources"]], ["Classifieds villa-owner listings"])
    check("every usable source is B2B-only",
          all(s["b2b_only"] for s in m["usable_sources"]), True)
    check("every usable source states why it is worth using",
          all(s.get("why") for s in m["usable_sources"]), True)


def test_claim_is_atomic():
    print("Claim, don't grab (6 Hunters, 1 task):")
    real = board.DB
    board.DB = _fresh_db()
    try:
        tid = board.post_task("Leads", "B2B source list", "detail", risk="green")
        winners, lock = [], threading.Lock()

        def go(n):
            if board.claim_task(tid, f"hunter-{n}"):
                with lock:
                    winners.append(n)

        ts = [threading.Thread(target=go, args=(i,)) for i in range(6)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        check("exactly one Hunter claims it", len(winners), 1)
    finally:
        board.DB = real


def test_end_to_end():
    print("Full cycle on a throwaway cabinet:")
    real = board.DB
    board.DB = _fresh_db()
    try:
        check("refuses to invent work with no task", ls.run(agent="t")["ok"], False)

        board.post_task("Leads", "B2B target-source list for the buy-intent districts",
                        "Sources only, no contact.", risk="green")
        r = ls.run(agent="leads-hunter")
        check("it runs", r["ok"], True)
        check("critic passed", r["critic"], "passed")
        check("all five segments covered", r["segments_covered"], 5)
        check("stored no leads", r["stored_leads"], 0)
        check("contacted nobody", r["contacted"], 0)

        con = sqlite3.connect(board.DB)
        con.row_factory = sqlite3.Row
        check("leads table still empty",
              con.execute("SELECT count(*) FROM leads").fetchone()[0], 0)
        check("outreach log still empty",
              con.execute("SELECT count(*) FROM outreach_log").fetchone()[0], 0)
        t = dict(con.execute("SELECT status,owner,result FROM tasks").fetchone())
        check("the task was finished", t["status"], "done")
        check("and records who did it", t["owner"], "leads-hunter")
        # Round-trip it, don't just grep it. A substring survives double-encoding,
        # which is exactly how the map got stored as an escaped string once.
        import json as _json
        parsed = _json.loads(t["result"] or "null")
        check("the result is a real object, not an escaped string",
              isinstance(parsed, dict), True)
        check("the map is the deliverable", sorted(parsed)[:2],
              ["refused_sources", "segments"])
        sec = con.execute("SELECT count(*) FROM requests WHERE to_sector='Security'").fetchone()[0]
        check("Security was asked to clear the sources", sec, 1)
        con.close()
    finally:
        board.DB = real


def main():
    test_privacy_gate()
    test_other_screens()
    test_never_collected_twice()
    test_intake_cannot_contact()
    test_critic()
    test_source_map_is_covered()
    test_claim_is_atomic()
    test_end_to_end()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: {_fail}")
        sys.exit(1)
    print("all leads tests passed")


if __name__ == "__main__":
    main()
