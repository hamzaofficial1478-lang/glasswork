"""
leftovers.py — what the last session left behind, and putting it right before
anything new starts.

    python -m coordination.leftovers --json         what is left (reads only)
    python -m coordination.leftovers --tidy --json  clear up what needs no thinking
    python -m coordination.leftovers --put-away --json
                                                    his "delete it" choice

THE OWNER'S POINT

  "what about the tasks that were unanswered or that was in queue? ... if we do
   not either delete or finish those unanswered or queued tasks they can make
   garbage for us and also the double load on models"

He is right, and the shape of it is specific. When the program is closed part
way through a day — or a run is stopped, or crashes — four kinds of thing are
left in the cabinet:

  claims held by a process that no longer exists   a task or a question nobody
                                                    can pick up, because it is
                                                    "taken"
  copies                                            the same job twice on one
                                                    board, the same question
                                                    asked twice, both of which
                                                    cost a model call each
  paperwork                                         chases and hand-offs that
                                                    need an answer, not a brain
  orphans                                           a thread still waiting on a
                                                    job that was withdrawn days
                                                    ago, which the Overseer
                                                    counts as unanswered for ever

and one kind that is simply unfinished: the steps of the last run that never
ran. The next run used to start from the top, on top of all of it — the MD
planning fresh work beside the old, the desks re-asking what was already asked.

THREE THINGS, IN ORDER OF HOW MUCH THEY DO

  survey()    reads. Nothing changes. The office shows it as "left over from
              last time".
  tidy()      clears what needs no thinking and no model: dead claims back on
              the board, copies folded into one, chases and hand-offs answered,
              orphans closed, stale work withdrawn, cleared faults resolved.
              Every run now starts with this (pipelines/day_run.start).
  put_away()  his decision to let the rest go: tidy, then every remaining
              unanswered question is closed with a note and every queued job
              withdrawn. Nothing is deleted — each row keeps the reason, so the
              history is intact. His own decisions (amber and red) and jobs he
              posted himself are never touched.

Resuming — re-running only the steps that did not finish, plus the sectors
that still have work waiting — is pipelines/day_run.start(resume=True), which
calls tidy() first.
"""
import argparse
import json
import re
import sqlite3
import sys
import time

try:
    from . import board
except ImportError:  # pragma: no cover
    import board

TIDY_AGENT = "agency-tidy"

# Step outcomes that mean the step did its work (or was deliberately left out).
SETTLED = ("ok", "idle", "skipped")
# And the ones that mean it did not get to finish.
UNFINISHED = ("waiting", "running", "stopped", "failed")


def _now():
    return int(time.time())


def _conn():
    c = sqlite3.connect(board.DB, timeout=30)
    c.row_factory = sqlite3.Row
    board._migrate(c)
    return c


def _rows(c, q, args=()):
    try:
        return [dict(r) for r in c.execute(q, args)]
    except sqlite3.Error:
        return []


def _shape(text) -> str:
    """Two titles that differ only in numbers or spacing are the same job."""
    t = str(text or "").strip().lower()
    t = re.sub(r"\d+(?:[.,]\d+)?", "#", t)
    return re.sub(r"\s+", " ", t)[:200]


def _alive_agents(c) -> set:
    """Desks with a run going right now, in a process that still exists."""
    alive = set()
    for r in _rows(c, "SELECT agent, pid, host FROM agent_runs WHERE status='running'"):
        try:
            if not r.get("pid") or board.process_alive(r["pid"], r.get("host") or ""):
                alive.add((r["agent"] or "").strip().lower())
        except Exception:
            alive.add((r["agent"] or "").strip().lower())
    return alive


def _day_running(c) -> bool:
    for r in _rows(c, "SELECT pid, host FROM day_runs WHERE status='running'"):
        try:
            if not r.get("pid") or board.process_alive(r["pid"], r.get("host") or ""):
                return True
        except Exception:
            return True
    return False


# ---------------------------------------------------------------------------
# WHAT IS LEFT
# ---------------------------------------------------------------------------

def last_run(c=None) -> dict:
    """The most recent day run, and which of its steps never finished."""
    own = c is None
    c = c or _conn()
    try:
        r = _rows(c, "SELECT * FROM day_runs ORDER BY started_at DESC LIMIT 1")
        if not r:
            return {}
        run = r[0]
        steps = _rows(c, "SELECT seq, label, module, status, summary FROM day_steps"
                         " WHERE run_id=? ORDER BY seq", (run["id"],))
        alive = run["status"] == "running" and _day_running(c)
        interrupted = run["status"] in ("stopped", "stalled", "failed") or (
            run["status"] == "running" and not alive)
        # A clean finish with a failed step is not "left part-way": the step
        # failed for a reason and the next ordinary run tries it again. Only a
        # run that was cut off has steps waiting to be picked up.
        unfinished = [s for s in steps if s["status"] in UNFINISHED] if interrupted else []
        return {"id": run["id"], "status": run["status"], "running": alive,
                "started_at": run.get("started_at") or 0,
                "ended_at": run.get("ended_at") or 0,
                "interrupted": interrupted, "total": len(steps),
                "finished": sum(1 for s in steps if s["status"] in SETTLED),
                "unfinished": [{"seq": s["seq"], "label": s["label"],
                                "module": s["module"], "status": s["status"]}
                               for s in unfinished]}
    finally:
        if own:
            c.close()


def _requests(c):
    """Every question still waiting, sorted into what kind of waiting it is."""
    from . import inbox
    rows = _rows(c, "SELECT r.*, (SELECT count(*) FROM request_msgs m"
                    "  WHERE m.request_id=r.id AND m.author=r.handler) AS replies"
                    " FROM requests r WHERE r.status IN ('open','claimed')"
                    " ORDER BY r.created_at")
    tasks_for = {}
    for t in _rows(c, "SELECT id, status, result, about FROM tasks"
                      " WHERE about LIKE 'requests:%'"):
        tasks_for.setdefault(t["about"].split(":", 1)[1], []).append(t)
    alive = _alive_agents(c)
    out = []
    for r in rows:
        kind = "question"
        if r["status"] == "claimed" and r["replies"]:
            linked = tasks_for.get(r["id"]) or []
            if linked and all(t["status"] not in ("open", "in_progress") for t in linked):
                kind = "orphan"          # its job ended; the thread never closed
            else:
                kind = "filed"           # answered "taken", waiting on its job
        elif r["status"] == "claimed" and (r["handler"] or "").lower() not in alive:
            kind = "dead_claim"
        elif inbox.is_a_chase(r):
            kind = "chase"
        elif inbox.handoff_kind(r):
            kind = "handoff"
        out.append({**r, "kind": kind})
    return out


def _duplicate_requests(reqs):
    """Open questions that are copies of an earlier one. Returns [(copy, kept)]."""
    groups = {}
    for r in reqs:
        if r["status"] != "open":
            continue
        about = (r.get("about") or "").strip().lower()
        if about and not about.startswith("requests:"):
            key = ("about", r["to_sector"], about)
        else:
            key = ("subject", r["to_sector"], r["from_sector"], _shape(r["subject"]))
        groups.setdefault(key, []).append(r)
    pairs = []
    for key, rs in groups.items():
        if len(rs) < 2:
            continue
        rs = sorted(rs, key=lambda x: x.get("created_at") or 0)
        # A newer hand-off supersedes an older one (it is the fresher list);
        # any other question keeps the first ask, which carries the history.
        keep = rs[-1] if key[0] == "about" and key[2].startswith("handoff:") else rs[0]
        pairs += [(r, keep) for r in rs if r["id"] != keep["id"]]
    return pairs


def _tasks(c):
    alive = _alive_agents(c)
    rows = _rows(c, "SELECT * FROM tasks WHERE status IN ('open','in_progress')"
                    " ORDER BY created_at")
    for t in rows:
        t["his"] = (t.get("risk") or "green") in ("amber", "red")
        t["dead_claim"] = (t["status"] == "in_progress"
                           and (t.get("owner") or "").lower() not in alive)
    return rows


def _duplicate_tasks(tasks):
    groups = {}
    for t in tasks:
        if t["status"] != "open" or t["his"]:
            continue
        key = (t["sector"], (t.get("desk") or "").lower(), _shape(t["title"]))
        groups.setdefault(key, []).append(t)
    pairs = []
    for rs in groups.values():
        if len(rs) < 2:
            continue
        rs = sorted(rs, key=lambda x: x.get("created_at") or 0)
        pairs += [(t, rs[0]) for t in rs[1:]]
    return pairs


def survey() -> dict:
    """What is left over. Reads only — nothing changes."""
    c = _conn()
    try:
        run = last_run(c)
        reqs = _requests(c)
        tasks = _tasks(c)
        dup_r = _duplicate_requests(reqs)
        dup_t = _duplicate_tasks(tasks)
        stale_cut = _now() - board.UNCLAIMED_DAYS * 86400
        faults_open = 0
        try:
            faults_open = c.execute("SELECT count(*) FROM faults WHERE status!='resolved'"
                                    ).fetchone()[0]
        except sqlite3.Error:
            pass
        dead_runs = 0
        for r in _rows(c, "SELECT pid, host FROM agent_runs WHERE status='running'"):
            try:
                if r.get("pid") and not board.process_alive(r["pid"], r.get("host") or ""):
                    dead_runs += 1
            except Exception:
                pass
        day_running = _day_running(c)
    finally:
        c.close()

    kinds = {}
    for r in reqs:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    by_room = {}
    for r in reqs:
        if r["kind"] in ("question", "dead_claim"):
            by_room.setdefault(r["to_sector"], {"questions": 0, "jobs": 0})["questions"] += 1
    for t in tasks:
        if not t["his"] and (t["status"] == "open" or t["dead_claim"]):
            by_room.setdefault(t["sector"], {"questions": 0, "jobs": 0})["jobs"] += 1

    garbage = (len(dup_r) + len(dup_t) + kinds.get("chase", 0) + kinds.get("handoff", 0)
               + kinds.get("orphan", 0) + kinds.get("dead_claim", 0)
               + sum(1 for t in tasks if t["dead_claim"]) + dead_runs
               + sum(1 for t in tasks if t["status"] == "open" and not t["his"]
                     and (t.get("created_at") or 0) < stale_cut))
    waiting = kinds.get("question", 0) + kinds.get("dead_claim", 0) + sum(
        1 for t in tasks if not t["his"] and (t["status"] == "open" or t["dead_claim"]))
    unfinished = len(run.get("unfinished") or [])

    bits = []
    if unfinished:
        bits.append(f"the last run stopped with {unfinished} of {run['total']} steps not done")
    if waiting:
        bits.append(f"{waiting} job(s) and question(s) are still waiting")
    if garbage:
        bits.append(f"{garbage} thing(s) can be cleared up without a model")
    head = ("Nothing is left over — the last run finished and nothing is waiting."
            if not bits else "; ".join(bits).capitalize() + ".")
    if day_running:
        head = "A run is going now — it clears up as it goes."

    return {
        "ok": True, "head": head, "day_running": day_running,
        "last_run": run,
        "resumable": bool(unfinished) or bool(by_room),
        "requests": {
            "waiting": kinds.get("question", 0), "dead_claims": kinds.get("dead_claim", 0),
            "filed": kinds.get("filed", 0), "orphans": kinds.get("orphan", 0),
            "chases": kinds.get("chase", 0), "handoffs": kinds.get("handoff", 0),
            "duplicates": len(dup_r)},
        "tasks": {
            "open": sum(1 for t in tasks if t["status"] == "open" and not t["his"]),
            "dead_claims": sum(1 for t in tasks if t["dead_claim"]),
            "working": sum(1 for t in tasks if t["status"] == "in_progress"
                           and not t["dead_claim"]),
            "duplicates": len(dup_t),
            "stale": sum(1 for t in tasks if t["status"] == "open" and not t["his"]
                         and (t.get("created_at") or 0) < stale_cut),
            "yours": sum(1 for t in tasks if t["his"])},
        "faults_open": faults_open, "dead_runs": dead_runs,
        "by_room": by_room, "garbage": garbage, "waiting": waiting,
    }


def rooms_with_work() -> list:
    """The sectors that still have a real question or a job waiting."""
    return sorted(survey().get("by_room") or {})


# ---------------------------------------------------------------------------
# CLEARING UP WHAT NEEDS NO THINKING
# ---------------------------------------------------------------------------

def tidy(dry_run=False) -> dict:
    """
    Put right everything that needs no model. Returns what was done, line by
    line, so the run panel can say it. Refuses while a run is going: that run
    is the thing holding the claims, and pulling them out from under it would
    hand live work to a second desk.
    """
    c = _conn()
    try:
        if _day_running(c):
            return {"ok": False, "done": [], "counts": {},
                    "error": "A run is going now — it clears up as it goes. "
                             "Try again when it has finished."}
        nothing_live = not _alive_agents(c)
    finally:
        c.close()
    if dry_run:
        s = survey()
        return {"ok": True, "dry_run": True, "would": s, "done": [], "counts": {}}

    done, counts = [], {}

    def note(key, n, line):
        if n:
            counts[key] = counts.get(key, 0) + n
            done.append(line)

    # 1. Runs, locks and days owned by a process that is gone.
    try:
        swept = board.sweep_dead_processes()
        note("dead_runs", len(swept.get("runs_closed") or []) + len(swept.get("days_closed") or []),
             f"closed {len(swept.get('runs_closed') or [])} run(s) left open by a closed program")
        note("locks", len(swept.get("locks_freed") or []),
             f"let go of {len(swept.get('locks_freed') or [])} lock(s) nobody was holding")
    except Exception:
        pass

    # 2. Questions claimed by a desk that is not running, back in the inbox.
    #    With nothing running at all, every unanswered claim is dead, however
    #    recent — the program was closed on it.
    try:
        freed = board.release_dead_request_claims(older_than=0 if nothing_live
                                                  else board.DEAD_REQUEST_CLAIM_SECS)
        note("request_claims", len(freed),
             f"put {len(freed)} question(s) back in their inbox — the desk that "
             "took them had stopped")
    except Exception:
        pass

    # 3. Jobs claimed by a desk that is not running, back on the board.
    c = _conn()
    try:
        tasks = _tasks(c)
    finally:
        c.close()
    n = 0
    for t in tasks:
        if not t["dead_claim"]:
            continue
        ok, _why = board.release_claim(
            t["id"], "owner", force=True,
            reason=("picked up again on resume: nothing was running, so the "
                    "desk that held this had stopped"))
        n += bool(ok)
    note("task_claims", n, f"put {n} job(s) back on the board — the desk holding "
                           "them had stopped")

    # 4. The same job twice on one board: one of them goes.
    c = _conn()
    try:
        pairs = _duplicate_tasks(_tasks(c))
        for copy, kept in pairs:
            c.execute("UPDATE tasks SET status='withdrawn', result=?, finished_at=?"
                      " WHERE id=? AND status='open'",
                      (f"The same job as task {kept['id']}, which is still on the "
                       f"board — folded into it so it is done once, not twice.",
                       _now(), copy["id"]))
        c.commit()
    finally:
        c.close()
    note("duplicate_tasks", len(pairs), f"folded {len(pairs)} copy job(s) into the "
                                        "original")

    # 5. The same question twice: the copies are answered with where the
    #    original is, and closed. Append-only — nothing is edited or removed.
    c = _conn()
    try:
        pairs = _duplicate_requests(_requests(c))
    finally:
        c.close()
    for copy, kept in pairs:
        board.reply_request(copy["id"], TIDY_AGENT,
                            f"This is the same as request {kept['id']}, which is "
                            "still open and will be answered there. Closed so the "
                            "sector answers it once.", close=True)
    note("duplicate_requests", len(pairs), f"closed {len(pairs)} copy question(s) — "
                                           "each is answered once, on the original")

    # 6. Chases and hand-offs: answered the way each sector would, no model.
    from . import inbox
    settled = {"noted": 0, "answered": 0, "declined": 0}
    for sector in inbox.SECTORS:
        try:
            got = inbox.settle_paperwork(sector, TIDY_AGENT)
        except Exception:
            continue
        for k in settled:
            settled[k] += got.get(k, 0)
    note("chases", settled["noted"], f"answered and closed {settled['noted']} "
                                     "chase(s) — reminders, not work")
    note("handoffs", settled["answered"], f"answered {settled['answered']} "
                                          "hand-off(s) on receipt")
    note("declined", settled["declined"], f"declined {settled['declined']} question(s) "
                                          "to a sector you have switched off")

    # 7. Orphans: a thread still waiting on a job that has ended.
    c = _conn()
    try:
        orphans = [r for r in _requests(c) if r["kind"] == "orphan"]
        ends = {}
        for t in _rows(c, "SELECT id, status, result, about FROM tasks"
                          " WHERE about LIKE 'requests:%'"):
            ends[t["about"].split(":", 1)[1]] = t
    finally:
        c.close()
    for r in orphans:
        t = ends.get(r["id"]) or {}
        board.reply_request(
            r["id"], TIDY_AGENT,
            f"The job filed for this (task {t.get('id', '?')}) ended as "
            f"'{t.get('status', 'closed')}'"
            + (f": {str(t.get('result'))[:300]}" if t.get("result") else "")
            + ". Closing the thread so it is not counted as waiting for ever. "
              "If it still matters, ask again.", close=True)
    note("orphans", len(orphans), f"closed {len(orphans)} thread(s) whose job had "
                                  "already ended")

    # 8. Work nobody has picked up in days is withdrawn, with the reason.
    try:
        gone = board.expire_unclaimed()
        note("stale", len(gone), f"withdrew {len(gone)} job(s) nobody had picked up "
                                 f"in {board.UNCLAIMED_DAYS}+ days")
    except Exception:
        pass

    # 9. Faults whose cause has gone are closed — the closing half of the
    #    Overseer's pass only. Its full pass files and routes, which posts new
    #    reminders; a clear-up must not make the mess it is clearing. The
    #    Overseer's own step does the rest when the run starts.
    try:
        from . import faults
        n = faults.close_cleared()
        note("faults", n, f"closed {n} fault(s) that had cleared on their own")
    except Exception:
        pass

    head = ("Cleared up: " + "; ".join(done) + ".") if done else "Nothing needed clearing up."
    return {"ok": True, "done": done, "counts": counts, "head": head}


# ---------------------------------------------------------------------------
# HIS "LET IT GO"
# ---------------------------------------------------------------------------

def put_away() -> dict:
    """
    Tidy, then let every remaining leftover go: questions closed with a note,
    queued jobs withdrawn. Never his own decisions (amber, red), never a job he
    posted himself, never a question he asked, and never work a live desk is
    doing right now. Nothing is deleted.
    """
    t = tidy()
    if not t.get("ok"):
        return t
    when = time.strftime("%Y-%m-%d %H:%M")
    c = _conn()
    try:
        reqs = [r for r in _requests(c)
                if r["kind"] in ("question", "dead_claim")
                and (r.get("from_sector") or "").lower() != "owner"]
        tasks = [x for x in _tasks(c)
                 if x["status"] == "open" and not x["his"]
                 and (x.get("created_by") or "").lower() != "owner"]
        for x in tasks:
            c.execute("UPDATE tasks SET status='withdrawn', result=?, finished_at=?"
                      " WHERE id=? AND status='open'",
                      (f"Put away by the owner on {when} without being done. If it "
                       "still matters, whatever asked for it will raise it again.",
                       _now(), x["id"]))
        c.commit()
    finally:
        c.close()
    for r in reqs:
        board.reply_request(r["id"], TIDY_AGENT,
                            f"Put away by the owner on {when} without an answer. If "
                            "it still matters, ask again.", close=True)
    head = (f"{t['head']} Then put away {len(reqs)} unanswered question(s) and "
            f"{len(tasks)} queued job(s). Nothing was deleted — each keeps the "
            "reason. Your own decisions and anything you posted yourself were left.")
    return {"ok": True, "tidy": t, "questions": len(reqs), "jobs": len(tasks),
            "head": head}


def main():
    ap = argparse.ArgumentParser(description="What the last session left behind.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--tidy", action="store_true")
    ap.add_argument("--put-away", action="store_true")
    a = ap.parse_args()
    try:
        out = put_away() if a.put_away else tidy() if a.tidy else survey()
    except Exception as e:
        out = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if a.json:
        print(json.dumps(out, default=str))
    else:
        print(out.get("head") or out.get("error"))
        for line in out.get("done") or []:
            print("  · " + line)
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
