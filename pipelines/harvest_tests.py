"""
harvest_tests.py — run: python3 -m pipelines.harvest_tests

Covers the contact harvester, against pages held in this file rather than the
open internet. Every one of these cases is a real shape found on a real UAE
small-business site, and three of them are bugs the first version shipped with
and lost leads to.

No network. Nothing touches the cabinet.
"""
import sys

from . import harvest as H

_fail = []


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


def vals(rows, key="value"):
    return [r[key] for r in rows]


# ---------------------------------------------------------------------------
# ONE NUMBER, HOWEVER IT IS WRITTEN
# ---------------------------------------------------------------------------

def test_a_number_written_five_ways_is_one_number():
    print("\nThe same UAE number, however the site chose to print it:")
    same = ["+971 56 510 0471", "00971565100471", "0565100471",
            "+971-56-510-0471", "(056) 510 0471", "971565100471"]
    check("all six normalise to one",
          sorted({H.normalise_phone(s) for s in same}), ["+971565100471"])
    check("a landline too",
          H.normalise_phone("04-385 1122"), "+97143851122")
    check("and it knows which is which",
          [H.phone_kind(H.normalise_phone(x)) for x in ("0565100471", "04 385 1122")],
          ["mobile", "landline"])

    print("\n...and things that are not phone numbers are not phone numbers:")
    for junk in ("", "PO Box 12345", "1234", "2024", "AED 4,500",
                 "+1 415 555 0100", "Licence 776543"):
        check(f"{junk!r} is not a number", H.normalise_phone(junk), "")


def test_the_brand_survives_a_change_of_tld():
    """
    The bug that lost the very first lead this was tested on.

    The owner's own site is on .com and publishes info@…​.ae. The 'is this
    address theirs?' test compared whole hostnames, decided the business's own
    published address belonged to a stranger, and dropped it. UAE firms split
    site and mail across .com and .ae constantly.
    """
    print("\nA .com site and a .ae mailbox are the same business:")
    check("www stripped and suffix stripped",
          H.brand("www.worlddealtechnicalservices.com"), "worlddealtechnicalservices")
    check("the .ae one matches it",
          H.brand("worlddealtechnicalservices.ae"), "worlddealtechnicalservices")
    check("multi-part suffixes too", H.brand("shop.acmeglass.co.ae"), "acmeglass")
    rows = [{"value": "info@acmeglass.ae", "how": "mailto link"},
            {"value": "hello@somewebagency.com", "how": "printed on the page"}]
    kept = vals(H.rank_emails(rows, "www.acmeglass.com"))
    check("theirs is kept", "info@acmeglass.ae" in kept, True)
    check("the web agency that built the site is not",
          "hello@somewebagency.com" in kept, False)


def test_the_role_inbox_outranks_the_person():
    print("\nBest address first:")
    rows = [{"value": "ahmed.khan@acmeglass.ae", "how": "printed on the page"},
            {"value": "info@acmeglass.ae", "how": "mailto link"},
            {"value": "acmeglass@gmail.com", "how": "printed on the page"}]
    order = vals(H.rank_emails(rows, "acmeglass.ae"))
    check("role inbox on their own domain leads", order[0], "info@acmeglass.ae")
    check("a public mailbox they published sorts last",
          order[-1], "acmeglass@gmail.com")


# ---------------------------------------------------------------------------
# READING A PAGE
# ---------------------------------------------------------------------------

SPACED_TEL = """
<html><head><title>Al Noor Cafeteria &amp; Juice — Al Quoz</title></head><body>
  <a href="tel:+971 4 385 1122">Call us</a>
  <a href="mailto:info@alnoor.ae?subject=Enquiry">Write to us</a>
  <a href="https://wa.me/971565100471">WhatsApp</a>
  <a href="https://www.facebook.com/">Facebook</a>
  <a href="https://instagram.com/alnoorcafeteria">Instagram</a>
</body></html>
"""


def test_a_number_spaced_for_humans_is_still_read():
    """
    The second shipped bug. `tel:+971 4 385 1122` is how half the UAE writes
    it, and a regex that stopped at whitespace harvested the string '+971' —
    which normalises to nothing. The phone silently vanished on exactly the
    sites that had bothered to publish one.
    """
    print("\nA tel: link with spaces in it:")
    got = H.read_page("https://alnoor.ae/", SPACED_TEL)
    check("the whole number is read", vals(got["phones"]), ["+97143851122"])
    check("the address survives its ?subject= too",
          vals(got["emails"]), ["info@alnoor.ae"])
    check("and it is recorded as a mailto, not a guess",
          got["emails"][0]["how"], "mailto link")


def test_a_placeholder_social_link_is_not_a_profile():
    """Third shipped bug: every template footer links a bare facebook.com."""
    print("\nA footer link that points at nothing:")
    got = H.read_page("https://alnoor.ae/", SPACED_TEL)
    check("only the real profile is kept",
          vals(got["socials"]), ["https://instagram.com/alnoorcafeteria"])


def test_html_entities_do_not_reach_the_lead_list():
    print("\nThe business name as a person would read it:")
    got = H.read_page("https://alnoor.ae/", SPACED_TEL)
    check("no &amp; in the name", "&amp;" in got["name"], False)
    check("the name itself", got["name"], "Al Noor Cafeteria & Juice — Al Quoz")


SCHEMA = """
<html><head>
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"LocalBusiness","name":"Marina Dental Clinic",
 "telephone":"+971 4 555 9000","email":"reception@marinadental.ae",
 "address":{"@type":"PostalAddress","streetAddress":"Unit 4, Marina Plaza",
            "addressLocality":"Dubai Marina","addressCountry":"AE"},
 "employee":{"@type":"Person","name":"Dr Layla Haddad","jobTitle":"Practice Manager"}}
</script></head><body><p>Open Sunday to Thursday.</p></body></html>
"""


def test_structured_data_is_read_and_prose_is_not():
    print("\nSchema.org is read; a name in prose is never invented:")
    got = H.read_page("https://marinadental.ae/", SCHEMA)
    check("the business name", got["name"], "Marina Dental Clinic")
    check("the number", vals(got["phones"]), ["+97145559000"])
    check("the address", got["address"],
          "Unit 4, Marina Plaza, Dubai Marina, AE")
    check("the person they published", vals(got["people"], "name"), ["Dr Layla Haddad"])
    check("with their role", got["people"][0]["role"], "Practice Manager")

    prose = ("<html><body><p>Ask for Mr Rashid at the counter, or speak to "
             "Fatima on reception.</p></body></html>")
    check("no JSON-LD means no name — a person is not guessed out of a sentence",
          H.read_page("https://x.ae/", prose)["people"], [])


# ---------------------------------------------------------------------------
# WHATSAPP — an invitation is not the same as a number we found
# ---------------------------------------------------------------------------

def test_a_chat_button_is_evidence_and_a_mobile_is_not():
    print("\nWhatsApp: what they invited, and what we merely found:")
    page = ("""<a href="https://api.whatsapp.com/send?phone=971501234567&text=Hi">"""
            """Chat</a><a href="tel:0565100471">Call</a>""")
    got = H.read_page("https://x.ae/", page)
    check("the click-to-chat number is taken", vals(got["whatsapp"]), ["+971501234567"])
    check("and it says the business published the button",
          "click-to-chat" in got["whatsapp"][0]["how"], True)
    check("the other mobile is a phone, not a chat number",
          vals(got["phones"]), ["+971565100471"])

    print("\n...and a group invite is never a contact:")
    g = H.read_page("https://x.ae/", '<a href="https://chat.whatsapp.com/AbCdEf123456">Join</a>')
    check("no chat number claimed", g["whatsapp"], [])
    check("but the invite is visible rather than dropped", len(g["groups"]), 1)
    check("labelled for what it is",
          "not a contact" in g["groups"][0]["how"], True)


# ---------------------------------------------------------------------------
# WHICH PAGE TO READ NEXT
# ---------------------------------------------------------------------------

NAV = """
<html><body>
 <a href="/">Home</a>
 <a href="/services.php">Our Services</a>
 <a href="/contact.php">Contact Us</a>
 <a href="https://other-site.com/contact">Our partner</a>
 <a href="/gallery.php">Gallery</a>
</body></html>
"""


def test_the_site_says_where_its_contact_page_is():
    """
    The guesser tried eight fixed paths. The owner's own site calls the page
    /contact.php, which is on none of them — so the crawler that was supposed
    to find contact details would have missed them on this very business.
    """
    print("\nFollow the site's own link rather than guessing a path:")
    ls = H.links(NAV, "https://acme.ae/")
    check("off-host links are not followed",
          [u for u, _ in ls if "other-site" in u], [])
    ranked = sorted(((H.worth_a_click(u, t), u) for u, t in ls), reverse=True)
    check("'Contact Us' is the first click", ranked[0][1], "https://acme.ae/contact.php")
    check("the gallery is not worth a click",
          H.worth_a_click("https://acme.ae/gallery.php", "Gallery"), 0)
    check("nor is the home page it came from",
          H.worth_a_click("https://acme.ae/", "Home"), 0)

    print("\n...and the same page in Arabic is not a second page:")
    # Caught on the first real run: a UAE site linked /uae/contact-us and
    # /ar/uae/contact-us, and the crawler spent a page of its budget — on a
    # big site, a metered render — reading the same details in Arabic.
    check("the locale prefix is not part of the page",
          H.page_identity("https://x.ae/ar/uae/contact-us"),
          H.page_identity("https://x.ae/uae/contact-us"))
    check("nor is a trailing slash",
          H.page_identity("https://x.ae/contact/"), H.page_identity("https://x.ae/contact"))
    check("nor the extension the server happens to use",
          H.page_identity("https://x.ae/contact.php"), H.page_identity("https://x.ae/contact"))
    check("but two different pages stay different",
          H.page_identity("https://x.ae/contact") == H.page_identity("https://x.ae/about"),
          False)
    check("and 'article' is not the 'ar' locale",
          H.page_identity("https://x.ae/article/glass"), "x.ae/article/glass")


def test_it_reads_a_whole_site_without_touching_the_network():
    """
    The crawl end to end, with the fetcher replaced. Proves the loop follows
    the link, merges both pages, and stops — without asking the internet.
    """
    print("\nThe whole crawl, on a two-page site:")
    pages = {
        "https://acme.ae": NAV,
        "https://acme.ae/contact.php":
            '<a href="mailto:info@acme.ae">Mail</a>'
            '<a href="tel:+971 4 385 1122">Call</a>'
            '<a href="https://wa.me/971565100471">WhatsApp</a>',
    }
    asked = []

    def fake_fetch(url, **kw):
        asked.append(url)
        html = pages.get(url.rstrip("/")) or pages.get(url)
        return ({"ok": True, "html": html, "how": "plain", "cost": 0.0, "error": ""}
                if html else
                {"ok": False, "html": "", "how": "plain", "cost": 0.0,
                 "error": "404"})

    real, H.fetcher.fetch = H.fetcher.fetch, fake_fetch
    try:
        r = H.harvest("acme.ae", pause=0)
    finally:
        H.fetcher.fetch = real

    check("it read the home page first", asked[0], "https://acme.ae")
    check("then the page the site pointed at",
          "https://acme.ae/contact.php" in asked, True)
    check("it did not go wandering", len(asked) <= H.MAX_PAGES, True)
    check("the address", vals(r["emails"]), ["info@acme.ae"])
    check("the number", vals(r["phones"]), ["+97143851122"])
    check("the chat button", vals(r["whatsapp"]), ["+971565100471"])
    check("and it says how it got there",
          "linked from" in r["pages"][1]["why"], True)
    check("nothing was spent", r["spend"], 0.0)
    check("the headline is readable",
          r["head"], "read 2 page(s), found 1 address(es), 1 number(s), a chat button")


def test_a_site_that_publishes_nothing_says_so():
    print("\nA business with no published contact route is an honest outcome:")
    def fake_fetch(url, **kw):
        return {"ok": True, "how": "plain", "cost": 0.0, "error": "",
                "html": "<html><body><h1>Coming soon</h1></body></html>"}
    real, H.fetcher.fetch = H.fetcher.fetch, fake_fetch
    try:
        r = H.harvest("https://nothing.ae", pause=0)
    finally:
        H.fetcher.fetch = real
    check("no address invented", r["emails"], [])
    check("no number invented", r["phones"], [])
    check("and it says as much",
          "published no way of reaching them" in r["head"], True)

    print("\n...and a site that cannot be read is not a site with no details:")
    def dead(url, **kw):
        return {"ok": False, "html": "", "how": "plain", "cost": 0.0,
                "error": "connection refused"}
    real, H.fetcher.fetch = H.fetcher.fetch, dead
    try:
        r = H.harvest("https://gone.ae", pause=0)
    finally:
        H.fetcher.fetch = real
    check("it reports a failure, not an empty business", r["ok"], False)
    check("with the reason", "connection refused" in (r["notes"] or [""])[0], True)


# ---------------------------------------------------------------------------
# THE WHATSAPP GATE
# ---------------------------------------------------------------------------

def _wa_cabinet():
    import sqlite3
    import tempfile
    from pathlib import Path
    from coordination import board
    d = Path(tempfile.mkdtemp())
    db = d / "agency.db"
    con = sqlite3.connect(db)
    con.executescript((Path(__file__).parents[1] / "coordination" / "schema.sql").read_text(encoding="utf-8"))
    con.commit()
    con.close()
    board.DB = db
    board.ensure_schema()
    return board, db


def test_only_a_published_chat_button_gets_on_the_list():
    """
    The line that matters. Finding a mobile number is not being invited to
    message it; a wa.me button the business put on its own website is.
    """
    from . import whatsapp as W
    print("\nWho may be messaged on WhatsApp:")
    board, db = _wa_cabinet()
    invited = board.add_lead_if_new({
        "company": "Al Noor Cafeteria", "area": "Al Quoz", "segment": "food_stall",
        "phone": "+97143851122", "whatsapp": "+971565100471",
        "website": "https://alnoor.ae"})
    board.add_lead_if_new({
        "company": "Marina Dental", "area": "Dubai Marina", "segment": "clinic_small",
        "phone": "+971501112233"})          # a mobile, but no button
    r = W.ready()
    check("the one that invited contact is on the list",
          [x["id"] for x in r["ready"]], [invited])
    check("the one that merely has a mobile is not",
          any("did not publish a chat button" in x["why"] for x in r["not_eligible"]),
          True)

    print("\n...and a business already approached is not approached again:")
    board.log_outreach(invited, "hello", channel="email") \
        if hasattr(board, "log_outreach") else None
    import sqlite3
    con = sqlite3.connect(db)
    con.execute("INSERT INTO outreach_log(lead_id,channel,direction,body,at)"
                " VALUES(?,'email','sent','hi',strftime('%s','now'))", (invited,))
    con.commit()
    con.close()
    r = W.ready()
    check("mailed yesterday means not messaged today", r["ready"], [])
    check("and it says why",
          any("per business, not per channel" in x["why"] for x in r["not_eligible"]),
          True)


def test_the_cap_is_real_and_separate_from_email():
    from . import whatsapp as W
    print("\nThe chat cap is its own cap, and tighter:")
    board, db = _wa_cabinet()
    for i in range(4):
        board.add_lead_if_new({"company": f"Shop {i}", "whatsapp": f"+97150000000{i}"})
    import os
    was = os.environ.get("WHATSAPP_DAILY_CAP")
    os.environ["WHATSAPP_DAILY_CAP"] = "2"
    try:
        r = W.ready()
        check("only today's room is offered", len(r["ready"]), 2)
        check("the rest are held, not dropped", len(r["held_back"]), 2)
        check("and the head says so", "2 of today's 2 left" in r["head"], True)
    finally:
        if was is None:
            os.environ.pop("WHATSAPP_DAILY_CAP", None)
        else:
            os.environ["WHATSAPP_DAILY_CAP"] = was
    check("the default is below the email cap",
          W.DEFAULT_DAILY_CAP < 20, True)


def test_the_link_opens_a_chat_and_does_not_send_one():
    from . import whatsapp as W
    print("\nThe link prepares a message; the owner presses send:")
    link = W.chat_link("+971565100471", "Hello there")
    check("wa.me, WhatsApp's own format",
          link.startswith("https://wa.me/971565100471?text="), True)
    check("the text is prefilled, not delivered", "Hello%20there" in link, True)
    check("a number that is not a number gets no link", W.chat_link("nonsense"), "")
    line = W.opening({"company": "Al Noor", "area": "Al Quoz"}, "glass shopfronts")
    check("it says where we got the number",
          "saw the WhatsApp button on your website" in line, True)
    check("and it quotes no price",
          any(w in line.lower() for w in ("aed", "price", "%", "offer")), False)


def main():
    for t in (test_a_number_written_five_ways_is_one_number,
              test_the_brand_survives_a_change_of_tld,
              test_the_role_inbox_outranks_the_person,
              test_a_number_spaced_for_humans_is_still_read,
              test_a_placeholder_social_link_is_not_a_profile,
              test_html_entities_do_not_reach_the_lead_list,
              test_structured_data_is_read_and_prose_is_not,
              test_a_chat_button_is_evidence_and_a_mobile_is_not,
              test_the_site_says_where_its_contact_page_is,
              test_it_reads_a_whole_site_without_touching_the_network,
              test_a_site_that_publishes_nothing_says_so,
              test_only_a_published_chat_button_gets_on_the_list,
              test_the_cap_is_real_and_separate_from_email,
              test_the_link_opens_a_chat_and_does_not_send_one):
        t()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        sys.exit(1)
    print("all harvest tests passed")


if __name__ == "__main__":
    main()
