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

So a request addressed to a sector gets exactly one of four outcomes here, and
every one of them writes back on the thread:

  ANSWERED   the sector had a handler that knew the answer. Thread closed.
  FILED      no handler, but the ask is real work — it becomes a claimable task
             on that sector's board and the asker is told the task id. The
             thread stays open until the work is done, which is honest: the
             question has been accepted, not answered.
  DECLINED   the sector cannot take it — switched off, board already backed up,
             the desk mid-run with a queue. The asker is told which, so it can
             decide rather than guess. Thread closed; asking again tomorrow is
             a fresh question.
  RETURNED   the handler raised. The claim is given back so the request returns
             to the inbox rather than dying at status='claimed', and the reason
             goes on the thread.

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


def _file_as_task(sector, req, agent):
    """
    Turn a request into claimable work, and say what happened.

    Returns (outcome, reply_text, task_id). Never raises: every refusal the
    board can make is a sentence the asker can act on.
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
        return ("declined",
                f"{sector} cannot take this on yet: {e}. It is not refused on "
                f"merit — the board is backed up. Ask again once it has cleared.",
                None)
    except board.DeskBusy as e:
        return ("declined",
                f"{sector} cannot queue this yet: {e} Ask again on the next run.",
                None)
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
            answer = handler(row, body, con) if handler else None
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
        board.reply_request(req["id"], agent, reply,
                            close=(outcome == "declined"))
        results.append({"request": req["id"], "from": req["from_sector"],
                        "subject": req["subject"], "outcome": outcome,
                        "task": task_id})
        handled += 1

    if mine is None:
        con.close()

    counts = {k: sum(1 for r in results if r["outcome"] == k)
              for k in ("answered", "filed", "declined", "returned")}
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
                "results": [], "answered": 0, "filed": 0, "declined": 0,
                "returned": 0,
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
    if counts["declined"]:
        bits.append(f"declined {counts['declined']}")
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
