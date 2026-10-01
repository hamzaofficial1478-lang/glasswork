"""
seo_index.py — which of his pages Google has actually put in its index, and
why the others are not there. Asked of Google itself, page by page.

    python -m pipelines.seo_index            ask Google now (the day runs this)
    python -m pipelines.seo_index --status   what was found last time, no asking

"still many pages and not indexed ... the program did not figure that more
 then 8 pages are still not indexed"

It could not: until Search Console answered, nothing in the agency could see
Google's index, and the audit reads his pages, not Google's opinion of them.
Search Console's URL Inspection — the box he types an address into — answers
for one page at a time, and the agency's account may ask it too. So each day
this asks it about every page in his sitemap, keeps the answer, and turns each
"not indexed" into what it means and what is being done:

  his page names another address     the canonical says the real page is
    as the real one                  somewhere else (his named the copy without
                                     www) — the one-address fix is queued for
                                     his yes
  Google has never heard of it       no sitemap and few links: the sitemap is
                                     queued for his yes, and goes to Search
                                     Console as soon as it is up
  read, and not listed yet           Google's own call, common for new pages;
                                     the sitemap, the one address and links from
                                     his other pages are what move it
  blocked, missing, asked to stay out  said plainly, with the fix

Nothing here writes to his site. What needs a change is queued for his yes,
once — not again every day while he thinks about it — and the Releaser does it.
"""
import argparse
import json
import sqlite3
import sys
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from coordination import board   # noqa: E402

AGENT = "seo-lead"
MOST_A_DAY = 40          # Google allows 2,000 inspections a day; his site is tens of pages
ASK_AGAIN_AFTER = 20 * 3600


def _table(c):
    c.execute("""CREATE TABLE IF NOT EXISTS index_status (
        url TEXT PRIMARY KEY, on_google INTEGER, verdict TEXT, coverage TEXT,
        why TEXT, doing TEXT, google_canonical TEXT, user_canonical TEXT,
        last_crawl TEXT, checked_at INTEGER)""")


def _bare(host):
    host = (host or "").lower()
    return host[4:] if host.startswith("www.") else host


def explain(url, r) -> tuple:
    """(on_google, why, doing) — Google's answer about one page, in his words."""
    cov = (r.get("coverage") or "").strip()
    low = cov.lower()
    uc, gc = r.get("user_canonical") or "", r.get("google_canonical") or ""
    host = urllib.parse.urlparse(url).netloc.lower()
    if r.get("verdict") == "PASS" or low.startswith("submitted and indexed") \
            or low.startswith("indexed"):
        return True, "on Google", ""
    if uc and urllib.parse.urlparse(uc).netloc.lower() != host:
        return (False,
                f"the page tells Google its real address is {uc} — a different address "
                "from the one your site is on, so Google treats this one as a copy",
                "the one-address fix is waiting for your yes under Waiting on you")
    if "canonical" in low or (gc and gc.rstrip("/") != url.rstrip("/")):
        return (False,
                "Google thinks this page is a copy of " + (gc or "another page")
                + (f" ({cov})" if cov else ""),
                "the one-address fix is waiting for your yes; if the content really "
                "repeats another page, Content can make it its own")
    if "noindex" in low:
        return False, "the page asks Google not to list it (noindex)", \
            "IT will take the noindex off if you say the page should be on Google"
    if "robots" in low or (r.get("robots") or "").upper() == "DISALLOWED":
        return False, "robots.txt stops Google reading this page", \
            "IT can lift that if the page should be on Google"
    if "404" in low or "not found" in low:
        return False, "Google found nothing at this address (404)", \
            "the link to it should be fixed or the page put back"
    if "redirect" in low:
        return False, "this address sends visitors on to another one", \
            "nothing — the page it goes to is the one that counts"
    if "discovered" in low:
        return (False, "Google knows the address but has not read it yet",
                "the sitemap tells Google it matters; give it a few days after that")
    if "crawled" in low:
        return (False, "Google read the page and has not listed it yet — its own call, "
                       "common for new or short pages and for a site with two addresses",
                "the one-address fix and the sitemap come first; then links to it from "
                "your other pages and more of your own words on it")
    if "unknown" in low or not cov:
        return (False, "Google has never heard of this page",
                "the sitemap will tell Google about it as soon as it is up")
    return False, cov, ""


def _pages():
    """The pages to ask about: the sitemap if it is up, else a gentle walk of the site."""
    from . import sitemap, site_api
    got = site_api.read_page(sitemap.PAGE)
    if got.get("ok") and "<urlset" in (got.get("html") or ""):
        import re
        import html as _h
        return [_h.unescape(u) for u in re.findall(r"<loc>([^<]+)</loc>", got["html"])], True
    d = sitemap.discover()
    return (d.get("pages") or []), False


def _asked_for(kind, days=14) -> bool:
    """Already put to him (or done) recently — do not ask again every day."""
    since = int(time.time()) - days * 86400
    try:
        with sqlite3.connect(board.DB, timeout=30) as c:
            return bool(c.execute(
                "SELECT 1 FROM site_queue WHERE kind=? AND (status IN ('pending','approved')"
                " OR COALESCE(decided_at, queued_at, 0)>=?) LIMIT 1", (kind, since)).fetchone())
    except sqlite3.Error:
        return False


def _queue(kind, title, why):
    if _asked_for(kind):
        return None
    return board.queue_site_change(AGENT, kind, {"change": {"call": kind},
                                                 "title": title, "why": why})


def run(agent=AGENT, most=MOST_A_DAY) -> dict:
    with board.agent_run(agent) as row:
        out = _run(most)
        row.note(out.get("head") or out.get("error") or "asked Google")
        return out


def _run(most):
    from . import seo_intel, site_api, sitemap
    creds, prop, why = seo_intel._gsc_session()
    if not creds:
        return {"ok": False, "idle": True,
                "error": "Search Console is not answering, so Google's index cannot be "
                         "read: " + (why or "not set up")}
    urls, from_sitemap = _pages()
    if not urls:
        return {"ok": False, "error": "no page of the site could be read to ask about"}
    site_host = urllib.parse.urlparse(urls[0]).netloc.lower()
    now = int(time.time())
    with sqlite3.connect(board.DB, timeout=30) as c:
        _table(c)
        fresh = {r[0] for r in c.execute("SELECT url FROM index_status WHERE checked_at>=?",
                                         (now - ASK_AGAIN_AFTER,))}
    asked, rows, errors = 0, [], []
    for url in urls:
        if url in fresh or asked >= most:
            continue
        r = seo_intel.gsc_inspect(url, creds=creds, prop=prop)
        asked += 1
        if not r.get("ok"):
            errors.append(r.get("why"))
            if r.get("status") in (401, 403, 429):
                break
            continue
        on, why_not, doing = explain(url, r)
        rows.append((url, int(on), r.get("verdict"), r.get("coverage"), why_not, doing,
                     r.get("google_canonical"), r.get("user_canonical"),
                     r.get("last_crawl"), now))
        time.sleep(0.3)
    with sqlite3.connect(board.DB, timeout=30) as c:
        _table(c)
        c.executemany("INSERT INTO index_status(url,on_google,verdict,coverage,why,doing,"
                      "google_canonical,user_canonical,last_crawl,checked_at)"
                      " VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET"
                      " on_google=excluded.on_google, verdict=excluded.verdict,"
                      " coverage=excluded.coverage, why=excluded.why, doing=excluded.doing,"
                      " google_canonical=excluded.google_canonical,"
                      " user_canonical=excluded.user_canonical,"
                      " last_crawl=excluded.last_crawl, checked_at=excluded.checked_at",
                      rows)

    # WHAT IT NEEDS DOING, PUT TO HIM ONCE.
    st = status()
    queued = []
    split = [p for p in st["pages"] if p.get("user_canonical")
             and urllib.parse.urlparse(p["user_canonical"]).netloc.lower() != site_host]
    if split or any("copy" in (p.get("why") or "") for p in st["pages"]):
        cid = _queue("use_one_domain",
                     f"Make every page name its real address: {site_host}",
                     f"{len(split) or 'Some'} of your pages tell Google their real "
                     f"address is the copy of your site without www (e.g. "
                     f"{(split[0]['user_canonical'] if split else '')}). Google treats the "
                     f"pages on {site_host} — the ones in your Search Console — as copies, "
                     "which is why they are not indexed. This changes every canonical tag "
                     f"and link to {site_host}.")
        if cid:
            queued.append(cid)
    if not from_sitemap and not sitemap.is_published():
        cid = _queue("publish_sitemap", "Put a sitemap on your website and give it to Google",
                     f"Your website has no sitemap, so Google finds pages only by "
                     f"following links. This puts {sitemap.live_url()} on your site listing "
                     f"your {len(urls)} pages and hands it to Search Console.")
        if cid:
            queued.append(cid)
    try:
        sb = site_api.plan_share_buttons()
        if sb.get("ok") and sb.get("times"):
            cid = _queue("repair_share_buttons",
                         "Put Facebook, WhatsApp and LinkedIn back in your share buttons",
                         f"The share buttons on your posts ({sb['times']} of them) open a "
                         "page on your own site that does not exist, instead of Facebook, "
                         "WhatsApp and LinkedIn.")
            if cid:
                queued.append(cid)
    except Exception:
        pass

    on, total = st["on_google"], len(st["pages"])
    head = (f"{on} of {total} page(s) are on Google"
            + (f"; {total - on} are not — reasons and fixes in the office under On Google"
               if total - on else "")
            + (f"; {len(queued)} fix(es) waiting for your yes" if queued else ""))
    if errors and not rows:
        return {"ok": False, "error": "Google would not answer about your pages: " + errors[0]}
    return {"ok": True, "asked": asked, "on_google": on, "pages": total,
            "queued": queued, "head": head, "errors": errors[:3]}


def status() -> dict:
    """What Google said last time, for the office. Reads only."""
    board.ensure_schema()
    try:
        with sqlite3.connect(board.DB, timeout=30) as c:
            c.row_factory = sqlite3.Row
            _table(c)
            pages = [dict(r) for r in c.execute(
                "SELECT * FROM index_status ORDER BY on_google, url")]
    except sqlite3.Error as e:
        return {"ok": False, "error": str(e), "pages": [], "on_google": 0}
    on = sum(1 for p in pages if p["on_google"])
    when = max([p["checked_at"] or 0 for p in pages] or [0])
    return {"ok": True, "pages": pages, "on_google": on,
            "checked_at": when,
            "head": (f"{on} of {len(pages)} page(s) on Google" if pages else
                     "not asked yet — it runs in the day once Search Console is working")}


def main():
    ap = argparse.ArgumentParser(description="Which of his pages Google has indexed.")
    ap.add_argument("--status", action="store_true", help="what was found last time")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    r = status() if a.status else run()
    if a.json or a.status:
        print(json.dumps(r, default=str))
    else:
        print(r.get("head") or r.get("error"))
    return 0 if r.get("ok") or r.get("idle") else 1


if __name__ == "__main__":
    sys.exit(main())
