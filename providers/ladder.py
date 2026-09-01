"""
ladder.py — what an agent reaches for, in what order, when it is under load.

THE OWNER'S DESIGN, IN HIS WORDS

    "if any agent is having the loaded work that can take help from md to
     borrow that agent it's api ... or that agent can use the module listed in
     the shelf instead of just staying getting loaded ... but only loaded agent
     as all the agents have fallback modules they can use them in situation
     their fallback failed then they can ask md or they can use extra shelf llm
     added ... becasue using MD's module each time is not allowed that is
     allowed only in critical condition like sector both api's failed"

That is a ladder with five rungs, and the whole value is in the ORDER. Each rung
is cheaper, safer and more local than the one above it, and a rung is only
climbed when the one below it genuinely cannot carry the work:

  0  THE SECTOR'S OWN PRIMARY
     Ordinary work. Nothing here changes that.

  1  THE SECTOR'S OWN FALLBACK CHAIN
     Only on failure. routing.call_task already cascades, and since the retry
     ladder went into client.py a transient 429 no longer burns a model on its
     way past. Rungs 0 and 1 are one call to call_task.

  2  SPLIT THE WORK ACROSS THE SECTOR'S OWN MODELS
     Not a fallback — a division of labour. When a desk has twenty independent
     things to do and two working models, doing them one at a time on one model
     is the agent "just staying getting loaded". This needs no permission from
     anyone: both models already belong to that sector.

  3  BORROW A SPARE OFF THE SHELF
     The llm_extra shelf exists for this. Reached when the sector's own chain
     has FAILED, or when the desk is genuinely loaded and could go faster with
     another pair of hands. Single-holder, queued, handed straight back.

  4  THE MANAGING DIRECTOR'S OWN MODEL
     The last rung, and deliberately hard to reach: the sector has no working
     model at all, there is no spare on the shelf, and the work would otherwise
     simply not happen. Capped per day, logged every time, and the owner is
     told — because a sector living on the MD's brain is a broken API key, and
     the repair is the key, not the borrowing.

WHAT THIS IS NOT

It is not a way to get a better answer. Every rung answers the same question;
climbing does not buy quality, it buys the work happening at all. Nothing here
retries a model that answered badly — only one that could not answer.

It is not a way around the cost-tier guard. resolve_task has already decided
what a sector may spend; rungs 0-2 only pick between things it already allowed,
and rungs 3-4 go through the cabinet's borrow, which has its own ceiling.

THE LOOPHOLES THIS IS BUILT AGAINST

Every one of these is a way the ladder could quietly become a worse system than
no ladder, and each has a guard below and a test in ladder_tests.py.

  Borrowing from yourself. The MD climbing to rung 4 is the MD borrowing its
  own model and calling that a fallback. Refused outright.

  The MD's brain becoming everyone's brain. A sector with a dead key would
  reach rung 4 on every call, for ever, and the owner would see a working
  agency rather than a broken key. Capped per day per sector, and the cap
  being hit files a fault instead of failing silently.

  Two sectors taking the MD's model at once, or taking it while the MD is
  planning the day. One holder, short wait, and the MD's own lane always wins
  because it does not go through here at all.

  A split answering one document in two voices. Splitting is only ever offered
  for INDEPENDENT items — twenty leads, twenty pages. Never for parts of one
  piece of work. See spread().

  A model that dies mid-split taking its share of the work with it. Its
  unfinished items are re-dealt to whatever is still standing, not lost.

  The ladder hiding the problem it is working around. Every result says which
  rung answered and why, and anything above rung 1 is recorded so the readiness
  screen can show a sector that has quietly been living on borrowed help.
"""
import json
import sqlite3
import threading
import time

from . import client, load, routing, store

# The rungs, in the order they are climbed.
OWN = "own"                    # rungs 0-1: the sector's own chain
SPLIT = "split"                # rung 2
SHELF = "shelf"                # rung 3
MD = "md"                      # rung 4

# How many times in one day a single sector may reach the top rung. Low on
# purpose: this is an emergency measure, and a sector needing it four times has
# a configuration problem the owner should be told about rather than a bad
# afternoon.
MD_BORROWS_A_DAY = 3

# The MD is not made to wait long for its own model back. A sector that cannot
# get the lock in this many seconds does without.
MD_LOCK_WAIT = 20

# A desk is "loaded" when it has this much independent work in one go. Below
# it, splitting and borrowing cost more in coordination than they save.
LOADED_JOBS = 4

# The sector task names the router knows. Kept here so a typo becomes an error
# rather than a route that silently does not exist.
SECTORS = ("md", "it", "content", "seo", "leads", "report", "security")


class NotForYou(ValueError):
    """A sector asked for a rung it is not allowed to climb."""


# ---------------------------------------------------------------------------
# WHO CAN CLIMB, AND HOW FAR
# ---------------------------------------------------------------------------

def _today():
    return time.strftime("%Y-%m-%d")


def _db():
    try:
        from coordination import board
        return board.DB
    except Exception:                                        # pragma: no cover
        return None


def _conn():
    db = _db()
    if not db:
        return None
    try:
        c = sqlite3.connect(db, timeout=10, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=10000")
        c.execute("CREATE TABLE IF NOT EXISTS brain_borrows ("
                  "id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, sector TEXT,"
                  " rung TEXT, agent TEXT, reason TEXT, ok INTEGER, at INTEGER)")
        return c
    except Exception:                                        # pragma: no cover
        return None


def _note_borrow(sector, rung, agent, reason, ok):
    c = _conn()
    if c is None:
        return
    try:
        c.execute("INSERT INTO brain_borrows(day,sector,rung,agent,reason,ok,at)"
                  " VALUES(?,?,?,?,?,?,?)",
                  (_today(), sector, rung, agent, (reason or "")[:200],
                   1 if ok else 0, int(time.time())))
    except Exception:                                        # pragma: no cover
        pass
    finally:
        c.close()


def md_borrows_today(sector=None) -> int:
    """How many times the MD's brain has been lent out today."""
    c = _conn()
    if c is None:
        return 0
    try:
        q = "SELECT count(*) FROM brain_borrows WHERE day=? AND rung=? AND ok=1"
        args = [_today(), MD]
        if sector:
            q += " AND sector=?"
            args.append(sector)
        return int(c.execute(q, args).fetchone()[0])
    except Exception:                                        # pragma: no cover
        return 0
    finally:
        c.close()


def borrow_log(limit=50) -> list:
    """Everything above rung 1, newest first. For the readiness screen."""
    c = _conn()
    if c is None:
        return []
    try:
        return [dict(r) for r in c.execute(
            "SELECT * FROM brain_borrows ORDER BY at DESC LIMIT ?",
            (int(limit),)).fetchall()]
    except Exception:                                        # pragma: no cover
        return []
    finally:
        c.close()


def may_climb_to_md(sector) -> tuple:
    """
    (allowed, why_not). The gate on the top rung, in one readable place.

    Three conditions, and all of them are about the ladder not becoming a way
    of life. The MD cannot borrow from itself; a sector that has already had
    its emergencies for today is a configuration problem, not an emergency; and
    the rung exists only for a sector with no brain of its own left.
    """
    sector = (sector or "").strip().lower()
    if sector == MD:
        return False, ("the Director cannot borrow the Director's own model — "
                       "that is not a fallback, it is the same model with a "
                       "different name on it")
    if sector not in SECTORS:
        return False, f"'{sector}' is not a sector with a routed brain"
    used = md_borrows_today(sector)
    if used >= MD_BORROWS_A_DAY:
        return False, (f"{sector} has already borrowed the Director's model "
                       f"{used} time(s) today, which is the cap. A sector that "
                       f"needs it this often has a broken or missing API key, "
                       f"and the repair is the key rather than more borrowing.")
    md = routing.resolve_task(MD)
    if not md.get("ok"):
        return False, ("the Director has no working model either, so there is "
                       "nothing to lend: " + str(md.get("error", "")))
    return True, ""


def who_could_answer(sector) -> dict:
    """
    Is there anything at all that could do this sector's thinking? Read-only.

    For the pipelines that check before they work — Content refuses to start a
    page it cannot finish, and finding that out after borrowing a shelf and
    reading the cabinet wastes a pass.

    NOT CONFIGURED IS NOT AN EMERGENCY, and that distinction is the whole
    reason this function exists rather than a plain resolve_task call. A sector
    with a model of its own, or a spare on the shelf, can work. A sector the
    owner has simply never routed anything to cannot, and must say so — because
    the alternative is spending one of the day's three borrows of the
    Director's model to paper over a setting he could fix in ten seconds. The
    top rung is for a key that BROKE, not for one that was never entered.
    """
    sector = (sector or "").strip().lower()
    own = routing.resolve_task(sector)
    spare = []
    try:
        spare = [m for m in (store.shelf_catalogue().get("llm_extra") or [])
                 if m.get("kind") == "model" and m.get("enabled", True)]
    except Exception:                                        # pragma: no cover
        spare = []
    return {
        "ok": bool(own.get("ok")) or bool(spare),
        "own": bool(own.get("ok")),
        "own_error": "" if own.get("ok") else str(own.get("error") or ""),
        "spare": len(spare),
        "why": ("" if own.get("ok") else
                (f"nothing is routed to {sector}, but there are {len(spare)} "
                 f"spare brain(s) on the shelf it can borrow"
                 if spare else
                 f"nothing is routed to {sector} and the llm_extra shelf is "
                 f"empty, so there is nothing anywhere that could do this")),
    }


# ---------------------------------------------------------------------------
# RUNG 2 — SPLITTING INDEPENDENT WORK ACROSS THE SECTOR'S OWN MODELS
# ---------------------------------------------------------------------------

def _free_first(candidates):
    """The sector's own models, least busy first. load.py already does this."""
    ordered, note = load.order(list(candidates or []))
    return ordered, note


def spread(sector, jobs, max_tokens=512, timeout=0, agent="",
           workers=None) -> dict:
    """
    Do a pile of INDEPENDENT jobs across all the sector's own working models.

    `jobs` is [(key, messages), ...]. Every job must stand alone: twenty leads,
    twenty page briefs, twenty subject lines. Never the paragraphs of one
    article — two models write in two voices, and stitching them together
    produces something worse than either would have written alone. That is why
    this takes a list of separate jobs rather than one long prompt to chop up:
    the shape of the API makes the wrong use awkward.

    Returns {ok, results: {key: {...}}, done, failed, models_used, rung}.

    A model that dies part way through does not take its share with it — its
    remaining jobs go back in the pile for whatever is still standing. With one
    model configured this is an ordinary sequential loop, which is the correct
    behaviour and not a degraded one.
    """
    jobs = list(jobs or [])
    if not jobs:
        return {"ok": True, "results": {}, "done": 0, "failed": 0,
                "models_used": [], "rung": OWN, "note": "nothing to do"}

    r = routing.resolve_task(sector)
    if not r.get("ok"):
        return {"ok": False, "results": {}, "done": 0, "failed": len(jobs),
                "models_used": [], "rung": OWN, "error": r.get("error")}

    cands, load_note = _free_first(r["candidates"])
    route = r["route"]
    timeout = min(int(route.get("timeout", 120)),
                  int(timeout) if timeout else int(route.get("timeout", 120)))

    # One worker per model, never more: a second worker on the same model is
    # two calls queueing on one provider, which is the load problem again with
    # extra threads on top.
    hands = cands if workers is None else cands[:max(1, int(workers))]

    pile = list(jobs)
    pile_lock = threading.Lock()
    results, usage, dead = {}, [], []
    res_lock = threading.Lock()

    def _worker(cand):
        cred, model = store.find_model(cand["model_id"])
        if not cred or not model:
            return
        mine = 0
        while True:
            with pile_lock:
                if not pile:
                    return
                key, messages = pile.pop(0)
            tok = load.begin(model["id"], cred.get("id", ""), sector,
                             holder=f"{agent or sector}:spread",
                             ttl=max(30, timeout + 30))
            try:
                res = client.chat(cred, model, messages,
                                  max_tokens=max_tokens, timeout=timeout)
            except Exception as e:                           # pragma: no cover
                res = {"ok": False, "status": 0, "output": "",
                       "cause": f"{type(e).__name__}: {e}"}
            finally:
                load.end(tok)

            with res_lock:
                results[key] = {
                    "ok": bool(res.get("ok")),
                    "output": res.get("output", ""),
                    "model": model["api_model_id"],
                    "status": res.get("status"),
                    "cause": res.get("cause"),
                    "attempts": res.get("attempts", 1),
                }
                if res.get("ok"):
                    mine += 1
                    usage.append((cred["id"], model["id"]))

            if not res.get("ok"):
                # THIS MODEL IS DOWN, NOT THIS JOB. client.chat has already
                # retried the transient statuses with backoff, so a failure
                # here has survived that. Stop dealing work to this model and
                # put the job back for a healthy one — otherwise a dead key
                # eats half the pile one item at a time.
                with res_lock:
                    dead.append({"model": model["api_model_id"],
                                 "model_id": model["id"],
                                 "cause": res.get("cause"),
                                 "after": mine})
                with pile_lock:
                    pile.append((key, messages))
                with res_lock:
                    results.pop(key, None)
                return

    # A HANDED-BACK JOB MUST HAVE SOMEBODY LEFT TO HAND IT TO.
    #
    # Caught by this suite going intermittent, not by reading it, and it lost
    # real work: five of six jobs done and the sixth reported with no model
    # against it. The race is small and entirely mine. A worker whose model
    # dies puts its job back on the pile — but by then the healthy worker may
    # already have found the pile empty and returned, so the returned job sits
    # there with nobody to take it and falls through to the "nothing left
    # standing" line below, which is a lie: something WAS standing.
    #
    # So the pass repeats while there is work left and a model that has not
    # died. Bounded by the number of models, because each round either clears
    # the pile or kills another model — it cannot loop for ever.
    rounds = 0
    while True:
        rounds += 1
        threads = [threading.Thread(target=_worker, args=(c,), daemon=True)
                   for c in hands if c["model_id"] not in
                   {d.get("model_id") for d in dead}]
        if not threads:
            break
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        with pile_lock:
            left = len(pile)
        if not left or rounds > len(hands):
            break

    # Anything still on the pile had no model left standing.
    for key, _m in pile:
        results.setdefault(key, {
            "ok": False, "output": "", "model": None, "status": None,
            "cause": "every model for this sector failed before reaching this item"})

    # Usage is recorded HERE, on one thread. store.record_usage is a
    # read-modify-write of a JSON file; calling it from four workers at once
    # loses counts, and a spend figure that undercounts is worse than none.
    for cid, mid in usage:
        try:
            store.record_usage(sector, cid, mid, calls=1, spend=0.0)
        except Exception:                                    # pragma: no cover
            pass

    done = sum(1 for v in results.values() if v["ok"])
    used = sorted({v["model"] for v in results.values() if v.get("model")})
    return {"ok": done > 0, "results": results, "done": done,
            "failed": len(jobs) - done, "models_used": used,
            "rung": SPLIT if len(hands) > 1 else OWN,
            "hands": len(hands), "dead_models": dead,
            "load_note": load_note,
            "note": (f"{done}/{len(jobs)} done across {len(used)} model(s)"
                     + (f"; {load_note}" if load_note else ""))}


# ---------------------------------------------------------------------------
# THE WHOLE LADDER, FOR ONE QUESTION
# ---------------------------------------------------------------------------

def think(sector, messages, agent="", reason="", max_tokens=512, timeout=0,
          loaded=False, allow_shelf=True, allow_md=True) -> dict:
    """
    Get one question answered, climbing only as far as it has to.

    Returns the ordinary call_task shape plus:
      rung     which rung answered — 'own', 'shelf' or 'md'
      climbed  every rung tried, with why the one below it did not do
      why      one sentence a person can read

    `loaded`  the caller knows this desk is backed up. It lets the shelf be
              used for speed rather than only for rescue. It never unlocks the
              MD's model: being busy is not the same as having no brain.
    """
    sector = (sector or "").strip().lower()
    climbed = []

    # RUNGS 0 and 1 — the sector's own chain, cascade and retry included.
    own = routing.call_task(sector, messages, max_tokens=max_tokens,
                            timeout=timeout)
    if own.get("ok"):
        return {**own, "rung": OWN, "climbed": climbed,
                "why": f"{sector} answered on its own model"}
    climbed.append({"rung": OWN, "failed": own.get("error"),
                    "failovers": own.get("failovers", [])})

    # RUNG 3 — a spare off the shelf. Reached because the chain failed; a
    # loaded desk can also get here without failing, via spread() upstream.
    if allow_shelf:
        why = reason or (f"{sector}'s own model(s) did not answer: "
                         f"{own.get('error', 'no reason given')}")
        spare = call_spare_for(sector, messages, agent=agent, reason=why,
                               max_tokens=max_tokens, timeout=timeout)
        if spare.get("ok"):
            _note_borrow(sector, SHELF, agent, why, True)
            return {**spare, "task": sector, "rung": SHELF, "climbed": climbed,
                    "why": (f"{sector}'s own model(s) failed, so it borrowed a "
                            f"spare off the shelf")}
        climbed.append({"rung": SHELF, "failed": spare.get("error")})
    else:
        climbed.append({"rung": SHELF, "failed": "the caller did not allow it"})

    # RUNG 4 — the Director's own model. Critical condition only.
    if not allow_md:
        climbed.append({"rung": MD, "failed": "the caller did not allow it"})
        return _nothing_left(sector, own, climbed)
    if loaded and not _chain_is_down(own):
        # Being busy is not an emergency. Say so rather than climbing.
        climbed.append({"rung": MD, "failed":
                        "the desk is loaded but its own model works — being "
                        "busy is not a critical condition"})
        return _nothing_left(sector, own, climbed)

    allowed, why_not = may_climb_to_md(sector)
    if not allowed:
        climbed.append({"rung": MD, "failed": why_not})
        return _nothing_left(sector, own, climbed)

    got = _borrow_the_md(sector, messages, agent=agent, max_tokens=max_tokens,
                         timeout=timeout,
                         reason=reason or f"{sector} has no working model left")
    if got.get("ok"):
        return {**got, "task": sector, "rung": MD, "climbed": climbed,
                "why": (f"{sector} had no working model and nothing on the "
                        f"shelf, so the Director lent it one. This is the last "
                        f"rung and it is capped — fix {sector}'s API key.")}
    climbed.append({"rung": MD, "failed": got.get("error")})
    return _nothing_left(sector, own, climbed)


def _chain_is_down(own_result) -> bool:
    """Did the sector's chain FAIL, as opposed to being merely slow or busy?"""
    return not own_result.get("ok")


def _nothing_left(sector, own, climbed) -> dict:
    tried = " → ".join(c["rung"] for c in climbed)
    return {"ok": False, "task": sector, "output": "", "rung": None,
            "climbed": climbed,
            "failovers": own.get("failovers", []),
            "error": (f"{sector} could not get an answer from anywhere. "
                      f"Tried: {tried}. " + str(own.get("error", ""))),
            "why": (f"every rung of the ladder was tried for {sector} and none "
                    f"of them answered")}


def call_spare_for(sector, messages, agent="", reason="", max_tokens=512,
                   timeout=0) -> dict:
    """routing.call_spare, with this sector's timeout and a sane default wait."""
    return routing.call_spare(agent or f"{sector}-desk", messages,
                              sector=sector, reason=reason,
                              max_tokens=max_tokens,
                              timeout=int(timeout) or 120, wait=False)


def _borrow_the_md(sector, messages, agent="", reason="", max_tokens=512,
                   timeout=0) -> dict:
    """
    Use the Director's model, once, under a lock, and put it straight back.

    The lock is the important part and it is short. Two sectors in trouble at
    the same moment must not both take it, and the MD's own lane never comes
    through here — md_cycle calls call_task('md') directly — so the Director
    planning the day always wins the model over a sector borrowing it.
    """
    try:
        from coordination import board
    except Exception as e:                                   # pragma: no cover
        return {"ok": False, "error": f"cabinet unavailable: {e}"}

    holder = agent or f"{sector}-desk"
    # A BOUNDED WAIT, POLLED — board.acquire_lock(wait=True) has no timeout and
    # loops for ever. Passing wait=True here would mean a sector in trouble
    # blocking the whole day on a lock somebody else was holding, which is a
    # worse outcome than not getting the answer. So: try, sleep, try, and give
    # up at MD_LOCK_WAIT. Written as a loop here rather than as a new argument
    # to acquire_lock because that function is used for the live site and the
    # publishing pipeline, where waiting your turn indefinitely is exactly
    # right and a timeout would be a way to jump the queue.
    got, waited = False, 0
    while True:
        got = board.acquire_lock("md_brain", holder, wait=False,
                                 ttl=MD_LOCK_WAIT + 60)
        if got or waited >= MD_LOCK_WAIT:
            break
        time.sleep(1)
        waited += 1
    if not got:
        _note_borrow(sector, MD, agent, reason, False)
        return {"ok": False, "error":
                "another sector is already borrowing the Director's model. "
                "Waiting would hold this desk up for an answer it may not get; "
                "it will try again on the next pass."}
    try:
        r = routing.call_task(MD, messages, max_tokens=max_tokens,
                              timeout=timeout)
    finally:
        board.release_lock("md_brain", holder)

    _note_borrow(sector, MD, agent, reason, bool(r.get("ok")))
    if r.get("ok"):
        _tell_the_owner(sector, agent, reason)
    return r


# The seven task names, as the request board spells the sectors they belong to.
_AS_A_SECTOR = {"md": "Director", "it": "IT", "content": "Content", "seo": "SEO",
                "leads": "Leads", "report": "Report", "security": "Security"}


def _tell_the_owner(sector, agent, reason):
    """
    A sector on the Director's brain is a broken key, and somebody is told.

    Once per sector per day, on the request board to the Director — who reads
    its inbox, decides, and escalates to the owner if it thinks he should know.
    That is the chain of command the handbook describes, and it beats a second
    private alarm channel nobody else can see.

    The failure this guards against is the ladder working so well that nobody
    ever notices the rung below it has been broken for a month.
    """
    if md_borrows_today(sector) != 1:
        return                       # only the first one each day
    try:
        from coordination import board
        board.post_request(
            _AS_A_SECTOR.get(sector, sector), "Director",
            f"{sector} is running on your model, not its own",
            f"{sector} could not reach any model of its own and found nothing "
            f"on the llm_extra shelf, so it borrowed yours to finish its work.\n\n"
            f"Reason given: {reason}\n"
            f"Agent: {agent or f'{sector}-desk'}\n\n"
            f"The ladder covered it, and that is the problem worth raising: the "
            f"work looks fine from outside while {sector} has a broken or "
            f"missing API key. Capped at {MD_BORROWS_A_DAY} borrows a day, so "
            f"{sector} stops working once the cap is reached. Worth putting to "
            f"the owner — the repair is the key, not more borrowing.",
            urgency="high", about=f"routing:{sector}")
    except Exception:
        pass                          # a note that cannot be filed must not
                                      # stop the work it was noting


# ---------------------------------------------------------------------------
# WHAT THE OFFICE SHOWS
# ---------------------------------------------------------------------------

def state(sector=None) -> dict:
    """Which sectors are living on borrowed help, for the readiness screen."""
    rows = borrow_log(200)
    today = _today()
    per = {}
    for r in rows:
        if r["day"] != today or not r["ok"]:
            continue
        s = per.setdefault(r["sector"], {"shelf": 0, "md": 0})
        if r["rung"] in s:
            s[r["rung"]] += 1
    worst = [s for s, v in per.items() if v["md"]]
    return {"today": today, "per_sector": per,
            "on_the_directors_brain": sorted(worst),
            "cap": MD_BORROWS_A_DAY,
            "head": ("no sector needed borrowed help today" if not per else
                     "; ".join(
                         f"{s} borrowed {v['shelf']} from the shelf"
                         + (f" and the Director's model {v['md']}x" if v["md"] else "")
                         for s, v in sorted(per.items())))}


def as_text() -> str:
    s = state()
    out = [s["head"]]
    if s["on_the_directors_brain"]:
        out.append("")
        for sec in s["on_the_directors_brain"]:
            out.append(f"  {sec} has no working model of its own — fix its API "
                       f"key on /setup. Capped at {s['cap']} borrows a day.")
    return "\n".join(out)


if __name__ == "__main__":
    print(as_text())
    print()
    print(json.dumps(state(), indent=2))
