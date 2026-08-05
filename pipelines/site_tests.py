"""
site_tests.py — run: python -m pipelines.site_tests

Tests for reading the owner's own website and working out what he sells.

The failure this guards against is subtle and expensive: an agency that thinks
it sells something the owner doesn't, or misses something he does, will say so
to a customer. So the tests are mostly about the boundary between "we found
this and can target it" and "we found this and cannot" — because the second
list is the one that is worth anything, and it only means something if the
matcher refuses to guess.

Three real bugs from building it are pinned here:
  * the page <title> was being read as a service, so the company's own name
    ended up on the list of things it sells
  * "Windows &amp; Doors" never matched anything, because the entity was left
    undecoded
  * the matcher used the BUYER signals instead of the service NAMES, so
    "Frameless Shower Enclosures" came back as something we don't sell

No network: every test runs against fixture HTML.
"""
import sys

from . import services
from . import site_services as ss

_fail = []


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


SITE = """<html><head><title>Glassworks Dubai | Glass &amp; Aluminium Experts</title></head>
<body>
  <nav>
    <a href="/services/partitions">Office Glass Partitions</a>
    <a href="/services/shower">Frameless Shower Enclosures</a>
    <a href="/services/smart">Smart Switchable Glass</a>
    <a href="/about">About us</a>
    <a href="/contact">Contact</a>
  </nav>
  <h1>Glass &amp; Aluminium Contractors in Dubai</h1>
  <h2>Aluminium Windows &amp; Doors</h2>
  <h2>Stainless Steel Handrails</h2>
  <ul><li>Pergolas and Shades</li><li>ACP Cladding Works</li>
      <li>Frosted Glass Films</li></ul>
  <p>We have been fitting glass, aluminium windows and shower screens across
     Dubai for over ten years, and every job is finished by our own team.</p>
</body></html>"""


def test_only_service_looking_text_is_taken():
    print("What counts as a candidate service:")
    ph = ss.phrases_from(SITE)
    low = [p.lower() for p in ph]

    check("a nav service link is taken", "office glass partitions" in low, True)
    check("a heading is taken", "aluminium windows & doors" in low, True)
    check("a list item is taken", "pergolas and shades" in low, True)

    # the three that must NOT be taken
    check("the page title is not a service",
          any("experts" in p for p in low), False)
    check("body copy is not a service",
          any("over ten years" in p for p in low), False)
    check("a non-trade nav link is not a service", "about us" in low, False)


def test_entities_are_decoded():
    """'Windows &amp; Doors' matched nothing and showed up as a service we
    supposedly do not sell. It is one service, spelled with an ampersand."""
    print("HTML entities:")
    ph = ss.phrases_from(SITE)
    check("the ampersand is real", any("Windows & Doors" in p for p in ph), True)
    check("no raw entity survives", any("&amp;" in p for p in ph), False)


def test_matching_uses_names_not_buyer_signals():
    """
    'hotel' is a SIGNAL for shower glass — it says who needs it. 'shower
    enclosure' is a NAME — it says what it is. Reading our own site needs names.
    """
    print("Names, not signals:")
    check("a service name matches",
          services.match_by_name("Frameless Shower Enclosures")[0], "shower_glass")
    check("and reports which name hit",
          services.match_by_name("Frameless Shower Enclosures")[1], "shower enclosure")
    check("the longest name wins over the generic one",
          services.match_by_name("aluminium door repair")[0], "aluminium_doors")
    check("a buyer word alone is NOT a service name",
          services.match_by_name("Marina Bay Hotel")[0], None)
    check("and neither is unrelated text",
          services.match_by_name("Careers at our company")[0], None)

    # every service must actually be findable by at least one of its own names
    for key, s in services.SERVICES.items():
        hits = [n for n in s["names"] if services.match_by_name(n)[0]]
        check(f"{key} is findable by its own names", bool(hits), True)


def test_reconciliation_splits_three_ways():
    print("Confirmed / uncatalogued / missing:")
    confirmed, uncatalogued = {}, []
    for p in ss.phrases_from(SITE):
        key, _ = services.match_by_name(p)
        (confirmed.setdefault(key, []).append(p) if key else uncatalogued.append(p))

    for want in ("glass_partition", "shower_glass", "aluminium_windows",
                 "glass_railing", "pergola", "cladding"):
        check(f"{want} recognised on the site", want in confirmed, True)

    low = [u.lower() for u in uncatalogued]
    check("smart glass is flagged as untargetable",
          "smart switchable glass" in low, True)
    check("frosted film is flagged as untargetable",
          "frosted glass films" in low, True)
    check("nothing we DO sell landed in the untargetable pile",
          [u for u in low if "partition" in u or "pergola" in u], [])


def test_critic_will_not_certify_an_unread_site():
    print("Critic:")
    ok, why = ss.critic_check({"pages_read": [], "phrases_found": 0})
    check("no page read -> refuse",
          (ok, any("nothing to conclude" in r for r in why)), (False, True))

    ok, why = ss.critic_check({"pages_read": ["/"], "phrases_found": 0})
    check("a page with no trade words at all is suspicious",
          (ok, any("wrong site" in r for r in why)), (False, True))

    ok, _ = ss.critic_check({"pages_read": ["/"], "phrases_found": 12,
                             "confirmed": {"pergola": {}}, "uncatalogued": ["x"]})
    check("a normal read passes", ok, True)

    # everything matching and nothing left over means the matcher is too loose
    everything = {k: {} for k in services.SERVICES}
    ok, why = ss.critic_check({"pages_read": ["/"], "phrases_found": 20,
                               "confirmed": everything, "uncatalogued": []})
    check("a suspiciously perfect result is rejected",
          (ok, any("suspiciously tidy" in r for r in why)), (False, True))


def test_no_site_configured():
    print("No website set:")
    d = ss.discover("")
    check("it refuses rather than guessing", d["ok"], False)
    check("and says how to fix it", "/setup" in d["error"], True)


def test_only_our_own_site_is_followed():
    """A competitor's link on our page is not our page."""
    print("It reads our site and nothing else:")
    html = ('<a href="/services/glass">Ours</a>'
            '<a href="https://competitor.ae/services">Theirs</a>'
            '<a href="https://facebook.com/us">Social</a>')
    links = ss._internal_links(html, "https://glassworks.ae")
    check("our own services page is followed",
          links, ["https://glassworks.ae/services/glass"])
    check("the competitor is not followed",
          any("competitor" in l for l in links), False)


def main():
    test_only_service_looking_text_is_taken()
    test_entities_are_decoded()
    test_matching_uses_names_not_buyer_signals()
    test_reconciliation_splits_three_ways()
    test_critic_will_not_certify_an_unread_site()
    test_no_site_configured()
    test_only_our_own_site_is_followed()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: {_fail}")
        sys.exit(1)
    print("all site tests passed")


if __name__ == "__main__":
    main()
