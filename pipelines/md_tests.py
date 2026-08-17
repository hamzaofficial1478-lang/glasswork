"""
md_tests.py — run: python -m pipelines.md_tests   (python3 on macOS/Linux)

The MD is the one agent that could quietly wreck the whole agency, in two
different ways, so both are pinned here.

It could start doing the work itself, and then no sector claim, no lock and no
red-line hook is in the path any more — the coordination layer would simply be
bypassed. So the first test reads md_cycle.py and fails if it imports a pipeline
or calls a run().

Or it could flood the board: post the same work every time it runs until a
hundred stale tasks bury the real ones. So re-running must be a no-op, and the
task ceiling must hold.

Everything runs against a throwaway cabinet.
"""
import inspect
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

from coordination import board, state, targets
from . import md_cycle as MD

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


def _issue(db, severity="critical", status="open"):
    con = sqlite3.connect(db)
    con.execute("INSERT INTO site_issues(kind,severity,detail,url,status,found_by,found_at)"
                " VALUES('form_dead',?,'d','https://x.ae',?,'it-watcher',strftime('%s','now'))",
                (severity, status))
    con.commit()
    con.close()


def _open_tasks(db):
    con = sqlite3.connect(db)
    rows = con.execute("SELECT sector,title FROM tasks WHERE status='open'").fetchall()
    con.close()
    return rows


def test_the_brain_never_touches_the_hands():
    """
    The single rule that keeps the coordination layer meaningful. If the MD ran
    the work itself there would be no claim, no lock and no hook in the path.
    """
    print("The MD plans and delegates, and does nothing else:")
    src = inspect.getsource(MD)

    imports = re.findall(r"^\s*(?:from|import)\s+(\S+)", src, re.M)
    pipelines = [m for m in imports
                 if m.startswith("pipelines") or m.startswith(".")
                 or m in ("it_health", "leads_hunt", "seo_gap", "mail")]
    check("it imports no pipeline", pipelines, [])

    # the only writes it may make are posting tasks and logging its own decision
    writes = set(re.findall(r"board\.(\w+)\(", src))
    allowed = {"post_task", "log_decision", "agent_run", "recent_hit_rate"}
    check("it only ever posts tasks and logs the decision", writes - allowed, set())

    check("it never sends outreach", "smtp" in src.lower(), False)
    check("it never touches site_queue", "queue_site_change" in src, False)


def test_nothing_ungrounded_reaches_the_board():
    print("No task without a fact behind it:")
    facts = {"missing": [], "open_titles": set(), "recent_failures": []}
    hunch = [{"sector": "IT", "title": "Have a look at things", "detail": "d",
              "evidence": ""}]
    kept, rejected = MD.critic_check(hunch, facts)
    check("a hunch is rejected", kept, [])
    check("and the Critic says why", "no evidence" in rejected[0]["why"], True)

    grounded = [{"sector": "IT", "title": "Check the site", "detail": "d",
                 "evidence": "2 critical issues open"}]
    kept, _ = MD.critic_check(grounded, facts)
    check("a grounded item survives", len(kept), 1)


def test_amber_work_goes_to_the_owner_not_the_board():
    print("The MD cannot authorise itself:")
    facts = {"missing": [], "open_titles": set(), "recent_failures": []}
    amber = [{"sector": "Leads", "title": "Offer a deal to close the hotel",
              "detail": "d", "evidence": "3 leads stalled", "risk": "amber"}]
    kept, rejected = MD.critic_check(amber, facts)
    check("amber never reaches the board", kept, [])
    check("and it is routed to the owner", "owner" in rejected[0]["why"], True)


def test_impossible_work_is_not_posted():
    print("Work that would fail the moment it ran:")
    facts = {"missing": ["maps"], "open_titles": set(), "recent_failures": []}
    doomed = [{"sector": "Leads", "title": "Collect today's leads", "detail": "d",
               "evidence": "owner asked for 20 leads today"}]
    kept, rejected = MD.critic_check(doomed, facts)
    check("no maps key -> not posted", kept, [])
    check("and the reason names the empty shelf", "maps" in rejected[0]["why"], True)


def test_a_failing_line_of_reasoning_gets_dropped():
    print("The MD is confronted with its own record:")
    facts = {"missing": [], "open_titles": set(),
             "recent_failures": ["seo-gap", "seo-gap", "seo-gap"]}
    again = [{"sector": "SEO", "title": "Pull more keyword gaps", "detail": "d",
              "evidence": "40 gaps tracked", "cycle": "seo-gap"}]
    kept, rejected = MD.critic_check(again, facts)
    check("three failures and it stops ordering it", kept, [])
    check("and says so", "failed three times" in rejected[0]["why"], True)


def test_running_twice_does_not_double_the_board():
    print("Safe to run on a timer:")
    real_b, real_s, real_t = board.DB, state.DB, targets.DB
    board.DB = state.DB = targets.DB = _fresh_db()
    try:
        _issue(board.DB, "critical")
        first = MD.run()
        check("the first run posts work", len(first["posted"]) > 0, True)
        n1 = len(_open_tasks(board.DB))

        second = MD.run()
        check("the second run posts nothing", second["posted"], [])
        check("the board did not grow", len(_open_tasks(board.DB)), n1)
        check("and every rejection explains itself",
              all(r["why"] for r in second["rejected"]), True)
    finally:
        board.DB, state.DB, targets.DB = real_b, real_s, real_t


def test_the_board_is_never_flooded():
    print("A plan nobody could finish is not a plan:")
    facts = {"missing": [], "open_titles": set(), "recent_failures": []}
    many = [{"sector": "IT", "title": f"job {i}", "detail": "d", "evidence": "a fact"}
            for i in range(MD.MAX_TASKS_PER_CYCLE + 4)]
    kept, rejected = MD.critic_check(many, facts)
    check("the ceiling holds", len(kept), MD.MAX_TASKS_PER_CYCLE)
    check("the rest are kept for tomorrow, not dropped silently",
          all("tomorrow" in r["why"] for r in rejected), True)


def test_the_decision_names_the_tasks_it_created():
    print("Report can score the MD later:")
    real_b, real_s, real_t = board.DB, state.DB, targets.DB
    board.DB = state.DB = targets.DB = _fresh_db()
    try:
        _issue(board.DB, "critical")
        out = MD.run()
        con = sqlite3.connect(board.DB)
        ev = con.execute("SELECT evidence FROM decisions WHERE cycle='md-cycle'").fetchone()
        con.close()
        import json
        ids = json.loads(ev[0])["task_ids"]
        check("the decision records its task ids",
              sorted(ids), sorted(p["task_id"] for p in out["posted"]))
    finally:
        board.DB, state.DB, targets.DB = real_b, real_s, real_t


def test_a_dry_run_writes_absolutely_nothing():
    print("--dry-run is honest:")
    real_b, real_s, real_t = board.DB, state.DB, targets.DB
    board.DB = state.DB = targets.DB = _fresh_db()
    try:
        _issue(board.DB, "critical")
        out = MD.run(dry_run=True)
        check("it says what it would do", len(out["would_post"]) > 0, True)
        check("but the board is still empty", _open_tasks(board.DB), [])
        con = sqlite3.connect(board.DB)
        n = con.execute("SELECT count(*) FROM decisions").fetchone()[0]
        con.close()
        check("and no decision was logged", n, 0)
    finally:
        board.DB, state.DB, targets.DB = real_b, real_s, real_t


def test_the_sector_can_actually_claim_what_the_md_posted():
    """The whole point: the MD's task must be one a real sector picks up."""
    print("The handover works:")
    real_b, real_s, real_t = board.DB, state.DB, targets.DB
    board.DB = state.DB = targets.DB = _fresh_db()
    try:
        _issue(board.DB, "critical")
        out = MD.run()
        it_task = next((p for p in out["posted"] if p["sector"] == "IT"), None)
        check("an IT task was posted", bool(it_task), True)

        claimed = board.next_open_task("IT", "it-watcher")
        check("the IT Watcher can claim it", bool(claimed), True)
        check("and it is the MD's task", claimed["id"], it_task["task_id"])
        check("the evidence travelled with it",
              "[MD evidence]" in (claimed["detail"] or ""), True)
        check("it is credited to the MD", claimed["created_by"], "MD")

        # and nobody else can take it now
        check("a second agent cannot claim the same task",
              board.claim_task(claimed["id"], "it-fixer"), False)
    finally:
        board.DB, state.DB, targets.DB = real_b, real_s, real_t


def test_the_md_does_not_grade_itself():
    """
    The card is computed by Report from rows the MD did not write. An agent that
    grades its own management is not being graded, so the separation is asserted
    rather than trusted.
    """
    print("The MD is judged, not self-judged:")
    src = inspect.getsource(MD)
    # an import, not a mention: the module comment is allowed to point at where
    # the card is computed, it just may not reach for it
    imported = re.findall(r"^\s*(?:from|import)\s+.*scorecard", src, re.M)
    check("md_cycle does not import the scorecard", imported, [])
    check("nor calls into it", "scorecard." in src, False)

    from coordination import scorecard
    sc_src = inspect.getsource(scorecard)
    check("and the scorecard writes nothing",
          any(w in sc_src for w in ("INSERT", "UPDATE", "DELETE")), False)


def test_the_card_measures_management_not_just_outcome():
    print("Five things a manager is accountable for:")
    from coordination import scorecard
    real_b, real_s, real_t = board.DB, state.DB, targets.DB
    board.DB = state.DB = targets.DB = _fresh_db()
    try:
        empty = scorecard.build()
        check("with no cycles it says so", empty["cycles_run"], 0)
        check("and refuses to grade nothing",
              "nothing to judge" in empty["verdict"], True)

        _issue(board.DB, "critical")
        targets.set_target("site_checks", 1)
        MD.run()
        card = scorecard.build()
        check("it counts the cycle", card["cycles_run"], 1)
        check("grounding is measured against what the Critic threw out",
              card["grounding"]["of"].endswith("proposals survived the Critic"), True)

        # a sector the MD could not direct must not count against it
        check("a sector with no key is out of the denominator",
              "had no key" in card["coverage"]["of"], True)

        # too little data is never dressed up as a score
        check("a two-task sample is not reported as a percentage",
              card["direction"]["value"], None)
        check("and is graded honestly", card["direction"]["grade"], "not enough yet")

        # the verdict names the WEAKEST measure, so a neglected sector cannot
        # hide behind four numbers that look fine
        worst = min((k for k in ("direction", "delivery", "grounding",
                                 "coverage", "judgement")
                     if card[k]["value"] is not None),
                    key=lambda k: card[k]["value"])
        check("the verdict names the weakest link", worst in card["verdict"], True)
    finally:
        board.DB, state.DB, targets.DB = real_b, real_s, real_t


def test_the_sector_proposal_queue():
    """
    A sector knows its own domain better than six hardcoded rules do. It asks;
    the MD ranks and allocates. A proposal is deliberately NOT claimable — a
    sector that could post its own task would decide the work, do the work and
    report on it, with nobody ranking it against anything else.
    """
    print("Sectors ask, the MD allocates:")
    from coordination import proposals as P
    real_b, real_s, real_t = board.DB, state.DB, targets.DB
    board.DB = state.DB = targets.DB = _fresh_db()
    try:
        check("a hunch with no evidence is refused at the door",
              P.propose("SEO", "have a look around", evidence=""), None)
        pid = P.propose("Content", "Write the Al Barsha shower glass page",
                        evidence="3 rival pages on shower glass this week", urgency=2)
        check("a grounded proposal is accepted into the queue", bool(pid), True)
        check("asking twice is one ask",
              P.propose("Content", "Write the Al Barsha shower glass page",
                        evidence="same thing again"), pid)
        check("it is waiting, not claimable", len(P.open_proposals()), 1)

        out = MD.run()
        titles = [p["title"] for p in out["posted"]]
        check("the MD turned it into real work",
              "Write the Al Barsha shower glass page" in titles, True)

        con = sqlite3.connect(board.DB)
        status, why = con.execute(
            "SELECT status, why FROM proposals WHERE id=?", (pid,)).fetchone()
        con.close()
        check("and the proposal was answered", status, "accepted")
        check("with a reason the sector can read", bool(why), True)
    finally:
        board.DB, state.DB, targets.DB = real_b, real_s, real_t


def test_a_rejected_proposal_is_never_binned_silently():
    print("Nothing disappears without a reason:")
    from coordination import proposals as P
    real_b, real_s, real_t = board.DB, state.DB, targets.DB
    board.DB = state.DB = targets.DB = _fresh_db()
    try:
        pid = P.propose("Leads", "Collect hotel leads",
                        evidence="owner wants hotels", needs="maps")
        MD.run()
        con = sqlite3.connect(board.DB)
        status, why = con.execute(
            "SELECT status, why FROM proposals WHERE id=?", (pid,)).fetchone()
        con.close()
        check("it was rejected", status, "rejected")
        check("and the sector is told the shelf is empty", "maps" in (why or ""), True)

        try:
            P.decide(pid, "rejected", "")
            silent = True
        except ValueError:
            silent = False
        check("a decision with no reason is impossible", silent, False)
    finally:
        board.DB, state.DB, targets.DB = real_b, real_s, real_t


def test_a_sector_can_refuse_the_wrong_work():
    """
    The board could always say 'claim, don't grab'. It had no way to say 'you
    have handed me the wrong thing', so a mis-routed task could only be done
    badly or left to rot.
    """
    print("The right of reply:")
    from coordination import proposals as P
    real_b, real_s, real_t = board.DB, state.DB, targets.DB
    board.DB = state.DB = targets.DB = _fresh_db()
    try:
        tid = board.post_task("Leads", "Write a service page", "d", created_by="MD")
        board.claim_task(tid, "leads-hunter")

        check("an invented objection is refused",
              _raises(lambda: P.object_to_task(tid, "Leads", "cannot-be-bothered")), True)

        ok = P.object_to_task(tid, "Leads", "wrong_sector", "that is Content's job")
        check("a real objection is accepted", ok, True)

        t = board.get_task(tid)
        check("the task is handed back, unowned", t["owner"], None)
        check("and reopened for re-routing", t["status"], "open")
        check("the refusal is visible on the task itself",
              "objected" in (t["detail"] or ""), True)
        check("and it is on the record for the MD's score",
              len(P.objections()), 1)

        # work already finished cannot be refused after the fact
        t2 = board.post_task("IT", "done job", "d")
        board.claim_task(t2, "it-watcher")
        board.finish_task(t2, "it-watcher", "done", status="done")
        check("a finished task cannot be objected to",
              P.object_to_task(t2, "IT", "wrong_sector"), False)
    finally:
        board.DB, state.DB, targets.DB = real_b, real_s, real_t


def test_the_owner_outranks_the_md():
    print("The owner's own orders:")
    from coordination import proposals as P
    real_b, real_s, real_t = board.DB, state.DB, targets.DB
    board.DB = state.DB = targets.DB = _fresh_db()
    try:
        P.set_orders("IT", "check the site twice today, the form felt slow")
        out = MD.run()
        check("the owner's order reaches the board",
              any("Owner's order" in p["title"] for p in out["posted"]), True)

        # an order that cannot run is NEWS, not a quiet rejection
        board.DB = state.DB = targets.DB = _fresh_db()
        P.set_orders("Leads", "focus on Sharjah hotels today")
        out = MD.run()
        check("a blocked order is surfaced, not binned",
              len(out.get("owner_blocked") or []), 1)
        check("in words the owner will notice",
              "CANNOT RUN" in out["summary"], True)
    finally:
        board.DB, state.DB, targets.DB = real_b, real_s, real_t


def _raises(fn):
    try:
        fn()
        return False
    except Exception:
        return True


def test_two_minds_on_the_allocation():
    """
    The MD's own reasoning stays deterministic — auditable and free. The models
    are asked only the judgement the rules cannot make: when more grounded work
    exists than the day holds, what matters most. Every uncertain outcome keeps
    the MD's own order rather than stalling or guessing.
    """
    print("Two models on what matters most:")
    facts = {"missing": [], "open_titles": set(), "recent_failures": [],
             "sector_orders": {}}
    many = [{"sector": "IT", "title": f"job {i}", "detail": "d",
             "evidence": "a fact", "urgency": 5}
            for i in range(MD.MAX_TASKS_PER_CYCLE + 3)]

    calls = []

    def never_called(*a, **k):
        calls.append(1)
        return {"ok": True, "consulted": 2, "agreed": True, "verdict": ""}

    few = many[:2]
    MD._ask_both_which_matters(few, facts, deliberate=never_called)
    check("with nothing to choose between, no money is spent", calls, [])

    def agree(task, msgs, max_tokens=400, verdict_of=None):
        titles = [c["title"] for c in reversed(many)]
        return {"ok": True, "consulted": 2, "agreed": True,
                "verdict": "|".join(t.lower() for t in titles)}
    order, note = MD._ask_both_which_matters(many, facts, deliberate=agree)
    check("when both agree the order is used", order[0], many[-1]["title"].lower())
    check("and it says so", "both models agreed" in note, True)

    def split(task, msgs, max_tokens=400, verdict_of=None):
        return {"ok": True, "consulted": 2, "agreed": False, "split": ["a", "b"]}
    order, note = MD._ask_both_which_matters(many, facts, deliberate=split)
    check("a split keeps the MD's own ranking", order, None)
    check("and is surfaced rather than hidden", "disagreed" in note, True)

    def alone(task, msgs, max_tokens=400, verdict_of=None):
        return {"ok": True, "consulted": 1, "agreed": None, "verdict": "x"}
    order, note = MD._ask_both_which_matters(many, facts, deliberate=alone)
    check("one model is not a cross-check", order, None)
    check("and it says why", "no cross-check" in note, True)

    def dead(task, msgs, max_tokens=400, verdict_of=None):
        return {"ok": False, "error": "nothing routed"}
    order, note = MD._ask_both_which_matters(many, facts, deliberate=dead)
    check("no model at all -> the day still runs", order, None)
    check("on the MD's own ranking", "kept my own ranking" in note, True)


def test_security_clearance_fails_safe():
    """
    A wrong refusal costs one lead. A wrong clearance reaches a customer. The two
    are not symmetric and the code must not treat them as if they were.
    """
    print("Security: refusing is solo, clearing risky needs a second:")
    from coordination import clearance as C

    def models(agreed, verdicts, ok=True, consulted=2):
        def d(task, msgs, max_tokens=200, verdict_of=None):
            if not ok:
                return {"ok": False, "error": "no model", "consulted": 0,
                        "agreed": None, "answers": []}
            return {"ok": True, "consulted": consulted, "agreed": agreed,
                    "verdict": verdicts[0] if agreed else None,
                    "split": None if agreed else verdicts,
                    "answers": [{"output": v} for v in verdicts]}
        return d

    r = C.screen("s", "b", "contact_stranger", rules_say="refuse",
                 deliberate=models(True, ["clear", "clear"]))
    check("no model may overturn a refusal", r["verdict"], "refuse")
    check("and it does not even ask", r["consulted"], 0)

    r = C.screen("s", "b", "", rules_say="clear",
                 deliberate=models(True, ["clear", "clear"]))
    check("a routine clearance costs nothing", r["consulted"], 0)
    check("and goes through", r["verdict"], "clear")

    r = C.screen("s", "b", "contact_stranger", rules_say="clear",
                 deliberate=models(True, ["clear", "clear"]))
    check("a risky clearance both models second is allowed", r["verdict"], "clear")

    r = C.screen("s", "b", "contact_stranger", rules_say="clear",
                 deliberate=models(False, ["clear", "refuse"]))
    check("a SPLIT refuses rather than picking a side", r["verdict"], "refuse")
    check("and goes to the owner", r["escalate"], True)

    r = C.screen("s", "b", "spend", rules_say="clear",
                 deliberate=models(None, ["clear"], consulted=1))
    check("one model is not a cross-check for a risky yes", r["verdict"], "refuse")
    check("and it says to add a second", "second model" in r["why"], True)

    r = C.screen("s", "b", "publish", rules_say="clear",
                 deliberate=models(None, [], ok=False))
    check("no model reachable -> held, not waved through", r["verdict"], "refuse")
    check("and escalated", r["escalate"], True)

    print("  the verdict parser resolves doubt as refusal:")
    for text, want in [("CLEAR\nordinary business", "clear"),
                       ("REFUSE\nlooks like a scam", "refuse"),
                       ("hmm, hard to say", "refuse"),
                       ("I am not clear on this", "refuse"),
                       ("", "refuse")]:
        check(f"    {text[:24]!r}", C._verdict_of(text), want)


def main():
    test_two_minds_on_the_allocation()
    test_security_clearance_fails_safe()
    test_the_sector_proposal_queue()
    test_a_rejected_proposal_is_never_binned_silently()
    test_a_sector_can_refuse_the_wrong_work()
    test_the_owner_outranks_the_md()
    test_the_md_does_not_grade_itself()
    test_the_card_measures_management_not_just_outcome()
    test_the_brain_never_touches_the_hands()
    test_nothing_ungrounded_reaches_the_board()
    test_amber_work_goes_to_the_owner_not_the_board()
    test_impossible_work_is_not_posted()
    test_a_failing_line_of_reasoning_gets_dropped()
    test_running_twice_does_not_double_the_board()
    test_the_board_is_never_flooded()
    test_the_decision_names_the_tasks_it_created()
    test_a_dry_run_writes_absolutely_nothing()
    test_the_sector_can_actually_claim_what_the_md_posted()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: {_fail}")
        sys.exit(1)
    print("all MD tests passed")


if __name__ == "__main__":
    main()
