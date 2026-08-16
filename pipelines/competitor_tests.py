"""
competitor_tests.py — run: python -m pipelines.competitor_tests

The rival watch has three ways to be quietly wrong, and each has a test here:

  * reporting every page a rival has ever published as "new", because it had
    no previous snapshot to diff against. That is the first-run trap, and it
    would put a hundred fabricated "moves" in front of the owner on day one.
  * reporting a clean result about rivals it never actually read, so a network
    problem reads as "the competition is quiet".
  * calling a page a service move when the slug says nothing of the sort.

Every fetch is faked and every run is against a throwaway cabinet, so no
competitor's server is touched by the test suite.
"""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

from coordination import board
from . import competitors as comp

_fail = []


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


def _fresh_db():
    d = Path(tempfile.mkdtemp())
    db = d / "agency.db"
    con = sqlite3.connect(db)
    con.executescript((Path(__file__).parents[1] / "coordination" / "schema.sql").read_text())
    con.commit()
    con.close()
    return db


def _sitemap(*urls):
    body = "".join(f"<url><loc>{u}</loc></url>" for u in urls)
    return f'<?xml version="1.0"?><urlset>{body}</urlset>'


def _index(*children):
    body = "".join(f"<sitemap><loc>{c}</loc></sitemap>" for c in children)
    return f'<?xml version="1.0"?><sitemapindex>{body}</sitemapindex>'


def main():
    _domain_tests()
    _parse_tests()
    _classify_tests()
    _critic_tests()
    _cycle_tests()

    if _fail:
        print(f"\n{len(_fail)} FAILED: {_fail}")
        sys.exit(1)
    print("\nall competitor tests passed")


def _domain_tests():
    print("A domain is stored one way, however it was typed:")
    for raw in ("alnoor.ae", "www.alnoor.ae", "https://alnoor.ae/",
                "https://www.alnoor.ae", "  ALNOOR.ae  "):
        check(f"{raw!r} normalises", comp._normalise(raw), "alnoor.ae")
    check("empty stays empty", comp._normalise(""), "")


def _parse_tests():
    print("Reading a sitemap:")
    urls = comp._extract_locs(_sitemap("https://x.ae/a", "https://x.ae/b"))
    check("both urls read", urls, ["https://x.ae/a", "https://x.ae/b"])
    check("nothing in an empty document", comp._extract_locs("<urlset></urlset>"), [])

    print("A sitemap index is followed to the real pages:")
    pages = {
        "https://r.ae/sitemap.xml": _index("https://r.ae/sm-1.xml"),
        "https://r.ae/sm-1.xml": _sitemap("https://r.ae/glass-partitions",
                                          "https://r.ae/contact"),
    }
    real = comp._get
    comp._get = lambda url, timeout=15: ((200, pages[url], None) if url in pages
                                         else (404, "", "HTTP 404"))
    try:
        urls, source, err = comp.sitemap_urls("r.ae")
        check("index was followed, not reported as the pages", len(urls), 2)
        check("the child's pages came back",
              urls, ["https://r.ae/glass-partitions", "https://r.ae/contact"])
        check("no error", err, None)
    finally:
        comp._get = real

    print("An unreachable rival is an error, never an empty 'they are quiet':")
    comp._get = lambda url, timeout=15: (0, "", "connection refused")
    try:
        urls, source, err = comp.sitemap_urls("gone.ae")
        check("no urls", urls, [])
        check("and it says why", bool(err), True)
    finally:
        comp._get = real


def _classify_tests():
    print("A rival page is judged by its own slug:")
    check("shower glass page",
          comp.classify("https://r.ae/frameless-shower-glass-dubai")[0], "shower_glass")
    check("partition page",
          comp.classify("https://r.ae/services/office-glass-partitions")[0], "glass_partition")
    check("an about page is not a service",
          comp.classify("https://r.ae/about-us")[0], None)
    check("a contact page is not a service",
          comp.classify("https://r.ae/contact")[0], None)


def _critic_tests():
    print("Critic:")
    sound = [{"domain": "r.ae", "new_pages": ["https://r.ae/x"], "first_sighting": False}]
    ok, reasons = comp.critic_check(sound, read_any=True)
    check("a real result passes", ok, True)

    ok, reasons = comp.critic_check([], read_any=False)
    check("checking no rivals is rejected", ok, False)

    ok, reasons = comp.critic_check(sound, read_any=False)
    check("a report on rivals nobody could read is rejected", ok, False)
    check("and names it a connectivity problem",
          "connectivity" in " ".join(reasons), True)

    first = [{"domain": "r.ae", "new_pages": ["https://r.ae/x"], "first_sighting": True}]
    ok, reasons = comp.critic_check(first, read_any=True)
    check("new pages on a first sighting is rejected", ok, False)


def _cycle_tests():
    real_db = board.DB
    board.DB = _fresh_db()
    real_get = comp._get
    try:
        print("With no rivals on file it refuses to invent one:")
        out = comp.run()
        check("it refuses", out["ok"], False)
        check("and names the command that fixes it", "--add" in out["error"], True)

        print("Registering a rival:")
        rid = comp.add_competitor("Al Noor Glass", "https://www.alnoor.ae/")
        check("stored", bool(rid), True)
        check("the same domain twice is refused",
              comp.add_competitor("Al Noor Again", "alnoor.ae"), None)
        check("exactly one on file", len(comp.list_competitors()), 1)

        print("First run records a baseline and claims no 'new' pages:")
        pages = {"https://alnoor.ae/sitemap.xml":
                 _sitemap("https://alnoor.ae/", "https://alnoor.ae/about-us")}
        comp._get = lambda url, timeout=15: ((200, pages[url], None) if url in pages
                                             else (404, "", "HTTP 404"))
        out = comp.run()
        check("it runs", out["ok"], True)
        check("critic passed", out.get("critic"), "passed")
        check("nothing is called new on a first sighting",
              out["results"][0]["new_pages"], [])
        check("and it says it is a first sighting",
              out["results"][0]["first_sighting"], True)
        check("no service move claimed", out["service_moves"], [])

        print("Second run reports only what actually appeared:")
        pages["https://alnoor.ae/sitemap.xml"] = _sitemap(
            "https://alnoor.ae/", "https://alnoor.ae/about-us",
            "https://alnoor.ae/frameless-shower-glass-dubai")
        out2 = comp.run()
        check("exactly one new page", out2["results"][0]["new_pages"],
              ["https://alnoor.ae/frameless-shower-glass-dubai"])
        check("and it is recognised as a service we sell",
              [m["service"] for m in out2["service_moves"]], ["shower_glass"])
        check("Content was asked, not told", bool(out2["request_id"]), True)

        con = sqlite3.connect(board.DB)
        req = con.execute("SELECT from_sector,to_sector FROM requests WHERE id=?",
                          (out2["request_id"],)).fetchone()
        check("the request runs SEO -> Content", req, ("SEO", "Content"))

        print("The decision carries the rows it is a claim about:")
        ev = con.execute("SELECT evidence FROM decisions WHERE id=?",
                         (out2["decision_id"],)).fetchone()[0]
        ev = json.loads(ev)
        check("snapshots recorded", len(ev["snapshots"]) >= 1, True)
        check("the new page is named",
              ev["new_pages"], ["https://alnoor.ae/frameless-shower-glass-dubai"])
        check("and the service is named", ev["services"], ["frameless shower glass"])

        print("A snapshot is written every run, so the next diff has a baseline:")
        n = con.execute("SELECT count(*) FROM competitor_snapshots").fetchone()[0]
        check("two snapshots after two runs", n, 2)

        print("No change is reported as no change, not as silence:")
        out3 = comp.run()
        check("nothing new", out3["results"][0]["new_pages"], [])
        check("and the note says so", "No change" in out3["results"][0]["note"], True)
        check("Content is not pestered when nothing moved", out3["request_id"], None)
        con.close()
    finally:
        comp._get = real_get
        board.DB = real_db


if __name__ == "__main__":
    main()
