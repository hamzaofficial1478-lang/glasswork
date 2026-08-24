"""
triage_tests.py — run: python -m coordination.triage_tests

The owner asked what happens when five sectors ask the MD at once, and whether
something urgent ends up behind something trivial that merely arrived first.

Before this, three things were true and all of them were bad: the queue was
arrival order, nothing measured urgency, and the MD never opened its inbox at
all — twenty-six requests had been sitting in it for two days, including every
escalation the Overseer had raised about a dead agent.

What is protected here is the fix, and the two failures it has to solve AT ONCE,
because solving either one alone makes the other worse:

  URGENT WAITS     first-come-first-served puts a broken enquiry form behind a
                   keyword question that asked earlier.
  TRIVIAL STARVES  pure priority never lets the bottom of the queue rise, and
                   "we will get to it" becomes something the system never does.

So there are tests that weight beats arrival order, and tests that a long enough
wait beats weight. And a test of the boundary the ageing must NOT cross: a
three-week-old question about a sitemap still does not outrank a customer.

The last group is about where weight comes from. An asker who says "urgent" is
not evidence; a row is. A request names the row it is about and triage reads it.
The first version guessed from words in the subject line and instantly ranked a
stalled-agent notice as a waiting customer, because the agent is called
'leads-outreach' and that contains 'lead'.
"""
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

from . import board
from . import triage

_fail = []
ROOT = Path(__file__).parents[1]
HOUR = 3600


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
        con.executescript((ROOT / "coordination" / "schema.sql").read_text())
        con.commit(); con.close()
        board.DB = str(db)
        board._MIGRATED.discard(str(db))
        return self

    def __exit__(self, *a):
        board.DB = self.real

    def age(self, rid, hours):
        c = sqlite3.connect(board.DB)
        c.execute("UPDATE requests SET created_at=? WHERE id=?",
                  (int(time.time()) - int(hours * HOUR), rid))
        c.execute("UPDATE request_msgs SET at=? WHERE request_id=?",
                  (int(time.time()) - int(hours * HOUR), rid))
        c.commit(); c.close()

    def sql(self, q, args=()):
        c = sqlite3.connect(board.DB); c.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in c.execute(q, args)]
        finally:
            c.close()


def _order(sector="Director", limit=20):
    q = triage.queue(sector, limit=limit)
    return [r["subject"] for r in q["next"] + q["waiting"]]


def test_arrival_order_is_not_the_answer():
    print("\nWhat it is worth beats when it arrived:")
    with _Cabinet() as cab:
        first = board.post_request("SEO", "Director", "Worth chasing this keyword?", "b")
        cab.age(first, 2)
        board.post_request("IT", "Director", "The contact form is dead", "b",
                           blocking=True)
        order = _order()
        check("the blocking one is first even though it asked second",
              order[0], "The contact form is dead")
        check("and the earlier one is still there, not dropped",
              "Worth chasing this keyword?" in order, True)


def test_nothing_at_the_back_starves():
    """
    The other half, and the half that pure priority gets wrong. A low-weight
    request must eventually overtake a high-weight one, or the bottom of the
    queue is a promise the agency never keeps.
    """
    print("\nWait long enough and you go first anyway:")
    with _Cabinet() as cab:
        old = board.post_request("SEO", "Director", "A small question", "b")
        cab.age(old, 2)
        blocked = board.post_request("IT", "Director", "I am blocked", "b", blocking=True)
        check("fresh and blocking outranks a 2h-old ordinary ask",
              _order()[0], "I am blocked")

        cab.age(old, 60)     # two and a half days of being ignored
        check("but after two and a half days the small one goes first",
              _order()[0], "A small question")
        check("and the blocked one has not been dropped, just moved",
              _order()[1], "I am blocked")


def test_ageing_cannot_outrank_a_person():
    """
    The boundary. Ageing has to be capped or a forgotten question about a
    sitemap eventually outranks a customer waiting for a quote, which would be
    the system confidently doing the wrong thing.
    """
    print("\nAgeing has a ceiling, and a waiting person is above it:")
    with _Cabinet() as cab:
        c = sqlite3.connect(board.DB)
        c.execute("INSERT INTO outreach_log(id,lead_id,direction,body,needs_owner,at)"
                  " VALUES(7,1,'reply','Can you quote this?',1,?)", (int(time.time()),))
        c.commit(); c.close()

        ancient = board.post_request("SEO", "Director", "Sitemap question", "b")
        cab.age(ancient, 24 * 30)                       # a month
        board.post_request("Leads", "Director", "Customer asked for a price", "b",
                           about="outreach_log:7")
        order = _order()
        check("the customer is first", order[0], "Customer asked for a price")
        check("even against a month-old request", order[1], "Sitemap question")
        top = triage.queue("Director")["next"][0]
        check("and it says why, from the row",
              "outreach_log:7" in top["why"][0]["label"], True)


def test_weight_comes_from_a_row_not_from_words():
    """
    The bug this replaced. 'Escalating: leads-outreach claimed ...' contains the
    word 'lead', and the first version read that as a customer waiting.
    """
    print("\nA word in a sentence is not evidence:")
    with _Cabinet() as cab:
        c = sqlite3.connect(board.DB)
        c.execute("INSERT INTO outreach_log(id,lead_id,direction,body,needs_owner,at)"
                  " VALUES(7,1,'reply','hello',1,?)", (int(time.time()),))
        c.commit(); c.close()
        # Mentions leads, replies and customers. Points at nothing.
        board.post_request("Overseer", "Director",
                           'Escalating: leads-outreach claimed "Mail the customers"',
                           "b")
        top = triage.queue("Director")["next"][0]
        check("it is scored as an escalation, not as a customer",
              top["why"][0]["label"],
              "the Overseer escalated it after routing failed")
        check("well below what a real waiting customer scores",
              top["points"] < triage.W_PERSON_WAITING, True)


def test_a_stale_reference_earns_nothing():
    print("\nUrgency that was true yesterday is not urgency today:")
    with _Cabinet():
        # The reply exists but has been dealt with — needs_owner is 0.
        c = sqlite3.connect(board.DB)
        c.execute("INSERT INTO outreach_log(id,lead_id,direction,body,needs_owner,at)"
                  " VALUES(9,1,'reply','sorted',0,?)", (int(time.time()),))
        c.commit(); c.close()
        board.post_request("Leads", "Director", "About a reply", "b",
                           about="outreach_log:9")
        top = triage.queue("Director")["next"][0]
        check("no boost for an answered reply", top["why"][0]["label"],
              "an ordinary ask")
        check("and a reference to a row that does not exist is ignored too",
              triage._about(sqlite3.connect(board.DB), "outreach_log:9999"), (None, ""))


def test_asking_five_times_is_one_problem_not_five():
    print("\nThe same question five times is one blockage, weighted as one:")
    with _Cabinet():
        # Inserted directly, not via post_request — post_request refuses to make
        # a duplicate now. This is the LEGACY case and it still matters: the
        # owner's cabinet already holds eighteen copies of one question from
        # before the dedup existed, and they have to collapse when read.
        now = int(time.time())
        c = sqlite3.connect(board.DB)
        for i in range(5):
            c.execute("INSERT INTO requests(id,from_sector,to_sector,subject,status,"
                      "created_at) VALUES(?,'SEO','Director','Clear these page targets',"
                      "'open',?)", (f"dup{i}", now - 3600 + i))
            c.execute("INSERT INTO request_msgs(request_id,author,body,at)"
                      " VALUES(?, 'SEO','b',?)", (f"dup{i}", now - 3600 + i))
        c.commit(); c.close()
        board.post_request("IT", "Director", "A different thing", "b")
        q = triage.queue("Director")
        check("five copies collapse to one entry", q["total"], 2)
        top = q["next"][0]
        check("the repeated one is first", top["subject"], "Clear these page targets")
        check("and it says how many times", top["copies"], 5)
        check("all five ids are carried, so answering closes them together",
              len(top["ids"]), 5)


def test_the_queue_shows_what_it_is_not_doing():
    print("\nA queue that hides its own length is how 26 went unnoticed:")
    with _Cabinet():
        for i in range(9):
            board.post_request("SEO", "Director", f"Question {i}", "b")
        q = triage.queue("Director", limit=3)
        check("three are for this pass", len(q["next"]), 3)
        check("six are visibly waiting", len(q["waiting"]), 6)
        check("every one knows its position",
              [r["position"] for r in q["next"] + q["waiting"]], list(range(1, 10)))
        check("and the headline says so", q["head"], "9 waiting, dealing with 3 this pass")


def test_every_ranking_can_be_argued_with():
    print("\nNothing is ranked without saying why:")
    with _Cabinet() as cab:
        r = board.post_request("IT", "Director", "Blocked on a decision", "b",
                               blocking=True)
        cab.age(r, 5)
        top = triage.queue("Director")["next"][0]
        labels = [w["label"] for w in top["why"]]
        check("the weight is explained", "IT says it cannot continue" in labels, True)
        check("the wait is explained",
              any("waiting 5h" in l for l in labels), True)
        check("and the points add up to the score",
              round(sum(w["points"] for w in top["why"]), 1), top["points"])


def test_the_inbox_is_read_in_triage_order():
    print("\nThe order reaches the sector reading its post, not just this module:")
    with _Cabinet() as cab:
        old = board.post_request("SEO", "Security", "Ordinary check", "b")
        cab.age(old, 3)
        board.post_request("Leads", "Security", "Clear this before we contact them",
                           "b", blocking=True)
        got = [r["subject"] for r in board.open_requests_for("Security")]
        check("open_requests_for hands back the urgent one first",
              got[0], "Clear this before we contact them")
        check("in plain arrival order it would have been second",
              [r["subject"] for r in board.open_requests_for("Security", triaged=False)][0],
              "Ordinary check")


def main():
    for t in (test_arrival_order_is_not_the_answer,
              test_nothing_at_the_back_starves,
              test_ageing_cannot_outrank_a_person,
              test_weight_comes_from_a_row_not_from_words,
              test_a_stale_reference_earns_nothing,
              test_asking_five_times_is_one_problem_not_five,
              test_the_queue_shows_what_it_is_not_doing,
              test_every_ranking_can_be_argued_with,
              test_the_inbox_is_read_in_triage_order,
              test_the_md_actually_opens_its_post,
              test_a_dry_run_reads_but_changes_nothing,
              test_it_does_not_pile_work_on_a_stopped_sector,
              test_asking_again_does_not_fill_the_board,
              test_two_equally_urgent_requests_have_a_defined_order):
        t()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
    else:
        print("all passed")
    return not _fail




def test_the_md_actually_opens_its_post():
    """
    The gap underneath the owner's question. Ordering an inbox nobody reads is
    tidying a room with the door locked: the MD had twenty-six requests, the
    oldest two days old, and not one line of code ever claimed one.
    """
    print("\nThe MD reads its inbox, and answering means closing it:")
    from pipelines import md_cycle
    with _Cabinet() as cab:
        # Real fault rows, referenced by the escalations — that is how the
        # Overseer actually raises these, and how the MD knows whose desk it is
        # without reading the subject line for a sector name.
        c = sqlite3.connect(board.DB)
        c.execute("INSERT INTO faults(id,key,kind,agent,sector,routed_to,severity,"
                  "what,status,first_seen,last_seen) VALUES"
                  "(1,'k1','stall','seo-strategist','SEO','SEO','degraded',"
                  "'seo-strategist stopped responding','routed',0,0)")
        c.execute("INSERT INTO faults(id,key,kind,agent,sector,routed_to,severity,"
                  "what,status,first_seen,last_seen) VALUES"
                  "(2,'k2','sector','','Content','Content','degraded',"
                  "'Content has 3 faults open','escalated',0,0)")
        c.commit(); c.close()
        rid = board.post_request(
            "Overseer", "Director",
            "Escalating: seo-strategist stopped responding", "It has been dead 4 days.",
            about="faults:1")
        board.post_request("Overseer", "Director", "Content has 3 faults open", "b",
                           about="faults:2")

        out = md_cycle._cycle("managing-director", dry_run=False)
        ib = out["inbox"]
        check("it dealt with both", len(ib["answered"]), 2)
        check("nothing left queued", ib["still_waiting"], 0)

        rows = {r["id"]: r for r in cab.sql("SELECT * FROM requests")}
        check("the escalation is closed, not just read", rows[rid]["status"], "closed")
        check("and the MD is on record as the handler",
              rows[rid]["handler"], "managing-director")

        msgs = cab.sql("SELECT * FROM request_msgs WHERE request_id=? ORDER BY at", (rid,))
        check("it replied in the thread", len(msgs) >= 2, True)
        reply = msgs[-1]["body"]
        check("saying where it came in the queue", "in the queue" in reply, True)

        # The brain never touches the hands: it routes, it does not fix.
        # Filtered to what the INBOX routed — the MD's ordinary planning posts
        # tasks too, and counting those made this assert on the wrong thing.
        tasks = cab.sql("SELECT sector, title FROM tasks"
                        " WHERE created_by='MD' AND title LIKE 'From the MD:%'")
        check("the work went to the sector that can act",
              sorted({t["sector"] for t in tasks}), ["Content", "SEO"])
        check("and it is only the inbox items that were routed", len(tasks), 2)
        check("and the reply names the task",
              any(t["title"][:20] in reply or "as task" in reply for t in tasks), True)


def test_a_dry_run_reads_but_changes_nothing():
    print("\n--dry-run reads the post and puts it back:")
    from pipelines import md_cycle
    with _Cabinet() as cab:
        rid = board.post_request("Overseer", "Director", "Escalating: IT is down", "b")
        out = md_cycle._cycle("managing-director", dry_run=True)
        check("it says what it would answer", len(out["inbox"]["answered"]), 1)
        check("but the request is untouched",
              cab.sql("SELECT status FROM requests WHERE id=?", (rid,))[0]["status"], "open")
        check("and nothing was posted to the board",
              cab.sql("SELECT count(*) n FROM tasks")[0]["n"], 0)


def test_it_does_not_pile_work_on_a_stopped_sector():
    print("\nRouting respects the owner's switch:")
    from pipelines import md_cycle
    with _Cabinet() as cab:
        board.set_sector("Content", False, "paused this month")
        c = sqlite3.connect(board.DB)
        c.execute("INSERT INTO faults(id,key,kind,agent,sector,routed_to,severity,"
                  "what,status,first_seen,last_seen) VALUES"
                  "(3,'k3','sector','','Content','Content','degraded',"
                  "'Content has 3 faults open','escalated',0,0)")
        c.commit(); c.close()
        rid = board.post_request("Overseer", "Director",
                                 "Escalating: Content has 3 faults open", "b",
                                 about="faults:3")
        out = md_cycle._cycle("managing-director", dry_run=False)
        check("the request is still answered", len(out["inbox"]["answered"]), 1)
        check("no task was forced onto the stopped sector",
              cab.sql("SELECT count(*) n FROM tasks WHERE sector='Content'")[0]["n"], 0)
        reply = cab.sql("SELECT body FROM request_msgs WHERE request_id=? ORDER BY at",
                        (rid,))[-1]["body"]
        check("and the reply says why nothing was posted",
              "switched off" in reply, True)




def test_asking_again_does_not_fill_the_board():
    """
    The eighteen copies. Every pipeline that raises a request runs daily and
    none of them checked whether they had already asked, so the owner's board
    carried the same question from SEO to Content eighteen times — one per run
    since the day it first went unanswered. That is one problem and seventeen
    copies, and it buries everything else in the receiving inbox.
    """
    print("\nAsking again is a nudge, not another row:")
    with _Cabinet() as cab:
        ids = {board.post_request("SEO", "Content", "AEO/GEO fixes", "the list")
               for _ in range(18)}
        check("eighteen asks make one thread", len(ids), 1)
        check("and one row on the board",
              cab.sql("SELECT count(*) n FROM requests")[0]["n"], 1)
        check("the cooldown stops it nudging on every run",
              cab.sql("SELECT count(*) n FROM request_msgs")[0]["n"], 1)

        rid = list(ids)[0]
        c = sqlite3.connect(board.DB)
        c.execute("UPDATE request_msgs SET at=? WHERE request_id=?",
                  (int(time.time()) - 5 * HOUR, rid))
        c.execute("UPDATE requests SET created_at=? WHERE id=?",
                  (int(time.time()) - 30 * HOUR, rid))
        c.commit(); c.close()
        board.post_request("SEO", "Content", "AEO/GEO fixes", "the list")
        msgs = cab.sql("SELECT body FROM request_msgs ORDER BY at")
        check("after the cooldown it nudges once", len(msgs), 2)
        check("saying how long it has waited", "1 days" in msgs[-1]["body"], True)
        check("still one row", cab.sql("SELECT count(*) n FROM requests")[0]["n"], 1)

        # A DIFFERENT question is a different thread — dedup must not swallow it.
        board.post_request("SEO", "Content", "Something else entirely", "b")
        check("a different question still opens its own thread",
              cab.sql("SELECT count(*) n FROM requests")[0]["n"], 2)
        # And once answered and closed, asking again is legitimately new.
        board.reply_request(rid, "content-writer", "done", close=True)
        board.post_request("SEO", "Content", "AEO/GEO fixes", "the list")
        check("after it is closed, a fresh ask opens a fresh thread",
              cab.sql("SELECT count(*) n FROM requests")[0]["n"], 3)


def test_two_equally_urgent_requests_have_a_defined_order():
    print("\nTwo things that score the same still have an order:")
    with _Cabinet() as cab:
        now = int(time.time())
        a = board.post_request("SEO", "Director", "SEO needs a decision", "b")
        b = board.post_request("IT", "Director", "IT needs a decision", "b",
                               blocking=True)
        c_ = board.post_request("Content", "Director", "Content needs one too", "b")
        con = sqlite3.connect(board.DB)
        for r in (a, b, c_):
            con.execute("UPDATE requests SET created_at=? WHERE id=?", (now - 2 * HOUR, r))
        con.commit(); con.close()

        first = triage.queue("Director")["next"]
        check("the blocked one goes first", first[0]["from"], "IT")
        # The remaining two score identically. The order must be defined and
        # repeatable, not whatever sort() happened to do.
        rest = [r["from"] for r in first[1:]]
        again = [r["from"] for r in triage.queue("Director")["next"][1:]]
        check("the rest are in a stable order", rest, again)
        check("and both are still there", sorted(rest), ["Content", "SEO"])


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
