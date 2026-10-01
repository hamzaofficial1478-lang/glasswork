"""
index_tests.py — run: python -m pipelines.index_tests

"i think i have no sitemap ... how can i add sitemaps to website? and still
 many pages are not indexed ... the program did not figure that more then 8
 pages are still not indexed"

What has to hold:

  one address        the canonical names the address his site is on, www and
                     all — and the fixer only ever changes HIS name, only in the
                     canonical, never a link to Facebook on a line that happens
                     to use a value called $canonical
  share buttons      the ones the old fixer pointed at his own site go back to
                     Facebook, WhatsApp and LinkedIn
  a sitemap          his pages, at his one address, nothing that is a view of
                     another page, no share links, sent as XML from a PHP page
                     that cannot break on short_open_tag; kept up as posts come
                     and go
  Google's own word  each page asked about, each "not indexed" explained, the
                     fixes queued for his yes once — not every day

A throwaway cabinet and a stand-in website. Nothing leaves this machine.
"""
import json
import sqlite3
import sys
import tempfile
import time
import types
from pathlib import Path

from coordination import board, site
from . import day_run, it_fix, it_release, seo_index, seo_intel, site_api, sitemap

_fail = []
ROOT = Path(__file__).parents[1]
HOME = "https://www.worlddealtechnicalservices.com/"


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


class _Cabinet:
    def __enter__(self):
        self.real = board.DB
        d = Path(tempfile.mkdtemp())
        db = d / "agency.db"
        con = sqlite3.connect(db)
        con.executescript((ROOT / "coordination" / "schema.sql").read_text(encoding="utf-8"))
        con.commit()
        con.close()
        board.DB = str(db)
        board.ensure_schema()
        return self

    def __exit__(self, *a):
        board.DB = self.real

    def sql(self, q, args=()):
        c = sqlite3.connect(board.DB)
        c.row_factory = sqlite3.Row
        try:
            rows = [dict(r) for r in c.execute(q, args)]
            c.commit()
            return rows
        finally:
            c.close()


class _Site:
    """His website through the connector, as a dict. Everything above call() is real."""

    def __init__(self, pages):
        self.pages, self.writes = dict(pages), []

    def __enter__(self):
        self.real = (site_api.call, site_api.settings, site.site_host)
        site_api.call = self._call
        site_api.settings = lambda env=None: {"mode": "full", "base": "https://x/agency-api",
                                              "token": "t" * 40, "site": HOME.rstrip("/")}
        site.site_host = lambda override="": "www.worlddealtechnicalservices.com"
        return self

    def __exit__(self, *a):
        site_api.call, site_api.settings, site.site_host = self.real

    def _call(self, path, method="GET", body=None, timeout=25, s=None):
        if path == "/pages" and method == "GET":
            return {"ok": True, "pages": [{"page": p, "bytes": 1, "changed": "t"}
                                          for p in self.pages if "/" not in p]}
        if path.startswith("/pages/") and method == "GET":
            name = path.split("/", 2)[2]
            if name not in self.pages:
                return {"ok": False, "error": "no such page", "status": 404}
            return {"ok": True, "page": name, "html": self.pages[name]}
        if path == "/pages" and method == "POST":
            self.writes.append(body["page"])
            self.pages[body["page"]] = body["html"]
            return {"ok": True, "page": body["page"], "backup": "b"}
        return {"ok": False, "error": f"no {method} {path}"}


BLOG = ('<?php $canonical = "https://worlddealtechnicalservices.com/blog-details.php?slug=" . $slug; ?>\n'
        '<link rel="canonical" href="<?= $canonical ?>">\n'
        '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
        '<a href="https://worlddealtechnicalservices.com/sharer/sharer.php?u=<?= urlencode($canonical) ?>">Facebook</a>\n'
        '<a href="https://worlddealtechnicalservices.com/?text=<?= urlencode($title . \' \' . $canonical) ?>">WhatsApp</a>\n'
        '<a href="https://www.worlddealtechnicalservices.com/sharing/share-offsite/?url=<?= urlencode($canonical) ?>">LinkedIn</a>\n'
        '<a href="https://www.facebook.com/worlddeal">Our Facebook page</a>\n')


def test_the_canonical_names_the_one_address_and_nothing_else():
    print("\nThe canonical fixer changes his name in the canonical, and nothing else:")
    check("a share button using $canonical is NOT a canonical line",
          site_api._canonical_line('<a href="https://www.facebook.com/sharer/sharer.php?u='
                                   '<?= urlencode($canonical) ?>">'), False)
    check("the canonical tag is", site_api._canonical_line('<link rel="canonical" href="x">'), True)
    check("so is the line that sets it", site_api._canonical_line('$canonical = "https://x.ae";'), True)
    with _Cabinet(), _Site({"blog-details.php": BLOG, "index.php":
                            '<link rel="canonical" href="https://worlddealtechnicalservices.ae/">'}) as s:
        plan = site_api.canonical_plan()
        check("both his wrong canonicals are found, nothing else",
              sorted(j["wrong_host"] for j in plan["jobs"]),
              ["worlddealtechnicalservices.ae", "worlddealtechnicalservices.com"])
        site_api.fix_canonical(agent="it-releaser")
        page = s.pages["blog-details.php"]
    check("the canonical value now has its www",
          '$canonical = "https://www.worlddealtechnicalservices.com/blog-details.php' in page, True)
    check("the Facebook share button was not touched by it",
          "https://worlddealtechnicalservices.com/sharer/sharer.php" in page, True)
    check("nor the font link", "https://fonts.googleapis.com" in page, True)
    check("the old .ae home canonical points at the www address",
          'href="https://www.worlddealtechnicalservices.com/"' in s.pages["index.php"], True)


def test_the_share_buttons_go_to_the_networks_again():
    print("\nThe share buttons the old fixer broke go back to Facebook, WhatsApp and LinkedIn:")
    with _Cabinet(), _Site({"blog-details.php": BLOG, "index.php": "<p>home</p>"}) as s:
        plan = site_api.plan_share_buttons()
        check("three broken buttons, on the one page", (plan["times"], [p["page"] for p in plan["pages"]]),
              (3, ["blog-details.php"]))
        queued = site_api.repair_share_buttons(agent="seo-lead")
        check("only the Releaser writes; anyone else queues it for his yes",
              (queued["ok"], bool(queued.get("queued")), s.writes), (False, True, []))
        r = site_api.repair_share_buttons(agent="it-releaser")
        page = s.pages["blog-details.php"]
    check("it went", r["ok"], True)
    check("Facebook", "https://www.facebook.com/sharer/sharer.php?u=" in page, True)
    check("WhatsApp", "https://wa.me/?text=" in page, True)
    check("LinkedIn", "https://www.linkedin.com/sharing/share-offsite/?url=" in page, True)
    check("his own Facebook page link is left alone",
          "https://www.facebook.com/worlddeal" in page, True)
    check("the canonical is not a share button", "$canonical = \"https://worlddealtechnicalservices.com/"
          in page, True)


def _fake_web(pages):
    def get(u):
        return (200, pages[u]) if u in pages else (404, "")
    return get


WEB = {
    HOME: ('<link rel="canonical" href="https://worlddealtechnicalservices.com/">'
           '<a href="/about.php">About</a> <a href="index.php">Home</a>'
           '<a href="https://worlddealtechnicalservices.com/blog-details.php?slug=glass-doors">Post</a>'
           '<a href="/gallery.php?cat=wooden-work">Wood</a> <a href="/gallery.php">Gallery</a>'
           '<a href="/sharer/sharer.php?u=https%3A%2F%2Fworlddealtechnicalservices.com%2F">Share</a>'
           '<a href="/?text=Hello+https%3A%2F%2Fx.com">WhatsApp</a>'
           '<a href="/admin/login.php">Admin</a> <a href="/assets/brochure.pdf">PDF</a>'
           '<a href="https://www.facebook.com/worlddeal">FB</a>'
           '<a href="/thanks.php">Thanks</a> <a href="/old.php">Old</a>'),
    HOME + "about.php": '<link rel="canonical" href="https://worlddealtechnicalservices.com/about.php">',
    HOME + "blog-details.php?slug=glass-doors": '<p>post</p>',
    HOME + "gallery.php?cat=wooden-work": '<link rel="canonical" href="https://worlddealtechnicalservices.com/gallery.php">',
    HOME + "gallery.php": '<link rel="canonical" href="https://worlddealtechnicalservices.com/gallery.php">',
    HOME + "thanks.php": '<meta name="robots" content="noindex, follow">',
}


def test_the_sitemap_lists_his_real_pages_at_his_one_address():
    print("\nThe sitemap: his real pages, at his one address:")
    asked = []
    web = _fake_web(WEB)
    d = sitemap.discover(HOME.rstrip("/"), get=lambda u: asked.append(u) or web(u))
    check("the pages, home first, all on www",
          d["pages"], [HOME, HOME + "about.php", HOME + "gallery.php",
                       HOME + "blog-details.php?slug=glass-doors"])
    check("a link to the copy without www counts as the www page",
          HOME + "about.php" in d["pages"], True)
    check("a gallery filter is a view of the gallery, not a page",
          d["views"].get(HOME + "gallery.php?cat=wooden-work"), HOME + "gallery.php")
    check("a page asking to stay out is left out",
          "noindex" in d["left_out"].get(HOME + "thanks.php", ""), True)
    check("a missing page is left out, with why", d["left_out"].get(HOME + "old.php"), "answered 404")
    check("share buttons, admin, files and other sites are never even asked for",
          [u for u in asked if any(w in u for w in ("sharer", "text=", "admin", ".pdf", "facebook"))],
          [])
    check("index.php is the home page, asked for once", asked.count(HOME), 1)

    php = sitemap.as_php([HOME, HOME + "gallery.php?cat=a&b=c"])
    check("it is a PHP page", php.startswith("<?php\n"), True)
    check("that sends XML", "Content-Type: application/xml" in php, True)
    check("with the XML inside a nowdoc, so short_open_tag cannot break it",
          php.index("echo <<<'XML'") < php.index("<?xml"), True)
    check("and the address escaped as XML must be", "cat=a&amp;b=c" in php, True)


def test_the_sitemap_goes_up_and_keeps_up():
    print("\nThe Releaser puts the sitemap up, gives it to Search Console, and keeps it up to date:")
    with _Cabinet(), _Site({"index.php": "<p>home</p>"}) as s:
        real = (sitemap.discover, sitemap.site_address, seo_intel.gsc_submit_sitemap)
        given = []
        sitemap.discover = lambda home=None, most=150, get=None: {
            "ok": True, "pages": [HOME, HOME + "about.php"], "views": {}, "left_out": {}}
        sitemap.site_address = lambda: HOME.rstrip("/")
        seo_intel.gsc_submit_sitemap = lambda url: given.append(url) or {"ok": True}
        try:
            refused = site_api.publish_sitemap(agent="seo-lead")
            r = site_api.publish_sitemap(agent="it-releaser")
            first = s.pages.get("sitemap.php", "")
            sitemap.keep_up("it-releaser", add=[
                "https://worlddealtechnicalservices.com/blog-details.php?slug=new-post"])
            added = s.pages["sitemap.php"]
            sitemap.keep_up("it-releaser", drop=[HOME + "about.php"])
            dropped = s.pages["sitemap.php"]
        finally:
            sitemap.discover, sitemap.site_address, seo_intel.gsc_submit_sitemap = real
    check("anyone but the Releaser queues it for his yes", bool(refused.get("queued")), True)
    check("the Releaser puts it up", (r["ok"], "<loc>" + HOME + "about.php</loc>" in first), (True, True))
    check("and hands it to Search Console", given, [HOME + "sitemap.php"])
    check("  and says so", "Search Console has been given it" in r["head"], True)
    check("a new post is added the same day, at the www address",
          "<loc>" + HOME + "blog-details.php?slug=new-post</loc>" in added, True)
    check("a post taken down comes out", "about.php" in dropped, False)


def test_googles_answer_explained():
    print("\nGoogle's answer about each page, in his words:")
    url = HOME + "about.php"
    on, why, _d = seo_index.explain(url, {"verdict": "PASS", "coverage": "Submitted and indexed"})
    check("indexed is on Google", on, True)
    on, why, doing = seo_index.explain(url, {"verdict": "NEUTRAL",
                                             "coverage": "Crawled - currently not indexed",
                                             "user_canonical": "https://worlddealtechnicalservices.com/about.php"})
    check("a canonical naming the copy without www is the reason given",
          (on, "different address" in why, "one-address" in doing), (False, True, True))
    on, why, _d = seo_index.explain(url, {"coverage": "Discovered - currently not indexed"})
    check("discovered: known, not read yet", "not read it yet" in why, True)
    on, why, _d = seo_index.explain(url, {"coverage": "URL is unknown to Google"})
    check("unknown: the sitemap is the answer", "never heard" in why, True)
    on, why, _d = seo_index.explain(url, {"coverage": "Excluded by 'noindex' tag"})
    check("noindex is said plainly", "noindex" in why, True)


def test_the_day_asks_google_and_queues_the_fixes_once():
    print("\nThe day asks Google about every page and queues the fixes once:")
    with _Cabinet() as cab:
        real = (seo_intel._gsc_session, seo_intel.gsc_inspect, seo_index._pages,
                sitemap.is_published, site_api.plan_share_buttons)
        asked = []
        seo_intel._gsc_session = lambda scope=None: (types.SimpleNamespace(token="t"), HOME, "")
        seo_intel.gsc_inspect = lambda u, creds=None, prop="": asked.append(u) or (
            {"ok": True, "verdict": "PASS", "coverage": "Submitted and indexed"}
            if u == HOME + "blog-details.php?slug=a" else
            {"ok": True, "verdict": "NEUTRAL", "coverage": "Crawled - currently not indexed",
             "user_canonical": u.replace("www.", "")})
        seo_index._pages = lambda: ([HOME, HOME + "about.php", HOME + "blog-details.php?slug=a"], False)
        sitemap.is_published = lambda home=None, get=None: False
        site_api.plan_share_buttons = lambda s=None: {"ok": True, "times": 3,
                                                      "pages": [{"page": "blog-details.php", "times": 3}]}
        try:
            r = seo_index._run(40)
            again = seo_index._run(40)
        finally:
            (seo_intel._gsc_session, seo_intel.gsc_inspect, seo_index._pages,
             sitemap.is_published, site_api.plan_share_buttons) = real
        check("every page was asked about", len(asked), 3)
        check("1 of 3 on Google", (r["on_google"], r["pages"]), (1, 3))
        kinds = sorted(x["kind"] for x in cab.sql("SELECT kind FROM site_queue"))
        check("the one-address fix, the sitemap and the share buttons are put to him",
              kinds, ["publish_sitemap", "repair_share_buttons", "use_one_domain"])
        check("and the Releaser can carry each out",
              all(it_release.plan_of(row)["do"] == "call"
                  for row in cab.sql("SELECT id, detail FROM site_queue")), True)
        check("a second run the same day asks Google nothing new and queues nothing more",
              (again["asked"], len(cab.sql("SELECT id FROM site_queue"))), (0, 3))
        st = seo_index.status()
        check("the office can show each page and why",
              [p["url"] for p in st["pages"] if not p["on_google"]], [HOME, HOME + "about.php"])
    check("it runs in the day, second in SEO's lane",
          [s[1] for s in day_run.STEPS if s[3] == "seo"][:2],
          ["pipelines.seo_audit", "pipelines.seo_index"])


def test_it_turns_the_audit_findings_into_fixes():
    print("\nIT turns the audit's findings into fixes, not advice:")
    ctx = {"env": {"SITE_URL": "https://www.worlddealtechnicalservices.com"}}
    real = (site_api.plan_share_buttons, site_api.plan_domains)
    site_api.plan_share_buttons = lambda s=None: {"ok": True, "times": 3,
                                                  "pages": [{"page": "blog-details.php", "times": 3}]}
    site_api.plan_domains = lambda s=None: {"ok": True, "times": 9, "wrong": {
        "worlddealtechnicalservices.com (without www)": 9},
        "pages": [{"page": "index.php", "wrong": {"x": 9}}]}
    try:
        share = it_fix.plans_for({"id": 4, "kind": "seo", "severity": "medium", "url": "",
                                  "detail": "[seo-audit:broken_internal_link] HTTP 404 — "
                                            "https://www.x.com/sharer/sharer.php?u=..."}, ctx)
        hosts = it_fix.plans_for({"id": 5, "kind": "seo", "severity": "medium", "url": "",
                                  "detail": "[seo-audit:two_hosts_serve_the_site] "
                                            "https://www.worlddealtechnicalservices.com/ and "
                                            "https://worlddealtechnicalservices.com/ both return 200"}, ctx)
    finally:
        site_api.plan_share_buttons, site_api.plan_domains = real
    check("a broken share link becomes the share-button repair",
          [(p["how"], p.get("call")) for p in share], [("call", "repair_share_buttons")])
    check("two addresses becomes the one-address fix, his saved address deciding",
          [(p["how"], p.get("call")) for p in hosts], [("call", "use_one_domain")])
    check("  and still tells him the redirect is his host's to set",
          "Hostinger" in hosts[0]["why"], True)


def main():
    for t in (test_the_canonical_names_the_one_address_and_nothing_else,
              test_the_share_buttons_go_to_the_networks_again,
              test_the_sitemap_lists_his_real_pages_at_his_one_address,
              test_the_sitemap_goes_up_and_keeps_up,
              test_googles_answer_explained,
              test_the_day_asks_google_and_queues_the_fixes_once,
              test_it_turns_the_audit_findings_into_fixes):
        t()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        sys.exit(1)
    print("indexing: all good — one address, a sitemap, and Google's own word on each page.")


if __name__ == "__main__":
    main()
