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

try:
    from . import competitors
except ImportError:  # pragma: no cover
    import competitors

UA = "Mozilla/5.0 (compatible; glasswork/1.0; +https://github.com/)"

# Zyte bills per request. Published pricing moves, so this is only used to put a
# number on the run's spend line and to let the cost ceiling bite — it is not a
# quote. A render costs more than a plain fetch because it runs a browser.
COST_PLAIN = 0.0002
COST_RENDER = 0.0015

_TAG = re.compile(r"<[^>]+>")
_SCRIPT = re.compile(r"<(script|style|noscript)[^>]*>.*?</\1>", re.S | re.I)
_WS = re.compile(r"\s+")

# Fingerprints of a page whose content has not arrived yet. Any one of these
# next to almost no text means we fetched the wrapper, not the page.
_SHELL_MARKS = ("__next_data__", "id=\"root\"", "id='root'", "id=\"app\"",
                "ng-app", "data-reactroot", "__nuxt__", "window.__initial")


def visible_text(html: str) -> str:
    return _WS.sub(" ", _TAG.sub(" ", _SCRIPT.sub(" ", html or ""))).strip()


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

    tool = crawler_tool() if allow_paid else None
    switched = ""

    if not render:
        html, err, status = _plain(url, timeout)
        if html and not looks_unrendered(html, min_chars):
            return {"ok": True, "html": html, "how": "plain", "cost": 0.0,
                    "status": status, "error": "", "switched": ""}

        shell = bool(html) and looks_unrendered(html, min_chars)
        reason = ("the page is built by JavaScript and arrived empty" if shell
                  else f"the free fetch failed ({err or 'no response'})")

        if not allow_paid:
            # Money is off for this caller. Say what was skipped and what it
            # would have cost — never bill quietly, and never pass a shell off
            # as the page.
            return {"ok": False, "html": html, "how": "plain", "cost": 0.0,
                    "status": status, "switched": "",
                    "declined": {"reason": reason, "would_cost": COST_RENDER},
                    "error": (f"{reason}. Paid rendering is switched off for this, "
                              f"so it was skipped (a render would have cost about "
                              f"${COST_RENDER:.4f}).")}
        if not tool:
            return {"ok": bool(html) and not shell, "html": html, "how": "plain",
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

    html, err, status = _zyte(url, tool, render=True, timeout=max(timeout, 40))
    cost = COST_RENDER
    if err:
        return {"ok": False, "html": "", "how": "crawler-render", "cost": cost,
                "status": status, "switched": switched, "error": err}
    return {"ok": bool(html), "html": html, "how": "crawler-render", "cost": cost,
            "status": status, "switched": switched,
            "error": "" if html else "the renderer returned an empty page"}


def fetch_many(urls, render=False, timeout=25, pause=0.25, allow_paid=True):
    """Several pages, with the same rules and a running cost."""
    out, spent = [], 0.0
    for u in urls:
        r = fetch(u, render=render, timeout=timeout, allow_paid=allow_paid)
        spent += r["cost"]
        out.append((u, r))
        if r["how"] == "plain":
            time.sleep(pause)              # only the free path needs manners
    return out, round(spent, 4)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="Read one page, the way the agency does.")
    ap.add_argument("url")
    ap.add_argument("--render", action="store_true")
    a = ap.parse_args()
    r = fetch(a.url, render=a.render)
    r["html"] = (r["html"] or "")[:400]
    print(json.dumps(r, indent=2))
