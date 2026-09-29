"""
day_run.py — the Run button. Check everything, take the owner's instructions,
then work.

    python3 -m pipelines.day_run --check            just the preflight, no work
    python3 -m pipelines.day_run --start            check, then run the day
    python3 -m pipelines.day_run --start --force    run even though something is broken
    python3 -m pipelines.day_run --status           what the current/last run did
    python3 -m pipelines.day_run --stop             stop the day that is running

WHAT "CHECK EVERYTHING" ACTUALLY MEANS

The owner asked for a button that "will firstly check the whole program like
apis, connectors, each agent and sector working or not and then user's input and
then will start to work". Four checks, in the order that fails cheapest first:

  1. the cabinet    is there a database, and does it have the tables? Free.
  2. the sectors    does each one have a brain and the keys it borrows?
                    (coordination/readiness.py — config only, free)
  3. the pipelines  does every module on today's list actually IMPORT? This one
                    is easy to skip and it is the one that bites: a typo in
                    seo_gap.py does not show up in any API test, it shows up
                    forty minutes into the run as a step that produced nothing.
                    Importing is cheap and it is the closest honest answer to
                    "is each agent working".
  4. the APIs       every model in every chain, every API on every shelf, and
                    the notification channel. This one spends money, so it is
                    the only part you can turn off (--quick).

BLOCKING VS WARNING, AND WHY FORCE EXISTS

Blocking means a sector cannot run at all. The day stops and the owner is told
which sector and why — starting anyway would produce a run that looks like it
worked and quietly did a third of the job, which is worse than not running.

But the owner is the tie-breaker, not this file. --force runs it anyway, records
forced=1 against the run, and the office shows it as forced. A safety rail you
cannot override is a safety rail people work around; one that makes you say so
out loud, in writing, is one that holds.

ONE DAY AT A TIME

The whole run takes the 'day_run' resource lock. Two presses of the button is
the most obvious way to get two MDs planning the same day and two Hunters
mailing the same lead, and the lock is the primitive the handbook already
provides for exactly that. Second press is refused, plainly, and told who holds it.
"""
import os
import argparse
import json
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from coordination import board, console, proposals, readiness   # noqa: E402

PY = sys.executable or "python3"
LOCK = "day_run"
LOCK_HOLDER = "owner-run-button"

# ---------------------------------------------------------------------------
# LANES — what actually runs at the same time
# ---------------------------------------------------------------------------
#
# THE OWNER'S QUESTION. "If an agent is taking more time and it's working, it
# should be put into the other line so it doesn't hold up the other agents'
# work."
#
# He is right, and the earlier answer — "the worst case is a writer waiting,
# and it's already told to wait" — was about the wrong thing. That was about
# the database, where a write takes about a millisecond and nobody waits for
# anything. The real queue was here: sixteen steps in one `for` loop, each one
# a `subprocess.run` that blocks until it returns, each allowed up to seven
# minutes. SEO's four passes could not start until IT had finished checking the
# site, and Content could not start until all four of those were done. Nothing
# in the work required that. It was just the shape of the loop.
#
# So the list below is not an order any more, it is a set of LANES. A lane is a
# queue of steps that genuinely must follow each other; separate lanes run at
# the same time. `needs` names the steps a lane's first step cannot start
# before.
#
# WHAT ACTUALLY DEPENDS ON WHAT — and nothing else does:
#
#   everything            waits for the MD, because until the MD plans, the
#                         board is empty and every sector correctly refuses to
#                         invent work for itself.
#   Security              waits for Leads to collect, because it screens what
#                         came in and there is nothing to screen before that.
#   Content               waits for the AEO/GEO audit, because the findings are
#                         what the briefs are written against.
#   the closing Overseer  waits for all of it, because it is looking for agents
#                         that died DURING the day.
#   Report                waits for the Overseer, so the digest that reaches
#                         his phone includes what broke.
#
# IT, Leads and SEO depend on none of each other. They are three lanes and they
# run together.
#
# The cap is MAX_LANES, not "as many as there are". Three subprocesses is fine;
# ten would have every sector's model call landing in the same second, which is
# the thing that makes a primary slow in the first place.

STEPS = [
    # ---- 'open': the three that must happen before anything else ----------
    # Read the phone FIRST. An approval he gave last night is a decision the
    # agency should already be acting on this morning — not one it discovers
    # after planning the day around him not having answered.
    ("Read your answers from the phone", "coordination.ntfy_replies", "Director", "open"),
    # Then the Overseer, still BEFORE the MD plans. The MD reasons from the live
    # state of the business, and "SEO's strategist has been dead since Tuesday"
    # is part of that state — planning a day of SEO work around a desk that is
    # not answering is exactly the mistake this is here to stop. It also closes
    # yesterday's faults that have since fixed themselves, so the MD is not
    # planning around a problem that has gone.
    ("The Overseer checks the agency itself", "coordination.faults", "Director", "open"),
    ("The MD plans the day", "pipelines.md_cycle", "Director", "open"),

    # ---- three lanes that need nothing from each other --------------------
    ("IT checks the site", "pipelines.it_health", "IT", "it"),
    # AFTER the check, in the same lane, because it works the tickets the check
    # just filed. It was missing entirely: there was an it-fixer desk, an
    # it-fixer agent card and an it-fixer light on the office floor, and no
    # pipeline that ever ran under that name — so every ticket the Watcher
    # filed sat open for ever and the board looked calm rather than stuck.
    ("IT patches what it found", "pipelines.it_fix", "IT", "it"),
    # AND THE AGENCY ITSELF, which is the half that was missing. The
    # Overseer files a fault when a desk crashes and posts it to IT; every
    # recipe the Fixer has is a WEBSITE recipe, so it reported "no recipe"
    # and the lamp stayed red for ever. This is IT's hands for its own
    # house: dead locks, dead claims, runs that never ended — and for what
    # it cannot fix, a word to him or to the MD rather than silence.
    ("IT repairs the agency itself", "pipelines.it_selfheal", "IT", "it"),
    # AFTER the Fixer, so anything he approved since yesterday goes on today.
    # It is the last link in the chain and it was the missing one: the Watcher
    # found problems, the Fixer wrote the changes, he pressed Approve, and his
    # yes went into a table nothing ever read. This is what reads it.
    ("IT carries out what you approved", "pipelines.it_release", "IT", "it"),
    ("IT reindexes site search", "pipelines.site_search", "IT", "it"),
    # LAST in the IT lane, after anything the agency itself might have changed,
    # so its own edits are already in the log and this reports only what moved
    # without us. That is the half he asked for: "there should be a section in
    # program where i can see whats changed in website".
    ("IT looks for changes to the site", "pipelines.site_watch", "IT", "it"),

    ("Leads maps its sources", "pipelines.leads_sources", "Leads", "leads"),
    ("Leads collects", "pipelines.leads_hunt", "Leads", "leads"),
    # The keyless collector, alongside the Places one rather than instead
    # of it. Two partial lists through one dedup gate beat one partial
    # list, and this one runs whether or not a Places key exists.
    ("Leads collects from OpenStreetMap", "pipelines.leads_osm", "Leads", "leads"),
    # LAST in the Leads lane, and only after Security has had its pass — see
    # the 'outreach' lane below. Collecting a lead and writing to it in the
    # same breath is how an unscreened one gets contacted.
    ("Leads writes to the cleared ones", "pipelines.leads_outreach", "Leads",
     "outreach"),

    # FIRST in the SEO lane: it reads fifteen pages in well under a minute and
    # claims nothing off the board, so it costs the lane almost nothing and
    # never takes the sector's one job for the run. What it files goes to IT's
    # queue, and it closes or reopens yesterday's tickets by re-reading them.
    ("SEO audits the site (technical)", "pipelines.seo_audit", "SEO", "seo"),
    ("SEO builds the keyword picture (free sources)", "pipelines.seo_intel", "SEO", "seo"),
    ("SEO finds the gaps", "pipelines.seo_gap", "SEO", "seo"),
    ("SEO watches the rivals", "pipelines.competitors", "SEO", "seo"),
    ("SEO works the link list", "pipelines.seo_backlinks", "SEO", "seo"),
    # Last in the SEO lane and before the briefs, deliberately: AEO/GEO findings
    # are what Content is briefed to fix, so they have to exist first.
    ("SEO audits the answers (AEO/GEO)", "pipelines.seo_answers", "SEO", "seo"),

    # ---- two that genuinely wait on one of the three above ----------------
    ("Security screens what came in", "pipelines.security_screen", "Security", "security"),
    ("Content briefs the pages", "pipelines.content_briefs", "Content", "content"),
    # AFTER the briefs, in the same lane, because it writes what they
    # briefed. It was the missing link the owner asked about: "how the
    # articles will get published?" — they were briefed and nothing on this
    # machine ever turned a brief into words. This writes them; the Auditor
    # checks them; he approves them; the Releaser puts them up.
    ("Content writes the pages", "pipelines.content_write", "Content", "content"),

    # ---- 'close': after everything, because it is about everything --------
    # The Overseer AGAIN. The first pass catches what broke yesterday; this one
    # catches what broke during today — an agent that died mid-morning would
    # otherwise sit unfiled until tomorrow, which is precisely the forty-one
    # minutes of nothing the owner watched. Before Report, so the digest that
    # reaches his phone includes it.
    ("The Overseer checks the agency again", "coordination.faults", "Director", "close"),
    ("Report scores the day", "pipelines.report_daily", "Report", "close"),
]

# Which lanes a lane waits for. Waiting is on COMPLETION, not on success — IT
# falling over must not stop Report scoring what Leads did, which was already
# true when this was one loop and stays true now it is several.
LANE_AFTER = {
    "open":     (),
    "it":       ("open",),
    "leads":    ("open",),
    "seo":      ("open",),
    "security": ("leads",),      # nothing to screen until Leads has collected
    "content":  ("seo",),        # briefs are written against the AEO findings
    # Mail goes out AFTER Security has screened what came in today. Its own
    # lane rather than the tail of 'leads' precisely so it cannot start early:
    # the handbook says Leads asks Security to clear a lead before Outreach
    # ever contacts it, and a lane boundary is how that is enforced rather
    # than hoped for.
    "outreach": ("security",),
    "close":    ("it", "leads", "seo", "security", "content", "outreach"),
}

# How many steps may be in flight at once. Not "all of them": every step is a
# Python process that will probably make a model call, and firing six at once
# is how you turn one slow provider into six slow providers. Three is roughly
# the number of independent lanes there are to run anyway.
MAX_LANES = 3

STEP_TIMEOUT = 420

# THE STEPS THAT HONESTLY NEED LONGER, NAMED ONE BY ONE.
#
# "the test suite did not finish inside 360s (my conerce is the time is out why
#  cannot we test by increasing the time out limit)"
#
# He is right and the answer was a circular one: the Releaser's test budget was
# capped under STEP_TIMEOUT so a suite would not be killed mid-run, and
# STEP_TIMEOUT was one number for every step in the day. So the suite could
# never be given more time without giving every step more time, which would
# have hidden genuinely hung steps behind a much longer wait.
#
# Two steps are different in kind rather than merely slower, and both showed up
# in his log on the same day:
#
#   it_release  runs the WHOLE test suite before it is allowed to write to his
#               website. That is the red line and it is worth the minutes. The
#               suite has grown to thirty-seven and his machine is not this one.
#   seo_intel   gathers from free sources one polite request at a time. It is
#               bounded by other people's servers, not by us.
#
# Everything else keeps the seven minutes, so a step that has genuinely hung is
# still caught quickly. A number in here is a promise that the step is doing
# real work for that long — not somewhere to hide a hang.
#   content_write  every page is a model call, and a service page is not a
#               short one. Six queued pages at a minute or two each cannot fit
#               in seven minutes, and being killed part-way used to throw away
#               the pages already written — so the queue never drained, and the
#               same six timed out again the next day, and the next. It now
#               stops one page short of its own budget and banks what it has
#               (see content_write._deadline), and the budget is honest about
#               what writing pages costs.
LONGER = {
    "pipelines.it_release": 1200,
    "pipelines.seo_intel": 1500,
    "pipelines.content_write": 1500,
}


# ---------------------------------------------------------------------------
# KEEP GOING — THE DAY IS NOT OVER WHILE HIS ORDERS ARE STILL OPEN
# ---------------------------------------------------------------------------
#
# "with out completing the tasks the program finish the daily run ... when the
#  any sector or any agent is running the program should not stop and says run
#  the day" / "i ask for 20 leads it just give 8 leads and that was it and
#  program stops"
#
# He was right, and it was the shape of the day rather than a crash. The day
# was ONE PASS: every step once, and every desk takes ONE job off its board per
# pass (coordination/deskwork). So an IT board holding three of his orders got
# one of them looked at and the day closed on the other two, and a Leads pass
# that stored 8 of 20 closed its job with nobody ever asking for the other 12.
#
# So after the main pass, and BEFORE the Overseer's closing check and Report
# (they are about the whole day, extra rounds included), the day looks at what
# is still unfinished and goes round again for the sectors that have some:
#
#   jobs still open on a sector's board     the desks those jobs are for run
#                                           again, and each takes its next one
#   a counted target still short            the MD tops the job up with what is
#     (his "20 leads")                      still to go, and the collectors go
#                                           again; Security screens what came
#                                           in, and only then is anyone written to
#   changes he approved, not yet on         the Releaser goes again
#     his site
#
# It stops, and says why, when nothing is left (the good ending); when a
# sector's round moved nothing — it has said all it can today (no recipe, no
# key, the map worked out), and running it again would spend tokens to say the
# same thing; after FOLLOW_UP_ROUNDS rounds or FOLLOW_UP_BUDGET seconds, so a
# bug can never keep the day going for ever; or the moment he presses Stop.
#
# Whatever is still open at the end is written on the run, each thing with its
# reason, so the day ends on "these two wait for your yes, this one needs a
# key" rather than on a Run button and silence.

FOLLOW_UP_ROUNDS = 4
FOLLOW_UP_BUDGET = 2 * 3600
# Extra steps are numbered from here, well clear of STEPS, so nothing that
# reads a step by its place in STEPS (resume, the preflight) ever mistakes one.
FOLLOW_UP_SEQ = 1000
# The lane that waits for the extra rounds: everything about the whole day.
FOLLOW_UP_BEFORE = "close"
FOLLOW_UP_LANE = "more"

# Which steps work each desk's jobs, in the order they run.
DESK_STEPS = {
    "it-watcher": ("pipelines.it_health",),
    "it-fixer": ("pipelines.it_fix",),
    "it-releaser": ("pipelines.it_release",),
    "it-search": ("pipelines.site_search",),
    "seo-strategist": ("pipelines.seo_gap",),
    "seo-answers": ("pipelines.seo_answers",),
    "seo-rival": ("pipelines.competitors",),
    "seo-backlinks": ("pipelines.seo_backlinks",),
    "content-writer": ("pipelines.content_briefs", "pipelines.content_write"),
    "leads-mapper": ("pipelines.leads_sources",),
    "leads-hunter": ("pipelines.leads_hunt", "pipelines.leads_osm"),
    "leads-outreach": ("pipelines.leads_outreach",),
    "security-screener": ("pipelines.security_screen",),
}
# A job posted to the whole sector, with no desk named on it.
SECTOR_STEPS = {
    "IT": ("pipelines.it_fix",),
    "SEO": ("pipelines.seo_gap",),
    "Content": ("pipelines.content_briefs", "pipelines.content_write"),
    "Leads": ("pipelines.leads_hunt", "pipelines.leads_osm"),
    "Security": ("pipelines.security_screen",),
}
# Which chain a sector's extra work runs in. Security rides behind Leads for
# the same reason as in the main pass: it screens what Leads just collected,
# and nobody is written to until it has.
CHAIN_OF = {"IT": "it", "SEO": "seo", "Content": "content",
            "Leads": "leads", "Security": "leads"}


def step_timeout_for(module) -> int:
    """How long this particular step is allowed to take."""
    return int(LONGER.get(module, STEP_TIMEOUT))


def longest_step() -> int:
    """The longest any step may run. What the reaper's fuse has to clear."""
    return max([STEP_TIMEOUT] + list(LONGER.values()))

# How long a run with NO PID RECORDED may go quiet before it is written off.
# Only rows from before the pid column existed land here; everything since is
# settled by asking the operating system whether the process is still there,
# which is a real answer rather than a guess. An hour is deliberately far more
# rope than a stopwatch alone deserves — the cost of waiting is a stale line on
# a screen, and the cost of being wrong is killing the books on a live run.
NO_PID_GRACE = 3600

# ---------------------------------------------------------------------------
# STOPPING
# ---------------------------------------------------------------------------
#
# "there is no any stop button that makes the agents or overseer to understand
# that the task is stopped by the user."
#
# He is describing two different things and both were missing. Closing the
# window is handled elsewhere — board.sweep_dead_processes() clears up after a
# process that has gone. This is the other one: stopping a run that is still
# alive, on purpose, and having every part of the agency understand that is
# what happened rather than inferring a crash.
#
# The shape is ask-then-insist, because those are genuinely different things:
#
#   ASK      board.request_stop() writes a row. One thread in the run watches
#            for it (STOP_POLL seconds apart) and sets an Event; the lanes read
#            the Event, which costs nothing, so a stop is noticed within a
#            second without three lanes hammering the cabinet.
#   INSIST   stop() waits STOP_GRACE seconds for the run to close itself. A
#            healthy run takes about as long as it takes to kill one child, so
#            the grace period is generous. If it is still 'running' after that
#            the run is not going to answer, and its recorded process is ended.
#
# The step in flight is a subprocess, so it is stopped the same way: TERM, then
# KILL two seconds later. A step's own grandchildren are not chased — nothing
# in the daily list spawns any, and killing a process tree by guesswork is how
# you end up killing the wrong tree.
STOP_POLL = 1.0
STOP_GRACE = 25
CHILD_GRACE = 5


def _now():
    return int(time.time())


# ---------------------------------------------------------------------------
# 1-4. THE PREFLIGHT
# ---------------------------------------------------------------------------

def check_cabinet() -> dict:
    if not Path(board.DB).exists():
        return {"name": "Filing cabinet", "ok": False,
                "says": "no database yet — it builds itself when you save the website form"}
    board.ensure_schema()
    try:
        with sqlite3.connect(board.DB, timeout=30) as c:
            have = {r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
    except Exception as e:
        return {"name": "Filing cabinet", "ok": False, "says": f"cannot be opened: {e}"}
    need = {"tasks", "agent_runs", "decisions", "leads", "site_issues",
            "proposals", "day_runs"}
    missing = sorted(need - have)
    return {"name": "Filing cabinet", "ok": not missing,
            "says": f"{len(have)} tables, all present" if not missing
                    else f"missing: {', '.join(missing)}"}


def check_pipelines() -> list:
    """
    Does every module on today's list import cleanly?

    Run in a SUBPROCESS on purpose. Importing them into this process would let a
    module with a side effect at import time — one that opens the cabinet, or
    posts something — do it during a check the owner thinks is read-only.
    """
    out = []
    for label, mod, room, _lane in STEPS:
        code = f"import importlib,sys; importlib.import_module({mod!r})"
        p = subprocess.run([PY, "-c", code], cwd=str(ROOT), capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=120)
        if p.returncode == 0:
            out.append({"name": label, "module": mod, "room": room, "ok": True,
                        "says": "loads"})
        else:
            last = [l for l in (p.stderr or "").strip().splitlines() if l.strip()]
            out.append({"name": label, "module": mod, "room": room, "ok": False,
                        "says": (last[-1] if last else "would not import")[:200]})
    return out


def check_apis(quick=False) -> dict:
    try:
        from providers import preflight
        return preflight.sweep(quick=quick)
    except Exception as e:
        return {"ok": False, "ready": False, "blocking": [f"could not sweep the APIs: {e}"],
                "warnings": [], "routing": [], "shelf": [], "integrations": []}


def _once(lines):
    """Same complaint from two checks is still one problem. Order preserved."""
    seen, out = set(), []
    for l in lines:
        k = (l or "").strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append(l)
    return out


def preflight(quick=False) -> dict:
    """The whole check, in one shape the UI can render top to bottom."""
    t0 = time.time()
    cab = check_cabinet()
    sect = readiness.all_sectors()
    pipes = check_pipelines()
    apis = check_apis(quick=quick)

    blocking, warnings = [], []
    if not cab["ok"]:
        blocking.append(f"Filing cabinet: {cab['says']}")
    for s in sect["sectors"]:
        for b in s["blocking"]:
            blocking.append(f"{s['label']}: {b}")
        for t in s["thin"]:
            warnings.append(f"{s['label']}: {t}")
    for p in pipes:
        if not p["ok"]:
            blocking.append(f"{p['name']} will not even load: {p['says']}")
    blocking += list(apis.get("blocking") or [])
    warnings += list(apis.get("warnings") or [])

    # Deduplicate: a dead brain shows up in both the sector light and the API
    # sweep, and telling the owner the same thing twice trains him to skim.
    blocking = _once(blocking)
    warnings = _once(warnings)

    res = {"ok": True, "at": _now(), "quick": quick,
           "took_ms": int((time.time() - t0) * 1000),
           "cabinet": cab, "sectors": sect, "pipelines": pipes, "apis": apis,
           "ready": not blocking, "blocking": blocking, "warnings": warnings,
           "head": ("ready to run" if not blocking else
                    f"{len(blocking)} thing{'s' if len(blocking) > 1 else ''} "
                    "must be fixed first")}
    readiness.record_check(res)
    return res


# ---------------------------------------------------------------------------
# THE RUN ITSELF
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# THIS RUN ONLY
# ---------------------------------------------------------------------------
#
# "i will not every time run the whole program, sometimes i will only run
#  content sector with MD and IT and Security because these 3 are the main,
#  we cannot disable them ... will save my token usage"
#
# The permanent switches (board.set_sector) are for "stop doing this until I
# say". This is for "today, just these" — it lives on the run row and is gone
# when the run ends, so nothing he picks for one run leaks into the next.
# The essential sectors always run: the MD plans, IT holds the door, Security
# clears, Report scores. See board.ESSENTIAL_SECTORS.

def pick_sectors(only) -> list:
    """The rooms that run this time, essentials always in; [] means every one."""
    if not only:
        return []
    rooms = sorted({room for _l, _m, room, _lane in STEPS})
    want = {str(x).strip().lower() for x in (only or []) if str(x).strip()}
    picked = [r for r in rooms if r.lower() in want or board.sector_essential(r)]
    return [] if set(picked) == set(rooms) else picked


def _only_for(rid) -> list:
    try:
        with sqlite3.connect(board.DB, timeout=30) as c:
            r = c.execute("SELECT only_sectors FROM day_runs WHERE id=?", (rid,)).fetchone()
        return json.loads(r[0]) if r and r[0] else []
    except Exception:
        return []


def _open_run(note, forced, blocking, only=None, skip=None, resume_of=None,
              tidied=None):
    """
    Write the run and its steps down. `skip` is {seq: why} for steps a resume
    does not run: they are settled here, with the reason, so the run panel shows
    from the first second what will and will not happen.
    """
    rid = uuid.uuid4().hex[:12]
    skip = skip or {}
    board.ensure_schema()
    now = _now()
    with sqlite3.connect(board.DB, timeout=30) as c:
        c.execute("INSERT INTO day_runs(id,started_at,status,started_by,note,forced,"
                  "blocking,only_sectors,steps_total,steps_done,pid,host,"
                  "resume_of,tidied)"
                  " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (rid, now, "running", "owner", (note or "")[:2000],
                   1 if forced else 0, json.dumps(blocking or []),
                   json.dumps(only) if only else None, len(STEPS), len(skip),
                   os.getpid(), board._this_host(), resume_of,
                   json.dumps(tidied) if tidied else None))
        for i, (label, mod, room, lane) in enumerate(STEPS):
            if i in skip:
                c.execute("INSERT INTO day_steps(run_id,seq,label,module,status,lane,"
                          "started_at,ended_at,code,summary)"
                          " VALUES(?,?,?,?, 'skipped', ?,?,?,0,?)",
                          (rid, i, label, mod, lane, now, now, skip[i]))
            else:
                c.execute("INSERT INTO day_steps(run_id,seq,label,module,status,lane)"
                          " VALUES(?,?,?,?, 'waiting', ?)", (rid, i, label, mod, lane))
    return rid


def _preskipped(rid) -> set:
    try:
        with sqlite3.connect(board.DB, timeout=30) as c:
            return {r[0] for r in c.execute(
                "SELECT seq FROM day_steps WHERE run_id=? AND status='skipped'",
                (rid,))}
    except Exception:
        return set()


# ---------------------------------------------------------------------------
# RESUME — START FROM WHERE IT LEFT OFF
# ---------------------------------------------------------------------------
#
# "add a button that should say resume or start from where you left by
#  clicking that button the program will start from where it was closed last
#  time it will finish that unanswered and queued tasks first"
#
# A resume is an ordinary run with a plan: the clear-up first (every run has
# that now — coordination/leftovers.tidy), then only
#
#   the steps the last run did not get to finish   if it was cut off
#   the steps of any sector that still has work   a question in its inbox or a
#     waiting                                       job on its board
#   the cheap ones about the agency as a whole    phone answers, the Overseer
#                                                   either side, Report
#
# Everything else finished last time and is not repeated — which is the model
# calls saved. The MD does not plan the day again unless its own step never
# ran or questions are waiting for it: the plan it made still stands, and a
# second plan on top of an unfinished first is the double load he means.

# Cheap, and about the agency as a whole: they run on every resume.
ALWAYS_ON_RESUME = ("coordination.ntfy_replies", "coordination.faults",
                    "pipelines.report_daily")

# Steps that do their own fresh work rather than working a queue. A sector
# with jobs waiting does not re-run these just because of the queue.
NOT_FOR_A_QUEUE = ("pipelines.seo_audit", "pipelines.site_watch",
                   "pipelines.site_search", "pipelines.md_cycle")


def resume_plan(prev, rooms) -> dict:
    """{seq: why} for each step a resume does NOT run."""
    prev = prev or {}
    unfinished = {u["label"] for u in prev.get("unfinished") or []}
    rooms = {str(r).lower() for r in (rooms or [])}
    when = prev.get("ended_at") or prev.get("started_at")
    at = f" ({time.strftime('%H:%M', time.localtime(when))})" if when else ""
    skip = {}
    for i, (label, mod, room, _lane) in enumerate(STEPS):
        if mod in ALWAYS_ON_RESUME or label in unfinished:
            continue
        if mod == "pipelines.md_cycle":
            if "director" in rooms:
                continue                  # questions are waiting for the MD
            skip[i] = ("the MD's plan from the last run still stands, so it is not "
                       "made again on top of work that is not finished")
            continue
        if room.lower() in rooms and mod not in NOT_FOR_A_QUEUE:
            continue
        skip[i] = f"done in the last run{at} and nothing is waiting for it — not repeated"
    return skip


def _step(rid, seq, **fields):
    sets = ", ".join(f"{k}=?" for k in fields)
    with sqlite3.connect(board.DB, timeout=30) as c:
        c.execute(f"UPDATE day_steps SET {sets} WHERE run_id=? AND seq=?",
                  (*fields.values(), rid, seq))


def _close_run(rid, status):
    with sqlite3.connect(board.DB, timeout=30) as c:
        # Settled, not 'succeeded' — same count status() shows on the bar. A
        # step that was skipped or stopped is never going to run again, and
        # leaving it out left a finished day showing as three-quarters done.
        done = c.execute("SELECT count(*) FROM day_steps WHERE run_id=?"
                         " AND status NOT IN ('waiting','running')",
                         (rid,)).fetchone()[0]
        c.execute("UPDATE day_runs SET status=?, ended_at=?, steps_done=? WHERE id=?",
                  (status, _now(), done, rid))


def _left_open(rid, more):
    """What the extra rounds did and what is still open, kept on the run."""
    try:
        with sqlite3.connect(board.DB, timeout=30) as c:
            c.execute("UPDATE day_runs SET left_open=? WHERE id=?",
                      (json.dumps({"rounds": more.get("rounds") or 0,
                                   "why": more.get("why") or "",
                                   "left": list(more.get("left") or [])[:12]}), rid))
    except sqlite3.Error:
        pass


def _as_json(text):
    try:
        return json.loads(text)
    except Exception:
        pass
    i = text.find("{")
    if i >= 0:
        try:
            return json.loads(text[i:])
        except Exception:
            pass
    return None


def _summarise(out, code):
    """
    One line saying what the step did.

    Most pipelines answer in pretty-printed JSON, so "the last line of output"
    — the obvious implementation — put a single closing brace next to every
    step in the owner's run panel. Read the object instead, and fall back to
    the FIRST line for the human-readable ones, because a pipeline that prints
    prose leads with its headline and trails off into detail.
    """
    text = (out or "").strip()
    if not text:
        return f"no output (exit {code})"

    obj = _as_json(text)
    if isinstance(obj, dict):
        if obj.get("ok") is False:
            return (str(obj.get("error") or obj.get("why")
                        or "the step reported it did not run"))[:300]
        bits = []
        for k in ("note", "summary", "head", "why"):
            if obj.get(k):
                bits.append(str(obj[k]))
                break
        # the counts a pipeline reports ARE its summary — 'gaps found 69' says
        # more than any sentence we could compose about it
        for k, v in obj.items():
            if isinstance(v, bool) or k in ("ok", "at", "ts"):
                continue
            if isinstance(v, int) and v:
                bits.append(f"{k.replace('_', ' ')} {v}")
            elif isinstance(v, list) and v:
                bits.append(f"{k.replace('_', ' ')} {len(v)}")
        return (", ".join(bits[:5]) or "finished")[:300]

    lines = [l.rstrip() for l in text.splitlines() if l.strip()]
    if any(l.startswith("Traceback") for l in lines):
        return lines[-1].strip()[:300]
    return lines[0][:300]


def _verdict(out, code):
    """
    Three outcomes, not two.

    Exit code alone reads 'IT had no task on the board' as a clean success, and
    the run panel then shows a green tick beside the words "did not run" — which
    is the dashboard contradicting itself. A pipeline that exits 0 while
    reporting ok:false did nothing wrong AND did nothing: that is 'idle', its
    own state, so a day where four sectors sat out cannot be read as a full day.
    """
    if code != 0:
        return "failed"
    obj = _as_json((out or "").strip())
    if isinstance(obj, dict) and obj.get("ok") is False:
        return "idle"
    return "ok"


class _StopWatch:
    """
    One thread asking the cabinet 'has he pressed Stop yet?', so the lanes
    don't have to.

    Three lanes each polling the database once a second is three times the
    reads for the same answer, and the answer is the same for all of them. One
    watcher, one Event, and every other thread reads a boolean in memory.

    is_set() is also what a lane checks before starting its next step, so a
    stop pressed during a long step takes effect the moment that step ends
    even if the kill somehow missed.
    """

    def __init__(self, scope=LOCK, every=STOP_POLL):
        self.scope, self.every = scope, every
        self.event = threading.Event()
        self.by, self.why = "", ""
        self._quit = threading.Event()
        self._t = None

    def _loop(self):
        while not self._quit.wait(self.every):
            try:
                r = board.stop_requested(self.scope)
            except Exception:
                continue          # a cabinet hiccup must not stop the day
            if r:
                self.by = r.get("asked_by") or "owner"
                self.why = r.get("why") or ""
                self.event.set()
                return

    def __enter__(self):
        self._t = threading.Thread(target=self._loop, name="stop-watch", daemon=True)
        self._t.start()
        return self

    def __exit__(self, *_):
        self._quit.set()
        if self._t:
            self._t.join(timeout=2)
        return False

    def is_set(self):
        return self.event.is_set()

    def interrupt(self, why):
        """Ctrl+C in the window the run was started from: a Stop, not a crash."""
        if not self.event.is_set():
            self.by, self.why = "owner", why
            self.event.set()

    def note(self):
        who = "you" if (self.by or "owner") == "owner" else self.by
        return f"stopped by {who}" + (f" — {self.why}" if self.why else "")


def _end_child(p):
    """TERM, then KILL. A step that handles the first can tidy up; one that is
    wedged gets the second, and the run is not held hostage either way."""
    try:
        p.terminate()
    except Exception:
        return
    try:
        p.wait(timeout=CHILD_GRACE)
        return
    except Exception:
        pass
    try:
        p.kill()
        p.wait(timeout=CHILD_GRACE)
    except Exception:
        pass


def _one_step(rid, seq, stop=None) -> str:
    """
    Run one step and record what it did. Returns its verdict.

    Everything a step needs is already in STEPS and in the cabinet, and the
    only thing it hands back is a verdict — so this is safe to call from
    several threads at once. The two writes it makes are a single-row UPDATE
    and an atomic increment, both of which SQLite serialises for us.

    WHY Popen AND A TEMP FILE, AND NOT subprocess.run.

    subprocess.run blocks until the child is finished, which is exactly what
    the Stop button cannot afford: a step is allowed seven minutes, and he
    should not have to wait out the seventh. So the child is opened, then
    waited on in short hops, checking the stop flag between them.

    Its output goes to a temporary file rather than a pipe on purpose. A pipe
    holds about 64KB before it fills, and a child writing more than that into a
    full pipe blocks for ever while we sit in wait() — a deadlock that only
    appears on the chattiest steps, which is the worst way to find one. A file
    cannot fill.
    """
    label, mod, room, _lane = STEPS[seq]
    return _work_step(rid, seq, mod, room, stop)


def _work_step(rid, seq, mod, room, stop=None, args=()) -> str:
    """One step, from STEPS or from an extra round: run it and write down how it went."""
    # THE OWNER'S OFF SWITCH. A sector he has stopped does not run, and the run
    # panel says so in as many words — a step that silently vanished would look
    # like the day was shorter than it was, and he would have no way to tell
    # "switched off" from "never got to it".
    if not board.sector_enabled(room):
        _step(rid, seq, status="skipped", started_at=_now(), ended_at=_now(),
              code=0, summary=f"{room} is switched off — you turned this "
                              "sector off, so it did no work today")
        _tick(rid)
        return "skipped"
    only = _only_for(rid)
    if only and room not in only:
        _step(rid, seq, status="skipped", started_at=_now(), ended_at=_now(),
              code=0, summary=f"{room} is not in this run — you picked "
                              f"{', '.join(only)} this time, so it spent nothing")
        _tick(rid)
        return "skipped"

    _step(rid, seq, status="running", started_at=_now())
    code, out, cut_short = _run_child(mod, stop, args)
    if cut_short:
        _step(rid, seq, status="stopped", ended_at=_now(), code=code,
              summary=(stop.note() if stop else "stopped")
                      + " — this step was cut off part-way through")
        _tick(rid)
        return "stopped"
    verdict = _verdict(out, code)
    _step(rid, seq, status=verdict, ended_at=_now(),
          code=code, summary=_summarise(out, code))
    _tick(rid)
    return verdict


def _run_child(mod, stop=None, args=()):
    """Start the step, wait on it in short hops, return (code, output, cut_short)."""
    try:
        # Output to a file, decoded here rather than by the child pipe. encoding
        # is named and never left to the machine: the locale codepage on Windows
        # is cp1252/cp1256, and every pipeline in this program prints em-dashes
        # and readiness lamps. A step that worked perfectly would come back as
        # mojibake, or take the run down with a UnicodeDecodeError. See
        # coordination/console.py.
        sink = tempfile.TemporaryFile()
    except Exception as e:                               # pragma: no cover
        return 1, str(e), False
    try:
        try:
            # Its own process group, and no window: see console.background_flags.
            p = subprocess.Popen([PY, "-m", mod, *args], cwd=str(ROOT),
                                 stdout=sink, stderr=subprocess.STDOUT,
                                 creationflags=console.background_flags())
        except Exception as e:                           # pragma: no cover
            return 1, str(e), False

        budget = step_timeout_for(mod)
        deadline = time.time() + budget
        cut_short = False
        while True:
            try:
                code = p.wait(timeout=0.4)
                break
            except subprocess.TimeoutExpired:
                pass
            if stop is not None and stop.is_set():
                _end_child(p)
                code, cut_short = (p.returncode if p.returncode is not None
                                   else -1), True
                break
            if time.time() >= deadline:
                _end_child(p)
                code = 124
                break

        try:
            sink.seek(0)
            out = sink.read().decode("utf-8", "replace")
        except Exception:                                # pragma: no cover
            out = ""
        if code == 124 and not cut_short:
            # The timeout has to LEAD, or _summarise reads the step's own last
            # cheerful line and the run panel says it finished.
            tail = [l for l in out.splitlines() if l.strip()][-1:]
            out = (f"timed out after {budget}s"
                   + (f" — last said: {tail[0].strip()[:120]}" if tail else ""))
        return code, out, cut_short
    finally:
        try:
            sink.close()
        except Exception:
            pass


def _tick(rid):
    with sqlite3.connect(board.DB, timeout=30) as c:
        c.execute("UPDATE day_runs SET steps_done=steps_done+1 WHERE id=?", (rid,))


def lanes() -> dict:
    """{lane: [step index, ...]} in the order the steps are listed."""
    out = {}
    for i, (_label, _mod, _room, lane) in enumerate(STEPS):
        out.setdefault(lane, []).append(i)
    return out


def what_is_left() -> dict:
    """
    What the day has not finished yet, read from the cabinet. Nothing written,
    no model.

      jobs          open, green jobs on any board — not his own to-dos, and not
                    the Overseer's fault tickets, which IT's self-repair settles
      leads_short   how many leads with an email his leads target still wants
      approved      changes he said yes to that are not on his site yet
    """
    board.ensure_schema()
    out = {"jobs": [], "leads_short": 0, "leads_asked": 0, "approved": 0}
    try:
        with sqlite3.connect(board.DB, timeout=30) as c:
            c.row_factory = sqlite3.Row
            out["jobs"] = [dict(r) for r in c.execute(
                "SELECT id, sector, title, desk FROM tasks WHERE status='open'"
                " AND COALESCE(risk,'green')='green'"
                " AND COALESCE(about,'') NOT LIKE 'faults:%' ORDER BY created_at")]
            out["approved"] = c.execute(
                "SELECT count(*) FROM site_queue WHERE status='approved'").fetchone()[0]
    except sqlite3.Error:
        pass
    try:
        from coordination import targets
        for j in targets.status().get("jobs") or []:
            if j.get("name") == "leads" and j.get("per_day"):
                out["leads_short"] = int(j.get("remaining") or 0)
                out["leads_asked"] = int(j.get("per_day") or 0)
    except Exception:
        pass
    return out


def _steps_for(job) -> tuple:
    """The steps that work this job: its desk's, or the sector's if no desk is named.
    A desk no step works (the reply handler, the reporter) gets none — running
    the collectors for a job they are not allowed to take would move nothing."""
    desk = (job.get("desk") or "").strip().lower()
    if desk:
        return DESK_STEPS.get(desk, ())
    return SECTOR_STEPS.get(job.get("sector"), ())


def _rooms_in(left) -> set:
    """The sectors with something an extra round could work on."""
    rooms = {j["sector"] for j in left.get("jobs") or [] if _steps_for(j)}
    if left.get("leads_short"):
        rooms.add("Leads")
    if left.get("approved"):
        rooms.add("IT")
    return rooms


def round_plan(left, rooms) -> dict:
    """{chain: [module, ...]} for one extra round, limited to `rooms`."""
    want = {}

    def add(chain, mods):
        lst = want.setdefault(chain, [])
        lst.extend(m for m in mods if m not in lst)

    for j in left.get("jobs") or []:
        room = j.get("sector")
        if room not in rooms:
            continue
        mods = _steps_for(j)
        if mods:
            add(CHAIN_OF.get(room, str(room).lower()), mods)
    if "Leads" in rooms and left.get("leads_short"):
        add("leads", ("pipelines.leads_hunt", "pipelines.leads_osm"))
    if "IT" in rooms and left.get("approved"):
        add("it", ("pipelines.it_release",))
    # New leads are screened before anyone is written to — as in the main pass.
    if want.get("leads"):
        add("leads", ("pipelines.security_screen", "pipelines.leads_outreach"))
    # What the Fixer queues goes on only with his yes; the Releaser carries out
    # any yes he has already given, in the same round.
    if "pipelines.it_fix" in want.get("it", []):
        add("it", ("pipelines.it_release",))
    order = {mod: i for i, (_l, mod, _r, _ln) in enumerate(STEPS)}
    order["pipelines.security_screen"] = 900
    order["pipelines.leads_outreach"] = 901
    return {k: sorted(v, key=lambda m: order.get(m, 999)) for k, v in want.items() if v}


def _got_through(since) -> dict:
    """
    {sector: how much it has got through since `since`} — the number a round
    has to move for its sector to be worth another one.

    Jobs settled count for everyone, blocked ones included: a job closed with
    its reason is off the board, and the next round works the one behind it.
    Each sector's own output counts too, because a collector that stores twelve
    leads has moved even if its job stays open for the rest.
    """
    out = {}
    extra = {
        "Leads": ["SELECT count(*) FROM leads WHERE found_at>=?"
                  " AND COALESCE(email,'')!=''",
                  "SELECT count(*) FROM outreach_log WHERE direction='sent' AND at>=?"],
        "IT": ["SELECT count(*) FROM site_queue WHERE agent LIKE 'it-%' AND queued_at>=?",
               "SELECT count(*) FROM site_queue WHERE status='applied' AND applied_at>=?"],
        "Content": ["SELECT count(*) FROM content_queue WHERE created_at>=?"],
    }
    try:
        with sqlite3.connect(board.DB, timeout=30) as c:
            for sector, n in c.execute(
                    "SELECT sector, count(*) FROM tasks WHERE finished_at>=?"
                    " AND status IN ('done','blocked','failed') GROUP BY sector",
                    (since,)):
                out[sector] = int(n or 0)
            for sector, qs in extra.items():
                for q in qs:
                    try:
                        out[sector] = out.get(sector, 0) + int(
                            c.execute(q, (since,)).fetchone()[0] or 0)
                    except sqlite3.Error:
                        pass
    except sqlite3.Error:
        pass
    return out


def _label_of(mod) -> str:
    for label, m, _room, _lane in STEPS:
        if m == mod:
            return label
    return mod.rsplit(".", 1)[-1].replace("_", " ")


def _room_of(mod) -> str:
    for _label, m, room, _lane in STEPS:
        if m == mod:
            return room
    return "Director"


def _add_rows(rid, rows) -> list:
    """Write extra steps onto the run, 'waiting'. rows: [(label, module)]. Returns seqs."""
    seqs = []
    with sqlite3.connect(board.DB, timeout=30) as c:
        top = c.execute("SELECT MAX(seq) FROM day_steps WHERE run_id=?", (rid,)).fetchone()[0]
        nxt = max(FOLLOW_UP_SEQ, int(top or 0) + 1)
        for label, mod in rows:
            c.execute("INSERT INTO day_steps(run_id,seq,label,module,status,lane)"
                      " VALUES(?,?,?,?, 'waiting', ?)",
                      (rid, nxt, label, mod, FOLLOW_UP_LANE))
            seqs.append(nxt)
            nxt += 1
        c.execute("UPDATE day_runs SET steps_total=steps_total+? WHERE id=?",
                  (len(rows), rid))
    return seqs


def _hold_the_day(rid):
    """Keep the day's lock for as long as the day is honestly still going."""
    try:
        with sqlite3.connect(board.DB, timeout=30) as c:
            c.execute("UPDATE locks SET expires_at=? WHERE resource=? AND holder=?",
                      (_now() + 3 * 3600, LOCK, LOCK_HOLDER))
    except sqlite3.Error:
        pass


def _still_open(left, stopped_rooms, only) -> list:
    """What is left at the end, one plain line each, with the reason where known."""
    lines = []
    by_room = {}
    for j in left.get("jobs") or []:
        by_room.setdefault(j["sector"], []).append(j["title"])
    for room, titles in sorted(by_room.items()):
        why = stopped_rooms.get(room) or ""
        if only and room not in only:
            why = "not in this run"
        elif not board.sector_enabled(room):
            why = "you have this sector switched off"
        lines.append(f"{room}: {len(titles)} job(s) still open — "
                     + "; ".join(t[:70] for t in titles[:3])
                     + (f" ({why})" if why else ""))
    if left.get("leads_short"):
        lines.append(f"Leads: {left['leads_short']} of the {left['leads_asked']} leads "
                     "with an email you asked for are still to find"
                     + (f" ({stopped_rooms['Leads']})" if stopped_rooms.get("Leads") else ""))
    try:
        with sqlite3.connect(board.DB, timeout=30) as c:
            waiting = c.execute("SELECT count(*) FROM site_queue WHERE status='pending'"
                                ).fetchone()[0]
    except sqlite3.Error:
        waiting = 0
    if waiting:
        lines.append(f"{waiting} website change(s) wait for your yes under Waiting on "
                     "you — nothing goes on your site without it")
    if left.get("approved"):
        lines.append(f"{left['approved']} change(s) you approved are not on your site yet"
                     + (f" ({stopped_rooms['IT']})" if stopped_rooms.get("IT") else ""))
    return lines


def _keep_going(rid, stop=None, slots=None) -> dict:
    """
    The extra rounds. See KEEP GOING at the top of this file. Returns what they
    did and what is still open, for the run's closing words.
    """
    stopping = (lambda: bool(stop is not None and stop.is_set()))
    slots = slots or threading.Semaphore(max(1, int(MAX_LANES)))
    only = _only_for(rid)
    t0 = time.time()
    in_play, gave_up, rounds, why = None, {}, 0, ""
    while True:
        if stopping():
            why = "you pressed Stop"
            break
        left = what_is_left()
        workable = {r for r in _rooms_in(left)
                    if board.sector_enabled(r) and (not only or r in only)}
        rooms = workable & in_play if in_play is not None else workable
        if not rooms:
            why = ("everything you asked for today is done or waiting on you"
                   if not workable else
                   "the last round moved nothing more, so running it again would "
                   "only spend tokens to say the same thing")
            break
        if rounds >= FOLLOW_UP_ROUNDS:
            why = f"{FOLLOW_UP_ROUNDS} extra rounds is the most one day takes"
            break
        if time.time() - t0 >= FOLLOW_UP_BUDGET:
            why = (f"the extra rounds used their {FOLLOW_UP_BUDGET // 60} minutes — "
                   "the rest carries on tomorrow, or press Resume")
            break
        plan = round_plan(left, rooms)
        if not plan:
            why = "nothing left that a desk can work on today"
            break
        rounds += 1
        _hold_the_day(rid)
        since = _now()

        # The MD first, when a counted target is short: the job it posted this
        # morning was closed with part of the number, and the collectors need
        # one that says what is still to go. Its own step, so the run panel
        # shows the MD asking rather than work appearing from nowhere.
        if "Leads" in rooms and left.get("leads_short"):
            seq = _add_rows(rid, [(f"Round {rounds + 1} · The MD asks for the "
                                   f"{left['leads_short']} leads still to find",
                                   "pipelines.md_cycle")])[0]
            with slots:
                _work_step(rid, seq, "pipelines.md_cycle", "Director", stop,
                           args=("--top-up", "--json"))

        chains = {name: _add_rows(rid, [(f"Round {rounds + 1} · {_label_of(m)}", m)
                                        for m in mods])
                  for name, mods in plan.items()}
        mods_of = {seq: m for name, mods in plan.items()
                   for seq, m in zip(chains[name], mods)}

        def walk(seqs):
            for seq in seqs:
                if stopping():
                    _step(rid, seq, status="stopped", ended_at=_now(),
                          summary=(stop.note() if stop else "stopped")
                                  + " — the run was stopped before this started")
                    _tick(rid)
                    continue
                with slots:
                    if stopping():
                        _step(rid, seq, status="stopped", ended_at=_now(),
                              summary=(stop.note() if stop else "stopped")
                                      + " — the run was stopped before this started")
                        _tick(rid)
                        continue
                    _work_step(rid, seq, mods_of[seq], _room_of(mods_of[seq]), stop)

        threads = [threading.Thread(target=walk, args=(seqs,), name=f"more-{n}",
                                    daemon=True) for n, seqs in chains.items()]
        for t in threads:
            t.start()
        for t in threads:
            while t.is_alive():
                t.join(timeout=0.5)

        moved = _got_through(since)
        in_play = {r for r in rooms if moved.get(r, 0) > 0}
        for r in rooms - in_play:
            gave_up[r] = (f"round {rounds + 1} moved nothing — see its steps above "
                          "for what it said")
    left = what_is_left()
    return {"rounds": rounds, "why": why, "left": _still_open(left, gave_up, only),
            "minutes": round((time.time() - t0) / 60, 1)}


def _run_lanes(rid, stop=None, keep_going=False, report=None):
    """
    Run the day. Lanes side by side, steps within a lane one after another.

    One thread per lane, and the thread does nothing but wait for the lanes it
    depends on and then walk its own steps. The actual work is a subprocess, so
    the GIL is not in the picture — the threads are just a way of waiting on
    several `subprocess.run` calls at once.

    MAX_LANES is held as a semaphore around the subprocess itself rather than
    around the lane. A lane that is only WAITING must not hold a slot, or two
    waiting lanes could between them stop the lane they are waiting for from
    ever starting — a deadlock with a plausible-looking cause, which is the
    worst kind.

    STOPPING. Three places a lane can be when he presses Stop, and all three
    have to notice:

      waiting on a lane it depends on   the wait is in short hops now, not one
                                        long one, so it drops out inside a
                                        second instead of at the seven-minute
                                        ceiling
      queued for one of the MAX_LANES   checked again after the slot is taken;
                                        the step never starts
      running a step                    _one_step kills the child

    Whatever it was doing, the steps it has not run are written down as
    'stopped' rather than left as 'waiting'. A waiting row is a promise the run
    is still going to get to it, and the run is over.

    Returns (steps that failed, was it stopped).
    """
    plan = lanes()
    done = {name: threading.Event() for name in plan}
    slots = threading.Semaphore(max(1, int(MAX_LANES)))
    failed = []
    lock = threading.Lock()
    stopping = (lambda: bool(stop is not None and stop.is_set()))

    pre = _preskipped(rid)

    def walk(name):
        try:
            for dep in LANE_AFTER.get(name, ()):
                ev = done.get(dep)
                if ev is None:
                    continue
                # Bounded, so a lane that somehow never finishes cannot hang
                # the day for ever. The ceiling is what its steps are allowed
                # to take, plus room to be queued behind others — but waited
                # for in short hops, so a Stop is not sat out to the ceiling.
                until = time.time() + longest_step() * (len(plan[dep]) + 2)
                while not ev.wait(timeout=0.5):
                    if stopping() or time.time() >= until:
                        break
            # THE EXTRA ROUNDS, once everything else has had its pass and
            # before the steps that are about the whole day. See KEEP GOING.
            if keep_going and name == FOLLOW_UP_BEFORE and not stopping():
                try:
                    more = _keep_going(rid, stop, slots)
                except Exception as e:
                    more = {"rounds": 0, "left": [],
                            "why": f"the extra rounds could not run: {e}"}
                if report is not None:
                    report["more"] = more
            for seq in plan[name]:
                if seq in pre:
                    continue              # a resume settled it at the start
                if stopping():
                    _step(rid, seq, status="stopped", ended_at=_now(),
                          summary=(stop.note() if stop else "stopped")
                                  + " — the run was stopped before this started")
                    _tick(rid)
                    continue
                with slots:
                    if stopping():
                        _step(rid, seq, status="stopped", ended_at=_now(),
                              summary=(stop.note() if stop else "stopped")
                                      + " — the run was stopped before this started")
                        _tick(rid)
                        continue
                    verdict = _one_step(rid, seq, stop)
                if verdict == "failed":
                    with lock:
                        failed.append(seq)
        finally:
            # ALWAYS set, even if the lane threw. A lane that dies holding its
            # event closed takes every lane behind it down with it, and the
            # owner sees a run that stopped for no stated reason.
            done[name].set()

    threads = [threading.Thread(target=walk, args=(n,), name=f"lane-{n}",
                                daemon=True) for n in plan]
    for t in threads:
        t.start()
    restore = _ctrl_c_is_a_stop(stop)
    try:
        for t in threads:
            while t.is_alive():
                t.join(timeout=0.5)
    finally:
        restore()
    return len(failed), stopping()


def _ctrl_c_is_a_stop(stop):
    """
    While the lanes run, Ctrl+C in the terminal presses Stop: each lane ends
    its running step and writes the rest down as stopped, and the day reads
    'stopped'. Before, the KeyboardInterrupt tore the run down with its steps
    still going and marked the day 'failed' — and on Windows it also landed in
    every step sharing the window, which the floor read as crashes.

    A handler rather than catching KeyboardInterrupt round join(): an
    interrupted join() can leave the thread reporting itself finished while it
    is still running, and the run would return with a step left 'running'.
    Only the main thread may set one; anywhere else this does nothing.
    Returns the function that puts the old handler back.
    """
    import signal
    if stop is None or threading.current_thread() is not threading.main_thread():
        return lambda: None

    def on_ctrl_c(_signum, _frame):
        stop.interrupt("Ctrl+C in the window the run was started from")

    try:
        old = signal.signal(signal.SIGINT, on_ctrl_c)
    except (ValueError, OSError):
        return lambda: None
    return lambda: signal.signal(signal.SIGINT, old)


def start(orders=None, note="", force=False, quick=False, skip_check=False,
          only=None, resume=False) -> dict:
    """
    Check, take the owner's instructions, then work. Returns when the day is done.

    `orders` is {sector: what the owner wants today}. It is written to the board
    BEFORE the MD plans, which is the whole point — an order that arrives after
    the plan is written is an order the MD never saw.

    Every run clears up what the last one left first (leftovers.tidy — no
    model). `resume` then runs only what was left: see RESUME above.
    """
    pre = {"ready": True, "blocking": [], "warnings": []} if skip_check \
        else preflight(quick=quick)
    if not pre["ready"] and not force:
        return {"ok": False, "ran": False, "refused": True, "preflight": pre,
                "why": ("The day was not started. " + "; ".join(pre["blocking"][:3])
                        + ". Fix these, or press Run again with 'run anyway'.")}

    if not board.acquire_lock(LOCK, LOCK_HOLDER, wait=False, ttl=3 * 3600):
        # IS THE HOLDER ACTUALLY ALIVE? Asking status() runs the reaper, which
        # settles it from progress and clears the lock if the run is a corpse.
        # Without this the owner is told "a day is already running" by a day
        # that died hours ago, and his only options are waiting out a
        # three-hour lease or editing the database.
        st = status()
        stale = (st.get("run") or {}).get("status") in ("stalled", None)
        if stale and board.acquire_lock(LOCK, LOCK_HOLDER, wait=False, ttl=3 * 3600):
            pass                          # took it over; the last run was dead
        else:
            live = (st.get("run") or {}).get("id", "")
            return {"ok": False, "ran": False, "busy": True,
                    "why": ("A day is already running"
                            + (f" ({st.get('head')})" if st.get("head") else "")
                            + ". Wait for it to finish — two runs at once means "
                              "two MDs planning the same day. If you are sure "
                              "nothing is running, clear it with: "
                              "python -m pipelines.day_run --unlock"),
                    "run_id": live}

    # CLEAR UP FIRST. Dead claims back on the board, copies folded, chases and
    # hand-offs answered, orphans closed — all without a model, and before any
    # desk starts, so no desk spends a call on garbage. It never stops the day.
    from coordination import leftovers
    try:
        tidied = leftovers.tidy()
        tidied = {"head": tidied.get("head") or tidied.get("error") or "",
                  "done": tidied.get("done") or [], "counts": tidied.get("counts") or {}}
    except Exception as e:
        tidied = {"head": f"the clear-up could not run: {e}", "done": [], "counts": {}}

    skip, resume_of = None, None
    if resume:
        prev = leftovers.last_run()
        rooms = leftovers.rooms_with_work()
        if not only and prev.get("id"):
            only = _only_for(prev["id"])    # the sectors he picked last time
        skip = resume_plan(prev, rooms)
        runnable = [i for i in range(len(STEPS)) if i not in skip
                    and STEPS[i][1] not in ALWAYS_ON_RESUME]
        if not runnable:
            board.release_lock(LOCK, LOCK_HOLDER)
            return {"ok": True, "ran": False, "nothing_to_resume": True,
                    "tidied": tidied,
                    "why": ("Nothing to pick up: the last run finished and no sector "
                            "has work waiting. " + (tidied.get("head") or "")
                            + " Press Run the day to start a new one.").strip()}
        resume_of = prev.get("id")

    # The owner's words go on the board FIRST, so the MD plans around them
    # rather than over them. An order for a sector that does not exist is
    # reported back, never swallowed — he asked for something and is entitled
    # to know it did not land.
    written, rejected = {}, {}
    for sec, text in (orders or {}).items():
        if not (text or "").strip():
            continue
        try:
            proposals.set_orders(sec, text)
            written[proposals.canonical_sector(sec)] = text.strip()
        except ValueError as e:
            rejected[str(sec)[:40]] = str(e)

    # A stop left standing by a program that was closed part-way through one
    # would kill this run in its first second, and he would press Run again and
    # again and watch nothing happen. The flag means "stop the run that is
    # going now"; there isn't one yet.
    board.clear_stop(LOCK)

    only = pick_sectors(only)
    rid = _open_run(note, force and not pre["ready"], pre.get("blocking"), only,
                    skip=skip, resume_of=resume_of, tidied=tidied)
    stopped = False
    report = {}
    try:
        with _StopWatch() as watch:
            failed, stopped = _run_lanes(rid, watch, keep_going=True, report=report)
            stopped_note = watch.note() if stopped else ""
        more = report.get("more") or {}
        if more:
            _left_open(rid, more)
        # A step that fails is not a day that failed. IT going down should not
        # stop Report scoring what Leads did — so every step runs, and the
        # verdict at the end says how many did not. A day he STOPPED is not a
        # failure either, and calling it one would put it in the Overseer's
        # sights as something to investigate.
        _close_run(rid, "stopped" if stopped else ("done" if not failed else "partial"))
    except BaseException:
        _close_run(rid, "failed")
        raise
    finally:
        board.release_lock(LOCK, LOCK_HOLDER)
        board.clear_stop(LOCK)

    if stopped:
        # The children were killed mid-task, so their agent_runs rows still say
        # 'running' and their locks are still held. This is the same tidy-up the
        # sweep does after a closed program, and the reason the Overseer will
        # not spend tomorrow morning filing faults about agents that stopped
        # because he told them to.
        swept = board.sweep_dead_processes()
        return {"ok": True, "ran": True, "stopped": True, "run_id": rid,
                "orders": written, "rejected_orders": rejected,
                "failed_steps": failed, "swept": swept, "preflight": pre,
                "why": f"The day was {stopped_note}. "
                       "Nothing further was started, and what was mid-task has "
                       "been closed off. Press Run when you want it to go again."}

    # Anything the day turned up that the owner has to answer goes to his phone
    # now, not into a table he has to remember to open. Only things that stop
    # the agency or involve a customer — see coordination/notify.py for the rule
    # about not sending progress. Best-effort: a notification that fails must
    # never turn a finished day into a failed one.
    pushed = {}
    try:
        from coordination import notify
        pushed = notify.send()
    except Exception as e:
        pushed = {"ok": False, "error": str(e)}

    total = (status(rid).get("progress") or [0, len(STEPS)])[1] or len(STEPS)
    why = ("The day ran clean." if not failed
           else f"The day ran; {failed} of {total} steps failed.")
    if more.get("rounds"):
        why += (f" It went round {more['rounds']} more time(s) for the work still "
                f"open, and stopped because {more.get('why') or 'it was done'}.")
    elif more.get("why"):
        why += f" No extra round was needed: {more['why']}."
    if more.get("left"):
        why += " Still open: " + " · ".join(more["left"][:5]) + "."
    if resume_of:
        why = ("Picked up where the last run left off — "
               f"{len(STEPS) - len(skip or {})} step(s) ran, the rest had already "
               "finished. " + why)
    if tidied.get("done"):
        why += " " + tidied["head"]
    if rejected:
        why += (" Your order for " + ", ".join(rejected)
                + " went nowhere — " + list(rejected.values())[0])
    if pushed.get("sent"):
        why += f" {pushed['sent']} thing(s) sent to your phone."
    return {"ok": True, "ran": True, "run_id": rid, "orders": written,
            "rejected_orders": rejected, "failed_steps": failed,
            "notified": pushed.get("sent", 0), "preflight": pre, "why": why,
            "resumed_from": resume_of, "tidied": tidied, "more": more}


# ---------------------------------------------------------------------------
# WHAT THE UI POLLS WHILE IT RUNS
# ---------------------------------------------------------------------------

def _reap(c, run, steps) -> bool:
    """
    Is this 'running' row actually a corpse?

    Nothing marks a run failed when the process is killed outright — no
    finally block runs on SIGKILL, on a reboot, or when the container goes
    away. Left alone, the office shows "running" for ever, the Run button
    stays greyed out, and the owner has no way back except deleting rows.

    PROGRESS IS THE SIGNAL, AND IT USED TO BE THE BACKSTOP TO ONE THAT DEADLOCKED.

    This used to treat the lock as the strong signal: nobody holding 'day_run'
    means the process is gone. True, and useless, because the lock outlives the
    process by its own three-hour TTL. So a run killed at 09:00 left the lock
    held until noon; the reaper refused to reap because the lock was held, and
    start() refused to start because the lock was held. The owner pressed Run
    and was told "a day is already running" by a day that had died hours
    earlier, with no way out but waiting.

    Progress alone settles it. A live run moves a step — and now that lanes run
    side by side, several at once — so nothing having moved in longer than a
    single step is allowed to take is not slow, it is dead. The lock is then a
    thing to CLEAN UP rather than a thing to consult.

    AND PROGRESS ALONE WAS WRONG, WHICH IS THE BUG HE HIT.

    "one major bug i found when the program was working and i opened the full
     control room section and when i get back to office the progarm had stoped
     working"

    It had not stopped. This reaped it. Every screen that asks for the run's
    status calls status(), status() calls this, and this declared the run dead
    on a stopwatch without ever asking the machine whether the process was
    still there. His own log from the same day says why the stopwatch ran out:
    "SEO builds the keyword picture — timed out after 420s" and "IT RELEASER —
    the test suite did not finish inside 360s". A step is allowed to take seven
    minutes and writes nothing while it does. Nine minutes of honest work by a
    perfectly healthy run, one page load, and the row was flipped to 'stalled',
    the live steps marked failed, and the door unlocked underneath a process
    that was still running.

    So the stopwatch is now only the thing that makes it WORTH ASKING. The
    answer comes from the operating system: board.process_alive, which is
    careful in the right direction — a pid on another machine, a pid we cannot
    place, anything it is not sure about counts as alive, because killing the
    books on a live run is far worse than showing 'running' for a few minutes
    longer.

    A row with no pid at all is an old one, from before the column was written.
    There is nothing to ask about, so those keep the old behaviour — on a much
    longer fuse, because a stopwatch is all they have.
    """
    now = _now()
    last = max([run["started_at"] or 0]
               + [s["ended_at"] or s["started_at"] or 0 for s in steps])
    if now - last <= longest_step() + 120:
        return False

    pid = run["pid"] if "pid" in run.keys() else None
    if pid:
        if board.process_alive(pid, (run["host"] if "host" in run.keys() else "") or ""):
            # Working, quietly, on something long. Not a corpse.
            return False
    elif now - last <= NO_PID_GRACE:
        # Nothing to ask. Give it a great deal of rope before calling it.
        return False
    c.execute("UPDATE day_runs SET status='stalled', ended_at=? WHERE id=? AND status='running'",
              (last, run["id"]))
    c.execute("UPDATE day_steps SET status='failed', ended_at=?,"
              " summary=COALESCE(summary,'the run was killed before this step finished')"
              " WHERE run_id=? AND status='running'", (last, run["id"]))
    # And let go of the door on the way out. The run held 'day_run' for its
    # whole life and died without releasing it; leaving it there is what locked
    # the owner out of his own agency for the rest of the lease.
    c.execute("DELETE FROM locks WHERE resource=? AND holder=?", (LOCK, LOCK_HOLDER))
    return True


def status(run_id=None) -> dict:
    board.ensure_schema()
    try:
        with sqlite3.connect(board.DB, timeout=30) as c:
            c.row_factory = sqlite3.Row
            r = (c.execute("SELECT * FROM day_runs WHERE id=?", (run_id,)).fetchone()
                 if run_id else
                 c.execute("SELECT * FROM day_runs ORDER BY started_at DESC LIMIT 1").fetchone())
            if not r:
                return {"ok": True, "run": None, "steps": [],
                        "head": "the agency has never been run from the button"}
            # The closing Overseer and Report run after any extra rounds, so
            # they are listed after them too, whatever their numbers say.
            q = ("SELECT seq,label,module,status,started_at,ended_at,code,summary,lane"
                 " FROM day_steps WHERE run_id=?"
                 " ORDER BY CASE WHEN lane='close' THEN 1 ELSE 0 END, seq")
            steps = [dict(s) for s in c.execute(q, (r["id"],))]
            if r["status"] == "running" and _reap(c, r, steps):
                r = c.execute("SELECT * FROM day_runs WHERE id=?", (r["id"],)).fetchone()
                steps = [dict(s) for s in c.execute(q, (r["id"],))]
    except Exception as e:
        return {"ok": False, "error": str(e)}

    run = dict(r)
    try:
        run["blocking"] = json.loads(run.get("blocking") or "[]")
    except Exception:
        run["blocking"] = []
    try:
        run["only_sectors"] = json.loads(run.get("only_sectors") or "[]")
    except Exception:
        run["only_sectors"] = []
    try:
        run["tidied"] = json.loads(run.get("tidied") or "null")
    except Exception:
        run["tidied"] = None
    try:
        run["left_open"] = json.loads(run.get("left_open") or "null")
    except Exception:
        run["left_open"] = None
    # How much Claude this run used, per sector, from Claude's own report of
    # each call — so "running only Content saves tokens" is something he can
    # see rather than take on trust.
    try:
        from providers import claude_team
        run["claude_usage"] = claude_team.usage_since(run.get("started_at") or 0,
                                                      run.get("ended_at") or None)
    except Exception:
        run["claude_usage"] = {}
    # PLURAL, now that lanes run side by side. Reading the first 'running' row
    # and calling that "what the agency is doing" was honest when the day was
    # one queue; with three lanes going it would name one of them and hide the
    # other two, which reads as a slower day than is actually happening.
    now_on = [s for s in steps if s["status"] == "running"]
    live = now_on[0] if now_on else None
    # SETTLED, not 'succeeded'. A step that was skipped because he switched the
    # sector off, or stopped because he pressed Stop, is never going to run
    # again — leaving it out of the count left the progress bar stuck at 80%
    # on a day that was completely over.
    done = sum(1 for s in steps
               if s["status"] not in ("waiting", "running", None, ""))
    bad = sum(1 for s in steps if s["status"] == "failed")
    sat_out = sum(1 for s in steps if s["status"] == "idle")
    cut = sum(1 for s in steps if s["status"] == "stopped")
    worked = sum(1 for s in steps if s["status"] == "ok")
    run["running"] = run["status"] == "running"
    # Has he pressed Stop and is the run still putting its tools down? That is
    # a state of its own and the button must say so, or he presses it again and
    # again in the seconds it takes the step in flight to die.
    pending_stop = None
    if run["running"]:
        try:
            pending_stop = board.stop_requested(LOCK)
        except Exception:
            pending_stop = None
    if run["status"] == "stopped":
        head = (f"you stopped this run — {worked} step"
                f"{'s' if worked != 1 else ''} had finished, "
                f"{cut} did not run.")
    elif pending_stop:
        head = ("stopping — waiting for what is mid-task to put its tools down"
                + (f" ({len(now_on)} still going)" if now_on else ""))
    elif run["status"] == "stalled":
        head = ("the run was killed part-way through — "
                f"{done} of {len(steps)} steps finished. Press Run again.")
    elif len(now_on) > 1:
        head = (live["label"] + f", and {len(now_on) - 1} other"
                + ("s" if len(now_on) > 2 else "") + " at the same time…")
    elif live:
        head = live["label"] + "…"
    elif run["status"] not in ("done", "partial", "failed"):
        head = run["status"]
    elif bad:
        head = f"finished — {bad} step{'s' if bad != 1 else ''} failed"
    elif sat_out:
        head = f"finished — {sat_out} sector{'s' if sat_out != 1 else ''} had nothing to do"
    else:
        head = "finished clean"
    return {"ok": True, "run": run, "steps": steps,
            "doing": live["label"] if live else None,
            "doing_now": [s["label"] for s in now_on],
            "progress": [done, len(steps)], "idle": sat_out, "failed": bad,
            "stopped_steps": cut,
            "stoppable": bool(run["running"]) and not pending_stop,
            "stopping": bool(pending_stop),
            "head": head}


def _drop_day_lock() -> bool:
    """Let go of the day lock. True if one was actually there."""
    with sqlite3.connect(board.DB, timeout=30) as c:
        held = c.execute("SELECT 1 FROM locks WHERE resource=?", (LOCK,)).fetchone()
        c.execute("DELETE FROM locks WHERE resource=?", (LOCK,))
    return bool(held)


def _run_row(run_id):
    with sqlite3.connect(board.DB, timeout=30) as c:
        c.row_factory = sqlite3.Row
        r = c.execute("SELECT id,status,pid,host,steps_total FROM day_runs"
                      " WHERE id=?", (run_id,)).fetchone()
        return dict(r) if r else None


def _force_stopped(run_id, why):
    """Close a run that would not close itself, and let go of everything it held."""
    now = _now()
    with sqlite3.connect(board.DB, timeout=30) as c:
        c.execute("UPDATE day_runs SET status='stopped', ended_at=?"
                  " WHERE id=? AND status='running'", (now, run_id))
        c.execute("UPDATE day_steps SET status='stopped', ended_at=?,"
                  " summary=COALESCE(summary,?)"
                  " WHERE run_id=? AND status IN ('running','waiting')",
                  (now, why, run_id))
        c.execute("DELETE FROM locks WHERE resource=?", (LOCK,))


def stop(by="owner", why="", grace=STOP_GRACE) -> dict:
    """
    The Stop button. Ask the run to put its tools down; insist if it will not.

    "there is no any stop button that makes the agents or overseer to
    understand that the task is stopped by the user."

    ASK FIRST, AND MEAN IT. A run told to stop closes its own books: it kills
    the step in flight, marks the steps behind it as not-run, closes as
    'stopped' rather than 'failed', releases the day lock and sweeps up after
    the children it killed. That is a far better outcome than a kill, because
    everything downstream — the Overseer especially — then knows the difference
    between work that was stopped and work that died.

    INSIST SECOND. A run that has not answered in `grace` seconds is not going
    to: it is wedged, or its watcher thread is gone, or the process is a
    zombie. At that point the recorded process is ended and this closes the
    books in its place. Only then, and only for a pid on this machine that the
    run itself wrote down — see board.end_process.

    Pressing Stop when nothing is running is not an error. It clears anything
    left over from a program that was closed, which is exactly what a person
    presses Stop for when the screen says something is still going and he knows
    perfectly well that it is not.
    """
    board.ensure_schema()
    board.sweep_dead_processes()       # first, so we don't chase a corpse
    st = status()
    run = st.get("run") or {}
    if not run or not run.get("running"):
        board.clear_stop(LOCK)
        freed = _drop_day_lock()
        return {"ok": True, "stopped": False, "was_running": False,
                "why": ("Nothing was running. "
                        + ("Cleared the door the last run left held. "
                           if freed else "")
                        + "Press Run to start a day.")}

    rid = run.get("id")
    board.request_stop(LOCK, by=by, why=why)

    waited = 0.0
    while waited < grace:
        time.sleep(0.5)
        waited += 0.5
        r = _run_row(rid)
        if not r or r["status"] != "running":
            board.clear_stop(LOCK)
            after = status(rid)
            return {"ok": True, "stopped": True, "forced": False,
                    "run_id": rid, "waited": round(waited, 1),
                    "progress": after.get("progress"),
                    "why": "Stopped. " + (after.get("head") or "")}

    # It did not answer.
    r = _run_row(rid) or {}
    ended = board.end_process(r.get("pid"), r.get("host") or "")
    who = "you" if (by or "owner") == "owner" else by
    _force_stopped(rid, f"stopped by {who} — the run had to be ended by force")
    board.clear_stop(LOCK)
    swept = board.sweep_dead_processes()
    after = status(rid)
    return {"ok": True, "stopped": True, "forced": True, "run_id": rid,
            "waited": round(waited, 1), "process": ended, "swept": swept,
            "progress": after.get("progress"),
            "why": (f"It did not answer in {int(grace)} seconds, so "
                    + (ended.get("why") or "the process was ended") + ". "
                    + (after.get("head") or ""))}


def unlock() -> dict:
    """
    Let go of the day lock by hand, and close off whatever was holding it.

    The reaper settles almost every case on its own. This is for the one it
    cannot: a run genuinely still moving that the owner nonetheless wants
    stopped — a machine he has just rebooted, a container that went away
    mid-step. It is deliberately explicit and deliberately loud, because
    releasing a lock somebody is legitimately holding is how two MDs end up
    planning the same day.
    """
    board.ensure_schema()
    with sqlite3.connect(board.DB, timeout=30) as c:
        c.row_factory = sqlite3.Row
        held = c.execute("SELECT holder, expires_at FROM locks WHERE resource=?",
                         (LOCK,)).fetchone()
        c.execute("DELETE FROM locks WHERE resource=?", (LOCK,))
        n = c.execute("SELECT count(*) FROM day_runs WHERE status='running'").fetchone()[0]
        c.execute("UPDATE day_runs SET status='stalled', ended_at=?"
                  " WHERE status='running'", (_now(),))
        c.execute("UPDATE day_steps SET status='failed', ended_at=?,"
                  " summary=COALESCE(summary,'cleared by hand with --unlock')"
                  " WHERE status='running'", (_now(),))
    return {"ok": True, "was_held_by": (dict(held)["holder"] if held else ""),
            "runs_closed": n,
            "why": ("The day lock is clear"
                    + (f"; {n} run(s) marked stalled" if n else "")
                    + ". Press Run again.")}


def _print_check(p):
    print(f"PREFLIGHT — {p['head']}  ({p['took_ms']}ms)")
    print(f"  {'ok ' if p['cabinet']['ok'] else 'BAD'} {p['cabinet']['name']}: {p['cabinet']['says']}")
    print()
    print(readiness.as_text())
    print()
    print("PIPELINES")
    for s in p["pipelines"]:
        print(f"  {'ok ' if s['ok'] else 'BAD'} {s['name']:34} {s['says']}")
    if p["blocking"]:
        print("\nSTOPPING THE RUN")
        for b in p["blocking"]:
            print("  ✕ " + b)
    if p["warnings"]:
        print("\nWORTH KNOWING")
        for w in p["warnings"]:
            print("  ! " + w)


def main():
    ap = argparse.ArgumentParser(description="Check everything, then run the day.")
    ap.add_argument("--check", action="store_true", help="preflight only, run nothing")
    ap.add_argument("--start", action="store_true", help="preflight, then run the day")
    ap.add_argument("--status", action="store_true", help="what the last run did")
    ap.add_argument("--stop", action="store_true",
                    help="stop the day that is running — asks it to finish, "
                         "then ends it if it will not")
    ap.add_argument("--why", default="", help="why you stopped it, for the record")
    ap.add_argument("--unlock", action="store_true",
                    help="clear a stuck day lock when nothing is really running")
    ap.add_argument("--force", action="store_true", help="run even if something is broken")
    ap.add_argument("--quick", action="store_true", help="skip the live API calls")
    ap.add_argument("--skip-check", action="store_true", help="run without checking first")
    ap.add_argument("--note", default="", help="what you want out of today")
    ap.add_argument("--only", default="",
                    help="this run only: the sectors to work, comma-separated "
                         "(MD, IT, Security and Report always run), e.g. --only content")
    ap.add_argument("--order", nargs=2, action="append", metavar=("SECTOR", "TEXT"),
                    default=[], help="today's order for one sector (repeatable)")
    ap.add_argument("--resume", action="store_true",
                    help="start from where the last run left off: clear up, then "
                         "run only what did not finish and what is waiting")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--stdin", action="store_true",
                    help="read {orders, note, force, quick, only, resume} as JSON on stdin")
    a = ap.parse_args()

    body = {}
    if a.stdin:
        try:
            body = json.loads(sys.stdin.read() or "{}")
        except Exception as e:
            print(json.dumps({"ok": False, "error": f"bad input: {e}"}))
            return 2

    if a.stop:
        r = stop(by=str(body.get("by") or "owner"),
                 why=str(body.get("why") or a.why))
        print(json.dumps(r) if (a.json or a.stdin) else r["why"])
        return 0

    if a.unlock:
        r = unlock()
        print(json.dumps(r) if a.json else r["why"])
        return 0

    if a.status:
        print(json.dumps(status()) if a.json else json.dumps(status(), indent=2))
        return 0
    if a.check and not a.start:
        p = preflight(quick=a.quick or bool(body.get("quick")))
        print(json.dumps(p)) if a.json else _print_check(p)
        return 0 if p["ready"] else 1

    orders = dict(body.get("orders") or {})
    orders.update({s: t for s, t in a.order})
    r = start(orders=orders,
              note=body.get("note", a.note),
              force=bool(body.get("force")) or a.force,
              quick=bool(body.get("quick")) or a.quick,
              skip_check=bool(body.get("skip_check")) or a.skip_check,
              only=(body.get("only") or [s for s in a.only.split(",") if s.strip()]),
              resume=bool(body.get("resume")) or a.resume)
    if a.json or a.stdin:
        print(json.dumps(r))
    else:
        if r.get("preflight") and not r["ok"]:
            _print_check(r["preflight"])
        print("\n" + r["why"])
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
