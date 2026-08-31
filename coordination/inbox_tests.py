"""
inbox_tests.py — run: python -m coordination.inbox_tests

The promise: a request addressed to a sector is always answered.

Not "answered correctly" — answered at all. The bug this file guards against is
silence: nine real messages a day composed, posted, and read by nobody, with no
error raised and no lamp turned red, because reading the inbox had only ever
been written for two of the seven sectors.

So every test here ends the same way — something is on the thread. Whether the
sector knew the answer, filed it as work, or refused it, the asker can tell.
"""
import sqlite3
import tempfile
import time
from pathlib import Path

from . import board
from . import inbox

_fail = []
ROOT = Path(__file__).parents[1]


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
        return self

    def __exit__(self, *a):
        board.DB = self.real


def _thread(rid):
    with board._conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT author, body FROM request_msgs WHERE request_id=? ORDER BY id",
            (rid,)).fetchall()]


def _open_tasks(sector):
    with board._conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM tasks WHERE sector=? AND status='open' ORDER BY created_at",
            (sector,)).fetchall()]


def _status(rid):
    with board._conn() as c:
        r = c.execute("SELECT status FROM requests WHERE id=?", (rid,)).fetchone()
    return r["status"] if r else None


# ---------------------------------------------------------------------------


def test_a_sector_with_no_handler_still_answers():
    print("\nNo handler is not an excuse for silence:")
    with _Cabinet():
        rid = board.post_request("SEO", "IT", "Add FAQ schema to the pergola page",
                                 "Six pages need the markup.")
        out = inbox.work_the_inbox("IT", "it-watcher")
        check("the request was read", out["read"], 1)
        check("and filed as work, since IT answers by doing", out["filed"], 1)
        check("the asker has a reply on the thread", len(_thread(rid)), 2)
        # 'claimed', not 'open': the sector has taken the question and owes an
        # answer when the work is done. It is deliberately NOT back in the
        # inbox — that would hand the same ask to the next desk that looks —
        # and release_dead_request_claims leaves it alone because there IS a
        # reply on the thread. inbox.unanswered() ignores it for the same reason.
        check("the sector holds it while the work runs", _status(rid), "claimed")

        tasks = _open_tasks("IT")
        check("a claimable task now exists", len(tasks), 1)
        check("titled after the ask", tasks[0]["title"],
              "Add FAQ schema to the pergola page")
        check("and it points back at the request",
              tasks[0]["about"], f"requests:{rid}")


def test_the_reply_names_the_task_so_the_asker_can_follow_it():
    print("\nAn acknowledgement is not an answer, and says so:")
    with _Cabinet():
        rid = board.post_request("Content", "Report", "How did last month's pages do?",
                                 "The four service pages.")
        inbox.work_the_inbox("Report", "reporter")
        reply = _thread(rid)[-1]["body"]
        tid = _open_tasks("Report")[0]["id"]
        check("the task id is in the reply", tid in reply, True)
        check("and it does not pretend to be an answer",
              "not an answer" in reply, True)


def test_a_handler_that_knows_the_answer_closes_the_thread():
    print("\nA sector that can answer, answers:")
    with _Cabinet():
        rid = board.post_request("Leads", "Security", "Clear this lead", "acme-glass.ae")

        def handler(req, body, con):
            return "Cleared — real company, real site."

        out = inbox.work_the_inbox("Security", "security-screener", handler=handler)
        check("answered", out["answered"], 1)
        check("nothing was filed as work", out["filed"], 0)
        check("the thread is closed", _status(rid), "closed")
        check("with the answer on it", _thread(rid)[-1]["body"],
              "Cleared — real company, real site.")


def test_a_handler_can_answer_and_leave_the_thread_open():
    print("\nAn answer that expects a follow-up stays open:")
    with _Cabinet():
        rid = board.post_request("Content", "SEO", "Clear these page targets", "- a\n- b")
        inbox.work_the_inbox("SEO", "seo-strategist",
                             handler=lambda r, b, c: ("Two cleared, one held.", False))
        check("not closed — Content still owes a reply", _status(rid) == "closed", False)
        check("and SEO's answer is on the thread",
              _thread(rid)[-1]["body"], "Two cleared, one held.")


def test_a_handler_that_raises_gives_the_claim_back():
    print("\nA crash must not bury the question at status='claimed':")
    with _Cabinet():
        rid = board.post_request("SEO", "Content", "Rivals published four pages", "list")

        def explodes(req, body, con):
            raise RuntimeError("the keyword table is not there")

        out = inbox.work_the_inbox("Content", "content-lead", handler=explodes)
        check("counted as returned, not answered", out["returned"], 1)
        check("back in the inbox for the next pass", _status(rid), "open")
        check("and the reason is on the thread",
              "the keyword table is not there" in _thread(rid)[-1]["body"], True)
        check("so the sector can see it again",
              [r["id"] for r in board.open_requests_for("Content")], [rid])


def test_a_switched_off_sector_declines_rather_than_going_quiet():
    print("\nSwitched off is an answer too:")
    with _Cabinet():
        rid = board.post_request("SEO", "Content", "Write these four pages", "list")
        board.set_sector("Content", False, note="testing")
        out = inbox.work_the_inbox("Content", "content-lead")
        check("declined", out["declined"], 1)
        check("thread closed — asking again tomorrow is a fresh question",
              _status(rid), "closed")
        check("and it says the sector is off",
              "switched off" in _thread(rid)[-1]["body"], True)


def test_a_busy_desk_holds_the_question_rather_than_losing_it():
    print("Busy is not the same as no:")
    with _Cabinet():
        board.post_task("Report", "First job", "d", desk="reporter")
        rid = board.post_request("SEO", "Report", "Score these decisions", "b")
        with board.agent_run("reporter"):
            out = inbox.work_the_inbox("Report", "reporter")
        check("held for later, not declined", out["later"], 1)
        check("nothing was closed", _status(rid) == "closed", False)
        check("and it is back in the inbox for the next pass",
              [r["id"] for r in board.open_requests_for("Report")], [rid])
        check("with the reason on the thread",
              "Left open" in _thread(rid)[-1]["body"], True)


def test_a_switched_off_sector_is_a_decision_and_closes():
    print("But an owner switching a sector off IS a no:")
    with _Cabinet():
        rid = board.post_request("SEO", "Content", "Write these pages", "b")
        board.set_sector("Content", False, note="testing")
        out = inbox.work_the_inbox("Content", "content-lead")
        check("declined, not held", out["declined"], 1)
        check("and closed", _status(rid), "closed")


def test_the_whole_inbox_is_read_not_just_the_first():
    print("\nEvery message, not the top one:")
    with _Cabinet():
        for i in range(4):
            board.post_request("SEO", "Leads", f"Question {i}", "b")
        out = inbox.work_the_inbox("Leads", "leads-hunter")
        check("all four read", out["read"], 4)
        check("nothing left waiting", board.open_requests_for("Leads"), [])


def test_a_pass_is_capped_so_one_sector_cannot_eat_the_day():
    print("\nA hundred-deep inbox is cleared over several passes:")
    with _Cabinet():
        for i in range(20):
            board.post_request("SEO", "Report", f"Question {i}", "b")
        out = inbox.work_the_inbox("Report", "reporter", limit=5)
        check("five this pass", out["read"], 5)
        check("the rest are still there", len(board.open_requests_for("Report")), 15)


def test_two_desks_cannot_both_answer_one_request():
    print("\nClaim, do not grab:")
    with _Cabinet():
        rid = board.post_request("SEO", "IT", "One question", "b")
        first = inbox.work_the_inbox("IT", "it-watcher")
        second = inbox.work_the_inbox("IT", "it-fixer")
        check("the first desk got it", first["read"], 1)
        check("the second found nothing to claim", second["read"], 0)
        del rid


# --- the address bug ---------------------------------------------------------


def test_a_request_cannot_be_addressed_to_a_sector_that_does_not_exist():
    print("\nThe 'MD' bug — an address nobody reads:")
    with _Cabinet():
        try:
            board.post_request("Leads", "Marketing", "Scan this", "b")
            check("refused", False, True)
        except inbox.UnknownSector as e:
            check("refused at the moment of posting", True, True)
            check("and names the seven", "Director, IT, Content" in str(e), True)


def test_md_is_understood_as_director():
    print("\nThe alias that mail.py needed:")
    with _Cabinet():
        rid = board.post_request("Leads", "MD", "Scan this mail before send", "draft")
        check("landed in the Director's inbox",
              [r["id"] for r in board.open_requests_for("Director")], [rid])
        check("and the Director's reader can see it",
              inbox.work_the_inbox("Director", "managing-director")["read"], 1)


def test_the_senders_name_is_normalised_too():
    print("\nSo a thread cannot come from two spellings of one sector:")
    with _Cabinet():
        board.post_request("md", "IT", "A question", "b")
        with board._conn() as c:
            got = c.execute("SELECT from_sector FROM requests").fetchone()[0]
        check("from Director", got, "Director")


def test_owner_and_overseer_are_not_corrected_into_sectors():
    print("\nThe two senders that are not sectors:")
    with _Cabinet():
        board.post_request("Overseer", "Director", "Escalating", "b")
        with board._conn() as c:
            got = c.execute("SELECT from_sector FROM requests").fetchone()[0]
        check("still the Overseer", got, "Overseer")


# --- closing the loop --------------------------------------------------------


def test_finishing_a_filed_task_answers_the_original_asker():
    print("\n'Filed' is only honest if the answer eventually arrives:")
    with _Cabinet():
        rid = board.post_request("SEO", "IT", "Add the schema", "six pages")
        inbox.work_the_inbox("IT", "it-watcher")
        tid = _open_tasks("IT")[0]["id"]
        back = inbox.answer_when_done(tid, "it-fixer", "Done — schema is on all six.")
        check("answered on the original thread", back, rid)
        check("closed", _status(rid), "closed")
        check("with the result", _thread(rid)[-1]["body"], "Done — schema is on all six.")


def test_a_task_that_did_not_come_from_a_request_answers_nobody():
    print("\nAnd it does not invent a thread for ordinary work:")
    with _Cabinet():
        tid = board.post_task("IT", "Ordinary work", "d")
        check("nothing to answer", inbox.answer_when_done(tid, "it-fixer", "done"), "")


def test_unanswered_counts_claimed_as_well_as_open():
    print("\nA claimed request nobody replied to is still unanswered:")
    with _Cabinet():
        rid = board.post_request("SEO", "IT", "A question", "b")
        board.claim_request(rid, "it-watcher")
        check("still counted", [r["id"] for r in inbox.unanswered("IT")], [rid])
        board.reply_request(rid, "it-watcher", "here you go")
        check("and not once it is answered", inbox.unanswered("IT"), [])


def test_read_post_never_takes_the_caller_down():
    print("\nReading the post is a courtesy, never a crash:")
    with _Cabinet():
        real = board.DB
        board.DB = "/nonexistent/place/agency.db"
        try:
            out = inbox.read_post("IT", "it-watcher")
        finally:
            board.DB = real
        check("came back rather than raising", out["ok"], False)
        check("and said why", bool(out.get("error")), True)


# --- the whole point ---------------------------------------------------------


def test_the_nine_dead_edges_are_all_answered_now():
    print("\nEvery edge that used to go nowhere:")
    with _Cabinet():
        edges = [
            ("SEO", "Content", "Service pages worth building this month"),
            ("SEO", "Content", "Guest posts worth writing"),
            ("SEO", "Content", "Answer-engine gaps on our own pages"),
            ("SEO", "Content", "Rivals published pages about services we sell"),
            ("SEO", "IT", "Schema and markup fixes"),
            ("Content", "SEO", "Clear these page targets"),
            ("IT", "IT", "Tickets I have no fix for"),
            ("Leads", "Director", "Scan this outreach draft"),
            ("Overseer", "Report", "Score these decisions"),
        ]
        ids = [board.post_request(f, t, s, "the body") for f, t, s in edges]
        readers = {"Content": "content-lead", "IT": "it-watcher", "SEO": "seo-strategist",
                   "Director": "managing-director", "Report": "reporter"}
        for sector, agent in readers.items():
            inbox.work_the_inbox(sector, agent)
        silent = [rid for rid in ids if len(_thread(rid)) < 2]
        check("none of the nine is left without a reply", silent, [])


def test_blocking_on_a_sector_that_went_home_returns_at_once():
    print("\nTwo minutes waiting for an empty room, per mail:")
    with _Cabinet():
        t0 = time.time()
        got = board.request_verification("Leads", "MD", "Scan this before send",
                                         "the draft", blocking=True, timeout=120)
        took = time.time() - t0
        check("no answer, same as before", got, None)
        check("but in a moment, not two minutes", took < 5, True)
        with board._conn() as c:
            note = c.execute("SELECT body FROM request_msgs ORDER BY id DESC"
                             " LIMIT 1").fetchone()[0]
        check("and the thread says nobody was there",
              "No desk in Director is running" in note, True)
        check("the ask stays open for the sector's next pass",
              len(board.open_requests_for("Director")), 1)


def test_it_still_waits_when_somebody_could_answer():
    print("\nBut a live desk is worth waiting for:")
    with _Cabinet():
        with board.agent_run("managing-director"):
            t0 = time.time()
            board.request_verification("Leads", "Director", "A question", "b",
                                       blocking=True, timeout=4)
            check("waited the timeout out", 3.5 <= time.time() - t0 <= 8, True)


def test_the_grace_check_reads_the_real_sector_name():
    print("\nThe bug inside the fix — 'MD' has no desks, so it waited anyway:")
    with _Cabinet():
        check("MD resolves to the sector whose desks we look up",
              board._answerable_grace("MD", 120), board._answerable_grace("Director", 120))


def test_a_sector_writing_to_itself_is_still_answered():
    print("\nIT filing a ticket to IT:")
    with _Cabinet():
        rid = board.post_request("IT", "IT", "No fix for this one", "the detail")
        out = inbox.work_the_inbox("IT", "it-watcher")
        check("read", out["read"], 1)
        check("and replied to", len(_thread(rid)) >= 2, True)


def main():
    for fn in list(globals().values()):
        if callable(fn) and getattr(fn, "__name__", "").startswith("test_"):
            fn()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        raise SystemExit(1)
    print("inbox: all good — every sector answers its post.")


if __name__ == "__main__":
    main()
