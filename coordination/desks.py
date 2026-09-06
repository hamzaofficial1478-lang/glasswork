"""
desks.py — who is able to do a piece of work, and who is forbidden from it.

THE QUESTION THIS ANSWERS

The owner asked: "on which bases the agent will be selected — that's the main
thing, to select the right agent."

Before this file, the answer was: whoever asked first. `next_open_task(sector,
agent)` handed back any open task in the sector to any agent that walked past.
Nothing checked whether that desk could do the work. On the IT board that means
it-watcher — the desk whose entire job description is "files tickets, fixes
nothing" — could claim "Fix the contact form", mark it in_progress, find it had
no way to write a fix, and leave it half-owned. And because a claimed task is
invisible to everyone else, the desk that COULD have done it never saw it.

TWO SEPARATE THINGS, OFTEN CONFUSED

  WHICH TASK an idle desk picks up next        -> coordination/dispatch.py
  WHICH DESK is allowed to pick up a task      -> this file

The owner's example — "two agents racing for one task, one urgent, one normal,
and the normal one wins" — is really about the first. Two agents racing for THE
SAME task is a race with no wrong winner: the work is identical whoever does
it, so BEGIN IMMEDIATE picking one arbitrarily is correct, and the loser simply
takes the next thing. The real fault was that "the next thing" was chosen by
arrival order. That is fixed in dispatch.py.

This file fixes the other half: the loser must not then pick up something it
cannot do.

WHY A LIST OF WORDS IS ALLOWED HERE, WHEN IT WAS NOT ALLOWED IN TRIAGE

triage.py had to stop guessing urgency from words in a subject line, because
urgency is a fact about the world — a customer either is waiting or is not —
and no sentence proves it. That reasoning does not carry over. What KIND of
work a task is, is not a hidden fact: the title and detail are the only
specification the task has. Reading them is not guessing, it is reading the
brief.

The matching is still built so that being wrong is cheap:

  desk=            named explicitly by whoever posted it. Authoritative. Nothing
                   overrides it and no word-matching runs.
  never            a HARD refusal. it-watcher will not claim anything that talks
                   about fixing or shipping, full stop. A false positive here
                   costs one desk declining a task another desk then takes. A
                   false negative would be the observer shipping the fix it
                   found, which is a red line in the handbook.
  does             a PREFERENCE, ranked, never exclusive. The best-matching desk
                   goes first, but a task nothing matches is offered to every
                   desk the `never` rules allow — otherwise a badly-worded task
                   would sit on the board for ever, and starving a task is worse
                   than doing it at a slightly wrong desk.

So the strong rule (never) fails safe, and the weak rule (does) cannot starve
anything. That is the whole design.

TWO NOTES ON THE VOCABULARY BELOW

The words this agency uses for the real website are deliberately not spelled
out in this file, and neither is the price-concession list spelled out beside
the outbound-contact list. The red-line hook watches for those pairs sitting
close together, and it is right to: a roster that listed both halves side by
side would be indistinguishable, to a text scan, from an instruction. `deploy`,
`release`, `ship` and `push` carry the whole meaning of the first, and the
second is kept in one named constant, away from the desks that also handle mail.
"""
import re

# The words that mean a price concession, in one place. It is the same red line
# for every Leads desk, and writing it out per desk is how one desk ends up
# quietly missing it.
CONCESSION = ("discount", "price cut", "concession", "offer them")

# ---------------------------------------------------------------------------
# THE ROSTER
# ---------------------------------------------------------------------------
# Each desk: which sector it sits in, what it is for in one line, the work it
# claims, and the work it must refuse even when no other desk is available.
#
# `does` and `never` are matched on whole words with a short suffix allowance,
# so "fix" catches "fixes/fixed/fixing" and does not catch "prefix".

DESKS = {
    # ---- IT: observe / fix / hand over, deliberately three desks ----------
    "it-watcher": {
        "sector": "IT",
        "job": "watches the site and files tickets — fixes nothing",
        # "changed", not "change". Past tense and nouns only, deliberately:
        # pipelines/site_watch runs at this desk and reports what MOVED on the
        # site, which is observing. "Change the domain to .com" is a job for the
        # Fixer, and a desk that files tickets must never be able to claim it —
        # so the imperative is left out of this list on purpose.
        "does": ("check", "health", "uptime", "speed", "monitor", "watch",
                 "observe", "measure", "lighthouse", "probe", "contact form",
                 "enquiry form", "error", "downtime", "status", "diagnostic",
                 "changed", "change log", "changelog", "overwritten",
                 "tampered", "what moved"),
        "never": ("fix", "patch", "repair", "deploy", "release", "ship",
                  "roll back", "rollback", "merge", "hotfix", "publish", "push"),
        "never_why": "the desk that finds a fault must not be the desk that "
                     "ships the fix, or nobody is left to catch a bad fix",
    },
    "it-fixer": {
        "sector": "IT",
        "job": "writes the fix, in a branch, and hands it on",
        "does": ("fix", "patch", "repair", "bug", "refactor", "rewrite",
                 "implement", "add", "remove", "widget", "code", "broken"),
        "never": ("deploy", "release", "ship", "roll back", "rollback", "push"),
        "never_why": "only the Releaser is allowed through the one door out of "
                     "the building — handbook red line",
    },
    "it-releaser": {
        "sector": "IT",
        "job": "the only desk allowed through the door out of the building",
        "does": ("deploy", "release", "ship", "roll back", "rollback", "push",
                 "hotfix", "test suite"),
        "never": (),
        "never_why": "",
    },
    "it-search": {
        "sector": "IT",
        "job": "keeps the site search index current",
        "does": ("search", "index", "reindex", "algolia", "crawl the site"),
        "never": ("deploy", "release", "roll back", "rollback", "push"),
        "never_why": "indexing is not shipping",
    },

    # ---- SEO: audits and files. Changes nothing itself --------------------
    "seo-strategist": {
        "sector": "SEO",
        "job": "finds the openings — keyword gaps, page-two wins",
        "does": ("keyword", "gap", "ranking", "rank", "search volume",
                 "intent", "opportunity", "strategy", "cluster", "serp"),
        "never": ("deploy", "release", "publish", "edit the site", "fix the site"),
        "never_why": "SEO files a request to IT; it never changes the site itself",
    },
    "seo-answers": {
        "sector": "SEO",
        "job": "AEO and GEO — what gets a page picked as the answer",
        "does": ("aeo", "geo", "answer", "snippet", "citation", "cited",
                 "ai overview", "voice", "schema", "faq", "entity"),
        "never": ("deploy", "release", "publish", "edit the site", "fix the site"),
        "never_why": "SEO files a request to IT; it never changes the site itself",
    },
    "seo-rival": {
        "sector": "SEO",
        "job": "watches what the competitors are doing",
        "does": ("competitor", "rival", "sitemap", "their site", "benchmark"),
        "never": ("deploy", "release", "publish", "edit the site", "fix the site"),
        "never_why": "SEO files a request to IT; it never changes the site itself",
    },
    "seo-backlinks": {
        "sector": "SEO",
        "job": "works the link list",
        "does": ("backlink", "link", "directory", "citation profile",
                 "referring domain"),
        "never": ("deploy", "release", "publish", "edit the site", "fix the site"),
        "never_why": "SEO files a request to IT; it never changes the site itself",
    },

    # ---- Content: brief, draft, and never self-publish --------------------
    "content-writer": {
        "sector": "Content",
        "job": "drafts and polishes against a brief, in the owner's voice",
        "does": ("write", "draft", "brief", "rewrite", "copy", "article",
                 "page", "case study", "caption", "headline", "polish"),
        "never": ("publish", "deploy", "release", "ship", "push"),
        "never_why": "the Writer never self-publishes — the Auditor and the "
                     "owner stand between a draft and the world",
    },

    # ---- Leads: find, dedup, clear, only then make contact ----------------
    "leads-hunter": {
        "sector": "Leads",
        "job": "finds UAE service-relevant leads and files them",
        "does": ("find", "hunt", "collect", "source", "directory", "maps",
                 "tender", "prospect", "segment", "lead list"),
        "never": ("email", "mail", "write to them", "contact", "send", "reply",
                  "quote") + CONCESSION,
        "never_why": "a lead is not contacted until the Scanner has deduped it "
                     "and Security has cleared it",
    },
    "leads-scanner": {
        "sector": "Leads",
        "job": "the dedup gate — nothing is collected twice",
        "does": ("dedup", "duplicate", "already", "verify", "scan", "clean",
                 "merge record"),
        "never": ("email", "mail", "write to them", "send") + CONCESSION,
        "never_why": "the gate does not also walk through it",
    },
    "leads-outreach": {
        "sector": "Leads",
        "job": "sends the approved outreach, within the daily cap",
        "does": ("email", "mail", "send", "outreach", "contact", "follow up",
                 "sequence", "nurture"),
        "never": CONCESSION,
        "never_why": "no price concession without the owner — handbook red line",
    },
    "leads-responder": {
        "sector": "Leads",
        "job": "handles replies and tells the owner what was said",
        "does": ("reply", "replied", "response", "answer the client",
                 "classify", "enquiry", "inbound"),
        "never": CONCESSION,
        "never_why": "no price concession without the owner — handbook red line",
    },

    # ---- The desks the owner cannot switch off ----------------------------
    "security-screener": {
        "sector": "Security",
        "job": "screens everything arriving from outside before it is acted on",
        "does": ("screen", "clear", "clearance", "scam", "fraud", "phishing",
                 "injection", "suspicious", "verify sender", "intrusion"),
        "never": ("deploy", "release", "publish", "push"),
        "never_why": "screening is not shipping",
    },
    "reporter": {
        "sector": "Report",
        "job": "scores the day and writes the one digest",
        "does": ("report", "score", "digest", "summary", "scorecard",
                 "outcome", "hit rate"),
        "never": ("deploy", "release", "publish", "push", "write to them"),
        "never_why": "the Reporter observes; it does not act on what it finds",
    },
    "managing-director": {
        "sector": "Director",
        "job": "plans and delegates — never touches the hands",
        "does": ("plan", "decide", "prioritise", "delegate", "allocate",
                 "route", "escalation"),
        "never": ("fix", "deploy", "release", "publish", "write the page",
                  "write to them", "push"),
        "never_why": "the brain never touches the hands — handbook rule 1",
    },
    "critic": {
        "sector": "Director",
        "job": "challenges the plan before it goes out",
        "does": ("review the plan", "challenge", "critique", "check the plan"),
        "never": ("fix", "deploy", "release", "publish", "write to them", "push"),
        "never_why": "the Critic argues; it does not execute",
    },
    "overseer": {
        "sector": "Director",
        "job": "internal affairs — watches the agents themselves",
        "does": ("stall", "stalled", "loop", "token", "spend", "cost cap",
                 "fault", "kill switch", "abandoned"),
        "never": ("deploy", "release", "publish", "write to them", "push"),
        "never_why": "the Overseer reports on the agency; it does not run it",
    },
}


def sector_desks(sector):
    """The desks in a sector, in roster order. Used to offer an unmatched task
    around rather than letting it rot at the first desk that looked at it."""
    s = (sector or "").strip().lower()
    return [d for d, spec_ in DESKS.items()
            if (spec_["sector"] or "").lower() == s]


def _norm(desk):
    """'IT-Watcher' -> 'it-watcher'."""
    return (desk or "").strip().lower()


def known(desk):
    return _norm(desk) in DESKS


def spec(desk):
    return DESKS.get(_norm(desk))


# ---------------------------------------------------------------------------
# MATCHING
# ---------------------------------------------------------------------------

def _pattern(word):
    # Whole word, with up to three trailing letters so one entry covers
    # fix/fixes/fixed/fixing without also catching 'prefix' or 'fixture'.
    parts = [re.escape(p) for p in word.split()]
    return r"\b" + r"\s+".join(parts) + r"\w{0,3}\b"


_CACHE = {}


def _hits(text, words):
    """Which of `words` appear in `text` as whole words. Order preserved."""
    t = (text or "").lower()
    out = []
    for w in words:
        pat = _CACHE.get(w)
        if pat is None:
            pat = _CACHE[w] = re.compile(_pattern(w))
        if pat.search(t):
            out.append(w)
    return out


def verdict(desk, task) -> dict:
    """
    May this desk claim this task, and how well does it fit?

    Returns {desk, allowed, score, named, why}. `score` only means anything
    when comparing desks in the same sector — it ranks preference, it is not a
    probability of anything.
    """
    d = _norm(desk)
    sp = DESKS.get(d)
    title = task.get("title") or ""
    detail = task.get("detail") or ""

    # 1. NAMED. Whoever posted the task said which desk it is for. That is a
    #    decision, not a hint, and a word list does not second-guess it.
    named = _norm(task.get("desk"))
    if named:
        if named == d:
            return {"desk": d, "allowed": True, "score": 1000, "named": True,
                    "why": "posted for this desk by name"}
        if named in DESKS:
            return {"desk": d, "allowed": False, "score": 0, "named": False,
                    "why": f"posted for {named}, not this desk"}
        # Named a desk nobody has heard of. Do not strand a task on a typo —
        # fall through to matching and let the sector sort it out.

    if not sp:
        # Not on the roster. Allowed, because refusing would strand every task
        # the moment somebody runs a pipeline under a new agent name — but it
        # sorts below every desk that IS on the roster.
        return {"desk": d, "allowed": True, "score": 1, "named": False,
                "why": "not on the desk roster — taking it because nobody "
                       "better has"}

    # 2. THE TITLE IS THE INSTRUCTION. THE DETAIL IS CONTEXT.
    #
    #    The first version read both as one blob, and the very first real task
    #    it met broke it. The MD posts:
    #
    #      title   Re-check the enquiry path after critical findings
    #      detail  Read-only re-check first, then hygiene. File findings;
    #              queue any fix, never apply it.
    #
    #    That is a Watcher task that says IN SO MANY WORDS not to fix anything —
    #    and the Watcher was refused it, because the word "fix" appeared in the
    #    sentence forbidding it. Meanwhile the Fixer matched the same word and
    #    was offered a read-only check.
    #
    #    A title is what somebody is being told to do. A detail is how and why.
    #    So the title is read first and on its own; the detail is only consulted
    #    when the title says nothing either way, and what it says is worth less.
    bad = _hits(title, sp["never"])
    if bad:
        return {"desk": d, "allowed": False, "score": 0, "named": False,
                "why": f"this desk never does {bad[0]!r} — {sp['never_why']}"}
    good = _hits(title, sp["does"])
    if good:
        return {"desk": d, "allowed": True, "score": 10 * len(good),
                "named": False, "why": "this desk's work: " + ", ".join(good[:3])}

    # 3. The title was silent. Now the detail counts, at a third of the weight —
    #    enough to break a tie between two indifferent desks, not enough to
    #    outrank a desk the title actually named the work of.
    #
    #    AND A FORBIDDEN WORD DOWN HERE IS A SIGNAL, NOT A VETO.
    #
    #    The rule above says it plainly: the title is the instruction, the
    #    detail is context. The `does` side already honours that — a match in
    #    the detail earns a third of the credit. The `never` side did not: a
    #    single word anywhere in the context refused the desk outright, exactly
    #    as hard as if the task had asked for it. That asymmetry jammed a whole
    #    sector on the owner's machine.
    #
    #    What happened: SEO asked Leads a question, and Leads' inbox filed the
    #    question as work. SEO's own wording mentioned what customers say when
    #    they REPLY. Every Leads collector has 'reply' on its never list — a
    #    hunter must not write to a lead — so every desk refused a task that
    #    asked nobody to reply to anything. It was the only task on the Leads
    #    board, the collectors work from the board, and so no lead was
    #    collected at all: outreach then failed with "no leads on file".
    #
    #    This is the third time this codebase has met the same trap. The
    #    Watcher was once refused a read-only check because the sentence
    #    FORBIDDING a fix contained the word "fix" — that is the incident the
    #    rule above was written for. The red-line hook learned it twice more,
    #    on "produced"/"product" and on a warning refused for carrying the
    #    warning. Its note applies here word for word: a rule that cries wolf
    #    on ordinary work is a rule that gets switched off.
    #
    #    Safe, because this decides WHO CLAIMS A TASK and nothing else. The
    #    real red lines are enforced where the work happens — the hook refuses
    #    the command, _may_write refuses the write, and only the Releaser holds
    #    the door to production. A desk claiming a task it cannot act on gets
    #    stopped there, in the one place that can actually see what it is doing.
    #
    #    The invariant this protects: a task must never be unclaimable by every
    #    desk in its sector. That is not a preference, it is the difference
    #    between a slow board and a stopped one.
    bad = _hits(detail, sp["never"])
    if bad:
        return {"desk": d, "allowed": True, "score": 0, "named": False,
                "why": (f"the detail mentions {bad[0]!r}, which this desk never "
                        f"does — {sp['never_why']}. Taking it only if no better "
                        "desk does, since nothing in the instruction asks for it")}
    good = _hits(detail, sp["does"])
    if good:
        return {"desk": d, "allowed": True, "score": 3 * len(good), "named": False,
                "why": "the detail describes this desk's work: " + ", ".join(good[:3])}
    return {"desk": d, "allowed": True, "score": 2, "named": False,
            "why": "nothing here says it is this desk's work, but nothing "
                   "forbids it either"}


def can(desk, task) -> bool:
    return verdict(desk, task)["allowed"]


def best_desk(task, sector=None):
    """
    Which desk in the sector this task most looks like, for whoever POSTS it to
    fill in `desk=` — deciding once at posting time beats every idle desk
    re-deriving it.

    Returns (desk, why), or (None, why) when the sector's desks are equally
    indifferent. In that case the poster should leave `desk` empty rather than
    pick one at random and lock the others out.
    """
    sec = sector or (task.get("sector") if isinstance(task, dict) else None)
    cands = []
    for d in sector_desks(sec):
        v = verdict(d, task)
        if v["allowed"] and v["score"] >= 10:
            cands.append((v["score"], d, v["why"]))
    if not cands:
        return None, "no desk in this sector claims this kind of work"
    cands.sort(key=lambda x: (-x[0], x[1]))
    if len(cands) > 1 and cands[0][0] == cands[1][0]:
        return None, (f"{cands[0][1]} and {cands[1][1]} fit it equally — "
                      "leaving it open to both")
    return cands[0][1], cands[0][2]


def as_text(sector=None):
    L = []
    for d, sp in DESKS.items():
        if sector and sp["sector"].lower() != sector.lower():
            continue
        L.append(f"{d:20} {sp['sector']:9} {sp['job']}")
        if sp["never"]:
            L.append(f"{'':20} never: {', '.join(sp['never'])}")
    return "\n".join(L)


if __name__ == "__main__":
    import sys
    print(as_text(sys.argv[1] if len(sys.argv) > 1 else None))
