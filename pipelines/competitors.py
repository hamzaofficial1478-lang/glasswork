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
import ipaddress
import json
import re
import socket
import sqlite3
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from coordination import board, cabinet
from . import services


# ---------------------------------------------------------------------------
# ASKING ANOTHER SECTOR, IN THIS SECTOR'S OWN WORDS
# ---------------------------------------------------------------------------
# The trigger and the facts are gathered above, from rows. The WORDING and the
# choice of who to ask go through coordination/ask.py, where this sector's own
# routed brain decides whether it is worth another sector's time, who actually
# owns it, and how to put it. Falls back to the fixed text — and says so in the
# request — when no brain is routed. See ask.py for why the evidence is attached
# by code and never written by the model.
def _ask_send(to, situation, facts, subject, body, from_sector="SEO"):
    try:
        from coordination import ask as _ask
    except Exception:
        return board.post_request(from_sector, to, subject, body)
    return _ask.send(from_sector, to, situation, facts, subject, body).get("id")


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


# ---------------------------------------------------------------------------
# WHERE WE ARE ALLOWED TO FETCH FROM
# ---------------------------------------------------------------------------
#
# This pipeline fetches URLs that come out of a RIVAL'S OWN XML. A sitemap index
# lists child sitemaps, and this reads them — so the remote file decides what
# the agency requests next. That is somebody else's content steering our HTTP
# client, which is the whole shape of an SSRF, and a rival's site does not have
# to be malicious to be compromised.
#
# Two things were reachable before this guard:
#
#   <loc>file:///etc/passwd</loc>          urlopen serves file:// happily, and
#                                          the contents landed in the snapshot
#   <loc>http://169.254.169.254/...</loc>  the cloud metadata endpoint, which on
#                                          a hosted box hands out credentials
#
# and either would then be stored in competitor_snapshots and shown to the owner
# as "a new page the rival published".
#
# So: http and https only, public addresses only, and every redirect hop is
# checked too — a perfectly ordinary rival URL is allowed to 302 to 127.0.0.1.

# The resolver's "temporary failure" codes: EAI_AGAIN on Linux and macOS, and
# WSATRY_AGAIN (11002) on Windows, where he runs it.
_DNS_TRY_AGAIN = {getattr(socket, "EAI_AGAIN", -3), 11002}


def host_verdict(host: str) -> tuple:
    """
    (ok, why) for one hostname. Two refusals, and they are NOT the same thing.

    The single message this used to give — "x is not a public address" — sent
    somebody looking for a firewall rule when the real answer was that the
    domain does not exist. A business whose website has lapsed is a normal
    thing to meet while harvesting leads, and it is worth saying plainly rather
    than dressing up as a security refusal.
    """
    infos = None
    for wait in (0, 2, 5):
        if wait:
            time.sleep(wait)
        try:
            infos = socket.getaddrinfo(host, None)
            break
        except socket.gaierror as e:
            # "Try again" is the resolver saying the line dropped, not that
            # the name does not exist — his own site was once reported as not
            # existing during a thirty-second blip on his connection. A name
            # that genuinely does not exist is not asked about three times.
            if e.errno not in _DNS_TRY_AGAIN:
                break
        except Exception:
            break
    if infos is None:
        return False, f"{host} does not resolve — no such host, or DNS is down"
    if not infos:
        return False, f"{host} does not resolve to anything"
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False, f"{host} resolved to something that is not an IP address"
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
            return False, (f"{host} points inside the network ({ip}) — refusing, "
                           "this is how a crawler becomes a credential leak")
    return True, ""


def _is_public_host(host: str) -> bool:
    """False for anything that resolves to a loopback, private or link-local IP."""
    return host_verdict(host)[0]


def safe_url(url: str) -> tuple:
    """(ok, why). The one gate every fetch in this module goes through."""
    try:
        u = urlparse((url or "").strip())
    except Exception:
        return False, "could not parse that URL"
    if u.scheme not in ("http", "https"):
        return False, f"refusing scheme {u.scheme or 'none'!r} — only http and https"
    if not u.hostname:
        return False, "no host in that URL"
    ok, why = host_verdict(u.hostname)
    if not ok:
        return False, why
    return True, ""


class _GuardedRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is a second URL choice, so it gets the same check as the first."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        ok, why = safe_url(newurl)
        if not ok:
            raise urllib.error.URLError(f"blocked redirect to {newurl}: {why}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_GuardedRedirect)


# What the crawler shelf cost this run. Reset per run; reported in the note so a
# paid fetch is never silent.
_SPENT = {"usd": 0.0, "via_crawler": 0}


def _get(url, timeout=15):
    """
    One read-only GET, to a public http(s) address only. (status, body, error).

    Plain HTTP first, because it is free and it works for most sitemaps. If a
    rival is behind Cloudflare or serves nothing to Python, the crawler shelf
    gets one attempt — that is the whole reason that shelf exists, and until now
    nothing in the codebase actually read a key off it. The watch reported
    "unreadable" for ever and the shelf light asked the owner to buy something
    that would never have been used.

    Free path unchanged, so a run with no crawler key behaves exactly as before.
    """
    ok, why = safe_url(url)
    if not ok:
        return 0, "", f"refused: {why}"
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with _OPENER.open(req, timeout=timeout) as r:
            return r.status, r.read(2_000_000).decode("utf-8", "replace"), None
    except urllib.error.HTTPError as e:
        status, err = e.code, f"HTTP {e.code}"
    except Exception as e:
        status, err = 0, str(e)

    # Blocked or unreachable. Ask the shelf, if there is anything on it.
    try:
        from . import fetcher
        if fetcher.crawler_tool() is None:
            return status, "", err
        r = fetcher.fetch(url, render=True, timeout=max(timeout, 40))
    except Exception:
        return status, "", err
    _SPENT["usd"] += r.get("cost", 0.0)
    if r.get("ok") and r.get("html"):
        _SPENT["via_crawler"] += 1
        return 200, r["html"], None
    return status, "", f"{err}; the crawler could not read it either: {r.get('error')}"


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
    """
    The rivals he has actually confirmed. These are the only ones crawled.

    NULL status means confirmed: every row that existed before suggestions were
    added is one he typed in himself.
    """
    con = sqlite3.connect(db or board.DB)
    try:
        board.ensure_schema(con)
        return [{"id": r[0], "name": r[1], "domain": r[2]}
                for r in con.execute(
                    "SELECT id,name,domain FROM competitors"
                    " WHERE COALESCE(status,'confirmed') <> 'suggested'"
                    " ORDER BY id")]
    finally:
        con.close()


def suggest_rival(name, evidence="", domain="", db=None):
    """
    Put a firm forward as a possible rival, on evidence, for him to confirm.

    THE DEAD END THIS REPLACES:

        "No competitors on file, and this pipeline will not invent any — who
         actually bids against you is your call. Add one with: python -m
         pipelines.competitors --add ..."

    The principle was right — the agency must not decide who his competitors
    are — but the consequence was a step of the day that could never do
    anything until he opened a terminal and typed a command he had no reason to
    know about. It had produced nothing for as long as the agency had existed.

    Suggesting is not inventing. The SEO desk sees people typing a firm's name
    followed by "reviews" or "contact number" while searching for glass work in
    his areas. That is not a guess about the market, it is an observation of it,
    and it comes with the searches attached so he can see what put it there.
    Nothing is crawled until he says yes.
    """
    nm = (name or "").strip()
    if len(nm) < 3:
        return None
    host = _normalise(domain) if domain else ""
    con = sqlite3.connect(db or board.DB)
    try:
        board.ensure_schema(con)
        row = con.execute(
            "SELECT id, evidence FROM competitors WHERE lower(name)=?"
            + (" OR (domain<>'' AND lower(domain)=?)" if host else ""),
            ((nm.lower(), host) if host else (nm.lower(),))).fetchone()
        if row:
            # Already known — add the new search to the evidence rather than
            # making a second row. More searches for the same firm is a
            # stronger case, not a duplicate.
            try:
                seen = json.loads(row[1] or "[]")
            except Exception:
                seen = []
            if evidence and evidence not in seen:
                seen.append(evidence)
                con.execute("UPDATE competitors SET evidence=? WHERE id=?",
                            (json.dumps(seen[:12]), row[0]))
                con.commit()
            return None
        cur = con.execute(
            "INSERT INTO competitors(name,domain,status,evidence,first_seen)"
            " VALUES(?,?, 'suggested', ?, ?)",
            (nm, host, json.dumps([evidence] if evidence else []), int(time.time())))
        con.commit()
        return cur.lastrowid
    finally:
        con.close()


# ---------------------------------------------------------------------------
# GOING AND FINDING THEM
# ---------------------------------------------------------------------------
#
# "the competitors adding? i think you ignored that i think either we should
#  add this or the agent should find our competitors in our related services
#  in UAE."
#
# The first version of this waited to be told. Then it waited for the SEO desk
# to happen to see somebody searching a rival by name — better, and still
# passive: it only ever learns about a firm popular enough that Google suggests
# its name, which is the handful of big ones he already knows about.
#
# This goes and looks. OpenStreetMap has the UAE's businesses tagged by trade,
# it is free, keyless, and the agency already talks to it — pipelines/leads_osm
# uses the same Overpass mirrors to find leads. A firm tagged as a glazier in
# Sharjah, or one whose name contains "aluminium" sitting in an industrial area
# in Ajman, is doing the work he does in the country he does it in. That is an
# observation, not a guess.
#
# It still suggests rather than adds. Nothing is crawled until he says yes.

# The trade, in OSM's vocabulary. Only tags that genuinely exist in the UAE
# extract — see the note in leads_osm: inventing a tag produces a query that
# always returns nothing, which looks exactly like "OSM has no data" and is
# much harder to spot.
RIVAL_TAGS = (
    ("craft", "glaziery"),
    ("shop", "glaziery"),
    ("craft", "window_construction"),
    ("craft", "metal_construction"),
    ("shop", "windows"),
    ("shop", "doors"),
)

# Tag coverage for this trade is thin in the Gulf — most of these firms are
# mapped as a plain office or industrial unit with a descriptive name. The name
# is the strongest signal there is, so it is searched directly. Anchored to the
# trade words rather than to anything generic, or half of Dubai comes back.
RIVAL_NAME_RE = r"[Gg]lass|GLASS|[Aa]lumin|ALUMIN|[Gg]laz|[Pp]ergola"

# Words that mean this is somebody's showroom or supplier rather than a rival
# fabricator — a car-glass fitter and a glass-bottle wholesaler are not bidding
# against him for a shower screen.
NOT_A_RIVAL = (
    "auto", "car ", "windscreen", "windshield", "vehicle", "tinting film",
    "bottle", "bottles", "spectacle", "optic", "optical", "eyewear",
    "glasses", "sunglass", "mirror shop", "crystal palace",
    "hospital", "school", "university", "hotel", "restaurant", "cafe",
    "pharmacy", "supermarket", "mosque", "tower", "residence",
)

# How many rivals it is worth putting in front of him at once. A list of forty
# is a list he will not read, and confirming a rival costs him a glance rather
# than nothing.
MOST_TO_SUGGEST = 12


def _looks_like_a_rival(name, tags) -> bool:
    n = (name or "").lower()
    if len(n) < 4:
        return False
    if any(bad in n for bad in NOT_A_RIVAL):
        return False
    # It has to actually be in the trade — either tagged as it, or named for it.
    tagged = any(tags.get(k) == v for k, v in RIVAL_TAGS)
    named = bool(re.search(r"glass|alumin|glaz|pergola|shopfront|curtain wall",
                           n, re.I))
    return tagged or named


def _ours(name, domain, mine) -> bool:
    """Us, not a rival. Finding your own company and reporting it is a bad look."""
    n = (name or "").strip().lower()
    d = _normalise(domain)
    if d and mine.get("domain") and d == mine["domain"]:
        return True
    own = (mine.get("name") or "").strip().lower()
    if own and len(own) > 4 and (own in n or n in own):
        return True
    return False


def _me():
    """Who we are, so the search does not put us forward as our own rival."""
    env = {}
    try:
        from providers import integrations
        env = dict(integrations.read_env())
    except Exception:
        pass
    import os
    for k in ("BUSINESS_NAME", "AGENCY_NAME", "SITE_URL"):
        if os.environ.get(k):
            env[k] = os.environ[k]
    return {"name": (env.get("BUSINESS_NAME") or env.get("AGENCY_NAME") or "").strip(),
            "domain": _normalise(env.get("SITE_URL") or "")}


# How long the whole search may take. It runs inside a step of the day that
# gets seven minutes, and Overpass is a gift rather than a service — four
# mirrors, each allowed to time out, across seven emirates is easily longer
# than the step has. Whatever it has found when the clock runs out is what he
# gets, which is the right way round: some rivals today beats none ever.
SEARCH_BUDGET = 150

# Emirates per pass. Seven queries against a free, donated endpoint every day
# is not neighbourly, and the map does not change hourly. It works through them
# a few at a time, oldest first, so the whole country is covered within a week
# of runs and no single run is heavy.
AREAS_PER_PASS = 3


def _next_areas(want, per_pass=AREAS_PER_PASS):
    """
    The emirates to look at this time, rotating so they all get a turn.

    Anchored to the day of the year rather than to a stored cursor: no state to
    migrate, no row to go stale, and two runs on the same day look in the same
    place — which is what you want, because the second one then finds nothing
    new rather than re-asking a busy public endpoint for a different slice.
    """
    if not want:
        return []
    start = (time.gmtime().tm_yday * per_pass) % len(want)
    doubled = list(want) + list(want)
    return doubled[start:start + per_pass]


def discover(areas=None, limit=MOST_TO_SUGGEST, timeout=45,
             budget=SEARCH_BUDGET) -> dict:
    """
    Go and find firms doing this trade in the UAE. Suggests; adds nothing.

    Returns {ok, found, suggested[], areas, error}. `found` counts everything
    the map returned; `suggested` is what actually went in front of him after
    the filtering, the de-duplication and taking ourselves out.

    Bounded by `budget` seconds, because this runs inside a step of the day and
    a public endpoint is allowed to be slow. Running out of time is not a
    failure — it stops, keeps what it found, and says how far it got.
    """
    try:
        from . import leads_osm as osm
    except Exception as e:                                    # pragma: no cover
        return {"ok": False, "error": f"the OSM reader did not load ({e})",
                "found": 0, "suggested": []}
    try:
        from coordination import uae
        wanted = [a for a in (areas or uae.EMIRATES) if a in osm.BBOX]
    except Exception:                                         # pragma: no cover
        wanted = [a for a in (areas or ()) if a in osm.BBOX] or list(osm.BBOX)
    if not wanted:
        wanted = list(osm.BBOX)
    # Only if the caller did not name them. "--find --area Dubai" means Dubai,
    # not "Dubai if it is Dubai's turn".
    if areas is None:
        wanted = _next_areas(wanted)

    mine = _me()
    stop_at = time.time() + max(20, int(budget))
    seen, put_forward, found, trouble = set(), [], 0, []
    ran_out = False
    for area in wanted:
        if time.time() >= stop_at:
            ran_out = True
            break
        bbox = osm.BBOX[area]
        tagged = "".join(f'nwr["{k}"="{v}"]({bbox});' for k, v in RIVAL_TAGS)
        named = (f'nwr["name"~"{RIVAL_NAME_RE}"]["shop"]({bbox});'
                 f'nwr["name"~"{RIVAL_NAME_RE}"]["craft"]({bbox});'
                 f'nwr["name"~"{RIVAL_NAME_RE}"]["office"]({bbox});'
                 f'nwr["name"~"{RIVAL_NAME_RE}"]["industrial"]({bbox});')
        q = (f"[out:json][timeout:{int(timeout)}];({tagged}{named});"
             f"out center {osm.MAX_RESULTS};")
        # Never ask the endpoint for longer than is left on the clock.
        left = max(10, int(stop_at - time.time()))
        r = osm.ask(q, timeout=min(timeout, left))
        if not r.get("ok"):
            trouble.append(f"{area}: {r.get('error') or 'no answer'}")
            continue
        for el in r.get("elements") or []:
            tags = el.get("tags") or {}
            name = (tags.get("name:en") or tags.get("name") or "").strip()
            if not _looks_like_a_rival(name, tags):
                continue
            found += 1
            site = (tags.get("website") or tags.get("contact:website")
                    or tags.get("url") or "").strip()
            domain = _normalise(site)
            if _ours(name, domain, mine):
                continue
            key = domain or name.lower()
            if key in seen:
                continue
            seen.add(key)
            put_forward.append({"name": name, "domain": domain, "area": area})
        if time.time() < stop_at:
            time.sleep(osm.PAUSE)

    # A rival with a website first: those are the ones this sector can actually
    # watch, because watching means reading their sitemap. A name with no site
    # is still worth showing him — he may know the firm — but it goes below.
    put_forward.sort(key=lambda r: (not r["domain"], r["name"].lower()))
    added = []
    for r in put_forward[:limit]:
        why = (f"mapped as a {('glass or aluminium firm' if r['domain'] else 'glass or aluminium firm')}"
               f" in {r['area']} on OpenStreetMap"
               + (f" ({r['domain']})" if r["domain"] else ", with no website listed"))
        if suggest_rival(r["name"], evidence=why, domain=r["domain"]):
            added.append(r["name"])

    return {"ok": bool(put_forward) or not trouble,
            "found": found, "candidates": len(put_forward),
            "suggested": added, "areas": wanted, "ran_out_of_time": ran_out,
            "error": ("; ".join(trouble) if trouble and not put_forward else ""),
            "trouble": trouble}


def suggested(db=None):
    """Firms waiting for him to say whether they are rivals."""
    con = sqlite3.connect(db or board.DB)
    try:
        board.ensure_schema(con)
        out = []
        for r in con.execute("SELECT id,name,domain,evidence FROM competitors"
                             " WHERE status='suggested' ORDER BY id"):
            try:
                ev = json.loads(r[3] or "[]")
            except Exception:
                ev = []
            out.append({"id": r[0], "name": r[1], "domain": r[2], "evidence": ev})
        return out
    finally:
        con.close()


def confirm_suggested(ids=None, db=None) -> int:
    """He said yes. Returns how many became real rivals."""
    con = sqlite3.connect(db or board.DB)
    try:
        board.ensure_schema(con)
        if ids:
            marks = ",".join("?" * len(ids))
            cur = con.execute(f"UPDATE competitors SET status='confirmed'"
                              f" WHERE status='suggested' AND id IN ({marks})",
                              tuple(ids))
        else:
            cur = con.execute("UPDATE competitors SET status='confirmed'"
                              " WHERE status='suggested'")
        con.commit()
        return cur.rowcount
    finally:
        con.close()


def drop_suggested(ids=None, db=None) -> int:
    """He said no. They go, rather than being asked about again every day."""
    con = sqlite3.connect(db or board.DB)
    try:
        board.ensure_schema(con)
        if ids:
            marks = ",".join("?" * len(ids))
            cur = con.execute(f"DELETE FROM competitors WHERE status='suggested'"
                              f" AND id IN ({marks})", tuple(ids))
        else:
            cur = con.execute("DELETE FROM competitors WHERE status='suggested'")
        con.commit()
        return cur.rowcount
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
    _SPENT["usd"], _SPENT["via_crawler"] = 0.0, 0
    with board.agent_run(agent) as run_row:
        out = _watch(agent, run_row, timeout)
        # A paid fetch is never silent: it goes in the note and on the run's
        # spend line, so the cost governor and the owner both see it.
        if _SPENT["via_crawler"]:
            out["read_via_crawler"] = _SPENT["via_crawler"]
            out["crawler_cost"] = round(_SPENT["usd"], 4)
            out["summary"] = (out.get("summary", "") +
                              f" ({_SPENT['via_crawler']} needed the crawler, "
                              f"${_SPENT['usd']:.4f})")
            run_row.spent(_SPENT["usd"])
        run_row.note(out.get("summary") or out.get("error") or "checked the rivals")
        return out


CONFIRM_TITLE = "Confirm who your competitors are"


def _ask_him_to_confirm(waiting):
    """
    Put the suggested rivals in front of him as an amber task, once.

    Amber is the right colour and the existing machinery does the rest: an
    amber task cannot be claimed until he approves it, approving drops it to
    green, and the next pass of this desk picks it up and confirms them. He
    never has to open a terminal, and the agency never decides on its own who
    he is competing against.
    """
    lines = []
    for r in waiting[:12]:
        ev = "; ".join(r["evidence"][:3]) or "seen in search"
        lines.append(f"- {r['name']}" + (f"  ({r['domain']})" if r["domain"] else "")
                     + f"\n    because people searched: {ev}")
    body = (
        "People are searching for these firms by name alongside the glass and "
        "aluminium searches you want to win. That usually means they are who you "
        "are up against.\n\n"
        + "\n".join(lines)
        + "\n\nApprove this and the agency starts watching their sitemaps — which "
          "pages they add, what they are pushing this month. Nothing is crawled "
          "until you say yes. If any of them are not really rivals, say so and "
          "they will not be suggested again:\n"
          "  python -m pipelines.competitors --not-a-rival \"Their Name\"")
    try:
        return board.try_post_task("SEO", CONFIRM_TITLE, body,
                                   risk="amber", created_by="seo-rival")
    except Exception:
        return None


def _watch(agent, run_row, timeout):
    rivals = list_competitors()
    if not rivals:
        # HE MAY HAVE ALREADY SAID YES. The confirm task is amber, so it can
        # only be claimed once he has approved it — claiming it IS the yes.
        task = board.next_open_task("SEO", agent)
        if task and CONFIRM_TITLE.lower() in (task.get("title") or "").lower():
            n = confirm_suggested()
            board.finish_task(task["id"], agent,
                              {"did": f"{n} firm(s) confirmed as competitors and "
                                      "added to the watch list."}, status="done")
            rivals = list_competitors()
        elif task:
            # Not the confirm job — put it straight back for whoever it is for.
            board.release_claim(task["id"], by=agent,
                                reason="the rival watcher has no rivals to watch yet")

    if not rivals:
        waiting = suggested()
        if not waiting:
            # NOTHING CONFIRMED AND NOTHING WAITING — so go and look, rather
            # than reporting an empty desk for the third day running. This is
            # the difference between "who bids against you is your call" and
            # "who bids against you is your homework".
            got = discover()
            waiting = suggested()
            if not waiting and not got.get("ok"):
                return {"ok": False, "idle": True,
                        "error": ("Went looking for competitors on OpenStreetMap "
                                  "and could not reach it: "
                                  + (got.get("error") or "no reason given")
                                  + ". Nothing was invented. Add one yourself with "
                                    'python -m pipelines.competitors --add "Their '
                                    'Name" theirdomain.ae, or let it try again '
                                    "tomorrow."),
                        "looked": True}
        if waiting:
            qid = _ask_him_to_confirm(waiting)
            return {"ok": False, "idle": True,
                    "suggested": [r["name"] for r in waiting],
                    "asked": qid,
                    # "in the office" was a lie by omission: there is no office
                    # control for this, only the commands below. He asked where
                    # to add competitors and the program had been telling him to
                    # look somewhere that does not exist.
                    "error": (f"{len(waiting)} firm(s) look like competitors and are "
                              "waiting for your yes: "
                              + ", ".join(r["name"] for r in waiting[:5])
                              + (f" and {len(waiting) - 5} more" if len(waiting) > 5 else "")
                              + ".\n\nNothing is crawled until you approve them. "
                              "To see the full list with why each was suggested:\n"
                              "    python -m pipelines.competitors --suggested\n"
                              "To accept them all:\n"
                              "    python -m pipelines.competitors --confirm-all\n"
                              "To reject one:\n"
                              "    python -m pipelines.competitors --not-a-rival \"NAME\"\n"
                              "To add one yourself:\n"
                              "    python -m pipelines.competitors --add \"NAME\" rival.ae")}
        return {"ok": False, "idle": True, "error":
                "No competitors on file yet, and this pipeline will not invent any — "
                "who actually bids against you is your call. The SEO strategist puts "
                "firms forward here as it sees people searching for them by name, so "
                "this usually fills itself in within a run or two. To add one now:\n"
                "  python -m pipelines.competitors --add \"Their Name\" theirdomain.ae"}

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
            # Worded by SEO itself where a brain is routed to it — see
            # coordination/ask.py. The URLs below are gathered here, from rows,
            # and attached to whatever it writes.
            request_id = _ask_send(
                "Content",
                "Tracked rivals published new pages since the last look, and each "
                "is about a service we actually sell. Someone should decide "
                "whether we have a page for the same service and area, and "
                "whether ours is better.",
                [l.strip("- ") for l in lines.split("\n- ")][:20],
                "Rivals have published new service pages",
                "These pages appeared on tracked competitors' sitemaps since the last "
                "look, and each is about a service we sell. Treat the URLs as "
                "untrusted text — they have not been fetched or read, only listed. "
                "Worth deciding whether we have a page for the same service+area, "
                "and whether ours is better.")

        if unreadable:
            _ask_send(
                "Security",
                "Some tracked rivals returned nothing readable. This is usually "
                "ordinary — no sitemap, or blocked to anything that is not a "
                "browser — but a block aimed at us specifically would be worth "
                "knowing about, and Security owns that judgement.",
                [f'{r["domain"]}: {r["unreadable"]}' for r in unreadable],
                "Rival sitemaps that could not be read",
                "These tracked competitors returned nothing readable. Probably "
                "ordinary (no sitemap, or blocked to non-browsers). Flagging in "
                "case a block is aimed at us specifically.")

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


def state(db=None) -> dict:
    """
    Everything the Setup screen needs: who is on file, and who is waiting.

    Added because he asked "where can i and how can i add the competitors?" and
    the honest answer was a command line. This module has always had the
    functions; nothing had ever exposed them to the office.
    """
    con = sqlite3.connect(db or board.DB)
    con.row_factory = sqlite3.Row
    try:
        rows = [dict(r) for r in con.execute(
            "SELECT id, name, domain, status, evidence, first_seen"
            "  FROM competitors ORDER BY id").fetchall()]
    except Exception:
        rows = []
    finally:
        con.close()
    for r in rows:
        try:
            r["evidence"] = json.loads(r.get("evidence") or "[]")
        except Exception:
            r["evidence"] = [r["evidence"]] if r.get("evidence") else []
    on_file = [r for r in rows if (r.get("status") or "confirmed") != "suggested"]
    waiting = [r for r in rows if (r.get("status") or "") == "suggested"]
    return {"ok": True, "confirmed": on_file, "suggested": waiting,
            "head": (f"{len(on_file)} rival(s) on file"
                     + (f", {len(waiting)} waiting for your yes" if waiting else "")
                     + ("." if on_file or waiting else
                        " — none yet. Add the firms you actually bid against."))}


def remove_competitor(which, db=None) -> int:
    """Take a rival off the list, by id or by name. Returns how many went."""
    con = sqlite3.connect(db or board.DB)
    try:
        key = str(which).strip()
        if key.isdigit():
            cur = con.execute("DELETE FROM competitors WHERE id=?", (int(key),))
        else:
            cur = con.execute("DELETE FROM competitors WHERE lower(name)=?",
                              (key.lower(),))
        con.commit()
        return cur.rowcount
    finally:
        con.close()


def main():
    ap = argparse.ArgumentParser(description="Watch what the competition publishes.")
    ap.add_argument("--add", nargs=2, metavar=("NAME", "DOMAIN"),
                    help='register a rival, e.g. --add "Al Noor Glass" alnoorglass.ae')
    ap.add_argument("--list", action="store_true", help="show the rivals on file")
    ap.add_argument("--find", action="store_true",
                    help="go and look for competitors in the UAE (OpenStreetMap)")
    ap.add_argument("--suggested", action="store_true",
                    help="firms the SEO desk has put forward, and why")
    ap.add_argument("--confirm-all", action="store_true",
                    help="accept every suggested firm as a real competitor")
    ap.add_argument("--not-a-rival", metavar="NAME",
                    help="reject one suggested firm so it stops being asked about")
    ap.add_argument("--remove", metavar="NAME_OR_ID",
                    help="take a rival off the list")
    ap.add_argument("--json", action="store_true",
                    help="machine-readable state, for the Setup screen")
    a = ap.parse_args()

    if a.json:
        out = state()
        if a.find:
            r = discover()
            out = {**state(), "found": r.get("found", 0),
                   "looked_at": r.get("areas", []), "error": r.get("error")}
        elif a.add:
            add_competitor(a.add[0], a.add[1])
            out = state()
        elif a.remove:
            remove_competitor(a.remove)
            out = state()
        elif a.confirm_all:
            out = {**state(), "confirmed_now": confirm_suggested()}
            out.update(state())
        elif a.not_a_rival:
            drop_suggested([a.not_a_rival])
            out = state()
        print(json.dumps(out))
        return 0
    if a.remove:
        n = remove_competitor(a.remove)
        print(f"removed {n}" if n else "nothing matched that")
        return 0

    if a.add:
        rid = add_competitor(a.add[0], a.add[1])
        print(f"added #{rid}: {a.add[0]} ({_normalise(a.add[1])})" if rid
              else f"already on file: {_normalise(a.add[1])}")
        return
    if a.find:
        r = discover()
        if r["suggested"]:
            print(f"Found {r['found']} firm(s) in the trade across "
                  f"{len(r['areas'])} emirate(s); {len(r['suggested'])} new one(s) "
                  "are waiting for your yes:")
            for n in r["suggested"]:
                print("   " + n)
            print("\nSee them with --suggested, accept them with --confirm-all.")
        elif r.get("error"):
            print("Could not look: " + r["error"])
        else:
            print(f"Looked across {len(r['areas'])} emirate(s), found "
                  f"{r['found']} firm(s) in the trade and nothing new to add.")
        return 0
    if a.suggested:
        waiting = suggested()
        if not waiting:
            print("Nothing waiting. The SEO strategist puts firms forward here "
                  "as it sees people searching for them by name.")
            return
        for r in waiting:
            print(f'#{r["id"]}  {r["name"]}' + (f'  {r["domain"]}' if r["domain"] else ""))
            for ev in r["evidence"][:3]:
                print(f'        because people searched: {ev}')
        print("\nAccept them all with --confirm-all, or reject one with "
              '--not-a-rival "Their Name".')
        return
    if a.confirm_all:
        print(f"{confirm_suggested()} firm(s) are now on the watch list.")
        return
    if a.not_a_rival:
        want = a.not_a_rival.strip().lower()
        ids = [r["id"] for r in suggested() if r["name"].strip().lower() == want]
        if not ids:
            print(f'"{a.not_a_rival}" is not among the suggested firms. '
                  "See --suggested.")
            return
        print(f"{drop_suggested(ids)} removed — it will not be suggested again.")
        return
    if a.list:
        rivals = list_competitors()
        waiting = suggested()
        print("\n".join(f'#{r["id"]}  {r["name"]}  {r["domain"]}' for r in rivals)
              if rivals else "No confirmed rivals on file. Add one with --add.")
        if waiting:
            print(f"\n{len(waiting)} waiting for your yes — see --suggested.")
        return
    print(json.dumps(run(), indent=2))


if __name__ == "__main__":
    main()
