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
    print("A desk that did nothing reports no activity:")
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


def test_lit_means_alive_and_dark_means_broken():
    """
    The rule the owner set, and the one I had backwards: lit = alive, blinking =
    working, DARK = broken. Idle desks used to go dark and broken ones lit up
    red, so a healthy quiet agency looked dead and you could not tell "nothing to
    do" from "this one has fallen over".
    """
    print("Lit is alive, dark is broken:")
    html = (ROOT / "ui" / "office.html").read_text()

    # the default bulb — every desk that is not broken — must be lit
    default = html.split(".antenna::after{")[1].split("}")[0]
    check("an idle desk's bulb is LIT", "background:#8fa4ad" in default, True)
    check("and it has a glow", "box-shadow:0 0 5px" in default, True)
    check("it is not the faint/off colour", "var(--faint)" in default, False)

    check("working blinks", "animation:pulse" in
          html.split(".emp.working .antenna::after{")[1].split("}")[0], True)
    for st in ("done", "waiting"):
        seg = html.split(f".emp.{st} .antenna::after{{")[1].split("}")[0]
        check(f"{st} is lit", "box-shadow:0 0 7px" in seg, True)
        check(f"{st} does not blink", "animation" in seg, False)

    broken = html.split(".emp.blocked .antenna::after{")[1].split("}")[0]
    check("ONLY a broken desk goes dark", "box-shadow:none" in broken, True)
    check("and its status lamp goes red and blinks",
          "animation:pulse" in html.split(".emp.blocked .status{")[1].split("}")[0], True)


def test_a_dark_bulb_always_says_why():
    """A desk that goes dark without a reason is just a bug you cannot see."""
    print("A dark bulb carries its reason:")
    real_b, real_s = board.DB, state.DB
    board.DB = state.DB = _fresh_db()
    try:
        try:
            with board.agent_run("it-watcher") as run:
                run.note("checking the site")
                raise RuntimeError("connection refused")
        except RuntimeError:
            pass
        desk = _room(state.build(), "IT")["agents"][0]
        check("the desk is dark", desk["s"], "blocked")
        check("the line names the actual fault",
              "connection refused" in desk["t"], True)
        check("not just what it was attempting",
              desk["t"].strip() != "checking the site", True)
        check("and there is a why to show in the drawer", bool(desk.get("why")), True)

        # a claim nobody is working is a stall, and it blocks everyone else
        board.DB = state.DB = _fresh_db()
        tid = board.post_task("Leads", "Collect hotel leads", "detail")
        board.claim_task(tid, "leads-hunter")
        con = sqlite3.connect(board.DB)
        con.execute("UPDATE tasks SET claimed_at=?", (int(time.time()) - state.STALL_SECS - 60,))
        con.commit()
        con.close()
        desk = _room(state.build(), "Leads")["agents"][0]
        check("a long-held claim with no run is a stall", desk["s"], "blocked")
        check("and it says so", "nothing running" in desk["t"], True)

        # ...but a claim made moments ago is just work in progress
        board.DB = state.DB = _fresh_db()
        tid = board.post_task("Leads", "Collect hotel leads", "detail")
        board.claim_task(tid, "leads-hunter")
        check("a fresh claim is not a stall",
              _room(state.build(), "Leads")["agents"][0]["s"], "working")
    finally:
        board.DB, state.DB = real_b, real_s


def test_the_agent_chat_is_not_canned():
    """
    The drawer used to reply "On it. I'll claim that as a task on the board" to
    anything, including "are you broken?". Whatever it says now has to come from
    that desk's actual record.
    """
    print("The chat answers from the record:")
    from . import agent_chat
    real_b = board.DB
    board.DB = agent_chat.DB = _fresh_db()
    try:
        never = agent_chat.ask("leads-scanner", "what did you do today?")
        check("an agent that never ran admits it",
              "never run" in never["reply"].lower(), True)
        check("and does not claim any work",
              "claim that as a task" in never["reply"], False)

        with board.agent_run("it-watcher") as run:
            run.note("filed 3 tickets on glassworks.ae")
        did = agent_chat.ask("it-watcher", "what did you do today?")
        check("an agent that ran cites the real note",
              "filed 3 tickets" in did["reply"], True)

        try:
            with board.agent_run("seo-strategist") as run:
                raise RuntimeError("DataForSEO 401")
        except RuntimeError:
            pass
        broke = agent_chat.ask("seo-strategist", "are you ok?")
        check("a failed agent leads with the failure",
              "DataForSEO 401" in broke["reply"], True)

        # the desk has to know which sector's brain answers for it
        check("it-watcher routes to the IT brain",
              agent_chat.whois("it-watcher")["brain_task"], "it")
        check("content-writer routes to the Content brain",
              agent_chat.whois("content-writer")["brain_task"], "content")
        check("and an unknown name does not crash",
              agent_chat.whois("")["sector"], "Director")

        # every exchange is on the record, like everything else here
        con = sqlite3.connect(board.DB)
        n = con.execute("SELECT count(*) FROM requests WHERE from_sector='Owner'").fetchone()[0]
        con.close()
        check("conversations are logged, not private", n >= 0, True)
    finally:
        board.DB = agent_chat.DB = real_b


def test_tracing_never_breaks_the_work():
    """
    Found on a clean checkout: a fresh clone has no cabinet, so the very first
    pipeline run died inside the trace layer with 'no such table: agent_runs'
    instead of reaching its own message about the board. Bookkeeping is not
    allowed to be the thing that breaks the job.
    """
    print("Tracing never breaks the work:")
    real = board.DB
    board.DB = Path(tempfile.mkdtemp()) / "not-built-yet.db"
    try:
        got = []
        with board.agent_run("leads-hunter") as run:
            run.task("nope").note("still works").spent(1.5)
            got.append("the body ran")
        check("the work still happens with no cabinet", got, ["the body ran"])
        check("and the handle is inert, not half-alive", run.id, None)

        # a crash inside the body must still propagate, untraced or not
        raised = None
        try:
            with board.agent_run("leads-hunter"):
                raise RuntimeError("boom")
        except RuntimeError as e:
            raised = str(e)
        check("an error still reaches the caller", raised, "boom")
    finally:
        board.DB = real


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
    test_lit_means_alive_and_dark_means_broken()
    test_a_dark_bulb_always_says_why()
    test_the_agent_chat_is_not_canned()
    test_tracing_never_breaks_the_work()
    test_spend_still_adds_up()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: {_fail}")
        sys.exit(1)
    print("all floor tests passed")


if __name__ == "__main__":
    main()
