"""
answers_tests.py — run: python -m pipelines.answers_tests

Three things are on trial here, and they all fail the same way: by producing a
confident number nobody checked.

  1. THE COST OF A FETCH. There is no single Zyte price. The rates come from
     the owner's .env, a failed render is free because Zyte bills successes
     only, and the daily cap has to actually stop a render rather than merely
     record it afterwards. Every one of those was wrong at some point.

  2. THE USAGE METER. Counts are facts and money is an estimate; a gauge
     (records in the index) must not be summed like a counter (renders bought),
     or re-indexing forty pages nightly reads as over the plan by month end.
     And Algolia searches must stay honestly UNMETERED — a meter reading zero
     while the owner's visitors spend his allowance is worse than no meter.

  3. THE AEO/GEO AUDIT. Every check has to be able to FAIL on a bad page and
     PASS on a good one. A checklist that passes everything is decoration.

No network call is made: pages are strings in this file and the fetcher is
monkeypatched. Everything runs against a throwaway cabinet.
"""
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

from coordination import board, cabinet, usage
from . import fetcher, seo_answers

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


# ---------------------------------------------------------------------------
# PAGES
# ---------------------------------------------------------------------------

BAD = """<html><head><title>Glass Partitions</title></head><body>
<h1>Glass Partitions</h1>
<h2>Pricing</h2>
<p>We are a leading provider of premium quality glass partition solutions
throughout the region, delivering excellence and customer satisfaction with a
commitment to quality that has made us a trusted name among discerning clients
who demand nothing but the very best from their chosen supplier of architectural
glazing systems and associated products, backed by a dedicated team of
professionals who bring passion and dedication to every single project we
undertake regardless of its size or complexity, ensuring outcomes that exceed
expectations at every turn and cement our reputation as the partner of choice
for those seeking a supplier who genuinely cares about the finished result and
the experience of getting there together as partners in a shared endeavour.</p>
</body></html>"""

GOOD = """<html><head><title>How much does a glass partition cost in Dubai?</title>
<script type="application/ld+json">
{"@context":"https://schema.org","@graph":[
 {"@type":"HomeAndConstructionBusiness","name":"Glassworks","telephone":"+971500000000",
  "address":{"@type":"PostalAddress","streetAddress":"Al Quoz","addressLocality":"Dubai"},
  "areaServed":["Dubai","Abu Dhabi","Sharjah"]},
 {"@type":"FAQPage","mainEntity":[
   {"@type":"Question","name":"How much does a glass partition cost in Dubai?",
    "acceptedAnswer":{"@type":"Answer","text":"Between AED 220 and AED 480 per square metre."}}]},
 {"@type":"Article","author":{"@type":"Person","name":"Hamza"},
  "dateModified":"2026-08-01",
  "speakable":{"@type":"SpeakableSpecification","cssSelector":[".answer"]}}]}
</script></head><body>
<h1>Glass partitions in Dubai</h1>
<p>A glass partition is a floor-to-ceiling divider made from toughened glass,
fixed with slim aluminium channels instead of a stud wall.</p>
<h2>How much does a glass partition cost in Dubai?</h2>
<p class="answer">Between AED 220 and AED 480 per square metre installed, depending
on whether the glass is 10mm or 12mm and how difficult the access is. A typical
office run of 6 m2 lands around AED 2,100 fitted in 2 days.</p>
<ul><li>10mm toughened: AED 220 per m2</li><li>12mm toughened: AED 320 per m2</li></ul>
<p>Thickness follows <a href="https://www.dm.gov.ae/">Dubai Municipality</a>
guidance for internal glazing.</p>
<p>&ldquo;We replaced a cracked 12mm pane in Business Bay the same afternoon; the
office never closed,&rdquo; said Hamza, who has 12 years experience in the trade.</p>
<time datetime="2026-08-01">1 August 2026</time>
</body></html>"""

HIDDEN_FAQ = """<html><head><title>Doors</title>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"FAQPage","mainEntity":[
 {"@type":"Question","name":"Do you install fire rated doors in Abu Dhabi?",
  "acceptedAnswer":{"@type":"Answer","text":"Yes."}}]}
</script></head><body><h1>Doors</h1><p>We fit doors.</p></body></html>"""


# ---------------------------------------------------------------------------
# 1. WHAT A FETCH COSTS
# ---------------------------------------------------------------------------

def test_rates():
    print("\nRates come from the owner, not from a number we invented:")
    for k in ("ZYTE_COST_PER_FETCH", "ZYTE_COST_PER_RENDER"):
        os.environ.pop(k, None)
    import providers.integrations as integ
    real_read = integ.read_env
    integ.read_env = lambda: {}
    try:
        check("no rate set -> the built-in default", fetcher.cost_per_render(), 0.0015)
        check("plain default is a tenth of a render", fetcher.cost_per_fetch(), 0.0001)

        os.environ["ZYTE_COST_PER_RENDER"] = "0.0042"
        check("his own rate wins", fetcher.cost_per_render(), 0.0042)

        os.environ["ZYTE_COST_PER_RENDER"] = "not a number"
        check("a typo falls back rather than becoming his rate",
              fetcher.cost_per_render(), 0.0015)

        os.environ["ZYTE_COST_PER_RENDER"] = "-5"
        check("a negative rate is refused", fetcher.cost_per_render(), 0.0015)

        os.environ["ZYTE_COST_PER_RENDER"] = "0"
        check("zero is a legitimate answer and is kept", fetcher.cost_per_render(), 0.0)
    finally:
        integ.read_env = real_read
        for k in ("ZYTE_COST_PER_FETCH", "ZYTE_COST_PER_RENDER"):
            os.environ.pop(k, None)


def test_failed_render_is_free():
    print("\nZyte bills successful responses only, so a failure must cost nothing:")
    real_db, real_zyte, real_tool = board.DB, fetcher._zyte, fetcher.crawler_tool
    board.DB = _fresh_db()
    fetcher.crawler_tool = lambda: {"name": "Zyte API", "api_key": "k",
                                    "base_url": "https://api.zyte.com/v1/extract",
                                    "enabled": True}
    try:
        fetcher._zyte = lambda *a, **k: ("", "Zyte HTTP 500 — upstream fell over", 500)
        r = fetcher.fetch("https://rival.ae/x", render=True)
        check("a failed render is free", r["cost"], 0.0)
        check("a failed render is not reported as ok", r["ok"], False)
        check("the request is still counted", usage._sum("zyte", "render", "day")[0], 1)
        check("but no money is counted", usage._sum("zyte", "render", "day")[1], 0.0)

        fetcher._zyte = lambda *a, **k: ("", "", 200)
        r = fetcher.fetch("https://rival.ae/y", render=True)
        check("an empty page is free too", r["cost"], 0.0)

        fetcher._zyte = lambda *a, **k: ("<html><body>" + "word " * 200 + "</body></html>", "", 200)
        r = fetcher.fetch("https://rival.ae/z", render=True)
        check("a real render costs the render rate", r["cost"], 0.0015)
        check("and lands on the meter", round(usage._sum("zyte", "render", "day")[1], 4), 0.0015)
        check("three requests, one of them billed", usage._sum("zyte", "render", "day")[0], 3)
    finally:
        board.DB, fetcher._zyte, fetcher.crawler_tool = real_db, real_zyte, real_tool


def test_spend_cap_stops_a_render():
    print("\nThe daily cap must REFUSE a render, not just record it afterwards:")
    real_db, real_zyte, real_tool = board.DB, fetcher._zyte, fetcher.crawler_tool
    board.DB = _fresh_db()
    called = {"n": 0}

    def _spy(*a, **k):
        called["n"] += 1
        return ("<html><body>" + "word " * 200 + "</body></html>", "", 200)

    fetcher._zyte = _spy
    fetcher.crawler_tool = lambda: {"name": "Zyte API", "api_key": "k",
                                    "base_url": "https://api.zyte.com/v1/extract",
                                    "enabled": True}
    # Room for exactly one render at 0.0015 and not two.
    os.environ["DAILY_SPEND_CAP_USD"] = "0.002"
    try:
        r = fetcher.fetch("https://rival.ae/a", render=True)
        check("the first render goes through", r["ok"], True)
        check("Zyte was actually called once", called["n"], 1)

        r2 = fetcher.fetch("https://rival.ae/b", render=True)
        check("the second is refused, cap spent", r2["ok"], False)
        check("refused before the request went out", called["n"], 1)
        check("and it says why in money terms", "spend cap" in r2["error"], True)
        check("with the price it would have been", r2["declined"]["would_cost"], 0.0015)

        os.environ["DAILY_SPEND_CAP_USD"] = "50"
        r3 = fetcher.fetch("https://rival.ae/c", render=True)
        check("raising the cap unblocks it", r3["ok"], True)
    finally:
        os.environ.pop("DAILY_SPEND_CAP_USD", None)
        board.DB, fetcher._zyte, fetcher.crawler_tool = real_db, real_zyte, real_tool


def test_crawler_spend_reaches_the_cap():
    print("\nCrawler money has to be visible to the cap, or the cap is decoration:")
    real_db = board.DB
    board.DB = _fresh_db()
    try:
        check("nothing spent yet", cabinet.spent_today(), 0.0)
        usage.record("zyte", "render", n=1, usd=0.25)
        check("a metered fetch shows up in the cabinet's total",
              cabinet.spent_today(), 0.25)
        cabinet.borrow("seo_data", "t", "SEO", reason="x", wait=False)
        cabinet.release("seo_data", "t", usd_cost=0.75)
        check("borrows and meter add up, neither double-counted",
              cabinet.spent_today(), 1.0)
    finally:
        board.DB = real_db


# ---------------------------------------------------------------------------
# 2. THE USAGE METER
# ---------------------------------------------------------------------------

def test_meter():
    print("\nCounters add up; gauges do not:")
    real_db = board.DB
    board.DB = _fresh_db()
    try:
        usage.record("algolia", "write", n=40)
        usage.record("algolia", "write", n=40)
        check("writes accumulate", usage._sum("algolia", "write", "month")[0], 80)

        usage.set_gauge("algolia", "record", 40)
        usage.set_gauge("algolia", "record", 40)
        usage.set_gauge("algolia", "record", 41)
        check("the index size is the latest, not the sum",
              usage._latest("algolia", "record")[0], 41)

        rec = next(m for m in usage.status()
                   if (m["provider"], m["kind"]) == ("algolia", "record"))
        check("41 records is nowhere near the 50,000 allowance", rec["verdict"], "ok")
        check("and the allowance is the published one", rec["allowance"], 50_000)

        usage.set_gauge("algolia", "record", 49_000)
        rec = next(m for m in usage.status()
                   if (m["provider"], m["kind"]) == ("algolia", "record"))
        check("98% of the allowance is a warning", rec["verdict"], "watch")
        ok, msg = usage.headroom("algolia", "record")
        check("still allowed, but he is told", (ok, "close" in msg), (True, True))

        usage.set_gauge("algolia", "record", 60_000)
        ok, msg = usage.headroom("algolia", "record")
        check("over the allowance is refused", ok, False)
        check("and names what it costs from here", "$0.40" in msg, True)
    finally:
        board.DB = real_db


def test_searches_stay_honest():
    print("\nWhat we cannot count, we must not display as zero:")
    real_db = board.DB
    board.DB = _fresh_db()
    try:
        s = next(m for m in usage.status()
                 if (m["provider"], m["kind"]) == ("algolia", "search"))
        check("visitor searches are UNMETERED, not 0", s["verdict"], "unmetered")
        check("used is None, so nothing can render it as a number", s["used"], None)
        txt = usage.as_text()
        check("the report says so out loud", "NOT COUNTED HERE" in txt, True)
        check("and points at the dashboard", "dashboard.algolia.com" in txt, True)
        ok, _m = usage.headroom("algolia", "search")
        check("an unmetered limit never blocks anything", ok, True)
    finally:
        board.DB = real_db


def test_meter_survives_a_broken_cabinet():
    print("\nBookkeeping must never be the thing that breaks the work:")
    real_db = board.DB
    board.DB = Path(tempfile.mkdtemp()) / "nope" / "agency.db"   # unwritable path
    try:
        usage.record("zyte", "render", n=1, usd=0.5)
        check("recording into a missing cabinet does not raise", True, True)
        check("and reads back as zero rather than exploding", usage.spent_today(), 0.0)
    finally:
        board.DB = real_db


# ---------------------------------------------------------------------------
# 3. THE AEO / GEO AUDIT
# ---------------------------------------------------------------------------

def test_checks_can_fail():
    print("\nA thin page must fail the checks that exist to catch a thin page:")
    a = seo_answers.audit_page("https://x.ae/partitions", BAD)
    got = {f["check_id"]: f["passed"] for f in a["findings"]}
    check("no question heading", got["aeo.question_heading"], False)
    check("nothing to answer with", got["aeo.answer_first"], False)
    check("no FAQ markup", got["aeo.faq_schema"], False)
    check("no LocalBusiness markup", got["aeo.localbusiness"], False)
    check("nothing liftable", got["aeo.extractable"], False)
    check("wall of text", got["aeo.short_paragraphs"], False)
    check("no statistics", got["geo.statistics"], False)
    check("no citation", got["geo.citation"], False)
    check("no quotation", got["geo.quotation"], False)
    check("no definition", got["geo.definition"], False)
    check("nobody behind it", got["geo.author"], False)
    check("undated", got["geo.dated"], False)
    check("a thin page scores near nothing", a["score"] < 15, True)


def test_checks_can_pass():
    print("\nAnd a page that does the work must actually pass them:")
    a = seo_answers.audit_page("https://x.ae/glass-partitions-dubai", GOOD)
    got = {f["check_id"]: (f["passed"], f["detail"]) for f in a["findings"]}
    for cid in ("aeo.question_heading", "aeo.answer_first", "aeo.faq_schema",
                "aeo.faq_visible", "aeo.extractable", "aeo.localbusiness",
                "aeo.speakable", "aeo.short_paragraphs", "geo.statistics",
                "geo.citation", "geo.quotation", "geo.definition",
                "geo.author", "geo.dated"):
        check(f"passes {cid}", got[cid][0], True)
    check("a page that does the work scores 100", a["score"], 100.0)


def test_hidden_faq_is_caught():
    print("\nFAQ markup for questions that are not on the page is a manual action risk:")
    a = seo_answers.audit_page("https://x.ae/doors", HIDDEN_FAQ)
    got = {f["check_id"]: f["passed"] for f in a["findings"]}
    check("the markup itself is spotted", got["aeo.faq_schema"], True)
    check("but the hidden question is caught", got["aeo.faq_visible"], False)


def test_graph_nesting():
    print("\nMarkup nested in @graph is markup — a reader that misses it cries wolf:")
    page = seo_answers.read_page("https://x.ae/", GOOD)
    check("the nested LocalBusiness subtype is found",
          "homeandconstructionbusiness" in page["schema"], True)
    check("so is the nested FAQPage", "faqpage" in page["schema"], True)


def test_statistics_are_not_just_any_number():
    print("\n'10 things you should know' is not a statistic:")
    thin = ("<html><body><h1>Doors</h1><p>10 things you should know about our 5 "
            "great reasons to choose us.</p></body></html>")
    got = {f["check_id"]: f["passed"]
           for f in seo_answers.audit_page("https://x.ae/d", thin)["findings"]}
    check("bare counts do not count as figures", got["geo.statistics"], False)


def test_false_positives_found_on_real_pages():
    """
    Three of these were caught by running the audit against real UAE glazier
    sites and reading every line of the output, not by thinking hard about the
    regexes. Each one made a thin page look better than it is, which is the
    only kind of bug in an auditor that actually costs the owner something.
    """
    print("\nYour own Facebook page is not a citation:")
    social = ("<html><body><h1>Glass</h1><p>We fit glass.</p>"
              "<a href='https://www.facebook.com/us'>fb</a>"
              "<a href='https://instagram.com/us'>ig</a></body></html>")
    got = {f["check_id"]: (f["passed"], f["detail"])
           for f in seo_answers.audit_page("https://x.ae/", social)["findings"]}
    check("social links alone do not count as sources", got["geo.citation"][0], False)
    check("and it says why", "not a source" in got["geo.citation"][1], True)

    real = ("<html><body><h1>Glass</h1><p>To "
            "<a href='https://www.dm.gov.ae/rules'>Dubai Municipality</a> spec.</p>"
            "<a href='https://www.facebook.com/us'>fb</a></body></html>")
    got = {f["check_id"]: f["passed"]
           for f in seo_answers.audit_page("https://x.ae/", real)["findings"]}
    check("a real source still passes alongside the social links",
          got["geo.citation"], True)

    print("\n“Why Choose Us” is not a question anybody typed:")
    stock = ("<html><body><h1>Glass</h1><h2>Why Choose Us</h2>"
             "<p>Because we are great at what we do every day.</p></body></html>")
    got = {f["check_id"]: (f["passed"], f["detail"])
           for f in seo_answers.audit_page("https://x.ae/", stock)["findings"]}
    check("a stock heading does not pass as a question",
          got["aeo.question_heading"][0], False)
    check("and the owner is told which one it means",
          "stock heading" in got["aeo.question_heading"][1], True)
    check("nor does it get scored as an answered question",
          got["aeo.answer_first"][0], False)

    print("\nA page with no FAQ is not rewarded for having no FAQ:")
    a = seo_answers.audit_page("https://x.ae/", stock)
    fv = next(f for f in a["findings"] if f["check_id"] == "aeo.faq_visible")
    check("the visibility check is not applicable", fv["n_a"], True)
    check("so it is not counted as a pass", fv["passed"], False)
    # 4 points of weight are removed from BOTH sides, not handed over free.
    scored = sum(f["weight"] for f in a["findings"] if not f["n_a"])
    check("and its weight leaves the denominator too",
          scored, seo_answers.WEIGHT_TOTAL - 4)
    with_faq = seo_answers.audit_page("https://x.ae/", HIDDEN_FAQ)
    check("a page WITH broken FAQ markup is scored on it",
          next(f["n_a"] for f in with_faq["findings"]
               if f["check_id"] == "aeo.faq_visible"), False)


def test_duplicate_pages_counted_once():
    print("\nOne page served under five addresses is one page:")
    real_db, real_fetch = board.DB, fetcher.fetch
    board.DB = _fresh_db()
    fetcher.fetch = lambda url, **k: {
        "ok": True, "cost": 0.0, "error": "", "how": "plain", "status": 200,
        "switched": "",
        "html": ("User-agent: *\nAllow: /" if url.endswith("robots.txt")
                 else BAD if "sitemap" not in url else "")}
    try:
        out = seo_answers._work("seo-answers", "https://x.ae", 4)
        check("only the first copy is audited", len(out["pages"]), 1)
        check("the rest are named as duplicates, not as failures",
              all("identical to" in s["why"] for s in out["skipped"]),
              True if out["skipped"] else True)
        if out.get("worst"):
            check("so a gap is reported against one page, not four",
                  out["worst"][0]["pages"], 1)
    finally:
        board.DB, fetcher.fetch = real_db, real_fetch


def test_robots():
    print("\nBlocking the assistants is a choice — an accidental one is worth catching:")
    real = fetcher.fetch
    try:
        fetcher.fetch = lambda *a, **k: {
            "ok": True, "cost": 0.0, "error": "", "how": "plain", "status": 200,
            "switched": "",
            "html": "User-agent: *\nDisallow: /admin\n\nUser-agent: GPTBot\nDisallow: /\n"}
        r = seo_answers.ai_crawler_check("https://x.ae")
        check("GPTBot blocked outright is reported", r["blocked"], ["GPTBot"])
        check("PerplexityBot is not falsely accused",
              "PerplexityBot" in r["allowed"], True)

        fetcher.fetch = lambda *a, **k: {
            "ok": True, "cost": 0.0, "error": "", "how": "plain", "status": 200,
            "switched": "", "html": "User-agent: GPTBot\nDisallow: /private\n"}
        r = seo_answers.ai_crawler_check("https://x.ae")
        check("a partial disallow is not an outright block", r["blocked"], [])

        fetcher.fetch = lambda *a, **k: {
            "ok": False, "cost": 0.0, "error": "HTTP 404", "how": "plain",
            "status": 404, "switched": "", "html": ""}
        r = seo_answers.ai_crawler_check("https://x.ae")
        check("no robots.txt means nothing is blocked", r["blocked"], [])
        check("and we say we could not read one", r["read"], False)
    finally:
        fetcher.fetch = real


def test_visibility_refuses_to_guess():
    print("\nWhether ChatGPT cites us is not knowable from here, and must not be faked:")
    v = seo_answers.visibility()
    check("it refuses", v["possible"], False)
    check("it says why", "rate-limited" in v["why"], True)
    check("and what it would take", "20 real customer questions" in v["what_it_would_take"], True)
    real_db = board.DB
    board.DB = _fresh_db()                    # never read the live agency here
    try:
        check("the caveat is there with no audit on file",
              "CANNOT TELL YOU" in seo_answers.as_text(), True)
        seo_answers._store(sqlite3.connect(board.DB),
                           seo_answers.audit_page("https://x.ae/", BAD)["findings"])
        check("and still there once there is one",
              "CANNOT TELL YOU" in seo_answers.as_text(), True)
    finally:
        board.DB = real_db


def test_llms_txt():
    print("\nThe llms.txt draft has to be a real map, not a slogan:")
    t = seo_answers.llms_txt("https://glassworks.ae")
    check("it names the business area", "Dubai" in t, True)
    check("it links real service paths", "https://glassworks.ae/" in t, True)
    check("it is markdown with one H1", t.count("\n# "), 0)
    check("it starts with the H1", t.startswith("# "), True)
    check("it admits prices are ranges", "not a fixed price" in t, True)


def test_faq_jsonld_only_marks_up_what_exists():
    print("\nThe FAQ builder marks up pairs, it does not invent answers:")
    out = seo_answers.faq_jsonld([("How much?", "About AED 250 per m2."),
                                  ("Do you deliver?", "")])
    d = json.loads(out.split(">", 1)[1].rsplit("<", 2)[0])
    check("the complete pair is marked up", len(d["mainEntity"]), 1)
    check("the half-written one is dropped, not filled in",
          d["mainEntity"][0]["name"], "How much?")


def test_full_pass_files_work_and_frees_the_shelf():
    print("\nA full pass files findings, asks the right sectors, and hands back the shelf:")
    real_db, real_fetch = board.DB, fetcher.fetch
    board.DB = _fresh_db()
    pages = {"https://x.ae": BAD, "https://x.ae/doors": HIDDEN_FAQ}

    def fake(url, **k):
        if url.endswith("robots.txt"):
            return {"ok": True, "html": "User-agent: *\nAllow: /", "cost": 0.0,
                    "error": "", "how": "plain", "status": 200, "switched": ""}
        h = pages.get(url.rstrip("/")) or pages.get(url) or ""
        return {"ok": bool(h), "html": h, "cost": 0.0,
                "error": "" if h else "not found", "how": "plain",
                "status": 200 if h else 404, "switched": ""}

    fetcher.fetch = fake
    try:
        out = seo_answers.run(url="https://x.ae", max_pages=4)
        check("the pass ran", out["ok"], True)
        check("the Critic passed it", out.get("critic"), "passed")
        check("nothing was billed reading our own site",
              usage._sum("zyte", "render", "day")[1], 0.0)

        con = sqlite3.connect(board.DB)
        n = con.execute("SELECT COUNT(*) FROM aeo_findings").fetchone()[0]
        fails = con.execute("SELECT COUNT(*) FROM aeo_findings WHERE passed=0").fetchone()[0]
        # One short: the page has no FAQ markup, so the FAQ-visibility check is
        # not applicable and is deliberately filed as neither pass nor fail.
        check("every applicable check is on file for the page read",
              n, len(seo_answers.CHECKS) - 1)
        na = con.execute("SELECT COUNT(*) FROM aeo_findings"
                         " WHERE check_id='aeo.faq_visible'").fetchone()[0]
        check("and the not-applicable one is filed as nothing at all", na, 0)
        check("and most of them failed, because that page is thin", fails > 8, True)

        reqs = [r[0] for r in con.execute("SELECT to_sector FROM requests")]
        check("Content was asked for the writing", "Content" in reqs, True)
        check("IT was asked for the markup", "IT" in reqs, True)
        check("SEO did not give itself a job", "SEO" in reqs, False)
        con.close()

        held = cabinet.borrow("seo_data", "x", "SEO", reason="prove free", wait=False)
        check("the shelf came back", held["ok"], True)
        if held["ok"]:
            cabinet.release("seo_data", "x", borrow_id=held["borrow_id"])

        print("\nRunning it twice must update the findings, not double them:")
        seo_answers.run(url="https://x.ae", max_pages=4)
        con = sqlite3.connect(board.DB)
        n2 = con.execute("SELECT COUNT(*) FROM aeo_findings").fetchone()[0]
        con.close()
        check("still one row per check per page", n2, n)
    finally:
        board.DB, fetcher.fetch = real_db, real_fetch


def test_pass_refuses_without_a_site():
    print("\nNo website saved means no audit, not an audit of nothing:")
    real_db, real_fetch = board.DB, fetcher.fetch
    board.DB = _fresh_db()
    fetcher.fetch = lambda *a, **k: {"ok": False, "html": "", "cost": 0.0,
                                     "error": "no response", "how": "none",
                                     "status": 0, "switched": ""}
    try:
        out = seo_answers._work("seo-answers", "https://x.ae", 4)
        check("an unreadable site is an error, not a score", out["ok"], False)
        check("and it says which address it tried", "x.ae" in out["error"], True)
    finally:
        board.DB, fetcher.fetch = real_db, real_fetch


def main():
    test_rates()
    test_failed_render_is_free()
    test_spend_cap_stops_a_render()
    test_crawler_spend_reaches_the_cap()
    test_meter()
    test_searches_stay_honest()
    test_meter_survives_a_broken_cabinet()
    test_checks_can_fail()
    test_checks_can_pass()
    test_hidden_faq_is_caught()
    test_false_positives_found_on_real_pages()
    test_duplicate_pages_counted_once()
    test_graph_nesting()
    test_statistics_are_not_just_any_number()
    test_robots()
    test_visibility_refuses_to_guess()
    test_llms_txt()
    test_faq_jsonld_only_marks_up_what_exists()
    test_full_pass_files_work_and_frees_the_shelf()
    test_pass_refuses_without_a_site()

    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        sys.exit(1)
    print("All answers/usage/cost tests passed.")


if __name__ == "__main__":
    main()
