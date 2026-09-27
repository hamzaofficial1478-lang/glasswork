"""
campaign_tests.py — run: python3 -m pipelines.campaign_tests

"the mails written are too basic and too generic ... with out any offer with
 out any poster and as a solo worker you can see it saying I word and using my
 own name ... we can use 1 mail for all leads"

What has to hold:

  the company speaks       "we", never "I", never a person's name or Gmail
  his offer, word for word  it passes; an offer the writer adds does not
  he approves it once       and any change after that puts it back to draft
  who gets it               only the leads he ticks, never one already written
                            to, Security again at sending, inside the cap
  what leaves               the designed mail with a text part, from the
                            company's name, with an unsubscribe header
  the daily pass            builds the campaign for him — no mail per lead

Throwaway cabinet. No SMTP, no model, no network.
"""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

from coordination import auditor, board
from . import campaign as C, leads_outreach as O, mail

_fail = []
ROOT = Path(__file__).parents[1]
OFFER = "10% off shower glass booked this month"


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


class _Cabinet:
    """A throwaway cabinet, his site never read, and the mail server faked."""
    def __enter__(self):
        self.real = (board.DB, C.tiles_from_site, mail._smtp_send, mail.daily_cap,
                     mail.sent_today, mail.setting)
        d = Path(tempfile.mkdtemp())
        db = d / "agency.db"
        con = sqlite3.connect(db)
        con.executescript((ROOT / "coordination" / "schema.sql").read_text(encoding="utf-8"))
        con.commit()
        con.close()
        board.DB = str(db)
        board.ensure_schema()
        C.tiles_from_site = lambda timeout=25: (
            [{"label": "Glass installation", "photo": "https://example.ae/a.jpg",
              "own": True, "bytes": 90_000},
             {"label": "Aluminium installation", "photo": "https://example.ae/b.png",
              "own": True, "bytes": 1_600_000},
             {"label": "Folding doors", "photo": "https://example.ae/c.jpg",
              "own": True, "bytes": 120_000},
             {"label": "Wooden doors", "photo": "https://example.ae/d.jpg",
              "own": True, "bytes": 80_000}],
            {"phone": "+971 4 000 0000", "whatsapp": "+971 50 000 0000",
             "email": "team@example.ae", "site": "https://example.ae"}, [])
        self.sent = []
        self.cap = 20

        def fake_send(lead, draft, html="", from_name="", headers=None):
            self.sent.append({"to": lead["email"], "draft": draft, "html": html,
                              "from_name": from_name, "headers": headers or {}})
            return {"ok": True}
        mail._smtp_send = fake_send
        mail.daily_cap = lambda: self.cap
        mail.sent_today = lambda con=None: len(self.sent)
        real_setting = self.real[5]
        mail.setting = lambda k, d="": ("team@example.ae" if k in ("SMTP_FROM", "SMTP_USER")
                                        else real_setting(k, d))
        return self

    def __exit__(self, *a):
        (board.DB, C.tiles_from_site, mail._smtp_send, mail.daily_cap,
         mail.sent_today, mail.setting) = self.real

    def sql(self, q, args=()):
        c = sqlite3.connect(board.DB)
        c.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in c.execute(q, args)]
        finally:
            c.close()


def _lead(company, email, **kw):
    return board.add_lead_if_new({"company": company, "email": email,
                                  "segment": "property_management", **kw})


def _approved(offer=OFFER):
    C.create(offer=offer, offer_until="31 October")
    r = C.approve()
    assert r["ok"], r
    return r["campaign"]


def test_the_company_speaks():
    print("\nThe company speaks, to each lead by name:")
    check("a shouted listing becomes a name",
          C.greeting({"company": "PARADISE PROPERTIES MANAGEMENT LLC OPC"}),
          "Dear Paradise Properties Management team,")
    check("initials stay initials",
          C.greeting({"company": "FMP HOMES REAL ESTATE L.L.C"}), "Dear FMP Homes Real Estate team,")
    check("a proper name is left alone",
          C.greeting({"company": "ReCare Facilities Management"}),
          "Dear ReCare Facilities Management team,")
    check("no name is 'Hello', not 'Hi ,'", C.greeting({"company": ""}), "Hello,")
    with _Cabinet():
        camp = C.create(offer=OFFER)["campaign"]
        r = C.render(camp, {"company": "Class Living Real Estate Brokers"})
        check("the standard campaign passes the Auditor", camp["audit"]["pass"], True)
        check("it is signed by the company",
              "The team at World Deal Technical Services" in r["text"], True)
        check("it never says 'I'",
              auditor.check_outreach(r["draft"], voice="company",
                                     owner_offer=C.his_words(camp))["pass"], True)
        check("the greeting names the lead",
              "Dear Class Living Real Estate Brokers team," in r["html"], True)
        check("it says what we do", "WHAT WE DO" in r["text"] and "Folding doors" in r["html"], True)
        check("and how to stop it", "won't write again" in r["text"], True)
        check("with WhatsApp and email buttons",
              "wa.me/971500000000" in r["html"] and "mailto:team@example.ae" in r["html"], True)

    print("\n...and a one-man mail is refused:")
    base = ("Subject: Glass work\n\nDear Example team, we fit glass. Our own team measures "
            "on site, makes each piece to size and installs it, for landlords and property "
            "managers across Dubai. Reply stop and we won't write again.")
    for said in ("I work in glass across Dubai.", "Drop me a message.",
                 "My team can measure."):
        v = auditor.check_outreach(base.replace("we fit glass.", said), voice="company")
        check(f"  {said!r} is held", v["pass"], False)
    v = auditor.check_outreach(base + "\n\nCrisp\nWhatsApp: crisp.abbas@gmail.com")
    check("a personal Gmail in the signature is held, whoever is speaking", v["pass"], False)
    v = auditor.check_outreach(base + "\n\nWhatsApp: https://wa.me/971500000000", voice="company")
    check("but WhatsApp's own wa.me link is not 'me'", v["pass"], True)
    v = auditor.check_outreach("**Subject:** Glass work\n\n---\n\nHi, we fit glass. "
                               "Reply stop and we won't write again.")
    check("'**Subject:**' is read as the subject line", v["subject"], "Glass work")
    check("and chat-window formatting is held", v["pass"], False)


def test_his_offer_word_for_word():
    print("\nHis offer goes out word for word; the writer cannot add one:")
    with _Cabinet():
        camp = C.create(offer=OFFER, offer_until="31 October")["campaign"]
        r = C.render(camp, {"company": "Unique Real Estate"})
        check("it is in its own box", OFFER in r["html"] and "Current offer" in r["html"], True)
        check("the Auditor lets his words through", camp["audit"]["pass"], True)
        bad = dict(camp, copy=dict(camp["copy"], cta=camp["copy"]["cta"] + " Book now for 15% off."))
        v = C.audit(bad)
        check("an offer the writer adds is refused", v["pass"], False)
        check("  and the reason names it", "concession" in " ".join(v["blocking"]), True)
        v = auditor.check_outreach(r["draft"])
        check("without his approval the same words are refused", v["pass"], False)


def test_he_approves_once_and_changes_mean_again():
    print("\nHe approves it once; a change puts it back to draft:")
    with _Cabinet():
        camp = _approved()
        check("approved", camp["status"], "approved")
        r = C.save({"offer": "Measuring visits this week"})
        check("changing the offer puts it back to draft", r["campaign"]["status"], "draft")
        check("and says so", "back to draft" in r["head"], True)
        r = C.save({"offer": "Measuring visits this week"})
        check("saving nothing new is not a change", r["head"], "Nothing changed.")
        C.save({"intro": "I can come and look at it myself."})
        r = C.approve()
        check("wording that says 'I' cannot be approved", r["ok"], False)
        check("  and he is told why", "one person" in r["error"], True)


def test_only_the_ticked_leads_get_it():
    print("\nOnly the leads he ticks, only once, only when approved:")
    with _Cabinet() as cab:
        a = _lead("Class Living Real Estate Brokers", "office@classliving.ae")
        b = _lead("Unique Real Estate Broker", "info@unique.ae")
        c = _lead("Darkom Real Estate", "pa@darkom.ae")
        C.create(offer=OFFER)
        r = C.send([a, b])
        check("nothing goes before he approves", (r["ok"], cab.sent), (False, []))
        C.approve()
        old = board.queue_outreach(a, "Class Living", "office@classliving.ae", "Old", "old body")
        r = C.send([a, b])
        check("the two he ticked went", sorted(s["to"] for s in cab.sent),
              ["info@unique.ae", "office@classliving.ae"])
        check("the one he did not tick did not", any(s["to"] == "pa@darkom.ae" for s in cab.sent), False)
        s = cab.sent[0]
        check("as the designed mail", "<table" in s["html"] and OFFER in s["html"], True)
        check("with the words as a text part too", "WHAT WE DO" in s["draft"], True)
        check("from the company's name", s["from_name"], "World Deal Technical Services")
        check("with an unsubscribe header", "List-Unsubscribe" in s["headers"], True)
        check("the one-off draft it replaced is put away",
              cab.sql("SELECT status FROM outreach_queue WHERE id=?", (old,))[0]["status"],
              "replaced")
        state = {x["id"]: x["state"] for x in C.recipients()}
        check("the Mail room shows them as written to", (state[a], state[b], state[c]),
              ("sent", "sent", "ready"))
        cab.sent.clear()
        r = C.send([a])
        check("a business written to is never written to again", cab.sent, [])
        check("  and he is told", "approved" in r["failed"][0]["why"] or
              "written to" in r["failed"][0]["why"], True)

    print("\nSecurity looks again, and the cap still holds:")
    with _Cabinet() as cab:
        bad = _lead("Quick Cash Glass", "win@mailinator.com")
        ids = [_lead(f"Firm {i}", f"info@firm{i}.ae") for i in range(4)]
        _approved()
        r = C.send([bad])
        check("a disposable address is refused", cab.sent, [])
        check("  by Security", "Security" in r["failed"][0]["why"], True)
        cab.cap = 2
        r = C.send(ids)
        check("two go now", len(r["sent"]), 2)
        check("two wait for the next allowance", len(r["later"]), 2)
        waiting = cab.sql("SELECT id, campaign_id, html FROM outreach_queue"
                          " WHERE status='approved'")
        check("  kept as his approved campaign mail", (len(waiting), bool(waiting[0]["html"])), (2, True))
        cab.cap = 10
        O._send_approved(10)
        check("the next pass sends them, designed", len(cab.sent), 4)
        check("  from the company", cab.sent[-1]["from_name"], "World Deal Technical Services")


def test_a_changed_campaign_is_not_sent_on_old_ticks():
    print("\nMail ticked before he changed the campaign is not sent as the new one:")
    with _Cabinet() as cab:
        ids = [_lead(f"Firm {i}", f"info@firm{i}.ae") for i in range(2)]
        _approved()
        cab.cap = 1
        C.send(ids)
        C.save({"offer": "Something new"})
        cab.cap = 10
        failed = []
        O._send_approved(10, failed=failed)
        check("it is not sent", len(cab.sent), 1)
        check("  and he is told to tick it again", "tick it again" in failed[0]["why"], True)
        check("  it is marked stale, not left waiting",
              cab.sql("SELECT status FROM outreach_queue WHERE status IN ('approved','stale')")[0]["status"],
              "stale")


def test_each_mail_counts_once():
    print("\nEach mail sent counts once against his daily limit:")
    with _Cabinet():
        logged = []
        real = mail.log_outreach
        mail.log_outreach = lambda *a, **k: logged.append(a)
        try:
            lid = _lead("Al Noor", "info@alnoor.ae")
            qid = board.queue_outreach(lid, "Al Noor", "info@alnoor.ae", "S", "body")
            O.send_now([qid])
        finally:
            mail.log_outreach = real
        check("the runner writes no second log row (the send boundary writes the one)",
              logged, [])


def test_the_poster_takes_the_light_photographs():
    print("\nThe poster takes his light photographs before his heavy ones:")
    with _Cabinet():
        tiles = C.tiles_from_site()[0]
        picked = [t["label"] for t in C.poster_photos(tiles)]
        check("three, the heavy one left out", picked,
              ["Glass installation", "Folding doors", "Wooden doors"])
    check("his site's spellings are written the British way",
          C._label("Aluminum Installation"), "Aluminium installation")


def test_the_daily_pass_builds_it_and_writes_no_mail_per_lead():
    print("\nThe day's Leads pass gets the campaign ready — no mail per lead:")
    with _Cabinet() as cab:
        _lead("Class Living Real Estate Brokers", "office@classliving.ae")
        board.post_task("Leads", "Write outreach mails", "d", created_by="md",
                        desk="leads-outreach")
        wrote = []
        real = mail._write_copy
        mail._write_copy = lambda *a, **k: wrote.append(1) or {"ok": False}
        try:
            r = O._run(O.AGENT, dry_run=False, limit=5)
        finally:
            mail._write_copy = real
        check("no model call per lead", wrote, [])
        check("the campaign is built for him", bool(C.current()), True)
        check("and he is told where it is", "Mail room" in r["head"], True)
        check("nothing was sent", cab.sent, [])


def test_the_agency_rewrite_never_makes_it_worse():
    print("\nThe agency's rewrite replaces the wording only if it passes:")
    good = {"subject": "Glass and aluminium for your buildings",
            "preheader": "Our own team measures, makes and fits.",
            "headline": "Glass & aluminium, handled by one team",
            "intro": "We are a UAE glass and aluminium company.\n\nOur own fitters measure on site.",
            "why_us": ["Our own fitters", "Measured on site"],
            "cta": "Send us a photo on WhatsApp for a quote.",
            "lines": {"Glass installation": "Cut to size and fitted."}}
    one_man = dict(good, intro="I run a glass workshop and I come myself.")

    def fake(answer):
        return lambda *a, **k: {"ok": True, "output": "```json\n" + json.dumps(answer) + "\n```"}
    with _Cabinet():
        before = C.create(offer=OFFER)["campaign"]["copy"]["intro"]
        r = C.rewrite(think=fake(one_man))
        check("a one-man version is refused", r["ok"], False)
        check("  and his current wording is kept", C.current()["copy"]["intro"], before)
        r = C.rewrite(think=fake(good))
        check("a good version is saved", r["ok"], True)
        check("  as a draft for him to read", C.current()["status"], "draft")
        check("  with his offer untouched", C.current()["offer"], OFFER)


def main():
    for t in (test_the_company_speaks,
              test_his_offer_word_for_word,
              test_he_approves_once_and_changes_mean_again,
              test_only_the_ticked_leads_get_it,
              test_a_changed_campaign_is_not_sent_on_old_ticks,
              test_each_mail_counts_once,
              test_the_poster_takes_the_light_photographs,
              test_the_daily_pass_builds_it_and_writes_no_mail_per_lead,
              test_the_agency_rewrite_never_makes_it_worse):
        t()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        sys.exit(1)
    print("campaign: all good — one mail, from the company, with his offer, sent only where he ticks.")


if __name__ == "__main__":
    main()
