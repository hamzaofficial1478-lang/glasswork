"""
markets_tests.py — run: python -m coordination.markets_tests

"in website setup program will ask from user in which country the user is
 willing to plan to establish his business like user can also mention 2 or 3
 or more countries or worldwide and according to that program will find leads
 do write posts do seo of website"

What is protected (a throwaway account folder and cabinet; no network):
  - home stays where the business is based; markets are where it wants
    customers, home first, codes checked on save, worldwide on its own switch;
  - a pass given a market reads that market as "the country", with its own
    biggest cities to search — and the owner's UAE map boxes are not used there;
  - the markets take turns, one per round, the same answer within a round;
  - a lead found in a market is not "outside the UAE", is stored with its
    country, and is written to under that country's own email rule;
  - keyword research keeps searches naming his markets, and drops none at all
    for a business selling worldwide;
  - posts name every market; the website check asks for a page per market;
  - the Your business page offers the choice and saves it.
"""
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_fail = []


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


class _Profile:
    """A website of its own, with a profile saved as given."""
    def __init__(self, data):
        self.data = data

    def __enter__(self):
        from coordination import board, profile, workspace
        self.real = (os.environ.get("AGENCY_MARKET"), board.DB, workspace.config_path)
        os.environ.pop("AGENCY_MARKET", None)
        d = Path(tempfile.mkdtemp())
        db = d / "agency.db"
        con = sqlite3.connect(db)
        con.executescript((ROOT / "coordination" / "schema.sql").read_text(encoding="utf-8"))
        con.commit()
        con.close()
        board.DB = str(db)
        board.ensure_schema()
        (d / "config").mkdir()
        workspace.config_path = lambda name: d / "config" / name
        (d / "config" / profile.FILE).write_text(json.dumps(self.data), encoding="utf-8")
        profile._CACHE.clear()
        return self

    def __exit__(self, *a):
        from coordination import board, profile, workspace
        m, board.DB, workspace.config_path = self.real
        if m is None:
            os.environ.pop("AGENCY_MARKET", None)
        else:
            os.environ["AGENCY_MARKET"] = m
        profile._CACHE.clear()


BASE = {"starter": "glass_uae", "country": "AE"}


def test_home_and_markets():
    print("\nHome is where the business is; markets are where it wants customers:")
    from coordination import profile
    with _Profile(BASE):
        check("with no markets set, the market is home", (profile.markets(), profile.market_phrase()), (["AE"], "the UAE"))
    with _Profile({**BASE, "markets": ["SA", "AE", "QA"]}):
        check("home first, then the others", profile.markets(), ["AE", "SA", "QA"])
        check("said as a person would", profile.market_phrase(), "the UAE, Saudi Arabia and Qatar")
        bad = profile.save({"markets": ["SA", "ZZ"]})
        check("a code the program does not know is refused", (bad["ok"], "ZZ" in bad.get("error", "")), (False, True))
        profile.save({"markets": ["sa", "SA", "PK"], "worldwide": 1})
        check("codes are cleaned and kept once", profile.stored()["markets"], ["SA", "PK"])
        check("worldwide is its own switch", (profile.worldwide(), profile.market_phrase()), (True, "customers worldwide"))


def test_a_pass_for_another_market():
    print("\nA pass given a market works in that market:")
    from coordination import profile
    with _Profile({**BASE, "markets": ["AE", "SA"]}):
        os.environ["AGENCY_MARKET"] = "SA"
        check("the country is the market, home is still home", (profile.country(), profile.home_country()), ("SA", "AE"))
        check("its biggest cities are where the finders look", profile.place()["cities"][:2], ["Riyadh", "Jeddah"])
        check("the owner's UAE map boxes are not used there", profile.starter_here(), False)
        os.environ["AGENCY_MARKET"] = "AE"
        check("giving home as the market changes nothing", (profile.home_market(), profile.starter_here()), (True, True))
        os.environ["AGENCY_MARKET"] = "XX"
        check("nonsense is ignored", profile.country(), "AE")


def test_they_take_turns():
    print("\nThe markets take turns, one per round:")
    from coordination import profile
    with _Profile({**BASE, "markets": ["AE", "SA", "QA"]}):
        picks = [profile.market_for_run("leads", f"run1:{r}") for r in range(6)]
        check("each in turn", picks, ["AE", "SA", "QA", "AE", "SA", "QA"])
        check("the same round always gets the same market", profile.market_for_run("leads", "run1:1"), "SA")
        check("keyword research keeps its own turns", profile.market_for_run("seo", "run1:0"), "AE")
    with _Profile(BASE):
        check("with one market there is nothing to choose", profile.market_for_run("leads", "x"), "")


def test_leads_in_a_market():
    print("\nA lead found in a market is his, stored with its country, and mailed under its rule:")
    from coordination import board, profile
    with _Profile({**BASE, "markets": ["AE", "DE"], "outreach_confirmed_for": []}):
        lid = board.add_lead_if_new({"company": "Berlin Glas GmbH", "email": "info@berlinglas.example",
                                     "country": "DE", "area": "Berlin"})
        with sqlite3.connect(board.DB) as c:
            cc = c.execute("SELECT country FROM leads WHERE id=?", (lid,)).fetchone()[0]
        check("stored with its country", cc, "DE")
        from pipelines import mail
        check("a lead in Germany is written under Germany's rule (consent first)",
              (mail.country_rule({"country": "DE"})["may_send"], mail.country_rule({"country": "DE"})["rule"]),
              (False, "consent"))
        check("a lead at home under home's rule", mail.country_rule({"country": "AE"})["may_send"], True)
        check("a market needing his word waits until he gives it",
              profile.outreach_for("SA")["may_send"], False)
    with _Profile({**BASE, "markets": ["AE", "SA"], "outreach_confirmed_for": ["SA"]}):
        check("and goes once he has", profile.outreach_for("SA")["may_send"], True)
        src = (ROOT / "pipelines" / "leads_sources.py").read_text(encoding="utf-8")
        check("a lead found in any of his markets is not 'outside'",
              "set(_profile.markets())" in src and "_profile.worldwide()" in src, True)


def test_searches_posts_and_pages():
    print("\nKeyword research, posts and the website check follow the markets:")
    src = (ROOT / "pipelines" / "seo_gap.py").read_text(encoding="utf-8")
    check("searches naming his markets are kept", "for _m in _profile.markets():" in src, True)
    check("  and a pass for another market asks Google as that country", "_GL = _profile.country().lower()" in src, True)
    check("  and worldwide drops nothing — without matching everything", 'NON_UAE = ()' in src and '(?!x)x' in src, True)
    from coordination import profile
    with _Profile({**BASE, "markets": ["AE", "SA"]}):
        from pipelines import content_write
        check("posts name every market", "Saudi Arabia" in content_write._local_conditions(), True)
        from pipelines import site_blueprint as bp
        site = {"pages": [{"url": "https://x.test/", "slug": "", "title": "Home", "h1": "Home", "html": "<h1>Home</h1>"}]}
        oks, gaps = [], []
        bp._markets(site, lambda c, t: oks.append(c), lambda *a, **k: gaps.append(a[0]))
        check("the website check asks for a page for customers in Saudi Arabia", gaps, ["market:sa"])
        site["pages"].append({"url": "https://x.test/riyadh.php", "slug": "riyadh", "title": "Glass work in Riyadh",
                              "h1": "Riyadh", "html": ""})
        oks, gaps = [], []
        bp._markets(site, lambda c, t: oks.append(c), lambda *a, **k: gaps.append(a[0]))
        check("  and a page about Riyadh counts", (gaps, oks), ([], ["market:sa"]))


def test_the_page():
    print("\nThe Your business page:")
    page = (ROOT / "ui" / "business.html").read_text(encoding="utf-8")
    check("it asks where he wants customers", all(w in page for w in ("Only in my country", "In these countries", "Worldwide")), True)
    check("it saves the markets and the switch", "markets: MKT.mode === 'home' ? [] : MKT.list" in page
          and "worldwide: MKT.mode === 'world'" in page, True)
    check("each market shows its own email rule", "outreach_confirmed_for" in page and "data-mconfirm" in page, True)
    check("country names are escaped", "${esc(cname(cc))}" in page, True)
    run = (ROOT / "pipelines" / "day_run.py").read_text(encoding="utf-8")
    check("the day gives lead and keyword passes their market",
          '"pipelines.leads_osm": "leads"' in run and 'env["AGENCY_MARKET"] = m' in run, True)


def main():
    for t in (test_home_and_markets, test_a_pass_for_another_market, test_they_take_turns, test_leads_in_a_market,
              test_searches_posts_and_pages, test_the_page):
        t()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        return 1
    print("markets: home stays home, and every market gets its turn.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
