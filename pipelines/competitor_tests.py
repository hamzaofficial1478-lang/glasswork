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
    con.executescript((Path(__file__).parents[1] / "coordination" / "schema.sql").read_text(encoding="utf-8"))
    con.commit()
    con.close()
    return db


def _sitemap(*urls):
    body = "".join(f"<url><loc>{u}</loc></url>" for u in urls)
    return f'<?xml version="1.0"?><urlset>{body}</urlset>'


def _index(*children):
    body = "".join(f"<sitemap><loc>{c}</loc></sitemap>" for c in children)
    return f'<?xml version="1.0"?><sitemapindex>{body}</sitemapindex>'


def test_a_rival_cannot_steer_our_http_client():
    """
    The child sitemap URLs come out of the RIVAL'S OWN XML, so the remote file
    decides what this pipeline requests next. That is an SSRF, and a rival site
    does not have to be malicious to be compromised.

    Before the guard, a sitemap index containing

        <loc>file:///etc/passwd</loc>
        <loc>http://169.254.169.254/latest/meta-data/</loc>

    was fetched, and the contents were stored in competitor_snapshots and shown
    to the owner as pages the rival had published. On a cloud box that second
    one hands out credentials.
    """
    print("A rival's sitemap cannot point us at ourselves:")
    import os, tempfile

    secret = os.path.join(tempfile.mkdtemp(), "secret.txt")
    with open(secret, "w") as fh:
        fh.write("PRETEND-CREDENTIALS")

    for url, why in [
        ("file://" + secret, "a local file"),
        ("http://127.0.0.1:3000/api/state", "our own server"),
        ("http://169.254.169.254/latest/meta-data/", "the cloud metadata endpoint"),
        ("http://localhost/admin", "localhost"),
        ("http://10.0.0.5/internal", "a private address"),
        ("gopher://x/1", "an exotic scheme"),
    ]:
        ok, _reason = comp.safe_url(url)
        check(f"{why} is refused", ok, False)

    status, body, err = comp._get("file://" + secret)
    check("and _get returns the refusal rather than the file", body, "")
    check("with the reason stated", "refusing scheme" in err, True)

    # a redirect is a second choice of URL and gets the same check
    import urllib.error
    handler = comp._GuardedRedirect()
    try:
        handler.redirect_request(None, None, 302, "Found", {},
                                 "http://127.0.0.1/secrets")
        blocked = False
    except urllib.error.URLError:
        blocked = True
    check("a redirect into the private network is blocked too", blocked, True)


def main():
    test_a_rival_cannot_steer_our_http_client()
    _domain_tests()
    _parse_tests()
    _classify_tests()
    _critic_tests()
    _cycle_tests()
    _suggestion_tests()
    _discovery_tests()

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
        print("With no rivals on file it goes and looks, and invents nothing:")
        # The map is mocked out here rather than called. This suite must not
        # depend on a free public endpoint being up — see _discovery_tests for
        # the search itself, against a recorded reply.
        from unittest import mock
        with mock.patch.object(comp, "discover",
                               lambda *a, **k: {"ok": False, "found": 0,
                                                "suggested": [], "areas": [],
                                                "error": "the map did not answer"}):
            out = comp.run()
        check("it refuses to invent one", out["ok"], False)
        check("it is not called a failure either", out.get("idle"), True)
        check("and it says it went looking", out.get("looked"), True)
        check("naming the command that fixes it now", "--add" in out["error"], True)

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



def _suggestion_tests():
    """
    "SEO watches the rivals — No competitors on file, and this pipeline will
     not invent any"

    The principle was right and the consequence was a step of the day that
    could never do anything until he opened a terminal and typed a command he
    had no reason to know about. Suggesting is not inventing: the SEO desk sees
    people searching for a firm BY NAME alongside the glass work he sells, and
    puts it forward with the searches attached. Nothing is crawled until he
    says yes.
    """
    from . import seo_gap
    real_db = board.DB
    # _fresh_db only BUILDS a cabinet; pointing board at it is the caller's job,
    # and forgetting means these assertions run against his real one.
    board.DB = _fresh_db()
    try:
        print("\nA name is read out of a branded search, and only out of one:")
        for phrase, want in (("febtech glass works dubai reviews", "febtech"),
                             ("al reef aluminium contact number", "al reef"),
                             ("glass partition dubai price", ""),
                             ("shower glass installation cost", ""),
                             ("aluminium windows near me", "")):
            check(f"{phrase!r}", seo_gap.rival_name_in(phrase), want)

        print("\nPut forward, with the reason attached:")
        comp.suggest_rival("febtech", evidence="febtech glass works dubai reviews")
        comp.suggest_rival("febtech", evidence="febtech glass works dubai photos")
        comp.suggest_rival("al reef", evidence="al reef aluminium contact number")
        waiting = comp.suggested()
        check("two firms, not three", len(waiting), 2)
        check("the second sighting strengthened the first rather than duplicating it",
              len(waiting[0]["evidence"]), 2)
        check("and nothing is a confirmed rival yet", comp.list_competitors(), [])

        print("\nThe watcher asks him instead of dead-ending:")
        out = comp._watch("seo-rival", _Row(), 5)
        check("it did not fail", out.get("idle"), True)
        check("it names them", "febtech" in out["error"], True)
        check("and says nothing is crawled yet",
              "until you approve" in out["error"], True)

        with sqlite3.connect(board.DB) as c:
            c.row_factory = sqlite3.Row
            task = c.execute("SELECT id,title,risk FROM tasks").fetchone()
        check("an amber task is waiting for him", task["risk"], "amber")
        check("no desk can take it before he answers",
              board.claim_task(task["id"], "seo-rival"), False)

        print("\nHe presses Approve:")
        board.decide_approval("task", task["id"], "approved")
        comp._watch("seo-rival", _Row(), 5)
        check("they are rivals now",
              sorted(r["name"] for r in comp.list_competitors()),
              ["al reef", "febtech"])
        check("and none are still waiting", comp.suggested(), [])

        print("\n...and one he says is not a rival goes for good:")
        comp.suggest_rival("crystal", evidence="crystal glass sharjah timings")
        ids = [r["id"] for r in comp.suggested()]
        check("dropped", comp.drop_suggested(ids), 1)
        check("gone", comp.suggested(), [])
        check("and it was never added to the watch list",
              [r["name"] for r in comp.list_competitors() if r["name"] == "crystal"], [])
    finally:
        board.DB = real_db


class _Row:
    """Stands in for the agent_run record. The watcher only ever stamps a task
    id on it."""

    def task(self, _id):
        pass

    def spent(self, _usd):
        pass

    def note(self, _text):
        pass


# ---------------------------------------------------------------------------
# GOING AND FINDING THEM
# ---------------------------------------------------------------------------

# What Overpass actually hands back, in its real shape — see osm_tests for the
# same fixtures used the other way round. The traps are deliberate: our own
# company, a windscreen fitter, an optician and a hotel all come back from a
# name search for "glass" and none of them is bidding against him for a shower
# screen.
def _el(_id, name, **tags):
    return {"type": "node", "id": _id, "tags": dict({"name": name}, **tags)}


OSM_REPLY = [
    _el(1, "Al Noor Glass & Aluminium LLC", craft="glaziery",
        website="https://www.alnoorglass.ae", **{"addr:city": "Dubai"}),
    _el(2, "Gulf Aluminium Fabrication", office="company",
        **{"contact:website": "gulfalufab.ae"}),
    _el(3, "Emirates Glaziery Works", shop="glaziery"),          # no website
    _el(4, "World Deal Technical Services", office="company",
        website="https://www.worlddealtechnicalservices.com"),   # us
    _el(5, "Speedy Auto Glass & Windscreen", shop="car_repair"),  # not a rival
    _el(6, "Al Jaber Optical Glasses", shop="optician"),          # not a rival
    _el(7, "Grand Millennium Hotel", tourism="hotel"),            # not in the trade
    _el(8, "AL NOOR GLASS & ALUMINIUM L.L.C.", craft="glaziery",
        website="http://alnoorglass.ae/contact"),                 # the same firm again
]


def _discovery_tests():
    """
    "the agent should find our competitors in our related services in UAE"

    Waiting to be told, and then waiting for somebody to search a rival by
    name, are both passive. This goes and looks at the map — and everything it
    finds is still only a suggestion until he says yes.
    """
    from unittest import mock
    from coordination import uae
    real_db = board.DB
    board.DB = _fresh_db()
    try:
        print("\nWhat comes back from the map, and what survives:")
        with mock.patch.object(comp, "_me",
                               lambda: {"name": "World Deal Technical Services",
                                        "domain": "worlddealtechnicalservices.com"}), \
             mock.patch("pipelines.leads_osm.ask",
                        lambda q, timeout=0: {"ok": True, "elements": OSM_REPLY}), \
             mock.patch("time.sleep", lambda *_: None):
            r = comp.discover(areas=["Dubai"])

        names = [s["name"] for s in comp.suggested()]
        check("the glazier is put forward", "Al Noor Glass & Aluminium LLC" in names, True)
        check("so is the fabricator named for the trade",
              "Gulf Aluminium Fabrication" in names, True)
        check("and one with no website, because he may know them",
              "Emirates Glaziery Works" in names, True)
        check("we are not our own rival",
              "World Deal Technical Services" in names, False)
        check("a windscreen fitter is not one either",
              any("Windscreen" in n for n in names), False)
        check("nor an optician", any("Optical" in n for n in names), False)
        check("nor a hotel", any("Hotel" in n for n in names), False)
        check("the same firm listed twice is one suggestion",
              sum(1 for n in names if n.lower().startswith("al noor glass")), 1)

        print("\nEach one says what put it there:")
        noor = next(s for s in comp.suggested() if s["name"].startswith("Al Noor"))
        check("the domain came off the map", noor["domain"], "alnoorglass.ae")
        check("and the evidence names the source",
              "OpenStreetMap" in noor["evidence"][0], True)
        check("and the emirate", "Dubai" in noor["evidence"][0], True)

        print("\nStill only a suggestion — nothing is crawled:")
        check("no confirmed rivals", comp.list_competitors(), [])

        print("\nRunning it again does not pile up duplicates:")
        before = len(comp.suggested())
        with mock.patch.object(comp, "_me",
                               lambda: {"name": "World Deal Technical Services",
                                        "domain": "worlddealtechnicalservices.com"}), \
             mock.patch("pipelines.leads_osm.ask",
                        lambda q, timeout=0: {"ok": True, "elements": OSM_REPLY}), \
             mock.patch("time.sleep", lambda *_: None):
            comp.discover(areas=["Dubai"])
        check("same count", len(comp.suggested()), before)

        print("\nThe query asks the map about the right trade:")
        seen = {}
        with mock.patch.object(comp, "_me", lambda: {"name": "", "domain": ""}), \
             mock.patch("pipelines.leads_osm.ask",
                        lambda q, timeout=0: (seen.setdefault("q", q),
                                              {"ok": True, "elements": []})[1]), \
             mock.patch("time.sleep", lambda *_: None):
            comp.discover(areas=["Sharjah"])
        q = seen.get("q", "")
        check("it asks for glaziers", 'craft"="glaziery' in q, True)
        check("and window makers", 'craft"="window_construction' in q, True)
        check("and searches names for the trade words", "[Aa]lumin" in q, True)
        check("inside Sharjah's box", "25.27,55.35,25.45,55.65" in q, True)
        check("and asks for centres so buildings come back too", "out center" in q, True)

        print("\nA few emirates per pass, and all seven within a week:")
        # Seven queries a day against a free, donated endpoint is not
        # neighbourly, and the map does not change hourly. It rotates.
        from pipelines import leads_osm as osm
        every = [a for a in uae.EMIRATES if a in osm.BBOX]
        check("every emirate has a box on the map", len(every), 7)
        check("including Umm Al Quwain, which had none",
              "Umm Al Quwain" in osm.BBOX, True)

        asked = []
        with mock.patch.object(comp, "_me", lambda: {"name": "", "domain": ""}), \
             mock.patch("pipelines.leads_osm.ask",
                        lambda q, timeout=0: (asked.append(q),
                                              {"ok": True, "elements": []})[1]), \
             mock.patch("time.sleep", lambda *_: None):
            comp.discover()
        check("a handful this pass, not all of them",
              len(asked), comp.AREAS_PER_PASS)

        covered = set()
        for day in range(1, 15):
            with mock.patch("time.gmtime",
                            lambda *_: type("T", (), {"tm_yday": day})()):
                covered.update(comp._next_areas(every))
        check("and a fortnight of runs covers the country",
              sorted(covered), sorted(every))

        print("\n...and an area he names is honoured rather than rotated:")
        asked = []
        with mock.patch.object(comp, "_me", lambda: {"name": "", "domain": ""}), \
             mock.patch("pipelines.leads_osm.ask",
                        lambda q, timeout=0: (asked.append(q),
                                              {"ok": True, "elements": []})[1]), \
             mock.patch("time.sleep", lambda *_: None):
            comp.discover(areas=["Fujairah"])
        check("one query", len(asked), 1)
        check("for Fujairah", osm.BBOX["Fujairah"] in asked[0], True)

        print("\nThe map being down is not the agency inventing rivals:")
        board.DB = _fresh_db()
        with mock.patch("pipelines.leads_osm.ask",
                        lambda q, timeout=0: {"ok": False, "elements": [],
                                              "error": "no Overpass mirror answered"}), \
             mock.patch("time.sleep", lambda *_: None):
            r = comp.discover(areas=["Dubai"])
        check("it says so", r["ok"], False)
        check("names the reason", "no Overpass mirror" in r["error"], True)
        check("and suggested nobody", comp.suggested(), [])

        print("\nAnd the watcher goes looking on its own rather than dead-ending:")
        board.DB = _fresh_db()
        with mock.patch.object(comp, "_me", lambda: {"name": "", "domain": ""}), \
             mock.patch("pipelines.leads_osm.ask",
                        lambda q, timeout=0: {"ok": True, "elements": OSM_REPLY}), \
             mock.patch("time.sleep", lambda *_: None):
            out = comp._watch("seo-rival", _Row(), 5)
        check("it found some", len(comp.suggested()) > 0, True)
        check("and put them to him", "waiting for your yes" in out["error"], True)
        check("which is not a failure", out.get("idle"), True)
    finally:
        board.DB = real_db


if __name__ == "__main__":
    main()
