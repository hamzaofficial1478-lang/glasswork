"""
site_api_tests.py — run: python3 -m pipelines.site_api_tests

Full access: the agency changing the owner's website itself.

WHAT THIS IS GUARDING

He said "i want to test the agents no matter if they will do any issue i will
solve it". Fine — but "I will fix it" is a promise made before anything has
gone wrong, and the thing to avoid is the failure he CANNOT fix: two agents
writing the same page, a block stacked a hundred times by a nightly run, a
placeholder address published to the world, or a change with no way back.

Nothing here touches the network or his real site. The HTTP layer is replaced
throughout; what is tested is the decisions either side of it.
"""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

from coordination import board
from . import site_api as S

_fail = []
ROOT = Path(__file__).parents[1]


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
            return [dict(r) for r in c.execute(q, args)]
        finally:
            c.close()


FULL = {"mode": "full", "base": "https://x.ae/agency-api",
        "token": "t" * 40, "site": "https://x.ae"}


class _Site:
    """His website, as a dict of pages. Records every call."""

    def __init__(self, pages=None):
        self.pages = dict(pages or {})
        self.calls = []
        self.backups = []

    def __call__(self, path, method="GET", body=None, timeout=25, s=None):
        self.calls.append((method, path, body))
        if path == "/health":
            return {"ok": True, "status": "up", "php": "8.2", "writable": True}
        if path == "/pages" and method == "GET":
            return {"ok": True, "pages": [{"page": p, "bytes": len(h),
                                           "changed": "2026-08-25T00:00:00+00:00"}
                                          for p, h in sorted(self.pages.items())]}
        if path.startswith("/pages/") and method == "GET":
            name = path.split("/", 2)[2]
            if name not in self.pages:
                return {"ok": False, "error": "no such page"}
            return {"ok": True, "page": name, "html": self.pages[name],
                    "bytes": len(self.pages[name])}
        if path == "/pages" and method == "POST":
            self.pages[body["page"]] = body["html"]
            return {"ok": True, "page": body["page"], "backup": "b"}
        if path == "/head" and method == "POST":
            page, mark, block = body["page"], body["id"], body["html"]
            src = self.pages.get(page, "")
            open_, close = f"<!-- agency:{mark} -->", f"<!-- /agency:{mark} -->"
            wrapped = f"{open_}\n{block}\n{close}\n"
            import re
            pat = re.compile(re.escape(open_) + ".*?" + re.escape(close) + r"\s*", re.S)
            if pat.search(src):
                new, what = pat.sub(wrapped, src), "replaced"
            else:
                new, what = src.replace("</head>", wrapped + "</head>", 1), "inserted"
            if new == src:
                return {"ok": True, "page": page, "id": mark, "action": "unchanged",
                        "backup": ""}
            self.backups.append(page)
            self.pages[page] = new
            return {"ok": True, "page": page, "id": mark, "action": what,
                    "backup": f"{page}.bak"}
        return {"ok": False, "error": "unknown endpoint " + path}


def _with_site(site, fn):
    real = S.call
    S.call = site
    try:
        return fn()
    finally:
        S.call = real


def _with_drafts(lb, faq, fn):
    real = S._draft
    S._draft = lambda n: faq if "faq" in n else lb
    try:
        return fn()
    finally:
        S._draft = real


LB = ('<script type="application/ld+json">{"@type":'
      '"HomeAndConstructionBusiness","streetAddress":"Warehouse 12"}</script>')
FAQ = '<script type="application/ld+json">{"@type":"FAQPage"}</script>'
PAGE = "<html><head><title>x</title>\n</head><body>hi</body></html>"


# ---------------------------------------------------------------------------

def test_the_two_setup_boxes_finally_lead_somewhere():
    """
    The setup screen has always saved SITE_API_BASE and SITE_API_TOKEN, and
    nothing read them. He could choose Full access, fill both in, save, and get
    the read-only agency he already had — with nothing telling him so.
    """
    print("\nRead-only is respected even when a token is sitting there:")
    s = S.settings({"SITE_MODE": "readonly", "SITE_API_BASE": "https://x.ae/a",
                    "SITE_API_TOKEN": "t" * 40})
    check("mode", s["mode"], "readonly")
    check("and a write is refused with the reason",
          "read-only mode" in S.why_not_ready(s), True)

    print("\n...and Full access with a box left empty says WHICH box:")
    s = S.settings({"SITE_MODE": "full", "SITE_API_TOKEN": "t" * 40})
    check("no address", "no Admin API address" in S.why_not_ready(s), True)
    s = S.settings({"SITE_MODE": "full", "SITE_API_BASE": "https://x.ae/a"})
    check("no token", "no Admin API token" in S.why_not_ready(s), True)
    check("and it says where to find it — the SAFE file, not the readable one",
          "token.php" in S.why_not_ready(s), True)

    print("\n...and when both are there, it is ready:")
    s = S.settings({"SITE_MODE": "full", "SITE_API_BASE": "https://x.ae/agency-api/",
                    "SITE_API_TOKEN": "t" * 40})
    check("ready", S.why_not_ready(s), "")
    check("the trailing slash is not carried into every URL",
          s["base"], "https://x.ae/agency-api")


def test_only_the_releaser_may_change_the_site():
    """
    The handbook's first red line. A writer that can edit the site directly is
    the whole reason the Releaser exists.
    """
    print("\nAnybody may look:")
    site = _Site({"index.php": PAGE})
    r = _with_site(site, lambda: S.pages(FULL))
    check("reading works", r["ok"], True)

    print("\n...but only one agent may write:")
    with _Cabinet() as cab:
        r = _with_site(site, lambda: S.put_head_block(
            "index.php", "lb", LB, agent="content-writer", s=FULL))
        check("refused", r["ok"], False)
        check("by name", "content-writer" in r["error"], True)
        check("and the site was never called",
              [c for c in site.calls if c[0] == "POST"], [])
        print("  ...and it is not lost — it goes to the owner:")
        check("queued", isinstance(r.get("queued"), int), True)
        rows = cab.sql("SELECT agent, kind FROM site_queue WHERE status='pending'")
        check("one pending change", len(rows), 1)
        check("recorded against the agent that asked",
              rows[0]["agent"], "content-writer")

    print("\n...and the Releaser goes straight through:")
    r = _with_site(site, lambda: S.put_head_block(
        "index.php", "lb", LB, agent="it-releaser", s=FULL))
    check("done", r["ok"], True)
    check("the block is on the page", "agency:lb" in site.pages["index.php"], True)


def test_running_it_every_night_leaves_one_copy():
    """
    An agent that appends rather than replaces turns a nightly job into a page
    with three hundred copies of the same markup by the end of the year.
    """
    print("\nThe same block, applied four times:")
    site = _Site({"index.php": PAGE})
    for _ in range(4):
        _with_site(site, lambda: S.put_head_block("index.php", "lb", LB,
                                                  agent="it-releaser", s=FULL))
    check("one opening marker", site.pages["index.php"].count("<!-- agency:lb -->"), 1)
    check("one closing marker", site.pages["index.php"].count("<!-- /agency:lb -->"), 1)
    check("and only the first one wrote anything", len(site.backups), 1)

    print("\n...and a CHANGED block replaces rather than adds:")
    _with_site(site, lambda: S.put_head_block("index.php", "lb", "<script>v2</script>",
                                              agent="it-releaser", s=FULL))
    check("still one block", site.pages["index.php"].count("<!-- agency:lb -->"), 1)
    check("carrying the new content", "v2" in site.pages["index.php"], True)
    check("and the old content is gone",
          "HomeAndConstructionBusiness" in site.pages["index.php"], False)


def test_it_refuses_to_publish_the_placeholder_address():
    """
    The generator leaves the address in CAPITALS because it is nowhere on his
    site and must not be invented. Publishing 'YOUR STREET / UNIT' to the world
    would be worse than publishing nothing: Google checks the address against
    his Business Profile, and a mismatch loses the map ranking the whole block
    exists to win.
    """
    print("\nAn unfilled address stops the job before it starts:")
    site = _Site({"index.php": PAGE})
    unfilled = LB.replace("Warehouse 12", "YOUR STREET / UNIT")
    r = _with_site(site, lambda: _with_drafts(
        unfilled, FAQ, lambda: S.apply_schema(s=FULL)))
    check("refused", r["ok"], False)
    check("saying it will not guess an address",
          "will not guess" in r["error"], True)
    check("and naming the one command that fixes it, not a file to hand-edit",
          "--set-address" in r["error"], True)
    check("nothing was written", [c for c in site.calls if c[0] == "POST"], [])

    print("\n...and a missing draft is named, not guessed around:")
    r = _with_site(site, lambda: _with_drafts(
        "", FAQ, lambda: S.apply_schema(s=FULL)))
    check("refused", r["ok"], False)
    check("with the command that makes it",
          "seo_answers --localbusiness" in r["error"], True)


def test_the_faq_markup_goes_on_the_faq_page_only():
    """
    FAQ markup is only allowed on a page that really shows those questions.
    On all seven, Google may ignore all of it, or penalise the site.
    """
    print("\nSeven pages, one of them the FAQ:")
    site = _Site({p: PAGE for p in ("index.php", "service.php", "about.php",
                                    "gallery.php", "blog.php", "faq.php",
                                    "contact.php")})
    r = _with_site(site, lambda: _with_drafts(
        LB, FAQ, lambda: S.apply_schema(agent="it-releaser", s=FULL)))
    check("it ran", r["ok"], True)
    check("the business card went on all seven",
          sum(1 for h in site.pages.values() if "agency:localbusiness" in h), 7)
    check("the questions went on exactly one",
          sum(1 for h in site.pages.values() if "agency:faqpage" in h), 1)
    check("and that one is the FAQ page",
          "agency:faqpage" in site.pages["faq.php"], True)
    check("the contact page did not get it",
          "agency:faqpage" in site.pages["contact.php"], False)

    print("\n...and a second run reports honestly that there is nothing to do:")
    r = _with_site(site, lambda: _with_drafts(
        LB, FAQ, lambda: S.apply_schema(agent="it-releaser", s=FULL)))
    check("0 changed", "0 page(s) changed" in r["head"], True)
    check("8 already correct", "8 already correct" in r["head"], True)


def test_undoing_is_never_held_in_a_queue():
    """
    If an agent has put something wrong on the site at two in the morning, the
    fix must not sit in an approval queue until breakfast. Restoring cannot
    invent content — it can only put back a file this system already saved.
    """
    print("\nA rollback is not gated behind the Releaser:")
    seen = []

    def site(path, method="GET", body=None, timeout=25, s=None):
        seen.append((method, path, body))
        return {"ok": True, "page": "index.php", "restored_from": "index.php.bak"}

    r = _with_site(site, lambda: S.undo(page="index.php", agent="it-watcher", s=FULL))
    check("allowed", r["ok"], True)
    check("and it really called the site", seen[0][1], "/rollback")

    print("\n...but it still has to be told WHAT to put back:")
    r = _with_site(site, lambda: S.undo(s=FULL))
    check("refused", r["ok"], False)
    check("asking which", "which page" in r["error"], True)


def test_a_website_that_is_down_is_a_tuesday_not_a_crash():
    print("\nEvery failure comes back as an answer, never an exception:")
    def dead(url, **kw):
        raise OSError("connection refused")
    real = S.urllib.request.urlopen
    S.urllib.request.urlopen = dead
    try:
        r = S.call("/health", s=FULL)
    finally:
        S.urllib.request.urlopen = real
    check("not ok", r["ok"], False)
    check("and it says what happened", "connection refused" in r["error"], True)

    print("\n...a wrong token is explained, not just '401':")
    import urllib.error
    import io

    def refused(url, **kw):
        raise urllib.error.HTTPError("u", 401, "Unauthorized", {},
                                     io.BytesIO(b'{"error":"bad token"}'))
    S.urllib.request.urlopen = refused
    try:
        r = S.call("/health", s=FULL)
    finally:
        S.urllib.request.urlopen = real
    check("refused", r["ok"], False)
    check("naming the two things to compare", "token.php" in r["error"], True)

    print("\n...and a PHP notice printed above the JSON is named as such:")
    def noisy(url, **kw):
        class R:
            def read(self): return b"<br />Notice: undefined index\n{\"ok\":true}"
            def __enter__(self): return self
            def __exit__(self, *a): return False
        return R()
    S.urllib.request.urlopen = noisy
    try:
        r = S.call("/health", s=FULL)
    finally:
        S.urllib.request.urlopen = real
    check("not ok", r["ok"], False)
    check("and it shows him what came back instead",
          "Notice: undefined index" in r["error"], True)


def test_it_writes_only_to_pages_the_site_actually_serves():
    """
    THE THIRTEEN WRONG FILES.

    His first real --apply-schema put LocalBusiness markup on Home.html,
    about.html, blog.html, faq.html, install.php and default.php — an old
    duplicate copy of the whole site sitting beside the live one, plus the
    installer — and REFUSED every page he actually serves.

    Two mistakes in one. It read the directory listing and believed it, and
    the directory is not the site. And when the real pages said "no </head>"
    it gave up instead of asking why.
    """
    print("\nThe disk holds more than the site serves:")
    site = _Site({"index.php": PAGE, "about.php": PAGE, "faq.php": PAGE,
                  "Home.html": PAGE, "about.html": PAGE, "faq.html": PAGE,
                  "install.php": PAGE, "default.php": PAGE})
    real_live, real_holder = S.live_pages, S.head_holder
    S.live_pages = lambda s=None: ["index.php", "about.php", "faq.php"]
    S.head_holder = lambda c, s=None: ""
    try:
        plan = _with_site(site, lambda: _with_drafts(
            LB, FAQ, lambda: S.schema_plan(FULL)))
    finally:
        S.live_pages, S.head_holder = real_live, real_holder
    targets = sorted({j["page"] for j in plan["jobs"]})
    check("only the pages the site links to",
          targets, ["about.php", "faq.php", "index.php"])
    check("the old .html duplicate is left alone",
          any(t.endswith(".html") for t in targets), False)
    check("and so is the installer", "install.php" in targets, False)
    check("what was skipped is counted, not silently dropped",
          sorted(plan["ignored"]),
          ["Home.html", "about.html", "default.php", "faq.html", "install.php"])
    check("and the headline says why", "not pages your site links to"
          in plan["head"], True)

    print("\n...and if the site cannot be read, it falls back rather than "
          "doing nothing:")
    S.live_pages = lambda s=None: []
    try:
        plan = _with_site(site, lambda: _with_drafts(
            LB, FAQ, lambda: S.schema_plan(FULL)))
    finally:
        S.live_pages, S.head_holder = real_live, real_holder
    check("every file on disk is a candidate again",
          len({j["page"] for j in plan["jobs"]}) >= 8, True)


def test_a_shared_header_gets_the_block_once():
    """
    Every real page came back "that page has no </head> to insert before",
    because his seven pages share one header include and that is where the
    </head> lives. One block in the header is one block on every page — which
    is what he asked for in the first place.
    """
    print("\nWhen the pages have no head of their own, find the file that does:")
    incl = "<html><head><title>x</title>\n</head><body>"
    pg = "<?php include 'header.php'; ?><h1>hello</h1>"
    site = _Site({"index.php": pg, "about.php": pg, "faq.php": pg,
                  "header.php": incl})
    holder = _with_site(site, lambda: S.head_holder(
        ["index.php", "about.php", "faq.php"], FULL))
    check("the include is found", holder, "header.php")

    real_live = S.live_pages
    S.live_pages = lambda s=None: ["index.php", "about.php", "faq.php"]
    try:
        plan = _with_site(site, lambda: _with_drafts(
            LB, FAQ, lambda: S.schema_plan(FULL)))
    finally:
        S.live_pages = real_live
    check("one job, not seven", len([j for j in plan["jobs"]
                                     if j["id"] == S.SCHEMA_ID]), 1)
    check("and it targets the header", plan["jobs"][0]["page"], "header.php")
    check("and the plan records which file that is",
          plan["head_include"], "header.php")
    # The headline shows the outstanding PROBLEM when there is one — here the
    # FAQ block, which cannot live in a shared header. That is the right thing
    # to lead with, so this checks the problem is what surfaces.
    check("the headline leads with what still needs him",
          "FAQ markup cannot go in a shared header" in plan["head"], True)

    print("\n...and the FAQ block is NOT put in the shared header:")
    check("it is not silently added to every page",
          any(j["id"] == S.FAQ_ID for j in plan["jobs"]), False)
    check("he is told to place it himself, and why",
          any("Google may ignore all of it" in p for p in plan["problems"]),
          True)

    print("\n...while a site whose pages DO have their own head is untouched "
          "by any of this:")
    plain = _Site({"index.php": PAGE, "faq.php": PAGE})
    check("no include hunted for",
          _with_site(plain, lambda: S.head_holder(["index.php"], FULL)), "")


def test_the_markup_can_be_taken_straight_back_off():
    """
    Thirteen files were edited that should not have been. Undoing that had to
    be one command — and a precise one: remove exactly what the agency put
    there, not roll the page back to whatever the newest backup happens to be.
    """
    print("\nOne command takes it all back off:")
    marked = ("<html><head><title>x</title>\n"
              "<!-- agency:localbusiness -->\n<script>lb</script>\n"
              "<!-- /agency:localbusiness -->\n</head><body>hi</body></html>")
    site = _Site({"Home.html": marked, "install.php": marked,
                  "index.php": PAGE})

    def call(path, method="GET", body=None, timeout=25, s=None):
        if path.startswith("/pages/") and method == "GET":
            name = path.split("/", 2)[2]
            return {"ok": True, "page": name, "html": site.pages.get(name, "")}
        if path == "/pages" and method == "POST":
            site.pages[body["page"]] = body["html"]
            return {"ok": True, "page": body["page"], "backup": "b"}
        return site(path, method, body, timeout, s)

    r = _with_site(call, lambda: S.undo_schema(s=FULL))
    check("both marked files cleaned", len(r["cleaned"]), 2)
    check("the block is gone",
          "agency:localbusiness" in site.pages["Home.html"], False)
    check("and the rest of the page is intact",
          "<title>x</title>" in site.pages["Home.html"], True)
    check("the head still closes properly",
          "</head>" in site.pages["Home.html"], True)
    check("a page that never had one is untouched",
          site.pages["index.php"], PAGE)

    print("\n...and running it twice is not an error:")
    r = _with_site(call, lambda: S.undo_schema(s=FULL))
    check("nothing left to clean", r["cleaned"], [])
    check("still ok", r["ok"], True)


def main():
    for t in (test_the_two_setup_boxes_finally_lead_somewhere,
              test_only_the_releaser_may_change_the_site,
              test_running_it_every_night_leaves_one_copy,
              test_it_refuses_to_publish_the_placeholder_address,
              test_the_faq_markup_goes_on_the_faq_page_only,
              test_undoing_is_never_held_in_a_queue,
              test_a_website_that_is_down_is_a_tuesday_not_a_crash,
              test_it_writes_only_to_pages_the_site_actually_serves,
              test_a_shared_header_gets_the_block_once,
              test_the_markup_can_be_taken_straight_back_off):
        t()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        sys.exit(1)
    print("all site_api tests passed")


if __name__ == "__main__":
    main()
