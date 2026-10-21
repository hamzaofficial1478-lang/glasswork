"""
mailed_tests.py — run: python -m pipelines.mailed_tests

"the mails that are sent they should not shown in the leads section and there
 should be the button to delete the mails sent or to download them in CSV file
 and make sure the lead of mail get once that should not again get listed and
 the lead that have no mail do not list that only leads with mails"

What has to hold:

  only leads he can mail     a lead with no address is never listed — not in the
                             Leads section, not in the Mail room
  mailed once, never again   a lead written to leaves every list for good, even
                             after the outreach log is cleared at thirty days,
                             and even after he deletes the sent mail
  delete and CSV             deleting wipes the mail's words and keeps the fact
                             that the business was written to; the CSV is every
                             business ever mailed, safe to open in Excel
  one lead, now mailable     the same business met again with an address adds
                             the address to the lead on file, never a second lead
"""
import csv
import io
import sqlite3
import sys
import tempfile
from pathlib import Path

from coordination import board, leads_view, review
from . import campaign, leads_outreach, leads_sources, mail

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
    board._MIGRATED.discard(str(db))
    board.ensure_schema()
    return db


def _lead(company, email="", phone="", website=""):
    return board.add_lead_if_new({"company": company, "email": email, "phone": phone,
                                  "website": website, "area": "Dubai", "source": "test"})


def sql(q, a=()):
    c = sqlite3.connect(board.DB)
    try:
        return c.execute(q, a).fetchall()
    finally:
        c.close()


def test_only_leads_he_can_mail_are_listed():
    print("\nOnly leads with an address, and not yet mailed, are listed:")
    _fresh()
    a = _lead("Al Noor Interiors", "info@alnoor.ae")
    b = _lead("Palm Fitout", "hello@palmfitout.ae")
    _lead("No Address Carpentry", phone="+971501112233")
    mail.log_outreach(b, "Hello from World Deal")
    v = leads_view.build("all")
    check("the Leads section lists only the one he can still mail",
          [x["company"] for x in v["leads"]], ["Al Noor Interiors"])
    check("and says where the others went", (v["mailed"], v["no_address"]), (1, 1))
    check("today's tile counts the same leads", leads_view.build("today")["today"], 1)
    rec = campaign.recipients()
    check("the Mail room never lists a lead with no address",
          sorted(r["company"] for r in rec), ["Al Noor Interiors", "Palm Fitout"])
    check("the mailed one is under Sent", next(r["state"] for r in rec if r["id"] == b), "sent")
    check("the lead itself is marked as mailed", bool(sql("SELECT mailed_at FROM leads WHERE id=?",
                                                          (b,))[0][0]), True)
    del a


def test_mailed_once_is_never_listed_again():
    print("\nMailed once, never listed again — even after the log is cleared:")
    _fresh()
    b = _lead("Palm Fitout", "hello@palmfitout.ae")
    mail.log_outreach(b, "Hello")
    c = sqlite3.connect(board.DB)
    c.execute("DELETE FROM outreach_log")          # what the thirty-day clear-out does
    c.commit()
    c.close()
    _can, cannot = leads_outreach.eligible(log=False, for_campaign=True)
    check("still refused as already written to",
          [x["why"] for x in cannot if x["id"] == b], ["already written to — a firm is approached once"])
    check("still under Sent, not back in Ready", next(r["state"] for r in campaign.recipients()
                                                      if r["id"] == b), "sent")
    check("and not collected again", _lead("Palm Fitout", "hello@palmfitout.ae"), None)

    print("\n...and a cabinet from before the mark is marked from what was sent:")
    _fresh()
    old = _lead("Gulf Glass Co", "sales@gulfglass.ae")
    c = sqlite3.connect(board.DB)
    c.execute("INSERT INTO outreach_queue(lead_id,company,to_email,subject,body,status,sent_at,queued_at)"
              " VALUES(?,?,?,?,?,'sent',1700000000,1700000000)",
              (old, "Gulf Glass Co", "sales@gulfglass.ae", "Glass work", "Hello"))
    c.execute("UPDATE leads SET mailed_at=NULL WHERE id=?", (old,))
    c.commit()
    c.close()
    board._MIGRATED.discard(str(board.DB))
    board.ensure_schema()
    check("marked on the next start", sql("SELECT mailed_at FROM leads WHERE id=?", (old,))[0][0],
          1700000000)


def test_sent_mail_can_be_deleted_and_downloaded():
    print("\nSent mail: deleted from the list, and taken away as a CSV:")
    _fresh()
    ids = [_lead(n, e) for n, e in (("Palm Fitout", "hello@palmfitout.ae"),
                                    ("=HYPERLINK(evil)", "x@evil-co.ae"))]
    c = sqlite3.connect(board.DB)
    for i, lid in enumerate(ids):
        c.execute("INSERT INTO outreach_queue(lead_id,company,to_email,subject,body,html,status,"
                  "sent_at,queued_at) VALUES(?,?,?,?,?,?,'sent',?,?)",
                  (lid, "x", "x", "Glass & aluminium work", "Dear team, the body", "<p>html</p>",
                   1700000000 + i, 1700000000))
    c.commit()
    c.close()
    for lid in ids:
        mail.log_outreach(lid, "Dear team, the body")
    got = review.sent_csv()
    rows = list(csv.reader(io.StringIO(got["csv"])))
    check("the CSV has a header and one row per business mailed", len(rows), 3)
    check("with the address and the subject",
          any(r[1] == "hello@palmfitout.ae" and r[5] == "Glass & aluminium work" for r in rows), True)
    check("a name that starts with = is kept as text, never a formula",
          any(r[0] == "'=HYPERLINK(evil)" for r in rows), True)
    r = review.act("sent_delete", [ids[0]])
    check("deleted", r["ok"], True)
    check("gone from the Sent list", [x["id"] for x in campaign.recipients()], [ids[1]])
    check("its words are wiped", sql("SELECT body, html FROM outreach_queue WHERE lead_id=?",
                                     (ids[0],))[0], ("", ""))
    check("but the business is still remembered as written to",
          bool(sql("SELECT mailed_at FROM leads WHERE id=?", (ids[0],))[0][0]), True)
    check("so it is not collected again", _lead("Palm Fitout", "hello@palmfitout.ae"), None)
    check("and it is still in the CSV, marked as deleted",
          any(r[1] == "hello@palmfitout.ae" and r[8] for r in
              csv.reader(io.StringIO(review.sent_csv()["csv"]))), True)
    check("delete all takes the rest", review.act("sent_delete_all", [])["deleted"], 1)


def test_the_same_business_met_again_brings_its_address():
    print("\nThe same business met again, this time with an address:")
    _fresh()
    lid = _lead("Marina Joinery LLC", phone="+971 50 123 4567", website="https://marinajoinery.ae")
    out = leads_sources.intake([{"company": "Marina Joinery LLC", "email": "info@marinajoinery.ae",
                                 "phone": "+971501234567", "website": "https://marinajoinery.ae",
                                 "area": "Dubai", "segment": "fitout",
                                 "source": "test"}])
    check("still one lead", sql("SELECT count(*) FROM leads")[0][0], 1)
    check("now with the address", sql("SELECT email FROM leads WHERE id=?", (lid,))[0][0],
          "info@marinajoinery.ae")
    check("and said so", out.get("address_added"), ["Marina Joinery LLC"])
    mail.log_outreach(lid, "Hello")
    check("an address is never added to a lead already mailed",
          board.fill_missing_email({"company": "Marina Joinery LLC", "email": "other@marinajoinery.ae"}),
          None)


def test_a_stuck_mail_is_never_a_loop_and_can_be_deleted():
    """
    "there are 2 mails not getting sent when ever i try to send them it again
     redirect me to the sent mails ... add the delete button here too"
    """
    print("\nA mail the allowance stopped says so, instead of 'press Try again' for ever:")
    from unittest import mock
    _fresh()
    lids = [_lead(n, e) for n, e in (("365 Holiday Homes Rental", "info@365luxuryhomes.com"),
                                     ("RGA Real Estate", "info@rga.ae"))]
    c = sqlite3.connect(board.DB)
    for lid in lids:
        c.execute("INSERT INTO outreach_queue(lead_id,company,to_email,subject,body,status,"
                  "campaign_id,note,queued_at) VALUES(?,?,?,?,?,'approved',1,'owner approved',1)",
                  (lid, "x", "x", "S", "B"))
    c.commit()
    c.close()
    full = {"ok": True, "problems": [], "room": 0, "cap": 10, "sender": "a@b.ae"}
    with mock.patch.object(mail, "preflight", lambda: full):
        r = campaign.retry()
    check("Try again with today's allowance used says so in words",
          (r["ok"], "allowance of 10 mails is used up" in r["error"]), (False, True))
    states = {x["id"]: (x["state"], x["why"]) for x in campaign.recipients()}
    check("and both show as going tomorrow, not as stuck",
          [states[i] for i in lids], [("queued", "goes with tomorrow's allowance")] * 2)

    print("\n...the sender itself writes the reason on each mail it could not send:")
    c = sqlite3.connect(board.DB)
    c.execute("UPDATE outreach_queue SET note=?", (campaign.WAITING,))
    c.commit()
    c.close()
    failed = []
    with mock.patch.object(mail, "batch", __import__("contextlib").nullcontext):
        leads_outreach._send_rows(0, None, failed, [])
    check("a full allowance is written on the mail",
          all("cap reached" in n for (n,) in sql("SELECT note FROM outreach_queue")), True)
    c = sqlite3.connect(board.DB)
    c.execute("UPDATE outreach_queue SET note=?", (campaign.WAITING,))
    c.commit()
    c.close()
    campaign._left_waiting("the sender finished without sending it — press Try again")
    check("and nothing is ever left saying 'waiting' after a pass",
          sql("SELECT count(*) FROM outreach_queue WHERE note=?", (campaign.WAITING,))[0][0], 0)

    print("\n...and one that keeps failing can be deleted:")
    r = review.act("campaign_drop", [lids[0]])
    check("deleted", (r["ok"], r["deleted"]), (True, 1))
    check("the mail will not be sent",
          sql("SELECT status FROM outreach_queue WHERE lead_id=?", (lids[0],))[0][0], "rejected")
    check("the business is off the Mail room's lists",
          lids[0] in [x["id"] for x in campaign.recipients()], False)
    check("and off the Leads section", lids[0] in [x["id"] for x in leads_view.build("all")["leads"]],
          False)
    check("but kept, so it is not collected again",
          _lead("365 Holiday Homes Rental", "info@365luxuryhomes.com"), None)


def main():
    real = board.DB
    try:
        for t in (test_only_leads_he_can_mail_are_listed,
                  test_mailed_once_is_never_listed_again,
                  test_sent_mail_can_be_deleted_and_downloaded,
                  test_the_same_business_met_again_brings_its_address,
                  test_a_stuck_mail_is_never_a_loop_and_can_be_deleted):
            t()
    finally:
        board.DB = real
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        return 1
    print("mailed: all good — only leads he can mail are listed, and a business is mailed once.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
