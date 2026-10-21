"""
mailer_tests.py — run: python3 -m pipelines.mailer_tests

"it sent the mails but how can i confirm if the mails are sent or not i
 checked into my mail sent there is nothing and also again it get failed as i
 think it should sent the mails with delay like 1 mail sent second mail will be
 sent after few seconds like 5 to 8 seconds"

What has to hold:

  one login a batch       not a fresh connection and login for every mail
  a pause between mails   five to eight seconds, as he asked
  a dropped connection    opened again and the mail tried again — and said
                          plainly if it keeps happening
  a refused address       not asked again, and marked as came-back
  his Sent folder         every mail sent is put there, where he looks
  what came back          bounce reports in his inbox are read and marked

A fake mail server and a fake mailbox. Nothing leaves this machine.
"""
import smtplib
import sqlite3
import sys
import tempfile
from pathlib import Path

from coordination import board
from . import mail

_fail = []
ROOT = Path(__file__).parents[1]


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


SETTINGS = {"SMTP_HOST": "smtp.hostinger.com", "SMTP_PORT": "587",
            "SMTP_USER": "team@example.ae", "SMTP_PASS": "pw",
            "SMTP_FROM": "team@example.ae", "OUTREACH_DOMAIN": "example.ae"}


class FakeServer:
    """Behaves like his mail server: logs in, takes mail, drops on request."""
    logins, sent, opened = 0, [], 0
    script = []          # what each send does: "ok", "drop", "refuse", "auth"

    def __init__(self, host, port, timeout=30):
        FakeServer.opened += 1
        self.host = host

    def ehlo(self):
        return (250, b"ok")

    def has_extn(self, name):
        return name == "starttls"

    def starttls(self):
        return (220, b"go")

    def login(self, user, pw):
        if FakeServer.script and FakeServer.script[0] == "auth":
            FakeServer.script.pop(0)
            raise smtplib.SMTPAuthenticationError(535, b"bad password")
        FakeServer.logins += 1

    def send_message(self, msg):
        step = FakeServer.script.pop(0) if FakeServer.script else "ok"
        if step == "drop":
            raise smtplib.SMTPServerDisconnected("Connection unexpectedly closed")
        if step == "timeout":
            raise TimeoutError("_ssl.c:1015: The handshake operation timed out")
        if step == "refuse":
            raise smtplib.SMTPRecipientsRefused(
                {msg["To"]: (550, b"5.1.1 The email account that you tried to reach "
                                   b"does not exist")})
        FakeServer.sent.append(msg["To"])

    def quit(self):
        pass


class FakeMailbox:
    """His IMAP mailbox: folders, what was appended, and what is in the inbox."""
    appended, folders, inbox = [], [], []

    def __init__(self, host, port, timeout=20):
        self.host = host

    def login(self, user, pw):
        return ("OK", [b"in"])

    def list(self):
        return ("OK", FakeMailbox.folders)

    def append(self, box, flags, when, raw):
        FakeMailbox.appended.append((box, flags, raw))
        return ("OK", [b"appended"])

    def select(self, box, readonly=False):
        self.readonly = readonly
        return ("OK", [b"1"])

    def search(self, charset, query):
        ids = b" ".join(str(i + 1).encode() for i in range(len(FakeMailbox.inbox)))
        return ("OK", [ids if "MAILER-DAEMON" in query else b""])

    def fetch(self, i, what):
        return ("OK", [(b"1 (BODY[] {1}", FakeMailbox.inbox[int(i) - 1])])

    def logout(self):
        pass


class _World:
    def __enter__(self):
        import imaplib
        self.real = (mail.setting, smtplib.SMTP, imaplib.IMAP4_SSL, board.DB)
        mail.setting = lambda k, d="": SETTINGS.get(k, d)
        smtplib.SMTP = FakeServer
        imaplib.IMAP4_SSL = FakeMailbox
        FakeServer.logins, FakeServer.sent, FakeServer.opened, FakeServer.script = 0, [], 0, []
        FakeMailbox.appended, FakeMailbox.inbox = [], []
        FakeMailbox.folders = [b'(\\HasNoChildren) "." "INBOX"',
                               b'(\\HasNoChildren \\Sent) "." "INBOX.Sent"',
                               b'(\\HasNoChildren \\Trash) "." "INBOX.Trash"']
        self.waits = []
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
        import imaplib
        mail.setting, smtplib.SMTP, imaplib.IMAP4_SSL, board.DB = self.real
        mail._BATCH = None


def _msg(to):
    return mail._message("team@example.ae", to, "Glass works", "body", "<p>body</p>",
                         "World Deal Technical Services")


def test_one_login_and_a_pause_between_mails():
    print("\nOne login for the batch, and five to eight seconds between mails:")
    with _World() as w:
        with mail.Mailer(sleep=w.waits.append) as m:
            for to in ("a@x.ae", "b@x.ae", "c@x.ae"):
                r = m.deliver(_msg(to))
        check("all three went", FakeServer.sent, ["a@x.ae", "b@x.ae", "c@x.ae"])
        check("over one connection and one login", (FakeServer.opened, FakeServer.logins), (1, 1))
        check("with a pause before the second and the third", len(w.waits), 2)
        check("  each between five and eight seconds",
              all(4.5 <= s <= 8.0 for s in w.waits), True)
        msg = _msg("a@x.ae")
        check("each carries a Date and a Message-ID",
              bool(msg["Date"]) and msg["Message-ID"].endswith("@example.ae>"), True)
        check("from the company's name", "World Deal Technical Services" in msg["From"], True)


def test_a_dropped_connection_is_tried_again():
    print("\nA dropped connection is opened again and the mail tried again:")
    with _World() as w:
        FakeServer.script = ["timeout", "drop", "ok"]
        with mail.Mailer(sleep=w.waits.append) as m:
            r = m.deliver(_msg("a@x.ae"))
        check("it went on the third go", (r["ok"], FakeServer.sent), (True, ["a@x.ae"]))
        check("on a fresh connection each time", FakeServer.opened, 3)
        check("waiting longer before each retry", w.waits, [10, 20])

    print("\n...and if it keeps dropping, it says so plainly and stops:")
    with _World() as w:
        FakeServer.script = ["drop", "drop", "drop"]
        with mail.Mailer(sleep=w.waits.append) as m:
            r = m.deliver(_msg("a@x.ae"))
        check("not sent", r["ok"], False)
        check("said as the server dropping the connection",
              "kept dropping the connection" in r["error"], True)
        check("and marked as worth another go later", r["transient"], True)

    print("\n...but a refused address or a wrong password is not asked again:")
    with _World() as w:
        FakeServer.script = ["refuse"]
        with mail.Mailer(sleep=w.waits.append) as m:
            r = m.deliver(_msg("nobody@x.ae"))
        check("the address was refused, once", (r["refused"], FakeServer.opened), (True, 1))
        check("in plain words", mail.bounce_words("", r["error"]), "the address does not exist")
    with _World() as w:
        FakeServer.script = ["auth"]
        with mail.Mailer(sleep=w.waits.append) as m:
            r = m.deliver(_msg("a@x.ae"))
        check("a refused login is not retried", (r["transient"], FakeServer.opened), (False, 1))
        check("and points at the password on Setup", "password on Setup" in r["error"], True)


def test_every_mail_goes_into_his_sent_folder():
    print("\nEvery mail sent is put in his Sent folder:")
    with _World() as w:
        with mail.Mailer(sleep=w.waits.append) as m:
            r = m.deliver(_msg("a@x.ae"))
        check("it went, and a copy was saved", (r["ok"], r["copied"]), (True, True))
        box, flags, raw = FakeMailbox.appended[0]
        check("into the folder his mailbox marks as Sent", box, "INBOX.Sent")
        check("marked as read", flags, "\\Seen")
        check("the whole mail", b"World Deal Technical Services" in raw, True)

    print("\n...found by name when the mailbox does not mark it:")
    with _World() as w:
        FakeMailbox.folders = [b'(\\HasNoChildren) "/" "INBOX"',
                               b'(\\HasNoChildren) "/" "Sent Items"']
        with mail.Mailer(sleep=w.waits.append) as m:
            m.deliver(_msg("a@x.ae"))
        check("  'Sent Items', quoted for the space", FakeMailbox.appended[0][0], '"Sent Items"')

    print("\n...and a mailbox that will not take it never stops a send:")
    with _World() as w:
        FakeMailbox.folders = [b'(\\HasNoChildren) "." "INBOX"']
        with mail.Mailer(sleep=w.waits.append) as m:
            r1 = m.deliver(_msg("a@x.ae"))
            r2 = m.deliver(_msg("b@x.ae"))
        check("both still went", FakeServer.sent, ["a@x.ae", "b@x.ae"])
        check("no copy, and the reason kept", (r1["copied"], bool(m.copy_error)), (False, True))


# His bounce, as his mail server wrote it.
HIS_BOUNCE = b"""From: Mail Delivery System <MAILER-DAEMON@mailchannels.net>
To: teamworlddeal@worlddealtechnicalservices.com
Subject: Undelivered Mail Returned to Sender
Date: Sun, 27 Sep 2026 14:13:00 +0400
Content-Type: text/plain; charset=utf-8

This is the mail system at host relay.mailchannels.net.

I'm sorry to have to inform you that your message could not
be delivered to one or more recipients. It's attached below.

                   The mail system

<mqasim@uniquerealestate.ae>: host smtp.google.com[74.125.20.27] said:
    550-5.1.1 The email account that you tried to reach does not exist. Please
    try 550-5.1.1 double-checking the recipient's email address for typos or
    550-5.1.1 unnecessary spaces. (in reply to RCPT TO command)
"""

DSN_BOUNCE = b"""From: MAILER-DAEMON@example.net
Subject: Delivery Status Notification (Failure)
MIME-Version: 1.0
Content-Type: multipart/report; report-type=delivery-status; boundary="B"

--B
Content-Type: text/plain

Your message could not be delivered.

--B
Content-Type: message/delivery-status

Reporting-MTA: dns; relay.example.net

Final-Recipient: rfc822; full@busy.ae
Action: failed
Status: 5.2.2
Diagnostic-Code: smtp; 552 5.2.2 Mailbox full

--B--
"""

DELAYED = b"""From: MAILER-DAEMON@example.net
Subject: Delayed Mail (still being retried)
Content-Type: text/plain

<slow@x.ae>: host mx.x.ae said: 451 4.7.1 Try again later
"""


def test_what_came_back_is_read_and_marked():
    print("\nHis bounce is read, and says what it means:")
    got = mail.parse_bounce(HIS_BOUNCE)
    check("the address that came back", [b["address"] for b in got], ["mqasim@uniquerealestate.ae"])
    check("with the status", got[0]["status"], "5.1.1")
    check("in plain words", got[0]["words"], "the address does not exist")
    got = mail.parse_bounce(DSN_BOUNCE)
    check("a standard report is read too",
          [(b["address"], b["words"]) for b in got], [("full@busy.ae", "their mailbox is full")])
    check("a 'still trying' warning is not a bounce", mail.parse_bounce(DELAYED), [])

    print("\n...and the mail and the lead are marked, read-only on his inbox:")
    with _World():
        lid = board.add_lead_if_new({"company": "Unique Real Estate Broker",
                                     "email": "mqasim@uniquerealestate.ae"})
        with sqlite3.connect(board.DB) as c:
            c.execute("INSERT INTO outreach_queue(lead_id,company,to_email,subject,body,"
                      "status,sent_at,campaign_id) VALUES(?,?,?,?,?,'sent',1,1)",
                      (lid, "Unique Real Estate Broker", "mqasim@uniquerealestate.ae",
                       "S", "b"))
        FakeMailbox.inbox = [HIS_BOUNCE]
        r = mail.check_bounces()
        check("one new bounce found", (r["ok"], r["new"]), (True, 1))
        with sqlite3.connect(board.DB) as c:
            row = c.execute("SELECT bounce_reason FROM outreach_queue").fetchone()
            stage = c.execute("SELECT stage FROM leads WHERE id=?", (lid,)).fetchone()[0]
        check("the mail is marked as came back", row[0], "the address does not exist")
        check("and so is the lead", stage, "bounced")
        check("looking twice does not count it twice", mail.check_bounces()["new"], 0)
        from . import campaign
        st = {x["id"]: (x["state"], x["why"]) for x in campaign.recipients()}
        check("the Mail room shows it as bounced", st[lid],
              ("bounced", "the address does not exist"))


def test_the_send_boundary_shares_one_batch():
    print("\nSending to several leads uses one login and a pause between them:")
    with _World() as w:
        real = (mail.daily_cap, mail.sent_today, mail.log_outreach)
        mail.daily_cap, mail.sent_today = (lambda: 20), (lambda con=None: 0)
        mail.log_outreach = lambda *a, **k: 1
        try:
            with mail.batch(sleep=w.waits.append):
                for to in ("a@x.ae", "b@x.ae"):
                    r = mail._smtp_send({"id": 1, "email": to}, "Subject: S\n\nbody")
        finally:
            mail.daily_cap, mail.sent_today, mail.log_outreach = real
        check("both went over one login", (FakeServer.sent, FakeServer.logins),
              (["a@x.ae", "b@x.ae"], 1))
        check("with one pause between them", len(w.waits), 1)
        check("and a copy of each in Sent", len(FakeMailbox.appended), 2)

    print("\nOnly one batch of mail goes out at a time:")
    with _World():
        from . import leads_outreach as O
        board.acquire_lock(O.SEND_LOCK, "someone-else", wait=False)
        failed = []
        check("a second sender waits its turn", O._send_approved(10, failed=failed), [])
        check("  and says why", "already going" in failed[0]["why"], True)


def main():
    for t in (test_one_login_and_a_pause_between_mails,
              test_a_dropped_connection_is_tried_again,
              test_every_mail_goes_into_his_sent_folder,
              test_what_came_back_is_read_and_marked,
              test_the_send_boundary_shares_one_batch):
        t()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        sys.exit(1)
    print("mailer: all good — one login, a pause between mails, copies in Sent, bounces read.")


if __name__ == "__main__":
    main()
