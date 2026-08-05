"""
floor_tests.py — run: python -m coordination.floor_tests  (python3 on macOS/Linux)

These test one thing: does the office floor tell the truth about who is working?

The bug they exist to stop came in two halves and both were real.

  1. The UI shipped every desk hardcoded as "working" with a made-up task, so a
     hard refresh lit the whole floor for a moment before the real state landed
     and switched it off. The office looked busiest at the exact moment it knew
     the least.

  2. Nothing recorded that an agent had run. A pipeline claims a task and
     finishes it inside the same second, so "who holds a claimed task right now"
     is almost always nobody — a sector that filed nine tickets two minutes ago
     read as dark as one that had never run in its life.

So: work leaves a trace, a crash is not a clean finish, and a desk that has
genuinely done nothing stays dark. Everything runs against a throwaway cabinet.
"""
import re
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

from . import board, state

_fail = []
ROOT = Path(__file__).parents[1]


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
    con.executescript((ROOT / "coordination" / "schema.sql").read_text())
    con.commit()
    con.close()
    return db


def _room(snapshot, key):
    return next(r for r in snapshot["rooms"] if r["key"] == key)


def test_a_run_is_recorded():
    print("Work leaves a trace:")
    real_b, real_s = board.DB, state.DB
    board.DB = state.DB = _fresh_db()
    try:
        with board.agent_run("it-watcher") as run:
            run.note("checking glassworks.ae")
            live = _room(state.build(), "IT")["agents"]
            check("mid-run the desk is working", [a["s"] for a in live], ["working"])
            check("and it says what it is doing", live[0]["t"], "checking glassworks.ae")
            check("and it names the agent", live[0]["n"], "it-watcher")

        after = _room(state.build(), "IT")["agents"]
        check("after the run the desk shows the work it did", after[0]["s"], "done")
        check("with how long ago", after[0]["t"].endswith("just now"), True)
    finally:
        board.DB, state.DB = real_b, real_s


def test_a_crash_is_not_a_clean_finish():
    print("A crash is not a clean finish:")
    real_b, real_s = board.DB, state.DB
    board.DB = state.DB = _fresh_db()
    try:
        try:
            with board.agent_run("leads-hunter") as run:
                run.note("mapping directories")
                raise RuntimeError("source went down")
        except RuntimeError:
            pass
        rows = board.recent_runs(0)
        check("the run is marked error", [r["status"] for r in rows], ["error"])
        check("and it is closed off, not left hanging", rows[0]["ended_at"] is not None, True)
        desk = _room(state.build(), "Leads")["agents"][0]
        check("the floor shows it blocked, not done", desk["s"], "blocked")
    finally:
        board.DB, state.DB = real_b, real_s


def test_a_desk_that_did_nothing_stays_dark():
    print("A desk that did nothing stays dark:")
    real_b, real_s = board.DB, state.DB
    board.DB = state.DB = _fresh_db()
    try:
        snap = state.build()
        for key in ("Director", "IT", "Content", "SEO", "Leads", "Report", "Security"):
            check(f"{key} reports no activity", _room(snap, key)["agents"], [])

        # an old run must not keep a desk lit for ever
        with board.agent_run("seo-strategist") as run:
            run.note("pulled 40 gaps")
        stale = int(time.time()) - state.RECENT_RUN_SECS - 60
        con = sqlite3.connect(board.DB)
        con.execute("UPDATE agent_runs SET started_at=?, ended_at=?", (stale, stale))
        con.commit()
        con.close()
        check("yesterday's run is not today's activity",
              _room(state.build(), "SEO")["agents"], [])
    finally:
        board.DB, state.DB = real_b, real_s


def test_the_run_wins_over_a_stale_task_row():
    """
    A task left 'in_progress' by a crashed agent is a stall, not work. The run
    is the live picture, and the two must not double up into one desk twice.
    """
    print("One line per desk:")
    real_b, real_s = board.DB, state.DB
    board.DB = state.DB = _fresh_db()
    try:
        tid = board.post_task("IT", "Read-only health baseline", "detail")
        board.claim_task(tid, "it-watcher")
        with board.agent_run("it-watcher", task_id=tid) as run:
            run.note("filed 3 tickets")
        agents = _room(state.build(), "IT")["agents"]
        check("the Watcher appears once", len(agents), 1)
        check("showing the run, not the stale claim", agents[0]["t"].startswith("filed 3"), True)

        # an unclaimed task is still shown — it is work waiting for someone
        board.post_task("IT", "Check the gallery on mobile", "detail")
        agents = _room(state.build(), "IT")["agents"]
        check("plus the unclaimed task, waiting", sorted(a["s"] for a in agents),
              ["done", "waiting"])
    finally:
        board.DB, state.DB = real_b, real_s


def test_each_agent_reaches_its_own_desk():
    print("The right bulb lights:")
    real_b, real_s = board.DB, state.DB
    board.DB = state.DB = _fresh_db()
    try:
        for agent in ("it-watcher", "content-writer", "seo-strategist",
                      "leads-hunter", "security-screener"):
            with board.agent_run(agent) as run:
                run.note("did a thing")
        got = {k: [(a["n"], a["slot"]) for a in _room(state.build(), k)["agents"]]
               for k in ("IT", "Content", "SEO", "Leads", "Security")}
        check("IT -> Watcher", got["IT"], [("it-watcher", "watcher")])
        check("Content -> Writer", got["Content"], [("content-writer", "writer")])
        check("SEO -> Strategist", got["SEO"], [("seo-strategist", "strategist")])
        check("Leads -> Hunter", got["Leads"], [("leads-hunter", "hunter")])
        check("Security -> Screener", got["Security"], [("security-screener", "screener")])
    finally:
        board.DB, state.DB = real_b, real_s


def test_the_ui_does_not_open_on_a_lie():
    """
    The first paint happens before /api/state answers. Whatever the staff list
    says is what the owner sees for that moment, so it may not claim anyone is
    working. This reads the actual file — a comment promising it is not enough.
    """
    print("The first paint is honest:")
    html = (ROOT / "ui" / "office.html").read_text()
    body = html[html.index("function sectors()"):html.index("const SITES=")]
    check("no desk ships as working", 's:"working"' in body, False)
    check("no desk ships as waiting", 's:"waiting"' in body, False)
    desks = re.findall(r'(?<![a-z])s:"(\w+)"', body)   # not cls:"dir"
    check("every desk starts idle", set(desks), {"idle"})
    check("and all sixteen desks are still there", len(desks), 16)

    # the CSS has to give 'done' a lit bulb, or the state is invisible
    check("a finished run lights the bulb", ".emp.done .antenna::after{background:var(--ok)" in html, True)
    check("an idle desk's bulb is dark",
          ".antenna::after{content:\"\";position:absolute;top:-3px;left:-1.5px;width:5px;"
          "height:5px;border-radius:50%;background:var(--faint)}" in html, True)


def test_spend_still_adds_up():
    print("Cost governor still reads the runs:")
    real_b, real_s = board.DB, state.DB
    board.DB = state.DB = _fresh_db()
    try:
        with board.agent_run("seo-strategist") as run:
            run.spent(0.42, tokens_in=1200, tokens_out=300)
        check("today's spend includes the run", state.build()["metrics"]["spend"], "$0.42")
    finally:
        board.DB, state.DB = real_b, real_s


def main():
    test_a_run_is_recorded()
    test_a_crash_is_not_a_clean_finish()
    test_a_desk_that_did_nothing_stays_dark()
    test_the_run_wins_over_a_stale_task_row()
    test_each_agent_reaches_its_own_desk()
    test_the_ui_does_not_open_on_a_lie()
    test_spend_still_adds_up()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: {_fail}")
        sys.exit(1)
    print("all floor tests passed")


if __name__ == "__main__":
    main()
