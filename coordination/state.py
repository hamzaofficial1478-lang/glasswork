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
        elif r["status"] == "stalled":
            # The Overseer reaped it: the agent stopped answering and the claim
            # to be running was retracted. Past tense, unlike the branch above.
            feed.append({**base, "s": "blocked",
                         "t": "stopped responding · " + _ago(age),
                         "why": ("This agent began a run and never reported back, so "
                                 "the Overseer declared it dead and closed the run off. "
                                 + (r["note"] or "") +
                                 " A fault was filed against this desk and given to IT; "
                                 "open the sector to see where it went.")})
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
    # SWITCHED OFF IS NOT FAILING. A sector the owner has stopped has not run,
    # produced nothing and cleared no backlog — every component below would
    # score it near zero, and it would then drag the agency average down for
    # obeying him. It has no score at all, and says why.
    if not board.sector_enabled(key):
        sw = board.sector_switches().get(key, {})
        return None, [{"label": "Switched off", "points": 0,
                       "detail": ("You turned this sector off, so it did no work "
                                  "and there is nothing to score. It keeps its "
                                  "history and its agents; turn it back on and it "
                                  "picks up from the next run."
                                  + (f" Your note: {sw['note']}" if sw.get("note") else ""))}]
    # AGENT_ALIASES, not a bare LIKE. The MD runs as 'managing-director' and
    # Report as 'reporter', so a `lower(agent) LIKE 'md-%'` test can never match
    # either of them — the same trap _run_feed already had to climb out of, and
    # this landed straight back in it: the Director showed "did not run today"
    # on a day it had planned the whole thing.
    where, args = _match_sql(match)
    where = where.replace("r.agent", "agent")
    ran = _one(c, "SELECT count(*) FROM agent_runs WHERE started_at>=? AND " + where,
               (day0, *args))
    # A run the Overseer had to declare dead did not finish cleanly either. It
    # used to fall through to 'done' and count as a clean run, which flattered a
    # sector for the exact failure the owner was watching.
    bad = _one(c, "SELECT count(*) FROM agent_runs WHERE started_at>=?"
                  " AND status IN ('error','stalled') AND " + where, (day0, *args))
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
    #
    # TURNING UP IS NOT AN ACHIEVEMENT, and scoring it like one produced the
    # number he could not account for:
    #
    #   "no any sector did any work but agency health is still 40 why? when
    #    there is no any work happened on which bases the secore is 40?"
    #
    # It was 30 for running cleanly plus 10 for not having been judged yet.
    # Exactly 40, on a day where every sector produced nothing. Both halves
    # were defensible on their own and together they said the agency was
    # fine — and a health score that reads 40 on a day nothing happened is
    # worse than no score, because he checks it instead of checking the work.
    #
    # A clean run is now a PRECONDITION worth a few marks, and the rest of
    # those marks are only earned if something came of it. Producing nothing
    # and finishing nothing is a run that happened, not a run that worked.
    finished = _one(c, "SELECT count(*) FROM tasks WHERE lower(sector)=?"
                       " AND status='done' AND COALESCE(finished_at,0)>=?",
                    (match, day0))
    if ran:
        clean = ran - bad
        showed_up = round(10 * (clean / ran))
        got_somewhere = round(20 * (clean / ran)) if (made or finished) else 0
        pts = showed_up + got_somewhere
        score += pts
        detail = f"{clean} of {ran} run(s) today ended without error."
        if bad:
            detail += f" {bad} crashed."
        if not (made or finished):
            detail += (" Nothing was produced and no task was finished, so this "
                       "scores as turning up rather than as working — a clean "
                       "run that changes nothing has changed nothing.")
        else:
            detail += f" {finished} task(s) finished."
        why.append({"label": ("Ran and finished cleanly" if (made or finished)
                              else "Turned up, but nothing came of it"),
                    "points": pts, "detail": detail})
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
        # A HALF-MARK FOR BEING UNJUDGED IS ONLY FAIR WHILE THE QUEUE IS SHORT.
        # He had 45 calls waiting to be scored; handing out ten free points on
        # that basis is not neutrality, it is a backlog paying itself a
        # dividend. Ten while the sector is genuinely new, nothing once the
        # unscored pile is deep enough to be the problem itself.
        pending = _one(c, "SELECT count(*) FROM decisions WHERE outcome IS NULL"
                          " AND lower(cycle) LIKE ?", (match + "%",))
        pts = 0 if pending >= 5 else 10
        score += pts
        why.append({"label": "Not yet judged", "points": pts,
                    "detail": ("No decision from this sector has been scored yet, "
                               "so this is a neutral half-mark rather than a "
                               "verdict either way." if pts else
                               f"{pending} decision(s) from this sector are still "
                               f"unscored. That is too many to keep paying a "
                               f"benefit of the doubt on — the scorecard cannot "
                               f"tell you whether this sector is any good until "
                               f"its calls are settled.")})

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


def _change_json(detail):
    try:
        d = json.loads(detail or "{}")
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _change_title(kind, detail):
    """
    What he is being asked to approve, in a line.

    'Apply queued change: new_page' is the table's word for it, not his. Once
    Content could queue a whole page, the difference between "add a block to
    the head of index.php" and "put a new page on my website" stopped being a
    detail — it is the biggest thing the agency does — and the card said
    neither.
    """
    d = _change_json(detail)
    page = ((d.get("change") or {}).get("page") if isinstance(d.get("change"), dict)
            else "") or ""
    if str(kind) == "new_page":
        title = str(d.get("title") or "").strip()
        return (f"Publish a new page: {page}" + (f" — “{title}”" if title else ""))
    if page:
        return f"Change {page}"
    return f"Apply queued change: {kind}"


def _change_why(detail):
    """
    The reason, not the payload.

    This used to be the first 150 characters of the row's JSON, which for a
    queued page is `{"change": {"page": "...", "html": "<?php include ...` — a
    wall of escaped markup, and a decision he cannot read is not a decision he
    can make. Every queuing caller writes a plain 'why'; use it when it is
    there and fall back to the raw text when it is not.
    """
    d = _change_json(detail)
    why = str(d.get("why") or "").strip()
    if why:
        return why[:400]
    return (detail or "")[:150]


def band(score, why=None):
    """
    (word, meaning) for a score — and when the reasons are handed in, the
    meaning is THIS sector's actual reason rather than a general one.

    "FOR EACH SECTOR DETAILS THAT IS FAILED IT SHOWS SOMETHING IS HOLDING IT
     BACK. while the program should show the actaull error what is holding it
     back."

    He is right and it was lazy. The band text said "something is holding it
    back — usually nothing produced today, or a task nobody picked up", which
    is a guess about sectors in general, printed underneath a breakdown that
    already knew the specific answer for this one. Every reason carries its
    own points and its own plain-words detail; the worst of them IS the answer,
    and it was sitting one line away being ignored.

    The general sentence is still the fallback for a sector with nothing
    negative to report, because there a guess is all there is.
    """
    if score is None:
        return ("not run yet", "No evidence to score. This is not a bad mark — the "
                               "sector simply has not run, produced anything, or been "
                               "given anything to do. Press Run the day.")
    word, meaning = BANDS[-1][1], BANDS[-1][2]
    for floor, w, m in BANDS:
        if score >= floor:
            word, meaning = w, m
            break

    if why:
        # The biggest thing costing it points. Ties go to the first, which is
        # the order they were measured in — running, producing, then stuck.
        hurt = [r for r in why if (r.get("points") or 0) < 0]
        if hurt:
            worst = min(hurt, key=lambda r: r["points"])
            return word, (f"{worst['label']} ({worst['points']}): "
                          f"{worst['detail']}")
        # Nothing NEGATIVE, but a low score still has a cause — almost always
        # a component that scored zero rather than one that scored against it.
        # "Produced something: nothing new in content_queue today" is the
        # answer he wants, and it is worth zero, not minus.
        blank = [r for r in why
                 if not (r.get("points") or 0) and (r.get("detail") or "").strip()]
        if blank and score < 60:
            return word, f"{blank[0]['label']}: {blank[0]['detail']}"
    return word, meaning


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
        review = _one(c, "SELECT count(*) FROM content_queue"
                         " WHERE status IN ('drafted','in_review','for_blog')")
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
    # Read once, not once per room: seven separate lookups for a table with at
    # most seven rows is seven connections for nothing.
    switches = board.sector_switches()
    rooms = []
    for key, name, match, sec in SECTORS:
        try:
            trows = c.execute(
                "SELECT id,title,status,owner,claimed_at,risk,desk FROM tasks"
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
            # WHICH DESK AN UNCLAIMED TASK BELONGS TO.
            #
            # Before the board routed work (coordination/desks.py) an open task
            # had no owner and therefore no desk, so the office sat it at
            # whichever desk happened to be free — and the owner watched the SEO
            # Auditor glow amber over a task that had nothing to do with the
            # Auditor. It now reports the desk the task is ADDRESSED to; a task
            # addressed to nobody still reports none, and the floor leaves every
            # desk alone rather than picking one at random.
            slot = _desk_of(owner) if t["owner"] else _desk_of(
                (t["desk"] if "desk" in t.keys() else "") or "")
            if t["status"] == "in_progress" and slot in busy:
                continue                      # already shown, with better detail
            if t["status"] != "in_progress":
                # AMBER MEANS "WAITING ON YOU", AND NOTHING ELSE.
                #
                # Every unclaimed task used to light its desk amber, and amber on
                # this floor reads as "this desk needs your permission". So the
                # owner cleared every approval, watched a bulb stay orange, and
                # reasonably concluded the agency had not heard him. It had — the
                # bulb was never about approvals. Only an amber or red task is
                # actually waiting on him; ordinary work nobody has picked up yet
                # is 'queued', which is a different thing and a different colour.
                pending = (t["risk"] if "risk" in t.keys() else "") in ("amber", "red")
                # NOT "UNASSIGNED" — WHY IT IS WAITING. "many tasks are still
                # unassigned why" — the word said nothing. Three different
                # things wear it: his decision, a job waiting its turn (one per
                # sector per run), and a job no desk in the sector does at all.
                if pending:
                    who, why = "waiting on you", (
                        "This one needs your yes or no before the desk can start. "
                        "Approve or decline it and the desk moves on to whatever "
                        "is next.")
                else:
                    try:
                        from . import desks as _desks
                        able = [d for d in _desks.sector_desks(match)
                                if _desks.verdict(d, dict(t))["allowed"]]
                    except Exception:
                        able = ["?"]
                    if able:
                        who, why = "next in line", (
                            f"Waiting its turn for {able[0]}. Each sector takes one "
                            "job per run and finishes it before starting the next; "
                            "your own orders go first.")
                    else:
                        who, why = "no desk can take this", (
                            f"No desk in {name} does this kind of work, so nobody can "
                            "pick it up. Resume, or Clear up only, takes jobs like "
                            "this off the board.")
                agents.append({"n": who, "slot": slot, "t": t["title"],
                               "s": "waiting" if pending else "queued", "why": why})
                continue
            # Claimed, but no run backing it. A claim with nothing behind it is a
            # held job nobody is doing — it blocks every other agent from taking
            # the task, so it is a fault, not activity.
            held = now - (t["claimed_at"] or now)
            agents.append({
                "n": owner, "slot": slot,
                # The task id travels with the line so the office can offer to
                # put it back. Without it the drawer could describe the problem
                # and do nothing about it, which is what the owner had.
                "task_id": t["id"],
                "s": "blocked" if held > STALL_SECS else "working",
                "t": (f"holding '{t['title']}' since {_ago(held)} with nothing running"
                      if held > STALL_SECS else t["title"]),
                **({"why": ("This task was claimed and never finished, and no run is "
                            "recording work against it. While it is held, no other "
                            "agent can take it — so it is not being done and nobody "
                            "else can do it either. Put it back on the board and the "
                            "next desk that can do the work picks it up.")}
                   if held > STALL_SECS else {})})
        open_tasks = _one(c, "SELECT count(*) FROM tasks WHERE lower(sector)=? AND status IN ('open','in_progress')", (match,))
        foot, _old = _room_stats(c, key, day0, open_tasks)
        score, why = _health(c, key, match, day0, now)
        word, meaning = band(score, why)
        sw = switches.get(key, {})
        on = board.sector_enabled(key)
        rooms.append({"key": key, "name": name, "score": score, "foot": foot,
                      "why": why, "band": word, "band_means": meaning,
                      "sec": sec,
                      # A stopped sector's desks are not idle, waiting or
                      # blocked — they are off. Every screen reads this, so the
                      # office floor and the control room cannot disagree about
                      # whether a room is working.
                      "agents": ([{**a, "s": "off", "t": "switched off"} for a in agents]
                                 if not on else agents),
                      # The owner's switch, so every screen can draw the room as
                      # deliberately stopped rather than mysteriously idle.
                      "enabled": on,
                      "essential": board.sector_essential(key),
                      "why_essential": board.WHY_ESSENTIAL.get(key.lower(), ""),
                      "off_note": sw.get("note", "")})

    # ---- header metrics ----
    # The same leads the tile opens onto: an address, and not mailed yet.
    leads_today = _one(c, "SELECT count(*) FROM leads WHERE found_at>=? AND "
                          + board.MAILABLE, (day0,))
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
    # WHY THERE IS NO NUMBER, in a sentence he can act on. Showing a blank and
    # leaving him to guess is what turned an honest "no evidence yet" into a
    # screen that just read `null` and told him nothing about how to change it.
    unscored = [r["name"] for r in rooms if r["score"] is None]
    if health is None:
        health_why = (
            "No sector has done anything measurable yet, so there is no honest "
            "number to average. The agency health is the mean of the sectors "
            "that HAVE a score, and a sector earns one the first time it runs, "
            "produces a row, or gets a decision marked. Press Run the day and "
            "this fills in as the sectors report back.")
    elif unscored:
        health_why = (
            f"The average of the {len(scored_rooms)} sector(s) with evidence today. "
            f"Not counted yet: {', '.join(unscored)} — they have not run, produced "
            "anything, or had a decision scored, and averaging a sector in as "
            "zero for having done nothing would report the agency as unhealthy "
            "when it is only untested.")
    else:
        health_why = (f"The average of all {len(scored_rooms)} sectors. Open any "
                      "room in the control room to see exactly what it scored and why.")
    # "Director · 24 tasks on the board" read as though the Director were
    # sitting on twenty-four jobs itself. It is the whole agency's board — work
    # the Director POSTED, waiting at other sectors' desks. Naming where they
    # are turns a number he could do nothing with into a place to look.
    open_all = _one(c, "SELECT count(*) FROM tasks WHERE status IN ('open','in_progress')")
    try:
        where = [(r[0], r[1]) for r in c.execute(
            "SELECT sector, count(*) FROM tasks"
            " WHERE status IN ('open','in_progress') AND sector <> 'Director'"
            " GROUP BY sector ORDER BY 2 DESC, 1").fetchall()]
    except Exception:
        where = []
    if not open_all:
        md_state = "idle — board clear"
    elif where:
        md_state = (f"{open_all} posted, waiting at "
                    + ", ".join(f"{s} ({n})" for s, n in where[:4])
                    + (f" and {len(where) - 4} more" if len(where) > 4 else ""))
    else:
        md_state = f"{open_all} tasks on the board"

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
    # HIS SITE STOPPED ANSWERING THE AGENCY. Said here, because it is the one
    # thing only he can fix (his host's firewall), and because the agency has
    # stopped reading the site until he does or the pause runs out.
    try:
        from . import sitepace
        rest = sitepace.resting()
        if rest:
            until = time.strftime("%H:%M", time.localtime(rest["until"]))
            approvals.append({
                "kind": "site_resting", "id": "site", "lvl": "red · IT",
                "text": f"Your website stopped answering the agency ({rest['why']})",
                "why": ("Your site is probably fine for everyone else: your host's "
                        "firewall has most likely blocked this computer's internet "
                        "address because the agency read the site quickly. The agency "
                        f"is leaving it alone until {until}. To check: open the site on "
                        "your phone using mobile data, not your Wi-Fi. If it opens "
                        "there, ask Hostinger support to unblock your address, or wait. "
                        "Press Approve once it opens on this computer again, and the "
                        "agency reads it again straight away.")})
    except Exception:
        pass
    # WRITTEN PAGES AND DRAFTED MAIL ARE READ ON THE REVIEW PAGE, NOT HERE.
    #
    # "i asked for 1 post and publish to website i do not know if it writes or
    #  not made a section where i can see if it write the post to publish and
    #  mail to sent i will approve both of them"
    #
    # This list showed three site changes and five mails, each as a paragraph
    # of text: a page was a filename and a sentence, never the page itself, and
    # pages held back by the checks were not on it at all. Each is now ONE
    # line here with the count, and a link to /review, where every page can be
    # read in full and every mail read and ticked.
    try:
        n_pages = c.execute("SELECT count(*) FROM site_queue WHERE status='pending'"
                            " AND kind='new_page'").fetchone()[0]
        # A post for his own blog backend waits on him just the same.
        n_posts = c.execute("SELECT count(*) FROM content_queue"
                            " WHERE status='for_blog'").fetchone()[0]
        if n_pages or n_posts:
            approvals.append({
                "kind": "review_pages", "id": "pages", "lvl": "amber · Content",
                "link": "/posts", "count": n_pages + n_posts,
                "text": "; ".join(t for t in (
                    f"{n_pages} page(s) written and waiting for your yes" if n_pages else "",
                    f"{n_posts} post(s) ready to add to your blog" if n_posts else "") if t),
                "why": ("Read each one in full before it goes on your website. A blog post "
                        "goes into your own blog from the Posts room.")})
    except Exception:
        pass
    try:
        # Only changes the Releaser could carry out once he says yes. An empty
        # note (see it_release.plan_of) is not a decision; putting it to him
        # only produced a red "refused" line after he approved it.
        try:
            from pipelines import it_release as _rel
            _can = lambda row: _rel.carryable(dict(row))[0]
        except Exception:
            _can = lambda row: True
        shown = [s for s in c.execute(
            "SELECT id,agent,kind,detail,status FROM site_queue WHERE status='pending'"
            " AND kind!='new_page' ORDER BY queued_at DESC LIMIT 20") if _can(s)][:3]
        for s in shown:
            approvals.append({"kind": "site_change", "id": s["id"],
                              "lvl": "amber · IT",
                              "text": _change_title(s["kind"], s["detail"]),
                              "why": _change_why(s["detail"])
                                     + " — queued by " + (s["agent"] or "IT")})
    except Exception:
        pass
    try:
        mails = [dict(m) for m in c.execute(
            "SELECT company, to_email FROM outreach_queue WHERE status='pending'"
            " ORDER BY queued_at")]
        if mails:
            who = ", ".join((m["company"] or m["to_email"] or "a lead")[:30]
                            for m in mails[:3])
            approvals.append({
                "kind": "review_mails", "id": "mails", "lvl": "amber · Leads",
                "link": "/mail", "count": len(mails),
                "text": f"{len(mails)} outreach mail(s) written — choose who gets them",
                "why": (f"To {who}" + (" and others" if len(mails) > 3 else "")
                        + ". Read each one, tick the ones you want and press Send. "
                          "Nothing has been sent.")})
    except Exception:
        pass
    # The campaign — one mail for every lead — waiting for his yes.
    try:
        camp = c.execute("SELECT id, copy, offer FROM campaigns WHERE status='draft'"
                         " ORDER BY id DESC LIMIT 1").fetchone()
        if camp:
            subject = (json.loads(camp["copy"] or "{}").get("subject") or "").strip()
            approvals.append({
                "kind": "review_campaign", "id": camp["id"], "lvl": "amber · Leads",
                "link": "/mail", "count": 1,
                "text": "Your campaign mail is ready — read it and approve it",
                "why": (f"“{subject}”" if subject else "One mail from the company")
                       + (f", with your offer: {camp['offer']}" if camp["offer"] else "")
                       + ". Nothing goes out until you approve it and tick who gets it."})
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
            "metrics": {"health": health, "health_why": health_why,
                        "scored_sectors": len(scored_rooms), "sectors": len(rooms),
                        "leads": leads_today, "spend": f"${spend:.2f}"},
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
    # 'type', not 'service'. content_queue has never had a service column, so
    # this query has raised OperationalError since the day it was written — and
    # sector_detail catches that with a bare except and returns an empty list,
    # so the Content sheet has quietly shown "Nothing yet" for its whole life
    # however much Content had actually produced. Found by trying to write a
    # row into the table. coordination/results_tests.py now runs every one of
    # these queries against a fresh schema so it cannot happen again.
    "Content": ("content queue",
                "SELECT id, title AS a, COALESCE(brief,'') AS b, status AS c,"
                " COALESCE(type,'') AS d, created_at AS at"
                " FROM content_queue ORDER BY created_at DESC LIMIT 40",
                ("title", "brief", "status", "kind")),
    "SEO": ("keyword intelligence",
            "SELECT id, phrase AS a, evidence AS b, score AS c, sources AS d,"
            " seen_at AS at FROM keyword_intel ORDER BY score DESC LIMIT 40",
            ("phrase", "evidence", "score", "sources")),
    "Leads": ("leads",
              "SELECT id, COALESCE(company,'') AS a, COALESCE(email,'') AS b,"
              " COALESCE(area,'') AS c, stage AS d, found_at AS at"
              " FROM leads WHERE " + board.MAILABLE + " ORDER BY found_at DESC LIMIT 40",
              ("company", "email", "area", "stage")),
    "Report": ("daily reports",
               "SELECT id, day AS a, summary AS b, COALESCE(health_score,'') AS c,"
               " COALESCE(spend_usd,'') AS d, created_at AS at"
               " FROM daily_reports ORDER BY created_at DESC LIMIT 40",
               ("day", "summary", "health", "spend")),
    "Security": ("security events",
                 "SELECT id, kind AS a, COALESCE(detail,'') AS b,"
                 " COALESCE(action,'') AS c, COALESCE(source,'') AS d, at"
                 " FROM security_events ORDER BY at DESC LIMIT 40",
                 ("kind", "detail", "what we did", "source")),
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
    word, meaning = band(score, why)
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
    # `--switch SEO=off "reason"` is the owner's on/off control, reachable from
    # a terminal as well as from the screen. It writes through board so the
    # rules that stop work reaching a stopped sector are the same rules.
    if "--switch" in sys.argv:
        i = sys.argv.index("--switch")
        spec = sys.argv[i + 1]
        note = sys.argv[i + 2] if len(sys.argv) > i + 2 else ""
        name, _, val = spec.partition("=")
        on = val.strip().lower() not in ("off", "0", "false", "no")
        key = next((r[0] for r in SECTORS if r[0].lower() == name.strip().lower()), None)
        if not key:
            print(json.dumps({"ok": False, "error": f"no sector called {name!r}",
                              "sectors": [r[0] for r in SECTORS]}))
        else:
            try:
                board.set_sector(key, on, note)
                print(json.dumps({"ok": True, "sector": key, "enabled": on,
                                  "note": note}))
            except board.SectorEssential as e:
                print(json.dumps({"ok": False, "sector": key, "essential": True,
                                  "error": str(e)}))
    elif "--switches" in sys.argv:
        sw = board.sector_switches()
        print(json.dumps({"ok": True, "sectors": [
            {"key": r[0], "name": r[1],
             "enabled": board.sector_enabled(r[0]),
             "essential": board.sector_essential(r[0]),
             "why_essential": board.WHY_ESSENTIAL.get(r[0].lower(), ""),
             "note": sw.get(r[0], {}).get("note", "")}
            for r in SECTORS]}))
    elif "--sector" in sys.argv:
        print(json.dumps(sector_detail(sys.argv[sys.argv.index("--sector") + 1])))
    else:
        print(json.dumps(build()))
