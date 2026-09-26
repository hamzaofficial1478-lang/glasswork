"""
sitepace.py — the agency reads the owner's own website gently, and stops the
moment the website says stop.

THE DAY HIS SITE "COULD NOT BE REACHED"

  "i checked the website and it says site cannot be reached with
   worlddealtechnicalservices.com unexpectedly closed the connection"

The site was up — read from outside it answered in under two seconds, header,
footer and all. It was HIS address that was being refused. His host
(Hostinger, a LiteSpeed server) runs a firewall that shuts the door on an
address making a lot of requests quickly, and the agency runs on his laptop:
during a run the IT lane (health check, change watch, search index), the SEO
lane (technical audit, answer audit) and the Releaser all read his site at the
same moment, each as fast as it could, from the same address he browses from.
To a firewall that is a bot. It blocked the bot, and blocked him with it.

Two rules, for every request any desk makes to HIS site — requests or urllib,
whichever the pipeline happens to use:

  ONE QUEUE        at most one request every GAP seconds, across every desk
                   and every process, in turn. Held in the cabinet, because the
                   lanes are separate processes and a per-process timer would
                   let three of them fire together, which is the whole problem.

  STOP WHEN TOLD   the first time his site closes the connection, resets it, or
                   answers 429 or 503, the agency leaves it alone for PAUSE.
                   Every desk that asks in that time is refused at once with
                   the reason, instead of retrying into the firewall and
                   stretching a short block into a long one.

Other websites — rivals, directories, map APIs — are not his to be banned from
and are not paced here; each of those pipelines has its own manners.
"""
import os
import sqlite3
import time
import urllib.parse

GAP = 1.0                  # seconds between any two requests to his site
PAUSE = 30 * 60            # how long his site is left alone after it says stop
BLOCK_STATUSES = {429, 503}
# What a firewall shutting the door looks like from this side.
BLOCK_WORDS = ("connection aborted", "remotedisconnected", "connectionreset",
               "connection reset", "closed connection", "forcibly closed",
               "unexpectedly closed", "connection refused", "eof occurred",
               "unexpected_eof")

_host_cache = None


class SiteResting(ConnectionError):
    """His site told the agency to stop; it is being left alone for a while."""


def _norm(host) -> str:
    h = (host or "").strip().lower()
    return h[4:] if h.startswith("www.") else h


def his_host() -> str:
    """His site's bare domain, or '' — read once per process."""
    global _host_cache
    if _host_cache is None:
        try:
            from . import site
            _host_cache = _norm(site.site_host())
        except Exception:
            _host_cache = ""
    return _host_cache


def _is_his(url) -> bool:
    mine = his_host()
    if not mine:
        return False
    try:
        return _norm(urllib.parse.urlsplit(str(url)).hostname or "") == mine
    except Exception:
        return False


def _active() -> bool:
    # The test suite runs isolated from his settings and must never be slowed
    # or blocked by his real site's state. It switches this on when it tests it.
    return os.environ.get("AGENCY_IGNORE_DOTENV") != "1" or os.environ.get("SITEPACE_TEST") == "1"


def _db():
    from . import board
    c = sqlite3.connect(board.DB, timeout=30, isolation_level=None)
    c.execute("CREATE TABLE IF NOT EXISTS site_pace (id INTEGER PRIMARY KEY CHECK (id=1),"
              " next_at REAL DEFAULT 0, resting_until REAL DEFAULT 0, why TEXT)")
    return c


def resting() -> dict:
    """{until, why} if his site is being left alone right now, else {}."""
    try:
        c = _db()
        try:
            r = c.execute("SELECT resting_until, why FROM site_pace WHERE id=1").fetchone()
        finally:
            c.close()
    except Exception:
        return {}
    if r and (r[0] or 0) > time.time():
        return {"until": r[0], "why": r[1] or ""}
    return {}


def before(url, sleep=time.sleep):
    """Wait for this request's turn on his site, or refuse if it is resting."""
    if not _active() or not _is_his(url):
        return
    now = time.time()
    try:
        c = _db()
        try:
            c.execute("BEGIN IMMEDIATE")
            r = c.execute("SELECT next_at, resting_until, why FROM site_pace WHERE id=1").fetchone()
            next_at, until, why = (r or (0, 0, ""))
            if (until or 0) > now:
                c.execute("COMMIT")
                raise SiteResting(
                    f"your website stopped answering this computer ({why}) — the "
                    f"agency is leaving it alone until "
                    f"{time.strftime('%H:%M', time.localtime(until))} so a firewall "
                    "block is not made worse. Nothing is wrong with the code.")
            wait = max(0.0, (next_at or 0) - now)
            c.execute("INSERT INTO site_pace(id, next_at, resting_until, why) VALUES(1,?,0,'')"
                      " ON CONFLICT(id) DO UPDATE SET next_at=excluded.next_at",
                      (max(now, next_at or 0) + GAP,))
            c.execute("COMMIT")
        finally:
            c.close()
    except SiteResting:
        raise
    except Exception:
        return                      # a cabinet hiccup must never stop a request
    if wait:
        sleep(min(wait, 30))


def told_to_stop(url, status=None, error=None) -> bool:
    """Record a refusal from his site. True if it was one."""
    if not _active() or not _is_his(url):
        return False
    text = f"{type(error).__name__}: {error}".lower() if error is not None else ""
    blocked = (status in BLOCK_STATUSES) or any(w in text for w in BLOCK_WORDS)
    if not blocked:
        return False
    why = (f"it answered {status}" if status in BLOCK_STATUSES
           else "it closed the connection")
    why += time.strftime(" at %H:%M")
    try:
        c = _db()
        try:
            c.execute("INSERT INTO site_pace(id, next_at, resting_until, why) VALUES(1,0,?,?)"
                      " ON CONFLICT(id) DO UPDATE SET resting_until=excluded.resting_until,"
                      " why=excluded.why", (time.time() + PAUSE, why))
        finally:
            c.close()
    except Exception:
        pass
    return True


def clear():
    """He has unblocked his address: let the agency read the site again now."""
    try:
        c = _db()
        try:
            c.execute("UPDATE site_pace SET resting_until=0, why='' WHERE id=1")
        finally:
            c.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# PUT ON EVERY DOOR ONCE
# ---------------------------------------------------------------------------

_installed = False


def install():
    """Wrap requests and urllib so every desk is paced without being edited."""
    global _installed
    if _installed:
        return
    _installed = True

    import urllib.request
    real_urlopen = urllib.request.urlopen

    def urlopen(url, *a, **kw):
        target = getattr(url, "full_url", url)
        before(target)
        try:
            r = real_urlopen(url, *a, **kw)
        except Exception as e:
            told_to_stop(target, status=getattr(e, "code", None), error=e)
            raise
        return r

    urllib.request.urlopen = urlopen

    try:
        import requests
    except Exception:
        return
    real_request = requests.sessions.Session.request

    def request(self, method, url, *a, **kw):
        before(url)
        try:
            r = real_request(self, method, url, *a, **kw)
        except Exception as e:
            told_to_stop(url, error=e)
            raise
        told_to_stop(url, status=getattr(r, "status_code", None))
        return r

    requests.sessions.Session.request = request
