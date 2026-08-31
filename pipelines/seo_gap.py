"""
seo_gap.py — the SEO Strategist's first real, read-only task.

One genuine end-to-end cycle, exactly as CLAUDE.md describes for SEO:

  1. BORROW the seo_data shelf (turn-taking via the cabinet lock). Keyword data
     is OUTSIDE data, so the borrow is flagged 'escalate' — in a full run that
     means Security screens it first; here we log the flag and proceed on the
     owner's say-so, because it's read-only public data.
  2. Use the shelf's data KEY if one is configured; otherwise fall back to a
     keyless public source (Google Suggest) so we still pull REAL demand data.
  3. Build a keyword-gap list for the UAE glass & aluminium business: real
     suggestions that aren't already in the cabinet, in-scope, by intent.
  4. The CRITIC checks it before anything is posted — grounded in real fetched
     evidence, deduped, in-scope, non-empty. It only ships if the Critic passes.
  5. Store the gaps in the cabinet and POST them to the board for Content.
  6. RELEASE the shelf and log the checkout.

Read-only throughout: no site change, no outreach, no money spent.

    python3 -m pipelines.seo_gap
"""
import json
import re
import sqlite3
import time
import urllib.parse
import urllib.request

from coordination import cabinet, board, deskwork, inbox, uae
from providers import store


# ---------------------------------------------------------------------------
# ASKING ANOTHER SECTOR, IN THIS SECTOR'S OWN WORDS
# ---------------------------------------------------------------------------
# The trigger and the facts are gathered above, from rows. The WORDING and the
# choice of who to ask go through coordination/ask.py, where this sector's own
# routed brain decides whether it is worth another sector's time, who actually
# owns it, and how to put it. Falls back to the fixed text — and says so in the
# request — when no brain is routed. See ask.py for why the evidence is attached
# by code and never written by the model.
def _ask_send(to, situation, facts, subject, body, from_sector="SEO"):
    try:
        from coordination import ask as _ask
    except Exception:
        return board.post_request(from_sector, to, subject, body)
    return _ask.send(from_sector, to, situation, facts, subject, body).get("id")


# the business's real service + area taxonomy (this is the grounding evidence)
SERVICES = ["glass work", "glass partition", "glass railing", "glass balustrade",
            "shower glass", "glass door", "aluminium windows", "aluminium doors",
            "aluminium work", "aluminium cladding", "wooden doors", "pergola"]
# THE WHOLE COUNTRY, NOT THREE CITIES OF IT.
#
# "there you mentioned only 3 cities of UAE while we are covering the whole UAE"
#
# This was ["Dubai", "Abu Dhabi", "Sharjah"], and it did more damage here than
# anywhere else: it seeded the searches AND decided what counted as a local
# phrase. "aluminium work ajman" came back from Google, found no area it
# recognised, and was filed as general interest rather than as a buying search
# in an emirate he works in. See coordination/uae.py — one list, read by all.
CORE_AREAS = uae.served()
BUY_SIGNALS = ("price", "cost", "installation", "install", "repair", "supplier",
               "company", "near me", "contractor", "fixing", "fit out", "fitting",
               "service", "manufacturer", "quotation", "quote")

# other-country / other-city modifiers Google Suggest returns that are useless to
# a UAE business — the Strategist drops these so the gap list stays local.
NON_UAE = ("sri lanka", "india", "indian", "pakistan", "bangladesh", "nepal",
           "philippines", "kerala", "delhi", "mumbai", "karachi", "lahore",
           # Islamabad reached his real gap list — "aluminium work in
           # islamabad" — because the list had Karachi and Lahore and not the
           # capital. Every city big enough for Google to suggest it.
           "islamabad", "rawalpindi", "peshawar", "faisalabad", "multan",
           "chennai", "bangalore", "hyderabad", "pune", "kolkata", "ahmedabad",
           "colombo", "dhaka", "kathmandu", "manila", "cebu",
           "uk", "u.k", "usa", "u.s", "canada", "australia", "kenya", "nigeria",
           "south africa", "london", "qatar", "saudi", "oman", "bahrain",
           "kuwait", "egypt", "riyadh", "doha", "singapore", "malaysia",
           "jeddah", "muscat", "manama", "kuwait city", "beirut", "amman")

# SOMEBODY ELSE'S BUSINESS IS NOT A KEYWORD.
#
# His real gap list came back with "febtech glass works dubai reviews",
# "febtech glass works dubai photos", "fairoozi aluminium & glass works abu
# dhabi". Every one is a person looking for a named competitor by name. They
# are not looking for a glazier; they are looking for THAT glazier. Ranking
# for them is close to impossible and worthless if you manage it — nobody
# searching "febtech reviews" is going to hire him instead.
#
# The tell is a navigational suffix on a branded phrase. That is a shape, not
# a list of competitor names to maintain, which is what makes it hold up when
# a new company opens next year.
BRAND_LOOKUP = ("reviews", "review", "photos", "contact number", "careers",
                "jobs", "vacancy", "salary", "owner", "address", "timings",
                "opening hours", "location map", "whatsapp number")
_BRAND_RE = re.compile(r"\b(" + "|".join(re.escape(b) for b in BRAND_LOOKUP)
                       + r")\b")
_NON_UAE_RE = re.compile(r"\b(" + "|".join(re.escape(g) for g in NON_UAE) + r")\b")


def _suggest(q):
    """Real, keyless keyword-demand data from Google Suggest (read-only)."""
    url = "https://suggestqueries.google.com/complete/search?" + urllib.parse.urlencode(
        {"client": "firefox", "hl": "en", "gl": "ae", "q": q})
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (glasswork-seo)"})
    with urllib.request.urlopen(req, timeout=12) as r:
        data = json.loads(r.read().decode("utf-8", "replace"))
    return [s.strip().lower() for s in (data[1] if len(data) > 1 else []) if s.strip()]


def _intent(phrase):
    p = phrase.lower()
    if any(b in p for b in BUY_SIGNALS):
        return "buy"
    # Any place in the country, not only the emirate names. A person searching
    # "glass partition mussafah" is in Abu Dhabi and is buying.
    if uae.is_uae(p):
        return "local"
    return "info"


def _area_of(phrase):
    """The emirate this phrase is about, via a district or town if need be."""
    return uae.emirate_in(phrase)


def _rival_names():
    """
    Competitor names the agency already tracks, as lowercase words.

    "fairoozi aluminium & glass works abu dhabi" survived the suffix rule
    above, because it is a branded search with no navigational tell — just a
    company name and a city. There is no shape to catch that on, and keeping a
    hand-written list of rivals here would go stale the week it was written.

    But the SEO sector already maintains a competitors table. Reading it means
    the filter improves every time the Rival Watcher runs, and never says
    anything this agency has not independently recorded.
    """
    try:
        from coordination import board
        import sqlite3
        c = sqlite3.connect(board.DB)
        try:
            rows = c.execute("SELECT name FROM competitors").fetchall()
        finally:
            c.close()
    except Exception:
        return set()
    out = set()
    for (name,) in rows:
        for w in re.split(r"[^a-z0-9]+", (name or "").lower()):
            # Only distinctive words. 'glass', 'dubai' and 'works' appear in
            # half the trade's names and would empty the gap list.
            if len(w) >= 5 and w not in _TRADE_WORDS:
                out.add(w)
    return out


# Words too common in this trade to identify anybody.
_TRADE_WORDS = {"glass", "aluminium", "aluminum", "works", "dubai", "sharjah",
                "technical", "services", "contracting", "trading", "company",
                "industries", "emirates", "national", "general", "decor"}


def rival_name_in(phrase) -> str:
    """
    The firm's name out of a branded lookup, or "" if there is not one.

    "febtech glass works dubai reviews"  ->  "febtech"
    "al reef aluminium contact number"   ->  "al reef"
    "glass partition dubai price"        ->  ""

    THE SEO DESK WAS ALREADY SEEING THESE AND THROWING THEM AWAY. A phrase like
    the first one is dropped from the gap list, correctly — nobody searching
    "febtech reviews" is going to hire him instead. But it is also the single
    best free evidence there is of who he is actually up against, and it went
    in the bin. It now goes to the Rival Watcher as a suggestion for him to
    confirm, which is what stops that step being dead until he types a command.

    What is left after the navigational suffix, the place names and the trade
    words come off is the part that identifies the firm. If nothing is left,
    there was no brand in it.
    """
    p = " " + re.sub(r"[^a-z0-9 &]+", " ", (phrase or "").lower()) + " "
    if not _BRAND_RE.search(p):
        return ""
    for suffix in sorted(BRAND_LOOKUP, key=len, reverse=True):
        p = p.replace(" " + suffix + " ", " ")
    words = [w for w in p.split() if w and w != "&"]
    # Trim from the RIGHT: the brand comes first in every one of these phrases
    # ("febtech glass works dubai"), so the trade and place words to strip are
    # the tail. Stopping at the first word that is neither keeps a two-word
    # brand like "al reef" intact.
    while words:
        last = words[-1]
        if last in _TRADE_WORDS or uae.place_in(last) or last in {"in", "near", "the"}:
            words.pop()
            continue
        break
    name = " ".join(words).strip()
    # One bare word that is itself generic is not a brand, and neither is a
    # phrase so long it is clearly a sentence rather than a company.
    if not name or len(name) < 3 or len(words) > 4:
        return ""
    if all(w in _TRADE_WORDS for w in words):
        return ""
    return name


def _in_scope(phrase):
    p = phrase.lower()
    # A search for a named rival is somebody looking for THAT firm, not for a
    # glazier. Read from the competitors table so it stays current.
    words = set(re.split(r"[^a-z0-9]+", p))
    if words & _rival_names():
        return False
    # drop other-country/other-city noise. Whole-word match only: a substring test
    # would wrongly kill 'sukkari'/'lukman' on "uk" (same trap as the red-line hook).
    if _NON_UAE_RE.search(p):
        return False
    # Somebody hunting a named competitor is not a customer we can win. See
    # BRAND_LOOKUP: it is the navigational suffix that gives it away, not the
    # company name, so this keeps working as new firms open.
    if _BRAND_RE.search(p):
        return False
    return ("glass" in p or "aluminium" in p or "aluminum" in p
            or "pergola" in p or "door" in p or "window" in p or "partition" in p
            or "balustrade" in p or "railing" in p)


def critic_check(gaps, seeds_used):
    """The Critic's gate: is it grounded, deduped, in-scope, non-empty?"""
    reasons = []
    if not seeds_used:
        reasons.append("no real source evidence (fetch returned nothing)")
    if not gaps:
        reasons.append("no new gaps beyond what's already tracked")
    if len({g["phrase"] for g in gaps}) != len(gaps):
        reasons.append("duplicate phrases present")
    off = [g for g in gaps if not _in_scope(g["phrase"])]
    if off:
        reasons.append(f"{len(off)} out-of-scope phrases")
    return (len(reasons) == 0, reasons)


def run(agent="seo-strategist", max_seeds=18, cost_ceiling=0.0):
    """
    Find keyword gaps — and look first at the ones the MD asked about.

    The seed list used to be every service crossed with every core area, every
    day, regardless of what was on the board. A job saying "find the gaps for
    pergolas in Sharjah" produced the same eighteen seeds as a job saying
    nothing, and then closed as though it had answered.
    """
    with board.agent_run(agent) as run_row:
        # SEO's one reader. It sits here rather than in seo_intel because this
        # is the desk that holds the keyword judgement other sectors ask for —
        # Content's "clear these page targets" is answered below from the
        # cabinet, not filed as a ticket and forgotten.
        post = inbox.read_post("SEO", agent, handler=answer_as_seo)
        with deskwork.at_the_desk("SEO", agent, run_row) as job:
            out = _gap(agent, max_seeds, cost_ceiling, job)
            deskwork.settle(job, out, made=out.get("stored"),
                            what="keyword gap")
        out["post"] = post
        note = (f"found {out['stored']} new keyword gaps" if out.get("stored")
                else out.get("error", "no new gaps found"))
        if post.get("read"):
            note = post["head"] + "; " + note
        run_row.note(note)
        return out


# What Content's clearance request is called. Matched on the shape of the ask,
# not on a word in the subject: Content's subject is written by whichever model
# is routed to Content, so the words move. See CLEARANCE_ASK below.
CLEARANCE_ASK = ("clear these page targets", "worth writing for",
                 "volume/difficulty", "volume and difficulty", "rank or reject")


def answer_as_seo(req, body, con):
    """
    The questions SEO can answer off its own cabinet, without a model.

    Returns None for anything else, which files it as work on SEO's board —
    the honest outcome, and still an answer on the thread.

    THE ONE THAT MATTERS: Content clusters the gap list into page candidates
    and asks SEO to clear them before writing. Nothing ever answered, so every
    candidate but one sat at status='blocked_on_seo' for ever and Content wrote
    a single page a day at best. This answers it from the keywords table: how
    many tracked phrases sit behind each candidate, and how many of those carry
    buying intent. That is the evidence SEO actually holds. It does not
    pretend to volume and difficulty figures, which need DataForSEO — it says
    so, and clears on the evidence it has rather than blocking on evidence it
    does not.
    """
    subject = (req.get("subject") or "").lower()
    text = (subject + "\n" + (body or "")).lower()
    if not any(p in text for p in CLEARANCE_ASK):
        return None

    rows = con.execute("SELECT phrase, area, intent FROM keywords").fetchall()
    if not rows:
        return ("No page targets can be cleared: SEO's keyword table is empty, so "
                "there is no evidence here to rank them against. Nothing is "
                "rejected — run the gap pass first and ask again. Anything you "
                "write off the back of no keyword data is a guess, and I would "
                "rather say that than wave it through.")

    candidates = read_candidates(body)
    if not candidates:
        return None                # not a candidate list after all — file it

    tracked = [(r["phrase"] or "").lower() for r in rows]
    cleared, held, lines = 0, 0, []
    for c in candidates:
        verdict, why = judge_candidate(c, tracked)
        if verdict == "CLEAR":
            cleared += 1
        else:
            held += 1
        lines.append(f"  {verdict:6}  {c['title']}  — {why}")

    return ("Checked against the keyword table SEO actually holds "
            f"({len(rows)} phrases tracked) — not volume and difficulty, which "
            "need DataForSEO and it is not connected. I am not going to quote "
            "numbers I cannot stand behind, so this is a check of whether the "
            "evidence you cited is really in the cabinet.\n\n"
            + "\n".join(lines)
            + f"\n\n{cleared} cleared to write, {held} held. Write the cleared "
              "ones now — do not wait for me again on those. For anything held, "
              "send the phrases you think sit behind it and I will look again.")


def cleared_from(reply, titles) -> list:
    """
    Which of these page titles did SEO clear? Content calls this with its own.

    Deliberately not a parser for SEO's reply. Content passes the titles IT
    knows about and this looks for each one on a CLEAR line — so the caller
    never has to understand the sender's format, and a reworded reply cannot
    silently stop working the way Content's "keyword" subject filter did. If
    the reply changes shape, the tests in seo_tests.py fail here, in one place,
    rather than a sector quietly writing nothing for a month.
    """
    if not reply:
        return []
    out = []
    for line in str(reply).splitlines():
        s = line.strip()
        if not s.startswith("CLEAR"):
            continue
        for t in titles:
            if t and t in s and t not in out:
                out.append(t)
    return out


# Content writes each candidate as:
#     - Glass Partition in Dubai  (glass partition dubai, 4 phrases, 2 buying-intent)
# The parenthetical is Content's own evidence. SEO's job is to check that claim
# against its table, not to re-derive it worse: matching the TITLE against
# phrases is what the first version of this did, and "glass partition in dubai"
# is not a substring of "glass partition dubai" — the word "in" — so it held
# every page on the board and Content still wrote nothing. Same starvation, one
# step further along.
_CANDIDATE = re.compile(
    r"^[-•*\s]*(?P<title>.+?)\s*\(\s*(?P<primary>[^,()]+?)\s*,\s*"
    r"(?P<phrases>\d+)\s+phrases?\s*,\s*(?P<buying>\d+)\s+buying-intent\s*\)\s*$")


def read_candidates(body) -> list:
    """The page candidates in Content's message, with the evidence it cited."""
    out = []
    for raw in (body or "").splitlines():
        m = _CANDIDATE.match(raw.strip())
        if not m:
            continue               # prose, headers and blank lines are not pages
        out.append({"title": m.group("title").strip(),
                    "primary": m.group("primary").strip().lower(),
                    "phrases": int(m.group("phrases")),
                    "buying": int(m.group("buying"))})
    return out


# A page wants more than one search behind it, or one search that someone is
# clearly ready to buy from. One informational phrase is not a page — it is a
# section of a page that already exists, and building it competes with our own.
ENOUGH_PHRASES = 2


def judge_candidate(cand, tracked) -> tuple:
    """
    CLEAR or HOLD, and why in one line the writer can act on.

    The check that matters is the first one: is the primary keyword Content
    named actually in SEO's table? If it is not, Content has clustered
    something SEO never found, and clearing it would be SEO rubber-stamping
    evidence it cannot see.
    """
    primary = cand["primary"]
    if primary not in tracked:
        near = [p for p in tracked if primary in p or p in primary]
        if not near:
            return ("HOLD", f"'{primary}' is not in SEO's keyword table at all, and "
                            f"nothing near it is. I cannot clear a page on evidence "
                            f"I do not hold.")
        return ("CLEAR", f"'{primary}' is not tracked verbatim but {len(near)} close "
                         f"phrase(s) are; {cand['buying']} carry buying intent.")
    if cand["buying"] >= 1:
        return ("CLEAR", f"{cand['phrases']} tracked phrase(s), {cand['buying']} of "
                         f"them buying intent. That is a page.")
    if cand["phrases"] >= ENOUGH_PHRASES:
        return ("CLEAR", f"{cand['phrases']} tracked phrases, none with buying intent "
                         f"— informational, so write it to be found rather than to "
                         f"sell, and link it to the service page.")
    return ("HOLD", f"only {cand['phrases']} tracked phrase and no buying intent. "
                    f"Not a no — a 'find me evidence first'. It would compete with "
                    f"our own pages for the same search.")


def seeds_for(job, max_seeds):
    """
    The seed phrases to expand, with anything the job named put first.

    Not *only* what the job named — a pass that finds nothing for "pergola"
    should still come back with the day's other gaps rather than empty-handed.
    The job decides the order and therefore what survives the max_seeds cut.
    """
    base = list(SERVICES)
    for svc in SERVICES:
        for area in CORE_AREAS:
            base.append(f"{svc} {area}")
    if not job:
        return base[:max_seeds]
    words = job.words()
    if not words:
        return base[:max_seeds]
    wanted = [s for s in base if any(w in s.lower() for w in words)]
    # A subject we do not sell yet is still worth one look: expand it as typed
    # rather than silently substituting the catalogue.
    named = [w for w in words
             if not any(w in s.lower() for s in base)][:2]
    rest = [s for s in base if s not in wanted]
    return (named + wanted + rest)[:max_seeds]


def _gap(agent, max_seeds, cost_ceiling, job=None):
    b = cabinet.borrow("seo_data", agent, "SEO",
                       reason="keyword-gap pull for UAE glass & aluminium",
                       data_class="external", cost_ceiling=cost_ceiling, wait=True)
    if not b["ok"]:
        return {"ok": False, "error": "could not borrow the seo_data shelf: " + b.get("error", "")}
    borrow_id, approval = b["borrow_id"], b["approval"]

    tools = store.tools_on_shelf("seo_data")
    if tools:
        source, source_kind = tools[0]["name"], "shelf key"
    else:
        source, source_kind = "Google Suggest (keyless public source)", "keyless fallback"

    con = sqlite3.connect(board.DB)
    try:
        existing = {r[0].strip().lower()
                    for r in con.execute("SELECT phrase FROM keywords").fetchall() if r[0]}

        seeds = seeds_for(job, max_seeds)

        fetched, seeds_used = [], []
        for q in seeds:
            try:
                sug = _suggest(q)
            except Exception:
                sug = []
            if sug:
                seeds_used.append(q)
                fetched.extend(sug)
            time.sleep(0.3)  # be polite to the source

        seen, gaps, rivals_seen = set(), [], {}
        for ph in fetched:
            if ph in existing or ph in seen or not _in_scope(ph):
                # A dropped phrase is not always worthless. A branded lookup is
                # somebody searching for a named firm alongside the work he
                # does, which is the best free evidence there is of who he
                # competes with — and it used to go straight in the bin. See
                # rival_name_in.
                who = rival_name_in(ph)
                if who:
                    rivals_seen.setdefault(who, []).append(ph)
                continue
            seen.add(ph)
            gaps.append({"phrase": ph, "intent": _intent(ph), "area": _area_of(ph)})
        gaps.sort(key=lambda g: (g["intent"] != "buy", g["phrase"]))

        # Suggestions only. Nothing is crawled until he confirms them in the
        # office — see pipelines/competitors.suggest_rival.
        put_forward = []
        for who, why in sorted(rivals_seen.items()):
            try:
                from . import competitors as _rivals
                if _rivals.suggest_rival(who, evidence=why[0]):
                    put_forward.append(who)
            except Exception:
                break

        ok, reasons = critic_check(gaps, seeds_used)
        result = {"ok": True, "agent": agent, "source": source, "source_kind": source_kind,
                  "borrow_approval": approval, "seeds_tried": len(seeds),
                  "seeds_answered": len(seeds_used), "suggestions_seen": len(fetched),
                  "gaps_found": len(gaps),
                  "rivals_put_forward": put_forward}
        if not ok:
            result.update({"posted": False, "critic": "rejected", "critic_reasons": reasons})
            return result

        top = gaps[:40]
        keyword_ids = []
        for g in top:
            cur = con.execute("INSERT INTO keywords(phrase,area,intent) VALUES(?,?,?)",
                              (g["phrase"], g["area"], g["intent"]))
            keyword_ids.append(cur.lastrowid)
        con.commit()

        buy = [g["phrase"] for g in top if g["intent"] == "buy"][:12]
        body = (f"Keyword-gap list for UAE glass & aluminium — {len(top)} new in-scope "
                f"phrases not yet tracked (source: {source}). Top buying-intent gaps:\n- "
                + "\n- ".join(buy or [g["phrase"] for g in top[:12]]))
        # SEO words this itself where it has a brain — coordination/ask.py. The
        # gap list is gathered here, from rows, and attached underneath.
        rid = _ask_send(
            "Content",
            f"{len(top)} service-and-area searches came back that nothing on our "
            "site targets yet. Content decides what gets written, so the list is "
            "theirs to judge — some of these will not be worth a page.",
            [str(t.get("phrase", t)) for t in top][:20],
            "Keyword-gap list ready", body)
        did = board.log_decision(
            "seo-gap",
            reasoning=(f"Pulled {len(fetched)} real suggestions from {source} across "
                       f"{len(seeds_used)} seeds; {len(top)} in-scope gaps weren't tracked yet."),
            expectation="Content briefs the buying-intent gaps into service+area pages.",
            confidence=0.7,
            # THESE phrases, not "any brief written afterwards" — otherwise an
            # unrelated page the owner asked for by hand scores this list.
            evidence={"keyword_ids": keyword_ids,
                      "phrases": [g["phrase"] for g in top]})
        result.update({"posted": True, "critic": "passed", "stored": len(top),
                       "request_id": rid, "decision_id": did, "top_buy": buy})
        return result
    finally:
        con.close()
        cabinet.release("seo_data", agent, borrow_id=borrow_id, item=source, usd_cost=0.0)


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
