"""
site_check.py — the first real run against your live website.

    python -m pipelines.site_check            read everything, change nothing
    python -m pipelines.site_check --index    also push the search index for real
    python -m pipelines.site_check --json     the same, as data

WHY THIS EXISTS

Everything needed to test a real website was already here, in five separate
commands with an order you had to know: read the site, check its health, see
what it says you sell, dry-run the search index, audit it for AEO and GEO. Miss
one and you get a partial answer. Run them out of order and the audit has
nothing to audit.

The first time you point this at a live site is exactly the moment you should
not be remembering a running order. So this is one command that does them in
sequence and says, in plain words, what is working and what is not.

READ-ONLY UNTIL YOU SAY OTHERWISE

Nothing here writes to your website, and by default nothing writes to Algolia
either. The search index is a DRY RUN unless you pass --index: it tells you how
many pages it would file and shows you a sample, and you decide. A first test
that quietly pushes forty records into a live index is not a test.

The one thing that is never automatic, with or without --index, is the search
BOX on your website. That is a change to the live site, so it is queued for your
approval like every other one — IT is the only door to production.

WHAT IT WILL NOT DO

It will not tell you the site is fine because the checks it could not run
happened to be the important ones. Every step reports ran / passed / failed
separately, and a step that could not run says why and what would turn it on.
"""
import argparse
import json
import re
import time

from coordination import board, site as site_cfg

AGENT = "it-watcher"

OK, WARN, FAIL, SKIP = "ok", "warn", "fail", "skip"
_MARK = {OK: "ok  ", WARN: "warn", FAIL: "FAIL", SKIP: "--  "}


def _step(name, state, says, **extra):
    return {"name": name, "state": state, "says": says, **extra}


# ---------------------------------------------------------------------------
# THE STEPS
# ---------------------------------------------------------------------------

def step_address(override=""):
    """
    Which address is being tested, and where it came from.

    The override matters: passing --url and then being told "not set" is the
    check contradicting the command that ran it, which is exactly what happened
    the first time this ran against a live site.
    """
    if override:
        return _step("Your website address", OK,
                     f"{override} (given on the command line, not the saved one)",
                     url=override)
    s = site_cfg.status()
    if not s["url"]:
        return _step("Your website address", FAIL,
                     "not set. Looked in " + ", ".join(s.get("looked_in", []))
                     + ". " + s.get("hint", ""))
    return _step("Your website address", OK,
                 f"{s['url']} (read from {s['source']})", url=s["url"])


def step_readable(url):
    """Can we fetch it at all? Everything downstream depends on this one."""
    from . import fetcher
    t0 = time.time()
    r = fetcher.fetch(url, allow_paid=False)
    ms = int((time.time() - t0) * 1000)
    if not r["html"]:
        return _step("Can the agency read it", FAIL,
                     f"no. {r['error'] or 'no response'}")
    text = len(fetcher.visible_text(r["html"]))
    if r.get("declined") or text < 400:
        return _step("Can the agency read it", WARN,
                     f"the page loads in {ms}ms but only {text} characters of "
                     f"readable text came back — it is probably built by "
                     f"JavaScript. Google reads it the same way this does, so "
                     f"this is worth fixing for search, not just for us.",
                     chars=text)
    return _step("Can the agency read it", OK,
                 f"yes — {ms}ms, {text:,} characters of readable text", chars=text)


def step_health(url):
    """
    Reachability, HTTPS, speed, mobile viewport, and — the one that matters —
    whether a visitor can contact this business at all.

    it_health.run() is NOT used here, deliberately. The Watcher works from the
    board: it claims an open IT task or refuses, which is exactly right for an
    agent and exactly wrong for a diagnostic. Calling it would either return
    "no open IT task to claim" or force this file to post a task on IT's behalf
    and pretend to be the MD. Its read-only check functions are public, so they
    are called directly and nothing is filed.
    """
    from . import it_health
    status, html, ms, err = it_health._fetch(url)
    if not html and not status:
        return _step("Site health", FAIL, f"no response: {err or 'unreachable'}")

    findings = it_health.check_health(status, ms, url, err)
    findings += it_health.check_hygiene(html or "")
    # The contact page too, not just the home page. Judging the enquiry path on
    # the home page alone told this owner his site was "missing a contact form"
    # when the form is at /contact.php, which is exactly where it belongs.
    contact_html, _u = it_health._contact_page(url, html or "")
    enquiry, routes = it_health.check_enquiry_path(html or "", contact_html)
    findings += enquiry

    crit = [f for f in findings if f["severity"] == "critical"]
    high = [f for f in findings if f["severity"] == "high"]
    ways = ", ".join(k for k, _ in routes) or "none"
    if crit:
        return _step("Site health", FAIL,
                     f"{crit[0]['detail']} Nothing else on this list is worth as "
                     f"much as a working enquiry path.",
                     issues=len(findings), critical=len(crit), routes=ways)
    if high:
        return _step("Site health", WARN,
                     f"HTTP {status} in {ms}ms. Ways to contact you: {ways}. "
                     f"{len(high)} high-severity issue(s): {high[0]['detail']}",
                     issues=len(findings), critical=0, routes=ways)
    if findings:
        return _step("Site health", WARN,
                     f"HTTP {status} in {ms}ms. Ways to contact you: {ways}. "
                     f"{len(findings)} minor issue(s): {findings[0]['detail']}",
                     issues=len(findings), critical=0, routes=ways)
    return _step("Site health", OK,
                 f"HTTP {status} in {ms}ms, nothing wrong found. Ways to contact "
                 f"you: {ways}.", issues=0, critical=0, routes=ways)


def step_services(url):
    from . import site_services
    try:
        out = site_services.run(url=url)
    except Exception as e:
        return _step("What your site says you sell", FAIL, f"could not read it: {e}")
    if not out.get("ok"):
        return _step("What your site says you sell", FAIL,
                     out.get("error") or "could not read it")
    confirmed = out.get("confirmed") or {}
    unknown = out.get("uncatalogued") or []
    absent = out.get("missing_from_site") or []
    says = (f"{len(confirmed)} catalogued service(s) found on the site"
            + (f"; {len(absent)} the agency knows about are NOT mentioned anywhere"
               if absent else ""))
    if unknown:
        return _step("What your site says you sell", WARN,
                     says + f". And {len(unknown)} thing(s) on your site the agency "
                     f"does not know: {', '.join(map(str, unknown[:6]))}. Until "
                     f"those are catalogued it cannot target or write about them.",
                     known=len(confirmed), unknown=list(unknown)[:12],
                     unknown_total=len(unknown))
    return _step("What your site says you sell", OK, says + ".",
                 known=len(confirmed), unknown=[])


def step_search(url, do_index, max_pages):
    from . import site_search
    try:
        out = site_search.run(dry_run=not do_index, max_pages=max_pages, url=url)
    except Exception as e:
        return _step("Search on your own site", FAIL, f"the index pass failed: {e}")
    if not out.get("ok"):
        return _step("Search on your own site", FAIL, out.get("error") or "failed")
    if out.get("critic") == "rejected":
        return _step("Search on your own site", FAIL,
                     "refused: " + "; ".join(out.get("critic_reasons", [])))

    n = out.get("pages_indexed", 0)
    skipped = out.get("pages_skipped") or []
    extra = {"pages": n, "skipped": len(skipped), "sample": out.get("sample", [])[:6]}
    if do_index:
        says = (f"{out.get('pushed', 0)} page(s) pushed to Algolia. The index "
                f"exists now — the 'index does not exist yet' message on /setup "
                f"will stop.")
        if out.get("queued_change") or out.get("change_id"):
            says += (" The search BOX for your website is queued for your approval "
                     "— it is a change to the live site, so nothing was published.")
    else:
        says = (f"{n} page(s) ready to index. Nothing was pushed — this was a dry "
                f"run. Add --index when you want the index built for real.")
    if skipped:
        says += (f" {len(skipped)} page(s) were skipped as JavaScript shells; "
                 f"{out.get('skipped_note', '')[:160]}")
        return _step("Search on your own site", WARN, says, **extra)
    if not n:
        return _step("Search on your own site", FAIL,
                     "no pages could be read, so there is nothing to search", **extra)
    return _step("Search on your own site", OK, says, **extra)


def step_answers(url, max_pages):
    from . import seo_answers
    try:
        out = seo_answers.run(url=url, max_pages=max_pages)
    except Exception as e:
        return _step("AEO and GEO audit", FAIL, f"the audit would not run: {e}")
    if not out.get("ok"):
        return _step("AEO and GEO audit", FAIL, out.get("error") or "failed")
    if out.get("critic") == "rejected":
        return _step("AEO and GEO audit", FAIL,
                     "refused: " + "; ".join(out.get("critic_reasons", [])))
    score = out.get("score", 0)
    worst = out.get("worst", [])[:3]
    says = (f"{len(out.get('pages', []))} page(s) audited, score {score}%. "
            + ("Worst: " + "; ".join(f"{w['label']} ({w['pages']} page(s))"
                                     for w in worst) if worst else "nothing failing."))
    blocked = (out.get("robots") or {}).get("blocked") or []
    if blocked:
        says += (f" robots.txt BLOCKS {', '.join(blocked)} — those are the "
                 f"assistants people ask for a glazier. Queued for you to confirm.")
    state = OK if score >= 70 else (WARN if score >= 40 else FAIL)
    return _step("AEO and GEO audit", state, says,
                 score=score, worst=worst, blocked=blocked)


def _bare(host: str) -> str:
    """
    A hostname with 'www.' taken off and nothing else.

    www and non-www ARE the same site for this purpose — pointing the www
    version at the bare one is a normal, correct thing to do. A different
    domain is not.
    """
    h = (host or "").strip().lower().rstrip(".")
    return h[4:] if h.startswith("www.") else h


def step_canonical(url, max_pages=10):
    """
    Where your pages tell Google their real home is.

    THIS FOUND THE WORST THING ON HIS SITE AND NOTHING WAS LOOKING FOR IT.

    Every one of his eight pages carried
    <link rel="canonical" href="https://worlddealtechnicalservices.ae/...">
    while the site he runs is .com. The .ae domain does not resolve — it has
    no DNS record at all.

    A canonical tag is not a hint. It tells Google "the authoritative copy of
    this page lives at this address; index that one instead of me." Every page
    on the site was pointing at a dead domain, which is an instruction to
    Google to drop the page it is reading. No amount of markup, content or
    keywords outranks a page asking not to be indexed.
    """
    from . import fetcher, harvest, site_search
    import socket
    import urllib.parse
    try:
        home = fetcher.fetch(url, timeout=20, allow_paid=False)
        if not home.get("ok") or not home.get("html"):
            return _step("Where pages say they live", WARN,
                         "could not read the site to check")
        base = url.rstrip("/")
        want = urllib.parse.urlparse(base).netloc.lower()
        pages_ = [base] + site_search.own_pages(home["html"], base)[:max_pages - 1]
        rows, missing = [], []
        for u in pages_:
            html = home["html"] if u == base else (
                fetcher.fetch(u, timeout=15, allow_paid=False).get("html") or "")
            if not html:
                continue
            m = (re.search(r'rel=["\']canonical["\'][^>]*href=["\']([^"\']+)', html, re.I)
                 or re.search(r'href=["\']([^"\']+)["\'][^>]*rel=["\']canonical', html, re.I))
            if not m:
                missing.append(u)
                continue
            # THE WHOLE HOST, TLD INCLUDED.
            #
            # harvest.brand() deliberately ignores the TLD, because a UAE firm
            # whose site is .com and whose email is .ae is one business, and
            # treating them as strangers threw away a real lead. Here the
            # opposite is true and the first version of this check got it
            # wrong: it compared brands, decided .ae and .com were "the same
            # site", and reported all clear on the single worst problem on
            # his site. To Google they are two different websites, and that
            # is the entire point of a canonical tag.
            host = _bare(urllib.parse.urlparse(m.group(1)).netloc.lower())
            if host and host != _bare(want):
                rows.append({"page": u, "points_at": m.group(1), "host": host})
        if not rows:
            note = f"{len(pages_)} page(s) checked, all pointing at your own site"
            if missing:
                note += f"; {len(missing)} have no canonical tag, which is fine"
            return _step("Where pages say they live", OK, note + ".")

        # Does the domain they point at even exist? A live sister domain is a
        # decision somebody made. A dead one is a catastrophe nobody noticed.
        hosts = sorted({r["host"] for r in rows})
        dead = []
        for h in hosts:
            try:
                socket.gethostbyname(h)
            except Exception:
                dead.append(h)
        says = (f"{len(rows)} page(s) tell Google their real home is "
                + ", ".join(hosts) + ", not this site.")
        if dead:
            says += (f" And {', '.join(dead)} does not exist — no DNS record at "
                     "all. You are telling Google to index a page that is not "
                     "there instead of the one it is reading, which is why "
                     "these pages do not rank. Fix the canonical tag in your "
                     "page template to point at your real address before "
                     "anything else on this list.")
        return _step("Where pages say they live", FAIL, says,
                     wrong=rows[:12], dead_hosts=dead)
    except Exception as e:
        return _step("Where pages say they live", WARN, f"could not check: {e}")


def step_secure(url):
    """
    What of his a stranger can download today.

    Part of the standing check rather than a command he has to remember,
    because the one time it was run by hand it found his API token published
    on the website as a text file. A one-off check finds a leak once; the leak
    that matters is the one introduced next week by a file somebody uploaded
    and forgot.
    """
    from . import site_secure
    try:
        out = site_secure.scan(url)
    except Exception as e:
        return _step("Private files", FAIL, f"the check would not run: {e}")
    if out.get("error"):
        return _step("Private files", FAIL, out["error"])
    bad, red, burned = out["exposed"], out["serious"], out["burned"]
    if burned:
        return _step("Private files", FAIL,
                     "A SECRET OF YOURS IS PUBLICLY READABLE: "
                     + ", ".join(b["path"] for b in burned)
                     + ". Treat that token as compromised — replace it, then "
                       "delete the file. Run: python -m pipelines.site_secure",
                     exposed=[b["path"] for b in bad], burned=True)
    if bad:
        return _step("Private files",
                     FAIL if red else WARN,
                     f"{len(bad)} thing(s) a stranger can download: "
                     + ", ".join(b["path"] for b in bad)
                     + ". Run: python -m pipelines.site_secure",
                     exposed=[b["path"] for b in bad])
    note = f"{out['checked']} known leak points checked, none readable"
    if out["unreachable"]:
        note += f" ({out['unreachable']} could not be reached — not the same as safe)"
    return _step("Private files", OK, note + ".")


def step_console():
    from . import seo_intel
    ok, why = seo_intel.gsc_ready()
    if ok:
        rows, err = seo_intel.gsc_queries(days=30, limit=50)
        if err:
            return _step("Google Search Console", FAIL, err)
        return _step("Google Search Console", OK,
                     f"connected — {len(rows)} real quer(ies) in the last 30 days",
                     queries=len(rows))
    return _step("Google Search Console", SKIP,
                 why + " This is the single most valuable free thing left on your "
                       "list: it is the only source that shows which searches "
                       "already reach you, and it is what turns `--wins` on.")


# ---------------------------------------------------------------------------
# THE PASS
# ---------------------------------------------------------------------------

def next_steps(steps):
    """
    What to do, worst first. Only ever built from a step that actually reported
    — nothing is recommended on the strength of a check that did not run.
    """
    todo = []
    by = {s["name"]: s for s in steps}
    h = by.get("Site health")
    if h and h["state"] == FAIL:
        todo.append("Fix the critical site issue first. Everything else on this "
                    "list is worth less than a working contact form.")
    a = by.get("Your website address")
    if a and a["state"] == FAIL:
        todo.append("Save your website on /setup — nothing below can run without it.")
    r = by.get("Can the agency read it")
    if r and r["state"] == WARN:
        todo.append("Your pages arrive nearly empty because they are built by "
                    "JavaScript. Google sees what this saw. Serving the text in "
                    "the HTML fixes it for free; CRAWLER_ON_OWN_SITE=1 works "
                    "around it and is billed per page.")
    s = by.get("Search on your own site")
    if s and s["state"] != FAIL and not s.get("indexed"):
        todo.append("Run this again with --index to build the Algolia index for "
                    "real, then approve the search box when it reaches you.")
    ans = by.get("AEO and GEO audit")
    if ans and ans.get("worst"):
        w = ans["worst"][0]
        todo.append(f"Biggest answer-engine gap: {w['label']} on {w['pages']} "
                    f"page(s). It is filed to {w['sector']}.")
    c = by.get("Google Search Console")
    if c and c["state"] == SKIP:
        todo.append("Connect Search Console. Free, and it is the only thing that "
                    "shows which searches already find you.")
    sv = by.get("What your site says you sell")
    if sv and sv.get("unknown"):
        # the true count, not the length of the truncated sample shown above
        todo.append(f"Your site sells {sv.get('unknown_total', len(sv['unknown']))} "
                    f"thing(s) the agency does not know about. Until they are "
                    f"catalogued it cannot target or write about them.")
    return todo


def run(url=None, do_index=False, max_pages=12, agent=AGENT) -> dict:
    with board.agent_run(agent, note="first real run against the live site") as row:
        out = _work(url, do_index, max_pages)
        row.note(out.get("summary", "site check"))
        return out


def _work(url, do_index, max_pages) -> dict:
    """
    One pass, one fetch per page.

    Five checks over the same site used to crawl independently, so testing one
    website made the owner's server serve his home page five times over — and
    one flaky response anywhere in that pile failed a step for no reason, which
    is exactly what happened on the first live run. fetcher.cache() makes the
    whole pass share its reads.
    """
    from . import fetcher
    with fetcher.cache() as pass_cache:
        out = _steps(url, do_index, max_pages)
        out["pages_fetched"] = len(pass_cache.store)
        out["fetches_saved"] = pass_cache.hits
        return out


def _steps(url, do_index, max_pages) -> dict:
    board.ensure_schema()
    steps = [step_address(url or "")]
    url = url or steps[0].get("url")
    if not url:
        return {"ok": False, "steps": steps, "next": next_steps(steps),
                "summary": "no website address — nothing could be tested"}

    steps.append(step_readable(url))
    if steps[-1]["state"] == FAIL:
        return {"ok": False, "site": url, "steps": steps, "next": next_steps(steps),
                "summary": f"could not read {url} — nothing else was attempted"}

    for fn in (lambda: step_health(url),
               lambda: step_services(url),
               lambda: step_search(url, do_index, max_pages),
               lambda: step_answers(url, max_pages),
               lambda: step_canonical(url),
               lambda: step_secure(url),
               step_console):
        try:
            steps.append(fn())
        except Exception as e:                     # one broken step, not a dead run
            steps.append(_step("a step", FAIL, f"crashed: {e}"))

    if do_index:
        for s in steps:
            if s["name"] == "Search on your own site":
                s["indexed"] = True

    bad = [s for s in steps if s["state"] == FAIL]
    warn = [s for s in steps if s["state"] == WARN]
    return {"ok": not bad, "site": url, "indexed": do_index, "steps": steps,
            "next": next_steps(steps),
            "summary": (f"{len(steps)} checks on {url}: "
                        f"{len(steps) - len(bad) - len(warn)} clean, "
                        f"{len(warn)} to watch, {len(bad)} broken")}


def as_text(out) -> str:
    L = ["", "YOUR WEBSITE, TESTED FOR REAL", "=" * 66]
    if not out.get("indexed"):
        L.append("Read-only. Nothing was changed on your site or in Algolia.")
    else:
        L.append("Index written for real. Your website itself was NOT changed —")
        L.append("the search box is queued for your approval.")
    L.append("")
    for s in out.get("steps", []):
        L.append(f"  {_MARK[s['state']]}  {s['name']}")
        for line in _wrap(s["says"], 64):
            L.append(f"          {line}")
        if s.get("sample"):
            for r in s["sample"][:4]:
                L.append(f"            · {r.get('title', '')[:44]}  {r.get('objectID', '')}")
        L.append("")
    nxt = out.get("next") or []
    if nxt:
        L += ["WHAT TO DO NEXT, WORST FIRST", "-" * 66]
        for i, t in enumerate(nxt, 1):
            wrapped = _wrap(t, 62)
            L.append(f"  {i}. {wrapped[0]}")
            L += [f"     {w}" for w in wrapped[1:]]
        L.append("")
    L.append(out.get("summary", ""))
    return "\n".join(L)


def _wrap(text, width):
    words, line, out = str(text or "").split(), "", []
    for w in words:
        if len(line) + len(w) + 1 > width:
            out.append(line)
            line = w
        else:
            line = (line + " " + w).strip()
    if line:
        out.append(line)
    return out or [""]


def main():
    ap = argparse.ArgumentParser(
        description="Test your real website, end to end, in one command.")
    ap.add_argument("--url", default="", help="test this address instead of the saved one")
    ap.add_argument("--index", action="store_true",
                    help="also push the Algolia index for real (default is a dry run)")
    ap.add_argument("--pages", type=int, default=12, help="how many pages to read")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    out = run(url=a.url or None, do_index=a.index, max_pages=a.pages)
    print(json.dumps(out, indent=2, ensure_ascii=False) if a.json else as_text(out))


if __name__ == "__main__":
    main()
