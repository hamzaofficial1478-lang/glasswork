"""
keyword_tests.py — run: python -m pipelines.keyword_tests

"the seo sector finds the keywords according to the post then it gives those
 keywords to the post room and then post room will add those keywords in the
 post ... competitors ranking analysis on keywords, finding keywords and adding
 them to the post"

What has to hold:

  the right keywords   about THIS job — "glass partition" is not a keyword for a
                       post on railings — and never a rival's name
  a winnable main one  a bare head term is sharpened to a longer local phrase
                       when the evidence has one
  who ranks            read from the results, his own site found among them,
                       directories skipped, the real pages read for length and
                       headings
  the writer is told   where the main keyword goes, every supporting keyword,
                       the questions, who it is up against, how long
  the check is honest  a draft missing the keyword in the title, or stuffed with
                       it, is sent back saying exactly that

No network: search results, autocomplete and pages are stand-ins.
"""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

from coordination import board
from . import seo_keywords as K

_fail = []
ROOT = Path(__file__).parents[1]


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


def _fresh():
    d = Path(tempfile.mkdtemp())
    db = d / "agency.db"
    con = sqlite3.connect(db)
    con.executescript((ROOT / "coordination" / "schema.sql").read_text(encoding="utf-8"))
    con.commit()
    con.close()
    board.DB = str(db)
    board.ensure_schema()
    return db


SUGGEST = ["glass railing price in dubai", "glass railing dubai", "glass partition dubai",
           "how much does glass railing cost", "how much glass railing cost",
           "what thickness glass for railing", "frameless glass railing",
           "glass balcony railing dubai", "glass railing installation dubai"]

RIVAL_PAGE = ("<html><head><title>Glass Railing Dubai | Rival</title></head><body>"
              "<h1>Glass railing in Dubai</h1><h2>Types of glass railing</h2>"
              "<h2>Glass railing cost factors</h2><h2>Installation process</h2>"
              "<p>" + ("Toughened laminated glass with stainless steel spigots and a base channel "
                       "system suits balconies. " * 90) + "</p></body></html>")


def _serp(q):
    return {"ok": True, "source": "Google", "results": [
        {"position": 1, "url": "https://www.facebook.com/somefirm", "domain": "facebook.com",
         "title": "Some firm", "snippet": ""},
        {"position": 2, "url": "https://rivalglass.ae/glass-railing", "domain": "rivalglass.ae",
         "title": "Glass Railing Dubai", "snippet": ""},
        {"position": 3, "url": "https://otherglass.ae/railings", "domain": "otherglass.ae",
         "title": "Railings", "snippet": ""},
        {"position": 4, "url": "https://www.example.ae/glass-railing", "domain": "example.ae",
         "title": "Ours", "snippet": ""}]}


def test_the_plan():
    print("\nSEO's plan for a post on glass railings:")
    _fresh()
    fetched = []
    pack = K.build({"primary_keyword": "glass railing", "area": "Dubai",
                    "secondary_keywords": ["glass balustrade dubai"]},
                   kind="blog", site="https://www.example.ae",
                   suggest=lambda q: SUGGEST, serp=_serp,
                   fetch=lambda u: fetched.append(u) or RIVAL_PAGE, budget=10)
    check("the head term is sharpened to a local phrase people search",
          pack["primary"], "glass railing price in dubai")
    check("the head term stays as a supporting keyword", pack["secondary"][0], "glass railing")
    worded = K.build({"primary_keyword": "glass railing"}, suggest=lambda q: [
        "how much does glass railing cost dubai", "how much does glass railing cost dubai"],
        serp=lambda q: {"ok": False}, fetch=lambda u: "", budget=5)
    check("a question is never made the main keyword — it goes in the FAQ",
          worded["primary"], "glass railing")
    check("a different job is not a keyword for this post",
          any("partition" in k for k in pack["secondary"]), False)
    check("the brief's own phrase is kept", "glass balustrade dubai" in pack["secondary"], True)
    cheap = K.build({"primary_keyword": "glass railing"}, suggest=lambda q: [
        "cheap glass railing dubai", "glass railing discount", "glass railing supplier dubai"],
        serp=lambda q: {"ok": False}, fetch=lambda u: "", budget=5)
    check("a word the post may not use is never handed over as a keyword",
          [k for k in cheap["secondary"] if "cheap" in k or "discount" in k], [])
    check("questions people type are collected, once each",
          (len(pack["questions"]), sum("how much" in q for q in pack["questions"]),
           "what thickness glass for railing?" in pack["questions"]), (2, 1, True))
    check("his own site is found among the results", pack["our_position"], 4)
    check("directories and social sites are not read as rivals",
          any("facebook" in u for u in fetched), False)
    check("the ranking pages are read", [c["domain"] for c in pack["competitors"]],
          ["rivalglass.ae", "otherglass.ae"])
    check("and the post is asked to be longer than they are",
          pack["target_words"] > pack["rival_median_words"], True)
    check("what they cover is passed on",
          any("cost" in h.lower() for h in pack["headings_to_cover"]), True)
    check("with the trade terms they share", "base channel" in " ".join(pack["related_terms"]), True)

    brief_lines = "\n".join(K.for_the_writer(pack, "blog"))
    check("the writer is told where the main keyword goes",
          all(w in brief_lines for w in ("in the title", "in the <h1>", "first 100 words",
                                         "in at least one <h2>", "meta description")), True)
    check("and who it is up against", "#2 rivalglass.ae" in brief_lines, True)
    check("a price keyword comes with the rule: explain the cost, never a figure",
          "never a figure" in brief_lines, True)

    print("\n...without a Google search key it says so, and still plans the keywords:")
    pack2 = K.build({"primary_keyword": "glass railing"}, suggest=lambda q: SUGGEST,
                    serp=lambda q: K.google_serp(q), fetch=lambda u: "", budget=5)
    check("the keywords are still there", bool(pack2["secondary"]), True)
    check("and it points him to Setup", any("Setup" in n for n in pack2["notes"]), True)


def test_the_check():
    print("\nA draft measured against the plan:")
    pack = {"primary": "glass railing price in dubai", "secondary": ["frameless glass railing",
            "glass balcony railing dubai", "glass railing installation"],
            "questions": ["how much does glass railing cost?"], "target_words": 300}
    body = ("<h1>Glass railing price in Dubai</h1><p>Glass railing price in Dubai depends on the "
            "glass, the fixing and the length. " + "We fit frameless glass railing on balconies. " * 20
            + "</p><h2>What drives the glass railing price in Dubai</h2><p>" + "Glass balcony railing "
            "Dubai jobs need toughened glass. " * 15 + "</p><h2>Glass railing installation</h2><p>"
            + "Measured, made, fitted. " * 20 + "</p><h3>How much does glass railing cost?</h3><p>It "
            "depends on the metre run.</p>")
    good = K.check({"title": "Glass Railing Price in Dubai: What It Costs",
                    "meta": "Glass railing price in Dubai explained.", "h1": "", "html": body}, pack)
    check("a draft that follows the plan passes every point", (good["score"], good["problems"]),
          (100, []))
    bad = K.check({"title": "Balcony safety guide", "meta": "A guide.", "html":
                   "<h1>Balcony safety</h1><p>" + "Glass railing price in dubai. " * 40 + "</p>"}, pack)
    check("missing from the title is named", any("in the title" in p for p in bad["problems"]), True)
    check("stuffing is named", any("too many" in p for p in bad["problems"]), True)
    check("the missing supporting keywords are named",
          any("frameless glass railing" in p for p in bad["problems"]), True)
    check("and the unanswered question", any("FAQ" in p for p in bad["problems"]), True)


def test_the_plan_is_kept_with_the_post():
    print("\nThe plan is made once by SEO and kept on the brief:")
    _fresh()
    brief = {"primary_keyword": "aluminium pergola", "title": "Aluminium pergolas"}
    with sqlite3.connect(board.DB) as c:
        c.execute("INSERT INTO content_queue(id,title,type,brief,status,created_at)"
                  " VALUES(3,'Pergolas','blog',?,'briefed',1)", (json.dumps(brief),))
    made = K.plan_for(3, brief, kind="blog")
    with sqlite3.connect(board.DB) as c:
        kept = json.loads(c.execute("SELECT brief FROM content_queue WHERE id=3").fetchone()[0])
        ran = c.execute("SELECT agent FROM agent_runs").fetchall()
    check("kept on the brief", kept.get("seo_pack", {}).get("primary"), made["primary"])
    check("made by the SEO desk, so the office shows SEO working for Content",
          [r[0] for r in ran], ["seo-strategist"])
    again = K.plan_for(3, kept, kind="blog")
    with sqlite3.connect(board.DB) as c:
        n = c.execute("SELECT count(*) FROM agent_runs").fetchone()[0]
    check("a rewrite uses the same plan rather than searching again", (again == made, n), (True, 1))


def main():
    real = board.DB
    try:
        for t in (test_the_plan, test_the_check, test_the_plan_is_kept_with_the_post):
            t()
    finally:
        board.DB = real
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        return 1
    print("keywords: all good — SEO plans every post and the writer is held to it.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
