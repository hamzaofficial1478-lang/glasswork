"""
offer_tests.py — run: python -m pipelines.offer_tests

The service-offering email. "i want a proper service offering based mail with
visuals."

An email is the one thing the agency makes that a customer sees before he does,
so the tests are about the ways it can quietly go out wrong:

  * a photograph paired with the wrong service
  * a picture his server will not serve AS a picture, which is a broken icon
    in the inbox and looks fine in every browser we might check it in
  * an html-only message, which some filters score as spam on sight and which
    a third of readers see as nothing at all
  * a site that will not load taking the whole email down with it

No network and no cabinet of his: the page is a fixture and the server's
answers are stubbed.
"""
import sqlite3
import sys
import tempfile
import urllib.request
from pathlib import Path

from coordination import board
from . import mail, offer_mail as O

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
    con.executescript((Path(__file__).parents[1] / "coordination" / "schema.sql")
                      .read_text(encoding="utf-8"))
    con.commit()
    con.close()
    board.DB = db
    board.ensure_schema()
    return db


def _sql(q, args=()):
    c = sqlite3.connect(board.DB)
    try:
        return c.execute(q, args).fetchall()
    finally:
        c.close()


# His page, cut down: both attribute orders, a repeat of the same card in the
# mobile menu, a service page link, and an unrelated image that must not be
# mistaken for a service.
PAGE = """<html><body>
<a href="/service.php">Services</a>
<div><img src="/assets/img/pages/glass.jfif" alt="Glass Installation">
     <h3><a href="/service.php"><span>01.</span> Glass Installation</a></h3></div>
<div><img alt="Folding Door" src="/assets/img/pages/folding.png"></div>
<div><img src="/assets/img/hero/hero-10-shape-1.png" alt="Decoration"></div>
<div><img src="/assets/img/pages/glass.jfif" alt="Glass Installation"></div>
<img src="/assets/img/worlddeallogo.svg" alt="World Deal Technical">
</body></html>"""


class _Site:
    """His website, answering the way it really does."""

    def __init__(self, page=PAGE, ok=True, heads=None):
        self.page, self.ok = page, ok
        self.heads = heads or {}
        self.asked = []

    def __enter__(self):
        from . import fetcher
        self.real_fetch = fetcher.fetch
        self.real_open = urllib.request.urlopen
        self.real_setting = mail.setting
        fetcher.fetch = self._fetch
        urllib.request.urlopen = self._head
        mail.setting = self._setting
        return self

    def __exit__(self, *a):
        from . import fetcher
        fetcher.fetch = self.real_fetch
        urllib.request.urlopen = self.real_open
        mail.setting = self.real_setting

    def _setting(self, name, default=""):
        return {"SITE_URL": "https://x.test",
                "BUSINESS_PHONE": "+971 56 510 0471",
                "BUSINESS_NAME": "World Deal Technical Services"}.get(name, default)

    def _fetch(self, url, timeout=25, allow_paid=False):
        if not self.ok:
            return {"ok": False, "error": "connection refused"}
        return {"ok": True, "html": self.page}

    def _head(self, req, timeout=12):
        url = req.full_url if hasattr(req, "full_url") else str(req)
        self.asked.append(url)
        kind, size = self.heads.get(url.rsplit("/", 1)[-1], ("image/png", 120_000))

        class _R:
            headers = {"Content-Type": kind, "Content-Length": str(size)}

            def __enter__(self_): return self_
            def __exit__(self_, *a): return False
        return _R()


# ---------------------------------------------------------------------------

def test_the_photo_belongs_to_the_service_beside_it():
    """
    THE FIRST VERSION PAIRED THEM BY POSITION — first heading to first photo —
    and that is a guess. One extra picture anywhere on the page and every card
    gets somebody else's work on it, which for a trade business is worse than
    having no pictures at all. The alt text is written on the image, so they
    cannot come apart.
    """
    print("\nEvery photograph is the one for that service:")
    _fresh_db()
    with _Site():
        r = O.read_the_site()
    check("it read the page", r["ok"], True)
    got = dict(r["services"])
    check("two services, not five", len(r["services"]), 2)
    check("glass has the glass photo",
          got["Glass Installation"].endswith("/pages/glass.jfif"), True)
    check("the folding door has its own, written the other way round",
          got["Folding Door"].endswith("/pages/folding.png"), True)
    check("a decoration is not a service", "Decoration" in got, False)
    check("nor is the logo", any("logo" in v for v in got.values()), False)
    check("the same card in the mobile menu is not a second service",
          [s for s, _ in r["services"]].count("Glass Installation"), 1)
    check("and it found where the rest of them live",
          r["services_url"], "https://x.test/service.php")


def test_a_picture_the_server_will_not_serve_as_a_picture():
    """
    glass.jfif comes back as text/plain from his hosting. A browser guesses and
    renders it, so it looks perfect on his site and in any preview we open —
    and an email client shows a broken icon. On the first service in the list.
    """
    print("\nA file served as text is not put in an email as a picture:")
    _fresh_db()
    with _Site(heads={"glass.jfif": ("text/plain", 10_176),
                      "folding.png": ("image/png", 120_000)}):
        r = O.build()
    check("the email is still built", r["ok"], True)
    check("both services are still named", len(r["services"]), 2)
    check("but only the real picture is used", r["photos"], 1)
    check("and it says which one and why",
          any("served as 'text/plain'" in w for w in r["warnings"]), True)
    check("naming the service it affects",
          any("Glass Installation" in w for w in r["warnings"]), True)

    print("\n  ...and it goes on the board, not only into a warning:")
    t = _sql("SELECT kind, severity, detail FROM site_issues WHERE found_by='leads-outreach'")
    check("a ticket was filed", len(t), 1)
    check("for IT to pick up", t[0][0], "bug")
    check("with the fix in it", "re-save the file as .jpg" in t[0][2], True)

    print("\n  ...once, not once per build:")
    with _Site(heads={"glass.jfif": ("text/plain", 10_176)}):
        O.build()
        O.build()
    check("still one ticket",
          len(_sql("SELECT id FROM site_issues WHERE found_by='leads-outreach'")), 1)


def test_photographs_too_heavy_to_email_are_reported_but_kept():
    """
    His service photos are up to 2.3MB. Dropping his main service out of his
    own brochure because the picture is heavy is not this file's call — saying
    so loudly is.
    """
    print("\nA 2MB photograph is kept, and he is told:")
    _fresh_db()
    with _Site(heads={"glass.jfif": ("image/jpeg", 2_335_014),
                      "folding.png": ("image/png", 1_583_016)}):
        r = O.build()
    check("nothing was dropped", r["photos"], 2)
    check("the size is named", any("2.3MB" in w for w in r["warnings"]), True)
    check("and the total", any("3.9MB" in w for w in r["warnings"]), True)
    check("with what to do about it",
          any("1600px" in w for w in r["warnings"]), True)
    t = _sql("SELECT kind FROM site_issues WHERE found_by='leads-outreach'")
    check("filed as a speed ticket", [x[0] for x in t], ["speed"])


def test_a_website_that_will_not_load_does_not_take_the_email_with_it():
    print("\nThe site being down costs the pictures and nothing else:")
    _fresh_db()
    with _Site(ok=False):
        r = O.build()
    check("it still built one", r["ok"], True)
    check("with the trade still right", len(r["services"]), 7)
    check("no pictures", r["photos"], 0)
    check("and it says so rather than sending something thinner quietly",
          any("without photographs" in w for w in r["warnings"]), True)
    check("the html is still a whole document",
          r["html"].startswith("<!doctype html>"), True)


def test_it_is_written_for_email_clients():
    print("\nThe things an email has to get right:")
    _fresh_db()
    with _Site():
        r = O.build()
    h = r["html"]
    check("there is a plain-text half at all", bool(r["text"].strip()), True)
    check("and it is not just the html with the tags off", "<" in r["text"], False)
    check("every service is in the text half",
          all(s in r["text"] for s in r["services"]), True)
    check("the text half carries a way to reach him",
          "wa.me/" in r["text"], True)

    check("no svg — Gmail strips it", ".svg" in h, False)
    check("tables, not divs, for the layout", 'role="presentation"' in h, True)
    check("it is 600px wide", "max-width:600px" in h, True)
    check("the preheader is there for the inbox list",
          r["preheader"] in h, True)
    check("every picture has alt text",
          h.count("<img") == h.count("alt="), True)
    check("and a width, for the clients that need one",
          h.count("<img") == h.count('width="260"'), True)
    check("there is one obvious thing to press", "wa.me/" in h, True)
    check("and his number is tappable", 'href="tel:' in h, True)


def test_the_message_that_goes_out_has_both_halves():
    """
    add_alternative, not set_content. Getting that wrong sends an html-only
    message: no text part, which some filters score as spam on sight, and
    nothing at all for a reader with pictures switched off.
    """
    print("\nOne message, two halves, and the plain one survives:")
    _fresh_db()
    sent = {}

    class _SMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): pass
        def login(self, *a): pass
        def send_message(self, msg): sent["msg"] = msg

    import smtplib
    real_smtp, real_ssl = smtplib.SMTP, smtplib.SMTP_SSL
    smtplib.SMTP, smtplib.SMTP_SSL = _SMTP, _SMTP
    real_setting = mail.setting
    mail.setting = lambda n, d="": {"SMTP_HOST": "smtp.test", "SMTP_USER": "me@x.ae",
                                    "SMTP_PASS": "x", "SMTP_PORT": "587",
                                    "SITE_URL": "https://x.test",
                                    "BUSINESS_PHONE": "+971 56 510 0471"}.get(n, d)
    try:
        with _Site():
            # _Site replaces mail.setting too; put the smtp one back over it
            mail.setting = lambda n, d="": {
                "SMTP_HOST": "smtp.test", "SMTP_USER": "me@x.ae", "SMTP_PASS": "x",
                "SMTP_PORT": "587", "SITE_URL": "https://x.test",
                "BUSINESS_PHONE": "+971 56 510 0471",
                "BUSINESS_NAME": "World Deal Technical Services"}.get(n, d)
            out = O.send("hamzaofficial1478@gmail.com")
    finally:
        smtplib.SMTP, smtplib.SMTP_SSL = real_smtp, real_ssl
        mail.setting = real_setting

    check("it reports sent", out.get("ok"), True)
    msg = sent.get("msg")
    check("a message was actually handed to smtp", msg is not None, True)
    kinds = sorted(p.get_content_type() for p in msg.walk()
                   if p.get_content_maintype() == "text")
    check("both halves are in it", kinds, ["text/html", "text/plain"])
    check("addressed to him", msg["To"], "hamzaofficial1478@gmail.com")
    check("with a subject about the trade",
          "Glass, aluminium" in msg["Subject"], True)


def test_sending_to_himself_is_not_outreach():
    """
    It must not count against the daily outreach cap and must not write a row
    saying somebody was contacted — reading his own agency's work should never
    cost him a real lead.
    """
    print("\nA copy to his own inbox is not a lead being contacted:")
    _fresh_db()
    src = (Path(__file__).parent / "offer_mail.py").read_text(encoding="utf-8")
    check("it sends through the owner's-own-address boundary",
          "mail.send_plain(" in src, True)
    check("and never through the outreach one", "_smtp_send" in src, False)
    check("nothing was logged as outreach",
          _sql("SELECT count(*) FROM outreach_log")[0][0], 0)



def test_both_buttons_survive_a_narrow_screen():
    """
    "in mobile view there was no whatsapp contact while in laptop it was."

    They were inline-block anchors side by side, which is fine at 600px and
    fragile at 360 — Gmail's mobile app rewrites widths, and a row that does not
    fit pushes the second button out of view rather than wrapping it under the
    first. Each button is its own table row now, which cannot do that.
    """
    print("\nTwo buttons, each on its own line, at any width:")
    _fresh_db()
    with _Site():
        r = O.build()
    h = r["html"]
    check("WhatsApp is there", "wa.me/971565100471" in h, True)
    check("and so is email", "mailto:teamworlddeal@" in h, True)
    check("neither is inline-block any more",
          "display:inline-block" in h, False)
    check("each is a full-width block", h.count("display:block;background:#25d366") , 1)
    check("the phone is still there, as a line rather than a third button",
          "or call" in h, True)


def test_the_dead_address_never_goes_out():
    """
    info@worlddealtechnicalservices.ae is printed on his live website and the
    domain has no DNS at all — no A record, no MX. Mail to it cannot arrive. It
    must never be the address on a brochure the agency sends.
    """
    print("\nThe address on his site is dead, so it is never the one used:")
    _fresh_db()
    with _Site() as s:
        # his page says the dead one; the email must not
        s.page = PAGE + '<a href="mailto:info@worlddealtechnicalservices.ae">Mail</a>'
        r = O.build()
    check("the dead domain is nowhere in the email",
          "worlddealtechnicalservices.ae" in r["html"], False)
    check("the working address is used instead",
          O.DEFAULT_EMAIL in r["html"], True)
    check("and it is in the plain half too", O.DEFAULT_EMAIL in r["text"], True)

    print("\n  ...and even if the setting itself holds the dead one:")
    real = mail.setting
    mail.setting = lambda n, d="": ("info@worlddealtechnicalservices.ae"
                                    if n == "BUSINESS_EMAIL" else
                                    {"SITE_URL": "https://x.test"}.get(n, d))
    try:
        con = O.contact_of()
    finally:
        mail.setting = real
    check("it is refused and replaced", con["email"], O.DEFAULT_EMAIL)


def test_a_heavy_photo_becomes_a_light_one_when_stock_is_there():
    """
    "you can pick from stock to make mail lighter." His folding door is 2.3MB.
    """
    print("\nA 2MB photograph is swapped for a light one:")
    _fresh_db()
    from . import stock_photos
    real = stock_photos.for_service
    stock_photos.for_service = lambda label, width=600: {
        "ok": True, "url": f"https://images.pexels.com/{label}.jpg?w=600",
        "where": "Pexels", "by": "Someone"}
    try:
        with _Site(heads={"glass.jfif": ("image/jpeg", 2_335_014),
                          "folding.png": ("image/png", 90_000)}):
            r = O.build(images="auto")
    finally:
        stock_photos.for_service = real
    urls = r["html"]
    check("the heavy one came from stock", "images.pexels.com" in urls, True)
    check("the light one is still his own", "/pages/folding.png" in urls, True)
    check("and it says which were swapped",
          any("came from the stock shelf" in w for w in r["warnings"]), True)

    print("\n  ...and with no stock shelf, his own heavy one is put BACK:")
    _fresh_db()
    stock_photos.for_service = lambda label, width=600: {
        "ok": False, "why": "no stock photo API is set up"}
    try:
        with _Site(heads={"glass.jfif": ("image/jpeg", 2_335_014),
                          "folding.png": ("image/png", 90_000)}):
            r = O.build(images="auto")
    finally:
        stock_photos.for_service = real
    # An empty stock shelf must not make the email emptier — which is exactly
    # what it did on the first build: the heavy photo was taken out for a
    # replacement that never came, and the card went out blank.
    check("his photograph is still on the card", "/pages/glass.jfif" in r["html"], True)
    check("nothing was silently lost", r["photos"], 2)
    check("and he is told why stock was not used",
          any("no stock picture was available" in w for w in r["warnings"]), True)

    print("\n  ...and --images site never asks the stock shelf at all:")
    asked = []
    stock_photos.for_service = lambda label, width=600: asked.append(label) or {"ok": False}
    try:
        with _Site():
            O.build(images="site")
    finally:
        stock_photos.for_service = real
    check("not one call", asked, [])


def main():
    real = board.DB
    _fresh_db()
    try:
        test_the_photo_belongs_to_the_service_beside_it()
        test_a_picture_the_server_will_not_serve_as_a_picture()
        test_photographs_too_heavy_to_email_are_reported_but_kept()
        test_a_website_that_will_not_load_does_not_take_the_email_with_it()
        test_it_is_written_for_email_clients()
        test_the_message_that_goes_out_has_both_halves()
        test_sending_to_himself_is_not_outreach()
        test_both_buttons_survive_a_narrow_screen()
        test_the_dead_address_never_goes_out()
        test_a_heavy_photo_becomes_a_light_one_when_stock_is_there()
    finally:
        board.DB = real

    print()
    if _fail:
        print(f"{len(_fail)} FAILED: {_fail}")
        sys.exit(1)
    print("all offer-email tests passed")


if __name__ == "__main__":
    main()
