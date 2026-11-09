"""
hardening_tests.py — run: python -m coordination.hardening_tests   (python3 on macOS/Linux)

    "check this security audit repo ... with those skills run a detailed audit
     on program on each factor of the program"

What the security audit found (docs/SECURITY-AUDIT.md), held in place:

  1. AI-written HTML carries no behaviour. A draft the writer was steered into
     — by something it read on the open web — could hold ontoggle= or onfocus=,
     which the Auditor's old list of four did not know. It ran in the post
     editor (proved with a harmless marker) and would have gone to the website.
     Now everything the writer returns is cut down to content, the editor shows
     only cleaned HTML, and the Auditor refuses ANY on…= attribute.
  2. The connector's picture download re-checks every redirect, so a public
     address cannot bounce it into the hosting's own network.
  3. The Home's download button is a link relative to the page, never built
     from the Host header a request carried.

Nothing reaches the internet; no AI is called.
"""
import json
import re
import sys
from pathlib import Path
from unittest import mock

from . import auditor, posts

ROOT = Path(__file__).resolve().parents[1]
_fail = []


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


BASE = ("<h1>Glass balustrades</h1><!--image 1--><p>Ring <a href=\"tel:+97141234567\">+971 4 123 4567</a> "
        "or <a href=\"https://wa.me/971509876543\">WhatsApp</a>.</p>")
HANDLERS = {"ontoggle": "<details open ontoggle=\"document.title='x'\"><summary>s</summary></details>",
            "onfocus": "<a href=\"#\" onfocus=\"document.title='x'\" autofocus>x</a>",
            "onanimationstart": "<b style=\"animation:x 1s\" onanimationstart=\"document.title='x'\">x</b>",
            "onpointerenter": "<p onpointerenter=\"document.title='x'\">x</p>",
            "onerror": "<img src=\"x\" onerror=\"document.title='x'\">"}


def test_the_auditor_refuses_any_event_attribute():
    print("The Auditor refuses any attribute that runs something, not a list of four:")
    for name, extra in HANDLERS.items():
        r = auditor.check_page(BASE + extra, {"service": "glass"}, "example.ae")
        check(f"  {name}= is blocked as code", any("contains code" in b for b in r["blocking"]), True)
    plain = BASE + "<p>Based on = what you need. Turn on the AC once the silicone has cured.</p>"
    r = auditor.check_page(plain, {"service": "glass"}, "example.ae")
    check("ordinary words with 'on' in them are not code", any("contains code" in b for b in r["blocking"]), False)


def test_what_the_writer_returns_is_content_only():
    print("\nWhat the AI writes is cut down to content before anything else sees it:")
    from pipelines import content_write as CW
    for name, extra in HANDLERS.items():
        out = CW.safe_page({"title": "t", "html": BASE + extra})["html"]
        check(f"  {name}= is gone", re.search(r"\son[a-z]+\s*=", out) is None, True)
    out = CW.safe_page({"html": BASE + "<script>x()</script><iframe src=\"https://x\"></iframe>"})["html"]
    check("  scripts and frames are gone, words and all", ("<script" in out, "<iframe" in out, "x()" in out), (False, False, False))
    kept = CW.safe_page({"html": BASE + "\n" + CW.PHOTO_SLOT})["html"]
    check("its heading, links and picture markers are kept",
          ("<h1>" in kept, 'href="tel:+97141234567"' in kept, "<!--image 1-->" in kept, "agency:photos" in kept),
          (True, True, True, True))
    check("what was taken out is noted, so the draft is held for him rather than quietly published",
          bool(CW.safe_page({"html": BASE + HANDLERS["ontoggle"]}).get("_had_code")), True)
    check("  and a clean draft carries no such note", "_had_code" in CW.safe_page({"html": BASE}), False)
    check("  a link to javascript: is not", 'href="javascript' in CW.safe_page(
        {"html": "<p><a href=\"javascript:alert(1)\">x</a></p>"})["html"], False)

    import providers
    reply = {"title": "Glass balustrades", "meta": "m", "h1": "Glass balustrades", "html": BASE + HANDLERS["ontoggle"]}
    with mock.patch.object(providers, "think",
                           lambda *a, **k: {"ok": True, "used": "stand-in", "rung": "own", "output": json.dumps(reply)}):
        got = CW._ask_the_writer({"title": "Glass balustrades", "primary_keyword": "glass balustrades"},
                                 {"name": "Gulf Glass", "phone": "+971 4 123 4567", "areas": ["Dubai"]}, kind="blog")
    check("the writer's own answer comes back cleaned", (got["ok"], "ontoggle" in got["page"]["html"]), (True, False))


def test_the_editor_shows_only_cleaned_html():
    print("\nThe post editor never puts a draft's raw HTML on the screen:")
    draft = {"title": "t", "meta": "m", "html": BASE + HANDLERS["ontoggle"] + HANDLERS["onerror"]}
    got = {"row": {"status": "in_review", "title": "t"}, "brief": {}, "draft": draft, "others": {}}
    with mock.patch.object(posts, "_load", lambda cid: got), \
         mock.patch.object(posts, "_report", lambda page, g: {}):
        r = posts.get(1)
    check("a held draft opens", r["ok"], True)
    check("  with no attribute that runs anything", re.search(r"\son[a-z]+\s*=", r["html"]) is None, True)
    check("  and its words intact", "+971 4 123 4567" in r["html"], True)
    app = (ROOT / "ui" / "app.html").read_text(encoding="utf-8")
    check("the polish button's answer is cleaned too (it goes into the editor)",
          "new = clean_html(r[\"page\"][\"html\"])" in (ROOT / "coordination" / "posts.py").read_text(encoding="utf-8")
          and "$('#edBody').innerHTML = j.html;" in app, True)


def test_the_connector_rechecks_every_redirect():
    print("\nThe connector's picture download re-checks every hop:")
    src = (ROOT / "paste-into-your-site" / "agency-api.php").read_text(encoding="utf-8")
    fn = src[src.index("function fetch_picture("):src.index("function save_picture(")]
    check("it does not let curl follow redirects by itself", "CURLOPT_FOLLOWLOCATION => false" in fn, True)
    check("  nor PHP's own downloader", "'follow_location' => 0" in fn, True)
    check("  each hop is asked again whether it is a public address", "public_ip_of($url)" in fn and "for ($hop" in fn, True)
    check("  and connects to the address it checked (no second DNS answer)", "CURLOPT_RESOLVE" in fn, True)
    from pipelines import site_api
    check("the fixed connector is a new version, so every website takes it by itself",
          (int(re.search(r"^const CONNECTOR = (\d+);", src, re.M).group(1)), site_api.CONNECTOR_NEWEST >= 11),
          (site_api.CONNECTOR_NEWEST, True))


def test_the_download_button_is_not_built_from_the_host_header():
    print("\nThe Home's download button cannot be pointed elsewhere by a forged Host header:")
    src = (ROOT / "publish-to-your-site" / "glasswork-home" / "index.php").read_text(encoding="utf-8")
    page = src[src.index("// --- the download page"):src.index("if ($do === 'ping'")]
    check("the link is relative to the page", "'updates/' . rawurlencode(" in page and "here()" not in page, True)
    check("  and the page is not kept by caches", "Cache-Control: no-store" in page, True)


def main():
    for t in (test_the_auditor_refuses_any_event_attribute, test_what_the_writer_returns_is_content_only,
              test_the_editor_shows_only_cleaned_html, test_the_connector_rechecks_every_redirect,
              test_the_download_button_is_not_built_from_the_host_header):
        t()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        return 1
    print("hardening: all good — what the AI writes carries no behaviour, redirects are re-checked, links are relative.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
