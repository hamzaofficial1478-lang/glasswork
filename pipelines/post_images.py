"""
post_images.py — the pictures that go in a written post, and where each is from.

    python -m pipelines.post_images "glass railing"

"i ask for blog posts but the program is writing like 150 to 160 words of too
 generic posts with out any images any graphics why?"

Because the writer was told "do not add an image" and nothing else ever did. A
marker was left for his own photographs and no photograph was ever put there.

TWO SOURCES, IN THIS ORDER

  his own     the photographs already on his website, found by what their
              file name and alt text say, matched to the post's subject. They
              are his real work — the one thing a competitor cannot copy — and
              they are already on his server, so the post links them by path.

  stock       only to fill what his own photos do not cover, from the Pexels
              or Pixabay keys he saved (pipelines/stock_photos). Every one is
              captioned "Illustrative photo" with the source, so no customer is
              ever told a stranger's glass is his. The Auditor refuses a stock
              photo that is not labelled that way (coordination/auditor.py).

Nothing here writes anything. It reads his public pages, gently — the same
pace as every other desk (coordination/sitepace.py).
"""
import argparse
import json
import re
import urllib.parse

MOST = 3                      # pictures in one post: enough to break the text up
PAGES_TO_READ = 4             # his pages looked through for photographs
ILLUSTRATIVE = "Illustrative photo"
SKIP_NAMES = ("logo", "icon", "favicon", "sprite", "avatar", "flag", "arrow",
              "quote", "bg", "background", "pattern", "shape", "dot", "banner-shape")


def _words(*phrases) -> set:
    out = set()
    for p in phrases:
        for w in re.findall(r"[a-z]{4,}", str(p or "").lower()):
            if w not in ("with", "your", "from", "that", "this", "dubai", "sharjah",
                         "abu", "dhabi", "uae", "across", "near", "best", "price"):
                out.add(w.rstrip("s"))
    # His site spells it "aluminum"; the briefs say "aluminium". Both.
    if any(w.startswith("alumini") for w in out):
        out.add("aluminum")
    if "aluminum" in out:
        out.add("aluminium")
    return out


def _fetch(url, timeout=15) -> str:
    import requests
    r = requests.get(url, timeout=timeout, headers={
        "User-Agent": "glasswork-agency/1.0 (reading our own site for photographs)"})
    return r.text if r.status_code == 200 else ""


def own_photos(site, subject_words, limit=MOST, fetch=_fetch) -> list:
    """
    Photographs on his own site that are about this subject, best first.
    [{src, alt, own: True}] with src as a path on his own site.
    """
    base = (site or "").rstrip("/")
    if not base:
        return []
    host = urllib.parse.urlsplit(base).hostname or ""
    try:
        home = fetch(base + "/")
    except Exception:
        return []
    pages = [home]
    links = []
    for href in re.findall(r'<a\b[^>]*href=["\']([^"\'#]+)["\']', home or "", re.I):
        u = urllib.parse.urljoin(base + "/", href)
        h = urllib.parse.urlsplit(u).hostname or ""
        if h.replace("www.", "") == host.replace("www.", "") and u not in links \
                and re.search(r"\.(php|html?)$|/$", urllib.parse.urlsplit(u).path or "/"):
            if any(w in u.lower() for w in subject_words) or "project" in u.lower() \
                    or "service" in u.lower() or "gallery" in u.lower():
                links.append(u)
    for u in links[:PAGES_TO_READ - 1]:
        try:
            pages.append(fetch(u))
        except Exception:
            continue

    seen, scored = set(), []
    for html in pages:
        for tag in re.findall(r"<img\b[^>]*>", html or "", re.I):
            src = re.search(r'src=["\']([^"\']+)["\']', tag, re.I)
            if not src:
                continue
            src = src.group(1).strip()
            alt = (re.search(r'alt=["\']([^"\']*)["\']', tag, re.I) or [None, ""])[1]
            path = urllib.parse.urlsplit(urllib.parse.urljoin(base + "/", src))
            if (path.hostname or "").replace("www.", "") != host.replace("www.", ""):
                continue                  # not his
            name = path.path.lower()
            if name in seen or name.endswith((".svg", ".gif", ".ico")):
                continue
            if any(s in name.rsplit("/", 1)[-1] for s in SKIP_NAMES):
                continue
            seen.add(name)
            said = f"{alt} {name}".lower()
            score = sum(1 for w in subject_words if w in said)
            if score:
                scored.append((score, {"src": path.path, "alt": alt.strip(), "own": True}))
    scored.sort(key=lambda x: -x[0])
    return [p for _s, p in scored[:limit]]


def stock_photos(label, subject, limit=MOST) -> list:
    """Labelled illustrations from his stock keys, for what his own photos miss."""
    try:
        from . import stock_photos as stock
    except Exception:
        return []
    out, tried = [], []
    for query in (stock.BETTER_WORDS.get(label.lower().strip(), f"{label} glass aluminium"),
                  f"{label} installation detail", f"{subject} modern interior"):
        if len(out) >= limit or query in tried:
            break
        tried.append(query)
        try:
            got = stock.find(query, width=1000)
        except Exception:
            continue
        if got.get("ok") and got["url"] not in {o["src"] for o in out}:
            out.append({"src": got["url"], "own": False,
                        "alt": f"{ILLUSTRATIVE}: {label}",
                        "credit": f"{ILLUSTRATIVE} — {got.get('where') or 'stock'}"
                                  + (f", {got['by']}" if got.get("by") else "")})
    return out


def for_post(brief, site, limit=MOST, fetch=_fetch) -> list:
    """His own photographs first, labelled stock for the rest. Never raises."""
    label = str(brief.get("service") or brief.get("primary_keyword") or "glass").strip()
    words = _words(label, brief.get("primary_keyword"), brief.get("title"),
                   *(brief.get("secondary_keywords") or [])[:4])
    try:
        mine = own_photos(site, words, limit=limit, fetch=fetch)
    except Exception:
        mine = []
    if len(mine) >= limit:
        return mine
    try:
        extra = stock_photos(label, brief.get("title") or label, limit=limit - len(mine))
    except Exception:
        extra = []
    return mine + extra


def figure(img) -> str:
    """The markup for one picture: his own, or a stock one labelled as such."""
    def esc(s):
        return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
                .replace(">", "&gt;").replace('"', "&quot;"))
    cap = img.get("credit") if not img.get("own") else (img.get("caption") or img.get("alt") or "")
    return (f'<figure><img src="{esc(img["src"])}" alt="{esc(img.get("alt") or "")}" '
            f'loading="lazy" width="1000">'
            + (f"<figcaption>{esc(cap)}</figcaption>" if cap else "") + "</figure>")


def main():
    ap = argparse.ArgumentParser(description="Pictures for a post about this subject.")
    ap.add_argument("subject")
    a = ap.parse_args()
    from coordination import site
    print(json.dumps(for_post({"service": a.subject, "title": a.subject},
                              site.site_url()), indent=2))


if __name__ == "__main__":
    main()
