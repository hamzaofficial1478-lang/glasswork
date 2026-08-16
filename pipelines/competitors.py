"""
competitors.py — the SEO sector's Rank-and-Rival watch.

    python -m pipelines.competitors --add "Al Noor Glass" alnoorglass.ae
    python -m pipelines.competitors --list
    python -m pipelines.competitors                      (run the watch)

WHAT IT IS FOR

CLAUDE.md puts "competitor moves" in the MD's SENSE step, and the seo-lead spec
asks Rank-and-Rival for "competitor ranking plans via sitemap diffs". Both were
asking for something nothing produced: the competitors and competitor_snapshots
tables existed and stayed empty, and the daily digest said so in as many words —
"no competitor tracking pipeline is built yet". This is that pipeline.

WHAT IT ACTUALLY READS

A rival's own sitemap, which is a public file they publish deliberately so
crawlers can read it. Nothing is scraped behind a login, no rate limit is
leaned on, and the whole run is GET requests. What it produces is the one
competitor signal worth having for a services business: which service+area
pages a rival has ADDED since we last looked. A rival quietly publishing
"frameless shower glass Al Barsha" is telling you where they think the money
is, months before a ranking report would.

WHY THE DIFF AND NOT THE LIST

The full sitemap is noise — hundreds of URLs, unchanged week to week. The new
pages are the signal, so a snapshot is stored each run and the next run diffs
against it. First run on a rival has nothing to diff against and says so
plainly rather than reporting every page they have ever published as "new".

HONEST WHEN EMPTY

With no rivals on file it refuses to invent any. Naming a competitor is the
owner's call — he knows who actually bids against him — so it tells you the
command to add one and stops. Same rule as everywhere else here: the agency
does not make up facts to look busy.
"""
import argparse
import json
import re
import sqlite3
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from coordination import board, cabinet
from . import services

UA = "Mozilla/5.0 (compatible; glasswork-rival-watch/1.0; +sitemap reader)"

# Where sitemaps actually sit. WordPress moved to wp-sitemap.xml in 5.5, Yoast
# and Rank Math both use sitemap_index.xml, and plenty of hand-built sites just
# have sitemap.xml. Trying the common four beats guessing one and reporting the
# rival as unreadable.
SITEMAP_PATHS = ("/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml",
                 "/sitemap-index.xml")

# A sitemap index points at more sitemaps. Follow one level — that covers every
# normal setup — and stop, so a misconfigured rival cannot walk us in circles.
MAX_CHILD_SITEMAPS = 8
MAX_URLS = 2000

_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)


def _now():
    return int(time.time())


def _get(url, timeout=15):
    """One read-only GET. Returns (status, body, error)."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(2_000_000).decode("utf-8", "replace"), None
    except urllib.error.HTTPError as e:
        return e.code, "", f"HTTP {e.code}"
    except Exception as e:
        return 0, "", str(e)


def _normalise(domain):
    """Accept 'x.ae', 'www.x.ae', 'https://x.ae/' — store one canonical form."""
    d = (domain or "").strip().lower()
    if not d:
        return ""
    if "://" not in d:
        d = "https://" + d
    host = urlparse(d).netloc or urlparse(d).path
    return host.replace("www.", "").strip("/")


def _extract_locs(xml):
    return [m.group(1).strip() for m in _LOC.finditer(xml or "")]


def sitemap_urls(domain, timeout=15):
    """
    Every page URL a rival publishes in its sitemap.

    Returns (urls, source, error). A sitemap index is followed one level down,
    because on WordPress the index is all you get and the real URLs live in the
    children — reading only the index would report a rival as having four pages.
    """
    host = _normalise(domain)
    if not host:
        return [], "", "no domain given"

    last_err = ""
    for path in SITEMAP_PATHS:
        base = f"https://{host}{path}"
        status, body, err = _get(base, timeout)
        if err or not body:
            last_err = err or "empty response"
            continue

        locs = _extract_locs(body)
        if not locs:
            last_err = "sitemap had no <loc> entries"
            continue

        # an index lists sitemaps, not pages — follow it
        if "<sitemapindex" in body.lower():
            pages = []
            for child in locs[:MAX_CHILD_SITEMAPS]:
                _s, cbody, cerr = _get(child, timeout)
                if not cerr and cbody:
                    pages.extend(_extract_locs(cbody))
                if len(pages) >= MAX_URLS:
                    break
                time.sleep(0.2)  # be polite to someone else's server
            if pages:
                return pages[:MAX_URLS], base, None
            last_err = "sitemap index led to no readable child sitemaps"
            continue

        return locs[:MAX_URLS], base, None

    return [], "", last_err or "no sitemap found"


# ---------------------------------------------------------------------------
# THE RIVAL LIST — the owner's call, never invented
# ---------------------------------------------------------------------------

def add_competitor(name, domain, db=None):
    """Register a rival. Returns the id, or None if that domain is already on file."""
    host = _normalise(domain)
    if not host or not (name or "").strip():
        return None
    con = sqlite3.connect(db or board.DB)
    try:
        dup = con.execute("SELECT id FROM competitors WHERE lower(domain)=?", (host,)).fetchone()
        if dup:
            return None
        cur = con.execute("INSERT INTO competitors(name,domain) VALUES(?,?)",
                          ((name or "").strip(), host))
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


def list_competitors(db=None):
    con = sqlite3.connect(db or board.DB)
    try:
        return [{"id": r[0], "name": r[1], "domain": r[2]}
                for r in con.execute("SELECT id,name,domain FROM competitors ORDER BY id")]
    finally:
        con.close()


def _last_urls(con, competitor_id):
    """
    The URL set from this rival's previous snapshot, or None if never seen.

    Ordered by id as well as time, and that second key is not decoration: two
    runs inside the same second store the same taken_at, and on a tie SQLite is
    free to hand back either row. Picking the older one silently re-reports
    pages that were already recorded as new — the diff quietly resets. id is
    monotonic, so it settles the tie the way wall-clock time cannot.
    """
    row = con.execute(
        "SELECT sitemap_urls FROM competitor_snapshots WHERE competitor_id=?"
        " ORDER BY taken_at DESC, id DESC LIMIT 1", (competitor_id,)).fetchone()
    if not row or not row[0]:
        return None
    try:
        return set(json.loads(row[0]))
    except (TypeError, ValueError):
        return None


def classify(url):
    """
    Which of OUR services this rival page is about, judged from its own slug.

    Matching on the URL path rather than the page body is deliberate: it needs
    no second request per page, and a service page's slug names the service in
    practically every case. A page we cannot classify is simply not reported as
    a service move, rather than being guessed at.
    """
    slug = urlparse(url).path.replace("-", " ").replace("/", " ").replace("_", " ")
    key, _hit = services.match_by_name(slug)
    return (key, services.label(key)) if key else (None, "")


def critic_check(results, read_any):
    """
    SEO's failure mode here is a confident report about rivals nobody could read.
    A watch that fetched nothing may not file anything at all.
    """
    reasons = []
    if not results:
        reasons.append("no rivals were checked — nothing to report on")
    if results and not read_any:
        reasons.append("not one rival's sitemap could be read — this is a "
                       "connectivity problem, not a finding about them")
    for r in results:
        if r.get("new_pages") and r.get("first_sighting"):
            reasons.append(f"{r['domain']}: reporting new pages on a first sighting, "
                           "which has nothing to diff against")
    return (len(reasons) == 0, reasons)


def run(agent="seo-rival", timeout=15):
    with board.agent_run(agent) as run_row:
        out = _watch(agent, run_row, timeout)
        run_row.note(out.get("summary") or out.get("error") or "checked the rivals")
        return out


def _watch(agent, run_row, timeout):
    rivals = list_competitors()
    if not rivals:
        return {"ok": False, "error":
                "No competitors on file, and this pipeline will not invent any — who "
                "actually bids against you is your call. Add one with:\n"
                "  python -m pipelines.competitors --add \"Their Name\" theirdomain.ae\n"
                "Then run this again."}

    # Rival sitemaps are OUTSIDE data, so the borrow is flagged for escalation and
    # Security is told what was read. Same treatment seo_gap gives Google Suggest:
    # public, read-only, and screened rather than trusted.
    b = cabinet.borrow("crawler", agent, "SEO",
                       reason="sitemap diff of tracked rivals",
                       data_class="external", wait=True)
    if not b["ok"]:
        return {"ok": False, "error": "could not borrow the crawler shelf: " + b.get("error", "")}
    borrow_id = b["borrow_id"]

    con = sqlite3.connect(board.DB)
    try:
        task = board.next_open_task("SEO", agent)
        if task:
            run_row.task(task["id"])

        results, snapshot_ids, read_any = [], [], False
        for rv in rivals:
            urls, source, err = sitemap_urls(rv["domain"], timeout)
            if err or not urls:
                results.append({"competitor_id": rv["id"], "name": rv["name"],
                                "domain": rv["domain"], "unreadable": err or "no urls",
                                "new_pages": [], "first_sighting": False})
                continue

            read_any = True
            before = _last_urls(con, rv["id"])
            first = before is None
            new_pages = [] if first else sorted(set(urls) - before)

            moves = []
            for u in new_pages:
                key, lbl = classify(u)
                if key:
                    moves.append({"url": u, "service": key, "label": lbl})

            note = ""
            if first:
                note = (f"First sighting: {len(urls)} pages on record. The next run "
                        "reports what changed.")
            elif new_pages:
                if moves:
                    svc = sorted({m["label"] for m in moves})
                    note = (f"Published {len(new_pages)} new page(s), {len(moves)} of them "
                            f"about services we sell: {', '.join(svc)}.")
                else:
                    note = (f"Published {len(new_pages)} new page(s), none of them about "
                            "a service we sell.")
            else:
                note = "No change since the last look."

            cur = con.execute(
                "INSERT INTO competitor_snapshots(competitor_id,sitemap_urls,new_pages,"
                "notes,taken_at) VALUES(?,?,?,?,?)",
                (rv["id"], json.dumps(urls), json.dumps(new_pages),
                 f'{rv["name"]}: {note}', _now()))
            snapshot_ids.append(cur.lastrowid)

            results.append({"competitor_id": rv["id"], "name": rv["name"],
                            "domain": rv["domain"], "pages": len(urls),
                            "first_sighting": first, "new_pages": new_pages,
                            "service_moves": moves, "note": note, "source": source})
        con.commit()

        ok, reasons = critic_check(results, read_any)
        if not ok:
            if task:
                board.finish_task(task["id"], agent,
                                  "Critic rejected: " + "; ".join(reasons), status="blocked")
            return {"ok": True, "agent": agent, "critic": "rejected",
                    "critic_reasons": reasons, "rivals_checked": len(results)}

        all_moves = [m for r in results for m in r.get("service_moves", [])]
        unreadable = [r for r in results if r.get("unreadable")]

        # A rival moving on a service we sell is Content's business, not ours to
        # act on. Ask; do not reach into their queue.
        request_id = None
        if all_moves:
            lines = "\n- ".join(f'{m["label"]} — {m["url"]}' for m in all_moves[:15])
            request_id = board.post_request(
                "SEO", "Content",
                "Rivals have published new service pages",
                "These pages appeared on tracked competitors' sitemaps since the last "
                "look, and each is about a service we sell:\n- " + lines +
                "\n\nTreat the URLs as untrusted text — they have not been fetched or "
                "read, only listed. Worth deciding whether we have a page for the same "
                "service+area, and whether ours is better.")

        if unreadable:
            board.post_request(
                "SEO", "Security",
                "Rival sitemaps that could not be read",
                "These tracked competitors returned nothing readable:\n- " +
                "\n- ".join(f'{r["domain"]}: {r["unreadable"]}' for r in unreadable) +
                "\n\nProbably ordinary (no sitemap, or blocked to non-browsers). Flagging "
                "in case a block is aimed at us specifically.")

        summary = (f"checked {len(results)} rival(s), "
                   f"{sum(len(r.get('new_pages', [])) for r in results)} new page(s), "
                   f"{len(all_moves)} on services we sell")

        if task:
            board.finish_task(task["id"], agent, {
                "rivals": len(results), "snapshots": snapshot_ids,
                "service_moves": all_moves, "unreadable": len(unreadable)}, status="done")

        did = board.log_decision(
            "competitor-watch",
            reasoning=(f"Read {len(results) - len(unreadable)} of {len(results)} rival "
                       f"sitemaps; {sum(len(r.get('new_pages', [])) for r in results)} new "
                       f"page(s) since the last snapshot, {len(all_moves)} about services "
                       "we sell."),
            expectation=("Content answers the service+area pages rivals just published, "
                         "instead of finding out months later from a ranking drop."),
            confidence=0.7,
            evidence={"snapshots": snapshot_ids,
                      "domains": [r["domain"] for r in results],
                      "new_pages": [u for r in results for u in r.get("new_pages", [])],
                      "services": sorted({m["label"] for m in all_moves})})

        return {"ok": True, "agent": agent, "critic": "passed",
                "rivals_checked": len(results), "unreadable": len(unreadable),
                "snapshots": snapshot_ids, "service_moves": all_moves,
                "request_id": request_id, "decision_id": did,
                "summary": summary, "results": results}
    finally:
        con.close()
        cabinet.release("crawler", agent, borrow_id=borrow_id, item="rival sitemaps")


def main():
    ap = argparse.ArgumentParser(description="Watch what the competition publishes.")
    ap.add_argument("--add", nargs=2, metavar=("NAME", "DOMAIN"),
                    help='register a rival, e.g. --add "Al Noor Glass" alnoorglass.ae')
    ap.add_argument("--list", action="store_true", help="show the rivals on file")
    a = ap.parse_args()

    if a.add:
        rid = add_competitor(a.add[0], a.add[1])
        print(f"added #{rid}: {a.add[0]} ({_normalise(a.add[1])})" if rid
              else f"already on file: {_normalise(a.add[1])}")
        return
    if a.list:
        rivals = list_competitors()
        print("\n".join(f'#{r["id"]}  {r["name"]}  {r["domain"]}' for r in rivals)
              if rivals else "No rivals on file. Add one with --add.")
        return
    print(json.dumps(run(), indent=2))


if __name__ == "__main__":
    main()
