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

from coordination import cabinet, board
from providers import store

# the business's real service + area taxonomy (this is the grounding evidence)
SERVICES = ["glass work", "glass partition", "glass railing", "glass balustrade",
            "shower glass", "glass door", "aluminium windows", "aluminium doors",
            "aluminium work", "aluminium cladding", "wooden doors", "pergola"]
CORE_AREAS = ["Dubai", "Abu Dhabi", "Sharjah"]
BUY_SIGNALS = ("price", "cost", "installation", "install", "repair", "supplier",
               "company", "near me", "contractor", "fixing", "fit out", "fitting",
               "service", "manufacturer", "quotation", "quote")

# other-country / other-city modifiers Google Suggest returns that are useless to
# a UAE business — the Strategist drops these so the gap list stays local.
NON_UAE = ("sri lanka", "india", "indian", "pakistan", "bangladesh", "nepal",
           "philippines", "kerala", "delhi", "mumbai", "karachi", "lahore",
           "uk", "u.k", "usa", "u.s", "canada", "australia", "kenya", "nigeria",
           "south africa", "london", "qatar", "saudi", "oman", "bahrain",
           "kuwait", "egypt", "riyadh", "doha", "singapore", "malaysia")
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
    if any(a.lower() in p for a in CORE_AREAS) or "uae" in p:
        return "local"
    return "info"


def _area_of(phrase):
    p = phrase.lower()
    for a in CORE_AREAS:
        if a.lower() in p:
            return a
    return ""


def _in_scope(phrase):
    p = phrase.lower()
    # drop other-country/other-city noise. Whole-word match only: a substring test
    # would wrongly kill 'sukkari'/'lukman' on "uk" (same trap as the red-line hook).
    if _NON_UAE_RE.search(p):
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
    with board.agent_run(agent) as run_row:
        out = _gap(agent, max_seeds, cost_ceiling)
        run_row.note(f"found {out['stored']} new keyword gaps" if out.get("stored")
                     else out.get("error", "no new gaps found"))
        return out


def _gap(agent, max_seeds, cost_ceiling):
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

        seeds = list(SERVICES)
        for svc in SERVICES:
            for area in CORE_AREAS:
                seeds.append(f"{svc} {area}")
        seeds = seeds[:max_seeds]

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

        seen, gaps = set(), []
        for ph in fetched:
            if ph in existing or ph in seen or not _in_scope(ph):
                continue
            seen.add(ph)
            gaps.append({"phrase": ph, "intent": _intent(ph), "area": _area_of(ph)})
        gaps.sort(key=lambda g: (g["intent"] != "buy", g["phrase"]))

        ok, reasons = critic_check(gaps, seeds_used)
        result = {"ok": True, "agent": agent, "source": source, "source_kind": source_kind,
                  "borrow_approval": approval, "seeds_tried": len(seeds),
                  "seeds_answered": len(seeds_used), "suggestions_seen": len(fetched),
                  "gaps_found": len(gaps)}
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
        rid = board.post_request("SEO", "Content", "Keyword-gap list ready", body)
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
