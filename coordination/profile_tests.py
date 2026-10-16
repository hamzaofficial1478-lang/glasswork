"""
profile_tests.py — run: python -m coordination.profile_tests

"any small business, anywhere"

What is protected here:
  - the owner's website is exactly what it was: the glass-and-aluminium-in-
    the-UAE catalogue, the UAE's places, phone rules and email rules;
  - a NEW website starts blank, and says what it still needs, rather than
    quietly selling shower glass in Dubai Marina;
  - a plumber in Manchester gets his own services, customers, areas, country,
    phone format, map searches, website guesses, writing instructions and
    email rule — and nothing of the owner's;
  - a bakery in Munich is never written to first, because Germany needs
    consent; somewhere the program has no settled rule for, nothing goes out
    until the owner confirms his country's rule;
  - "Draft it from my website" keeps only what is real;
  - bringing the owner's website into an account keeps his starter.

Every website's checks run in a program of their own, because the tables are
read when a program starts — exactly as they are in a real run.
"""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

from . import profile, workspace

ROOT = Path(__file__).parents[1]
_fail = []


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


PLUMBER = {
    "business": {"name": "Sam Plumbing", "trade": "plumbing and heating repairs and installation",
                 "trade_short": "plumbing & heating", "tagline": "Plumbing and heating, done right",
                 "description": "Boilers, leaks and bathrooms for landlords and businesses."},
    "country": "GB", "language": "en-GB", "currency": "GBP",
    "place": {"home": "Manchester", "served": ["Manchester", "Salford", "Stockport", "Trafford"],
              "cities": ["Manchester", "Salford", "Stockport", "Trafford"]},
    "services": {
        "boiler": {"label": "boiler repair and servicing",
                   "names": ["boiler repair", "boiler service", "gas safety check"],
                   "buyers": ["landlord", "care_home"], "related": ["leak"],
                   "line": "Boilers repaired, serviced and certified."},
        "leak": {"label": "leak detection and repair", "names": ["leak repair", "burst pipe"],
                 "buyers": ["property_management"], "related": []},
    },
    "service_order": ["boiler", "leak"],
    "customers": {"landlord": {"why": "Landlords", "queries": ["letting agent", "landlord"]},
                  "care_home": {"why": "Care homes", "queries": ["care home", "nursing home"]},
                  "property_management": {"why": "Property managers",
                                          "queries": ["property management company"]}},
    "local_customers": ["landlord"],
}


class _Website:
    """A data folder with one account and one website carrying this profile."""

    def __init__(self, prof=None):
        self.prof = prof

    def __enter__(self):
        self.saved = {k: os.environ.get(k) for k in ("AGENCY_DATA", "AGENCY_DB", "AGENCY_KEY")}
        self.dir = Path(tempfile.mkdtemp(prefix="profile-"))
        os.environ["AGENCY_DATA"] = str(self.dir / "data")
        os.environ.pop("AGENCY_DB", None)
        os.environ.pop("AGENCY_KEY", None)
        acct = workspace.create_account("Owner")["account"]
        site = workspace.add_site(acct["id"], "A website", "https://www.example.co.uk")["site"]
        workspace.open_site(acct["id"], site["id"])
        self.acct, self.site = acct["id"], site["id"]
        if self.prof is not None:
            profile.save(self.prof)
        return self

    def __exit__(self, *a):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def run(self, code, extra_env=None) -> dict:
        """Run this code in a program of its own on this website; it prints one JSON line."""
        env = {k: v for k, v in os.environ.items() if k not in ("AGENCY_DB", "AGENCY_KEY")}
        env.update(AGENCY_DATA=str(self.dir / "data"), AGENCY_IGNORE_DOTENV="1",
                   **(extra_env or {}))
        r = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env,
                           capture_output=True, text=True, encoding="utf-8", timeout=180)
        try:
            return json.loads((r.stdout or "").strip().splitlines()[-1])
        except Exception:
            return {"_error": (r.stderr or r.stdout or "")[-800:]}


def test_his_website_is_unchanged():
    print("The owner's website is exactly what it was:")
    from pipelines import leads_sources, services
    from . import uae
    check("it builds on the glass-and-aluminium starter", profile.is_starter(), True)
    check("every one of his services", len(services.SERVICES), len(services.GLASS_SERVICES))
    check("every one of his customer types", leads_sources.SEGMENTS is leads_sources.GLASS_SEGMENTS, True)
    check("the UAE, all seven emirates", (uae.COUNTRY, len(uae.EMIRATES)), ("AE", 7))
    check("said as he says it", uae.served_phrase(), "the UAE")
    check("his UAE sources, Dubai Chamber and all",
          leads_sources.SOURCES is leads_sources.GLASS_SOURCES, True)
    check("writing to businesses is allowed there", profile.outreach()["may_send"], True)
    check("nothing is missing", profile.missing(), [])
    check("and the one-line description", profile.describe(),
          "a glass and aluminium fabrication and installation business in the UAE")


def test_a_new_website_starts_blank():
    print("\nA new website starts blank, and says what it needs:")
    with _Website() as w:
        got = w.run(
            "import json\n"
            "from coordination import profile\n"
            "from pipelines import services, leads_sources\n"
            "print(json.dumps({'missing': profile.missing(), 'services': len(services.SERVICES),\n"
            "  'segments': len(leads_sources.SEGMENTS), 'match': services.match_service('Hilton', segment='hotel'),\n"
            "  'sources': [s['name'] for s in leads_sources.SOURCES]}))")
    check("it lists the five things it still needs", len(got.get("missing") or []), 5)
    check("  starting with what the business does", (got.get("missing") or [""])[0],
          "what the business does")
    check("no glass services borrowed from the owner", got.get("services"), 0)
    check("no customer types either", got.get("segments"), 0)
    check("it does not guess a service", (got.get("match") or ["x"])[0], "")
    check("and no Dubai Chamber of Commerce",
          any("Dubai" in s for s in got.get("sources") or []), False)


def test_a_plumber_in_manchester():
    print("\nA plumber in Manchester gets his own everything:")
    with _Website(PLUMBER) as w:
        got = w.run(r'''
import json
from coordination import profile, uae, board, countries
from pipelines import services, leads_sources as ls, harvest, leads_hunt, leads_osm, find_site
from pipelines import content_write, mail, seo_gap, seo_answers, campaign, competitors, it_health
ok, why = ls.screen_lead({"company": "Riverside Lettings", "area": "Salford", "country": "GB",
                          "phone": "0161 496 0000", "segment": "landlord"})
far, far_why = ls.screen_lead({"company": "Le Bistro", "area": "Paris", "phone": "+33 1 23 45 67 89",
                               "segment": "landlord"})
calls = []
box = leads_osm.box_for("Manchester", geocode=lambda a, c: calls.append((a, c)) or "53.4,-2.3,53.5,-2.1")
again = leads_osm.box_for("Manchester", geocode=lambda a, c: calls.append((a, c)) or "x")
sp = content_write.system_prompt()
lb = seo_answers.localbusiness_jsonld("https://www.example.co.uk", [])[0]
print(json.dumps({
  "services": list(services.SERVICES), "labels": services.ALL_SERVICE_LABELS,
  "match": services.match_service("Riverside Care Home", segment="care_home")[0],
  "segments": sorted(ls.SEGMENTS), "sources": [s["name"] for s in ls.SOURCES],
  "screen": [ok, why], "far": [far, far_why],
  "country": uae.COUNTRY, "phrase": uae.served_phrase(), "in": uae.is_uae("Unit 4, Salford M5"),
  "dubai": uae.is_uae("Business Bay, Dubai"),
  "phone": harvest.normalise_phone("0161 496 0000"), "foreign": harvest.normalise_phone("+971 4 385 1122"),
  "keys": [board.phone_key("0161 496 0000"), board.phone_key("+44 161 496 0000")],
  "cc": leads_hunt._cc(), "cname": leads_hunt._country_name(),
  "areas": list(leads_osm.DEFAULT_AREAS), "box": box, "box_again": again, "geocoded": len(calls),
  "tlds": list(find_site.TLDS)[:2],
  "prompt_trade": "plumbing" in sp, "prompt_glass": "glass" in sp.lower(), "prompt_uae": "UAE" in sp,
  "prompt_money": "no GBP amount" in sp, "prompt_where": "the UK" in sp,
  "may_send": mail.country_rule()["may_send"], "subject": mail._default_subject(),
  "gl": seo_gap._GL, "nonlocal_uae": "united arab emirates" in seo_gap.NON_UAE,
  "nonlocal_uk": "united kingdom" in seo_gap.NON_UAE,
  "llms": seo_answers.llms_txt("https://www.example.co.uk").splitlines()[0],
  "lb_country": '"addressCountry": "GB"' in lb,
  "copy": campaign.standard_copy({"name": "Sam Plumbing", "areas_phrase": uae.served_phrase()}, [])["subject"],
  "rivals": "boiler" in competitors.RIVAL_NAME_RE,
  "tel_ok": it_health.looks_like_local_number("0161 496 0000"),
  "tel_uae": it_health.looks_like_local_number("+971 4 385 1122"),
}))''')
    if got.get("_error"):
        check("the checks ran", got["_error"], "")
        return
    check("his services, in his order", got["services"], ["boiler", "leak"])
    check("  named as he names them", got["labels"],
          ["boiler repair and servicing", "leak detection and repair"])
    check("a care home is matched to boilers, from who buys them", got["match"], "boiler")
    check("his customer types", got["segments"], ["care_home", "landlord", "property_management"])
    check("sources that work in any country",
          any("Chamber" in s or "DED" in s for s in got["sources"]), False)
    check("a Salford landlord on the map passes the screen", got["screen"][0], True)
    check("  said as 'in the UK'", "in the UK" in got["screen"][1], True)
    check("a Paris bistro does not", got["far"][0], False)
    check("  and says why", "outside the UK" in got["far"][1], True)
    check("the country is the UK", got["country"], "GB")
    check("his areas, said as he would", got["phrase"], "Manchester, Salford, Stockport and Trafford")
    check("Salford is in his country", got["in"], True)
    check("Dubai is not", got["dubai"], False)
    check("a Manchester number in one shape", got["phone"], "+441614960000")
    check("a UAE number is not his country's", got["foreign"], "")
    check("the same number written two ways is one number", got["keys"][0] == got["keys"][1], True)
    check("map searches fenced to the UK", (got["cc"], got["cname"]), ("GB", "United Kingdom"))
    check("OpenStreetMap starts at home", got["areas"], ["Manchester", "Salford", "Stockport"])
    check("an area's box is looked up", got["box"], "53.4,-2.3,53.5,-2.1")
    check("  once, then remembered", (got["box_again"], got["geocoded"]), ("53.4,-2.3,53.5,-2.1", 1))
    check("website guesses try .co.uk first", got["tlds"], ["co.uk", "uk"])
    check("the writer is told his trade", got["prompt_trade"], True)
    check("  and nothing about glass", got["prompt_glass"], False)
    check("  or the UAE", got["prompt_uae"], False)
    check("  that no pounds figure is allowed", got["prompt_money"], True)
    check("  and the conditions where he works", got["prompt_where"], True)
    check("the UK lets the agency write to businesses", got["may_send"], True)
    check("a mail with no subject is about his trade", got["subject"], "Plumbing & heating work")
    check("SEO asks Google UK", got["gl"], "gb")
    check("  and treats the UAE as somewhere else", (got["nonlocal_uae"], got["nonlocal_uk"]), (True, False))
    check("llms.txt says what he does", got["llms"], "# Plumbing & heating, the UK")
    check("his business markup is in the UK", got["lb_country"], True)
    check("the campaign is about his trade", "Plumbing" in got["copy"], True)
    check("rivals are found by his trade's words", got["rivals"], True)
    check("a UK click-to-call number looks right to IT", got["tel_ok"], True)
    check("  and a UAE one does not", got["tel_uae"], False)


def test_a_bakery_in_munich_is_never_written_to_first():
    print("\nA bakery in Munich: Germany needs consent first, so nothing is sent:")
    bakery = dict(PLUMBER, country="DE", language="de-DE", currency="EUR",
                  business={"trade": "bakery", "trade_short": "bakery"},
                  place={"home": "München", "served": ["München"], "cities": ["München"]})
    with _Website(bakery) as w:
        got = w.run(
            "import json\nfrom pipelines import mail\n"
            "r = mail._smtp_send({'id': 1, 'email': 'info@cafe.de'}, 'Subject: hi\\n\\nhello')\n"
            "print(json.dumps({'rule': mail.country_rule(), 'send': r}))",
            extra_env={"SMTP_HOST": "smtp.example.de", "SMTP_USER": "me@example.de"})
    check("the rule is consent", (got.get("rule") or {}).get("rule"), "consent")
    check("nothing may be sent", (got.get("rule") or {}).get("may_send"), False)
    check("the send point refuses it", ((got.get("send") or {}).get("ok"),
                                        (got.get("send") or {}).get("law")), (False, True))
    check("  naming the law", "UWG" in ((got.get("send") or {}).get("error") or ""), True)


def test_elsewhere_waits_for_his_word():
    print("\nWhere the program has no settled rule, nothing goes until he confirms his:")
    elsewhere = dict(PLUMBER, country="PK", place={"home": "Lahore", "served": ["Lahore"],
                                                   "cities": ["Lahore"]})
    with _Website(elsewhere) as w:
        before = w.run("import json\nfrom pipelines import mail\nprint(json.dumps(mail.country_rule()))")
        profile.save({"outreach_confirmed": True})
        after = w.run("import json\nfrom pipelines import mail\nprint(json.dumps(mail.country_rule()))")
    check("before: not sent", (before.get("rule"), before.get("may_send")), ("confirm", False))
    check("  and it says to check and confirm", "confirm it on Setup" in (before.get("why") or ""), True)
    check("after he confirms: allowed", after.get("may_send"), True)


def test_the_draft_keeps_only_what_is_real():
    print("\n'Draft it from my website' keeps only what is real, and saves nothing:")
    from pipelines import business_draft
    reply = json.dumps({
        "business_name": "Sam Plumbing", "trade": "plumbing", "trade_short": "plumbing",
        "description": "Plumbers.", "country": "GB", "home": "Manchester", "areas": ["Salford"],
        "services": [{"label": "Boiler repair", "also_called": ["boiler service"],
                      "line": "Boilers fixed.", "buyers": ["landlord", "spaceships"]},
                     {"label": ""}],
        "customers": ["care_home", "made_up_kind"]})
    asked = []

    def think(sector, messages, **kw):
        asked.append(messages[0]["content"])
        return {"ok": True, "output": "Here you are: " + reply}

    def read(url):
        return {"ok": True, "url": url, "pages": [url], "text": "We fix boilers in Manchester."}

    before = profile.stored()
    r = business_draft.draft("https://www.example.co.uk", think=think, read=read)
    d = r.get("draft") or {}
    check("it drafts", r.get("ok"), True)
    check("the website's text went to the AI", "We fix boilers" in (asked or [""])[0], True)
    check("a service with no name is dropped", list(d.get("services") or {}), ["boiler_repair"])
    check("a buyer the program cannot find on a map is dropped",
          d["services"]["boiler_repair"]["buyers"], ["landlord"])
    check("customer types are only real ones, and include each service's buyers",
          sorted(d.get("customers") or {}), ["care_home", "landlord"])
    check("home comes first among the areas", d["place"]["served"], ["Manchester", "Salford"])
    check("nothing was saved", profile.stored(), before)
    bad = business_draft.shape({"country": "XX"})
    check("a country code that does not exist is left empty", bad["country"], "")


def test_his_website_keeps_its_starter_when_brought_in():
    print("\nBringing his website into an account keeps the glass starter:")
    with _Website() as w:
        old = w.dir / "program"
        (old / "config").mkdir(parents=True)
        (old / "coordination").mkdir()
        (old / ".env").write_text("SITE_URL=https://www.worlddealtechnicalservices.com\n", encoding="utf-8")
        con = sqlite3.connect(old / "coordination" / "agency.db")
        con.execute("CREATE TABLE t (x)")
        con.commit()
        con.close()
        real = workspace.ROOT
        workspace.ROOT = old
        try:
            site = workspace.add_site(w.acct, "World Deal", "https://www.worlddealtechnicalservices.com")["site"]
            workspace.bring_in_current(w.acct, site["id"])
            workspace.open_site(w.acct, site["id"])
        finally:
            workspace.ROOT = real
        got = w.run("import json\nfrom coordination import profile\nfrom pipelines import services\n"
                    "print(json.dumps({'starter': profile.is_starter(), 'n': len(services.SERVICES)}))")
    check("it is still his glass-and-aluminium website", got.get("starter"), True)
    check("  with all his services", got.get("n"), 20)


def test_country_names_are_quick_and_the_same():
    print("\nCountry names come from the short list, and it agrees with the library:")
    from . import countries
    try:
        import phonenumbers as pn
        from phonenumbers import geocoder
    except Exception:
        pn = None
    if pn:
        differ = [cc for cc, (nm, _code) in countries._FALLBACK.items()
                  if pn.example_number(cc) and geocoder.country_name_for_number(pn.example_number(cc), "en") != nm]
        check("every listed country is named as the library names it", differ, [])
    code = ("import sys; from coordination import countries; countries.name('AE'); "
            "print('phonenumbers.geocoder' in sys.modules)")
    r = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), capture_output=True, text=True, encoding="utf-8")
    check("naming the UAE does not load the place-name tables", r.stdout.strip(), "False")
    check("a country off the list is still named", bool(countries.name("JP")) if pn else True, True)


def main():
    for t in (test_country_names_are_quick_and_the_same,
              test_his_website_is_unchanged, test_a_new_website_starts_blank,
              test_a_plumber_in_manchester, test_a_bakery_in_munich_is_never_written_to_first,
              test_elsewhere_waits_for_his_word, test_the_draft_keeps_only_what_is_real,
              test_his_website_keeps_its_starter_when_brought_in):
        t()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        sys.exit(1)
    print("profile: all good — his website as it was, and any business anywhere its own.")


if __name__ == "__main__":
    main()
