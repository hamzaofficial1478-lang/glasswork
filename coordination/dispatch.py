"""
dispatch.py — which task an idle desk picks up next, and why that one.

THE QUESTION THIS ANSWERS

The owner asked, about two agents racing for one task: "suppose one task is
urgent and the other is normal, and the normal one wins and the urgent one gets
listed — on which basis is the agent selected?"

Two different things are tangled in that, and separating them is most of the
answer.

  THE RACE          Two agents reaching for THE SAME task. `BEGIN IMMEDIATE` in
                    board.claim_task means exactly one UPDATE lands and exactly
                    one agent wins. There is no wrong winner here: the task is
                    the same task whoever does it, and the loser is not blocked
                    — it comes straight back for the next one. Picking the
                    winner "fairly" would be solving a problem nobody has.

  THE CHOICE        Which task the loser then picks up. THIS is where an urgent
                    task got left behind a normal one, and it was not a race at
                    all — it was the query:

                        SELECT id FROM tasks WHERE sector=? AND status='open'
                        ORDER BY created_at

                    Arrival order. Nothing else. A trivial task posted at 09:00
                    was handed out before a broken enquiry form posted at 09:05,
                    every time, for as long as both existed.

So the fix is not a better race. It is a board that knows what it is holding.

THE SAME TWO HALVES AS THE REQUEST QUEUE

triage.py already solved this for sector-to-sector REQUESTS. Tasks were left on
arrival order — the same bug, one table over. The scoring here is deliberately
the same shape so there is one idea in the building rather than two:

  WEIGHT    what the task is worth, measured from a row somebody can go and
            look at — never from the poster's opinion of its own importance.
  AGE       one point per hour of waiting, capped. A low-weight task WILL
            eventually overtake a high-weight one. That is what stops "we will
            get to it" being a lie.

WHERE THE WEIGHT COMES FROM

A task carries `about` — 'site_issues:7', 'leads:12', 'requests:88' — the row it
exists because of. dispatch reads that row. A task about a lead that has since
gone cold, or a fault already fixed, earns nothing: urgency that was true
yesterday is not urgency today. A task pointing at nothing gets the ordinary
weight, which is correct — "this might be about a customer" is not evidence
that it is.

AND THEN: CAN THIS DESK ACTUALLY DO IT

Ordering the board is half the job. The other half is coordination/desks.py:
the highest-scoring task is skipped by a desk that is not allowed to do it, and
that desk takes the best task it CAN do. So the Watcher never claims a fix, and
the fix is still waiting when the Fixer asks.
"""
import json
import sqlite3
import time

try:
    from . import board, desks, triage
except ImportError:  # pragma: no cover
    import board
    import desks
    import triage

# ---- weights, in the same scale triage uses so the two are comparable -------
W_PERSON_WAITING = 100   # a customer or lead is on the other end
W_ENQUIRY_PATH   = 90    # the way people reach the business is broken
W_SECTOR_BLOCKED = 70    # another sector is standing still until this is done
W_FAULT          = 55    # the Overseer filed it, on evidence
W_NORMAL         = 30    # ordinary planned work
W_BACKGROUND     = 12    # nobody is waiting

AGE_PER_HOUR = triage.AGE_PER_HOUR
AGE_CAP = triage.AGE_CAP

# How long a task named for one desk waits before the name is treated as a
# preference rather than a lock. Six hours is longer than any desk's normal gap
# between runs and short enough that a desk switched off overnight does not
# strand its work until morning.
ORPHAN_AFTER = 6 * 3600

# The rows a task may point at, and what makes that row urgent. Everything in
# triage's list applies — a task about a replied lead is as urgent as a request
# about one — plus the one that only makes sense for tasks.
ABOUT_TABLES = dict(triage.ABOUT_TABLES)
ABOUT_TABLES["requests"] = (
    "SELECT 1 FROM requests WHERE id=? AND status IN ('open','claimed')"
    " AND blocking=1",
    "blocked", "a sector said it cannot continue until this is done "
               "(requests:{id})")


def _conn():
    c = sqlite3.connect(board.DB, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def _ago(secs):
    return triage._ago(secs)


def score(task, now=None, kind=None, note="") -> tuple:
    """
    (points, reasons[]) for one task. Pure: `kind` is the verdict already read
    off the row the task points at, so this function touches no database and is
    trivial to argue with.
    """
    now = int(now or time.time())
    why = []

    if kind == "person":
        weight, label = W_PERSON_WAITING, "a person is waiting on this"
    elif kind == "enquiry":
        weight, label = W_ENQUIRY_PATH, "the enquiry path is broken — money is leaving"
    elif kind == "blocked":
        weight, label = W_SECTOR_BLOCKED, "a sector is standing still behind it"
    elif (task.get("created_by") or "").strip().lower() in ("overseer", "the overseer"):
        weight, label = W_FAULT, "the Overseer filed it after finding a fault"
    elif (task.get("risk") or "").strip().lower() == "background":
        weight, label = W_BACKGROUND, "background work, nobody is waiting"
    else:
        weight, label = W_NORMAL, "ordinary planned work"
    if note:
        label += f" — {note}"
    why.append({"points": weight, "label": label})

    waited = max(0, now - int(task.get("created_at") or now))
    age = min(AGE_CAP, (waited / 3600.0) * AGE_PER_HOUR)
    if age >= 1:
        weight += age
        why.append({"points": round(age, 1),
                    "label": f"open {_ago(waited)} — nothing is left to starve"})
    return round(weight, 1), why


def _about(c, ref):
    """Read the row a task says it is about. (kind, note) or (None, "")."""
    ref = (ref or "").strip()
    if ":" not in ref:
        return None, ""
    table, _, rid = ref.partition(":")
    spec = ABOUT_TABLES.get(table.strip().lower())
    if not spec or not rid.strip().isdigit():
        return None, ""
    sql, kind, note = spec
    try:
        if c.execute(sql, (int(rid),)).fetchone():
            return kind, note.format(id=rid)
    except Exception:
        pass
    return None, ""


def _cols(c):
    try:
        return {r[1] for r in c.execute("PRAGMA table_info(tasks)")}
    except Exception:
        return set()


def board_order(sector, now=None) -> list:
    """
    Every open task in a sector, worst first, with the reasoning attached.

    Ordering, and why each step is a fact rather than a preference:

      1. POINTS, highest first. Weight from the row it is about, plus one point
         per hour waited.
      2. NAMED BEFORE UNNAMED. Two tasks worth the same, one of which somebody
         decided belongs at a particular desk. The decided one goes first — a
         task with a desk on it has already been thought about once.
      3. THEN THE OLDER. Between two equal claims, the one that has already
         waited longer is served.
      4. THEN THE ID. Never a coin-flip. The same board must produce the same
         order twice, or nobody can reproduce a complaint about it.
    """
    now = int(now or time.time())
    c = _conn()
    try:
        board._migrate(c)
        cols = _cols(c)
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM tasks WHERE sector=? AND status='open'"
            " ORDER BY created_at", (sector,))]
        out = []
        for t in rows:
            kind, note = _about(c, t.get("about") if "about" in cols else "")
            pts, why = score(t, now, kind=kind, note=note)
            waited = max(0, now - int(t.get("created_at") or now))
            out.append({
                "id": t["id"], "sector": t["sector"], "title": t.get("title"),
                "detail": t.get("detail"), "risk": t.get("risk"),
                "created_by": t.get("created_by"), "created_at": t.get("created_at"),
                "about": (t.get("about") or "") if "about" in cols else "",
                "desk": (t.get("desk") or "") if "desk" in cols else "",
                "waited": _ago(waited), "waited_secs": waited,
                "points": pts, "why": why,
            })
        out.sort(key=lambda r: (-r["points"],
                                0 if r["desk"] else 1,
                                r["created_at"] or 0,
                                r["id"]))
        for i, r in enumerate(out):
            r["position"] = i + 1
        return out
    finally:
        c.close()


def offer(sector, agent, now=None) -> dict:
    """
    What this desk should pick up, and everything it walked past on the way.

    Returns {sector, agent, take, passed, orphans, total}. `take` is the task to
    claim, or None. `passed` says WHY each higher-scoring task was skipped —
    without that, a desk that keeps coming back empty is indistinguishable from
    a board that is empty.
    """
    now = int(now or time.time())
    ordered = board_order(sector, now)
    take, passed, orphans = None, [], []
    for t in ordered:
        # WAITING ON THE OWNER IS NOT WAITING FOR A DESK.
        #
        # board.claim_task refuses an unapproved amber or red task — that is
        # what the approval means. This has to agree with it: without the check
        # here, dispatch offered the task, the claim was refused, and the desk
        # reported "it is claimable now, run again" for ever. Two parts of the
        # same decision disagreeing is worse than either answer.
        if (t.get("risk") or "green").strip().lower() in ("amber", "red"):
            passed.append({"id": t["id"], "title": t["title"],
                           "points": t["points"],
                           "why": "waiting on the owner's yes — approve it on "
                                  "'Awaiting you' and the desk can start"})
            continue
        v = desks.verdict(agent, t)
        if v["allowed"]:
            if take is None:
                take = dict(t, why_me=v["why"], desk_score=v["score"])
                continue
            passed.append({"id": t["id"], "title": t["title"],
                           "points": t["points"], "why": "already taking a "
                           "higher-scoring one this pass"})
            continue

        # NOT ALLOWED. If it is only the desk NAME standing in the way, and the
        # named desk has left it sitting long enough, hand it to a desk that
        # positively does this kind of work. A name is a routing decision, not
        # a reservation for ever — but the `never` rules are never relaxed, so
        # the Watcher still cannot inherit an abandoned fix.
        stale = t["waited_secs"] >= ORPHAN_AFTER
        if stale and t["desk"]:
            free = desks.verdict(agent, dict(t, desk=""))
            if free["allowed"] and free["score"] >= 10:
                orphans.append({"id": t["id"], "named": t["desk"],
                                "waited": t["waited"]})
                if take is None:
                    take = dict(t, why_me=(f"named for {t['desk']}, which has "
                                           f"left it {t['waited']} — "
                                           + free["why"]),
                                desk_score=free["score"])
                    continue
        passed.append({"id": t["id"], "title": t["title"],
                       "points": t["points"], "why": v["why"]})
    return {"ok": True, "sector": sector, "agent": agent, "now": now,
            "take": take, "passed": passed, "orphans": orphans,
            "total": len(ordered)}


def _held_by(agent, skip=()):
    """
    The task this desk already claimed and never finished, oldest first.

    Read straight from the board rather than through `offer`, because offer
    scores OPEN work and this is not open — it belongs to this desk already.

    `skip` is what stops this becoming a livelock. One agent name can cover two
    passes — it-watcher is both the health check and the change watcher — and a
    pass that has just looked at a job and said "not mine" must not be handed
    the same job again on the next breath, for ever. deskwork passes the ids it
    has already put down.
    """
    skip = {str(s) for s in (skip or ())}
    try:
        with _conn() as c:
            rows = c.execute(
                "SELECT * FROM tasks WHERE status='in_progress' AND owner=?"
                " ORDER BY claimed_at", (agent or "",)).fetchall()
        for r in rows:
            if str(r["id"]) not in skip:
                return dict(r)
        return None
    except Exception:
        return None


def next_task(sector, agent, now=None, skip=()):
    """
    Claim the task this desk should be doing, and return it. None if there is
    nothing it can take.

    The loop matters: `offer` reads the board, then the claim races. Losing the
    race is normal — another desk got there first — so it asks again with that
    task now gone rather than giving up. The scan is bounded by the board, and
    each pass is strictly shorter than the last, so it terminates.
    """
    # FINISH WHAT YOU ARE ALREADY HOLDING FIRST.
    #
    # "if they had some task left behind those agents will complete their
    #  previous recorded task first"
    #
    # Nothing stopped a desk claiming a second job while still holding the
    # first. next_open_task only ever looked at status='open', so an in-progress
    # claim was invisible to the very desk that made it: the desk took a new
    # job every morning, the old claim aged, and eventually the Overseer filed
    # it as abandoned — by the same desk that was busy doing something else.
    # That is how a claim reaches ninety hours old on a desk that runs daily.
    #
    # Handed straight back, before the board is even read. It is already this
    # desk's work and no scoring can rank anything above it.
    held = _held_by(agent, skip=skip)
    if held:
        held["why_me"] = ("you claimed this and did not finish it — it comes "
                          "before anything new")
        return held

    # THE DAY GATE — and note it is BELOW the held-work check, deliberately.
    #
    # "until i did not start the day they should not start the work if they had
    #  some task left behind those agents will complete their previous recorded
    #  task first"
    #
    # Both halves of that sentence, in the only order that satisfies both.
    # Finishing something you already hold is not starting work, so it happens
    # whether the day is open or not — gating it would leave a claim aging
    # until the Overseer filed it as abandoned, which is the ninety-hour
    # deadlock rebuilt on purpose. Taking something NEW off the board is
    # starting work, and that waits for him.
    #
    # See board.day_is_open for when the gate is in force and how he stands it
    # down from his own terminal.
    day = board.day_is_open()
    if day["enforced"] and not day["open"]:
        return None

    seen = {str(s) for s in (skip or ())}
    while True:
        o = offer(sector, agent, now)
        t = o.get("take")
        if not t or str(t["id"]) in seen:
            return None
        seen.add(str(t["id"]))
        if board.claim_task(t["id"], agent):
            got = board.get_task(t["id"])
            if got is not None:
                got = dict(got)
                got["why_me"] = t.get("why_me", "")
                got["points"] = t.get("points")
                got["position"] = t.get("position")
            return got


def nothing_here(sector, agent) -> dict:
    """
    The standard result for a desk that came back empty-handed.

    `ok: False` because no work was done and nothing must read this as a
    success. `idle: True` because DOING NOTHING WRONG IS NOT FAILING, and the
    two were the same thing on his screen:

        "its work failed: Leads collects from OpenStreetMap — Nothing open on
         the Leads board. This desk works from the board, never on its own
         initiative."

    Nothing failed there. Another Leads desk had already taken the one job on
    the board, which is the coordination layer working exactly as designed. It
    read as a red step because these pipelines exited 1, and an exit code is
    all the runner had to go on. See deskwork.exit_code.
    """
    return {"ok": False, "idle": True, "error": why_nothing(sector, agent)}


def why_nothing(sector, agent) -> str:
    """
    One honest sentence for a pipeline that found nothing to claim.

    "No open task on the board" is true and useless when there ARE three open
    tasks and this desk is forbidden all of them. A desk that comes back empty
    should say which it walked past and why, or the owner sees an idle sector
    and no reason for it.
    """
    # THE DAY HAS NOT STARTED is the first thing to say, and it must be said
    # before the board is even counted. Reporting "nothing open on the SEO
    # board" while six tasks sit on it, because the real reason is that he has
    # not pressed Run, is the exact shape of misleading message the owner keeps
    # catching — true in the letter, useless in the fact.
    day = board.day_is_open()
    if day["enforced"] and not day["open"]:
        waiting = board.open_task_count(sector)
        return (f"The day has not started, so no desk takes anything new — "
                f"{day['why']}. "
                + (f"{waiting} task(s) are waiting on the {sector} board and "
                   f"will be picked up the moment you start the day. "
                   if waiting else "")
                + "Press Run in the office, or run "
                  "'python -m pipelines.day_run --start'. To work one pipeline "
                  "outside the day, set WORK_OUTSIDE_THE_DAY=1. Anything this "
                  "desk had already claimed is finished first either way.")

    o = offer(sector, agent)
    if not o["total"]:
        return (f"Nothing open on the {sector} board. This desk works from the "
                "board, never on its own initiative.")
    if o["take"]:
        # Something IS claimable now, which means it appeared, or was released,
        # between the desk failing to claim and this being asked. Say that
        # rather than the flat contradiction of listing work it could do under
        # a heading saying there is none.
        return (f"{o['total']} task(s) open on the {sector} board. "
                f"{o['take']['title'][:60]!r} is claimable now — it was taken by "
                "another desk mid-pass, or arrived just after this one looked. "
                "Run again.")
    if not o["passed"]:
        return (f"{o['total']} task(s) open on the {sector} board, none of them "
                "claimable — another desk got there first this pass.")
    lines = [f"{o['total']} task(s) open on the {sector} board, none of them for "
             f"{agent}:"]
    for p in o["passed"][:4]:
        lines.append(f"  · {p['title'][:60]} — {p['why']}")
    if len(o["passed"]) > 4:
        lines.append(f"  · and {len(o['passed']) - 4} more")
    named = [p for p in o["passed"] if "posted for" in (p["why"] or "")]
    if named:
        lines.append("The desk each is waiting for has not run. If it never "
                     "does, this work never gets done.")
    return "\n".join(lines)


def unclaimed(sector=None, older_than=ORPHAN_AFTER, now=None) -> list:
    """
    Open tasks nobody has picked up in a long time, and who they are waiting on.

    Handing a task to the right desk creates a failure the old free-for-all did
    not have: if that desk never runs, the task waits for ever and looks like a
    quiet board rather than a stuck one. This is what makes it visible.
    """
    now = int(now or time.time())
    c = _conn()
    try:
        board._migrate(c)
        cols = _cols(c)
        q = "SELECT * FROM tasks WHERE status='open'"
        args = []
        if sector:
            q += " AND sector=?"
            args.append(sector)
        out = []
        for r in c.execute(q + " ORDER BY created_at", args):
            t = dict(r)
            waited = now - int(t.get("created_at") or now)
            if waited < older_than:
                continue
            out.append({"id": t["id"], "sector": t["sector"], "title": t["title"],
                        "desk": (t.get("desk") or "") if "desk" in cols else "",
                        "waited": _ago(waited), "waited_secs": waited,
                        "created_by": t.get("created_by")})
        return out
    finally:
        c.close()


def as_text(sector, agent=None) -> str:
    ordered = board_order(sector)
    L = [f"{sector.upper()} BOARD — "
         + (f"{len(ordered)} open task(s)" if ordered else "nothing open")]
    for t in ordered:
        L.append(f"  {t['position']:>2}. [{t['points']:>5}] {t['title'][:58]}"
                 f"  ({t['waited']} open"
                 + (f", for {t['desk']}" if t["desk"] else "") + ")")
        for w in t["why"]:
            L.append(f"          +{w['points']:<5} {w['label']}")
    if agent:
        o = offer(sector, agent)
        L.append("")
        if o["take"]:
            L.append(f"{agent} would take #{o['take']['position']} "
                     f"{o['take']['title'][:50]} — {o['take']['why_me']}")
        else:
            L.append(f"{agent} can take nothing on this board")
        for p in o["passed"][:6]:
            L.append(f"   walked past {p['title'][:44]}: {p['why']}")
    return "\n".join(L)


def main():
    import argparse
    ap = argparse.ArgumentParser(
        description="Which task a desk should pick up next, and why that one.")
    ap.add_argument("sector")
    ap.add_argument("agent", nargs="?", default=None)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.json:
        print(json.dumps(offer(a.sector, a.agent) if a.agent
                         else {"board": board_order(a.sector)}))
    else:
        print(as_text(a.sector, a.agent))


if __name__ == "__main__":
    main()
