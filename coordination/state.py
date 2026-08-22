"""
state.py — the live snapshot the Control Room reads.

Reads the filing cabinet (agency.db) and returns the exact shape ui/agency.html
renders: header metrics, the six rooms with their real task feed, the approvals
waiting on the owner, the security feed, and the shelf. No secrets leave here.

The Node server shells into this (python3 -m coordination.state) and merges in
the .env-derived connections, so the dashboard shows the real agency instead of
placeholder data. Every read is wrapped so a missing table or an empty cabinet
degrades to zeros, never a crash.
"""
import json
import sqlite3
import time

try:
    from . import board, cabinet
except ImportError:  # pragma: no cover - script/path fallback
    import board
    import cabinet

# Where the cabinet lives is board's to say, not ours.
#
# This module used to compute its own path to agency.db, which looks harmless
# because it is the same path — right up until something points board.DB
# somewhere else. Every test that redirects the cabinet to a throwaway file did
# exactly that, and this one module kept reading the owner's REAL data behind
# its back: Report builds its digest from state.build(), so the Report tests
# were quietly scoring whatever happened in the live agency yesterday, and
# passed or failed depending on it. One definition, read fresh on every call.
def _db():
    return board.DB

# key, display name, task-sector match, is-security-room
#
# The Director is in this list deliberately. The office draws a Director's Office
# but the server used to omit it, so the UI's overlay skipped that room and its
# desk kept its hardcoded "working" placeholder — the MD's lamp glowed forever
# whether or not a cycle had ever run, while sectors doing real work sat dark.
# A room the dashboard draws must be a room the server reports on.
SECTORS = [
    ("Director", "Director's Office", "md", False),
    ("IT", "IT & Site Health", "it", False),
    ("Content", "Content & Social", "content", False),
    ("SEO", "SEO & Rankings", "seo", False),
    ("Leads", "Leads & Outreach", "leads", False),
    ("Report", "Reports & Scorecard", "report", False),
    ("Security", "Security", "security", True),
]

# Which agent names belong to which sector.
#
# The run feed used to find a sector's agents with `agent LIKE '<sector>-%'`,
# which is true of it-watcher, leads-hunter, seo-strategist and the rest — and
# false of the only two that do not follow the pattern. The MD runs as
# 'managing-director' and Report runs as 'reporter', so neither ever matched
# its own sector: the Director's Office sat dark through every cycle the MD
# planned, and the Reporter's desk sat dark through every digest it wrote.
# Two of nine desks could not light up, no matter how much work they did.
#
# Fixed here rather than by renaming the agents. The name is written into every
# agent_runs row, every claimed task and every decision already in the cabinet;
# renaming would light the desk today and silently orphan the history.
AGENT_ALIASES = {
    "md": ("md", "managing-director", "director", "critic"),
    "report": ("report", "reporter"),
}


def _match_sql(sector):
    """(sql, args) selecting every agent name that belongs to this sector."""
    names = AGENT_ALIASES.get(sector, (sector,))
    parts, args = [], []
    for n in names:
        parts.append("lower(r.agent)=? OR lower(r.agent) LIKE ?")
        args += [n, n + "-%"]
    return "(" + " OR ".join(parts) + ")", args


def _conn():
    con = sqlite3.connect(_db())
    con.row_factory = sqlite3.Row
    return con


def _day_start():
    lt = time.localtime()
    return int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)))


def _one(c, q, args=(), default=0):
    try:
        r = c.execute(q, args).fetchone()
        return r[0] if r and r[0] is not None else default
    except Exception:
        return default


def _clamp(n, lo, hi):
    return max(lo, min(hi, int(n)))


def _hhmm(ts):
    try:
        return time.strftime("%H:%M", time.localtime(ts))
    except Exception:
        return ""


def _hitrate(c):
    try:
        rows = c.execute("SELECT outcome FROM decisions WHERE outcome IS NOT NULL"
                         " ORDER BY scored_at DESC LIMIT 30").fetchall()
    except Exception:
        return None
    if not rows:
        return None
    wins = sum(1 for r in rows if r[0] == "worked")
    return round(wins / len(rows), 2)


# How long a finished run keeps the desk lit. A pipeline claims a task and
# finishes it inside the same second, so "holding a task right now" is almost
# never true — read the board alone and a sector that filed nine tickets two
# minutes ago is as dark as one that has never run. An hour is roughly a
# working session: long enough that you see what the agency has been doing,
# short enough that yesterday's run is not passed off as activity.
RECENT_RUN_SECS = 3600

# Which desk an agent sits at. The pipelines are named sector-role, so the role
# is the last segment: 'it-watcher' sits at the Watcher desk. Anything that does
# not match falls through to the first free desk rather than being dropped.
def _desk_of(agent):
    return (agent or "").rsplit("-", 1)[-1].lower()


def _ago(secs):
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60} min ago"
    return f"{secs // 3600}h ago"


# A run that started and never ended is not "still going" after this long — it
# is a stall. Nothing here takes twenty minutes; the site check times out in
# under a minute and the Hunter is capped well below it.
STALL_SECS = 1200


def _run_feed(c, match, now):
    """
    What this sector's agents have actually been doing, newest first.

    Four states, and the important one is 'blocked'. A desk goes dark ONLY when
    something is genuinely wrong — the last run raised, or it started and never
    came back — and when it does, the reason travels with it so the desk can say
    what broke rather than just looking off.
    """
    where, args = _match_sql(match)
    try:
        rows = c.execute(
            "SELECT r.agent, r.status, r.started_at, r.ended_at, r.note, t.title"
            " FROM agent_runs r LEFT JOIN tasks t ON t.id=r.task_id"
            " WHERE r.started_at>=? AND " + where +
            " ORDER BY r.started_at DESC LIMIT 12",
            (now - RECENT_RUN_SECS, *args)).fetchall()
    except Exception:
        return []

    feed, seen = [], set()
    for r in rows:
        desk = _desk_of(r["agent"])
        if desk in seen:          # one line per desk: its most recent run
            continue
        seen.add(desk)
        age = now - (r["ended_at"] or r["started_at"])
        base = {"n": r["agent"], "slot": desk}

        if r["status"] == "running" and (now - r["started_at"]) > STALL_SECS:
            # said it was working, then stopped saying anything
            feed.append({**base, "s": "blocked",
                         "t": f"stalled — started {_ago(now - r['started_at'])} and "
                              "never finished",
                         "why": ("This agent began a run and never reported back. Either "
                                 "the process was killed, or it is stuck waiting on "
                                 "something that will not answer. Re-run it from the "
                                 "command line and watch the output.")})
        elif r["status"] == "running":
            feed.append({**base, "s": "working", "t": r["note"] or r["title"] or "working…"})
        elif r["status"] == "error":
            feed.append({**base, "s": "blocked",
                         "t": (r["note"] or "the run failed") + " · " + _ago(age),
                         "why": ("The last run raised an error and stopped. " +
                                 (r["note"] or "No detail was recorded.") +
                                 " Nothing was half-written — the run is marked failed, "
                                 "not finished.")})
        else:
            feed.append({**base, "s": "done",
                         "t": (r["note"] or r["title"] or "finished a run") + " · " + _ago(age)})
    return feed


# ---------------------------------------------------------------------------
# WHAT A SECTOR'S HEALTH ACTUALLY MEANS
# ---------------------------------------------------------------------------
#
# It used to be `92 - open_tasks * 4`, clamped. Content, SEO and Leads all
# returned exactly that, so three rooms showed a confident number that had
# nothing to do with whether the work was any good — a sector that ran, failed
# every run and produced nothing scored 92 as long as its board was clear. The
# owner called them dummy numbers, which is what they were.
#
# Health is now built from four things the cabinet can actually answer, each
# carrying its own reasoning so the room can say WHY it scores what it does:
#
#   DID IT RUN      work in the last day, and did the runs end clean or error
#   DID IT PRODUCE  rows its own pipelines create — the output, not the effort
#   IS IT STUCK     stale claims, unanswered requests, a growing backlog
#   WAS IT RIGHT    decisions this sector logged that were later scored
#
# And when there is no evidence at all, the score is None and the room says
# "has not run yet" instead of inventing a number. A dashboard that cannot tell
# the difference between healthy and untested is worse than a blank.

# What counts as OUTPUT for each sector: the table its work lands in. Effort is
# not output — a run that finished cleanly and wrote nothing produced nothing.
_OUTPUT = {
    "Director": ("decisions", "created_at"),
    "IT":       ("site_issues", "found_at"),
    "Content":  ("content_queue", "created_at"),
    "SEO":      ("keyword_intel", "seen_at"),
    "Leads":    ("leads", "found_at"),
    "Report":   ("daily_reports", "created_at"),
    "Security": ("security_events", "at"),
}


def _health(c, key, match, day0, now):
    """
    (score or None, reasons[]) — an honest read of one sector.

    Every reason is {label, points, detail}: what was measured, what it was
    worth, and the plain-words why. The room shows the number; clicking it
    shows this.
    """
    why = []
    # AGENT_ALIASES, not a bare LIKE. The MD runs as 'managing-director' and
    # Report as 'reporter', so a `lower(agent) LIKE 'md-%'` test can never match
    # either of them — the same trap _run_feed already had to climb out of, and
    # this landed straight back in it: the Director showed "did not run today"
    # on a day it had planned the whole thing.
    where, args = _match_sql(match)
    where = where.replace("r.agent", "agent")
    ran = _one(c, "SELECT count(*) FROM agent_runs WHERE started_at>=? AND " + where,
               (day0, *args))
    bad = _one(c, "SELECT count(*) FROM agent_runs WHERE started_at>=?"
                  " AND status='error' AND " + where, (day0, *args))
    table, when = _OUTPUT.get(key, (None, None))
    made = _one(c, f"SELECT count(*) FROM {table} WHERE {when}>=?", (day0,)) if table else 0
    backlog = _one(c, "SELECT count(*) FROM tasks WHERE lower(sector)=?"
                      " AND status IN ('open','in_progress')", (match,))
    stale = _one(c, "SELECT count(*) FROM tasks WHERE lower(sector)=?"
                    " AND status='in_progress' AND claimed_at < ?",
                 (match, now - STALL_SECS))
    unanswered = _one(c, "SELECT count(*) FROM requests WHERE lower(to_sector)=?"
                         " AND status IN ('open','claimed')", (match,))

    if not ran and not made and not backlog:
        return None, [{"label": "No evidence yet", "points": 0,
                       "detail": "This sector has not run today, has produced "
                                 "nothing, and has nothing on the board. There is "
                                 "no honest score to give — press Run the day."}]

    score = 0

    # DID IT RUN — and did the running work.
    if ran:
        clean = ran - bad
        pts = round(30 * (clean / ran))
        score += pts
        why.append({"label": "Ran and finished cleanly", "points": pts,
                    "detail": f"{clean} of {ran} run(s) today ended without error."
                              + (f" {bad} crashed." if bad else "")})
    else:
        why.append({"label": "Did not run today", "points": 0,
                    "detail": "No agent in this sector recorded a run today. "
                              "Whatever else is true, nothing moved."})

    # DID IT PRODUCE — output, not effort.
    if table:
        pts = 30 if made >= 5 else (20 if made >= 2 else (10 if made else 0))
        score += pts
        why.append({"label": "Produced something", "points": pts,
                    "detail": (f"{made} new row(s) in {table} today."
                               if made else
                               f"Nothing new in {table} today. A clean run that "
                               f"writes nothing has produced nothing.")})

    # IS IT STUCK — the things that quietly stop a sector.
    penalty = min(stale * 10, 20) + min(unanswered * 5, 15) + min(max(backlog - 3, 0) * 3, 15)
    if penalty:
        score -= penalty
        bits = []
        if stale:
            bits.append(f"{stale} task(s) claimed and abandoned — nobody else can "
                        f"take them while they are held")
        if unanswered:
            bits.append(f"{unanswered} request(s) from other sectors unanswered")
        if backlog > 3:
            bits.append(f"{backlog} items on the board")
        why.append({"label": "Stuck or overloaded", "points": -penalty,
                    "detail": "; ".join(bits) + "."})
    else:
        why.append({"label": "Nothing stuck", "points": 0,
                    "detail": f"{backlog} item(s) on the board, none abandoned, "
                              f"no unanswered requests."})

    # WAS IT RIGHT — only where decisions have actually been scored.
    scored = _one(c, "SELECT count(*) FROM decisions WHERE outcome IS NOT NULL"
                     " AND lower(cycle) LIKE ?", (match + "%",))
    if scored:
        good = _one(c, "SELECT count(*) FROM decisions WHERE outcome='worked'"
                       " AND lower(cycle) LIKE ?", (match + "%",))
        pts = round(20 * good / scored)
        score += pts
        why.append({"label": "Its calls came off", "points": pts,
                    "detail": f"{good} of {scored} scored decision(s) worked."})
    else:
        score += 10          # neutral: not yet judged, and not punished for it
        why.append({"label": "Not yet judged", "points": 10,
                    "detail": "No decision from this sector has been scored yet, "
                              "so this is a neutral half-mark rather than a "
                              "verdict either way."})

    return _clamp(score, 0, 100), why


# WHAT THE NUMBER MEANS, in words, because a bare 30 tells nobody anything.
#
# The scale is deliberately NOT a school grade. It is out of 100 because that
# reads naturally, but 100 means "ran clean, produced plenty, nothing stuck and
# its calls came off" — a sector firing on every cylinder — and a real working
# day for a small agency sits in the 50s and 60s. A 30 is not a failure; it
# usually means "ran fine, produced nothing today", which is exactly what a
# sector with nothing to do looks like.
BANDS = [
    (80, "strong", "Running, producing, nothing stuck, and its past calls came off."),
    (60, "healthy", "Doing its job. Anything above 60 is a good working day."),
    (40, "working", "It runs, but something is holding it back — usually nothing "
                    "produced today, or a task nobody picked up."),
    (20, "idling", "Alive but not delivering. Look at the breakdown: most often it "
                   "ran cleanly and wrote nothing, which means it had no work."),
    (0,  "stalled", "Barely functioning. Something is stuck or failing every run."),
]


def band(score):
    """(word, meaning) for a score, or the unknown case."""
    if score is None:
        return ("not run yet", "No evidence to score. This is not a bad mark — the "
                               "sector simply has not run, produced anything, or been "
                               "given anything to do. Press Run the day.")
    for floor, word, meaning in BANDS:
        if score >= floor:
            return word, meaning
    return BANDS[-1][1], BANDS[-1][2]


# HOW A SCORE GOES UP. Written here so the dashboard can say it, rather than
# leaving the owner to reverse-engineer it from a number.
HOW_TO_RAISE = [
    "Run the day. A sector that has not run scores nothing for the biggest "
    "component — 30 of the 100 is simply 'did it run, and did the runs finish "
    "without crashing'.",
    "Give it something to produce. 30 more is output: rows it actually wrote "
    "today. Effort is not output — a clean run that writes nothing scores zero "
    "here, on purpose.",
    "Clear what is stuck. Abandoned task claims cost 10 each and block everyone "
    "else from picking them up; unanswered requests from other sectors cost 5 "
    "each; a board over three items starts costing 3 an item.",
    "Let its decisions be scored. Up to 20 for calls that came off. Until "
    "Report has scored anything this sits at a neutral 10 — not a verdict.",
]


def _room_stats(c, key, day0, open_tasks):
    """Return (foot[2], score) for a room, from real counts."""
    base = _clamp(92 - open_tasks * 4, 60, 96)
    if key == "Director":
        posted = _one(c, "SELECT count(*) FROM tasks WHERE created_by='MD'")
        unscored = _one(c, "SELECT count(*) FROM decisions WHERE outcome IS NULL")
        hr = _hitrate(c)
        return ([f"{posted} tasks posted", f"{unscored} calls unscored"],
                (int(hr * 100) if hr is not None else base))
    if key == "IT":
        issues = _one(c, "SELECT count(*) FROM site_issues WHERE status IN ('open','fixing','in_review')")
        perf = _one(c, "SELECT perf FROM lighthouse ORDER BY at DESC LIMIT 1", default=None)
        return [f"{perf if perf is not None else '—'} perf score", f"{issues} tickets open"], _clamp(base - issues * 6, 55, 96)
    if key == "Content":
        review = _one(c, "SELECT count(*) FROM content_queue WHERE status IN ('drafted','in_review')")
        queued = _one(c, "SELECT count(*) FROM content_queue WHERE status IN ('idea','briefed','approved')")
        # A page held for another sector's call is real work, not nothing — if it
        # isn't shown, six finished briefs look like an idle room.
        held = _one(c, "SELECT count(*) FROM content_queue WHERE status LIKE 'blocked_on_%'")
        right = f"{queued} queued" + (f", {held} held" if held else "")
        return [f"{review} in review", right], base
    if key == "SEO":
        kw = _one(c, "SELECT count(*) FROM keywords")
        ranks = _one(c, "SELECT count(*) FROM rankings")
        return [f"{kw} keywords", f"{ranks} ranks tracked"], base
    if key == "Leads":
        valid = _one(c, "SELECT count(*) FROM leads WHERE found_at>=? AND email_valid=1", (day0,))
        replies = _one(c, "SELECT count(*) FROM outreach_log WHERE direction='reply' AND at>=?", (day0,))
        return [f"{valid} valid today", f"{replies} replies"], base
    if key == "Report":
        hr = _hitrate(c)
        unscored = _one(c, "SELECT count(*) FROM decisions WHERE outcome IS NULL")
        return ([f"MD hit-rate {int(hr * 100)}%" if hr is not None else "MD hit-rate —",
                 f"{unscored} to score"],
                (int(hr * 100) if hr is not None else base))
    if key == "Security":
        today = _one(c, "SELECT count(*) FROM security_events WHERE at>=?", (day0,))
        flagged = _one(c, "SELECT count(*) FROM security_events WHERE kind IN ('scam_lead','fraud','phishing')")
        return [f"{today} events today", f"{flagged} flagged"], _clamp(96 - today * 5, 60, 98)
    return ["—", "—"], base


def build():
    c = _conn()
    day0 = _day_start()
    now = int(time.time())

    # ---- rooms: what each sector's agents are doing, and what's waiting ----
    rooms = []
    for key, name, match, sec in SECTORS:
        try:
            trows = c.execute(
                "SELECT title,status,owner,claimed_at FROM tasks"
                " WHERE lower(sector)=? AND status IN ('open','in_progress')"
                " ORDER BY created_at DESC LIMIT 6", (match,)).fetchall()
        except Exception:
            trows = []

        # A run beats a task row. An agent mid-run IS the live picture; a task
        # sitting 'in_progress' with nobody running is a stall, not work.
        agents = _run_feed(c, match, now)
        busy = {a["slot"] for a in agents}
        for t in trows:
            owner = t["owner"] or "unassigned"
            slot = _desk_of(owner) if t["owner"] else ""
            if t["status"] == "in_progress" and slot in busy:
                continue                      # already shown, with better detail
            if t["status"] != "in_progress":
                agents.append({"n": owner, "slot": slot, "t": t["title"], "s": "waiting"})
                continue
            # Claimed, but no run backing it. A claim with nothing behind it is a
            # held job nobody is doing — it blocks every other agent from taking
            # the task, so it is a fault, not activity.
            held = now - (t["claimed_at"] or now)
            agents.append({
                "n": owner, "slot": slot,
                "s": "blocked" if held > STALL_SECS else "working",
                "t": (f"holding '{t['title']}' since {_ago(held)} with nothing running"
                      if held > STALL_SECS else t["title"]),
                **({"why": ("This task was claimed and never finished, and no run is "
                            "recording work against it. While it is held, no other "
                            "agent can take it. Finish it or release it.")}
                   if held > STALL_SECS else {})})
        open_tasks = _one(c, "SELECT count(*) FROM tasks WHERE lower(sector)=? AND status IN ('open','in_progress')", (match,))
        foot, _old = _room_stats(c, key, day0, open_tasks)
        score, why = _health(c, key, match, day0, now)
        word, meaning = band(score)
        rooms.append({"key": key, "name": name, "score": score, "foot": foot,
                      "why": why, "band": word, "band_means": meaning,
                      "sec": sec, "agents": agents})

    # ---- header metrics ----
    leads_today = _one(c, "SELECT count(*) FROM leads WHERE found_at>=?", (day0,))
    spend = _one(c, "SELECT COALESCE(SUM(usd_cost),0) FROM agent_runs WHERE started_at>=?", (day0,), 0.0)
    spend += _one(c, "SELECT COALESCE(SUM(usd_cost),0) FROM borrows WHERE granted_at>=?", (day0,), 0.0)
    # Crawler requests are billed per call and borrow nothing, so they land in
    # the usage meter rather than the borrow trail. Left out, the headline spend
    # figure would read $0.00 on a day the agency rendered two hundred pages.
    spend += _one(c, "SELECT COALESCE(SUM(usd),0) FROM api_usage WHERE at>=?", (day0,), 0.0)
    # Only sectors that HAVE a score count towards the headline. A sector with
    # no evidence yet returns None, and averaging it in as a zero would report
    # the agency as unhealthy on the morning of its first run — untested is not
    # unhealthy, and the header says "—" rather than pretending otherwise.
    scored_rooms = [r["score"] for r in rooms if r["score"] is not None]
    health = round(sum(scored_rooms) / len(scored_rooms)) if scored_rooms else None
    open_all = _one(c, "SELECT count(*) FROM tasks WHERE status IN ('open','in_progress')")
    md_state = f"{open_all} tasks on the board" if open_all else "idle — board clear"

    # ---- approvals waiting on the owner (amber/red tasks + queued site changes) ----
    # Each item carries the id and kind it was read from. Without them the
    # Control Room could draw an Approve button but had nothing to address the
    # decision TO, which is how it ended up printing "sent to the agent" while
    # sending nothing. See board.decide_approval.
    approvals = []
    try:
        for t in c.execute("SELECT id,sector,title,detail,risk FROM tasks"
                           " WHERE risk IN ('amber','red') AND status IN ('open','in_progress')"
                           " ORDER BY created_at DESC LIMIT 6"):
            approvals.append({"kind": "task", "id": t["id"],
                              "lvl": f"{t['risk']} · {t['sector']}", "text": t["title"],
                              "why": t["detail"] or "Needs your nod."})
    except Exception:
        pass
    try:
        for s in c.execute("SELECT id,agent,kind,detail FROM site_queue WHERE status='pending'"
                           " ORDER BY queued_at DESC LIMIT 3"):
            approvals.append({"kind": "site_change", "id": s["id"],
                              "lvl": "amber · IT", "text": f"Apply queued change: {s['kind']}",
                              "why": ((s["detail"] or "")[:150] + " — queued by " + (s["agent"] or "IT"))})
    except Exception:
        pass

    # ---- security feed ----
    sevmap = {"critical": "hi", "high": "hi", "hi": "hi",
              "medium": "mid", "mid": "mid", "low": "lo", "lo": "lo"}
    security = []
    try:
        for e in c.execute("SELECT kind,severity,detail,at FROM security_events ORDER BY at DESC LIMIT 6"):
            security.append({"sev": sevmap.get((e["severity"] or "lo").lower(), "mid"),
                             "title": (e["kind"] or "event").replace("_", " ").title(),
                             "sub": (e["detail"] or "")[:80], "time": _hhmm(e["at"])})
    except Exception:
        pass

    # ---- the shelf (shared API cabinet) ----
    try:
        # the shelf list is NOT hardcoded — it comes from the store, so a custom
        # shelf you invent on /setup shows up here with no code change.
        try:
            from providers import store as _store
            cat = _store.shelf_catalogue()
            names = sorted(set(_store.known_shelves()) | set(cat))
            kind_of = _store.shelf_kind
        except Exception:
            cat, names, kind_of = {}, [], (lambda _n: "custom")
        state = cabinet.shelf_state(names)
        shelf = []
        for k, v in sorted(state.items()):
            # the borrow that is open right now tells us WHICH sector holds it
            active = next((b for b in v.get("recent", []) if b.get("status") == "active"), None)
            shelf.append({
                "shelf": k,
                "holder": (v["holder"]["agent"] if v.get("holder") else None),
                "holder_sector": (active or {}).get("sector"),
                "using": (active or {}).get("item"),
                "queue": len(v.get("queue", [])),
                "queued_by": [q.get("agent") for q in v.get("queue", [])],
                "spend": v.get("today_spend", 0),
                # store.shelf_items, not the raw catalogue: SMTP is a real mail
                # route that lives in .env, and reading the catalogue alone made
                # this screen say "no API" about a shelf that sends mail daily —
                # while the setup screen, reading a different function, said it
                # was configured. Same fact, two answers, one of them wrong.
                "items": len(_store.shelf_items(k)),
                "names": [i.get("name", "") for i in _store.shelf_items(k)],
                "purpose": kind_of(k),
            })
    except Exception:
        shelf = []

    c.close()

    # ---- three more things the office draws, fetched in the SAME call ------
    # Each of these could have its own endpoint, and each endpoint would be
    # another Python process spawned every few seconds. The office polls fast
    # while a day is running so the bulbs are actually visible, so one snapshot
    # per poll is the difference between a live floor and a machine fighting
    # itself. All three degrade to empty rather than taking the snapshot down.
    try:
        from . import readiness as _readiness
        ready = _readiness.all_sectors()
    except Exception as e:
        ready = {"ok": False, "error": str(e), "sectors": []}
    try:
        from . import activity as _activity
        acts = _activity.feed(limit=50)
    except Exception as e:
        acts = {"ok": False, "error": str(e), "events": []}
    try:
        from pipelines import day_run as _day_run
        run = _day_run.status()
    except Exception as e:
        run = {"ok": False, "error": str(e), "run": None, "steps": []}
    try:
        from . import proposals as _proposals
        todays_orders = _proposals.orders()
    except Exception:
        todays_orders = {}

    return {"ok": True, "ts": int(time.time()),
            "md": {"state": md_state},
            "metrics": {"health": health, "leads": leads_today, "spend": f"${spend:.2f}"},
            "rooms": rooms, "approvals": approvals, "security": security, "shelf": shelf,
            "readiness": ready, "activity": acts, "run": run,
            "orders": todays_orders}


# ---------------------------------------------------------------------------
# ONE SECTOR, IN FULL — what it actually made
# ---------------------------------------------------------------------------

# The rows worth showing a person, per sector, and how to say each one in a
# line. Not "12 rows in keyword_intel" — the actual phrases, the actual tickets,
# the actual leads. A dashboard that only counts is a dashboard you cannot act
# on, and the owner asked to be able to click a sector and read what it made.
_DETAIL = {
    "Director": ("decisions",
                 "SELECT id, cycle AS a, reasoning AS b, confidence AS c,"
                 " COALESCE(outcome,'not scored yet') AS d, created_at AS at"
                 " FROM decisions ORDER BY created_at DESC LIMIT 40",
                 ("cycle", "reasoning", "confidence", "outcome")),
    "IT": ("site issues",
           "SELECT id, kind AS a, detail AS b, severity AS c, status AS d,"
           " found_at AS at FROM site_issues ORDER BY found_at DESC LIMIT 40",
           ("kind", "what is wrong", "severity", "status")),
    "Content": ("content queue",
                "SELECT id, title AS a, COALESCE(brief,'') AS b, status AS c,"
                " COALESCE(service,'') AS d, created_at AS at"
                " FROM content_queue ORDER BY created_at DESC LIMIT 40",
                ("title", "brief", "status", "service")),
    "SEO": ("keyword intelligence",
            "SELECT id, phrase AS a, evidence AS b, score AS c, sources AS d,"
            " seen_at AS at FROM keyword_intel ORDER BY score DESC LIMIT 40",
            ("phrase", "evidence", "score", "sources")),
    "Leads": ("leads",
              "SELECT id, COALESCE(company,'') AS a, COALESCE(email,'') AS b,"
              " COALESCE(area,'') AS c, stage AS d, found_at AS at"
              " FROM leads ORDER BY found_at DESC LIMIT 40",
              ("company", "email", "area", "stage")),
    "Report": ("daily reports",
               "SELECT id, day AS a, summary AS b, COALESCE(health,'') AS c,"
               " COALESCE(spend_usd,'') AS d, created_at AS at"
               " FROM daily_reports ORDER BY created_at DESC LIMIT 40",
               ("day", "summary", "health", "spend")),
    "Security": ("security events",
                 "SELECT id, kind AS a, COALESCE(detail,'') AS b,"
                 " COALESCE(verdict,'') AS c, COALESCE(source,'') AS d, at"
                 " FROM security_events ORDER BY at DESC LIMIT 40",
                 ("kind", "detail", "verdict", "source")),
}


def sector_detail(key):
    """Everything one sector has to show for itself. Read-only."""
    row = next((r for r in SECTORS if r[0].lower() == (key or "").lower()), None)
    if not row:
        return {"ok": False, "error": f"no sector called {key!r}"}
    key, name, match, _sec = row
    c = _conn()
    day0, now = _day_start(), int(time.time())

    score, why = _health(c, key, match, day0, now)
    label, sql, cols = _DETAIL.get(key, ("output", "", ()))
    made = []
    if sql:
        try:
            for r in c.execute(sql):
                made.append({"id": r["id"], "cols": [r["a"], r["b"], r["c"], r["d"]],
                             "at": _hhmm(r["at"]), "ts": r["at"]})
        except Exception:
            made = []

    def _rows(q, args=()):
        try:
            return [dict(x) for x in c.execute(q, args)]
        except Exception:
            return []

    tasks = _rows("SELECT id,title,detail,status,risk,owner,created_at FROM tasks"
                  " WHERE lower(sector)=? ORDER BY created_at DESC LIMIT 25", (match,))
    where, args = _match_sql(match)
    runs = _rows("SELECT r.agent,r.status,r.note,r.started_at,r.ended_at FROM agent_runs r"
                 " WHERE " + where + " ORDER BY r.started_at DESC LIMIT 25", tuple(args))
    inbox = _rows("SELECT id,from_sector,subject,status,created_at FROM requests"
                  " WHERE lower(to_sector)=? ORDER BY created_at DESC LIMIT 15", (match,))
    outbox = _rows("SELECT id,to_sector,subject,status,created_at FROM requests"
                   " WHERE lower(from_sector)=? ORDER BY created_at DESC LIMIT 15", (match,))
    # What is broken about this sector's own agents, and — the part that was
    # missing — who was told. A desk drawn in red with nothing beside it is how
    # a stall sat for forty-one minutes with nobody acting on it.
    faults = _rows("SELECT id,kind,severity,what,routed_to,escalated_at,seen_count,"
                   "first_seen FROM faults WHERE status!='resolved' AND lower(sector)=?"
                   " ORDER BY CASE severity WHEN 'blocking' THEN 0"
                   " WHEN 'degraded' THEN 1 ELSE 2 END, last_seen DESC LIMIT 12",
                   (match,))
    c.close()
    word, meaning = band(score)
    return {"ok": True, "key": key, "name": name, "score": score, "why": why,
            "band": word, "band_means": meaning, "how_to_raise": HOW_TO_RAISE,
            "output_label": label, "columns": list(cols), "made": made,
            "tasks": tasks, "runs": runs, "inbox": inbox, "outbox": outbox,
            "faults": faults}


if __name__ == "__main__":
    import sys
    # `-m coordination.state` prints the whole board; `--sector SEO` prints one
    # sector in full, which is what the control room asks for when a room is
    # clicked. Same module either way, so the numbers can never disagree.
    if "--sector" in sys.argv:
        print(json.dumps(sector_detail(sys.argv[sys.argv.index("--sector") + 1])))
    else:
        print(json.dumps(build()))
