"""
seo_fixer.py — the Fix button: put one SEO problem right on his website, look
at the live pages afterwards, and say exactly how far it got.

    python -m pipelines.seo_fixer --start CODE --json   open a fix for one problem (gives its number)
    python -m pipelines.seo_fixer --run ID              do it (the office starts this in the background)
    python -m pipelines.seo_fixer --verify ID           look at the live pages again (after a cache clears)
    python -m pipelines.seo_fixer --status ID --json    where a fix has got to

"there will be button says fix on every issue that program find when someone
 will press the fix button on the issues that program find on website the
 program will fix those issues and will report to the user that if issue had
 solved or not or if issue had solved then how much is it solved no lies the
 report should be authentic"

WHAT PRESSING FIX DOES

  1. Looks first.   Every page named in the problem is fetched from the live
                    site and checked against the rule again. A page that is
                    already right is counted as already right — never as one
                    the button fixed.
  2. Works it out.  For each page, the file that makes it (pipelines/site_map)
                    and the file that holds its <head>: the page's own file,
                    or a header file the pages share. The change is made to
                    those files' own source as read through the connector —
                    never to the HTML a visitor sees, which may be built by PHP.
  3. Writes nothing itself. The changes go in as the Fixer's, approved in his
                    name because he pressed the button, and the IT Releaser —
                    the only desk that writes to his website — runs the tests,
                    writes each file, keeps the old copy and reads it back.
                    Handbook red line 1, unchanged.
  4. Looks again.   Each page is fetched from the live site once more and the
                    same rule run on it. That, and only that, decides "fixed".
                    If the home page stops answering, every file this fix wrote
                    is put back at once and the report says so.
  5. Reports.       Fixed on all of them, on some (how many of how many, and
                    why not the rest), or not fixed, with the reason in plain
                    words. A file that was written but whose live page still
                    shows the old version says exactly that (usually a cache),
                    and "Check again" looks once more later.

WHERE A PAGE'S HEAD IS WRITTEN

  its own file              the tag is changed in place (plain HTML or PHP)
  a header the pages share  (PHP) one small marked block is added to the
                            header, and each page's own words live in
                            botix-meta.php beside it, by page address — so every
                            page gets its own title from one shared header. The
                            header's original tag stays as the fallback for any
                            page the agency has not written for.
  anything else             WordPress, a site that builds every page through one
                            front file with no header file found, a page whose
                            file could not be found: nothing is guessed. The
                            report says why, and gives the words so they can be
                            pasted in by hand.

WHAT THE BUTTON WILL NOT DO BY ITSELF

Anything where a blind change could break the site or say something untrue:
defer on scripts, deleting pages, new pages, his photographs, his decisions.
Those are handed to the desk that owns them (and the report says it was handed
on, not fixed). The words it writes never mention prices, discounts or offers.
"""
import argparse
import html as _html
import json
import posixpath
import re
import sqlite3
import sys
import time
import urllib.parse

from coordination import board
from . import seo_rules as R

FIXER = "it-fixer"
RELEASER = "it-releaser"
META_FILE = "botix-meta.php"
MARK = "botix:page-meta"
MOST_PAGES = 40
BUSY_FOR = 3600
UA = "Mozilla/5.0 (compatible; botix-fix/1.0; checking a change)"

# What the button can put right by itself, by the rulebook's fix code.
AUTO = {
    "title": "writes a title of the right length for each page named",
    "description": "writes a description for Google for each page named",
    "canonical": "points each page's canonical tag at its own address",
    "og": "adds a preview card (title, description and a picture) to each page",
    "viewport": "puts the one right phone-screen line in the page head",
    "lang": "says the page's language on its <html> tag",
    "noindex": "takes the 'do not list me' line off the pages named",
    "h1": "leaves each page one main heading (an extra one becomes a sub-heading; a page with none gets its first sub-heading promoted)",
    "alt": "describes each picture that has no description, or a weak one",
    "lazy": "makes pictures further down the page load when they are needed",
    "noopener": "makes links that open a new tab safe",
    "https_links": "changes links to your own site from http to https",
    "copyright": "makes the copyright year in the footer always the current one",
    "scrub": "takes out characters nobody can see (zero-width spaces and the like) from the files that make each page",
}
# Routines in site_api the Releaser may call (it_release.ALLOWED_CALLS).
CALLS = {"put_robots", "put_llms", "publish_sitemap", "apply_schema"}
FIX_CALL = {"schema": "apply_schema"}
# Words that do not belong in a title or description the agency writes (red line: no offers without him).
NOT_IN_WORDS = re.compile(r"\b(discount\w*|free|cheap\w*|offer\w*|sale|deal\w*|lowest price|% off|\d+\s*%)\b", re.I)
# Pages hidden on purpose are left hidden.
LOOKS_DELIBERATE = re.compile(r"(thank|success|confirm|sent|404|not-found|test|staging|draft|private|login|"
                              r"account|cart|checkout|search)", re.I)
_PHP = re.compile(r"<\?(?:php|=)?.*?(?:\?>|\Z)", re.S | re.I)


def _now():
    return int(time.time())


def _table(c):
    c.execute("""CREATE TABLE IF NOT EXISTS seo_fix_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT, title TEXT, started_at INTEGER,
        finished_at INTEGER, state TEXT, step TEXT, report TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS seo_page_meta (
        holder TEXT, path TEXT, key TEXT, value TEXT, set_at INTEGER, run_id INTEGER,
        PRIMARY KEY (holder, path, key))""")


# ---------------------------------------------------------------------------
# what the button can do for a problem (the office shows this before he presses)
# ---------------------------------------------------------------------------

def _gap(code):
    from . import site_blueprint as bp
    with board._conn() as c:
        bp._table(c)
        c.row_factory = sqlite3.Row
        r = c.execute("SELECT * FROM site_gaps WHERE code=?", (code,)).fetchone()
    if not r:
        return None
    g = dict(r)
    g["pages"] = bp._json_list(g.get("pages"))
    return g


def describe(code, fix="", part="") -> dict:
    """{auto, does, needs}: whether pressing Fix changes the site, and what it does."""
    from . import site_blueprint as bp
    fix = fix or ""
    call = bp.CALLS.get(code) or FIX_CALL.get(fix) or (fix if fix in CALLS else "")
    if call:
        from . import it_release
        return {"auto": True, "call": call, "does": it_release.ALLOWED_CALLS.get(call, call), "needs": ""}
    if fix in AUTO:
        return {"auto": True, "does": AUTO[fix], "needs": ""}
    needs = {"favicon_missing": "your logo, as a square picture",
             "robots_blocks_all": "your decision — it may be on purpose while the site is being built",
             "soft_404": "your hosting company or whoever built the site",
             "address": "the business address you want shown"}.get(code)
    if not needs and part == "pages":
        needs = "a new page, with your own photographs and words"
    return {"auto": False, "does": "", "needs": needs or "a person: it cannot be changed safely without looking"}


# ---------------------------------------------------------------------------
# starting, and where it has got to
# ---------------------------------------------------------------------------

def start(code) -> dict:
    """Open a fix for one problem. One at a time: a second press waits for the first."""
    g = _gap(code)
    if not g or g.get("status") != "open":
        return {"ok": False, "error": "that problem is not on the list any more — check the website again"}
    with board._conn() as c:
        _table(c)
        busy = c.execute("SELECT id, title FROM seo_fix_runs WHERE state='working' AND started_at>?",
                         (_now() - BUSY_FOR,)).fetchone()
        if busy:
            return {"ok": False, "busy": busy[0],
                    "error": f"one fix is already going ({busy[1]}). It finishes first; then press this one again."}
        cur = c.execute("INSERT INTO seo_fix_runs(code,title,started_at,state,step,report) VALUES(?,?,?,?,?,?)",
                        (code, g["title"], _now(), "working", "Starting", "{}"))
        return {"ok": True, "id": cur.lastrowid}


def _step(run_id, step):
    with board._conn() as c:
        _table(c)
        c.execute("UPDATE seo_fix_runs SET step=? WHERE id=?", (step[:200], run_id))


def _finish(run_id, report):
    with board._conn() as c:
        _table(c)
        c.execute("UPDATE seo_fix_runs SET state=?, step='', finished_at=?, report=? WHERE id=?",
                  (report["state"], _now(), json.dumps(report, default=str), run_id))
    return report


def status(run_id) -> dict:
    with board._conn() as c:
        _table(c)
        c.row_factory = sqlite3.Row
        r = c.execute("SELECT * FROM seo_fix_runs WHERE id=?", (run_id,)).fetchone()
    if not r:
        return {"ok": False, "error": "no such fix"}
    d = dict(r)
    d["report"] = json.loads(d.get("report") or "{}")
    if d["state"] == "working" and _now() - int(d["started_at"] or 0) > BUSY_FOR:
        d["state"], d["step"] = "failed", ""
        d["report"] = {"state": "failed", "headline": "This fix stopped part-way and did not finish. "
                                                      "Nothing more will happen; press Fix to try again."}
    return {"ok": True, **d}


def last_for(codes=None) -> dict:
    """{code: the latest fix's state, headline and when} for the office's list."""
    out = {}
    try:
        with board._conn() as c:
            _table(c)
            for r in c.execute("SELECT id, code, state, step, started_at, finished_at, report FROM seo_fix_runs"
                               " ORDER BY id"):
                if codes and r[1] not in codes:
                    continue
                rep = json.loads(r[6] or "{}")
                out[r[1]] = {"id": r[0], "state": r[2], "step": r[3], "started_at": r[4], "finished_at": r[5],
                             "headline": rep.get("headline") or "", "fixed": rep.get("fixed"),
                             "total": rep.get("total")}
    except sqlite3.Error:
        pass
    return out


# ---------------------------------------------------------------------------
# reading the live site and the files behind it
# ---------------------------------------------------------------------------

def _fetcher():
    """GET through requests (paced by coordination/sitepace). (status, html, headers, final_url)."""
    import requests
    s = requests.Session()
    s.headers["User-Agent"] = UA
    s.headers["Cache-Control"] = "no-cache"

    def fetch(url):
        r = s.get(url, timeout=25, allow_redirects=True)
        return r.status_code, (r.text or ""), dict(r.headers), r.url
    return fetch


def _look(url, fetch):
    """The live page, parsed for the rules, or None with the reason."""
    try:
        st, html, headers, final = fetch(url)
    except Exception as e:
        return None, f"could not be fetched ({type(e).__name__})"
    if st != 200:
        return None, f"answered {st}"
    return R.parse(html, url, headers=headers, status=st), ""


def _path_key(url) -> str:
    p = urllib.parse.urlparse(url).path or "/"
    p = re.sub(r"/index\.(php|html?)$", "/", p, flags=re.I)
    return "/" if p in ("", "/") else p.rstrip("/")


def _reader(s=None):
    from . import site_api

    def read(names):
        got = site_api.read_pages(list(names), s=s) if names else {}
        return {n: r.get("html") for n, r in got.items() if isinstance(r, dict) and r.get("ok")}
    return read


class Site:
    """The map, the sources and where each page's head is written."""

    def __init__(self, read, smap):
        self.read = read
        self.map = smap or {}
        self.src = {}
        self.by_path = {}
        self.users = {}
        for row in self.map.get("pages") or []:
            if row.get("file"):
                self.by_path[_path_key(row["url"])] = row["file"]
                self.users[row["file"]] = self.users.get(row["file"], 0) + 1
        self.includes = self.map.get("includes") or {}

    def load(self, names):
        want = [n for n in names if n and n not in self.src]
        if want:
            got = self.read(want)
            for n in want:
                self.src[n] = got.get(n)

    def file_for(self, url, live_html=""):
        """The file that makes this page: from the map, or for a page the map has not got (a hidden one), the
        file the server would try that exists — and when several do, the one whose words are on the page."""
        name = self.by_path.get(_path_key(url), "")
        if name:
            return name
        from . import site_map
        cands = site_map.candidates(url)
        self.load(cands)
        have = [c for c in cands if self.src.get(c) is not None]
        if len(have) > 1 and live_html:
            have.sort(key=lambda c: -site_map.score(self.src.get(c) or "", live_html))
        if have:
            self.by_path[_path_key(url)] = have[0]
            self.users[have[0]] = self.users.get(have[0], 0) + 1
        return have[0] if have else ""

    def chain(self, name):
        """The file and everything it pulls in, in order."""
        out, todo = [], [name]
        while todo:
            n = todo.pop(0)
            if n in out:
                continue
            out.append(n)
            todo = list(self.includes.get(n) or []) + todo
        self.load(out)
        return out

    def holder(self, name):
        """The file that holds this page's </head>: its own, or one it pulls in."""
        for n in self.chain(name):
            if "</head" in (self.src.get(n) or "").lower():
                return n
        return ""

    def html_tag_file(self, name):
        for n in self.chain(name):
            if re.search(r"<html\b", self.src.get(n) or "", re.I):
                return n
        return ""

    def shared(self, name):
        """More than one page reads this file (a header, or a front file)."""
        return self.users.get(name, 0) != 1 or name not in self.users


def _is_php(name, src=""):
    return name.lower().endswith(".php") or "<?" in (src or "")


def _php_spans(src):
    return [(m.start(), m.end()) for m in _PHP.finditer(src or "")]


def _outside_php(spans, a, b):
    return not any(s < b and a < e for s, e in spans)


def _mask(src):
    """The source with every PHP block blanked out (same length), so tags are found whole."""
    return _PHP.sub(lambda m: "\x00" * len(m.group(0)), src or "")


def _matches(pattern, src, static=True, flags=re.I | re.S):
    """Tags written in the file itself. static: only those with no PHP inside them."""
    return [m for m in re.finditer(pattern, _mask(src), flags) if not (static and "\x00" in m.group(0))]


def _finditer_outside(pattern, src, flags=re.I | re.S):
    return _matches(pattern, src, static=True, flags=flags)


def _echoed(src, needle):
    """Is this tag printed by PHP code (inside an echo) rather than written in the file? The agency's own
    marked block does not count."""
    return any(re.search(needle, src[a:b], re.I) for a, b in _php_spans(src)
               if "botix_meta" not in src[a:b] and MARK not in src[a:b])


def _head_open(src):
    m = next(iter(_finditer_outside(r"<head\b[^>]*>", src)), None)
    return m.end() if m else None


def _head_close(src):
    m = next(iter(_finditer_outside(r"</head\s*>", src)), None)
    return m.start() if m else None


def _insert_in_head(src, tag, first=False):
    at = _head_open(src) if first else _head_close(src)
    if at is None:
        at = _head_close(src) if first else _head_open(src)
    if at is None:
        return None
    return src[:at] + ("\n" if first else "") + tag + ("" if first else "\n") + src[at:]


def _esc(v):
    return _html.escape(v or "", quote=True)


def _attr(tag, name):
    m = re.search(r"\b" + re.escape(name) + r"\s*=\s*(\"([^\"]*)\"|'([^']*)'|([^\s>]+))", tag, re.I)
    return (m.group(2) if m.group(2) is not None else m.group(3) if m.group(3) is not None else m.group(4)) if m else None


def _set_attr(tag, name, value):
    """The tag with name="value", replacing any value it had."""
    if _attr(tag, name) is not None:
        return re.sub(r"\b" + re.escape(name) + r"\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+)",
                      lambda m: f'{name}="{value}"', tag, count=1, flags=re.I)
    end = len(tag) - (2 if tag.endswith("/>") else 1)
    return tag[:end].rstrip() + f' {name}="{value}"' + tag[end:]


# ---------------------------------------------------------------------------
# edits to a page's own head (plain HTML, or a PHP page with its own head)
# ---------------------------------------------------------------------------

def _set_title(src, text):
    ms = _finditer_outside(r"<title\b[^>]*>(.*?)</title\s*>", src)
    if ms:
        m = ms[0]
        return src[:m.start()] + f"<title>{_esc(text)}</title>" + src[m.end():]
    return _insert_in_head(src, f"<title>{_esc(text)}</title>", first=True)


def _tag_re(kind, key):
    if kind == "canonical":
        return r"<link\b[^>]*\brel\s*=\s*[\"']?canonical\b[^>]*>"
    attr = "property" if key.startswith("og:") else "name"
    return r"<meta\b[^>]*\b" + attr + r"\s*=\s*[\"']?" + re.escape(key) + r"[\"'\s>][^>]*>"


def _new_tag(kind, key, value):
    if kind == "canonical":
        return f'<link rel="canonical" href="{_esc(value)}">'
    attr = "property" if key.startswith("og:") else "name"
    return f'<meta {attr}="{key}" content="{_esc(value)}">'


def _set_tag(src, kind, key, value):
    """One tag with this value; any copies removed (outside PHP)."""
    ms = _finditer_outside(_tag_re(kind, key), src)
    if not ms:
        return _insert_in_head(src, _new_tag(kind, key, value))
    first = ms[0]
    out = src[:first.start()] + _new_tag(kind, key, value)
    pos = first.end()
    for m in ms[1:]:
        out += src[pos:m.start()]
        pos = m.end()
    return out + src[pos:]


# ---------------------------------------------------------------------------
# a header the pages share: one marked block, each page's own words beside it
# ---------------------------------------------------------------------------

LOADER = """<?php /* botix:page-meta — each page's own title, description and preview card are kept in
   botix-meta.php beside this file, by page address. Added by Botix; remove this block and that file to undo. */
if (!function_exists('botix_meta')) {
    function botix_meta($key) {
        static $mine = null;
        if ($mine === null) {
            $mine = array();
            $all = array();
            $file = __DIR__ . '/botix-meta.php';
            if (is_file($file)) { $all = include $file; }
            if (is_array($all)) {
                $path = parse_url(isset($_SERVER['REQUEST_URI']) ? $_SERVER['REQUEST_URI'] : '/', PHP_URL_PATH);
                $path = rawurldecode($path ? $path : '/');
                $path = preg_replace('#/index\\.(php|html?)$#i', '/', $path);
                $path = ($path === '/' || $path === '') ? '/' : rtrim($path, '/');
                if (isset($all[$path]) && is_array($all[$path])) { $mine = $all[$path]; }
            }
        }
        return isset($mine[$key]) ? (string) $mine[$key] : '';
    }
}
?>"""


def _with_loader(src):
    if MARK in src:
        return src
    at = _head_open(src)
    if at is None:
        return LOADER + "\n" + src
    return src[:at] + "\n" + LOADER + src[at:]


def _php_if(key, tag, original=""):
    head = f"<?php if (botix_meta('{key}') !== '') {{ ?>{tag}<?php }}"
    return head + (f" else {{ ?>{original}<?php }} ?>" if original else " ?>")


def _wrapped_tag(kind, key):
    if kind == "title":
        return "<title><?php echo botix_meta('title'); ?></title>"
    if kind == "canonical":
        return "<link rel=\"canonical\" href=\"<?php echo botix_meta('canonical'); ?>\">"
    attr = "property" if key.startswith("og:") else "name"
    return f"<meta {attr}=\"{key}\" content=\"<?php echo botix_meta('{key}'); ?>\">"


def _wrap(src, kind, key):
    """The header with this tag drawn from botix-meta.php, its own tag kept as the fallback."""
    if f"botix_meta('{key}')" in src:
        return src
    src = _with_loader(src)
    pattern = r"<title\b[^>]*>.*?</title\s*>" if kind == "title" else _tag_re(kind, key)
    # The tag may hold PHP (<title><?php echo $t ?></title>): kept whole, as written, as the fallback.
    ms = _matches(pattern, src, static=False)
    if ms:
        m = ms[0]
        return src[:m.start()] + _php_if(key, _wrapped_tag(kind, key), src[m.start():m.end()]) + src[m.end():]
    return _insert_in_head(src, _php_if(key, _wrapped_tag(kind, key)))


def _needle(kind, key):
    return r"<title\b" if kind == "title" else (r"rel\s*=\s*[\"']?canonical" if kind == "canonical"
                                                else re.escape(key) + r"[\"']")


def _php_str(v):
    return "'" + str(v).replace("\\", "\\\\").replace("'", "\\'") + "'"


def meta_file_text(rows) -> str:
    """botix-meta.php: {page address: {key: value}}, values already safe for HTML."""
    by = {}
    for path, key, value in rows:
        by.setdefault(path, {})[key] = value
    L = ["<?php", "// Botix: each page's own title, description and preview card, by page address.",
         "// Written by the agency and read by the botix:page-meta block in the header beside it.",
         "return array("]
    for path in sorted(by):
        inner = ", ".join(f"{_php_str(k)} => {_php_str(v)}" for k, v in sorted(by[path].items()))
        L.append(f"    {_php_str(path)} => array({inner}),")
    L.append(");")
    return "\n".join(L) + "\n"


def _meta_rows(holder):
    with board._conn() as c:
        _table(c)
        return [tuple(r) for r in c.execute("SELECT path, key, value FROM seo_page_meta WHERE holder=?", (holder,))]


def _keep_meta(holder, path, key, value, run_id):
    with board._conn() as c:
        _table(c)
        c.execute("INSERT OR REPLACE INTO seo_page_meta(holder,path,key,value,set_at,run_id) VALUES(?,?,?,?,?,?)",
                  (holder, path, key, _esc(value), _now(), run_id))


# ---------------------------------------------------------------------------
# the words (titles, descriptions, picture descriptions)
# ---------------------------------------------------------------------------

def _brand():
    try:
        from providers import integrations
        from coordination import profile
        return (integrations.read_env().get("BUSINESS_NAME") or profile.business().get("name") or "").strip()
    except Exception:
        return ""


def _language():
    try:
        from coordination import profile
        return (profile.language() or "en").split("-")[0].lower() or "en"
    except Exception:
        return "en"


def _trim(text, most):
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= most:
        return text
    cut = text[:most + 1].rsplit(" ", 1)[0].rstrip(" ,;:-–|")
    return cut


def _label(url):
    path = urllib.parse.urlparse(url).path
    words = re.sub(r"\.(php|html?)$", "", path.rsplit("/", 1)[-1] or "")
    return re.sub(r"[-_]+", " ", words).strip().capitalize()


def _paragraphs(p):
    """The page's own paragraphs (not its menu), or all its words when it has none."""
    paras = [re.sub(r"\s+", " ", _html.unescape(re.sub(r"<[^>]+>", " ", x))).strip()
             for x in re.findall(r"<p\b[^>]*>(.*?)</p\s*>", _PHP.sub(" ", p.get("html") or ""), re.I | re.S)]
    paras = [x for x in paras if len(x.split()) >= 8]
    return " ".join(paras) or p.get("text") or ""


def _sentences(text, most):
    """Whole sentences from the text, as many as fit."""
    out = ""
    for s in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", text or "").strip()):
        if len(s.split()) < 5 or NOT_IN_WORDS.search(s) or len(s) > most:
            continue
        if len(out) + len(s) + 1 > most:
            break
        out = (out + " " + s).strip()
    return out


def _plain_title(p, brand, host):
    h1 = next((t for lvl, t in p["headings"] if lvl == 1 and t), "")
    old = next((t for t in p["titles"] if t), "")
    base = h1 or re.split(r"\s+[|\-–—]\s+", old)[0] or _label(p["url"]) or "Home"
    brand = brand or host
    t = f"{base} | {brand}" if brand and brand.lower() not in base.lower() else base
    if len(t) > 60:
        t = _trim(base, 60)
    if len(t) < 30 and brand and brand.lower() not in t.lower():
        t = _trim(f"{t} | {brand}", 60)
    return t


def _plain_description(p, brand):
    d = _sentences(_paragraphs(p), 160)
    if len(d) >= 110:
        return d
    h1 = next((t for lvl, t in p["headings"] if lvl == 1 and t), "") or _label(p["url"])
    extra = ""
    try:
        from coordination import profile, uae
        extra = f" {profile.trade_line().capitalize()} in {uae.served_phrase()}."
    except Exception:
        pass
    d = _trim(f"{h1}{' from ' + brand if brand else ''}. {d}{extra}".replace("..", "."), 160)
    return d


def _plain_alt(img, page_h1):
    src = (img.get("src") or img.get("data-src") or "").split("?")[0]
    name = posixpath.basename(urllib.parse.urlparse(src).path)
    words = re.sub(r"\.[a-z0-9]{2,5}$", "", name, flags=re.I)
    words = re.sub(r"[-_.]+|\d+x\d+|\b(img|dsc|image|photo|pic|copy|final|scaled|min)\b|\d+", " ", words, flags=re.I)
    words = re.sub(r"\s+", " ", words).strip()
    if len(re.findall(r"[a-zA-Z]{3,}", words)) >= 2:
        return _trim(words.capitalize(), 110)
    return _trim(page_h1 or "Our work", 110)


def _ai_words(kind, items, think=None):
    """{key: text} from the AI for these items, or {} when it is not there."""
    if not items:
        return {}
    rules = {"title": "a page title of 35 to 58 characters: what the page is about first, the business name last "
                      "after ' | ' when it fits. Each title different from the others.",
             "description": "a description for Google of 120 to 155 characters: what the page offers and where, "
                            "in plain words, ending in a reason to click. No quotation marks.",
             "alt": "a picture description of 5 to 12 words saying what the picture shows, using the page's "
                    "subject. No 'image of' or 'picture of'."}[kind]
    lines = []
    for it in items[:40]:
        lines.append(json.dumps({k: v for k, v in it.items() if k != "_p"}, ensure_ascii=False)[:900])
    prompt = (f"Write {rules}\nBritish English, plain and human, nothing invented: only what the facts say. "
              "Never mention prices, discounts, free things or offers.\n"
              "Answer with one JSON object mapping each item's \"key\" to the text, nothing else.\n\n"
              + "\n".join(lines))
    try:
        if think is None:
            from providers import think
        r = think("seo", [{"role": "user", "content": prompt}], agent="seo-fixer", max_tokens=2500)
    except Exception:
        return {}
    if not r or not r.get("ok"):
        return {}
    raw = r.get("output") or r.get("text") or ""
    m = re.search(r"\{.*\}", raw, re.S)
    try:
        d = json.loads(m.group(0)) if m else {}
    except ValueError:
        d = {}
    from .scrub import scrub
    return {str(k): scrub(str(v)).strip().strip('"') for k, v in d.items() if isinstance(v, str)}


def _good(kind, text, taken=()):
    if not text or NOT_IN_WORDS.search(text) or "<" in text:
        return False
    n = len(text)
    if kind == "title":
        return 30 <= n <= 60 and text.lower() not in taken
    if kind == "description":
        return 110 <= n <= 165
    return 3 <= n <= 125


def write_words(kind, items, think=None):
    """
    ({key: text}, how) — 'ai' when the AI wrote every one, 'page' when they were
    made from each page's own heading and lines, 'mixed' when both. Every text is
    checked (length, no offers) before it is used; a bad one is replaced.
    """
    got = _ai_words(kind, items, think=think)
    out, used_ai, used_page = {}, 0, 0
    taken = {t.lower() for t in (items[0].get("_others") or [])} if items else set()
    for it in items:
        k = it["key"]
        text = got.get(k, "")
        if _good(kind, text, taken):
            used_ai += 1
        else:
            text = it["_plain"]
            if kind == "title" and text.lower() in taken:
                text = _trim(f"{text.split(' | ')[0]} — {_label(it.get('url') or '') or 'more'}", 60)
            used_page += 1
        out[k] = text
        if kind == "title":
            taken.add(text.lower())
    how = "ai" if used_ai and not used_page else "page" if used_page and not used_ai else "mixed"
    return out, how


# ---------------------------------------------------------------------------
# working out the change
# ---------------------------------------------------------------------------

def _host(url):
    h = urllib.parse.urlparse(url).netloc.lower()
    return h[4:] if h.startswith("www.") else h


class Plan:
    def __init__(self):
        self.files = {}            # name -> new source
        self.before = {}           # name -> source as read
        self.new_files = set()
        self.pages = {}            # url -> {"did": [...], "skip": reason, "values": {}}
        self.calls = []
        self.words_by = ""
        self.notes = []

    def edit(self, site, name, fn):
        cur = self.files.get(name, site.src.get(name))
        if cur is None:
            return False
        new = fn(cur)
        if new is None or new == cur:
            return new == cur
        self.before.setdefault(name, site.src.get(name))
        self.files[name] = new
        return True

    def did(self, url, text):
        self.pages.setdefault(url, {"did": [], "skip": "", "values": {}})["did"].append(text)

    def skip(self, url, why):
        self.pages.setdefault(url, {"did": [], "skip": "", "values": {}})["skip"] = why

    def value(self, url, key, v):
        self.pages.setdefault(url, {"did": [], "skip": "", "values": {}})["values"][key] = v


def _per_page_values(fix, targets, looks, think, others):
    """{url: {key: value}} for the head fixes, and how the words were made."""
    brand = _brand()
    out, how = {u: {} for u in targets}, ""
    if fix in ("title", "description"):
        items = []
        for u in targets:
            p = looks[u]
            h1 = next((t for lvl, t in p["headings"] if lvl == 1 and t), "")
            items.append({"key": u, "url": u, "heading": h1, "current": next((t for t in p["titles"] if t), "")
                          if fix == "title" else R._meta_content(p, "name", "description"),
                          "page_text": p["text"][:500], "business": brand,
                          "_plain": _plain_title(p, brand, _host(u)) if fix == "title" else _plain_description(p, brand),
                          "_others": others})
        words, how = write_words(fix, items, think=think)
        for u in targets:
            out[u][fix] = words[u]
    elif fix == "canonical":
        for u in targets:
            out[u]["canonical"] = u
    elif fix == "og":
        for u in targets:
            p = looks[u]
            title = next((t for t in p["titles"] if t), "") or next((t for lvl, t in p["headings"] if lvl == 1), "")
            desc = R._meta_content(p, "name", "description") or _sentences(_paragraphs(p), 160)
            img = next((urllib.parse.urljoin(u, i.get("src") or "") for i in R._content_imgs(p)
                        if re.search(r"\.(jpe?g|png|webp)(\?|$)", i.get("src") or "", re.I)
                        and not re.search(r"logo|icon|sprite", i.get("src") or "", re.I)), "")
            vals = {"og:title": title, "og:description": desc, "og:url": u,
                    "twitter:card": "summary_large_image" if img else "summary"}
            if img:
                vals["og:image"] = img
            out[u] = {k: v for k, v in vals.items() if v}
    return out, how


HEAD_KEYS = {"title": [("title", "title")], "description": [("meta", "description")],
             "canonical": [("canonical", "canonical")],
             "og": [("meta", "og:title"), ("meta", "og:description"), ("meta", "og:image"), ("meta", "og:url"),
                    ("meta", "twitter:card")]}


def plan_head(fix, targets, looks, site, plan, run_id, think=None, others=()):
    values, how = _per_page_values(fix, targets, looks, think, list(others))
    plan.words_by = how
    holders_used = {}
    for u in targets:
        for k, v in values[u].items():
            plan.value(u, k, v)
        name = site.file_for(u)
        if not name:
            plan.skip(u, "the file that makes this page could not be found (the site may build its pages "
                         "through one front file); the words are below to paste in by hand")
            continue
        holder = site.holder(name)
        if not holder:
            plan.skip(u, f"{name} and the files it pulls in have no <head> the agency can find")
            continue
        hsrc = plan.files.get(holder, site.src.get(holder)) or ""
        echoed = [key for kind, key in HEAD_KEYS[fix] if _echoed(hsrc, _needle(kind, key))]
        if echoed:
            plan.skip(u, f"the {echoed[0]} tag is printed by PHP code in {holder}, not written as a tag, so changing "
                         "it safely needs IT to look; the words are below")
            continue
        own = holder == name and not site.shared(name)
        dynamic = any("\x00" in m.group(0) for kind, key in HEAD_KEYS[fix]
                      for m in _matches(r"<title\b[^>]*>.*?</title\s*>" if kind == "title" else _tag_re(kind, key),
                                        hsrc, static=False))
        if own and not dynamic:
            for kind, key in HEAD_KEYS[fix]:
                v = values[u].get(key)
                if not v:
                    continue
                if kind == "title":
                    plan.edit(site, holder, lambda s, v=v: _set_title(s, v))
                else:
                    plan.edit(site, holder, lambda s, kind=kind, key=key, v=v: _set_tag(s, kind, key, v))
            plan.did(u, f"changed in {holder}")
            continue
        if not _is_php(holder, hsrc):
            plan.skip(u, f"the page head is in {holder}, which many pages share and which is plain HTML, so one "
                         "page's words cannot be given to it alone; the words are below to paste in by hand")
            continue
        for kind, key in HEAD_KEYS[fix]:
            v = values[u].get(key)
            if not v:
                continue
            plan.edit(site, holder, lambda s, kind=kind, key=key: _wrap(s, kind, key))
            _keep_meta(holder, _path_key(u), key, v, run_id)
        holders_used[holder] = True
        plan.did(u, f"set for this page in {posixpath.join(posixpath.dirname(holder), META_FILE)} "
                    f"(read by {holder})")
    for holder in holders_used:
        mf = posixpath.join(posixpath.dirname(holder), META_FILE)
        site.load([mf])
        if site.src.get(mf) is None:
            plan.new_files.add(mf)
        plan.before.setdefault(mf, site.src.get(mf))
        plan.files[mf] = meta_file_text(_meta_rows(holder))
    return plan


def plan_viewport(targets, site, plan):
    tag = '<meta name="viewport" content="width=device-width, initial-scale=1">'
    for u in targets:
        name = site.file_for(u)
        holder = site.holder(name) if name else ""
        if not holder:
            plan.skip(u, "the file holding this page's head could not be found")
            continue

        def fix(s):
            for m in reversed(_finditer_outside(_tag_re("meta", "viewport"), s)):
                s = s[:m.start()] + s[m.end():]
            return _insert_in_head(s, tag, first=True)
        plan.edit(site, holder, fix)
        plan.did(u, f"viewport line set in {holder}")
    return plan


def plan_lang(targets, site, plan):
    lang = _language()
    for u in targets:
        name = site.file_for(u)
        f = site.html_tag_file(name) if name else ""
        if not f:
            plan.skip(u, "the file with this page's <html> tag could not be found")
            continue

        def fix(s):
            m = next(iter(_finditer_outside(r"<html\b[^>]*>", s)), None)
            if not m:
                return None
            return s[:m.start()] + _set_attr(m.group(0), "lang", lang) + s[m.end():]
        plan.edit(site, f, fix)
        plan.did(u, f'lang="{lang}" set in {f}')
    return plan


def plan_noindex(targets, site, plan, looks=None):
    looks = looks or {}
    for u in targets:
        if LOOKS_DELIBERATE.search(urllib.parse.urlparse(u).path):
            plan.skip(u, "left hidden: it looks like a page hidden on purpose (a thank-you or test page). "
                         "Tell IT if it should be on Google")
            continue
        name = site.file_for(u, (looks.get(u) or {}).get("html") or "")
        found = False
        for f in (site.chain(name) if name else []):
            src = plan.files.get(f, site.src.get(f)) or ""
            tags = _finditer_outside(r"<meta\b[^>]*\bname\s*=\s*[\"']?(robots|googlebot)\b[^>]*>", src)
            tags = [m for m in tags if re.search(r"noindex|none|nosnippet|max-snippet\s*:\s*0", m.group(0), re.I)]
            if not tags:
                continue
            if f != name and site.shared(f):
                users = [r["url"] for r in site.map.get("pages") or [] if f in site.chain(r.get("file") or "")]
                if any(x not in targets for x in users):
                    plan.skip(u, f"the 'do not list' line is in {f}, which other pages read too; changing it "
                                 "there could unhide pages hidden on purpose, so IT should look")
                    found = True
                    break

            def fix(s):
                for m in reversed(_finditer_outside(r"<meta\b[^>]*\bname\s*=\s*[\"']?(robots|googlebot)\b[^>]*>", s)):
                    tag = m.group(0)
                    if not re.search(r"noindex|none|nosnippet|max-snippet\s*:\s*0", tag, re.I):
                        continue
                    left = [d.strip() for d in (_attr(tag, "content") or "").split(",")
                            if d.strip() and not re.match(r"(noindex|none|nosnippet|max-snippet\s*:\s*0)$",
                                                          d.strip(), re.I)]
                    new = _set_attr(tag, "content", ", ".join(left)) if left else ""
                    s = s[:m.start()] + new + s[m.end():]
                return s
            plan.edit(site, f, fix)
            plan.did(u, f"'do not list' line taken out of {f}")
            found = True
            break
        if not found and u not in plan.pages:
            plan.skip(u, "the 'do not list' line is not written in any file the agency can read — it may come "
                         "from the server (an X-Robots-Tag header) or from code; IT should look")
    return plan


def plan_h1(code, targets, looks, site, plan):
    for u in targets:
        name = site.file_for(u)
        if not name:
            plan.skip(u, "the file that makes this page could not be found")
            continue
        chain = site.chain(name)
        if code == "core-h1-present":
            src = plan.files.get(name, site.src.get(name)) or ""
            m = next(iter(_finditer_outside(r"<h2\b([^>]*)>(.*?)</h2\s*>", src)), None)
            if not m:
                plan.skip(u, f"{name} has no sub-heading to promote; the page needs a main heading written for it")
                continue
            plan.edit(site, name, lambda s, m=m: s[:m.start()] + f"<h1{m.group(1)}>{m.group(2)}</h1>" + s[m.end():])
            plan.did(u, f"its first sub-heading in {name} is now the main heading (it may look a little bigger)")
            continue
        # One main heading: the page's own file keeps its first; the rest become h2.
        kept = False
        changed = []
        for f in [name] + [x for x in chain if x != name]:
            src = plan.files.get(f, site.src.get(f)) or ""
            hs = _finditer_outside(r"<h1\b([^>]*)>(.*?)</h1\s*>", src)
            if not hs:
                continue
            drop = hs[1:] if (f == name and not kept) else hs
            if f == name and hs:
                kept = True
            if not drop:
                continue

            def fix(s, drop_at=[(m.start(), m.end()) for m in drop]):
                for a, b in reversed(drop_at):
                    m = re.match(r"<h1\b([^>]*)>(.*?)</h1\s*>", s[a:b], re.I | re.S)
                    if m:
                        s = s[:a] + f"<h2{m.group(1)}>{m.group(2)}</h2>" + s[b:]
                return s
            plan.edit(site, f, fix)
            changed.append(f)
        if changed:
            plan.did(u, "extra main heading(s) made sub-headings in " + ", ".join(changed)
                     + " (they may look a little smaller)")
        else:
            plan.skip(u, "the extra main headings are made by code the agency cannot follow")
    return plan


def _img_tags(src):
    return _finditer_outside(r"<img\b[^>]*>", src)


def plan_images(fix, targets, looks, site, plan, think=None):
    """alt and lazy: change the <img> tags in the files that make each page, matched by their src."""
    want = {}                                  # raw src -> {"page": url, "img": img dict}
    for u in targets:
        p = looks[u]
        imgs = R._content_imgs(p)
        if fix == "lazy":
            bad = [i for i in imgs[3:] if (i.get("loading") or "").lower() != "lazy"]
        else:
            bad = [i for i in imgs if "alt" not in i or re.fullmatch(
                r"(image|photo|picture|img|logo|banner|icon|pic)?\s*\d*", (i.get("alt") or "").strip(), re.I)
                or re.search(r"\.(jpe?g|png|webp|gif)$|^(img|dsc|photo|image)[-_]?\d+", (i.get("alt") or "").strip(), re.I)]
        for i in bad:
            raw = i.get("src") or i.get("data-src") or ""
            if raw:
                want.setdefault(raw, {"page": u, "img": i, "pages": set()})["pages"].add(u)
    words = {}
    if fix == "alt" and want:
        items = []
        for raw, w in want.items():
            p = looks[w["page"]]
            h1 = next((t for lvl, t in p["headings"] if lvl == 1 and t), "")
            items.append({"key": raw, "file_name": posixpath.basename(urllib.parse.urlparse(raw).path),
                          "page_heading": h1, "page_title": next((t for t in p["titles"] if t), ""),
                          "_plain": _plain_alt(w["img"], h1)})
        words, plan.words_by = write_words("alt", items, think=think)
    done_src, files_by_page = set(), {}
    for u in targets:
        name = site.file_for(u)
        files_by_page[u] = site.chain(name) if name else []
    for raw, w in want.items():
        for u in sorted(w["pages"]):
            for f in files_by_page.get(u) or []:
                src = plan.files.get(f, site.src.get(f)) or ""
                hit = [m for m in _img_tags(src) if _attr(m.group(0), "src") == raw or _attr(m.group(0), "data-src") == raw]
                if not hit:
                    continue

                def fix_tags(s, raw=raw):
                    for m in reversed([m for m in _img_tags(s) if _attr(m.group(0), "src") == raw
                                       or _attr(m.group(0), "data-src") == raw]):
                        tag = m.group(0)
                        tag = _set_attr(tag, "alt", _esc(words[raw])) if fix == "alt" else _set_attr(tag, "loading", "lazy")
                        s = s[:m.start()] + tag + s[m.end():]
                    return s
                plan.edit(site, f, fix_tags)
                done_src.add(raw)
                break
            if raw in done_src:
                break
    for u in targets:
        mine = [raw for raw, w in want.items() if u in w["pages"]]
        if not mine:
            continue
        ok = [r for r in mine if r in done_src]
        if ok:
            plan.did(u, f"{len(ok)} of {len(mine)} picture(s) " + ("described" if fix == "alt" else "set to load when needed"))
        if len(ok) < len(mine):
            plan.skip(u, f"{len(mine) - len(ok)} picture(s) are put on the page by code, not written in a file, so "
                         "they cannot be changed here")
    for raw in want:
        if fix == "alt" and raw in done_src:
            for u in want[raw]["pages"]:
                plan.value(u, "alt:" + posixpath.basename(raw)[:60], words[raw])
    return plan


def plan_source_edits(fix, targets, site, plan):
    """noopener, https_links, copyright: the same small change in every file that makes the pages."""
    year = time.strftime("%Y")
    seen_files = set()
    for u in targets:
        name = site.file_for(u)
        if not name:
            plan.skip(u, "the file that makes this page could not be found")
            continue
        host = _host(u)
        touched = []
        for f in site.chain(name):
            if f in seen_files:
                touched.append(f)
                continue
            php = _is_php(f, site.src.get(f))

            def fix_src(s):
                if fix == "noopener":
                    for m in reversed(_finditer_outside(r"<a\b[^>]*\btarget\s*=\s*[\"']?_blank\b[^>]*>", s)):
                        tag = m.group(0)
                        rel = (_attr(tag, "rel") or "")
                        if re.search(r"noopener|noreferrer", rel, re.I):
                            continue
                        s = s[:m.start()] + _set_attr(tag, "rel", (rel + " noopener").strip()) + s[m.end():]
                    return s
                if fix == "https_links":
                    spans = _php_spans(s)
                    pat = r"http://((?:www\.)?" + re.escape(host) + r")(?=[/\"'\s>]|$)"
                    out, pos = "", 0
                    for m in re.finditer(pat, s, re.I):
                        if not _outside_php(spans, m.start(), m.end()):
                            continue
                        out += s[pos:m.start()] + "https://" + m.group(1)
                        pos = m.end()
                    return out + s[pos:]
                if fix == "scrub":
                    from .scrub import scrub
                    spans = _php_spans(s)
                    out, pos = "", 0
                    for a, b in spans + [(len(s), len(s))]:
                        out += scrub(s[pos:a], spaces=False) + s[a:b]
                        pos = b
                    return out
                if fix == "copyright":
                    now = "<?php echo date('Y'); ?>" if php else year

                    def year_of(m):
                        return m.group(1) + m.group(2) + (m.group(3) + m.group(4) if m.group(3) else "") + now
                    spans = _php_spans(s)
                    out, pos = "", 0
                    for m in re.finditer(r"(©|&copy;|&#169;|\(c\)|copyright)(\s*)(?:((?:19|20)\d{2})(\s*[-–]\s*))?((?:19|20)\d{2})",
                                         s, re.I):
                        if not _outside_php(spans, m.start(), m.end()) or int(m.group(5)) >= int(year) - 1:
                            continue
                        out += s[pos:m.start()] + m.group(1) + m.group(2) + ((m.group(3) or "") + (m.group(4) or "")) + now
                        pos = m.end()
                    return out + s[pos:]
                return s
            if plan.edit(site, f, fix_src) and f in plan.files:
                touched.append(f)
                seen_files.add(f)
        if touched:
            plan.did(u, "changed in " + ", ".join(dict.fromkeys(touched)))
        else:
            plan.skip(u, "nothing to change was found written in the files that make this page (it may be put "
                         "there by code or a plugin)")
    return plan


# ---------------------------------------------------------------------------
# checking on the live site
# ---------------------------------------------------------------------------

def _rule_status(code, p, others=()):
    r = R.BY_ID.get(code)
    if not r:
        return R.NA, ""
    if r.get("fn") and not r.get("site"):
        try:
            return r["fn"](p)
        except Exception as e:
            return R.NA, f"could not be checked ({type(e).__name__})"
    if code == "core-title-unique":
        t = next((x for x in p["titles"] if x), "").strip().lower()
        return (R.PASS, "") if t and t not in others else (R.WARN, "still shares its title")
    if code == "content-description-unique":
        d = R._meta_content(p, "name", "description").strip().lower()
        return (R.PASS, "") if d and d not in others else (R.WARN, "still shares its description")
    return R.NA, ""


def _others(code, targets):
    """Titles (or descriptions) of the pages not being changed, from the last full check."""
    from . import site_blueprint as bp
    if code not in ("core-title-unique", "content-description-unique"):
        return set()
    with board._conn() as c:
        bp._table(c)
        last = bp._last_run(c)
    seen = json.loads(last[5] or "[]") if last else []
    keep = {_path_key(u) for u in targets}
    if code == "core-title-unique":
        return {(x.get("title") or "").strip().lower() for x in seen
                if _path_key(x.get("url") or "") not in keep and x.get("title")}
    return set()


def check_live(code, urls, fetch, others=()):
    """{url: (status, found)} for each page, fetched now."""
    out = {}
    titles = {}
    looks = {}
    for u in urls:
        p, why = _look(u, fetch)
        looks[u] = p
        if p is None:
            out[u] = (None, why)
            continue
        if code == "core-title-unique":
            titles[u] = next((x for x in p["titles"] if x), "").strip().lower()
    for u in urls:
        p = looks[u]
        if p is None:
            continue
        mine = set(others)
        if code == "core-title-unique":
            mine |= {t for x, t in titles.items() if x != u and t}
        out[u] = _rule_status(code, p, mine)
    return out, looks


def _ok(st):
    return st in (R.PASS, R.NA)


# ---------------------------------------------------------------------------
# the blueprint's own checks (robots.txt, the sitemap, llms.txt, the markup)
# ---------------------------------------------------------------------------

def check_file(code, fetch, home):
    """(True|False|None, what was seen) for one of the blueprint's file checks, read live now."""
    from . import site_blueprint as bp
    from . import sitemap
    home = home.rstrip("/")

    def get(u):
        try:
            st, body, headers, _final = fetch(u)
            return st, body
        except Exception as e:
            return None, f"{type(e).__name__}"
    if code in ("robots_missing", "robots_no_sitemap"):
        st, body = get(home + "/robots.txt")
        if st != 200 or not body.strip() or body.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
            return False, f"robots.txt answered {st}"
        if code == "robots_no_sitemap":
            return bool(re.search(r"(?im)^\s*sitemap\s*:", body)), "robots.txt " + (
                "names the sitemap" if re.search(r"(?im)^\s*sitemap\s*:", body) else "has no Sitemap line")
        return True, "robots.txt is there"
    if code == "llms_missing":
        st, body = get(home + "/llms.txt")
        good = st == 200 and body.strip() and not body.lstrip()[:15].lower().startswith(("<!doctype", "<html"))
        return bool(good), f"llms.txt answered {st}"
    if code in ("sitemap_missing", "sitemap_incomplete"):
        for u in (sitemap.live_url(home), home + "/sitemap.xml"):
            st, body = get(u)
            if st == 200 and re.search(r"<(urlset|sitemapindex)", body or ""):
                if code == "sitemap_missing":
                    return True, f"{u} lists the pages"
                listed = {bp._same(x) for x in bp._sitemap_locs(body)}
                with board._conn() as c:
                    bp._table(c)
                    last = bp._last_run(c)
                seen = [x["url"] for x in json.loads(last[5] or "[]")] if last else []
                left = [x for x in seen if bp._same(x) not in listed]
                return (not left), (f"{len(left)} page(s) still not in it" if left else "every page read is in it")
        return False, "no sitemap answered"
    if code in ("schema_missing", "schema-local-business", "schema-valid"):
        try:
            st, body, headers, final = fetch(home + "/")
        except Exception as e:
            return None, type(e).__name__
        p = R.parse(body, home + "/", headers=headers, status=st)
        if code == "schema_missing":
            objs, _bad = R._jsonld_objects(p)
            ok = any(t in R.LOCAL_TYPES for t in R._types(objs))
            return ok, "business markup " + ("found" if ok else "not found") + " on the home page"
        st_, found = _rule_status(code, p)
        return _ok(st_), found or "checked"
    return None, "not something this check can read"


# ---------------------------------------------------------------------------
# the run
# ---------------------------------------------------------------------------

def _queue(plan, title):
    """Every change, queued as the Fixer's (so the Releaser carries it out). Returns the queue ids."""
    from . import site_api
    ids = []
    order = sorted(plan.files, key=lambda n: (0 if n in plan.new_files or n.endswith(META_FILE) else 1, n))
    for name in order:
        detail = {"change": {"page": name, "html": plan.files[name], "whole_page": name not in plan.new_files},
                  "why": f"Fix button: {title}. Pressed by you in the office."}
        kind = "new_page" if name in plan.new_files else "page"
        try:
            cid = board.queue_site_change(FIXER, kind, detail)
        except ValueError as e:
            plan.notes.append(f"{name} was too big to queue: {e}")
            continue
        ids.append(cid)
    for call in plan.calls:
        ids.append(board.queue_site_change(FIXER, call, {"change": {"call": call},
                                                         "why": f"Fix button: {title}. Pressed by you in the office."}))
    _ = site_api
    return ids


def _approve_and_release(ids, release=None):
    """He pressed Fix: that is his yes. The Releaser does the writing, after the tests."""
    for i in ids:
        board.decide_site_change(i, "approved")
    from . import it_release
    run = release or it_release.run
    r = run(limit=max(10, len(ids) + 5))
    with board._conn() as c:
        c.row_factory = sqlite3.Row
        rows = {r_["id"]: dict(r_) for r_ in c.execute(
            "SELECT id, kind, detail, status, note FROM site_queue WHERE id IN (%s)" % ",".join("?" * len(ids)), ids)} \
            if ids else {}
    return r or {}, rows


def _home_alive(fetch, home):
    try:
        st, body, _h, _f = fetch(home)
        return st == 200 and bool((body or "").strip())
    except Exception:
        return None


def run(run_id, fetch=None, read=None, smap=None, think=None, release=None) -> dict:
    """Do one fix from start to finish. Never raises: a failure is a report too."""
    try:
        return _run(run_id, fetch, read, smap, think, release)
    except Exception as e:                                       # pragma: no cover - last resort
        return _finish(run_id, {"state": "failed", "headline": f"The fix stopped with an error and changed "
                                f"nothing more: {type(e).__name__}: {e}"[:400]})


def _run(run_id, fetch, read, smap, think, release):
    st = status(run_id)
    if not st.get("ok"):
        return st
    code = st["code"]
    g = _gap(code)
    if not g:
        return _finish(run_id, {"state": "failed", "headline": "That problem is no longer on the list."})
    from . import site_blueprint as bp
    fetch = fetch or _fetcher()
    from . import sitemap
    home = (bp.stored().get("home") or sitemap.site_address() or "").rstrip("/") + "/"
    d = describe(code, g.get("fix") or "", g.get("part") or "")
    report = {"code": code, "title": g["title"], "pages": [], "total": 0, "fixed": 0, "already": 0,
              "written": [], "words_by": "", "notes": []}

    # ---- not something the button does by itself: handed on, said plainly ----
    if not d["auto"]:
        _step(run_id, "Handing it to the desk that owns it")
        where = _hand_over(g)
        report.update({"state": "cannot", "headline": f"Not fixed — this needs {d['needs']}. "
                                                      f"{where}", "how": g.get("how") or "",
                       "why": g.get("why") or ""})
        steps = bp.ADVICE.get(code)
        if steps:
            report["steps"] = steps
        return _finish(run_id, report)

    # ---- nothing can be written: said before anything is worked out ------------
    from . import site_api
    blocked = site_api.why_not_ready()
    if blocked:
        report.update({"state": "cannot", "headline": "Not fixed — the agency cannot change your website yet: "
                                                      + blocked})
        return _finish(run_id, report)

    # ---- one of the routines with one right answer --------------------------
    if d.get("call"):
        _step(run_id, "Checking it is still missing")
        now_ok, seen = check_file(code, fetch, home)
        if now_ok:
            _close_gap(code, fully=True)
            report.update({"state": "fixed", "headline": f"Already right on your live site ({seen}) — "
                                                         "nothing needed changing.", "already": 1, "total": 1})
            return _finish(run_id, report)
        plan = Plan()
        plan.calls = [d["call"]]
        _step(run_id, "Running the safety tests, then putting it on your website")
        ids = _queue(plan, g["title"])
        rel, rows = _approve_and_release(ids, release)
        report["release"] = _release_words(rel, rows)
        if any(r_["status"] == "approved" for r_ in rows.values()):
            report.update({"state": "waiting", "headline": "Approved, not on your website yet: "
                                                           + report["release"]})
            return _finish(run_id, report)
        _step(run_id, "Checking your live website")
        now_ok, seen = check_file(code, fetch, home)
        report["total"] = 1
        if now_ok:
            report.update({"state": "fixed", "fixed": 1, "headline": f"Fixed — {seen}, checked on your live "
                                                                     "website just now."})
            _close_gap(code, fully=True)
        else:
            report.update({"state": "not_fixed", "headline": f"Not fixed — {report['release']} Your live website "
                                                             f"still shows: {seen}."})
        return _finish(run_id, report)

    # ---- a change to the pages themselves -------------------------------------
    targets = list(dict.fromkeys(g.get("pages") or []))
    over = max(0, len(targets) - MOST_PAGES)
    targets = targets[:MOST_PAGES]
    if not targets:
        return _finish(run_id, {**report, "state": "failed",
                                "headline": "The problem does not name any pages to change — check the website "
                                            "again, then press Fix."})
    _step(run_id, f"Looking at {len(targets)} page(s) on your live website first")
    others = _others(code, targets)
    before, looks = check_live(code, targets, fetch, others)
    report["total"] = len(targets)
    gone = [u for u in targets if before[u][0] is None]
    already = [u for u in targets if before[u][0] is not None and _ok(before[u][0])]
    todo = [u for u in targets if u not in gone and u not in already]
    report["already"] = len(already)
    if not todo:
        report["pages"] = [_page_row(u, "already" if u in already else "gone", before[u][1]) for u in targets]
        state = "fixed" if not gone else "not_fixed"
        report.update({"state": state, "headline": (
            f"Already right on all {len(targets)} page(s) when the agency looked just now — nothing needed changing."
            if not gone else f"{len(gone)} page(s) could not be read just now, so nothing was changed.")})
        if state == "fixed":
            _close_gap(code, fully=True)
        return _finish(run_id, report)

    _step(run_id, "Reading the files that make those pages")
    from . import site_map
    if read is None:
        why = site_api.why_cannot_read()
        if why:
            report.update({"state": "cannot", "headline": "Not fixed — the agency cannot read your website's files: "
                                                          + why})
            return _finish(run_id, report)
        read = _reader()
    if smap is None:
        smap = site_map.stored(site_map.FRESH) or site_map.build()
    if not smap or not smap.get("pages"):
        report.update({"state": "cannot", "headline": "Not fixed — the agency could not work out which file makes "
                                                      "each page: " + str((smap or {}).get("error") or "no map")})
        return _finish(run_id, report)
    if smap.get("kind") == "wordpress":
        report.update({"state": "cannot", "headline": "Not fixed by the button — your site is WordPress, where "
                                                      "titles and tags belong to your SEO plugin (Yoast or Rank Math), "
                                                      "not to files the agency should edit."})
        return _finish(run_id, report)
    site = Site(read, smap)
    plan = Plan()
    fix = g.get("fix") or ""
    _step(run_id, "Working out the change" + (" and writing the words" if fix in ("title", "description", "alt") else ""))
    if fix in HEAD_KEYS:
        plan_head(fix, todo, looks, site, plan, run_id, think=think, others=others)
    elif fix == "viewport":
        plan_viewport(todo, site, plan)
    elif fix == "lang":
        plan_lang(todo, site, plan)
    elif fix == "noindex":
        plan_noindex(todo, site, plan, looks)
    elif fix == "h1":
        plan_h1(code, todo, looks, site, plan)
    elif fix in ("alt", "lazy"):
        plan_images(fix, todo, looks, site, plan, think=think)
    elif fix in ("noopener", "https_links", "copyright", "scrub"):
        plan_source_edits(fix, todo, site, plan)
    report["words_by"] = plan.words_by
    report["files"] = sorted(plan.files)
    if not plan.files:
        report["pages"] = [_page_row(u, "already", before[u][1]) for u in already] + \
                          [_page_row(u, "skipped", (plan.pages.get(u) or {}).get("skip") or before[u][1],
                                     plan.pages.get(u)) for u in todo] + \
                          [_page_row(u, "gone", before[u][1]) for u in gone]
        report.update({"state": "cannot", "headline": "Not fixed — nothing on your website could be changed "
                                                      "safely for this. The reasons are listed by page."})
        return _finish(run_id, report)

    _step(run_id, "Running the safety tests, then writing the change to your website")
    ids = _queue(plan, g["title"])
    rel, rows = _approve_and_release(ids, release)
    report["release"] = _release_words(rel, rows)
    applied = [r_ for r_ in rows.values() if r_["status"] == "applied"]
    waiting = [r_ for r_ in rows.values() if r_["status"] == "approved"]
    failed = [r_ for r_ in rows.values() if r_["status"] not in ("applied", "approved")]
    report["written"] = [json.loads(r_["detail"] or "{}").get("change", {}).get("page") for r_ in applied]
    if waiting and not applied:
        report["pages"] = [_page_row(u, "waiting", "", plan.pages.get(u)) for u in todo]
        report.update({"state": "waiting", "headline": "Approved, not on your website yet: " + report["release"]})
        return _finish(run_id, report)

    _step(run_id, "Checking your live website")
    if _home_alive(fetch, home) is False:
        for name in report["written"]:
            site_api.undo(page=name, agent=RELEASER)
        report.update({"state": "not_fixed", "put_back": report["written"],
                       "headline": "Not fixed — after the change your home page stopped answering properly, so every "
                                   "file this fix wrote was put back at once. Your site is as it was. IT will look at "
                                   "why."})
        _hand_over(g, extra="The Fix button's change made the home page stop answering and was put back.")
        return _finish(run_id, report)
    after, _looks = check_live(code, todo, fetch, others)
    return _finish(run_id, _tally(run_id, code, g, report, targets, todo, already, gone, before, after, plan,
                                  failed, waiting, over, read, site))


def _tally(run_id, code, g, report, targets, todo, already, gone, before, after, plan, failed, waiting, over,
           read, site):
    fixed = [u for u in todo if after[u][0] is not None and _ok(after[u][0])]
    still = [u for u in todo if u not in fixed]
    rows = [_page_row(u, "already", before[u][1]) for u in already]
    cached = []
    for u in todo:
        info = plan.pages.get(u) or {}
        if u in fixed:
            rows.append(_page_row(u, "fixed", "", info))
            continue
        why = info.get("skip") or ""
        if not why and after[u][0] is None:
            why = f"the page {after[u][1]} when checked afterwards"
        if not why and info.get("did"):
            # Written, but the live page still shows the problem. Is it in the file?
            name = site.file_for(u)
            holder = site.holder(name) if name else ""
            back = read([n for n in plan.files if n in (name, holder) or n.endswith(META_FILE)]) if read else {}
            landed = all(back.get(n) == plan.files[n] for n in back) and bool(back)
            if landed:
                cached.append(u)
                why = ("the change is in your files, but the live page still shows the old version — usually a "
                       "cache on your hosting; press Check again in an hour")
            else:
                why = "the change did not reach the file" + (f" ({failed[0].get('note')})" if failed else "")
        rows.append(_page_row(u, "still", why or f"still shows: {after[u][1]}", info))
    rows += [_page_row(u, "gone", before[u][1]) for u in gone]
    n_total = len(targets)
    n_fixed = len(fixed)
    report.update({"pages": rows, "fixed": n_fixed, "still": len(still), "cached": len(cached),
                   "notes": plan.notes + ([f"Only the first {MOST_PAGES} pages were done; press Fix again for the "
                                           f"other {over}."] if over else [])})
    fixed_all = n_fixed + len(already)
    if fixed_all == n_total and not over:
        report["state"] = "fixed"
        report["headline"] = (f"Fixed on all {n_fixed} page(s) that had it"
                              + (f" ({len(already)} more were already right)" if already else "")
                              + " — checked on your live website just now.")
        _close_gap(code, fully=True)
    elif n_fixed:
        report["state"] = "partly"
        report["headline"] = (f"Partly fixed: {n_fixed} of {len(todo)} page(s) now pass on your live website"
                              + (f" ({len(already)} were already right)" if already else "")
                              + f". {len(todo) - n_fixed} still show the problem — the reason is beside each one.")
        _close_gap(code, fully=False, left=still + gone, total=n_total)
    else:
        report["state"] = "not_fixed"
        report["headline"] = ("Not fixed yet: " + (rows[-1]["why"] if len(still) == 1 else
                              f"none of the {len(todo)} page(s) pass on your live website yet — the reason is "
                              "beside each one."))
    if waiting:
        report["headline"] += f" {len(waiting)} change(s) are still waiting: " + report.get("release", "")
    return report


def _page_row(url, state, why, info=None):
    info = info or {}
    return {"url": url, "path": urllib.parse.urlparse(url).path or "/", "state": state, "why": why or "",
            "did": info.get("did") or [], "values": info.get("values") or {}}


def _release_words(rel, rows):
    if not rows:
        return rel.get("summary") or rel.get("error") or ""
    applied = sum(1 for r in rows.values() if r["status"] == "applied")
    waiting = [r for r in rows.values() if r["status"] == "approved"]
    failed = [r for r in rows.values() if r["status"] not in ("applied", "approved")]
    out = f"{applied} of {len(rows)} change(s) written by the Releaser."
    if waiting:
        out += " Waiting: " + ((waiting[0].get("note") or rel.get("error") or "the Releaser has not run yet")
                               .split("\n")[0][:240])
    if failed:
        out += " Could not be written: " + (failed[0].get("note") or "")[:240]
    return out


def _close_gap(code, fully=True, left=None, total=0):
    from . import site_blueprint as bp
    with board._conn() as c:
        bp._table(c)
        if fully:
            c.execute("UPDATE site_gaps SET status='closed', closed_at=? WHERE code=? AND status='open'",
                      (_now(), code))
        else:
            left = left or []
            c.execute("UPDATE site_gaps SET pages=?, evidence=? WHERE code=?",
                      (json.dumps(left), f"{len(left)} of {total} page(s) still have it after the Fix button: "
                                         + bp._paths(left), code))


def _hand_over(g, extra="") -> str:
    """Put the problem on the owning desk's list now. Returns where it went, in his words."""
    from coordination import proposals
    from . import site_blueprint as bp
    sector = g.get("sector") or "IT"
    pages = g.get("pages") or []
    detail = (f"[blueprint:{g['code']}] {g['title']}. Found: {g.get('evidence') or ''}. "
              f"Why it matters: {g.get('why') or ''}" + (f" What to do: {g['how']}" if g.get("how") else "")
              + (f" {extra}" if extra else ""))
    if sector == "IT":
        iid, _new = board.file_site_issue("blueprint", g.get("severity") or "medium", detail,
                                          (pages[0] if pages else g.get("page") or ""), FIXER)
        ref, words = f"issue:{iid}", "Handed to IT — it is on their list now."
    else:
        pid = proposals.propose(sector, g["title"], detail, g.get("evidence") or "",
                                g.get("how") or "", 2)
        ref, words = (f"proposal:{pid}", f"Handed to {sector} through the MD — it is on the list now.") if pid else \
            ("", f"It is already on {sector}'s list.")
    if ref:
        with board._conn() as c:
            bp._table(c)
            c.execute("UPDATE site_gaps SET ref=? WHERE code=?", (ref, g["code"]))
    return words


def verify(run_id, fetch=None) -> dict:
    """Look at the live pages again for a fix already done (a cache may have cleared)."""
    st = status(run_id)
    if not st.get("ok"):
        return st
    rep = st["report"] or {}
    code = st["code"]
    fetch = fetch or _fetcher()
    if rep.get("state") not in ("partly", "not_fixed", "waiting") or not rep.get("pages"):
        return {"ok": True, **st}
    urls = [r["url"] for r in rep["pages"] if r["state"] in ("still", "waiting")]
    after, _ = check_live(code, urls, fetch, _others(code, urls))
    now_fixed = [u for u in urls if after[u][0] is not None and _ok(after[u][0])]
    for r in rep["pages"]:
        if r["url"] in now_fixed:
            r["state"], r["why"] = "fixed", ""
    rep["fixed"] = sum(1 for r in rep["pages"] if r["state"] == "fixed")
    done = rep["fixed"] + sum(1 for r in rep["pages"] if r["state"] == "already")
    left = [r["url"] for r in rep["pages"] if r["state"] not in ("fixed", "already")]
    rep["checked_again"] = _now()
    if not left:
        rep["state"] = "fixed"
        rep["headline"] = f"Fixed on all {rep['fixed']} page(s) — checked on your live website again just now."
        _close_gap(code, fully=True)
    elif rep["fixed"]:
        rep["state"] = "partly"
        rep["headline"] = (f"Partly fixed: {rep['fixed']} of {rep['total'] - (done - rep['fixed'])} page(s) pass on "
                           f"your live website now. {len(left)} still show the problem.")
        _close_gap(code, fully=False, left=left, total=rep["total"])
    else:
        rep["headline"] = "Checked again just now: the live pages still show the problem. " + (
            rep["pages"][0].get("why") if len(rep["pages"]) == 1 else "The reason is beside each page.")
    with board._conn() as c:
        _table(c)
        c.execute("UPDATE seo_fix_runs SET state=?, report=? WHERE id=?", (rep["state"], json.dumps(rep), run_id))
    return status(run_id)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="The Fix button: put one SEO problem right and check it live.")
    ap.add_argument("--start", default="")
    ap.add_argument("--run", type=int, default=0)
    ap.add_argument("--verify", type=int, default=0)
    ap.add_argument("--status", type=int, default=0)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    board.ensure_schema()
    if a.start:
        r = start(a.start)
    elif a.run:
        with board.agent_run(FIXER) as row:
            r = run(a.run)
            row.note("Fix button: " + (r.get("headline") or r.get("error") or ""))
    elif a.verify:
        r = verify(a.verify)
    elif a.status:
        r = status(a.status)
    else:
        ap.print_help()
        return 1
    print(json.dumps(r, indent=2, ensure_ascii=False, default=str) if a.json else
          (r.get("headline") or (r.get("report") or {}).get("headline") or r.get("error") or json.dumps(r)))
    return 0 if r.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
