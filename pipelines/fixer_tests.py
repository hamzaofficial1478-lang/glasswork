"""
fixer_tests.py — run: python -m pipelines.fixer_tests

The Fix button (pipelines/seo_fixer.py), on a real PHP website served on this
machine with the real connector in it — nothing reaches the internet.

"the program will fix those issues and will report to the user that if issue
 had solved or not or if issue had solved then how much is it solved no lies
 the report should be authentic"

What is protected:
  - pressing Fix changes the file that really makes each page (index.php, not
    the old index.html beside it), through the Releaser only, and the result is
    read off the live pages — "fixed" is what the live site shows, nothing else;
  - pages sharing one PHP header each get their own title from it (the
    per-page block and botix-meta.php), the header's own title kept as the
    fallback, and the PHP still runs;
  - a page's own head is edited in place; pictures get descriptions; the
    copyright year stops going out of date;
  - the report counts honestly: pages already right are "already right", not
    fixed by us; a page that could not be changed says why; part-fixed says how
    many of how many; a change stuck in a cache says so and Check again finds it
    later; a fix waiting on the tests says it is waiting;
  - a change that breaks the home page is put back at once, file for file;
  - what the button cannot do safely is handed to the desk that owns it, and
    the report says "not fixed — handed on", never "fixed".
"""
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from coordination import board
from . import it_release, seo_fixer as F, site_api as S, site_blueprint as bp, site_map

ROOT = Path(__file__).parents[1]
TOKEN = "fixer-test-token-" + "y" * 30
_fail = []

WORDS = ("We measure, make and fit frameless shower screens, glass partitions and aluminium doors. "
         "Every job is measured on site by the person who will fit it. Toughened glass is cut to the millimetre "
         "and checked before it leaves the workshop. Fitting usually takes one morning, and we leave the room "
         "clean. Hinges come in chrome, brushed nickel or matt black, and every quote lists the glass thickness. ")


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


def _port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


HEADER = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<title><?php echo isset($title) ? $title : 'Glass Co'; ?></title>
<meta name="viewport" content="width=device-width, initial-scale=1">
</head>
<body>
<header><div class="logo">Glass Co</div><nav><a href="/">Home</a> <a href="/about.php">About us</a>
<a href="/services.php">Our services</a> <a href="/plain.html">Price guide</a></nav></header>
"""
FOOTER = "<footer><p>&copy; 2019 Glass Co. Dubai.</p></footer>\n</body>\n</html>\n"


def _page(h1, extra=""):
    return (f"<?php include __DIR__ . '/includes/header.php'; ?>\n<main><h1>{h1}</h1><p>{WORDS}</p>{extra}</main>\n"
            "<?php include __DIR__ . '/includes/footer.php'; ?>\n")


FILES = {
    "includes/header.php": HEADER,
    "includes/footer.php": FOOTER,
    "index.php": "<?php $title = 'Glass Co'; ?>" + _page("Glass and aluminium work in Dubai",
                                                         '<img src="/img/frameless-shower-screen-marina.jpg">'
                                                         '<img src="/img/IMG_4031.jpg" alt="image">'),
    # An old copy beside the real home page, with the same header words.
    "index.html": "<!doctype html><html><head><title>Glass Co</title></head><body><header>Glass Co Home About us "
                  "Our services</header><p>Old site, coming soon.</p></body></html>",
    "about.php": _page("About Glass Co, fitters since 2012"),
    "services.php": _page("Shower screens, partitions and doors"),
    # Its own head, plain HTML: changed in place.
    "plain.html": ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><title>Price guide for glass "
                   "work in Dubai | Glass Co</title><meta name=\"viewport\" content=\"width=device-width, "
                   "initial-scale=1\"></head><body><nav><a href=\"/\">Home</a></nav><main><h1>Price guide</h1><p>"
                   + WORDS + "</p></main></body></html>"),
    # Hidden on purpose, and hidden by mistake.
    "thanks.php": ("<html><head><title>Thanks</title><meta name=\"robots\" content=\"noindex, nofollow\"></head>"
                   "<body><p>Thank you, we will call you back.</p><a href=\"/\">Home</a></body></html>"),
    "doors.php": ("<!doctype html><html lang=\"en\"><head><title>Aluminium doors made to measure | Glass Co</title>"
                  "<meta name=\"robots\" content=\"noindex\"></head><body><h1>Aluminium doors</h1><p>" + WORDS
                  + "</p><a href=\"/\">Home</a></body></html>"),
}


class Site:
    """A PHP website with the connector, served on this machine; the cabinet on a throwaway file."""

    def __enter__(self):
        self.dir = Path(tempfile.mkdtemp())
        self.web = self.dir / "public_html"
        (self.web / "agency-api").mkdir(parents=True)
        (self.web / "includes").mkdir()
        (self.web / "img").mkdir()
        shutil.copy(ROOT / "paste-into-your-site" / "agency-api.php", self.web / "agency-api" / "index.php")
        (self.web / "agency-api" / "token.php").write_text(f"<?php\nreturn '{TOKEN}';\n", encoding="utf-8")
        for name, body in FILES.items():
            (self.web / name).write_text(body, encoding="utf-8")
        # the hidden-by-mistake page is linked from the home page, as a real site would
        idx = (self.web / "index.php").read_text(encoding="utf-8")
        idx = idx.replace("</main>", '<a href="/doors.php">Doors</a> <a href="/thanks.php">Thanks</a></main>')
        (self.web / "index.php").write_text(idx, encoding="utf-8")
        self.port = _port()
        self.proc = subprocess.Popen(["php", "-S", f"127.0.0.1:{self.port}", "-t", str(self.web)],
                                     env=dict(os.environ, PHP_CLI_SERVER_WORKERS="4"),
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=0.5).close()
                break
            except OSError:
                time.sleep(0.2)
        self.home = f"http://127.0.0.1:{self.port}"
        self.env = {k: os.environ.get(k) for k in ("SITE_MODE", "SITE_API_BASE", "SITE_API_TOKEN", "SITE_URL",
                                                   "SITE_CONNECT")}
        os.environ.update({"SITE_MODE": "full", "SITE_API_BASE": self.home + "/agency-api",
                           "SITE_API_TOKEN": TOKEN, "SITE_URL": self.home, "SITE_CONNECT": "php"})
        self.real_db = board.DB
        board.DB = str(self.dir / "agency.db")
        board.ensure_schema()
        S._SEEN.clear()
        S._VERSION.clear()
        return self

    def __exit__(self, *a):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        board.DB = self.real_db
        shutil.rmtree(self.dir, ignore_errors=True)

    def text(self, name):
        return (self.web / name).read_text(encoding="utf-8")

    def live(self, path="/"):
        import requests
        return requests.get(self.home + path, timeout=10).text


def _release(limit=10):
    """The Releaser, without the half-hour suite (the suite is what is running this)."""
    return it_release.run(limit=limit, skip_tests=True)


def _think(sector, messages, **kw):
    """A stand-in AI: a fitting title or description for each item, from its heading."""
    prompt = messages[0]["content"]
    out = {}
    for line in prompt.splitlines():
        if not line.startswith("{"):
            continue
        it = json.loads(line)
        h = it.get("heading") or it.get("page_heading") or "Glass work"
        if "page title" in prompt:
            out[it["key"]] = (h + " in Dubai | Glass Co")[:58]
        elif "description for Google" in prompt:
            out[it["key"]] = (h + ". Measured, made and fitted by our own team across Dubai and Sharjah, "
                              "with clean seals and a tidy finish.")[:155]
        else:
            out[it["key"]] = "A frameless shower screen fitted in a Dubai flat"
    return {"ok": True, "output": json.dumps(out)}


def _no_ai(*a, **kw):
    return {"ok": False, "error": "no AI connected"}


def _checked(w):
    r = bp.check(home=w.home, force=True)
    return r, {g["code"]: g for g in r["gaps"]}


def _fix(code, **kw):
    st = F.start(code)
    assert st.get("ok"), st
    kw.setdefault("release", _release)
    return F.run(st["id"], **kw)


def _php_ok(path):
    p = subprocess.run(["php", "-l", str(path)], capture_output=True, text=True, encoding="utf-8")
    return p.returncode == 0


def test_shared_header_titles():
    print("\nPages sharing one PHP header each get their own title, checked on the live site:")
    with Site() as w:
        r, g = _checked(w)
        check("the site was read and mapped", (r["ok"], (r.get("map") or {}).get("home_file")), (True, "index.php"))
        check("repeated titles are found", "core-title-unique" in g, True)
        before = w.text("index.html")
        rep = _fix("core-title-unique", think=_think)
        check("it says fixed, on every page that had it", (rep["state"], rep["fixed"], rep["total"]),
              ("fixed", len(g["core-title-unique"]["pages"]), len(g["core-title-unique"]["pages"])))
        check("  and that the words came from the AI", rep["words_by"], "ai")
        head = w.text("includes/header.php")
        check("the shared header gained the marked block, its own title kept as the fallback",
              (F.MARK in head, "isset($title) ? $title : 'Glass Co'" in head), (True, True))
        check("  and the PHP still runs (php -l)", _php_ok(w.web / "includes/header.php"), True)
        meta = w.text("includes/" + F.META_FILE)
        check("each page's words live in botix-meta.php, by address", ("'/about.php'" in meta, "'/services.php'" in meta),
              (True, True))
        check("  which is valid PHP too", _php_ok(w.web / "includes" / F.META_FILE), True)
        about = re.search(r"<title>(.*?)</title>", w.live("/about.php")).group(1)
        home = re.search(r"<title>(.*?)</title>", w.live("/")).group(1)
        check("the live pages now have titles of their own", (about != home, "About Glass Co" in about), (True, True))
        check("the old index.html beside the home page was not touched", w.text("index.html"), before)
        check("the problem comes off the list", F._gap("core-title-unique")["status"], "closed")
        with board._conn() as c:
            changes = c.execute("SELECT agent, status FROM site_queue").fetchall()
        check("every write went through the Releaser's queue, approved by the button",
              {(c[0], c[1]) for c in changes}, {("it-fixer", "applied")})


def test_own_head_and_words_without_ai():
    print("\nA page with its own head is changed in place; with no AI the words come from the page:")
    with Site() as w:
        r, g = _checked(w)
        check("missing descriptions are found", "core-description-present" in g, True)
        rep = _fix("core-description-present", think=_no_ai)
        check("fixed on every page", (rep["state"], rep["fixed"] == rep["total"] - rep["already"]), ("fixed", True))
        check("  and it says the words came from each page's own lines", rep["words_by"], "page")
        plain = w.text("plain.html")
        check("plain.html got its description in its own head",
              bool(re.search(r'<meta name="description" content="[^"]{110,165}">', plain)), True)
        check("  with no offer or price in any description written", bool(F.NOT_IN_WORDS.search(
            " ".join(v for row in rep["pages"] for v in row["values"].values()))), False)
        about = w.live("/about.php")
        check("the shared-header pages show theirs live",
              bool(re.search(r'<meta name="description" content="[^"]{60,}">', about)), True)


def test_pictures_and_copyright():
    print("\nPictures get descriptions where they are written; the copyright year keeps itself current:")
    with Site() as w:
        r, g = _checked(w)
        rep = _fix("images-alt", think=_think)
        check("the picture without a description now has one", (rep["state"], 'alt="A frameless shower screen'
                                                                  in w.text("index.php")), ("fixed", True))
        r, g = _checked(w)
        check("the weak one ('image') was described in the same pass, so it is off the list too",
              ("images-alt" in g, "images-alt-quality" in g, 'alt="image"' in w.text("index.php")), (False, False, False))
        rep = _fix("content-old-copyright")
        check("the footer year is now always this year", ("<?php echo date('Y'); ?>" in w.text("includes/footer.php"),
                                                          rep["state"]), (True, "fixed"))
        check("  and the live page shows it", time.strftime("%Y") in w.live("/about.php"), True)


def test_hidden_pages():
    print("\nA page hidden by mistake comes back; one hidden on purpose stays hidden, and the report says so:")
    with Site() as w:
        r, g = _checked(w)
        pages = g["core-noindex"]["pages"]
        check("both hidden pages are found", sorted(p.rsplit("/", 1)[-1] for p in pages), ["doors.php", "thanks.php"])
        rep = _fix("core-noindex")
        rows = {x["path"]: x for x in rep["pages"]}
        check("the doors page is listed again", (rows["/doors.php"]["state"], "noindex" in w.text("doors.php")),
              ("fixed", False))
        check("the thank-you page is left hidden, with the reason", (rows["/thanks.php"]["state"],
                                                                     "on purpose" in rows["/thanks.php"]["why"]),
              ("still", True))
        check("so it is partly fixed: 1 of 2, never 'fixed'", (rep["state"], rep["fixed"], rep["total"]),
              ("partly", 1, 2))
        check("  and the list keeps the one left", [p.rsplit("/", 1)[-1] for p in F._gap("core-noindex")["pages"]],
              ["thanks.php"])


def test_honest_counts():
    print("\nThe counts are honest: already right, a cache, waiting, put back:")
    with Site() as w:
        r, g = _checked(w)
        # Someone fixes about.php's title by hand before the button is pressed.
        mine = "<?php $title = 'About Glass Co, glass fitters in Dubai since 2012'; ?>" + FILES["about.php"]
        (w.web / "about.php").write_text(mine, encoding="utf-8")
        real = F._fetcher()
        stale = {}

        def cached(url):
            # services.php keeps answering with what it showed before the fix.
            st, html, h, fin = real(url)
            if url.endswith("/services.php"):
                stale.setdefault(url, (st, html, h, fin))
                return stale[url]
            return st, html, h, fin
        rep = _fix("core-title-unique", think=_think, fetch=cached)
        rows = {x["path"]: x for x in rep["pages"]}
        check("a page already right is counted as already right, not as fixed",
              (rows["/about.php"]["state"], rep["already"]), ("already", 1))
        check("a page stuck in a cache is not called fixed, and says why",
              (rows["/services.php"]["state"], "cache" in rows["/services.php"]["why"]), ("still", True))
        check("  so the fix is not 'fixed'", rep["state"] in ("partly", "not_fixed"), True)
        st = F.start  # noqa — a second check, once the cache clears
        again = F.verify(F.last_for()["core-title-unique"]["id"], fetch=real)
        check("Check again, after the cache clears, finds it fixed", again["report"]["state"], "fixed")

    with Site() as w:
        r, g = _checked(w)

        def red_tests(limit=10):
            return it_release.run(limit=limit, skip_tests=False) if False else {"ok": False, "error": "tests red"}
        rep = _fix("core-description-present", think=_think, release=red_tests)
        check("a fix the Releaser has not written yet says it is waiting", (rep["state"], "waiting" in
                                                                            rep["headline"].lower() or "not on your website yet" in rep["headline"]),
              ("waiting", True))
        check("  and nothing on the site changed", "botix_meta" in w.text("includes/header.php"), False)

    with Site() as w:
        r, g = _checked(w)
        header_before = w.text("includes/header.php")
        real = F._fetcher()
        broke = {"on": False}

        def release_then_break(limit=10):
            out = _release(limit)
            broke["on"] = True
            return out

        def fetch(url):
            if broke["on"] and url.rstrip("/") == w.home:
                return 500, "", {}, url
            return real(url)
        rep = _fix("core-title-unique", think=_think, fetch=fetch, release=release_then_break)
        check("a change after which the home page fails is put back at once",
              (rep["state"], bool(rep.get("put_back")), w.text("includes/header.php")),
              ("not_fixed", True, header_before))
        check("  and the report says so plainly", "put back" in rep["headline"], True)


def test_what_it_will_not_do():
    print("\nWhat the button cannot do safely is handed on, and never called fixed:")
    with Site() as w:
        r, g = _checked(w)
        code = next(c for c in ("content-thin", "links-anchor-text", "images-format", "url-tidy", "ai-semantic")
                    if c in g) if any(c in g for c in ("content-thin", "links-anchor-text", "images-format",
                                                       "url-tidy", "ai-semantic")) else ""
        if code:
            d = F.describe(code, g[code]["fix"], "rules")
            check(f"{code}: the office says before he presses that a person is needed", d["auto"], False)
            rep = _fix(code)
            check("  pressing Fix says 'not fixed', and where it went", (rep["state"], "Not fixed" in rep["headline"],
                                                                        "Handed to" in rep["headline"] or "already on" in rep["headline"]),
                  ("cannot", True, True))
        os.environ["SITE_MODE"] = "readonly"
        try:
            rep = _fix("core-title-unique", think=_think)
        finally:
            os.environ["SITE_MODE"] = "full"
        check("in read-only mode it says so before anything is worked out",
              (rep["state"], "read-only" in rep["headline"], "botix_meta" in w.text("includes/header.php")),
              ("cannot", True, False))
        check("the button's automatic fixes are described", F.describe("core-title-present", "title")["auto"], True)
        check("a blueprint file with one right answer is a Releaser routine", F.describe("robots_missing")["call"],
              "put_robots")
        check("a favicon needs his logo", F.describe("favicon_missing")["needs"].startswith("your logo"), True)


def test_robots_by_the_releaser():
    print("\nrobots.txt, one right answer, is written by the Releaser and read back live:")
    with Site() as w:
        r, g = _checked(w)
        check("no robots.txt is found", "robots_missing" in g, True)
        rep = _fix("robots_missing")
        check("fixed, and checked on the live site", (rep["state"], (w.web / "robots.txt").exists()), ("fixed", True))


def test_the_edits_themselves():
    print("\nThe edits, on their own (no website needed):")
    head = "<html><head><meta charset=\"utf-8\"><title>Old</title><meta name=\"description\" content=\"a\">" \
           "<meta name=\"description\" content=\"b\"></head><body></body></html>"
    check("a title is replaced, not added twice", F._set_title(head, "New & better").count("<title>"), 1)
    check("  and written safely", "<title>New &amp; better</title>" in F._set_title(head, "New & better"), True)
    one = F._set_tag(head, "meta", "description", "Only one")
    check("two descriptions become one", (one.count('name="description"'), 'content="Only one"' in one), (1, True))
    php = "<head><title><?php echo $t; ?></title><?php echo '<meta name=\"x\">'; ?></head>"
    check("a tag with PHP inside is not taken for a plain one", F._finditer_outside(r"<title\b[^>]*>.*?</title>", php), [])
    wrapped = F._wrap(php, "title", "title")
    check("the shared header keeps its own title as the fallback, PHP and all",
          ("<?php echo $t; ?>" in wrapped, "botix_meta('title')" in wrapped, F.MARK in wrapped), (True, True, True))
    check("  and wrapping twice changes nothing", F._wrap(wrapped, "title", "title"), wrapped)
    echoed = "<head><?php echo '<title>' . $t . '</title>'; ?></head>"
    check("a title printed by PHP code is recognised as such", F._echoed(echoed, r"<title\b"), True)
    text = F.meta_file_text([("/a'b.php", "title", "It\\'s &amp; more")])
    check("botix-meta.php quotes what it holds", "'/a\\'b.php'" in text and "'It\\\\\\'s &amp; more'" in text, True)
    words, how = F.write_words("title", [{"key": "u", "_plain": "Shower screens in Dubai | Glass Co", "_others": []}],
                               think=lambda *a, **k: {"ok": True, "output": '{"u": "50% off shower screens this week | Glass Co"}'})
    check("an AI title with an offer in it is not used", (words["u"], how), ("Shower screens in Dubai | Glass Co", "page"))
    check("the words never carry an offer", bool(F.NOT_IN_WORDS.search("Free quote and a discount")), True)


def main():
    test_the_edits_themselves()
    if not shutil.which("php"):
        print("\nSKIPPED the website tests: PHP is not installed here, so a website cannot be served.")
        print("\nfixer: the edits are right; the website tests need PHP.")
        return 1 if _fail else 0
    for t in (test_shared_header_titles, test_own_head_and_words_without_ai, test_pictures_and_copyright,
              test_hidden_pages, test_honest_counts, test_what_it_will_not_do, test_robots_by_the_releaser):
        t()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        return 1
    print("fixer: changed where it is written, checked where it is seen, reported as it is.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
