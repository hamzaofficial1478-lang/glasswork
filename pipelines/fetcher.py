"""
fetcher.py — one way to read a web page, with a renderer behind it.

    from pipelines import fetcher
    r = fetcher.fetch("https://example.ae/pergolas")          # free first
    r = fetcher.fetch(url, render=True)                       # force the browser
    r["html"], r["how"], r["cost"], r["error"]

WHY THIS EXISTS

Two problems turned out to be the same problem.

  1. Half the web is rendered by JavaScript. urllib fetches the shell — a <div
     id="root"></div> and a script tag — so the site indexer would have quietly
     filed forty blank pages and the owner would have had a search box that
     found nothing.
  2. Rival sites block plain Python. The competitor watch reads sitemaps, which
     usually survive, but a page behind Cloudflare comes back 403 and the watch
     reports "unreadable" for ever.

A rendering fetch API answers both. Zyte's does it in one call: ask for
`browserHtml` and you get the page after its JavaScript has run, from an IP
that is not your laptop.

THE ORDER MATTERS, BECAUSE THE FREE ONE IS FREE

Plain urllib first, always. It costs nothing and it works for most pages. The
paid fetch happens only when the free one FAILED, or came back looking like an
empty shell, or the caller asked for a render outright. A crawler API billed per
request will happily charge for forty pages that plain HTTP could have read.

WHAT IT WILL NOT DO

The address is checked before either path — scheme allow-list, no private or
loopback or link-local hosts. A crawler that can be pointed at 169.254.169.254
is a credential leak with a user-agent, and "it goes through Zyte" does not fix
that: the URL still comes from a sitemap a stranger wrote.
"""
import base64
import json
import re
import time
import urllib.error
import urllib.request
from contextlib import contextmanager as _ctx

try:
    from . import competitors
except ImportError:  # pragma: no cover
    import competitors

UA = "Mozilla/5.0 (compatible; glasswork/1.0; +https://github.com/)"

# WHAT A FETCH COSTS — an estimate the owner calibrates, never a quote.
#
# There is no single Zyte price to hardcode, and pretending otherwise would put
# a confident wrong number on his spend line. Their own docs: cost "depends on
# the target website and selected features", with five tiers each for HTTP and
# browser requests, plus a volume discount of 25–52%. A hard site costs several
# times what an easy one does.
#
# So the split is: the REQUEST COUNT is a fact we know exactly, and the money is
# an estimate he sets from his own invoice or Zyte's cost estimator:
#
#     ZYTE_COST_PER_FETCH=0.0001      # plain httpResponseBody
#     ZYTE_COST_PER_RENDER=0.0015     # browserHtml
#
# The defaults below are the cheapest tier — deliberately, because an estimate
# that flatters the bill is worse than none: it lets the spend cap sail past the
# real spend. Every report says "N renders" as fact and the money as "about".
DEFAULT_COST_PLAIN = 0.0001
DEFAULT_COST_RENDER = 0.0015


def _rate(name, fallback):
    try:
        from providers import integrations
        env = integrations.read_env()
    except Exception:
        env = {}
    import os as _os
    raw = (_os.environ.get(name) or env.get(name) or "").strip()
    try:
        v = float(raw)
        return v if v >= 0 else fallback
    except (TypeError, ValueError):
        return fallback


def cost_per_fetch():
    return _rate("ZYTE_COST_PER_FETCH", DEFAULT_COST_PLAIN)


def cost_per_render():
    return _rate("ZYTE_COST_PER_RENDER", DEFAULT_COST_RENDER)


# Kept as module attributes so existing callers and tests still read a number.
# Both are only ever used through the functions above at call time, so an owner
# who edits .env does not have to restart anything.
COST_PLAIN = DEFAULT_COST_PLAIN
COST_RENDER = DEFAULT_COST_RENDER


# ---------------------------------------------------------------------------
# ONE PASS, ONE FETCH
# ---------------------------------------------------------------------------
#
# site_check runs five things over the same website: can we read it, is it
# healthy, what does it sell, what would we index, how does it score for AEO.
# Each one used to crawl independently, so a single "test my website" made the
# owner's server serve the same home page five times and every service page
# three or four times — thirty-odd requests to prove one thing, and one flaky
# response anywhere in that pile turns into a step that fails for no reason.
# It happened on the first live run.
#
# So a caller can declare "this is one pass" and every repeat of the same URL
# inside it is served from memory. Deliberately opt-in and deliberately
# scoped: a long-lived cache would eventually hand somebody a stale page and
# call it live, which is a worse failure than a duplicate request.
#
#     with fetcher.cache():
#         ...                      # the same URL is fetched at most once
_CACHE = None


class _Pass:
    def __init__(self):
        self.hits = 0
        self.store = {}


@_ctx
def cache():
    """One fetch per URL for the duration of the block. Nested blocks share."""
    global _CACHE
    if _CACHE is not None:
        yield _CACHE                       # already inside one; do not nest a second
        return
    _CACHE = _Pass()
    try:
        yield _CACHE
    finally:
        _CACHE = None


def _meter(kind, usd, note=""):
    """
    Log the request to the usage meter. Never let bookkeeping break a fetch —
    if the cabinet is missing or locked, the page still gets read.
    """
    try:
        from coordination import usage
        usage.record("zyte", kind, n=1, usd=usd, note=note)
    except Exception:
        pass


def _affordable(cost):
    """
    (ok, why_not) — is there room under the owner's daily spend cap?

    Fails OPEN on a broken cabinet, deliberately. A crawler that stops working
    because the bookkeeping database is locked is a worse failure than one that
    spends a fraction of a cent it should not have; the cap is a budget, not a
    security boundary, and the spend is logged either way.
    """
    try:
        from coordination import cabinet
        cap = cabinet.daily_spend_cap()
        so_far = cabinet.spent_today()
    except Exception:
        return True, ""
    if so_far + cost <= cap:
        return True, ""
    return False, (f"the daily spend cap is spent — about ${so_far:.2f} of "
                   f"${cap:.2f} used today, and this render would add about "
                   f"${cost:.4f}. Nothing was fetched. Raise DAILY_SPEND_CAP_USD "
                   f"on /setup or let it reset at midnight.")


_TAG = re.compile(r"<[^>]+>")
_SCRIPT = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.S | re.I)
_WS = re.compile(r"\s+")

# Fingerprints of a page whose content has not arrived yet. Any one of these
# next to almost no text means we fetched the wrapper, not the page.
_SHELL_MARKS = ("__next_data__", "id=\"root\"", "id='root'", "id=\"app\"",
                "ng-app", "data-reactroot", "__nuxt__", "window.__initial")


def visible_text(html: str) -> str:
    return _WS.sub(" ", _TAG.sub(" ", _SCRIPT.sub(" ", html or ""))).strip()


# A bot-check interstitial is not the page. Cloudflare and friends answer 200
# with "One moment, please..." or "Just a moment...", and a naive reader files
# that as a real page — the owner's search index ends up holding two records
# titled "One moment, please...", which is exactly what a dry run turned up on
# his own site. Short AND carrying one of these is the test; a real page about
# glass can mention "just a moment" in a sentence without being a challenge.
_CHALLENGE_MARKS = (
    "one moment, please", "just a moment", "checking your browser",
    "cf-browser-verification", "attention required! | cloudflare",
    "enable javascript and cookies to continue", "ddos protection by",
    "verifying you are human", "please wait while we verify",
)


def looks_like_challenge(html: str) -> bool:
    """Is this a bot check rather than the page we asked for?"""
    if not html:
        return False
    low = html.lower()
    if not any(m in low for m in _CHALLENGE_MARKS):
        return False
    return len(visible_text(html)) < 1500


def looks_unrendered(html: str, min_chars: int = 400) -> bool:
    """
    Did we get the page, or the box the page arrives in?

    Two signals together, never one. Plenty of real pages are short, and plenty
    of real pages are built with React — it is a framework marker AND almost no
    readable text that means the content is still on its way.
    """
    if not html:
        return True
    text = visible_text(html)
    if len(text) >= min_chars:
        return False
    low = html.lower()
    return any(m in low for m in _SHELL_MARKS) or len(text) < 120


# ---------------------------------------------------------------------------
# THE CRAWLER SHELF
# ---------------------------------------------------------------------------

def crawler_tool():
    """The first usable API on the crawler shelf, or None."""
    try:
        from providers import store
        for t in store.tools_on_shelf("crawler"):
            if (t.get("api_key") or "").strip() and t.get("enabled", True):
                return t
    except Exception:
        pass
    return None


def _is_zyte(tool) -> bool:
    blob = ((tool.get("base_url") or "") + " " + (tool.get("name") or "")).lower()
    return "zyte" in blob


def _plain(url, timeout):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with competitors._OPENER.open(req, timeout=timeout) as r:
            ctype = (r.headers.get("Content-Type") or "").lower()
            if "html" not in ctype and "xml" not in ctype and "text" not in ctype:
                return "", f"not a text page ({ctype or 'no content-type'})", r.status
            return r.read(2_000_000).decode("utf-8", "replace"), "", r.status
    except urllib.error.HTTPError as e:
        return "", f"HTTP {e.code}", e.code
    except Exception as e:
        return "", str(e), 0


def _zyte(url, tool, render, timeout):
    """
    One Zyte API call.

    browserHtml returns the page AFTER its JavaScript has run, as a plain
    string. httpResponseBody is the raw bytes, base64-encoded — which is what
    Zyte's own starter snippet uses, and why a naive copy of it hands you
    base64 instead of HTML.
    """
    from providers import auth
    key = (tool.get("api_key") or "").strip()
    # Zyte wants the key as the HTTP Basic USERNAME with an empty password.
    # The owner pastes the bare key; the colon belongs to us, not to him.
    if not key.endswith(":"):
        key = key + ":"
    body = {"url": url, "followRedirect": True}
    body["browserHtml" if render else "httpResponseBody"] = True

    # Basic, ALWAYS — never the tool's saved auth_style. Zyte accepts nothing
    # else, so an owner who left the shelf form on its 'bearer' default sent
    # "Bearer <key>" and got a 401 that read like a bad key. The style field is
    # irrelevant for Zyte; the code knows what Zyte needs and does not ask.
    endpoint = (tool.get("base_url") or "https://api.zyte.com/v1/extract").strip()
    req = urllib.request.Request(
        endpoint, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json",
                 **auth.auth_header("basic", key)})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        return "", f"Zyte HTTP {e.code} — {detail}", e.code
    except Exception as e:
        return "", f"Zyte: {e}", 0

    if render:
        return d.get("browserHtml") or "", "", 200
    raw = d.get("httpResponseBody")
    if not raw:
        return "", "Zyte returned no body", 200
    try:
        return base64.b64decode(raw).decode("utf-8", "replace"), "", 200
    except Exception as e:
        return "", f"Zyte body would not decode: {e}", 200


# ---------------------------------------------------------------------------
# THE ONE ENTRY POINT
# ---------------------------------------------------------------------------

def fetch(url, render=False, timeout=25, allow_paid=True, min_chars=400) -> dict:
    r = _fetch_uncached(url, render, timeout, allow_paid, min_chars)

    # A FAILURE IS NEVER CACHED. This was found the hard way: the first read of
    # a run came back as a 73-character stub, the cache pinned it, and every
    # step afterwards saw a broken site. A cache that remembers failures turns
    # one flaky response into a wrong verdict about the whole website — worse
    # than the duplicate requests it exists to prevent.
    if _CACHE is not None and r.get("ok") and r.get("html"):
        _CACHE.store.setdefault((url, bool(render), bool(allow_paid)), r)
    return r


def _fetch_uncached(url, render, timeout, allow_paid, min_chars) -> dict:
    """
    Read a page. Free first, paid only when free is not enough AND permitted.

    render=False   try plain HTTP; if it fails or comes back a shell, and paid
                   fetching is permitted, render it properly
    render=True    go straight to the renderer
    allow_paid     the money switch. False means the free path is the ONLY path:
                   a shell is reported as a shell and nothing is billed. This is
                   how the owner's own site is read — see site_search.

    Returns {ok, html, how, cost, status, error, switched}.

      how        'plain' | 'crawler-render' | 'refused' | 'none'
      switched   a plain-English line when the free path FAILED and the paid one
                 took over: which one, and why. The owner asked to be told when
                 that happens rather than discovering it on an invoice, and this
                 is the only place in the system where one provider genuinely
                 substitutes for another.
      declined   set when a render WOULD have helped and money was not permitted,
                 so the caller can total up what was skipped and what it would
                 have cost instead of silently indexing empty pages.
    """
    ok, why = competitors.safe_url(url)
    if not ok:
        return {"ok": False, "html": "", "how": "refused", "cost": 0.0,
                "status": 0, "error": f"refused: {why}", "switched": ""}

    # Inside a declared pass, the same URL is read once. The key includes
    # render and allow_paid: "the free version of this page" and "the rendered
    # version" are different answers and must not be confused for each other.
    key = (url, bool(render), bool(allow_paid))
    if _CACHE is not None and key in _CACHE.store:
        _CACHE.hits += 1
        hit = dict(_CACHE.store[key])
        # The cost was already counted when it was really fetched. Billing the
        # cap again for a page we did not request would be the cache inventing
        # spend, which is worse than the duplicate request it saved.
        hit["cost"] = 0.0
        hit["from_cache"] = True
        return hit

    tool = crawler_tool() if allow_paid else None
    switched = ""

    if not render:
        html, err, status = _plain(url, timeout)
        if html and not looks_unrendered(html, min_chars):
            return {"ok": True, "html": html, "how": "plain", "cost": 0.0,
                    "status": status, "error": "", "switched": ""}

        # ONE RETRY BEFORE BLAMING JAVASCRIPT.
        #
        # A busy shared host will occasionally answer 200 with a near-empty
        # body. Measured on a real UAE site: about one request in ten during a
        # burst. Reported as-is, that becomes "your site is built by JavaScript"
        # — a wrong diagnosis whose fix is CRAWLER_ON_OWN_SITE=1, so the owner
        # switches on paid rendering and pays per page for a server hiccup.
        #
        # A genuine JavaScript site returns the same shell every time and the
        # retry costs one free request. A hiccup comes back with the real page.
        # Worth a second look before spending his money on the wrong answer.
        if err or looks_unrendered(html, min_chars) or looks_like_challenge(html):
            time.sleep(0.6)
            html2, err2, status2 = _plain(url, timeout)
            if (html2 and not looks_unrendered(html2, min_chars)
                    and not looks_like_challenge(html2)):
                return {"ok": True, "html": html2, "how": "plain", "cost": 0.0,
                        "status": status2, "error": "", "switched": "",
                        "retried": True}
            html, err, status = (html2 or html), (err2 or err), (status2 or status)

        # A PAGE THAT IS NOT THERE IS NOT A PAGE THAT NEEDS RENDERING.
        #
        # The owner's site indexed 2 of its 9 pages and reported the other 7 as
        # "built by JavaScript". They were not. They were guessed URLs —
        # /services, /about, /products — that his .php site answers with a 404,
        # and a 404 body is short, so the shell test fired. The remedy that
        # diagnosis points at is switching on paid rendering, so he would have
        # paid Zyte, per page, to render pages that do not exist.
        #
        # Rendering cannot conjure a page the server does not have. Say what it
        # is, and never put it on the "would have cost" list.
        if status in (404, 410):
            return {"ok": False, "html": "", "how": "plain", "cost": 0.0,
                    "status": status, "switched": "",
                    "error": (f"HTTP {status} — there is no page at this "
                              "address. Nothing to render.")}

        challenged = bool(html) and looks_like_challenge(html)
        shell = bool(html) and looks_unrendered(html, min_chars)
        if challenged:
            reason = ("a bot check answered instead of the page (Cloudflare or "
                      "similar). Whatever came back is an interstitial, not your "
                      "content, and must not be indexed as one")
        elif shell:
            reason = "the page is built by JavaScript and arrived empty"
        else:
            reason = f"the free fetch failed ({err or 'no response'})"

        if not allow_paid:
            # Money is off for this caller. Say what was skipped and what it
            # would have cost — never bill quietly, and never pass a shell off
            # as the page.
            return {"ok": False,
                    # A shell is handed back so the caller can see how thin it
                    # was. A bot check is NOT: it is somebody else's page, and
                    # every caller that receives html indexes or audits it.
                    "html": "" if challenged else html,
                    "how": "plain", "cost": 0.0,
                    "status": status, "switched": "",
                    "declined": {"reason": reason, "would_cost": cost_per_render()},
                    "error": (f"{reason}. Paid rendering is switched off for this, "
                              f"so it was skipped (a render would have cost about "
                              f"${cost_per_render():.4f}).")}
        if not tool:
            return {"ok": bool(html) and not shell and not challenged,
                    "html": "" if challenged else html, "how": "plain",
                    "cost": 0.0, "status": status, "switched": "",
                    "error": (err or ("the page arrived empty — it is built by "
                                      "JavaScript. Put a crawler key on the "
                                      "'crawler' shelf and this renders properly."))}
        switched = (f"free fetch gave up on {url} — {reason}. Switching to "
                    f"{tool.get('name') or 'the crawler'}.")
        render = True                      # fall through and render it

    if not tool:
        why = ("paid fetching is switched off for this caller" if not allow_paid
               else "there is no crawler API on the 'crawler' shelf — add Zyte on /setup")
        return {"ok": False, "html": "", "how": "none", "cost": 0.0, "status": 0,
                "switched": "", "error": f"a render was asked for and {why}."}
    if not _is_zyte(tool):
        return {"ok": False, "html": "", "how": "none", "cost": 0.0, "status": 0,
                "switched": switched,
                "error": (f"'{tool.get('name')}' is on the crawler shelf but only "
                          "Zyte is wired up so far. Name the tool 'Zyte API' with "
                          "base URL https://api.zyte.com/v1/extract, or ask for "
                          "another provider to be added.")}

    # RED LINE: "No money is spent above the daily cost cap without owner
    # approval." The cap lives in cabinet.borrow(), and a fetch does not borrow
    # — so until now the one genuinely metered paid call in the whole agency was
    # the one call that could sail straight past it. Checked here, at the door,
    # before the request goes out.
    afford, why_not = _affordable(cost_per_render())
    if not afford:
        return {"ok": False, "html": "", "how": "capped", "cost": 0.0,
                "status": 0, "switched": switched,
                "declined": {"reason": why_not, "would_cost": cost_per_render()},
                "error": why_not}

    html, err, status = _zyte(url, tool, render=True, timeout=max(timeout, 40))
    # Zyte charges for SUCCESSFUL responses only, so a failed render must not
    # land on the spend line. Billing ourselves for their failures would inflate
    # every report and trip the daily cap on requests nobody was invoiced for.
    # The REQUEST still gets counted — a hundred failing renders is a fact the
    # owner needs to see even though it costs him nothing.
    if err:
        _meter("render", usd=0.0, note=f"failed: {err[:80]}")
        return {"ok": False, "html": "", "how": "crawler-render", "cost": 0.0,
                "status": status, "switched": switched, "error": err}
    if not html:
        _meter("render", usd=0.0, note="empty page returned")
        return {"ok": False, "html": "", "how": "crawler-render", "cost": 0.0,
                "status": status, "switched": switched,
                "error": "the renderer returned an empty page"}
    _meter("render", usd=cost_per_render(), note=url[:120])
    return {"ok": True, "html": html, "how": "crawler-render",
            "cost": cost_per_render(), "status": status, "switched": switched,
            "error": ""}


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Read one page, the way the agency does.")
    ap.add_argument("url")
    ap.add_argument("--render", action="store_true")
    a = ap.parse_args()
    r = fetch(a.url, render=a.render)
    r["html"] = (r["html"] or "")[:400]
    print(json.dumps(r, indent=2))
