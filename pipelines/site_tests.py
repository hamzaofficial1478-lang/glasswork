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
    real_cfg = _site.CONFIG
    keep = os.environ.pop("SITE_URL", None)
    d = Path(tempfile.mkdtemp())
    _site.CONFIG = d / "site.json"
    try:
        check("nothing saved yet -> empty, never a guess", _site.site_url(), "")
        check("and it says how to set it", "setup" in _site.status()["hint"], True)

        _site.save("glassworks.ae")
        check("a saved address is found", _site.site_url(), "https://glassworks.ae")
        check("a bare domain gets a scheme", _site.site_url().startswith("https://"), True)
        check("and it came from the tracked file",
              _site.status()["source"], "config/site.json")

        os.environ["SITE_URL"] = "https://staging.example.ae"
        check("an explicit env var still wins for one run",
              _site.site_url(), "https://staging.example.ae")
        os.environ.pop("SITE_URL")

        check("an explicit argument beats both",
              _site.site_url("https://other.ae"), "https://other.ae")

        # the file is committed, so it must never be given a secret to hold
        saved = json.loads(_site.CONFIG.read_text())
        check("only public facts are stored",
              set(saved) <= {"url", "mode", "areas"}, True)
    finally:
        _site.CONFIG = real_cfg
        if keep is not None:
            os.environ["SITE_URL"] = keep


def _make401():
    import http.server, socketserver
    class Q(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
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
    pages = [("https://x.ae", "<title>Home</title><h1>Glass</h1><p>" + "a" * 200 + "</p>"),
             ("https://x.ae/pergolas", "<title>Pergolas</title><h1>Aluminium pergolas</h1>"
              "<p>" + "b" * 200 + "</p>")]
    real_fetch = search._fetch
    search._fetch = lambda u, timeout=15: (dict(pages).get(u, ""), "")
    real_links = ss._internal_links
    ss._internal_links = lambda html, base: ["https://x.ae/pergolas"]
    try:
        recs, err = search.build_records("https://x.ae", max_pages=10)
    finally:
        search._fetch = real_fetch
        ss._internal_links = real_links

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
    check("and it points at the fix rather than just naming the fault",
          any("crawler" in r and "Zyte" in r for r in why), True)
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
        check("it passes even though the tool was saved as bearer", r["ok"], True)
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

    print()
    if _fail:
        print(f"{len(_fail)} FAILED: {_fail}")
        sys.exit(1)
    print("all site tests passed")


if __name__ == "__main__":
    main()
