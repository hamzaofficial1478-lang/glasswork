"""
review_tests.py — run: python -m coordination.review_tests

The Review page is where he reads what the agency wrote before it leaves. What
has to hold:

  every drafted mail is on it, whole, with who it is for
  a written page is on it with the page itself, not a filename
  a page the checks held back is on it too — the one he could not find
  his yes on a page goes to the Releaser's queue, and nothing else
  his no, a rewrite and a drop each do exactly that
  Send refuses, and approves nothing, while his email is not set up

Throwaway cabinet. No SMTP, no model, no network.
"""
import json
import sqlite3
import tempfile
import time
from pathlib import Path

from . import board
from . import review

_fail = []
ROOT = Path(__file__).parents[1]


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


class _Cabinet:
    def __enter__(self):
        self.real = board.DB
        d = Path(tempfile.mkdtemp())
        db = d / "agency.db"
        con = sqlite3.connect(db)
        con.executescript((ROOT / "coordination" / "schema.sql").read_text(encoding="utf-8"))
        con.commit()
        con.close()
        board.DB = str(db)
        board.ensure_schema()
        return self

    def __exit__(self, *a):
        board.DB = self.real

    def sql(self, q, args=()):
        c = sqlite3.connect(board.DB)
        c.row_factory = sqlite3.Row
        try:
            rows = [dict(r) for r in c.execute(q, args)]
            c.commit()
            return rows
        finally:
            c.close()


def _written_page(cab, title="Pergola in Dubai Marina", html="<h1>Pergolas</h1>"):
    now = int(time.time())
    cab.sql("INSERT INTO content_queue(title,type,brief,draft,status,created_at)"
            " VALUES(?,?,?,?,?,?)",
            (title, "service_page", json.dumps({"primary_keyword": "pergola dubai marina"}),
             json.dumps({"title": title, "html": html, "_page_html": html}), "drafted", now))
    cid = cab.sql("SELECT max(id) n FROM content_queue")[0]["n"]
    sid = board.queue_site_change("content-writer", "new_page", {
        "change": {"page": "pergola-dubai-marina.php", "html": html, "content_id": cid},
        "title": title, "why": "A new page for 'pergola dubai marina'.", "auditor": []})
    return cid, sid


def _held_page(cab, title="Shower Glass in JVC"):
    cab.sql("INSERT INTO content_queue(title,type,brief,draft,status,created_at)"
            " VALUES(?,?,?,?,?,?)",
            (title, "service_page", json.dumps({"primary_keyword": "shower glass jvc"}),
             json.dumps({"title": title, "html": "<p>draft</p>",
                         "_held_because": ["his phone number is not on the page"]}),
             "in_review", int(time.time())))
    return cab.sql("SELECT max(id) n FROM content_queue")[0]["n"]


def test_every_mail_is_there_whole():
    print("\nEvery drafted mail is on the page, whole, with who it is for:")
    with _Cabinet():
        lid = board.add_lead_if_new({"company": "Al Noor Cafeteria", "email": "info@alnoor.ae",
                                     "area": "Al Quoz", "segment": "food_stall"})
        board.queue_outreach(lid, "Al Noor Cafeteria", "info@alnoor.ae",
                             "Shopfront glass in Al Quoz", "Hello — the whole body.",
                             service="glass shopfronts")
        board.add_lead_if_new({"company": "Marina Fitout", "email": "hello@marinafit.ae"})
        m = review.build()["mails"]
        check("one waiting", len(m["waiting"]), 1)
        w = m["waiting"][0]
        check("with the address, subject and the whole body",
              (w["to"], w["subject"], w["body"]),
              ("info@alnoor.ae", "Shopfront glass in Al Quoz", "Hello — the whole body."))
        check("and where the lead is", w["lead"]["area"], "Al Quoz")
        check("the lead with no mail yet is counted", m["leads_without_mail"], 1)


def test_the_mail_room_lists_every_lead_and_where_it_stands():
    print("\nThe Mail room lists the collected leads, and where each stands:")
    with _Cabinet() as cab:
        ready = board.add_lead_if_new({"company": "Marina Fitout", "email": "hello@marinafit.ae"})
        drafted = board.add_lead_if_new({"company": "Al Noor", "email": "info@alnoor.ae"})
        board.queue_outreach(drafted, "Al Noor", "info@alnoor.ae", "S", "b")
        sent = board.add_lead_if_new({"company": "Done Before", "email": "hi@done.ae"})
        cab.sql("INSERT INTO outreach_log(lead_id,channel,direction,body,at)"
                " VALUES(?,'email','sent','hi',?)", (sent, int(time.time())))
        noaddr = board.add_lead_if_new({"company": "No Address", "phone": "+97143851122"})
        st = {l["id"]: l for l in review.build()["mails"]["leads"]}
        check("ready to write", st[ready]["state"], "ready")
        check("a mail already written", st[drafted]["state"], "drafted")
        check("written to already", st[sent]["state"], "sent")
        check("no address: cannot, and says why",
              (st[noaddr]["state"], st[noaddr]["why"]), ("no", "no address on file"))


def test_write_only_for_the_ticked_leads():
    print("\nWrite writes for the leads he ticked, and nobody else:")
    from pipelines import leads_outreach as O
    real = O.draft_for
    O.draft_for = lambda lead, company, owner_voice="": {
        "ok": True, "lead": lead, "service": "glass work", "service_key": "glass",
        "draft": f"Subject: For {lead['company']}\n\nHello.", "subject": f"For {lead['company']}",
        "body": "Hello.", "audit": {"pass": True, "words": 40, "warnings": [], "blocking": []}}
    try:
        with _Cabinet():
            a = board.add_lead_if_new({"company": "Marina Fitout", "email": "hello@marinafit.ae"})
            board.add_lead_if_new({"company": "Palm Villas", "email": "office@palmvillas.ae"})
            r = review.draft_mails([a])
            check("it ran", r.get("ok"), True)
            q = board.pending_outreach()
            check("one mail, for the ticked lead", [x["lead_id"] for x in q], [a])
            check("and nothing was sent", [x["status"] for x in q], ["pending"])
    finally:
        O.draft_for = real


def test_send_refuses_without_his_email():
    print("\nSend refuses, and approves nothing, while his email is not set up:")
    with _Cabinet():
        qid = board.queue_outreach(1, "X", "x@y.ae", "S", "b")
        real = review._mail_settings
        review._mail_settings = lambda: {"ready": False, "from": "", "cap": 20, "sent_today": 0}
        try:
            r = review.act("send", [qid])
        finally:
            review._mail_settings = real
        check("refused", r["ok"], False)
        check("and says where to set it up", "Setup" in r["error"], True)
        check("the draft is still waiting, not approved",
              [q["id"] for q in board.pending_outreach()], [qid])


def test_a_written_page_is_there_with_the_page_itself():
    print("\nA written page is on the page with the page itself:")
    with _Cabinet() as cab:
        _cid, sid = _written_page(cab)
        p = review.build()["pages"]
        check("one ready for his yes", [x["id"] for x in p["waiting"]], [sid])
        check("with its html to read", p["waiting"][0]["html"], "<h1>Pergolas</h1>")
        check("and the phrase it was written for",
              p["waiting"][0]["keyword"], "pergola dubai marina")


def test_the_held_page_he_could_not_find_is_there():
    print("\nA page the checks held back is on it too, with the reason:")
    with _Cabinet() as cab:
        cid = _held_page(cab)
        p = review.build()["pages"]
        check("listed", [x["id"] for x in p["held"]], [cid])
        check("with why", p["held"][0]["why"], ["his phone number is not on the page"])


def test_his_decisions_on_pages_do_exactly_that():
    print("\nHis yes goes to the Releaser's queue; nothing else happens:")
    with _Cabinet() as cab:
        cid, sid = _written_page(cab)
        r = review.act("publish", [sid])
        check("recorded", r["ok"], True)
        check("the Releaser will see it", [x["id"] for x in board.approved_site_changes()], [sid])
        check("and it says the tests come first", "tests" in r["head"], True)
        check("a second yes on the same page changes nothing",
              review.act("publish", [sid])["ok"], False)

    print("\nHis no means it is not published:")
    with _Cabinet() as cab:
        cid, sid = _written_page(cab)
        review.act("reject_page", [sid])
        check("off the queue", board.approved_site_changes(), [])
        check("and the page is marked turned down",
              cab.sql("SELECT status FROM content_queue WHERE id=?", (cid,))[0]["status"],
              "rejected")

    print("\nOnly a written page can be approved from here:")
    with _Cabinet():
        other = board.queue_site_change("it-fixer", "head_block", {"change": {}})
        check("an IT change is not approved through this door",
              review.act("publish", [other])["ok"], False)

    print("\nA held page goes back to the writer, or is dropped:")
    with _Cabinet() as cab:
        a, b = _held_page(cab, "A"), _held_page(cab, "B")
        review.act("rewrite", [a])
        review.act("discard_page", [b])
        rows = {r["id"]: r for r in cab.sql("SELECT id, status, draft FROM content_queue")}
        check("rewrite: briefed again with the old draft cleared",
              (rows[a]["status"], rows[a]["draft"]), ("briefed", None))
        check("drop: gone from the list", rows[b]["status"], "rejected")


def test_nothing_picked_is_not_an_action():
    print("\nAn empty press does nothing:")
    with _Cabinet():
        check("refused", review.act("send", [])["ok"], False)
        check("an unknown action is refused", review.act("delete_everything", [1])["ok"], False)


def main():
    for t in (test_every_mail_is_there_whole,
              test_the_mail_room_lists_every_lead_and_where_it_stands,
              test_write_only_for_the_ticked_leads,
              test_send_refuses_without_his_email,
              test_a_written_page_is_there_with_the_page_itself,
              test_the_held_page_he_could_not_find_is_there,
              test_his_decisions_on_pages_do_exactly_that,
              test_nothing_picked_is_not_an_action):
        t()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        raise SystemExit(1)
    print("review: all good — he reads it before it leaves.")


if __name__ == "__main__":
    main()
