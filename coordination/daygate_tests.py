"""
daygate_tests.py — run: python -m coordination.daygate_tests

The promise: nothing starts before the owner starts the day, and nothing
half-finished is left hanging because of it.

    "until i did not start the day they should not start the work if they had
     some task left behind those agents will complete their previous recorded
     task first"

Two rules that pull in opposite directions, and most of this file is about the
seam between them. Taking something NEW off the board is starting work, and
waits for him. Finishing something already claimed is not starting work — it is
stopping being half-done — and gating that would rebuild the ninety-hour
deadlock on purpose: a claim aging on the board until the Overseer files it as
abandoned by a desk that was never allowed to finish it.
"""
import os
import sqlite3
import tempfile
import time
from pathlib import Path

from . import board, dispatch

_fail = []
ROOT = Path(__file__).parents[1]


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


class _Cabinet:
    """A throwaway cabinet. `in_service` gives it a past day, so the gate bites."""

    def __init__(self, in_service=True):
        self.in_service = in_service

    def __enter__(self):
        self.real = board.DB
        self.real_env = os.environ.pop("WORK_OUTSIDE_THE_DAY", None)
        d = Path(tempfile.mkdtemp())
        db = d / "agency.db"
        con = sqlite3.connect(db)
        con.executescript((ROOT / "coordination" / "schema.sql").read_text(encoding="utf-8"))
        con.commit()
        con.close()
        board.DB = str(db)
        if self.in_service:
            self.yesterday()
        return self

    def __exit__(self, *a):
        board.DB = self.real
        os.environ.pop("WORK_OUTSIDE_THE_DAY", None)
        if self.real_env is not None:
            os.environ["WORK_OUTSIDE_THE_DAY"] = self.real_env

    def yesterday(self):
        """A day that ran and finished. The cabinet is now in service."""
        with sqlite3.connect(board.DB) as c:
            c.execute("INSERT INTO day_runs(id,started_at,status,started_by,"
                      "steps_total,steps_done,pid,host) VALUES(?,?,'ok','owner',"
                      "1,1,?,?)", (f"past{time.time()}", int(time.time()) - 90000,
                                   os.getpid(), board._this_host()))

    def open_the_day(self, pid=None):
        with sqlite3.connect(board.DB) as c:
            c.execute("INSERT INTO day_runs(id,started_at,status,started_by,"
                      "steps_total,steps_done,pid,host) VALUES('today',?,"
                      "'running','owner',1,0,?,?)",
                      (int(time.time()), pid or os.getpid(), board._this_host()))

    @staticmethod
    def hold(task_id, agent, ago=100):
        with sqlite3.connect(board.DB) as c:
            c.execute("UPDATE tasks SET status='in_progress', owner=?,"
                      " claimed_at=? WHERE id=?",
                      (agent, int(time.time()) - ago, task_id))


# ---------------------------------------------------------------------------


def test_a_cabinet_that_has_never_run_a_day_is_not_gated():
    print("\nA cabinet with no history is not out of step with anything:")
    with _Cabinet(in_service=False):
        d = board.day_is_open()
        check("no day open", d["open"], False)
        check("but the gate is not in force", d["enforced"], False)
        check("and it says why plainly",
              "never run a day" in d["why"], True)
        board.post_task("SEO", "Find the gaps", "d", desk="seo-strategist")
        got = board.next_open_task("SEO", "seo-strategist")
        check("so the desk works", bool(got), True)


def test_once_a_day_has_run_the_gate_is_live_for_ever():
    print("\nOne day in the history and the rhythm exists:")
    with _Cabinet():
        d = board.day_is_open()
        check("closed", d["open"], False)
        check("and enforced", d["enforced"], True)
        check("saying so", d["why"], "no day is running")


def test_no_new_work_is_taken_before_the_day_starts():
    print("\nNew work waits for him:")
    with _Cabinet():
        board.post_task("SEO", "Find the gaps", "d", desk="seo-strategist")
        got = board.next_open_task("SEO", "seo-strategist")
        check("nothing claimed", got, None)
        with board._conn() as c:
            still = c.execute("SELECT status FROM tasks").fetchone()[0]
        check("and the task is untouched, not lost", still, "open")


def test_the_desk_is_told_the_real_reason():
    print("\nAnd it does NOT say 'nothing on the board' with work sitting on it:")
    with _Cabinet():
        board.post_task("SEO", "Find the gaps", "d", desk="seo-strategist")
        why = dispatch.why_nothing("SEO", "seo-strategist")
        check("it names the day, not the board",
              "The day has not started" in why, True)
        check("it counts the work that is waiting", "1 task(s) are waiting" in why, True)
        check("it says how to start", "day_run --start" in why, True)
        check("and how to work one pipeline anyway",
              "WORK_OUTSIDE_THE_DAY=1" in why, True)
        check("it never claims the board is empty",
              "Nothing open on the SEO board" in why, False)


def test_held_work_is_finished_whatever_the_clock_says():
    print("\nBut a half-finished job is not 'starting work':")
    with _Cabinet():
        tid = board.post_task("SEO", "Half-finished job", "d", desk="seo-strategist")
        _Cabinet.hold(tid, "seo-strategist")
        got = board.next_open_task("SEO", "seo-strategist")
        check("handed straight back", got["id"] if got else None, tid)
        check("with the reason",
              "did not finish it" in (got or {}).get("why_me", ""), True)


def test_held_work_comes_back_even_with_new_work_also_waiting():
    print("\nHeld first, and the new work still waits:")
    with _Cabinet():
        held = board.post_task("SEO", "Half-finished job", "d", desk="seo-strategist")
        _Cabinet.hold(held, "seo-strategist")
        board.post_task("SEO", "Something new", "d", desk="seo-strategist")
        first = board.next_open_task("SEO", "seo-strategist")
        check("the held one", first["id"] if first else None, held)
        board.finish_task(held, "seo-strategist", "done")
        second = board.next_open_task("SEO", "seo-strategist")
        check("and then nothing, because the day is still shut", second, None)


def test_starting_the_day_releases_everything():
    print("\nHe presses Run:")
    with _Cabinet() as cab:
        board.post_task("SEO", "Find the gaps", "d", desk="seo-strategist")
        check("shut", board.next_open_task("SEO", "seo-strategist"), None)
        cab.open_the_day()
        d = board.day_is_open()
        check("open", d["open"], True)
        check("and enforced still", d["enforced"], True)
        got = board.next_open_task("SEO", "seo-strategist")
        check("the waiting task is picked up",
              got["title"] if got else None, "Find the gaps")


def test_a_day_row_left_by_a_closed_program_is_not_an_open_day():
    print("\nThe program he shut is not a day that is running:")
    with _Cabinet() as cab:
        cab.open_the_day(pid=999_999)          # a pid that is not alive
        d = board.day_is_open()
        check("not open", d["open"], False)
        check("and it says why",
              "no longer running" in d["why"], True)
        board.post_task("SEO", "Find the gaps", "d", desk="seo-strategist")
        check("so no new work", board.next_open_task("SEO", "seo-strategist"), None)


def test_a_step_subprocess_sees_the_days_open_even_though_it_is_not_the_parent():
    print("\nThe mechanic the whole gate rests on:")
    # day_run runs each step as its own `python -m <module>` subprocess, so the
    # process asking "is the day open?" is never the process whose pid is on
    # the day_runs row. If liveness were read as "am I the day", every step of
    # every real day would refuse its own work — the gate would break exactly
    # the thing it was built to protect. Proved with a real, live, different
    # process rather than a mocked pid.
    import subprocess
    import sys
    parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        with _Cabinet() as cab:
            cab.open_the_day(pid=parent.pid)
            check("this process is not the parent", os.getpid() == parent.pid, False)
            check("and still sees the day open", board.day_is_open()["open"], True)
            board.post_task("SEO", "the day's job", "d", desk="seo-strategist")
            got = board.next_open_task("SEO", "seo-strategist")
            check("so the step does its work",
                  got["title"] if got else None, "the day's job")

            parent.terminate()
            parent.wait()
            time.sleep(0.3)
            check("and when the parent goes, the day shuts for everyone",
                  board.day_is_open()["open"], False)
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait()


def test_he_can_stand_the_gate_down_at_his_own_terminal():
    print("\nRefusing the owner at his own terminal would be the program arguing:")
    with _Cabinet():
        board.post_task("SEO", "Find the gaps", "d", desk="seo-strategist")
        check("shut", board.next_open_task("SEO", "seo-strategist"), None)
        os.environ["WORK_OUTSIDE_THE_DAY"] = "1"
        d = board.day_is_open()
        check("standing open", d["open"], True)
        check("and honest that it is an override", d["enforced"], False)
        got = board.next_open_task("SEO", "seo-strategist")
        check("he gets his work", bool(got), True)


def test_the_override_takes_the_usual_spellings():
    print("\nAnd it is not fussy about how he writes it:")
    with _Cabinet():
        for v in ("1", "true", "TRUE", "yes", "on"):
            os.environ["WORK_OUTSIDE_THE_DAY"] = v
            check(f"{v!r} opens it", board.day_is_open()["open"], True)
        for v in ("0", "false", "no", ""):
            os.environ["WORK_OUTSIDE_THE_DAY"] = v
            check(f"{v!r} does not", board.day_is_open()["open"], False)


def test_the_reporter_does_not_wire_a_digest_for_a_day_that_did_not_happen():
    print("\nThe one desk that works without claiming, and messages him:")
    import pipelines.report_daily as RD
    sent = []
    real = RD.integrations.send_message
    RD.integrations.send_message = lambda text: (sent.append(text),
                                                 {"ok": True, "detail": "sent"})[1]
    try:
        with _Cabinet() as cab:
            out = RD.run()
            check("it still did the sums", out.get("ok"), True)
            check("but sent nothing", out.get("telegram_sent"), False)
            check("and said why", "has not happened" in out.get("telegram_detail", ""), True)
            check("the digest is there to read", bool(out.get("digest")), True)
            check("nothing left the building", sent, [])

            cab.open_the_day()
            out2 = RD.run()
            check("with the day open it sends", out2.get("telegram_sent"), True)
            check("once", len(sent), 1)
    finally:
        RD.integrations.send_message = real


def test_an_unreadable_cabinet_does_not_stop_the_work():
    print("\nA cabinet that cannot be read is a different problem:")
    real = board.DB
    board.DB = "/nonexistent/place/agency.db"
    try:
        d = board.day_is_open()
        check("the gate stands aside", d["enforced"], False)
        check("rather than blocking everything", d["open"], True)
    finally:
        board.DB = real


def main():
    for fn in list(globals().values()):
        if callable(fn) and getattr(fn, "__name__", "").startswith("test_"):
            fn()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        raise SystemExit(1)
    print("day gate: all good — new work waits for him, held work never does.")


if __name__ == "__main__":
    main()
