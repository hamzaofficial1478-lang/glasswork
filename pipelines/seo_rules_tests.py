"""
seo_rules_tests.py — run: python -m pipelines.seo_rules_tests

The SEO rulebook (pipelines/seo_rules.py): every check weighed, one score.
What is protected (pages written here; nothing reaches a network):
  - the arithmetic is SEOmator's: pass 100, warn 50, fail 0, unmeasured left
    out; categories by rule weight; overall by category weight (adding to 100);
  - a home page Google cannot use caps the score, so it never looks healthy;
  - worst first: rule weight x category weight x how bad x share of pages;
  - the reader is careful: an SVG's title is not the page title, a canonical in
    the body is caught, script text is not counted as words;
  - a clean page passes the basics, and each rule says what it found;
  - site-wide checks: repeated titles, near-identical pages, pages competing
    for the same words, broken links, orphans;
  - every rule has a name, a reason and either a fix or what a person does.
"""
import sys

from . import seo_rules as R

_fail = []
H = "https://www.example-trade.test"


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


GOOD_HEAD = ('<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
             '<title>Frameless shower screens made to measure | Example Glass</title>'
             '<meta name="description" content="Frameless shower screens measured, made and fitted by our own team. '
             'Toughened glass, clean seals, and a fitter who turns up when he says he will.">'
             '<link rel="canonical" href="{url}"><link rel="icon" href="/favicon.png">'
             '<meta property="og:title" content="Shower screens"><meta property="og:description" content="Made to measure">'
             '<meta property="og:image" content="https://www.example-trade.test/a.jpg"><meta property="og:url" content="{url}">'
             '<meta name="twitter:card" content="summary_large_image">'
             '<script type="application/ld+json">{{"@context":"https://schema.org","@type":"HomeAndConstructionBusiness",'
             '"name":"Example Glass","telephone":"+971 4 000 0000","address":{{"@type":"PostalAddress","streetAddress":"Office 3"}}}}'
             '</script>')
WORDS = " ".join([
    "Toughened glass is measured on site and cut to the millimetre.", "Every panel is checked before it leaves.",
    "Fitters arrive with silicone, clamps and spare seals.", "A typical bathroom takes one morning to finish.",
    "Hinges come in chrome, brushed nickel or matt black.", "Customers often ask about cleaning and limescale.",
    "We suggest a quick squeegee after each shower.", "Frameless screens suit small rooms because light passes through.",
    "Larger walk-in designs need a fixed return panel.", "Sloping ceilings are handled with a template first.",
    "Quotes list the glass thickness, finish and hardware.", "Payment is taken only after the job is signed off.",
    "Old enclosures are removed and recycled responsibly.", "Tiles are protected with sheets throughout the visit.",
    "Questions about warranty are answered in writing.", "Some apartments require building management approval.",
    "Delivery windows are agreed by phone the day before.", "Clear, frosted and tinted options are all available.",
    "Anti-limescale coating can be applied at the factory.", "Photographs of finished jobs are on our projects page.",
] * 3)


def page(url=H + "/", head=None, body=None):
    head = GOOD_HEAD.format(url=url) if head is None else head
    body = body if body is not None else (
        '<header><nav><a href="/about.php">About our workshop</a> <a href="/contact.php">Contact us</a> '
        '<a href="/shower-screens.php">Shower screens</a></nav></header>'
        f'<main><h1>Shower screens fitted properly</h1><h2>How it works</h2><p>{WORDS}</p>'
        '<img src="/shower-screen-marina.webp" alt="A frameless shower screen in a Marina flat" width="800" height="600">'
        '</main><footer>© 2026 Example Glass</footer>')
    return R.parse(f'<!doctype html><html lang="en-GB"><head>{head}</head><body>{body}</body></html>', url,
                   headers={"Content-Encoding": "gzip", "Cache-Control": "max-age=600",
                            "Strict-Transport-Security": "max-age=31536000", "X-Content-Type-Options": "nosniff"},
                   ms=320)


def test_a_clean_page():
    print("\nA well-made page passes the basics:")
    res = R.check_page(page())
    bad = {k: v for k, v in res.items() if v[0] in (R.FAIL, R.WARN)}
    check("nothing fails or warns on it", bad, {})
    out = R.check_site([page()])
    check("it scores an A", (out["grade"], out["score"] >= 90, out["capped_by"]), ("A", True, ""))


def test_the_reader():
    print("\nThe reader is careful:")
    p = page(head='<title>Real title here for the page</title>',
             body='<svg><title>an icon</title></svg><script>var words = "one two three four five";</script>'
                  '<h1>Hi</h1><link rel="canonical" href="https://x.test/">')
    check("an SVG's title is not the page's title", p["titles"], ["Real title here for the page"])
    check("script text is not counted as words", p["words"], 1)
    res = R.check_page(p)
    check("a canonical in the body is caught", res["core-canonical-in-head"][0], R.FAIL)


def test_rules_find_things():
    print("\nEach rule says what it found:")
    body = ('<h1>One</h1><h1>Two</h1><h4>Skipped</h4><img src="/IMG_4031.jpg"><img src="/b.jpg" alt="image">'
            '<a href="javascript:void(0)">x</a><a href="#" onclick="go()">menu</a><a href="/x.php">click here</a>'
            '<a href="tel:abc">call</a><a target="_blank" href="https://other.test/">them</a>'
            '<p>lorem ipsum dolor sit amet Ã© © 2019</p>')
    p = page(url=H + "/Our_Services.php", head='<meta name="viewport" content="width=1024">'
             '<script src="/a.js"></script><script src="/b.js"></script><script src="/c.js"></script>', body=body)
    r = R.check_page(p)
    for rid, want in (("core-title-present", R.FAIL), ("core-description-present", R.FAIL),
                      ("core-canonical-present", R.FAIL), ("core-h1-single", R.WARN), ("content-heading-order", R.WARN),
                      ("images-alt", R.FAIL), ("images-alt-quality", R.WARN), ("images-filenames", R.WARN),
                      ("links-invalid", R.WARN), ("links-onclick", R.FAIL), ("links-anchor-text", R.WARN),
                      ("links-phone-email", R.FAIL), ("links-new-tab-safe", R.WARN), ("content-placeholder", R.FAIL),
                      ("content-mojibake", R.FAIL), ("content-old-copyright", R.WARN), ("mobile-viewport-width", R.FAIL),
                      ("speed-blocking-scripts", R.FAIL), ("url-tidy", R.WARN), ("i18n-lang", R.PASS),
                      ("content-thin", R.FAIL)):
        check(f"  {rid}", r[rid][0], want)
    check("and says what it saw", r["images-alt"][1], "1 of 2 without a description")


def test_scoring():
    print("\nThe arithmetic:")
    rows = {"core-title-present": [("a", R.PASS, ""), ("b", R.FAIL, "")],      # weight 25
            "core-viewport": [("a", R.PASS, ""), ("b", R.PASS, "")],           # weight 20
            "core-h1-single": [("a", R.NA, ""), ("b", R.NA, "")]}              # not measured: left out
    out = R.score(rows, [page()])
    core = next(c for c in out["categories"] if c["id"] == "core")
    check("category = rules averaged by weight; unmeasured left out", core["score"],
          round((100 * 25 + 0 * 25 + 100 * 20 * 2) / (100 * 25 * 2 + 100 * 20 * 2) * 100))
    check("overall over the categories measured", out["score"], core["score"])
    check("the title issue is on half the pages", out["issues"][0]["pages"], ["b"])
    check("priority = weight x category weight x how bad x share", out["issues"][0]["priority"], 25 * 16 * 1.0 * 0.5)
    check("grades", [R.grade(x) for x in (95, 85, 75, 55, 10)], ["A", "B", "C", "D", "F"])


def test_caps():
    print("\nA home page Google cannot use caps the score:")
    hidden = page(head=GOOD_HEAD.format(url=H + "/") + '<meta name="robots" content="noindex">')
    out = R.check_site([hidden])
    check("noindex on the home page: never above 20", (out["score"] <= 20, "not to list" in out["capped_by"]), (True, True))
    bare = page(head='<meta name="viewport" content="width=device-width">', body="<p>hi</p>")
    out = R.check_site([bare])
    check("no title and no words: never above 30", out["score"] <= 30, True)


def test_site_wide():
    print("\nAcross the whole site:")
    a = page(H + "/a.php")
    b = page(H + "/b.php")                                   # same title, same text as a
    out = R.check_site([page(), a, b], {"link_status": {H + "/gone.php": 404, H + "/a.php": 200},
                                        "sitemap_urls": [H + "/", H + "/lonely.php"]})
    ids = {i["id"]: i for i in out["issues"]}
    check("repeated titles are named", "core-title-unique" in ids, True)
    check("near-identical pages are named", "content-duplicate-pages" in ids, True)
    check("pages aiming at the same words are named", "content-cannibal" in ids, True)
    check("a broken link target is named", ids.get("links-broken", {}).get("pages"), [H + "/gone.php"])
    check("a page only the sitemap knows is named", ids.get("links-orphans", {}).get("pages"), [H + "/lonely.php"])
    out = R.check_site([page()], {"files_checked": {"robots_missing": (R.FAIL, "no robots.txt")}})
    check("the blueprint's file checks count in the same score",
          ("robots_missing" in {i["id"] for i in out["issues"]}, out["score"] < 100), (True, True))


def test_from_seomachine():
    print("\nTwo checks taken from seomachine:")
    from . import scrub
    sneaky = page(body="<main><h1>Shower screens</h1><p>Frameless\u200b shower\u2060 screens " + WORDS + "</p></main>")
    check("hidden characters in the words are found", R.check_page(sneaky)["content-invisible"], (R.WARN, "2 hidden character(s)"))
    check("  and the scrubber takes them out, leaving the words",
          scrub.scrub("Frameless\u200b shower\u2060 screens\u00a0here"), "Frameless shower screens here")
    check("  but not the joiners Arabic and emoji need", scrub.scrub("\u0644\u200d\u0627"), "\u0644\u200d\u0627")
    a = page(H + "/", body='<main><h1>Glass work in Dubai</h1><p>We fit frameless shower screens and glass '
                           'partitions. ' + WORDS + '</p><a href="/glass-partitions.php">Partitions</a></main>')
    b = page(H + "/frameless-shower-screens.php", body="<main><h1>Frameless shower screens</h1><p>" + WORDS + "</p></main>")
    c = page(H + "/glass-partitions.php", body="<main><h1>Glass partitions</h1><p>Our frameless shower screens use "
                                               "the same glass. " + WORDS + "</p></main>")
    out = R.check_site([a, b, c])
    ids = {i["id"]: i for i in out["issues"]}
    got = ids.get("links-internal-opportunities", {})
    check("a page naming another page's subject without a link is found", sorted(got.get("pages") or []),
          sorted([H + "/", H + "/glass-partitions.php"]))
    check("  and says which link to add", "frameless shower screens” → /frameless-shower-screens.php" in got.get("detail", ""),
          True)
    check("  a mention that already links is left alone", "/glass-partitions.php mentions “glass partitions”" in
          got.get("detail", "") or "/ mentions “glass partitions”" in got.get("detail", ""), False)


def test_every_rule_is_explained():
    print("\nEvery rule can be read and acted on:")
    weak = [r["id"] for r in R.ALL_RULES if not r["name"] or (not r.get("external") and not r["why"])]
    check("a name and a reason for every rule", weak, [])
    check("the categories add up to 100", sum(w for _c, _n, w in R.CATEGORIES), 100)
    check("every rule is in a known category", [r["id"] for r in R.ALL_RULES if r["cat"] not in R.CAT_WEIGHT], [])
    check("ids are unique", len(R.BY_ID), len(R.ALL_RULES))
    check("at least seventy checks", len(R.ALL_RULES) >= 70, True)


def main():
    for t in (test_a_clean_page, test_the_reader, test_rules_find_things, test_scoring, test_caps, test_site_wide,
              test_from_seomachine, test_every_rule_is_explained):
        t()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        return 1
    print("seo rules: every check weighed, one honest score.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
