"""
md_cycle.py — the Managing Director's daily cycle. The brain that starts the day.

    python -m pipelines.md_cycle            plan, challenge, post
    python -m pipelines.md_cycle --dry-run  show the plan, post nothing

WHY THIS EXISTS

Every sector was built to work from the board: the IT Watcher claims an IT task,
the Leads sector claims a Leads task, Security answers requests other sectors
raise. All of that worked. Nothing put anything on the board. The only post_task
call in the whole codebase was inside board.py's own smoke test, so every
pipeline stopped at "No open task to claim" and the agency could not start
itself. This is that missing piece.

THE FOUR RULES FROM THE HANDBOOK, ENFORCED IN CODE

1. THE BRAIN NEVER TOUCHES THE HANDS. This module imports no pipeline and calls
   no run(). It reads the cabinet and posts tasks. That is the whole surface.
   md_tests.py asserts it, because a comment promising it is not enforcement.

2. NO DECISION WITHOUT EVIDENCE. Every candidate carries `evidence` — a fact or
   number already in the cabinet. The Critic drops anything that cannot point to
   one. A plan built on a hunch does not reach the board.

3. EVERY PLAN IS CHALLENGED. critic_check() runs before a single task is posted
   and can empty the plan entirely. It rejects the ungrounded, the duplicated,
   the impossible (work needing a capability the agency does not have), and the
   repeatedly-failed.

4. IT KEEPS SCORE. The cycle reads its own recent hit-rate first and says so in
   its reasoning, then logs the decision with the task ids it created so Report
   can mark it later against real rows.

TWO THINGS IT DELIBERATELY WILL NOT DO

Amber work is not posted. Anything risky goes to the owner as a message and
waits; the MD does not get to authorise itself.

It does not flood the board. Re-running it must not double the work, so a
candidate matching an already-open task for that sector is dropped rather than
posted again — the cycle is safe to run on a timer.
"""
import argparse
import json
import sqlite3

from coordination import board, briefing, proposals as _props, state as _state, targets
from coordination import targets as _targets

MAX_TASKS_PER_CYCLE = 6      # a plan nobody can finish is not a plan
# How many of its own requests the MD answers in one pass. A queue of twenty-six
# is not a reason to answer twenty-six: the rest stay queued, in order, and the
# ageing in triage.py guarantees they rise rather than starve.
INBOX_PER_CYCLE = 5


# ---------------------------------------------------------------------------
# TWO MINDS ON THE ALLOCATION
# ---------------------------------------------------------------------------
#
# The MD's own reasoning is deterministic and stays that way — the rules above
# are auditable and free, and replacing them with a model would make the plan
# harder to check, not better.
#
# What the models are for is the ONE judgement the rules genuinely cannot make:
# when more work is grounded than the day can hold, which of it matters most.
# Getting that wrong costs a real day, so it is asked of both models and a
# disagreement is treated as information rather than noise.
#
# The safe default matters here. If the models split, or none is configured, the
# MD keeps its own deterministic ranking and says so — it does not stall, and it
# does not pretend a coin-flip was a decision.

def _ask_both_which_matters(candidates, facts, deliberate=None):
    """
    Returns (ordered_titles, note). On any doubt the MD's own order is kept.
    """
    if len(candidates) <= MAX_TASKS_PER_CYCLE:
        return None, ""          # nothing to choose between; do not spend
    if deliberate is None:
        try:
            from providers import deliberate as _d
            deliberate = _d
        except Exception:
            return None, "no provider layer — kept my own ranking"

    listing = "\n".join(f"- [{c['sector']}] {c['title']}  (because: {c['evidence']})"
                         for c in candidates)
    orders = "; ".join(f"{k}: {v}" for k, v in (facts.get("sector_orders") or {}).items())
    r = deliberate(
        "md",
        [{"role": "system", "content":
          "You rank a day's work for a UAE glass and aluminium business. Reply with "
          "the titles only, most important first, one per line, nothing else. A dead "
          "enquiry path outranks everything; the owner's own orders come next."},
         {"role": "user", "content":
          f"The owner's orders today: {orders or 'none'}\n\nCandidates:\n{listing}"}],
        max_tokens=400,
        verdict_of=lambda t: "|".join(l.strip().lstrip("-0123456789. ").lower()
                                      for l in (t or "").splitlines() if l.strip()))
    if not r.get("ok"):
        return None, f"kept my own ranking ({r.get('error')})"
    if r.get("consulted", 0) < 2:
        return None, "kept my own ranking — only one model, so no cross-check"
    if not r.get("agreed"):
        # A split on what matters most is worth the owner knowing about; it is
        # not worth stalling the day over, so the deterministic order stands.
        return None, ("the two models disagreed on what matters most today, "
                      "so I kept my own ranking — worth a look")
    return [t for t in (r.get("verdict") or "").split("|") if t], \
           "both models agreed on the order"


# ---------------------------------------------------------------------------
# SENSE — read the live state of the business. No judgement here, just facts.
# ---------------------------------------------------------------------------

def sense() -> dict:
    """Everything the MD is allowed to reason from. Rows, not impressions."""
    facts = {"hit_rate": None, "targets": {}, "metrics": {}, "approvals": 0,
             "open_tasks": {}, "site": {}, "leads": {}, "missing": []}
    try:
        facts["hit_rate"] = board.recent_hit_rate()
    except Exception:
        pass
    try:
        facts["targets"] = targets.status()
    except Exception:
        facts["targets"] = {"jobs": [], "orders": "", "any_target_set": False}
    try:
        st = _state.build()
        facts["metrics"] = st.get("metrics", {})
        facts["approvals"] = len(st.get("approvals", []))
    except Exception:
        pass
    try:
        b = briefing.build()
        facts["missing"] = [s.get("shelf") or s.get("would_let_us")
                            for s in b.get("cannot_do_yet", [])]
    except Exception:
        pass

    con = sqlite3.connect(board.DB)
    try:
        facts["open_tasks"] = {
            r[0]: r[1] for r in con.execute(
                "SELECT sector, count(*) FROM tasks WHERE status IN ('open','in_progress')"
                " GROUP BY sector")}
        facts["open_titles"] = {
            (r[0], (r[1] or "").strip().lower()) for r in con.execute(
                "SELECT sector, title FROM tasks WHERE status IN ('open','in_progress')")}
        facts["site"] = {
            r[0]: r[1] for r in con.execute(
                "SELECT severity, count(*) FROM site_issues"
                " WHERE status IN ('open','fixing','in_review') GROUP BY severity")}
        facts["leads"] = {"total": con.execute(
            "SELECT count(*) FROM leads").fetchone()[0]}
        facts["source_map_done"] = con.execute(
            "SELECT count(*) FROM decisions WHERE cycle='leads-sources'").fetchone()[0] > 0
        facts["objected_titles"] = {
            (r[0] or "").strip().lower() for r in con.execute(
                "SELECT t.title FROM task_objections o JOIN tasks t ON t.id=o.task_id")}
        facts["recent_failures"] = [
            r[0] for r in con.execute(
                "SELECT cycle FROM decisions WHERE outcome='failed'"
                " ORDER BY scored_at DESC LIMIT 8") if r[0]]
    finally:
        con.close()

    # What the sectors are asking for, and what the owner told each of them.
    # These outrank the MD's own hardcoded rules: the sector knows its domain
    # and the owner outranks everyone.
    try:
        facts["proposals"] = _props.open_proposals()
    except Exception:
        facts["proposals"] = []
    try:
        facts["sector_orders"] = _props.orders()
    except Exception:
        facts["sector_orders"] = {}

    # THE MD'S OWN INBOX.
    #
    # This was missing, and it was the worst gap in the file. Security reads its
    # inbox and Content reads its inbox; nothing read the Director's. The
    # Overseer escalates a stalled agent to the MD, the row lands in `requests`,
    # and there it stayed — twenty-six of them, the oldest fifty-three hours
    # old, including every escalation about an agent that had been dead for
    # days. The MD was planning each morning around a building it had not
    # checked the post of.
    #
    # Triaged, not in arrival order: see coordination/triage.py for why, and
    # what stops the bottom of the queue starving.
    try:
        from coordination import triage as _triage
        facts["inbox"] = _triage.queue("Director", limit=INBOX_PER_CYCLE)
    except Exception as e:
        facts["inbox"] = {"next": [], "waiting": [], "total": 0,
                          "head": f"could not read the inbox ({e})"}
    return facts


# ---------------------------------------------------------------------------
# ANSWER THE POST — the MD's inbox, worst first
# ---------------------------------------------------------------------------
#
# Reading the inbox is not the same as dealing with it, and the second half is
# the one that was missing. An escalation the MD has READ and left open is
# indistinguishable from one it never saw.
#
# Each one is claimed (so two passes cannot both work it), answered, and closed.
# Where the answer is "somebody should do something", that something is posted
# to the board as a task and the reply names the task id — a promise with a row
# behind it, rather than a sentence.

def clear_inbox(facts, dry_run=False) -> dict:
    """Deal with the top of the MD's queue. Returns what was done and what waits."""
    inbox = facts.get("inbox") or {}
    todo = inbox.get("next") or []
    done, posted, left = [], [], list(inbox.get("waiting") or [])

    for item in todo:
        why = "; ".join(f"{w['label']}" for w in (item.get("why") or [])[:2])
        line = {"id": item["id"], "from": item["from"], "subject": item["subject"],
                "points": item["points"], "why": why, "waited": item["waited"],
                "copies": item.get("copies", 1)}
        if dry_run:
            done.append({**line, "action": "would answer"})
            continue

        if not board.claim_request(item["id"], "managing-director"):
            left.append(item)        # another pass got there first; not an error
            continue

        # An escalation about a sector is not work for the MD to do itself —
        # the brain never touches the hands. What it CAN do is put the work on
        # the board, addressed to whoever can act, and say so in the reply.
        task_id = None
        note = ""
        target = _who_should_act(item)

        # A CHASE IS NOT WORK, AND THE MD WAS THE OTHER DOOR IT CAME THROUGH.
        #
        # The inbox reader stopped filing "you have not answered" and "work
        # nobody is picking up" as tasks. This did not, and it is where the
        # rest of them were getting in: the Overseer escalates a fault to the
        # Director, the MD reads it and posts "From the MD: Seo has 10 faults
        # open at once" onto SEO's board, unassigned, where nobody claims it
        # because there is nothing in it to do. Every one of the QUEUED
        # unassigned rows in his control room is one of these.
        #
        # And it feeds itself: those unclaimed rows make the Overseer file
        # "work nobody is picking up", which escalates, which the MD turns into
        # another unclaimable row. Two doors into the same loop; I shut one and
        # left the other open.
        #
        # The MD still ANSWERS the escalation — it just answers it with what
        # the sector is already carrying instead of adding to the pile.
        try:
            from coordination import inbox as _inbox
            chase = _inbox.is_a_chase({"from_sector": item.get("from"),
                                       "subject": item.get("subject")})
        except Exception:
            chase = False
        if chase:
            waiting, is_off = 0, False
            try:
                waiting = board.open_task_count(target) if target else 0
                # Worth saying even though nothing was going to be posted
                # anyway: a sector he has switched off explains every quiet
                # thing about it, and he should not have to work that out.
                is_off = bool(target) and not board.sector_enabled(target)
            except Exception:
                waiting, is_off = 0, False
            board.reply_request(
                item["id"], "managing-director",
                f"Read, and deliberately not turned into a job. This is a notice "
                f"about {target or 'a sector'}, not work for it — putting a "
                f"complaint that a sector is behind onto the board that is behind "
                f"is what made the board that way.\n\n"
                + (f"{target} has {waiting} job(s) open. Those are what this "
                   f"notice is about and what it will do next.\n\n"
                   if target else "")
                + (f"{target} is switched off, so it is doing nothing at all "
                   f"until you turn it back on — that is the whole reason this "
                   f"keeps being raised.\n\n" if is_off else "")
                + "The fault stays open and keeps counting until the work behind "
                  "it is done.", close=True)
            done.append({**line, "action": "noted, not filed as work"})
            continue

        if target:
            try:
                task_id = board.post_task(
                    target, f"From the MD: {item['subject'][:80]}",
                    f"Raised by {item['from']}, waiting {item['waited']}.\n\n"
                    f"The MD picked this up because: {why}.\n\n"
                    f"Request {item['id']} on the board has the detail and the "
                    "evidence.", risk="green", created_by="MD",
                    # The task carries forward the row the REQUEST was about, so
                    # it keeps its urgency on the task board instead of arriving
                    # there as ordinary work. Failing that, it is about the
                    # request itself — which still counts for something if that
                    # request said a sector was blocked.
                    about=(item.get("about") or f"requests:{item['id']}"))
                posted.append({"task_id": task_id, "sector": target})
            except board.SectorOff as e:
                note = f" {target} is switched off, so nothing was posted: {e}"
            except board.BoardFull as e:
                note = (f" {target}'s board is already backed up, so nothing was "
                        f"posted: {e}")
            except board.DeskBusy as e:
                # Not a refusal — a courtesy. The desk is out doing the last
                # thing and already has one queued; a third row would not make
                # any of it happen sooner.
                note = f" That desk is mid-run, so it was left alone: {e}"
            except Exception as e:
                note = f" Could not post it to {target}: {e}"

        reply = (f"Read and picked up. This came {item['position']} in the queue "
                 f"because: {why}."
                 + (f" It has been asked {item['copies']} times."
                    if item.get("copies", 1) > 1 else "")
                 + (f"\n\nPosted to {target} as task {task_id}." if task_id
                    else "\n\nNoted — no task posted, this needed acknowledging "
                         "rather than doing.")
                 + note)
        try:
            board.reply_request(item["id"], "managing-director", reply, close=True)
        except Exception as e:
            note += f" (reply failed: {e})"
        done.append({**line, "action": "answered", "task_id": task_id,
                     "posted_to": target})

    return {"answered": done, "posted": posted,
            "still_waiting": len(left),
            "head": inbox.get("head", ""),
            "total": inbox.get("total", 0)}


SECTORS = ("IT", "Content", "SEO", "Leads", "Report", "Security")


def _who_should_act(item):
    """
    Which sector should actually do something about this request.

    The MD is a router here, not a doer.

    Read from the ROW the request names, not from words in its subject. The
    first version searched the subject for a sector name, which is the same
    guess-from-text mistake triage.py had to unpick — "Reporting" contains
    "Report", and a subject mentioning two sectors picks whichever is first in
    my list rather than whichever owns the problem. An Overseer escalation
    carries about='faults:<id>', and that row already knows whose desk broke.
    """
    frm = (item.get("from") or "").strip()
    ref = (item.get("about") or "")

    if ref.startswith("faults:"):
        try:
            con = sqlite3.connect(board.DB)
            row = con.execute("SELECT sector, routed_to FROM faults WHERE id=?",
                              (int(ref.split(":", 1)[1]),)).fetchone()
            con.close()
            if row:
                # routed_to is who the Overseer already gave it to (IT for code
                # faults); sector is whose desk it is. Prefer the fixer.
                for cand in (row[1], row[0]):
                    if cand and cand.strip() in SECTORS:
                        return cand.strip()
        except Exception:
            pass
    if ref.startswith("sector:"):
        name = ref.split(":", 1)[1].strip().title()
        if name in SECTORS:
            return name

    if frm in SECTORS:
        return frm
    if frm.lower() == "overseer":
        return "IT"          # a fault naming no sector is code, and IT owns code
    return None


# ---------------------------------------------------------------------------
# REASON — turn facts into candidate work. Each carries the fact behind it.
# ---------------------------------------------------------------------------

# Jobs that go ahead on the SUGGESTED number when the owner has set none.
#
# THIS IS WHY LEADS AND CONTENT PRODUCED NOTHING, EVERY RUN, IN SILENCE.
#
# The plan posts a Leads task only when the leads target has a per_day above
# zero, and the same for Content and posts. A target the owner has never opened
# Setup and typed a number into reads per_day = 0. So:
#
#   MD posts no Leads task -> the board is empty -> the collectors say
#   "Nothing open on the Leads board. This desk works from the board, never on
#   its own initiative." -> they EXIT 0 -> the day run records a clean pass.
#
# Every part of that is behaving exactly as designed and the result is a sector
# that has never once collected a lead. Worse, targets.py has carried a
# `suggested` number for each of these since it was written — leads 20, posts 1
# — sitting one field away from the check that was reading zero.
#
# Collecting and drafting are safe to start on their own: a collected lead
# contacts nobody (Security clears it first, then Outreach, separately), and a
# drafted page goes to the owner's queue, not to the website.
#
# MAILS IS DELIBERATELY NOT ON THIS LIST. It is the one number that can do
# permanent damage — targets.py says so itself: "A young domain pushing volume
# gets filed as spam for good, and that takes every future lead with it." A
# suggested 10 is a suggestion to HIM, never a default the agency grants itself.
# Nothing is emailed until he sets that number by hand.
SELF_STARTING = ("leads", "posts", "site_checks")


def _target(facts, name):
    """
    Where a job stands — and, when the owner has set no number, the suggested
    one, marked as suggested so the task can say whose idea it was.
    """
    for j in (facts.get("targets") or {}).get("jobs") or []:
        if j["name"] != name:
            continue
        if j.get("per_day") or j.get("set_by_owner") or name not in SELF_STARTING:
            return j
        proposed = int(j.get("suggested") or 0)
        if not proposed:
            return j
        done = int(j.get("done_today") or 0)
        return {**j, "per_day": proposed,
                "remaining": max(0, proposed - done),
                "on_suggestion": True}
    return {}



def _unit(tgt, job, n) -> str:
    """'1 post', '20 leads'. He reads these lines; '1 posts' looks careless."""
    word = str(tgt.get("unit") or job)
    return word[:-1] if n == 1 and word.endswith("s") else word


def _counted_job(sector, job, tgt, folded, title, how) -> dict:
    """
    One task for a job that has a number on it, carrying where the sector is.

    Three things go into it, and the third is the one that was missing:

      HIS WORDS      if the order that set this number was written in a
                     sentence, that sentence is the title. He asked for "find
                     20 leads"; the board should say that back to him, not a
                     phrase the MD made up about the same thing.
      TODAY'S NUMBER what is still to do, not the whole target — a sector that
                     has already collected twelve of twenty needs eight.
      WHERE IT IS    "and also the previous day's leads". Yesterday's count and
                     the running total, so the sector knows whether it is
                     catching up or starting fresh, and so a day that collected
                     nothing is visible on the task itself rather than only in
                     a report nobody opens.
    """
    owner_said = folded.get(job)
    per, done = int(tgt.get("per_day") or 0), int(tgt.get("done_today") or 0)
    left = max(0, per - done)
    yday, on_file = int(tgt.get("done_yesterday") or 0), int(tgt.get("on_file") or 0)

    where = (f"Where this stands: {done} done today of {per} asked for, so {left} "
             f"still to go. Yesterday: {yday}. On file altogether: {on_file}.")
    if not yday and on_file:
        where += (" Nothing at all came in yesterday — if today comes back empty "
                  "too, say why on the task rather than closing it quietly.")
    elif not on_file:
        where += " Nothing on file yet, so this is the first real batch."

    # Say whose number this is. A sector working to a figure the owner never
    # typed should be honest that it picked the figure itself, and say where he
    # changes it \u2014 otherwise the first he knows of it is twenty leads appearing.
    suggested = bool(tgt.get("on_suggestion"))
    if suggested:
        where += (f" Nobody has set a number for this, so the agency is working "
                  f"to the suggested {per}. Setup \u2192 Targets changes it, and "
                  f"setting it to 0 stops this job entirely.")

    detail = how + "\n\n" + where
    if owner_said:
        detail = (f"The owner asked for this in his own words:\n"
                  f"    \u201c{owner_said[1]}\u201d\n\n" + detail)

    if owner_said:
        evidence = (f"owner asked for {per} {_unit(tgt, job, per)} today, "
                    f"{done} so far, {yday} yesterday, {on_file} on file")
    elif suggested:
        evidence = (f"no {job} target set, so the suggested {per} "
                    f"{_unit(tgt, job, per)} applies \u2014 {done} so far, "
                    f"{yday} yesterday, {on_file} on file")
    else:
        evidence = (f"owner asked for {per} {_unit(tgt, job, per)} today, "
                    f"{done} so far, {yday} yesterday, {on_file} on file")

    return {"sector": sector, "risk": "green",
            # His own order outranks the MD's own scheduling, so a folded task
            # keeps the urgency the standalone order task would have had.
            "urgency": 1 if owner_said else 4,
            "title": (f"Owner's order: {owner_said[1][:60]}" if owner_said else title),
            "detail": detail,
            "evidence": evidence}


def plan(facts: dict) -> list:
    """
    Candidate tasks, each citing the row that justifies it.

    Ordered by what actually costs the business money: a dead enquiry path
    outranks a keyword gap every time, because the site keeps attracting people
    and quietly dropping every one of them.
    """
    out = []
    site = facts.get("site") or {}

    # 1. WHAT THE SECTORS ASKED FOR. They know their own domain; the MD's job is
    #    to rank, not to second-guess the expertise. Their urgency is respected
    #    but not obeyed — a sector cannot promote itself past everything else.
    for p in facts.get("proposals") or []:
        out.append({
            "sector": p["sector"], "risk": "green",
            "title": p["title"],
            "detail": p.get("detail") or "",
            "evidence": p.get("evidence") or "",
            "needs": p.get("needs") or "",
            "urgency": max(2, int(p.get("urgency") or 5)),
            "proposal_id": p["id"]})

    # 2. WHAT THE OWNER TOLD A SECTOR TO DO. He outranks the MD outright, so this
    #    is urgency 1 and the Critic will not let it be dropped for the ceiling.
    #
    #    ONE TASK, NOT TWO, WHEN THE ORDER IS THE NUMBER.
    #
    #    "merge them into 1 task just on the base when the user will ask to find
    #     the next leads and also the previous day's leads"
    #
    #    His Leads board carried both "Owner's order: find 20 leads" and
    #    "Collect today's leads" — the same instruction twice, from two paths
    #    that had never been introduced. With one job per sector per run that
    #    is not merely untidy: the sector spends a whole run on the copy that
    #    only restates the number, and does no collecting at all.
    #
    #    So an order that NAMES a target is folded into that target's own task
    #    below, in his own words. An order that names no number — "focus on
    #    Sharjah hotels" — has no target to fold into and stays its own task,
    #    which is right: it is direction, not a quantity.
    folded = {}
    for sector, text in (facts.get("sector_orders") or {}).items():
        named = []
        try:
            named = [x["name"] for x in _targets.from_words(text, apply=False)]
        except Exception:
            named = []
        covered = [n for n in named if _target(facts, n).get("per_day")]
        if covered:
            for n in covered:
                folded[n] = (sector, text)
            continue
        out.append({
            "sector": sector, "risk": "green", "urgency": 1,
            "title": f"Owner's order: {text[:60]}",
            "detail": text,
            "evidence": f"the owner set this order for {sector} today"})

    crit = site.get("critical", 0)
    if crit:
        out.append({
            "sector": "IT", "risk": "green", "urgency": 3,
            "title": "Re-check the enquiry path after critical findings",
            "detail": ("Read-only re-check of the enquiry path first, then hygiene. "
                       "File findings; queue any fix, never apply it."),
            "evidence": f"{crit} critical site issue(s) open in site_issues"})

    high = site.get("high", 0)
    if high and not crit:
        out.append({
            "sector": "IT", "risk": "green", "urgency": 4,
            "title": "Read-only health check on the site",
            "detail": "Enquiry path first, then hygiene. File findings only.",
            "evidence": f"{high} high-severity site issue(s) open in site_issues"})

    checks = _target(facts, "site_checks")
    if checks.get("per_day") and checks.get("remaining"):
        out.append({
            "sector": "IT", "risk": "green", "urgency": 4,
            "title": "Daily website diagnostic",
            "detail": "The owner's daily site check. Read-only; file what is wrong.",
            "evidence": (f"owner asked for {checks['per_day']} site check(s) today, "
                         f"{checks['done_today']} done")})

    if not facts.get("source_map_done"):
        out.append({
            "sector": "Leads", "risk": "green", "urgency": 4,
            "title": "Map the lead sources across every buying segment",
            "detail": ("Build the source map: which directories and segments to work, "
                       "each tied to the service that segment actually buys. "
                       "Collect nothing yet."),
            "evidence": "no leads-sources decision on file — the map has never been built"})

    lead_t = _target(facts, "leads")
    if lead_t.get("per_day") and lead_t.get("remaining"):
        out.append(_counted_job(
            "Leads", "leads", lead_t, folded,
            "Collect today's leads across the mapped segments",
            "Work the source map. Every lead carries the service that business "
            "needs. Dedup before storing; contact nobody."))

    # THE POSTS TARGET HAD NOWHERE TO GO. targets.py has counted "Pages or
    # posts published" since it was written, the owner can set it, Report scores
    # against it — and nothing in this plan ever read it, so asking for a post
    # produced a target nobody acted on and a Content board with nothing on it.
    # Leads had this branch; Content did not, and that asymmetry is the whole
    # reason "1 content post" came back as no post.
    post_t = _target(facts, "posts")
    if post_t.get("per_day") and post_t.get("remaining"):
        out.append(_counted_job(
            "Content", "posts", post_t, folded,
            "Write the page that is next in the queue",
            "Take the next briefed page and write it. Real facts only, from the "
            "cabinet — no invented projects, no invented numbers. It queues for "
            "the owner's approval; nothing goes on the site from here."))

    if facts.get("approvals"):
        out.append({
            "sector": "Report", "risk": "green", "urgency": 6,
            "title": "Chase the items waiting on the owner",
            "detail": "Summarise what is queued for approval and why each is blocking.",
            "evidence": f"{facts['approvals']} item(s) sitting in the approval queue"})

    return out


# ---------------------------------------------------------------------------
# CHALLENGE — the Critic. Nothing reaches the board until this passes.
# ---------------------------------------------------------------------------

# What each sector needs on the shelf before its work can possibly succeed.
#
# THIS WAS {"Leads": "maps"}, AND IT IS THE SECOND REASON LEADS NEVER PRODUCED.
#
# With no Google Places key the Critic rejected every Leads task as "doomed for
# want of a key" — before the task ever reached the board. So even once the
# target was set, the sector stayed empty, and the rejection read like the
# system being careful rather than the system being wrong.
#
# It was wrong. Leads has needed no key since the OpenStreetMap path was added:
# 'Leads collects from OpenStreetMap' is a step in the day run, it asks for
# nothing on the shelf, and on a plain run here it collected 18 real UAE
# businesses with contact routes. leads_hunt's own no-key message has said so
# in as many words for months — "you do not need one to get leads" — while this
# line went on cancelling the work that message was pointing at.
#
# Places is an upgrade, not a prerequisite: it carries opening hours, ratings
# and categories OSM does not. An upgrade belongs in the output of a hunt that
# ran, not in a gate that stops it running.
#
# The mechanism stays — a proposal can still name the shelf it genuinely cannot
# run without, via its own `needs`. What is gone is the standing assumption
# about a whole sector. Third time this codebase has met this trap: a rule that
# cries wolf on ordinary work is a rule that stops the work.
NEEDS = {}


def critic_check(candidates: list, facts: dict) -> tuple:
    """
    Challenge the plan. Returns (kept, rejected) — each rejection says why.

    This is the guard against a confident MD steering everyone wrong, so it is
    deliberately harsh: it would rather post nothing than post work that is
    ungrounded, already on the board, doomed for want of a key, or a repeat of
    something that has been failing.
    """
    kept, rejected = [], []
    missing = set(facts.get("missing") or [])
    open_titles = facts.get("open_titles") or set()
    failures = facts.get("recent_failures") or []
    seen = set()

    for c in candidates:
        key = (c["sector"], c["title"].strip().lower())

        if not (c.get("evidence") or "").strip():
            rejected.append({**c, "why": "no evidence cited — a hunch, not a plan"})
            continue
        if key in open_titles:
            rejected.append({**c, "why": "already on the board, unclaimed or in progress"})
            continue
        if c.get("title", "").strip().lower() in facts.get("objected_titles", set()):
            # a sector has already said this is not its work. Re-posting it
            # unchanged is the MD ignoring the only feedback it gets.
            rejected.append({**c, "why": "a sector objected to this and it has not "
                                         "been re-routed — needs a different sector"})
            continue
        if key in seen:
            rejected.append({**c, "why": "duplicated inside this same plan"})
            continue
        # a proposal names the shelf it cannot run without; otherwise fall back
        # to what we know the sector needs in general
        need = (c.get("needs") or "").strip() or NEEDS.get(c["sector"])
        if need and need in missing:
            # An OWNER'S order that cannot run is not a rejection, it is news he
            # needs. Dropping it into the reject pile with everything else is how
            # an instruction gets quietly binned and nobody finds out for a week.
            owner_order = int(c.get("urgency") or 5) <= 1
            rejected.append({**c, "owner_blocked": owner_order,
                             "why": (f"YOU ASKED FOR THIS AND IT CANNOT RUN: the {need} "
                                     f"shelf is empty. Add the key and it goes straight out."
                                     if owner_order else
                                     f"the {need} shelf is empty — this would fail "
                                     "the moment it ran")})
            continue
        if failures.count(c.get("cycle", "")) >= 3:
            rejected.append({**c, "why": "this line of reasoning has failed three times"})
            continue
        if c.get("risk", "green") != "green":
            rejected.append({**c, "why": "not green — goes to the owner, not the board"})
            continue

        seen.add(key)
        kept.append(c)

    # Rank before the ceiling bites, or the ceiling drops whatever happened to be
    # last in the list rather than whatever matters least. The owner's own orders
    # are urgency 1 and are never dropped: if they alone exceed the ceiling, the
    # ceiling yields, because a limit that silently bins the owner's instruction
    # is worse than a long day.
    kept.sort(key=lambda c: (int(c.get("urgency") or 5), c["sector"]))
    order, note = _ask_both_which_matters(kept, facts)
    if order:
        rank = {t: i for i, t in enumerate(order)}
        # the owner's orders stay pinned to the front whatever the models think
        kept.sort(key=lambda c: (int(c.get("urgency") or 5) > 1,
                                 rank.get(c["title"].strip().lower(), 999)))
    if note:
        facts["ranking_note"] = note
    if len(kept) > MAX_TASKS_PER_CYCLE:
        overflow = [c for c in kept[MAX_TASKS_PER_CYCLE:] if int(c.get("urgency") or 5) > 1]
        owner_work = [c for c in kept[MAX_TASKS_PER_CYCLE:] if int(c.get("urgency") or 5) <= 1]
        for c in overflow:
            rejected.append({**c, "why": "over the day's task ceiling — kept for tomorrow"})
        kept = kept[:MAX_TASKS_PER_CYCLE] + owner_work
    return kept, rejected


# ---------------------------------------------------------------------------
# DELEGATE — post the surviving plan. The only thing the MD ever writes.
# ---------------------------------------------------------------------------

def run(agent="managing-director", dry_run=False) -> dict:
    with board.agent_run(agent) as run_row:
        out = _cycle(agent, dry_run)
        run_row.note(out.get("summary") or out.get("error") or "planned the day")
        return out


def _cycle(agent, dry_run):
    facts = sense()

    # THE POST, BEFORE THE PLAN. Deliberately first: an escalation saying SEO's
    # strategist has been dead for two days changes what the day should be, and
    # reading it after planning is reading it too late. This is also the step
    # that was missing entirely — the MD had an inbox nothing ever opened.
    inbox = clear_inbox(facts, dry_run)

    candidates = plan(facts)
    kept, rejected = critic_check(candidates, facts)

    # Answer every proposal FIRST. This used to sit after the "nothing to post"
    # early return, so on a day when the Critic rejected the whole plan not one
    # sector was told why — the precise silent-binning this queue exists to stop.
    answered = 0
    for c in rejected:
        if c.get("proposal_id"):
            try:
                _props.decide(c["proposal_id"], "rejected", c["why"])
                answered += 1
            except Exception:
                pass

    hit = facts.get("hit_rate")
    scoreline = ("no scored decisions yet — no track record to reason from"
                 if hit is None else f"recent hit-rate {hit:.0%}")

    if not kept:
        stuck = [r for r in rejected if r.get("owner_blocked")]
        summary = (f"Nothing to post. {len(rejected)} candidate(s) rejected; {scoreline}."
                   + (f" {len(stuck)} OF YOUR OWN ORDER(S) CANNOT RUN." if stuck else ""))
        return {"ok": True, "agent": agent, "posted": [], "rejected": rejected,
                "owner_blocked": stuck, "answered": answered, "inbox": inbox,
                "facts": facts, "summary": _with_inbox(summary, inbox)}

    if dry_run:
        return {"ok": True, "agent": agent, "dry_run": True, "would_post": kept,
                "rejected": rejected, "facts": facts, "inbox": inbox,
                "summary": _with_inbox(
                    f"{len(kept)} task(s) would be posted; nothing written", inbox)}

    posted = []
    for c in kept:
        # A sector the owner has switched off is not a failure to plan around —
        # it is an instruction. The item is recorded as skipped, with the reason,
        # rather than crashing the cycle or silently disappearing.
        try:
            tid = board.post_task(c["sector"], c["title"],
                                  c["detail"] + f"\n\n[MD evidence] {c['evidence']}",
                                  risk=c.get("risk", "green"), created_by="MD")
        except board.SectorOff as e:
            rejected.append({"sector": c["sector"], "title": c["title"],
                             "why": str(e)})
            continue
        except board.BoardFull as e:
            # Not a failure of the plan — a fact about the sector. The MD is
            # told so it can plan around a desk that is not clearing its board,
            # rather than adding a ninth job to eight nobody has touched.
            rejected.append({"sector": c["sector"], "title": c["title"],
                             "why": str(e)})
            continue
        except board.DeskBusy as e:
            # The desk is working. Let it work — see board.desk_at_work.
            rejected.append({"sector": c["sector"], "title": c["title"],
                             "why": str(e)})
            continue
        posted.append({"task_id": tid, "sector": c["sector"], "title": c["title"],
                       "evidence": c["evidence"]})
        if c.get("proposal_id"):
            try:
                _props.decide(c["proposal_id"], "accepted",
                              f"posted to {c['sector']} as task {tid}", task_id=tid)
                answered += 1
            except Exception:
                pass

    did = board.log_decision(
        "md-cycle",
        reasoning=(f"Planned from the cabinet: {scoreline}; "
                   f"{facts.get('site', {}).get('critical', 0)} critical and "
                   f"{facts.get('site', {}).get('high', 0)} high site issue(s) open; "
                   f"{facts.get('leads', {}).get('total', 0)} lead(s) on file. "
                   f"{len(kept)} task(s) posted, {len(rejected)} rejected by the Critic."),
        expectation="Each sector claims its task and the day's work actually starts.",
        confidence=0.7,
        # proposed/rejected/sectors are what the MD is MANAGED on, not just what
        # it produced: how much of the plan was guesswork the Critic had to throw
        # out, and which sectors it actually gave work to. The management card in
        # coordination/ reads these. The MD records them and never grades itself.
        evidence={"task_ids": [p["task_id"] for p in posted],
                  "proposed": len(candidates),
                  "rejected": len(rejected),
                  "sectors": sorted({p["sector"] for p in posted}),
                  # sectors it was not free to direct at all, so the scorecard
                  # does not mark it down for neglecting them
                  "blocked_sectors": sorted({r["sector"] for r in rejected
                                             if "shelf is empty" in r["why"]})})

    stuck = [r for r in rejected if r.get("owner_blocked")]
    note = facts.get("ranking_note") or ""
    summary = (f"posted {len(posted)} task(s), Critic rejected {len(rejected)}"
               + (f", answered {answered} proposal(s)" if answered else "")
               + (f" — {len(stuck)} OF YOUR OWN ORDER(S) CANNOT RUN" if stuck else "")
               + (f". {note}" if note else ""))
    return {"ok": True, "agent": agent, "posted": posted, "rejected": rejected,
            "owner_blocked": stuck, "decision_id": did, "facts": facts,
            "inbox": inbox, "summary": _with_inbox(summary, inbox)}


def _with_inbox(summary, inbox):
    """The post is part of the day's work, so it belongs in the day's summary."""
    n = len(inbox.get("answered") or [])
    if not n and not inbox.get("still_waiting"):
        return summary
    bits = []
    if n:
        bits.append(f"answered {n} request(s)")
    if inbox.get("still_waiting"):
        bits.append(f"{inbox['still_waiting']} still queued")
    return "; ".join([summary] + bits) if summary else "; ".join(bits)


def as_text(out) -> str:
    L = [f"MD CYCLE — {out.get('summary', '')}", ""]
    ib = out.get("inbox") or {}
    if ib.get("answered") or ib.get("still_waiting"):
        L.append(f"THE POST — {ib.get('head', '')}")
        for a in ib.get("answered") or []:
            L.append(f"  [{a['points']:>5}] {a['from']} · {a['subject'][:56]}")
            L.append(f"          {a['why']}")
            if a.get("task_id"):
                L.append(f"          -> posted to {a['posted_to']} as {a['task_id']}")
        if ib.get("still_waiting"):
            L.append(f"  ({ib['still_waiting']} left in the queue, rising as they wait)")
        L.append("")
    if out.get("posted") or out.get("would_post"):
        L.append("POSTED TO THE BOARD" if out.get("posted") else "WOULD POST")
        for p in out.get("posted") or out.get("would_post"):
            L.append(f"  + [{p['sector']}] {p['title']}")
            L.append(f"      because: {p['evidence']}")
    if out.get("rejected"):
        L += ["", "REJECTED BY THE CRITIC"]
        for r in out["rejected"]:
            L.append(f"  - [{r['sector']}] {r['title']}")
            L.append(f"      {r['why']}")
    return "\n".join(L)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="The MD's daily planning cycle.")
    ap.add_argument("--dry-run", action="store_true", help="show the plan, post nothing")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    res = run(dry_run=a.dry_run)
    print(json.dumps(res, indent=2, default=str) if a.json else as_text(res))
