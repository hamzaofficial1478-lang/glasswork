"""
day_run.py — the Run button. Check everything, take the owner's instructions,
then work.

    python3 -m pipelines.day_run --check            just the preflight, no work
    python3 -m pipelines.day_run --start            check, then run the day
    python3 -m pipelines.day_run --start --force    run even though something is broken
    python3 -m pipelines.day_run --status           what the current/last run did

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
import argparse
import json
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from coordination import board, proposals, readiness   # noqa: E402

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
    ("IT reindexes site search", "pipelines.site_search", "IT", "it"),

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

def _open_run(note, forced, blocking):
    rid = uuid.uuid4().hex[:12]
    board.ensure_schema()
    with sqlite3.connect(board.DB, timeout=30) as c:
        c.execute("INSERT INTO day_runs(id,started_at,status,started_by,note,forced,"
                  "blocking,steps_total,steps_done) VALUES(?,?,?,?,?,?,?,?,0)",
                  (rid, _now(), "running", "owner", (note or "")[:2000],
                   1 if forced else 0, json.dumps(blocking or []), len(STEPS)))
        for i, (label, mod, room, lane) in enumerate(STEPS):
            c.execute("INSERT INTO day_steps(run_id,seq,label,module,status,lane)"
                      " VALUES(?,?,?,?, 'waiting', ?)", (rid, i, label, mod, lane))
    return rid


def _step(rid, seq, **fields):
    sets = ", ".join(f"{k}=?" for k in fields)
    with sqlite3.connect(board.DB, timeout=30) as c:
        c.execute(f"UPDATE day_steps SET {sets} WHERE run_id=? AND seq=?",
                  (*fields.values(), rid, seq))


def _close_run(rid, status):
    with sqlite3.connect(board.DB, timeout=30) as c:
        done = c.execute("SELECT count(*) FROM day_steps WHERE run_id=?"
                         " AND status IN ('ok','idle')", (rid,)).fetchone()[0]
        c.execute("UPDATE day_runs SET status=?, ended_at=?, steps_done=? WHERE id=?",
                  (status, _now(), done, rid))


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


def _one_step(rid, seq) -> str:
    """
    Run one step and record what it did. Returns its verdict.

    Everything a step needs is already in STEPS and in the cabinet, and the
    only thing it hands back is a verdict — so this is safe to call from
    several threads at once. The two writes it makes are a single-row UPDATE
    and an atomic increment, both of which SQLite serialises for us.
    """
    label, mod, room, lane = STEPS[seq]

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

    _step(rid, seq, status="running", started_at=_now())
    try:
        # encoding is named, never left to the machine. text=True decodes with
        # the locale codepage, which on Windows is cp1252/cp1256 — and every
        # pipeline in this program prints em-dashes and readiness lamps. A step
        # that worked perfectly would come back as mojibake, or take the run
        # down with a UnicodeDecodeError. See coordination/console.py.
        p = subprocess.run([PY, "-m", mod], cwd=str(ROOT), capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=STEP_TIMEOUT)
        code, out = p.returncode, (p.stdout or "") + (p.stderr or "")
    except subprocess.TimeoutExpired:
        code, out = 124, f"timed out after {STEP_TIMEOUT}s"
    except Exception as e:                               # pragma: no cover
        code, out = 1, str(e)
    verdict = _verdict(out, code)
    _step(rid, seq, status=verdict, ended_at=_now(),
          code=code, summary=_summarise(out, code))
    _tick(rid)
    return verdict


def _tick(rid):
    with sqlite3.connect(board.DB, timeout=30) as c:
        c.execute("UPDATE day_runs SET steps_done=steps_done+1 WHERE id=?", (rid,))


def lanes() -> dict:
    """{lane: [step index, ...]} in the order the steps are listed."""
    out = {}
    for i, (_label, _mod, _room, lane) in enumerate(STEPS):
        out.setdefault(lane, []).append(i)
    return out


def _run_lanes(rid) -> int:
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

    Returns the number of steps that failed.
    """
    plan = lanes()
    done = {name: threading.Event() for name in plan}
    slots = threading.Semaphore(max(1, int(MAX_LANES)))
    failed = []
    lock = threading.Lock()

    def walk(name):
        try:
            for dep in LANE_AFTER.get(name, ()):
                ev = done.get(dep)
                if ev is not None:
                    # Bounded, so a lane that somehow never finishes cannot
                    # hang the day for ever. The ceiling is what its steps are
                    # allowed to take, plus room to be queued behind others.
                    ev.wait(timeout=STEP_TIMEOUT * (len(plan[dep]) + 2))
            for seq in plan[name]:
                with slots:
                    verdict = _one_step(rid, seq)
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
    for t in threads:
        t.join()
    return len(failed)


def start(orders=None, note="", force=False, quick=False, skip_check=False) -> dict:
    """
    Check, take the owner's instructions, then work. Returns when the day is done.

    `orders` is {sector: what the owner wants today}. It is written to the board
    BEFORE the MD plans, which is the whole point — an order that arrives after
    the plan is written is an order the MD never saw.
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

    rid = _open_run(note, force and not pre["ready"], pre.get("blocking"))
    try:
        failed = _run_lanes(rid)
        # A step that fails is not a day that failed. IT going down should not
        # stop Report scoring what Leads did — so every step runs, and the
        # verdict at the end says how many did not.
        _close_run(rid, "done" if not failed else "partial")
    except BaseException:
        _close_run(rid, "failed")
        raise
    finally:
        board.release_lock(LOCK, LOCK_HOLDER)

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

    why = ("The day ran clean." if not failed
           else f"The day ran; {failed} of {len(STEPS)} steps failed.")
    if rejected:
        why += (" Your order for " + ", ".join(rejected)
                + " went nowhere — " + list(rejected.values())[0])
    if pushed.get("sent"):
        why += f" {pushed['sent']} thing(s) sent to your phone."
    return {"ok": True, "ran": True, "run_id": rid, "orders": written,
            "rejected_orders": rejected, "failed_steps": failed,
            "notified": pushed.get("sent", 0), "preflight": pre, "why": why}


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
    """
    now = _now()
    last = max([run["started_at"] or 0]
               + [s["ended_at"] or s["started_at"] or 0 for s in steps])
    if now - last <= STEP_TIMEOUT + 120:
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
            q = ("SELECT seq,label,module,status,started_at,ended_at,code,summary,lane"
                 " FROM day_steps WHERE run_id=? ORDER BY seq")
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
    # PLURAL, now that lanes run side by side. Reading the first 'running' row
    # and calling that "what the agency is doing" was honest when the day was
    # one queue; with three lanes going it would name one of them and hide the
    # other two, which reads as a slower day than is actually happening.
    now_on = [s for s in steps if s["status"] == "running"]
    live = now_on[0] if now_on else None
    done = sum(1 for s in steps if s["status"] in ("ok", "idle", "failed"))
    bad = sum(1 for s in steps if s["status"] == "failed")
    sat_out = sum(1 for s in steps if s["status"] == "idle")
    run["running"] = run["status"] == "running"
    if run["status"] == "stalled":
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
            "head": head}


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
    ap.add_argument("--unlock", action="store_true",
                    help="clear a stuck day lock when nothing is really running")
    ap.add_argument("--force", action="store_true", help="run even if something is broken")
    ap.add_argument("--quick", action="store_true", help="skip the live API calls")
    ap.add_argument("--skip-check", action="store_true", help="run without checking first")
    ap.add_argument("--note", default="", help="what you want out of today")
    ap.add_argument("--order", nargs=2, action="append", metavar=("SECTOR", "TEXT"),
                    default=[], help="today's order for one sector (repeatable)")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--stdin", action="store_true",
                    help="read {orders, note, force, quick} as JSON on stdin")
    a = ap.parse_args()

    body = {}
    if a.stdin:
        try:
            body = json.loads(sys.stdin.read() or "{}")
        except Exception as e:
            print(json.dumps({"ok": False, "error": f"bad input: {e}"}))
            return 2

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
              skip_check=bool(body.get("skip_check")) or a.skip_check)
    if a.json or a.stdin:
        print(json.dumps(r))
    else:
        if r.get("preflight") and not r["ok"]:
            _print_check(r["preflight"])
        print("\n" + r["why"])
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
