"""
find_site_tests.py — run: python3 -m pipelines.find_site_tests

Guessing a business's website from its name, and — the part that matters —
refusing to guess when a wrong answer would mail a stranger.

WHAT THIS IS PROTECTING

A signage search in Dubai returned 49 businesses worth having and 7 with any
way to reach them. The other 42 had a name and an address. Most of them DO
have a website; nobody typed it into OpenStreetMap.

The failure mode here is not "found nothing". It is finding the WRONG site,
writing to a stranger about a signage business they have never run, and
burning the sending domain every other lead in the cabinet depends on. So most
of these tests are about the refusals, not the hits.

Nothing here touches the network — the fetcher is replaced throughout.
"""
import sys

from . import find_site as F

_fail = []


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


class _Web:
    """The internet, as a dict of {url: html}. Everything else 404s."""

    def __init__(self, pages):
        self.pages, self.asked = pages, []

    def __call__(self, url, timeout=12, allow_paid=False, **kw):
        self.asked.append(url)
        if url in self.pages:
            return {"ok": True, "html": self.pages[url], "how": "plain",
                    "cost": 0.0}
        return {"ok": False, "error": "does not resolve — no such host"}


def _with_web(web, fn):
    real = F.fetcher.fetch
    F.fetcher.fetch = web
    try:
        return fn()
    finally:
        F.fetcher.fetch = real


def _page(title, body=""):
    return f"<html><head><title>{title}</title></head><body>{body}</body></html>"


# ---------------------------------------------------------------------------

def test_it_strips_what_nobody_puts_in_a_domain():
    print("\nA UAE company name is mostly boilerplate:")
    check("the legal form goes", F.tokens("Daffodils Advertising LLC"),
          ["daffodils", "advertising"])
    check("so does the emirate, and the district inside it",
          F.tokens("Al Quoz Glass Trading Dubai"), ["glass"])
    check("and 'Al', which is in front of half of Dubai",
          F.tokens("Al Furgen"), ["furgen"])

    print("\n...and the trade word identifies nobody:")
    check("Daffodils Advertising -> daffodils",
          F.distinctive("Daffodils Advertising LLC"), ["daffodils"])
    check("Bin Halber Real Estate -> halber",
          F.distinctive("Bin Halber Real Estate"), ["halber"])
    check("Economical Printing Press -> economical",
          F.distinctive("Economical Printing Press LLC"), ["economical"])


def test_it_refuses_to_guess_from_a_name_that_identifies_nobody():
    """
    'Typing & Photocopying' is a description, not a name. Guessing a domain
    from it means picking a stranger and calling them a lead.
    """
    print("\nSome names are not names:")
    for nm in ("Typing & Photocopying", "Typing Documents Clearing",
               "HBC Printer Supplies"):
        check(f"{nm!r} -> no guess", F.candidate_domains(nm), [])
        r = F.find(nm)
        check(f"{nm!r} -> refused before a single fetch", r["tried"], [])
        check(f"{nm!r} -> and says why",
              "no distinctive word" in r["why"], True)


def test_the_domains_it_tries_are_the_ones_uae_firms_register():
    print("\nUAE first, and the short forms too:")
    d = F.candidate_domains("Star Bay Print Centre LLC")
    check(".ae is tried before .com", d.index("starbayprintcentre.ae")
          < d.index("starbayprintcentre.com"), True)
    check("the two-word short form is tried — it is what they actually use",
          "starbay.ae" in d, True)
    print("  ...and .ae on every stem before .com on any of them, or the")
    print("     first stem eats the whole budget:")
    check("every .ae comes first",
          all(x.endswith(".ae") for x in d[:d.index("starbayprintcentre.com")]),
          True)


def test_a_page_has_to_prove_it_belongs_to_this_business():
    print("\nThe whole name run together is the strongest evidence:")
    ok, why = F.page_is_theirs(_page("Jaguar Printing Press LLC",
                                     "Jaguar Printing Press, Dubai"),
                               "Jaguar Printing Press LLC")
    check("accepted", ok, True)
    check("and it says what convinced it", "runs together" in why, True)

    print("\n...a parked domain is refused however well the name matches:")
    ok, why = F.page_is_theirs(
        _page("daffodils.com", "This domain is for sale. Buy this domain. "
                               "Daffodils Advertising"),
        "Daffodils Advertising LLC")
    check("refused", ok, False)
    check("as a parked page", "parked" in why, True)

    print("\n...a page that never says the name is somebody else's:")
    ok, why = F.page_is_theirs(_page("Acme Printing", "We print things"),
                               "Daffodils Advertising LLC")
    check("refused", ok, False)
    check("and names the word it wanted", "daffodils" in why, True)

    print("\n...one SHORT word matching is a coincidence, not proof:")
    ok, why = F.page_is_theirs(_page("Star Corp", "Star Corp of Ohio"),
                               "Star Bay Print Centre LLC")
    check("refused", ok, False)
    check("and says so plainly", "coincidence" in why, True)

    print("\n...and one long word alone still needs corroboration:")
    us = _page("Daisytek", "Daisytek International, Allen, Texas")
    ok, why = F.page_is_theirs(us, "Daisytek", hints=("Dubai", "signage"))
    check("a US firm of the same name is refused", ok, False)
    check("because the page never mentions Dubai",
          "never mentions" in why, True)
    ae = _page("Daisytek", "Daisytek — signage and print, Dubai UAE")
    ok, why = F.page_is_theirs(ae, "Daisytek", hints=("Dubai", "signage"))
    check("the Dubai one is accepted", ok, True)
    check("naming the corroboration", "Dubai" in why, True)

    print("\n...two distinctive words is enough on its own:")
    ok, why = F.page_is_theirs(_page("Silver Finder", "silver finder realty"),
                               "Silver Home Finder")
    check("accepted", ok, True)


def test_end_to_end_it_finds_the_real_one_and_walks_past_the_others():
    print("\nWalking the candidates in order until one proves itself:")
    web = _Web({
        # the .ae does not exist; the .com is theirs
        "https://jaguarprintingpress.com/":
            _page("Jaguar Printing Press LLC",
                  "Jaguar Printing Press LLC, Al Quoz, Dubai. "
                  "info@jaguarpp.com"),
    })
    r = _with_web(web, lambda: F.find("Jaguar Printing Press LLC"))
    check("found", r["ok"], True)
    check("the right domain", r["domain"], "jaguarprintingpress.com")
    check("it tried the .ae first and said what happened",
          web.asked[0], "https://jaguarprintingpress.ae/")
    check("and it stopped as soon as it was sure",
          web.asked[-1], "https://jaguarprintingpress.com/")

    print("\n...and a same-name stranger is walked past, not taken:")
    web = _Web({"https://daffodils.com/":
                _page("Daffodils Flowers", "Daffodils, a florist in Kent")})
    r = _with_web(web, lambda: F.find("Daffodils Advertising LLC",
                                      hints=("Dubai", "signage")))
    check("not found", r["ok"], False)
    check("the stranger was seen and rejected, not skipped",
          any(t["domain"] == "daffodils.com" for t in r["tried"]), True)


def test_it_only_spends_a_fetch_where_it_could_help():
    print("\nA lead we can already reach gains nothing from a guess:")
    cands = [
        {"company": "Has A Site", "website": "https://x.ae", "email": ""},
        {"company": "Has An Email", "website": "", "email": "a@b.ae"},
        {"company": "Daffodils Advertising LLC", "website": "", "email": "",
         "area": "Dubai", "segment": "signage"},
    ]
    web = _Web({"https://daffodilsadvertising.ae/":
                _page("Daffodils Advertising",
                      "Daffodils Advertising LLC, Dubai. Signage and print.")})
    out = _with_web(web, lambda: F.fill_in(cands))
    check("only the one with nothing was looked up", out["looked_for"], 1)
    check("and it was found", out["sites_found"], 1)
    check("the lead now has a site",
          cands[2]["website"], "https://daffodilsadvertising.ae/")
    check("with the evidence attached, so it is arguable later",
          "daffodils" in cands[2]["website_why"].lower(), True)
    check("the others were not touched", cands[0]["website"], "https://x.ae")

    print("\n...the too-generic ones are reported, not silently skipped:")
    out = _with_web(_Web({}), lambda: F.fill_in(
        [{"company": "Typing & Photocopying", "website": "", "email": ""}]))
    check("counted as too generic", out["too_generic_to_guess"],
          ["Typing & Photocopying"])
    check("and not counted as a miss", out["no_site_found"], [])

    print("\n...and one pass will not try four hundred domains:")
    many = [{"company": f"Kainatt{i} Properties", "website": "", "email": ""}
            for i in range(40)]
    web = _Web({})
    out = _with_web(web, lambda: F.fill_in(many, limit=3))
    check("capped at three businesses", out["looked_for"], 3)
    check("the rest are counted", out["not_reached_this_pass"], 37)


def main():
    for t in (test_it_strips_what_nobody_puts_in_a_domain,
              test_it_refuses_to_guess_from_a_name_that_identifies_nobody,
              test_the_domains_it_tries_are_the_ones_uae_firms_register,
              test_a_page_has_to_prove_it_belongs_to_this_business,
              test_end_to_end_it_finds_the_real_one_and_walks_past_the_others,
              test_it_only_spends_a_fetch_where_it_could_help):
        t()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        sys.exit(1)
    print("all find_site tests passed")


if __name__ == "__main__":
    main()
