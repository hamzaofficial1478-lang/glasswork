"""
seo_answers.py — AEO and GEO. Being the answer, and being the source.

    python -m pipelines.seo_answers                audit the owner's own pages
    python -m pipelines.seo_answers --report       what the last audit found
    python -m pipelines.seo_answers --llms-txt     draft the /llms.txt file
    python -m pipelines.seo_answers --faq <path>   draft FAQ markup for one page

WHAT THESE TWO WORDS MEAN, WITHOUT THE MARKETING

SEO is about being FOUND: ten blue links, and you want to be one of them.

AEO — Answer Engine Optimisation — is about being THE ANSWER. Google's AI
Overview, the featured snippet, the voice assistant: the box that answers
without anyone clicking. A page can rank fourth and still be the text in the
box, and it can rank first and be skipped entirely. What gets a page lifted is
structural, not clever: a heading phrased the way a person actually asks, the
answer given in the first forty words underneath it before any preamble, and
markup that tells a machine which bit is the question and which bit is the
answer.

GEO — Generative Engine Optimisation — is about being CITED. When somebody asks
ChatGPT or Perplexity "who does frameless glass partitions in Dubai", the model
answers from sources it retrieved, and names some of them. The 2024 GEO study
out of Princeton and IIT Delhi tested nine tactics across thousands of queries
and found the ones that actually moved visibility, by roughly thirty to forty
per cent, were unglamorous: cite credible sources, include direct quotations,
include real statistics, and write with authority. Keyword stuffing moved
nothing. That is the whole method, and it happens to be the same thing as
writing an honest page.

WHY THIS MATTERS MORE THAN USUAL FOR THIS BUSINESS

A glass and aluminium firm gets found on questions with an answer: what does a
shower screen cost in Dubai, can you replace a single pane in a double-glazed
unit, how long does an aluminium pergola take to install. Those are exactly the
queries an answer engine intercepts before the click ever reaches a website. If
the answer in that box comes from a competitor, the enquiry does too.

WHAT THIS FILE WILL NOT PRETEND TO DO

It cannot tell you whether ChatGPT currently cites you. Nothing on this machine
can: finding out means asking the engines themselves, repeatedly, from an
account, and that is a paid, rate-limited, non-deterministic measurement rather
than a lookup. So `visibility()` says so plainly instead of inventing a score.
What this file does is audit the things that are checkable — on the owner's own
pages, which we can read for free — and hand the fixes to whoever owns them.

It also edits nothing. Every finding becomes a task for Content or a queued site
change for IT. IT is the only door to production, here as everywhere.
"""
import argparse
import json
import re
import sqlite3
import time
import urllib.parse
from html import unescape as _unescape

from coordination import board, cabinet

try:
    from . import fetcher, services, site_services
except ImportError:  # pragma: no cover
    import fetcher, services, site_services

AGENT = "seo-answers"

# ---------------------------------------------------------------------------
# READING A PAGE
# ---------------------------------------------------------------------------

_SCRIPT = re.compile(r"<(script|style|noscript|svg)[^>]*>.*?</\1>", re.S | re.I)
_LDJSON = re.compile(
    r"<script[^>]+type\s*=\s*[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
    re.S | re.I)
_TAGS = re.compile(r"<[^>]+>")
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_HEAD = re.compile(r"<h([1-6])[^>]*>(.*?)</h\1>", re.S | re.I)
_PARA = re.compile(r"<p[^>]*>(.*?)</p>", re.S | re.I)
_LIST = re.compile(r"<(ul|ol)[^>]*>", re.I)
_TABLE = re.compile(r"<table[^>]*>", re.I)
_LINK = re.compile(r"<a\s[^>]*href\s*=\s*[\"']([^\"']+)[\"']", re.I)
_TIME = re.compile(r"<time[^>]*datetime\s*=\s*[\"']([^\"']+)", re.I)
_WS = re.compile(r"\s+")

# A heading that asks something. Either it ends in a question mark, or it opens
# with a question word — plenty of real sites write "How much a pergola costs"
# with no punctuation and it is still the question a person typed.
_ASKS = re.compile(
    r"^\s*(how|what|why|when|where|which|who|can|do|does|is|are|should|will)\b"
    r"|\?\s*$", re.I)

# Stock headings that start with a question word and ask nothing. "Why Choose
# Us" passed the test above and is not a question any customer typed into
# anything, so a page could score for AEO on a heading every website has.
_BOILERPLATE_HEADING = re.compile(
    r"^\s*(why choose (us|our)|who we are|what we do|what our clients say"
    r"|what we offer|why us|how we work|what makes us)\b", re.I)

# A number worth quoting: money, a percentage, a measurement, a year, a count
# with a unit. Deliberately narrow — "10 things you should know" is not a
# statistic and must not pass a check that exists to reward real ones.
_STAT = re.compile(
    r"(AED\s?[\d,]+|\$[\d,]+|[\d,]+\s?(?:AED|dirhams?)"
    r"|\b\d{1,3}(?:\.\d+)?\s?%"
    r"|\b\d{1,4}(?:\.\d+)?\s?(?:mm|cm|m2|m²|sqm|sq\.?\s?ft|kg|years?|months?|weeks?|days?|hours?)\b"
    r"|\b(?:19|20)\d{2}\b)", re.I)

_QUOTE = re.compile(r"[“\"][^”\"]{25,400}[”\"]")
_ATTRIB = re.compile(r"\b(said|says|according to|reports?|per the|—\s?[A-Z])", re.I)

# Assistants worth being read by. Blocking these is a legitimate choice and the
# check says so — but an owner who wants to be cited and is quietly blocking
# GPTBot has made that choice by accident, which is the thing worth catching.
AI_CRAWLERS = ("GPTBot", "OAI-SearchBot", "ChatGPT-User", "PerplexityBot",
               "ClaudeBot", "Claude-SearchBot", "Google-Extended",
               "Applebot-Extended", "CCBot")


def _text(raw):
    # Entities have to be decoded, not just stripped. A page that writes its
    # quotation marks as &ldquo; — which is most of them — would otherwise read
    # as having no quotations at all, and the GEO check would fail a page that
    # had actually done the work.
    return _WS.sub(" ", _unescape(_TAGS.sub(" ", _SCRIPT.sub(" ", raw or "")))).strip()


def _words(s):
    return [w for w in re.split(r"\s+", s or "") if w]


def _jsonld(html):
    """Every JSON-LD block on the page, flattened out of @graph wrappers."""
    out = []
    for m in _LDJSON.finditer(html or ""):
        try:
            d = json.loads(m.group(1).strip())
        except Exception:
            continue
        for item in (d if isinstance(d, list) else [d]):
            if not isinstance(item, dict):
                continue
            # A @graph wrapper is the common WordPress shape: the LocalBusiness
            # is nested inside it, so a reader that only looks at the top level
            # reports "no markup" on a site that has plenty.
            graph = item.get("@graph")
            if isinstance(graph, list):
                out.extend(g for g in graph if isinstance(g, dict))
            out.append(item)
    return out


def _typeset(block):
    v = block.get("@type")
    return {str(x).lower() for x in (v if isinstance(v, list) else [v]) if x}


def _types(blocks):
    t = set()
    for b in blocks:
        t |= _typeset(b)
    return t


def read_page(url, html) -> dict:
    """Everything the checks need, parsed once."""
    body = _SCRIPT.sub(" ", html or "")
    heads = [(int(m.group(1)), _text(m.group(2))) for m in _HEAD.finditer(body)]
    paras = [_text(m.group(1)) for m in _PARA.finditer(body)]
    paras = [p for p in paras if p]
    blocks = _jsonld(html or "")
    host = urllib.parse.urlparse(url).netloc.lower()
    outbound = []
    for m in _LINK.finditer(body):
        h = urllib.parse.urlparse(m.group(1)).netloc.lower()
        if h and h != host and not h.endswith("." + host):
            outbound.append(h)
    t = _TITLE.search(html or "")
    return {
        "url": url,
        "path": urllib.parse.urlparse(url).path or "/",
        "title": _text(t.group(1)) if t else "",
        "headings": heads,
        "h1": next((h for lvl, h in heads if lvl == 1), ""),
        "paragraphs": paras,
        "text": _text(html or ""),
        "html": body,
        "jsonld": blocks,
        "schema": _types(blocks),
        "lists": len(_LIST.findall(body)),
        "tables": len(_TABLE.findall(body)),
        "outbound": sorted(set(outbound)),
        "dates": _TIME.findall(html or ""),
    }


# The answer to a question ends where the next question starts. Obvious, and it
# was missing: on a FAQ page whose answers are not wrapped in <p>, this ran on
# past the heading and swallowed the next three questions and their answers. It
# showed up as FAQ markup where every answer contained the rest of the page —
# and it was quietly wrong in the audit too, because aeo.answer_first counts the
# words in whatever this returns.
_ANY_HEADING = re.compile(r"<h[1-6][\s>]", re.I)


def answer_after(page, heading_text):
    """
    The prose under a heading and BEFORE the next one — which is where an
    answer engine looks, and where most sites put throat-clearing instead.

    The heading is located as an ELEMENT, not as a string. Searching the raw
    HTML for the text finds it in the <title> first on any page whose title
    repeats its question, and everything after that is the wrong block.
    """
    want = " ".join(heading_text.lower().split())[:60]
    end = None
    for m in _HEAD.finditer(page["html"]):
        if " ".join(_text(m.group(2)).lower().split())[:60] == want:
            end = m.end()
            break
    if end is None:
        return ""
    body = page["html"][end:]

    # An answer stops where the next question starts.
    stop = _ANY_HEADING.search(body)
    block = body[:stop.start()] if stop else body[:4000]

    m = _PARA.search(block)
    if m:
        return _text(m.group(1))
    return _text(block)[:600]


# ---------------------------------------------------------------------------
# THE CHECKS
#
# weight is how much it matters for THIS business, not in general. A trade firm
# with one location lives or dies on LocalBusiness markup and question headings;
# speakable is a nice-to-have. The score is weighted so a page cannot look
# healthy by passing five cheap checks and failing the two that count.
# ---------------------------------------------------------------------------

def questions_on(p):
    return [h for _l, h in p["headings"]
            if _ASKS.search(h) and not _BOILERPLATE_HEADING.search(h)]


def _c_question_heading(p):
    qs = questions_on(p)
    if qs:
        return True, f"{len(qs)} question-shaped heading(s), e.g. “{qs[0][:70]}”"
    stock = [h for _l, h in p["headings"] if _BOILERPLATE_HEADING.search(h)]
    if stock:
        return False, (f"“{stock[0][:40]}” is a stock heading, not a question anybody "
                       f"typed — ask the one the customer asks")
    return False, "no heading is phrased as a question a customer would type"


def _c_answer_first(p):
    qs = questions_on(p)
    if not qs:
        return False, "no question heading to answer"
    for h in qs:
        a = answer_after(p, h)
        n = len(_words(a))
        if 15 <= n <= 90:
            return True, f"“{h[:50]}” is answered in {n} words, straight away"
    worst = _words(answer_after(p, qs[0]))
    return False, (f"the text under “{qs[0][:50]}” runs {len(worst)} words before "
                   f"it answers — an answer engine lifts the first 40 to 60")


def _c_faq_schema(p):
    if p["schema"] & {"faqpage", "qapage"}:
        return True, "FAQPage/QAPage markup is present"
    return False, "no FAQ markup — the questions on the page are invisible to a machine"


def _c_faq_visible(p):
    """
    Marked-up questions that do not appear in the visible page are against
    Google's own rules and risk a manual action. A check that rewards adding
    FAQ markup has to also catch the lazy way of adding it.

    Returns None — not applicable — when there is no FAQ markup at all. Scoring
    that as a pass paid a page four points for having nothing, which is how a
    thin page ends up looking healthier than it is.
    """
    if not (p["schema"] & {"faqpage", "qapage"}):
        return None, "no FAQ markup on this page, so nothing to verify"
    low = p["text"].lower()
    missing = []
    for b in p["jsonld"]:
        for q in (b.get("mainEntity") or []):
            if not isinstance(q, dict):
                continue
            name = (q.get("name") or "").strip()
            if name and name.lower()[:40] not in low:
                missing.append(name)
    if missing:
        return False, (f"{len(missing)} marked-up question(s) are not on the visible "
                       f"page, e.g. “{missing[0][:60]}” — that breaks Google's rules")
    return True, "every marked-up question is also on the page"


def _c_extractable(p):
    if p["lists"] or p["tables"]:
        return True, f"{p['lists']} list(s) and {p['tables']} table(s) to lift from"
    return False, ("no list or table — a price range or a step-by-step is what gets "
                   "pulled into an answer box")


def _c_localbusiness(p):
    # Any of Schema.org's ~200 LocalBusiness subtypes counts — HomeAndConstruction
    # Business, GeneralContractor, Store. Matching only the literal string
    # "LocalBusiness" would fail a site that did it more precisely than we asked.
    biz = [b for b in p["jsonld"]
           if any("business" in t or t in ("generalcontractor", "homeandconstructionbusiness")
                  for t in _typeset(b))]
    if not biz:
        return False, ("no LocalBusiness markup — for a trade with one address this "
                       "is the single most valuable piece of schema on the site")
    b = biz[0]
    missing = [k for k in ("name", "address", "telephone") if not b.get(k)]
    if missing:
        return False, "LocalBusiness markup is missing " + ", ".join(missing)
    if not b.get("areaServed"):
        return False, ("LocalBusiness markup has no areaServed — name Dubai, Abu Dhabi "
                       "and Sharjah explicitly")
    return True, "LocalBusiness markup with name, address, phone and areas served"


def _c_speakable(p):
    if any("speakable" in json.dumps(b).lower() for b in p["jsonld"]):
        return True, "speakable markup present"
    return False, "no speakable markup — voice assistants have nothing marked to read out"


def _c_short_paragraphs(p):
    if not p["paragraphs"]:
        return False, "no paragraphs found to check"
    lens = sorted(len(_words(x)) for x in p["paragraphs"])
    mid = lens[len(lens) // 2]
    if mid <= 80:
        return True, f"paragraphs run about {mid} words — extractable"
    return False, (f"the middle paragraph runs {mid} words — long blocks do not get "
                   f"lifted into an answer, they get skipped")


def _c_statistics(p):
    hits = _STAT.findall(p["text"])
    if len(hits) >= 3:
        return True, f"{len(hits)} concrete figures on the page"
    return False, (f"only {len(hits)} concrete figure(s) — the GEO study found real "
                   f"statistics among the strongest lifts to being cited")


# Your own Facebook page is not a citation. An early version of this check
# passed a page whose only outbound links were its social icons, which is the
# exact false positive that makes an audit worthless: it rewarded a page for
# having a footer.
_NOT_A_SOURCE = (
    "facebook.com", "instagram.com", "twitter.com", "x.com", "linkedin.com",
    "youtube.com", "tiktok.com", "pinterest.com", "wa.me", "whatsapp.com",
    "t.me", "snapchat.com", "maps.google.com", "goo.gl", "bit.ly",
    "wordpress.org", "elementor.com", "wix.com", "squarespace.com",
    "godaddy.com", "shopify.com", "gravatar.com", "w3.org", "schema.org",
    "gstatic.com", "googleapis.com", "cloudflare.com",
)


def sources_cited(p):
    """Outbound hosts that could plausibly be a source, socials and CDNs aside."""
    return [h for h in p["outbound"]
            if not any(h == b or h.endswith("." + b) for b in _NOT_A_SOURCE)]


def _c_citation(p):
    real = sources_cited(p)
    if real:
        return True, "cites " + ", ".join(real[:3])
    if p["outbound"]:
        return False, (f"the only outbound links are social and platform links "
                       f"({', '.join(p['outbound'][:3])}) — your own Facebook page "
                       f"is not a source")
    return False, ("nothing on the page is sourced to anyone else — citing a standard, "
                   "a supplier spec or a municipality rule is what makes a model quote you")


def _c_quotation(p):
    q = _QUOTE.search(p["text"])
    if q and _ATTRIB.search(p["text"]):
        return True, "an attributed quotation is present"
    if q:
        return False, "there is a quotation but nobody is credited for it"
    return False, ("no quotation — one line from the owner, named, about a real job "
                   "is the cheapest GEO win on this list")


def subject_of(p):
    """
    What the page is about, as a noun phrase: "Glass partitions in Dubai" is
    about glass partitions. The location and the question wrapper are trimmed
    off so the definition check looks for the thing, not the headline.
    """
    s = (p["h1"] or p["title"] or "").strip()
    s = re.split(r"[|–—:?]", s)[0]
    s = re.sub(r"\s+\b(in|for|across|near|around)\b.*$", "", s, flags=re.I)
    s = re.sub(r"^\s*(the|a|an|our)\s+", "", s, flags=re.I)
    return " ".join(_words(s))


def _c_definition(p):
    subject = subject_of(p)
    if not subject:
        return False, "no H1 or title to define"
    # The subject must be followed, immediately, by a defining verb — not
    # merely appear within forty characters of one. "Glass Partitions Pricing
    # We are a leading provider" is not a definition, and an earlier version of
    # this check passed it, which meant the check rewarded exactly the kind of
    # page it exists to catch.
    stem = subject[:-1] if subject.lower().endswith("s") else subject
    core = r"\s+".join(re.escape(w) for w in _words(stem))
    pat = re.compile(r"\b" + core + r"s?\b\s+(?:is|are)\s+(?:a|an|the)\b"
                     r"|\b" + core + r"s?\b\s+(?:means|refers to)\b", re.I)
    if pat.search(p["text"]):
        return True, f"“{subject[:40]}” is defined in plain words on the page"
    return False, (f"the page never says what “{subject[:40]}” actually is — a model "
                   f"quoting a definition needs one to quote")


def _c_author(p):
    low = p["text"].lower()
    if any(b.get("author") for b in p["jsonld"]):
        return True, "an author is named in the markup"
    if re.search(r"\b(\d{1,2})\+?\s*years?\b.{0,40}\b(experience|trade|business)\b", low):
        return True, "experience is stated on the page"
    return False, ("nobody is behind this page — name the person and the years in the "
                   "trade; authority is one of the four things that actually moved the "
                   "GEO numbers")


def _c_dated(p):
    if p["dates"]:
        return True, f"dated {p['dates'][0][:10]}"
    if any(b.get("dateModified") or b.get("datePublished") for b in p["jsonld"]):
        return True, "a date is in the markup"
    return False, ("no visible or marked-up date — an undated page loses to a dated one "
                   "when a model picks which source to trust on a price")


CHECKS = [
    # id, family, label, weight, fn, fix, sector
    ("aeo.question_heading", "aeo", "A heading asks the customer's question", 5,
     _c_question_heading,
     "Add an H2 phrased exactly as somebody would type it — “How much does a "
     "frameless glass partition cost in Dubai?” — not “Pricing”.", "Content"),
    ("aeo.answer_first", "aeo", "The answer comes first, in 40–60 words", 5,
     _c_answer_first,
     "Put the direct answer in the first two sentences under the question. "
     "Background afterwards. An answer engine takes the top of the block.", "Content"),
    ("aeo.faq_schema", "aeo", "FAQ markup so a machine can find the Q and the A", 4,
     _c_faq_schema,
     "Add FAQPage JSON-LD. `python -m pipelines.seo_answers --faq <path>` drafts it "
     "from the questions already on the page.", "IT"),
    ("aeo.faq_visible", "aeo", "Marked-up questions are visible on the page", 4,
     _c_faq_visible,
     "Every question in the FAQ markup must be readable on the page itself. "
     "Hidden FAQ markup is against Google's rules and can earn a manual action.", "IT"),
    ("aeo.extractable", "aeo", "Something liftable — a list or a table", 3,
     _c_extractable,
     "Add a short price-range table or a numbered installation sequence. That is "
     "the shape of thing an answer box quotes.", "Content"),
    ("aeo.localbusiness", "aeo", "LocalBusiness markup with the real details", 5,
     _c_localbusiness,
     "Add LocalBusiness JSON-LD: legal name, the actual address, the phone that "
     "gets answered, and areaServed listing Dubai, Abu Dhabi and Sharjah.", "IT"),
    ("aeo.speakable", "aeo", "Something marked for a voice assistant to read", 1,
     _c_speakable,
     "Add a speakable specification pointing at the answer paragraph. Small win, "
     "small effort.", "IT"),
    ("aeo.short_paragraphs", "aeo", "Paragraphs short enough to be quoted", 2,
     _c_short_paragraphs,
     "Break the long blocks. Under about 80 words each.", "Content"),

    ("geo.statistics", "geo", "Real numbers on the page", 5,
     _c_statistics,
     "Put actual figures in: a price range in AED, glass thickness in mm, how many "
     "days an install takes, how many units you have fitted. Vague pages do not get "
     "quoted.", "Content"),
    ("geo.citation", "geo", "Sources cited", 4,
     _c_citation,
     "Reference something checkable — a Dubai Municipality regulation, a glass "
     "standard, a manufacturer's spec sheet — and link it. Citing sources was one "
     "of the strongest lifts in the GEO study.", "Content"),
    ("geo.quotation", "geo", "An attributed quotation", 3,
     _c_quotation,
     "One or two sentences from the owner, named, about a job he actually did. "
     "Models reproduce quotes, and a reproduced quote carries the business name.",
     "Content"),
    ("geo.definition", "geo", "The subject is defined in plain words", 3,
     _c_definition,
     "Open with one sentence saying what the thing IS — “A frameless glass "
     "partition is a floor-to-ceiling divider made from toughened glass with no "
     "vertical framing.” That sentence is what gets quoted back.", "Content"),
    ("geo.author", "geo", "A person and their experience stand behind it", 3,
     _c_author,
     "Name the author and the years in the trade, on the page and in the markup.",
     "Content"),
    ("geo.dated", "geo", "The page is dated", 2,
     _c_dated,
     "Show a published or updated date. Prices from an undated page lose to prices "
     "from a dated one.", "IT"),
]

WEIGHT_TOTAL = sum(c[3] for c in CHECKS)


def audit_page(url, html) -> dict:
    """
    Score one page. A check returns True, False, or None for not-applicable —
    and a not-applicable check is left out of BOTH sides of the score, never
    counted as a pass. The difference matters: the FAQ-visibility check has
    nothing to say about a page with no FAQ, and paying that page for it would
    make the emptiest pages on the site score best.
    """
    p = read_page(url, html)
    rows, got, possible = [], 0, 0
    for cid, fam, label, weight, fn, fix, sector in CHECKS:
        try:
            ok, detail = fn(p)
        except Exception as e:                      # a broken check is not a failed page
            ok, detail = False, f"check could not run: {e}"
        if ok is not None:
            possible += weight
            got += weight if ok else 0
        rows.append({"url": url, "check_id": cid, "family": fam, "label": label,
                     "weight": weight, "passed": bool(ok),
                     "n_a": ok is None, "detail": detail,
                     "fix": fix, "sector": sector})
    return {"url": url, "path": p["path"], "title": p["title"],
            "score": round(100.0 * got / possible, 1) if possible else 0.0,
            "findings": rows}


# ---------------------------------------------------------------------------
# SITE-LEVEL: robots.txt and llms.txt
# ---------------------------------------------------------------------------

def ai_crawler_check(base) -> dict:
    """
    Is the site blocking the assistants it wants to be cited by?

    Blocking them is a real choice — some owners do not want their photographs
    in a training set, and that is theirs to decide. What this catches is the
    accident: a robots.txt copied off a template that quietly disallows GPTBot
    while the owner wonders why he is never mentioned.
    """
    r = fetcher.fetch(base.rstrip("/") + "/robots.txt", allow_paid=False, min_chars=0)
    if not r["html"]:
        return {"read": False, "blocked": [], "allowed": list(AI_CRAWLERS),
                "note": "no robots.txt found — nothing is blocked, which is fine"}
    txt = r["html"]
    blocked, agent, disallows = [], "", {}
    for line in txt.splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        k, v = (x.strip() for x in line.split(":", 1))
        k = k.lower()
        if k == "user-agent":
            agent = v
            disallows.setdefault(agent.lower(), [])
        elif k == "disallow" and agent:
            disallows.setdefault(agent.lower(), []).append(v)
    for bot in AI_CRAWLERS:
        rules = disallows.get(bot.lower())
        if rules and any(x == "/" for x in rules):
            blocked.append(bot)
    return {"read": True, "blocked": blocked,
            "allowed": [b for b in AI_CRAWLERS if b not in blocked],
            "note": ("nothing blocked" if not blocked else
                     f"blocked outright: {', '.join(blocked)}")}


# Paths that are never worth auditing: they are not the pages an answer engine
# would quote, and reading them wastes the page budget on the same site.
_SKIP_PATH = re.compile(
    r"\.(?:jpe?g|png|gif|webp|svg|ico|css|js|pdf|zip|mp4|woff2?)$"
    r"|/(?:wp-admin|wp-content|wp-json|cart|checkout|my-account|feed|tag|author"
    r"|category/page|page/\d+)(?:/|$)", re.I)


def _fingerprint(html):
    """The visible words of a page, hashed. Two URLs with the same prose are
    the same page, however different their markup wrappers."""
    import hashlib
    return hashlib.sha1(_text(html).lower().encode("utf-8", "ignore")).hexdigest()


def pages_to_audit(base, home_html, max_pages=12):
    """
    Which of the owner's pages to read.

    The sitemap first, because it is the list he publishes himself and it is
    the same list Google works from. Falling back to links off the home page
    when there is no sitemap, and then to the usual service paths.

    This deliberately does NOT reuse site_services._internal_links: that helper
    only returns paths containing "service" or "product", because its job is to
    work out what the business sells. Used here it audited the home page and
    nothing else on a real site — the audit needs the pages people land on,
    which includes contact, about and every individual job page.
    """
    host = urllib.parse.urlparse(base).netloc.lower()
    found = []

    def add(u):
        p = urllib.parse.urlparse(u)
        if p.netloc.lower() != host or _SKIP_PATH.search(p.path or ""):
            return
        clean = f"{p.scheme}://{p.netloc}{(p.path or '/').rstrip('/') or '/'}"
        if clean not in found:
            found.append(clean)

    add(base)
    try:
        from . import competitors as _c
    except ImportError:  # pragma: no cover
        import competitors as _c
    try:
        for u in _c.sitemap_urls(host)[:max_pages * 3]:
            add(u)
    except Exception:
        pass
    if len(found) < max_pages:
        for href in re.findall(r'href=["\']([^"\'#]+)', home_html or "", re.I):
            add(urllib.parse.urljoin(base, href))
            if len(found) >= max_pages * 2:
                break
    if len(found) < 2:
        for p in getattr(site_services, "LIKELY_PATHS", ()):
            add(base + p)
    # Shortest paths first: /pergolas before /blog/2019/some-old-post. The
    # pages nearest the root are the ones customers land on and the ones an
    # answer engine is choosing between.
    rest = sorted(found[1:], key=lambda u: (len(urllib.parse.urlparse(u).path), u))
    return ([found[0]] + rest)[:max_pages]


def llms_txt(base="", svc_keys=None) -> str:
    """
    Draft an /llms.txt — a plain-Markdown map of the site for a model reading it.

    It is a convention, not a standard anybody is obliged to honour, and it is
    written here as one: a short, honest index. It costs one file and it is the
    only artefact on this list that speaks to the models directly.
    """
    base = (base or "").rstrip("/") or "https://example.ae"
    keys = list(svc_keys or [])
    if not keys:
        try:
            keys = list(services.SERVICES.keys())
        except Exception:
            keys = []
    lines = [
        "# Glass & aluminium services, UAE",
        "",
        "> Fabrication and installation of glass and aluminium work across Dubai, "
        "Abu Dhabi and Sharjah. Over ten years in the trade. Quotes from site "
        "measurements, not from a calculator.",
        "",
        "## Services",
    ]
    for k in keys:
        try:
            lab = services.label(k)
        except Exception:
            lab = k.replace("_", " ").title()
        lines.append(f"- [{lab}]({base}/{k.replace('_', '-')}): {lab} supplied, "
                     f"fabricated and fitted across the UAE.")
    lines += [
        "",
        "## Contact",
        f"- [Enquiries]({base}/contact): site visits and written quotations.",
        "",
        "## Notes",
        "- Prices vary with glass thickness, span and access. Any figure quoted on "
        "the site is a range from real jobs, not a fixed price.",
        "- Work is carried out in the UAE only.",
    ]
    return "\n".join(lines) + "\n"


def faq_pairs_on(url, html=None):
    """
    Question headings and the answer written under each, lifted off a real page.

    Only what is ALREADY THERE. Nothing is invented and nothing is rephrased —
    marking up an answer the visitor cannot read is against Google's own rules
    and is the thing aeo.faq_visible exists to catch. If the page has no
    question headings this returns nothing, which is the honest outcome.
    """
    if html is None:
        try:
            from . import fetcher
        except ImportError:  # pragma: no cover
            import fetcher
        r = fetcher.fetch(url, allow_paid=False)
        if not r["html"]:
            return [], (r["error"] or "could not read the page")
        html = r["html"]
    p = read_page(url, html)
    pairs = []
    for _lvl, h in p["headings"]:
        if not _ASKS.search(h) or _BOILERPLATE_HEADING.search(h):
            continue
        a = answer_after(p, h).strip()
        # Google wants a real answer, not a stub, and a 900-word essay is not an
        # answer either. Both ends are trimmed rather than silently accepted.
        if len(_words(a)) < 5:
            continue
        pairs.append((h.strip(), a[:1200].strip()))
    if not pairs:
        return [], ("no question-shaped headings on that page. Write the question "
                    "as an H2 first — 'How much does a glass partition cost in "
                    "Dubai?' — with the answer underneath, then run this again.")
    return pairs, ""


def faq_jsonld(pairs) -> str:
    """
    FAQPage markup from question/answer pairs.

    Only ever built from text that is already ON the page — see the
    aeo.faq_visible check for why. This function does not invent answers, it
    marks up the ones Content wrote.
    """
    doc = {"@context": "https://schema.org", "@type": "FAQPage",
           "mainEntity": [{"@type": "Question", "name": q,
                           "acceptedAnswer": {"@type": "Answer", "text": a}}
                          for q, a in pairs if q and a]}
    return ('<script type="application/ld+json">\n'
            + json.dumps(doc, indent=2, ensure_ascii=False) + "\n</script>")


_TEL = re.compile(r'href=["\']tel:([^"\']+)', re.I)
_MAILTO = re.compile(r'href=["\']mailto:([^"\'?]+)', re.I)
_WA = re.compile(r'wa\.me/(\d{6,})', re.I)


def localbusiness_jsonld(base, pages) -> tuple:
    """
    Draft LocalBusiness markup from the details ALREADY on the site.

    This is the single heaviest AEO check and the one most often missing: for a
    trade with one address it is what puts you in the map pack. It is also a
    one-time paste, which is why drafting it is worth doing properly.

    Nothing is invented. The phone, email and name are lifted from the pages;
    anything not found is left as a NAMED PLACEHOLDER in capitals rather than
    guessed, because a wrong address in structured data is worse than none —
    Google cross-checks it against your Business Profile and a mismatch costs
    you the ranking the markup was meant to win.

    Returns (snippet, missing_fields).
    """
    tel = email = name = ""
    for url, html in pages:
        if not tel:
            m = _TEL.search(html) or None
            if m:
                tel = re.sub(r"[^\d+]", "", m.group(1))
            else:
                w = _WA.search(html)
                if w:
                    tel = "+" + w.group(1)
        if not email:
            m = _MAILTO.search(html)
            if m:
                email = m.group(1).strip()
        if not name:
            t = _TITLE.search(html)
            if t:
                name = re.split(r"[|\u2013\u2014-]", _text(t.group(1)))[0].strip()

    missing = []
    if not tel:
        tel = "YOUR PHONE, IN FULL INTERNATIONAL FORM e.g. +9715XXXXXXXX"
        missing.append("telephone")
    if not name:
        name = "YOUR REGISTERED BUSINESS NAME"
        missing.append("name")
    if not email:
        email = "YOUR EMAIL"
        missing.append("email")
    missing.append("address")          # never scraped: too easy to get wrong

    doc = {
        "@context": "https://schema.org",
        "@type": "HomeAndConstructionBusiness",
        "name": name,
        "url": base.rstrip("/") + "/",
        "telephone": tel,
        "email": email,
        "address": {
            "@type": "PostalAddress",
            "streetAddress": "YOUR STREET / UNIT",
            "addressLocality": "YOUR EMIRATE e.g. Dubai",
            "addressCountry": "AE",
        },
        "areaServed": ["Dubai", "Abu Dhabi", "Sharjah"],
        "openingHours": "Mo-Sa 08:00-18:00",
    }
    snippet = ('<script type="application/ld+json">\n'
               + json.dumps(doc, indent=2, ensure_ascii=False) + "\n</script>")
    return snippet, missing


def visibility():
    """
    Are we actually being cited by ChatGPT, Perplexity or Gemini?

    Not answerable from here, and this says so rather than producing a number
    that looks like an answer. Measuring it means asking each engine a set of
    real customer questions, repeatedly, from an account, and counting how often
    the business is named — a paid, rate-limited, non-deterministic measurement
    that changes between two runs of the same prompt.

    Standing offer, not a silent gap: if the owner wants it, it is a job for the
    Report sector with a model on the shelf and a fixed question set, and the
    cost lands on the daily cap like everything else.
    """
    return {"possible": False,
            "why": ("measuring citation in ChatGPT or Perplexity means querying "
                    "those engines from an account — paid, rate-limited, and "
                    "different on every run. Nothing here can look it up."),
            "what_it_would_take": ("a fixed set of ~20 real customer questions, "
                                   "asked weekly through a model on the shelf, "
                                   "counting how often the business is named. "
                                   "Ask and it gets built and costed.")}


# ---------------------------------------------------------------------------
# THE PASS
# ---------------------------------------------------------------------------

def _store(con, findings):
    now = int(time.time())
    for f in findings:
        if f.get("n_a"):
            # Not applicable to this page. Filed as neither pass nor fail, and
            # any row left over from a run when it DID apply is cleared, so the
            # report never shows a stale failure for a check that has since
            # stopped being relevant.
            con.execute("DELETE FROM aeo_findings WHERE url=? AND check_id=?",
                        (f["url"], f["check_id"]))
            continue
        con.execute(
            "INSERT INTO aeo_findings(url, family, check_id, passed, weight, detail,"
            " fix, owner_sector, found_at) VALUES(?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(url, check_id) DO UPDATE SET"
            " passed=excluded.passed, detail=excluded.detail, fix=excluded.fix,"
            " family=excluded.family, weight=excluded.weight,"
            " owner_sector=excluded.owner_sector, found_at=excluded.found_at",
            (f["url"], f["family"], f["check_id"], 1 if f["passed"] else 0,
             f["weight"], f["detail"][:400], f["fix"][:400], f["sector"], now))
    con.commit()


def critic_check(pages, robots):
    """
    The Critic's questions, asked before anything is filed.

    Every one of these is a way this pass could report something it did not
    actually establish.
    """
    reasons = []
    if not pages:
        reasons.append("no page was read, so there is nothing to conclude")
    if any(p["score"] == 100.0 for p in pages) and len(pages) == 1:
        reasons.append("a single perfect page is more likely a parse failure than a "
                       "perfect page — read more before claiming it")
    thin = [p for p in pages if len(p["findings"]) != len(CHECKS)]
    if thin:
        reasons.append(f"{len(thin)} page(s) did not run every check")
    if robots.get("read") is False and robots.get("blocked"):
        reasons.append("claimed a crawler is blocked without having read robots.txt")
    return (len(reasons) == 0, reasons)


def run(agent=AGENT, url=None, max_pages=12) -> dict:
    with board.agent_run(agent) as run_row:
        out = _work(agent, url, max_pages)
        run_row.note(out.get("summary") or out.get("error") or "AEO/GEO pass")
        return out


def _work(agent, url, max_pages) -> dict:
    board.ensure_schema()
    from coordination import site as site_cfg
    base = (url or site_cfg.site_url() or "").strip()
    if not base:
        return {"ok": False, "error": "no website saved — put it on /setup first"}
    if "://" not in base:
        base = "https://" + base
    base = base.rstrip("/")

    # The owner's own site, so the money switch is off: his server does not
    # block him and there is nothing to pay for. Anything JavaScript-built is
    # reported as skipped with the price named, exactly as the indexer does.
    b = cabinet.borrow("seo_data", agent, "SEO",
                       reason="AEO and GEO audit of our own pages",
                       data_class="internal", wait=True)
    borrow_id = b.get("borrow_id")
    try:
        home = fetcher.fetch(base, allow_paid=False)
        if not home["html"]:
            return {"ok": False,
                    "error": f"could not read {base} — {home['error'] or 'no response'}"}

        urls, skipped = pages_to_audit(base, home["html"], max_pages), []

        # Same page under several addresses is one page. Plenty of sites answer
        # every path with the same shell, and a site with one real page must not
        # be reported as "no question heading on twelve pages" — that turns one
        # job for Content into a headline number that is simply untrue.
        pages, htmls, seen_body = [], {base: home["html"]}, {_fingerprint(home["html"]): base}
        for u in urls[1:]:
            r = fetcher.fetch(u, allow_paid=False)
            if not r["html"] or r.get("declined"):
                skipped.append({"url": u, "why": r.get("error") or "unreadable"})
            else:
                fp = _fingerprint(r["html"])
                if fp in seen_body:
                    skipped.append({"url": u,
                                    "why": f"identical to {seen_body[fp]} — counted once"})
                else:
                    seen_body[fp] = u
                    htmls[u] = r["html"]
            time.sleep(0.2)                        # his own server, still be polite

        for u, h in htmls.items():
            pages.append(audit_page(u, h))

        robots = ai_crawler_check(base)
        ok, reasons = critic_check(pages, robots)
        result = {"ok": True, "agent": agent, "base": base,
                  "pages": [{k: v for k, v in p.items() if k != "findings"}
                            for p in pages],
                  "skipped": skipped, "robots": robots,
                  "visibility": visibility()}
        if not ok:
            result.update({"critic": "rejected", "critic_reasons": reasons,
                           "summary": "critic rejected: " + "; ".join(reasons)})
            return result
        result["critic"] = "passed"

        con = sqlite3.connect(board.DB, timeout=30)
        con.row_factory = sqlite3.Row
        for p in pages:
            _store(con, p["findings"])

        # What is broken, grouped by the check rather than by the page: "eleven
        # pages have no question heading" is one job for Content. Eleven tickets
        # saying the same thing is eleven tickets nobody reads.
        fails = {}
        for p in pages:
            for f in p["findings"]:
                if not f["passed"] and not f.get("n_a"):
                    fails.setdefault(f["check_id"], {"n": 0, **f})["n"] += 1
        ranked = sorted(fails.values(), key=lambda f: (-f["weight"] * f["n"], f["check_id"]))
        con.close()

        avg = round(sum(p["score"] for p in pages) / len(pages), 1) if pages else 0.0
        result["score"] = avg
        result["worst"] = [{"check": f["check_id"], "label": f["label"],
                            "pages": f["n"], "fix": f["fix"], "sector": f["sector"]}
                           for f in ranked[:6]]

        # Content owns the writing, IT owns the markup. Ask each for its own,
        # and never both for the same thing.
        for sector in ("Content", "IT"):
            mine = [f for f in ranked[:8] if f["sector"] == sector]
            if not mine:
                continue
            body = "\n".join(
                f"- {f['label']} — failing on {f['n']} of {len(pages)} page(s). "
                f"{f['fix']}" for f in mine)
            head = ("Answer-engine and generative-engine gaps on our own pages. "
                    "Ranked by what they cost us, worst first.\n\n")
            tail = ("\n\nNothing here is a ranking theory: these are the structural "
                    "things an answer box and a model look for, and every one is "
                    "checkable on the page afterwards.")
            result[f"asked_{sector.lower()}"] = board.post_request(
                "SEO", sector, f"AEO/GEO fixes for {sector}", head + body + tail)

        if robots.get("blocked"):
            board.queue_site_change(
                agent, "robots",
                "robots.txt blocks " + ", ".join(robots["blocked"]) + ". These are "
                "the assistants people ask for a glazier. Blocking them is a fair "
                "choice, but it is a choice — confirm it is deliberate, and if not, "
                "allow them.")
            result["queued_robots"] = True

        did = board.log_decision(
            "seo-answers",
            reasoning=(f"Audited {len(pages)} of our own pages against {len(CHECKS)} "
                       f"AEO and GEO checks, average score {avg}%. Worst gap: "
                       + (f"{ranked[0]['label']} on {ranked[0]['n']} page(s)"
                          if ranked else "none") + ". These are structural checks on "
                       "our own HTML, not estimates — every one was read off the page."),
            expectation=("The Content and IT requests are worked and a re-run of this "
                         "audit scores higher. Whether that turns into citations is "
                         "not measurable from here and is not claimed."),
            confidence=0.8,
            evidence={"pages": [p["url"] for p in pages], "score": avg,
                      "worst": [f["check_id"] for f in ranked[:5]],
                      "robots_blocked": robots.get("blocked", [])})
        result["decision_id"] = did
        result["summary"] = (
            f"{len(pages)} pages audited, AEO/GEO score {avg}%, "
            f"{len(ranked)} distinct gaps, worst: "
            + (ranked[0]["label"] if ranked else "none"))
        return result
    finally:
        if borrow_id:
            cabinet.release("seo_data", agent, borrow_id=borrow_id, usd_cost=0.0)


def as_text() -> str:
    board.ensure_schema()
    con = sqlite3.connect(board.DB, timeout=30)
    con.row_factory = sqlite3.Row
    rows = [dict(r) for r in con.execute(
        "SELECT * FROM aeo_findings ORDER BY passed, family, weight DESC, check_id")]
    con.close()
    v = visibility()
    caveat = ["", "WHAT THIS CANNOT TELL YOU", "  " + v["why"],
              "  " + v["what_it_would_take"]]
    if not rows:
        return "\n".join(
            ["No AEO/GEO audit on file yet — run `python -m pipelines.seo_answers`."]
            + caveat)
    pages = {r["url"] for r in rows}
    fails = [r for r in rows if not r["passed"]]
    L = [f"AEO / GEO — {len(pages)} page(s) audited, {len(fails)} of {len(rows)} "
         f"checks failing", ""]
    if not fails:
        L.append("  Everything checkable passes. What that does NOT tell you is "
                 "whether anyone is actually citing you — see `visibility()`.")
    grouped = {}
    for r in fails:
        grouped.setdefault((r["family"], r["check_id"]), []).append(r)
    for (fam, cid), rs in sorted(grouped.items(),
                                 key=lambda kv: -(kv[1][0]["weight"] * len(kv[1]))):
        L.append(f"  [{fam.upper()}] {cid} — failing on {len(rs)} page(s) "
                 f"(weight {rs[0]['weight']}, owner: {rs[0]['owner_sector']})")
        L.append(f"      {rs[0]['detail']}")
        L.append(f"      fix: {rs[0]['fix']}")
    return "\n".join(L + caveat)


def main():
    ap = argparse.ArgumentParser(
        description="AEO and GEO — being the answer, and being the source.")
    ap.add_argument("--report", action="store_true", help="what the last audit found")
    ap.add_argument("--llms-txt", action="store_true", help="draft /llms.txt")
    ap.add_argument("--url", default="", help="audit this address instead of the saved one")
    ap.add_argument("--pages", type=int, default=12, help="how many pages to read")
    ap.add_argument("--localbusiness", action="store_true",
                    help="draft LocalBusiness markup from what is already on your site")
    ap.add_argument("--faq", default="",
                    help="draft FAQPage markup from the questions ALREADY on this "
                         "page (give a full URL or a path like /faq.php)")
    a = ap.parse_args()

    if a.report:
        print(as_text())
        return
    if a.llms_txt:
        from coordination import site as site_cfg
        print(llms_txt(a.url or site_cfg.site_url()))
        return
    if a.localbusiness:
        from coordination import site as site_cfg
        base = (a.url or site_cfg.site_url() or "").strip()
        if not base:
            print("No website saved. Give --url, or save your site on /setup.")
            return
        if "://" not in base:
            base = "https://" + base
        base = base.rstrip("/")
        got = []
        for path in ("", "/contact.php", "/contact", "/contact-us", "/about.php", "/about"):
            r = fetcher.fetch(base + path, allow_paid=False)
            if r["html"]:
                got.append((base + path, r["html"]))
            if len(got) >= 3:
                break
        if not got:
            print(f"Could not read {base}.")
            return
        snippet, missing = localbusiness_jsonld(base, got)
        print(f"<!-- LocalBusiness markup for {base}")
        print( "     Paste into the <head> of EVERY page, not just the home page.")
        print( "     For a trade with one address this is the heaviest single item")
        print( "     on the AEO list: it is what puts you in the map pack.")
        if missing:
            print( "")
            print(f"     CHECK BEFORE YOU PASTE: {', '.join(missing)}")
            print( "     Anything in CAPITALS below was not found on your site and")
            print( "     has NOT been guessed. A wrong address here is worse than")
            print( "     none — Google cross-checks it against your Business")
            print( "     Profile and a mismatch costs you the map-pack ranking.")
        print( "-->")
        print(snippet)
        return

    if a.faq:
        from coordination import site as site_cfg
        target = a.faq
        if "://" not in target:
            base = (a.url or site_cfg.site_url() or "").rstrip("/")
            if not base:
                print("No website saved. Give a full URL, or save your site on /setup.")
                return
            target = base + ("/" + target.lstrip("/"))
        pairs, err = faq_pairs_on(target)
        if err:
            print(f"Nothing to mark up on {target}:\n  {err}")
            return
        print(f"<!-- FAQPage markup for {target}")
        print(f"     {len(pairs)} question(s), taken word-for-word off the page.")
        print( "     Paste it into that page's <head>. Change the wording on the")
        print( "     page and change it here too: marked-up text that a visitor")
        print( "     cannot read breaks Google's rules. -->")
        print(faq_jsonld(pairs))
        return
    out = run(url=a.url, max_pages=a.pages)
    print(json.dumps(out, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
