"""
site_map.py — which file on his server makes each page a visitor sees.

    python -m pipelines.site_map                 build it (reads the site gently)
    python -m pipelines.site_map --stored --json the last map, from the cabinet

"the program will check ... is the code written html or php and program will
 adopt to that and in which file is home page file written or which file is
 working as the main file not as the blind to work on the file that is not
 even on website's pages"

THE BLIND SPOT THIS CLOSES

The agency could list every .php and .html file on his server, and it could
read every page a visitor sees — but nothing joined the two. So it assumed:
the home page was index.php (on an HTML site it is index.html), "/about" was
a file called "about" (it is about.php), and a find-and-replace walked every
file on the server, old copies and test pages included — files no visitor
ever sees.

HOW A PAGE IS MATCHED TO ITS FILE

For each page a visitor can reach, the files that could serve its address are
the ones a web server would try: /about → about.php, about.html, about/index.php;
/ → index.php, index.html and the other usual home-page names. Of those that
exist, the one whose own text actually appears on the live page is the one —
for a .html file the page IS the file; for a .php file the words written in
the file outside its PHP code show up on the page. Where no file could serve
an address, the site makes its pages through one front file (WordPress and
most frameworks do), and the map says so rather than guessing.

From that: the home page's file, whether the site is PHP or HTML, the shared
files every page pulls in (header, footer, menu) and which of them holds the
page head, and — only when every page was read — the files on the server that
no page uses, which the agency now leaves alone.

Reads through the connector (two or three requests for the files, many at a
time) and reuses the pages the blueprint already read where it can. Never
writes anything.
"""
import argparse
import json
import posixpath
import re
import sqlite3
import sys
import time
import urllib.parse

from coordination import board

AGENT = "it-watcher"
FRESH = 7 * 86400
INDEX_NAMES = ("index.php", "index.html", "index.htm", "default.php", "default.html",
               "home.php", "home.html")
PAGE_EXT = (".php", ".html", ".htm")
MOST_READ = 120
_PHP = re.compile(r"<\?(?:php|=)?.*?(?:\?>|\Z)", re.S | re.I)
_SCRIPTISH = re.compile(r"<(script|style|noscript|template)\b.*?</\1\s*>", re.I | re.S)
_TAG = re.compile(r"<[^>]+>")
_INCLUDE = re.compile(r"""(?:include|require)(?:_once)?\s*\(?\s*(?:__DIR__\s*\.\s*)?['"]([^'"]+\.(?:php|html?))['"]""",
                      re.I)
# The agency's own files on his server are not "his files nobody uses".
OURS = ("agency-api/", "sitemap.php")


def _now():
    return int(time.time())


def _table(c):
    c.execute("CREATE TABLE IF NOT EXISTS site_map (id INTEGER PRIMARY KEY CHECK (id = 1),"
              " built_at INTEGER, home TEXT, data TEXT)")


# ---------------------------------------------------------------------------
# matching
# ---------------------------------------------------------------------------

def candidates(url) -> list:
    """The files a web server would try for this address, most likely first."""
    path = urllib.parse.unquote(urllib.parse.urlparse(url).path or "/").lstrip("/")
    if ".." in path:
        return []
    if path == "" or path.endswith("/"):
        return [path + n for n in INDEX_NAMES]
    if path.lower().endswith(PAGE_EXT):
        return [path]
    return [path + ".php", path + ".html", path + ".htm", path + "/index.php", path + "/index.html"]


def _text(html) -> str:
    return re.sub(r"\s+", " ", _TAG.sub(" ", _SCRIPTISH.sub(" ", html or ""))).strip().lower()


def _bits(src) -> list:
    """The words a file writes out as they are: its text outside any PHP code."""
    plain = _PHP.sub("\n", src or "")
    runs = re.split(r"<[^>]+>|\n", _SCRIPTISH.sub(" ", plain))
    out = []
    for r in runs:
        r = re.sub(r"\s+", " ", r).strip().lower()
        if len(r) >= 12 and r not in out:
            out.append(r)
    return out[:200]


def score(src, live_html) -> float:
    """How much of this file's own text is on the live page, 0 to 1."""
    bits = _bits(src)
    if not bits:
        return 0.0
    live = _text(live_html)
    return sum(1 for b in bits if b in live) / len(bits)


def _includes(name, src, names) -> list:
    """The files this one pulls in, as names on the server."""
    out = []
    here = posixpath.dirname(name)
    for inc in _INCLUDE.findall(src or ""):
        inc = inc.replace("\\", "/")
        full = posixpath.normpath(posixpath.join(here, inc.lstrip("/"))) if not inc.startswith("/") \
            else inc.lstrip("/")
        for cand in (full, inc.lstrip("./").lstrip("/")):
            if cand in names and cand not in out:
                out.append(cand)
                break
    return out


def build_from(pages, listing, read, partial=False) -> dict:
    """
    pages: [{url, html}] as a visitor sees them (the home page first).
    listing: the page files on the server. read(names) -> {name: source}.
    """
    names = set(listing)
    want = []
    for p in pages:
        for c in candidates(p["url"]):
            if c in names and c not in want:
                want.append(c)
    src = dict(read(want[:MOST_READ])) if want else {}
    rows, chosen = [], []
    for p in pages:
        cands = [c for c in candidates(p["url"]) if c in names]
        row = {"url": p["url"], "path": urllib.parse.urlparse(p["url"]).path or "/", "file": "", "sure": "none",
               "candidates": cands}
        if cands:
            scored = sorted(((score(src.get(c, ""), p.get("html") or ""), -i, c) for i, c in enumerate(cands)),
                            reverse=True)
            best, _, name = scored[0]
            row["file"] = name
            row["match"] = round(best, 2)
            row["sure"] = "sure" if best >= 0.3 else ("likely" if len(cands) == 1 or best > 0 else "guess")
            if name not in chosen:
                chosen.append(name)
        rows.append(row)
    # The shared parts: what the matched files pull in, and what those pull in.
    inc_of, todo, shared = {}, list(chosen), []
    for _depth in range(3):
        nxt = []
        missing = [n for n in todo if n not in src]
        if missing:
            src.update(read(missing[:MOST_READ]))
        for n in todo:
            for inc in _includes(n, src.get(n, ""), names):
                inc_of.setdefault(n, []).append(inc)
                if inc not in shared and inc not in chosen:
                    shared.append(inc)
                    nxt.append(inc)
        todo = nxt
        if not todo:
            break
    home = rows[0]["file"] if rows else ""
    head = ""
    if home and "</head" in (src.get(home, "") or "").lower():
        head = home
    else:
        for inc in _walk_includes(home, inc_of):
            if "</head" in (src.get(inc, "") or "").lower():
                head = inc
                break
    unmatched = [r for r in rows if not r["file"]]
    exts = {posixpath.splitext(n)[1].lower() for n in chosen}
    live_html = " ".join((p.get("html") or "")[:20000] for p in pages[:3])
    if "/wp-content/" in live_html or "wp-config.php" in names:
        kind = "wordpress"
    elif rows and len(unmatched) > len(rows) / 2:
        kind = "router"
    elif exts <= {".php"} and exts:
        kind = "php"
    elif exts and exts <= {".html", ".htm"}:
        kind = "html"
    elif exts:
        kind = "mixed"
    else:
        kind = "unknown"
    used = set(chosen) | set(shared)
    complete = not partial and bool(rows)
    unused = sorted(n for n in names if n not in used and not n.startswith(OURS)) if complete and kind != "router" else []
    return {"ok": bool(rows), "home": rows[0]["url"] if rows else "", "home_file": home, "head_file": head,
            "kind": kind, "pages": rows, "files": chosen, "shared": shared, "includes": inc_of,
            "unused": unused, "complete": complete, "unmatched": [r["path"] for r in unmatched]}


def _walk_includes(name, inc_of, seen=None):
    seen = seen or set()
    for inc in inc_of.get(name, []):
        if inc in seen:
            continue
        seen.add(inc)
        yield inc
        yield from _walk_includes(inc, inc_of, seen)


# ---------------------------------------------------------------------------
# with the real site
# ---------------------------------------------------------------------------

def _connector_reader(s=None):
    from . import site_api

    def read(names):
        got = site_api.read_pages(list(names), s=s) if names else {}
        return {n: (r.get("html") or "") for n, r in got.items() if isinstance(r, dict) and r.get("ok")}
    return read


def build(pages=None, partial=False, s=None) -> dict:
    """The map for his site. `pages` from the blueprint's own read saves reading the site twice."""
    from . import site_api
    if site_api.why_cannot_read(s):
        return {"ok": False, "error": site_api.why_cannot_read(s)}
    listing = site_api.pages(s=s)
    if not listing.get("ok"):
        return {"ok": False, "error": listing.get("error") or "the list of files could not be read"}
    names = [(x.get("page") if isinstance(x, dict) else x) for x in listing.get("pages") or []]
    if pages is None:
        from . import site_blueprint
        site = site_blueprint.read_site((s or site_api.settings()).get("site") or "")
        if not site.get("ok"):
            return {"ok": False, "error": site.get("error")}
        pages, partial = site["pages"], site.get("partial", False)
    got = build_from(pages, names, _connector_reader(s), partial=partial)
    if got.get("ok"):
        save(got)
    return got


def save(m, now=None):
    m = {k: v for k, v in m.items() if k != "ok"}
    with board._conn() as c:
        _table(c)
        c.execute("INSERT OR REPLACE INTO site_map(id,built_at,home,data) VALUES(1,?,?,?)",
                  (now or _now(), m.get("home") or "", json.dumps(m)))


def stored(max_age=None) -> dict:
    """The last map, or {} when there is none (or it is older than max_age)."""
    try:
        with board._conn() as c:
            _table(c)
            r = c.execute("SELECT built_at, data FROM site_map WHERE id=1").fetchone()
    except sqlite3.Error:
        return {}
    if not r:
        return {}
    if max_age and _now() - int(r[0] or 0) > max_age:
        return {}
    d = json.loads(r[1] or "{}")
    d["built_at"] = int(r[0] or 0)
    return d


def live_files(max_age=FRESH) -> list:
    """Every file that makes a page a visitor sees, plus what they pull in — or [] if unknown."""
    m = stored(max_age)
    return list(dict.fromkeys((m.get("files") or []) + (m.get("shared") or []))) if m else []


def summary() -> dict:
    """For the Website screen: how his site is built, in words."""
    m = stored()
    if not m:
        return {"ok": True, "known": False}
    kinds = {"php": "PHP pages", "html": "plain HTML pages", "mixed": "PHP and HTML pages",
             "wordpress": "WordPress", "router": "one front file that makes every page",
             "unknown": "pages the agency could not match to files"}
    pages = m.get("pages") or []
    return {"ok": True, "known": True, "built_at": m.get("built_at"), "kind": m.get("kind"),
            "kind_words": kinds.get(m.get("kind"), m.get("kind")), "home_file": m.get("home_file"),
            "head_file": m.get("head_file"), "shared": m.get("shared") or [],
            "pages": [{"path": p["path"], "file": p["file"], "sure": p["sure"]} for p in pages[:200]],
            "matched": sum(1 for p in pages if p["file"]), "total": len(pages),
            "unused": m.get("unused") or [], "complete": bool(m.get("complete"))}


def as_text(m) -> str:
    if not m.get("ok", True) and m.get("error"):
        return "Could not map the website: " + str(m["error"])
    L = ["HOW YOUR WEBSITE IS BUILT", "=" * 60,
         f"  Kind:        {m.get('kind')}", f"  Home page:   {m.get('home_file') or '(not matched)'}",
         f"  Page head:   {m.get('head_file') or '(not found)'}",
         f"  Shared:      {', '.join(m.get('shared') or []) or '(none)'}", ""]
    for p in m.get("pages") or []:
        L.append(f"  {p['path']:<40} {p['file'] or '(made by the front file)'}  [{p['sure']}]")
    if m.get("unused"):
        L += ["", "Files no page uses (left alone): " + ", ".join(m["unused"][:30])]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Which file makes each page of the website.")
    ap.add_argument("--stored", action="store_true")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    board.ensure_schema()
    if a.summary:
        r = summary()
    elif a.stored:
        r = stored() or {"ok": True, "known": False}
    else:
        with board.agent_run(AGENT) as run:
            r = build()
            run.note("Site map: " + (f"{r.get('kind')}, home is {r.get('home_file')}" if r.get("ok")
                                     else str(r.get("error"))))
    print(json.dumps(r, indent=2, ensure_ascii=False) if a.json else as_text(r))
    return 0 if r.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
