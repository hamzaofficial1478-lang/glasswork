"""
seo_audit.py — the technical audit. Every page read the way Google receives it.

    python -m pipelines.seo_audit              audit, file what is wrong, verify fixes
    python -m pipelines.seo_audit --dry-run    audit and print; file nothing
    python -m pipelines.seo_audit --json

WHY THIS EXISTS

The SEO sector audited for AEO and GEO — questions, answers, citations — and
nothing audited the basics underneath them. Nothing checked whether a page had
a meta description, whether two pages carried the same title, whether a
canonical pointed somewhere else, whether the JSON-LD even parsed, whether an
internal link led to a 404. Those are the checks every SEO tool starts with, and
the agency had none of them.

The owner pointed at BeyondSEO (github.com/beyondtahir/beyondseo), a native
Python SEO engine that needs no API key, and asked for its brain. This is that:
its checks, and — the part that makes them worth having — its rules about what
a check is allowed to claim. Read off its source (extract.py, evidence.py,
findings.py), not paraphrased from its README.

WHAT WAS TAKEN, AND WHY IT MATTERS HERE

  THE CHECKS          Title, description, H1, canonical, JSON-LD, image alt and
                      dimensions, noindex, redirect chains, soft-404 screens,
                      JavaScript shells — per page. Duplicate titles and
                      descriptions and broken internal links — across the site.

  ABSENCE IS EARNED   "Positive observations can survive a partial capture;
                      absence claims cannot." A page that failed to load, or
                      came back as a JavaScript shell, is not evidence that it
                      has no H1. BeyondSEO suppresses every "missing" finding
                      unless the capture was good enough to prove the absence.
                      That one rule is what stops an audit filing twenty false
                      tickets the morning the site is slow.

  THE CONTRACT        Every finding carries what was seen and when, whether it
                      is a verified fact, an inference or a hypothesis, why it
                      matters to a customer, the action, and an ACCEPTANCE
                      CHECK — how to tell the fix worked. The handbook's
                      "no decision without evidence", as a data structure.

  VERIFY THE FIX      BeyondSEO separates making a change from verifying it.
                      Each run re-reads the pages behind the tickets this audit
                      filed: gone on a clean capture means closed, still there
                      after IT marked it deployed means reopened. A fix that did
                      not take is caught the next morning instead of never.

WHAT WAS NOT TAKEN

  - The headless browser. His site is PHP rendered on the server; what Google
    receives is the HTML itself, so that is what is read. The JavaScript-shell
    check stays, so a page that needs a browser still says so.
  - Search-engine discovery and the reputation crawler. The keyword and link
    desks already cover that ground, and search engines refuse datacentre IPs.
  - The agent skill, the dashboard and the branded PDF. The agency has its own
    agents, its own office and its own daily report.
  - Arbitrary thresholds. BeyondSEO does not fail a title for being 61
    characters, and neither does this — that is a style preference dressed as a
    defect.

WHERE IT GOES

Findings go to site_issues, the queue IT's Fixer already works, as kind 'seo'.
SEO does not touch the website — the handbook's line — and the Releaser remains
the only door to production. The full contract for each finding is kept in
seo_findings, so Report and the verifier can read it back.

Reads only. Respects robots.txt. Claims no task off the board, so it never
takes the SEO sector's one job for the run.
"""
import argparse
import hashlib
import html as _html
import json
import re
import sqlite3
import time
import urllib.parse
import urllib.robotparser

import requests

from coordination import board, site as site_cfg

AGENT = "seo-lead"
SECTOR = "SEO"
UA = "WorldDealAgency-SEOAudit/1.0 (reads the owner's own site)"

# BeyondSEO's standard depth: fifteen priority pages, home first.
MAX_PAGES = 15
# Internal link targets checked for a 404 beyond the pages already read.
MAX_LINK_CHECKS = 40
FETCH_TIMEOUT = 20

# The marker that ties a site_issues row back to this audit and its check.
# Kept in the ticket's text because site_issues has no column for it, and a
# migration on his live cabinet is a bigger risk than a bracketed tag.
MARK = "[seo-audit:{code}]"
# Digits included: this was [a-z_]+, and "missing_h1" has a 1 in it. The marker
# went unrecognised, so that ticket was re-filed every run and the verifier
# could never close it. Caught by audit_tests before it reached his board.
MARK_RE = re.compile(r"\[seo-audit:([a-z0-9_]+)\]")


# ---------------------------------------------------------------------------
# THE CHECKS — severity, confidence, why it matters, what to do, fixed when
# ---------------------------------------------------------------------------
#
# confidence is BeyondSEO's: observed (a fact on the page), heuristic (a
# pattern that usually means this), review (worth a human look; not by itself
# a defect). It becomes the claim type every finding carries.

CHECKS = {
    "fetch_failed": (
        "high", "observed",
        "This run could not read the page at all, so its content and visibility are unknown.",
        "Open the page yourself; if it fails for you too, restore it.",
        "The page returns 200 on the next run."),
    "http_error": (
        "high", "observed",
        "Visitors and Google get an error page instead of the service.",
        "Restore the page, or point every link at its replacement and redirect the old address.",
        "The page returns 200, or a single 301 to its replacement."),
    "redirect_chain": (
        "medium", "observed",
        "Every extra hop is a delay for a visitor on a phone and a weaker signal to Google.",
        "Point links straight at the final address instead of through the chain.",
        "A fresh read reaches the page in at most one redirect."),
    "missing_title": (
        "medium", "observed",
        "Search results and the browser tab show the address instead of what the page offers.",
        "Write one title naming the service and the emirate.",
        "A fresh read finds exactly one non-empty <title>."),
    "multiple_titles": (
        "medium", "observed",
        "Two titles make it unclear which one names the page.",
        "Keep one <title> in the head and remove the other.",
        "A fresh read finds exactly one <title>."),
    "missing_description": (
        "low", "observed",
        "A page-specific summary is what someone reads under the link before deciding to click.",
        "Add a one-sentence description of this page's service and area.",
        "A fresh read finds one non-empty meta description. Google may still choose its own snippet."),
    "multiple_meta_descriptions": (
        "medium", "observed",
        "Two descriptions contradict each other; neither can be relied on.",
        "Keep one meta description and remove the other — often a template default.",
        "A fresh read finds exactly one meta description."),
    "missing_h1": (
        "medium", "observed",
        "A visitor needs one visible heading that says what this page is for.",
        "Give the page one main heading matching the customer's question.",
        "A fresh read finds at least one H1 in the visible page."),
    "multiple_h1_review": (
        "low", "review",
        "Several H1s can blur what the page is about. It is not a defect by itself.",
        "Check the headings read as one main heading and its sub-headings.",
        "The heading structure reads correctly on a fresh read."),
    "canonical_not_declared": (
        "low", "review",
        "Without a canonical, Google picks between address variants on its own.",
        "Add a self-referencing canonical where two addresses show the same page.",
        "A fresh read finds one canonical pointing at this page's preferred address."),
    "multiple_canonicals": (
        "medium", "observed",
        "Conflicting canonicals cancel each other out.",
        "Keep one canonical link in the head.",
        "A fresh read finds exactly one canonical."),
    "canonical_elsewhere": (
        "info", "review",
        "This page tells Google a different address is the real one.",
        "Confirm that is intended; if not, point the canonical at this page.",
        "The canonical matches the page's own preferred address, or the consolidation is intended."),
    "invalid_canonical_url": (
        "medium", "observed",
        "A canonical that is not a usable address is ignored.",
        "Replace it with the page's full https address.",
        "A fresh read finds a canonical that is a valid absolute URL."),
    "jsonld_syntax_error": (
        "medium", "observed",
        "Structured data that does not parse tells Google nothing — the business details are lost.",
        "Fix the JSON so every ld+json block parses.",
        "Every ld+json block on a fresh read parses without error."),
    "jsonld_duplicate_block": (
        "low", "review",
        "The same structured data twice suggests a block pasted in two places.",
        "Keep one copy of each block.",
        "A fresh read finds each ld+json block once."),
    "missing_image_alt": (
        "low", "observed",
        "Images of finished work with no alt text say nothing to Google Images or a screen reader.",
        "Describe each image in a few plain words: what was installed, and where.",
        "Every <img> on a fresh read has an alt attribute."),
    "image_dimensions_review": (
        "low", "review",
        "Images without width and height make the page jump as it loads on a phone.",
        "Add width and height to the images listed.",
        "The listed images carry width and height on a fresh read."),
    "image_loading_conflict": (
        "low", "review",
        "An image marked both lazy and high-priority asks the browser for two opposite things.",
        "Drop loading=lazy from the image that should appear first.",
        "No image on a fresh read is both lazy and fetchpriority=high."),
    "noindex_observed": (
        "info", "review",
        "This page asks Google not to list it. On a service page that means no search visitors at all.",
        "Confirm that is intended. If not, remove the noindex.",
        "No noindex on a fresh read, or it is confirmed intended."),
    "possible_js_shell": (
        "medium", "heuristic",
        "Very little text arrives in the HTML itself; what Google reads first may be nearly empty.",
        "Check the page's main text is in the HTML the server sends, not added later by script.",
        "A fresh read finds the page's main text in the server's HTML."),
    "missing_content_candidate": (
        "high", "heuristic",
        "The page looks like a 'not found' screen but answers 200 — a soft 404 that wastes a crawl and a visitor.",
        "Restore the content, or make the address return a real 404 and fix what links to it.",
        "The address returns real content, or a real 404."),
    "two_hosts_serve_the_site": (
        "medium", "observed",
        "Every page answers at two addresses — with and without www — so Google sees two copies of the whole site and has to guess which to rank.",
        "Choose one address. Redirect the other to it with a single 301 at the server, and make every canonical name the one you chose. Your canonicals currently name one host and your saved address the other, so this is your decision.",
        "Fetching the other host returns one 301 to the chosen host, and canonicals name the chosen host."),
    "canonical_target_redirects": (
        "medium", "observed",
        "The canonicals name an address that itself redirects, so the signal passes through a hop instead of landing on the page.",
        "Point every canonical at the address visitors actually end up on.",
        "The canonical address returns 200 directly on a fresh read."),
    "duplicate_title": (
        "medium", "observed",
        "Pages sharing one title compete with each other and look identical in search results.",
        "Give each page a title naming its own service or area.",
        "Each listed page has its own title on a fresh read."),
    "duplicate_meta_description": (
        "low", "observed",
        "Pages sharing one description look the same in search results, so neither stands out.",
        "Write a description for each page about that page — often a template default is to blame.",
        "Each listed page has its own description on a fresh read."),
    "broken_internal_link": (
        "high", "observed",
        "A link on his own site leads a visitor to an error.",
        "Fix or remove the link on the pages listed, or restore the page it points to.",
        "The target returns 200, or no page links to it."),
}

# Findings that claim something is NOT there. BeyondSEO's ABSENCE set: never
# filed from a capture that could not prove it.
ABSENCE = {"missing_title", "missing_description", "missing_h1",
           "canonical_not_declared", "missing_image_alt",
           "missing_content_candidate"}

CLAIM_TYPE = {"observed": "verified fact", "heuristic": "hypothesis",
              "review": "inference"}
PRIORITY = {"high": "P1", "medium": "P2", "low": "P3", "info": "review"}
# site_issues speaks critical/high/medium/low; 'info' is not a severity there.
TO_ISSUE_SEVERITY = {"high": "high", "medium": "medium", "low": "low", "info": "low"}


# ---------------------------------------------------------------------------
# READING A PAGE
# ---------------------------------------------------------------------------

_TAG = re.compile(r"<[^>]+>")
_SCRIPT = re.compile(r"<(script|style|noscript|template)\b.*?</\1\s*>", re.I | re.S)


_ATTR = re.compile(r'([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*(?:=\s*("[^"]*"|\'[^\']*\'|[^\s>]+))?')


def _attrs(tag):
    """Attributes of one tag as a dict, lower-cased names, unescaped values."""
    out = {}
    inner = re.sub(r"^<\s*[a-zA-Z0-9]+", "", tag).rstrip(">").rstrip("/")
    for m in _ATTR.finditer(inner):
        name = m.group(1).lower()
        val = m.group(2) or ""
        if val[:1] in "\"'":
            val = val[1:-1]
        out[name] = _html.unescape(val)
    return out


def _tags(html, name):
    return [(m.group(0), _attrs(m.group(0)))
            for m in re.finditer(rf"<{name}\b[^>]*>", html, re.I)]


def _text(s):
    return re.sub(r"\s+", " ", _html.unescape(_TAG.sub(" ", s or ""))).strip()


def _without_svg(html):
    # BeyondSEO's rule: an SVG <title> is an image label, not the document title.
    return re.sub(r"<svg\b.*?</svg\s*>", " ", html, flags=re.I | re.S)


def extract(html, url) -> dict:
    """Everything the checks need out of one page's HTML. Never raises."""
    html = html or ""
    head_html = _without_svg(html)
    titles = [_text(t) for t in re.findall(r"<title\b[^>]*>(.*?)</title\s*>",
                                            head_html, re.I | re.S)]
    metas = _tags(html, "meta")
    descriptions = [a.get("content", "").strip() for _, a in metas
                    if a.get("name", "").lower() == "description"]
    robots = " ".join(a.get("content", "") for _, a in metas
                      if a.get("name", "").lower() in ("robots", "googlebot")).lower()
    canon = [a.get("href", "").strip() for _, a in _tags(html, "link")
             if "canonical" in a.get("rel", "").lower().split()]
    body = _SCRIPT.sub(" ", html)
    h1s = [_text(h) for h in re.findall(r"<h1\b[^>]*>(.*?)</h1\s*>", body, re.I | re.S)]
    h1s = [h for h in h1s if h]
    images = []
    for tag, a in _tags(html, "img"):
        images.append({"src": a.get("src", "")[:200],
                       "alt_present": "alt" in a,
                       "width": a.get("width"), "height": a.get("height"),
                       "loading": a.get("loading", "").lower(),
                       "fetchpriority": a.get("fetchpriority", "").lower()})
    blocks, errors = [], []
    for raw in re.findall(r'<script\b[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script\s*>',
                          html, re.I | re.S):
        raw = raw.strip()
        try:
            blocks.append(json.loads(raw))
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {str(exc)[:120]}")
    base_host = urllib.parse.urlparse(url).netloc.lower()
    links = []
    for _, a in _tags(html, "a"):
        href = (a.get("href") or "").strip()
        if not href or href.startswith(("#", "mailto:", "tel:", "javascript:", "whatsapp:")):
            continue
        full = urllib.parse.urljoin(url, href)
        p = urllib.parse.urlparse(full)
        if p.scheme in ("http", "https") and p.netloc.lower() in (base_host,
                                                                  _other_www(base_host)):
            links.append(urllib.parse.urlunparse(p._replace(fragment="")))
    words = _text(body)
    return {"titles": [t for t in titles if t], "title": next((t for t in titles if t), ""),
            "descriptions": [d for d in descriptions if d],
            "robots": robots, "canonicals": [c for c in canon if c],
            "h1": h1s, "images": images, "jsonld": blocks, "jsonld_errors": errors,
            "jsonld_raw_count": len(blocks) + len(errors),
            "links": sorted(set(links)), "word_count": len(words.split()),
            "script_count": len(re.findall(r"<script\b", html, re.I)),
            "main_text": words[:2000]}


def _other_www(host):
    return host[4:] if host.startswith("www.") else "www." + host


def capture(url, timeout=FETCH_TIMEOUT, session=None) -> dict:
    """
    One page, exactly as the server sends it: status, the redirect hops, the
    headers and the body. The evidence every finding points back to.
    """
    s = session or requests
    page = {"url": url, "fetched_at": int(time.time())}
    try:
        r = s.get(url, timeout=timeout, allow_redirects=True,
                  headers={"User-Agent": UA, "Accept": "text/html,*/*;q=0.8"})
    except Exception as exc:
        page["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        return page
    body = r.text or ""
    page.update({
        "status": r.status_code, "final_url": r.url,
        "redirects": [h.url for h in r.history] + ([r.url] if r.history else []),
        "x_robots": (r.headers.get("X-Robots-Tag") or "").lower(),
        "content_type": r.headers.get("Content-Type", ""),
        "sha256": hashlib.sha256(body.encode("utf-8", "ignore")).hexdigest()[:16],
    })
    if "html" in page["content_type"].lower() or body.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
        page["data"] = extract(body, r.url)
        page["html"] = body
    return page


def capture_quality(page) -> dict:
    """
    BeyondSEO's rule, whole: positive observations can survive a partial
    capture; absence claims cannot.
    """
    data = page.get("data") or {}
    usable = bool(data) and not page.get("error") and 200 <= page.get("status", 0) < 300
    limits = []
    if not usable:
        limits.append("no successful, usable capture of this page")
    if usable and data.get("script_count") and data.get("word_count", 0) < 80:
        limits.append("possible JavaScript shell — the server's HTML carries almost no text")
    return {"usable": usable, "absence_supported": usable and not limits,
            "limits": limits}


_NOT_FOUND = r"(?:page|story|article|content)\s+(?:was\s+)?not\s+found"


def looks_not_found(data) -> bool:
    """BeyondSEO's conservative soft-404 screen: English wording, short page."""
    main, title = data.get("main_text", ""), data.get("title", "")
    if re.fullmatch(_NOT_FOUND + r"[.!]?", main, re.I):
        return True
    heading = any(re.fullmatch(r"404|" + _NOT_FOUND, h.strip(), re.I) for h in data.get("h1", []))
    in_title = bool(re.match(r"^(?:404\b|" + _NOT_FOUND + r"\b)", title, re.I))
    return (heading or in_title) and bool(re.search(_NOT_FOUND, main, re.I)) \
        and len(main.split()) <= 100


def _path_key(u):
    """An address without its host — www and non-www are the same key."""
    p = urllib.parse.urlparse(u or "")
    return (p.path or "/").rstrip("/") or "/"


_INDEX_FILE = re.compile(r"/(?:index|default|home)\.(?:php|html?|aspx?)$", re.I)


def _index_of(path, canonical_path) -> bool:
    """
    /index.php pointing its canonical at / is the textbook consolidation of an
    index file onto its folder — the right answer, not something to "review".
    Filing it would ask him to confirm the one thing on the page done properly.
    """
    m = _INDEX_FILE.search(path or "")
    if not m:
        return False
    folder = (path[:m.start()] or "/").rstrip("/") or "/"
    return (canonical_path or "/") == folder


def _identity(page):
    """
    Which page this really is: where its canonical points, or itself.

    / and /index.php are one page at two addresses, and a canonical from one
    to the other says so. Counting them as two pages "sharing a title" would
    file a ticket to give the same page two different names.
    """
    d = page.get("data") or {}
    base = page.get("final_url") or page["url"]
    target = urllib.parse.urljoin(base, d["canonicals"][0]) if d.get("canonicals") else base
    return _path_key(target)


def template_images(pages) -> frozenset:
    """Images on two or more pages: the header, the footer, the logo."""
    counts = {}
    for p in pages:
        if capture_quality(p)["usable"]:
            for src in {i["src"] for i in p["data"]["images"] if i["src"]}:
                counts[src] = counts.get(src, 0) + 1
    return frozenset(s for s, n in counts.items() if n >= 2)


def _norm(u):
    p = urllib.parse.urlparse(u or "")
    return f"{p.scheme}://{p.netloc.lower()}{(p.path or '/').rstrip('/') or '/'}"


# ---------------------------------------------------------------------------
# THE CHECKS, PER PAGE AND ACROSS THE SITE
# ---------------------------------------------------------------------------

def page_findings(page, template_srcs=frozenset()) -> list:
    """
    Every check that one page can answer on its own.

    Two things are left to the site-wide pass on purpose. A canonical that
    differs from the page only by www is one decision about the whole site, not
    fifteen findings. And an image that appears on several pages is the header
    or footer — one template fix, not a ticket per page.
    """
    out = []
    url = page["url"]
    quality = capture_quality(page)

    def add(code, evidence, urls=None):
        if code in ABSENCE and not quality["absence_supported"]:
            return                      # absence is earned, never assumed
        out.append({"code": code, "url": url, "affected_urls": urls or [url],
                    "evidence": evidence, "limits": list(quality["limits"])})

    if page.get("error"):
        add("fetch_failed", page["error"])
        return out
    status = page.get("status", 0)
    if status >= 400:
        add("http_error", f"HTTP {status}")
        return out
    if len(page.get("redirects") or []) > 2:          # start + two or more hops
        add("redirect_chain", " -> ".join(page["redirects"]))
    d = page.get("data")
    if not d:
        return out
    if quality["limits"] and quality["usable"]:
        add("possible_js_shell",
            f"{d['word_count']} words of text in the server's HTML, {d['script_count']} scripts")
    if looks_not_found(d):
        add("missing_content_candidate",
            f"title {d['title']!r}; page reads: {d['main_text'][:160]!r}")
        return out
    if not d["title"]:
        add("missing_title", "no non-empty <title> in the head")
    if len(d["titles"]) > 1:
        add("multiple_titles", json.dumps(d["titles"], ensure_ascii=False))
    if not d["descriptions"]:
        add("missing_description", "no non-empty meta description")
    if len(d["descriptions"]) > 1:
        add("multiple_meta_descriptions", json.dumps(d["descriptions"], ensure_ascii=False)[:300])
    if not d["h1"]:
        add("missing_h1", "no H1 in the visible page")
    if len(d["h1"]) > 1:
        add("multiple_h1_review", json.dumps(d["h1"], ensure_ascii=False)[:300])
    if not d["canonicals"]:
        add("canonical_not_declared", "no <link rel=canonical>")
    if len(d["canonicals"]) > 1:
        add("multiple_canonicals", json.dumps(d["canonicals"]))
    for c in d["canonicals"][:1]:
        full = urllib.parse.urljoin(page.get("final_url") or url, c)
        p = urllib.parse.urlparse(full)
        if p.scheme not in ("http", "https") or not p.netloc:
            add("invalid_canonical_url", c)
        elif _norm(full) != _norm(page.get("final_url") or url):
            here = _path_key(page.get("final_url") or url)
            if _path_key(full) != here and not _index_of(here, _path_key(full)):
                add("canonical_elsewhere", f"canonical is {full}")
    if d["jsonld_errors"]:
        add("jsonld_syntax_error", "; ".join(d["jsonld_errors"][:3]))
    seen = set()
    for b in d["jsonld"]:
        fp = hashlib.sha1(json.dumps(b, sort_keys=True).encode()).hexdigest()
        if fp in seen:
            add("jsonld_duplicate_block", f"a block of type {_types_of(b)} appears twice")
            break
        seen.add(fp)
    own = [i for i in d["images"] if i["src"] not in template_srcs]
    no_alt = [i["src"] for i in own if not i["alt_present"]]
    if no_alt:
        add("missing_image_alt", f"{len(no_alt)} image(s) with no alt: " + ", ".join(no_alt[:5]))
    if quality["absence_supported"]:
        no_dims = [i["src"] for i in own if not (i["width"] and i["height"])]
        if no_dims:
            add("image_dimensions_review",
                f"{len(no_dims)} image(s) with no width/height: " + ", ".join(no_dims[:5]))
    clash = [i["src"] for i in d["images"]
             if i["loading"] == "lazy" and i["fetchpriority"] == "high"]
    if clash:
        add("image_loading_conflict", ", ".join(clash[:5]))
    if "noindex" in d["robots"] or "noindex" in (page.get("x_robots") or ""):
        add("noindex_observed", "meta robots: " + (d["robots"] or "-")
            + "; X-Robots-Tag: " + (page.get("x_robots") or "-"))
    return out


def _types_of(block):
    t = block.get("@type") if isinstance(block, dict) else None
    return ", ".join(t) if isinstance(t, list) else (t or "unknown")


def site_findings(pages, link_status, hosts=None) -> list:
    """The checks no single page can answer: duplicates, hosts, templates, links."""
    out = []
    readable = [p for p in pages if capture_quality(p)["usable"]]

    def dupes(field, code):
        groups = {}
        for p in readable:
            val = (p["data"].get(field) or "")
            val = val[0] if isinstance(val, list) and val else val
            if isinstance(val, str) and val.strip():
                groups.setdefault(val.strip().lower(), {}).setdefault(
                    _identity(p), []).append(p["url"])
        for val, by_page in groups.items():
            if len(by_page) > 1:                 # distinct PAGES, not addresses
                urls = [u for us in by_page.values() for u in us[:1]]
                out.append({"code": code, "url": urls[0], "affected_urls": urls,
                            "evidence": f"{len(by_page)} different pages share {val[:120]!r}",
                            "limits": []})

    dupes("title", "duplicate_title")
    dupes("descriptions", "duplicate_meta_description")

    # ONE DECISION ABOUT THE WHOLE SITE, NOT A FINDING PER PAGE.
    canon_hosts = {}
    for p in readable:
        c = p["data"].get("canonicals") or []
        if c:
            full = urllib.parse.urljoin(p.get("final_url") or p["url"], c[0])
            canon_hosts[urllib.parse.urlparse(full).netloc.lower()] = \
                canon_hosts.get(urllib.parse.urlparse(full).netloc.lower(), 0) + 1
    served = urllib.parse.urlparse(readable[0].get("final_url") or readable[0]["url"]).netloc.lower() \
        if readable else ""
    other = max(canon_hosts, key=canon_hosts.get) if canon_hosts else ""
    h = hosts or {}
    if served and other and other != served and other == _other_www(served):
        named = canon_hosts[other]
        o = h.get(other) or {}
        if o.get("status") == 200 and not o.get("hops") and \
                (h.get(served) or {}).get("status") == 200 and not (h.get(served) or {}).get("hops"):
            out.append({"code": "two_hosts_serve_the_site", "url": f"https://{served}/",
                        "affected_urls": [f"https://{served}/", f"https://{other}/"],
                        "evidence": (f"https://{served}/ and https://{other}/ both return 200 with "
                                     f"no redirect; {named} page(s) name {other} as canonical"),
                        "limits": []})
        elif o.get("hops"):
            out.append({"code": "canonical_target_redirects", "url": f"https://{other}/",
                        "affected_urls": [f"https://{other}/"],
                        "evidence": (f"{named} canonical(s) name {other}, which redirects to "
                                     f"{o.get('final')}"),
                        "limits": []})

    # THE TEMPLATE, ONCE.
    tmpl = template_images(pages)
    if tmpl:
        seen = {}
        for p in readable:
            for i in p["data"]["images"]:
                if i["src"] in tmpl:
                    seen.setdefault(i["src"], i)
        no_alt = [s for s, i in seen.items() if not i["alt_present"]]
        no_dims = [s for s, i in seen.items() if not (i["width"] and i["height"])]
        home = readable[0]["url"] if readable else ""
        if no_alt:
            out.append({"code": "missing_image_alt", "url": home, "affected_urls": [home],
                        "evidence": (f"{len(no_alt)} image(s) repeated across pages (header/footer) "
                                     "with no alt: " + ", ".join(no_alt[:5])), "limits": []})
        if no_dims:
            out.append({"code": "image_dimensions_review", "url": home, "affected_urls": [home],
                        "evidence": (f"{len(no_dims)} image(s) repeated across pages (header/footer) "
                                     "with no width/height — one template fix: "
                                     + ", ".join(no_dims[:5])), "limits": []})

    linked_from = {}
    for p in readable:
        for target in p["data"]["links"]:
            linked_from.setdefault(_norm(target), set()).add(p["url"])
    for target, status in link_status.items():
        if status and status >= 400 and target in linked_from:
            sources = sorted(linked_from[target])
            out.append({"code": "broken_internal_link", "url": target,
                        "affected_urls": [target] + sources,
                        "evidence": f"HTTP {status}; linked from " + ", ".join(sources[:5]),
                        "limits": []})
    return out


def contract(f, captured_at) -> dict:
    """
    BeyondSEO's finding contract. Every field is something the owner, IT or
    the verifier needs; none of them is decoration.
    """
    sev, conf, why, action, accept = CHECKS[f["code"]]
    limits = list(f.get("limits") or [])
    limits.append("Observed in the HTML the server sent; rankings, indexing and "
                  "AI citations were not measured.")
    return {"code": f["code"], "url": f["url"], "affected_urls": f["affected_urls"],
            "severity": sev, "priority": PRIORITY[sev],
            "claim_type": CLAIM_TYPE[conf], "observation": f["evidence"],
            "captured_at": captured_at, "why_it_matters": why,
            "action": action, "acceptance_check": accept, "uncertainty": limits}


# ---------------------------------------------------------------------------
# THE CRAWL
# ---------------------------------------------------------------------------

def _robots(base, session):
    rp = urllib.robotparser.RobotFileParser()
    try:
        r = session.get(base.rstrip("/") + "/robots.txt", timeout=10,
                        headers={"User-Agent": UA})
        rp.parse((r.text or "").splitlines() if r.status_code == 200 else [])
    except Exception:
        rp.parse([])
    return rp


def crawl(base, max_pages=MAX_PAGES, deadline=None, session=None) -> dict:
    """
    Read his priority pages, then check the internal links they point at.

    Page choice is seo_answers.pages_to_audit — the sitemap he publishes, then
    links off the home page, shortest paths first — so both audits look at the
    same pages and a finding in one is never about a page the other never read.
    """
    from . import seo_answers
    s = session or requests.Session()
    rp = _robots(base, s)
    home = capture(base, session=s)
    wanted = seo_answers.pages_to_audit(base, home.get("html") or "",
                                        max_pages=max_pages)
    pages, skipped, blocked = [home], [], []
    for url in wanted:
        if _norm(url) == _norm(base):
            continue
        if deadline and time.time() >= deadline:
            skipped.append(url)
            continue
        if not rp.can_fetch(UA, url):
            blocked.append(url)
            continue
        pages.append(capture(url, session=s))

    read = {_norm(p.get("final_url") or p["url"]): p.get("status") for p in pages}
    read.update({_norm(p["url"]): p.get("status") for p in pages})
    link_status, targets = {}, set()
    for p in pages:
        for t in (p.get("data") or {}).get("links", []):
            targets.add(_norm(t))
    extra = 0
    for t in sorted(targets):
        if t in read:
            link_status[t] = read[t]
            continue
        if extra >= MAX_LINK_CHECKS:
            break
        extra += 1
        if (deadline and time.time() >= deadline) or not rp.can_fetch(UA, t):
            continue
        try:
            r = s.head(t, timeout=10, allow_redirects=True, headers={"User-Agent": UA})
            if r.status_code in (405, 501):          # servers that refuse HEAD
                r = s.get(t, timeout=10, allow_redirects=True, headers={"User-Agent": UA})
            link_status[t] = r.status_code
        except Exception:
            link_status[t] = None                    # unknown is not broken
    hosts = {}
    host = urllib.parse.urlparse(base).netloc.lower()
    for h in (host, _other_www(host)):
        try:
            r = s.get(f"https://{h}/", timeout=15, allow_redirects=True,
                      headers={"User-Agent": UA})
            hosts[h] = {"status": r.status_code, "hops": len(r.history), "final": r.url}
        except Exception as exc:
            hosts[h] = {"status": None, "hops": 0, "final": "",
                        "error": type(exc).__name__}
    return {"pages": pages, "link_status": link_status, "skipped": skipped,
            "blocked_by_robots": blocked, "hosts": hosts}


# ---------------------------------------------------------------------------
# FILING, AND VERIFYING WHAT WAS FILED BEFORE
# ---------------------------------------------------------------------------

def _ensure_table(con):
    con.execute("""CREATE TABLE IF NOT EXISTS seo_findings (
        id INTEGER PRIMARY KEY AUTOINCREMENT, run_at INTEGER, url TEXT,
        code TEXT, severity TEXT, claim_type TEXT, issue_id INTEGER,
        payload TEXT)""")


def ticket_text(c) -> str:
    """What IT and the owner read on the ticket. Plain, and complete."""
    more = [u for u in c["affected_urls"] if u != c["url"]]
    return (MARK.format(code=c["code"]) + " "
            + c["observation"]
            + (f" (also: {', '.join(more[:4])})" if more else "")
            + f". Why it matters: {c['why_it_matters']}"
            + f" Do: {c['action']}"
            + f" Fixed when: {c['acceptance_check']}"
            + f" [{c['claim_type']}, {c['priority']}]")


def file_findings(con, findings, now) -> list:
    """
    New findings become tickets for IT; one already open is not filed twice.
    Returns [(issue_id or None, contract)] — None for one already on file.
    """
    _ensure_table(con)
    open_rows = con.execute(
        "SELECT id, url, detail FROM site_issues WHERE status IN "
        "('open','fixing','in_review','deployed') AND detail LIKE '[seo-audit:%'").fetchall()
    on_file = {(r[1], (MARK_RE.search(r[2] or "") or [None, None])[1]) for r in open_rows}
    out = []
    for c in findings:
        key = (c["url"], c["code"])
        iid = None
        if key not in on_file:
            cur = con.execute(
                "INSERT INTO site_issues(kind,severity,detail,url,status,found_by,found_at)"
                " VALUES('seo',?,?,?,'open',?,?)",
                (TO_ISSUE_SEVERITY[c["severity"]], ticket_text(c), c["url"], AGENT, now))
            iid = cur.lastrowid
            on_file.add(key)
        con.execute("INSERT INTO seo_findings(run_at,url,code,severity,claim_type,"
                    "issue_id,payload) VALUES(?,?,?,?,?,?,?)",
                    (now, c["url"], c["code"], c["severity"], c["claim_type"], iid,
                     json.dumps(c, ensure_ascii=False)))
        out.append((iid, c))
    return out


def verify(con, pages, found_now, now, link_status=None) -> dict:
    """
    BeyondSEO: making a change and verifying it are separate steps.

    Every open ticket this audit filed is checked against today's capture of
    its page. Only a page read cleanly TODAY can close or reopen anything —
    and an absence finding ("no H1") can only be judged gone if today's
    capture was good enough to have seen it.
    """
    captured = {}
    for p in pages:
        for u in {p["url"], p.get("final_url") or p["url"]}:
            captured[u] = p
    still = {(f["url"], f["code"]) for f in found_now}
    closed, reopened = [], []
    rows = con.execute(
        "SELECT id, url, detail, status FROM site_issues WHERE detail LIKE '[seo-audit:%'"
        " AND status IN ('open','fixing','in_review','deployed')").fetchall()
    stamp = time.strftime("%Y-%m-%d", time.localtime(now))
    for iid, url, detail, status in rows:
        m = MARK_RE.search(detail or "")
        if not m:
            continue
        code = m.group(1)
        page = captured.get(url)
        if code == "broken_internal_link":
            # Judged on today's check of that target. Not looked at today is
            # not the same as fixed.
            seen_today = (link_status or {}).get(_norm(url))
            if (url, code) not in still and seen_today is not None:
                closed.append(iid)
            continue
        if code in ("duplicate_title", "duplicate_meta_description",
                    "two_hosts_serve_the_site", "canonical_target_redirects"):
            # Site-wide: judged only when the page it was filed against was
            # read cleanly today.
            if (url, code) not in still and page is not None \
                    and capture_quality(page)["usable"]:
                closed.append(iid)
            continue
        if page is None:
            continue                       # not read today: nothing to say
        q = capture_quality(page)
        if code in ABSENCE and not q["absence_supported"]:
            continue                       # cannot see it, so cannot say it is gone
        if not q["usable"] and code not in ("fetch_failed", "http_error"):
            continue
        if (url, code) not in still:
            closed.append(iid)
        elif status == "deployed":
            reopened.append(iid)
    for iid in closed:
        con.execute("UPDATE site_issues SET status='closed', detail=detail || ? WHERE id=?",
                    (f" — VERIFIED FIXED {stamp}: the acceptance check passed on a fresh read.", iid))
    for iid in reopened:
        con.execute("UPDATE site_issues SET status='open', detail=detail || ? WHERE id=?",
                    (f" — STILL THERE {stamp}: marked deployed, but a fresh read still shows it.", iid))
    return {"verified_fixed": closed, "reopened": reopened}


# ---------------------------------------------------------------------------
# THE PASS
# ---------------------------------------------------------------------------

def step_budget() -> int:
    """80% of this step's ceiling in the day run, read from the runner."""
    try:
        from . import day_run
        return max(60, int(day_run.step_timeout_for("pipelines.seo_audit") * 0.8))
    except Exception:
        return 300


def run(agent=AGENT, url=None, max_pages=MAX_PAGES, dry_run=False, session=None) -> dict:
    with board.agent_run(agent) as run_row:
        out = _audit(agent, url, max_pages, dry_run, session)
        run_row.note(out.get("note") or out.get("error") or "audited")
        return out


def _audit(agent, url, max_pages, dry_run, session) -> dict:
    if not board.sector_enabled(SECTOR):
        return {"ok": False, "idle": True, "error": "SEO is switched off."}
    base = (url or site_cfg.site_url() or "").strip().rstrip("/")
    if not base:
        return {"ok": False, "idle": True,
                "error": "No website saved, so there is nothing to audit. Save it on /setup."}
    started = time.time()
    got = crawl(base, max_pages=max_pages, deadline=started + step_budget() - 20,
                session=session)
    pages, now = got["pages"], int(time.time())
    raw, tmpl = [], template_images(pages)
    for p in pages:
        raw += page_findings(p, tmpl)
    raw += site_findings(pages, got["link_status"], got.get("hosts"))
    findings = [contract(f, now) for f in raw]
    findings.sort(key=lambda c: (c["priority"], c["code"], c["url"]))

    readable = sum(1 for p in pages if capture_quality(p)["usable"])
    result = {"ok": True, "agent": agent, "site": base,
              "pages_read": readable, "pages_attempted": len(pages),
              "links_checked": len(got["link_status"]),
              "not_reached": got["skipped"], "blocked_by_robots": got["blocked_by_robots"],
              "findings": findings}
    if not readable:
        result.update({"ok": False, "error": f"Could not read any page of {base}; "
                                             "nothing is filed from a site that did not answer."})
        return result

    by_p = {}
    for c in findings:
        by_p[c["priority"]] = by_p.get(c["priority"], 0) + 1
    if dry_run:
        result["note"] = (f"read {readable} page(s), {len(findings)} finding(s) "
                          f"(dry run — nothing filed)")
        return result

    con = sqlite3.connect(board.DB, timeout=30)
    try:
        checked = verify(con, pages, findings, now, got["link_status"])
        filed = file_findings(con, findings, now)
        con.commit()
    finally:
        con.close()
    new = [c for iid, c in filed if iid]
    result.update({"filed_new": len(new),
                   "already_on_file": len(filed) - len(new),
                   "verified_fixed": len(checked["verified_fixed"]),
                   "reopened": len(checked["reopened"]),
                   "by_priority": by_p})
    bits = [f"read {readable} page(s)",
            f"{len(findings)} finding(s)" + (f" ({', '.join(f'{v} {k}' for k, v in sorted(by_p.items()))})"
                                            if by_p else ""),
            f"{len(new)} new ticket(s) for IT"]
    if checked["verified_fixed"]:
        bits.append(f"{len(checked['verified_fixed'])} earlier fix(es) verified")
    if checked["reopened"]:
        bits.append(f"{len(checked['reopened'])} 'deployed' fix(es) did not take and were reopened")
    result["note"] = "; ".join(bits)
    try:
        board.log_decision(
            "seo-audit",
            reasoning=(f"Technical audit of {readable} page(s) of {base}: "
                       f"{len(findings)} finding(s), {len(new)} new ticket(s)."),
            expectation="IT fixes the P1 and P2 findings; the next audit verifies them.",
            confidence=0.7,
            evidence={"codes": sorted({c['code'] for c in findings}),
                      "new_tickets": [iid for iid, _ in filed if iid]})
    except Exception:
        pass
    return result


def as_text(r) -> str:
    if not r.get("ok"):
        return "SEO AUDIT — " + (r.get("error") or "did not run")
    lines = ["SEO AUDIT — " + (r.get("note") or ""), ""]
    for c in r.get("findings", [])[:25]:
        lines.append(f"  {c['priority']:<6} {c['code']:<28} {c['url']}")
        lines.append(f"         {c['observation'][:110]}")
    if r.get("not_reached"):
        lines.append(f"\n  {len(r['not_reached'])} page(s) not reached this pass — next run.")
    return "\n".join(lines)


def main(argv=None):
    from coordination import deskwork
    ap = argparse.ArgumentParser(description="Technical SEO audit of the owner's site.")
    ap.add_argument("--url", default="")
    ap.add_argument("--max-pages", type=int, default=MAX_PAGES)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    r = run(url=a.url or None, max_pages=a.max_pages, dry_run=a.dry_run)
    print(json.dumps(r, indent=2, ensure_ascii=False, default=str) if a.json else as_text(r))
    return deskwork.exit_code(r)


if __name__ == "__main__":
    raise SystemExit(main())
