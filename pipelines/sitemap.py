"""
sitemap.py — the list of his pages Google should know about, on his website.

    python -m pipelines.sitemap --plan        which pages would go in (reads his site)
    python -m pipelines.sitemap --json        the same, as JSON

"i think i have no sitemap as in search console it shows no any sitemap ...
 how can i add sitemaps to website?"

He had none: /sitemap.xml and /robots.txt both answered 404, so Google found
his pages only by following links, and Search Console had nothing to read.

WHY sitemap.php AND NOT sitemap.xml

The connector on his site writes .php and .html pages and nothing else — on
purpose, so a stolen token can never drop an arbitrary file on his server.
Google does not care what a sitemap is called: Search Console takes any address
on the site. So the sitemap is a PHP page that sends the XML, with the
content type Google expects, through the same door as every other change.

WHAT GOES IN

The pages a visitor can reach by following his own links from the home page,
read gently (sitepace keeps a second between requests and stops the moment his
firewall objects), each at the ONE address his site is on:

  only his own site            other domains, admin, the connector, assets — out
  the address he saved         a link to the copy without www is counted as the
                               www page, because that is the page to be indexed
  only pages that are the      a page whose canonical names another page (his
    real thing                 gallery filters all point at gallery.php) is a
                               view of that page, not a page of its own
  nothing asking to be left    a noindex page is not offered to Google
    out
  index.php is the home page   one page, one address

Rebuilt whenever a post goes up or comes down (it_release), so a new post is
in the list the same day.
"""
import argparse
import html as _html
import json
import re
import sys
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PAGE = "sitemap.php"
MOST_PAGES = 150          # a glazier's site is tens of pages; this is a ceiling, not a goal
UA = "Mozilla/5.0 (compatible; glasswork-sitemap/1.0)"

SKIP_EXT = (".css", ".js", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".ico",
            ".pdf", ".zip", ".mp4", ".mp3", ".woff", ".woff2", ".ttf", ".eot",
            ".xml", ".txt", ".json", ".bak")
SKIP_DIRS = ("admin", "agency-api", "agency-pictures", "includes", "inc", "assets",
             "config", "vendor", "backup", "backups", "cgi-bin", "wp-admin")
SKIP_PARAMS = ("utm_", "fbclid", "gclid", "ref", "sort", "page_id")


def site_address() -> str:
    """'https://www.worlddealtechnicalservices.com' — the address he saved."""
    try:
        from coordination import site
        url = (site.status().get("url") or "").strip()
    except Exception:
        url = ""
    p = urllib.parse.urlparse(url if "://" in url else "https://" + url) if url else None
    return f"{p.scheme or 'https'}://{p.netloc.lower()}" if p and p.netloc else ""


def _bare(host):
    host = (host or "").lower()
    return host[4:] if host.startswith("www.") else host


def normalise(href, base, home) -> str:
    """One of his pages at his one address, or '' if it is not one of his pages."""
    try:
        p = urllib.parse.urlparse(urllib.parse.urljoin(base, (href or "").strip()))
    except Exception:
        return ""
    if p.scheme not in ("http", "https"):
        return ""
    hp = urllib.parse.urlparse(home)
    if _bare(p.netloc) != _bare(hp.netloc):
        return ""
    path = re.sub(r"/{2,}", "/", p.path or "/")
    low = path.lower()
    if any(low.endswith(e) for e in SKIP_EXT):
        return ""
    first = low.strip("/").split("/")[0] if low.strip("/") else ""
    if first in SKIP_DIRS or "/." in low:
        return ""
    if low.endswith("/index.php") or low.endswith("/index.html"):
        path = path[: path.rfind("/") + 1]
    q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=False)
         if not any(k.lower().startswith(s) for s in SKIP_PARAMS)]
    # A share button carries a whole address in its query (…?u=https://…) —
    # it is a door to Facebook or WhatsApp, not a page of his.
    if any("://" in v or v.lower().startswith("http") for _k, v in q) or \
            re.search(r"/(sharer|sharing|share|intent)/", low):
        return ""
    query = urllib.parse.urlencode(q)
    return f"{hp.scheme}://{hp.netloc}{path or '/'}" + (f"?{query}" if query else "")


def _canonical(page_html, url, home) -> str:
    m = re.search(r"<link\b[^>]*\brel=[\"']?canonical[\"']?[^>]*>", page_html or "", re.I)
    if not m:
        return ""
    h = re.search(r"\bhref=[\"']([^\"']+)[\"']", m.group(0), re.I)
    return normalise(h.group(1), url, home) if h else ""


def _noindex(page_html) -> bool:
    for m in re.finditer(r"<meta\b[^>]*>", page_html or "", re.I):
        tag = m.group(0).lower()
        if re.search(r"name=[\"']?(robots|googlebot)", tag) and "noindex" in tag:
            return True
    return False


def discover(home=None, most=MOST_PAGES, get=None) -> dict:
    """
    Walk his site from the home page. Returns {ok, pages, views, left_out, why}.

      pages      the addresses that go in the sitemap
      views      address -> the page its canonical names instead
      left_out   address -> why it is not in (noindex, not found, ...)
    """
    home = (home or site_address()).rstrip("/")
    if not home:
        return {"ok": False, "error": "no website address is saved on Setup"}
    home_url = home + "/"
    if get is None:
        import requests
        s = requests.Session()

        def get(u):
            r = s.get(u, timeout=20, headers={"User-Agent": UA}, allow_redirects=True)
            return r.status_code, r.text if "html" in (r.headers.get("Content-Type") or "") else ""
    todo, seen, pages, views, left = [home_url], {home_url}, [], {}, {}
    stopped = ""
    while todo and len(seen) <= most:
        url = todo.pop(0)
        try:
            status, body = get(url)
        except Exception as e:
            why = f"{type(e).__name__}: {e}"
            if "resting" in why.lower() or "stopped answering" in why.lower() \
                    or "connection" in why.lower():
                stopped = ("your website asked the agency to slow down part-way, so the "
                           "list holds what was read before that")
                break
            left[url] = f"could not be read ({type(e).__name__})"
            continue
        if status != 200:
            left[url] = f"answered {status}"
            continue
        canon = _canonical(body, url, home_url)
        if canon and canon != url:
            views[url] = canon
            if canon not in seen and len(seen) < most:
                seen.add(canon)
                todo.append(canon)
        elif _noindex(body):
            left[url] = "asks Google not to list it (noindex)"
        else:
            pages.append(url)
        for href in re.findall(r"<a\b[^>]*\bhref=[\"']([^\"'#]+)", body or "", re.I):
            u = normalise(href, url, home_url)
            if u and u not in seen and len(seen) < most:
                seen.add(u)
                todo.append(u)
    pages = sorted(set(pages), key=lambda u: (u.count("/"), len(u), u))
    if home_url in pages:
        pages.remove(home_url)
        pages.insert(0, home_url)
    return {"ok": bool(pages), "home": home_url, "pages": pages, "views": views,
            "left_out": left, "stopped": stopped,
            "error": "" if pages else "no page of the site could be read"}


def today() -> str:
    return time.strftime("%Y-%m-%d")


def as_xml(urls, dates=None) -> str:
    """
    The sitemap. `dates` maps a page to the day it last really changed, sent as
    <lastmod> — the one thing a sitemap can say that makes Google come back to
    a page sooner. Only a date that is TRUE: Google stops trusting the dates of
    a site that stamps every page with today on every rebuild.
    """
    dates = dates or {}

    def row(u):
        d = dates.get(u)
        return (f"  <url><loc>{_html.escape(u, quote=True)}</loc>"
                + (f"<lastmod>{d}</lastmod>" if d else "") + "</url>")
    rows = "\n".join(row(u) for u in urls)
    return ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            + rows + "\n</urlset>\n")


def dates_in(xml) -> dict:
    """The <lastmod> of each page in a sitemap already up, so a rebuild keeps them."""
    out = {}
    for block in re.findall(r"<url>(.*?)</url>", xml or "", re.S):
        loc = re.search(r"<loc>([^<]+)</loc>", block)
        mod = re.search(r"<lastmod>([^<]+)</lastmod>", block)
        if loc and mod:
            out[_html.unescape(loc.group(1).strip())] = mod.group(1).strip()
    return out


def as_php(urls, dates=None) -> str:
    """
    The page that sends the sitemap. Written as a nowdoc, never as XML inline:
    with short_open_tag on, PHP reads '<?xml' as the start of PHP code and the
    page dies with a parse error — Google would get a blank page.
    """
    xml = as_xml(urls, dates)
    if "\nXML;" in xml:                                   # pragma: no cover — cannot occur
        raise ValueError("the sitemap cannot contain its own end marker")
    return ("<?php\n"
            "// Made by the agency: every page of this website Google should know about.\n"
            "// Rebuilt by the agency whenever a post goes up or comes down.\n"
            "header('Content-Type: application/xml; charset=utf-8');\n"
            "header('X-Robots-Tag: noindex');\n"
            "echo <<<'XML'\n" + xml + "XML;\n")


def live_url(home=None) -> str:
    return (home or site_address()).rstrip("/") + "/" + PAGE


def is_published(home=None, get=None) -> bool:
    """Is the sitemap on his site now?"""
    url = live_url(home)
    try:
        if get is None:
            import requests
            r = requests.get(url, timeout=20, headers={"User-Agent": UA})
            return r.status_code == 200 and "<urlset" in (r.text or "")
        status, body = get(url)
        return status == 200 and "<urlset" in (body or "")
    except Exception:
        return False


def keep_up(agent, add=(), drop=(), touched=()) -> dict:
    """
    After a post goes up or comes down: the same sitemap with that address in or
    out. Reads the sitemap the agency put there; does nothing if there is none
    yet (publishing the first one is his decision).

    Each page keeps the date it last changed. A page added, or one in
    `touched` (it changed today), is dated today, so Google knows which pages
    are worth reading again.
    """
    from . import site_api
    got = site_api.read_page(PAGE)
    if not got.get("ok") or "<urlset" not in (got.get("html") or ""):
        return {"ok": False, "skipped": True, "why": "no sitemap on the site yet"}
    home = site_address().rstrip("/") + "/"
    have = [_html.unescape(u) for u in re.findall(r"<loc>([^<]+)</loc>", got["html"])]
    had = dates_in(got["html"])
    dates = dict(had)
    gone = {normalise(u, home, home) for u in drop if u}
    urls = [u for u in have if u not in gone]
    for u in add:
        n = normalise(u, home, home) if u else ""
        if n and n not in urls:
            urls.append(n)
        if n:
            dates[n] = today()
    for u in touched:
        n = normalise(u, home, home) if u else ""
        if n in urls:
            dates[n] = today()
    dates = {u: d for u, d in dates.items() if u in urls}
    if urls == have and dates == had:
        return {"ok": True, "unchanged": True, "pages": len(urls)}
    r = site_api.put_page(PAGE, as_php(urls, dates), agent=agent, before=got["html"],
                          _action="replace",
                          summary=("added the new post to your sitemap" if add and not drop
                                   else "brought your sitemap up to date"))
    return {"ok": bool(r.get("ok")), "pages": len(urls), "error": r.get("error"),
            "dated_today": sorted(u for u in urls if dates.get(u) == today()
                                  and had.get(u) != today())}


def main():
    ap = argparse.ArgumentParser(description="The sitemap of his website.")
    ap.add_argument("--plan", action="store_true", help="which pages would go in")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    d = discover()
    if a.json:
        print(json.dumps(d, indent=2))
        return 0
    if not d.get("ok"):
        print(d.get("error"))
        return 1
    print(f"{len(d['pages'])} page(s) would go in {live_url()}:")
    for u in d["pages"]:
        print("  " + u)
    for u, c in sorted(d["views"].items()):
        print(f"  (left out) {u} — a view of {c}")
    for u, why in sorted(d["left_out"].items()):
        print(f"  (left out) {u} — {why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
