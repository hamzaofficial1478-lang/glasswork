"""
website_check.py — a report on any website: every desk's view of it, scored,
with the fixes that matter most and the evidence for each.

    python -m pipelines.website_check --url www.example.com            quick check, printed
    python -m pipelines.website_check --url www.example.com --full     up to 200 pages
    python -m pipelines.website_check --list --json
    python -m pipelines.website_check --get 7 --json
    python -m pipelines.website_check --html 7                         the report, as a page
    python -m pipelines.website_check --pdf 7 report.pdf               and as a PDF

"make a report maker ... any other website ... analyze it by its all sector"
— docs/PUBLIC-LAUNCH-PLAN.md, idea 6.

SIX DESKS, ONE REPORT

  Speed and health       the server's answer time, phones, broken links and
                         pages, the security certificate
  Found on Google        titles, descriptions, headings, canonicals, sitemap
                         and robots.txt, pages Google is told not to list
  Words and trust        thin and repeated pages, how fresh it looks, reviews,
                         real work, an address
  Picked by AI answers   whether ChatGPT, Gemini and Google's answers would pick
                         and quote it: questions answered, business details for
                         machines, and AI assistants not shut out
  Easy to get in touch   a number you can tap, a form, WhatsApp, a contact page,
                         a clear next step
  Security, from outside what any visitor's browser receives: HTTPS, security
                         headers, mixed content, what the site gives away

It reuses the agency's own checks — the technical audit (pipelines/seo_audit.py)
and the answer-engine audit (pipelines/seo_answers.py) — pointed at someone
else's site, with the wording made general: their fix texts were written for
one glass business in the UAE.

THE RULES IT KEEPS

  - It never changes the site. Every request is a GET or a HEAD; no form is
    ever sent.
  - It reads politely: robots.txt is followed (its Crawl-delay too), one
    request a second at most, and a fixed number of pages.
  - On a site the user does not own, security stays PASSIVE. It looks only at
    what any visitor's browser receives, and never asks for hidden files or
    tests for ways in. Scanning someone else's website for weaknesses without
    permission is illegal in many countries, including the UAE. That is why
    pipelines/site_secure.py — which does ask for hidden files, on the owner's
    own site — is not used here.
  - It will not read addresses on this computer or its local network, so the
    tool cannot be pointed inwards.

SCORES

Each desk starts at 100 and loses points for what it found: a lot for what
matters a lot, little for what is merely worth doing, and a problem found on
one page of twenty costs less than one found on every page. All six desks are
scored the same way, so their numbers can be compared. The overall score is
the average of the six. A score is a summary, not a verdict; every point lost is a finding in
the report with the page it was found on.
"""
import argparse
import base64
import datetime as _dt
import hashlib
import ipaddress
import json
import os
import re
import shutil
import socket
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.robotparser
from pathlib import Path

import requests

from coordination import board, workspace

UA = "BotixWebsiteCheck/1.0 (read-only report; follows robots.txt)"
QUICK_PAGES, FULL_PAGES = 20, 200
QUICK_LINKS, FULL_LINKS = 40, 150
QUICK_MINUTES, FULL_MINUTES = 8, 45
GAP = 1.0                      # seconds between requests, at least
MAX_CRAWL_DELAY = 10           # a robots.txt Crawl-delay is honoured up to this
TIMEOUT = 20
BRAND_FILE = "report-brand.local.json"

SECTORS = (("it", "Speed and health"), ("seo", "Found on Google"), ("content", "Words and trust"),
           ("ai", "Picked by AI answers"), ("leads", "Easy to get in touch"),
           ("security", "Security, from outside"))
SECTOR_NAME = dict(SECTORS)
WEIGHT = {"high": 22, "medium": 10, "low": 4}
IMPACT = {"high": "Matters a lot", "medium": "Matters", "low": "Worth doing"}
EFFORT = {1: "10 minutes", 2: "An hour", 3: "Half a day", 4: "A day or more"}

_TAG = re.compile(r"<[^>]+>")
_SCRIPTISH = re.compile(r"<(script|style|noscript|template)\b.*?</\1\s*>", re.I | re.S)


class NotAllowed(ValueError):
    """An address the check will not read."""


# ---------------------------------------------------------------------------
# reading politely
# ---------------------------------------------------------------------------

class PoliteSession(requests.Session):
    """A requests session that waits its turn and stops at its budget."""

    def __init__(self, gap=None, budget=400):
        super().__init__()
        self.gap = GAP if gap is None else gap
        self.budget, self.used, self._last = budget, 0, 0.0
        self.headers.update({"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"})

    def request(self, method, url, *args, **kw):
        if method.upper() not in ("GET", "HEAD"):
            raise NotAllowed("the check only ever reads")     # never sends a form
        if self.used >= self.budget:
            raise NotAllowed("the check's request budget is spent")
        wait = self._last + self.gap - time.time()
        if wait > 0:
            time.sleep(wait)
        self.used += 1
        try:
            return super().request(method, url, *args, **kw)
        finally:
            self._last = time.time()


def _local_allowed() -> bool:
    return os.environ.get("WEBSITE_CHECK_ALLOW_LOCAL") == "1"


def normalise(url) -> str:
    """'www.example.com/about' -> 'https://www.example.com'. Raises NotAllowed."""
    u = (url or "").strip()
    if not u:
        raise NotAllowed("type the website's address")
    if "://" not in u:
        u = "https://" + u
    p = urllib.parse.urlparse(u)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise NotAllowed("that is not a website address")
    host = p.hostname.lower()
    if not _local_allowed():
        if host in ("localhost",) or host.endswith((".local", ".lan", ".internal", ".home")):
            raise NotAllowed("the check reads public websites only, not this computer or its network")
        try:
            infos = socket.getaddrinfo(host, None)
        except socket.gaierror:
            raise NotAllowed(f"{host} could not be found — check the spelling")
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
                raise NotAllowed("the check reads public websites only, not this computer or its network")
    port = f":{p.port}" if p.port else ""
    return f"{p.scheme}://{host}{port}"


def _norm(u):
    p = urllib.parse.urlparse(u or "")
    return f"{p.scheme}://{(p.netloc or '').lower()}{(p.path or '/').rstrip('/') or '/'}"


def _path(u):
    p = urllib.parse.urlparse(u or "")
    return (p.path or "/") + (("?" + p.query) if p.query else "")


def capture(s, url) -> dict:
    """One page as the server sends it, in the shape seo_audit's checks read — plus headers."""
    from . import seo_audit
    page = {"url": url, "fetched_at": int(time.time())}
    t0 = time.time()
    try:
        r = s.get(url, timeout=TIMEOUT, allow_redirects=True)
    except NotAllowed:
        raise
    except Exception as e:
        page["error"] = f"{type(e).__name__}: {str(e)[:160]}"
        return page
    page.update({
        "status": r.status_code, "final_url": r.url, "elapsed_ms": int(r.elapsed.total_seconds() * 1000)
        if r.elapsed else int((time.time() - t0) * 1000),
        "redirects": [h.url for h in r.history] + ([r.url] if r.history else []),
        "x_robots": (r.headers.get("X-Robots-Tag") or "").lower(),
        "content_type": r.headers.get("Content-Type", ""),
        "headers": {k.lower(): v for k, v in r.headers.items()},
        "set_cookies": [c for c in (r.raw.headers.get_all("Set-Cookie") or [])] if hasattr(r.raw, "headers")
        and hasattr(r.raw.headers, "get_all") else [],
        "bytes": len(r.content or b""),
    })
    body = r.text or ""
    if "html" in page["content_type"].lower() or body.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
        page["data"] = seo_audit.extract(body, r.url)
        page["html"] = body
    return page


def _robots(s, base):
    rp = urllib.robotparser.RobotFileParser()
    text, found = "", False
    try:
        r = s.get(base + "/robots.txt", timeout=10, allow_redirects=True)
        if r.status_code == 200 and "<html" not in (r.text or "")[:200].lower():
            text, found = r.text or "", True
    except NotAllowed:
        raise
    except Exception:
        pass
    rp.parse(text.splitlines())
    delay = None
    try:
        delay = rp.crawl_delay(UA) or rp.crawl_delay("*")
    except Exception:
        delay = None
    sitemaps = [urllib.parse.urljoin(base + "/", u) for u in re.findall(r"(?im)^\s*sitemap\s*:\s*(\S+)", text)]
    return {"parser": rp, "text": text, "found": found, "delay": delay, "sitemaps": sitemaps}


def _locs(xml):
    return [u.strip() for u in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", xml or "", re.I)]


def _sitemap(s, base, listed, limit):
    """Page addresses from the site's own sitemap — an index followed one level down."""
    urls, found, tried = [], "", 0
    for sm in (listed or []) + [base + "/sitemap.xml", base + "/sitemap_index.xml"]:
        if tried >= 3 or len(urls) >= limit:
            break
        tried += 1
        try:
            r = s.get(sm, timeout=15)
        except NotAllowed:
            raise
        except Exception:
            continue
        if r.status_code != 200 or "<loc>" not in (r.text or ""):
            continue
        found = found or sm
        locs = _locs(r.text)
        if "<sitemapindex" in r.text[:500].lower():
            for child in locs[:2]:
                try:
                    c = s.get(child, timeout=15)
                    if c.status_code == 200:
                        urls += _locs(c.text)
                except NotAllowed:
                    raise
                except Exception:
                    continue
        else:
            urls += locs
    return urls[:limit * 2], found


def _twin(host):
    """example.com <-> www.example.com; an IP address has no twin."""
    name = host.rsplit(":", 1)[0] if host.count(":") == 1 else host
    try:
        ipaddress.ip_address(name.strip("[]"))
        return ""
    except ValueError:
        pass
    return host[4:] if host.startswith("www.") else "www." + host


_SKIP = re.compile(r"\.(?:jpe?g|png|gif|webp|svg|ico|css|js|pdf|zip|mp4|mp3|woff2?|xml|txt)$"
                   r"|/(?:wp-admin|wp-json|cart|checkout|my-account|feed|login|logout|wp-login\.php)(?:/|$)", re.I)


def crawl(base, max_pages, max_links, deadline, progress=None, session=None) -> dict:
    """Read up to max_pages pages: the home page, the sitemap's pages, then links, nearest first."""
    s = session or PoliteSession(budget=max_pages + max_links + 20)
    robots = _robots(s, base)
    if robots["delay"]:
        s.gap = max(s.gap, min(float(robots["delay"]), MAX_CRAWL_DELAY))
    rp = robots["parser"]
    scheme, host = urllib.parse.urlparse(base).scheme, urllib.parse.urlparse(base).netloc.lower()
    own = {host, _twin(host)} - {""}

    def ours(u):
        p = urllib.parse.urlparse(u)
        return p.scheme in ("http", "https") and p.netloc.lower() in own and not _SKIP.search(p.path or "")

    home = capture(s, base + "/")
    final_host = urllib.parse.urlparse(home.get("final_url") or base).netloc.lower() or host
    own |= {final_host, _twin(final_host)} - {""}
    sm_urls, sm_found = _sitemap(s, base, robots["sitemaps"], max_pages)
    queue, seen = [], {_norm(home.get("final_url") or base + "/"), _norm(base + "/")}
    for u in sm_urls + ((home.get("data") or {}).get("links") or []):
        if ours(u) and _norm(u) not in seen:
            seen.add(_norm(u))
            queue.append(u)
    queue.sort(key=lambda u: (len(urllib.parse.urlparse(u).path), u))
    pages, blocked, skipped = [home], [], 0
    while queue and len(pages) < max_pages:
        if time.time() >= deadline:
            skipped += len(queue)
            break
        u = queue.pop(0)
        if not rp.can_fetch(UA, u):
            blocked.append(u)
            continue
        try:
            p = capture(s, u)
        except NotAllowed:
            break
        pages.append(p)
        if progress:
            progress(len(pages), max_pages)
        for link in (p.get("data") or {}).get("links") or []:
            if ours(link) and _norm(link) not in seen and len(seen) < max_pages * 6:
                seen.add(_norm(link))
                queue.append(link)
    read = {}
    for p in pages:
        read[_norm(p["url"])] = p.get("status")
        if p.get("final_url"):
            read[_norm(p["final_url"])] = p.get("status")
    link_status, targets, checked = {}, set(), 0
    for p in pages:
        for t in (p.get("data") or {}).get("links") or []:
            if ours(t):
                targets.add(_norm(t))
    for t in sorted(targets):
        if t in read:
            link_status[t] = read[t]
            continue
        if checked >= max_links or time.time() >= deadline or not rp.can_fetch(UA, t):
            continue
        checked += 1
        try:
            r = s.head(t, timeout=10, allow_redirects=True)
            if r.status_code in (405, 501, 403):
                r = s.get(t, timeout=10, allow_redirects=True)
            link_status[t] = r.status_code
        except NotAllowed:
            break
        except Exception:
            link_status[t] = None
    hosts = {}
    final_scheme = urllib.parse.urlparse(home.get("final_url") or base).scheme or scheme
    for h in [x for x in (final_host, _twin(final_host)) if x]:
        try:
            r = s.get(f"{final_scheme}://{h}/", timeout=15, allow_redirects=True)
            hosts[h] = {"status": r.status_code, "hops": len(r.history), "final": r.url}
        except NotAllowed:
            break
        except Exception as e:
            hosts[h] = {"status": None, "hops": 0, "final": "", "error": type(e).__name__}
    http_redirect = None
    if base.startswith("https://"):
        try:
            r = s.get("http://" + final_host + "/", timeout=15, allow_redirects=False)
            loc = r.headers.get("Location", "")
            http_redirect = r.status_code in (301, 302, 307, 308) and loc.startswith("https://")
        except NotAllowed:
            pass
        except Exception:
            http_redirect = None
    return {"pages": pages, "link_status": link_status, "hosts": hosts, "robots": robots,
            "sitemap": {"found": sm_found, "count": len(sm_urls)}, "blocked": blocked,
            "skipped": skipped, "requests": s.used, "http_redirect": http_redirect,
            "final_host": final_host}


def certificate(host, port=443, timeout=10) -> dict:
    """The security certificate, read the way any browser reads it on connecting."""
    if os.environ.get("WEBSITE_CHECK_NO_TLS") == "1":
        return {"checked": False}
    ctx = ssl.create_default_context()
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as tls:
                cert = tls.getpeercert()
        ends = _dt.datetime.strptime(cert["notAfter"], "%b %d %H:%M:%S %Y %Z")
        days = (ends - _dt.datetime.utcnow()).days
        issuer = dict(x[0] for x in cert.get("issuer", []) if x).get("organizationName", "")
        return {"checked": True, "valid": True, "days_left": days, "ends": ends.strftime("%d %b %Y"),
                "issuer": issuer}
    except ssl.SSLCertVerificationError as e:
        return {"checked": True, "valid": False, "error": str(e.verify_message or e)[:160]}
    except Exception as e:
        return {"checked": False, "error": f"{type(e).__name__}"}


# ---------------------------------------------------------------------------
# what each desk finds
# ---------------------------------------------------------------------------

def _f(sector, code, severity, effort, title, why, fix, pages=None, evidence=""):
    return {"sector": sector, "code": code, "severity": severity, "effort": effort,
            "title": title, "why": why, "fix": fix, "pages": list(dict.fromkeys(pages or []))[:12],
            "page_count": len(set(pages or [])), "evidence": str(evidence)[:400]}


# The technical audit's findings, in general words.
SEO_WORDS = {
    "fetch_failed": ("it", "high", 2, "Some pages could not be read",
                     "If the check could not read them, some visitors cannot either.",
                     "Open each page yourself; restore any that fail to load."),
    "http_error": ("it", "high", 2, "Pages answer with an error",
                   "A visitor who lands there sees an error instead of the business.",
                   "Restore each page, or redirect it to the page that replaced it."),
    "redirect_chain": ("it", "medium", 2, "Links pass through several redirects",
                       "Each extra hop is a delay on a phone and a weaker signal to Google.",
                       "Point links straight at the final address."),
    "missing_content_candidate": ("seo", "high", 2, "A page says ‘not found’ but tells Google all is well",
                                  "Google keeps a dead page in its list, and visitors land on nothing.",
                                  "Make missing pages answer as missing (404), or put the content back."),
    "possible_js_shell": ("seo", "medium", 4, "Pages arrive almost empty until scripts run",
                          "Google and AI assistants read the page as sent; an empty page tells them nothing.",
                          "Have the server send the main text in the page itself."),
    "missing_title": ("seo", "high", 2, "Pages with no title",
                      "Search results and browser tabs show the address instead of what the page offers.",
                      "Give each page one title naming what it offers and where."),
    "multiple_titles": ("seo", "low", 1, "Pages with two titles",
                        "Two titles leave it unclear which one names the page.",
                        "Keep one title per page."),
    "missing_description": ("seo", "low", 2, "Pages with no description for search results",
                            "The line under the link in Google is what makes someone choose it.",
                            "Write one short description for each page, saying what a customer gets."),
    "multiple_meta_descriptions": ("seo", "low", 1, "Pages with two descriptions",
                                   "Two descriptions contradict each other.", "Keep one per page."),
    "missing_h1": ("seo", "medium", 2, "Pages with no main heading",
                   "A visitor — and Google — needs one heading that says what the page is for.",
                   "Give each page one main heading (H1)."),
    "canonical_not_declared": ("seo", "low", 1, "No preferred address declared",
                               "Without it, Google chooses between versions of the same page itself.",
                               "Add a canonical link on each page naming its own address."),
    "multiple_canonicals": ("seo", "medium", 1, "Pages name more than one preferred address",
                            "Conflicting signals cancel each other out.", "Keep one canonical per page."),
    "invalid_canonical_url": ("seo", "medium", 1, "A preferred address that is not a real address",
                              "Google ignores it.", "Correct the canonical link to the page's full address."),
    "jsonld_syntax_error": ("ai", "medium", 1, "Business details for machines are broken",
                            "Structured data that does not parse tells Google and AI assistants nothing.",
                            "Fix the JSON-LD block so it parses (a validator shows the line)."),
    "missing_image_alt": ("content", "low", 2, "Images with no description",
                          "Photos of real work say nothing to Google Images or to someone using a screen reader.",
                          "Add a short alt text to each image saying what it shows."),
    "image_dimensions_review": ("it", "low", 2, "Images make the page jump as it loads",
                                "Images without a set size push the text around while a phone loads them.",
                                "Give each image its width and height."),
    "noindex_observed": ("seo", "medium", 1, "Pages ask Google not to list them",
                         "A page marked ‘noindex’ will not appear in search results at all.",
                         "Remove the noindex instruction from any page customers should find."),
    "two_hosts_serve_the_site": ("seo", "medium", 1, "The site answers at two addresses",
                                 "With and without www, Google sees two copies and splits their strength.",
                                 "Redirect one address to the other, permanently (301)."),
    "canonical_target_redirects": ("seo", "low", 1, "The preferred address itself redirects",
                                   "The signal to Google passes through a detour.",
                                   "Name the final address in the canonical links."),
    "duplicate_title": ("seo", "medium", 2, "Different pages share one title",
                        "They compete with each other and look identical in search results.",
                        "Give each page a title of its own."),
    "duplicate_meta_description": ("seo", "low", 2, "Different pages share one description",
                                   "In search results they look the same, so neither stands out.",
                                   "Write a description for each page."),
    "broken_internal_link": ("it", "high", 1, "Links that lead to an error page",
                             "A visitor clicking them hits a dead end.",
                             "Fix or remove each broken link."),
}


def _seo_findings(pages, link_status, hosts) -> list:
    """The technical audit run over the pages read, grouped into one finding per problem."""
    from . import seo_audit
    raw = []
    tmpl = seo_audit.template_images(pages)
    for p in pages:
        raw += seo_audit.page_findings(p, tmpl)
    raw += seo_audit.site_findings(pages, link_status, hosts)
    grouped = {}
    for r in raw:
        if r["code"] not in SEO_WORDS:
            continue
        g = grouped.setdefault(r["code"], {"pages": [], "evidence": r.get("evidence", "")})
        g["pages"] += [u for u in r.get("affected_urls") or [r.get("url")] if u][:20]
    out = []
    home = _norm(pages[0].get("final_url") or pages[0]["url"]) if pages else ""
    for code, g in grouped.items():
        sector, sev, effort, title, why, fix = SEO_WORDS[code]
        if code == "noindex_observed" and any(_norm(u) == home for u in g["pages"]):
            sev, title = "high", "The home page asks Google not to list it"
        out.append(_f(sector, code, sev, effort, title, why, fix, [_path(u) for u in g["pages"]], g["evidence"]))
    return out


def _text(html):
    return re.sub(r"\s+", " ", _TAG.sub(" ", _SCRIPTISH.sub(" ", html or ""))).strip()


def _usable(pages):
    return [p for p in pages if p.get("data") and 200 <= (p.get("status") or 0) < 300]


def check_it(c, cert) -> tuple:
    pages, out, good = c["pages"], [], []
    home = pages[0] if pages else {}
    ms = home.get("elapsed_ms")
    if ms is not None and home.get("status"):
        if ms > 4000:
            out.append(_f("it", "slow_server", "high", 3, "The server is slow to answer",
                          "Many visitors on a phone leave before a slow page appears.",
                          "Ask the host about faster hosting or caching; remove heavy plugins.",
                          ["/"], f"the home page took {ms / 1000:.1f} s to start arriving"))
        elif ms > 1800:
            out.append(_f("it", "slow_server", "medium", 3, "The server is slow to answer",
                          "Every second of waiting costs visitors, most of all on a phone.",
                          "Turn on caching, or ask the host why the server is slow.",
                          ["/"], f"the home page took {ms / 1000:.1f} s to start arriving"))
        else:
            good.append(("it", f"The server answers quickly ({ms / 1000:.1f} s for the home page)"))
    hd = home.get("data") or {}
    html = home.get("html") or ""
    if home.get("data"):
        if not re.search(r'<meta[^>]+name=["\']viewport["\']', html, re.I):
            out.append(_f("it", "no_viewport", "high", 2, "Not set up for phones",
                          "Without it, phones show a shrunken desktop page that people have to pinch and zoom.",
                          "Add the viewport line to the page head and check the layout on a phone.", ["/"],
                          "no <meta name=\"viewport\"> on the home page"))
        else:
            good.append(("it", "Set up for phones"))
        if (home.get("bytes") or 0) > 450_000:
            out.append(_f("it", "heavy_page", "low", 2, "The home page is heavy",
                          "A large page is slow on mobile data.",
                          "Remove unused code and inline images from the page.", ["/"],
                          f"{home['bytes'] // 1024} KB of HTML before any picture or script"))
        if (hd.get("script_count") or 0) > 30:
            out.append(_f("it", "many_scripts", "low", 3, "Many scripts on the home page",
                          "Each script is another thing a phone has to download and run.",
                          "Remove plugins and trackers that are not earning their place.", ["/"],
                          f"{hd['script_count']} <script> tags"))
    checked = len(c["link_status"])
    if checked and not any(s and s >= 400 for s in c["link_status"].values()):
        good.append(("it", f"No broken links among the {checked} checked"))
    if cert.get("checked"):
        if not cert.get("valid"):
            out.append(_f("it", "cert_invalid", "high", 1, "The security certificate is not valid",
                          "Browsers warn visitors away with a full-page ‘not secure’ warning.",
                          "Ask the host to install a valid certificate (most hosts include one).",
                          ["/"], cert.get("error", "")))
        elif cert["days_left"] < 14:
            out.append(_f("it", "cert_expiring", "high", 1, "The security certificate runs out within two weeks",
                          "When it does, browsers warn every visitor away.",
                          "Renew it, or turn on automatic renewal with the host.", ["/"],
                          f"ends {cert['ends']} ({cert['days_left']} days)"))
        elif cert["days_left"] < 30:
            out.append(_f("it", "cert_expiring", "medium", 1, "The security certificate runs out this month",
                          "When it does, browsers warn every visitor away.",
                          "Check automatic renewal is on with the host.", ["/"],
                          f"ends {cert['ends']} ({cert['days_left']} days)"))
        else:
            good.append(("it", f"Security certificate valid until {cert['ends']}"))
    return out, good


def check_seo(c) -> tuple:
    out, good = [], []
    pages = _usable(c["pages"])
    robots = c["robots"]
    if robots["found"]:
        rp = robots["parser"]
        base = "https://" + c["final_host"] + "/"
        if not rp.can_fetch("Googlebot", base) and not rp.can_fetch("*", base):
            out.append(_f("seo", "robots_blocks_all", "high", 1, "robots.txt tells search engines to stay away",
                          "Google will not read the site at all.",
                          "Remove the ‘Disallow: /’ line for all robots from robots.txt.", ["/robots.txt"],
                          "Disallow: / for every robot"))
    if not c["sitemap"]["found"]:
        out.append(_f("seo", "no_sitemap", "medium", 1, "No list of pages for Google (sitemap)",
                      "A sitemap is how Google learns every page exists and when it changed.",
                      "Publish /sitemap.xml listing every page, and add it in Google Search Console.",
                      ["/sitemap.xml"], "no sitemap at /sitemap.xml or named in robots.txt"))
    else:
        good.append(("seo", f"A sitemap is published ({c['sitemap']['count']} addresses)"))
    home = c["pages"][0] if c["pages"] else {}
    if home.get("html") and not re.search(r"<html[^>]*\blang=", home["html"][:2000], re.I):
        out.append(_f("seo", "no_lang", "low", 1, "The page does not say what language it is in",
                      "Google and screen readers have to guess.", "Add lang=\"en\" (or the right language) to the <html> tag.",
                      ["/"], "no lang on <html>"))
    titled = [p for p in pages if (p["data"].get("title") or "").strip()]
    if pages and len(titled) == len(pages):
        good.append(("seo", f"Every page read has a title ({len(pages)} of {len(pages)})"))
    described = [p for p in pages if p["data"].get("descriptions")]
    if pages and len(described) == len(pages):
        good.append(("seo", "Every page read has a description for search results"))
    return out, good


_LEGAL = re.compile(r"(contact|privacy|terms|cookie|legal|login|cart|thank)", re.I)
_TRUST = re.compile(r"\b(testimonial|review(?:s|ed)?|what (?:our )?(?:clients|customers) say|rated|stars?|trustpilot)\b", re.I)
_WORK = re.compile(r"\b(projects?|portfolio|case stud(?:y|ies)|our work|recent work|gallery|completed)\b", re.I)
_ADDRESS = re.compile(r"\b(street|st\.|road|rd\.|avenue|ave\.|lane|building|floor|suite|p\.?\s?o\.?\s?box|"
                      r"[A-Z]{1,2}\d{1,2}[A-Z]?\s?\d[A-Z]{2}|\d{5}(?:-\d{4})?)\b", re.I)


def _jsonld_types(pages):
    types = set()
    for p in pages:
        for b in (p.get("data") or {}).get("jsonld") or []:
            for node in (b.get("@graph") if isinstance(b, dict) and isinstance(b.get("@graph"), list) else [b]):
                if isinstance(node, dict):
                    t = node.get("@type")
                    for x in (t if isinstance(t, list) else [t]):
                        if x:
                            types.add(str(x))
    return types


def check_content(c) -> tuple:
    out, good = [], []
    pages = _usable(c["pages"])
    if not pages:
        return out, good
    thin = [p for p in pages if p["data"].get("word_count", 0) < 150 and not _LEGAL.search(_path(p["url"]))
            and _path(p["url"]) not in ("/",)]
    if thin:
        out.append(_f("content", "thin_pages", "medium" if len(thin) >= 3 else "low", 3,
                      "Pages with very few words",
                      "A page with a line or two gives Google nothing to rank and a visitor nothing to trust.",
                      "Add what a customer wants to know: what is done, where, how long it takes, and a real example.",
                      [_path(p["url"]) for p in thin], f"{len(thin)} page(s) under 150 words"))
    fps = {}
    for p in pages:
        words = (p["data"].get("main_text") or "").lower()
        if len(words.split()) >= 60:
            fps.setdefault(hashlib.sha1(words.encode("utf-8", "ignore")).hexdigest(), []).append(_path(p["url"]))
    dup = [v for v in fps.values() if len(v) > 1]
    if dup:
        out.append(_f("content", "repeated_pages", "medium", 3, "Different pages carry the same words",
                      "Google lists one and ignores the rest.",
                      "Give each page its own words, or merge the copies into one page.",
                      [u for v in dup for u in v], f"{len(dup)} set(s) of identical pages"))
    all_text = " ".join(_text(p.get("html")) for p in pages)
    home_text = _text(pages[0].get("html"))
    years = [int(y) for y in re.findall(r"(?:©|&copy;|copyright)\s*(?:\d{4}\s*[-–]\s*)?(\d{4})", home_text, re.I)]
    this_year = _dt.date.today().year
    if years and max(years) < this_year - 1:
        out.append(_f("content", "stale_copyright", "low", 1, "The site looks out of date",
                      f"A footer saying © {max(years)} makes visitors wonder if the business is still trading.",
                      "Update the year in the footer, or let the site fill it in.", ["/"], f"© {max(years)}"))
    types = _jsonld_types(pages)
    if _TRUST.search(all_text) or {"Review", "AggregateRating"} & types:
        good.append(("content", "Reviews or testimonials are shown"))
    else:
        out.append(_f("content", "no_reviews", "medium", 2, "No reviews or testimonials",
                      "People trust what other customers say more than what a business says about itself.",
                      "Show a few real reviews, with names, and link to the Google reviews.", ["/"],
                      "no review, testimonial or rating found on the pages read"))
    links_text = " ".join(" ".join(p["data"].get("links") or []) for p in pages)
    if _WORK.search(all_text) or _WORK.search(links_text):
        good.append(("content", "Real work is shown (projects, gallery or case studies)"))
    else:
        out.append(_f("content", "no_real_work", "medium", 3, "No real work shown",
                      "Photos of finished jobs, with the place and the date, win more enquiries than any slogan.",
                      "Add a projects page: four or five photos per job and a line on what was done.", ["/"],
                      "no projects, gallery or case-study page found"))
    if {"PostalAddress", "LocalBusiness"} & types or _ADDRESS.search(all_text):
        good.append(("content", "An address or location is given"))
    else:
        out.append(_f("content", "no_address", "low", 1, "No address shown",
                      "A real address is a strong sign of a real business, to people and to Google.",
                      "Put the business address in the footer and on the contact page.", ["/"],
                      "no street address or postcode found"))
    if not re.search(r"about", links_text, re.I):
        out.append(_f("content", "no_about", "low", 2, "No ‘about us’ page",
                      "People want to know who they would be dealing with.",
                      "Add an about page: who you are, how long you have done this, and a photo.", ["/"],
                      "no link to an about page"))
    return out, good


AI_WORDS = {
    "faq": ("medium", 2, "No questions and answers on the pages",
            "AI assistants and Google’s answer boxes quote pages that ask and answer customers’ questions plainly.",
            "Add the five questions customers ask most, each answered in two or three sentences, with FAQ markup."),
    "aeo.answer_first": ("medium", 2, "Pages do not start with a direct answer",
                         "Answer engines lift the first clear answer they find.",
                         "Open each service page with one or two sentences saying what is offered, where and for whom."),
    "aeo.localbusiness": ("medium", 1, "No business details for machines",
                          "LocalBusiness markup tells Google and AI assistants the name, address, phone and hours exactly.",
                          "Add LocalBusiness structured data with the real details."),
    "geo.statistics": ("medium", 2, "No real numbers",
                       "Specific facts — years in business, jobs done, sizes, times — are what assistants quote.",
                       "Add the real numbers: years trading, jobs completed, typical timings."),
    "geo.author": ("low", 1, "No person stands behind the words",
                   "Assistants and Google prefer content with a named, experienced person behind it.",
                   "Say who wrote it or who does the work, and their experience."),
    "geo.dated": ("low", 1, "Pages carry no date",
                  "Undated pages look stale to people and to answer engines.",
                  "Show when each page was last updated."),
    "aeo.extractable": ("low", 1, "Nothing easy to lift — no lists or tables",
                        "A short list or table is the easiest thing for an assistant to quote.",
                        "Turn steps, options or prices-on-request into short lists."),
}


def check_ai(c) -> tuple:
    from . import seo_answers
    out, good = [], []
    pages = _usable(c["pages"])[:20]
    audits = []
    for p in pages:
        try:
            audits.append(seo_answers.audit_page(p.get("final_url") or p["url"], p.get("html") or ""))
        except Exception:
            continue
    rate, where = {}, {}
    for a in audits:
        for row in a["findings"]:
            if row["n_a"]:
                continue
            r = rate.setdefault(row["check_id"], [0, 0])
            r[1] += 1
            if row["passed"]:
                r[0] += 1
            else:
                where.setdefault(row["check_id"], []).append(_path(a["url"]))
    anywhere = lambda cid: rate.get(cid, [0, 0])[0] > 0          # one good page of questions is enough
    if not (anywhere("aeo.question_heading") or anywhere("aeo.faq_schema") or anywhere("aeo.faq_visible")):
        sev, eff, title, why, fix = AI_WORDS["faq"]
        out.append(_f("ai", "faq", sev, eff, title, why, fix, where.get("aeo.question_heading", ["/"]),
                      "no page asks and answers a customer's question"))
    else:
        good.append(("ai", "Questions are asked and answered on the pages"))
    for cid in ("aeo.answer_first", "aeo.localbusiness", "geo.statistics", "geo.author", "geo.dated", "aeo.extractable"):
        r = rate.get(cid)
        if not r or not r[1]:
            continue
        if cid == "aeo.localbusiness":              # the business's details belong on one page, not every page
            r = [r[1] if r[0] else 0, r[1]]
        if r[0] / r[1] < 0.3:
            sev, eff, title, why, fix = AI_WORDS[cid]
            out.append(_f("ai", cid, sev, eff, title, why, fix, where.get(cid, []), f"{r[1] - r[0]} of {r[1]} pages"))
        elif r[0] / r[1] >= 0.7:
            label = {"aeo.localbusiness": "Business details are marked up for machines",
                     "geo.statistics": "Real numbers are given", "geo.author": "A person stands behind the words",
                     "geo.dated": "Pages are dated", "aeo.answer_first": "Pages open with a direct answer",
                     "aeo.extractable": "Lists and tables make it easy to quote"}[cid]
            good.append(("ai", label))
    blocked = []
    txt = c["robots"]["text"]
    if txt:
        agent, rules = "", {}
        for line in txt.splitlines():
            line = line.split("#", 1)[0].strip()
            if ":" not in line:
                continue
            k, v = (x.strip() for x in line.split(":", 1))
            if k.lower() == "user-agent":
                agent = v.lower()
                rules.setdefault(agent, [])
            elif k.lower() == "disallow" and agent:
                rules[agent].append(v)
        blocked = [b for b in seo_answers.AI_CRAWLERS if "/" in rules.get(b.lower(), [])]
    if blocked:
        out.append(_f("ai", "ai_blocked", "medium", 1, "AI assistants are shut out",
                      "robots.txt stops them reading the site, so they cannot recommend it. That may be on purpose.",
                      "If it is not deliberate, remove those blocks from robots.txt.", ["/robots.txt"],
                      "blocked: " + ", ".join(blocked)))
    else:
        good.append(("ai", "AI assistants may read the site"))
    return out, good


_PHONE = re.compile(r"(?:\+|00)?\d[\d\s().-]{7,}\d")
_CTA = re.compile(r"\b(get a quote|request a quote|quote|call (?:us|now)|book|contact us|get in touch|enquire|enquiry|"
                  r"request a (?:visit|call)|message us|whatsapp)\b", re.I)


def check_leads(c) -> tuple:
    out, good = [], []
    pages = _usable(c["pages"])
    if not pages:
        return out, good
    home = pages[0]
    html_all = " ".join(p.get("html") or "" for p in pages)
    text_all = _text(html_all)
    tel = re.search(r'href=["\']tel:', html_all, re.I)
    phones = [m for m in _PHONE.findall(text_all) if len(re.sub(r"\D", "", m)) >= 8]
    if tel:
        good.append(("leads", "A phone number you can tap on a phone"))
    elif phones:
        out.append(_f("leads", "no_click_to_call", "high", 1, "The phone number cannot be tapped",
                      "On a phone, visitors have to copy the number by hand — many will not bother.",
                      "Make every phone number a tap-to-call link (tel:).", ["/"], f"number shown: {phones[0].strip()[:30]}"))
    else:
        out.append(_f("leads", "no_phone", "medium", 1, "No phone number shown",
                      "Many customers want to ring before they decide.",
                      "Show a phone number at the top of every page, as a tap-to-call link.", ["/"], "no phone number found"))
    forms = [p for p in pages if re.search(r"<form\b.*?<(?:input|textarea)\b", p.get("html") or "", re.I | re.S)
             and not re.search(r'type=["\']search["\']|role=["\']search["\']', p.get("html") or "", re.I)]
    if forms:
        good.append(("leads", "An enquiry form"))
    else:
        out.append(_f("leads", "no_form", "medium", 2, "No enquiry form",
                      "Some people would rather type than ring, especially outside working hours.",
                      "Add a short form: name, phone, what they need. Test that it reaches you.", ["/"],
                      "no form on the pages read"))
    if re.search(r"wa\.me/|api\.whatsapp\.com|whatsapp://", html_all, re.I):
        good.append(("leads", "A WhatsApp button"))
    else:
        out.append(_f("leads", "no_whatsapp", "low", 1, "No WhatsApp button",
                      "Where people use WhatsApp, a chat button is often the easiest first step.",
                      "Add a WhatsApp link (wa.me) beside the phone number.", ["/"], "no WhatsApp link found"))
    links = " ".join((home["data"].get("links") or []))
    if re.search(r"contact", links, re.I) or re.search(r'href=["\'][^"\']*contact', home.get("html") or "", re.I):
        good.append(("leads", "A contact page linked from the home page"))
    else:
        out.append(_f("leads", "no_contact_page", "medium", 1, "No contact page linked from the home page",
                      "Visitors look for ‘Contact’ in the menu; if it is not there, many leave.",
                      "Add ‘Contact’ to the main menu.", ["/"], "no link containing ‘contact’ on the home page"))
    home_clickables = " ".join(_text(m) for m in re.findall(r"<(?:a|button)\b[^>]*>.*?</(?:a|button)>",
                                                              home.get("html") or "", re.I | re.S))
    if _CTA.search(home_clickables):
        good.append(("leads", "A clear next step on the home page"))
    else:
        out.append(_f("leads", "no_cta", "medium", 1, "No clear next step on the home page",
                      "Visitors who are not told what to do next usually do nothing.",
                      "Put one clear button near the top: ‘Get a quote’ or ‘Call now’.", ["/"],
                      "no button or link inviting contact on the home page"))
    return out, good


_SEC_HEADERS = (("strict-transport-security", "Strict-Transport-Security (forces HTTPS)"),
                ("content-security-policy", "Content-Security-Policy (limits what the page may load)"),
                ("x-content-type-options", "X-Content-Type-Options"),
                ("x-frame-options", "X-Frame-Options (stops other sites framing it)"),
                ("referrer-policy", "Referrer-Policy"))


def check_security(c) -> tuple:
    out, good = [], []
    pages = c["pages"]
    home = pages[0] if pages else {}
    final = home.get("final_url") or ""
    if final.startswith("http://") or not home.get("status"):
        out.append(_f("security", "no_https", "high", 2, "The site is not served securely (HTTPS)",
                      "Browsers mark it ‘Not secure’, and anything typed into it can be read on the way.",
                      "Turn on HTTPS with the host — most include a certificate — and redirect every page to it.",
                      ["/"], final or "no secure answer"))
    else:
        good.append(("security", "Served securely over HTTPS"))
        if c.get("http_redirect") is False:
            out.append(_f("security", "no_http_redirect", "medium", 1, "The insecure address does not move visitors to HTTPS",
                          "Someone typing the address without https can land on the insecure version.",
                          "Redirect every http:// address to https:// (one setting with most hosts).", ["/"],
                          "http:// answers without redirecting to https://"))
    h = home.get("headers") or {}
    secure = final.startswith("https://")
    missing = [label for key, label in _SEC_HEADERS if key not in h
               and not (key == "x-frame-options" and "frame-ancestors" in h.get("content-security-policy", ""))
               and (secure or key != "strict-transport-security")]      # HSTS means nothing without HTTPS
    if missing and home.get("status"):
        out.append(_f("security", "missing_headers", "medium" if len(missing) >= 3 else "low", 1,
                      "Missing security headers",
                      "These standard instructions tell browsers to block common attacks on visitors.",
                      "Ask the host or developer to add them — a few lines in the server settings.",
                      ["/"], "missing: " + "; ".join(missing)))
    elif home.get("status"):
        good.append(("security", "The standard security headers are set"))
    leaks = [f"{k}: {h[k]}" for k in ("server", "x-powered-by") if k in h and re.search(r"\d", h[k] or "")]
    if leaks:
        out.append(_f("security", "version_shown", "low", 1, "The server says which software versions it runs",
                      "It saves an attacker a step: they know exactly which known weaknesses to try.",
                      "Hide the version numbers in the server settings.", ["/"], "; ".join(leaks)))
    mixed_hi, mixed_lo = [], []
    for p in _usable(pages):
        if not (p.get("final_url") or "").startswith("https://"):
            continue
        html = p.get("html") or ""
        if re.search(r'<(?:script|iframe)\b[^>]*\bsrc=["\']http://|<link\b[^>]*\bhref=["\']http://[^"\']+\.css', html, re.I):
            mixed_hi.append(_path(p["url"]))
        elif re.search(r'<img\b[^>]*\bsrc=["\']http://', html, re.I):
            mixed_lo.append(_path(p["url"]))
    if mixed_hi:
        out.append(_f("security", "mixed_content", "high", 2, "Secure pages load scripts over an insecure connection",
                      "Browsers block them, which can break the page, and they are a way in for tampering.",
                      "Change every http:// script, style and frame address to https://.", mixed_hi,
                      "script, frame or stylesheet loaded over http://"))
    elif mixed_lo:
        out.append(_f("security", "mixed_content", "low", 1, "Secure pages load pictures over an insecure connection",
                      "Browsers flag the page as not fully secure.", "Change image addresses to https://.", mixed_lo,
                      "image loaded over http://"))
    insecure_forms = [_path(p["url"]) for p in _usable(pages)
                      if re.search(r'<form\b[^>]*\baction=["\']http://', p.get("html") or "", re.I)]
    if insecure_forms:
        out.append(_f("security", "form_insecure", "high", 1, "A form sends what visitors type insecurely",
                      "Names and phone numbers typed into it travel unprotected.",
                      "Point the form at an https:// address.", insecure_forms, "form action starts with http://"))
    cookies = home.get("set_cookies") or []
    weak = [ck.split("=", 1)[0] for ck in cookies if final.startswith("https://") and "secure" not in ck.lower()]
    if weak:
        out.append(_f("security", "cookies_not_secure", "low", 1, "Cookies are not marked secure",
                      "They could be sent over an insecure connection.",
                      "Mark cookies Secure and HttpOnly in the site's settings.", ["/"], "cookies: " + ", ".join(weak[:5])))
    old = []
    for p in _usable(pages)[:5]:
        for m in re.finditer(r"jquery[.-]?(\d)\.(\d+)(?:\.\d+)?(?:\.min)?\.js", p.get("html") or "", re.I):
            major, minor = int(m.group(1)), int(m.group(2))
            if major < 3 or (major == 3 and minor < 5):
                old.append(f"jQuery {major}.{minor}")
    if old:
        out.append(_f("security", "old_library", "medium", 2, "An old version of jQuery is loaded",
                      "Versions before 3.5 have published weaknesses.",
                      "Update jQuery, or the theme or plugin that loads it.", ["/"], ", ".join(sorted(set(old)))))
    gen = re.search(r'<meta[^>]+name=["\']generator["\'][^>]+content=["\']([^"\']*\d[^"\']*)["\']', home.get("html") or "", re.I)
    if gen:
        out.append(_f("security", "generator_shown", "low", 1, "The page says which version of its software it runs",
                      "Like the server version, it tells an attacker which weaknesses to look up.",
                      "Remove the generator tag (a setting or a small plugin).", ["/"], gen.group(1)[:60]))
    return out, good


# ---------------------------------------------------------------------------
# scores and the report
# ---------------------------------------------------------------------------

def _penalty(f, readable):
    w = WEIGHT.get(f["severity"], 0)
    if f["page_count"] > 1 and readable > 1:
        share = min(1.0, f["page_count"] / readable)
        w *= 0.4 + 0.6 * share
    return w


def score(findings, readable) -> dict:
    out = {}
    for key, _ in SECTORS:
        lost = sum(_penalty(f, readable) for f in findings if f["sector"] == key)
        out[key] = max(0, int(round(100 - lost)))
    return out


def summary(overall) -> str:
    if overall >= 85:
        return "In good shape — a few small fixes."
    if overall >= 70:
        return "Sound, with fixes that would win more enquiries."
    if overall >= 50:
        return "Working, but losing visitors and enquiries it could be winning."
    return "Needs work: visitors and Google are being let down in several places."


def top_fixes(findings, n=5) -> list:
    def rank(f):
        return (WEIGHT.get(f["severity"], 0) * (1 + min(f["page_count"], 10) / 10)) / f["effort"]
    return sorted(findings, key=rank, reverse=True)[:n]


def rulebook(c) -> dict:
    """
    The same SEO rulebook Botix runs on the owner's own site (pipelines/seo_rules),
    over the pages read here: one score from every check, so a check of any site
    and the owner's own score are on the same scale. {} when nothing was readable.
    """
    from . import seo_rules as R
    pages = [R.parse(p["html"], p.get("final_url") or p["url"], headers=p.get("headers"), status=p.get("status") or 200,
                     ms=p.get("elapsed_ms")) for p in c.get("pages") or [] if p.get("html")]
    if not pages:
        return {}
    try:
        out = R.check_site(pages, {"link_status": c.get("link_status") or {}})
    except Exception:
        return {}
    return {"score": out["score"], "grade": out["grade"], "label": out["label"], "capped_by": out["capped_by"],
            "checks": len(R.ALL_RULES), "categories": out["categories"],
            "worst": [i["name"] for i in out["issues"][:5]]}


def analyse(c, cert) -> dict:
    findings, good = [], []
    findings += _seo_findings(c["pages"], c["link_status"], c["hosts"])
    for fn in (check_it, check_seo, check_content, check_ai, check_leads, check_security):
        got = fn(c, cert) if fn is check_it else fn(c)
        findings += got[0]
        good += got[1]
    readable = max(1, len(_usable(c["pages"])))
    for f in findings:
        f["impact"], f["effort_label"] = IMPACT.get(f["severity"], ""), EFFORT.get(f["effort"], "")
        f["sector_name"] = SECTOR_NAME[f["sector"]]
    scores = score(findings, readable)
    overall = int(round(sum(scores.values()) / len(scores)))
    findings.sort(key=lambda f: (-WEIGHT.get(f["severity"], 0), f["sector"]))
    return {"overall": overall, "scores": scores, "summary": summary(overall), "rulebook": rulebook(c),
            "sectors": [list(x) for x in SECTORS],
            "top": [f["code"] + "|" + f["sector"] for f in top_fixes(findings)],
            "findings": findings,
            "working": [{"sector": s, "sector_name": SECTOR_NAME[s], "text": t} for s, t in good]}


# ---------------------------------------------------------------------------
# storage — in the open website's own cabinet, sealed like everything else
# ---------------------------------------------------------------------------

def _conn():
    c = sqlite3.connect(str(board.DB), timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE IF NOT EXISTS website_checks (id INTEGER PRIMARY KEY AUTOINCREMENT,"
              " url TEXT, host TEXT, depth TEXT, status TEXT, phase TEXT, pages_done INTEGER DEFAULT 0,"
              " pages_total INTEGER DEFAULT 0, started_at INTEGER, finished_at INTEGER, overall INTEGER,"
              " report TEXT, error TEXT, pid INTEGER, machine TEXT)")
    return c


def new(url, depth="quick") -> dict:
    try:
        base = normalise(url)
    except NotAllowed as e:
        return {"ok": False, "error": str(e)}
    depth = "full" if depth == "full" else "quick"
    # THE PLAN: Website Check reports this month. A look now; counted when the
    # report is made (run), so a website that does not answer costs nothing.
    from coordination import licence
    allowed = licence.take("checks", dry=True)
    if not allowed.get("ok"):
        return {"ok": False, "plan": True, "error": allowed.get("error")}
    c = _conn()
    _settle(c)
    busy = c.execute("SELECT host FROM website_checks WHERE status='running' LIMIT 1").fetchone()
    if busy:
        c.close()
        return {"ok": False, "busy": True,
                "error": f"A check of {busy['host']} is still running — wait for it to finish, or stop it."}
    with c:
        cur = c.execute("INSERT INTO website_checks (url, host, depth, status, phase, pages_total, started_at)"
                        " VALUES (?,?,?,?,?,?,?)", (base, urllib.parse.urlparse(base).netloc.lower(), depth,
                                                    "running", "Starting", FULL_PAGES if depth == "full" else QUICK_PAGES,
                                                    int(time.time())))
    c.close()
    return {"ok": True, "id": cur.lastrowid, "url": base, "depth": depth}


def _set(cid, **kw):
    c = _conn()
    with c:
        c.execute("UPDATE website_checks SET " + ", ".join(f"{k}=?" for k in kw) + " WHERE id=?",
                  (*kw.values(), cid))
    c.close()


def run(cid) -> dict:
    """Carry out check number cid, writing its progress and its report."""
    c = _conn()
    row = c.execute("SELECT * FROM website_checks WHERE id=?", (cid,)).fetchone()
    c.close()
    if not row:
        return {"ok": False, "error": "no such check"}
    base, depth = row["url"], row["depth"]
    full = depth == "full"
    pages_cap, links_cap = (FULL_PAGES, FULL_LINKS) if full else (QUICK_PAGES, QUICK_LINKS)
    deadline = time.time() + (FULL_MINUTES if full else QUICK_MINUTES) * 60
    try:
        _set(cid, phase="Reading the website", pid=os.getpid(), machine=board._this_host())
        crawled = crawl(base, pages_cap, links_cap, deadline,
                        progress=lambda n, total: _set(cid, pages_done=n, pages_total=total))
        if not crawled["pages"] or (not crawled["pages"][0].get("status") and len(crawled["pages"]) == 1):
            err = crawled["pages"][0].get("error") if crawled["pages"] else ""
            raise RuntimeError(f"the website did not answer ({err or 'no response'})")
        _set(cid, phase="Checking the certificate")
        cert = certificate(crawled["final_host"]) if base.startswith("https://") else {"checked": False}
        _set(cid, phase="Every desk is looking at it")
        rep = analyse(crawled, cert)
        rep.update({"url": base, "host": row["host"], "depth": depth, "checked_at": int(time.time()),
                    "pages_read": len(crawled["pages"]), "requests": crawled["requests"],
                    "blocked_by_robots": len(crawled["blocked"]), "not_reached": crawled["skipped"],
                    "certificate": cert})
        rep["progress"] = _progress(row["host"], cid, rep)
        _set(cid, status="done", phase="Done", finished_at=int(time.time()), overall=rep["overall"],
             report=json.dumps(rep, ensure_ascii=False), pages_done=len(crawled["pages"]))
        try:
            from coordination import licence
            licence.take("checks")
        except Exception:
            pass
        return {"ok": True, "id": cid, **rep}
    except Exception as e:
        _set(cid, status="failed", phase="Stopped", finished_at=int(time.time()), error=str(e)[:400])
        return {"ok": False, "id": cid, "error": str(e)}


def _progress(host, cid, rep):
    """What changed since the last finished check of the same website."""
    c = _conn()
    prev = c.execute("SELECT id, finished_at, report FROM website_checks WHERE host=? AND status='done'"
                     " AND id<? ORDER BY id DESC LIMIT 1", (host, cid)).fetchone()
    c.close()
    if not prev:
        return None
    try:
        old = json.loads(prev["report"] or "{}")
    except ValueError:
        return None
    old_codes = {f["code"] for f in old.get("findings", [])}
    new_codes = {f["code"] for f in rep["findings"]}
    titles = {f["code"]: f["title"] for f in old.get("findings", [])}
    return {"since": prev["finished_at"], "previous_id": prev["id"],
            "overall_was": old.get("overall"), "overall_change": rep["overall"] - (old.get("overall") or 0),
            "scores_was": old.get("scores", {}),
            "fixed": [titles[x] for x in sorted(old_codes - new_codes)],
            "new": [f["title"] for f in rep["findings"] if f["code"] in new_codes - old_codes]}


GONE = "the check stopped before it finished — the office may have been closed. Start it again."


def _settle(c):
    """A check whose process has gone, or is long past its time limit, stopped without finishing."""
    late = int(time.time()) - (FULL_MINUTES + 10) * 60
    gone = [r["id"] for r in c.execute("SELECT id, pid, machine, started_at FROM website_checks"
                                       " WHERE status='running'")
            if (r["started_at"] or 0) < late or (r["pid"] and not board.process_alive(r["pid"], r["machine"] or ""))]
    if gone:
        with c:
            c.executemany("UPDATE website_checks SET status='failed', phase='Stopped', error=?,"
                          " finished_at=? WHERE id=? AND status='running'",
                          [(GONE, int(time.time()), i) for i in gone])


def stop(cid) -> dict:
    """Stop a running check — its process first, then its record."""
    c = _conn()
    row = c.execute("SELECT status, pid, machine FROM website_checks WHERE id=?", (cid,)).fetchone()
    c.close()
    if not row:
        return {"ok": False, "error": "no such check"}
    if row["status"] != "running":
        return {"ok": True, "already": row["status"]}
    if row["pid"] and row["pid"] != os.getpid():
        board.end_process(row["pid"], row["machine"] or "")
    _set(cid, status="failed", phase="Stopped", finished_at=int(time.time()), error="stopped by you")
    return {"ok": True}


def get(cid) -> dict:
    c = _conn()
    _settle(c)
    row = c.execute("SELECT * FROM website_checks WHERE id=?", (cid,)).fetchone()
    c.close()
    if not row:
        return {"ok": False, "error": "no such check"}
    out = {"ok": True, "id": row["id"], "url": row["url"], "host": row["host"], "depth": row["depth"],
           "status": row["status"], "phase": row["phase"], "pages_done": row["pages_done"],
           "pages_total": row["pages_total"], "started_at": row["started_at"], "finished_at": row["finished_at"],
           "error": row["error"] or ""}
    if row["report"]:
        out["report"] = json.loads(row["report"])
    return out


def listing(limit=30) -> dict:
    c = _conn()
    _settle(c)
    rows = [dict(r) for r in c.execute("SELECT id, url, host, depth, status, phase, overall, started_at,"
                                       " finished_at, pages_done, pages_total, error FROM website_checks"
                                       " ORDER BY id DESC LIMIT ?", (limit,))]
    c.close()
    return {"ok": True, "checks": rows}


def delete(cid) -> dict:
    c = _conn()
    with c:
        n = c.execute("DELETE FROM website_checks WHERE id=?", (cid,)).rowcount
    c.close()
    return {"ok": bool(n)}


# ---------------------------------------------------------------------------
# the report as a page, and as a PDF — with the user's own logo
# ---------------------------------------------------------------------------

def brand() -> dict:
    try:
        d = json.loads(workspace.read_secret(workspace.config_path(BRAND_FILE), "{}") or "{}")
    except Exception:
        d = {}
    d = d if isinstance(d, dict) else {}
    if d.get("logo") and not own_logo():
        d = {**d, "logo": ""}            # kept, and back on the reports the day the plan has it
    return d


def own_logo() -> bool:
    """Reports with your own logo: on the plans that have it (coordination/licence)."""
    try:
        from coordination import licence
        return licence.feature("own_logo")
    except Exception:
        return True


def save_brand(name=None, logo=None, remove_logo=False) -> dict:
    d = brand()
    if name is not None:
        d["name"] = str(name).strip()[:80]
    if remove_logo:
        d.pop("logo", None)
    if logo:
        m = re.match(r"^data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/=]+)$", str(logo).strip())
        if not m:
            return {"ok": False, "error": "the logo must be a PNG, JPEG or WebP picture"}
        raw = base64.b64decode(m.group(2))
        if len(raw) > 600_000:
            return {"ok": False, "error": "the logo is too large — keep it under 600 KB"}
        magic = {"image/png": b"\x89PNG", "image/jpeg": b"\xff\xd8\xff", "image/webp": b"RIFF"}[m.group(1)]
        if not raw.startswith(magic):
            return {"ok": False, "error": "that file is not the picture it says it is"}
        d["logo"] = f"data:{m.group(1)};base64,{m.group(2)}"
    workspace.write_secret(workspace.config_path(BRAND_FILE), json.dumps(d))
    return {"ok": True, "name": d.get("name", ""), "has_logo": bool(d.get("logo"))}


def _e(s):
    return (str(s if s is not None else "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def report_html(chk) -> str:
    """The whole report as one self-contained page, laid out for screen and for A4."""
    rep = chk.get("report") or {}
    b = brand()
    name = b.get("name") or ""
    try:
        if not name:
            from coordination import site
            name = site.agency_name()
    except Exception:
        name = name or ""
    logo = b.get("logo") or ""
    d = _dt.datetime.fromtimestamp(rep.get("checked_at") or time.time())
    when = f"{d.day} {d:%B %Y}"
    scores = rep.get("scores") or {}
    by_key = {f["code"] + "|" + f["sector"]: f for f in rep.get("findings", [])}
    top = [by_key[k] for k in rep.get("top", []) if k in by_key]

    def bar(v):
        tone = "#1E6642" if v >= 80 else "#8A5300" if v >= 55 else "#97241C"
        return (f'<div class="bar"><i style="width:{max(2, int(v))}%;background:{tone}"></i></div>')

    sec_rows = "".join(f'<div class="srow"><span>{_e(SECTOR_NAME[k])}</span><b>{scores.get(k, 0)}</b>{bar(scores.get(k, 0))}</div>'
                       for k, _ in SECTORS)
    tops = "".join(f'<li><div><b>{_e(f["title"])}</b><p>{_e(f["why"])}</p><p class="fix">{_e(f["fix"])}</p></div>'
                   f'<div class="tags"><span class="t {f["severity"]}">{_e(f["impact"])}</span>'
                   f'<span class="t">{_e(f["effort_label"])}</span></div></li>' for f in top)
    groups = ""
    for k, label in SECTORS:
        fs = [f for f in rep.get("findings", []) if f["sector"] == k]
        if not fs:
            continue
        rows = "".join(f'<tr><td><b>{_e(f["title"])}</b><div class="small">{_e(f["fix"])}</div></td>'
                       f'<td><span class="t {f["severity"]}">{_e(f["impact"])}</span></td>'
                       f'<td class="small">{_e(", ".join(f["pages"][:6]))}{" …" if f["page_count"] > 6 else ""}'
                       f'<div class="ev">{_e(f["evidence"])}</div></td></tr>' for f in fs)
        groups += (f'<h3>{_e(label)} <span class="sc">{scores.get(k, 0)}/100</span></h3>'
                   f'<table><thead><tr><th>What was found</th><th>How much it matters</th><th>Where, and the evidence</th></tr></thead>'
                   f'<tbody>{rows}</tbody></table>')
    working = "".join(f"<li>{_e(w['text'])}</li>" for w in rep.get("working", []))
    prog = rep.get("progress")
    prog_html = ""
    if prog:
        ch = prog.get("overall_change") or 0
        prog_html = (f'<section class="box"><h2>Since the last check</h2><p>Overall score {prog.get("overall_was")} → '
                     f'{rep.get("overall")} ({"+" if ch >= 0 else ""}{ch}).</p>'
                     + (f'<p><b>Fixed:</b> {_e("; ".join(prog["fixed"]))}</p>' if prog.get("fixed") else "")
                     + (f'<p><b>New:</b> {_e("; ".join(prog["new"]))}</p>' if prog.get("new") else "") + "</section>")
    overall = rep.get("overall", 0)
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Website Check — {_e(rep.get('host') or chk.get('host') or '')}</title>
<style>
@page{{size:A4;margin:16mm 14mm}}
*{{box-sizing:border-box}}
body{{margin:0;background:#FFFFFF;color:#142022;font-family:'Instrument Sans','Segoe UI',system-ui,-apple-system,Arial,sans-serif;font-size:13.5px;line-height:1.5}}
.wrap{{max-width:860px;margin:0 auto;padding:32px 24px 48px}}
header{{display:flex;justify-content:space-between;align-items:center;gap:20px;border-bottom:3px solid #1F5F66;padding-bottom:16px}}
header img{{max-height:56px;max-width:220px}}
.by{{font-size:12px;color:#4A5A5D;text-align:right}}
h1{{font-size:28px;margin:22px 0 2px;letter-spacing:-.01em}}
h2{{font-size:18px;margin:0 0 10px}}
h3{{font-size:16px;margin:26px 0 8px;display:flex;justify-content:space-between;border-bottom:1px solid #D9E0E0;padding-bottom:6px}}
.sc{{font-family:'IBM Plex Mono',ui-monospace,monospace;color:#4A5A5D;font-weight:500}}
.muted,.small{{color:#4A5A5D}} .small{{font-size:12px}}
.hero{{display:flex;flex-wrap:wrap;gap:24px;align-items:center;margin:18px 0 8px}}
.big{{font-family:'IBM Plex Mono',ui-monospace,monospace;font-size:56px;line-height:1;font-weight:500}}
.scores{{flex:1 1 360px;display:grid;gap:8px}}
.srow{{display:grid;grid-template-columns:170px 36px 1fr;gap:10px;align-items:center}}
.srow b{{font-family:'IBM Plex Mono',ui-monospace,monospace;font-weight:500;text-align:right}}
.bar{{height:8px;background:#E6EBEB;border-radius:99px;overflow:hidden}} .bar i{{display:block;height:100%}}
.box{{border:1px solid #D9E0E0;border-radius:10px;padding:16px 18px;margin:18px 0;break-inside:avoid}}
ol.tops{{list-style:none;margin:0;padding:0;counter-reset:n}}
ol.tops li{{display:flex;justify-content:space-between;gap:16px;padding:10px 0;border-top:1px solid #EEF2F2;break-inside:avoid}}
ol.tops li:first-child{{border-top:0}}
ol.tops p{{margin:2px 0 0}} .fix{{color:#1F5F66}}
.tags{{display:flex;flex-direction:column;gap:4px;align-items:flex-end;flex:none}}
.t{{display:inline-block;padding:1px 8px;border-radius:6px;font-size:11.5px;font-weight:600;background:#EEF2F2;color:#33464A;white-space:nowrap}}
.t.high{{background:#F9E3E1;color:#97241C}} .t.medium{{background:#FBEFD9;color:#7D4B00}}
table{{width:100%;border-collapse:collapse}} th{{text-align:left;font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:#5F7074;padding:6px 8px;border-bottom:1px solid #D9E0E0}}
td{{padding:8px;border-bottom:1px solid #EEF2F2;vertical-align:top}} tr{{break-inside:avoid}}
.ev{{font-family:'IBM Plex Mono',ui-monospace,monospace;font-size:11px;color:#5F7074;margin-top:3px;overflow-wrap:anywhere}}
ul.ok{{margin:0;padding-left:18px}}
footer{{margin-top:28px;border-top:1px solid #D9E0E0;padding-top:10px;font-size:11.5px;color:#5F7074}}
.print{{position:fixed;right:16px;bottom:16px;padding:10px 16px;border-radius:8px;border:0;background:#1F5F66;color:#fff;font:inherit;font-weight:600;cursor:pointer}}
@media print{{.print{{display:none}} .wrap{{padding:0}}}}
</style></head><body><div class="wrap">
<header><div>{f'<img src="{_e(logo)}" alt="{_e(name)}">' if logo else f'<b style="font-size:18px">{_e(name)}</b>'}</div>
<div class="by">Website Check<br>{_e(when)}</div></header>
<h1>{_e(rep.get('host') or '')}</h1>
<div class="muted">{_e(rep.get('url') or '')} · {_e(rep.get('pages_read', 0))} pages read · {'full' if rep.get('depth') == 'full' else 'quick'} check</div>
<div class="hero"><div><div class="big">{overall}<span style="font-size:20px;color:#5F7074">/100</span></div>
<div style="max-width:220px;margin-top:6px">{_e(rep.get('summary') or '')}</div>
{(f'<div class="muted" style="max-width:240px;margin-top:6px">SEO rulebook: {_e((rep.get("rulebook") or {}).get("score"))}/100 '
  f'({_e((rep.get("rulebook") or {}).get("grade"))}) — {_e((rep.get("rulebook") or {}).get("checks"))} checks on every page read.</div>')
 if (rep.get("rulebook") or {}).get("score") is not None else ''}</div>
<div class="scores">{sec_rows}</div></div>
{prog_html}
<section class="box"><h2>The fixes that matter most</h2><ol class="tops">{tops or '<li>Nothing important was found.</li>'}</ol></section>
<section class="box"><h2>What is working well</h2><ul class="ok">{working or '<li>—</li>'}</ul></section>
<h2 style="margin-top:28px">Everything that was found</h2>
{groups or '<p class="muted">Nothing.</p>'}
<footer>Prepared by {_e(name or 'Botix')}. This check only read what any visitor's browser receives: it followed the site's
robots.txt, read at most one page a second, and changed nothing. Security was checked passively — nothing was probed or tested.
Scores summarise the findings; each finding names the page it was seen on.</footer>
</div><button class="print" onclick="window.print()">Print or save as PDF</button></body></html>"""


def find_browser() -> str:
    """Edge or Chrome already on this computer — they print a page to PDF themselves."""
    named = os.environ.get("WEBSITE_CHECK_BROWSER", "").strip()
    if named and Path(named).exists():
        return named
    cands = []
    if os.name == "nt":
        for env in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA"):
            root = os.environ.get(env)
            if root:
                cands += [Path(root) / "Microsoft/Edge/Application/msedge.exe",
                          Path(root) / "Google/Chrome/Application/chrome.exe"]
    elif sys.platform == "darwin":
        cands += [Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
                  Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
                  Path("/Applications/Chromium.app/Contents/MacOS/Chromium")]
    else:
        for n in ("google-chrome", "chromium", "chromium-browser", "microsoft-edge"):
            w = shutil.which(n)
            if w:
                cands.append(Path(w))
        cands.append(Path("/opt/pw-browsers/chromium"))
    return next((str(p) for p in cands if p.exists()), "")


def pdf(cid, out_path) -> dict:
    chk = get(cid)
    if not chk.get("ok") or not chk.get("report"):
        return {"ok": False, "error": "that check has no report yet"}
    exe = find_browser()
    if not exe:
        return {"ok": False, "no_browser": True,
                "error": "No Chrome or Edge was found on this computer to make the PDF. Open the report and use "
                         "Print, then Save as PDF."}
    tmp = Path(tempfile.mkdtemp(prefix="website-check-"))
    page = tmp / "report.html"
    page.write_text(report_html(chk), encoding="utf-8")
    out = Path(out_path)
    cmd = [exe, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
           f"--user-data-dir={tmp / 'profile'}", "--no-pdf-header-footer", "--print-to-pdf-no-header",
           f"--print-to-pdf={out}", page.resolve().as_uri()]
    if os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() == 0:
        cmd.insert(1, "--no-sandbox")
    try:
        subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90)
    except Exception as e:
        return {"ok": False, "error": f"the browser could not make the PDF: {type(e).__name__}"}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if not out.exists() or out.stat().st_size < 1000:
        return {"ok": False, "error": "the browser did not produce a PDF. Open the report and use Print, then Save as PDF."}
    return {"ok": True, "path": str(out), "bytes": out.stat().st_size,
            "filename": f"website-check-{re.sub(r'[^A-Za-z0-9.-]+', '-', chk['host'])}-{time.strftime('%Y-%m-%d')}.pdf"}


# ---------------------------------------------------------------------------

def _print(r):
    if not r.get("ok"):
        print("Could not check it: " + r.get("error", ""))
        return
    print(f"\n{r['host']} — {r['overall']}/100 · {r['summary']}\n")
    for k, label in SECTORS:
        print(f"  {label:<24} {r['scores'][k]:>3}")
    by_key = {f["code"] + "|" + f["sector"]: f for f in r["findings"]}
    print("\nThe fixes that matter most:")
    for k in r["top"]:
        f = by_key[k]
        print(f"  - {f['title']} ({f['impact']}, {f['effort_label']})\n      {f['fix']}")
    print()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="A report on any website.")
    ap.add_argument("--url")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--new", metavar="URL", help="make a check and print its id (the office runs it)")
    ap.add_argument("--depth", default="quick")
    ap.add_argument("--run", type=int, metavar="ID")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--get", type=int, metavar="ID")
    ap.add_argument("--html", type=int, metavar="ID")
    ap.add_argument("--pdf", nargs=2, metavar=("ID", "OUT"))
    ap.add_argument("--delete", type=int, metavar="ID")
    ap.add_argument("--stop", type=int, metavar="ID")
    ap.add_argument("--brand", action="store_true", help="show the report's name and logo")
    ap.add_argument("--save-brand", action="store_true", help="read {name, logo, remove_logo} on stdin")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    def emit(obj):
        print(json.dumps(obj, ensure_ascii=False))
    if a.new:
        emit(new(a.new, a.depth))
    elif a.run:
        r = run(a.run)
        emit({"ok": r.get("ok"), "id": a.run, "error": r.get("error", "")}) if a.json else _print(r)
    elif a.list:
        emit(listing())
    elif a.get:
        emit(get(a.get))
    elif a.html:
        chk = get(a.html)
        if not chk.get("report"):
            emit({"ok": False, "error": "no report yet"})
            return 1
        sys.stdout.write(report_html(chk))
    elif a.pdf:
        emit(pdf(int(a.pdf[0]), a.pdf[1]))
    elif a.delete:
        emit(delete(a.delete))
    elif a.stop:
        emit(stop(a.stop))
    elif a.brand:
        b = brand()
        emit({"ok": True, "name": b.get("name", ""), "has_logo": bool(b.get("logo")), "logo": b.get("logo", "")})
    elif a.save_brand:
        d = json.loads(sys.stdin.read() or "{}")
        emit(save_brand(d.get("name"), d.get("logo"), bool(d.get("remove_logo"))))
    elif a.url:
        made = new(a.url, "full" if a.full else "quick")
        if not made.get("ok"):
            emit(made) if a.json else print("Could not check it: " + made["error"])
            return 1
        r = run(made["id"])
        emit(r) if a.json else _print(r)
        return 0 if r.get("ok") else 1
    else:
        ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
