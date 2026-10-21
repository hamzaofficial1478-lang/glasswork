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
        last_crawl TEXT, checked_at INTEGER, live_canonical TEXT)""")
    try:
        c.execute("ALTER TABLE index_status ADD COLUMN live_canonical TEXT")
    except sqlite3.Error:
        pass                                  # already there


# His server's firewall resets visits that come too close together, and a
# page that did not answer used to read exactly like a page naming no address —
# so about.php, which names the www address, was shown as "still names the
# other address". A read now waits its turn, is tried twice, and an unread page
# is said to be unread.
LIVE_PAUSE = 1.5
LIVE_RETRY_PAUSE = 4
UA = {"User-Agent": "Mozilla/5.0 (glasswork-index)"}


def live_canonical(url, get=None):
    """
    The canonical this page names on his site right now: the address, '' when
    the page was read and names none, or None when it could not be read.
    """
    import re
    import os
    try:
        if get is None:
            if os.environ.get("AGENCY_IGNORE_DOTENV") == "1":
                return None                   # the test suite never reaches a real site
            import requests
            time.sleep(LIVE_PAUSE)
            r = requests.get(url, timeout=20, headers=UA)
            status, html = r.status_code, r.text or ""
        else:
            status, html = get(url)
    except Exception:
        return None
    if status != 200:
        return None
    m = re.search(r"<link\b[^>]*\brel=[\"']?canonical[\"']?[^>]*>", html, re.I)
    h = re.search(r"\bhref=[\"']([^\"']+)[\"']", m.group(0), re.I) if m else None
    return urllib.parse.urljoin(url, h.group(1).strip()) if h else ""


def _live_now(url):
    """live_canonical, asked a second time after a pause if the page did not answer."""
    got = live_canonical(url)
    if got is None:
        time.sleep(LIVE_RETRY_PAUSE)
        got = live_canonical(url)
    return got


def twin_moves(url, get=None):
    """
    Whether the site's other address — the same host with or without www —
    sends visitors on to this one. True, False (it opens a page of its own),
    or None when that could not be told.

    A page can name the right address and still be listed as a copy while
    the other address opens the same page by itself: Google sees two copies
    and picks one. Only a redirect on the other address settles that, and
    that is a hosting setting, not a file the agency can write.
    """
    import os
    p = urllib.parse.urlparse(url)
    host = p.netloc.lower()
    twin = host[4:] if host.startswith("www.") else "www." + host
    probe = urllib.parse.urlunparse((p.scheme or "https", twin, "/", "", "", ""))
    try:
        if get is None:
            if os.environ.get("AGENCY_IGNORE_DOTENV") == "1":
                return None
            import requests
            time.sleep(LIVE_PAUSE)
            r = requests.get(probe, timeout=20, headers=UA, allow_redirects=False)
            status, where = r.status_code, r.headers.get("Location", "")
        else:
            status, where = get(probe)
    except Exception:
        return None
    if status in (301, 302, 303, 307, 308):
        return urllib.parse.urlparse(urllib.parse.urljoin(probe, where or "")).netloc.lower() == host
    if status == 200:
        return False
    return None


def fix_state(kind="use_one_domain") -> str:
    """The state of the most recent change of this kind: pending / approved / applied / failed / ''."""
    try:
        with sqlite3.connect(board.DB, timeout=30) as c:
            r = c.execute("SELECT status FROM site_queue WHERE kind=? OR detail LIKE ?"
                          " ORDER BY id DESC LIMIT 1",
                          (kind, f'%"{kind}"%')).fetchone()
        return (r[0] if r else "") or ""
    except sqlite3.Error:
        return ""


def _bare(host):
    host = (host or "").lower()
    return host[4:] if host.startswith("www.") else host


# What is being done about a page naming the wrong address, by the REAL state
# of the one-address change — never a fixed sentence. "it asks waiting for yes
# in waiting room while in waiting room there was nothing": the old line said
# "waiting for your yes" whatever the change's state, including long after it
# had gone on.
FIX_STATE = {
    "pending": "the one-address fix is waiting for your yes under Waiting on you",
    "approved": "the one-address fix is approved — IT is putting it on",
    "applied": "IT has changed your files to name one address",
    "failed": "the one-address fix could not go on — the reason is in Your orders",
}
SOON = "IT has been asked to make every page name one address"
ASK_AGAIN = ("nothing to change on your site — Google re-reads it by itself, and the "
             "agency marks it as changed in your sitemap so Google comes sooner; to "
             "hurry it further, open URL Inspection in Search Console for this page and "
             "press Request indexing (only you can press that — Google gives programs "
             "no way to)")
UNREAD = "the agency reads the page again on the next run and says which"


def redirect_advice(url) -> str:
    """What settles two addresses opening the same page: his host's redirect."""
    host = urllib.parse.urlparse(url).netloc.lower()
    twin = host[4:] if host.startswith("www.") else "www." + host
    return (f"in Hostinger, make {twin} send visitors on to {host} with a permanent "
            "(301) redirect — in hPanel open your website and search for Redirects, "
            "or ask Hostinger's chat to set it — then press Request indexing for this "
            "page in Search Console")


def _doing(fix, live, host):
    """What is being done, by the real state of the change AND the page today."""
    if fix == "applied" and live and urllib.parse.urlparse(live).netloc.lower() != host:
        return ("IT changed your files, yet this page still names " + live
                + " — see Your orders for which file")
    return FIX_STATE.get(fix or "", SOON)


def _when(last_crawl):
    return f" (on {last_crawl[:10]})" if last_crawl else ""


def explain(url, r, live=None, fix=None, moved=None) -> tuple:
    """
    (on_google, why, doing) — Google's answer about one page, in his words.

    `live` is the canonical the page names TODAY, read off his site: the
    address, '' when the page names none, None when the page did not answer.
    `fix` is the state of the one-address change; `moved` whether the other
    address of the site (with or without www) sends visitors to this one.

    Google's answer is about the page as it was when Google last read it —
    weeks ago, sometimes — so a canonical it reports is checked against the
    page now: "the page tells Google its real address is
    https://worlddealtechnicalservices.com/" was shown as today's fault while
    every page had already been put right. And a page that could not be read
    is never reported as still wrong: about.php said "this page still names
    the other address" when it names the www one and simply had not answered.
    """
    cov = (r.get("coverage") or "").strip()
    low = cov.lower()
    uc, gc = r.get("user_canonical") or "", r.get("google_canonical") or ""
    host = urllib.parse.urlparse(url).netloc.lower()
    seen = _when(r.get("last_crawl") or "")
    live_ok = bool(live) and urllib.parse.urlparse(live).netloc.lower() == host
    gc_host = urllib.parse.urlparse(gc).netloc.lower() if gc else ""
    twin_chosen = bool(gc_host) and gc_host != host and _bare(gc_host) == _bare(host)
    open_twin = "" if moved is not False else (
        "; the address without www still opens by itself, so " + redirect_advice(url)
        if host.startswith("www.") else "; " + redirect_advice(url))
    if r.get("verdict") == "PASS" or low.startswith("submitted and indexed") \
            or low.startswith("indexed"):
        return True, "on Google", ""
    if uc and urllib.parse.urlparse(uc).netloc.lower() != host:
        if live_ok:
            return (False,
                    f"when Google last read this page{seen} it named {uc} as the real "
                    f"address. Your page names {live} now, so this clears when Google "
                    "reads it again", ASK_AGAIN + open_twin)
        if live is None:
            return (False,
                    f"when Google last read this page{seen} it named {uc} as the real "
                    "address. Your page did not answer just now, so whether it still "
                    "does could not be checked",
                    (FIX_STATE["applied"] + "; " if fix == "applied" else "") + UNREAD)
        if live == "":
            return (False,
                    f"when Google last read this page{seen} it named {uc} as the real "
                    "address. Your page names no address of its own now, so Google "
                    "chooses one itself", _doing(fix, live, host))
        return (False,
                f"the page tells Google its real address is {live} — a different "
                "address from the one your site is on, so Google treats this one as a copy",
                _doing(fix, live, host))
    if "canonical" in low or (gc and gc.rstrip("/") != url.rstrip("/")):
        if live_ok and twin_chosen and moved is False:
            return (False,
                    f"Google picked {gc} — the same page at your site's other address — "
                    f"as the real one. Your page names {live}, but the other address "
                    "still opens the same page by itself, so Google sees two copies",
                    redirect_advice(url))
        if live_ok:
            return (False,
                    f"when Google last read this page{seen} it treated it as a copy of "
                    f"{gc or 'another page'}. Your page names itself as the real one now, "
                    "so this clears when Google reads it again", ASK_AGAIN + open_twin)
        if live is None:
            return (False,
                    f"when Google last read this page{seen} it treated it as a copy of "
                    f"{gc or 'another page'}. Your page did not answer just now, so what "
                    "it names today could not be checked",
                    (FIX_STATE["applied"] + "; " if fix == "applied" else "") + UNREAD)
        return (False,
                "Google thinks this page is a copy of " + (gc or "another page")
                + (f" ({cov})" if cov else ""),
                _doing(fix, live, host)
                + "; if the content really repeats another page, Content can make it its own")
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


def _in_property(url, prop) -> str:
    """
    The same page at the address Search Console knows the site by.

    "Search Console said 403: You do not own this site, or the inspected URL is
    not part of this property." His website address on Setup was saved without
    www and his Search Console property is the www one, so every page was asked
    about at an address the property does not cover. A URL-prefix property
    answers only for its own scheme and host; a domain property for any.
    """
    if not prop or prop.startswith("sc-domain:"):
        return url
    pp, pu = urllib.parse.urlparse(prop), urllib.parse.urlparse(url)
    return urllib.parse.urlunparse((pp.scheme, pp.netloc, pu.path or "/", "", pu.query, ""))


def address_mismatch(prop) -> str:
    """
    The two addresses he has given the agency for one site, when they differ —
    Setup's website address and his Search Console property — or ''.

    One of them has to be the site's one address. Fixing canonicals, links and
    the sitemap towards Setup's while Search Console holds the other would only
    move the problem, so nothing is changed until he makes them agree.
    """
    if not prop or prop.startswith("sc-domain:"):
        return ""
    from . import sitemap
    mine = sitemap.site_address()
    if not mine:
        return ""
    a = urllib.parse.urlparse(mine).netloc.lower()
    b = urllib.parse.urlparse(prop).netloc.lower()
    if a == b:
        return ""
    return (f"Your website address on Setup is {mine} but your Search Console property is "
            f"{prop.rstrip('/')}. They must be the same address. Open Setup → Website and "
            f"save {prop.rstrip('/')} (your pages' own links and Search Console already "
            "use it); the agency then makes every page and the sitemap name it.")


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


def changed_since_google() -> list:
    """
    The pages not on Google whose page today is not what Google last read:
    it names a different address now, or Google picked the copy at the
    site's other address. Those are the pages a <lastmod> of today is TRUE
    for — and the only ones worth telling Google about.
    """
    try:
        with sqlite3.connect(board.DB, timeout=30) as c:
            c.row_factory = sqlite3.Row
            _table(c)
            rows = [dict(r) for r in c.execute(
                "SELECT url, user_canonical, google_canonical, live_canonical"
                " FROM index_status WHERE on_google=0 AND COALESCE(live_canonical,'')<>''")]
    except sqlite3.Error:
        return []
    out = []
    for r in rows:
        live = urllib.parse.urlparse(r["live_canonical"]).netloc.lower()
        if urllib.parse.urlparse(r["url"]).netloc.lower() != live:
            continue                          # still wrong today: fix it first, then tell
        before = {urllib.parse.urlparse(x).netloc.lower()
                  for x in (r["user_canonical"], r["google_canonical"]) if x}
        if before - {live}:
            out.append(r["url"])
    return out


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


def run(agent=AGENT, most=MOST_A_DAY, force=False) -> dict:
    with board.agent_run(agent) as row:
        out = _run(most, force=force)
        row.note(out.get("head") or out.get("error") or "asked Google")
        return out


def _run(most, force=False):
    from . import seo_intel, site_api, sitemap
    creds, prop, why = seo_intel._gsc_session()
    if not creds:
        return {"ok": False, "idle": True,
                "error": "Search Console is not answering, so Google's index cannot be "
                         "read: " + (why or "not set up")}
    urls, from_sitemap = _pages()
    if not urls:
        return {"ok": False, "error": "no page of the site could be read to ask about"}
    seen_urls = set()
    urls = [u for u in (_in_property(u, prop) for u in urls)
            if not (u in seen_urls or seen_urls.add(u))]
    mismatch = address_mismatch(prop)
    site_host = urllib.parse.urlparse(urls[0]).netloc.lower()
    now = int(time.time())
    with sqlite3.connect(board.DB, timeout=30) as c:
        _table(c)
        # Fresh means asked today AND read against the live page — a row from
        # before the live check is asked again, or its old words would stand.
        fresh = set() if force else {r[0] for r in c.execute(
            "SELECT url FROM index_status WHERE checked_at>=? AND"
            " (on_google=1 OR live_canonical IS NOT NULL)", (now - ASK_AGAIN_AFTER,))}
    asked, rows, errors = 0, [], []
    moved = "ask"                      # the other address: asked once, when needed
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
        live = None if r.get("verdict") == "PASS" else _live_now(url)
        if moved == "ask" and r.get("verdict") != "PASS":
            moved = twin_moves(url)
        on, why_not, doing = explain(url, r, live=live, fix=fix_state(),
                                     moved=None if moved == "ask" else moved)
        rows.append((url, int(on), r.get("verdict"), r.get("coverage"), why_not, doing,
                     r.get("google_canonical"), r.get("user_canonical"),
                     r.get("last_crawl"), now, live))
        time.sleep(0.3)
    with sqlite3.connect(board.DB, timeout=30) as c:
        _table(c)
        c.executemany("INSERT INTO index_status(url,on_google,verdict,coverage,why,doing,"
                      "google_canonical,user_canonical,last_crawl,checked_at,live_canonical)"
                      " VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(url) DO UPDATE SET"
                      " on_google=excluded.on_google, verdict=excluded.verdict,"
                      " coverage=excluded.coverage, why=excluded.why, doing=excluded.doing,"
                      " google_canonical=excluded.google_canonical,"
                      " user_canonical=excluded.user_canonical,"
                      " last_crawl=excluded.last_crawl, checked_at=excluded.checked_at,"
                      " live_canonical=excluded.live_canonical",
                      rows)

    # WHAT IT NEEDS DOING, PUT TO HIM ONCE.
    st = status()
    queued = []
    # On what each page names TODAY. Google's last reading can be weeks old;
    # asking him to approve a fix that has already gone on is exactly the
    # "waiting for your yes" with nothing waiting that he caught.
    split = [p for p in st["pages"] if p.get("live_canonical")
             and urllib.parse.urlparse(p["live_canonical"]).netloc.lower() != site_host]
    if mismatch:
        # Two addresses for one site in his own settings: say so, once, and
        # change nothing on the site until they agree.
        try:
            with sqlite3.connect(board.DB, timeout=30) as c:
                have = c.execute("SELECT 1 FROM tasks WHERE about='address:owner'"
                                 " AND status IN ('open','in_progress')").fetchone()
            if not have:
                board.try_post_task("SEO", "Make your website address on Setup match "
                                    "Search Console", mismatch, risk="amber",
                                    created_by=AGENT, about="address:owner")
        except Exception:
            pass
    elif split:
        cid = _queue("use_one_domain",
                     f"Make every page name its real address: {site_host}",
                     f"{len(split)} of your pages tell Google their real "
                     f"address is the copy of your site without www (e.g. "
                     f"{split[0]['live_canonical']}). Google treats the "
                     f"pages on {site_host} — the ones in your Search Console — as copies, "
                     "which is why they are not indexed. This changes every canonical tag "
                     f"and link to {site_host}.")
        if cid:
            queued.append(cid)
    # THE PART OF "REQUEST INDEXING" A PROGRAM CAN DO. The button has no API;
    # a sitemap dating the changed pages, given to Search Console again, is
    # the nearest thing. Once the sitemap is up, and once per fortnight.
    changed = changed_since_google() if not mismatch else []
    if changed and (from_sitemap or sitemap.is_published()):
        cid = _queue("tell_google_again",
                     f"Tell Google {len(changed)} of your pages have changed",
                     f"{len(changed)} page(s) Google has not listed are different now from "
                     f"when Google last read them (they name {site_host} now"
                     + (", and your site's other address moves to it" if moved is True
                        else "") + "). This dates them as changed "
                     "today in your sitemap and gives it to Search Console again, so "
                     "Google reads them sooner. Nothing a visitor sees changes.")
        if cid:
            queued.append(cid)
    if not mismatch and not from_sitemap and not sitemap.is_published():
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
            + (f"; {len(queued)} fix(es) waiting for your yes" if queued else "")
            + ("; your site's other address (with or without www) still opens by itself — "
               "a redirect in Hostinger settles that, the steps are under On Google"
               if moved is False else ""))
    if mismatch:
        head = mismatch + " " + head
    if errors and not rows:
        return {"ok": False, "error": (mismatch + " " if mismatch else "")
                + "Google would not answer about your pages: " + errors[0]}
    return {"ok": True, "asked": asked, "on_google": on, "pages": total,
            "queued": queued, "head": head, "errors": errors[:3], "mismatch": mismatch,
            "other_address_redirects": None if moved == "ask" else moved}


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
    try:
        from providers import integrations
        mismatch = address_mismatch((integrations.read_env().get("GSC_PROPERTY") or "").strip())
    except Exception:
        mismatch = ""
    return {"ok": True, "pages": pages, "on_google": on,
            "checked_at": when, "mismatch": mismatch,
            "head": (f"{on} of {len(pages)} page(s) on Google" if pages else
                     "not asked yet — it runs in the day once Search Console is working")}


def main():
    ap = argparse.ArgumentParser(description="Which of his pages Google has indexed.")
    ap.add_argument("--status", action="store_true", help="what was found last time")
    ap.add_argument("--now", action="store_true",
                    help="ask Google again about every page, even ones asked today")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    r = status() if a.status else run(force=a.now)
    if a.json or a.status:
        print(json.dumps(r, default=str))
    else:
        print(r.get("head") or r.get("error"))
    return 0 if r.get("ok") or r.get("idle") else 1


if __name__ == "__main__":
    sys.exit(main())
