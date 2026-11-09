"""
A desk taking one job off the board — claim it, READ it, answer it.

WHY THIS EXISTS

Seventeen pipelines run in a day. Six of them called `next_open_task`. The
other eleven — the Releaser, the site indexer, the site watcher, the lead
hunter, four SEO desks, Security, Content and Report — did their standard pass
every day and never once looked at the board. So the MD posted work, the desks
ran, and the work stayed open. That is the whole of "Director · 24 tasks on the
board" and every sector holding a pile nobody picked up. The board was a
noticeboard nobody read.

The obvious repair — bolt `next_open_task` onto eleven pipelines — recreates a
worse bug, and it is one the owner already caught once: a desk that claims a
job, runs its usual pass, and marks the job done without reading a word of it.
That is not the work getting done, that is the evidence of it not getting done
being deleted. Twenty leads asked for, none collected, task closed green.

So claiming goes through here, and this file makes three things true:

  READING IS SEPARATE FROM CLAIMING. `job.text`, `job.how_many`, `job.mentions`
  hand the pipeline what the owner actually asked for, so a pass can be steered
  by the job rather than merely stamped with it.

  NOTHING IS MARKED DONE BY ACCIDENT. A job is only closed if the pipeline says
  `job.did(...)`. A pipeline that claims and then forgets — or crashes, or
  returns early down some branch nobody thought about — hands the job back to
  the board, with the reason on the row. Silence is never success.

  A JOB THAT CANNOT BE DONE SAYS WHY. `job.cannot("no Places key on the maps
  shelf")` closes it blocked with that sentence, which is what the control room
  then shows instead of "something is holding it back".

WHAT IT DELIBERATELY DOES NOT DO

It does not make claiming soft, and it does not let a desk take work outside
its remit — both of those are decided upstream in dispatch and desks, and this
just calls them. It does not retry. It does not escalate. A job handed back
three times stops being handed back and is closed blocked, because a fourth
identical failure is not new information.
"""

import contextlib
import json
import re

from . import board

# How many times a desk may hand the same job back before the agency admits it
# is not going to happen this way and closes it with the reason. Three passes
# is three days of the day-run, which is long enough to survive a missing key
# being added and short enough that the board still describes this week.
GIVE_UP_AFTER = 3

_HANDED_BACK = "handed back"

# How many jobs a desk will look at and put down before it accepts that none of
# them are its pass. Higher than any sane board, low enough that a bug upstream
# cannot spin here.
MOST_LOOKS = 40


class Job:
    """
    The job this desk claimed, what it says, and the honest record of what came
    of it. Also the object a desk gets when there was nothing to claim — then
    `on_the_board` is False and every reader returns an empty answer, so a
    pipeline never has to branch on None.
    """

    def __init__(self, task, sector, agent):
        self.task = dict(task) if task else None
        self.sector = sector
        self.agent = agent
        self.id = self.task["id"] if self.task else None
        self._outcome = None          # ("done"|"blocked", note)
        self._nothing = ""            # why there was nothing to claim

    # ---- is there a job at all -------------------------------------------

    @property
    def on_the_board(self) -> bool:
        return self.task is not None

    def __bool__(self):
        return self.on_the_board

    @property
    def why_nothing(self) -> str:
        """
        One sentence for a pass that claimed nothing. Not "no open task" — that
        is true and useless when there are four open tasks this desk is
        forbidden. dispatch names which it walked past and why.
        """
        if self.on_the_board:
            return ""
        if not self._nothing:
            try:
                from . import dispatch
                self._nothing = dispatch.why_nothing(self.sector, self.agent)
            except Exception as e:                            # pragma: no cover
                self._nothing = (f"Nothing claimed on the {self.sector} board "
                                 f"and the reason could not be read: {e}")
        return self._nothing

    @property
    def idle(self) -> dict:
        """The result to return when there was nothing to claim. See
        dispatch.nothing_here — not a success, and not a failure either."""
        return {"ok": False, "idle": True, "error": self.why_nothing}

    # ---- reading what was asked for --------------------------------------

    @property
    def title(self) -> str:
        return (self.task or {}).get("title") or ""

    @property
    def detail(self) -> str:
        return (self.task or {}).get("detail") or ""

    @property
    def text(self) -> str:
        """Title and detail as one lowercase string — what the owner asked for,
        in the words he asked for it."""
        return f"{self.title}. {self.detail}".strip().lower()

    def wants(self, *words) -> bool:
        """True if the job names any of these. Whole words, so 'add' does not
        match 'address' and 'link' does not match 'blinking'."""
        t = self.text
        return any(re.search(r"(?<![a-z0-9])" + re.escape(w.lower()) +
                             r"(?![a-z0-9])", t) for w in words if w)

    def how_many(self, default=None):
        """
        The count the job asks for — the 20 in "find 20 leads in Dubai".

        Years and prices are not counts. Anything above 500 is not a day's work
        for one desk, and a bare number attached to a unit we did not ask about
        ("within 24 hours") is not one either, so a number is only taken when
        nothing disqualifies it. No number in the text means the caller's own
        default stands, which is the common case and must stay cheap.
        """
        if not self.on_the_board:
            return default
        # A counted job from the MD says where it stands: "5 done today of 20
        # asked for, so 15 still to go". The first number there is what is
        # already DONE, and reading it as the ask sent the collector after five
        # more instead of fifteen. What is left is the job.
        left = re.search(r"(?<![\w.])(\d{1,3}) still to go", self.text)
        if left and 1 <= int(left.group(1)) <= 500:
            return int(left.group(1))
        for m in re.finditer(r"(?<![\w.%$-])(\d{1,3})(?![\w.%])", self.text):
            n = int(m.group(1))
            if not 1 <= n <= 500:
                continue
            after = self.text[m.end():m.end() + 14]
            if re.match(r"\s*(hour|hr|minute|min|day|week|month|year|"
                        r"am\b|pm\b|%|aed|dhs|dirham)", after):
                continue
            return n
        return default

    def mentions(self, candidates) -> list:
        """
        Which of these the job names, longest first so "abu dhabi" wins over a
        stray "dhabi". Give it your own list — the areas you search, the
        services you sell — because this file has no business knowing them.
        """
        if not self.on_the_board:
            return []
        t = self.text
        out = []
        for c in sorted({str(x) for x in candidates if x},
                        key=lambda s: -len(s)):
            if re.search(r"(?<![a-z0-9])" + re.escape(c.lower()) +
                         r"(?![a-z0-9])", t):
                if not any(c.lower() in seen.lower() for seen in out):
                    out.append(c)
        return out

    # Words that appear in every second task title and tell you nothing about
    # what the job is. Kept short on purpose: over-filtering loses "door".
    _NOISE = frozenset("""
        about after also with from into that this they them then than there
        these those your yours ours have here what when which while will would
        should could does done make made need needs want wants take takes
        please page pages site website work works task job jobs today week
        month year new open close find make write post send give show list
        first next last more most some each every all any our the and for
        """.split())

    def words(self, least=4) -> list:
        """
        The words in the job that carry meaning — what it is actually about.
        Use it to steer a pass towards what was asked for rather than
        pretending the job was never read.
        """
        if not self.on_the_board:
            return []
        out = []
        for w in re.findall(r"[a-z][a-z-]{%d,}" % (least - 1,), self.text):
            if w in self._NOISE or w in out:
                continue
            out.append(w)
        return out

    def first_mention(self, candidates, default=None):
        got = self.mentions(candidates)
        return got[0] if got else default

    # ---- answering it -----------------------------------------------------

    def did(self, note, **extra):
        """
        This pass answered the job. `note` is one plain sentence saying what
        came of it — it is what the owner reads on the closed task, so write it
        for him, not for a log.
        """
        body = {"did": str(note), "by": self.agent}
        body.update(extra)
        self._outcome = ("done", body)
        return self

    def cannot(self, why, **extra):
        """
        This desk cannot do the job, and here is the actual reason. Closes it
        blocked with that sentence, which is what the control room shows.
        Use it for a real obstacle — a missing key, a site that refuses, a
        service we do not sell — not for "found nothing today".
        """
        body = {"blocked": str(why), "by": self.agent}
        body.update(extra)
        self._outcome = ("blocked", body)
        return self

    def answered(self) -> bool:
        return self._outcome is not None

    # ---- settling ---------------------------------------------------------

    def _hand_backs(self) -> int:
        """How many times this job has already been claimed and handed back."""
        try:
            prev = json.loads((self.task or {}).get("result") or "{}")
        except Exception:
            return 0
        notes = prev.get("notes") if isinstance(prev, dict) else None
        if not isinstance(notes, list):
            return 0
        return sum(1 for n in notes
                   if isinstance(n, dict) and _HANDED_BACK in str(n.get("note", "")))

    def _settle(self, failure=""):
        """
        Close the job the way the pass actually went. Never raises — a desk
        must not fail its whole run because the board would not take the
        answer.
        """
        if not self.on_the_board:
            return
        try:
            if self._outcome and not failure:
                status, body = self._outcome
                board.finish_task(self.id, self.agent, body, status=status)
                return

            why = failure or (
                f"{self.agent} claimed this, ran its pass and produced nothing "
                "against it. Nothing was done, so nothing is being marked done.")

            if self._hand_backs() + 1 >= GIVE_UP_AFTER:
                board.finish_task(
                    self.id, self.agent,
                    {"blocked": why + f" That is {GIVE_UP_AFTER} passes with the "
                                      "same result, so it is not being handed "
                                      "round again — it needs a person.",
                     "by": self.agent},
                    status="blocked")
                return

            board.release_claim(self.id, by=self.agent,
                                reason=f"{_HANDED_BACK} — {why}")
        except Exception:                                     # pragma: no cover
            # The pass's own result matters more than the bookkeeping. A claim
            # left hanging is swept by board.release_dead_request_claims and
            # the Overseer, which is exactly what they are for.
            pass


_VOWELS = "aeiou"


def exit_code(result) -> int:
    """
    What a pipeline's `main()` should hand back to the shell.

    THREE OUTCOMES, NOT TWO, and the runner already knows it — day_run._verdict
    reads a step that exits 0 while reporting ok:false as 'idle' rather than
    'ok'. The pipelines were throwing that distinction away one line earlier by
    exiting 1 for anything that was not a success, so a desk with nothing on
    its board came out the same colour as a desk that crashed:

        "its work failed: Leads collects from OpenStreetMap — Nothing open on
         the Leads board."

    Nothing failed. Another Leads desk had the one job. An exit code was all
    the runner had to go on, so this is where the distinction has to survive.

      0   it worked, OR it did nothing wrong and had nothing to do
      1   something is actually broken and somebody should look
    """
    if not isinstance(result, dict):
        return 0 if result else 1
    if result.get("ok"):
        return 0
    return 0 if result.get("idle") else 1


def _plural(word, n):
    """
    Enough English to not embarrass itself on the words this agency uses.
    'link opportunitys' on his screen is the kind of thing that makes the whole
    program look like it was written by a machine that had never read anything.

    Pass "singular|plural" for the ones no rule gets right — "page indexed"
    pluralises at the front, and guessing which word to bend is worse than
    being told.
    """
    if "|" in (word or ""):
        one, _, many = word.partition("|")
        return one if n == 1 else many
    if n == 1 or not word:
        return word
    head, _, last = word.rpartition(" ")
    if last.endswith("y") and len(last) > 1 and last[-2] not in _VOWELS:
        last = last[:-1] + "ies"
    elif last.endswith(("s", "x", "z", "ch", "sh")):
        last = last + "es"
    else:
        last = last + "s"
    return f"{head} {last}".strip()


# Long enough to carry a real stack-less error message, short enough that the
# control room shows a sentence rather than a wall.
REASON_CHARS = 400


def settle(job, out, made=None, what="", **extra):
    """
    Close a job from a pipeline's ordinary result dict.

    Most desks return the same shape — `ok`, `error`, `summary`, sometimes a
    Critic verdict — so most desks can settle in one line rather than each
    inventing its own idea of what counts as done. Three outcomes, and the
    difference between them is the point:

      it could not run          BLOCKED, carrying the real error. This is the
                                one he asked for by name: not "something is
                                holding it back" but the sentence the pass
                                actually produced.
      it ran and made something DONE, saying how many of what.
      it ran and made nothing   DONE, saying plainly that it made nothing. A
                                clean pass that found nothing IS the answer to
                                "go and look", and pretending otherwise by
                                leaving the job open for ever is how the board
                                filled up. But it is never dressed up as a win.

    `made=None` means the desk does not count things — a watcher, the Releaser
    with nothing approved — so the summary stands on its own.
    """
    if not job:
        return
    out = out or {}
    if not out.get("ok", True):
        why = str(out.get("error") or "").strip()
        job.cannot(why[:REASON_CHARS] or
                   "the pass did not complete and did not say why.", **extra)
        return
    if out.get("critic") == "rejected":
        job.cannot("The Critic rejected this pass: "
                   + "; ".join(out.get("critic_reasons") or ["no reason given"]),
                   **extra)
        return
    summary = str(out.get("summary") or "").strip()
    if made is None:
        job.did(summary or f"{job.agent} completed its pass.", **extra)
        return
    n = int(made or 0)
    if n:
        head = f"{n} {_plural(what, n)}" if what else summary
        job.did(f"{head}. {summary}".strip().rstrip(".") + "."
                if summary and what else (head or summary), made=n, **extra)
    else:
        job.did(f"The pass ran and produced no {_plural(what, 0) or 'result'}. "
                + (summary or "There was nothing to act on."), made=0, **extra)


def _claim(sector, agent, takes):
    """
    Claim the next job this desk will actually work, handing back anything
    `takes` does not recognise and asking again.

    ASKING AGAIN IS THE POINT. Claiming once, giving it back and stopping means
    one unrecognised job at the top of the board hides every job behind it, for
    ever — the desk comes back empty every day while its own work sits two rows
    down.

    A rejected job is HELD, not released, until the search is over. Release it
    as you go and the board hands you the very same job on the next ask — it is
    open again and it still sorts highest — so the search never gets past the
    first row. Holding is also the only honest way to do it: while this desk is
    deciding, nobody else should be able to take the job out from under it.
    """
    held, guard = [], 0
    try:
        while True:
            guard += 1
            if guard > MOST_LOOKS:                            # pragma: no cover
                return None, Job(None, sector, agent)
            try:
                task = board.next_open_task(sector, agent, skip=held)
            except Exception:                                 # pragma: no cover
                task = None
            if task is None:
                return None, Job(None, sector, agent)
            job = Job(task, sector, agent)
            if takes is None:
                return task, job
            try:
                mine = bool(takes(job))
            except Exception:                                 # pragma: no cover
                mine = True
            if mine:
                return task, job
            held.append(task["id"])
    finally:
        for tid in held:
            try:
                board.release_claim(
                    tid, by=agent,
                    reason=f"not {agent}'s pass — put straight back for the "
                           "desk that does this")
            except Exception:                                 # pragma: no cover
                pass


@contextlib.contextmanager
def at_the_desk(sector, agent, run_row=None, takes=None):
    """
    Claim this desk's next job, hand it over to be read, and settle it however
    the pass actually went.

        with deskwork.at_the_desk("Leads", agent, run_row) as job:
            wanted = job.how_many(default=20)
            ...
            job.did(f"Collected {n} new firms in {area}.")

    A pass that claims nothing still gets a Job — falsy, with `why_nothing`
    explaining which open work it was not allowed to take. Nothing about the
    pass has to change for that case.

    `takes` is for the case where one desk name covers two passes. Two
    pipelines run as it-watcher: one checks the site's health, one watches for
    changes to its files. Routing cannot tell them apart, so without this the
    change-watcher could claim "check the contact form", diff some fingerprints
    and close it. Give it a predicate on the Job and anything it does not
    recognise goes straight back on the board, unmarked, for the pass that does
    — that is a routing miss, not a failure, so it does not count towards
    giving up on the job.
    """
    task, job = _claim(sector, agent, takes)
    if task is not None and run_row is not None:
        try:
            run_row.task(task["id"])
        except Exception:                                     # pragma: no cover
            pass
    try:
        yield job
    except BaseException as e:
        job._settle(failure=f"{agent} stopped part-way through this: "
                            f"{type(e).__name__}: {e}")
        raise
    job._settle()
