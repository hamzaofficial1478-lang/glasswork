"""
site_blueprint.py — what a complete website for this business has, set against
his own site, with every gap handed to the desk that owns it.

    python -m pipelines.site_blueprint                 check (at most every few days) and hand gaps on
    python -m pipelines.site_blueprint --now           check now, however recent the last check
    python -m pipelines.site_blueprint --plan          check now, hand nothing on
    python -m pipelines.site_blueprint --stored --json the last check, from the cabinet (no requests)

"the program's internal intellegence ... what on website or what content or
 pages on website should have or the important files for indexing like
 sitemap or other files program should be able to check like what website is
 missing or what should be in the website that makes the website setup
 complete"

Every desk already looked at the site through its own window: the Watcher at
the enquiry path and speed, the SEO audit at each page's tags, the answer audit
at what AI assistants read. Nobody held the whole picture: does this website
have everything a website for this business should have? That is this file.

THE BLUEPRINT, IN FOUR PARTS

  Files machines read    robots.txt (there, not shutting search engines out,
                         naming the sitemap), a sitemap listing every page,
                         llms.txt, a favicon, a real "not found" answer
  The business, for      business details as schema markup on the home page,
  machines               the page's language, a preview card for links shared
                         on WhatsApp and social media
  Pages a visitor        contact, about, real projects, reviews, questions
  looks for              answered, privacy; a page for each service he sells;
                         pages for the main areas he works in; somewhere for news
  Trust                  his address written on the site

AND EVERY PAGE, AGAINST THE SEO RULEBOOK

  Every page read is also run through pipelines/seo_rules — some eighty
  weighted checks (titles, descriptions, headings, pictures, links, markup,
  speed, safety, phones, sharing) — and the blueprint's own file checks count
  in the same score. One score out of 100, a grade, a score per category, and
  each problem once, naming its pages, worst first. These are the owner's to
  put right with the Fix button (pipelines/seo_fixer); they are not handed to
  a desk here, because the SEO audit already files its own tickets for the
  same pages and two tickets for one problem is how work gets done twice.

Built from his business profile, not a fixed list: the services and areas are
his, so a plumber in Leeds gets a plumber's blueprint. Page kinds are matched
in English; on a site in another language only his own service names, the
files and the markup are checked, rather than calling pages missing that are
simply named in another language.

WHO GETS WHAT

  IT        the files and the markup, filed as tickets the Fixer reads. The ones
            with exactly one right answer — robots.txt, llms.txt, the sitemap,
            the business markup — become a change waiting for his yes.
  SEO       pages for services and areas. SEO decides whether the search is
            worth a page before Content writes a word (the handbook's rule), and
            the FAQ, which is answer-engine work.
  Content   about, projects, reviews, privacy, news.

Anything that is not IT's goes to the MD as a proposal with the evidence
attached, so it is ranked against everything else the business needs rather
than jumping the queue. A desk is never handed more than OPEN_ASKS of these at
once; the next gap goes in as one is done, so a long list becomes steady work
instead of a pile nobody can finish. A gap that has gone is closed on the next
check, and a proposal still waiting for it is withdrawn with the reason.

GENTLE ON HIS SITE

Every request goes through coordination/sitepace like every other desk's: one a
second, across every desk, and a rest the moment his site objects. A check is
at most MOST_PAGES pages plus a handful of files, and on its own it runs at most
every CHECK_EVERY hours; the app reads the stored result in between.
"""
import argparse
import json
import re
import sqlite3
import sys
import time
import urllib.parse

from coordination import board

AGENT = "it-watcher"          # it looks and files; it never writes to the site
MOST_PAGES = 80
CHECK_EVERY = 72 * 3600
UA = "Mozilla/5.0 (compatible; botix-blueprint/1.0; read-only)"
URGENCY = {"high": 2, "medium": 4, "low": 6}
SEV_ORDER = {"high": 0, "medium": 1, "low": 2}
# At most this many of the blueprint's proposals open per desk at once.
OPEN_ASKS = {"SEO": 4, "Content": 3, "Leads": 2}
MOST_AREAS = 3
# A proposal the MD turned down is not asked again inside this window.
RESPECT_NO = 14 * 86400
# Codes the blueprint used to check itself, now checked page by page by the
# SEO rulebook. Any still open from before are closed, with their tickets.
RETIRED = {"titles", "titles_repeat", "descriptions", "headings", "image_alt", "noindex", "canonical",
           "lang_missing", "og_missing"}

# Changes with exactly one right answer: the Fixer queues these for his yes.
CALLS = {"robots_missing": "put_robots", "robots_no_sitemap": "put_robots",
         "llms_missing": "put_llms", "sitemap_missing": "publish_sitemap",
         "sitemap_incomplete": "publish_sitemap", "schema_missing": "apply_schema"}

_TAG = re.compile(r"<[^>]+>")
_SCRIPTISH = re.compile(r"<(script|style|noscript|template)\b.*?</\1\s*>", re.I | re.S)

# (code, sector, severity, pattern, title, why, a section on the home page counts)
PAGE_KINDS = (
    ("contact", "Leads", "high", r"\b(contact|get in touch|enquir\w*|request a quote|get a quote)\b",
     "A contact page",
     "The page people look for when they are ready to ask for a price. Without one, a "
     "visitor who wants to call has to hunt, and many will not.", False),
    ("projects", "Content", "high", r"\b(projects?|portfolio|gallery|our work|recent work|case stud\w*)\b",
     "A page of real, finished projects",
     "Real completed jobs with photographs are what wins work: they prove the "
     "standard before anyone rings. It is the strongest page a trade website has.", True),
    ("about", "Content", "medium", r"\b(about|who we are|our story|our company)\b",
     "An about page",
     "People hiring someone to work in their home or office want to know who they "
     "are dealing with: how long, who, and why to trust them.", False),
    ("reviews", "Content", "medium", r"\b(reviews?|testimonials?|what (?:our )?(?:clients|customers) say)\b",
     "Reviews from customers",
     "What other customers say does more than anything the business says about "
     "itself. A section on the home page is enough.", True),
    ("faq", "SEO", "medium", r"\b(faqs?|frequently asked|questions)\b",
     "Common questions answered",
     "The questions people type into Google and ask AI assistants. A page that "
     "answers them plainly is the one that gets picked and quoted.", True),
    ("privacy", "Content", "medium", r"\bprivacy\b",
     "A privacy notice",
     "The site takes names and numbers through its enquiry form, so it should say "
     "what happens to them. Data protection law expects it, and so do careful customers.", False),
    ("blog", "Content", "low", r"\b(blog|news|articles?|insights|updates|guides?)\b",
     "Somewhere to post news and guides",
     "New jobs, advice and guides give Google fresh reasons to visit and give people "
     "a reason to share the site.", False),
)

ADVICE = {
    "robots_blocks_all": ["Open robots.txt on your hosting (in the folder your domain shows).",
                          "If it says 'Disallow: /' under 'User-agent: *' and you did not mean "
                          "to hide the whole site, delete that line, or tell IT to replace it.",
                          "If the site is still being built and you meant it, leave it until launch."],
    "favicon_missing": ["Send the agency your logo as a square picture (at least 512 by 512).",
                        "IT will turn it into the little icon browsers and Google show next to "
                        "your name."],
    "soft_404": ["Ask your hosting company or the person who built the site to make missing "
                 "pages answer '404 Not Found' instead of showing the home page.",
                 "Until then Google can list addresses that are not real pages."],
    "lang_missing": ["The page does not say which language it is written in.",
                     "Add lang=\"en\" (or your language) to the <html> tag in the header file, "
                     "or tell IT which file holds the header."],
    "og_missing": ["Choose one good photograph of your work to show when someone shares your site.",
                   "IT can then add the preview tags to the header so WhatsApp, Facebook and "
                   "LinkedIn show your name, a line about you and that photograph."],
    "noindex": ["Open each page named and look in its <head> for a robots meta tag saying 'noindex'.",
                "If the page should be found on Google, remove that tag (or tell IT which file holds it).",
                "If you hid it on purpose (a thank-you page, a test page), leave it."],
    "canonical": ["IT can add the right address to each page's head once the header file is known (see How your "
                  "website is built). Say yes when it asks, or tell IT to go ahead."],
    "sitemap_theirs_incomplete": ["Your sitemap is made by something other than the agency (a "
                                  "plugin or the site builder).",
                                  "Either let it rebuild (most plugins have a button), or tell IT to "
                                  "put the agency's sitemap up instead and give that to Google."],
}


def _now():
    return int(time.time())


def _conn():
    return board._conn()


def _table(c):
    c.execute("""CREATE TABLE IF NOT EXISTS site_gaps (
        code TEXT PRIMARY KEY, part TEXT, sector TEXT, severity TEXT, title TEXT,
        why TEXT, evidence TEXT, fix TEXT, page TEXT, status TEXT DEFAULT 'open', ref TEXT,
        first_seen INTEGER, last_seen INTEGER, closed_at INTEGER)""")
    c.execute("""CREATE TABLE IF NOT EXISTS site_gaps_runs (
        at INTEGER PRIMARY KEY, home TEXT, pages INTEGER, partial INTEGER DEFAULT 0,
        have TEXT, pages_seen TEXT, note TEXT)""")
    # Added with the SEO rulebook: the pages a problem is on, how much it
    # matters, what a person does, pass/warn/fail; and each check's score.
    for table, col, typ in (("site_gaps", "pages", "TEXT"), ("site_gaps", "priority", "REAL"),
                            ("site_gaps", "how", "TEXT"), ("site_gaps", "level", "TEXT"),
                            ("site_gaps", "cat", "TEXT"), ("site_gaps_runs", "score", "TEXT")):
        try:
            c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError:
            pass


# ---------------------------------------------------------------------------
# reading the site
# ---------------------------------------------------------------------------

def _getter():
    """GET (or HEAD) through requests, which coordination/sitepace paces."""
    import requests
    s = requests.Session()
    s.headers["User-Agent"] = UA

    def get(url, head=False):
        r = s.request("HEAD" if head else "GET", url, timeout=20, allow_redirects=True)
        meta = {"headers": dict(r.headers), "ms": int(r.elapsed.total_seconds() * 1000),
                "final": r.url, "hops": len(r.history)}
        return r.status_code, ("" if head else (r.text or "")), r.headers.get("Content-Type") or "", meta
    return get


def _is_html(body, ctype):
    return "html" in (ctype or "").lower() or (body or "").lstrip()[:15].lower().startswith(("<!doctype", "<html"))


def _file(get, url, head=False) -> dict:
    try:
        st, body, ct = (get(url, head=head) if head else get(url))[:3]
    except Exception as e:
        return {"status": None, "text": "", "error": type(e).__name__}
    # A missing file that answers with the home page is not the file.
    text = "" if (_is_html(body, ct) and not url.endswith((".php", "/"))) else (body or "")
    return {"status": st, "text": text, "type": ct}


def _plain(html):
    return re.sub(r"\s+", " ", _TAG.sub(" ", _SCRIPTISH.sub(" ", html or ""))).strip()


def _first(pattern, html):
    m = re.search(pattern, html or "", re.I | re.S)
    return _plain(m.group(1))[:160] if m else ""


def _slug(url):
    path = urllib.parse.urlparse(url).path.lower()
    path = re.sub(r"\.(php|html?|aspx?)$", "", path)
    return re.sub(r"[-_/.]+", " ", path).strip()


def read_site(home, get=None, most=MOST_PAGES) -> dict:
    """The pages a visitor can reach from the home page, plus the files machines read."""
    from . import sitemap
    get = get or _getter()
    home = home.rstrip("/")
    bodies, metas = {}, {}

    def page_get(u):
        r = get(u)
        st, body, ct = r[:3]
        html = body if _is_html(body, ct) else ""
        bodies[u] = html
        metas[u] = r[3] if len(r) > 3 else {}
        return st, html
    d = sitemap.discover(home, most=most, get=page_get)
    # EVERY PAGE HIDDEN is a finding, not a failure to read. WordPress's
    # "Discourage search engines" box hides the whole site; the walk then lists
    # nothing, and the check has to say so rather than "could not be read".
    all_hidden = not d.get("ok") and any("noindex" in why and bodies.get(u)
                                         for u, why in (d.get("left_out") or {}).items())
    if not d.get("ok") and not all_hidden:
        return {"ok": False, "error": d.get("error") or "the home page could not be read"}
    pages = []
    for u in d["pages"]:
        html = bodies.get(u, "")
        pages.append({"url": u, "slug": _slug(u), "html": html,
                      "title": _first(r"<title[^>]*>(.*?)</title>", html),
                      "h1": _first(r"<h1[^>]*>(.*?)</h1>", html),
                      "headers": (metas.get(u) or {}).get("headers") or {}, "ms": (metas.get(u) or {}).get("ms")})
    left = d.get("left_out") or {}
    # Pages that ask Google not to list them are not in the walk, but the
    # rulebook needs to see them: a hidden page is a finding, and a hidden home
    # page caps the score.
    hidden = [{"url": u, "html": bodies[u], "headers": (metas.get(u) or {}).get("headers") or {},
               "ms": (metas.get(u) or {}).get("ms")} for u, why in left.items() if "noindex" in why and bodies.get(u)]
    # A link a visitor can click that answered an error is a broken link.
    link_status = {}
    for u, why in left.items():
        m = re.match(r"answered (\d{3})", why or "")
        if m:
            link_status[u] = int(m.group(1))
    files = {name: _file(get, f"{home}/{name}") for name in ("robots.txt", "llms.txt", "sitemap.xml")}
    # Someone else's sitemap may live elsewhere: WordPress's own (wp-sitemap.xml),
    # Yoast's and Rank Math's (sitemap_index.xml), or wherever robots.txt says.
    if not _is_sitemap(files["sitemap.xml"]):
        for u in _declared_sitemaps((files.get("robots.txt") or {}).get("text"), home) + \
                [f"{home}/wp-sitemap.xml", f"{home}/sitemap_index.xml"]:
            f = _file(get, u)
            if _is_sitemap(f):
                files["sitemap.xml"] = {**f, "url": u}
                break
    files["sitemap.php"] = _file(get, sitemap.live_url(home))
    files["favicon.ico"] = _file(get, f"{home}/favicon.ico", head=True)
    probe = _file(get, f"{home}/botix-no-such-page-{_now() % 100000}")
    return {"ok": True, "home": home + "/", "pages": pages, "files": files,
            "not_found_status": probe.get("status"), "partial": bool(d.get("stopped")),
            "left_out": left, "hidden": hidden, "link_status": link_status, "all_hidden": all_hidden,
            **_addresses(get, home, metas.get(home + "/") or {})}


def _is_sitemap(f) -> bool:
    return (f or {}).get("status") == 200 and bool(re.search(r"<(urlset|sitemapindex)", (f or {}).get("text") or ""))


def _declared_sitemaps(robots, home) -> list:
    """Sitemap addresses robots.txt names on his own site (other sites' are not his)."""
    host = urllib.parse.urlparse(home).netloc.lower().removeprefix("www.")
    out = []
    for u in re.findall(r"(?im)^\s*sitemap\s*:\s*(\S+)", robots or ""):
        if urllib.parse.urlparse(u).netloc.lower().removeprefix("www.") == host and u not in out:
            out.append(u)
    return out[:3]


def _addresses(get, home, home_meta) -> dict:
    """
    Is the site one address? The twin (with or without www) and the plain-http
    address, each asked once. Only when the reader can say where a request
    ended up; otherwise the two rules stay unmeasured rather than guessed.
    """
    if not home_meta.get("final"):
        return {"hosts": {}, "http_redirect": None}
    p = urllib.parse.urlparse(home)
    host = p.netloc
    twin = host[4:] if host.lower().startswith("www.") else "www." + host
    hosts = {f"{p.scheme}://{host}/": {"status": 200, "final": home_meta.get("final")}}
    try:
        r = get(f"{p.scheme}://{twin}/")
        if len(r) > 3:
            hosts[f"{p.scheme}://{twin}/"] = {"status": r[0], "final": r[3].get("final") or ""}
    except Exception:
        pass                                # no such address at all: one site, nothing to say
    http_redirect = None
    if p.scheme == "https":
        try:
            r = get(f"http://{host}/")
            if len(r) > 3 and r[3].get("final"):
                http_redirect = urllib.parse.urlparse(r[3]["final"]).scheme == "https"
        except Exception:
            http_redirect = None
    return {"hosts": hosts, "http_redirect": http_redirect}


# ---------------------------------------------------------------------------
# the blueprint
# ---------------------------------------------------------------------------

def _gap(code, part, sector, severity, title, why, evidence, page=""):
    return {"code": code, "part": part, "sector": sector, "severity": severity, "title": title,
            "why": why, "evidence": evidence, "fix": CALLS.get(code, ""), "page": page}


def _robots_blocks_all(text) -> bool:
    """'Disallow: /' in the group every robot reads, with no 'Allow: /' beside it."""
    groups, agents, rules, after_agent = [], [], [], False
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        k, v = (x.strip() for x in line.split(":", 1))
        k = k.lower()
        if k == "user-agent":
            if not after_agent and (agents or rules):
                groups.append((agents, rules))
                agents, rules = [], []
            agents.append(v)
            after_agent = True
        else:
            if k in ("allow", "disallow"):
                rules.append((k, v))
            after_agent = False
    if agents or rules:
        groups.append((agents, rules))
    return any("*" in a and ("disallow", "/") in r and ("allow", "/") not in r for a, r in groups)


def _sitemap_locs(text):
    return [u.strip() for u in re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", text or "", re.I)]


def _same(u):
    p = urllib.parse.urlparse(u)
    host = (p.netloc or "").lower()
    host = host[4:] if host.startswith("www.") else host
    path = re.sub(r"/index\.(php|html?)$", "/", p.path or "/").rstrip("/") or "/"
    return host + path + (("?" + p.query) if p.query else "")


def _english() -> bool:
    try:
        from coordination import profile
        return (profile.language() or "en").lower().startswith("en")
    except Exception:
        return True


def _services():
    """[(key, label, names)] in the order he sells them."""
    try:
        from coordination import profile
        svc = profile.services()
        out = []
        for k in profile.service_order():
            v = svc.get(k) or {}
            names = [str(n).lower() for n in (v.get("names") or ()) if str(n).strip()]
            label = str(v.get("label") or k.replace("_", " "))
            # Too general to count on its own: 'shower' or 'railing' appears on pages about anything.
            names = [n for n in names if " " in n or len(n) >= 9] or names[:1]
            out.append((k, label, sorted(set(names + [label.lower()]), key=len)))
        return out
    except Exception:
        return []


def _trade():
    try:
        from coordination import profile
        return profile.trade_line()
    except Exception:
        return "your trade"


def _areas():
    try:
        from coordination import uae
        served = list(uae.served())
    except Exception:
        served = []
    return served[:MOST_AREAS] if len(served) > 1 else []


def assess(site) -> dict:
    """{gaps, have, checked}: what a complete site has that this one does not."""
    gaps, have, checked = [], [], set()
    pages, files = site["pages"], site["files"]
    home = pages[0] if pages else {"html": "", "url": site["home"]}
    hh = home.get("html") or ""

    def ok(code, title):
        checked.add(code)
        have.append({"code": code, "title": title})

    def miss(*a, **kw):
        checked.add(a[0])
        gaps.append(_gap(*a, **kw))

    # ---- files machines read -------------------------------------------
    rb = files.get("robots.txt") or {}
    rtext = rb.get("text") or ""
    has_robots = rb.get("status") == 200 and bool(rtext.strip())
    if not has_robots:
        miss("robots_missing", "files", "IT", "medium", "A robots.txt file",
             "The first file search engines ask for. Without it they guess, and the "
             "sitemap is not pointed out to them.",
             f"{site['home']}robots.txt answered {rb.get('status') or 'nothing'}")
    else:
        ok("robots_missing", "A robots.txt file")
        if _robots_blocks_all(rtext):
            miss("robots_blocks_all", "files", "IT", "high", "robots.txt lets search engines in",
                 "It tells every search engine to stay out of the whole site, so nothing "
                 "on it can be found on Google.", "robots.txt has 'Disallow: /' for every robot")
        else:
            ok("robots_blocks_all", "robots.txt lets search engines in")
        if re.search(r"(?im)^\s*sitemap\s*:", rtext):
            ok("robots_no_sitemap", "robots.txt names the sitemap")
        else:
            miss("robots_no_sitemap", "files", "IT", "low", "robots.txt names the sitemap",
                 "One line in robots.txt tells every search engine, not only Google, where the "
                 "list of pages is.", "robots.txt has no 'Sitemap:' line")

    ours, theirs = files.get("sitemap.php") or {}, files.get("sitemap.xml") or {}
    ours_ok = ours.get("status") == 200 and "<urlset" in (ours.get("text") or "")
    theirs_ok = theirs.get("status") == 200 and re.search(r"<(urlset|sitemapindex)", theirs.get("text") or "")
    if not (ours_ok or theirs_ok):
        miss("sitemap_missing", "files", "IT", "high", "A sitemap",
             "The list of every page Google should know about. Without it Google finds "
             "pages only by following links, slowly, and Search Console has nothing to read.",
             "no sitemap answered: not sitemap.php, sitemap.xml, wp-sitemap.xml, sitemap_index.xml "
             "or one named in robots.txt")
    else:
        ok("sitemap_missing", "A sitemap")
        sm = ours if ours_ok else theirs
        if "<sitemapindex" in (sm.get("text") or "")[:600].lower():
            ok("sitemap_incomplete", "The sitemap lists every page")
        else:
            listed = {_same(u) for u in _sitemap_locs(sm.get("text"))}
            unlisted = [p["url"] for p in pages if _same(p["url"]) not in listed]
            if unlisted and not site.get("partial"):
                code = "sitemap_incomplete" if ours_ok else "sitemap_theirs_incomplete"
                miss(code, "files", "IT", "medium", "The sitemap lists every page",
                     "Pages missing from the sitemap are found late or not at all.",
                     f"{len(unlisted)} page(s) a visitor can reach are not in it, e.g. "
                     + ", ".join(urllib.parse.urlparse(u).path or "/" for u in unlisted[:3]))
            else:
                ok("sitemap_incomplete", "The sitemap lists every page")

    lf = files.get("llms.txt") or {}
    if lf.get("status") == 200 and (lf.get("text") or "").strip():
        ok("llms_missing", "An llms.txt for AI assistants")
    else:
        miss("llms_missing", "files", "IT", "low", "An llms.txt for AI assistants",
             "A short plain summary of the business and its main pages, written for ChatGPT, "
             "Gemini and the like. New, cheap, and it helps them describe the business correctly.",
             f"{site['home']}llms.txt answered {lf.get('status') or 'nothing'}")

    fav = files.get("favicon.ico") or {}
    # a host that answers every address with the home page "has" /favicon.ico too
    real_ico = fav.get("status") == 200 and "html" not in (fav.get("type") or "").lower()
    if re.search(r"<link\b[^>]*rel=[\"'][^\"']*icon", hh, re.I) or real_ico:
        ok("favicon_missing", "A favicon")
    else:
        miss("favicon_missing", "files", "IT", "low", "A favicon",
             "The small logo browsers show on the tab and Google shows beside the name in "
             "results. Without it the site looks unfinished.", "no icon link on the home page and no /favicon.ico")

    nf = site.get("not_found_status")
    if nf is not None:
        if nf == 200:
            miss("soft_404", "files", "IT", "medium", "Missing pages answer 'not found'",
                 "An address that does not exist shows a normal page, so Google can list "
                 "nonsense addresses and the real pages count for less.",
                 "a made-up address answered 200 instead of 404")
        else:
            ok("soft_404", "Missing pages answer 'not found'")

    # ---- the business, for machines --------------------------------------
    if re.search(r'"@type"\s*:\s*\[?\s*"[^"]*(LocalBusiness|HomeAndConstructionBusiness|GeneralContractor|'
                 r'Contractor|Plumber|Electrician|Locksmith|HVACBusiness|RoofingContractor|Store|'
                 r'ProfessionalService)"', hh):
        ok("schema_missing", "Business details as schema markup")
    else:
        miss("schema_missing", "markup", "IT", "medium", "Business details as schema markup",
             "Name, phone, address, hours and areas written so Google and AI assistants can read "
             "them for certain. It is what local results and answer boxes are built from.",
             "the home page carries no LocalBusiness markup", page=home["url"])
    # The page's language and the preview card are checked on every page by
    # the rulebook below (i18n-lang, social-og), so they are not asked twice.

    # ---- pages a visitor looks for ---------------------------------------
    whole = not site.get("partial") and not site.get("all_hidden")
    if whole:
        words = [" ".join((p["slug"], p["title"], p["h1"])).lower() for p in pages]
        sections = " ".join(re.findall(r"<h[1-4][^>]*>(.*?)</h[1-4]>", hh, re.I | re.S)).lower()
        sections = _plain(sections)
        if _english():
            for code, sector, sev, pat, title, why, section_ok in PAGE_KINDS:
                found = next((p["url"] for p, w in zip(pages, words) if re.search(pat, w)), "")
                if found or (section_ok and re.search(pat, sections)):
                    ok(code, title)
                else:
                    miss(code, "pages", sector, sev, title, why,
                         f"none of the {len(pages)} page(s) read is one, by its address, title or heading")
        for i, (key, label, names) in enumerate(_services()):
            code = f"service:{key}"
            found = any(any(n in w for n in names) for w in words[1:])
            if found:
                ok(code, f"A page for {label}")
            else:
                miss(code, "pages", "SEO", "medium" if i < 3 else "low", f"A page for {label}",
                     f"People search for {label} by name. A page of its own, with real jobs and "
                     "plain answers, is what ranks; a line on the home page rarely does.",
                     f"no page's address, title or heading names it ({', '.join(names[:3])})")
        trade = _trade()
        for area in _areas():
            code = f"area:{area.lower()}"
            if any(area.lower() in w for w in words[1:]):
                ok(code, f"A page for {area}")
            else:
                miss(code, "pages", "SEO", "low", f"A page for work in {area}",
                     f"Searches like '{trade} in {area}' favour a page about that place, with "
                     "jobs done there.", f"no page's address, title or heading names {area}")

    # ---- his markets ---------------------------------------------------------
    # A page for each other country he sells in, and for a business selling
    # worldwide, one that says so — the page a search from there lands on.
    if whole:
        _markets(site, ok, miss)

    # ---- trust -------------------------------------------------------------
    contact = next((p for p in pages if re.search(r"\bcontact\b", p["slug"] + " " + p["title"].lower())), None)
    text = _plain(hh + " " + ((contact or {}).get("html") or ""))
    if not whole:
        pass                              # nothing could be read as a visitor sees it, so nothing is called missing
    elif re.search(r"PostalAddress|<address\b|itemprop=[\"']address", hh + ((contact or {}).get("html") or ""), re.I) \
            or re.search(r"\b(street|st\.|road|rd\.|avenue|building|bldg|floor|office\s+\d+|warehouse|"
                         r"p\.?\s?o\.?\s?box|industrial area)\b", text, re.I):
        ok("address", "The business address on the site")
    else:
        miss("address", "trust", "SEO", "medium", "The business address on the site",
             "An address (even just the area and building) tells people and Google the business "
             "is real and local, and it should match the Google Business Profile exactly.",
             "no address found on the home or contact page")

    # ---- every page, against the SEO rulebook ------------------------------
    #
    # "is basic seo in code is done or not ... the main seo gaps are the main
    #  points to cover from all over" — not the home page alone: every page
    # read, every rule, one score, and the worst first. One entry per kind of
    # problem, naming the pages, so twelve untitled pages are one job.
    seo = _rules(site, gaps, checked)
    for g in gaps:
        g.setdefault("priority", _priority(g))
    gaps.sort(key=lambda g: (-g["priority"], SEV_ORDER.get(g["severity"], 9), g["part"], g["code"]))
    return {"gaps": gaps, "have": have, "checked": sorted(checked), "seo": seo}


def _priority(g) -> float:
    """The rulebook's measure for a blueprint gap: its weight there, or by severity."""
    from . import seo_rules as R
    r = R.BY_ID.get(g["code"])
    if r:
        return float(r["weight"] * R.CAT_WEIGHT[r["cat"]])
    return float({"high": 200, "medium": 80, "low": 25}.get(g["severity"], 10))


def _rules(site, gaps, checked) -> dict:
    """Run the rulebook over every page read; add its problems to the gaps. Returns the score."""
    from . import seo_rules as R
    parsed = [R.parse(p["html"], p["url"], headers=p.get("headers"), ms=p.get("ms"))
              for p in site.get("pages") or [] if p.get("html")]
    hidden = [R.parse(h["html"], h["url"], headers=h.get("headers"), ms=h.get("ms"))
              for h in site.get("hidden") or []]
    if not parsed and hidden:
        # Every page hidden: they are the site, the home page first.
        parsed = sorted(hidden, key=lambda p: (not R._is_home(p), p["url"]))
        hidden = []
    if not parsed:
        return {}
    missing = {g["code"]: g for g in gaps}
    files = {}
    for code, *_rest in R.EXTERNAL:
        if code in missing:
            files[code] = (R.FAIL, missing[code]["evidence"])
        elif code in checked:
            files[code] = (R.PASS, "")
    sitemap_urls = []
    if not site.get("partial"):
        for name in ("sitemap.php", "sitemap.xml"):
            f = (site.get("files") or {}).get(name) or {}
            if f.get("status") == 200 and "<urlset" in (f.get("text") or ""):
                sitemap_urls = _sitemap_locs(f["text"])
                break
    out = R.check_site(parsed, {"files_checked": files, "hidden_pages": hidden,
                                "link_status": site.get("link_status") or {}, "sitemap_urls": sitemap_urls,
                                "hosts": site.get("hosts") or {}, "http_redirect": site.get("http_redirect")})
    for i in out["issues"]:
        r = R.BY_ID[i["id"]]
        if r.get("external"):
            continue                          # the blueprint's own gap already says it
        bad = i["status"] == R.FAIL
        sev = "high" if bad and i["weight"] >= 10 else "medium" if bad or i["weight"] >= 8 else "low"
        pages = list(i["pages"])
        if r["site"]:
            evidence = i["detail"] + (f": {_paths(pages)}" if pages else "")
        else:
            seen = (i["examples"][0].get("found") or "") if i["examples"] else ""
            evidence = (f"{len(pages)} of {i['measured']} page(s): {_paths(pages)}"
                        + (f" (e.g. {seen})" if seen and len(seen) < 90 else ""))
        g = _gap(i["id"], "rules", r["owner"], sev, i["name"], i["why"], evidence)
        g.update({"fix": i["fix"], "pages": pages, "priority": i["priority"], "how": i["how"],
                  "level": i["status"], "cat": i["cat"]})
        gaps.append(g)
        checked.add(i["id"])
    for x in out["passed"]:
        if not R.BY_ID[x["id"]].get("external"):
            checked.add(x["id"])
    out["passed_n"] = len(out.pop("passed"))
    out.pop("issues")
    return out


def _markets(site, ok, miss):
    try:
        from coordination import countries, profile
        ms, world, home = profile.markets(), profile.worldwide(), profile.home_country()
    except Exception:
        return
    words = [" ".join((p["slug"], p["title"], p["h1"])).lower() for p in site.get("pages") or []]
    for cc in [m for m in ms if m != home]:
        name = countries.name(cc)
        names = [w.lower() for w in (name,) + tuple(countries.aliases(cc)) + tuple(countries.cities(cc)) if w]
        code = f"market:{cc.lower()}"
        if any(any(n in w for n in names) for w in words):
            ok(code, f"A page for customers in {name}")
        else:
            miss(code, "pages", "SEO", "low", f"A page for customers in {name}",
                 f"You sell in {countries.in_a_sentence(cc)}. A page about your work there — the cities you "
                 "cover, how jobs are run from where you are — is what a search from there finds.",
                 f"no page's address, title or heading names {name} or its cities")
    if world:
        intl = ("international", "worldwide", "overseas", "export", "shipping", "global")
        if any(any(n in w for n in intl) for w in words):
            ok("worldwide", "A page for customers abroad")
        else:
            miss("worldwide", "pages", "SEO", "low", "A page for customers abroad",
                 "You work with customers worldwide. One page that says how — delivery, remote work, the "
                 "countries you have worked in — answers the first question a buyer abroad has.",
                 "no page says the business works internationally")


def _paths(urls, n=4):
    shown = [urllib.parse.urlparse(u).path or "/" for u in urls[:n]]
    return ", ".join(shown) + (f" and {len(urls) - n} more" if len(urls) > n else "")


# ---------------------------------------------------------------------------
# handing gaps on
# ---------------------------------------------------------------------------

def _detail(g):
    return f"[blueprint:{g['code']}] {g['title']} is missing. Found: {g['evidence']}. Why it matters: {g['why']}"


def _ref_alive(c, ref) -> bool:
    """Is the ticket or proposal for this gap still being dealt with?"""
    kind, _, rid = (ref or "").partition(":")
    if kind == "issue":
        r = c.execute("SELECT status FROM site_issues WHERE id=?", (rid,)).fetchone()
        return bool(r) and r[0] in ("open", "fixing", "in_review")
    if kind == "proposal":
        r = c.execute("SELECT status, decided_at FROM proposals WHERE id=?", (rid,)).fetchone()
        if not r:
            return False
        if r[0] in ("open", "accepted"):
            return True
        return r[0] == "rejected" and (_now() - int(r[1] or 0)) < RESPECT_NO
    return False


def hand_on(result, now=None, route_only=False) -> dict:
    """
    Store the gaps, close the ones that went, and route the rest — a few per
    desk at a time. route_only: nothing was read today; only hand on what has
    room now (a desk finished one, so the next goes in).
    """
    from coordination import proposals
    now = now or _now()
    gaps = {g["code"]: g for g in result.get("gaps") or []}
    checked = set(result.get("checked") or [])
    filed, proposed, closed = [], [], []
    with _conn() as c:
        board._migrate(c)
        _table(c)
        c.row_factory = sqlite3.Row
        rows = {r["code"]: dict(r) for r in c.execute("SELECT * FROM site_gaps")}
        for code, r in rows.items():
            retired = code in RETIRED
            if route_only or r["status"] != "open" or code in gaps or (code not in checked and not retired):
                continue
            c.execute("UPDATE site_gaps SET status='closed', closed_at=? WHERE code=?", (now, code))
            closed.append(code)
            kind, _, rid = (r["ref"] or "").partition(":")
            why = ("now checked page by page in the SEO score" if retired
                   else "the website has this now — the blueprint check found it")
            if kind == "issue":
                c.execute("UPDATE site_issues SET status='closed' WHERE id=? AND status IN ('open','fixing')", (rid,))
            elif kind == "proposal":
                c.execute("UPDATE proposals SET status='superseded', why=?, decided_at=? WHERE id=? AND status='open'",
                          (why, now, rid))
        for code, g in gaps.items():
            if route_only:
                break
            c.execute("INSERT INTO site_gaps(code,part,sector,severity,title,why,evidence,fix,page,status,"
                      "first_seen,last_seen,pages,priority,how,level,cat)"
                      " VALUES(?,?,?,?,?,?,?,?,?, 'open', ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(code) DO UPDATE SET"
                      " part=excluded.part, sector=excluded.sector, severity=excluded.severity,"
                      " title=excluded.title, why=excluded.why, evidence=excluded.evidence, fix=excluded.fix,"
                      " page=excluded.page, status='open', last_seen=excluded.last_seen, closed_at=NULL,"
                      " pages=excluded.pages, priority=excluded.priority, how=excluded.how, level=excluded.level,"
                      " cat=excluded.cat",
                      (code, g["part"], g["sector"], g["severity"], g["title"], g["why"], g["evidence"],
                       g["fix"], g.get("page") or "", now, now, json.dumps(g.get("pages") or []),
                       float(g.get("priority") or 0), g.get("how") or "", g.get("level") or "", g.get("cat") or ""))
        live = {code: _ref_alive(c, (rows.get(code) or {}).get("ref")) for code in gaps}
        busy = {}
        for code, alive in live.items():
            if alive and gaps[code]["sector"] != "IT":
                busy[gaps[code]["sector"]] = busy.get(gaps[code]["sector"], 0) + 1
        for code, g in sorted(gaps.items(), key=lambda kv: (SEV_ORDER.get(kv[1]["severity"], 9), kv[0])):
            # The rulebook's problems are his to fix with the button, and the
            # SEO audit already tickets the same pages: not routed again here.
            if live.get(code) or g.get("part") == "rules":
                continue
            if g["sector"] == "IT":
                iid, _new = board.file_site_issue("blueprint", g["severity"], _detail(g),
                                                  g.get("page") or "", AGENT, con=c)
                c.execute("UPDATE site_gaps SET ref=? WHERE code=?", (f"issue:{iid}", code))
                filed.append(code)
                continue
            if busy.get(g["sector"], 0) >= OPEN_ASKS.get(g["sector"], 2):
                continue
            needs = ("SEO: is the search worth a page? If yes, brief Content." if code.startswith(("service:", "area:"))
                     else "real photographs and job details from the owner" if code in ("projects", "reviews")
                     else "the owner's details, and someone to read it before it goes up" if code == "privacy"
                     else "")
            pending = {"sector": g["sector"], "title": g["title"], "code": code, "needs": needs,
                       "detail": (f"Found by the website blueprint check. {g['why']}"), "evidence": g["evidence"],
                       "urgency": URGENCY.get(g["severity"], 5)}
            proposed.append(pending)
            busy[g["sector"]] = busy.get(g["sector"], 0) + 1
    # Proposals are written on their own connection (proposals.propose opens one).
    for p in proposed:
        pid = proposals.propose(p["sector"], p["title"], p["detail"], p["evidence"], p["needs"], p["urgency"])
        if pid:
            with _conn() as c:
                c.execute("UPDATE site_gaps SET ref=? WHERE code=?", (f"proposal:{pid}", p["code"]))
    return {"filed": filed, "proposed": [p["code"] for p in proposed], "closed": closed}


def _last_run(c):
    r = c.execute("SELECT at, home, pages, partial, have, pages_seen, score FROM site_gaps_runs"
                  " ORDER BY at DESC LIMIT 1").fetchone()
    return r


def check(home=None, get=None, plan=False, force=False, now=None, map_files=None) -> dict:
    """Read the site, set it against the blueprint, and (unless plan) hand the gaps on."""
    from . import sitemap
    now = now or _now()
    if map_files is None:
        map_files = get is None          # stand-in sites in tests have no connector
    home = (home or sitemap.site_address() or "").rstrip("/")
    if not home:
        return {"ok": False, "error": "no website address is saved yet — add it under Website"}
    if not force and not plan:
        with _conn() as c:
            _table(c)
            last = _last_run(c)
        if last and now - int(last[0]) < CHECK_EVERY:
            routed = hand_on({"gaps": stored()["gaps"]}, now=now, route_only=True)
            hrs = (now - int(last[0])) // 3600
            return {"ok": True, "skipped": True, **routed,
                    "summary": f"checked {hrs} hour(s) ago; the next full look is in "
                               f"{max(1, (CHECK_EVERY - (now - int(last[0]))) // 3600)} hour(s)"}
    site = read_site(home, get=get)
    if not site.get("ok"):
        return {"ok": False, "error": site.get("error")}
    got = assess(site)
    out = {"ok": True, "home": site["home"], "pages": len(site["pages"]), "partial": site["partial"],
           "all_hidden": bool(site.get("all_hidden")),
           "gaps": got["gaps"], "have": got["have"], "checked": got["checked"], "seo": got.get("seo") or {}}
    # The same pages, matched to the files that make them (pipelines/site_map),
    # when the connector can read the files — one read of the site, two uses.
    if not plan and map_files:
        try:
            from . import site_api, site_map
            if not site_api.why_cannot_read():
                m = site_map.build(pages=site["pages"], partial=site["partial"])
                out["map"] = {k: m.get(k) for k in ("ok", "kind", "home_file", "head_file", "error")}
        except Exception as e:
            out["map"] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    if plan:
        out["summary"] = _summary(out)
        return out
    out.update(hand_on(got, now=now))
    with _conn() as c:
        _table(c)
        c.execute("INSERT OR REPLACE INTO site_gaps_runs(at,home,pages,partial,have,pages_seen,note,score)"
                  " VALUES(?,?,?,?,?,?,?,?)",
                  (now, site["home"], len(site["pages"]), 1 if site["partial"] else 0,
                   json.dumps(got["have"]),
                   json.dumps([{"url": p["url"], "title": p["title"]} for p in site["pages"]]),
                   _summary(out), json.dumps(got.get("seo") or {})))
    out["summary"] = _summary(out)
    return out


def _summary(r):
    g = len([x for x in r.get("gaps") or [] if x.get("part") != "rules"])
    h = len(r.get("have") or [])
    s = f"your website has {h} of {g + h} things a complete site has"
    seo = r.get("seo") or {}
    if seo.get("score") is not None and seo.get("grade"):
        s += f"; SEO score {seo['score']} out of 100 ({seo['grade']})"
    if r.get("filed") or r.get("proposed"):
        s += (f"; {len(r.get('filed') or [])} handed to IT, "
              f"{len(r.get('proposed') or [])} put to the MD")
    if r.get("partial"):
        s += " (the site asked us to slow down part-way, so page checks wait for the next look)"
    if r.get("all_hidden"):
        s += (" — every page asks Google not to list it, so the site cannot be found at all (on WordPress: "
              "Settings → Reading → 'Discourage search engines'). Fix it first")
    return s


def stored() -> dict:
    """The last check and the gaps still open, with where each one is. No requests."""
    with _conn() as c:
        board._migrate(c)
        _table(c)
        c.row_factory = sqlite3.Row
        last = _last_run(c)
        rows = [dict(r) for r in c.execute("SELECT * FROM site_gaps WHERE status='open'")]
        gone = [dict(r) for r in c.execute("SELECT code, title, closed_at, part FROM site_gaps WHERE status='closed'"
                                           " AND closed_at > ? ORDER BY closed_at DESC LIMIT 10",
                                           (_now() - 30 * 86400,))]
        for g in rows:
            g["pages"] = _json_list(g.get("pages"))
            g["where"] = "" if g.get("part") == "rules" else _where(c, g.get("ref"), g.get("sector"))
    # What the Fix button does for each, and how its last press went (pipelines/seo_fixer).
    try:
        from . import seo_fixer
        fixes = seo_fixer.last_for()
        for g in rows:
            g["fixable"] = seo_fixer.describe(g["code"], g.get("fix") or "", g.get("part") or "")
            g["last_fix"] = fixes.get(g["code"])
        for g in gone:
            g["last_fix"] = fixes.get(g["code"])
    except Exception:
        pass
    gaps = [g for g in rows if g.get("part") != "rules"]
    issues = [g for g in rows if g.get("part") == "rules"]
    for g in gaps:
        if not g.get("priority"):
            g["priority"] = _priority(g)
    gaps.sort(key=lambda g: (-(g.get("priority") or 0), SEV_ORDER.get(g["severity"], 9), g["part"] or "", g["code"]))
    issues.sort(key=lambda g: (-(g.get("priority") or 0), g["code"]))
    have = json.loads(last["have"] or "[]") if last else []
    seo = {}
    if last and last["score"]:
        try:
            seo = json.loads(last["score"]) or {}
        except ValueError:
            seo = {}
    if seo or issues:
        seo["issues"] = issues
    return {"ok": True, "checked_at": int(last["at"]) if last else 0, "home": last["home"] if last else "",
            "pages": int(last["pages"]) if last else 0, "partial": bool(last["partial"]) if last else False,
            "gaps": gaps, "have": have, "fixed_lately": gone, "seo": seo,
            "summary": (_summary({"gaps": gaps, "have": have, "seo": seo}) if last else "not checked yet")}


def _json_list(v):
    try:
        out = json.loads(v or "[]")
        return out if isinstance(out, list) else []
    except ValueError:
        return []


def _where(c, ref, sector) -> str:
    """Where a gap is, in his words."""
    kind, _, rid = (ref or "").partition(":")
    if kind == "issue":
        r = c.execute("SELECT status FROM site_issues WHERE id=?", (rid,)).fetchone()
        return {"open": "With IT", "fixing": "IT is on it", "in_review": "Waiting for your yes",
                "deployed": "Done — checking again soon", "closed": "Closed"}.get(r[0] if r else "", "With IT")
    if kind == "proposal":
        r = c.execute("SELECT status, why FROM proposals WHERE id=?", (rid,)).fetchone()
        if not r:
            return f"Waiting for {sector}"
        if r[0] == "open":
            return "With the MD"
        if r[0] == "accepted":
            return f"{sector} is on it"
        if r[0] == "rejected":
            return "The MD said not now: " + (r[1] or "")[:120]
    return f"Next in line for {sector}"


def sitemap_address(get=None) -> str:
    """The address of the sitemap that is live on his site, or ''."""
    from . import sitemap
    home = sitemap.site_address()
    if not home:
        return ""
    get = get or _getter()
    for url in (sitemap.live_url(home), home.rstrip("/") + "/sitemap.xml", home.rstrip("/") + "/wp-sitemap.xml",
                home.rstrip("/") + "/sitemap_index.xml"):
        f = _file(get, url)
        if f.get("status") == 200 and re.search(r"<(urlset|sitemapindex)", f.get("text") or ""):
            return url
    return ""


# ---------------------------------------------------------------------------
# the two files IT may write (site_api.put_robots / put_llms call these)
# ---------------------------------------------------------------------------

def robots_text(existing, sitemap_url):
    """robots.txt as it should be, or None when it is already right. His lines are kept."""
    if existing is None or not existing.strip():
        return "User-agent: *\nAllow: /\n" + (f"\nSitemap: {sitemap_url}\n" if sitemap_url else "")
    if re.search(r"(?im)^\s*sitemap\s*:", existing) or not sitemap_url:
        return None
    return existing.rstrip("\n") + f"\n\nSitemap: {sitemap_url}\n"


def _label(url, title=""):
    t = re.split(r"\s+[|\-–—]\s+", title or "")[0].strip()
    if t:
        return t[:80]
    slug = _slug(url)
    return (slug.split(" ")[-1] if not slug else slug).replace("  ", " ").strip().capitalize() or "Home"


def llms_text(pages, home="", name="", description="", areas="", phone="", email="") -> str:
    """A short plain summary for AI assistants: who, what, where, the main pages, how to reach them."""
    host = urllib.parse.urlparse(home).netloc
    L = [f"# {name or host or 'Our business'}", ""]
    if description:
        L += [f"> {description.strip()}", ""]
    if areas:
        L += [f"Works in {areas}.", ""]
    L += ["## Pages", ""]
    for p in pages[:40]:
        L.append(f"- [{_label(p['url'], p.get('title')) if p['url'].rstrip('/') != home.rstrip('/') else 'Home'}]({p['url']})")
    contact = [x for x in (f"- Phone: {phone}" if phone else "", f"- Email: {email}" if email else "",
                           f"- Website: {home}" if home else "") if x]
    if contact:
        L += ["", "## Contact", ""] + contact
    return "\n".join(L).rstrip() + "\n"


def llms_for_his_site(get=None) -> str:
    """llms.txt from his profile and the pages the last check read (or a fresh walk)."""
    from . import sitemap
    home = sitemap.site_address().rstrip("/") + "/"
    pages = []
    with _conn() as c:
        _table(c)
        last = _last_run(c)
    if last and _now() - int(last[0]) < 7 * 86400:
        pages = json.loads(last[5] or "[]")
    if not pages:
        d = sitemap.discover(home.rstrip("/"))
        pages = [{"url": u, "title": ""} for u in d.get("pages") or []]
    name, description, areas, phone, email = "", "", "", "", ""
    try:
        from coordination import profile, uae
        from providers import integrations
        env = integrations.read_env()
        b = profile.business()
        name = env.get("BUSINESS_NAME") or b.get("name") or ""
        description = b.get("description") or b.get("tagline") or ""
        areas = uae.served_phrase()
        phone = env.get("BUSINESS_PHONE") or ""
        from . import site_api
        email = site_api.right_email() or ""
    except Exception:
        pass
    return llms_text(pages, home=home, name=name, description=description, areas=areas, phone=phone, email=email)


# ---------------------------------------------------------------------------

def as_text(r) -> str:
    if not r.get("ok"):
        return "Could not check the website: " + str(r.get("error"))
    L = ["WHAT YOUR WEBSITE HAS, AND WHAT IT IS MISSING", "=" * 60, r.get("summary") or "", ""]
    seo = r.get("seo") or {}
    rules = [g for g in r.get("gaps") or [] if g.get("part") == "rules"] or seo.get("issues") or []
    if seo.get("score") is not None and seo.get("grade"):
        L.append(f"SEO score {seo['score']}/100 — {seo['grade']}, {seo.get('label', '')}"
                 + (f" (held down because {seo['capped_by']})" if seo.get("capped_by") else ""))
        for cat in seo.get("categories") or []:
            L.append(f"  {cat['name']:<32} {cat['score']}")
        for g in rules[:15]:
            L.append(f"  [{g.get('level') or g['severity']:<4}] {g['title']} — {g.get('evidence') or ''}")
        L.append("")
    for g in [x for x in r.get("gaps") or [] if x.get("part") != "rules"]:
        L.append(f"  [{g['severity']:<6}] {g['sector']:<8} {g['title']}")
        L.append(f"           {g.get('evidence') or ''}")
        if g.get("where"):
            L.append(f"           -> {g['where']}")
    if r.get("have"):
        L += ["", "Already there: " + "; ".join(h["title"] for h in r["have"][:30])]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="What a complete website has, and what this one is missing.")
    ap.add_argument("--now", action="store_true", help="check now, however recent the last check")
    ap.add_argument("--plan", action="store_true", help="check, hand nothing on")
    ap.add_argument("--stored", action="store_true", help="the last check, without reading the site")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    board.ensure_schema()
    if a.stored:
        r = stored()
    else:
        with board.agent_run(AGENT) as run:
            r = check(plan=a.plan, force=a.now)
            run.note("Blueprint: " + (r.get("summary") or r.get("error") or ""))
    print(json.dumps(r, indent=2, ensure_ascii=False, default=str) if a.json else as_text(r))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
