"""
seo_rules.py — the SEO rulebook: every check, its weight, and one score.

    python -m pipelines.seo_rules --url https://example.com/   one page, checked and scored

"seo sector needs more improvements ... cover up the messes will make the seo
 sector cleaner and to work more effiecivly"

Before this, three desks each kept their own short list of checks — the
technical audit, the answer audit and the website blueprint — with overlapping
wording and no way to say which problem mattered most. This is one rulebook,
built on the design of SEOmator (github.com/seo-skills/seo-audit-skill), the
open-source audit with hundreds of weighted rules, cut down to the checks that
can be made honestly from what a visitor's browser receives (no headless
render), and written for a small business owner to read.

HOW A SCORE IS MADE (the same arithmetic SEOmator uses, so it can be compared)

  each rule, each page   pass = 100, warn = 50, fail = 0, not measured = left out
  each category          the rules' scores averaged, each counted by its weight
  overall                the categories averaged, each counted by its weight
                         (the category weights add up to 100)
  caps                   while the home page asks Google not to list it the
                         overall score cannot pass 20; no title and almost no
                         words, 30; almost no words, 49; no title, 69 — so a
                         site Google cannot use never looks healthy
  grade                  A 90+, B 80+, C 70+, D 50+, F below

WHAT TO FIX FIRST

  priority = rule weight x category weight x how bad (fail 1, warn 0.5)
             x the share of pages it is on

so a missing title on every page outranks a slightly long description on one.

Every rule says, in words, what it found and on which pages, and whether the
Fix button can put it right (pipelines/seo_fixer) or what a person must do.
"""
import argparse
import html as _html
import json
import re
import sys
import urllib.parse
from html.parser import HTMLParser

PASS, WARN, FAIL, NA = "pass", "warn", "fail", "na"
SCORE = {PASS: 100, WARN: 50, FAIL: 0}
SEVERITY = {FAIL: 1.0, WARN: 0.5, PASS: 0.0, NA: 0.0}

# (id, name in his words, weight). Weights add up to 100.
CATEGORIES = (
    ("core", "The basics Google reads", 16),
    ("technical", "Files and server", 10),
    ("content", "Words on the pages", 9),
    ("images", "Pictures", 8),
    ("links", "Links", 8),
    ("schema", "Business details for machines", 7),
    ("speed", "Speed", 7),
    ("security", "Safety", 6),
    ("trust", "Trust", 6),
    ("crawl", "Being found and read", 5),
    ("mobile", "Phones", 5),
    ("social", "Sharing", 4),
    ("ai", "AI assistants", 4),
    ("url", "Page addresses", 3),
    ("i18n", "Language", 2),
)
CAT_WEIGHT = {c: w for c, _n, w in CATEGORIES}
CAT_NAME = {c: n for c, n, _w in CATEGORIES}
GENERIC_ANCHORS = {"click here", "here", "read more", "more", "learn more", "this", "link", "continue",
                   "details", "click", "go", "more info", "find out more"}


# ---------------------------------------------------------------------------
# reading a page
# ---------------------------------------------------------------------------

class _Reader(HTMLParser):
    """One pass over the page, keeping what the rules look at."""

    VOID = {"meta", "link", "img", "br", "hr", "input", "source", "area", "base", "col", "embed", "wbr", "track"}
    HIDDEN = {"script", "style", "noscript", "template", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.p = {"titles": [], "metas": [], "links": [], "scripts": [], "jsonld": [], "headings": [],
                  "imgs": [], "anchors": [], "forms": [], "iframes": [], "lang": "", "doctype": False,
                  "charset": False, "semantic": set(), "body_links": [], "body_metas": 0, "text": [],
                  "html_tags": 0}
        self.in_head, self.seen_body = False, False
        self.stack = []
        self.hidden = 0
        self.cur_title = None
        self.cur_h = None
        self.cur_a = None
        self.cur_script = None
        self.in_svg = 0

    def handle_decl(self, decl):
        if decl.lower().startswith("doctype"):
            self.p["doctype"] = True

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        tag = tag.lower()
        if tag == "html":
            self.p["html_tags"] += 1
            self.p["lang"] = self.p["lang"] or a.get("lang", "").strip()
        elif tag == "head":
            self.in_head = True
        elif tag == "body":
            self.in_head, self.seen_body = False, True
        if tag == "svg":
            self.in_svg += 1
        if tag in self.HIDDEN:
            self.hidden += 1
        if tag in ("main", "article", "nav", "header", "footer", "section", "aside"):
            self.p["semantic"].add(tag)
        head = self.in_head and not self.seen_body
        if tag == "title" and not self.in_svg:
            self.cur_title = []
        elif tag == "meta":
            a["_head"] = head
            self.p["metas"].append(a)
            if "charset" in a or a.get("http-equiv", "").lower() == "content-type":
                self.p["charset"] = True
            if not head and (a.get("name") or a.get("property")):
                self.p["body_metas"] += 1
        elif tag == "link":
            a["_head"] = head
            self.p["links"].append(a)
        elif tag == "script":
            a["_head"] = head
            self.cur_script = a
            a["_len"] = 0
            a["_text"] = []
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6") and not self.hidden:
            self.cur_h = [int(tag[1]), []]
        elif tag == "img":
            a["_n"] = len(self.p["imgs"])
            self.p["imgs"].append(a)
        elif tag == "a":
            self.cur_a = dict(a, _text=[])
        elif tag == "form":
            self.p["forms"].append(a)
        elif tag == "iframe":
            self.p["iframes"].append(a)
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "head":
            self.in_head = False
        if tag == "title" and self.cur_title is not None:
            self.p["titles"].append(re.sub(r"\s+", " ", "".join(self.cur_title)).strip())
            self.cur_title = None
        elif tag == "script" and self.cur_script is not None:
            text = "".join(self.cur_script.pop("_text"))
            self.cur_script["_len"] = len(text)
            if "ld+json" in self.cur_script.get("type", "").lower():
                self.p["jsonld"].append(text)
            self.p["scripts"].append(self.cur_script)
            self.cur_script = None
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6") and self.cur_h is not None:
            self.p["headings"].append((self.cur_h[0], re.sub(r"\s+", " ", "".join(self.cur_h[1])).strip()))
            self.cur_h = None
        elif tag == "a" and self.cur_a is not None:
            self.cur_a["_text"] = re.sub(r"\s+", " ", "".join(self.cur_a["_text"])).strip()
            self.p["anchors"].append(self.cur_a)
            self.cur_a = None
        if tag == "svg" and self.in_svg:
            self.in_svg -= 1
        if tag in self.HIDDEN and self.hidden:
            self.hidden -= 1
        if tag in self.stack:
            while self.stack and self.stack.pop() != tag:
                pass

    def handle_data(self, data):
        if self.cur_script is not None:
            self.cur_script["_text"].append(data)
            return
        if self.cur_title is not None:
            self.cur_title.append(data)
            return
        if self.hidden:
            return
        if self.cur_h is not None:
            self.cur_h[1].append(data)
        if self.cur_a is not None:
            self.cur_a["_text"].append(data)
        if not self.in_head:
            self.p["text"].append(data)


def parse(html, url, headers=None, status=200, ms=None) -> dict:
    """A page as the rules read it."""
    r = _Reader()
    try:
        r.feed(html or "")
        r.close()
    except Exception:
        pass
    p = r.p
    text = re.sub(r"\s+", " ", " ".join(p.pop("text"))).strip()
    p.update({"url": url, "status": status, "headers": {k.lower(): str(v) for k, v in (headers or {}).items()},
              "ms": ms, "bytes": len((html or "").encode("utf-8", "ignore")), "html": html or "",
              "text": text, "words": len(re.findall(r"[^\W\d_][\w'’-]*", text)),
              "path": urllib.parse.urlparse(url).path or "/"})
    p["semantic"] = sorted(p["semantic"])
    return p


def _meta(p, key, value):
    return [m for m in p["metas"] if m.get(key, "").lower() == value]


def _meta_content(p, key, value):
    got = _meta(p, key, value)
    return (got[0].get("content") or "").strip() if got else ""


def _rel(p, rel):
    return [l for l in p["links"] if rel in (l.get("rel") or "").lower().split()]


def _is_home(p):
    return p["path"] in ("", "/") or p["path"].lower().rstrip("/").endswith(("/index.php", "/index.html"))


def _host(u):
    h = urllib.parse.urlparse(u).netloc.lower()
    return h[4:] if h.startswith("www.") else h


def _own(href, p):
    try:
        u = urllib.parse.urljoin(p["url"], href)
    except Exception:
        return False
    return urllib.parse.urlparse(u).scheme in ("http", "https") and _host(u) == _host(p["url"])


def _robots_directives(p):
    out = set()
    for m in p["metas"]:
        if m.get("name", "").lower() in ("robots", "googlebot"):
            out |= {x.strip() for x in m.get("content", "").lower().split(",")}
    out |= {x.strip() for x in p["headers"].get("x-robots-tag", "").lower().split(",") if x.strip()}
    return out


def _jsonld_objects(p):
    objs, bad = [], 0
    for raw in p["jsonld"]:
        try:
            data = json.loads(raw)
        except Exception:
            bad += 1
            continue
        stack = data if isinstance(data, list) else [data]
        while stack:
            o = stack.pop()
            if isinstance(o, dict):
                objs.append(o)
                if isinstance(o.get("@graph"), list):
                    stack.extend(o["@graph"])
    return objs, bad


def _types(objs):
    out = []
    for o in objs:
        t = o.get("@type")
        out += [str(x) for x in (t if isinstance(t, list) else [t]) if x]
    return out


LOCAL_TYPES = ("LocalBusiness", "HomeAndConstructionBusiness", "GeneralContractor", "Plumber", "Electrician",
               "Locksmith", "HVACBusiness", "RoofingContractor", "Store", "ProfessionalService", "AutoRepair",
               "Restaurant", "CafeOrCoffeeShop", "Bakery", "BeautySalon", "HairSalon", "Dentist", "MedicalClinic",
               "LegalService", "AccountingService", "RealEstateAgent", "MovingCompany", "Painter", "HousePainter")


# ---------------------------------------------------------------------------
# the rules — each returns (status, what was found)
# ---------------------------------------------------------------------------

RULES = []


def rule(rid, cat, weight, name, why, fix="", owner="IT", how="", site=False):
    def wrap(fn):
        RULES.append({"id": rid, "cat": cat, "weight": weight, "name": name, "why": why, "fix": fix,
                      "owner": owner, "how": how, "site": site, "fn": fn})
        return fn
    return wrap


# ---- core -------------------------------------------------------------------

@rule("core-title-present", "core", 25, "Every page has a title",
      "The title is the blue line in Google's results. Without one Google shows whatever it guesses.",
      fix="title", owner="SEO")
def _(p):
    t = [x for x in p["titles"] if x]
    return (PASS, t[0]) if t else (FAIL, "no title")


@rule("core-title-length", "core", 3, "Titles are 30 to 60 characters",
      "Shorter says too little; longer is cut off in Google's results.", fix="title", owner="SEO")
def _(p):
    t = next((x for x in p["titles"] if x), "")
    if not t:
        return NA, ""
    n = len(t)
    return (PASS, f"{n} characters") if 30 <= n <= 60 else (WARN, f"{n} characters: “{t[:70]}”")


@rule("core-title-single", "core", 4, "One title per page",
      "Two title tags confuse search engines about which to show.", owner="IT")
def _(p):
    return (PASS, "") if len(p["titles"]) <= 1 else (FAIL, f"{len(p['titles'])} title tags")


@rule("core-description-present", "core", 15, "Every page has a description for Google",
      "The two lines under the title in Google's results. Without one Google picks a sentence off the page.",
      fix="description", owner="SEO")
def _(p):
    d = _meta_content(p, "name", "description")
    return (PASS, d[:80]) if d else (FAIL, "no description")


@rule("core-description-length", "core", 3, "Descriptions are 120 to 160 characters",
      "Shorter wastes the space Google gives you; longer is cut off.", fix="description", owner="SEO")
def _(p):
    d = _meta_content(p, "name", "description")
    if not d:
        return NA, ""
    return (PASS, f"{len(d)} characters") if 110 <= len(d) <= 165 else (WARN, f"{len(d)} characters")


@rule("core-canonical-present", "core", 10, "Every page names its one address",
      "A canonical tag tells Google which address is the real one when a page can be reached several ways.",
      fix="canonical")
def _(p):
    c = _rel(p, "canonical")
    return (PASS, c[0].get("href", "")) if c else (FAIL, "no canonical tag")


@rule("core-canonical-valid", "core", 4, "The page's address is written in full",
      "A canonical must be a full address (https://…). A relative or empty one is ignored.", fix="canonical")
def _(p):
    c = _rel(p, "canonical")
    if not c:
        return NA, ""
    href = (c[0].get("href") or "").strip()
    return (PASS, href) if re.match(r"https?://", href) else (FAIL, f"“{href or 'empty'}”")


@rule("core-canonical-single", "core", 6, "Only one canonical tag",
      "Several canonical tags that disagree make Google ignore all of them.", fix="canonical")
def _(p):
    c = _rel(p, "canonical")
    hrefs = {(x.get("href") or "").strip().rstrip("/") for x in c}
    if len(c) <= 1:
        return PASS, ""
    return (WARN if len(hrefs) == 1 else FAIL), f"{len(c)} canonical tags"


@rule("core-canonical-in-head", "core", 8, "The canonical tag is in the page head",
      "Search engines ignore a canonical placed in the body.", fix="canonical")
def _(p):
    c = _rel(p, "canonical")
    if not c:
        return NA, ""
    return (PASS, "") if all(x.get("_head") for x in c) else (FAIL, "canonical tag outside the head")


@rule("core-canonical-to-home", "core", 6, "Inner pages do not point their address at the home page",
      "An inner page whose canonical is the home page tells Google to drop it.", fix="canonical")
def _(p):
    c = _rel(p, "canonical")
    if not c or _is_home(p):
        return NA, ""
    href = urllib.parse.urljoin(p["url"], c[0].get("href") or "")
    return (FAIL, href) if (urllib.parse.urlparse(href).path or "/") in ("", "/") else (PASS, "")


@rule("core-canonical-protocol", "core", 7, "The canonical uses the same https as the page",
      "An https page naming an http address (or the other way) splits its ranking.", fix="canonical")
def _(p):
    c = _rel(p, "canonical")
    if not c:
        return NA, ""
    href = urllib.parse.urljoin(p["url"], c[0].get("href") or "")
    return (PASS, "") if urllib.parse.urlparse(href).scheme == urllib.parse.urlparse(p["url"]).scheme \
        else (FAIL, href)


@rule("core-viewport", "core", 20, "Pages are set up for phones",
      "Without the viewport tag a phone shows the desktop page shrunk to fit, and Google ranks it lower.",
      fix="viewport")
def _(p):
    return (PASS, "") if _meta(p, "name", "viewport") else (FAIL, "no viewport tag")


@rule("core-h1-present", "core", 15, "Every page has a main heading",
      "One clear main heading (h1) tells people and Google what the page is about.", fix="h1", owner="SEO")
def _(p):
    h1 = [t for lvl, t in p["headings"] if lvl == 1]
    return (PASS, h1[0][:70]) if h1 else (FAIL, "no h1")


@rule("core-h1-single", "core", 4, "Only one main heading per page",
      "Several h1 headings blur what the page is about. The others should be h2.", fix="h1", owner="SEO")
def _(p):
    n = sum(1 for lvl, _t in p["headings"] if lvl == 1)
    if n == 0:
        return NA, ""
    return (PASS, "") if n == 1 else ((WARN if n == 2 else FAIL), f"{n} h1 headings")


@rule("core-noindex", "core", 12, "Pages are not hidden from Google",
      "A page that says 'noindex' is left out of Google entirely.", fix="noindex",
      how="If you hid it on purpose (a thank-you page), leave it.")
def _(p):
    d = _robots_directives(p)
    return (FAIL, "noindex") if ("noindex" in d or "none" in d) else (PASS, "")


@rule("core-nosnippet", "core", 5, "Google may show a preview of the page",
      "'nosnippet' stops Google showing the two lines under your title, and fewer people click.", fix="noindex")
def _(p):
    d = _robots_directives(p)
    return (WARN, "nosnippet") if ("nosnippet" in d or "max-snippet:0" in d) else (PASS, "")


# ---- content ------------------------------------------------------------

@rule("content-thin", "content", 10, "Pages have enough words to be worth ranking",
      "A page with very little text gives Google nothing to rank and visitors nothing to read.",
      owner="Content", how="Content can add real detail: what you do, where, how a job runs, questions answered.")
def _(p):
    w = p["words"]
    if w >= 300:
        return PASS, f"{w} words"
    return (WARN if w >= 120 else FAIL), f"{w} words"


@rule("content-title-same-as-h1", "content", 4, "Title and main heading are not word for word the same",
      "When they differ, the page can match more of what people search.", fix="title", owner="SEO")
def _(p):
    t = next((x for x in p["titles"] if x), "")
    h1 = next((x for lvl, x in p["headings"] if lvl == 1), "")
    if not t or not h1:
        return NA, ""
    return (WARN, f"both “{t[:60]}”") if t.strip().lower() == h1.strip().lower() else (PASS, "")


@rule("content-title-same-as-description", "content", 4, "The description is not a copy of the title",
      "A description that repeats the title wastes the two lines under it.", fix="description", owner="SEO")
def _(p):
    t = next((x for x in p["titles"] if x), "").strip().lower()
    d = _meta_content(p, "name", "description").strip().lower()
    if not t or not d:
        return NA, ""
    return (WARN, "same text") if t == d else (PASS, "")


@rule("content-heading-order", "content", 2, "Headings go in order",
      "Jumping from h1 to h4 makes the page harder to follow for screen readers and search engines.",
      owner="Content")
def _(p):
    levels = [lvl for lvl, _t in p["headings"]]
    jumps = sum(1 for a, b in zip(levels, levels[1:]) if b > a + 1)
    if not levels:
        return NA, ""
    return (PASS, "") if not jumps else (WARN, f"{jumps} skipped level(s)")


@rule("content-empty-heading", "content", 3, "No empty headings",
      "An empty heading is noise to search engines and screen readers.", owner="IT")
def _(p):
    n = sum(1 for _lvl, t in p["headings"] if not t)
    return (PASS, "") if not n else (WARN, f"{n} empty heading(s)")


@rule("content-placeholder", "content", 8, "No placeholder text left on the page",
      "'Lorem ipsum' or template code left on a live page looks unfinished to customers and Google.",
      owner="Content", how="Content replaces it with real words.")
def _(p):
    m = re.search(r"\blorem ipsum\b|\{\{\s*\w+|\[\[\s*\w+|\bTODO\b|\bFIXME\b|<\?php", p["text"], re.I)
    return (FAIL, f"“{p['text'][max(0, m.start() - 10):m.end() + 20]}”") if m else (PASS, "")


@rule("content-mojibake", "content", 8, "No garbled characters",
      "Characters like 'Ã©' or 'â€™' mean the page's text encoding is wrong.", owner="IT",
      how="Save the page as UTF-8 and make sure the page head says charset utf-8.")
def _(p):
    m = re.search(r"Ã[\x80-\xBF©¨ª«®¡¢£¤¥¦§]|â€[™œ”“˜¦]|Ã¢|Â\s", p["text"])
    return (FAIL, f"“{p['text'][max(0, m.start() - 10):m.end() + 10]}”") if m else (PASS, "")


@rule("content-old-copyright", "content", 2, "The copyright year is current",
      "An old year in the footer makes a business look closed.", fix="copyright")
def _(p):
    import time as _t
    year = int(_t.strftime("%Y"))
    m = re.findall(r"(?:©|&copy;|copyright)\s*(?:\d{4}\s*[-–]\s*)?(\d{4})", p["text"], re.I)
    if not m:
        return NA, ""
    return (PASS, "") if max(int(x) for x in m) >= year - 1 else (WARN, f"© {max(int(x) for x in m)}")


@rule("content-meta-in-body", "content", 6, "Page tags are in the head, not the body",
      "Description and other tags placed in the body are ignored.", owner="IT")
def _(p):
    return (PASS, "") if not p["body_metas"] else (FAIL, f"{p['body_metas']} tag(s) in the body")


@rule("content-keyword-stuffing", "content", 5, "No word is repeated unnaturally",
      "One word making up a large share of the page reads as spam to Google.", owner="Content")
def _(p):
    words = [w.lower() for w in re.findall(r"[^\W\d_]{4,}", p["text"])]
    if len(words) < 150:
        return NA, ""
    from collections import Counter
    stop = {"that", "this", "with", "your", "from", "have", "will", "they", "their", "what", "when", "which",
            "about", "there", "were", "been", "more", "also", "into", "than", "them", "then", "only", "some"}
    top = [(w, n) for w, n in Counter(words).most_common(8) if w not in stop]
    if not top:
        return PASS, ""
    w, n = top[0]
    share = n / len(words) * 100
    return (PASS, "") if share < 4 else (WARN if share < 6 else FAIL, f"“{w}” is {share:.1f}% of the words")


@rule("content-invisible", "content", 4, "No hidden characters in the words",
      "Zero-width spaces and the like, often left by AI tools or copy-and-paste, break searches for the words "
      "they sit in and can mark text as machine-written (seomachine's content scrubber).", fix="scrub")
def _(p):
    from .scrub import found
    titles = " ".join(p["titles"])
    descs = " ".join(m.get("content", "") for m in p["metas"] if m.get("name", "").lower() == "description")
    n = found(p["text"] + " " + titles + " " + descs)
    return (PASS, "") if not n else (WARN, f"{n} hidden character(s)")


# ---- images -------------------------------------------------------------

def _content_imgs(p):
    return [i for i in p["imgs"] if (i.get("src") or i.get("data-src")) and i.get("role", "") != "presentation"
            and not re.search(r"(pixel|spacer|blank|tracking)\.(gif|png)", i.get("src") or "", re.I)]


@rule("images-alt", "images", 20, "Pictures are described",
      "A short description on each picture (alt text) lets Google Images find your work and screen readers "
      "describe it.", fix="alt", owner="Content")
def _(p):
    imgs = _content_imgs(p)
    if not imgs:
        return NA, ""
    miss = [i for i in imgs if "alt" not in i]
    return (PASS, f"{len(imgs)} pictures") if not miss else (FAIL, f"{len(miss)} of {len(imgs)} without a description")


@rule("images-alt-quality", "images", 8, "Picture descriptions say what is in them",
      "'image', 'photo', a file name or one word does not describe your work.", fix="alt", owner="Content")
def _(p):
    imgs = [i for i in _content_imgs(p) if (i.get("alt") or "").strip()]
    if not imgs:
        return NA, ""
    weak = [i for i in imgs if re.fullmatch(r"(image|photo|picture|img|logo|banner|icon|pic)?\s*\d*", i["alt"].strip(), re.I)
            or re.search(r"\.(jpe?g|png|webp|gif)$|^(img|dsc|photo|image)[-_]?\d+", i["alt"].strip(), re.I)]
    return (PASS, "") if not weak else (WARN, f"{len(weak)} weak description(s), e.g. “{weak[0]['alt'][:40]}”")


@rule("images-alt-length", "images", 3, "Picture descriptions are not essays",
      "Over 125 characters is too long for a screen reader and reads as stuffing.", owner="Content")
def _(p):
    imgs = [i for i in _content_imgs(p) if len(i.get("alt") or "") > 125]
    return (PASS, "") if not imgs else (WARN, f"{len(imgs)} over 125 characters")


@rule("images-dimensions", "images", 8, "Pictures have their size written in",
      "Width and height on each picture stop the page jumping about as it loads (Google measures this).",
      owner="IT", how="IT can add them once each picture's size is read.")
def _(p):
    imgs = _content_imgs(p)
    if not imgs:
        return NA, ""
    miss = [i for i in imgs if not (i.get("width") and i.get("height"))]
    if not miss:
        return PASS, ""
    return (WARN if len(miss) < len(imgs) else FAIL), f"{len(miss)} of {len(imgs)} without width and height"


@rule("images-lazy", "images", 5, "Pictures further down the page load when needed",
      "loading=\"lazy\" on pictures below the first screen makes the page open faster.", fix="lazy")
def _(p):
    imgs = _content_imgs(p)
    below = imgs[3:]
    if not below:
        return NA, ""
    miss = [i for i in below if (i.get("loading") or "").lower() != "lazy"]
    return (PASS, "") if not miss else (WARN, f"{len(miss)} picture(s) below the first screen load straight away")


@rule("images-format", "images", 4, "Pictures use modern formats",
      "WebP pictures are about a third the size of JPEG for the same look.", owner="IT",
      how="Save new pictures as WebP; IT can convert existing ones on your hosting.")
def _(p):
    imgs = _content_imgs(p)
    if not imgs:
        return NA, ""
    old = [i for i in imgs if re.search(r"\.(jpe?g|png|bmp|tiff?)(\?|$)", i.get("src") or "", re.I)]
    return (PASS, "") if len(old) <= len(imgs) / 2 else (WARN, f"{len(old)} of {len(imgs)} in JPEG/PNG")


@rule("images-filenames", "images", 3, "Picture file names say what they show",
      "'shower-screen-marina.jpg' helps Google Images; 'IMG_4031.jpg' does not.", owner="Content")
def _(p):
    imgs = _content_imgs(p)
    if not imgs:
        return NA, ""
    bad = [i for i in imgs if re.search(r"/(img|dsc|image|photo|whatsapp image|screenshot)[-_ ]?\d+[^/]*$",
                                         urllib.parse.unquote(i.get("src") or ""), re.I)]
    return (PASS, "") if not bad else (WARN, f"{len(bad)} named like IMG_1234")


# ---- links --------------------------------------------------------------

@rule("links-internal", "links", 4, "Pages link to other pages of the site",
      "Links between your pages help Google find them all and visitors keep reading.", owner="Content")
def _(p):
    n = sum(1 for a in p["anchors"] if _own(a.get("href", ""), p))
    return (PASS, f"{n} internal links") if n >= 3 else ((WARN if n else FAIL), f"{n} internal link(s)")


@rule("links-anchor-text", "links", 4, "Links say where they go",
      "'Click here' tells Google nothing; 'our shower screens' tells it what the next page is about.",
      owner="Content")
def _(p):
    bad = [a for a in p["anchors"] if a.get("_text", "").strip().lower() in GENERIC_ANCHORS]
    return (PASS, "") if not bad else (WARN, f"{len(bad)} link(s) like “{bad[0]['_text']}”")


@rule("links-invalid", "links", 4, "No broken or empty link addresses",
      "Empty links, 'javascript:' links and malformed addresses lead nowhere.", owner="IT")
def _(p):
    bad = [a for a in p["anchors"] if not (a.get("href") or "").strip()
           or (a.get("href") or "").strip().lower().startswith("javascript:")
           or (a.get("href") or "") != (a.get("href") or "").strip()]
    return (PASS, "") if not bad else (WARN, f"{len(bad)} link(s)")


@rule("links-phone-email", "links", 4, "Phone and email links are written correctly",
      "A tap-to-call link needs a full number (+971…); a mailto link a real address.", owner="IT")
def _(p):
    bad = []
    for a in p["anchors"]:
        h = (a.get("href") or "").strip()
        if h.lower().startswith("tel:") and not re.fullmatch(r"tel:\+?[\d\s().-]{7,}", h, re.I):
            bad.append(h)
        if h.lower().startswith("mailto:") and not re.match(r"mailto:[^@\s]+@[^@\s]+\.[a-z]{2,}", h, re.I):
            bad.append(h)
    return (PASS, "") if not bad else (FAIL, ", ".join(bad[:3]))


@rule("links-http-on-https", "links", 3, "Links to your own pages use https",
      "An https page linking to http addresses of the same site sends visitors through a redirect.", fix="https_links")
def _(p):
    if not p["url"].startswith("https://"):
        return NA, ""
    bad = [a for a in p["anchors"] if (a.get("href") or "").startswith("http://") and _own(a["href"], p)]
    return (PASS, "") if not bad else (WARN, f"{len(bad)} link(s) to http://")


@rule("links-dev-addresses", "links", 8, "No links to a developer's computer",
      "Links to localhost or file:// addresses are left over from building the site and lead nowhere.", owner="IT")
def _(p):
    bad = [a for a in p["anchors"] + [{"href": i.get("src", "")} for i in p["imgs"]]
           if re.match(r"(https?://(localhost|127\.0\.0\.1|0\.0\.0\.0)|file://|[a-z]:\\|/users/|/home/)",
                       (a.get("href") or "").strip(), re.I)]
    return (PASS, "") if not bad else (FAIL, (bad[0].get("href") or "")[:60])


@rule("links-nofollow-internal", "links", 4, "Your own links are not marked nofollow",
      "nofollow on links to your own pages tells Google not to follow your site.", owner="IT")
def _(p):
    bad = [a for a in p["anchors"] if _own(a.get("href", ""), p) and "nofollow" in (a.get("rel") or "").lower()]
    return (PASS, "") if not bad else (WARN, f"{len(bad)} link(s)")


@rule("links-new-tab-safe", "security", 3, "Links that open a new tab are safe",
      "target=\"_blank\" without rel=\"noopener\" lets the other site control your tab.", fix="noopener")
def _(p):
    bad = [a for a in p["anchors"] if (a.get("target") or "").lower() == "_blank"
           and not _own(a.get("href", ""), p) and "noopener" not in (a.get("rel") or "").lower()
           and "noreferrer" not in (a.get("rel") or "").lower()]
    return (PASS, "") if not bad else (WARN, f"{len(bad)} link(s)")


@rule("links-onclick", "links", 4, "Navigation uses real links",
      "Links that only work through onclick are invisible to Google.", owner="IT")
def _(p):
    bad = [a for a in p["anchors"] if a.get("onclick") and not (a.get("href") or "").strip("#")]
    return (PASS, "") if not bad else (FAIL, f"{len(bad)} link(s)")


# ---- schema -------------------------------------------------------------

@rule("schema-valid", "schema", 10, "Schema markup is valid",
      "Markup with a typing error is thrown away whole.", fix="schema")
def _(p):
    if not p["jsonld"]:
        return NA, ""
    _objs, bad = _jsonld_objects(p)
    if not bad:
        return PASS, ""
    return (WARN if bad < len(p["jsonld"]) else FAIL), f"{bad} block(s) do not read as JSON"


@rule("schema-local-business", "schema", 10, "Local business markup has name, address and phone",
      "These three are what local results and maps are built from.", fix="schema")
def _(p):
    if not _is_home(p):
        return NA, ""
    objs, _bad = _jsonld_objects(p)
    local = [o for o in objs if any(t in LOCAL_TYPES for t in _types([o]))]
    if not local:
        return NA, ""                    # no business markup at all is the blueprint's schema_missing
    o = local[0]
    missing = [k for k in ("name", "address", "telephone") if not o.get(k)]
    return (PASS, "") if not missing else (WARN, "missing " + ", ".join(missing))


@rule("schema-breadcrumb", "schema", 3, "Inner pages carry a breadcrumb",
      "A breadcrumb trail can show in Google's result instead of a bare address.", owner="IT")
def _(p):
    if _is_home(p):
        return NA, ""
    objs, _bad = _jsonld_objects(p)
    return (PASS, "") if "BreadcrumbList" in _types(objs) else (WARN, "no BreadcrumbList")


# ---- speed (what the page itself says; no browser needed) ---------------

@rule("speed-response", "speed", 8, "The server answers quickly",
      "Under 0.8 seconds is good; over 1.8 seconds people start leaving.", owner="IT",
      how="Ask the host about caching (LiteSpeed Cache) or a faster plan.")
def _(p):
    ms = p.get("ms")
    if ms is None:
        return NA, ""
    return (PASS, f"{ms} ms") if ms < 800 else ((WARN if ms < 1800 else FAIL), f"{ms} ms")


@rule("speed-page-weight", "speed", 6, "The page itself is not heavy",
      "Over 100 KB of HTML is slow on phones; over 300 KB is very slow.", owner="IT")
def _(p):
    kb = p["bytes"] / 1024
    return (PASS, f"{kb:.0f} KB") if kb <= 100 else ((WARN if kb <= 300 else FAIL), f"{kb:.0f} KB")


@rule("speed-compression", "speed", 6, "The server packs pages before sending",
      "gzip or Brotli makes pages several times smaller to download.", owner="IT",
      how="Turn on compression in your hosting panel (it is usually one switch).")
def _(p):
    if not p["headers"]:
        return NA, ""
    enc = p["headers"].get("content-encoding", "").lower()
    return (PASS, enc) if any(x in enc for x in ("gzip", "br", "deflate", "zstd")) else (WARN, "not packed")


@rule("speed-caching", "speed", 4, "The server says how long a page may be kept",
      "Caching headers let returning visitors open pages instantly.", owner="IT",
      how="Turn on caching in your hosting panel.")
def _(p):
    h = p["headers"]
    if not h:
        return NA, ""
    return (PASS, "") if any(k in h for k in ("cache-control", "etag", "last-modified", "expires")) else (WARN, "none")


@rule("speed-blocking-scripts", "speed", 8, "Scripts do not hold up the page",
      "Scripts in the page head without async or defer stop the page appearing until they load.",
      how="IT adds defer to each script one at a time and checks the menus and forms still work — done blind, "
          "it can break them, so the Fix button does not do it on its own.")
def _(p):
    bad = [s for s in p["scripts"] if s.get("_head") and s.get("src") and "async" not in s and "defer" not in s
           and "module" not in (s.get("type") or "")]
    return (PASS, "") if not bad else ((WARN if len(bad) <= 2 else FAIL), f"{len(bad)} script(s)")


@rule("speed-inline-js", "speed", 3, "Not too much script written into the page",
      "Over 50 KB of script inside the page slows every visit.", owner="IT")
def _(p):
    kb = sum(s.get("_len", 0) for s in p["scripts"] if not s.get("src") and "json" not in (s.get("type") or "")) / 1024
    return (PASS, "") if kb <= 50 else ((WARN if kb <= 150 else FAIL), f"{kb:.0f} KB")


# ---- security (what any visitor's browser receives) ---------------------

@rule("security-https", "security", 10, "The site uses https",
      "Browsers mark http sites 'Not secure', and Google prefers https.", owner="IT",
      how="Turn on the free SSL certificate in your hosting panel.")
def _(p):
    return (PASS, "") if p["url"].startswith("https://") else (FAIL, "http")


@rule("security-mixed", "security", 8, "No insecure pictures or scripts on secure pages",
      "An https page loading http pictures or scripts shows a warning and may break.", fix="https_links")
def _(p):
    if not p["url"].startswith("https://"):
        return NA, ""
    bad = [i.get("src") for i in p["imgs"] if (i.get("src") or "").startswith("http://")] + \
          [s.get("src") for s in p["scripts"] if (s.get("src") or "").startswith("http://")] + \
          [l.get("href") for l in p["links"] if (l.get("rel") or "").lower() == "stylesheet"
           and (l.get("href") or "").startswith("http://")]
    return (PASS, "") if not bad else (FAIL, f"{len(bad)} item(s), e.g. {bad[0][:60]}")


@rule("security-hsts", "security", 3, "Browsers are told to always use https",
      "The Strict-Transport-Security header stops anyone being sent to the http version.", owner="IT",
      how="Your host can switch on HSTS (or 'Force HTTPS' with HSTS).")
def _(p):
    if not p["headers"] or not p["url"].startswith("https://"):
        return NA, ""
    return (PASS, "") if "strict-transport-security" in p["headers"] else (WARN, "no HSTS header")


@rule("security-nosniff", "security", 2, "The server sends the nosniff header",
      "X-Content-Type-Options: nosniff stops browsers guessing file types (a common attack).", owner="IT")
def _(p):
    if not p["headers"]:
        return NA, ""
    return (PASS, "") if "nosniff" in p["headers"].get("x-content-type-options", "").lower() else (WARN, "missing")


@rule("security-form-https", "security", 5, "Forms send over https",
      "A form that posts to an http address sends customers' details in the clear.", owner="IT")
def _(p):
    bad = [f for f in p["forms"] if (f.get("action") or "").startswith("http://")]
    return (PASS, "") if not bad else (FAIL, f"{len(bad)} form(s)")


# ---- mobile -------------------------------------------------------------

@rule("mobile-viewport-width", "mobile", 10, "The page fits the phone's width",
      "A viewport with a fixed width shows the page too wide on phones.", fix="viewport")
def _(p):
    v = _meta(p, "name", "viewport")
    if not v:
        return NA, ""
    c = v[0].get("content", "").lower().replace(" ", "")
    return (PASS, "") if "width=device-width" in c else (FAIL, c[:40])


@rule("mobile-zoom", "mobile", 6, "People can zoom in",
      "user-scalable=no or maximum-scale=1 stops people with poor eyesight zooming in.", fix="viewport")
def _(p):
    v = _meta(p, "name", "viewport")
    if not v:
        return NA, ""
    c = v[0].get("content", "").lower().replace(" ", "")
    locked = "user-scalable=no" in c or "user-scalable=0" in c or \
        re.search(r"maximum-scale=(0?\.\d+|1(\.0*)?)(,|;|$)", c)
    return (WARN, c[:40]) if locked else (PASS, "")


@rule("mobile-one-viewport", "mobile", 5, "Only one viewport tag",
      "Two viewport tags make phones pick one at random.", fix="viewport")
def _(p):
    return (PASS, "") if len(_meta(p, "name", "viewport")) <= 1 else (FAIL, "more than one")


# ---- social -------------------------------------------------------------

@rule("social-og", "social", 6, "A preview card when the site is shared",
      "og:title, og:description and og:image decide what WhatsApp, Facebook and LinkedIn show for your link.",
      fix="og")
def _(p):
    have = {k for k in ("og:title", "og:description", "og:image") if _meta_content(p, "property", k)}
    if len(have) == 3:
        return PASS, ""
    return (WARN if have else FAIL), "missing " + ", ".join(sorted({"og:title", "og:description", "og:image"} - have))


@rule("social-og-url", "social", 2, "The preview card names the page's address",
      "og:url should be the page's own full address.", fix="og")
def _(p):
    u = _meta_content(p, "property", "og:url")
    return (PASS, "") if re.match(r"https?://", u) else (WARN, "missing" if not u else u[:50])


@rule("social-twitter", "social", 2, "A card for X (Twitter)",
      "twitter:card makes links on X show as a card rather than a bare address.", fix="og")
def _(p):
    return (PASS, "") if _meta_content(p, "name", "twitter:card") else (WARN, "missing")


# ---- url ----------------------------------------------------------------

@rule("url-tidy", "url", 6, "Page addresses are tidy",
      "Lowercase words with hyphens read well in Google: /shower-screens, not /Shower_Screens.", owner="IT")
def _(p):
    path = urllib.parse.unquote(p["path"])
    bad = []
    if re.search(r"[A-Z]", path):
        bad.append("capitals")
    if "_" in path:
        bad.append("underscores")
    if "//" in path:
        bad.append("double slash")
    if " " in path or "%20" in p["path"]:
        bad.append("spaces")
    return (PASS, "") if not bad else (WARN, " and ".join(bad))


@rule("url-length", "url", 3, "Page addresses are not too long",
      "Over 100 characters is hard to share and is cut off in Google.", owner="IT")
def _(p):
    n = len(p["url"])
    return (PASS, "") if n <= 100 else ((WARN if n <= 200 else FAIL), f"{n} characters")


@rule("url-parameters", "url", 4, "No session numbers or tracking in page addresses",
      "Addresses with ?sessionid= or utm_ tracking create copies of the same page.", owner="IT")
def _(p):
    q = urllib.parse.urlparse(p["url"]).query.lower()
    if not q:
        return PASS, ""
    if re.search(r"(phpsessid|sessionid|sid|jsessionid)=", q):
        return FAIL, "session id"
    return (WARN, "tracking") if "utm_" in q else (PASS, "")


# ---- language -----------------------------------------------------------

@rule("i18n-lang", "i18n", 10, "The page says its language",
      "lang on the <html> tag lets Google and screen readers serve the page to the right people.", fix="lang")
def _(p):
    lang = p["lang"]
    if not lang:
        return FAIL, "no lang"
    return (PASS, lang) if re.fullmatch(r"[a-zA-Z]{2,3}(-[a-zA-Z0-9]{2,8})*", lang) else (WARN, f"“{lang}”")


@rule("i18n-hreflang", "i18n", 5, "Language versions point at each other correctly",
      "hreflang addresses must be full addresses, and the page must list itself.", owner="IT")
def _(p):
    alts = [l for l in p["links"] if "alternate" in (l.get("rel") or "").lower() and l.get("hreflang")]
    if not alts:
        return NA, ""
    rel = [a for a in alts if not re.match(r"https?://", a.get("href") or "")]
    return (PASS, "") if not rel else (FAIL, f"{len(rel)} relative address(es)")


# ---- ai assistants -----------------------------------------------------

@rule("ai-semantic", "ai", 6, "The page is built with clear sections",
      "main, header, nav and footer tags help AI assistants find the part of the page that matters.", owner="IT")
def _(p):
    have = set(p["semantic"])
    if "main" in have or {"header", "footer"} <= have:
        return PASS, ""
    return (WARN if have else FAIL), ("only " + ", ".join(sorted(have))) if have else "none"


# ---- the whole site ----------------------------------------------------

SITE_RULES = []


def site_rule(rid, cat, weight, name, why, fix="", owner="IT", how=""):
    def wrap(fn):
        SITE_RULES.append({"id": rid, "cat": cat, "weight": weight, "name": name, "why": why, "fix": fix,
                           "owner": owner, "how": how, "site": True, "fn": fn})
        return fn
    return wrap


@site_rule("core-title-unique", "core", 5, "Each page's title is its own",
           "Pages with the same title compete with each other.", fix="title", owner="SEO")
def _(site):
    seen = {}
    for p in site["pages"]:
        t = next((x for x in p["titles"] if x), "").strip().lower()
        if t:
            seen.setdefault(t, []).append(p["url"])
    dup = [u for urls in seen.values() if len(urls) > 1 for u in urls[1:]]
    return (PASS, "", []) if not dup else (WARN, f"{len(dup)} page(s) repeat another's title", dup)


@site_rule("content-description-unique", "content", 5, "Each page's description is its own",
           "Copied descriptions tell Google the pages are the same.", fix="description", owner="SEO")
def _(site):
    seen = {}
    for p in site["pages"]:
        d = _meta_content(p, "name", "description").strip().lower()
        if d:
            seen.setdefault(d, []).append(p["url"])
    dup = [u for urls in seen.values() if len(urls) > 1 for u in urls[1:]]
    return (PASS, "", []) if not dup else (WARN, f"{len(dup)} page(s) repeat another's description", dup)


@site_rule("content-duplicate-pages", "content", 8, "No two pages say the same thing",
           "Two pages with the same text compete, and Google shows neither well.", owner="Content")
def _(site):
    import difflib
    pages = [p for p in site["pages"] if p["words"] >= 80]
    dup = []
    for i, a in enumerate(pages):
        for b in pages[i + 1:]:
            if abs(a["words"] - b["words"]) > max(a["words"], b["words"]) * 0.2:
                continue
            m = difflib.SequenceMatcher(None, a["text"][:6000], b["text"][:6000], autojunk=False)
            if m.quick_ratio() > 0.9 and m.ratio() > 0.9:
                dup.append(b["url"])
    return (PASS, "", []) if not dup else (FAIL, f"{len(dup)} page(s) nearly identical to another", dup)


@site_rule("content-cannibal", "content", 6, "Pages do not compete for the same search",
           "Two pages whose titles and main headings target the same words split Google's attention between them "
           "(seomachine's cannibalisation check).", owner="SEO",
           how="SEO decides which page keeps the search; the other is reworded or linked to it.")
def _(site):
    def key(p):
        t = next((x for x in p["titles"] if x), "") + " " + next((x for lvl, x in p["headings"] if lvl == 1), "")
        words = [w for w in re.findall(r"[a-z]{3,}", t.lower())
                 if w not in {"the", "and", "for", "with", "our", "your", "best", "top", "home", "page", "services",
                              "service", "company", "dubai", "uae", "llc"}]
        return frozenset(words[:6])
    seen, clash = {}, []
    for p in site["pages"]:
        k = key(p)
        if len(k) < 2:
            continue
        for other, url in seen.items():
            if len(k & other) >= max(2, int(0.8 * min(len(k), len(other)))):
                clash.append(p["url"])
                break
        seen[k] = p["url"]
    return (PASS, "", []) if not clash else (WARN, f"{len(clash)} page(s) aim at the same words as another", clash)


@site_rule("links-broken", "links", 10, "No links to pages that do not exist",
           "A link to a missing page loses the visitor and wastes Google's visit.", owner="IT")
def _(site):
    bad = [u for u, st in (site.get("link_status") or {}).items() if st and int(st) >= 400]
    if not site.get("link_status"):
        return NA, "", []
    return (PASS, "", []) if not bad else (FAIL, f"{len(bad)} broken link target(s)", bad)


@site_rule("links-internal-opportunities", "links", 4, "Pages link to each other where they mention each other",
           "A page that names another page's subject without linking to it misses the easiest link there is: it "
           "helps visitors move on and tells Google which page is about what (seomachine's internal linker).",
           owner="Content", how="Content links the first mention on each page named to the page about it.")
def _(site):
    pages = site["pages"]
    if len(pages) < 3:
        return NA, "", []

    def key(u):
        return urllib.parse.urljoin(u, "").split("#")[0].rstrip("/").replace("://www.", "://")
    topics = []
    for t in pages:
        if _is_home(t):
            continue
        h1 = next((x for lvl, x in t["headings"] if lvl == 1 and x), "")
        phrase = re.sub(r"\s+", " ", re.split(r"\s+[|\-–—:]\s+", h1 or next((x for x in t["titles"] if x), ""))[0]).strip()
        words = re.findall(r"[^\W\d_]+", phrase.lower())
        if 2 <= len(words) <= 5 and len(phrase) >= 8:
            topics.append((key(t["url"]), phrase.lower()))
    found, where = [], []
    for s in pages:
        links = {key(urllib.parse.urljoin(s["url"], a.get("href") or "")) for a in s["anchors"] if a.get("href")}
        text = s["text"].lower()
        for url, phrase in topics:
            if url == key(s["url"]) or url in links:
                continue
            if re.search(r"\b" + re.escape(phrase) + r"\b", text):
                found.append(f"{s['path']} mentions “{phrase}” → {urllib.parse.urlparse(url).path or '/'}")
                if s["url"] not in where:
                    where.append(s["url"])
    if not found:
        return PASS, "", []
    return WARN, f"{len(found)} mention(s) with no link, e.g. " + "; ".join(found[:3]), where


@site_rule("links-orphans", "links", 4, "Every page is linked from another page",
           "A page only the sitemap knows about is one Google rarely visits.", owner="Content")
def _(site):
    linked = set()
    for p in site["pages"]:
        for a in p["anchors"]:
            if _own(a.get("href", ""), p):
                u = urllib.parse.urljoin(p["url"], a["href"]).split("#")[0].rstrip("/")
                linked.add(u.replace("://www.", "://"))
    sm = site.get("sitemap_urls") or []
    orphans = [u for u in sm if u.rstrip("/").replace("://www.", "://") not in linked
               and (urllib.parse.urlparse(u).path or "/") not in ("", "/")]
    if not sm:
        return NA, "", []
    return (PASS, "", []) if not orphans else (WARN, f"{len(orphans)} page(s) in the sitemap no page links to", orphans)


@site_rule("technical-www", "technical", 6, "The site is on one address, with or without www",
           "Both versions answering on their own splits the site in two for Google.", owner="IT",
           how="Your host can send one to the other with a permanent (301) redirect.")
def _(site):
    # {address asked: {status, final}} for the www and the bare address. Each
    # answering for itself, without sending one to the other, is two sites.
    hosts = site.get("hosts") or {}
    if len(hosts) < 2:
        return NA, "", []
    own = [a for a, h in hosts.items() if h.get("status") == 200
           and urllib.parse.urlparse(h.get("final") or a).netloc.lower() == urllib.parse.urlparse(a).netloc.lower()]
    return (PASS, "", []) if len(own) < 2 else (FAIL, "both www and non-www answer on their own", [])


@site_rule("technical-https-redirect", "technical", 6, "The http address sends people to https",
           "Without it, old links and typed addresses land on the insecure version.", owner="IT",
           how="Turn on 'Force HTTPS' in your hosting panel.")
def _(site):
    r = site.get("http_redirect")
    if r is None:
        return NA, "", []
    return (PASS, "", []) if r else (FAIL, "http does not redirect to https", [])


# Checks the website blueprint makes itself (the files machines read), counted
# in the same score under the same arithmetic. Their codes are the blueprint's.
EXTERNAL = (
    ("schema_missing", "schema", 15, "Business details as schema markup", "apply_schema"),
    ("favicon_missing", "core", 2, "A favicon", ""),
    ("robots_missing", "technical", 8, "A robots.txt file", "put_robots"),
    ("robots_blocks_all", "technical", 25, "robots.txt lets search engines in", ""),
    ("robots_no_sitemap", "crawl", 6, "robots.txt names the sitemap", "put_robots"),
    ("sitemap_missing", "technical", 12, "A sitemap", "publish_sitemap"),
    ("sitemap_incomplete", "crawl", 10, "The sitemap lists every page", "publish_sitemap"),
    ("sitemap_theirs_incomplete", "crawl", 6, "The sitemap lists every page", ""),
    ("soft_404", "technical", 10, "Missing pages answer 'not found'", ""),
    ("llms_missing", "ai", 6, "An llms.txt for AI assistants", "put_llms"),
)
for _code, _cat, _w, _name, _fix in EXTERNAL:
    SITE_RULES.append({"id": _code, "cat": _cat, "weight": _w, "name": _name, "why": "", "fix": _fix,
                       "owner": "IT", "how": "", "site": True, "fn": None, "external": True})

ALL_RULES = RULES + SITE_RULES
BY_ID = {r["id"]: r for r in ALL_RULES}


# ---------------------------------------------------------------------------
# running and scoring
# ---------------------------------------------------------------------------

def check_page(p) -> dict:
    """{rule id: (status, detail)} for one parsed page."""
    out = {}
    for r in RULES:
        try:
            out[r["id"]] = r["fn"](p)
        except Exception as e:                                   # a broken rule is not a broken page
            out[r["id"]] = (NA, f"could not be checked: {type(e).__name__}")
    return out


def check_site(pages, extra=None) -> dict:
    """
    Run every rule over the pages a visitor can reach.

    pages: parsed pages (parse()), the home page first. extra: site facts —
    link_status, sitemap_urls, hosts, http_redirect, files (robots, sitemap…).
    Returns {score, grade, categories, issues, passed, capped_by, pages}.
    """
    site = dict(extra or {}, pages=pages)
    per_page = [check_page(p) for p in pages]
    results = {}                                     # rule id -> list of (url, status, detail)
    for p, res in zip(pages, per_page):
        for rid, (st, detail) in res.items():
            results.setdefault(rid, []).append((p["url"], st, detail))
    # Pages that ask Google not to list them are left out of the walk; they
    # count for that one rule, and a hidden home page caps the score.
    hidden = (extra or {}).get("hidden_pages") or []
    for p in hidden:
        d = _robots_directives(p)
        if "noindex" in d or "none" in d:
            results.setdefault("core-noindex", []).append((p["url"], FAIL, "noindex"))
    home = next((p for p in hidden if _is_home(p)), None)
    for r in SITE_RULES:
        if r.get("external"):
            continue
        try:
            st, detail, urls = r["fn"](site)
        except Exception as e:
            st, detail, urls = NA, f"could not be checked: {type(e).__name__}", []
        results[r["id"]] = [("site", st, detail, urls)]
    # The blueprint's own file checks: {code: (status, detail)}.
    for code, (st, detail) in (extra or {}).get("files_checked", {}).items():
        if code in BY_ID:
            results[code] = [("site", st, detail, [])]
    return score(results, pages, home=home)


def score(results, pages, home=None) -> dict:
    cats = {}
    issues, passed = [], []
    for rid, rows in results.items():
        r = BY_ID.get(rid)
        if not r:
            continue
        measured = [x for x in rows if x[1] in SCORE]
        if not measured:
            continue
        c = cats.setdefault(r["cat"], {"num": 0.0, "den": 0.0})
        for x in measured:
            c["num"] += SCORE[x[1]] * r["weight"]
            c["den"] += 100 * r["weight"]
        bad = [x for x in measured if x[1] != PASS]
        if not bad:
            passed.append({"id": rid, "name": r["name"], "cat": r["cat"]})
            continue
        worst = FAIL if any(x[1] == FAIL for x in bad) else WARN
        if r["site"]:
            affected = list(bad[0][3]) if len(bad[0]) > 3 else []
            share = 1.0
            detail = bad[0][2]
        else:
            affected = [x[0] for x in bad]
            share = len(bad) / len(measured)
            detail = bad[0][2]
        issues.append({
            "id": rid, "name": r["name"], "cat": r["cat"], "cat_name": CAT_NAME[r["cat"]], "status": worst,
            "why": r["why"], "fix": r["fix"], "owner": r["owner"], "how": r["how"], "weight": r["weight"],
            "pages": affected, "measured": len(measured) if not r["site"] else 1,
            "detail": detail, "examples": [{"url": x[0], "found": x[2]} for x in bad[:5] if x[0] != "site"],
            "priority": round(r["weight"] * CAT_WEIGHT[r["cat"]] * SEVERITY[worst] * share, 1),
        })
    cat_scores = {c: round(v["num"] / v["den"] * 100) for c, v in cats.items() if v["den"]}
    wsum = sum(CAT_WEIGHT[c] for c in cat_scores)
    overall = round(sum(cat_scores[c] * CAT_WEIGHT[c] for c in cat_scores) / wsum) if wsum else 0
    capped_by, cap = "", 100
    home = home or (pages[0] if pages else None)
    if home:
        d = _robots_directives(home)
        no_title = not any(home["titles"])
        if "noindex" in d or "none" in d:
            cap, capped_by = 20, "your home page asks Google not to list it"
        elif no_title and home["words"] < 20:
            cap, capped_by = 30, "your home page has no title and almost no words"
        elif home["words"] < 20:
            cap, capped_by = 49, "your home page has almost no words Google can read"
        elif no_title:
            cap, capped_by = 69, "your home page has no title"
    final = min(overall, cap)
    issues.sort(key=lambda i: (-i["priority"], -i["weight"], -CAT_WEIGHT[i["cat"]], i["id"]))
    return {"score": final, "uncapped": overall, "capped_by": capped_by if final < overall else "",
            "grade": grade(final), "label": label(final),
            "categories": [{"id": c, "name": CAT_NAME[c], "weight": w, "score": cat_scores.get(c)}
                           for c, _n, w in CATEGORIES if c in cat_scores],
            "issues": issues, "passed": passed, "pages": len(pages)}


def grade(s) -> str:
    return "A" if s >= 90 else "B" if s >= 80 else "C" if s >= 70 else "D" if s >= 50 else "F"


def label(s) -> str:
    return {"A": "Excellent", "B": "Good", "C": "Fair", "D": "Needs work", "F": "Poor"}[grade(s)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Check and score one page against the SEO rulebook.")
    ap.add_argument("--url", required=True)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    import requests
    r = requests.get(a.url, timeout=20, headers={"User-Agent": "Mozilla/5.0 (compatible; botix-rules/1.0)"})
    p = parse(r.text, r.url, headers=dict(r.headers), status=r.status_code,
              ms=int(r.elapsed.total_seconds() * 1000))
    out = check_site([p])
    if a.json:
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        print(f"Score {out['score']} ({out['grade']}, {out['label']})" + (f" — capped: {out['capped_by']}"
                                                                         if out["capped_by"] else ""))
        for i in out["issues"][:30]:
            print(f"  [{i['status']:<4}] {i['name']} — {_html.unescape(i['detail'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
