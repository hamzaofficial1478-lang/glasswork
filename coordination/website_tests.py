"""
website_tests.py — run: python -m coordination.website_tests

"i want you to build me website with name of TechDuo and there our this first
 product will be listed and all about the details how it works and for what is
 was build the privacy all the stuff ... when do click on botix it will
 redirect to botix setup and usage there will be all theh details of botix
 pricing and how to use stuff and i want to see the animation"

The TechDuo website (website/) is plain files he uploads to his hosting. What
is protected here:
  - the four pages are there, each a whole page with its own title;
  - every link and picture on them points at something that exists, including
    the #places inside a page, so nobody clicks into nothing;
  - Botix on the home page leads to the Botix page, and that page has setup,
    usage, privacy, pricing and questions;
  - it loads nothing from elsewhere but Google Fonts and one pinned copy of
    three.js, and it carries no trackers, no cookies, no analytics;
  - the opening animation and the BT logo are the program's own files, byte
    for byte, so the website and the program can't drift apart;
  - the privacy promise is word for word the one on the sign-up page, and
    nothing on the site promises more than the program does;
  - the Starter plan says what he set: 7 leads and 5 emails a day, 1 post of
    300–400 words;

"the prices will be in $ starter will be 10$ with our key it will be 30$ and
 Growth will be 30 to 35 but with our api it will be 60$ and the pro will be
 95$ with our api it will be 120$ and prices will be automatically be
 converted according to the location"

  - the prices are his, in US dollars, and the page says people are billed in
    US dollars;
  - the visitor's currency is worked out on their own device (time zone, then
    language), and today's rates come from the public rates file only, with
    no cookies and no address of the page sent along; the privacy page says so;

"there is nothing 3d that i can see no any animations ... where is the
 animation of the program starting?"

  - the opening can be played again and watched full screen, and with reduced
    motion it waits behind a Play button instead of simply not moving;
  - the 3D objects can be dragged round, and stop drawing off the screen;
  - all seven sectors and 21 desks are on the Botix page;
  - the website never ships inside a copy of the program.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "website"
PAGES = ("index.html", "botix.html", "privacy.html", "terms.html")
THREE = "https://cdnjs.cloudflare.com/ajax/libs/three.js/r128/three.min.js"
_fail = []


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


def read(rel):
    return (SITE / rel).read_text(encoding="utf-8")


def links(html):
    return re.findall(r'\b(?:href|src)="([^"]+)"', html)


def ids(html):
    return set(re.findall(r'\bid="([^"]+)"', html))


def test_the_pages():
    print("\nThe four pages, each a whole page:")
    for p in PAGES:
        check(f"{p} is there", (SITE / p).is_file(), True)
    for p in PAGES:
        h = read(p)
        whole = (h.lstrip().lower().startswith("<!doctype html>") and '<html lang="en">' in h
                 and '<meta charset="UTF-8">' in h and 'name="viewport"' in h)
        check(f"{p} is a whole page (doctype, language, characters, phone width)", whole, True)
        t = re.search(r"<title>([^<]+)</title>", h)
        check(f"{p} has a short title", bool(t) and 1 <= len(t.group(1).split()) <= 4, True)
    css = read("assets/site.css")
    check("the background is painted, not borrowed", "body{margin:0;background:var(--paper)" in css, True)
    check("reduced motion is respected", "prefers-reduced-motion:reduce" in css, True)


def test_every_link_lands():
    print("\nEvery link and picture points at something that exists:")
    for p in PAGES:
        h = read(p)
        broken = []
        for url in links(h):
            if url.startswith(("https://", "mailto:")):
                continue
            path, _, frag = url.partition("#")
            target = (SITE / path) if path else (SITE / p)
            if not target.is_file():
                broken.append(url)
            elif frag and target.suffix == ".html" and frag not in ids(target.read_text(encoding="utf-8")):
                broken.append(url)
        check(f"{p}: nothing points at nothing", broken, [])
    home, botix = read("index.html"), read("botix.html")
    check("Botix on the home page opens the Botix page", 'href="botix.html"' in home, True)
    for part in ("setup", "using", "privacy", "pricing", "faq", "day", "what", "opening"):
        check(f"the Botix page has its #{part} section", f'id="{part}"' in botix, True)


def test_nothing_from_elsewhere():
    print("\nNothing from elsewhere, nothing that follows people:")
    for p in PAGES:
        h = read(p)
        outside = sorted({u for u in links(h) if u.startswith("http")
                          and not u.startswith(("https://fonts.googleapis.com", "https://fonts.gstatic.com"))
                          and u != THREE})
        check(f"{p}: only Google Fonts and the pinned three.js", outside, [])
        scripts = re.findall(r'<script[^>]*\bsrc="([^"]+)"', h)
        check(f"{p}: every script is ours or the pinned three.js",
              [s for s in scripts if not (s.startswith("assets/") or s == THREE)], [])
    everything = "\n".join(f.read_text(encoding="utf-8") for f in SITE.rglob("*") if f.suffix in (".html", ".js", ".css"))
    for word in ("google-analytics", "googletagmanager", "gtag(", "fbq(", "hotjar", "document.cookie",
                 "localStorage", "navigator.sendBeacon"):
        check(f"no {word}", word in everything, False)
    check("no form posts anything anywhere", "<form" in everything, False)


def test_the_programs_own_files():
    print("\nThe animation and the logo are the program's own files:")
    check("the opening animation is the program's, byte for byte",
          (SITE / "assets" / "opening.js").read_bytes() == (ROOT / "ui" / "opening.js").read_bytes(), True)
    check("the BT logo is the program's, byte for byte",
          (SITE / "assets" / "botix-logo.svg").read_bytes() == (ROOT / "ui" / "logo.svg").read_bytes(), True)
    botix = read("botix.html")
    check("the Botix page plays the opening inside its frame",
          "data-opening" in botix and '<script src="assets/opening.js" data-embed-only></script>' in botix, True)
    check("and has a button to play it again", "data-replay" in botix, True)
    check("both product pages stand the logo up in 3D", 'data-scene="botix"' in botix
          and 'data-scene="techduo"' in read("index.html"), True)


def test_privacy_words():
    print("\nThe privacy promise, and nothing more than the program keeps:")
    signup = (ROOT / "ui" / "account.html").read_text(encoding="utf-8")
    m = re.search(r"Keep your password and recovery key safe and private\.[^<]*", signup)
    promise = m.group(0) if m else ""
    check("the sign-up page still makes the promise", bool(promise), True)
    check("the Botix page makes it word for word", promise in read("botix.html"), True)
    for p in PAGES:
        low = re.sub(r"\s+", " ", read(p).lower())
        claims = [c for c in ("end to end", "end-to-end", "we don't store", "we do not store", "never stored",
                              "zero-knowledge", "100% secure", "unhackable") if c in low]
        check(f"{p}: no promise the program can't keep", claims, [])
    priv = read("privacy.html")
    check("the privacy page says the server counts sign-ins (a number, never who)", "counts how many people sign in each day" in priv, True)
    check("and that Google Fonts and cdnjs see a visitor's address", "Google Fonts" in priv and "cdnjs" in priv, True)
    check("legal pages say they are drafts", all('class="draft"' in read(p) for p in ("privacy.html", "terms.html")), True)


def test_plans():
    print("\nThe plans say what he set, in US dollars:")
    botix = read("botix.html")
    plans = re.findall(r'<article class="plan[^"]*"[^>]*>(.*?)</article>', botix, re.S)
    check("three plans", len(plans), 3)
    start = plans[0] if plans else ""
    check("Starter: 1 website", "<h3" in start and "Starter" in start and "1 website" in start, True)
    check("Starter: 7 leads and 5 emails a day", "7 new leads and 5 emails" in start, True)
    check("Starter: 1 post a day of 300–400 words", "1 post a day, 300–400 words" in start, True)
    check("every plan starts with the 14-day trial", all("Start your 14-day trial" in p and "data-trial" in p for p in plans), True)
    usd = [re.search(r'data-usd-own="(\d+)" data-usd-ai="(\d+)"', p) for p in plans]
    check("his prices: Starter $10 / $30, Growth $35 / $60, Pro $95 / $120 (own key / Botix AI)",
          [m.groups() if m else None for m in usd], [("10", "30"), ("35", "60"), ("95", "120")])
    check("before any script runs, the prices read in dollars", all(re.search(r'>\$\d+</b>', p) for p in plans), True)
    check("the page says people are billed in US dollars", "that is what you are billed in" in botix, True)
    check("anyone can choose another currency", "data-cur-select" in botix and '<script src="assets/currency.js"></script>' in botix, True)


def test_currency():
    print("\nPrices in the visitor's own money, worked out on their device:")
    js = read("assets/currency.js")
    fetched = sorted(set(re.findall(r"'(https://[^']+)'", js)))
    check("rates come only from the public rates file (and its second address)", fetched,
          ["https://cdn.jsdelivr.net/npm/@fawazahmed0/currency-api@latest/v1/currencies/usd.min.json",
           "https://latest.currency-api.pages.dev/v1/currencies/usd.min.json"])
    check("the rates request carries no cookies and no page address", "credentials: 'omit'" in js and "referrerPolicy: 'no-referrer'" in js, True)
    check("the country comes from the device's time zone, then its language", "resolvedOptions().timeZone" in js and "navigator.languages" in js, True)
    check("Pakistan's time zone means rupees", '"PK":"PKR"' in js and "Asia/Karachi" in js, True)
    check("Saudi Arabia's means riyals", '"SA":"SAR"' in js and "Asia/Riyadh" in js, True)
    check("the UAE's means dirhams", '"AE":"AED"' in js and "Asia/Dubai" in js, True)
    check("if the rates can't be fetched, saved rates and their date are used", "var SAVED = { date: '" in js, True)
    check("nothing is remembered about the visitor", "localStorage" in js or "document.cookie" in js, False)
    priv = read("privacy.html")
    check("the privacy page explains the exchange rates", "exchange rates" in priv and "jsDelivr" in priv, True)


def test_motion():
    print("\nThe 3D and the opening are there to be seen:")
    botix, js, scene = read("botix.html"), read("assets/site.js"), read("assets/scene.js")
    check("the opening has Play again and Full screen", "data-replay" in botix and "data-full" in botix, True)
    check("with reduced motion the opening waits behind a Play button", "data-play" in botix and "cover.hidden = false" in js, True)
    check("the opening can play over the whole page", "BotixOpening.full" in js and "full: function" in read("assets/opening.js"), True)
    check("the 3D objects can be dragged round", "pointerdown" in scene and "pointermove" in scene, True)
    check("they stop drawing off the screen", "IntersectionObserver" in scene, True)
    check("and still show a picture where there is no WebGL", botix.count('class="still"') >= 1 and "webgl()" in scene, True)


def test_sectors_and_desks():
    print("\nAll seven sectors and 21 desks:")
    botix = read("botix.html")
    check("seven sectors described", botix.count('<div class="sector">'), 7)
    check("21 desks named", botix.count('<span class="chip"'), 21)
    check("seven rooms on the 3D floor", botix.count('<div class="blk'), 7)
    check("21 desks on the 3D floor", len(re.findall(r'<i title="[^"]+">', botix)), 21)


def test_fill_ins_and_releases():
    print("\nWhat he fills in, and where the website never goes:")
    js = read("assets/site.js")
    check("one place to put Botix's address and the contact email",
          bool(re.search(r"var SITE = \{\s*botix: '[^']*',\s*email: '[^']*'\s*\};", js)), True)
    check("until then the trial buttons lead to Contact, not to nowhere", "'#contact'" in js, True)
    sys.path.insert(0, str(ROOT))
    from coordination import product
    check("the website never ships inside a copy of the program", product.publisher_only("website/index.html"), True)
    check("nor does this test", product.publisher_only("coordination/website_tests.py"), True)


def main():
    if not SITE.is_dir():
        print("The TechDuo website is not in this copy; nothing to check.")
        return 0
    test_the_pages()
    test_every_link_lands()
    test_nothing_from_elsewhere()
    test_the_programs_own_files()
    test_privacy_words()
    test_plans()
    test_currency()
    test_motion()
    test_sectors_and_desks()
    test_fill_ins_and_releases()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        return 1
    print("All website tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
