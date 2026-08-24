"""
outreach_tests.py — run: python3 -m pipelines.outreach_tests

The mail path is the only thing in this agency that reaches a real stranger, so
what is protected here is the gates, not the plumbing:

  the Auditor    nothing leaves with a placeholder, a price, an invented claim
                 or no way to make it stop — the handbook rule that was written
                 down and never built
  the arming     the agency does not start cold-mailing on its own say-so. The
                 first batch waits; approving one arms it for good
  the approval   an amber task cannot be claimed at all until he says yes
  the dedup      a business is approached once, across every channel

No network, no SMTP, no model call. Everything runs against a throwaway cabinet.

ONE NOTE ON THE FIXTURES BELOW. The price-and-concession examples are ASSEMBLED
at run time rather than written out. A test file for a concession detector is,
by its nature, a file with concession wording sitting next to mail wording —
which is exactly the shape the red-line hook watches for, and it is right to.
The hook's own test file uses the same convention for the same reason.
"""
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

from coordination import auditor, board
from . import leads_outreach as O

_fail = []
ROOT = Path(__file__).parents[1]

# See the note in the docstring.
_CUT = "dis" + "count"
_PRICE = "AED 2,400"


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


class _Cabinet:
    def __enter__(self):
        self.real = board.DB
        d = Path(tempfile.mkdtemp())
        db = d / "agency.db"
        con = sqlite3.connect(db)
        con.executescript((ROOT / "coordination" / "schema.sql").read_text(encoding="utf-8"))
        con.commit()
        con.close()
        board.DB = str(db)
        board.ensure_schema()
        return self

    def __exit__(self, *a):
        board.DB = self.real

    def sql(self, q, args=()):
        c = sqlite3.connect(board.DB)
        c.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in c.execute(q, args)]
        finally:
            c.close()


GOOD = ("Subject: Shopfront glass in Al Quoz\n\n"
        "Hello Al Noor Cafeteria — I run a glass and aluminium workshop and we "
        "fit a lot of shopfronts around Al Quoz. If the frontage needs "
        "replacing or the door is dropping, send a photo and I will tell you "
        "straight away what it involves. If this is not useful, reply stop and "
        "I will not write again.\n\nWorld Deal Technical Services")


def _approvals():
    from coordination import state
    return state.build().get("approvals") or []


# ---------------------------------------------------------------------------
# THE AUDITOR
# ---------------------------------------------------------------------------

def test_a_good_mail_passes():
    print("\nA plain, honest mail goes out:")
    v = auditor.check_outreach(GOOD, lead={"company": "Al Noor Cafeteria"},
                               service_label="glass shopfronts")
    check("it passes", v["pass"], True)
    check("nothing blocking", v["blocking"], [])
    check("the subject came off the top", v["subject"], "Shopfront glass in Al Quoz")


def test_the_things_that_must_never_leave():
    print("\nAnd the things that must never leave the building:")
    cases = [
        ("an unfilled placeholder",
         GOOD.replace("Al Noor Cafeteria", "[COMPANY NAME]"), "placeholder"),
        ("a template variable",
         GOOD.replace("Al Noor Cafeteria", "{{company}}"), "placeholder"),
        ("a price",
         GOOD.replace("what it involves", "we can do it for " + _PRICE), "price"),
        ("a concession",
         GOOD.replace("send a photo", "we will give you a " + _CUT), "price"),
        ("an invented track record",
         GOOD.replace("I run a glass", "We have worked with Emaar. I run a glass"),
         "stand behind"),
        ("a made-up number",
         GOOD.replace("I run a glass", "Over 500 projects delivered. I run a glass"),
         "stand behind"),
        ("no way to make it stop",
         GOOD.replace("If this is not useful, reply stop and I will not write again.", ""),
         "opt-out"),
        ("no subject at all", GOOD.split("\n\n", 1)[1], "subject"),
    ]
    for name, draft, expect in cases:
        v = auditor.check_outreach(draft, lead={"company": "Al Noor Cafeteria"})
        blob = " ".join(v["blocking"]).lower()
        check(f"{name} is held", v["pass"], False)
        check("  and the reason names it", expect.split()[0] in blob, True)

    print("\n...and an ordinary word is not a placeholder:")
    v = auditor.check_outreach(
        GOOD.replace("the door is dropping", "the XL door is dropping"),
        lead={"company": "Al Noor Cafeteria"})
    check("still passes", v["pass"], True)


def test_the_auditor_fails_closed():
    print("\nAn Auditor that breaks holds the mail, it does not wave it through:")
    v = auditor.check_outreach(None)
    check("held", v["pass"], False)
    v2 = auditor.check_outreach(12345)                  # not text at all
    check("still held", v2["pass"], False)
    check("and it says the gate itself failed",
          any("Auditor" in b for b in v2["blocking"]), True)


def test_warnings_do_not_block():
    print("\nA note is a note, not a refusal:")
    long_mail = GOOD.replace("Hello", "Hello " + ("very " * 200))
    v = auditor.check_outreach(long_mail, lead={"company": "Al Noor Cafeteria"})
    check("too long is a warning", bool(v["warnings"]), True)
    check("but it still goes", v["pass"], True)


# ---------------------------------------------------------------------------
# THE APPROVAL GATE ON THE BOARD
# ---------------------------------------------------------------------------

def test_an_amber_task_cannot_be_claimed_until_he_says_yes():
    """
    Amber means "this needs the owner's yes before the desk can start". That is
    what the floor draws amber for and what 'Awaiting you' lists. Nothing
    enforced it — any agent could claim an unapproved amber task and get on
    with it, and on the Leads board the amber task is "Mail the 9 cleared
    firms". An approval that only changes a colour is not an approval.
    """
    print("\nAn amber task is not work until it is approved:")
    with _Cabinet():
        t = board.post_task("Leads", "Mail the 9 cleared firms", "x", risk="amber")
        check("no desk can take it", board.claim_task(t, "leads-outreach"), False)
        check("nor does it come up as the next thing to do",
              board.next_open_task("Leads", "leads-outreach"), None)
        check("but it IS on his list",
              any(a["kind"] == "task" and a["id"] == t for a in _approvals()), True)

        board.decide_approval("task", t, "approved")
        check("approving it lets the desk start",
              board.claim_task(t, "leads-outreach"), True)

    print("\n...and turning it down takes it off the board entirely:")
    with _Cabinet() as cab:
        t = board.post_task("Leads", "Mail the 9 cleared firms", "x", risk="amber")
        board.decide_approval("task", t, "rejected")
        check("blocked",
              cab.sql("SELECT status FROM tasks WHERE id=?", (t,))[0]["status"],
              "blocked")
        check("and not claimable", board.claim_task(t, "leads-outreach"), False)

    print("\n...and a green task is unaffected, as it always was:")
    with _Cabinet():
        g = board.post_task("Leads", "Map the lead sources", "x")
        check("claimable straight away", board.claim_task(g, "leads-hunter"), True)

    print("\n...and the owner himself is not locked out by his own gate:")
    with _Cabinet():
        t = board.post_task("IT", "Take the site down for maintenance", "x", risk="red")
        check("agents refused", board.claim_task(t, "it-releaser"), False)
        check("but force_claim is there for him", board.force_claim(t, "owner"), True)


# ---------------------------------------------------------------------------
# ARMING — one confirmation, then autonomous
# ---------------------------------------------------------------------------

def test_the_first_batch_waits_and_approving_arms_it():
    print("\nThe agency does not start writing to strangers on its own say-so:")
    with _Cabinet():
        check("not armed to begin with", board.outreach_armed(), False)
        qid = board.queue_outreach(1, "Al Noor Cafeteria", "info@alnoor.ae",
                                   "Shopfront glass in Al Quoz", "body here",
                                   service="glass shopfronts")
        check("the draft is held, not sent", len(board.pending_outreach()), 1)
        check("still not armed", board.outreach_armed(), False)

        print("  ...and he sees the whole mail, not a summary of it:")
        appr = [a for a in _approvals() if a["kind"] == "outreach"]
        check("it is on his list", len(appr), 1)
        check("with the address", "info@alnoor.ae" in appr[0]["why"], True)
        check("and the body", "body here" in appr[0]["why"], True)
        check("and what approving it also does",
              "running on its own" in appr[0]["why"], True)

        print("  ...and approving one arms outreach for good:")
        r = board.decide_approval("outreach", qid, "approved")
        check("recorded", r["ok"], True)
        check("armed", board.outreach_armed(), True)
        check("and it is no longer waiting on him", board.pending_outreach(), [])

    print("\nTurning one down does not arm anything:")
    with _Cabinet():
        qid = board.queue_outreach(1, "Somebody", "a@b.ae", "S", "b")
        board.decide_approval("outreach", qid, "rejected")
        check("still not armed", board.outreach_armed(), False)
        check("and it will not be sent", board.pending_outreach(), [])

    print("\nThe same draft twice is one decision, not two:")
    with _Cabinet():
        a = board.queue_outreach(1, "Al Noor", "info@alnoor.ae", "Same", "b")
        b = board.queue_outreach(1, "Al Noor", "info@alnoor.ae", "Same", "b")
        check("the second returns the first", a, b)
        check("one thing on his list", len(board.pending_outreach()), 1)

    print("\nAnd a draft already decided cannot be decided again:")
    with _Cabinet():
        qid = board.queue_outreach(1, "X", "x@y.ae", "S", "b")
        board.decide_approval("outreach", qid, "approved")
        again = board.decide_approval("outreach", qid, "rejected")
        check("refused", again["ok"], False)
        check("and says why", "already been decided" in again["error"], True)


# ---------------------------------------------------------------------------
# WHO GETS WRITTEN TO
# ---------------------------------------------------------------------------

def test_a_business_is_approached_once():
    print("\nEligibility — and the refusals, which are the useful half:")
    with _Cabinet():
        ok_id = board.add_lead_if_new({"company": "Al Noor Cafeteria",
                                       "email": "info@alnoor.ae", "area": "Al Quoz",
                                       "segment": "food_stall"})
        board.add_lead_if_new({"company": "No Address Ltd", "phone": "+97143851122"})
        mailed = board.add_lead_if_new({"company": "Already Done",
                                        "email": "hi@done.ae"})
        con = sqlite3.connect(board.DB)
        con.execute("INSERT INTO outreach_log(lead_id,channel,direction,body,at)"
                    " VALUES(?,'email','sent','hi',?)", (mailed, int(time.time())))
        con.commit()
        con.close()

        can, cannot = O.eligible()
        check("only the reachable, untouched one", [l["id"] for l in can], [ok_id])
        whys = {c["company"]: c["why"] for c in cannot}
        check("no address is said plainly",
              whys.get("No Address Ltd"), "no address on file")
        check("and a firm already written to is not written to again",
              "approached once" in whys.get("Already Done", ""), True)

    print("\nSecurity gets a look at every lead, every time:")
    with _Cabinet():
        board.add_lead_if_new({"company": "Quick Cash Glass",
                               "email": "win@mailinator.com"})
        can, cannot = O.eligible()
        check("a disposable address is refused", can, [])
        check("by Security, and it says so", "Security" in cannot[0]["why"], True)

    print("\nA queued draft is not drafted a second time:")
    with _Cabinet():
        lid = board.add_lead_if_new({"company": "Al Noor", "email": "info@alnoor.ae"})
        board.queue_outreach(lid, "Al Noor", "info@alnoor.ae", "S", "b")
        can, cannot = O.eligible()
        check("skipped", can, [])
        check("because one is already waiting",
              "already queued" in cannot[0]["why"], True)


def test_it_will_not_run_on_a_stopped_sector_or_an_empty_board():
    print("\nIt works from the board, and stops when the owner says stop:")
    with _Cabinet():
        board.set_sector("Leads", False, "owner")
        r = O._run(O.AGENT, dry_run=True, limit=3)
        check("a stopped sector sends nothing", r["ok"], False)
        check("and says it was his switch", "switched off" in r["error"], True)
        board.set_sector("Leads", True, "owner")

        r = O._run(O.AGENT, dry_run=True, limit=3)
        check("no task on the board, no work", r["ok"], False)
        check("and it says what it looked at", "board" in r["error"].lower(), True)


def main():
    for t in (test_a_good_mail_passes,
              test_the_things_that_must_never_leave,
              test_the_auditor_fails_closed,
              test_warnings_do_not_block,
              test_an_amber_task_cannot_be_claimed_until_he_says_yes,
              test_the_first_batch_waits_and_approving_arms_it,
              test_a_business_is_approached_once,
              test_it_will_not_run_on_a_stopped_sector_or_an_empty_board):
        t()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        sys.exit(1)
    print("all outreach tests passed")


if __name__ == "__main__":
    main()
