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
    con.executescript((ROOT / "coordination" / "schema.sql").read_text(encoding="utf-8"))
    con.commit()
    con.close()
    return db


def _room(snapshot, key):
    return next(r for r in snapshot["rooms"] if r["key"] == key)


def test_a_run_is_recorded():
    print("Work leaves a trace:")
    real_b = board.DB
    board.DB = _fresh_db()
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
        board.DB = real_b


def test_a_crash_is_not_a_clean_finish():
    print("A crash is not a clean finish:")
    real_b = board.DB
    board.DB = _fresh_db()
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
        board.DB = real_b


def test_a_desk_that_did_nothing_stays_dark():
    print("A desk that did nothing reports no activity:")
    real_b = board.DB
    board.DB = _fresh_db()
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
        board.DB = real_b


def test_the_run_wins_over_a_stale_task_row():
    """
    A task left 'in_progress' by a crashed agent is a stall, not work. The run
    is the live picture, and the two must not double up into one desk twice.
    """
    print("One line per desk:")
    real_b = board.DB
    board.DB = _fresh_db()
    try:
        tid = board.post_task("IT", "Read-only health baseline", "detail")
        board.claim_task(tid, "it-watcher")
        with board.agent_run("it-watcher", task_id=tid) as run:
            run.note("filed 3 tickets")
        agents = _room(state.build(), "IT")["agents"]
        check("the Watcher appears once", len(agents), 1)
        check("showing the run, not the stale claim", agents[0]["t"].startswith("filed 3"), True)

        # An unclaimed task is still shown — it is work waiting for someone.
        # 'queued', not 'waiting': nobody has picked it up, and that is a
        # different statement from "this needs the owner's permission", which
        # is what amber means on the floor.
        board.post_task("IT", "Check the gallery on mobile", "detail")
        agents = _room(state.build(), "IT")["agents"]
        check("plus the unclaimed task, queued", sorted(a["s"] for a in agents),
              ["done", "queued"])

        # And the one that genuinely needs him IS amber, so the colour keeps
        # meaning exactly one thing.
        board.post_task("IT", "Buy the paid rank tracker", "detail", risk="amber")
        states = sorted(a["s"] for a in _room(state.build(), "IT")["agents"])
        check("an amber task is the only thing that turns a desk amber",
              states, ["done", "queued", "waiting"])
        amber = [a for a in _room(state.build(), "IT")["agents"] if a["s"] == "waiting"]
        check("and it says what the owner has to do",
              "yes or no" in (amber[0].get("why") or ""), True)
    finally:
        board.DB = real_b


def test_each_agent_reaches_its_own_desk():
    print("The right bulb lights:")
    real_b = board.DB
    board.DB = _fresh_db()
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
        board.DB = real_b


def test_every_desk_fits_in_its_room():
    """
    Rooms are absolutely positioned at fixed sizes, and .desks clips whatever
    overflows. Adding the Answers desk to SEO pushed a fifth agent onto a third
    row that fell outside the room wall, so the desk rendered with no name at
    all — visible only by looking at the screen, which is exactly how it was
    found. This makes the arithmetic a test instead.

    A desk is 72px wide with a 4px gap inside 8px padding either side, so a
    room fits floor((w-16)/76) per row; a row is 89px tall under a 24px plaque.
    """
    print("Every room is big enough for the agents in it:")
    html = (ROOT / "ui" / "office.html").read_text(encoding="utf-8")
    src = html[html.index("const LAYOUT="):][:800]
    layout = {k: (int(w), int(h)) for k, w, h in
              re.findall(r"(\w+):\{x:\d+,y:\d+,w:(\d+),h:(\d+)\}", src)}
    rooms = re.findall(r'\{key:"(\w+)",name:"[^"]*",cls:"[^"]*",agents:\[(.*?)\]\}', html)
    check("every room in the roster has a box", len(rooms) > 0 and
          all(k in layout for k, _ in rooms), True)
    for key, blob in rooms:
        n = len(re.findall(r'\{id:"', blob))
        w, h = layout[key]
        per_row = max(1, (w - 16) // 76)
        rows = -(-n // per_row)                       # ceiling division
        needed = 24 + rows * 89
        check(f"{key}: {n} desk(s) at {per_row}/row need {needed}px of {h}px",
              needed <= h, True)


def test_a_room_label_and_its_chip_do_not_collide():
    """
    The readiness chip sits beside the room plaque and hides its word when the
    room is too narrow. The width test for that was about 38px too generous, so
    "not proven" printed straight through "CONTENT & SOCIAL". Pinned here with
    the real numbers rather than trusting the constant.
    """
    print("A room's chip never prints over its name:")
    html = (ROOT / "ui" / "office.html").read_text(encoding="utf-8")
    m = re.search(r"const fits = width >= String\(roomName\|\|''\)\.length\*([\d.]+)"
                  r" \+ word\.length\*([\d.]+) \+ (\d+);", html)
    check("the fit rule is still where the test can read it", bool(m), True)
    if not m:
        return
    name_px, word_px, pad = float(m.group(1)), float(m.group(2)), float(m.group(3))
    layout = {k: int(w) for k, w in
              re.findall(r"(\w+):\{x:\d+,y:\d+,w:(\d+),h:\d+\}",
                         html[html.index("const LAYOUT="):][:800])}
    # measured in the browser: "Content & Social" + "not proven" in a 246px room
    # overlapped by 15px, so that combination must NOT be judged to fit.
    fits = lambda name, word, w: w >= len(name) * name_px + len(word) * word_px + pad
    check("the case that overlapped is now refused",
          fits("Content & Social", "not proven", 246), False)
    check("and so is the SEO one beside it",
          fits("SEO & Rankings", "no fallback", 246), False)
    check("a room with real room to spare still shows its word",
          fits("Leads & Outreach", "cannot run", layout["Leads"]), True)
    check("and so does the widest room on the floor",
          fits("IT & Site Health", "cannot run", layout["IT"]), True)


def test_a_desk_label_says_the_whole_job():
    """
    The floor rendered role.split(' ')[0] — the FIRST WORD ONLY. So the
    Releaser's job read "Only", the Screener's read "Scam", and the Answers
    desk read "AEO", which is why the owner asked why GEO was missing from his
    SEO sector. It never was; the label ate it. Every desk was affected.
    """
    print("A desk label says the whole job, not its first word:")
    html = (ROOT / "ui" / "office.html").read_text(encoding="utf-8")
    # Match the RENDERING expression, not the prose: the CSS comment above it
    # explains the old bug and names the old call, which failed this check the
    # first time it ran.
    check("the first-word chop is gone", "${a.role.split(' ')[0]}" in html, False)
    check("the full role is rendered", "${esc(a.role)}" in html, True)
    check("with the whole thing on hover", 'title="${escA(a.role)}"' in html, True)
    # Kept to ONE line deliberately: a second line adds ~10px per desk row and
    # the SEO room, at five desks, has 8px of slack. See the room-capacity test.
    #
    # Anchored to the START of the rule, not to the substring '.role{'. A later
    # rule for switched-off desks — '.emp.off .name,.emp.off .role{' — contains
    # those six characters too, so a plain index() found the wrong block and
    # reported the ellipsis missing when it was there all along.
    m = re.search(r"(?:^|\})\s*\.role\{([^}]*)\}", html, re.M)
    check("the .role rule is found at all", bool(m), True)
    css = m.group(1) if m else ""
    check("it stays on one line, cut with an ellipsis",
          "nowrap" in css and "ellipsis" in css, True)
    roles = re.findall(r'role:"([^"]+)"', html)
    check("and the Answers desk names both halves of its job",
          "AEO & GEO" in roles, True)


def test_the_ui_does_not_open_on_a_lie():
    """
    The first paint happens before /api/state answers. Whatever the staff list
    says is what the owner sees for that moment, so it may not claim anyone is
    working. This reads the actual file — a comment promising it is not enough.
    """
    print("The first paint is honest:")
    html = (ROOT / "ui" / "office.html").read_text(encoding="utf-8")
    body = html[html.index("function sectors()"):html.index("const SITES=")]
    check("no desk ships as working", 's:"working"' in body, False)
    check("no desk ships as waiting", 's:"waiting"' in body, False)
    desks = re.findall(r'(?<![a-z])s:"(\w+)"', body)   # not cls:"dir"
    check("every desk starts idle", set(desks), {"idle"})
    # 20: SEO gained Rival and Backlinks (pipelines/competitors.py runs as
    # 'seo-rival' daily and had no chair, so its work lit the Auditor's lamp),
    # IT gained Search, and SEO gained Answers when AEO/GEO arrived. Pinned so
    # nobody silently drops one — and see test_every_desk_fits_in_its_room for
    # why adding one is never only a roster change.
    check("and all twenty desks are still there", len(desks), 20)

    # Every desk must be ADDRESSABLE, not just readable. The chat drawer used to
    # send the label under the desk - 'Watcher', 'Hunter' - which carries no
    # sector, so the server could not tell whose brain should answer and fell
    # back to the Director's. Sixteen of seventeen desks were answering on the
    # MD's model. The id is what gets sent, and every desk has one.
    ids = re.findall(r'id:"([a-z-]+)"', body)
    check("every desk carries a stable id", len(ids), len(desks))
    check("and no two share one", len(set(ids)), len(ids))


def test_the_bulb_says_powered_working_or_broken():
    """
    The rule the owner set, revised twice by him after using it.

    v1: lit = alive, blinking = working, dark = broken. Fixed a real fault but
        tied the lamp to ACTIVITY alone, so a sector with every key in place
        looked the same as one with nothing configured.
    v2: dark = no API, steady = powered, blinking = working, red = broken.
        Right rule, and it still did not glow — see the two faults below.

    FAULT ONE: two lamps. A separate 9px `.status` dot sat at z-index:4 directly
    over the bulb and was painted flat grey whenever an agent was idle, which is
    most of the day. Every fix went to the bulb underneath it.

    FAULT TWO: the "lit" colour was #8fa4ad — a grey bead with a grey halo. It
    satisfied every assertion about not being the off colour and read as OFF to
    a person. I confirmed each fix by reading the computed value instead of
    looking at the screen, which is how this ran for ten rounds.

    So this test now asserts what a PERSON would see, not merely that a value
    changed.
    """
    print("The bulb says powered, working, or broken:")
    html = (ROOT / "ui" / "office.html").read_text(encoding="utf-8")

    def seg(sel):
        return html.split(sel + "{")[1].split("}")[0]

    print("\n  There is only ONE lamp:")
    check("the covering status dot is gone from the markup",
          'class="status"' in html, False)
    check("and its rules are gone with it", ".emp.idle .status" in html, False)

    print("\n  No API behind it -> dark:")
    off = seg(".antenna::after")
    check("a dark bead", "#26333a" in off, True)
    check("no halo at all", "box-shadow:none" in off, True)

    print("\n  Powered -> visibly lit, not merely a different hex:")
    powered = seg(".emp.pw .antenna::after")
    check("not the old grey that read as off", "#8fa4ad" in powered, False)
    check("a real colour", "var(--ok)" in powered, True)
    check("a halo big enough to see across the room", "0 0 20px" in powered, True)
    check("and it does not blink when it is only powered",
          "animation" in powered, False)
    check("the HEAD is outlined too, not just the bead",
          "var(--ok)" in seg(".emp.pw .head"), True)

    print("\n  Working -> the same lamp, blinking:")
    working = seg(".emp.working .antenna::after,\n.emp.pw.working .antenna::after")
    check("it blinks", "animation:pulse" in working, True)
    check("in the working colour", "var(--glass)" in working, True)
    check("and it blinks even before the sector has been proven — mid-run is mid-run",
          ".emp.working .antenna::after" in html, True)

    print("\n  Waiting -> lit amber, steady:")
    waiting = seg(".emp.pw.waiting .antenna::after")
    check("amber", "var(--amber)" in waiting, True)
    check("steady", "animation" in waiting, False)

    print("\n  Broken -> red, and red beats everything:")
    broken = seg(".emp.blocked .antenna::after,\n.emp.pw.blocked .antenna::after")
    check("red, not dark", "var(--danger)" in broken, True)
    check("and it throbs for attention", "animation:pulse" in broken, True)
    check("dark and broken are no longer the same signal", "#26333a" in broken, False)
    check("a powered desk that broke still goes red",
          ".emp.pw.blocked .antenna::after" in html, True)

    print("\n  Power comes from readiness, not from a guess:")
    check("the room asks the sector's own state",
          "rd.state !== 'blocked'" in html, True)


def test_a_dark_bulb_always_says_why():
    """A desk that goes dark without a reason is just a bug you cannot see."""
    print("A dark bulb carries its reason:")
    real_b = board.DB
    board.DB = _fresh_db()
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
        board.DB = _fresh_db()
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
        board.DB = _fresh_db()
        tid = board.post_task("Leads", "Collect hotel leads", "detail")
        board.claim_task(tid, "leads-hunter")
        check("a fresh claim is not a stall",
              _room(state.build(), "Leads")["agents"][0]["s"], "working")
    finally:
        board.DB = real_b


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
        # THE ONE THAT WAS COSTING THE MOST. The office chat drawer sends the
        # label under the desk - 'Watcher', 'Hunter', 'Strategist' - which has
        # no sector prefix in it. whois() looked only at prefixes and defaulted
        # everything else to the Director, so sixteen desks out of seventeen
        # answered on the MD's model. That is the same override the owner had
        # the LLM shelf emptied to prevent, arriving through another door.
        for label, want in [("Watcher", "it"), ("Hunter", "leads"),
                            ("Strategist", "seo"), ("Writer", "content"),
                            ("Screener", "security"), ("Reporter", "report"),
                            ("Director", "md")]:
            check(f"the office label '{label}' finds its OWN brain",
                  agent_chat.whois(label)["brain_task"], want)

        # a wrong guess puts the MD's voice behind a sector it never ran.
        check("an unknown name does not crash",
              agent_chat.whois("")["sector"], "unknown")
        check("...and is given no brain at all",
              agent_chat.whois("nonsense-desk")["brain_task"], None)
        unknown = agent_chat.ask("nonsense-desk", "what have you done?")
        check("...it still answers, from the record", unknown["ok"], True)
        check("...and says why there is no model",
              "not a desk I recognise" in unknown.get("unrouted", ""), True)

        # An unrouted sector is a setup step, not a crash, and the two must not
        # be dressed up the same. Nothing is configured in a fresh cabinet, so
        # every answer above came back unrouted.
        check("no model on the shelf reads as unrouted", bool(broke.get("unrouted")), True)
        check("and is NOT reported as a failure", broke.get("brain_error"), None)
        check("the record does not also explain itself",
              "routed to my sector" in broke["reply"], False)

        # Every exchange is on the record, like everything else here. This used
        # to assert n >= 0, which is true of every number there has ever been —
        # it would have passed just as happily with the logging deleted.
        con = sqlite3.connect(board.DB)
        n = con.execute("SELECT count(*) FROM requests WHERE from_sector='Owner'").fetchone()[0]
        con.close()
        # four now: the three above plus the unrecognised desk. A conversation
        # that produced no model answer is still a conversation and still logged.
        check("every conversation is logged, not private", n, 4)
    finally:
        board.DB = agent_chat.DB = real_b


def test_a_desk_answers_from_its_own_findings():
    """
    The owner said the agents give guesses rather than real analysis, and the
    cause was not the rules — those already forbid inventing anything. It was
    the RECORD. Every desk was handed its own run notes plus six agency-wide
    counts, identical for every desk in the building, and nothing else. The
    Auditor had three hundred and twenty-three findings on file and not one of
    them in front of it, so all it could do was describe its job.
    """
    print("A desk answers from the rows it wrote, not from what its job sounds like:")
    from . import agent_chat
    real_b = board.DB
    board.DB = agent_chat.DB = _fresh_db()
    try:
        con = sqlite3.connect(board.DB)
        con.execute("""CREATE TABLE IF NOT EXISTS aeo_findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT, url TEXT, family TEXT,
            check_id TEXT, passed INTEGER, weight REAL, detail TEXT, fix TEXT,
            owner_sector TEXT, found_at INTEGER)""")
        con.execute("INSERT INTO aeo_findings(url,family,check_id,passed,weight,"
                    "detail,fix,found_at) VALUES"
                    "('https://x.ae/faq.php','aeo','aeo.localbusiness',0,9,"
                    "'no LocalBusiness markup','Add LocalBusiness JSON-LD',1)")
        con.execute("INSERT INTO aeo_findings(url,family,check_id,passed,weight,"
                    "detail,fix,found_at) VALUES"
                    "('https://x.ae/','aeo','aeo.answer_first',1,5,'fine','',1)")
        con.execute("INSERT INTO site_issues(kind,severity,detail,url,status,"
                    "found_by,found_at) VALUES"
                    "('form_dead','critical','the contact form posts nowhere',"
                    "'https://x.ae/contact','open','it-watcher',1)")
        con.commit(); con.close()

        rec = agent_chat._facts_text(agent_chat.gather("seo-answers"))
        check("the Auditor's own failing check is in its record",
              "no LocalBusiness markup" in rec, True)
        check("with the page it is on", "faq.php" in rec, True)
        check("and the fix it wrote", "Add LocalBusiness JSON-LD" in rec, True)
        check("the agency-wide counts are labelled as NOT its work",
              "this is NOT your work" in rec, True)

        # A desk is never handed another desk's output to talk about — that is
        # how an agent ends up confidently describing work it had no part in.
        check("the Auditor is not given IT's tickets",
              "the contact form posts nowhere" in rec, False)
        it_rec = agent_chat._facts_text(agent_chat.gather("it-watcher"))
        check("but IT is", "the contact form posts nowhere" in it_rec, True)
        check("and IT is not given the answer audit",
              "no LocalBusiness markup" in it_rec, False)

        # With no brain configured the findings must still be read out. An
        # agency with no API key used to report that it had run and not one
        # thing it had found.
        r = agent_chat.ask("seo-answers", "what did you find?")
        check("the offline reply carries the real finding",
              "no LocalBusiness markup" in r["reply"], True)
        check("and it is still marked as coming from the record, not a model",
              r["offline"], True)

        # A desk with no output table of its own says so rather than filling in.
        ill = agent_chat._facts_text(agent_chat.gather("content-illustrator"))
        check("a desk with no output table admits it",
              "YOU HAVE NO OUTPUT TABLE" in ill, True)

        # The prompt has to demand specifics, or the record is wasted.
        check("the prompt tells it to name the page, not describe the job",
              "That is a description of your job" in agent_chat.SYSTEM, True)
        check("and to say when a question falls outside its record",
              "Your record has edges" in agent_chat.SYSTEM, True)

        # And a name we cannot place gets NO brain rather than the strongest one
        # in the building. A wrong refusal costs one answer read off the record;
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
        # It used to end here with an inert handle: no cabinet, so no run
        # recorded, and the only promise was that the job survived. The
        # migration builds a missing cabinet on first touch now — which was
        # forced by three tables never reaching the owner's existing one — so
        # the stronger thing is true. The work happens AND it is traced.
        check("and the run is now actually recorded, not silently dropped",
              isinstance(run.id, int), True)
        con = sqlite3.connect(board.DB)
        row = con.execute("SELECT agent, note, usd_cost FROM agent_runs").fetchone()
        con.close()
        check("with what it did and what it cost",
              (row[0], row[1], row[2]), ("leads-hunter", "still works", 1.5))

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
    real_b = board.DB
    board.DB = _fresh_db()
    try:
        with board.agent_run("seo-strategist") as run:
            run.spent(0.42, tokens_in=1200, tokens_out=300)
        check("today's spend includes the run", state.build()["metrics"]["spend"], "$0.42")
    finally:
        board.DB = real_b


from providers import store as _store


def main():
    with _store.isolated():
        _main()


def _main():
    test_a_run_is_recorded()
    test_a_crash_is_not_a_clean_finish()
    test_a_desk_that_did_nothing_stays_dark()
    test_the_run_wins_over_a_stale_task_row()
    test_each_agent_reaches_its_own_desk()
    test_every_desk_fits_in_its_room()
    test_a_room_label_and_its_chip_do_not_collide()
    test_a_desk_label_says_the_whole_job()
    test_the_ui_does_not_open_on_a_lie()
    test_the_bulb_says_powered_working_or_broken()
    test_a_dark_bulb_always_says_why()
    test_the_agent_chat_is_not_canned()
    test_a_desk_answers_from_its_own_findings()
    test_tracing_never_breaks_the_work()
    test_spend_still_adds_up()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: {_fail}")
        sys.exit(1)
    print("all floor tests passed")


if __name__ == "__main__":
    main()
