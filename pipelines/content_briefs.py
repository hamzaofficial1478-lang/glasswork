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

from coordination import board

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
    where = f" in {area}" if area else " across Dubai, Abu Dhabi & Sharjah"
    title = f"{svc.title()}{where}".replace("Uae", "UAE")
    primary = sorted(g["phrases"], key=len)[0]      # shortest = the head term
    secondary = [p for p in sorted(set(g["phrases"])) if p != primary][:10]
    return {
        "rank": rank,
        "title": title,
        "url": "/" + _slug(f"{svc} {area}" if area else svc),
        "type": "service_page",
        "service": svc,
        "area": area or "UAE-wide",
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


def run(agent="content-writer", max_briefs=6, draft_allowance=1):
    with board.agent_run(agent) as run_row:
        out = _brief(agent, max_briefs, draft_allowance)
        if out.get("ok"):
            run_row.note(f"briefed {out.get('stored', 0)} page(s)"
                         + (f", {out['already_briefed']} already done"
                            if out.get("already_briefed") else ""))
        else:
            run_row.note(out.get("error", "nothing to brief"))
        return out


def _brief(agent, max_briefs, draft_allowance):
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

        # 1. claim the handoff SEO left on the board (atomic — no grabbing)
        waiting = [r for r in board.open_requests_for("Content")
                   if "keyword" in (r["subject"] or "").lower()]
        claimed = None
        for r in waiting:
            if board.claim_request(r["id"], agent):
                claimed = r
                break
        if not claimed:
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

        result = {"ok": True, "agent": agent, "claimed_request": claimed["id"],
                  "keywords_read": len(rows), "clusters": len(groups),
                  "briefs": len(briefs), "already_briefed": len(skipped)}

        if not briefs:
            board.reply_request(
                claimed["id"], agent,
                f"Nothing new to brief — all {len(skipped)} page candidates from this gap "
                "list are already in the content queue. Send fresh gaps and I'll pick them up.",
                close=True)
            result.update({"stored": 0, "critic": "skipped",
                           "note": "every candidate page was already briefed"})
            return result

        # 5. the MD's gate: exactly one template brief, the rest wait on SEO
        for i, b in enumerate(briefs):
            b["_ready"] = i < draft_allowance

        # 6. critic
        ok, reasons = critic_check(briefs, len(rows), already)
        if not ok:
            board.reply_request(claimed["id"], agent,
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
        board.reply_request(
            claimed["id"], agent,
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
