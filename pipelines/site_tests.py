import tempfile
import json
import os
from pathlib import Path
"""
site_tests.py — run: python -m pipelines.site_tests

Tests for reading the owner's own website and working out what he sells.

The failure this guards against is subtle and expensive: an agency that thinks
it sells something the owner doesn't, or misses something he does, will say so
to a customer. So the tests are mostly about the boundary between "we found
this and can target it" and "we found this and cannot" — because the second
list is the one that is worth anything, and it only means something if the
matcher refuses to guess.

Three real bugs from building it are pinned here:
  * the page <title> was being read as a service, so the company's own name
    ended up on the list of things it sells
  * "Windows &amp; Doors" never matched anything, because the entity was left
    undecoded
  * the matcher used the BUYER signals instead of the service NAMES, so
    "Frameless Shower Enclosures" came back as something we don't sell

No network: every test runs against fixture HTML.
"""
import sys

from . import services
from . import site_services as ss

_fail = []


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


SITE = """<html><head><title>Glassworks Dubai | Glass &amp; Aluminium Experts</title></head>
<body>
  <nav>
    <a href="/services/partitions">Office Glass Partitions</a>
    <a href="/services/shower">Frameless Shower Enclosures</a>
    <a href="/services/smart">Smart Switchable Glass</a>
    <a href="/about">About us</a>
    <a href="/contact">Contact</a>
  </nav>
  <h1>Glass &amp; Aluminium Contractors in Dubai</h1>
  <h2>Aluminium Windows &amp; Doors</h2>
  <h2>Stainless Steel Handrails</h2>
  <ul><li>Pergolas and Shades</li><li>ACP Cladding Works</li>
      <li>Frosted Glass Films</li></ul>
  <p>We have been fitting glass, aluminium windows and shower screens across
     Dubai for over ten years, and every job is finished by our own team.</p>
</body></html>"""


def test_only_service_looking_text_is_taken():
    print("What counts as a candidate service:")
    ph = ss.phrases_from(SITE)
    low = [p.lower() for p in ph]

    check("a nav service link is taken", "office glass partitions" in low, True)
    check("a heading is taken", "aluminium windows & doors" in low, True)
    check("a list item is taken", "pergolas and shades" in low, True)

    # the three that must NOT be taken
    check("the page title is not a service",
          any("experts" in p for p in low), False)
    check("body copy is not a service",
          any("over ten years" in p for p in low), False)
    check("a non-trade nav link is not a service", "about us" in low, False)


def test_entities_are_decoded():
    """'Windows &amp; Doors' matched nothing and showed up as a service we
    supposedly do not sell. It is one service, spelled with an ampersand."""
    print("HTML entities:")
    ph = ss.phrases_from(SITE)
    check("the ampersand is real", any("Windows & Doors" in p for p in ph), True)
    check("no raw entity survives", any("&amp;" in p for p in ph), False)


def test_matching_uses_names_not_buyer_signals():
    """
    'hotel' is a SIGNAL for shower glass — it says who needs it. 'shower
    enclosure' is a NAME — it says what it is. Reading our own site needs names.
    """
    print("Names, not signals:")
    check("a service name matches",
          services.match_by_name("Frameless Shower Enclosures")[0], "shower_glass")
    check("and reports which name hit",
          services.match_by_name("Frameless Shower Enclosures")[1], "shower enclosure")
    check("the longest name wins over the generic one",
          services.match_by_name("aluminium door repair")[0], "aluminium_doors")
    check("a buyer word alone is NOT a service name",
          services.match_by_name("Marina Bay Hotel")[0], None)
    check("and neither is unrelated text",
          services.match_by_name("Careers at our company")[0], None)

    # every service must actually be findable by at least one of its own names
    for key, s in services.SERVICES.items():
        hits = [n for n in s["names"] if services.match_by_name(n)[0]]
        check(f"{key} is findable by its own names", bool(hits), True)


def test_reconciliation_splits_three_ways():
    print("Confirmed / uncatalogued / missing:")
    confirmed, uncatalogued = {}, []
    for p in ss.phrases_from(SITE):
        key, _ = services.match_by_name(p)
        (confirmed.setdefault(key, []).append(p) if key else uncatalogued.append(p))

    for want in ("glass_partition", "shower_glass", "aluminium_windows",
                 "glass_railing", "pergola", "cladding"):
        check(f"{want} recognised on the site", want in confirmed, True)

    low = [u.lower() for u in uncatalogued]
    check("smart glass is flagged as untargetable",
          "smart switchable glass" in low, True)
    check("frosted film is flagged as untargetable",
          "frosted glass films" in low, True)
    check("nothing we DO sell landed in the untargetable pile",
          [u for u in low if "partition" in u or "pergola" in u], [])


def test_critic_will_not_certify_an_unread_site():
    print("Critic:")
    ok, why = ss.critic_check({"pages_read": [], "phrases_found": 0})
    check("no page read -> refuse",
          (ok, any("nothing to conclude" in r for r in why)), (False, True))

    ok, why = ss.critic_check({"pages_read": ["/"], "phrases_found": 0})
    check("a page with no trade words at all is suspicious",
          (ok, any("wrong site" in r for r in why)), (False, True))

    ok, _ = ss.critic_check({"pages_read": ["/"], "phrases_found": 12,
                             "confirmed": {"pergola": {}}, "uncatalogued": ["x"]})
    check("a normal read passes", ok, True)

    # everything matching and nothing left over means the matcher is too loose
    everything = {k: {} for k in services.SERVICES}
    ok, why = ss.critic_check({"pages_read": ["/"], "phrases_found": 20,
                               "confirmed": everything, "uncatalogued": []})
    check("a suspiciously perfect result is rejected",
          (ok, any("suspiciously tidy" in r for r in why)), (False, True))


def test_no_site_configured():
    print("No website set:")
    d = ss.discover("")
    check("it refuses rather than guessing", d["ok"], False)
    check("and says how to fix it", "/setup" in d["error"], True)


def test_only_our_own_site_is_followed():
    """A competitor's link on our page is not our page."""
    print("It reads our site and nothing else:")
    html = ('<a href="/services/glass">Ours</a>'
            '<a href="https://competitor.ae/services">Theirs</a>'
            '<a href="https://facebook.com/us">Social</a>')
    links = ss._internal_links(html, "https://glassworks.ae")
    check("our own services page is followed",
          links, ["https://glassworks.ae/services/glass"])
    check("the competitor is not followed",
          any("competitor" in l for l in links), False)


def test_the_site_address_survives_a_fresh_clone():
    """
    The owner saved his website on /setup and every agent elsewhere still said
    "No SITE_URL set". It had gone into .env, which is gitignored because it
    holds the mail password and the API token, so the address never travelled.
    A public web address is not a credential; it belongs in a tracked file.
    """
    print("The website address travels with the repo:")
    from coordination import site as _site
    real_cfg, real_envf = _site.CONFIG, _site.from_env_file
    keep = os.environ.pop("SITE_URL", None)
    d = Path(tempfile.mkdtemp())
    _site.CONFIG = d / "site.json"
    # Never read the real .env here. This test asserts "nothing saved yet ->
    # empty", and on the owner's own machine .env genuinely holds his address —
    # so without this the suite would fail on his laptop and pass on mine.
    _site.from_env_file = lambda: ""
    try:
        check("nothing saved yet -> empty, never a guess", _site.site_url(), "")
        check("and it says how to set it", "setup" in _site.status()["hint"], True)
        check("and it lists every drawer it opened",
              len(_site.status()["looked_in"]), 3)

        _site.save("glassworks.ae")
        check("a saved address is found", _site.site_url(), "https://glassworks.ae")
        check("a bare domain gets a scheme", _site.site_url().startswith("https://"), True)
        check("and it came from the file", _site.status()["source"], "config/site.json")

        # THE BUG THE OWNER HIT. config/site.json used to be tracked, so a pull
        # reset it to {"url": ""} and the health check told him to set a website
        # he had already set. .env is gitignored and is what actually survives.
        _site.from_env_file = lambda: "https://glassworks.ae"
        _site.CONFIG.write_text('{"url": ""}\n', encoding="utf-8")
        check("a pull that empties the file does not lose the address",
              _site.site_url(), "https://glassworks.ae")
        check("and the owner is told which copy answered",
              _site.status()["source"], ".env")
        _site.from_env_file = lambda: ""
        _site.save("glassworks.ae")

        os.environ["SITE_URL"] = "https://staging.example.ae"
        check("an explicit env var still wins for one run",
              _site.site_url(), "https://staging.example.ae")
        os.environ.pop("SITE_URL")

        check("an explicit argument beats both",
              _site.site_url("https://other.ae"), "https://other.ae")

        # the file is committed, so it must never be given a secret to hold
        saved = json.loads(_site.CONFIG.read_text(encoding="utf-8"))
        check("only public facts are stored",
              set(saved) <= {"url", "mode", "areas"}, True)
    finally:
        _site.CONFIG, _site.from_env_file = real_cfg, real_envf
        if keep is not None:
            os.environ["SITE_URL"] = keep


def test_the_owners_own_site_is_read_for_free():
    """
    Algolia costs nothing; Zyte is billed per page. Indexing forty of the owner's
    own pages every day through a paid renderer is a standing charge for
    something plain HTTP does for nothing — and they are HIS pages, on HIS
    server, so there is no blocking to get past. Zyte earns its keep on RIVALS.

    So the money switch is OFF for his own site by default, and the default has
    to be the cheap one: a default nobody chose must never be the one that spends.
    """
    from . import fetcher as fx
    from . import site_search as search

    print("\nPaid rendering on his own site is OFF unless he says otherwise:")
    check("default is the cheap path", search.paid_allowed_on_own_site(env={}), False)
    check("and stays cheap on junk input",
          search.paid_allowed_on_own_site(env={"CRAWLER_ON_OWN_SITE": "maybe"}), False)
    for on in ("1", "true", "yes", "on", "ON"):
        check(f"'{on}' turns it on",
              search.paid_allowed_on_own_site(env={"CRAWLER_ON_OWN_SITE": on}), True)

    shell = ('<html><head><title>x</title></head><body><div id="root"></div>'
             '<script>__NEXT_DATA__={}</script></body></html>')
    paid_calls = []
    real_plain, real_zyte, real_tool = fx._plain, fx._zyte, fx.crawler_tool
    # The address guard is tested on its own above. Stub it here so these
    # assertions measure the MONEY policy and not DNS — invented domains do not
    # resolve, and a refusal before the policy runs would test nothing.
    real_safe = fx.competitors.safe_url
    fx.competitors.safe_url = lambda u: (True, "")
    fx._plain = lambda u, t: (shell, "", 200)
    fx.crawler_tool = lambda: {"name": "Zyte API", "api_key": "K", "enabled": True,
                               "base_url": "https://api.zyte.com/v1/extract"}
    fx._zyte = lambda *a, **k: (paid_calls.append(1) or ("<html>rendered</html>", "", 200))
    try:
        print("\nA JavaScript page on HIS site is skipped, not billed:")
        r = fx.fetch("https://his-site.ae/p", allow_paid=False)
        check("nothing was charged", r["cost"], 0.0)
        check("and Zyte was never called", paid_calls, [])
        check("it is not passed off as a good page", r["ok"], False)
        check("the price of the alternative is named",
              r["declined"]["would_cost"], fx.COST_RENDER)
        # fetcher.py is generic and deliberately does NOT know about a
        # site-search-specific env switch; naming it is site_search's job, and
        # asserted below where the run reports it.
        check("and it says paid rendering was switched off, not that it failed",
              "switched off" in r["error"], True)

        print("\nThe SAME page on a rival's site IS rendered — that is the point:")
        r = fx.fetch("https://a-rival.ae/p", allow_paid=True)
        check("Zyte was used", r["how"], "crawler-render")
        check("it cost the render price", r["cost"], fx.COST_RENDER)
        check("and the owner is TOLD one provider took over from the other",
              "Switching to Zyte API" in r["switched"], True)
        check("with the reason in plain words",
              "built by JavaScript" in r["switched"], True)
    finally:
        fx._plain, fx._zyte, fx.crawler_tool = real_plain, real_zyte, real_tool

    print("\nA plain fetch that simply works never claims a provider switch:")
    fx.competitors.safe_url = lambda u: (True, "")
    good = "<html><body><h1>Pergolas</h1><p>" + ("real copy " * 60) + "</p></body></html>"
    fx._plain = lambda u, t: (good, "", 200)
    try:
        r = fx.fetch("https://his-site.ae/pergolas", allow_paid=True)
        check("read on the plain path", r["how"], "plain")
        check("no switch line", r["switched"], "")
        check("and it cost nothing", r["cost"], 0.0)
    finally:
        fx._plain = real_plain
        fx.competitors.safe_url = real_safe

    print("\nThe run says what it skipped and what that would have cost:")
    real_fetch = search.fetcher.fetch
    search.fetcher.fetch = lambda url, timeout=15, allow_paid=True, **k: {
        "ok": False, "html": shell, "how": "plain", "cost": 0.0, "status": 200,
        "switched": "", "error": "skipped",
        "declined": {"reason": "JavaScript", "would_cost": fx.COST_RENDER}}
    try:
        recs, err = search.build_records("https://his-site.ae", max_pages=3)
        check("nothing was billed", search._LAST["cost"], 0.0)
        check("and the skipped pages were counted",
              len(search._LAST["skipped"]) >= 1, True)
        check("with a running total of what rendering would cost",
              search._LAST["would_cost"] > 0, True)

        # and the RUN is where the owner is told which switch to flip
        real_cfg, real_site = search._cfg, search.site.site_url
        search._cfg = lambda env=None: {"app_id": "A", "search_key": "S",
                                        "write_key": "W", "index": "i"}
        search.site.site_url = lambda *a, **k: "https://his-site.ae"
        try:
            out = search._work("it-search", dry_run=True, max_pages=3)
            check("the run reports the skipped pages", bool(out.get("pages_skipped")), True)
            check("names the price per run", out["skipped_would_cost"] > 0, True)
            check("names the switch to flip",
                  "CRAWLER_ON_OWN_SITE=1" in out["skipped_note"], True)
            check("and says Zyte still works on rivals either way",
                  "rival sites either way" in out["skipped_note"], True)
        finally:
            search._cfg, search.site.site_url = real_cfg, real_site
    finally:
        search.fetcher.fetch = real_fetch


def _read_body(handler):
    """
    Read what was sent before answering. A stand-in server that replies and
    hangs up with the request still unread makes WINDOWS reset the connection
    (WinError 10053/10054) — sometimes, depending on timing — so the client sees
    "could not connect" and a test that is green on Linux goes red on his PC.
    """
    n = int(handler.headers.get("Content-Length") or 0)
    if n:
        handler.rfile.read(n)


def _make401():
    import http.server, socketserver
    class Q(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            _read_body(self)
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            body = b'{"title":"Authentication Info Invalid"}'
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *a):
            pass
    return socketserver.TCPServer(("127.0.0.1", 0), Q)


def test_site_search_never_publishes_the_write_key():
    """
    Algolia gives you three values and exactly one of them may reach a browser.

    The search key is public BY DESIGN — the widget queries from the visitor's
    machine, so it has to be in the page. The write key can delete every record
    in the index. Putting it in front-end JavaScript is the classic Algolia
    mistake and it is one view-source away from someone wiping the site index.

    So the check is not "the code looks careful". It reads the FINISHED widget
    and refuses if the write key is anywhere in it.
    """
    from . import site_search as search

    cfg = {"app_id": "APP123", "search_key": "SEARCHONLY111",
           "write_key": "WRITEKEY999", "index": "glasswork_site"}

    print("\nThe widget carries the search key and nothing else:")
    html = search.widget_html(cfg)
    check("the search key is in it", cfg["search_key"] in html, True)
    check("THE WRITE KEY IS NOT", cfg["write_key"] in html, False)
    check("the app id is in it", cfg["app_id"] in html, True)
    check("and it says in the page why that key is safe to publish",
          "SEARCH-ONLY" in html, True)

    print("\nPasting the write key into the search-key box is caught:")
    try:
        search.widget_html({**cfg, "search_key": cfg["write_key"]})
        check("refused", "no error raised", "WriteKeyLeak")
    except search.WriteKeyLeak as e:
        check("refused", True, True)
        check("and it says what would have happened",
              "wipe the index" in str(e), True)

    print("\nA template that leaks is caught by the check, not by good intentions:")
    real = search.WIDGET
    search.WIDGET = real.replace('KEY="__SEARCH_KEY__"', 'KEY="' + cfg["write_key"] + '"')
    try:
        search.widget_html(cfg)
        check("a leaking template is refused", "no error raised", "WriteKeyLeak")
    except search.WriteKeyLeak:
        check("a leaking template is refused", True, True)
    finally:
        search.WIDGET = real

    print("\nThe same mistake is caught at the connector, before anything runs:")
    from providers import integrations as ig
    r = ig.test_algolia(env={"ALGOLIA_APP_ID": "A", "ALGOLIA_SEARCH_KEY": "K",
                             "ALGOLIA_WRITE_KEY": "K"})
    check("identical keys fail the connector test", r["ok"], False)
    check("and it explains the consequence", "write access" in r["detail"], True)
    r = ig.test_algolia(env={})
    check("unconfigured is 'not set up', not 'broken'", r["ok"], None)

    print("\nRecords are stable, so a re-index updates rather than duplicates:")
    # base is rstrip("/")-ed inside build_records, so the home key has no slash
    # The home page LINKS to the pergola page, the way a real site does. It
    # used to be reached by stubbing site_services._internal_links; the indexer
    # now reads the site's own anchors, which is the whole point of the fix.
    pages = [("https://x.ae", '<title>Home</title><h1>Glass</h1>'
              '<a href="/pergolas">Pergolas</a><p>' + "a" * 200 + "</p>"),
             ("https://x.ae/pergolas", "<title>Pergolas</title><h1>Aluminium pergolas</h1>"
              "<p>" + "b" * 200 + "</p>")]
    real_fetch = search._fetch
    search._fetch = lambda u, timeout=15, allow_paid=None: (dict(pages).get(u, ""), "")
    try:
        recs, err = search.build_records("https://x.ae", max_pages=10)
    finally:
        search._fetch = real_fetch

    check("both pages became records", len(recs), 2)
    check("the objectID is the path, not a counter",
          sorted(r["objectID"] for r in recs), ["/", "/pergolas"])
    check("so no two records collide",
          len({r["objectID"] for r in recs}), len(recs))
    check("the title is read off the page",
          sorted(r["title"] for r in recs), ["Home", "Pergolas"])
    check("and the pergola page is tagged with the service it sells",
          next(r["service"] for r in recs if r["objectID"] == "/pergolas"),
          "pergola")

    print("\nThe critic catches an index that would be useless:")
    ok, why = search.critic_check([], cfg)
    check("no pages is rejected", ok, False)
    thin = [{"objectID": "/", "body": "hi", "service": ""}]
    ok, why = search.critic_check(thin, cfg)
    check("a JavaScript shell with no readable text is rejected", ok, False)
    check("and it points at both fixes, the paid one and the free one",
          any("CRAWLER_ON_OWN_SITE" in r and "serve its content" in r for r in why),
          True)
    dupe = [{"objectID": "/a", "body": "x" * 200, "service": ""},
            {"objectID": "/a", "body": "y" * 200, "service": ""}]
    ok, why = search.critic_check(dupe, cfg)
    check("two records sharing an objectID is rejected", ok, False)


def test_the_fetcher_pays_only_when_it_has_to():
    """
    One way to read a page, with a renderer behind it.

    The rule that matters is the ORDER: free HTTP first, always. A crawler API
    billed per request will happily charge for forty pages that plain HTTP could
    have read, and nobody notices until the invoice.
    """
    from . import fetcher as fx

    print("\nA JavaScript shell is told apart from a short page:")
    shell = ('<html><head><title>x</title></head><body><div id="root"></div>'
             '<script>__NEXT_DATA__={}</script></body></html>')
    real = "<html><body><h1>Pergolas</h1><p>" + ("We install pergolas. " * 40) + "</p></body></html>"
    check("the shell is spotted", fx.looks_unrendered(shell), True)
    check("a real page is not", fx.looks_unrendered(real), False)
    check("nothing at all counts as unrendered", fx.looks_unrendered(""), True)
    # A framework marker ALONE must not condemn a page — plenty of real sites
    # are built with React and render server-side.
    big_react = ('<div data-reactroot></div><p>' + ("real copy " * 90) + "</p>")
    check("a React page with real text is left alone",
          fx.looks_unrendered(big_react), False)

    print("\nThe address is checked before either path — crawler or not:")
    for bad in ("http://169.254.169.254/latest/meta-data/", "file:///etc/passwd",
                "http://127.0.0.1:3000/", "http://10.0.0.5/"):
        r = fx.fetch(bad)
        check(f"{bad.split('/')[2] if '//' in bad else bad} is refused",
              r["how"], "refused")
    check("and it costs nothing to refuse", fx.fetch("file:///etc/passwd")["cost"], 0.0)

    print("\nWith no crawler key it says what one would buy, and never pretends:")
    real_tool = fx.crawler_tool
    fx.crawler_tool = lambda: None
    try:
        r = fx.fetch("https://example.com", render=True)
        check("a render without a key fails", r["ok"], False)
        check("and names the shelf to put it on", "'crawler' shelf" in r["error"], True)
        check("and charges nothing", r["cost"], 0.0)
    finally:
        fx.crawler_tool = real_tool

    print("\nThe free path is tried first, and a good page never costs anything:")
    real_plain = fx._plain
    fx._plain = lambda u, t: (real, "", 200)
    fx.crawler_tool = lambda: {"name": "Zyte API", "api_key": "K",
                               "base_url": "https://api.zyte.com/v1/extract",
                               "auth_style": "basic", "enabled": True}
    try:
        r = fx.fetch("https://example.com")
        check("read for free", r["how"], "plain")
        check("cost nothing", r["cost"], 0.0)
        check("and the crawler was never called", r["ok"], True)
    finally:
        fx._plain, fx.crawler_tool = real_plain, real_tool

    print("\nA shell falls through to the renderer, and only then costs money:")
    calls = []
    fx._plain = lambda u, t: (shell, "", 200)
    fx.crawler_tool = lambda: {"name": "Zyte API", "api_key": "MYKEY",
                               "base_url": "https://api.zyte.com/v1/extract",
                               "auth_style": "basic", "enabled": True}
    real_zyte = fx._zyte
    fx._zyte = lambda url, tool, render, timeout: (calls.append((url, render, tool)) or
                                                   (real, "", 200))
    try:
        r = fx.fetch("https://example.com/pergolas")
        check("it rendered", r["how"], "crawler-render")
        check("and the page is real now", fx.looks_unrendered(r["html"]), False)
        check("it cost the render price", r["cost"], fx.COST_RENDER)
        check("the renderer was asked for a browser render",
              calls[0][1], True)
    finally:
        fx._zyte, fx._plain, fx.crawler_tool = real_zyte, real_plain, real_tool

    print("\nThe Test button probes Zyte correctly, whatever style was saved:")
    from providers import client as _client
    check("a crawler-shelf tool is recognised as an extract API",
          _client._is_extract_api({"shelf": "crawler", "base_url": "", "name": "x"}), True)
    check("...and one recognised by its address",
          _client._is_extract_api({"base_url": "https://api.zyte.com/v1/extract"}), True)
    check("a plain search API is NOT",
          _client._is_extract_api({"shelf": "seo_data", "base_url": "https://api.dataforseo.com"}), False)

    import base64 as _b, json as _j, threading as _t, http.server as _h, socketserver as _sk
    got = {}

    class P(_h.BaseHTTPRequestHandler):
        def do_GET(self):        # the generic probe would land here and mislead
            got["GET"] = True
            self.send_response(405); self.end_headers()
        def do_POST(self):
            got["auth"] = self.headers.get("Authorization")
            _read_body(self)
            self.send_response(200); self.send_header("Content-Type", "application/json")
            out = _j.dumps({"httpResponseBody": _b.b64encode(b"<html>ok</html>").decode()}).encode()
            self.send_header("Content-Length", str(len(out))); self.end_headers()
            self.wfile.write(out)
        def log_message(self, *a): pass

    srv2 = _sk.TCPServer(("127.0.0.1", 0), P)
    _t.Thread(target=srv2.serve_forever, daemon=True).start()
    try:
        ep = f"http://127.0.0.1:{srv2.server_address[1]}/v1/extract"
        # saved as bearer — the exact state that produced the owner's 401
        r = _client.probe_tool({"name": "Zyte API", "shelf": "crawler",
                                "base_url": ep, "api_key": "GOODKEY",
                                "auth_style": "bearer"}, timeout=10)
        # On a failure this shows WHY (the probe's own words), not just "False".
        check("it passes even though the tool was saved as bearer", r["ok"] or r.get("cause"), True)
        check("because it used POST, not a GET", got.get("GET"), None)
        check("and HTTP Basic, not Bearer", got["auth"].startswith("Basic "), True)
        check("with the key as the Basic username",
              _b.b64decode(got["auth"].split()[1]).decode(), "GOODKEY:")
    finally:
        srv2.shutdown()

    print("\nA bad crawler key is honest, and never advises 'raw':")
    srv3 = _make401()
    _t.Thread(target=srv3.serve_forever, daemon=True).start()
    try:
        ep = f"http://127.0.0.1:{srv3.server_address[1]}/v1/extract"
        r = _client.probe_tool({"name": "Zyte API", "shelf": "crawler",
                                "base_url": ep, "api_key": "BADKEY"}, timeout=10)
        check("a rejected key fails", r["ok"], False)
        check("it is reported as 401", r["status"], 401)
        check("the advice never says 'raw' (the old wrong hint)",
              "raw" in r["cause"].lower(), False)
        check("and it names the Proxy-vs-API confusion instead",
              "Proxy" in r["cause"], True)
    finally:
        srv3.shutdown()

    print("\nZyte is called the way Zyte documents:")
    import base64 as _b64, json as _json, threading, http.server, socketserver
    seen = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            seen["auth"] = self.headers.get("Authorization")
            n = int(self.headers.get("Content-Length") or 0)
            seen["body"] = _json.loads(self.rfile.read(n) or b"{}")
            out = _json.dumps({"browserHtml": real}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(out)))
            self.end_headers()
            self.wfile.write(out)

        def log_message(self, *a):
            pass

    srv = socketserver.TCPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        tool = {"name": "Zyte API", "api_key": "MYZYTEKEY", "auth_style": "basic",
                "base_url": f"http://127.0.0.1:{srv.server_address[1]}/v1/extract",
                "enabled": True}
        html, err, status = fx._zyte("https://example.com/p", tool, True, 20)
        check("it answered", (bool(html), err), (True, ""))
        # The owner pastes the bare key. Zyte wants it as the Basic USERNAME with
        # an empty password, so the trailing colon is ours to add, not his.
        creds = _b64.b64decode(seen["auth"].split()[1]).decode()
        check("HTTP Basic, key as the username", creds, "MYZYTEKEY:")
        check("and he did not have to type the colon", "MYZYTEKEY::" in creds, False)
        check("browserHtml is what we asked for", seen["body"].get("browserHtml"), True)
        check("redirects followed", seen["body"].get("followRedirect"), True)
        check("the target url is passed through",
              seen["body"].get("url"), "https://example.com/p")
    finally:
        srv.shutdown()


# ---------------------------------------------------------------------------
# THE SITE TELLS YOU WHERE ITS PAGES ARE. LISTEN BEFORE GUESSING.
# ---------------------------------------------------------------------------

# The owner's real site, in miniature. Every page carries the same navigation
# block and the same service list, which is how a trade site is built and is
# exactly what fooled the indexer.
_NAV = ('<nav><a href="/index.php">Home</a><a href="/service.php">Services</a>'
        '<a href="/about.php">About</a><a href="/gallery.php">Gallery</a>'
        '<a href="/blog.php">Blog</a><a href="/faq.php">FAQ</a>'
        '<a href="/contact.php">Contact</a>'
        '<a href="/assets/img/hero.jpg">pic</a>'
        '<a href="/assets/css/main.css">css</a></nav>')
_BLOCK = ("<h3>01. Glass Installation</h3><h3>02. Aluminum Installation</h3>"
          "<h3>Color Switcher</h3>")


def _php(title, h1, extra=""):
    return (f"<html><head><title>{title} | Glass, Aluminium &amp; Wooden Work "
            f"Dubai</title></head><body>{_NAV}<h1>{h1}</h1>{extra}{_BLOCK}"
            "<p>" + ("World Deal Technical Services, Dubai. " * 40) + "</p>"
            "</body></html>")


def test_the_indexer_reads_the_pages_he_has_not_the_ones_it_imagined():
    """
    His site indexed 2 of its 9 pages. The other 7 were reported as "built by
    JavaScript" — they were /services, /our-services, /what-we-do, /products,
    /solutions, /about and /about-us, none of which exist on a .php site. The
    guess list was queued FIRST and used up the page budget before the real
    links in his navigation bar were reached.
    """
    from . import site_search as search
    print("\nThe navigation bar is a list of the pages that exist:")
    home = _php("World Deal Technical Services", "Best Aluminium Glass Company")
    got = search.own_pages(home, "https://wd.example")
    check("every real page is found",
          [u.rsplit("/", 1)[-1] for u in got],
          ["index.php", "service.php", "about.php", "gallery.php", "blog.php",
           "faq.php", "contact.php"])
    check("a jpg is not a page", any("hero.jpg" in u for u in got), False)
    check("nor is a stylesheet", any(".css" in u for u in got), False)
    check("and the home page is not queued twice",
          any(u.rstrip("/") == "https://wd.example" for u in got), False)

    print("\n...and they are read before anything is guessed at:")
    real = search._fetch
    pages = {
        "https://wd.example": home,
        "https://wd.example/service.php": _php("Services", "Services",
                                               "<h2>Glass Partition</h2>"),
        "https://wd.example/faq.php": _php("FAQ", "Frequently Asked Questions"),
        "https://wd.example/contact.php": _php("Contact", "Contact Us"),
    }
    asked = []

    def fake(url, timeout=15, allow_paid=None):
        asked.append(url)
        return pages.get(url, ""), "" if url in pages else "HTTP 404"

    search._fetch = fake
    try:
        recs, err = search.build_records("https://wd.example", max_pages=4)
    finally:
        search._fetch = real
    check("no error", err, "")
    check("the first page asked for after the home page is a real one",
          asked[1], "https://wd.example/index.php")
    check("nothing invented was reached before the real pages ran out",
          [a for a in asked[:4] if a.endswith((".php", "example"))], asked[:4])


def test_the_same_page_under_two_names_is_indexed_once():
    """
    '/' and '/index.php' are one page. Indexed twice, the home page appears in
    every result list twice.
    """
    from . import site_search as search
    print("\nOne page, one record, whatever address it answers on:")
    home = _php("World Deal Technical Services", "Best Aluminium Glass Company")
    real = search._fetch
    pages = {"https://wd.example": home,
             "https://wd.example/index.php": home,      # byte-identical
             "https://wd.example/faq.php": _php("FAQ", "Frequently Asked")}
    search._fetch = lambda u, timeout=15, allow_paid=None: (
        pages.get(u, ""), "" if u in pages else "HTTP 404")
    try:
        recs, _e = search.build_records("https://wd.example", max_pages=8)
    finally:
        search._fetch = real
    paths = sorted(r["path"] for r in recs)
    check("the duplicate is dropped", paths, ["/", "/faq.php"])
    check("and the one kept is the canonical one", recs[0]["path"], "/")


def test_a_heading_on_every_page_describes_the_site_not_the_page():
    """
    His site repeats its service list on all seven pages, so the contact page,
    the gallery and the FAQ all matched 'glass installation' and the service
    field said the same thing seven times. That field exists so a search for
    'pergola' outranks a passing mention on the home page.
    """
    from . import site_search as search
    print("\nThe repeated block is recognised as template, not content:")
    read = [("u1", "", "Home", ["Welcome", "01. Glass Installation", "Color Switcher"]),
            ("u2", "", "FAQ", ["Frequently Asked", "01. Glass Installation", "Color Switcher"]),
            ("u3", "", "Contact", ["Contact Us", "01. Glass Installation", "Color Switcher"])]
    boiler = search._repeated_headings(read)
    check("the repeated service heading is template",
          "01. glass installation" in boiler, True)
    check("so is the widget", "color switcher" in boiler, True)
    check("the page's own heading is not", "frequently asked" in boiler, False)

    print("\n...but two pages is not enough to call anything boilerplate:")
    check("no verdict on two", search._repeated_headings(read[:2]), set())

    print("\n...so each page gets its own service, or honestly none:")
    real = search._fetch
    pages = {
        "https://wd.example": _php("Home", "Best Aluminium Glass Company"),
        "https://wd.example/service.php":
            _php("Services", "Services", "<h2>Pergola and Shades</h2>"),
        "https://wd.example/contact.php": _php("Contact", "Contact Us"),
    }
    search._fetch = lambda u, timeout=15, allow_paid=None: (
        pages.get(u, ""), "" if u in pages else "HTTP 404")
    try:
        recs, _e = search.build_records("https://wd.example", max_pages=8)
    finally:
        search._fetch = real
    by = {r["path"]: r["service"] for r in recs}
    check("the services page is the pergola page", by.get("/service.php"),
          "pergola")
    check("the contact page claims no service at all", by.get("/contact.php"), "")
    check("and the nav block is out of the indexed headings",
          any("Color Switcher" in h
              for r in recs for h in r["headings"]), False)

    print("\n...and the title is readable, not HTML:")
    check("entities decoded",
          all("&amp;" not in r["title"] for r in recs), True)


def test_a_404_is_not_a_javascript_page():
    """
    A missing page has a short body, so the shell test fired and reported
    "built by JavaScript". The remedy that points at is switching on paid
    rendering — so he would have paid Zyte, per page, to render pages that do
    not exist. Rendering cannot conjure a page the server does not have.
    """
    from . import fetcher
    print("\nA page that is not there is not a page that needs rendering:")
    # A host that really resolves, because safe_url checks DNS before anything
    # else — and nothing leaves the machine, since _plain is replaced.
    real = fetcher._plain
    fetcher._plain = lambda url, timeout: (
        "<html><body><h1>404 Not Found</h1></body></html>", "", 404)
    try:
        r = fetcher.fetch("https://example.com/services-that-are-not-there",
                          allow_paid=False)
    finally:
        fetcher._plain = real
    check("not ok", r["ok"], False)
    check("it says what it is", "404" in r["error"], True)
    check("and that there is nothing to render",
          "Nothing to render" in r["error"], True)
    check("so it never lands on the 'would have cost' list",
          "declined" in r, False)


def test_a_canonical_tag_pointing_at_another_domain_is_caught():
    """
    THE WORST THING ON HIS SITE, AND NOTHING WAS LOOKING FOR IT.

    All eight of his pages carried
        <link rel="canonical" href="https://worlddealtechnicalservices.ae/...">
    while the site he runs is .com. The .ae domain has no DNS record at all.

    A canonical tag is not a hint. It tells Google "the real copy of this page
    is over there, index that instead of me." Every page was pointing at a
    domain that does not exist. No amount of markup or content outranks a page
    asking not to be indexed.

    The first version of this check REPORTED ALL CLEAR on it. It compared
    harvest.brand(), which strips the TLD on purpose — right for deciding
    whether a .com site's .ae email belongs to the same business, and exactly
    wrong here, where .ae and .com are two different websites.
    """
    from . import site_check as C
    print("\nwww and the bare domain are the same site:")
    check("www stripped", C._bare("www.example.ae"), "example.ae")
    check("bare left alone", C._bare("example.ae"), "example.ae")
    check("a trailing dot is not a different host", C._bare("example.ae."),
          "example.ae")
    print("  ...but a different TLD is a different website:")
    check("not equal", C._bare("example.ae") == C._bare("example.com"), False)

    def _page(canon):
        tag = f'<link rel="canonical" href="{canon}">' if canon else ""
        return f"<html><head><title>t</title>{tag}</head><body>hi</body></html>"

    def _run(pages, host="https://www.mysite.com"):
        real_fetch, real_own = C.__dict__.get("_x"), None
        from . import fetcher, site_search
        rf, ro = fetcher.fetch, site_search.own_pages
        fetcher.fetch = lambda u, **kw: {
            "ok": True, "html": pages.get(u, pages.get(host, "")), "cost": 0.0,
            "how": "plain", "error": ""}
        site_search.own_pages = lambda html, base, limit=40: [
            u for u in pages if u != base]
        try:
            return C.step_canonical(host)
        finally:
            fetcher.fetch, site_search.own_pages = rf, ro

    print("\nPages pointing at a dead domain are a FAILURE, not a note:")
    host = "https://www.mysite.com"
    r = _run({host: _page("https://mysite.ae/"),
              host + "/about.php": _page("https://mysite.ae/about.php")})
    check("FAIL", r["state"], C.FAIL)
    check("it names the domain", "mysite.ae" in r["says"], True)
    check("and says it does not exist", "does not exist" in r["says"], True)
    check("and why that matters", "do not rank" in r["says"], True)
    check("listing the pages", len(r["wrong"]), 2)

    print("\n...pointing at your own site, www or not, is correct:")
    r = _run({host: _page("https://mysite.com/"),
              host + "/about.php": _page("https://www.mysite.com/about.php")})
    check("ok", r["state"], C.OK)

    print("\n...and no canonical at all is fine, not a problem to invent:")
    r = _run({host: _page(""), host + "/about.php": _page("")})
    check("ok", r["state"], C.OK)
    check("but it is mentioned", "no canonical tag" in r["says"], True)


def main():
    test_the_site_address_survives_a_fresh_clone()
    test_only_service_looking_text_is_taken()
    test_entities_are_decoded()
    test_matching_uses_names_not_buyer_signals()
    test_reconciliation_splits_three_ways()
    test_critic_will_not_certify_an_unread_site()
    test_no_site_configured()
    test_only_our_own_site_is_followed()
    test_site_search_never_publishes_the_write_key()
    test_the_fetcher_pays_only_when_it_has_to()
    test_the_owners_own_site_is_read_for_free()
    test_the_indexer_reads_the_pages_he_has_not_the_ones_it_imagined()
    test_the_same_page_under_two_names_is_indexed_once()
    test_a_heading_on_every_page_describes_the_site_not_the_page()
    test_a_404_is_not_a_javascript_page()
    test_a_canonical_tag_pointing_at_another_domain_is_caught()

    print()
    if _fail:
        print(f"{len(_fail)} FAILED: {_fail}")
        sys.exit(1)
    print("all site tests passed")


if __name__ == "__main__":
    main()
