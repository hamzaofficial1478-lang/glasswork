"""
run_tests.py — run: python -m pipelines.run_tests   (python3 on macOS/Linux)

Covers the three pieces the Run button stands on:

  coordination/readiness.py   can each sector work RIGHT NOW, and does the
                              answer distinguish 'proven' from 'saved'
  coordination/activity.py    is the timeline honestly derived from the rows
                              the work itself wrote
  pipelines/day_run.py        does the check refuse a broken day, does one
                              press mean one day, and does a step that sat out
                              read as having sat out rather than as a success

Everything runs against a throwaway cabinet and a throwaway provider store. No
network, no real API call, and nothing touches the owner's real config.
"""
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

from coordination import activity, board, readiness
from providers import store

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
    # One place says where the cabinet is. Every reader goes through board.DB,
    # so redirecting it here redirects all of them — that is the whole point of
    # having one definition, and it is what these tests are standing on.
    board.DB = db
    board.ensure_schema()
    return db


def _now():
    return int(time.time())


# ---------------------------------------------------------------------------
# READINESS
# ---------------------------------------------------------------------------

def _readiness_tests():
    print("\nConfigured is not working:")

    saved = {"n": 1, "verified": 0, "tested": 0, "names": ["X · m"]}
    proven = {"n": 2, "verified": 2, "tested": 2, "names": ["X · m", "Y · n"]}
    lone = {"n": 1, "verified": 1, "tested": 1, "names": ["X · m"]}

    real = readiness._brains
    try:
        readiness._brains = lambda task: saved
        r = readiness.sector("report", "report", env={"NTFY_TOPIC": "t"}, shelf={})
        check("saved but never called -> unchecked, not ready", r["state"], "unchecked")

        readiness._brains = lambda task: proven
        r = readiness.sector("report", "report", env={"NTFY_TOPIC": "t"}, shelf={})
        check("two proven brains -> ready", r["state"], "ready")

        readiness._brains = lambda task: lone
        r = readiness.sector("report", "report", env={"NTFY_TOPIC": "t"}, shelf={})
        check("one proven brain, no fallback -> thin", r["state"], "thin")

        # MD and Security are the two that put hard calls to two models
        r = readiness.sector("md", "md", env={}, shelf={})
        check("MD on one brain -> thin (the cross-check stops silently)", r["state"], "thin")
        check("and it says why", any("cross-check" in t for t in r["thin"]), True)
        r = readiness.sector("security", "security", env={}, shelf={})
        check("Security on one brain -> thin", r["state"], "thin")

        readiness._brains = lambda task: {"n": 0, "verified": 0, "names": [],
                                          "error": "no routing"}
        r = readiness.sector("seo", "seo", env={},
                             shelf={"seo_data": {"items": 2, "working": 2},
                                    "crawler": {"items": 1, "working": 1}})
        check("no brain -> blocked whatever the shelf says", r["state"], "blocked")

        print("\nA required shelf outranks everything else:")
        readiness._brains = lambda task: proven
        r = readiness.sector("leads", "leads",
                             env={"SMTP_HOST": "h", "OUTREACH_DOMAIN": "d"},
                             shelf={"maps": {"items": 0, "working": 0},
                                    "email": {"items": 2, "working": 2}})
        check("empty maps shelf blocks Leads", r["state"], "blocked")
        check("and names the shelf", any("maps" in b for b in r["blocking"]), True)

        r = readiness.sector("content", "content", env={},
                             shelf={"stock_images": {"items": 0, "working": 0},
                                    "image_gen": {"items": 0, "working": 0}})
        check("an OPTIONAL shelf missing does not block Content", r["state"], "ready")

        print("\nA missing key is a blocker, not a warning:")
        r = readiness.sector("it", "it", env={}, shelf={})
        check("no SITE_URL -> IT blocked", r["state"], "blocked")
        r = readiness.sector("it", "it", env={"SITE_URL": "https://x.ae"}, shelf={})
        check("with SITE_URL -> IT ready", r["state"], "ready")

        print("\nThe worst state wins, never the average:")
        check("blocked beats ready", readiness._worst("ready", "blocked"), "blocked")
        check("thin beats unchecked", readiness._worst("unchecked", "thin"), "thin")
        check("unchecked beats ready", readiness._worst("ready", "unchecked"), "unchecked")
    finally:
        readiness._brains = real

    print("\nThe remembered check:")
    check("no check yet -> empty, not a fake pass", readiness.last_check(), {})
    readiness.record_check({"ready": False, "blocking": ["seo has no brain"]})
    lc = readiness.last_check()
    check("the check is remembered", lc["ready"], False)
    check("with the reason", lc["blocking"], ["seo has no brain"])


# ---------------------------------------------------------------------------
# ACTIVITY
# ---------------------------------------------------------------------------

def _activity_tests():
    now = _now()
    con = sqlite3.connect(board.DB)
    con.execute("INSERT INTO tasks(id,sector,title,risk,status,owner,created_by,"
                "created_at,claimed_at,finished_at) VALUES"
                "('t1','Leads','Collect Al Quoz firms','green','done','leads-hunter','MD',?,?,?)",
                (now - 300, now - 280, now - 200))
    con.execute("INSERT INTO agent_runs(agent,started_at,ended_at,status,note,usd_cost)"
                " VALUES('leads-hunter',?,?,'ok','added 34 leads',0.2)", (now - 280, now - 200))
    con.execute("INSERT INTO agent_runs(agent,started_at,ended_at,status,note)"
                " VALUES('content-writer',?,?,'error','image_gen returned 402')",
                (now - 150, now - 149))
    con.execute("INSERT INTO borrows(shelf,item,agent,sector,reason,status,granted_at)"
                " VALUES('maps','Places','leads-hunter','Leads','Al Quoz','active',?)",
                (now - 280,))
    con.execute("INSERT INTO security_events(kind,severity,source,detail,action,at)"
                " VALUES('injection','high','scraped page','ignore previous','refused',?)",
                (now - 100,))
    # 90 leads in one pass: the noise case
    for i in range(90):
        con.execute("INSERT INTO leads(company,email,email_valid,found_at)"
                    " VALUES(?,?,1,?)", (f"co{i}", f"a{i}@x.ae", now - 250))
    con.commit()
    con.close()

    ev = activity.since(now - 3600, limit=200)
    verbs = {(e["who"], e["verb"]) for e in ev}

    print("\nThe five verbs come off real rows:")
    check("a finished run is delivered", ("leads-hunter", "delivered") in verbs, True)
    check("a claimed task is grabbed", ("leads-hunter", "grabbed") in verbs, True)
    check("the MD posting a task is pushed", ("MD", "pushed") in verbs, True)
    check("a failed run is errored", ("content-writer", "errored") in verbs, True)

    print("\nSecurity catching a scam is Security WORKING:")
    sec = [e for e in ev if e["who"] == "security-screener"]
    check("a refused injection is 'got', not 'errored'",
          {e["verb"] for e in sec}, {"got"})
    check("but it still reads as bad", {e["level"] for e in sec}, {"bad"})

    print("\nNinety leads are one line, not ninety:")
    check("no per-lead rows on the timeline",
          sum(1 for e in ev if "co50" in e["what"]), 0)
    check("the run's own count carries it instead",
          any("added 34 leads" in e["what"] for e in ev), True)

    print("\nA stalled run is not a working one:")
    con = sqlite3.connect(board.DB)
    con.execute("INSERT INTO agent_runs(agent,started_at,status,note)"
                " VALUES('seo-strategist',?,'running','reading ranks')", (now - 4000,))
    con.commit(); con.close()
    ev = activity.since(now - 9000, limit=200)
    st = [e for e in ev if e["who"] == "seo-strategist"]
    check("started 66 minutes ago and never came back -> errored",
          st[0]["verb"], "errored")
    check("and says so", "stalled" in st[0]["what"], True)

    print("\nA cabinet with none of these tables degrades, never crashes:")
    # An owner who built his cabinet before half these tables existed must get
    # an empty timeline, not a stack trace on the office floor.
    was = board.DB
    board.DB = Path(tempfile.mkdtemp()) / "bare.db"
    sqlite3.connect(board.DB).close()
    try:
        check("empty cabinet -> empty timeline", activity.since(now - 3600), [])
        check("and the feed still has a head",
              activity.feed()["head"], "nothing has run yet")
    finally:
        board.DB = was


# ---------------------------------------------------------------------------
# THE RUN ITSELF
# ---------------------------------------------------------------------------

def _day_run_tests():
    from . import day_run

    print("\nA step that sat out is not a step that succeeded:")
    check("exit 0 and ok:false -> idle",
          day_run._verdict('{"ok": false, "error": "no task"}', 0), "idle")
    check("exit 0 and ok:true -> ok", day_run._verdict('{"ok": true, "posted": 3}', 0), "ok")
    check("non-zero exit -> failed", day_run._verdict("boom", 1), "failed")
    check("prose output -> ok", day_run._verdict("MD CYCLE — posted 1 task", 0), "ok")

    print("\nThe one-line summary reads the answer, not the closing brace:")
    check("a JSON object is read, not tailed",
          day_run._summarise('{\n "ok": true,\n "gaps_found": 69\n}', 0),
          "gaps found 69")
    check("ok:false leads with the reason",
          day_run._summarise('{"ok": false, "error": "No open IT task"}', 0),
          "No open IT task")
    check("prose leads with its headline, not its last line",
          day_run._summarise("MD CYCLE — posted 1\n\nPOSTED\n  + a thing", 0),
          "MD CYCLE — posted 1")
    check("a traceback shows the exception, not the header",
          day_run._summarise("Traceback (most recent call last):\n  File x\nKeyError: 'seo'", 1),
          "KeyError: 'seo'")
    check("no output at all still says something",
          day_run._summarise("", 3), "no output (exit 3)")

    print("\nThe same complaint from two checks is one problem:")
    check("deduplicated, order kept",
          day_run._once(["b has no brain", "B HAS NO BRAIN", "a is empty"]),
          ["b has no brain", "a is empty"])

    print("\nOne press means one day:")
    check("the lock is free to start with",
          board.acquire_lock(day_run.LOCK, "someone-else", wait=False, ttl=60), True)
    r = day_run.start(force=True, skip_check=True)
    check("a second run is refused while the first holds the lock", r.get("busy"), True)
    check("and it says why in plain words", "already running" in r["why"], True)
    board.release_lock(day_run.LOCK, "someone-else")

    print("\nA broken day does not start itself:")
    real = day_run.preflight
    day_run.preflight = lambda quick=False: {
        "ready": False, "blocking": ["Leads: the email shelf is empty"], "warnings": []}
    try:
        r = day_run.start()
        check("refused", r.get("refused"), True)
        check("nothing ran", r.get("ran"), False)
        check("the reason is the owner's, not a stack trace",
              "email shelf is empty" in r["why"], True)

        print("\n...but the owner is the tie-breaker:")
        real_steps, day_run.STEPS = day_run.STEPS, []
        try:
            r = day_run.start(force=True)
            check("--force runs it anyway", r.get("ran"), True)
            con = sqlite3.connect(board.DB)
            forced = con.execute("SELECT forced FROM day_runs WHERE id=?",
                                 (r["run_id"],)).fetchone()[0]
            con.close()
            check("and it is recorded as forced, in writing", forced, 1)
        finally:
            day_run.STEPS = real_steps
    finally:
        day_run.preflight = real

    print("\nThe owner's orders reach the board BEFORE the MD plans:")
    from coordination import proposals
    seen = {}
    real_set, real_pre = proposals.set_orders, day_run.preflight
    real_steps, day_run.STEPS = day_run.STEPS, []   # no subprocesses here
    day_run.preflight = lambda quick=False: {"ready": True, "blocking": [], "warnings": []}
    proposals.set_orders = lambda sec, text, day=None: seen.setdefault(sec, text)
    try:
        day_run.start(orders={"Leads": "fitout only", "SEO": "  "})
        check("a real order is written", seen.get("Leads"), "fitout only")
        check("an empty box is not written as an order", "SEO" in seen, False)
    finally:
        proposals.set_orders, day_run.preflight = real_set, real_pre
        day_run.STEPS = real_steps


def main():
    real_db, real_store = board.DB, store.STORE
    _fresh_db()
    store.STORE = Path(tempfile.mkdtemp()) / "providers.local.json"
    try:
        _readiness_tests()
        _activity_tests()
        _day_run_tests()
    finally:
        board.DB, store.STORE = real_db, real_store

    if _fail:
        print(f"\n{len(_fail)} FAILED: {_fail}")
        sys.exit(1)
    print("\nall run tests passed")


if __name__ == "__main__":
    main()
