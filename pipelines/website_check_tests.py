"""
website_check_tests.py — run: python -m pipelines.website_check_tests

"make a report maker ... any other website ... analyze it by its all sector"

What is protected here:
  - it only ever reads: every request is a GET or a HEAD, sent as itself (its
    own User-Agent), and a session asked to send anything else refuses;
  - it reads politely: robots.txt is followed — a page it disallows is never
    asked for — its Crawl-delay is honoured, and the request budget is a
    hard stop;
  - it cannot be pointed inwards: this computer and its local network are
    refused unless a test lets them through;
  - each desk finds what a poor website gets wrong and a good one gets right,
    with the page and the evidence, and the scores and the top fixes follow;
  - security is passive: what the browser receives, never a probe for
    hidden files;
  - a second check of the same website says what was fixed and what is new;
  - one check at a time, a stopped check stops, and one whose process has
    gone does not say 'running' for ever;
  - the report page puts the website's words on the page as text, never as
    markup, carries the user's name and logo, and a logo that is not the
    picture it claims to be is refused;
  - the PDF is made by the browser on this computer, when there is one;
  - and the office starts a check, shows its progress and serves the report.

Nothing here reaches a real website: every site is a stand-in on this
computer.
"""
import base64
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ.setdefault("AGENCY_DB", str(Path(tempfile.mkdtemp(prefix="check-db-")) / "agency.db"))
os.environ["WEBSITE_CHECK_ALLOW_LOCAL"] = "1"
os.environ["WEBSITE_CHECK_NO_TLS"] = "1"
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost,::1"

from coordination import workspace  # noqa: E402

from . import website_check as wc  # noqa: E402

ROOT = Path(__file__).parents[1]
wc.GAP = 0.01
_fail = []


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


# ---------------------------------------------------------------------------
# stand-in websites
# ---------------------------------------------------------------------------

GOOD = {
    "/robots.txt": "User-agent: *\nDisallow: /private/\nSitemap: /sitemap.xml\n",
    "/sitemap.xml": "",           # filled in with the port
    "/": """<!doctype html><html lang="en"><head><title>Clearview Glass | Shower screens in Leeds</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="description" content="Made-to-measure shower screens, fitted across Leeds.">
<link rel="canonical" href="/">
<script type="application/ld+json">{"@context":"https://schema.org","@type":"LocalBusiness","name":"Clearview Glass",
"telephone":"+44 113 000 0000","address":{"@type":"PostalAddress","streetAddress":"12 Park Road","addressLocality":"Leeds",
"postalCode":"LS1 2AB"},"areaServed":["Leeds","Bradford"]}</script></head><body>
<a href="/about">About</a> <a href="/projects">Our projects</a> <a href="/contact">Contact us</a> <a href="/private/notes">x</a>
<h1>Shower screens in Leeds</h1>
<p>Clearview makes and fits frameless shower screens across Leeds, usually within 10 days of measuring.</p>
<a href="/contact">Get a quote</a> <a href="tel:+441130000000">0113 000 0000</a>
<a href="https://wa.me/441130000000">WhatsApp us</a>
<h2>How long does fitting take?</h2><p>Most screens are fitted in 4 hours. We have fitted over 900 since 2011.</p>
<ul><li>Measure</li><li>Make</li><li>Fit</li></ul>
<p>What our customers say: "Brilliant job" — Sarah, Headingley. Rated 4.9 stars.</p>
<footer>12 Park Road, Leeds LS1 2AB · © 2026</footer></body></html>""",
    "/about": ("<!doctype html><html lang='en'><head><title>About Clearview</title>"
               "<meta name='viewport' content='width=device-width'><meta name='description' content='Who we are.'>"
               "</head><body><h1>About us</h1><p>" + "We have fitted glass in Leeds for fifteen years. " * 30
               + "</p></body></html>"),
    "/projects": ("<!doctype html><html lang='en'><head><title>Recent projects</title>"
                  "<meta name='viewport' content='width=device-width'><meta name='description' content='Our work.'>"
                  "</head><body><h1>Recent work</h1><p>" + "A walk-in screen in Roundhay, fitted in a morning. " * 20
                  + "</p></body></html>"),
    "/contact": ("<!doctype html><html lang='en'><head><title>Contact Clearview</title>"
                 "<meta name='viewport' content='width=device-width'><meta name='description' content='Ring or write.'>"
                 "</head><body><h1>Contact</h1><form action='/send' method='post'><input name='name'>"
                 "<textarea name='msg'></textarea></form></body></html>"),
}
GOOD_HEADERS = {"Strict-Transport-Security": "max-age=31536000", "Content-Security-Policy": "default-src 'self'",
                "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "same-origin"}

POOR = {
    "/robots.txt": "User-agent: *\nDisallow: /private/\n\nUser-agent: GPTBot\nDisallow: /\n",
    "/": """<!doctype html><html><head><title>Home</title><meta name="generator" content="WordPress 5.2.1">
<script src="http://cdn.example.org/jquery-1.12.4.min.js"></script></head><body>
<a href="/about">About</a> <a href="/gone">Old offers</a> <a href="/private/x">x</a> <a href="/services">Services</a>
<p>Phone 050 123 4567</p><p>Copyright © 2019 Somebody</p></body></html>""",
    "/about": "<!doctype html><html><head><title>Home</title></head><body><p>We are good.</p></body></html>",
    "/services": ("<!doctype html><html><head><title>Services</title></head><body>"
                  "<form action='http://example.org/s'><input name='q'></form><p>Glass.</p></body></html>"),
}
POOR_HEADERS = {"Server": "Apache/2.4.29 (Ubuntu) <b>hi</b>", "X-Powered-By": "PHP/7.1.3"}


class Site:
    """A website on this computer that writes down every request it is sent."""

    def __init__(self, pages, headers=None):
        self.pages, self.headers, self.seen = dict(pages), dict(headers or {}), []

    def __enter__(self):
        site = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _answer(self, body=True):
                site.seen.append((self.command, self.path, self.headers.get("User-Agent", "")))
                path = self.path.split("?", 1)[0]
                if path in site.pages and site.pages[path] != "":
                    text, code = site.pages[path], 200
                else:
                    text, code = "<html><body>Not found</body></html>", 404
                b = text.encode("utf-8")
                ct = "text/plain" if path == "/robots.txt" else (
                    "application/xml" if path.endswith(".xml") else "text/html; charset=utf-8")
                self.send_response(code)
                self.send_header("Content-Type", ct)
                self.send_header("Content-Length", str(len(b)))
                for k, v in site.headers.items():
                    self.send_header(k, v)
                self.end_headers()
                if body:
                    self.wfile.write(b)

            def do_GET(self):
                self._answer()

            def do_HEAD(self):
                self._answer(False)

            def do_POST(self):
                self._answer()

            def do_PUT(self):
                self._answer()

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        if "/sitemap.xml" in self.pages:
            locs = "".join(f"<url><loc>{self.url}{p}</loc></url>" for p in self.pages
                           if not p.endswith((".txt", ".xml")))
            self.pages["/sitemap.xml"] = ('<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/'
                                          f'schemas/sitemap/0.9">{locs}</urlset>')
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *a):
        self.srv.shutdown()
        self.srv.server_close()


def _codes(rep):
    return {f["code"] for f in rep["findings"]}


def _find(rep, code):
    return next((f for f in rep["findings"] if f["code"] == code), {})


def _clear_checks():
    c = wc._conn()
    with c:
        c.execute("DELETE FROM website_checks")
    c.close()


class _Account:
    """A data folder and an account of its own, so the logo is never written into the real config."""

    def __enter__(self):
        self.saved = {k: os.environ.get(k) for k in ("AGENCY_DATA", "AGENCY_KEY")}
        self.dir = Path(tempfile.mkdtemp(prefix="check-acct-"))
        os.environ["AGENCY_DATA"] = str(self.dir / "data")
        os.environ.pop("AGENCY_KEY", None)
        acct = workspace.create_account("Reports")["account"]["id"]
        site = workspace.add_site(acct, "Reports", "https://example.co.uk")["site"]["id"]
        workspace.open_site(acct, site)
        return self

    def __exit__(self, *a):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _png(w=40, h=20):
    raw = b"".join(b"\x00" + bytes([31, 95, 102, 255]) * w for _ in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


# ---------------------------------------------------------------------------
# the tests
# ---------------------------------------------------------------------------

def test_it_cannot_be_pointed_inwards():
    print("\nThis computer and its network are refused; a typed address is tidied:")
    os.environ.pop("WEBSITE_CHECK_ALLOW_LOCAL", None)
    try:
        for bad in ("localhost", "http://127.0.0.1:3000", "10.0.0.5", "192.168.1.1", "http://[::1]/",
                    "printer.local", "169.254.169.254"):
            try:
                wc.normalise(bad)
                got = "allowed"
            except wc.NotAllowed as e:
                got = "refused" if "public websites only" in str(e) else str(e)
            check(f"{bad} is refused", got, "refused")
        for bad, why in (("", "type the website"), ("ftp://example.com", "not a website address")):
            try:
                wc.normalise(bad)
                got = "allowed"
            except wc.NotAllowed as e:
                got = str(e)
            check(f"{bad!r} is refused in words", why in got, True)
    finally:
        os.environ["WEBSITE_CHECK_ALLOW_LOCAL"] = "1"
    check("a typed address becomes the site's front door",
          wc.normalise("WWW.Example.com/about?x=1"), "https://www.example.com")
    check("  and http:// is kept when typed", wc.normalise("http://example.com:8080/"), "http://example.com:8080")
    check("a check of a refused address is never made", wc.new("ftp://example.com").get("ok"), False)


def test_it_only_ever_reads():
    print("\nA session that only reads, waits its turn, and stops at its budget:")
    with Site({"/": "<html><body>hi</body></html>"}) as site:
        s = wc.PoliteSession(gap=0.2, budget=3)
        for method in ("POST", "PUT", "DELETE", "PATCH"):
            try:
                s.request(method, site.url + "/")
                got = "sent"
            except wc.NotAllowed:
                got = "refused"
            check(f"{method} is refused before anything is sent", got, "refused")
        t0 = time.time()
        s.get(site.url + "/")
        s.head(site.url + "/")
        check("two requests are at least the gap apart", time.time() - t0 >= 0.2, True)
        s.get(site.url + "/")
        try:
            s.get(site.url + "/")
            got = "sent"
        except wc.NotAllowed:
            got = "refused"
        check("past its budget it sends nothing more", got, "refused")
        check("the website saw three reads and nothing else",
              [m for m, _p, _u in site.seen], ["GET", "HEAD", "GET"])
        check("  each sent as itself", {u for _m, _p, u in site.seen}, {wc.UA})


def test_robots_txt_is_followed():
    print("\nrobots.txt is followed, its Crawl-delay too:")
    _clear_checks()
    with Site(POOR, POOR_HEADERS) as site:
        made = wc.new(site.url)
        rep = wc.run(made["id"])
        check("the check finishes", rep.get("ok"), True)
        paths = [p for _m, p, _u in site.seen]
        check("a page robots.txt disallows is never asked for", [p for p in paths if p.startswith("/private")], [])
        check("robots.txt is read first", paths[0], "/robots.txt")
        check("only GET and HEAD reach the website", {m for m, _p, _u in site.seen} <= {"GET", "HEAD"}, True)
        check("  and the enquiry form was never sent", "/s" in paths or any("example.org" in p for p in paths), False)
    slow = {"/robots.txt": "User-agent: *\nCrawl-delay: 1\n",
            "/": "<html><body><a href='/a'>a</a></body></html>", "/a": "<html><body>a</body></html>"}
    with Site(slow) as site:
        s = wc.PoliteSession(gap=0.01, budget=20)
        stamps = []
        orig = s.request

        def timed(method, url, *a, **k):
            r = orig(method, url, *a, **k)
            stamps.append(time.time())
            return r
        s.request = timed
        wc.crawl(site.url, 3, 2, time.time() + 60, session=s)
        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        check("a Crawl-delay of one second is kept between requests",
              bool(gaps) and min(gaps[1:] or gaps) >= 0.9, True)


def test_the_desks_find_what_is_wrong():
    print("\nA poor website: every desk finds what it gets wrong, with the evidence:")
    _clear_checks()
    with Site(POOR, POOR_HEADERS) as site:
        rep = wc.run(wc.new(site.url)["id"])
    codes = _codes(rep)
    for code, desk in (("http_error", "Speed and health"), ("broken_internal_link", "Speed and health"),
                       ("no_viewport", "Speed and health"), ("no_sitemap", "Found on Google"),
                       ("duplicate_title", "Found on Google"), ("missing_h1", "Found on Google"),
                       ("no_lang", "Found on Google"), ("thin_pages", "Words and trust"),
                       ("stale_copyright", "Words and trust"), ("no_reviews", "Words and trust"),
                       ("ai_blocked", "Picked by AI answers"), ("faq", "Picked by AI answers"),
                       ("no_click_to_call", "Easy to get in touch"), ("no_whatsapp", "Easy to get in touch"),
                       ("no_contact_page", "Easy to get in touch"), ("no_cta", "Easy to get in touch"),
                       ("no_https", "Security, from outside"), ("version_shown", "Security, from outside"),
                       ("old_library", "Security, from outside"), ("generator_shown", "Security, from outside"),
                       ("form_insecure", "Security, from outside")):
        f = _find(rep, code)
        check(f"{desk}: {f.get('title') or code}", bool(f) and f["sector_name"] == desk, True)
    check("a broken link names the page it was on", "/gone" in _find(rep, "broken_internal_link").get("pages", []), True)
    check("the old jQuery is named", "jQuery 1.12" in _find(rep, "old_library").get("evidence", ""), True)
    check("the blocked AI assistant is named", "GPTBot" in _find(rep, "ai_blocked").get("evidence", ""), True)
    check("the untappable number is quoted", "050 123 4567" in _find(rep, "no_click_to_call").get("evidence", ""), True)
    check("the old year is quoted", "2019" in _find(rep, "stale_copyright").get("evidence", ""), True)
    check("every finding says why, what to do, and how much it matters",
          all(f["why"] and f["fix"] and f["impact"] and f["effort_label"] for f in rep["findings"]), True)
    check("six desks, six scores", sorted(rep["scores"]), sorted(k for k, _ in wc.SECTORS))
    check("the overall score is their average",
          rep["overall"], int(round(sum(rep["scores"].values()) / 6)))
    check("five fixes at the top, each a real finding",
          len(rep["top"]) == 5 and all(k in {f["code"] + "|" + f["sector"] for f in rep["findings"]}
                                       for k in rep["top"]), True)
    top = [next(f for f in rep["findings"] if f["code"] + "|" + f["sector"] == k) for k in rep["top"]]
    check("  led by what matters a lot", top[0]["severity"], "high")
    check("a poor site is told so", rep["overall"] < 70, True)
    return rep


def test_a_good_website_scores_well(poor):
    print("\nA good website: what it does right is found, and it scores well:")
    _clear_checks()
    with Site(GOOD, GOOD_HEADERS) as site:
        rep = wc.run(wc.new(site.url)["id"])
        paths = [p for _m, p, _u in site.seen]
    codes = _codes(rep)
    check("its sitemap is used", "/sitemap.xml" in paths, True)
    for code in ("no_click_to_call", "no_phone", "no_form", "no_whatsapp", "no_contact_page", "no_cta",
                 "no_reviews", "no_real_work", "no_address", "faq", "aeo.localbusiness", "no_sitemap",
                 "missing_headers", "no_viewport", "broken_internal_link", "ai_blocked", "jsonld_syntax_error"):
        check(f"no '{code}' on a site that has it", code in codes, False)
    working = " ".join(w["text"] for w in rep["working"])
    for said in ("tap on a phone", "enquiry form", "WhatsApp", "Reviews", "Real work", "sitemap",
                 "security headers", "AI assistants may read"):
        check(f"what works is said: {said}", said in working, True)
    check("easy to get in touch scores full marks", rep["scores"]["leads"], 100)
    check("and it beats the poor site overall", rep["overall"] > poor["overall"], True)
    check("security was passive: no hidden file was asked for",
          [p for p in paths if p.startswith(("/.env", "/.git", "/wp-config", "/admin", "/backup"))], [])


def test_progress_since_last_time():
    print("\nA second check of the same website says what changed:")
    _clear_checks()
    pages = dict(POOR)
    with Site(pages, POOR_HEADERS) as site:
        first = wc.run(wc.new(site.url)["id"])
        check("the first check has nothing to compare with", first.get("progress"), None)
        site.pages["/"] = site.pages["/"].replace("<p>Phone 050 123 4567</p>",
                                                  '<p><a href="tel:+971501234567">050 123 4567</a></p>')
        second = wc.run(wc.new(site.url)["id"])
    p = second.get("progress") or {}
    check("the second knows the first", p.get("previous_id"), first["id"])
    check("the fixed problem is listed as fixed", "The phone number cannot be tapped" in p.get("fixed", []), True)
    check("and the score went up", p.get("overall_change", 0) > 0, True)
    check("nothing new was claimed", p.get("new"), [])
    listed = wc.listing()["checks"]
    check("both are kept, newest first", [c["id"] for c in listed], [second["id"], first["id"]])


def test_one_at_a_time_and_stopping():
    print("\nOne check at a time; a stopped check stops; a vanished one is not 'running' for ever:")
    _clear_checks()
    a = wc.new("https://www.example.com")
    b = wc.new("https://www.example.org")
    check("a second check waits for the first", (b.get("ok"), b.get("busy")), (False, True))
    check("  and says so in words", "still running" in b.get("error", ""), True)
    check("stopping it works", wc.stop(a["id"]).get("ok"), True)
    got = wc.get(a["id"])
    check("  and it says it was stopped by you", (got["status"], got["error"]), ("failed", "stopped by you"))
    c = wc.new("https://www.example.net")
    check("then another may start", c.get("ok"), True)
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    wc._set(c["id"], pid=gone.pid, machine="")
    got = wc.get(c["id"])
    check("a check whose process has gone is marked as stopped", got["status"], "failed")
    check("  with the reason, in words", "stopped before it finished" in got["error"], True)
    d = wc.new("https://www.example.net")
    wc._set(d["id"], started_at=int(time.time()) - (wc.FULL_MINUTES + 11) * 60)
    check("one far past its time limit is marked as stopped", wc.get(d["id"])["status"], "failed")
    check("deleting a check deletes it", (wc.delete(d["id"])["ok"], wc.get(d["id"])["ok"]), (True, False))
    _clear_checks()


def test_the_report_page():
    print("\nThe report: the website's words as text, the user's name and logo on top:")
    _clear_checks()
    hostile = dict(POOR)
    hostile["/"] = hostile["/"].replace("<title>Home</title>", "<title><script>alert(1)</script></title>")
    with _Account(), Site(hostile, POOR_HEADERS) as site:
        cid = wc.new(site.url)["id"]
        wc.run(cid)
        logo = "data:image/png;base64," + base64.b64encode(_png()).decode()
        saved = wc.save_brand("Clearview <img src=x onerror=alert(1)>", logo)
        check("the name and logo are saved", (saved["ok"], saved["has_logo"]), (True, True))
        check("  in this account's own config, not the program's",
              workspace.config_path(wc.BRAND_FILE).is_relative_to(Path(os.environ["AGENCY_DATA"])), True)
        page = wc.report_html(wc.get(cid))
        check("the logo is on the report", f'src="{logo}"' in page, True)
        check("the name is text, not markup", "<img src=x" not in page and "&lt;img src=x" in page, True)
        check("the server's own words are text too", "<b>hi</b>" not in page and "&lt;b&gt;hi&lt;/b&gt;" in page, True)
        check("nothing from the website runs on the report", "<script>alert" not in page, True)
        check("it is one page that needs nothing from elsewhere",
              "http://" not in page.split("<body>")[0] and "https://" not in page.split("<body>")[0], True)
        check("it says how the check was done", "changed nothing" in page and "passively" in page, True)
        for bad, why in (("data:image/png;base64," + base64.b64encode(b"GIF89a....").decode(), "not the picture"),
                         ("data:image/svg+xml;base64,PHN2Zz4=", "PNG, JPEG or WebP"),
                         ("data:image/png;base64," + base64.b64encode(b"\x89PNG" + b"0" * 700_000).decode(), "too large")):
            r = wc.save_brand(logo=bad)
            check(f"a logo that is {why} is refused", (r["ok"], why in r.get("error", "")), (False, True))
        check("the good logo is still there after the refusals", wc.brand().get("logo"), logo)
        r = wc.save_brand(remove_logo=True)
        check("and it can be taken off", (r["ok"], r["has_logo"], "Clearview" in wc.brand().get("name", "")),
              (True, False, True))
        if wc.find_browser():
            out = Path(tempfile.mkdtemp(prefix="check-pdf-")) / "report.pdf"
            got = wc.pdf(cid, out)
            check("the browser on this computer makes the PDF",
                  got.get("ok") and out.read_bytes()[:5] == b"%PDF-", True)
            check("  named after the website, safe on Windows", ":" in got.get("filename", ":"), False)
        else:
            print("  SKIP  no Chrome or Edge on this computer to make a PDF")
            check("without a browser it says what to do instead", "Print" in wc.pdf(cid, "x.pdf").get("error", ""), True)


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _get(url, timeout=30):
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.headers.get("Content-Type", ""), r.read()


def _post(url, body, timeout=60):
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def test_the_office_runs_a_check():
    print("\nThe office starts a check, shows its progress and serves the report:")
    if not shutil.which("node"):
        print("  SKIP  Node is not installed here")
        return
    tmp = Path(tempfile.mkdtemp(prefix="check-office-"))
    port = _free_port()
    env = {k: v for k, v in os.environ.items() if k not in ("AGENCY_KEY",)}
    env.update(AGENCY_DATA=str(tmp / "data"), AGENCY_DB=str(tmp / "agency.db"), AGENCY_IGNORE_DOTENV="1",
               CLAUDE_TEAM_OFF="1", PORT=str(port), WEBSITE_CHECK_ALLOW_LOCAL="1", WEBSITE_CHECK_NO_TLS="1",
               NO_PROXY="127.0.0.1,localhost,::1", no_proxy="127.0.0.1,localhost,::1")
    proc = subprocess.Popen(["node", str(ROOT / "server.js")], cwd=str(ROOT), env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(120):
            try:
                if json.loads(_get(base + "/api/ping", 2)[2]).get("office"):
                    break
            except Exception:
                time.sleep(0.25)
        ping = json.loads(_get(base + "/api/ping")[2])
        check("the office says it can check websites", "check" in ping.get("api", []), True)
        small = {"/robots.txt": "User-agent: *\nDisallow:\n",
                 "/": "<!doctype html><html lang='en'><head><title>Hi</title></head><body><a href='/a'>a</a></body></html>",
                 "/a": "<!doctype html><html lang='en'><head><title>A</title></head><body>a</body></html>"}
        with Site(small) as site:
            refused = _post(base + "/api/check/start", {"url": "", "depth": "quick"})
            check("an empty address is refused in words", (refused["ok"], "address" in refused["error"]), (False, True))
            started = _post(base + "/api/check/start", {"url": site.url, "depth": "quick"})
            check("a check starts", started.get("ok"), True)
            cid = started.get("id")
            busy = _post(base + "/api/check/start", {"url": site.url, "depth": "quick"})
            check("a second one waits its turn", busy.get("busy"), True)
            state, end = {}, time.time() + 120
            while time.time() < end:
                state = json.loads(_get(f"{base}/api/check/{cid}")[2])
                if state.get("status") != "running":
                    break
                time.sleep(0.5)
            check("it finishes on its own, carried on by its own process", state.get("status"), "done")
            check("  with a score", isinstance((state.get("report") or {}).get("overall"), int), True)
        listed = json.loads(_get(base + "/api/check/list")[2])
        check("it is in the list", [c["id"] for c in listed.get("checks", [])], [cid])
        code, ctype, body = _get(f"{base}/check/{cid}/report")
        check("the report is served as a page", (code, "html" in ctype, b"Website Check" in body), (200, True, True))
        try:
            _get(base + "/check/abc/report")
            got = 200
        except urllib.error.HTTPError as e:
            got = e.code
        check("a report that is not a number is not found", got, 404)
        if wc.find_browser():
            code, ctype, body = _get(f"{base}/api/check/{cid}/pdf", 150)
            check("the PDF downloads", (code, "pdf" in ctype, body[:5]), (200, True, b"%PDF-"))
        else:
            print("  SKIP  no Chrome or Edge on this computer to make a PDF")
        gone = _post(f"{base}/api/check/{cid}/delete", {})
        check("and it can be deleted", gone.get("ok"), True)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


def main():
    test_it_cannot_be_pointed_inwards()
    test_it_only_ever_reads()
    test_robots_txt_is_followed()
    poor = test_the_desks_find_what_is_wrong()
    test_a_good_website_scores_well(poor)
    test_progress_since_last_time()
    test_one_at_a_time_and_stopping()
    test_the_report_page()
    test_the_office_runs_a_check()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        return 1
    print("All website check tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
