"""
audit_tests.py — run: python3 -m pipelines.audit_tests

The SEO technical audit, whose method is BeyondSEO's (pipelines/seo_audit.py).

WHAT THIS IS GUARDING

Three promises, and the first is the one an audit most often breaks:

  1. ABSENCE IS EARNED. A page that failed to load, or arrived as a
     JavaScript shell, is not evidence that it has no title. No "missing"
     finding is ever filed from a capture that could not have seen the thing.
  2. ONE FACT, ONE TICKET. The first run against his real site produced 22
     findings; 17 were the same three facts counted per page. The site-wide
     pass exists so IT gets one ticket for one fix.
  3. VERIFIED, NOT ASSUMED. A ticket closes only when a clean read shows the
     problem gone — and reopens when it was marked deployed and is still
     there.

Nothing here touches the network. Pages are built from HTML strings.
"""
import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

from coordination import board
from . import it_fix
from . import seo_audit as A

_fail = []
SITE = "https://www.glass.ae"


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


def page(path, html, status=200, final=None, error=None, x_robots=""):
    url = SITE + path
    p = {"url": url, "fetched_at": int(time.time()), "x_robots": x_robots}
    if error:
        p["error"] = error
        return p
    p.update({"status": status, "final_url": final or url, "redirects": []})
    if status < 400:
        p["data"] = A.extract(html, final or url)
    return p


def doc(title="Glass partitions Dubai", desc="We fit glass partitions.",
        h1="Glass partitions", canonical=None, body="", head_extra="",
        words=200):
    canon = f'<link rel="canonical" href="{canonical}">' if canonical else ""
    text = " ".join(["word"] * words)
    return (f"<html><head><title>{title}</title>"
            f'<meta name="description" content="{desc}">{canon}{head_extra}</head>'
            f"<body><h1>{h1}</h1><p>{text}</p>{body}</body></html>")


def codes(findings):
    return sorted(f["code"] for f in findings)


def _fresh_db():
    d = Path(tempfile.mkdtemp())
    db = d / "agency.db"
    con = sqlite3.connect(db)
    con.executescript((Path(__file__).parents[1] / "coordination" / "schema.sql")
                      .read_text(encoding="utf-8"))
    con.commit()
    con.close()
    return str(db)


# ---------------------------------------------------------------------------

def test_absence_is_earned():
    """
    BeyondSEO's rule: positive observations survive a partial capture; absence
    claims cannot. The morning the site is slow must not file twenty false
    "missing title" tickets.
    """
    print("\nA page that failed to load says it failed — and nothing else:")
    f = A.page_findings(page("/a.php", "", error="ConnectTimeout: timed out"))
    check("only fetch_failed", codes(f), ["fetch_failed"])

    print("\nAn error status is the finding, not a list of missing tags:")
    f = A.page_findings(page("/gone.php", "", status=404))
    check("only http_error", codes(f), ["http_error"])

    print("\nA JavaScript shell does not prove the page has no heading:")
    shell = "<html><head></head><body><div id=app></div>" + "<script></script>" * 5 + "</body></html>"
    f = A.page_findings(page("/app", shell))
    check("the shell is reported", "possible_js_shell" in codes(f), True)
    check("but no missing title/H1/description is claimed",
          [c for c in codes(f) if c.startswith("missing_")], [])

    print("\n...while a clean capture with nothing there IS reported:")
    bare = "<html><head></head><body><p>" + "word " * 200 + "</p></body></html>"
    f = A.page_findings(page("/bare", bare))
    for c in ("missing_title", "missing_description", "missing_h1", "canonical_not_declared"):
        check(c, c in codes(f), True)


def test_the_per_page_checks():
    print("\nThe checks read what is actually in the HTML:")
    two = doc(head_extra="<title>Second title</title>"
                         '<meta name="description" content="another">')
    f = codes(A.page_findings(page("/p", two, final=SITE + "/p")))
    check("two titles", "multiple_titles" in f, True)
    check("two descriptions", "multiple_meta_descriptions" in f, True)

    svg = doc(body="<svg><title>logo</title></svg>")
    check("an SVG <title> is an image label, not a second document title",
          "multiple_titles" in codes(A.page_findings(page("/s", svg))), False)

    bad = doc(head_extra='<script type="application/ld+json">{"@type": "Local</script>')
    check("JSON-LD that does not parse is caught",
          "jsonld_syntax_error" in codes(A.page_findings(page("/j", bad))), True)
    blk = '<script type="application/ld+json">{"@type":"LocalBusiness","name":"X"}</script>'
    check("the same block twice is caught",
          "jsonld_duplicate_block" in codes(A.page_findings(page("/d", doc(head_extra=blk * 2)))), True)

    imgs = doc(body='<img src="/a.jpg"><img src="/b.jpg" alt="shower screen" width=4 height=3>')
    f = A.page_findings(page("/i", imgs))
    check("an image with no alt", "missing_image_alt" in codes(f), True)
    check("and it names that image, not the good one",
          "/a.jpg" in next(x["evidence"] for x in f if x["code"] == "missing_image_alt")
          and "/b.jpg" not in next(x["evidence"] for x in f if x["code"] == "missing_image_alt"),
          True)

    ni = doc(head_extra='<meta name="robots" content="noindex, follow">')
    check("noindex in the page", "noindex_observed" in codes(A.page_findings(page("/n", ni))), True)
    check("noindex in the header",
          "noindex_observed" in codes(A.page_findings(page("/h", doc(), x_robots="noindex"))), True)

    nf = ("<html><head><title>404 Page not found</title></head><body>"
          "<h1>404</h1><p>Sorry, page not found.</p></body></html>")
    check("a 'not found' screen answering 200 is a soft-404 candidate",
          codes(A.page_findings(page("/old", nf))), ["missing_content_candidate"])


def test_canonicals_are_judged_like_a_person_would():
    print("\nA canonical to a different page is worth a look:")
    f = A.page_findings(page("/blog-details.php", doc(canonical=SITE + "/blog.php")))
    check("canonical_elsewhere", "canonical_elsewhere" in codes(f), True)

    print("\n...an index file pointing at its folder is the textbook fix, not a finding:")
    f = A.page_findings(page("/index.php", doc(canonical=SITE + "/")))
    check("/index.php -> / is not filed", "canonical_elsewhere" in codes(f), False)

    print("\n...and www against non-www is one site-wide decision, not a finding per page:")
    f = A.page_findings(page("/about.php", doc(canonical="https://glass.ae/about.php")))
    check("not filed per page", "canonical_elsewhere" in codes(f), False)


def test_one_fact_is_one_ticket():
    """
    The first run against his real site: 22 findings, 17 of them the same
    three facts counted once per page. After the site-wide pass: 5, each true
    and distinct.
    """
    print("\n/ and /index.php are one page, not two pages sharing a title:")
    same = [page("/", doc(canonical=SITE + "/")),
            page("/index.php", doc(canonical=SITE + "/"))]
    check("no duplicate_title", "duplicate_title" in codes(A.site_findings(same, {})), False)

    print("\n...but two different pages with one title is a real duplicate:")
    diff = [page("/glass.php", doc(canonical=SITE + "/glass.php")),
            page("/doors.php", doc(canonical=SITE + "/doors.php"))]
    f = A.site_findings(diff, {})
    check("duplicate_title", "duplicate_title" in codes(f), True)
    check("duplicate_meta_description", "duplicate_meta_description" in codes(f), True)
    check("filed once, listing both pages",
          len(next(x for x in f if x["code"] == "duplicate_title")["affected_urls"]), 2)

    print("\nThe header's images are one template fix, not a ticket per page:")
    hdr = '<img src="/logo.svg">'
    pages = [page(f"/{n}.php", doc(title=n, desc=n, canonical=f"{SITE}/{n}.php", body=hdr))
             for n in ("a", "b", "c")]
    tmpl = A.template_images(pages)
    check("the logo is recognised as template", "/logo.svg" in tmpl, True)
    per_page = [c for p in pages for c in codes(A.page_findings(p, tmpl))]
    check("no per-page ticket for it", [c for c in per_page if "image" in c], [])
    site = [x for x in A.site_findings(pages, {}) if x["code"] == "missing_image_alt"]
    check("one site-wide ticket instead", len(site), 1)

    print("\nBoth hosts answering with no redirect is one finding:")
    hosts = {"www.glass.ae": {"status": 200, "hops": 0},
             "glass.ae": {"status": 200, "hops": 0}}
    pages = [page(f"/{n}.php", doc(title=n, desc=n, canonical=f"https://glass.ae/{n}.php"))
             for n in ("a", "b")]
    f = A.site_findings(pages, {}, hosts)
    check("two_hosts_serve_the_site, once",
          [x["code"] for x in f].count("two_hosts_serve_the_site"), 1)
    hosts["glass.ae"] = {"status": 200, "hops": 1, "final": "https://www.glass.ae/"}
    check("a canonical host that redirects is its own finding",
          "canonical_target_redirects" in codes(A.site_findings(pages, {}, hosts)), True)

    print("\nA link on his site to a page that 404s:")
    linker = page("/home", doc(body=f'<a href="{SITE}/missing.php">x</a>'))
    f = A.site_findings([linker], {SITE + "/missing.php": 404})
    check("broken_internal_link", codes(f), ["broken_internal_link"])
    check("and it names the page carrying the link",
          SITE + "/home" in f[0]["affected_urls"], True)
    check("an unknown status is not called broken",
          A.site_findings([linker], {SITE + "/missing.php": None}), [])


def test_every_finding_carries_the_contract():
    print("\nEvery finding says what, how sure, why, what to do and how to tell:")
    f = A.page_findings(page("/bare", "<html><head></head><body><p>"
                                      + "word " * 200 + "</p></body></html>"))
    c = A.contract(next(x for x in f if x["code"] == "missing_h1"), int(time.time()))
    for key in ("observation", "captured_at", "claim_type", "why_it_matters",
                "action", "acceptance_check", "uncertainty", "priority"):
        check(key, bool(c.get(key)), True)
    check("a missing tag on a clean read is a verified fact", c["claim_type"], "verified fact")
    check("and it admits what was not measured",
          any("not measured" in u for u in c["uncertainty"]), True)
    check("every check in the catalogue has all five parts",
          all(len(v) == 5 and all(v) for v in A.CHECKS.values()), True)
    check("every check's name survives the ticket marker — 'missing_h1' once did not",
          [c for c in A.CHECKS if (A.MARK_RE.search(A.MARK.format(code=c)) or [0, 0])[1] != c], [])


def test_filed_once_verified_later():
    print("\nFiled once, then verified against the next read:")
    real = board.DB
    board.DB = _fresh_db()
    try:
        now = int(time.time())
        con = sqlite3.connect(board.DB)
        bare = page("/about.php", "<html><head><title>About</title></head><body><p>"
                                  + "word " * 200 + "</p></body></html>")
        found = [A.contract(f, now) for f in A.page_findings(bare)]
        A.file_findings(con, found, now)
        again = A.file_findings(con, found, now)
        check("a second run files nothing new", [iid for iid, _ in again if iid], [])
        n = con.execute("SELECT count(*) FROM site_issues WHERE kind='seo'").fetchone()[0]
        check("one ticket per finding", n, len(found))

        print("\n...the H1 is added, and the next clean read closes that ticket:")
        fixed = page("/about.php", "<html><head><title>About</title></head><body>"
                                   "<h1>About us</h1><p>" + "word " * 200 + "</p></body></html>")
        still = [A.contract(f, now) for f in A.page_findings(fixed)]
        out = A.verify(con, [fixed], still, now)
        h1 = con.execute("SELECT status, detail FROM site_issues WHERE detail LIKE "
                         "'[seo-audit:missing_h1]%'").fetchone()
        check("closed", h1[0], "closed")
        check("and says it was verified", "VERIFIED FIXED" in h1[1], True)
        check("the description ticket stays open — it is still missing",
              con.execute("SELECT status FROM site_issues WHERE detail LIKE "
                          "'[seo-audit:missing_description]%'").fetchone()[0], "open")

        print("\n...a page not read today closes nothing:")
        before = con.execute("SELECT count(*) FROM site_issues WHERE status='open'").fetchone()[0]
        A.verify(con, [], [], now)
        check("no change", con.execute("SELECT count(*) FROM site_issues WHERE "
                                       "status='open'").fetchone()[0], before)

        print("\n...a page that failed today cannot prove the absence is fixed:")
        A.verify(con, [page("/about.php", "", error="timed out")], [], now)
        check("still open", con.execute("SELECT status FROM site_issues WHERE detail LIKE "
                                        "'[seo-audit:missing_description]%'").fetchone()[0], "open")

        print("\n...and one marked deployed that is still there is reopened:")
        con.execute("UPDATE site_issues SET status='deployed' WHERE detail LIKE "
                    "'[seo-audit:missing_description]%'")
        A.verify(con, [fixed], still, now)
        row = con.execute("SELECT status, detail FROM site_issues WHERE detail LIKE "
                          "'[seo-audit:missing_description]%'").fetchone()
        check("reopened", row[0], "open")
        check("and says why", "STILL THERE" in row[1], True)
        con.close()
    finally:
        board.DB = real


def test_a_dropped_connection_is_not_a_failed_page():
    """
    The first full day run with this step: "Could not read any page" about a
    site IT had read fine a minute earlier, because a connection dropped for a
    few seconds. BeyondSEO's crawler retries; the first version of this did
    not. Two retries on a dropped connection or a 429/5xx, 1s then 2s.
    """
    print("\nA connection that drops, then answers, is a page read:")

    class _Resp:
        def __init__(self, code):
            self.status_code, self.text, self.url, self.history = code, doc(), SITE + "/", []
            self.headers = {"Content-Type": "text/html"}

    class _Session:
        def __init__(self, script):
            self.script, self.calls = list(script), 0

        def get(self, url, **kw):
            self.calls += 1
            step = self.script.pop(0)
            if isinstance(step, Exception):
                raise step
            return _Resp(step)

    slept = []
    s = _Session([ConnectionError("tunnel closed"), 200])
    p = A.capture(SITE + "/", session=s, sleep=slept.append)
    check("read on the second attempt", p.get("status"), 200)
    check("and not recorded as an error", p.get("error"), None)
    check("the attempts are on the evidence", p.get("attempts"), 2)
    check("with a short wait between", slept, [1])

    print("\n...a busy server gets the same patience:")
    s = _Session([503, 503, 200])
    slept = []
    check("503, 503, then 200 is a page read",
          A.capture(SITE + "/", session=s, sleep=slept.append).get("status"), 200)
    check("waiting 1s then 2s", slept, [1, 2])

    print("\n...but a page that is really gone is not retried into the ground:")
    s = _Session([404])
    p = A.capture(SITE + "/gone", session=s, sleep=lambda n: None)
    check("one attempt for a 404", s.calls, 1)
    check("and it is reported as the 404 it is", p.get("status"), 404)

    print("\n...and a connection that never comes back is, after three tries, an error:")
    s = _Session([ConnectionError("x")] * 3)
    p = A.capture(SITE + "/", session=s, sleep=lambda n: None)
    check("three attempts", s.calls, 3)
    check("then the error is recorded", "ConnectionError" in (p.get("error") or ""), True)


def test_it_reaches_the_right_recipe():
    """
    fix_canonical matches on the word "canonical" and never looks at the
    kind. An audit finding "no canonical declared" reaching it would find the
    hostnames fine and close the ticket as overtaken — a real finding binned
    by the wrong recipe.
    """
    print("\nAn audit ticket goes to the audit recipe, whatever words it contains:")
    for code in ("canonical_not_declared", "missing_title", "two_hosts_serve_the_site"):
        c = A.contract({"code": code, "url": SITE + "/", "affected_urls": [SITE + "/", "https://glass.ae/"],
                        "evidence": "https://www.glass.ae/ and https://glass.ae/ both 200",
                        "limits": []}, int(time.time()))
        issue = {"id": 1, "kind": "seo", "severity": "low", "url": c["url"],
                 "detail": A.ticket_text(c)}
        got = it_fix.plans_for(issue, {})
        check(f"{code} -> fix_seo_finding",
              got[0]["recipe"] if got else None, "fix_seo_finding")
    print("\nThe two-hosts ticket gives him the exact lines, both ways round:")
    steps = " ".join(it_fix.plans_for(issue, {})[0]["steps"])
    check("a 301 to the bare domain", "https://glass.ae/$1 [R=301,L]" in steps, True)
    check("and to www", "https://www.glass.ae/$1 [R=301,L]" in steps, True)
    check("and the backup warning", "Keep a copy of .htaccess" in steps, True)

    print("\n...while a Watcher ticket still reaches its own recipe:")
    got = it_fix.plans_for({"id": 2, "kind": "bug", "severity": "high", "url": SITE,
                            "detail": "No viewport meta tag"}, {"head_page": "includes/header.php"})
    check("viewport still goes to fix_viewport", got[0]["recipe"], "fix_viewport")


def test_it_is_in_the_day_and_takes_no_ones_job():
    from . import day_run
    import inspect
    print("\nIn the day, first in SEO's lane:")
    seo = [s for s in day_run.STEPS if s[3] == "seo"]
    check("the audit is a step", any(s[1] == "pipelines.seo_audit" for s in seo), True)
    check("and runs first in the lane", seo[0][1], "pipelines.seo_audit")
    check("it claims nothing off the board, so SEO's one job per run is untouched",
          "at_the_desk" in inspect.getsource(A) or "next_open_task" in inspect.getsource(A),
          False)


def main():
    for t in (test_absence_is_earned,
              test_the_per_page_checks,
              test_canonicals_are_judged_like_a_person_would,
              test_one_fact_is_one_ticket,
              test_every_finding_carries_the_contract,
              test_filed_once_verified_later,
              test_a_dropped_connection_is_not_a_failed_page,
              test_it_reaches_the_right_recipe,
              test_it_is_in_the_day_and_takes_no_ones_job):
        t()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        sys.exit(1)
    print("all SEO audit tests passed")


if __name__ == "__main__":
    main()
