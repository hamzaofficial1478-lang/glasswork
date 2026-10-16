"""
site_map_tests.py — run: python -m pipelines.site_map_tests

"which file is working as the main file not as the blind to work on the file
 that is not even on website's pages"

What is protected (stand-in websites; nothing reaches a network):
  - a PHP site: the home page's file, the shared header that holds the page
    head, /about matched to about.php, and an old copy nobody uses named and
    left alone;
  - an HTML site with a stale index.php beside index.html: the live home page
    is matched to index.html, by its own words, not by assuming index.php;
  - a site whose pages are made by one front file is said to be one, and no
    file is called unused on its account;
  - a read that was cut short never calls any file unused;
  - the bulk editors, the page list and the header lookup follow the map.
"""
import sqlite3
import sys
import tempfile
from pathlib import Path

from coordination import board
from . import site_api, site_map as sm

ROOT = Path(__file__).resolve().parents[1]
_fail = []
H = "https://www.example-trade.test"


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


class _Cabinet:
    def __enter__(self):
        self.real = board.DB
        db = Path(tempfile.mkdtemp()) / "agency.db"
        con = sqlite3.connect(db)
        con.executescript((ROOT / "coordination" / "schema.sql").read_text(encoding="utf-8"))
        con.commit()
        con.close()
        board.DB = str(db)
        board.ensure_schema()
        return self

    def __exit__(self, *a):
        board.DB = self.real


HEADER = ("<!doctype html><html lang=\"en\"><head><title><?php echo $title; ?></title></head><body>"
          "<nav><a href=\"/\">Home</a> <a href=\"/about\">About us</a></nav>")
FOOTER = "<footer>Call us any day for a free site visit and measure</footer></body></html>"
PHP_FILES = {
    "index.php": "<?php $title='Home'; include 'includes/header.php'; ?>\n<main><h1>Glass work done properly</h1>"
                 "<p>Shower screens, partitions and railings across the city.</p></main>\n<?php include 'includes/footer.php'; ?>",
    "about.php": "<?php $title='About'; include 'includes/header.php'; ?>\n<main><h1>About our workshop</h1>"
                 "<p>Fifteen people in one workshop since the start.</p></main>\n<?php include 'includes/footer.php'; ?>",
    "includes/header.php": HEADER,
    "includes/footer.php": FOOTER,
    "index-old.php": "<html><head><title>Old</title></head><body><h1>Our old home page from years ago</h1></body></html>",
    "test.html": "<html><body><p>testing testing one two three</p></body></html>",
}


def _live(title, main):
    return HEADER.replace("<?php echo $title; ?>", title) + main + FOOTER


PHP_PAGES = [
    {"url": H + "/", "html": _live("Home", "<main><h1>Glass work done properly</h1><p>Shower screens, partitions and "
                                           "railings across the city.</p></main>")},
    {"url": H + "/about", "html": _live("About", "<main><h1>About our workshop</h1><p>Fifteen people in one "
                                                 "workshop since the start.</p></main>")},
]


def _reader(files, log=None):
    def read(names):
        if log is not None:
            log.append(list(names))
        return {n: files[n] for n in names if n in files}
    return read


def test_a_php_site():
    print("\nA PHP site with a shared header:")
    m = sm.build_from(PHP_PAGES, list(PHP_FILES), _reader(PHP_FILES))
    check("it is a PHP site", m["kind"], "php")
    check("the home page is index.php", m["home_file"], "index.php")
    check("/about is about.php, matched by its own words",
          [(p["path"], p["file"], p["sure"]) for p in m["pages"]][1], ("/about", "about.php", "sure"))
    check("the shared parts are found", sorted(m["shared"]), ["includes/footer.php", "includes/header.php"])
    check("  and the page head is in the header", m["head_file"], "includes/header.php")
    check("files no page uses are named, to be left alone", m["unused"], ["index-old.php", "test.html"])


def test_an_html_site_with_a_stale_php():
    print("\nAn HTML site with an old index.php beside index.html:")
    home = "<html><head><title>Home</title></head><body><h1>Frameless glass for homes and offices</h1></body></html>"
    files = {"index.html": home, "index.php": "<?php echo 'Coming soon — under construction page here'; ?>"}
    m = sm.build_from([{"url": H + "/", "html": home}], list(files), _reader(files))
    check("the live home page is index.html, not an assumed index.php", m["home_file"], "index.html")
    check("it is an HTML site", m["kind"], "html")
    check("the page head is in the page itself", m["head_file"], "index.html")


def test_the_server_decides():
    print("\nThe server decides which file it serves, not the file's extension:")
    # An old index.html left beside the real index.php, with the same header and
    # footer words in it — matching by words alone picks the old copy.
    old_copy = (HEADER.replace("<?php echo $title; ?>", "Home")
                + "<main><h1>Glass work done properly</h1><p>Shower screens, partitions and railings across "
                  "the city.</p><p>Our old offer from years ago, still in this copy.</p></main>" + FOOTER)
    files = dict(PHP_FILES, **{"index.html": old_copy,
                               "index.php": "<?php $title='Home'; include 'includes/header.php'; ?>\n"
                                            "<?php include 'parts/home-body.php'; ?>\n<?php include 'includes/footer.php'; ?>"})
    blind = sm.build_from(PHP_PAGES[:1], list(files), _reader(files))
    check("by words alone, the old copy wins (the bug he saw)", blind["home_file"], "index.html")
    asked = []

    def fetch(url):
        asked.append(url)
        if url.endswith("/index.php"):
            return 200, PHP_PAGES[0]["html"], False          # the server answers with the home page
        if url.endswith("/index.html"):
            return 200, old_copy, False                      # an old page, not the home page
        return 404, "", False
    m = sm.build_from(PHP_PAGES[:1], list(files), _reader(files), fetch=fetch)
    check("asked, the server says index.php", (m["home_file"], m["pages"][0]["sure"]), ("index.php", "server"))
    check("  every candidate that exists was asked about", sorted(u.rsplit("/", 1)[1] for u in asked),
          ["index.html", "index.php"])
    check("  and it is still a PHP site", m["kind"], "php")

    def redirects_home(url):
        if url.endswith("/index.php"):
            return 200, PHP_PAGES[0]["html"], True           # /index.php sends you to /
        if url.endswith("/index.html"):
            return 404, "", False
        return 404, "", False
    m = sm.build_from(PHP_PAGES[:1], list(files), _reader(files), fetch=redirects_home)
    check("a redirect back to the home page still counts", m["home_file"], "index.php")

    html_only = {"index.html": "<html><head><title>Home</title></head><body><h1>Frames and doors made to measure</h1></body></html>"}
    live = [{"url": H + "/", "html": html_only["index.html"]}]
    m = sm.build_from(live, list(html_only), _reader(html_only),
                      fetch=lambda u: (200, html_only["index.html"], False) if u.endswith("index.html") else (404, "", False))
    check("on a site that really is index.html, that is what it says", (m["home_file"], m["pages"][0]["sure"]),
          ("index.html", "server"))
    check("nobody's wording is wrong: two copies answering the same are told apart by words",
          sm.same_page("<p>a b c</p>", "<p>a b c</p>"), 1.0)


def test_a_front_file_site():
    print("\nPages made by one front file:")
    pages = [{"url": H + "/", "html": "<html><body><p>Home page words here ok</p></body></html>"},
             {"url": H + "/services/glass", "html": "<p>x</p>"}, {"url": H + "/contact-us", "html": "<p>x</p>"}]
    files = {"index.php": "<?php require 'app/boot.php'; ?>"}
    m = sm.build_from(pages, list(files), _reader(files))
    check("it is said to be one front file", m["kind"], "router")
    check("  and nothing is called unused on its account", m["unused"], [])
    check("the addresses no file serves are listed", m["unmatched"], ["/services/glass", "/contact-us"])


def test_a_short_read():
    print("\nA read cut short calls nothing unused:")
    m = sm.build_from(PHP_PAGES, list(PHP_FILES), _reader(PHP_FILES), partial=True)
    check("not complete", (m["complete"], m["unused"]), (False, []))


def test_the_editors_follow_the_map():
    print("\nThe bulk editors, the page list and the header lookup follow the map:")
    with _Cabinet():
        sm.save(sm.build_from(PHP_PAGES, list(PHP_FILES), _reader(PHP_FILES)))
        check("the page list is the real files, home first", site_api.live_pages(), ["index.php", "about.php"])
        check("the header lookup names the shared header", site_api.head_holder(["index.php"]), "includes/header.php")
        real = (site_api.pages, site_api.read_pages, site_api.read_page)
        site_api.pages = lambda s=None: {"ok": True, "pages": [{"page": n} for n in PHP_FILES]}
        site_api.read_pages = lambda names, s=None: {n: {"ok": True, "html": PHP_FILES[n]} for n in names if n in PHP_FILES}
        site_api.read_page = lambda n, s=None: ({"ok": True, "html": PHP_FILES[n]} if n in PHP_FILES
                                                else {"ok": False, "status": 404})
        try:
            walked = [n for n, _h in site_api._walk()]
        finally:
            site_api.pages, site_api.read_pages, site_api.read_page = real
        check("a find-and-replace walks only the files the website uses",
              sorted(walked), ["about.php", "includes/footer.php", "includes/header.php", "index.php"])
        s = sm.summary()
        check("the Website screen gets it in words", (s["known"], s["kind_words"], s["home_file"]),
              (True, "PHP pages", "index.php"))


def main():
    for t in (test_a_php_site, test_an_html_site_with_a_stale_php, test_the_server_decides, test_a_front_file_site,
              test_a_short_read,
              test_the_editors_follow_the_map):
        t()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        return 1
    print("site map: every page matched to the file that makes it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
