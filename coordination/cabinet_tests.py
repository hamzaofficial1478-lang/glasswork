"""
cabinet_tests.py — run: python3 -m coordination.cabinet_tests

Covers the promises the shelf makes: a borrow needs a reason, only one agent
holds a shelf at a time (others are refused/queued), external data and
over-ceiling spend are flagged for escalation, and the live board reports the
holder, the queue and the day's spend. Runs against a throwaway DB so it never
touches the real filing cabinet.
"""
import re
import sqlite3
import tempfile
from pathlib import Path

from . import board, cabinet

ROOT = Path(__file__).parents[1]

_fail = []


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


def _fresh_db():
    tmp = Path(tempfile.mkdtemp()) / "test_agency.db"
    con = sqlite3.connect(tmp)
    con.executescript((Path(__file__).parent / "schema.sql").read_text(encoding="utf-8"))
    con.commit()
    con.close()
    # point both modules at the throwaway DB (they read board.DB at call time)
    board.DB = tmp
    return tmp


def test_classify():
    print("Approval policy:")
    check("internal, no cost -> auto", cabinet.classify("internal", 0, 0), "auto")
    check("external -> escalate", cabinet.classify("external", 0, 0), "escalate")
    check("under ceiling -> auto", cabinet.classify("internal", 1.0, 5.0), "auto")
    check("over ceiling -> escalate", cabinet.classify("internal", 9.0, 5.0), "escalate")


def test_borrow_rules():
    print("Borrow rules:")
    _fresh_db()
    check("no reason -> refused", cabinet.borrow("email", "x", reason="")["ok"], False)
    a = cabinet.borrow("email", "leads-responder", "Leads", reason="reply to enquiry")
    check("first borrow ok", a["ok"], True)
    b = cabinet.borrow("email", "leads-hunter", "Leads", reason="cold intro", wait=False)
    check("second on same shelf is busy", b["ok"], False)
    # a DIFFERENT shelf is available in parallel
    c = cabinet.borrow("stock_images", "content-writer", "Content",
                       reason="header", wait=False)
    check("different shelf available in parallel", c["ok"], True)
    cabinet.release("email", "leads-responder", borrow_id=a["borrow_id"], usd_cost=0.0)
    d = cabinet.borrow("email", "leads-hunter", "Leads", reason="cold intro", wait=False)
    check("shelf reusable after release", d["ok"], True)
    cabinet.release("email", "leads-hunter", borrow_id=d["borrow_id"])
    cabinet.release("stock_images", "content-writer", borrow_id=c["borrow_id"])


def test_live_state():
    print("Live board:")
    _fresh_db()
    a = cabinet.borrow("seo_data", "seo-strategist", "SEO",
                       reason="keyword gaps", data_class="external")
    check("external borrow flagged escalate", a["approval"], "escalate")
    cabinet.borrow("seo_data", "leads-hunter", "Leads",
                   reason="directory scrape", wait=False)  # -> waiting/cancelled
    st = cabinet.shelf_state(["seo_data"])["seo_data"]
    check("board shows the holder", st["holder"]["agent"], "seo-strategist")
    check("recent shows escalate", st["recent"][0]["approval"], "escalate")
    cabinet.release("seo_data", "seo-strategist", borrow_id=a["borrow_id"], usd_cost=2.5)
    st2 = cabinet.shelf_state(["seo_data"])["seo_data"]
    check("holder cleared after release", st2["holder"], None)
    check("today spend recorded", st2["today_spend"], 2.5)


def test_the_daily_spend_cap_actually_bites():
    """
    "No money is spent above the daily cost cap without owner approval" is a
    red line. DAILY_SPEND_CAP_USD was written to .env by the setup screen and
    read by nothing at all, and classify()'s cost_ceiling is a per-borrow limit
    that defaults to 0.0 — which disables it. A thousand small borrows could
    run all day and trip nothing.
    """
    print("The daily spend cap:")
    import os
    real_db = board.DB
    board.DB = _fresh_db()
    keep = os.environ.get("DAILY_SPEND_CAP_USD")
    os.environ["DAILY_SPEND_CAP_USD"] = "2.00"
    try:
        check("the owner's cap is read", cabinet.daily_spend_cap(), 2.0)
        check("nothing spent yet", cabinet.spent_today(), 0.0)

        b = cabinet.borrow("seo_data", "a", "SEO", reason="pull gaps")
        check("a borrow under the cap is auto-granted", b["approval"], "auto")
        cabinet.release("seo_data", "a", borrow_id=b["borrow_id"], usd_cost=1.50)
        check("spend is counted from the borrow trail", cabinet.spent_today(), 1.5)

        b2 = cabinet.borrow("seo_data", "a", "SEO", reason="again", est_cost=1.00)
        check("one that would cross the line escalates first", b2["approval"], "escalate")
        cabinet.release("seo_data", "a", borrow_id=b2["borrow_id"], usd_cost=1.00)

        b3 = cabinet.borrow("seo_data", "a", "SEO", reason="third")
        check("past the cap, the shelf is refused", b3["ok"], False)
        check("and it says so plainly", b3.get("capped"), True)
        check("the shelf was never even locked",
              board.acquire_lock("shelf:seo_data", "someone", wait=False), True)
        board.release_lock("shelf:seo_data", "someone")

        # a cap of zero or junk must not read as "unlimited"
        os.environ["DAILY_SPEND_CAP_USD"] = "0"
        check("a zero cap falls back, never to infinity",
              cabinet.daily_spend_cap(), cabinet.DEFAULT_DAILY_SPEND_CAP)
        os.environ["DAILY_SPEND_CAP_USD"] = "not-a-number"
        check("junk falls back too", cabinet.daily_spend_cap(),
              cabinet.DEFAULT_DAILY_SPEND_CAP)
    finally:
        if keep is None:
            os.environ.pop("DAILY_SPEND_CAP_USD", None)
        else:
            os.environ["DAILY_SPEND_CAP_USD"] = keep
        board.DB = real_db


def test_only_shelves_something_borrows():
    """
    Twelve shelves, five of which no code ever opened. Each showed "no APIs on
    this shelf yet", which reads as a job left undone, so the owner went looking
    for keys to fill shelves that would never be used.

    The rule: a shelf exists only if a pipeline borrows it. This test greps for
    the borrow, so adding a decorative shelf back fails here.
    """
    print("Every shelf is one something actually borrows:")
    from providers import store
    # providers/ counts too. The rule is "something borrows it", not "a
    # pipeline does".
    src = ""
    for folder in ("pipelines", "providers", "coordination"):
        for f in (ROOT / folder).glob("*.py"):
            if not f.name.endswith("_tests.py"):
                src += f.read_text(encoding="utf-8")
    for shelf in store.SHELF_KINDS:
        if shelf == "other":
            continue          # the landing place for a tool saved with no shelf
        check(f"something borrows '{shelf}'", f'"{shelf}"' in src, True)

    gone = ("vision", "speech", "trends", "embedding", "image")
    for dead in gone:
        check(f"'{dead}' is not offered as a shelf any more",
              dead in store.SHELF_KINDS, False)
    check("and there is no spare-brain shelf any more — the brains are the Claude team",
          "llm_extra" in store.SHELF_KINDS, False)


def test_brains_are_not_a_shelf():
    """
    The owner asked whether agents would end up using the MD's model. They
    cannot: every sector thinks with its own Claude model, chosen on the Claude
    team table, and a model is never put on a shelf where an agent could take
    a lock on it. The board says so where he reads it.
    """
    print("A brain is the Claude team's, never lent:")
    from providers import store, api
    check("there is no llm shelf", "llm" in store.SHELF_KINDS, False)
    out = api.cmd_shelves(None)
    check("every shelf on the board is a tool shelf",
          sorted({s["kind"] for s in out["shelves"]}), ["tool"])
    check("no model categories are injected",
          [n for n in ("llm", "llm_extra", "image", "embedding")
           if n in {s["shelf"] for s in out["shelves"]}], [])
    html = (ROOT / "ui" / "setup.html").read_text(encoding="utf-8")
    check("and the board says so where he reads it",
          "never shared" in html, True)


def test_a_sectors_brain_is_never_on_a_shelf():
    """
    The owner's point, and he was right: a shelf is a single-holder lock, so an
    agent that borrowed the MD's model would BLOCK the MD from its own brain.

    With the Claude team there is nothing to borrow: a model is not a seat, so
    MD and IT can both use Opus 4.8 at the same moment, and the old
    spare-brain shelf, if anything still named it, would hold nothing at all.
    """
    print("A brain cannot be borrowed by anyone:")
    from providers import routing, store
    with store.isolated():
        check("the catalogue holds tools only",
              all(i.get("kind") == "tool" for items in store.shelf_catalogue().values()
                  for i in items), True)
    check("there is no spare-brain door left to knock on",
          hasattr(routing, "call_spare"), False)
    with store.isolated():
        check("the old spare-brain shelf has nothing on it to hand out",
              store.resolve_shelf("llm_extra")["ok"], False)


def test_an_abandoned_waiter_is_reaped():
    """
    A queue row is cancelled by the waiter itself on timeout — which needs that
    process to still be alive. Kill a run step and the row sits 'waiting' for
    ever; the board then showed somebody queued for four days for a shelf
    nobody was holding, which reads as a deadlock and is really a corpse.
    """
    print("A waiter whose process died does not queue for ever:")
    real = board.DB
    board.DB = _fresh_db()
    try:
        import sqlite3 as _sq
        now = cabinet._now()
        con = _sq.connect(board.DB)
        con.execute("INSERT INTO shelf_queue(shelf,agent,sector,reason,data_class,"
                    "status,enqueued_at) VALUES('seo_data','ghost','SEO','x',"
                    "'internal','waiting',?)", (now - 4 * 86400,))
        con.execute("INSERT INTO shelf_queue(shelf,agent,sector,reason,data_class,"
                    "status,enqueued_at) VALUES('seo_data','live','SEO','x',"
                    "'internal','waiting',?)", (now - 5,))
        con.commit(); con.close()

        st = cabinet.shelf_state(["seo_data"])
        waiting = [q["agent"] for q in st["seo_data"]["queue"]]
        check("the four-day-old waiter is gone", "ghost" in waiting, False)
        check("the one that started five seconds ago is left alone",
              "live" in waiting, True)
    finally:
        board.DB = real


def test_the_office_does_not_wait_for_a_run():
    """
    "no matter in which section i am doing anything the work in progress
     should not get stop"

    The office reads this board on every refresh, and the read used to write
    (the reaper above) — so while a day run held the pen, every refresh sat on
    "Loading…" until the run let go. A busy cabinet must cost the office a
    moment, not the run's whole write. The corpse is still hidden, and still
    reaped the next time the cabinet is free.
    """
    print("\nThe office's read does not queue behind a run's write:")
    import sqlite3 as _sq
    import threading
    import time as _t
    real = board.DB
    board.DB = _fresh_db()
    try:
        now = cabinet._now()
        con = _sq.connect(board.DB)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("INSERT INTO shelf_queue(shelf,agent,sector,reason,data_class,"
                    "status,enqueued_at) VALUES('seo_data','ghost','SEO','x',"
                    "'internal','waiting',?)", (now - 4 * 86400,))
        con.commit(); con.close()

        held, let_go = threading.Event(), threading.Event()

        def a_run_writing():
            c = _sq.connect(board.DB, isolation_level=None, timeout=30)
            c.execute("BEGIN IMMEDIATE")
            c.execute("UPDATE shelf_queue SET reason='busy' WHERE agent='ghost'")
            held.set()
            let_go.wait(20)
            c.execute("COMMIT"); c.close()

        t = threading.Thread(target=a_run_writing, daemon=True)
        t.start()
        held.wait(5)
        t0 = _t.time()
        st = cabinet.shelf_state(["seo_data"])
        took = _t.time() - t0
        let_go.set(); t.join(10)
        check("it answered while the run held the pen", took < 3, True)
        check("the dead waiter is still not shown",
              [q["agent"] for q in st["seo_data"]["queue"]], [])
        cabinet.shelf_state(["seo_data"])
        c = _sq.connect(board.DB)
        left = c.execute("SELECT status FROM shelf_queue WHERE agent='ghost'").fetchone()[0]
        c.close()
        check("and it is put away once the cabinet is free", left, "abandoned")
    finally:
        board.DB = real

    # The other half of the freeze was the office server itself: every Python
    # call was spawnSync, which stops Node dead until it returns, so one slow
    # answer (a check, a mail send, a busy cabinet) held every screen — the
    # Mail room link included — on "Loading…". Only two may stay: the one-off
    # Python version probe, and the cabinet check before the server serves.
    print("\n...and the office server never stops to wait for Python:")
    src = (Path(__file__).parents[1] / "server.js").read_text(encoding="utf-8")
    inside = []
    for m in re.finditer(r"\bspawnSync\(", src):
        fn = re.findall(r"function\s+(\w+)\s*\(", src[:m.start()])
        inside.append(fn[-1] if fn else "?")
    check("spawnSync only in the version probe and the boot-time check",
          sorted(set(inside)), ["ensureDbSync", "resolvePython"])
    # "clicked on try again ... the message appear unknow action": a pulled
    # update, a refreshed screen, and the server still running the old code.
    print("\n...and an update to the server loads itself:")
    check("the server exits with 75 when its own file changes, if run.py started it",
          "OFFICE_SUPERVISED" in src and "RESTART_CODE = 75" in src
          and "process.exit(RESTART_CODE)" in src, True)
    check("and no longer turns away a Mail room action it has not heard of",
          "REVIEW_ACTS" not in src, True)
    import run as _run
    launched, codes = [], [75, 0]

    class _Proc:
        def wait(self):
            return codes.pop(0)
    real = (_run.subprocess.Popen, _run.webbrowser.open, _run.time.sleep, _run.say,
            _run.choose_port, _run.wait_for_office)
    _run.subprocess.Popen = lambda cmd, **kw: launched.append(kw.get("env", {}).get(
        "OFFICE_SUPERVISED")) or _Proc()
    _run.webbrowser.open = lambda url: None
    _run.time.sleep = lambda s: None
    _run.say = lambda *a, **k: None
    # Which port, and whether the office answered, are coordination/launch_tests'
    # business; this is about starting it supervised and starting it again.
    _run.choose_port = lambda preferred: (int(preferred), "")
    _run.wait_for_office = lambda proc, port, seconds=45: True
    try:
        _run.office()
    finally:
        (_run.subprocess.Popen, _run.webbrowser.open, _run.time.sleep, _run.say,
         _run.choose_port, _run.wait_for_office) = real
    check("run.py starts it supervised, and starts it again when it asks",
          launched, ["1", "1"])

    listen = src.find("app.listen(")
    calls = [m.start() for m in re.finditer(r"(?<!function )\bensureDbSync\(\)", src)]
    check("and the boot-time check runs only at boot",
          bool(calls) and all(at > listen for at in calls), True)


# ---------------------------------------------------------------------------
# WINDOWS
# ---------------------------------------------------------------------------

def test_no_file_is_read_at_the_mercy_of_the_machines_codepage():
    """
    The owner ran the suite on Windows and five suites failed that pass on every
    Linux box. Nothing was wrong with the code they tested. Python on Windows
    reads text files and writes stdout in the machine's ANSI codepage — cp1252,
    or cp1256 in the UAE — not UTF-8. Every file in this repository is UTF-8,
    and the program prints readiness lamps (● ○ ◐ ✕) that cp1252 has no
    characters for, so `print()` raised before a single assertion ran.

    This walks the source rather than trusting anybody to remember. A new
    read_text() without an encoding is the exact shape of the bug coming back,
    and it would come back silently: it works perfectly on the machine of
    whoever writes it.
    """
    print("\nNo file is read at the mercy of the machine's codepage:")
    root = Path(__file__).parents[1]
    skip = {"__pycache__", ".git", "node_modules", ".venv"}
    bare_read, bare_write, bare_open, bare_sub = [], [], [], []
    for py in sorted(root.rglob("*.py")):
        if any(part in skip for part in py.parts):
            continue
        src = py.read_text(encoding="utf-8")
        rel = str(py.relative_to(root))
        for m in re.finditer(r"\.read_text\(([^)]*)\)", src):
            if "encoding" not in m.group(1):
                bare_read.append(rel)
        for m in re.finditer(r"\.write_text\(", src):
            line = src[m.start():src.find("\n", m.start())]
            if "encoding" not in line and not line.rstrip().endswith(","):
                bare_write.append(rel)
        for m in re.finditer(r"text=True", src):
            line = src[max(0, m.start() - 200):m.start() + 200]
            if "encoding" not in line:
                bare_sub.append(rel)
    check("every read_text names its encoding", sorted(set(bare_read)), [])
    check("so does every write_text", sorted(set(bare_write)), [])
    check("and every subprocess that captures text",
          sorted(set(bare_sub)), [])

    print("\n...and the console is set up by importing the package, not by ritual:")
    from . import console
    check("coordination sets it up", "console" in
          (root / "coordination" / "__init__.py").read_text(encoding="utf-8"), True)
    check("so does pipelines", "console" in
          (root / "pipelines" / "__init__.py").read_text(encoding="utf-8"), True)
    check("so does providers", "console" in
          (root / "providers" / "__init__.py").read_text(encoding="utf-8"), True)
    check("and calling it twice is harmless", (console.setup(), console.setup()),
          (None, None))


def test_the_test_run_never_writes_in_his_cabinet():
    """
    "'carpet cleaning' is not a service this business sells" arrived in his
    Activity at 18:16 — a test the Releaser ran before a release, writing into
    his real cabinet. The whole run gets a throwaway cabinet of its own now.
    """
    print("\nThe test run has a cabinet of its own:")
    import os
    import subprocess
    import sys
    root = Path(__file__).parents[1]
    src = (root / "run.py").read_text(encoding="utf-8")
    body = src.split("def tests():", 1)[1].split("\ndef ", 1)[0]
    check("run.py --tests points every suite at a throwaway cabinet",
          'os.environ["AGENCY_DB"]' in body and "mkdtemp" in body, True)
    # Compared as paths, not text: Windows writes the same path with
    # backslashes, and its temp folder can have a space in it ("C:\\Users\\Your
    # Name\\..."), which is how this check first went red on his PC only.
    import tempfile
    target = Path(tempfile.gettempdir()) / "not-his.db"
    code = ("import coordination.board as b, coordination.agent_chat as a,"
            " coordination.targets as t, coordination.briefing as r;"
            " print('|'.join(str(x) for x in (b.DB, a.DB, t.DB, r.DB)))")
    out = subprocess.run([sys.executable, "-c", code], cwd=str(root), capture_output=True,
                         text=True, encoding="utf-8",
                         env={**os.environ, "AGENCY_DB": str(target)}).stdout.strip().split("|")
    check("and the code that opens the cabinet follows it",
          [Path(x) == target for x in out], [True] * 4)


def main():
    test_the_test_run_never_writes_in_his_cabinet()
    test_no_file_is_read_at_the_mercy_of_the_machines_codepage()
    test_classify()
    test_borrow_rules()
    test_live_state()
    test_the_daily_spend_cap_actually_bites()
    test_only_shelves_something_borrows()
    test_brains_are_not_a_shelf()
    test_a_sectors_brain_is_never_on_a_shelf()
    test_an_abandoned_waiter_is_reaped()
    test_the_office_does_not_wait_for_a_run()
    print("\n" + ("all cabinet tests passed" if not _fail else f"{len(_fail)} FAILED: {_fail}"))
    return 1 if _fail else 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
