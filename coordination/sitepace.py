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
PAUSE = 30 * 60            # the longest rest: his site keeps closing the door
# HOW LONG TO LEAVE IT, BY HOW OFTEN IT HAS HAPPENED.
#
# "approved a blog and passed it to publish to wesbite but this issue happenes"
# — one closed connection at 14:24 stopped every desk for thirty minutes, and
# the blog he had just approved was refused inside that half hour. His line
# drops now and then on its own; a single drop is far more often that than a
# firewall. So the first one rests the agency for two minutes, a second within
# the hour for ten, and only a third for the full thirty. An explicit 429 ("too
# many requests") is the site saying it in words, and gets fifteen at once.
REST_STEPS = (2 * 60, 10 * 60, PAUSE)
STRIKE_MEMORY = 60 * 60    # a clean hour and earlier drops are forgotten
# ONE DROP IS A BLIP, TWO IN A ROW IS A DOOR.
#
# "i want that thing to be more strong more fast more reliable errors free ...
#  so the user will not face the error while he will be doing the multiple
#  tasks with webiste" — a single dropped line used to pause every desk for two
#  minutes, and whatever he had just pressed failed with it. A firewall that has
#  shut the door shuts it on every request, so the first drop is now only noted:
#  the request is tried again a moment later (pipelines/site_api), and only a
#  second drop inside this window rests the agency. A request that gets through
#  in between wipes the note — that was the line, not a firewall.
WARN_WINDOW = 90
THROTTLED = 15 * 60
BLOCK_STATUSES = {429, 503}
# What a firewall shutting the door looks like from this side.
#
# WINDOWS SAYS IT THREE WAYS. The same hang-up comes back on his PC as
# 10054 "forcibly closed", or 10053 "An established connection was aborted by
# the software in your host machine" — which one depends on whether the
# request was still being sent when the door shut. 10053 matched none of these
# words, so a hang-up was missed at random and the test suite went red on his
# machine and green on Linux. And urllib wraps it (URLError), so the real
# error's own name is read from inside as well — see told_to_stop.
BLOCK_WORDS = ("connection aborted", "connection was aborted", "connectionaborted",
               "remotedisconnected", "connectionreset",
               "connection reset", "closed connection", "forcibly closed",
               "unexpectedly closed", "connection refused", "eof occurred",
               "unexpected_eof", "winerror 10053", "winerror 10054", "winerror 10061")

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
    for col in ("strikes INTEGER DEFAULT 0", "last_strike REAL DEFAULT 0", "warned_at REAL DEFAULT 0"):
        try:
            c.execute("ALTER TABLE site_pace ADD COLUMN " + col)
        except sqlite3.OperationalError:
            pass
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
    inner = getattr(error, "reason", None)
    text = (f"{type(error).__name__}: {type(inner).__name__ if inner is not None else ''}"
            f" {error}".lower() if error is not None else "")
    blocked = (status in BLOCK_STATUSES) or any(w in text for w in BLOCK_WORDS)
    if not blocked:
        return False
    # Which request, and what exactly came back — so "it closed the
    # connection" can be told apart later from a blip on his line.
    try:
        path = urllib.parse.urlsplit(str(url)).path or "/"
    except Exception:
        path = ""
    why = (f"it answered {status}" if status in BLOCK_STATUSES
           else "it closed the connection")
    why += time.strftime(" at %H:%M")
    if path:
        why += f" on {path[:60]}"
    if error is not None:
        why += f" ({type(error).__name__})"
    now = time.time()
    global _noted
    try:
        c = _db()
        try:
            if status != 429:
                w = c.execute("SELECT warned_at FROM site_pace WHERE id=1").fetchone()
                if not w or now - (w[0] or 0) > WARN_WINDOW:
                    c.execute("INSERT INTO site_pace(id, next_at, resting_until, why, warned_at)"
                              " VALUES(1,0,0,'',?) ON CONFLICT(id) DO UPDATE SET warned_at=excluded.warned_at",
                              (now,))
                    _noted = True
                    return True
            c.execute("UPDATE site_pace SET warned_at=0 WHERE id=1")
            r = c.execute("SELECT strikes, last_strike FROM site_pace WHERE id=1").fetchone()
            strikes, last = (r or (0, 0))
            strikes = (strikes or 0) + 1 if (now - (last or 0)) < STRIKE_MEMORY else 1
            rest = REST_STEPS[min(strikes, len(REST_STEPS)) - 1]
            if status == 429:
                rest = max(rest, THROTTLED)
            c.execute("INSERT INTO site_pace(id, next_at, resting_until, why, strikes,"
                      " last_strike) VALUES(1,0,?,?,?,?)"
                      " ON CONFLICT(id) DO UPDATE SET resting_until=excluded.resting_until,"
                      " why=excluded.why, strikes=excluded.strikes,"
                      " last_strike=excluded.last_strike",
                      (now + rest, why, strikes, now))
        finally:
            c.close()
    except Exception:
        pass
    return True


_noted = False


def warned() -> bool:
    """A first drop is on record and the window for a second is still open."""
    try:
        c = _db()
        try:
            r = c.execute("SELECT warned_at FROM site_pace WHERE id=1").fetchone()
        finally:
            c.close()
    except Exception:
        return False
    return bool(r and r[0] and time.time() - r[0] <= WARN_WINDOW)


def got_through(url):
    """A request to his site was answered: a drop noted by this process was a blip."""
    global _noted
    if not _noted or not _active() or not _is_his(url):
        return
    _noted = False
    try:
        c = _db()
        try:
            c.execute("UPDATE site_pace SET warned_at=0 WHERE id=1")
        finally:
            c.close()
    except Exception:
        pass


def clear():
    """He has unblocked his address: let the agency read the site again now."""
    try:
        c = _db()
        try:
            c.execute("UPDATE site_pace SET resting_until=0, why='', strikes=0, warned_at=0 WHERE id=1")
        finally:
            c.close()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# PUT ON EVERY DOOR ONCE
# ---------------------------------------------------------------------------

_installed = False


def _pace_urllib(mod):
    real_urlopen = mod.urlopen

    def urlopen(url, *a, **kw):
        target = getattr(url, "full_url", url)
        before(target)
        try:
            r = real_urlopen(url, *a, **kw)
        except Exception as e:
            told_to_stop(target, status=getattr(e, "code", None), error=e)
            raise
        got_through(target)
        return r

    urlopen.paced = True
    mod.urlopen = urlopen


def _pace_requests(mod):
    real_request = mod.sessions.Session.request

    def request(self, method, url, *a, **kw):
        before(url)
        try:
            r = real_request(self, method, url, *a, **kw)
        except Exception as e:
            told_to_stop(url, error=e)
            raise
        if not told_to_stop(url, status=getattr(r, "status_code", None)):
            got_through(url)
        return r

    request.paced = True
    mod.sessions.Session.request = request


# WRAPPED WHEN FIRST LOADED, not loaded in order to be wrapped. Importing
# requests and urllib.request just to put the wrapper on was most of the start-up
# time of every program the screens run — a tenth of a second here, far more on
# Windows — and most of those programs never make a web request at all. So a
# finder waits for either library to be imported and wraps it the moment it has
# loaded, before whoever asked for it gets it: same wrapper, same door, paid for
# only by the programs that actually read a website.
class _WrapWhenLoaded:
    def __init__(self, patches):
        self.patches = dict(patches)        # module name -> wrap(module)
        self.looking = set()

    def find_spec(self, name, path=None, target=None):
        wrap = self.patches.get(name)
        if wrap is None or name in self.looking:
            return None
        self.looking.add(name)
        try:
            import importlib.util
            spec = importlib.util.find_spec(name)
        except Exception:
            spec = None
        finally:
            self.looking.discard(name)
        if spec is None or spec.loader is None or not hasattr(spec.loader, "exec_module"):
            return None
        spec.loader = _Loader(spec.loader, wrap, lambda: self.patches.pop(name, None))
        return spec


class _Loader:
    def __init__(self, real, wrap, done):
        self.real, self.wrap, self.done = real, wrap, done

    def create_module(self, spec):
        return self.real.create_module(spec)

    def exec_module(self, module):
        self.real.exec_module(module)
        self.done()
        try:
            self.wrap(module)
        except Exception:                                   # pragma: no cover
            pass

    def __getattr__(self, name):                            # anything else is the real one's
        return getattr(self.real, name)


def install():
    """Pace every desk's requests to his site, without editing any desk."""
    global _installed
    if _installed:
        return
    _installed = True
    import sys
    waiting = {}
    for name, wrap in (("urllib.request", _pace_urllib), ("requests", _pace_requests)):
        if name in sys.modules:
            try:
                wrap(sys.modules[name])
            except Exception:                               # pragma: no cover
                pass
        else:
            waiting[name] = wrap
    if waiting:
        sys.meta_path.insert(0, _WrapWhenLoaded(waiting))
