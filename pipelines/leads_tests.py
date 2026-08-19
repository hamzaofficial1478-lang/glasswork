"""
leads_tests.py — run: python -m pipelines.leads_tests   (python3 on macOS/Linux)

Leads is the sector where a bug reaches a real person, so the tests aim at the
failures that actually cost something:

  * collecting the same firm twice        (burns the relationship and the domain)
  * contacting anyone before Security clears them
  * inventing an email address that was never published   (bounces, then blacklist)
  * pitching the wrong service, or reciting the whole catalogue at someone
  * quietly narrowing the target list back down to a handful of segments

That last one is a real regression, not a hypothetical. This sector shipped with
five contractor segments and threw away every hotel, clinic, salon, restaurant
and school in the country, so there is a test that fails if the list ever
shrinks back.

Everything runs against a throwaway cabinet; the real one is never touched.
"""
import os
import sqlite3
import sys
import tempfile
import threading
from pathlib import Path

from coordination import board
from . import leads_hunt as lh
from . import leads_sources as ls
from . import mail
from . import services

_fail = []


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


def _fresh_db():
    d = Path(tempfile.mkdtemp())
    db = d / "agency.db"
    con = sqlite3.connect(db)
    con.executescript((Path(__file__).parents[1] / "coordination" / "schema.sql").read_text())
    con.commit()
    con.close()
    return db


BIZ = {"company": "Marina Fitout LLC", "segment": "fitout_contractor",
       "area": "Dubai Marina", "email": "info@marinafitout.ae", "source": "Dubai Chamber"}


# ---------------------------------------------------------------------------
# the market we are allowed to sell to
# ---------------------------------------------------------------------------

def test_the_target_list_is_the_whole_market():
    """
    The regression that matters. Five contractor segments meant a hotel, a
    clinic, a salon and a school were all out of scope — for a firm that fits
    glass into every building in the UAE. If someone trims this list again, this
    test is what tells them.
    """
    print("The target list covers the market:")
    segs = set(ls.SEGMENTS)
    check("there are at least twenty segments", len(segs) >= 20, True)
    for must in ("hotel", "hospital", "school", "restaurant", "salon", "gym",
                 "retail", "mall", "office", "warehouse", "real_estate_agency",
                 "property_management", "showroom", "bank", "startup"):
        check(f"{must} is a target", must in segs, True)
    check("every segment says why they buy",
          all(v.get("why") for v in ls.SEGMENTS.values()), True)
    check("every segment has search terms to find them",
          all(v.get("queries") for v in ls.SEGMENTS.values()), True)
    check("all three emirates are covered",
          all(any(a in d for d in ls.DISTRICTS)
              for a in ("Dubai", "Abu Dhabi", "Sharjah")), True)


def test_every_segment_knows_what_to_sell_it():
    print("Every segment has a service to pitch:")
    m = ls.build_source_map()
    check("no segment is left with nothing to sell",
          [k for k, v in m["segments"].items() if not v["services_they_need"]], [])
    check("no segment is left with no source",
          [k for k, v in m["segments"].items() if not v["source_count"]], [])
    hotel = m["segments"]["hotel"]["services_they_need"]
    check("hotels are pitched shower glass", "frameless shower glass" in hotel, True)
    check("hotels are not pitched a random single line", len(hotel) >= 3, True)


# ---------------------------------------------------------------------------
# service-based targeting
# ---------------------------------------------------------------------------

def test_service_matching():
    """One business, one job. The mail is only as good as this match."""
    print("Which service does this business need:")
    cases = [
        ("Marina Bay Hotel", "hotel", "hotel", "shower_glass"),
        ("Glow Beauty Salon", "beauty salon", "salon", "mirror"),
        ("Al Noor Warehouse & Logistics", "warehouse", "warehouse", "aluminium_doors"),
        ("Prime Facility Services", "facility management", "facility_management", "glass_repair"),
        ("Nova Interiors", "interior design company", "interior_design", "glass_partition"),
        ("Skyline Developers", "real estate developer", "developer", "glass_railing"),
    ]
    for name, cat, seg, want in cases:
        got, _ = services.match_service(name, cat, segment=seg)
        check(f"{seg} -> {want}", got, want)

    # a substring must not decide what we sell. 'small' is not a mall, and
    # 'restore' is not a store — the same trap the SEO scope check fell into.
    got, _ = services.match_service("Al Salam Small Business Centre",
                                    "business centre", segment="office")
    check("'small' is not a shopping mall", got, "glass_partition")
    got, _ = services.match_service("Gulf Restore Trading", "trading", segment="warehouse")
    check("'restore' is not a storefront", got != "shopfront", True)

    # a prefix signal still has to work
    got, _ = services.match_service("Green Landscaping LLC", "landscaping",
                                    segment="villa_compound")
    check("'landscap' still catches landscaping", got, "glass_railing")


def test_related_services_are_named():
    print("Related services ride along:")
    for key in services.SERVICES:
        rel = services.related_labels(key)
        check(f"{key} names others", len(rel) >= 3, True)
        check(f"{key} does not list itself", services.label(key) in rel, False)
    check("the full service list is 14 long", len(services.ALL_SERVICE_LABELS), 14)


# ---------------------------------------------------------------------------
# the screens
# ---------------------------------------------------------------------------

def test_private_individuals_are_the_owner_s_call():
    """
    Not a wall — a switch. The default is off because cold-mailing a named
    person at home is the one case UAE rules bite on, but the owner can open it.
    """
    print("Private individuals (owner's switch):")
    was = os.environ.pop("LEADS_ALLOW_INDIVIDUALS", None)
    try:
        ok, why = ls.screen_lead({**BIZ, "segment": "private_individual"})
        check("off by default", (ok, "LEADS_ALLOW_INDIVIDUALS" in why), (False, True))

        os.environ["LEADS_ALLOW_INDIVIDUALS"] = "1"
        ok, _ = ls.screen_lead({**BIZ, "segment": "private_individual"})
        check("the owner can turn it on", ok, True)
        m = ls.build_source_map()
        check("and the classifieds source becomes usable",
              any("Classifieds" in s["name"] for s in m["usable_sources"]), True)
    finally:
        os.environ.pop("LEADS_ALLOW_INDIVIDUALS", None)
        if was is not None:
            os.environ["LEADS_ALLOW_INDIVIDUALS"] = was

    m = ls.build_source_map()
    check("and it is refused again once the switch is off",
          [r["name"] for r in m["refused_sources"]], ["Classifieds villa-owner listings"])


def test_scope_screens():
    print("Scope screens:")
    ok, why = ls.screen_lead({**BIZ, "area": "London", "company": "London Glass Ltd",
                              "email": "a@b.co.uk"})
    check("outside the UAE refused", (ok, "UAE" in why), (False, True))

    # the wide relevance list is the point: these all occupy buildings
    for company, seg in [("Gulf Catering Co", "restaurant"),
                         ("Bright Smile Dental Clinic", "hospital"),
                         ("Cut & Style Barber Shop", "salon"),
                         ("Falcon Logistics Warehouse", "warehouse")]:
        ok, _ = ls.screen_lead({"company": company, "area": "Dubai",
                                "segment": seg, "email": "a@b.ae"})
        check(f"{company} is in scope now", ok, True)

    ok, why = ls.screen_lead({**BIZ, "email": "", "phone": ""})
    check("no contact route refused", (ok, "contact route" in why), (False, True))
    ok, _ = ls.screen_lead({**BIZ, "company": ""})
    check("no company name refused", ok, False)
    ok, _ = ls.screen_lead(BIZ)
    check("a clean lead passes", ok, True)


# ---------------------------------------------------------------------------
# collection
# ---------------------------------------------------------------------------

PLACE = {
    "displayName": {"text": "Marina Bay Hotel"},
    "formattedAddress": "Dubai Marina, Dubai, UAE",
    "internationalPhoneNumber": "+971 4 123 4567",
    "websiteUri": "https://marinabayhotel.ae",
    "primaryTypeDisplayName": {"text": "Hotel"},
    "types": ["lodging", "hotel"],
    "businessStatus": "OPERATIONAL",
    "userRatingCount": 812,
}


def test_a_place_becomes_a_usable_lead():
    print("A map listing becomes a lead:")
    c = lh._place_to_candidate(PLACE, "hotel", "Dubai Marina")
    check("company name carried over", c["company"], "Marina Bay Hotel")
    check("phone carried over", c["phone"], "+971 4 123 4567")
    check("website carried over", c["website"], "https://marinabayhotel.ae")
    check("area is where the firm is", c["area"], "Dubai Marina")
    check("the service is worked out", c["service"], "shower_glass")
    check("and it says why", "hotel" in c["service_reason"], True)
    check("no email is invented from the listing", c["email"], "")
    ok, _ = ls.screen_lead(c)
    check("and it passes the screen", ok, True)


def test_emails_are_read_not_guessed():
    """
    A guessed info@ bounces, and bounces are what get an outreach domain
    blacklisted. Every address must come from a page we actually read.
    """
    print("Emails are read off their site, never guessed:")
    page = """<html><body>
      <a href="mailto:reservations@marinabayhotel.ae">Book</a>
      Careers: ahmed.khan@marinabayhotel.ae · General: info@marinabayhotel.ae
      Built by <a href="mailto:hello@somewebagency.com">Some Web Agency</a>
      <img src="logo@2x.png"> noreply@marinabayhotel.ae
    </body></html>"""
    got = lh.rank_emails(lh.EMAIL_RE.findall(page)
                         + [m.split("?")[0] for m in
                            __import__("re").findall(r'mailto:([^"\'>\s]+)', page)],
                         "marinabayhotel.ae")
    check("the role inbox on their own domain wins", got[0], "info@marinabayhotel.ae")
    check("noreply is thrown out", any("noreply" in a for a in got), False)
    check("the web agency's address is not our lead",
          any("somewebagency" in a for a in got), False)
    check("an image filename is not an email", any(".png" in a for a in got), False)
    check("the named person is kept, but behind the role inbox",
          got.index("ahmed.khan@marinabayhotel.ae") > got.index("info@marinabayhotel.ae"),
          True)

    saved = lh._get
    lh._get = lambda url, timeout=12: ""      # site unreachable
    try:
        emails, pages = lh.emails_from_site("https://nothing.example")
        check("an unreachable site yields no address at all", (emails, pages), ([], []))
    finally:
        lh._get = saved


def test_the_hunt_spreads_across_the_market():
    print("A capped run still reaches the whole market:")
    t = lh.plan_targets(per_segment=1)
    check("every segment is planned", len({x["segment"] for x in t}), len(ls.SEGMENTS))
    first = [x["segment"] for x in t[:10]]
    check("the first ten searches are ten different segments", len(set(first)), 10)
    check("one segment can still be targeted alone",
          {x["segment"] for x in lh.plan_targets(segment="hotel")}, {"hotel"})


def test_hunt_without_a_key_explains_itself():
    print("No Places key:")
    for var in ("GOOGLE_PLACES_API_KEY", "GOOGLE_MAPS_API_KEY"):
        os.environ.pop(var, None)
    real = board.DB
    board.DB = _fresh_db()
    try:
        r = lh.run(agent="leads-hunter", segment="hotel", max_searches=3)
        check("it refuses rather than pretending", r["ok"], False)
        check("and names the key to set", "GOOGLE_PLACES_API_KEY" in r["error"], True)
        check("and still shows what it would have run", r["searches_planned"], 3)
        con = sqlite3.connect(board.DB)
        check("nothing was stored",
              con.execute("SELECT count(*) FROM leads").fetchone()[0], 0)
        con.close()
    finally:
        board.DB = real


def test_hunt_stores_screens_and_dedupes():
    print("A full hunt, with the network faked:")
    real = board.DB
    board.DB = _fresh_db()
    saved_search, saved_key, saved_get = lh.search_places, lh._places_key, lh._get
    try:
        lh._places_key = lambda: ("fake-key", "test")
        lh._get = lambda url, timeout=12: ""          # no site reading in this test
        pages = [PLACE,
                 {**PLACE, "displayName": {"text": "Barsha Grand Hotel"},
                  "websiteUri": "", "internationalPhoneNumber": "+971 4 999 8888"},
                 {**PLACE, "displayName": {"text": "Closed Down Hotel"},
                  "businessStatus": "CLOSED_PERMANENTLY"}]
        lh.search_places = lambda q, a, k, limit=20, timeout=25: (pages, None)

        r = lh.run(agent="leads-hunter", segment="hotel", area="Dubai Marina",
                   max_searches=1, enrich_sites=False)
        check("it runs", r["ok"], True)
        check("critic passed", r["critic"], "passed")
        check("the closed business is not a lead", r["found"], 2)
        check("both live ones stored", r["stored"], 2)
        check("nobody was contacted", r["contacted"], 0)
        check("and it reports what it would pitch",
              r["by_service"], {"frameless shower glass": 2})

        # run it again: the same hotels must not be collected twice
        again = lh.run(agent="leads-hunter", segment="hotel", area="Dubai Marina",
                       max_searches=1, enrich_sites=False)
        check("a second run stores nothing new", again["stored"], 0)
        check("and calls them duplicates", again["duplicates"], 2)

        con = sqlite3.connect(board.DB)
        check("exactly two rows in the cabinet",
              con.execute("SELECT count(*) FROM leads").fetchone()[0], 2)
        check("outreach log untouched",
              con.execute("SELECT count(*) FROM outreach_log").fetchone()[0], 0)
        check("every lead sits at 'new', uncleared",
              [r[0] for r in con.execute("SELECT DISTINCT stage FROM leads")], ["new"])
        con.close()
    finally:
        lh.search_places, lh._places_key, lh._get = saved_search, saved_key, saved_get
        board.DB = real


def test_never_collected_twice():
    print("Never collected twice:")
    real = board.DB
    board.DB = _fresh_db()
    try:
        first = ls.intake([BIZ])
        check("first sighting is stored", len(first["stored"]), 1)
        again = ls.intake([{**BIZ, "source": "Google Maps"}])
        check("same firm from another source is a duplicate", len(again["duplicates"]), 1)
        check("and is NOT stored twice", len(again["stored"]), 0)
        other = ls.intake([{**BIZ, "company": "Barsha Glass Co",
                            "email": "hi@barshaglass.ae"}])
        check("a genuinely new firm still gets in", len(other["stored"]), 1)
        con = sqlite3.connect(board.DB)
        check("exactly two rows in the cabinet",
              con.execute("SELECT count(*) FROM leads").fetchone()[0], 2)
        con.close()
    finally:
        board.DB = real


# ---------------------------------------------------------------------------
# the mail
# ---------------------------------------------------------------------------

COMPANY = {"name": "Glassworks Dubai", "tagline": "Glass & aluminium, done properly",
           "phone": "+971 4 000 0000", "whatsapp": "+971 50 000 0000",
           "website": "https://glassworks.ae", "areas": ["Dubai", "Abu Dhabi", "Sharjah"]}


def test_the_mail_is_about_one_service():
    print("The mail is about the job they need:")
    lead = {"company": "Marina Bay Hotel", "area": "Dubai Marina", "segment": "hotel",
            "stage": "new", "service": "shower_glass", "email": "info@x.ae"}
    b = mail.writing_brief(lead, COMPANY)
    check("the headline service is the one the Hunter tagged",
          b["headline_service"], "frameless shower glass")
    check("related services ride along", len(b["also_offer"]) >= 3, True)
    check("the body is told not to list the lot",
          any("footer" in x for x in b["must_avoid"]), True)
    check("the website goes in the footer block",
          b["footer_block"]["website"], "https://glassworks.ae")

    # an untagged lead still gets a sensible service rather than a generic pitch
    b2 = mail.writing_brief({**lead, "service": ""}, COMPANY)
    check("an untagged lead is still matched", b2["headline_service"],
          "frameless shower glass")


def test_the_rendered_mail_is_readable():
    """
    The complaint this answers: what came out was a nested plan, not an email.
    A person should be able to read the output and a browser should be able to
    render it.
    """
    print("The rendered email reads like an email:")
    lead = {"company": "Marina Bay Hotel", "area": "Dubai Marina", "segment": "hotel",
            "stage": "new", "service": "shower_glass", "email": "info@x.ae"}
    draft = ("Shower screens for the Marina Bay refurb\n\n"
             "Saw you're refreshing the tower rooms. We fitted 120 frameless "
             "screens in JBR last spring.\n\n"
             "Worth a quick WhatsApp?")
    out = mail.render(mail.plan(lead, COMPANY), draft, COMPANY, lead)

    check("the subject is lifted off the first line",
          out["subject"], "Shower screens for the Marina Bay refurb")
    check("the subject is not repeated in the body",
          out["subject"] in out["text"].split("\n")[0], False)
    check("the body survives", "120 frameless screens" in out["text"], True)
    check("the text version names the other services",
          "We also do:" in out["text"], True)
    check("the website is in the text version",
          "https://glassworks.ae" in out["text"], True)
    check("the phone is in the text version", "+971 4 000 0000" in out["text"], True)

    h = out["html"]
    check("the html is a real document", h.startswith("<!doctype html>"), True)
    check("it opens with the brand block", "Glassworks Dubai" in h, True)
    check("it names the one service", "frameless shower glass" in h, True)
    check("it links the website", 'href="https://glassworks.ae"' in h, True)
    check("WhatsApp is a tappable link", "https://wa.me/97150" in h, True)
    check("the related services are chips, not a paragraph",
          h.count("border-radius:14px") >= 3, True)
    check("no unclosed table", h.count("<table") == h.count("</table>"), True)

    # a draft with no subject line must not lose its first paragraph
    only_body = "We fit shower screens across Dubai Marina. Worth a chat?"
    out2 = mail.render(mail.plan(lead, COMPANY), only_body, COMPANY, lead)
    check("a bodyless-subject draft keeps its words",
          "We fit shower screens" in out2["text"], True)
    check("and gets a subject built from the service",
          out2["subject"], "Frameless shower glass in Dubai Marina")


def test_mail_preview_is_pure():
    print("Preview touches nothing:")
    real = board.DB
    board.DB = _fresh_db()
    try:
        out = mail.preview({"company": "X", "area": "Deira", "segment": "gym",
                            "stage": "new"}, COMPANY)
        check("it renders without a key or a network", bool(out["html"]), True)
        check("a gym is pitched mirrors", out["service"], "mirror")
        con = sqlite3.connect(board.DB)
        check("nothing was written to the cabinet",
              con.execute("SELECT count(*) FROM outreach_log").fetchone()[0], 0)
        con.close()
    finally:
        board.DB = real


# ---------------------------------------------------------------------------
# coordination
# ---------------------------------------------------------------------------

def test_intake_cannot_contact():
    print("Intake contacts nobody:")
    real = board.DB
    board.DB = _fresh_db()
    try:
        res = ls.intake([BIZ, {**BIZ, "company": "X Co", "email": "x@y.ae"},
                         {**BIZ, "company": "Villa owner Ali",
                          "segment": "private_individual"}])
        check("the private one was refused", len(res["refused"]), 1)
        check("two businesses stored", len(res["stored"]), 2)
        check("nothing was contacted", res["contacted"], 0)
        con = sqlite3.connect(board.DB)
        check("outreach log untouched",
              con.execute("SELECT count(*) FROM outreach_log").fetchone()[0], 0)
        con.close()
    finally:
        board.DB = real


def test_critic():
    print("Critic:")
    good = ls.build_source_map()
    check("the real map passes", ls.critic_check(good, env_ready=False)[0], True)

    blind = {**good, "refused_sources": []}
    ok, why = ls.critic_check(blind, env_ready=False)
    check("a screen that refuses nothing is rejected",
          (ok, any("cannot be working" in r for r in why)), (False, True))

    # the narrowing regression, caught by the Critic and not just by a test
    narrow = {**good, "segments": dict(list(good["segments"].items())[:5])}
    ok, why = ls.critic_check(narrow, env_ready=False)
    check("a five-segment target list is rejected",
          (ok, any("decision nobody made" in r for r in why)), (False, True))

    ok, why = ls.critic_check(good, env_ready=True)
    check("scope creep is caught if outreach is suddenly live",
          (ok, any("re-plan" in r for r in why)), (False, True))
    check("no usable sources is rejected",
          ls.critic_check({"usable_sources": [], "segments": {},
                           "refused_sources": [{"name": "x"}]}, False)[0], False)


def test_hunt_critic():
    print("Hunt critic:")
    good = [{"company": "A", "email": "a@a.ae", "service": "mirror"},
            {"company": "B", "phone": "+9714", "service": "pergola"}]
    check("a sound batch passes", lh.critic_check(good, [{"q": 1}], [])[0], True)
    ok, why = lh.critic_check(good + [dict(good[0])], [{"q": 1}], [])
    check("the same company twice is rejected",
          (ok, any("twice" in r for r in why)), (False, True))
    ok, why = lh.critic_check([{"company": "C", "service": "mirror"}], [{"q": 1}], [])
    check("a row nobody can contact is rejected",
          (ok, any("no way to reach" in r for r in why)), (False, True))
    ok, why = lh.critic_check([], [{"q": 1}], [])
    check("every search empty with no error is rejected",
          (ok, any("not a real result" in r for r in why)), (False, True))


def test_claim_is_atomic():
    print("Claim, don't grab (6 Hunters, 1 task):")
    real = board.DB
    board.DB = _fresh_db()
    try:
        tid = board.post_task("Leads", "target-source list", "detail", risk="green")
        winners, lock = [], threading.Lock()

        def go(n):
            if board.claim_task(tid, f"hunter-{n}"):
                with lock:
                    winners.append(n)

        ts = [threading.Thread(target=go, args=(i,)) for i in range(6)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        check("exactly one Hunter claims it", len(winners), 1)
    finally:
        board.DB = real


def test_end_to_end():
    print("Source map cycle on a throwaway cabinet:")
    real = board.DB
    board.DB = _fresh_db()
    try:
        check("refuses to invent work with no task", ls.run(agent="t")["ok"], False)

        board.post_task("Leads", "Target-source list across the market",
                        "Sources only, no contact.", risk="green")
        r = ls.run(agent="leads-hunter")
        check("it runs", r["ok"], True)
        check("critic passed", r["critic"], "passed")
        check("every segment is covered", r["segments_covered"], len(ls.SEGMENTS))
        check("stored no leads", r["stored_leads"], 0)
        check("contacted nobody", r["contacted"], 0)

        con = sqlite3.connect(board.DB)
        con.row_factory = sqlite3.Row
        check("leads table still empty",
              con.execute("SELECT count(*) FROM leads").fetchone()[0], 0)
        t = dict(con.execute("SELECT status,owner,result FROM tasks").fetchone())
        check("the task was finished", t["status"], "done")
        check("and records who did it", t["owner"], "leads-hunter")
        import json as _json
        parsed = _json.loads(t["result"] or "null")
        check("the result is a real object, not an escaped string",
              isinstance(parsed, dict), True)
        check("the map is the deliverable", "segments" in parsed, True)
        sec = con.execute("SELECT count(*) FROM requests WHERE to_sector='Security'").fetchone()[0]
        check("Security was asked to clear the sources", sec, 1)
        con.close()
    finally:
        board.DB = real


def test_outreach_is_capped_and_recorded():
    """
    The send boundary. Three promises the handbook makes about outgoing mail,
    each of which used to have nothing behind it:

      nothing goes out above the daily cap
      nothing goes out from the wrong domain
      nothing goes out without being written down

    That last one mattered most: eleven places read outreach_log and NOTHING
    ever wrote it, so the cap had nothing to count, the dashboard read 0 for
    ever, and the promise never to approach the same firm twice had no record
    of who had already been approached.
    """
    print("Outreach cannot exceed the cap, leave the wrong domain, or go unrecorded:")
    real_db = board.DB
    board.DB = _fresh_db()
    from coordination import targets as _t
    real_t = _t.DB
    _t.DB = board.DB
    keep = {k: os.environ.get(k) for k in
            ("SMTP_HOST", "SMTP_USER", "SMTP_PASS", "SMTP_FROM", "OUTREACH_DOMAIN")}
    try:
        for k in keep:
            os.environ.pop(k, None)

        lead = {"id": 1, "email": "info@example.ae"}
        r = mail._smtp_send(lead, "Subject line\n\nbody")
        check("with no SMTP it refuses rather than pretending", r["ok"], False)
        check("and says the credential is missing", "not configured" in r["error"], True)

        check("nothing was recorded for a send that never happened", mail.sent_today(), 0)

        # the log is what every counter reads
        mail.log_outreach(1, "a real mail")
        check("a recorded send is counted", mail.sent_today(), 1)
        con = sqlite3.connect(board.DB)
        n = con.execute("SELECT count(*) FROM outreach_log WHERE direction='sent'").fetchone()[0]
        con.close()
        check("and it is really in outreach_log", n, 1)

        os.environ.update(SMTP_HOST="smtp.test", SMTP_USER="hello@mail.example.ae",
                          SMTP_PASS="x")
        cap = mail.daily_cap()
        for i in range(cap):
            mail.log_outreach(i, "filler")
        over = mail._smtp_send(lead, "Subject\n\nbody")
        check("over the cap, nothing is sent", over["ok"], False)
        check("and it says so plainly", over.get("capped"), True)

        # clear the log, then prove the domain guard bites before any connection
        con = sqlite3.connect(board.DB)
        con.execute("DELETE FROM outreach_log")
        con.commit(); con.close()
        os.environ["OUTREACH_DOMAIN"] = "mail.example.ae"
        os.environ["SMTP_FROM"] = "someone@gmail.com"
        bad = mail._smtp_send(lead, "Subject\n\nbody")
        check("the wrong sending domain is refused", bad["ok"], False)
        check("and named in the error", "not on the approved outreach" in bad["error"], True)
        check("a refused send is never logged", mail.sent_today(), 0)

        # a lead with no address must not reach the network at all
        os.environ["SMTP_FROM"] = "hello@mail.example.ae"
        none = mail._smtp_send({"id": 9, "email": ""}, "S\n\nb")
        check("a lead with no email is refused", none["ok"], False)
    finally:
        for k, v in keep.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _t.DB = real_t
        board.DB = real_db


from providers import store as _store


def test_hunting_for_one_service():
    """
    The owner's ask: "the agent must know which service related leads it's
    collecting and target the accurate audience related to that service".

    Aiming by SEGMENT answers "find me hotels". Aiming by SERVICE answers "get me
    shower-glass work this week", which is the question that actually fills a
    diary — and it must go to the businesses that BUY shower glass, not sweep the
    market and sort it out afterwards.
    """
    print("Hunting for one service targets only its buyers:")
    from . import leads_hunt as lh
    from . import services as sv

    targets = lh.plan_targets(service="shower glass", per_segment=1)
    segs = {t["segment"] for t in targets}
    check("it found some targets", bool(targets), True)
    check("every segment targeted actually buys shower glass",
          segs - set(sv.buyers_of("shower_glass")), set())
    check("and a segment that does not buy it is left out",
          "warehouse" in segs, False)
    check("every target is stamped with the service it was collected for",
          {t.get("for_service") for t in targets}, {"shower_glass"})

    print("\nThe owner does not have to learn our spelling:")
    for typed in ("shower glass", "Shower Glass", "shower_glass", "frameless shower"):
        check(f"'{typed}' finds the job", sv.resolve_service(typed), "shower_glass")
    check("a service we do not sell resolves to nothing",
          sv.resolve_service("swimming pools"), "")

    print("\nAsking for work we do not sell finds nobody, loudly:")
    check("no targets are planned", lh.plan_targets(service="swimming pools"), [])
    out = lh.run(service="swimming pools", dry_run=True)
    check("the hunt refuses", out["ok"], False)
    check("and says what we DO sell", "aluminium_doors" in out["error"], True)

    print("\nThe two directions of the table cannot drift apart:")
    # buyers_of and services_for_segment are the same fact read both ways
    for svc in sv.SERVICES:
        for seg in sv.buyers_of(svc):
            if svc not in sv.services_for_segment(seg):
                check(f"{seg} buys {svc} both ways", False, True)
                break
    check("every buyer link agrees in both directions", True, True)


def test_locals_come_first():
    """
    "Main target is locals." Not a preference — a Business Bay enquiry is
    surveyed tomorrow, a Ras Al Khaimah one is two hours each way. The search
    budget always runs out, and it should run out at the far end.
    """
    print("\nNearest areas are searched first:")
    from unittest import mock
    from . import leads_hunt as lh
    areas = ["Abu Dhabi", "Sharjah", "Business Bay", "Ras Al Khaimah", "Al Quoz"]

    with mock.patch("coordination.site.status", return_value={"url": "https://glassworks-dubai.ae"}):
        order = lh.local_first(areas)
    check("a Dubai firm searches Dubai districts first",
          order[:2], ["Business Bay", "Al Quoz"])
    check("and the far emirates last", order[-1], "Ras Al Khaimah")

    with mock.patch("coordination.site.status", return_value={"url": "https://sharjahglass.ae"}):
        check("a Sharjah firm starts in Sharjah",
              lh.local_first(areas)[0], "Sharjah")

    with mock.patch("coordination.site.status", return_value={"url": ""}):
        check("with no site saved nothing is invented — the list is untouched",
              lh.local_first(areas), areas)

    # and the plan itself must honour it
    with mock.patch("coordination.site.status", return_value={"url": "https://glassworks-dubai.ae"}):
        t = lh.plan_targets(segment="hotel", per_segment=1)
    check("the search plan starts local", t[0]["area"], "Business Bay")


def main():
    with _store.isolated():
        _main()


def _main():
    test_the_target_list_is_the_whole_market()
    test_every_segment_knows_what_to_sell_it()
    test_service_matching()
    test_related_services_are_named()
    test_private_individuals_are_the_owner_s_call()
    test_scope_screens()
    test_a_place_becomes_a_usable_lead()
    test_emails_are_read_not_guessed()
    test_the_hunt_spreads_across_the_market()
    test_hunting_for_one_service()
    test_locals_come_first()
    test_hunt_without_a_key_explains_itself()
    test_hunt_stores_screens_and_dedupes()
    test_never_collected_twice()
    test_the_mail_is_about_one_service()
    test_the_rendered_mail_is_readable()
    test_mail_preview_is_pure()
    test_intake_cannot_contact()
    test_critic()
    test_hunt_critic()
    test_claim_is_atomic()
    test_end_to_end()
    test_outreach_is_capped_and_recorded()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: {_fail}")
        sys.exit(1)
    print("all leads tests passed")


if __name__ == "__main__":
    main()
