"""
blueprint_tests.py — run: python -m pipelines.blueprint_tests

"what website is missing or what should be in the website that makes the
 website setup complete in terms of seo"

What is protected here (all on stand-in websites; nothing reaches a network):
  - a bare site is told what it is missing: robots.txt, sitemap, llms.txt,
    markup, real projects, a page per service — and a complete site is told
    nothing;
  - robots.txt is read properly: shutting every robot out is caught, shutting
    one robot out is not;
  - the robots.txt and llms.txt the agency writes keep his own lines, never shut
    anyone out, and say only what is already true;
  - IT's gaps become tickets the Fixer turns into a change for his yes or steps
    for him; everyone else's go to the MD with the evidence, a few per desk at a
    time, and come off the list when the site has them;
  - a check that was cut short never calls pages missing it did not read, and a
    recent check is not repeated.
"""
import re
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

from coordination import board, profile, uae
from . import it_fix, it_release, site_api, site_blueprint as bp

ROOT = Path(__file__).resolve().parents[1]
_fail = []
HOME = "https://www.example-trade.test"


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

    def sql(self, q, args=()):
        c = sqlite3.connect(board.DB)
        c.row_factory = sqlite3.Row
        try:
            rows = [dict(r) for r in c.execute(q, args)]
            c.commit()
            return rows
        finally:
            c.close()


def _page(title, body="", head="", links=()):
    nav = "".join(f'<a href="{u}">{t}</a>' for u, t in links)
    return (f"<!doctype html><html{head and ' ' + head}><head><title>{title}</title></head>"
            f"<body><nav>{nav}</nav><h1>{title}</h1>{body}</body></html>")


def _site(files, pages, soft=False):
    """A get() for a stand-in site: {path: (status, text, type)}. soft: unknown
    addresses answer 200 with the home page, as many cheap hosts do."""
    calls = []

    def get(url, head=False):
        calls.append(url)
        path = url[len(HOME):] or "/"
        if path in pages:
            return 200, ("" if head else pages[path]), "text/html; charset=utf-8"
        if path in files:
            st, text, ct = files[path]
            return st, ("" if head else text), ct
        if soft:
            return 200, ("" if head else pages["/"]), "text/html"
        return 404, ("" if head else "<html><body>Not found</body></html>"), "text/html"
    get.calls = calls
    return get


def _bare():
    pages = {"/": _page("Home", "<p>We do good work.</p>", links=[("/contact.php", "Contact us")]),
             "/contact.php": _page("Contact us", '<form><input type="email" name="email"></form>')}
    return _site({}, pages, soft=True)


def _complete():
    services = bp._services()
    links = [("/contact.php", "Contact"), ("/about.php", "About us"), ("/projects.php", "Our projects"),
             ("/reviews.php", "Reviews"), ("/faq.php", "FAQ"), ("/privacy.php", "Privacy"),
             ("/blog.php", "Blog")]
    for key, label, _names in services:
        links.append((f"/{key.replace('_', '-')}.php", label.title()))
    for area in bp._areas():
        links.append((f"/{area.lower().replace(' ', '-')}.php", f"Work in {area}"))
    head = ('<link rel="icon" href="/favicon.png"><meta property="og:title" content="X">'
            '<meta property="og:image" content="/x.jpg">'
            '<script type="application/ld+json">{"@type": "LocalBusiness", "name": "X"}</script>')
    pages = {"/": _page("Home", head, head='lang="en"', links=links).replace("<title>Home</title>",
                                                                             "<title>Home</title>" + head)}
    for path, title in links:
        body = "<p>Office 12, Al Quoz Industrial Area, Dubai</p>" if path == "/contact.php" else "<p>Text.</p>"
        pages[path] = _page(title, body, links=[("/", "Home")])
    locs = "".join(f"<url><loc>{HOME}{p}</loc></url>" for p in pages)
    files = {"/robots.txt": (200, f"User-agent: *\nAllow: /\nSitemap: {HOME}/sitemap.php\n", "text/plain"),
             "/sitemap.php": (200, f'<?xml version="1.0"?><urlset>{locs}</urlset>', "application/xml"),
             "/llms.txt": (200, "# X\n\n> A business.\n", "text/plain")}
    return _site(files, pages)


def test_bare_and_complete():
    print("\nA bare site is told what it is missing; a complete one is told nothing:")
    site = bp.read_site(HOME, get=_bare())
    check("the stand-in site was read", (site["ok"], len(site["pages"])), (True, 2))
    got = bp.assess(site)
    codes = {g["code"] for g in got["gaps"]}
    for code in ("robots_missing", "sitemap_missing", "llms_missing", "soft_404", "schema_missing",
                 "projects", "about", "reviews", "favicon_missing", "address"):
        check(f"  missing: {code}", code in codes, True)
    first = bp._services()[0]
    check("  and a page for the first service he sells", f"service:{first[0]}" in codes, True)
    check("  the contact page it has is not called missing", "contact" in codes, False)
    check("  and it is counted as there", "contact" in {h["code"] for h in got["have"]}, True)
    it = [g for g in got["gaps"] if g["sector"] == "IT"]
    check("the files and markup are IT's", {g["code"] for g in it} >= {"robots_missing", "sitemap_missing",
                                                                       "llms_missing", "schema_missing"}, True)
    check("  the ones with one right answer carry the routine that fixes them",
          {g["code"]: g["fix"] for g in it if g["fix"]},
          {"robots_missing": "put_robots", "sitemap_missing": "publish_sitemap",
           "llms_missing": "put_llms", "schema_missing": "apply_schema"})
    check("service pages are SEO's to judge before Content writes",
          {g["sector"] for g in got["gaps"] if g["code"].startswith("service:")}, {"SEO"})
    check("worst first", got["gaps"][0]["severity"], "high")

    full = bp.read_site(HOME, get=_complete())
    done = bp.assess(full)
    check("a complete site has nothing missing", [g["code"] for g in done["gaps"]], [])
    check("  and every check was counted as there", len(done["have"]), len(done["checked"]))


def test_robots():
    print("\nrobots.txt is read properly, and written carefully:")
    blocks = bp._robots_blocks_all
    check("everyone shut out is caught", blocks("User-agent: *\nDisallow: /\n"), True)
    check("  also when * shares a group", blocks("User-agent: a\nUser-agent: *\nDisallow: /"), True)
    check("one robot shut out is not everyone", blocks("User-agent: BadBot\nDisallow: /\n\nUser-agent: *\nAllow: /"), False)
    check("a private folder is not the whole site", blocks("User-agent: *\nDisallow: /admin/"), False)
    check("comments are ignored", blocks("# Disallow: /\nUser-agent: *\nDisallow: /tmp # Disallow: /"), False)
    new = bp.robots_text(None, HOME + "/sitemap.php")
    check("a new robots.txt lets everyone in and names the sitemap",
          new, f"User-agent: *\nAllow: /\n\nSitemap: {HOME}/sitemap.php\n")
    his = "User-agent: *\nDisallow: /private/\n"
    added = bp.robots_text(his, HOME + "/sitemap.php")
    check("his own lines are kept and the sitemap added", added.startswith(his.rstrip("\n")) and
          added.endswith(f"Sitemap: {HOME}/sitemap.php\n"), True)
    check("one that already names a sitemap is left alone", bp.robots_text(his + "Sitemap: x\n", "y"), None)
    check("with no sitemap anywhere, an existing file is left alone", bp.robots_text(his, ""), None)
    check("nothing it writes shuts anyone out", any(blocks(x or "") for x in (new, added)), False)


def test_llms():
    print("\nllms.txt says only what is true, as plain text:")
    t = bp.llms_text([{"url": HOME + "/", "title": "Home | X"},
                      {"url": HOME + "/shower-glass.php", "title": "Frameless shower glass - X"},
                      {"url": HOME + "/about.php", "title": ""}],
                     home=HOME + "/", name="X Glass", description="Glass work in Dubai.",
                     areas="Dubai and Sharjah", phone="+971 4 000 0000", email="info@example-trade.test")
    check("it starts with the business's name", t.splitlines()[0], "# X Glass")
    check("the pages are links with their own titles",
          ("- [Home](" in t, "- [Frameless shower glass](" in t, "- [About](" in t), (True, True, True))
    check("how to reach them", "- Phone: +971 4 000 0000" in t and "- Email: info@example-trade.test" in t, True)
    check("no markup in it", "<" in t, False)
    blank = bp.llms_text([{"url": HOME + "/", "title": ""}], home=HOME + "/")
    check("nothing invented when nothing is known", ("Phone" in blank, "Works in" in blank, ">" in blank),
          (False, False, False))


def test_handing_on():
    print("\nIT gets tickets, the rest go to the MD a few at a time, and gaps come off when fixed:")
    with _Cabinet() as cab:
        got = bp.assess(bp.read_site(HOME, get=_bare()))
        t0 = int(time.time()) - 3600
        r = bp.hand_on(got, now=t0)
        issues = cab.sql("SELECT kind, severity, detail, status FROM site_issues WHERE kind='blueprint'")
        check("every IT gap is a ticket", len(issues), len([g for g in got["gaps"] if g["sector"] == "IT"]))
        check("  each names its gap for the Fixer", all(re.match(r"\[blueprint:[a-z0-9_]+\]", i["detail"]) for i in issues), True)
        props = cab.sql("SELECT sector, title, evidence, status FROM proposals")
        check("everyone else's are proposals for the MD, with the evidence",
              bool(props) and all(p["evidence"] for p in props), True)
        per = {}
        for p in props:
            per[p["sector"]] = per.get(p["sector"], 0) + 1
        check("no desk is handed more than its share at once",
              all(n <= bp.OPEN_ASKS.get(s, 2) for s, n in per.items()), True)
        waiting = [g for g in cab.sql("SELECT code, ref FROM site_gaps WHERE status='open'") if not g["ref"]]
        check("the rest wait their turn, on the list", bool(waiting), True)
        again = bp.hand_on(got, now=t0 + 100)
        check("asking again files nothing twice", (again["filed"], again["proposed"]), ([], []))

        # The MD finishes an SEO page: the next one goes in.
        one = cab.sql("SELECT ref FROM site_gaps WHERE sector='SEO' AND ref LIKE 'proposal:%' LIMIT 1")[0]["ref"]
        cab.sql("UPDATE proposals SET status='superseded', decided_at=1 WHERE id=?", (one.split(':')[1],))
        nxt = bp.hand_on({"gaps": bp.stored()["gaps"]}, now=t0 + 200, route_only=True)
        check("a freed place goes to the next gap", len(nxt["proposed"]), 1)

        # The site gains robots.txt and a projects page: both come off.
        gone = {"robots_missing", "projects"}
        left = {"gaps": [g for g in got["gaps"] if g["code"] not in gone], "checked": got["checked"]}
        out = bp.hand_on(left, now=t0 + 300)
        check("what the site now has comes off the list", sorted(out["closed"]), sorted(gone))
        rid = cab.sql("SELECT ref FROM site_gaps WHERE code='robots_missing'")[0]["ref"].split(":")[1]
        check("  and its ticket is closed", cab.sql("SELECT status FROM site_issues WHERE id=?", (rid,))[0]["status"], "closed")
        pr = cab.sql("SELECT ref FROM site_gaps WHERE code='projects'")[0]["ref"]
        if pr.startswith("proposal:"):
            st = cab.sql("SELECT status, why FROM proposals WHERE id=?", (pr.split(':')[1],))[0]
            check("  and a proposal still waiting is withdrawn, with the reason",
                  (st["status"], "has this now" in st["why"]), ("superseded", True))
        s = bp.stored()
        check("the app reads where each gap is", all(g.get("where") for g in s["gaps"]), True)
        check("  and what came off lately", {g["code"] for g in s["fixed_lately"]} >= gone, True)


def test_the_fixer_and_releaser():
    print("\nThe Fixer turns IT's gaps into a change for his yes, or steps for him:")
    with _Cabinet():
        def issue(code, title="x"):
            g = bp._gap(code, "files", "IT", "medium", title, "Because.", "seen")
            return {"id": 1, "kind": "blueprint", "severity": "medium", "detail": bp._detail(g), "url": ""}
        p = it_fix.plans_for(issue("robots_missing"), {})
        check("robots.txt becomes a change", (p[0]["recipe"], p[0]["how"], p[0]["call"]),
              ("fix_blueprint", "call", "put_robots"))
        p = it_fix.plans_for(issue("sitemap_missing", "A sitemap"), {})
        check("a sitemap gap is the blueprint's, not read as his order", (p[0]["recipe"], p[0]["call"]),
              ("fix_blueprint", "publish_sitemap"))
        p = it_fix.plans_for(issue("favicon_missing"), {})
        check("a favicon needs his logo, so it is steps for him", (p[0]["how"], bool(p[0]["steps"])), ("advice", True))
        p = it_fix.plans_for(issue("robots_blocks_all"), {})
        check("shutting robots out may be deliberate, so he decides", p[0]["how"], "advice")
    for call in ("put_robots", "put_llms"):
        check(f"the Releaser may carry out {call}", call in it_release.ALLOWED_CALLS and callable(getattr(site_api, call)), True)
        plan = it_release.plan_of({"id": 9, "kind": "fix_blueprint", "agent": "it-fixer",
                                   "detail": '{"change": {"run": "%s"}, "why": "w"}' % call})
        check(f"  an approved {call} is a call, not a refusal", plan.get("do"), "call")
    with _Cabinet() as cab:
        r = site_api.put_robots(agent="it-fixer")
        check("anyone but the Releaser is queued, not written",
              (bool(r.get("queued")), r.get("ok"), len(cab.sql("SELECT 1 FROM site_queue WHERE kind='put_robots'"))),
              (True, False, 1))


def test_partial_and_recent():
    print("\nA cut-short check calls nothing missing it did not read; a recent one is not repeated:")
    site = bp.read_site(HOME, get=_bare())
    site["partial"] = True
    got = bp.assess(site)
    check("no page gaps from a partial read", [g["code"] for g in got["gaps"] if g["part"] == "pages"], [])
    check("  and they are not counted as checked", "projects" in got["checked"], False)
    with _Cabinet():
        r1 = bp.check(home=HOME, get=_bare(), now=2_000_000)
        check("a first check reads and hands on", (r1["ok"], r1.get("skipped", False), bool(r1["filed"])), (True, False, True))

        def no_requests(url, head=False):
            raise AssertionError("read the site again")
        r2 = bp.check(home=HOME, get=no_requests, now=2_000_000 + 3600)
        check("an hour later it does not read the site again", (r2["ok"], r2.get("skipped")), (True, True))
        r3 = bp.check(home=HOME, get=_bare(), now=2_000_000 + bp.CHECK_EVERY + 1)
        check("after the wait it looks again", r3.get("skipped", False), False)


def main():
    test_bare_and_complete()
    test_robots()
    test_llms()
    test_handing_on()
    test_the_fixer_and_releaser()
    test_partial_and_recent()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        return 1
    print("All blueprint tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
