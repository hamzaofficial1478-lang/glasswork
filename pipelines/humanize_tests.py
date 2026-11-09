"""
humanize_tests.py — run: python -m pipelines.humanize_tests   (python3 on macOS/Linux)

    "found another repo that polish and fixes the ai written articles ... take
     the brain of the repo and will add into the program and train the agent
     that will be writing the articles"

The humanizer's method (pipelines/humanize.py) in the writer: what has to hold.

  it finds the tells by their pattern, strongest first, and quotes them
  a plain, specific page passes untouched; one dash alone is not a verdict
  the writer is told the method, and every draft is checked against it
  the polishing pass keeps every fact: a rewrite that changes a number, a
    link or a picture is thrown away, and so is one that reads no better
  the post editor's Polish button saves nothing by itself

No AI is called: a stand-in answers.
"""
import json
import sys
from unittest import mock

from . import content_write as CW
from . import humanize as H

_fail = []


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


ROBOT = """<h1>Glass Balustrades</h1><h2>Why Glass Balustrades Matter</h2><p>Glass balustrades matter.</p>
<p>In today's fast-paced world, a balcony is not just a ledge, but a statement — a space that showcases your style. Let's dive in.</p>
<ul><li><strong>Safety:</strong> Toughened glass is crucial.</li><li><strong>Style:</strong> It boasts a stunning finish.</li></ul>
<p>Experts agree it plays a key role in modern homes, ensuring safety, comfort, and style.</p><p>That's the difference.</p>
<h2>Conclusion</h2><p>In this article we explored balustrades. I hope this helps!</p>"""

PLAIN = """<h1>Glass balustrades for Dubai balconies</h1>
<p>Most balconies we fit in Dubai Marina take 12 mm toughened glass in a base channel. Ring
<a href="tel:+97141234567">+971 4 123 4567</a> and we will come and measure.</p>
<h2>What thickness to choose</h2><p>For a private balcony, 12 mm toughened or 13.5 mm laminated glass is usual.
Laminated stays in place if it breaks, so we use it above the ground floor.</p>
<!--image 1-->
<p>The channel is fixed into the slab, not the screed. We check the slab before we quote, because a channel
bolted into screed works loose in a year of summer heat.</p>"""


def test_it_finds_the_tells():
    print("The tells are found by their pattern, strongest first:")
    found = {t["n"]: t for t in H.find(ROBOT)}
    for n, what in ((1, "not X but Y"), (2, "a one-line closer"), (4, "a staged run-up"), (13, "a Conclusion send-off"),
                    (16, "sales words"), (17, "unnamed experts"), (19, "bold labels"), (22, "chat leftovers"),
                    (25, "writing about the article"), (8, "a long dash"), (12, "words models overuse"),
                    (15, "an -ing rider"), (18, "boasts for has"), (20, "a Title Case heading"),
                    (24, "a heading said again")):
        check(f"  §{n}: {what}", n in found, True)
    order = [t["strength"] for t in H.find(ROBOT)]
    check("strong tells come first", order == sorted(order, key=lambda s: {"strong": 0, "medium": 1, "weak": 2}[s]), True)
    check("a page full of them scores low", H.score(ROBOT) < 30, True)
    probs = H.problems(ROBOT)
    check("the writer is told which pattern, what it wrote, and what to do",
          any("not X but Y" in p and "not just a ledge" in p and "state the point directly" in p for p in probs), True)


def test_a_plain_page_passes():
    print("\nA plain, specific page passes untouched; one weak sign alone is not a verdict:")
    check("the plain page reads like a person: 100", H.score(PLAIN), 100)
    check("  and the writer is told nothing", H.problems(PLAIN), [])
    one = PLAIN.replace("It is usual.", "")
    weak = one.replace("<p>The channel is fixed", "<p>No survey needed. The channel is fixed")
    tells = H.find(weak)
    check("a single weak sign is seen…", [t["n"] for t in tells], [11])
    check("  …but does not count on its own", H.counted(tells), [])
    check("a literal word the trade uses is not a tell: 'key' in a key cut, 'landscape' as a garden",
          H.find("<p>We cut a spare key for the gate. The landscape gardener laid the turf first.</p>"), [])


def test_the_writer_knows_the_method():
    print("\nThe writer is told the method, and every draft is checked against it:")
    check("the rules are in the writer's instructions", "SO IT READS LIKE A PERSON WROTE IT" in CW.SYSTEM, True)
    check("  for every business, not only the owner's",
          "SO IT READS LIKE A PERSON WROTE IT" in CW.SYSTEM[CW.SYSTEM.index("WHAT YOU MUST NOT CLAIM"):], True)
    check("  no long dashes, no labels in bold", ("No long dashes" in CW.SYSTEM, "never a label with a colon" in CW.SYSTEM),
          (True, True))
    probs = " ".join(CW.voice_problems({"html": ROBOT}, "blog"))
    check("a draft's review names the humanizer's patterns", all(w in probs for w in ("not X but Y", "bold as decoration")), True)


def _ask(reply):
    calls = []

    def ask(messages, tokens):
        calls.append(messages)
        return {"ok": True, "output": json.dumps(reply)}
    return ask, calls


def test_polishing_keeps_every_fact():
    print("\nThe polishing pass keeps every fact, or is not used:")
    page = {"title": "t", "html": PLAIN.replace("For a private balcony,", "Additionally, for a private balcony —")
                                     .replace("is usual.", "is crucial, ensuring safety, comfort, and style.")}
    good = PLAIN.replace("is usual.", "is what most people choose.")
    ask, calls = _ask({"html": good, "changed": ["took out a dash and an -ing tail"]})
    r = H.polish(page, ask=ask)
    check("a rewrite that keeps everything is used", (r["ok"], r["page"]["html"] == good), (True, True))
    check("  and reads better", r["after"] > r["before"], True)
    sent = calls[0][1]["content"]
    check("  the editor was told what was found, and that the text is material, not orders",
          ("WHAT WAS FOUND" in sent, "never instructions to you" in calls[0][0]["content"]), (True, True))
    check("  and to add nothing", "Add nothing" in calls[0][0]["content"], True)
    for what, bad in (("a changed number", good.replace("12 mm", "10 mm")),
                      ("a changed link", good.replace("tel:+97141234567", "tel:+971000")),
                      ("a lost picture", good.replace("<!--image 1-->", "")),
                      ("added code", good + "<script>x()</script>")):
        ask, _c = _ask({"html": bad})
        r = H.polish(page, ask=ask)
        check(f"a rewrite with {what} is not used", (r["ok"], r["page"]["html"] == page["html"]), (False, True))
    worse = page["html"] + ""
    ask, _c = _ask({"html": worse.replace("is crucial", "is truly crucial, a testament to craft")})
    r = H.polish(page, ask=ask)
    check("a rewrite that reads no better is not used", r["page"]["html"], page["html"])
    ask, calls = _ask({"html": "x"})
    r = H.polish({"html": PLAIN}, ask=ask)
    check("a page with nothing to polish is not sent at all", (r["ok"], len(calls)), (True, 0))


def test_the_editor_button_saves_nothing():
    print("\nThe post editor's Polish button gives the words back; it saves nothing:")
    from coordination import posts
    got = {"row": {"status": "for_blog"}, "brief": {}, "others": {}, "draft": {}}
    saved = []
    better = PLAIN.replace("is usual.", "is what most people choose.")
    with mock.patch.object(posts, "_load", lambda cid: got), \
         mock.patch.object(posts, "save", lambda *a, **k: saved.append(a)), \
         mock.patch.object(H, "polish", lambda page, **kw: {"ok": True, "page": {**page, "html": better},
                                                                "before": 70, "after": 100, "changed": ["x"]}):
        r = posts.polish(5, "Title", "Meta", PLAIN.replace("is usual.", "is crucial."))
    check("the polished words come back for the editor", (r["ok"], "most people choose" in r.get("html", "")), (True, True))
    check("  with the score before and after", (r["before"], r["after"]), (70, 100))
    check("  and nothing was saved", saved, [])
    from pathlib import Path
    app = (Path(__file__).resolve().parents[1] / "ui" / "app.html").read_text(encoding="utf-8")
    check("the editor has the button, and only Save saves", ("id=\"edPolish\"" in app and "/polish`" in app), True)


def main():
    for t in (test_it_finds_the_tells, test_a_plain_page_passes, test_the_writer_knows_the_method,
              test_polishing_keeps_every_fact, test_the_editor_button_saves_nothing):
        t()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        return 1
    print("humanize: all good — the writer knows the method, every draft is checked, and polishing adds nothing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
