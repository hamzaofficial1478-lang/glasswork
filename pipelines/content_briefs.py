"""
content_briefs.py — the Content sector's first real, read-only task.

SEO left 40 keyword gaps and an open request on the board. This is the other
half of that handoff: turn them into PAGE BRIEFS a writer could actually work
from, and stop there. Nothing is drafted, nothing is published, the site is
never touched.

The cycle, exactly as CLAUDE.md describes for Content:

  1. CLAIM the open SEO->Content request. Claim, don't grab — the claim is
     atomic, so if two Content agents run at once only one gets the job.
  2. Read the gaps SEO actually stored. Every brief must trace back to real
     phrases in the cabinet; a brief with no keyword behind it is a hunch.
  3. CLUSTER them into page candidates (service x area). One page per cluster —
     twelve near-identical phrases are one page, not twelve.
  4. ASK SEO, on the board, to clear volume/difficulty. Content does not get to
     decide on its own whether a keyword is worth writing for; that is SEO's
     home turf. Non-blocking, because SEO cannot answer yet (DataForSEO is off)
     and a blocking wait would just stall for nothing.
  5. RESPECT THE MD'S GATE. There is a task in the cabinet that says, in the
     MD's own words: produce the list, draft ONE brief as a template, and gate
     the rest behind SEO clearing volume. So exactly one brief is marked ready
     and the remainder are stored 'blocked_on_seo'. A drafting spree is the
     failure mode here, and it is deliberately not possible.
  6. The CRITIC checks before anything is stored.
  7. REPLY on the request thread and close it. Append, never overwrite.

    python -m pipelines.content_briefs       (python3 on macOS/Linux)
"""
import json
import re
import sqlite3
import time

from coordination import board, deskwork, inbox, uae

# The service taxonomy, same grounding the SEO Strategist used. A phrase has to
# name a service we actually sell, or it cannot become a page.
SERVICES = [
    ("glass partition", ["glass partition", "office partition", "partition"]),
    ("glass railing", ["glass railing", "glass balustrade", "balustrade", "handrail"]),
    ("shower glass", ["shower glass", "shower cabin", "shower enclosure", "shower screen"]),
    ("glass door", ["glass door", "sliding glass", "frameless door"]),
    ("aluminium windows", ["aluminium window", "aluminum window"]),
    ("aluminium doors", ["aluminium door", "aluminum door"]),
    ("aluminium cladding", ["cladding", "aluminium composite", "acp"]),
    ("wooden doors", ["wooden door", "wood door", "solid door"]),
    ("pergola", ["pergola", "gazebo", "shade structure"]),
    ("glass work", ["glass work", "glass fixing", "glass replacement", "glass repair", "glass"]),
    ("aluminium work", ["aluminium work", "aluminum work", "aluminium"]),
]
AREAS = ["Dubai Marina", "Business Bay", "Downtown Dubai", "Jumeirah Village Circle",
         "Al Quoz", "Al Barsha", "Abu Dhabi", "Sharjah", "Dubai"]

# The lists above are the owner's. Any other website's services and areas come
# from its own business profile (coordination/profile.py).
from coordination import profile as _profile  # noqa: E402

if not _profile.is_starter():
    from . import services as _svc
    SERVICES = [(s["label"], list(dict.fromkeys([s["label"]] + list(s["names"]))))
                for s in _svc.SERVICES.values()]
    AREAS = uae.search_areas(9)
WIDE = "UAE-wide" if _profile.is_starter() else "everywhere"

# What every page on this site has to do, because the business's edge is real
# finished work and fast replies — not word count. These are brief requirements,
# not decoration: a page missing them is a page that wins no job.
PROOF_BLOCKS = [
    "3-6 photos of REAL finished jobs — the owner's own, ideally in this area. No stock.",
    "One short line on what the job involved, the material spec and the turnaround.",
    "The enquiry route directly under the photos — the WhatsApp widget and the phone "
    "number, not buried in a footer. Most enquiries here start on WhatsApp.",
    "Google reviews pulled through, with the reviewer's area if it matches this page.",
    "A plain answer to 'how fast can you come and measure?' — response speed wins these jobs.",
]


def _slug(s):
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", s.lower())).strip("-")


def _service_of(phrase):
    p = phrase.lower()
    for name, needles in SERVICES:      # ordered: specific before generic
        if any(n in p for n in needles):
            return name
    return None


def _area_of(phrase, stored_area=""):
    if stored_area:
        return stored_area
    p = phrase.lower()
    for a in AREAS:                     # ordered: districts before the emirate
        if a.lower() in p:
            return a
    return ""


def cluster(rows):
    """
    Group keyword rows into page candidates. One page per service+area, because
    'glass partition Dubai' and 'glass partitions in Dubai price' are the same
    page — splitting them would be two thin pages competing with each other.
    """
    groups = {}
    for phrase, area, intent in rows:
        svc = _service_of(phrase)
        if not svc:
            continue                    # no service named -> not a page we can sell
        ar = _area_of(phrase, area)
        key = (svc, ar)
        g = groups.setdefault(key, {"service": svc, "area": ar, "phrases": [],
                                    "buy": 0, "local": 0, "info": 0})
        g["phrases"].append(phrase)
        g[intent if intent in ("buy", "local", "info") else "info"] += 1
    out = list(groups.values())
    # buying intent first, then the biggest evidence base — that is the order a
    # page should be written in if you want a job this month rather than traffic.
    out.sort(key=lambda g: (-g["buy"], -len(g["phrases"]), g["service"]))
    return out


def make_brief(g, rank):
    """A brief a human could actually write from, grounded in the real phrases."""
    svc, area = g["service"], g["area"]
    where = f" in {area}" if area else f" across {uae.served_phrase('&')}"
    title = f"{svc.title()}{where}".replace("Uae", "UAE").replace("The Uk", "the UK")
    primary = sorted(g["phrases"], key=len)[0]      # shortest = the head term
    secondary = [p for p in sorted(set(g["phrases"])) if p != primary][:10]
    return {
        "rank": rank,
        "title": title,
        "url": "/" + _slug(f"{svc} {area}" if area else svc),
        "type": "service_page",
        "service": svc,
        "area": area or WIDE,
        "primary_keyword": primary,
        "secondary_keywords": secondary,
        "evidence": {
            "phrases_behind_this_page": len(g["phrases"]),
            "buying_intent": g["buy"], "local_intent": g["local"], "info_intent": g["info"],
            "source": "keyword gaps stored by the SEO Strategist",
        },
        "angle": (f"Someone searching '{primary}' wants a price and a date, not an essay. "
                  f"Lead with finished work{where}, then make it one tap to ask."),
        "must_include": list(PROOF_BLOCKS),
        "word_count": "450-700 — short. Anything longer is padding.",
        "internal_links": [f"/{_slug(svc)}", "/projects", "/contact"],
        "do_not": [
            "No stock photography of other people's buildings.",
            "No claims about years, certifications or clients we cannot evidence.",
            "No price or special deal on the page — that needs the owner's sign-off.",
        ],
    }


def existing_pages(con):
    """
    URLs already in the queue. Without this a second run briefs the same pages
    again and you end up with two /glass-partition-dubai entries competing —
    the exact failure the Critic's duplicate-URL rule exists to prevent, just
    spread across runs instead of within one.
    """
    seen = set()
    for brief, title in con.execute("SELECT brief, title FROM content_queue"):
        url = ""
        try:
            url = (json.loads(brief) or {}).get("url", "")
        except Exception:
            pass
        seen.add(url or "title:" + (title or ""))
    return seen


def critic_check(briefs, gap_count, already=()):
    """
    The Critic's gate, before anything is stored. Content's specific failure mode
    is volume for its own sake, so the checks are aimed straight at that.
    """
    reasons = []
    if not gap_count:
        reasons.append("no keyword gaps in the cabinet — nothing to brief from")
    if not briefs:
        reasons.append("no page candidates survived clustering")
    urls = [b["url"] for b in briefs]
    if len(set(urls)) != len(urls):
        reasons.append("two briefs point at the same URL — they would compete")
    thin = [b["title"] for b in briefs if b["evidence"]["phrases_behind_this_page"] < 2]
    if thin:
        reasons.append(f"{len(thin)} brief(s) rest on a single phrase — too thin to justify a page")
    ready = [b for b in briefs if b.get("_ready")]
    if len(ready) > 1:
        reasons.append(f"{len(ready)} briefs marked ready — the MD gated this at one")
    clash = [b["url"] for b in briefs if b["url"] in set(already)]
    if clash:
        reasons.append(f"{len(clash)} page(s) already briefed: {', '.join(clash[:3])}")
    return (len(reasons) == 0, reasons)


def is_briefing_work(job) -> bool:
    """A job for this pass: about briefs, or at least not asking for a post written."""
    return (job.wants("brief", "keyword", "gap", "plan", "topics")
            or not job.wants("write", "publish", "post", "blog", "article",
                             "case study", "rewrite", "draft"))


def run(agent="content-writer", max_briefs=6, draft_allowance=1):
    """
    Brief the next pages — from SEO's handoff, or from a job the MD posted.

    CONTENT USED TO WORK FROM ONE DOOR ONLY: a keyword handoff request from
    SEO. Everything else it refused, on the honest grounds that Content does
    not invent work nobody asked for. The flaw was that a task on the Content
    board IS somebody asking. He posted "write a case study about the Marina
    pergola", it sat open for days, and Content reported — correctly, and
    uselessly — that there was nothing to claim.

    A board task is now the second door. It cannot mint work on its own, and
    the Critic and the no-drafting-spree gate are unchanged; it just stops the
    desk walking past a job addressed to it.
    """
    with board.agent_run(agent) as run_row:
        # The briefs pass and the writing pass both run as content-writer. A
        # job that asks for a post to be WRITTEN — "write 1 post and publish on
        # website" — is the writing pass's, and this pass used to take it, make
        # six briefs out of the words in it, and mark it done with no post
        # written. It now hands such a job back for content_write, and takes
        # only what is about briefing.
        with deskwork.at_the_desk("Content", agent, run_row,
                                  takes=is_briefing_work) as job:
            out = _brief(agent, max_briefs, draft_allowance, job)
            deskwork.settle(job, out, made=out.get("stored"),
                            what="page brief")
        # _brief claims and answers ONE request — the handoff it briefs from.
        # Everything else addressed to Content is read here, so a second and
        # third message in the same day are not left to age. Four of the nine
        # edges that went nowhere pointed at this sector.
        post = inbox.read_post("Content", agent)
        out["post"] = post
        if out.get("ok"):
            note = (f"briefed {out.get('stored', 0)} page(s)"
                    + (f", {out['already_briefed']} already done"
                       if out.get("already_briefed") else ""))
        else:
            note = out.get("error", "nothing to brief")
        if post.get("read"):
            note = post["head"] + "; " + note
        run_row.note(note)
        return out


def _reply(claimed, agent, body, close=False):
    """
    Answer the request this work came from — if it came from one.

    Work off a board task has no request behind it, and there is nothing to
    reply to. Saying so here once beats three `if claimed:` guards buried in
    the flow, where the next person to add a fourth reply forgets one and
    Content silently stops answering SEO.
    """
    if not claimed:
        return None
    return board.reply_request(claimed["id"], agent, body, close=close)


def take_seo_clearance(con, agent) -> list:
    """
    Read SEO's answer and unblock the pages it cleared.

    Content holds a page at status='blocked_on_seo' until SEO says the search
    demand behind it is real. Nothing ever read the answer, so even after SEO
    started replying the rows stayed blocked for ever and Content wrote one
    page a day at most — the clearance loop closed on the thread and nowhere
    that mattered.

    Matching is done by asking seo_gap which of OUR OWN titles it cleared, so
    Content never parses SEO's wording. That is the same mistake as the
    "keyword" subject filter, and it is not being made twice.
    """
    from . import seo_gap

    blocked = [dict(r) for r in con.execute(
        "SELECT id, title FROM content_queue WHERE status='blocked_on_seo'")]
    if not blocked:
        return []
    titles = [b["title"] for b in blocked]

    # Every thread Content opened with SEO that SEO has since replied on.
    with board._conn() as bc:
        threads = bc.execute(
            "SELECT r.id FROM requests r WHERE r.from_sector='Content'"
            "   AND r.to_sector='SEO'"
            "   AND EXISTS (SELECT 1 FROM request_msgs m"
            "                WHERE m.request_id=r.id AND m.author!='Content')"
            " ORDER BY r.created_at DESC LIMIT 20").fetchall()
        replies = []
        for t in threads:
            replies += [m["body"] for m in bc.execute(
                "SELECT body FROM request_msgs WHERE request_id=? AND author!='Content'"
                " ORDER BY id", (t["id"],)).fetchall()]

    cleared = []
    for reply in replies:
        cleared += [t for t in seo_gap.cleared_from(reply, titles) if t not in cleared]
    if not cleared:
        return []

    freed = []
    for b in blocked:
        if b["title"] in cleared:
            con.execute("UPDATE content_queue SET status='briefed' WHERE id=?",
                        (b["id"],))
            freed.append({"id": b["id"], "title": b["title"]})
    con.commit()
    return freed


def _brief(agent, max_briefs, draft_allowance, job=None):
    con = sqlite3.connect(board.DB)
    con.row_factory = sqlite3.Row
    try:
        # 0. Anything stuck in our own name, first.
        #
        # A request an agent claimed and then died on sits at status='claimed'
        # for ever. The inbox below lists status='open' only, so the sector
        # cannot see it — while the Overseer counts it as this sector ignoring
        # the question. That combination ran for nineteen days: "Content has
        # not answered SEO" on the owner's phone every morning, and this
        # function reporting, correctly, that there was nothing to claim.
        #
        # Checking here rather than only in the Overseer's pass means the
        # sector unsticks itself the moment somebody runs it, instead of
        # needing a second command in the right order.
        try:
            board.release_dead_request_claims()
        except Exception:
            pass                       # never let housekeeping stop the work

        # 0b. Pages SEO has since cleared stop being blocked. This runs before
        #     the claim below, so a pass that finds no new handoff can still
        #     free yesterday's held pages — that is real work, and reporting
        #     "nothing to brief" while three cleared pages sit blocked is the
        #     kind of quiet contradiction the owner keeps catching.
        try:
            freed = take_seo_clearance(con, agent)
        except Exception:
            freed = []

        # 1. claim the handoff SEO left on the board (atomic — no grabbing)
        #
        # THE MAGIC WORD. This used to read:
        #
        #     waiting = [r for r in board.open_requests_for("Content")
        #                if "keyword" in (r["subject"] or "").lower()]
        #
        # and it worked, by luck, for exactly as long as nobody routed a model
        # to SEO. The template subject SEO falls back to is "Keyword-gap list
        # ready", which contains the word. Give SEO a brain and the brain
        # writes the subject in its own voice — "Service pages worth building
        # this month" — which does not, so Content walked straight past the one
        # message it exists to answer. Nothing errored. Nothing was logged. The
        # better the SEO model, the more certainly Content heard nothing.
        #
        # Proved both ways on a throwaway cabinet before this was changed:
        #     "Keyword-gap list ready"                  -> seen
        #     "Service pages worth building this month" -> not seen
        #
        # Content now claims anything addressed to it. If it cannot brief from
        # a request it says so on the thread, which is a worse outcome for that
        # one message and a far better one than a filter that quietly drops
        # every message somebody bothered to write well.
        waiting = board.open_requests_for("Content")
        # Brief from a hand-off that is ABOUT pages, first. The inbox answers
        # the others on receipt (inbox.answer_handoff): AEO/GEO fixes are edits
        # to live pages and go to IT's list, guest posts wait on the owner. A
        # briefs pass that claimed one of those could only reply with a brief
        # nobody asked for. Tagged kinds decide, not words in the subject.
        from coordination import inbox as _inbox
        not_briefs = ("aeo_fixes", "guest_posts", "gsc_down")
        first = ("keyword_gaps", "pages")
        waiting = sorted((r for r in waiting
                          if _inbox.handoff_kind(dict(r)) not in not_briefs),
                         key=lambda r: 0 if _inbox.handoff_kind(dict(r)) in first else 1)
        claimed = None
        for r in waiting:
            if board.claim_request(r["id"], agent):
                claimed = r
                break
        if not claimed and not job and freed:
            # A pass that unblocked pages did something, and must not report
            # "nothing to claim" over the top of it. This is the outcome after
            # SEO answers: no new handoff, but yesterday's held pages are now
            # writable, and content_write can pick them up on the next step.
            return {"ok": True, "agent": agent, "stored": 0,
                    "unblocked": freed,
                    "summary": (f"{len(freed)} page(s) SEO has cleared are no longer "
                                f"blocked and are ready to write: "
                                + ", ".join(f["title"] for f in freed))}
        if not claimed and not job:
            # Not a fault: Content is refusing to invent work nobody asked for.
            # Say WHY, and what the cabinet actually holds, so the fix is obvious.
            kw = con.execute("SELECT count(*) FROM keywords").fetchone()[0]
            done = con.execute("SELECT count(*) FROM requests WHERE to_sector='Content'"
                               " AND status!='open'").fetchone()[0]
            why = (f"{kw} keywords are tracked but SEO has not posted a handoff yet"
                   if kw else "the cabinet has no keywords yet")
            if done:
                why += f"; {done} earlier handoff(s) were already answered"
            return {"ok": False,
                    "error": f"Nothing to claim — {why}. "
                             "Content only works from a request on the board, never on its "
                             "own. Run the SEO strategist first:  python -m pipelines.seo_gap "
                             "(use python3 on macOS/Linux), then run this again.",
                    "keywords_in_cabinet": kw, "handoffs_already_answered": done}

        # 2. the real evidence: gaps the SEO Strategist stored
        rows = [(r["phrase"], r["area"] or "", r["intent"] or "info")
                for r in con.execute(
                    "SELECT phrase, area, intent FROM keywords ORDER BY id DESC LIMIT 200")]

        # If the MD named a subject, brief THAT subject. Reading two hundred
        # phrases and returning whatever clustered largest is how "write a case
        # study about the Marina pergola" turned into six shower-glass pages.
        asked_about, narrowed = [], []
        if job:
            asked_about = job.words()
            narrowed = [r for r in rows
                        if any(w in r[0].lower() for w in asked_about)]
            # Only narrow when the job actually matches something we track. A
            # subject with no phrases behind it is a reason to say so, not a
            # reason to brief nothing.
            if narrowed:
                rows = narrowed

        # 3. cluster into page candidates, then drop any page already briefed.
        #    Re-running must not mint a second /glass-partition-dubai to compete
        #    with the first one.
        already = existing_pages(con)
        groups = cluster(rows)
        fresh, skipped = [], []
        for g in groups:
            b = make_brief(g, len(fresh) + 1)
            (skipped if b["url"] in already else fresh).append(b)
            if len(fresh) >= max_briefs:
                break
        briefs = fresh

        result = {"ok": True, "agent": agent,
                  "claimed_request": claimed["id"] if claimed else None,
                  "from_task": job.id if job else None,
                  "asked_about": asked_about,
                  "narrowed_to_subject": bool(narrowed),
                  "keywords_read": len(rows), "clusters": len(groups),
                  "briefs": len(briefs), "already_briefed": len(skipped)}

        if not briefs:
            why = (f"Nothing new to brief — all {len(skipped)} page candidates from this "
                   "gap list are already in the content queue. Send fresh gaps and I'll "
                   "pick them up.")
            # THE EMPTY TABLE FIRST, because it is the cause and the others are
            # symptoms. A full day run reported "no tracked phrase mentions
            # queue, briefed, real, facts" — words out of the MD's instruction,
            # blamed for a table that had nothing in it at all. That sends him
            # looking at the wording of a task when the fault was upstream in
            # SEO, which is where this now points.
            if not rows:
                why = ("Nothing to brief: SEO's keyword table is empty, so there "
                       "is no search demand to build a page on. That is an SEO "
                       "problem, not a Content one — see what 'SEO finds the "
                       "gaps' said in today's run.")
            elif job and asked_about and not narrowed:
                why = ("Nothing to brief on that subject: no tracked phrase mentions "
                       + ", ".join(asked_about[:4])
                       + ". SEO has to find the phrases before Content can build a page "
                         "on them — or say it plainly and I will write it from the job "
                         "itself rather than from search data.")
            _reply(claimed, agent, why, close=True)
            result.update({"stored": 0, "critic": "skipped", "note": why})
            return result

        # 5. the MD's gate: exactly one template brief, the rest wait on SEO
        for i, b in enumerate(briefs):
            b["_ready"] = i < draft_allowance

        # 6. critic
        ok, reasons = critic_check(briefs, len(rows), already)
        if not ok:
            _reply(claimed, agent,
                   "Could not brief this yet — the Critic rejected it: "
                   + "; ".join(reasons))
            result.update({"stored": 0, "critic": "rejected", "critic_reasons": reasons})
            return result

        # 4. SEO's call, not ours: ask whether these are worth writing for.
        #    Non-blocking — SEO cannot answer until DataForSEO is connected, and
        #    a blocking wait would stall the cycle for an answer that isn't coming.
        ask = board.request_verification(
            "Content", "SEO", "Clear these page targets for volume/difficulty",
            "Clustered your gap list into " + str(len(briefs)) + " page candidates:\n- "
            + "\n- ".join(f'{b["title"]}  ({b["primary_keyword"]}, '
                          f'{b["evidence"]["phrases_behind_this_page"]} phrases, '
                          f'{b["evidence"]["buying_intent"]} buying-intent)' for b in briefs)
            + "\n\nOne is briefed as the template. The rest are held until you clear "
              "volume and difficulty — please rank or reject them.",
            blocking=False)

        # 7. store: one ready, the rest visibly gated
        now = int(time.time())
        stored = []
        for b in briefs:
            ready = b.pop("_ready")
            status = "briefed" if ready else "blocked_on_seo"
            cur = con.execute(
                "INSERT INTO content_queue(title,type,brief,status,channel,created_at)"
                " VALUES(?,?,?,?,?,?)",
                (b["title"], b["type"], json.dumps(b, indent=2), status, "website", now))
            stored.append({"id": cur.lastrowid, "title": b["title"], "url": b["url"],
                           "status": status, "primary": b["primary_keyword"],
                           "phrases": b["evidence"]["phrases_behind_this_page"]})
        con.commit()

        ready_one = next((s for s in stored if s["status"] == "briefed"), None)
        _reply(
            claimed, agent,
            f"Briefed. {len(stored)} page candidates from {len(rows)} tracked phrases.\n"
            f"Ready to draft (1, per the MD's gate): {ready_one['title'] if ready_one else '—'}\n"
            f"Held for your volume/difficulty call: {len(stored) - 1}\n"
            f"Asked on request {ask}. Nothing drafted, nothing published.",
            close=True)

        did = board.log_decision(
            "content-briefs",
            reasoning=(f"Clustered {len(rows)} tracked phrases into {len(groups)} service+area "
                       f"pages; briefed {len(stored)}, held {len(stored)-1} behind SEO's "
                       "volume call per the MD's no-drafting-spree gate."),
            expectation="One template service page proves the format before any volume is built.",
            confidence=0.7,
            evidence={"content_queue_ids": [s["id"] for s in stored]})
        result.update({"stored": len(stored), "critic": "passed", "pages": stored,
                       "verification_request": ask, "decision_id": did,
                       "ready_to_draft": ready_one["title"] if ready_one else None})
        return result
    finally:
        con.close()


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
