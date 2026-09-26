"""
inbox.py — one way for a sector to read its post, used by all seven.

WHY THIS FILE EXISTS

The owner asked why only the Director and Security ever answered anything, and
guessed either that the other sectors were handcuffed, or that the strong
reasoning models were somehow overriding the weaker ones.

Neither. It was simpler and worse than that. Reading the inbox was never a
shared facility — it was hand-written inside two pipelines and nowhere else:

    md_cycle.py        reads Director's inbox, answers, closes
    security_screen.py reads Security's inbox, answers, closes
    content_briefs.py  reads Content's inbox, but only subjects containing
                       the word "keyword"
    IT, SEO, Leads, Report                — nothing. No code opens the inbox.

So nine real messages a day were composed, posted, and read by nobody. Not
dropped, not errored, not rate-limited: written to a room with no door. Model
strength had nothing to do with it. The two sectors that answered are the two
sectors somebody had written the reading code for.

WHAT A SECTOR OWES THE ASKER

An answer, always. Even "I cannot do this" is an answer. The one thing a sector
may never do is take a question and go quiet, because a blocking asker then
waits out its whole timeout and everything behind it stalls.

So a request addressed to a sector gets exactly one of five outcomes here, and
every one of them writes back on the thread:

  ANSWERED   the sector had a handler that knew the answer. Thread closed.
  FILED      no handler, but the ask is real work — it becomes a claimable task
             on that sector's board and the asker is told the task id. The
             thread stays open until the work is done, which is honest: the
             question has been accepted, not answered.
  LATER      the board is backed up, or the desk is mid-run with a queue behind
             it. Nothing is wrong with the ask and it is not refused. The thread
             stays open and the sector looks again on its next pass.
  DECLINED   the owner has switched the sector off. That is a decision rather
             than a delay, so the thread closes and asking tomorrow is a fresh
             question.
  RETURNED   the handler raised. The claim is given back so the request returns
             to the inbox rather than dying at status='claimed', and the reason
             goes on the thread.

LATER and DECLINED were one outcome to begin with, and running it showed why
they cannot be. A 'desk is busy' close makes the question depend on the asker
remembering to repeat itself tomorrow — which is precisely how work disappears
quietly, and precisely what this file exists to stop.

FILED is the important one. It is the floor under every sector: even with no
handler written and no model routed, a request now becomes visible, claimable
work instead of silence. That single fallback is what turns the nine dead edges
live.

WHAT THIS DELIBERATELY DOES NOT DO

It does not call a model. A sector reading its post must work when its brain is
down — that is the whole point of the complaint that started this. Handlers may
call models; the reader itself never does, so the inbox is the last thing to
fail in a sector, not the first.

It does not invent work. A request is somebody asking, which is the same
standard content_briefs already held itself to. Nothing here mints a task off
its own bat.
"""
import re
import sqlite3
import traceback

from . import board

# The seven sectors, spelled the one way the cabinet spells them. Anything else
# is a typo, and a typo used to be invisible: post_request happily wrote a row
# addressed to "MD", nothing reads "MD" (the Managing Director's sector is
# called "Director"), and the asker blocked for its full timeout waiting for a
# reply from a sector that does not exist. pipelines/mail.py did exactly that
# on every outreach mail. See to_sector().
SECTORS = ("Director", "IT", "Content", "SEO", "Leads", "Report", "Security")

# Names people and pipelines actually type, mapped to the real one.
_ALIASES = {
    "md": "Director", "managing director": "Director",
    "managing-director": "Director", "director": "Director", "the md": "Director",
    "it": "IT", "tech": "IT", "engineering": "IT",
    "content": "Content", "content & social": "Content", "social": "Content",
    "seo": "SEO", "seo & ranking": "SEO", "ranking": "SEO",
    "leads": "Leads", "sales": "Leads", "outreach": "Leads",
    "report": "Report", "reporting": "Report", "reports": "Report",
    "security": "Security", "sec": "Security",
    # Not sectors, but they appear as senders and must not be "corrected".
    "owner": "Owner", "overseer": "Overseer",
}

# How many requests one pass will work through. A sector that comes back to a
# hundred-deep inbox should clear the worst of it and run again, not spend the
# whole day's step in here while the work it was actually posted stays open.
MOST_PER_PASS = 12


class UnknownSector(ValueError):
    """A request addressed to a sector that does not exist."""


def to_sector(name, strict=True) -> str:
    """
    Canonical sector name, or raise.

    Called by post_request, so a message can never again be addressed to a
    sector nobody reads. The alternative — silently routing "MD" to Director —
    was considered and rejected: it would fix the symptom and leave the next
    typo ("Marketing") just as invisible. Better to fail at the moment of
    posting, where the traceback names the caller.
    """
    raw = (name or "").strip()
    hit = _ALIASES.get(raw.lower())
    if hit:
        return hit
    for s in SECTORS:
        if s.lower() == raw.lower():
            return s
    if not strict:
        return raw
    raise UnknownSector(
        f"there is no sector called {raw!r}. The seven are: "
        + ", ".join(SECTORS)
        + ". A request addressed to a sector that does not exist is never read "
          "by anyone, and a blocking asker waits out its whole timeout for an "
          "answer that cannot come.")


def _thread(con, request_id) -> str:
    rows = con.execute(
        "SELECT author, body FROM request_msgs WHERE request_id=? ORDER BY id",
        (request_id,)).fetchall()
    return "\n".join((r["body"] or "") for r in rows)


def _unclaim(request_id):
    """Give a claim back so the request returns to the inbox instead of dying."""
    try:
        with board._conn() as c:
            c.execute("UPDATE requests SET status='open', handler=NULL,"
                      " claimed_at=NULL WHERE id=? AND status='claimed'",
                      (request_id,))
    except Exception:
        pass


# A COMPLAINT THAT A SECTOR IS NOT WORKING IS NOT MORE WORK FOR THAT SECTOR.
#
# This is the worst thing this file has done, and it is mine. The Overseer
# chases a sector that has gone quiet — "You have not answered", "Work on your
# board that nobody is picking up" — and the inbox reader dutifully filed each
# chase as a claimable task on that sector's own board. So:
#
#   SEO is behind  ->  Overseer chases SEO  ->  the chase becomes a task on
#   SEO's board    ->  SEO is further behind and the board is fuller  ->  the
#   Overseer files 'work nobody is picking up'  ->  THAT becomes a task too
#
# His SEO board reached eight and every real request after it was refused with
# "SEO already has 8 job(s) on its board that nobody has picked up". Reading his
# log, most of what was jammed in there was this: notices about SEO not doing
# work, filling the space SEO needed to do work. He called it the sectors
# colliding with each other and that is exactly what it was.
#
# A chase is answered on the thread and files nothing. The fault behind it
# already exists, already counts its passes and already escalates to the MD —
# the sector does not need a second copy of it as a job, and there is nothing
# it could DO with one. The work the chase is about is the work already on the
# board.
CHASES = (
    # Both forms. The Overseer addresses the sector directly ("You have not
    # answered") and forwards the fault text to the Director ("SEO has not
    # answered Overseer in 2 days") — same complaint, two wordings, and
    # matching only the first let half of them through the MD's door.
    "you have not answered",
    "has not answered",
    "work on your board that nobody is picking up",
    "you are sitting on a claimed task",
    "you are holding a lock nobody can take",
    # "Seo has 10 faults open at once" — the actionable thing is the ten
    # faults, which already exist and are already routed. A row telling the
    # sector it has them adds nothing it can do.
    "faults open",
    # "3 task(s) on the Content board have been open up to 1 hour and nobody
    # has picked them up" — the Overseer's wording for work nobody claims. It
    # was missing here, so the MD turned the escalation into "From the MD:
    # Escalating: 3 task(s) on the Content board..." — a FOURTH unclaimable
    # task on the board that was being complained about.
    "nobody has picked them up",
    "nobody has picked it up",
)

# NOT chases, and the distinction cost a test to find: a desk that has stopped
# responding or crashed is a REAL job for IT, whatever the Overseer calls it.
# The word "escalating" is only a wrapper the MD puts round something else, so
# it is stripped and the thing inside is judged on its own merits — otherwise
# "Escalating: seo-strategist stopped responding" would be filed away as
# paperwork and the dead desk would stay dead.
_WRAPPERS = ("escalating:", "escalating", "from the md:", "you have not answered:",
             "it cannot fix this on its own:")

# Who is allowed to be chasing. A sector genuinely asking another sector for
# something is never a chase, whatever words it happens to use.
CHASERS = ("overseer", "director")


def is_a_chase(req) -> bool:
    """
    Is this a notice ABOUT the sector rather than work FOR it?

    The wrappers are peeled off first and the inside is judged, because an
    escalation is a delivery mechanism rather than a kind of message. What the
    Overseer escalated decides whether there is anything to do about it.
    """
    frm = (req.get("from_sector") or "").strip().lower()
    subject = (req.get("subject") or "").strip().lower()
    # IT forwarding a fault it cannot repair is a delivery mechanism like any
    # other: judge what is inside. Older builds sent "IT has not answered SEO"
    # this way, every pass, and those may still be sitting in the MD's post.
    forwarded = frm == "it" and subject.startswith("it cannot fix this on its own:")
    if frm not in CHASERS and not forwarded:
        return False
    peeled = True
    while peeled:
        peeled = False
        for w in _WRAPPERS:
            if subject.startswith(w):
                subject, peeled = subject[len(w):].strip(), True
    return any(p in subject for p in CHASES)


def _answer_a_chase(sector, req) -> str:
    """Acknowledge the chase, in terms that say why no task was created."""
    try:
        waiting = board.open_task_count(sector)
    except Exception:
        waiting = 0
    return (
        f"Noted, and deliberately not filed as a job. This is a notice about "
        f"{sector}, not work for it — turning it into a task would put a "
        f"complaint that {sector} is behind onto the same board that is behind, "
        f"and the board is what needs clearing.\n\n"
        f"{sector} has {waiting} job(s) open right now. Those are the work this "
        f"notice is about, and they are what {sector} will do next. The fault "
        f"behind this stays open and keeps counting until they are done.")


# ---------------------------------------------------------------------------
# HAND-OFFS: SEO TELLING A SECTOR SOMETHING IT HAS ALREADY PUT ON FILE
# ---------------------------------------------------------------------------
#
# "Keyword-gap list ready", "Pages worth writing", "Guest-post pieces worth
# writing", "AEO/GEO fixes", "Search Console is not answering". None of these
# is a question. Each is SEO saying "this is on file now" — and each was filed
# on the receiving board as a task no desk was built to claim. So the thread
# stayed open, the Overseer counted it as "Content has not answered SEO in 69
# minutes", IT asked the MD about it every pass, and the owner watched the same
# complaint arrive again and again.
#
# A hand-off is answered on receipt with what actually happens to it next, and
# closed. The kind comes from the request's `about` tag (handoff:<kind>), set
# by the sender, because SEO's subjects are now worded by its own model and a
# word match would miss the day it phrases one differently. Rows from before
# the tag existed are recognised by their old template subjects.
HANDOFF_SUBJECTS = (
    ("keyword_gaps", r"keyword.?gap|keyword list"),
    ("pages", r"pages? worth (writing|building)"),
    ("guest_posts", r"guest.?post"),
    ("aeo_fixes", r"\baeo\b|\bgeo fixes|answer.?(engine|audit)"),
    ("gsc_down", r"search console"),
)


def handoff_kind(req) -> str:
    about = (req.get("about") or "").strip().lower()
    if about.startswith("handoff:"):
        return about.split(":", 1)[1].strip()
    if (req.get("from_sector") or "").strip().lower() != "seo":
        return ""
    subject = (req.get("subject") or "").lower()
    for kind, pattern in HANDOFF_SUBJECTS:
        if re.search(pattern, subject):
            return kind
    return ""


def _content_queue_state(con):
    try:
        briefed = con.execute("SELECT count(*) FROM content_queue WHERE status='briefed'").fetchone()[0]
        review = con.execute("SELECT count(*) FROM content_queue WHERE status='in_review'").fetchone()[0]
        return briefed, review
    except Exception:
        return 0, 0


def _aeo_ticket(line) -> tuple:
    """One AEO/GEO line from SEO, as an IT ticket the Fixer turns into a plan."""
    label, _, rest = line.partition(" — ")
    fix = rest.split("page(s).", 1)[-1].strip() if "page(s)." in rest else rest
    slug = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:40] or "check"
    return slug, (f"[aeo:{slug}] {label.strip()} ({rest.split('.', 1)[0].strip()}). "
                  "Why it matters: answer boxes and AI assistants pick pages that "
                  "have this, and quote the business that does. "
                  f"Do: {fix or 'see the answer audit'} "
                  "Fixed when: the answer audit passes this check on every page.")


def answer_handoff(sector, req, agent, con):
    """The reply to a hand-off, having done what it asks for; None if it is not one."""
    kind = handoff_kind(req)
    body = req.get("_body") or ""
    if kind in ("keyword_gaps", "pages") and sector == "Content":
        briefed, review = _content_queue_state(con)
        return (f"Received, and nothing is lost. The briefs desk reads SEO's whole "
                f"keyword table each time it briefs, not one list at a time, so "
                f"these phrases are in what it works from — the next hand-off SEO "
                f"sends starts that pass. Right now {briefed} page(s) are briefed "
                f"and waiting to be written, and {review} are written and waiting "
                f"for the owner's approval. Closing the thread: there is no "
                f"question in it left to answer.")
    if kind == "guest_posts" and sector == "Content":
        return ("Guest posts are published on other people's sites, and only the "
                "owner can agree one with a publication. Content writes for our "
                "own site, so there is nothing to write until a site has said yes. "
                "The list stays on SEO's link list for when that happens.")
    if kind == "aeo_fixes" and sector in ("Content", "IT"):
        lines = [ln.strip()[2:].strip() for ln in body.splitlines()
                 if ln.strip().startswith("- ")][:12]
        new = old = 0
        for line in lines:
            _slug, text = _aeo_ticket(line)
            _iid, is_new = board.file_site_issue("seo", "low", text, "",
                                                 f"{sector} inbox", con=con)
            new, old = new + bool(is_new), old + (not is_new)
        con.commit()
        what = ("edits to the wording of pages that are already live"
                if sector == "Content" else "changes to the markup of live pages")
        return (f"Taken. These are {what}, and every change to the live site goes "
                f"through IT's fix list and the owner's approval — so each one is "
                f"now a ticket there: {new} new"
                + (f", {old} already on the list" if old else "")
                + ". Nothing changes on the site until he says yes.")
    if kind == "gsc_down" and sector == "IT":
        title = "You need to set up Google Search Console"
        try:
            exists = con.execute("SELECT 1 FROM tasks WHERE title=? AND status IN"
                                 " ('open','in_progress')", (title,)).fetchone()
            if not exists:
                board.try_post_task(
                    "IT", title,
                    "SEO's best free source is Search Console: the searches that "
                    "already reach the site, and the ones it ranks 5th to 20th for. "
                    "It needs a Google service account with access to the property, "
                    "which only you can create. Setup → Integrations → Google "
                    "Search Console: paste the property and the service-account "
                    "key.\n\nSEO's own words:\n" + body[:800],
                    risk="amber", created_by=f"{sector} inbox")
        except Exception:
            pass
        return ("This needs the owner, not IT: Search Console access is a Google "
                "property and a service-account key that only he can create. It is "
                "on his list now (Setup → Integrations → Google Search Console). "
                "There is nothing in the code to change, so this thread is closed; "
                "SEO carries on with the free sources until it is set up.")
    return None


# Hand-offs a desk works FROM, rather than ones answered on receipt. The briefs
# desk claims SEO's gap list to brief from it (pipelines/content_briefs), so
# settling it in a tidy-up before that desk has run would leave it nothing to
# claim. Duplicates of it are still folded — see leftovers.tidy.
WORKED_FROM_BY_A_DESK = {("Content", "keyword_gaps"), ("Content", "pages")}


def settle_paperwork(sector, agent="agency-tidy", limit=200) -> dict:
    """
    Answer only the post that needs no thinking, and leave the rest alone.

    Chases are answered and closed. Hand-offs are answered on receipt the way
    the sector would (answer_handoff). Post for a sector he has switched off is
    declined, as the sector's own inbox would. Real questions are NOT touched:
    those belong to the sector's own pass, where its handler may know the
    answer — filing them from here would turn an answerable question into a
    task. No model is called.
    """
    sector = to_sector(sector)
    off = not board.sector_enabled(sector)
    out = {"noted": 0, "answered": 0, "declined": 0}
    con = sqlite3.connect(board.DB)
    con.row_factory = sqlite3.Row
    try:
        for req in board.open_requests_for(sector, triaged=False)[:max(0, int(limit))]:
            row = dict(req)
            chase = is_a_chase(row)
            kind = handoff_kind(row)
            if kind and (sector, kind) in WORKED_FROM_BY_A_DESK:
                kind = ""
            if not (chase or kind or off):
                continue
            if not board.claim_request(req["id"], agent):
                continue
            row["_body"] = _thread(con, req["id"])
            try:
                if chase:
                    board.reply_request(req["id"], agent,
                                        _answer_a_chase(sector, row), close=True)
                    out["noted"] += 1
                    continue
                answer = answer_handoff(sector, row, agent, con) if kind else None
                if answer is not None:
                    board.reply_request(req["id"], agent, str(answer), close=True)
                    out["answered"] += 1
                elif off:
                    board.reply_request(
                        req["id"], agent,
                        f"{sector} is switched off, so this cannot be worked right "
                        f"now. Nothing has been lost — ask again once the owner "
                        f"turns the sector back on.", close=True)
                    out["declined"] += 1
                else:
                    _unclaim(req["id"])
            except Exception:
                _unclaim(req["id"])
    finally:
        con.close()
    return out


def _file_as_task(sector, req, agent):
    """
    Turn a request into claimable work, and say what happened.

    Returns (outcome, reply_text, task_id). Never raises: every refusal the
    board can make is a sentence the asker can act on.

    Two shapes of no, and they are not the same:

      'declined'  the owner switched the sector off. That is a decision, the
                  thread closes, and asking again tomorrow is a fresh question.
      'later'     the board is backed up or the desk is mid-run with a queue.
                  Nothing is wrong with the ask. The thread stays OPEN so this
                  sector picks it up on its own next pass — closing it would
                  make the question depend on the asker remembering to repeat
                  itself, which is how work quietly disappears.
    """
    title = (req.get("subject") or "").strip() or f"Request from {req.get('from_sector')}"
    detail = (
        f"Asked by {req.get('from_sector')} on the request board "
        f"(request {req.get('id')}).\n\n"
        f"{req.get('_body', '')}\n\n"
        f"— Filed by the {sector} inbox because no handler in this sector "
        f"answers this kind of question directly. Answer it on the thread when "
        f"the work is done: the asker is still waiting.")
    try:
        tid = board.post_task(sector, title[:180], detail,
                              risk="green", created_by=f"{sector} inbox",
                              about=f"requests:{req.get('id')}")
    except board.SectorOff:
        return ("declined",
                f"{sector} is switched off, so this cannot be worked right now. "
                f"Nothing has been lost — ask again once the owner turns the "
                f"sector back on.", None)
    except board.BoardFull as e:
        return ("later",
                f"{sector} cannot take this on yet: {e} It is not refused on "
                f"merit — the board is backed up. Left open; {sector} picks it "
                f"up on its next pass without you having to ask again.", None)
    except board.DeskBusy as e:
        return ("later",
                f"{sector} cannot queue this yet: {e} Left open; the desk takes "
                f"it when it finishes what it is holding.", None)
    except Exception as e:                       # never leave the asker hanging
        return ("declined",
                f"{sector} could not file this as work: {e}", None)
    return ("filed",
            f"Taken. Filed on the {sector} board as task {tid}, and it will be "
            f"claimed by whichever desk in {sector} handles this. This thread "
            f"stays open until the work is done — you have an acknowledgement, "
            f"not an answer.", tid)


def work_the_inbox(sector, agent, handler=None, limit=MOST_PER_PASS,
                   con=None) -> dict:
    """
    Read this sector's post and answer all of it.

    `handler(req, body, con) -> None | str | (str, close)`
        The sector's own knowledge, if it has any for this question. Return
        None to mean "not mine, file it as work". Return text to answer and
        close the thread, or (text, False) to answer and leave it open.

    Anything the handler raises is caught: the claim goes back, the reason goes
    on the thread, and the pass continues to the next request. One bad question
    must not stop a sector reading the rest of its post.
    """
    sector = to_sector(sector)
    mine = con
    if con is None:
        con = sqlite3.connect(board.DB)
        con.row_factory = sqlite3.Row

    # A request an agent claimed and then died on sits at 'claimed' for ever:
    # invisible to the sector that owes the answer, and permanently visible to
    # the Overseer as that sector ignoring it. Unstick before reading, so a
    # sector rescues itself the moment anyone runs it.
    try:
        board.release_dead_request_claims()
    except Exception:
        pass

    handled, results = 0, []
    for req in board.open_requests_for(sector)[:max(0, int(limit))]:
        if not board.claim_request(req["id"], agent):
            continue                              # another desk got there first
        body = _thread(con, req["id"])
        row = dict(req)
        row["_body"] = body
        outcome, reply, task_id = "filed", "", None
        try:
            # A chase is answered and closed here, before the handler and
            # before the filing fallback. It is not this sector's judgement
            # to make and it is not work — see CHASES above.
            if is_a_chase(row):
                board.reply_request(req["id"], agent,
                                    _answer_a_chase(sector, row), close=True)
                results.append({"request": req["id"], "from": req["from_sector"],
                                "subject": req["subject"], "outcome": "noted",
                                "task": None})
                handled += 1
                continue

            answer = handler(row, body, con) if handler else None
            if answer is None:
                answer = answer_handoff(sector, row, agent, con)
            if answer is None:
                outcome, reply, task_id = _file_as_task(sector, row, agent)
            else:
                close = True
                if isinstance(answer, tuple):
                    answer, close = answer[0], bool(answer[1])
                outcome, reply = "answered", str(answer)
                board.reply_request(req["id"], agent, reply, close=close)
                results.append({"request": req["id"], "from": req["from_sector"],
                                "subject": req["subject"], "outcome": outcome,
                                "task": None})
                handled += 1
                continue
        except Exception as e:
            _unclaim(req["id"])
            board.reply_request(
                req["id"], agent,
                f"{sector} tried to answer this and hit an error, so it is back "
                f"in the inbox for the next pass rather than sitting claimed and "
                f"invisible.\n\n{type(e).__name__}: {e}\n\n"
                + traceback.format_exc(limit=3))
            results.append({"request": req["id"], "from": req["from_sector"],
                            "subject": req["subject"], "outcome": "returned",
                            "error": f"{type(e).__name__}: {e}"})
            continue

        # filed / declined both reply; only 'declined' closes the thread, because
        # a filed request is still owed a real answer when the task finishes.
        # 'later' deliberately does NOT close: the sector owes this an answer
        # and will look again itself. See _file_as_task.
        board.reply_request(req["id"], agent, reply,
                            close=(outcome == "declined"))
        if outcome == "later":
            _unclaim(req["id"])
        results.append({"request": req["id"], "from": req["from_sector"],
                        "subject": req["subject"], "outcome": outcome,
                        "task": task_id})
        handled += 1

    if mine is None:
        con.close()

    counts = {k: sum(1 for r in results if r["outcome"] == k)
              for k in ("answered", "filed", "noted", "declined", "later",
                        "returned")}
    return {"ok": True, "sector": sector, "agent": agent,
            "read": handled, "results": results, **counts,
            "head": _head(sector, counts, handled)}


def read_post(sector, agent, handler=None, limit=MOST_PER_PASS, con=None) -> dict:
    """
    work_the_inbox, but it can never take the caller down with it.

    Meant for the top of a pipeline's run(): reading the post is a courtesy the
    sector owes other sectors, and a locked cabinet or a bad row in it must not
    stop the sector doing the work it was actually posted. Returns the same
    shape on failure, with ok=False and the reason, so the caller can still
    report what happened.
    """
    try:
        return work_the_inbox(sector, agent, handler=handler, limit=limit, con=con)
    except Exception as e:
        return {"ok": False, "sector": sector, "agent": agent, "read": 0,
                "results": [], "answered": 0, "filed": 0, "noted": 0,
                "declined": 0, "later": 0, "returned": 0,
                "error": f"{type(e).__name__}: {e}",
                "head": f"could not read {sector}'s post: {e}"}


def _head(sector, counts, handled) -> str:
    if not handled:
        return f"nothing waiting in {sector}'s inbox"
    bits = []
    if counts["answered"]:
        bits.append(f"answered {counts['answered']}")
    if counts["filed"]:
        bits.append(f"filed {counts['filed']} as work")
    if counts.get("noted"):
        bits.append(f"noted {counts['noted']} chase(s) without making work of them")
    if counts["declined"]:
        bits.append(f"declined {counts['declined']}")
    if counts.get("later"):
        bits.append(f"held {counts['later']} for the next pass")
    if counts["returned"]:
        bits.append(f"put {counts['returned']} back after an error")
    return f"{sector} read {handled} request(s): " + ", ".join(bits)


def answer_when_done(task_id, agent, body) -> str:
    """
    Close the loop a FILED request left open.

    A task filed from a request carries about='requests:<id>'. When the work
    finishes, the asker gets the answer on the original thread — otherwise
    'filed' would be a politer kind of silence, which is the thing this whole
    file exists to stop.
    """
    with board._conn() as c:
        row = c.execute("SELECT about FROM tasks WHERE id=?", (task_id,)).fetchone()
    about = (row["about"] if row else "") or ""
    if not about.startswith("requests:"):
        return ""
    rid = about.split(":", 1)[1].strip()
    if not rid:
        return ""
    board.reply_request(rid, agent, body, close=True)
    return rid


def unanswered(sector=None) -> list:
    """
    Every request still waiting, oldest first — for the Overseer and the office.

    Counts 'open' AND 'claimed': a request claimed by a desk that never replied
    is unanswered from the asker's side, whatever the status column says.
    """
    q = ("SELECT r.*, (SELECT count(*) FROM request_msgs m"
         "   WHERE m.request_id=r.id AND m.author != r.from_sector) AS replies"
         "  FROM requests r WHERE r.status IN ('open','claimed')")
    args = []
    if sector:
        q += " AND r.to_sector=?"
        args.append(to_sector(sector))
    q += " ORDER BY r.created_at"
    with board._conn() as c:
        return [dict(r) for r in c.execute(q, args).fetchall() if not r["replies"]]
