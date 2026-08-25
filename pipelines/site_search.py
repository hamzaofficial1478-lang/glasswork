"""
site_search.py — search on the owner's own website, powered by Algolia.

    python -m pipelines.site_search --dry-run     read the site, build the records,
                                                  push nothing
    python -m pipelines.site_search               read, push to Algolia, queue the
                                                  widget for approval
    python -m pipelines.site_search --widget      just print the paste-in snippet

WHAT THIS IS FOR

A visitor types "shower glass" into the site and lands on the shower-glass page
instead of scrolling a menu. For a trade site with a page per service per area,
that is the difference between an enquiry and a bounce — and it is the one thing
an Algolia account is genuinely good at. It is NOT a crawler: it cannot read a
competitor's site, and nothing here pretends otherwise.

THREE KEYS, AND ONLY ONE OF THEM MAY EVER REACH A BROWSER

  ALGOLIA_APP_ID        public. Identifies the account.
  ALGOLIA_SEARCH_KEY    public BY DESIGN. Read-only. This is the one that goes
                        into the widget, because a search box has to query from
                        the visitor's browser.
  ALGOLIA_WRITE_KEY     SECRET. Adds, changes and deletes every record in the
                        index. It stays on the server, in .env, and this module
                        refuses to emit a widget that contains it — see
                        widget_html(), which checks rather than trusts itself.

Putting the write key in front-end JavaScript is the classic Algolia mistake and
it hands anyone who views source the ability to wipe the index. The check is a
hard failure, not a warning.

WHAT TOUCHES WHAT

This writes to ALGOLIA, which is the agency's own account — that is not the live
website and needs no release. The widget is a change to the SITE, so it is
queued for the owner exactly like any other: written down, waiting, never
applied from here. Only the IT Releaser goes near production.
"""
import argparse
import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request

from coordination import board, site

try:
    from . import fetcher, harvest, services, site_services
except ImportError:  # pragma: no cover
    import fetcher
    import harvest
    import services
    import site_services

AGENT = "it-search"
DEFAULT_INDEX = "glasswork_site"
UA = "Mozilla/5.0 (compatible; glasswork-site-search/1.0)"

_TAG = re.compile(r"<[^>]+>")
_SCRIPT = re.compile(r"<(script|style|nav|footer|svg)[^>]*>.*?</\1>", re.S | re.I)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_H = re.compile(r"<h([12345])[^>]*>(.*?)</h\1>", re.S | re.I)
_WS = re.compile(r"\s+")


def _cfg(env=None):
    """Algolia's four settings, from .env. Nothing here is guessed."""
    if env is None:
        try:
            from providers import integrations
            env = integrations.read_env()
        except Exception:
            env = {}
    g = lambda k: (os.environ.get(k) or env.get(k) or "").strip()
    return {"app_id": g("ALGOLIA_APP_ID"),
            "search_key": g("ALGOLIA_SEARCH_KEY"),
            "write_key": g("ALGOLIA_WRITE_KEY"),
            "index": g("ALGOLIA_INDEX") or DEFAULT_INDEX}


def _text(html):
    html = _SCRIPT.sub(" ", html or "")
    return _WS.sub(" ", _TAG.sub(" ", html)).strip()


# ---------------------------------------------------------------------------
# 1. READ THE SITE
# ---------------------------------------------------------------------------

# THE OWNER'S OWN SITE IS READ FOR FREE, BY DEFAULT.
#
# His point, and he is right: Algolia is free and Zyte is billed per request.
# Indexing forty of his own pages every day through a paid renderer is a
# standing charge for something plain HTTP does for nothing — and the pages are
# HIS, on a server he controls, so there is no blocking to get past. Zyte earns
# its money on RIVAL sites, which is where the blocking actually is.
#
# So the money switch is OFF here unless he turns it on. If some of his pages
# turn out to be JavaScript-built, they are reported and skipped with the price
# named, rather than quietly billed. One line in .env changes his mind:
#
#     CRAWLER_ON_OWN_SITE=1
#
# The default is the cheap one because a default nobody chose should never be
# the one that spends money.
def paid_allowed_on_own_site(env=None) -> bool:
    if env is None:
        try:
            from providers import integrations
            env = integrations.read_env()
        except Exception:
            env = {}
    v = (os.environ.get("CRAWLER_ON_OWN_SITE")
         or env.get("CRAWLER_ON_OWN_SITE") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _fetch(url, timeout=15, allow_paid=None):
    """
    One page of the owner's own site. Free HTTP, and paid rendering only if he
    has switched it on.

    Returns (html, error). Cost, route and anything skipped are totalled in
    _LAST so build_records can report them without threading extra values
    through every caller.
    """
    if allow_paid is None:
        allow_paid = paid_allowed_on_own_site()
    r = fetcher.fetch(url, timeout=timeout, allow_paid=allow_paid)
    _LAST["cost"] += r["cost"]
    if r["how"] == "crawler-render":
        _LAST["rendered"] += 1
        if r.get("switched"):
            _LAST["switched"].append(r["switched"])
    if r.get("declined"):
        _LAST["skipped"].append(url)
        _LAST["would_cost"] += r["declined"]["would_cost"]
    return r["html"], r["error"]


# Running totals for one build_records pass. Reset at the top of it, never read
# across passes — a module-level counter that nobody resets is a slow lie.
_LAST = {"cost": 0.0, "rendered": 0, "skipped": [], "would_cost": 0.0,
         "switched": []}


# Not a page: a picture, a stylesheet, a download. site_services' link reader
# is deliberately narrow — it wants the pages that describe what we sell — but
# a search index wants the whole site, so this one takes everything except the
# things that are not pages at all.
_NOT_A_PAGE = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico",
               ".css", ".js", ".pdf", ".zip", ".mp4", ".webm", ".woff",
               ".woff2", ".ttf", ".xml", ".json", ".txt")


def _unescape(s):
    """'Glass &amp; Aluminium' is not what anybody typed into a search box."""
    import html as _htmlmod
    return _htmlmod.unescape(s or "")


def _headline(title):
    """
    The part of a page title that is about THIS page.

    A trade site puts the whole business in every title — 'FAQ | Glass,
    Aluminium, Sign Board & Wooden Work Dubai | World Deal Technical Services'.
    Only the first segment says what the page is.
    """
    for sep in ("|", "–", "—", " - ", "::"):
        if sep in (title or ""):
            head = title.split(sep)[0].strip()
            if len(head) >= 3:
                return head
    return title or ""


def _repeated_headings(read, share=0.6):
    """
    The headings that belong to the template rather than to any one page.

    A nav block, a footer service list, a 'Color Switcher' widget. Judged by
    how many pages carry them, not by a list of words to ignore — his site's
    boilerplate is '01. Glass Installation', which is a perfectly good heading
    on the page that is actually about it.

    Needs three pages before it will call anything boilerplate. On two pages
    "appears on both" is as likely to mean "they are both about glass".
    """
    if len(read) < 3:
        return set()
    counts = {}
    for _u, _h, _t, heads in read:
        for h in {x.strip().lower() for x in heads if x.strip()}:
            counts[h] = counts.get(h, 0) + 1
    need = max(2, int(len(read) * share))
    return {h for h, n in counts.items() if n >= need}


def _fingerprint(html):
    """
    What this page SAYS, ignoring how it was reached.

    '/' and '/index.php' are one page served under two addresses, and indexing
    both puts the home page in every result list twice.
    """
    import hashlib
    return hashlib.sha1(_text(html)[:4000].encode("utf-8", "replace")).hexdigest()


def own_pages(html, base, limit=40):
    """
    Every page of his own site the home page links to, in nav order.

    Uses harvest.links, which reads the href attribute rather than pattern-
    matching whitespace-delimited text — the same reader that found the
    /contact.php no guess list contained.
    """
    host = urllib.parse.urlparse(base).netloc.lower()
    out = []
    for u, _text_ in harvest.links(html or "", base):
        p = urllib.parse.urlparse(u)
        if p.netloc.lower() != host:
            continue
        path = (p.path or "/").lower()
        if path.endswith(_NOT_A_PAGE):
            continue
        clean = f"{p.scheme}://{p.netloc}{p.path}"
        if clean.rstrip("/") == base.rstrip("/"):
            continue                       # the home page is already read
        if clean not in out:
            out.append(clean)
    return out[:limit]


def build_records(url, max_pages=40, timeout=15):
    """
    One search record per page of the site.

    objectID is the path, not a counter. That is what makes a re-index an
    UPDATE rather than a second copy of the whole site: run this daily and the
    index tracks the site instead of growing by 40 records a day.
    """
    _LAST.update({"cost": 0.0, "rendered": 0, "skipped": [], "would_cost": 0.0,
                  "switched": []})
    if not url:
        return [], "no website saved — put it on /setup first"
    if "://" not in url:
        url = "https://" + url
    base = url.rstrip("/")

    home, err = _fetch(base, timeout)
    if not home:
        return [], f"could not read {base} — {err or 'no response'}"

    # THE SITE'S OWN LINKS FIRST, GUESSES ONLY IF IT LINKED NOWHERE.
    #
    # This read the guess list first and appended the real links after it. On
    # the owner's own site that indexed /services, /our-services, /what-we-do,
    # /products, /solutions, /about and /about-us — seven 404s — and ran out of
    # page budget before it reached /service.php, /about.php, /gallery.php,
    # /blog.php, /faq.php and /contact.php, which are the pages he actually
    # has and which his home page links to in the navigation bar.
    #
    # Two of nine pages were indexed and the other seven were reported as
    # JavaScript. The site had told us where everything was; we were not
    # listening. Same fix as harvest.py, which stopped guessing for the same
    # reason and found the /contact.php no guess list contained.
    seen = [base]
    queue = own_pages(home, base)
    linked = len(queue)
    queue += [base + p for p in getattr(site_services, "LIKELY_PATHS", ())
              if base + p not in queue]

    records, pages, bodies = [], [(base, home)], {_fingerprint(home)}
    for u in queue:
        if u in seen or len(seen) >= max_pages:
            continue
        html, _e = _fetch(u, timeout)
        if not html:
            continue
        # '/' and '/index.php' are the same page served twice, and indexing
        # both puts the home page into every result list twice. Judged on the
        # text, not on a list of filenames, so it also catches /home.html and
        # whatever else his host decides to alias.
        fp = _fingerprint(html)
        if fp in bodies:
            continue
        bodies.add(fp)
        seen.append(u)
        pages.append((u, html))
        time.sleep(0.25)                   # his own server, but still be polite

    read = []
    for u, html in pages:
        t = _TITLE.search(html)
        title = _text(t.group(1)) if t else u.rsplit("/", 1)[-1].replace("-", " ").title()
        heads = [_unescape(_text(m.group(2))) for m in _H.finditer(html)][:12]
        read.append((u, html, _unescape(title), heads))

    # A HEADING ON EVERY PAGE DESCRIBES THE SITE, NOT THE PAGE.
    #
    # His site carries the same service list — '01. Glass Installation',
    # '02. Aluminum Installation' — in a block on every single page, so the
    # contact page, the gallery and the FAQ all matched 'glass installation'
    # and the service field said the same thing seven times. That field exists
    # so a search for 'pergola' ranks the pergola page above a passing mention
    # on the home page, and identical values make it worth nothing.
    boilerplate = _repeated_headings(read)

    for u, html, title, heads in read:
        own = [h for h in heads if h.strip().lower() not in boilerplate]
        body = _text(html)
        # Matched on the DISTINCTIVE part of the title and this page's OWN
        # headings. Nothing left to match on means no service, which is the
        # honest answer for a contact page.
        svc, _why = services.match_by_name(
            " ".join([_headline(title)] + own)) or ("", "")
        path = urllib.parse.urlparse(u).path or "/"
        records.append({
            "objectID": path,
            "url": u,
            "path": path,
            "title": title[:200],
            # This page's own headings, not the navigation repeated seven
            # times. Algolia ranks on these, so the site-wide block being in
            # every record would have made every record look alike.
            "headings": own or heads,
            "service": svc or "",
            "service_label": services.label(svc) if svc else "",
            # Enough to match on and to show a snippet. The whole page would
            # blow past Algolia's record size limit on a long service page.
            "body": body[:7000],
        })
    return records, ""


# ---------------------------------------------------------------------------
# 2. PUSH TO ALGOLIA  (the agency's own account — not the website)
# ---------------------------------------------------------------------------

def _algolia(method, path, cfg, body=None, key=None, timeout=20):
    host = f"{cfg['app_id']}.algolia.net"
    req = urllib.request.Request(
        f"https://{host}{path}",
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers={"X-Algolia-API-Key": key or cfg["write_key"],
                 "X-Algolia-Application-Id": cfg["app_id"],
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace")), ""
    except urllib.error.HTTPError as e:
        return {}, f"HTTP {e.code} — {e.read().decode('utf-8', 'replace')[:200]}"
    except Exception as e:
        return {}, str(e)


# What Algolia should match on, in order. Title first or a search for "pergola"
# returns the home page (which mentions it once) above the pergola page.
INDEX_SETTINGS = {
    "searchableAttributes": ["title", "service_label", "unordered(headings)",
                             "unordered(body)"],
    "attributesForFaceting": ["service"],
    "attributesToSnippet": ["body:30"],
    "customRanking": ["asc(path)"],
}


def _meter(kind, n=1, note="", gauge=False):
    """
    Tell the usage meter. Never let bookkeeping break an index run.

    gauge=True records a LEVEL (how many records the index holds now) rather
    than an addition — readers take the latest, not the sum.
    """
    try:
        from coordination import usage
        if gauge:
            usage.set_gauge("algolia", kind, n, note=note)
        else:
            usage.record("algolia", kind, n=n, usd=0.0, note=note)
    except Exception:
        pass


def configure(cfg):
    out, err = _algolia("PUT", f"/1/indexes/{urllib.parse.quote(cfg['index'])}/settings",
                        cfg, INDEX_SETTINGS)
    if not err:
        _meter("write", n=1, note="index settings")
    return out, err


def push(records, cfg):
    if not records:
        return {}, "nothing to push"
    body = {"requests": [{"action": "updateObject", "body": r} for r in records]}
    out, err = _algolia("POST", f"/1/indexes/{urllib.parse.quote(cfg['index'])}/batch",
                        cfg, body)
    if not err:
        # Two different numbers, deliberately. The WRITE count is how many
        # operations we sent this month; the RECORD count is how big the index
        # is right now. objectID is the page path, so re-indexing the same
        # forty pages updates forty records rather than adding forty more —
        # summing the pushes would have the owner "over" 50,000 records inside
        # a month while his index still held forty.
        _meter("write", n=len(records), note=cfg.get("index") or "")
        _meter("record", n=len(records), gauge=True, note=cfg.get("index") or "")
    return out, err


# ---------------------------------------------------------------------------
# 3. THE WIDGET  (a change to the SITE, so it is queued, never applied)
# ---------------------------------------------------------------------------

WIDGET = """<!-- Glassworks site search — paste this where you want the box.
     Safe to publish: the key below is Algolia's SEARCH-ONLY key, which is
     designed to sit in a browser. It can read the index and nothing else.
     Never replace it with the write key. -->
<div id="gw-search">
  <input id="gw-q" type="search" placeholder="Search: shower glass, pergola, partitions…"
         autocomplete="off" aria-label="Search this site">
  <ul id="gw-results" hidden></ul>
</div>
<style>
  #gw-search{position:relative;max-width:520px;margin:0 auto;font-family:inherit}
  #gw-q{width:100%;padding:13px 16px;font-size:16px;border:1px solid #ccd6da;
        border-radius:10px;box-sizing:border-box}
  #gw-q:focus{outline:none;border-color:#0b7285;box-shadow:0 0 0 3px rgba(11,114,133,.15)}
  #gw-results{position:absolute;z-index:99;left:0;right:0;margin:6px 0 0;padding:6px;
    list-style:none;background:#fff;border:1px solid #ccd6da;border-radius:10px;
    box-shadow:0 12px 28px rgba(0,0,0,.14);max-height:60vh;overflow:auto}
  #gw-results li a{display:block;padding:10px 12px;border-radius:7px;color:#12333c;
    text-decoration:none}
  #gw-results li a:hover,#gw-results li a:focus{background:#eef6f8}
  #gw-results b{display:block;font-size:15px;margin-bottom:2px}
  #gw-results span{font-size:13px;color:#5a6b72}
  #gw-results em{background:#fdf3d0;font-style:normal}
</style>
<script>
(function(){
  var APP="__APP_ID__", KEY="__SEARCH_KEY__", INDEX="__INDEX__";
  var q=document.getElementById('gw-q'), out=document.getElementById('gw-results'), t;
  function hide(){ out.hidden=true; out.innerHTML=''; }
  function run(){
    var term=q.value.trim();
    if(term.length<2){ hide(); return; }
    fetch('https://'+APP+'-dsn.algolia.net/1/indexes/'+encodeURIComponent(INDEX)+'/query',{
      method:'POST',
      headers:{'X-Algolia-API-Key':KEY,'X-Algolia-Application-Id':APP,
               'Content-Type':'application/json'},
      body:JSON.stringify({query:term,hitsPerPage:6,attributesToSnippet:['body:28']})
    }).then(function(r){return r.json();}).then(function(d){
      var hits=(d&&d.hits)||[];
      if(!hits.length){ out.innerHTML='<li><a>Nothing found for &ldquo;'+
        term.replace(/[<>&]/g,'')+'&rdquo;</a></li>'; out.hidden=false; return; }
      out.innerHTML=hits.map(function(h){
        var s=(h._snippetResult&&h._snippetResult.body&&h._snippetResult.body.value)||'';
        return '<li><a href="'+h.url+'"><b>'+(h.title||h.path)+'</b><span>'+s+'</span></a></li>';
      }).join('');
      out.hidden=false;
    }).catch(hide);
  }
  q.addEventListener('input',function(){ clearTimeout(t); t=setTimeout(run,180); });
  q.addEventListener('blur',function(){ setTimeout(hide,180); });
})();
</script>"""


class WriteKeyLeak(Exception):
    """Raised rather than returning a widget that would publish the write key."""


def widget_html(cfg):
    """
    The paste-in snippet, with the SEARCH key and only the search key.

    The final check is deliberately not "I was careful above". It looks at the
    finished string for the write key and raises if it is in there. Careful is
    what everybody is right up until the afternoon they refactor this function.
    """
    if not cfg["app_id"] or not cfg["search_key"]:
        raise ValueError("no Algolia app id / search key saved")
    html = (WIDGET.replace("__APP_ID__", cfg["app_id"])
                  .replace("__SEARCH_KEY__", cfg["search_key"])
                  .replace("__INDEX__", cfg["index"]))
    wk = (cfg.get("write_key") or "").strip()
    if wk and wk in html:
        raise WriteKeyLeak(
            "the write key ended up in the widget — refusing to hand that over. "
            "Anyone viewing source could wipe the index. Check ALGOLIA_SEARCH_KEY "
            "and ALGOLIA_WRITE_KEY are not set to the same value.")
    return html


# ---------------------------------------------------------------------------
# THE RUN
# ---------------------------------------------------------------------------

def critic_check(records, cfg):
    reasons = []
    if not records:
        reasons.append("no pages were read, so there is nothing to search")
    if records and all(len(r["body"]) < 80 for r in records):
        reasons.append(
            "every page came back nearly empty — the site is built by JavaScript, "
            "so there is nothing to search. Either it needs rendering "
            "(CRAWLER_ON_OWN_SITE=1, billed per page) or the site should serve "
            "its content in the HTML, which is free and better for Google too")
    if len({r["objectID"] for r in records}) != len(records):
        reasons.append("two records share an objectID; a re-index would silently "
                       "overwrite one page with another")
    if cfg["search_key"] and cfg["search_key"] == cfg["write_key"]:
        reasons.append("the search key and the write key are the same value — that "
                       "would publish write access in the widget")
    return (len(reasons) == 0, reasons)


def run(agent=AGENT, dry_run=False, max_pages=40, url=None) -> dict:
    with board.agent_run(agent) as run_row:
        out = _work(agent, dry_run, max_pages, url)
        run_row.note(out.get("summary") or out.get("error") or "site search pass")
        return out


def _work(agent, dry_run, max_pages, url=None) -> dict:
    cfg = _cfg()
    # An explicit address wins, so a one-off test of a staging site or
    # somebody else's does not need the saved one changed and changed back.
    url = site.site_url(url or "")
    missing = [k for k in ("app_id", "search_key", "write_key") if not cfg[k]]
    if missing and not dry_run:
        return {"ok": False, "error":
                "Algolia is not set up — missing " + ", ".join(
                    "ALGOLIA_" + m.upper() for m in missing) +
                ". Add them on /setup (Connectors), then run this again. The app id "
                "and search key are safe to publish; the write key is not."}

    records, err = build_records(url, max_pages=max_pages)
    if err:
        return {"ok": False, "error": err}

    ok, reasons = critic_check(records, cfg)
    skipped = list(_LAST["skipped"])
    result = {"ok": True, "agent": agent, "site": url,
              "pages_indexed": len(records),
              "pages_rendered": _LAST["rendered"],
              "crawler_cost": round(_LAST["cost"], 4),
              "paid_rendering": paid_allowed_on_own_site(),
              "services_matched": len({r["service"] for r in records if r["service"]}),
              "dry_run": bool(dry_run)}
    if _LAST["switched"]:
        # The owner asked to be told when one provider takes over from another.
        # This is the only place in the system where that genuinely happens.
        result["switched_to_crawler"] = _LAST["switched"][:5]
    if skipped:
        result["pages_skipped"] = skipped[:20]
        result["skipped_would_cost"] = round(_LAST["would_cost"], 4)
        result["skipped_note"] = (
            f"{len(skipped)} page(s) on your own site are built by JavaScript and "
            f"were NOT indexed, because paid rendering is off for your own site "
            f"(Algolia is free, Zyte is billed per page). Rendering them would "
            f"cost about ${_LAST['would_cost']:.4f} per run. To allow it, put "
            f"CRAWLER_ON_OWN_SITE=1 in .env. Zyte stays on for rival sites either way.")
    if not ok:
        result.update({"critic": "rejected", "critic_reasons": reasons,
                       "pushed": 0,
                       "summary": "critic rejected: " + "; ".join(reasons)})
        return result
    result["critic"] = "passed"

    if dry_run:
        result["sample"] = [{"objectID": r["objectID"], "title": r["title"],
                             "service": r["service_label"]} for r in records[:8]]
        result["summary"] = (
            f"{len(records)} page(s) ready to index from {url}"
            + (f" ({_LAST['rendered']} needed rendering, ${_LAST['cost']:.4f})"
               if _LAST["rendered"] else "")
            + "; nothing was pushed")
        return result

    # Writing to OUR OWN Algolia index is not a site change and needs no release.
    _s, serr = configure(cfg)
    _p, perr = push(records, cfg)
    if perr:
        return {**result, "ok": False, "pushed": 0,
                "error": f"Algolia refused the batch: {perr}"}
    result["settings_error"] = serr or None
    result["pushed"] = len(records)

    # The WIDGET is a change to the live site, so it is queued and waits for him.
    try:
        html = widget_html(cfg)
    except (ValueError, WriteKeyLeak) as e:
        return {**result, "widget": None, "widget_error": str(e),
                "summary": f"indexed {len(records)} page(s); widget NOT produced — {e}"}

    change_id = board.queue_site_change(agent, "add_search_widget", {
        "where": "paste into the header or the top of the home page",
        "bytes": len(html),
        "uses_key": "search-only (safe to publish)",
        "html": html,
    })
    did = board.log_decision(
        "site-search",
        reasoning=(f"Indexed {len(records)} page(s) of {url} into Algolia so a visitor "
                   f"can type a service name instead of hunting the menu. On a site "
                   f"with a page per service per area, the menu is the bounce."),
        expectation="Visitors search for a service by name and land on that page; "
                    "the enquiry rate off search beats the rate off the menu.",
        confidence=0.65,
        evidence={"pages": [r["objectID"] for r in records],
                  "site_queue_id": change_id})
    result.update({"widget_queued": change_id, "decision_id": did,
                   "summary": (f"indexed {len(records)} page(s) into Algolia"
                               + (f" ({_LAST['rendered']} rendered, ${_LAST['cost']:.4f})"
                                  if _LAST["rendered"] else "")
                               + f"; the search box is queued for your approval "
                                 f"(change #{change_id})")})
    return result


def main():
    ap = argparse.ArgumentParser(description="Search on your own website, via Algolia.")
    ap.add_argument("--dry-run", action="store_true", help="read and build, push nothing")
    ap.add_argument("--widget", action="store_true", help="print the paste-in snippet")
    ap.add_argument("--max-pages", type=int, default=40)
    a = ap.parse_args()
    if a.widget:
        try:
            print(widget_html(_cfg()))
            return 0
        except (ValueError, WriteKeyLeak) as e:
            raise SystemExit(str(e))
    print(json.dumps(run(dry_run=a.dry_run, max_pages=a.max_pages), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
