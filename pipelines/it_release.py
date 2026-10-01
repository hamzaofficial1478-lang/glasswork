"""
it_release.py — the IT Releaser. Takes what the owner approved and puts it on
his website. The only desk in the agency allowed through that door.

    python -m pipelines.it_release              apply what he has approved
    python -m pipelines.it_release --list       what is waiting, change nothing
    python -m pipelines.it_release --dry-run    work it all out, write nothing
    python -m pipelines.it_release --skip-tests he is watching and in a hurry

WHY THIS FILE EXISTS

"if it cannot make changes to site then what was full acess of website and the
 files uploaded into the website?"

Fair question, and the answer was embarrassing. He set up full access, pasted
the endpoint onto his server, and his approval went into a table that nothing
ever read. pending → approved → and there it sat, for ever. The Watcher found
problems, the Fixer wrote the changes, he said yes, and the chain stopped one
link short of his website.

This is the last link. It reads what he approved, checks the tests still pass,
takes the lock, writes the change, reads the page back to confirm it is really
there, and records the lot in the change log.

THE FOUR THINGS THAT GUARD IT, IN ORDER

  1. HE HAS TO HAVE SAID YES. Only rows at status='approved' are touched.
     Nothing here can promote its own work — the Fixer queues, he decides, this
     applies. Three desks, and no desk does two of those jobs.

  2. THE TESTS HAVE TO PASS. The handbook is explicit and it is the right rule:
     a change goes out after the suite is green, not before. It runs the whole
     suite, once, before the first write of the batch — not per change, because
     the code has not moved between them. --skip-tests exists because he is the
     tie-breaker, and using it is recorded against every change in the batch so
     the record says so out loud.

  3. ONE DESK AT A TIME. The whole batch holds a single lock, and each page
     holds its own on top. Two agents writing one file is the thing the
     coordination layer exists to stop.

  4. IT READS THE PAGE BACK. "The server said ok" and "the change is on the
     page" are different claims, and only the second one is worth telling him.
     A write that cannot be confirmed is undone immediately and recorded as
     failed, because a half-applied change nobody knows about is worse than no
     change at all.

WHAT IT WILL NOT DO

It does not invent. Every change it makes was written out in full by the Fixer
and shown to him before he approved it — the page, the block, the literal
markup. If an approved row does not carry a change it can carry out, it says so
and leaves it alone rather than doing something nearby that looks similar.
"""
import argparse
import json
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from coordination import board, changelog, console, deskwork
from . import site_api

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable or "python3"

AGENT = "it-releaser"
SECTOR = "IT"

# One lock for the whole batch. Named for what it protects rather than for this
# module, so anything else that ever needs to hold the site still queues behind
# it properly.
LOCK = "site_writes"

# BOUNDED BY THE DAY, not by patience. This runs as a step inside day_run, and
# a budget larger than the step's would be killed mid-suite and read as a crash
# rather than as "the tests took too long", which is a much worse thing to see.
#
# "the test suite did not finish inside 360s (my conerce is the time is out why
#  cannot we test by increasing the time out limit)"
#
# He is right, and the reason it could not be raised was circular: this had to
# fit under a step timeout that was one number for every step in the day, so
# giving the suite more time meant giving a hung step more time too. day_run
# names the few steps that honestly need longer — this is one — so the budget
# is now read from the slot this step actually gets, less the time the writes
# afterwards need. It is not a copy of that number; a second copy is a second
# thing to forget when the first one changes.
#
# Running out of time means nothing is written. That is the safe direction: an
# unproven suite is exactly the case the rule exists for.
WRITE_RESERVE = 120


def _test_budget() -> int:
    try:
        from . import day_run
        slot = int(day_run.step_timeout_for("pipelines.it_release"))
    except Exception:                                        # pragma: no cover
        slot = 420
    return max(120, slot - WRITE_RESERVE)


TEST_TIMEOUT = _test_budget()

MAX_PER_PASS = 10


def _now():
    return int(time.time())


# ---------------------------------------------------------------------------
# HIS SITE DID NOT ANSWER — THE CHANGE WAITS, IT IS NOT REFUSED
# ---------------------------------------------------------------------------
#
# "approved a blog and passed it to publish to wesbite but this issue happenes
#  ... why the changes are still not happening?"
#
# The agency had paused contact with his site after one closed connection, the
# Releaser tried his blog inside the pause, and _could_not marked it failed —
# which took it off every list that retries anything. His approval was about
# the page, not about his site answering at that minute. Now a site that is
# resting, down or unreachable leaves the change approved with a note saying
# so, and a small waiter tries again as soon as the site answers.

# The words a failure carries when his site simply did not answer. Not "could
# not reach your website: <a setting is missing>" — that one is his to fix on
# Setup, fails loudly, and must not bounce back into the queue every pass.
UNREACHABLE_WORDS = ("could not reach your site:", "siteresting",
                     "stopped answering this computer")
# And when the only thing wrong was this program's own tests.
RED_SUITE_WORDS = ("the test suite is red", "the test suite did not finish",
                   "could not run the tests")


def _unreachable(r) -> bool:
    return bool(r.get("unreachable")) if isinstance(r, dict) else False


def _wait_for_site(plan, why):
    """Keep his approval; say the change waits for his site, and when it goes."""
    from coordination import sitepace
    rest = sitepace.resting()
    when = (" It goes by itself at " + time.strftime("%H:%M", time.localtime(rest["until"]))
            + ", when the pause ends." if rest else
            " It is tried again by itself in a few minutes.")
    board.note_site_change(plan["row"]["id"],
                           "waiting for your website to answer — " + str(why)[:300] + when)
    return False


RETRY_LOCK = "release_retry"
RETRY_MOST = 12            # goes a chain of waiters makes before leaving it to the day run
FIRST_WAIT = 120           # a line that has just dropped: give it two minutes
LONGEST_WAIT = 3 * 3600
_ATTEMPT = 0               # which go this is, when a waiter started the run


def _spawn_retry(attempt):
    """A waiter, as its own process, so it outlives the run that started it."""
    import os
    import subprocess
    kw = {"cwd": str(Path(__file__).parents[1]), "stdin": subprocess.DEVNULL,
          "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        from coordination import console
        kw["creationflags"] = console.background_flags()
    else:
        kw["start_new_session"] = True
    subprocess.Popen([sys.executable, "-m", "pipelines.it_release",
                      "--when-site-answers", "--attempt", str(attempt)], **kw)


def _schedule_retry() -> bool:
    """One waiter at a time, that runs the Releaser again when his site answers."""
    import os
    if _ATTEMPT >= RETRY_MOST:
        return False
    if os.environ.get("AGENCY_IGNORE_DOTENV") == "1" and \
            os.environ.get("RELEASE_RETRY_TEST") != "1":
        return False                      # the test suite never starts processes
    with board._conn() as c:
        r = c.execute("SELECT expires_at, pid FROM locks WHERE resource=?",
                      (RETRY_LOCK,)).fetchone()
    if r and (r["expires_at"] or 0) > _now():
        try:
            if not r["pid"] or board.process_alive(r["pid"]):
                return False              # one is already waiting
        except Exception:
            return False
    try:
        _spawn_retry(_ATTEMPT + 1)
        return True
    except Exception:
        return False


def when_site_answers(attempt=1, sleep=None) -> dict:
    """
    The waiter: sit out the pause (at least a couple of minutes), then run the
    Releaser once. If his site is still not answering, that run starts the next
    waiter — up to RETRY_MOST goes, after which the day run carries on.
    """
    global _ATTEMPT
    from coordination import sitepace
    sleep = sleep or time.sleep
    if not board.acquire_lock(RETRY_LOCK, AGENT + "-waiter", wait=False,
                              ttl=LONGEST_WAIT + 600):
        return {"ok": True, "already": True}
    try:
        started = time.time()
        sleep(FIRST_WAIT)
        while time.time() - started < LONGEST_WAIT:
            rest = sitepace.resting()
            if not rest:
                break
            sleep(min(300, max(10, rest["until"] - time.time() + 10)))
    finally:
        board.release_lock(RETRY_LOCK, AGENT + "-waiter")
    if not board.approved_site_changes(limit=1):
        return {"ok": True, "nothing": True}
    _ATTEMPT = attempt
    return run()


def _reopen_misread_calls(days=30) -> list:
    """
    His "make every email on my website mine", approved from the chat and then
    refused by an older plan_of that could not read the name it was queued
    under. The routine is on the allow-list and his yes still stands, so it
    goes back in the queue once, now that it can be read.
    """
    out = []
    since = int(time.time()) - days * 86400
    try:
        with board._conn() as c:
            for r in c.execute("SELECT id, detail, note FROM site_queue WHERE status='failed'"
                               " AND COALESCE(applied_at, decided_at, queued_at, 0)>=?",
                               (since,)).fetchall():
                if "carries no change" not in (r["note"] or ""):
                    continue
                try:
                    ch = (json.loads(r["detail"] or "{}") or {}).get("change") or {}
                except Exception:
                    continue
                if isinstance(ch, dict) and str(ch.get("call") or "") in ALLOWED_CALLS:
                    c.execute("UPDATE site_queue SET status='approved', applied_at=NULL,"
                              " note=? WHERE id=?",
                              ("waiting — put back: it was misread before, and your "
                               "yes still stands", r["id"]))
                    out.append(r["id"])
    except Exception:
        return out
    return out


def _could_not(plan, why):
    """
    Mark one approved change as failed, and put it where he will see it.

    A FAILURE THAT ONLY MARKS THE ROW IS A FAILURE HE NEVER HEARS ABOUT. The
    approval queue lists status='pending', so the moment a row goes to 'failed'
    it vanishes off the one screen he was watching — he pressed Approve, the
    item disappeared, and he would reasonably assume it had gone on his site.

    So every failure also writes a line to the change log, which is the screen
    he now opens to ask exactly that question.
    """
    row = plan["row"]
    board.mark_site_change(row["id"], "failed", why)
    changelog.record(
        "not_done", page=(plan.get("page") or ", ".join(plan.get("pages") or []) or ""),
        agent=AGENT, ok=False, error=why, issue_id=plan.get("issue_id"),
        summary="a change you approved could not be carried out")
    return False


# ---------------------------------------------------------------------------
# READING WHAT HE APPROVED
# ---------------------------------------------------------------------------

def plan_of(row) -> dict:
    """
    Turn one approved row into something this desk can carry out, or say why not.

    The Fixer wrote these, so the shapes are known and small:

      change {page, block, html}   put this markup into that page's head
      change {run, pages}          call the named site_api routine
      what_to_do [...]             advice. There is nothing here to apply — it
                                   was always something for him to do — so it
                                   is acknowledged and closed rather than
                                   pretended at.

    Anything else is refused by name. Guessing at an unfamiliar shape is how an
    agent writes something nobody approved.
    """
    try:
        d = json.loads(row.get("detail") or "{}")
    except Exception:
        d = {}
    if not isinstance(d, dict):
        d = {}

    issue = d.get("issue_id")
    change = d.get("change") or {}

    # A WHOLE NEW PAGE, written by Content and passed by the Auditor.
    #
    # The last link in the publishing chain, and it was the missing one in the
    # same way the whole of this file was: Content could brief a page, nothing
    # could write one, and nothing could have put one on the site if it had.
    #
    # Deliberately its own shape rather than reusing 'page'. Writing a file
    # that does not exist yet is a different act from editing one that does —
    # there is no backup to fall back to, nothing links to it yet, and the
    # confirmation has to be "the page is now there" rather than "the change
    # is in it". Reading the row as a plain page edit would have been quietly
    # wrong on all three.
    # A POST FOR HIS BLOG goes into his own blog backend through the connector's
    # blog section (pipelines/blog_backend) — a row in the table his blog page
    # reads, not a file. And taken back out the same way.
    # A PAGE TAKEN OFF HIS WEBSITE — one the agency put up as a post of its own
    # before posts went into his blog. The connector keeps a copy first.
    if isinstance(change, dict) and change.get("remove_page"):
        return {"do": "remove_page", "page": str(change["remove_page"]),
                "was": change.get("was"), "go_to": change.get("go_to") or "",
                "content_id": change.get("content_id"), "issue_id": issue,
                "why": d.get("why") or "", "row": row}

    if isinstance(change, dict) and (change.get("blog_post") or change.get("blog_remove")
                                     or change.get("blog_repost")):
        which = next(k for k in ("blog_post", "blog_remove", "blog_repost") if change.get(k))
        return {"do": which, "content_id": change.get(which), "page": "blog",
                "issue_id": issue, "why": d.get("why") or "", "row": row}

    if isinstance(change, dict) and change.get("html") and change.get("page") \
            and str(row.get("kind") or "") == "new_page":
        return {"do": "new_page", "page": change["page"], "html": change["html"],
                "content_id": change.get("content_id"),
                "issue_id": issue, "why": d.get("why") or "", "row": row}

    # A WHOLE EXISTING PAGE, rewritten — queued by site_api when a desk other
    # than the Releaser was stopped from writing it. Checked before the head
    # block, which has the same two keys: reading a whole page as a block
    # would paste an entire document into the <head>.
    if isinstance(change, dict) and change.get("html") and change.get("page") \
            and (change.get("whole_page") or str(row.get("kind") or "") == "page"):
        return {"do": "page", "page": change["page"], "html": change["html"],
                "issue_id": issue, "why": d.get("why") or "", "row": row}

    if isinstance(change, dict) and change.get("html") and change.get("page"):
        return {"do": "head_block", "page": change["page"],
                "block": change.get("block") or "agency",
                "html": change["html"], "issue_id": issue,
                "why": d.get("why") or "", "row": row}

    if isinstance(change, dict) and change.get("replace") and change.get("with"):
        return {"do": "replace", "find": change["replace"],
                "to": change["with"], "pages": change.get("pages") or [],
                "issue_id": issue, "why": d.get("why") or "", "row": row}

    # The search widget, queued by pipelines/site_search with the markup in it.
    # It was un-carryable purely because nothing here knew the shape, so he
    # approved it and got "this approval does not carry a change anyone can
    # carry out" — which is the program blaming him for its own gap.
    if isinstance(change, dict) and change.get("html") and not change.get("page"):
        return {"do": "refuse", "issue_id": issue, "row": row,
                "why": "this one is markup to paste into your header, and it "
                       "does not say which file — open it from the office and "
                       "paste it, or tell IT which file holds your header."}

    # 'run' from the Fixer, 'call' from the chat and from site_api's own queue.
    # Reading only 'run' is why "change the dummy mail on my website" was
    # approved on his word and then refused here as a change nobody could
    # carry out — the same routine, under the other name.
    if isinstance(change, dict) and (change.get("run") or change.get("call")):
        name = str(change.get("run") or change.get("call"))
        if name not in ALLOWED_CALLS:
            return {"do": "refuse", "issue_id": issue, "row": row,
                    "why": f"'{name}' is not something the Releaser knows how "
                           "to carry out, so it has not been attempted."}
        return {"do": "call", "call": name, "pages": change.get("pages") or [],
                "issue_id": issue, "why": d.get("why") or "", "row": row}

    if d.get("what_to_do"):
        return {"do": "noted", "issue_id": issue, "row": row,
                "why": d.get("why") or "",
                "steps": d.get("what_to_do") or []}

    # A PLAIN NOTE IS NOT A BROKEN CHANGE.
    #
    # Four other places queue things for him and only one of them queues a
    # change to carry out. seo_answers asks whether blocking an AI crawler in
    # robots.txt was deliberate; site_services asks which website services
    # should join the catalogue. Those are questions, and their detail is a
    # sentence rather than an object.
    #
    # Answering one used to produce "this approval does not carry a change
    # anyone can carry out" — the program calling his answer malformed when it
    # was the program that had never learned to read it. A question he has
    # answered is answered: noted, and off his list.
    if isinstance(d, str) or not d:
        return {"do": "noted", "issue_id": issue, "row": row,
                "why": (row.get("detail") or "")[:300],
                "steps": []}

    # AN EMPTY NOTE FROM AN OLDER BUILD. site_api used to queue a blocked
    # write as its page name and size only, dropping the change itself (see
    # site_api._queue_for_him). Say what it actually was.
    if d.get("page") and ("bytes" in d or "id" in d) or d.get("find"):
        return {"do": "refuse", "issue_id": issue, "row": row, "empty_note": True,
                "why": (f"this was only a note that {row.get('agent') or 'a desk'} "
                        f"had been stopped from writing to "
                        f"{d.get('page') or 'your site'} — the change itself was "
                        "never saved with it, so there was nothing to put on. "
                        "Nothing was changed. Newer notes carry the change, and "
                        "the desk will ask again if it still wants it.")}

    return {"do": "refuse", "issue_id": issue, "row": row,
            "why": ("this approval carries no change and no instruction — it "
                    "looks like a record of something that was refused rather "
                    "than a proposal. Nothing was done to your site.")}


def carryable(row) -> tuple:
    """
    (True, "") if the Releaser could carry this out once he says yes, else
    (False, why). Asked BEFORE a change is put to him, so he is never asked to
    approve a note with nothing in it.
    """
    try:
        plan = plan_of(dict(row))
    except Exception as e:
        return False, f"it could not be read: {e}"
    if plan.get("do") == "refuse":
        return False, plan.get("why") or ""
    return True, ""


# The only routines this desk may call, by name. A list rather than getattr on
# whatever string is in the row: getattr would happily run anything in site_api
# that a malformed or tampered-with row named, including undo_schema, which
# would strip the agency's markup off every page because a queue row said so.
ALLOWED_CALLS = {
    "fix_canonical": "point every canonical tag at the right domain",
    "fix_address": "put the saved address where the placeholder is",
    "apply_schema": "put the business markup on the pages that should carry it",
    "use_one_email": "make every email address on the website the one he reads",
    "use_one_domain": "make every mention of his old domain the one his site is on",
    "repair_share_buttons": "put Facebook, WhatsApp and LinkedIn back in the share buttons",
    "publish_sitemap": "put the sitemap on his website and give it to Search Console",
}


# ---------------------------------------------------------------------------
# THE GUARDS
# ---------------------------------------------------------------------------

def tests_pass(timeout=TEST_TIMEOUT) -> dict:
    """
    Run the whole suite. Green or it does not go out.

    Run in a subprocess and read by exit code, deliberately. Importing the
    suites into this process would let one of them redirect board.DB — several
    of them do exactly that on purpose — and leave this desk writing its
    records into a temporary file that is deleted a moment later.
    """
    t0 = time.time()
    try:
        # No window of its own (console.background_flags): started from the
        # Review page's "Put it live now", this runs with no console, and on
        # Windows the suite would otherwise open one on his screen.
        p = subprocess.run([PY, "run.py", "--tests", "--no-update"],
                           cwd=str(ROOT), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=timeout,
                           creationflags=console.background_flags())
    except subprocess.TimeoutExpired:
        return {"ok": False, "took_s": int(time.time() - t0),
                "why": f"the test suite did not finish inside {timeout}s"}
    except Exception as e:                                   # pragma: no cover
        return {"ok": False, "took_s": 0, "why": f"could not run the tests: {e}"}
    out = (p.stdout or "") + (p.stderr or "")
    lines = [l.rstrip() for l in out.splitlines()]
    # Which suites went red...
    # Only the lines that NAME a suite. A detail line also starts with FAIL,
    # and letting it in put the same sentence in the headline and the body.
    suites = [l.strip() for l in lines
              if (l.strip().startswith("FAIL  ") and not l.startswith("        ")
                  and "." in l.split()[-1] and " " not in l.strip()[6:].strip())
              or "suite(s) failing" in l]
    # ...and WHY. run.py now prints the failing check under its suite, indented.
    # Carrying it through is the difference between "a file is unhappy" and a
    # sentence he can act on without going back to a terminal.
    why_lines, grab = [], False
    for l in lines:
        s = l.strip()
        if s.startswith("FAIL  ") and not l.startswith("        "):
            grab = True
            continue
        if grab:
            if l.startswith("        ") and s:
                why_lines.append(s)
            elif s:
                grab = False
    # WHAT FAILED FIRST, then what it means. "What actually failed: FAIL the
    # age" was all he could read — the Activity line cut the sentence off
    # after the suite names, before the check that failed.
    names = [x.replace("FAIL", "").strip().replace("coordination.", "").replace("pipelines.", "")
             for x in suites if "suite(s) failing" not in x]
    first = next((w.replace("FAIL", "", 1).strip() for w in why_lines if w.startswith("FAIL")), "")
    head = ("the test suite is red"
            + (f" ({', '.join(names[:3])}" + (f": “{first[:110]}”" if first else "") + ")"
               if names else f" (exit {p.returncode})")
            + " — nothing goes on the website until it is green again")
    if why_lines:
        head += "\n\nWhat actually failed:\n" + "\n".join(
            "    " + w[:200] for w in why_lines[:10])
    return {"ok": p.returncode == 0, "took_s": int(time.time() - t0),
            "failing": suites[:6], "detail": why_lines[:20],
            "why": "all suites green" if p.returncode == 0 else head}


def confirm(page, needle, s=None) -> bool:
    """
    Read the page back and look for what we just wrote.

    'The server returned ok' and 'the change is on the page' are different
    claims. Only the second one is worth telling him, and it costs one GET.
    """
    return bool(_read_back(page, needle, s=s))


def _read_back(page, needle, s=None):
    """True: it is on the page. False: it is not. None: the site did not
    answer, so nobody can say — which must not be read as "it is not there"
    and undo a write that worked."""
    try:
        got = site_api.read_page(page, s=s)
    except Exception:
        return None
    if _unreachable(got):
        return None
    if not got.get("ok"):
        return False
    return (needle or "") in (got.get("html") or "")


UNCHECKED = (" — written, but your website stopped answering before it could be "
             "read back, so open the page to check it")


def _fail_or_wait(plan, r, fallback):
    """A write that did not happen: waiting if the site was not there, failed
    if the site said no."""
    if _unreachable(r):
        plan["_waiting"] = True
        return _wait_for_site(plan, r.get("error") or "")
    return _could_not(plan, str(r.get("error") or fallback))


# ---------------------------------------------------------------------------
# THE PASS
# ---------------------------------------------------------------------------

def run(agent=AGENT, limit=MAX_PER_PASS, dry_run=False, skip_tests=False) -> dict:
    with board.agent_run(agent) as run_row:
        with deskwork.at_the_desk(SECTOR, agent, run_row) as job:
            out = _work(agent, limit, dry_run, skip_tests)
            # "Nothing approved" comes back ok=False, and blocking the job on it
            # is right: the thing holding a release up is his own yes, and that
            # sentence says so by name rather than "something is holding it back".
            deskwork.settle(job, out, made=out.get("applied"),
                            what="change put on the site|changes put on the site")
        run_row.note(out.get("summary") or out.get("error") or "nothing approved")
        return out


def _work(agent, limit, dry_run, skip_tests):
    try:
        board.release_dead_request_claims()
    except Exception:
        pass

    # Changes marked failed only because his site did not answer go back in
    # the queue — including the blog refused in the pause that asked for this.
    try:
        board.reopen_site_changes(UNREACHABLE_WORDS)
    except Exception:
        pass
    _reopen_misread_calls()
    # And the ones an older version marked failed only because the tests were
    # red: his yes was never about the tests either.
    try:
        board.reopen_site_changes(RED_SUITE_WORDS,
                                  why="waiting — put back after the tests were red")
    except Exception:
        pass

    waiting = board.approved_site_changes(limit=limit)
    if not waiting:
        # NOTHING APPROVED IS NOT A FAILURE, AND IT WAS BEING SCORED AS ONE.
        #
        # This returned ok:false with no idle flag, so exit_code() handed the
        # shell a 1 and the day run painted the step red: "IT carries out what
        # you approved — failed". On a fresh day, with no approvals waiting —
        # which is most days — every single run therefore finished with a
        # failed step, and "a successful run" could not happen however well
        # every other sector did. He has been asking why it never has.
        #
        # The Releaser did its job: it looked, there was nothing to put on the
        # site, it put nothing on the site. That is idle — the grey state the
        # runner already has for a desk that did nothing wrong and had nothing
        # to do. When changes ARE queued for his yes, the summary still says so
        # by count, because that is the one thing here he can act on.
        pending = len(board.pending_site_changes())
        return {"ok": False, "idle": True, "agent": agent,
                "waiting_on_you": pending,
                "error": ("Nothing to put on the site — "
                          + (f"{pending} change(s) are still waiting for your "
                             "yes or no in the office."
                             if pending else
                             "nobody has approved anything since the last pass.")),
                }

    plans = [plan_of(r) for r in waiting]
    doable = [p for p in plans
              if p["do"] in ("head_block", "call", "replace", "new_page", "page",
                             "blog_post", "blog_remove", "blog_repost", "remove_page")]
    noted = [p for p in plans if p["do"] == "noted"]
    refused = [p for p in plans if p["do"] == "refuse"]

    why_not_ready = site_api.why_not_ready()
    if doable and why_not_ready:
        for p in doable:
            _could_not(p, "could not reach your website: " + why_not_ready)
        return {"ok": False, "agent": agent, "error":
                "There are " + str(len(doable)) + " approved change(s) and the "
                "agency cannot reach your website. " + why_not_ready,
                "blocked": len(doable)}

    # PAUSED: nothing is tried (and no test suite run) while the agency is
    # leaving his site alone. The changes wait and go when the pause ends.
    from coordination import sitepace
    rest = sitepace.resting()
    if doable and rest and not dry_run:
        for p in doable:
            _wait_for_site(p, rest.get("why") and ("the agency paused contact with "
                                                    "your site: " + rest["why"]) or "")
        _schedule_retry()
        at = time.strftime("%H:%M", time.localtime(rest["until"]))
        return {"ok": False, "idle": True, "agent": agent, "waiting": len(doable),
                "error": (f"{len(doable)} approved change(s) wait for your website: the "
                          f"agency paused contact with it ({rest.get('why')}). They go "
                          f"by themselves at {at}.")}

    # THE TESTS, ONCE, BEFORE THE FIRST WRITE. Not per change: the code has not
    # moved between them, and running a fifteen-minute suite ten times would
    # turn a batch into an afternoon.
    tests = {"ok": True, "skipped": True,
             "why": "you told it to go without running the tests"}
    if doable and not dry_run and not skip_tests:
        tests = tests_pass()
        if not tests["ok"]:
            # HIS YES STANDS. A red suite is about this program, not about the
            # change he approved, and marking every approval 'failed' threw his
            # decisions away — "i asked to change the mail it did not change"
            # — so each one would have had to be found and approved again.
            # They wait, with the reason on them, and go on the first pass
            # after the suite is green.
            for p in doable:
                board.note_site_change(
                    p["row"]["id"],
                    "waiting — " + tests["why"].split("\n")[0][:300]
                    + ". It goes on by itself once the tests pass.")
            changelog.record(
                "not_done", page="", agent=AGENT, ok=False,
                error=tests["why"].split("\n")[0][:300],
                summary=(f"{len(doable)} change(s) you approved are waiting — the "
                         "tests must pass before anything goes on your site"))
            return {"ok": False, "agent": agent, "tests": tests,
                    "error": tests["why"], "blocked": len(doable), "kept": len(doable)}

    applied, failed, waited = [], [], []
    if doable and not dry_run:
        # One holder for the batch. A second Releaser — a cron overlapping a
        # button press — waits rather than interleaving writes with this one.
        if not board.acquire_lock(LOCK, agent, wait=False, ttl=1800):
            return {"ok": False, "agent": agent,
                    "error": "another release is already going. Wait for it to "
                             "finish rather than writing over it."}
        try:
            for p in doable:
                if _carry_out(agent, p, skip_tests):
                    applied.append(p)
                elif p.get("_waiting"):
                    waited.append(p)
                else:
                    failed.append(p)
        finally:
            board.release_lock(LOCK, agent)
        if waited:
            _schedule_retry()

    if not dry_run:
        for p in noted:
            board.mark_site_change(
                p["row"]["id"], "applied",
                "nothing to write — this one was always something for you to "
                "do. Marked as read so it stops asking.")
        for p in refused:
            _could_not(p, p["why"])

    result = {
        "ok": True, "agent": agent, "dry_run": bool(dry_run),
        "tests": tests,
        "approved_read": len(waiting),
        "applied": len(applied), "failed": len(failed), "waiting": len(waited),
        "noted": len(noted), "refused": len(refused),
        "changes": [{"queue_id": p["row"]["id"], "do": p["do"],
                     "page": p.get("page") or ", ".join(p.get("pages") or []),
                     "issue_id": p.get("issue_id")} for p in doable],
    }
    if not dry_run:
        result["decision_id"] = board.log_decision(
            "it-release",
            reasoning=(f"{len(waiting)} approved item(s): {len(applied)} written "
                       f"to the site, {len(failed)} could not be, {len(noted)} "
                       "were advice with nothing to write."),
            expectation=("The changes he approved are on his website within the "
                         "day, and the change log says exactly what went where."),
            confidence=0.8,
            evidence={"site_queue_ids": [p["row"]["id"] for p in doable],
                      "site_issue_ids": [p["issue_id"] for p in doable
                                         if p.get("issue_id")]})

    result["summary"] = (
        f"{len(applied)} change(s) now on your website"
        + (f", {len(failed)} could not be written" if failed else "")
        + (f", {len(waited)} waiting for your website to answer — tried again by "
           "themselves" if waited else "")
        + (f", {len(noted)} were for you to do" if noted else "")
        + (f", {len(refused)} refused" if refused else "")
        + (" (dry run — nothing written)" if dry_run else "")
        + ("" if tests.get("skipped") or dry_run
           else f". Tests green in {tests['took_s']}s first."))
    return result


def _carry_out(agent, plan, skipped_tests) -> bool:
    """
    Write one change, confirm it landed, and record what happened. True if it did.

    Every path through here ends with the queue row marked and a line in the
    change log — including the failures, especially the failures. A change that
    quietly disappears from his queue is a change he will assume went on.
    """
    row = plan["row"]
    page = plan.get("page") or (plan.get("pages") or [""])[0] or "site"
    resource = f"page:{page}"
    if not board.acquire_lock(resource, agent, wait=True, ttl=300):
        return _could_not(plan, f"{page} was held by another agent")
    try:
        if plan["do"] == "head_block":
            r = site_api.put_head_block(plan["page"], plan["block"],
                                        plan["html"], agent=agent)
            wrote = bool(r.get("ok"))
            if not wrote:
                return _fail_or_wait(plan, r, "the write failed")
            seen = _read_back(plan["page"], plan["html"])
            landed = seen is not False
            if wrote and not landed:
                # IT SAID YES AND THE PAGE DISAGREES. Undo at once rather than
                # leaving something half-written that nobody knows the shape of.
                site_api.undo(page=plan["page"], agent=agent)
                return _could_not(
                    plan,
                    "the website accepted the change and then did not show it, "
                    "so it was put straight back. Nothing has been left "
                    "half-done. This is worth a look by hand.")
            _close_ticket(plan.get("issue_id"))
            board.mark_site_change(
                row["id"], "applied",
                (f"written to {plan['page']} and confirmed on the page" if seen
                 else f"{plan['page']}" + UNCHECKED)
                + (" (tests were skipped at your request)" if skipped_tests else ""))
            return True

        if plan["do"] == "remove_page":
            r = site_api.remove_page(plan["page"], go_to=plan.get("go_to"), agent=agent)
            if not r.get("ok"):
                return _fail_or_wait(plan, r, "the page could not be taken off")
            _page_taken_off(plan, r)
            board.mark_site_change(
                row["id"], "applied",
                (r.get("head") or f"{plan['page']} is off your website")
                + (" (tests were skipped at your request)" if skipped_tests else ""))
            return True

        if plan["do"] in ("blog_post", "blog_remove", "blog_repost"):
            from . import blog_backend
            fn = {"blog_post": blog_backend.publish, "blog_remove": blog_backend.remove,
                  "blog_repost": blog_backend.repost}[plan["do"]]
            was_at = ""
            if plan["do"] in ("blog_remove", "blog_repost"):
                try:
                    was_at = (blog_backend._load(plan.get("content_id"))[1] or {}).get(
                        "posted_url") or ""
                except Exception:
                    was_at = ""
            r = fn(plan.get("content_id"), agent=agent)
            if not r.get("ok"):
                return _fail_or_wait(plan, r, "your blog did not take it")
            # The sitemap follows the blog the same day: a new post is in it,
            # a post taken down is out of it. Never a reason to fail the post.
            try:
                from . import sitemap
                sitemap.keep_up(agent,
                                add=[r.get("url")] if plan["do"] != "blog_remove" else [],
                                drop=[was_at] if was_at and was_at != r.get("url") else [])
            except Exception:
                pass
            _close_ticket(plan.get("issue_id"))
            board.mark_site_change(
                row["id"], "applied",
                (r.get("head") or "done")
                + (" (tests were skipped at your request)" if skipped_tests else ""))
            return True

        if plan["do"] == "page":
            # An existing page, rewritten whole. The server keeps the old copy
            # and the change log carries the undo, the same as every write —
            # and the lines that changed, read off the page as it was.
            was = site_api.read_page(plan["page"])
            if _unreachable(was):
                return _fail_or_wait(plan, was, "")
            r = site_api.put_page(plan["page"], plan["html"], agent=agent,
                                  before=(was.get("html") if was.get("ok") else None))
            if not r.get("ok"):
                return _fail_or_wait(plan, r, "the write failed")
            seen = _read_back(plan["page"], _needle(plan["html"]))
            if seen is False:
                site_api.undo(page=plan["page"], agent=agent)
                return _could_not(
                    plan, "the website accepted the page and then did not serve "
                          "it back, so it was put straight back. Nothing has been "
                          "left half-done.")
            _close_ticket(plan.get("issue_id"))
            board.mark_site_change(
                row["id"], "applied",
                (f"{plan['page']} rewritten and confirmed on the page" if seen
                 else plan["page"] + UNCHECKED)
                + (" (tests were skipped at your request)" if skipped_tests else ""))
            return True

        if plan["do"] == "new_page":
            # A PAGE THAT DOES NOT EXIST YET HAS NOTHING TO ROLL BACK TO, so
            # the checks run in the other order: make sure we are not about to
            # write over something first, then write, then read it back.
            already = site_api.read_page(plan["page"])
            if _unreachable(already):
                return _fail_or_wait(plan, already, "")
            if already.get("ok") and (already.get("html") or "").strip():
                return _could_not(
                    plan, f"{plan['page']} already exists on your website. A new "
                          "page must not quietly overwrite one you have — if it "
                          "is meant to replace it, that is a different decision.")
            r = site_api.put_page(plan["page"], plan["html"], agent=agent,
                                  _action="new_page")
            if not r.get("ok"):
                return _fail_or_wait(plan, r, "the write failed")
            # Confirm on something specific to THIS page rather than on the
            # whole body: a needle out of the middle of it, so a server that
            # wrote an empty file or a cached copy of something else fails the
            # check instead of passing it.
            seen = _read_back(plan["page"], _needle(plan["html"]))
            if seen is False:
                site_api.undo(page=plan["page"], agent=agent)
                return _could_not(
                    plan,
                    "the website accepted the page and then did not serve it "
                    "back, so it was undone. Nothing has been left half-done.")
            _mark_published(plan.get("content_id"), plan["page"])
            _close_ticket(plan.get("issue_id"))
            board.mark_site_change(
                row["id"], "applied",
                (f"{plan['page']} is now on your website" if seen
                 else plan["page"] + UNCHECKED)
                + (" (tests were skipped at your request)" if skipped_tests else ""))
            return True

        if plan["do"] == "replace":
            r = site_api.replace_text(plan["find"], plan["to"], agent=agent)
            if not r.get("ok"):
                return _fail_or_wait(plan, r, "the replacement did not finish")
            # Read one page back and check the old text has really gone. The
            # same rule as a block: the server saying yes is not the page
            # agreeing.
            page = (r.get("changed") or [{}])[0].get("page", "")
            if page and _read_back(page, plan["find"]) is True:
                return _could_not(
                    plan, f"'{plan['find']}' is still on {page} after the "
                          "change was written. Nothing further was touched; "
                          "this is worth a look by hand.")
            _close_ticket(plan.get("issue_id"))
            board.mark_site_change(
                row["id"], "applied",
                (r.get("head") or "text replaced")
                + (" (tests were skipped at your request)" if skipped_tests else ""))
            return True

        # a named routine — the allow-list is checked in plan_of, not here
        fn = getattr(site_api, plan["call"])
        r = fn(agent=agent)
        if not r.get("ok"):
            return _fail_or_wait(plan, r, "the routine reported it could not finish")
        _close_ticket(plan.get("issue_id"))
        board.mark_site_change(
            row["id"], "applied",
            (r.get("head") or ALLOWED_CALLS[plan["call"]])
            + (" (tests were skipped at your request)" if skipped_tests else ""))
        return True
    except Exception as e:                                   # pragma: no cover
        text = f"{type(e).__name__}: {e}".lower()
        if isinstance(e, (ConnectionError, TimeoutError, OSError)) or \
                any(w in text for w in UNREACHABLE_WORDS):
            plan["_waiting"] = True
            return _wait_for_site(plan, f"{type(e).__name__}: {e}")
        return _could_not(plan, f"it errored: {e}")
    finally:
        board.release_lock(resource, agent)


def _needle(html, want=60) -> str:
    """
    A distinctive line out of the middle of a page, to read back afterwards.

    Confirming on the whole body is fragile — a server that re-indents, adds a
    trailing newline or gzips differently would report a good write as failed.
    Confirming on nothing at all is worse. One real sentence is the middle
    ground: specific enough that a cached copy of a different page fails it,
    short enough to survive the server touching the whitespace around it.
    """
    text = re.sub(r"<[^>]+>", "\n", html or "")
    for line in text.splitlines():
        line = line.strip()
        if len(line) >= want and "agency:" not in line:
            return line[:want]
    return (html or "").strip()[:want]


def _page_taken_off(plan, r):
    """The page that was put up is marked as taken off, and the post knows it."""
    note = ("taken off your website — a copy is kept" if not r.get("stubbed") else
            "now points to your blog; upload the new connector to remove it outright")
    try:
        with sqlite3.connect(board.DB, timeout=30) as c:
            if plan.get("was"):
                c.execute("UPDATE site_queue SET status='removed', note=? WHERE id=?",
                          (note, int(plan["was"])))
            if plan.get("content_id"):
                got = c.execute("SELECT draft FROM content_queue WHERE id=?",
                                (int(plan["content_id"]),)).fetchone()
                draft = json.loads((got[0] if got else "") or "{}")
                pp = (draft.get("_for_blog") or {}).get("plain_page")
                if pp:
                    pp["down"] = note
                    c.execute("UPDATE content_queue SET draft=? WHERE id=?",
                              (json.dumps(draft, indent=2), int(plan["content_id"])))
    except Exception:
        pass


def _mark_published(content_id, page):
    """Close the loop back to Content, so the brief stops looking unfinished."""
    if not content_id:
        return
    try:
        with sqlite3.connect(board.DB, timeout=30) as c:
            c.execute("UPDATE content_queue SET status='published' WHERE id=?",
                      (int(content_id),))
    except Exception:
        pass


def _close_ticket(issue_id):
    """The ticket that started this is finished. It is on the site now."""
    if not issue_id:
        return
    try:
        with sqlite3.connect(board.DB, timeout=30) as c:
            c.execute("UPDATE site_issues SET status='deployed' WHERE id=?",
                      (int(issue_id),))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _as_text(r):
    if not r.get("ok"):
        return "IT RELEASER — " + (r.get("error") or "nothing to do")
    lines = ["IT RELEASER — " + (r.get("summary") or "")]
    for c in r.get("changes") or []:
        lines.append(f"   #{c['queue_id']:<4} {c['do']:<11} {c['page']}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(
        description="Put what the owner approved onto his website.")
    ap.add_argument("--list", action="store_true", help="what is waiting, write nothing")
    ap.add_argument("--dry-run", action="store_true", help="work it out, write nothing")
    ap.add_argument("--skip-tests", action="store_true",
                    help="go without running the suite first — recorded against "
                         "every change in the batch")
    ap.add_argument("--limit", type=int, default=MAX_PER_PASS)
    ap.add_argument("--when-site-answers", action="store_true",
                    help="wait until the agency's pause on your site is over, then go")
    ap.add_argument("--attempt", type=int, default=1)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.when_site_answers:
        r = when_site_answers(a.attempt)
        print(json.dumps(r, indent=2, default=str))
        return 0
    r = run(limit=a.limit, dry_run=bool(a.dry_run or a.list),
            skip_tests=bool(a.skip_tests))
    print(json.dumps(r, indent=2) if a.json else _as_text(r))
    return deskwork.exit_code(r)


if __name__ == "__main__":
    sys.exit(main())
