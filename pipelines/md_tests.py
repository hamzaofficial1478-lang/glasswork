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

from coordination import board, targets
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
    con.executescript((Path(__file__).parents[1] / "coordination" / "schema.sql").read_text(encoding="utf-8"))
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

    # The only writes it may make are posting work, answering its own post, and
    # logging its own decision.
    #
    # claim_request and reply_request were added to this list deliberately, and
    # the reasoning matters because widening a red line to make new code pass is
    # exactly how a red line stops meaning anything.
    #
    # The rule is "the MD executes nothing". Answering a request ADDRESSED TO THE
    # MD is not executing work — it is the coordination protocol the handbook
    # lays down: open -> claimed -> answered -> closed. The MD must claim its own
    # post before answering it or two passes both answer it, and it must reply or
    # the asker waits for ever. What it does about the content is still
    # post_task, to the sector that can act. Before this the MD had an inbox
    # nothing ever opened, and twenty-six requests sat in it for two days.
    #
    # The assertions below are what actually enforce the rule, and they are
    # tighter than the allowlist: whatever the MD may call, it may never finish
    # somebody else's work, and it may never claim a TASK.
    writes = set(re.findall(r"board\.(\w+)\(", src))
    allowed = {"post_task", "log_decision", "agent_run", "recent_hit_rate",
               "claim_request", "reply_request",
               # A read, not a hand on the work. The MD counts what a
               # sector is already carrying so it can answer an
               # escalation with that number instead of adding to it.
               "open_task_count", "sector_enabled"}
    check("it only posts work, answers its own post, and logs the decision",
          writes - allowed, set())
    check("it never finishes a task — that is the doing", "finish_task" in writes, False)
    check("it never claims a task off the board", "claim_task" in writes, False)
    check("it never takes a shelf or a lock",
          bool(writes & {"acquire", "borrow", "release"}), False)

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
    """
    Work that would fail the moment it ran is refused — but ONLY work that
    would actually fail.

    This check used to say the opposite of what it says now: that collecting
    leads without a Google Places key was doomed and must not be posted. That
    was the standing assumption in NEEDS, and it cancelled every Leads task the
    MD ever wrote on a machine with no key. It was not true. The OpenStreetMap
    step needs nothing on the shelf and collected 18 real UAE businesses on a
    plain run, and leads_hunt's own no-key message has said "you do not need
    one to get leads" the whole time.

    So the first half guards the fix, and the second half keeps the safeguard
    the fix did not touch: a task that names a shelf it truly cannot run
    without is still refused, and the refusal still names the shelf.
    """
    print("A missing maps key does NOT stop leads being collected:")
    facts = {"missing": ["maps"], "open_titles": set(), "recent_failures": []}
    collecting = [{"sector": "Leads", "title": "Collect today's leads",
                   "detail": "d", "evidence": "owner asked for 20 leads today"}]
    kept, rejected = MD.critic_check(collecting, facts)
    check("the task reaches the board", len(kept), 1)
    check("and nothing was rejected for a key", rejected, [])

    print("\n...but work that really does need an empty shelf is refused:")
    doomed = [{"sector": "Leads", "title": "Rank today's leads by map distance",
               "detail": "d", "needs": "maps",
               "evidence": "owner asked for 20 leads today"}]
    kept, rejected = MD.critic_check(doomed, facts)
    check("not posted", kept, [])
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
    real_b, real_t = board.DB, targets.DB
    board.DB = targets.DB = _fresh_db()
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
        board.DB, targets.DB = real_b, real_t


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
    real_b, real_t = board.DB, targets.DB
    board.DB = targets.DB = _fresh_db()
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
        board.DB, targets.DB = real_b, real_t


def test_a_dry_run_writes_absolutely_nothing():
    print("--dry-run is honest:")
    real_b, real_t = board.DB, targets.DB
    board.DB = targets.DB = _fresh_db()
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
        board.DB, targets.DB = real_b, real_t


def test_the_sector_can_actually_claim_what_the_md_posted():
    """The whole point: the MD's task must be one a real sector picks up."""
    print("The handover works:")
    real_b, real_t = board.DB, targets.DB
    board.DB = targets.DB = _fresh_db()
    try:
        _issue(board.DB, "critical")
        out = MD.run()
        it_posted = [p for p in out["posted"] if p["sector"] == "IT"]
        check("an IT task was posted", bool(it_posted), True)

        claimed = board.next_open_task("IT", "it-watcher")
        check("the IT Watcher can claim it", bool(claimed), True)
        # Which one it picks is the dispatcher's call — it ranks by urgency, and
        # IT can hold more than one task now that the daily diagnostic starts on
        # its own. The property here is the handover: what the Watcher picks up
        # came from the MD.
        check("and it is one of the MD's tasks",
              claimed["id"] in [p["task_id"] for p in it_posted], True)
        check("the evidence travelled with it",
              "[MD evidence]" in (claimed["detail"] or ""), True)
        check("it is credited to the MD", claimed["created_by"], "MD")

        # and nobody else can take it now
        check("a second agent cannot claim the same task",
              board.claim_task(claimed["id"], "it-fixer"), False)
    finally:
        board.DB, targets.DB = real_b, real_t


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
    real_b, real_t = board.DB, targets.DB
    board.DB = targets.DB = _fresh_db()
    try:
        empty = scorecard.build()
        check("with no cycles it says so", empty["cycles_run"], 0)
        check("and refuses to grade nothing",
              "nothing to judge" in empty["verdict"], True)

        _issue(board.DB, "critical")
        targets.set_target("site_checks", 1)
        # Collecting and writing now start on their own suggested numbers when
        # nobody has set one — which is what finally got leads out of the
        # Leads sector. This check is about a SMALL plan not being dressed up
        # as a percentage, so switch those two off the way the owner would, and
        # keep the two-task sample the check was written for. Setting a target
        # to 0 is recorded as his choice, so it stops the job outright.
        targets.set_target("leads", 0)
        targets.set_target("posts", 0)
        # Staged, for the same reason as in test_the_owner_outranks_the_md: no
        # real sector is key-blocked any more, and the measure below is about
        # how the card TREATS a blocked sector, not about which one it is.
        real_needs = MD.NEEDS
        MD.NEEDS = {"Leads": "maps"}
        try:
            MD.run()
        finally:
            MD.NEEDS = real_needs
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
        board.DB, targets.DB = real_b, real_t


def test_the_sector_proposal_queue():
    """
    A sector knows its own domain better than six hardcoded rules do. It asks;
    the MD ranks and allocates. A proposal is deliberately NOT claimable — a
    sector that could post its own task would decide the work, do the work and
    report on it, with nobody ranking it against anything else.
    """
    print("Sectors ask, the MD allocates:")
    from coordination import proposals as P
    real_b, real_t = board.DB, targets.DB
    board.DB = targets.DB = _fresh_db()
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
        board.DB, targets.DB = real_b, real_t


def test_a_rejected_proposal_is_never_binned_silently():
    print("Nothing disappears without a reason:")
    from coordination import proposals as P
    real_b, real_t = board.DB, targets.DB
    board.DB = targets.DB = _fresh_db()
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
        board.DB, targets.DB = real_b, real_t


def test_a_sector_can_refuse_the_wrong_work():
    """
    The board could always say 'claim, don't grab'. It had no way to say 'you
    have handed me the wrong thing', so a mis-routed task could only be done
    badly or left to rot.
    """
    print("The right of reply:")
    from coordination import proposals as P
    real_b, real_t = board.DB, targets.DB
    board.DB = targets.DB = _fresh_db()
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
        board.DB, targets.DB = real_b, real_t


def test_the_owner_outranks_the_md():
    print("The owner's own orders:")
    from coordination import proposals as P
    real_b, real_t = board.DB, targets.DB
    board.DB = targets.DB = _fresh_db()
    try:
        P.set_orders("IT", "check the site twice today, the form felt slow")
        out = MD.run()
        check("the owner's order reaches the board",
              any("Owner's order" in p["title"] for p in out["posted"]), True)

        # an order that cannot run is NEWS, not a quiet rejection
        #
        # Leads no longer needs a key for anything — the OpenStreetMap path
        # collects without one, and the standing assumption that it could not
        # was itself starving the sector. So the blocking is staged here rather
        # than borrowed from a real sector: what is under test is the MECHANISM
        # (a shelf a task genuinely cannot run without stops it, loudly), not
        # the claim that Leads is the sector it applies to.
        board.DB = targets.DB = _fresh_db()
        real_needs = MD.NEEDS
        MD.NEEDS = {"Leads": "maps"}
        try:
            P.set_orders("Leads", "focus on Sharjah hotels today")
            out = MD.run()
            check("a blocked order is surfaced, not binned",
                  len(out.get("owner_blocked") or []), 1)
            check("in words the owner will notice",
                  "CANNOT RUN" in out["summary"], True)
        finally:
            MD.NEEDS = real_needs
    finally:
        board.DB, targets.DB = real_b, real_t


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


from providers import store as _store


def test_one_leads_task_not_two_when_the_order_names_the_number():
    print("\n'merge them into 1 task':")
    tgt = {"jobs": [{"name": "leads", "per_day": 20, "done_today": 0,
                     "remaining": 20, "done_yesterday": 7, "on_file": 7,
                     "unit": "leads"}]}
    facts = {"targets": tgt, "sector_orders": {"Leads": "find 20 leads"},
             "site": {}, "proposals": [], "source_map_done": True, "approvals": 0}
    leads = [c for c in MD.plan(facts) if c["sector"] == "Leads"]
    check("one Leads task, not two", len(leads), 1)
    check("titled in his own words", leads[0]["title"], "Owner's order: find 20 leads")
    check("and it carries his sentence", "find 20 leads" in leads[0]["detail"], True)
    check("plus how the work is actually done",
          "Dedup before storing" in leads[0]["detail"], True)
    check("it keeps the owner's urgency", leads[0]["urgency"], 1)


def test_the_merged_task_carries_yesterday_and_the_running_total():
    print("\n'and also the previous day's leads':")
    tgt = {"jobs": [{"name": "leads", "per_day": 20, "done_today": 5,
                     "remaining": 15, "done_yesterday": 7, "on_file": 12,
                     "unit": "leads"}]}
    facts = {"targets": tgt, "sector_orders": {"Leads": "find 20 leads"},
             "site": {}, "proposals": [], "source_map_done": True, "approvals": 0}
    d = [c for c in MD.plan(facts) if c["sector"] == "Leads"][0]
    check("today's position", "5 done today of 20" in d["detail"], True)
    check("what is left", "15 still to go" in d["detail"], True)
    check("yesterday", "Yesterday: 7" in d["detail"], True)
    check("and the running total", "On file altogether: 12" in d["detail"], True)


def test_an_empty_yesterday_is_called_out_rather_than_buried():
    print("\nA day that collected nothing should be visible on the task:")
    tgt = {"jobs": [{"name": "leads", "per_day": 20, "done_today": 0,
                     "remaining": 20, "done_yesterday": 0, "on_file": 40,
                     "unit": "leads"}]}
    facts = {"targets": tgt, "sector_orders": {}, "site": {}, "proposals": [],
             "source_map_done": True, "approvals": 0}
    d = [c for c in MD.plan(facts) if c["sector"] == "Leads"][0]
    check("it says so", "Nothing at all came in yesterday" in d["detail"], True)
    check("and asks for a reason rather than a quiet close",
          "say why on the task" in d["detail"], True)


def test_an_order_with_no_number_stays_its_own_task():
    print("\nDirection is not a quantity, and is not folded into one:")
    facts = {"targets": {"jobs": []},
             "sector_orders": {"SEO": "focus on Sharjah hotels this week"},
             "site": {}, "proposals": [], "source_map_done": True, "approvals": 0}
    seo = [c for c in MD.plan(facts) if c["sector"] == "SEO"]
    check("still a task of its own", len(seo), 1)
    check("in his words", "Sharjah hotels" in seo[0]["title"], True)


def test_the_wording_is_right_for_one():
    print("\n'1 post', not '1 posts':")
    tgt = {"jobs": [{"name": "posts", "per_day": 1, "done_today": 0,
                     "remaining": 1, "done_yesterday": 0, "on_file": 0,
                     "unit": "posts"}]}
    facts = {"targets": tgt, "sector_orders": {}, "site": {}, "proposals": [],
             "source_map_done": True, "approvals": 0}
    d = [c for c in MD.plan(facts) if c["sector"] == "Content"][0]
    check("singular", "asked for 1 post today" in d["evidence"], True)


def test_a_sector_is_never_starved_by_a_number_nobody_set():
    """
    THE REASON LEADS AND CONTENT NEVER PRODUCED ANYTHING.

    Proven on a real run, not read out of the code. With an empty board the
    collector says:

        "Nothing open on the Leads board. This desk works from the board,
         never on its own initiative."

    and exits 0, so the day records a clean pass. The board was empty because
    the MD posts a collection task only when the leads target has a per_day
    above zero, and a target the owner has never typed a number into is zero.
    targets.py has carried a suggested 20 since it was written, one field away
    from the check that was reading the zero.

    With a task on the board the same pipeline collected 18 real UAE leads from
    OpenStreetMap in one pass. Nothing was broken. Nothing was ever asked.

    MAILS IS THE EXCEPTION AND STAYS THE EXCEPTION. Collecting contacts nobody;
    sending can burn the domain permanently. A suggested 10 mails is a
    suggestion to him, never a default the agency grants itself.
    """
    print("\nNo targets set at all — the sectors still get work:")
    unset = {"jobs": [
        {"name": "leads", "unit": "leads", "per_day": 0, "set_by_owner": False,
         "suggested": 20, "done_today": 0, "done_yesterday": 0, "on_file": 0,
         "remaining": 0},
        {"name": "posts", "unit": "posts", "per_day": 0, "set_by_owner": False,
         "suggested": 1, "done_today": 0, "done_yesterday": 0, "on_file": 0,
         "remaining": 0},
        {"name": "mails", "unit": "mails", "per_day": 0, "set_by_owner": False,
         "suggested": 10, "done_today": 0, "done_yesterday": 0, "on_file": 0,
         "remaining": 0},
    ]}
    facts = {"targets": unset, "sector_orders": {}, "site": {},
             "proposals": [], "source_map_done": True, "approvals": 0}
    got = MD.plan(facts)
    leads = [c for c in got if c["sector"] == "Leads"]
    content = [c for c in got if c["sector"] == "Content"]
    check("Leads is given something to collect", len(leads), 1)
    check("Content is given something to write", len(content), 1)

    print("  ...and each says whose number it is working to:")
    check("the task admits nobody set it",
          "no leads target set" in leads[0]["evidence"], True)
    check("and says where he changes it",
          "Setup" in leads[0]["detail"], True)
    check("and that zero switches it off",
          "0 stops this job" in leads[0]["detail"], True)

    print("\nMail is NOT started on a suggestion — it can burn the domain:")
    everything = " ".join((c["title"] + c["detail"]).lower() for c in got)
    check("nothing was posted to send mail",
          any(w in everything for w in ("send the", "email them", "outreach email")),
          False)
    from pipelines import md_cycle
    check("mails is deliberately off the self-starting list",
          "mails" in md_cycle.SELF_STARTING, False)

    print("\nA number he DID set is his, in both directions:")
    his = {"jobs": [dict(j) for j in unset["jobs"]]}
    his["jobs"][0].update(per_day=5, remaining=5, set_by_owner=True)
    his["jobs"][1].update(per_day=0, remaining=0, set_by_owner=True)   # switched off
    facts = {**facts, "targets": his}
    got = MD.plan(facts)
    lead_task = [c for c in got if c["sector"] == "Leads"][0]
    check("his 5 is used, not the suggested 20",
          "5 leads" in lead_task["evidence"], True)
    check("and it is not described as a suggestion",
          "no leads target set" in lead_task["evidence"], False)
    check("a target he set to 0 stays off",
          [c for c in got if c["sector"] == "Content"], [])


def main():
    with _store.isolated():
        _main()


def _main():
    test_one_leads_task_not_two_when_the_order_names_the_number()
    test_the_merged_task_carries_yesterday_and_the_running_total()
    test_an_empty_yesterday_is_called_out_rather_than_buried()
    test_an_order_with_no_number_stays_its_own_task()
    test_the_wording_is_right_for_one()
    test_a_sector_is_never_starved_by_a_number_nobody_set()
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
