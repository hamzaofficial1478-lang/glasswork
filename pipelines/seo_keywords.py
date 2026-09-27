"""
seo_keywords.py — SEO's keyword plan for one post, made before a word is written.

    python -m pipelines.seo_keywords "glass railing price dubai"     the plan, printed

"my main idea is the seo sector finds the keywords according to the post then it
 gives those keywords to the post room and then post room will add those keywords
 in the post ... we have to play with keywords not just plain posts ... competitors
 ranking analysis on keywords, finding keywords and adding them to the post"

The writer used to get one phrase and a list of "other phrases people search", and
nothing about where they go or who it is up against. So posts came out as decent
prose that no search engine had a reason to rank. This is SEO's half of the job,
done for every post:

  THE KEYWORDS    the main keyword — a longer, local, buying phrase when the brief
                  only had a two-word head term — and ten or so supporting ones,
                  from real evidence: his own Search Console queries, the words his
                  customers used, and what Google and Bing complete people's
                  searches with.
  THE QUESTIONS   what people type as questions about it. Answered in the post, they
                  are how a page gets picked for "People also ask" and by AI answers.
  WHO RANKS       the pages Google shows for the main keyword now (Google's own
                  search API, set up on /setup — free for 100 searches a day), and
                  where his site sits among them.
  WHAT THEY COVER the top pages read: their length, their headings, and the trade
                  terms they all use. A post that covers less than the pages already
                  ranking does not overtake them.

Then check() marks a finished draft against the plan — keyword in the title, the
heading, the first lines, a subheading and the description; each supporting
keyword used; the questions answered; long enough to compete; not stuffed — and
anything missing goes back to the writer by name.

Nothing here writes to his site. It reads public search results and public pages,
gently, and never fetches anything paid.
"""
import argparse
import json
import re
import sqlite3
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from coordination import board

AGENT = "seo-strategist"
SECTOR = "SEO"
SERP_PER_DAY = 90            # Google gives 100 free a day; leave some for him
PAGES_TO_READ = 4            # rival pages read for what they cover
BUDGET = 90                  # seconds the whole plan may take
STALE_AFTER = 14 * 86400     # a plan older than this is made again

# Not rivals to learn writing from: directories, marketplaces, social sites.
NOT_A_PAGE = ("facebook.", "instagram.", "youtube.", "linkedin.", "twitter.", "x.com",
              "tiktok.", "pinterest.", "dubizzle.", "bayut.", "propertyfinder.", "yelp.",
              "justdial.", "yellowpages.", "connect.ae", "2gis.", "wikipedia.", "amazon.",
              "noon.com", "reddit.", "quora.", "google.", "maps.")
ASK = ("how", "what", "which", "why", "when", "where", "can", "is", "are", "does", "do",
       "should")
# The material alone does not make a phrase about the same job: "glass partition"
# is not a keyword for a post on glass railings. The job word does.
MATERIAL = {"glass", "aluminium", "aluminum", "steel", "wooden", "wood", "metal", "upvc"}
SAME_JOB = {"railing": ("railing", "balustrade", "handrail"),
            "balustrade": ("railing", "balustrade", "handrail"),
            "shower": ("shower", "enclosure", "screen"), "pergola": ("pergola", "canopy", "shade"),
            "partition": ("partition",), "door": ("door",), "window": ("window",),
            "mirror": ("mirror",), "facade": ("facade", "curtain", "cladding"),
            "cladding": ("cladding", "facade")}
STOP = set("""a an the and or of for to in on at by with from your our you we is are
be it its this that these those as vs versus near me best top cheap price cost
how what which why when where can does do should much uae dubai sharjah abu dhabi
ajman al ras khaimah fujairah umm quwain company companies services service""".split())
_TOKEN = re.compile(r"[a-z][a-z\-]{2,}")
# Words the post may not use — a price concession is the owner's call — so they
# are never handed to the writer as keywords to work in.
NOT_OURS_TO_SAY = re.compile(r"\b(cheap|cheapest|discount\w*|offers?|deals?|free|best price|"
                             r"lowest price|sale)\b")


def _env(name):
    import os
    v = (os.environ.get(name) or "").strip()
    if v:
        return v
    try:
        from providers import integrations
        return (integrations.read_env().get(name) or "").strip()
    except Exception:
        return ""


def _words(s):
    return [w for w in _TOKEN.findall(str(s or "").lower()) if w not in STOP]


def _norm(s):
    return " ".join(str(s or "").lower().split())


# ---------------------------------------------------------------------------
# WHO RANKS — Google's own search API, counted against a daily allowance
# ---------------------------------------------------------------------------

def _used_today(c):
    try:
        return c.execute("SELECT COALESCE(SUM(n),0) FROM api_usage WHERE provider='google_cse'"
                         " AND day=?", (time.strftime("%Y-%m-%d"),)).fetchone()[0]
    except sqlite3.Error:
        return 0


def _count(n=1, note=""):
    try:
        with board._conn() as c:
            board._migrate(c)
            c.execute("INSERT INTO api_usage(at,day,month,provider,kind,n,usd,note)"
                      " VALUES(?,?,?,'google_cse','search',?,0,?)",
                      (int(time.time()), time.strftime("%Y-%m-%d"), time.strftime("%Y-%m"),
                       n, note[:200]))
    except Exception:
        pass


def google_serp(q, num=10, timeout=15) -> dict:
    """{ok, results: [{position, url, domain, title, snippet}]} or {ok: False, why}."""
    key, cx = _env("GOOGLE_CSE_KEY"), _env("GOOGLE_CSE_ID")
    if not (key and cx):
        return {"ok": False, "why": "no Google search key saved — add one on Setup → "
                                    "Google search to see who ranks"}
    try:
        c = sqlite3.connect(board.DB, timeout=10)
        used = _used_today(c)
        c.close()
    except sqlite3.Error:
        used = 0
    if used >= SERP_PER_DAY:
        return {"ok": False, "why": f"today's {SERP_PER_DAY} free Google searches are used"}
    url = "https://www.googleapis.com/customsearch/v1?" + urllib.parse.urlencode(
        {"key": key, "cx": cx, "q": q, "num": min(10, num), "gl": "ae", "hl": "en"})
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={
                "User-Agent": "glasswork-seo/1.0"}), timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        why = f"Google search said HTTP {e.code}"
        if e.code in (400, 403):
            why += " — check the key and search engine ID on Setup, and that the Custom " \
                   "Search API is turned on"
        return {"ok": False, "why": why}
    except Exception as e:
        return {"ok": False, "why": f"Google search did not answer ({type(e).__name__})"}
    _count(1, q)
    out = []
    for i, it in enumerate(d.get("items") or [], 1):
        link = it.get("link") or ""
        out.append({"position": i, "url": link,
                    "domain": (urllib.parse.urlsplit(link).hostname or "").replace("www.", ""),
                    "title": (it.get("title") or "")[:120],
                    "snippet": (it.get("snippet") or "")[:240]})
    return {"ok": True, "results": out, "source": "Google"}


# ---------------------------------------------------------------------------
# WHAT THE RANKING PAGES COVER
# ---------------------------------------------------------------------------

def _fetch(url):
    from . import fetcher
    r = fetcher.fetch(url, timeout=20, allow_paid=False)
    return r.get("html") or "" if r.get("ok") else ""


def page_facts(html) -> dict:
    """Title, headings, length and wording of one page."""
    h = str(html or "")
    body = re.sub(r"(?is)<(script|style|noscript|nav|footer|header)\b.*?</\1>", " ", h)
    strip = lambda s: " ".join(re.sub(r"<[^>]+>", " ", s).split())
    title = strip((re.search(r"(?is)<title[^>]*>(.*?)</title>", h) or [None, ""])[1])
    h1 = strip((re.search(r"(?is)<h1\b[^>]*>(.*?)</h1>", body) or [None, ""])[1])
    heads = [strip(x) for x in re.findall(r"(?is)<h[23]\b[^>]*>(.*?)</h[23]>", body)]
    text = strip(body)
    words = len(re.findall(r"\b[\w'’-]+\b", text))
    return {"title": title[:140], "h1": h1[:140], "headings": [x[:90] for x in heads if x][:20],
            "words": words, "text": text.lower()[:60000],
            "faq": bool(re.search(r"faq|frequently asked", text, re.I))
                   or sum(1 for x in heads if x.endswith("?")) >= 2}


def _phrases(text, n):
    toks = [w for w in re.findall(r"[a-z][a-z\-]+", text) if len(w) > 2]
    out = {}
    for i in range(len(toks) - n + 1):
        gram = toks[i:i + n]
        if gram[0] in STOP or gram[-1] in STOP:
            continue
        g = " ".join(gram)
        out[g] = out.get(g, 0) + 1
    return out


def shared_terms(pages, known, most=12) -> list:
    """Two- and three-word trade terms that at least two ranking pages use, and we have not listed."""
    seen = {}
    for p in pages:
        grams = set()
        for n in (2, 3):
            grams |= {g for g, k in _phrases(p.get("text") or "", n).items() if k >= 2}
        for g in grams:
            seen[g] = seen.get(g, 0) + 1
    have = " | ".join(_norm(k) for k in known)
    brandish = {(p.get("domain") or "").split(".")[0] for p in pages}
    out = [g for g, k in sorted(seen.items(), key=lambda x: (-x[1], x[0]))
           if k >= 2 and g not in have and not any(b and b in g for b in brandish)
           and not re.search(r"\b(click|cookie|privacy|copyright|reserved|login|cart|menu|"
                             r"whatsapp|call us|contact us|read more|home)\b", g)]
    return out[:most]


# ---------------------------------------------------------------------------
# THE PLAN
# ---------------------------------------------------------------------------

def _evidence(c, head):
    """keyword_intel rows about this subject: real impressions, customers' words."""
    try:
        return [dict(zip(("phrase", "impressions", "position", "score", "in_enquiries"), r))
                for r in c.execute("SELECT phrase, impressions, position, score, in_enquiries"
                                   " FROM keyword_intel WHERE phrase LIKE ?"
                                   " ORDER BY impressions DESC, score DESC LIMIT 40",
                                   (f"%{head}%",))]
    except sqlite3.Error:
        return []


def _suggest_all(primary, areas, deadline, suggest=None):
    from . import seo_intel
    if suggest is None:
        found, _stopped = seo_intel.expand(primary, areas=areas[:3], pause=0.05, deadline=deadline)
        return found
    found = {}
    probes = [primary] + [f"{w} {primary}" for w in ("how much", "what", "best", "which")] + \
             [f"{primary} {a}" for a in areas[:3]] + [f"{primary} price", f"{primary} cost"]
    for p in probes:
        if time.time() > deadline:
            break
        for s in suggest(p) or []:
            found[s] = found.get(s, 0) + 1
    return found


def build(brief, kind="blog", site="", suggest=None, serp=None, fetch=None,
          budget=BUDGET) -> dict:
    """The keyword plan for one post. Never raises; says what it could not do."""
    deadline = time.time() + budget
    notes = []
    was = _norm(brief.get("primary_keyword") or brief.get("title") or "")
    head_words = _words(was) or _words(brief.get("service") or "")
    core = [w for w in head_words if w not in MATERIAL] or head_words or was.split()[:1]
    job = set()
    for w in core:
        job |= set(SAME_JOB.get(w.rstrip("s"), (w.rstrip("s"),)))
    head = core[0] if core else was
    try:
        from coordination import uae
        areas = list(uae.served() or [])
    except Exception:
        areas = ["Dubai", "Abu Dhabi", "Sharjah"]
    if brief.get("area") and brief.get("area") != "UAE-wide":
        areas = [brief["area"]] + [a for a in areas if a != brief["area"]]

    # 1. THE CANDIDATES, WITH THEIR EVIDENCE
    try:
        c = sqlite3.connect(board.DB, timeout=10)
        ev = _evidence(c, head)
        c.close()
    except sqlite3.Error:
        ev = []
    try:
        sugg = _suggest_all(was, areas, deadline, suggest)
    except Exception:
        sugg = {}
        notes.append("Google and Bing autocomplete did not answer, so the plan leans on "
                     "your Search Console and the brief")
    cand = {}

    try:
        from . import seo_gap
        rival = seo_gap.rival_name_in
    except Exception:
        rival = lambda _p: ""

    def add(p, pts, why):
        p = _norm(p)
        if not p or len(p) > 80 or NOT_OURS_TO_SAY.search(p):
            return
        if why != "the brief" and not any(re.search(r"\b" + re.escape(j), p) for j in job):
            return                        # not about this job
        try:
            if rival(p):
                return                    # somebody else's name is not our keyword
        except Exception:
            pass
        c_ = cand.setdefault(p, {"phrase": p, "points": 0, "why": set()})
        c_["points"] += pts
        c_["why"].add(why)

    for r in ev:
        add(r["phrase"], 3 + min(10, (r.get("impressions") or 0) / 20)
            + (4 if r.get("in_enquiries") else 0), "Search Console / your enquiries")
    for p, hits in sugg.items():
        add(p, 1 + min(4, hits), "autocomplete")
    for p in brief.get("secondary_keywords") or []:
        add(p, 2, "the brief")

    # 2. THE MAIN KEYWORD: the brief's, unless it is a bare head term and a
    #    longer local or buying phrase with evidence behind it contains it.
    primary = was
    if len(was.split()) <= 2:
        longer = [x for x in cand.values() if was in x["phrase"] and x["phrase"] != was
                  and 3 <= len(x["phrase"].split()) <= 7
                  and x["phrase"].split()[0] not in ASK          # a question is for the FAQ
                  and (any(a.lower() in x["phrase"] for a in areas)
                       or re.search(r"price|cost|install|supplier|company|fitting|near", x["phrase"]))]
        if longer:
            local = lambda x: 3 if any(a.lower() in x["phrase"] for a in areas) else 0
            buying = lambda x: 2 if re.search(r"price|cost|install|supplier|company|fitting",
                                                x["phrase"]) else 0
            best = max(longer, key=lambda x: (x["points"] + local(x) + buying(x),
                                              -len(x["phrase"])))
            if best["points"] >= 2:
                primary = best["phrase"]
                notes.append(f"main keyword sharpened from “{was}” to “{primary}” — longer, "
                             "local phrases are the ones a small firm can win")
    # 3. THE SUPPORTING KEYWORDS: best evidence first, no near-duplicates.
    secondary, sets = [], [set(_words(primary))]
    questions, asked_already = [], set()
    for x in sorted(cand.values(), key=lambda x: (-x["points"], len(x["phrase"]))):
        p = x["phrase"]
        if p == primary:
            continue
        if p.split()[0] in ASK:
            qkey = frozenset(_words(p)) | frozenset(w for w in p.split() if w in ("much", "long", "best"))
            if len(questions) < 8 and qkey not in asked_already:
                asked_already.add(qkey)
                questions.append(p.rstrip("?") + "?")
            continue
        ws = set(_words(p))
        if any(ws == s for s in sets):
            continue
        sets.append(ws)
        secondary.append(p)
        if len(secondary) >= 12:
            break
    if was != primary and was not in secondary:
        secondary.insert(0, was)

    # 4. WHO RANKS, AND WHAT THEIR PAGES COVER
    serp = serp or google_serp
    fetch = fetch or _fetch
    ranked, pages, ours = [], [], None
    mine = (urllib.parse.urlsplit(site).hostname or "").replace("www.", "") if site else ""
    got = serp(primary) if time.time() < deadline else {"ok": False, "why": "out of time"}
    if got.get("ok"):
        for r in got.get("results") or []:
            if mine and r["domain"].endswith(mine):
                ours = ours or r["position"]
                continue
            ranked.append(r)
        for r in ranked:
            if len(pages) >= PAGES_TO_READ or time.time() > deadline:
                break
            if any(b in r["domain"] for b in NOT_A_PAGE):
                continue
            try:
                html = fetch(r["url"])
            except Exception:
                html = ""
            if html:
                pages.append({**r, **page_facts(html)})
    else:
        notes.append("who ranks: " + str(got.get("why") or "not checked"))

    lengths = [p["words"] for p in pages if p.get("words", 0) > 200]
    floor, ceil = (1000, 2200) if kind == "blog" else (600, 1400)
    median = int(statistics.median(lengths)) if lengths else 0
    target = max(floor, min(ceil, int(median * 1.15))) if median else floor + 200
    # Every heading on these pages says "glass railing"; what tells two topics
    # apart is the rest of it — "types", "cost factors", "installation".
    topic = set(_words(primary)) | set(core) | job
    covered, seen_topics = [], []
    for p in pages:
        for h in p.get("headings") or []:
            hs = set(_words(h)) - topic
            if not hs and "?" not in h:
                continue
            if any(len(hs & t) >= max(1, len(hs) // 2) for t in seen_topics if hs):
                continue
            seen_topics.append(hs)
            covered.append(h)
    related = shared_terms(pages, [primary] + secondary)
    return {
        "primary": primary, "was_primary": was, "secondary": secondary,
        "questions": questions[:6], "related_terms": related,
        "areas": areas[:5], "target_words": target, "rival_median_words": median,
        "headings_to_cover": covered[:12],
        "competitors": [{"position": p["position"], "domain": p["domain"], "url": p["url"],
                         "title": p.get("title") or "", "words": p.get("words") or 0,
                         "faq": bool(p.get("faq")), "headings": (p.get("headings") or [])[:8]}
                        for p in pages],
        "ranking": [{"position": r["position"], "domain": r["domain"], "title": r["title"]}
                    for r in ranked[:10]],
        "our_position": ours, "serp": got.get("source") if got.get("ok") else "",
        "sources": {"search_console_and_enquiries": len(ev), "autocomplete": len(sugg),
                    "ranking_pages_read": len(pages)},
        "notes": notes, "built_at": int(time.time())}


def plan_for(content_id, brief, kind="blog", site="", **kw) -> dict:
    """
    The plan for one queued post, made by SEO and kept on the brief. An existing
    plan is used again unless it is old or was made for another keyword.
    """
    pack = brief.get("seo_pack") or {}
    fresh = pack and time.time() - int(pack.get("built_at") or 0) < STALE_AFTER \
        and pack.get("was_primary") == _norm(brief.get("primary_keyword") or brief.get("title") or "")
    if fresh:
        return pack
    import os
    if os.environ.get("AGENCY_IGNORE_DOTENV") == "1" and os.environ.get("SEO_KEYWORDS_TEST") != "1":
        # The test suite never searches the internet: a plan from the brief alone.
        kw = {"suggest": lambda q: [], "serp": lambda q: {"ok": False, "why": "not in tests"},
              "fetch": lambda u: "", **kw}
    with board.agent_run(AGENT) as run:
        pack = build(brief, kind=kind, site=site, **kw)
        run.note(f"keyword plan for Content: “{pack['primary']}”, {len(pack['secondary'])} "
                 f"supporting keywords, {len(pack['questions'])} questions, "
                 f"{len(pack['competitors'])} ranking page(s) read")
    if content_id:
        try:
            with board._conn() as c:
                board._migrate(c)
                r = c.execute("SELECT brief FROM content_queue WHERE id=?",
                              (int(content_id),)).fetchone()
                b = json.loads((r[0] if r else "") or "{}")
                b["seo_pack"] = pack
                c.execute("UPDATE content_queue SET brief=? WHERE id=?",
                          (json.dumps(b), int(content_id)))
        except Exception:
            pass
    return pack


def for_the_writer(pack, kind="blog") -> list:
    """The plan as the writer reads it: what goes where, and what it is up against."""
    if not pack:
        return []
    L = ["", "SEO'S KEYWORD PLAN — the SEO desk researched this. Build the piece around it:",
         f"  MAIN KEYWORD (exact phrase): “{pack['primary']}”",
         "    - in the title, as near the start as reads naturally (title under 60 characters)",
         "    - in the <h1>",
         "    - in the first sentence or two, within the first 100 words",
         "    - in at least one <h2>",
         "    - in the meta description",
         f"    - {3 if kind == 'blog' else 2} to {6 if kind == 'blog' else 4} times in all, "
         "worded naturally — never forced, never in a list of keywords"]
    if pack.get("secondary"):
        L.append("  SUPPORTING KEYWORDS — use every one at least once, several in <h2>/<h3> "
                 "headings, in sentences a person would say:")
        L += [f"    - {k}" for k in pack["secondary"]]
    if pack.get("questions"):
        L.append("  QUESTIONS PEOPLE SEARCH — answer each in a short FAQ section, the question "
                 "as an <h3>, a straight two-to-four sentence answer under it:")
        L += [f"    - {q}" for q in pack["questions"]]
    if pack.get("related_terms"):
        L.append("  TERMS THE RANKING PAGES ALL USE — work in the ones that fit, so the post "
                 "covers the subject as fully as they do: " + ", ".join(pack["related_terms"]))
    if pack.get("areas"):
        L.append("  PLACES TO NAME where it fits: " + ", ".join(pack["areas"]))
    comp = pack.get("competitors") or []
    if comp:
        L.append(f"  WHO RANKS NOW for “{pack['primary']}” (Google):")
        for p in comp:
            L.append(f"    #{p['position']} {p['domain']} — {p['words']} words — “{p['title'][:70]}”")
        if pack.get("headings_to_cover"):
            L.append("  WHAT THEIR PAGES COVER — cover all of it, better and more specifically:")
            L += [f"    - {h}" for h in pack["headings_to_cover"]]
        L.append("  BEAT THEM ON: real trade detail, a clear comparison table, straight answers, "
                 "UAE conditions — the things a generic page lacks.")
    if re.search(r"price|cost|rate|quote", " ".join([pack["primary"]] + list(pack.get("secondary") or []))):
        L.append("  PRICE KEYWORDS: use the words, and explain what the cost depends on and how "
                 "a quote is worked out — never a figure, an AED amount, a range or a rate. "
                 "A page with one is thrown out.")
    L.append(f"  LENGTH: about {pack['target_words']} words"
             + (f" — the pages ranking now run to about {pack['rival_median_words']}"
                if pack.get("rival_median_words") else ""))
    return L


# ---------------------------------------------------------------------------
# THE CHECK
# ---------------------------------------------------------------------------

def _has(text, phrase):
    t, p = _norm(text), _norm(phrase)
    if not p:
        return False
    if p in t:
        return True
    ws = _words(p)
    return bool(ws) and all(re.search(r"\b" + re.escape(w.rstrip("s")), t) for w in ws)


def check(page, pack, kind="blog") -> dict:
    """{score, items, secondary_used, secondary_missing, problems} for one draft."""
    if not pack or not pack.get("primary"):
        return {"score": None, "items": [], "problems": [], "secondary_used": [],
                "secondary_missing": []}
    html = str(page.get("html") or "")
    text = " ".join(re.sub(r"<[^>]+>", " ", html).split())
    first = " ".join(text.split()[:110])
    h1 = " ".join(re.sub(r"<[^>]+>", " ", " ".join(re.findall(r"(?is)<h1\b[^>]*>(.*?)</h1>", html)
                                                        + [page.get("h1") or ""])).split())
    h2s = [re.sub(r"<[^>]+>", " ", x) for x in re.findall(r"(?is)<h[23]\b[^>]*>(.*?)</h[23]>", html)]
    p = pack["primary"]
    words = len(re.findall(r"\b[\w'’-]+\b", text))
    uses = len(re.findall(re.escape(_norm(p)), _norm(text)))
    density = round(100.0 * uses / max(1, words), 1)       # uses per hundred words
    items = [("main keyword in the title", _has(page.get("title") or "", p)),
             ("main keyword in the heading", _has(h1, p)),
             ("main keyword in the first lines", _has(first, p)),
             ("main keyword in a subheading", any(_has(h, p) for h in h2s)),
             ("main keyword in the description", _has(page.get("meta") or "", p)),
             ("long enough to compete", words >= int(pack.get("target_words", 0) * 0.85)),
             ("not stuffed", density <= 2.5)]
    used = [k for k in pack.get("secondary") or [] if _has(text, k)]
    missing = [k for k in pack.get("secondary") or [] if k not in used]
    qs = pack.get("questions") or []
    def asked(q, h):
        share = len(set(_words(q)) & set(_words(h)))
        return ("?" in h and share >= 1) or (share and share == len(set(_words(q))))
    answered = [q for q in qs if any(asked(q, h) for h in h2s)]
    if qs:
        items.append(("the questions people search are answered", len(answered) >= min(3, len(qs))))
    need = max(1, int(len(pack.get("secondary") or []) * 0.8))
    items.append(("supporting keywords used", len(used) >= need or not pack.get("secondary")))
    ok = sum(1 for _i, v in items if v)
    problems = []
    for name, v in items:
        if v:
            continue
        if name == "long enough to compete":
            problems.append(f"it is {words} words; the plan asks for about "
                            f"{pack['target_words']} to compete with the pages ranking now — "
                            "add real substance, not padding")
        elif name == "not stuffed":
            problems.append(f"the main keyword appears {uses} times in {words} words — too "
                            "many; use natural variations instead")
        elif name.startswith("main keyword"):
            problems.append(f"put the main keyword “{p}” {name.replace('main keyword ', '')}")
        elif name.startswith("the questions"):
            problems.append("answer these questions in the FAQ, each as an <h3>: "
                            + "; ".join(q for q in qs if q not in answered))
        elif name == "supporting keywords used":
            problems.append("work these supporting keywords in naturally: " + ", ".join(missing))
    return {"score": round(100 * ok / len(items)), "items": [{"what": n, "ok": v} for n, v in items],
            "words": words, "density": density, "secondary_used": used,
            "secondary_missing": missing, "questions_answered": len(answered),
            "problems": problems}


def summary(pack, report) -> dict:
    """What the Posts room shows about a post's keywords."""
    if not pack:
        return {}
    return {"primary": pack.get("primary"), "was_primary": pack.get("was_primary"),
            "secondary": pack.get("secondary") or [],
            "used": (report or {}).get("secondary_used") or [],
            "questions": pack.get("questions") or [],
            "competitors": [{k: c.get(k) for k in ("position", "domain", "url", "words", "title")}
                            for c in pack.get("competitors") or []],
            "ranking": pack.get("ranking") or [], "our_position": pack.get("our_position"),
            "target_words": pack.get("target_words"), "serp": pack.get("serp"),
            "score": (report or {}).get("score"), "items": (report or {}).get("items") or [],
            "words": (report or {}).get("words"), "notes": pack.get("notes") or []}


def main():
    ap = argparse.ArgumentParser(description="SEO's keyword plan for one post.")
    ap.add_argument("keyword")
    ap.add_argument("--kind", default="blog", choices=["blog", "service_page"])
    a = ap.parse_args()
    pack = build({"primary_keyword": a.keyword}, kind=a.kind, site=_env("SITE_URL"))
    print("\n".join(for_the_writer(pack, a.kind)))
    for n in pack["notes"]:
        print("  note:", n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
