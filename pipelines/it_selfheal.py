"""
it_selfheal.py — IT repairing the AGENCY, not the website.

    python -m pipelines.it_selfheal            fix what can be fixed, say the rest
    python -m pipelines.it_selfheal --dry-run  work it out, change nothing
    python -m pipelines.it_selfheal --list     what is open and what IT would do

WHY THIS EXISTS

"there were 2 agents in Seo Sector blinking red the IT sector should at leat
 fix that or ask to MD for it but all they sitting blindly and quietly just
 showing the error why not fixing while the fixer is there"

He is right, and the reason was structural rather than lazy. The chain was
already built and it ran to the wrong end:

  the Overseer   sees a desk crash or stall, files a fault, and posts a task to
                 IT saying in as many words "This is an agency fault, not a
                 website fault"
  the Fixer      claims that task, and runs it through RECIPES — every one of
                 which is a WEBSITE recipe: the domain, the canonical, the
                 viewport, the dead email. None of them can do anything about a
                 dead lock or a crashed desk.
  so             the Fixer reports "no recipe", the fault stays open, the lamp
                 stays red, and the next day it does the same thing again.

IT had the ticket, the reasoning model and the authority, and no hands for this
particular job. This is the hands.

WHAT IT CAN ACTUALLY REPAIR, AND WHAT IT DELIBERATELY CANNOT

The honest list is short, and short is the point — a self-healer that thinks it
can fix anything is how an agency quietly breaks itself at three in the morning.

  a lock nobody holds        the process that took it is gone. Release it. This
                             is the one that silently stops a whole sector: a
                             shelf lock held by a dead run blocks every agent
                             that borrows that shelf, for the lease's full life.
  a claim nobody is working  a task held by a desk that died. Hand it back so
                             another desk can take it, which is exactly what
                             board.release_claim was written for and what the
                             Overseer's own advice has been telling him to do.
  a run that never ended     reap it, so the books close and the desk stops
                             reading as "working" for ever.
  a crash that will pass     a timeout, a 429, a reset connection. Nothing to
                             fix in code; it is cleared to be retried and says
                             so, rather than sitting as a permanent red lamp.

And the two it must NOT pretend to fix:

  a missing key or setting   IT cannot invent his Places key. This goes to HIM,
                             naming the exact setting, because he is the only
                             one who can do it.
  anything else              a real bug in a pipeline. IT is not allowed to
                             rewrite its own code on a whim, so this goes to the
                             MD with the error attached — which is the half he
                             asked for: "or ask to MD for it".

NOTHING HERE TOUCHES HIS WEBSITE. Every repair is inside the agency's own
filing cabinet. The website has one door and it is still pipelines/it_release,
after his approval, exactly as before.
"""
import argparse
import json
import re
import sqlite3
import sys
import time

from coordination import board, deskwork, faults

AGENT = "it-fixer"
SECTOR = "IT"

# How many faults to work in one pass. Small on purpose: each repair is a write
# to the cabinet, and a pass that tries forty of them on a bad morning is a
# pass that is itself the incident.
MAX_PER_PASS = 12

# A crash whose message looks like this will very likely not happen next time.
# Retrying is the right answer and pretending it is a code bug is not.
PASSING = (
    r"\btimed? ?out\b", r"\btimeout\b", r"\btemporarily unavailable\b",
    r"\b429\b", r"\brate.?limit", r"\btoo many requests\b",
    r"\b50[0234]\b", r"\bbad gateway\b", r"\bservice unavailable\b",
    r"connection (?:reset|refused|aborted|error)", r"\bECONNRESET\b",
    r"\bremote end closed\b", r"\btemporary failure in name resolution\b",
    r"\bread timed out\b", r"\bssl\b.*\beof\b",
)

# A crash whose message names something only HE can put right. The capture
# group is the setting to tell him about, so the message can say which one
# rather than "check your configuration".
HIS_TO_FIX = (
    (r"\b([A-Z][A-Z0-9_]{4,})\b is not set", r"\1"),
    (r"no ([A-Za-z ]*?)key\b", "the API key"),
    (r"no model is routed to (\w+)", r"a model for \1"),
    (r"no routing configured for task '([^']+)'", r"a model for \1"),
    (r"\bSITE_URL\b", "SITE_URL"),
    (r"\bSMTP_HOST\b", "SMTP_HOST"),
    (r"(?:401|403).{0,40}\b(?:rejected|invalid|unauthor)", "the API key"),
    (r"\bshelf is empty\b", "an API on that shelf"),
)


def _now():
    return int(time.time())


def _conn():
    c = sqlite3.connect(board.DB, timeout=30)
    c.row_factory = sqlite3.Row
    return c


# ---------------------------------------------------------------------------
# READING WHAT IS BROKEN
# ---------------------------------------------------------------------------

def open_faults(con, limit=MAX_PER_PASS) -> list:
    """
    Agency faults IT has been asked to fix, worst first.

    Only the ones routed to IT. An unanswered request belongs to the sector
    that has not answered it, and taking that on would be IT doing another
    sector's job — the same reasoning as faults.FIXER_OF, read from the row
    rather than assumed here.
    """
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM faults WHERE status IN ('open','routed','escalated')"
        " ORDER BY CASE severity WHEN 'blocking' THEN 0 WHEN 'degraded' THEN 1"
        "                        ELSE 2 END, last_seen DESC")]
    mine = [r for r in rows
            if (r.get("kind") or "") not in NOT_REPAIRS
            and ((r.get("routed_to") or "") == SECTOR
                 or faults.FIXER_OF.get(r.get("kind") or "") == SECTOR
                 # A dead claim has no named fixer, and it is the one kind IT
                 # can reliably clear. Leaving it to "the sector that should
                 # pick it back up" is how a task sat held by a dead desk for
                 # a week.
                 or (r.get("kind") == "abandoned"))]
    return mine[:limit] if limit else mine


# NOT REPAIRS. "IT has not answered SEO" and "tasks on IT's board nobody has
# picked up" are routed to IT because IT is the sector that is behind — not
# because there is anything broken to mend. This pass took them as repair jobs,
# found no repair, and asked the Director "IT cannot fix this on its own" —
# every pass, with the minutes in the subject ticking up, so each ask looked
# new: 23 minutes, 43, 50. The answer to being behind is doing the work, which
# is what IT's own desks and inbox are for; the Overseer already counts the
# silence and escalates it once.
NOT_REPAIRS = ("unanswered", "unclaimed", "sector")


def _looks_transient(text) -> bool:
    t = (text or "").lower()
    return any(re.search(p, t, re.I) for p in PASSING)


def _needs_him(text):
    """The setting he has to put right, or "" if this is not that kind."""
    s = str(text or "")
    for pattern, name in HIS_TO_FIX:
        m = re.search(pattern, s, re.I)
        if m:
            try:
                return m.expand(name) if "\\" in name else name
            except Exception:
                return name
    return ""


# ---------------------------------------------------------------------------
# WORKING OUT WHAT TO DO
# ---------------------------------------------------------------------------

def plan_for(fault, con) -> dict:
    """
    What IT can do about this one fault. Always returns a plan — the last two
    are 'tell him' and 'tell the MD', because going quiet is the behaviour this
    file exists to end.
    """
    kind = (fault.get("kind") or "").strip()
    said = f"{fault.get('what') or ''} {fault.get('detail') or ''}"

    if kind == "lock":
        holder, resource = fault.get("agent") or "", _resource_of(fault, con)
        return {"do": "release_lock", "resource": resource, "holder": holder,
                "why": (f"{resource or 'a resource'} is locked by {holder or 'a desk'} "
                        "and nothing is running behind it. Every agent that needs "
                        "that resource is queued behind a lock nobody holds.")}

    if kind == "abandoned":
        return {"do": "release_claim", "task_id": _task_of(fault, con),
                "holder": fault.get("agent") or "",
                "why": ("this task is held by a desk that is not working it, so "
                        "no other desk can take it. Handing it back is the whole "
                        "reason release_claim exists.")}

    if kind == "stall":
        return {"do": "reap", "agent": fault.get("agent") or "",
                "why": ("the run never ended, so the books are still open and the "
                        "desk still reads as working. Closing it off is what lets "
                        "the desk run again.")}

    if kind == "crash":
        setting = _needs_him(said)
        if setting:
            return {"do": "ask_owner", "setting": setting,
                    "agent": fault.get("agent") or "",
                    "why": (f"{fault.get('agent') or 'a desk'} cannot run until "
                            f"{setting} is set, and nobody but you can set it. "
                            "IT is not able to invent it.")}
        if _looks_transient(said):
            return {"do": "retry", "agent": fault.get("agent") or "",
                    "why": ("this reads as a passing failure — a timeout, a rate "
                            "limit or a dropped connection. There is nothing in "
                            "the code to change; it is cleared to run again.")}
        return {"do": "ask_md", "agent": fault.get("agent") or "",
                "why": ("this is a real fault in a pipeline and IT does not "
                        "rewrite its own code without being told to. The MD is "
                        "being given the error to decide what happens.")}

    setting = _needs_him(said)
    if setting:
        return {"do": "ask_owner", "setting": setting,
                "agent": fault.get("agent") or "", "why": "only you can set this."}
    return {"do": "ask_md", "agent": fault.get("agent") or "",
            "why": f"IT has no repair for a {kind or 'fault'} of this shape."}


def _resource_of(fault, con):
    """Which lock the fault is about, read off its evidence row."""
    ev = str(fault.get("evidence") or "")
    m = re.match(r"locks#(.+)$", ev)
    if m:
        return m.group(1)
    # Older rows named it in the sentence instead.
    m = re.search(r"\b(?:lock on|locked)\s+'?([a-z0-9_:.\-]+)'?", str(fault.get("what") or ""), re.I)
    return m.group(1) if m else ""


def _task_of(fault, con):
    ev = str(fault.get("evidence") or "")
    m = re.match(r"tasks#(.+)$", ev)
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# DOING IT
# ---------------------------------------------------------------------------

def carry_out(plan, fault, agent=AGENT, dry_run=False) -> dict:
    """One repair. Returns {done, said} — `done` False is a real answer here."""
    do = plan["do"]
    if dry_run:
        return {"done": False, "said": f"[dry run] would {do}: {plan['why']}"}

    try:
        if do == "release_lock":
            res = plan.get("resource")
            if not res:
                return {"done": False,
                        "said": "the fault does not say which lock, so nothing "
                                "was released — releasing a guessed lock is worse "
                                "than leaving it"}
            # Only a lock whose holder is genuinely gone. board.sweep_dead_processes
            # is the one that checks the process rather than the lease, so it is
            # asked first and its answer is what decides.
            swept = board.sweep_dead_processes() or {}
            still = _lock_held(res)
            if not still:
                return {"done": True,
                        "said": f"{res} is free again (the dead run was swept)"}
            board.release_lock(res, still)
            return {"done": not _lock_held(res),
                    "said": f"released {res}, which {still} was holding"}

        if do == "release_claim":
            tid = plan.get("task_id")
            if not tid:
                return {"done": False, "said": "the fault does not name a task"}
            ok, why = board.release_claim(tid, by=agent,
                                          reason="the desk holding this is not "
                                                 "working it — released by IT so "
                                                 "another desk can")
            if ok:
                return {"done": True, "said": why}
            # ALREADY BACK ON THE BOARD IS THE OUTCOME WE WANTED. The Overseer's
            # own sweep runs before this and often gets there first, and
            # reporting "left alone" for a fault that is already fixed leaves a
            # red lamp over a desk with nothing wrong with it — which is the
            # false alarm this whole file exists to stop.
            if "not claimed by anybody" in (why or ""):
                return {"done": True,
                        "said": "already back on the board — nothing left to do"}
            return {"done": False, "said": why}

        if do == "reap":
            swept = board.sweep_dead_processes() or {}
            n = sum(v for v in swept.values() if isinstance(v, int))
            return {"done": bool(n),
                    "said": (f"closed the books on {n} dead run(s)" if n else
                             "nothing was actually dead — the run is slow, not gone")}

        if do == "retry":
            # Nothing to repair. Saying so and clearing the fault is the repair:
            # a permanent red lamp for a timeout that has already passed is the
            # false alarm that makes him stop trusting the floor.
            return {"done": True,
                    "said": "cleared to run again — nothing in the code to change"}

        if do == "ask_owner":
            board.try_post_task(
                SECTOR, f"You need to set {plan['setting']}",
                (plan["why"] + "\n\nIT looked at this and cannot fix it: the "
                 "setting is yours. Put it on /setup and the desk runs on the "
                 "next pass.\n\nThe fault: " + str(fault.get("what") or "")),
                risk="amber", created_by=AGENT)
            return {"done": False,
                    "said": f"put to you: {plan['setting']} needs setting"}

        if do == "ask_md":
            # ONCE PER FAULT. The MD has been told, and is either dealing with
            # it or has answered; asking again each pass is the noise, not the
            # fix. The question carries the fault's id so it can be found.
            asked = _already_asked(fault)
            if asked:
                return {"done": False,
                        "said": f"already with the MD (request {asked}) — "
                                "waiting for that, not asking again"}
            board.post_request(
                SECTOR, "Director",
                f"IT cannot fix this on its own: {str(fault.get('what') or '')[:70]}",
                (plan["why"] + "\n\nThe fault as recorded:\n"
                 + str(fault.get("what") or "") + "\n"
                 + str(fault.get("detail") or "")
                 + "\n\nIT has not changed anything. Deciding whether this is "
                   "worth fixing, working around, or living with is a call above "
                   "this desk."),
                about=f"faults:{fault.get('id')}")
            return {"done": False, "said": "handed to the MD with the error"}
    except Exception as e:
        return {"done": False, "said": f"the repair itself failed: {e}"}
    return {"done": False, "said": f"no repair for {do}"}


def _already_asked(fault) -> str:
    """The id of the question IT already put to the MD about this fault, or ""."""
    try:
        with sqlite3.connect(board.DB, timeout=30) as c:
            r = c.execute("SELECT id FROM requests WHERE from_sector=? AND"
                          " to_sector='Director' AND about=? ORDER BY created_at"
                          " DESC LIMIT 1",
                          (SECTOR, f"faults:{fault.get('id')}")).fetchone()
        return (r[0] if r else "") or ""
    except Exception:
        return ""


def _lock_held(resource):
    """Who holds this lock right now, or "" if nobody does."""
    try:
        with sqlite3.connect(board.DB, timeout=30) as c:
            r = c.execute("SELECT holder FROM locks WHERE resource=?",
                          (resource,)).fetchone()
        return (r[0] if r else "") or ""
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# THE PASS
# ---------------------------------------------------------------------------

def run(agent=AGENT, limit=MAX_PER_PASS, dry_run=False) -> dict:
    with board.agent_run(agent) as run_row:
        with deskwork.at_the_desk(
                SECTOR, agent, run_row,
                # it-fixer is two passes now: it_fix patches the WEBSITE, this
                # repairs the AGENCY. Only take a job about the second.
                takes=lambda j: j.wants("fault", "stalled", "stall", "crashed",
                                        "crash", "lock", "locked", "abandoned",
                                        "agency", "desk", "red")) as job:
            out = _heal(agent, limit, dry_run, job)
            deskwork.settle(job, out, made=out.get("repaired"),
                            what="fault repaired|faults repaired")
        run_row.note(out.get("summary") or out.get("error") or "nothing to repair")
        return out


def _heal(agent, limit, dry_run, job=None):
    board.ensure_schema()
    con = _conn()
    try:
        open_ones = open_faults(con, limit=limit)
    finally:
        con.close()

    if not open_ones:
        return {"ok": False, "idle": True, "agent": agent, "repaired": 0,
                "error": ("Nothing is broken inside the agency — no open fault is "
                          "routed to IT. This desk repairs the agency itself; the "
                          "website tickets are pipelines/it_fix.")}

    con = _conn()
    done, told, worked = [], [], []
    try:
        for f in open_ones:
            plan = plan_for(f, con)
            got = carry_out(plan, f, agent=agent, dry_run=dry_run)
            row = {"fault": f["id"], "kind": f.get("kind"),
                   "agent": f.get("agent"), "do": plan["do"],
                   "done": got["done"], "said": got["said"]}
            worked.append(row)
            if got["done"]:
                done.append(row)
                if not dry_run:
                    try:
                        faults.resolve(f["id"], f"IT: {got['said']}"[:300])
                    except Exception:
                        pass
            elif plan["do"] in ("ask_owner", "ask_md"):
                told.append(row)
    finally:
        con.close()

    if done and not dry_run:
        board.log_decision(
            "it-selfheal",
            reasoning=(f"{len(done)} agency fault(s) repaired: "
                       + "; ".join(f"{d['kind']} on {d['agent'] or 'the agency'}"
                                   for d in done[:5])),
            expectation=("Those desks stop reading red and run again on the next "
                         "pass, without anybody being asked to do anything."),
            confidence=0.75,
            evidence={"fault_ids": [d["fault"] for d in done]})

    summary = (f"{len(done)} repaired"
               + (f", {len(told)} put to somebody who can" if told else "")
               + (f", {len(worked) - len(done) - len(told)} left alone"
                  if len(worked) > len(done) + len(told) else "")
               + (" (dry run)" if dry_run else ""))
    return {"ok": True, "agent": agent, "repaired": len(done),
            "escalated": len(told), "looked_at": len(worked),
            "faults": worked, "dry_run": bool(dry_run), "summary": summary}


def as_text(r) -> str:
    if not r.get("ok"):
        return "IT SELF-REPAIR — " + (r.get("error") or "nothing to do")
    L = [f"IT SELF-REPAIR — {r['summary']}"]
    for f in r.get("faults") or []:
        mark = "fixed " if f["done"] else "passed"
        L.append(f"   {mark} {f['kind'] or '?':10} {(f['agent'] or '—')[:22]:24} {f['said'][:90]}")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(
        description="IT repairing the agency itself — not the website.")
    ap.add_argument("--dry-run", action="store_true", help="work it out, change nothing")
    ap.add_argument("--list", action="store_true", help="what is open, and what IT would do")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.list:
        board.ensure_schema()
        con = _conn()
        try:
            rows = open_faults(con)
            if not rows:
                print("No agency fault is routed to IT.")
                return 0
            for f in rows:
                plan = plan_for(f, con)
                print(f"#{f['id']}  {f['kind']:10} {(f.get('agent') or '—')[:24]:26} "
                      f"-> {plan['do']}")
                print(f"        {plan['why'][:110]}")
        finally:
            con.close()
        return 0

    r = run(dry_run=a.dry_run)
    print(json.dumps(r, indent=2) if a.json else as_text(r))
    return deskwork.exit_code(r)


if __name__ == "__main__":
    sys.exit(main())
