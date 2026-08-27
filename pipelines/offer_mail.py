"""
offer_mail.py — the service-offering email, with real photographs of real work.

    python -m pipelines.offer_mail --out offer.html        look at it first
    python -m pipelines.offer_mail --to you@example.com    send it to yourself
    python -m pipelines.offer_mail --to a@b.ae --send      the same thing
    python -m pipelines.offer_mail --json

"send me a mail from the sending agent my mail is 'hamzaofficial1478@gmail.com'
 ... i want a proper service offering based mail with visuals"

WHAT THIS IS, AND WHAT IT IS NOT

pipelines/leads_outreach.py writes a DIFFERENT email: a short, plain, one-
service note to a stranger who has never heard of us, written per-lead by the
brain. That one stays light on purpose — a cold approach loaded with pictures
lands in Promotions, and the whole point of it is to start a conversation.

This is the other one. It is the business, laid out: everything he does, with a
photograph of each, the emirates he covers, and one obvious way to reach him.
It is for people who have already asked — a reply to an enquiry, a follow-up
after a site visit, the thing you send when somebody says "what else do you
do?". Sending it cold would waste it.

EVERY PICTURE IS ONE OF HIS

The photographs are read off his own website at build time and served from his
own domain. Nothing is generated, nothing is stock, and nothing is hotlinked
from anywhere else — which matters three times over: it is honest, the images
load in Gmail because the domain already has a reputation, and if he changes a
photo on his site the email changes with it.

If his site cannot be read, the email is still built and still correct — it
simply carries the service names without pictures, and says so in the result
rather than quietly sending something thinner than he expected.

WRITTEN FOR EMAIL CLIENTS, NOT FOR BROWSERS

Tables, inline styles, 600px, explicit background colours, width and height on
every image, and real alt text so it reads properly with images switched off —
which is how a good third of people will first see it. No SVG: Gmail strips it,
and his logo is an SVG, so the header is set as type rather than as a broken
picture. A plain-text alternative goes in the same message, because a message
with no text part is a message some filters score as spam on sight.
"""
import argparse
import html as _html
import json
import re
import sys
import urllib.parse

from coordination import board
from . import mail

SITE_TIMEOUT = 25

# The trade, in the order he lists it himself. The label and the photograph are
# read off his site; this is the fallback for when the site cannot be read, so
# the email is still right rather than empty.
FALLBACK_SERVICES = [
    ("Glass installation", ""),
    ("Aluminium installation", ""),
    ("Laminated glass", ""),
    ("Folding doors", ""),
    ("Glass partitions", ""),
    ("Wooden doors", ""),
    ("Sign boards", ""),
]

# A service card on his site is an <img> whose src is under assets/img/pages/
# and whose ALT is the service name:
#
#   <img src=".../assets/img/pages/glass.jfif" alt="Glass Installation">
#
# THE FIRST VERSION READ THE HEADINGS INSTEAD and paired them with the photos
# by position — first heading to first photo, and so on. It found no headings
# at all, because his are wrapped in a link with the number in its own span,
# and even if it had worked, position is a guess: one extra photo anywhere on
# the page and every card gets somebody else's picture on it.
#
# The alt text is written on the image itself, so the label and the photograph
# cannot come apart no matter what else is on the page. Both attribute orders,
# because either is valid html and his site uses both.
_CARD = re.compile(
    r'<img[^>]*?(?:src="([^"]*assets/img/pages/[^"]+)"[^>]*?alt="([^"]*)"'
    r'|alt="([^"]*)"[^>]*?src="([^"]*assets/img/pages/[^"]+)")',
    re.I)


def _text(s):
    return " ".join(re.sub(r"<[^>]+>", " ", s or "").split())


def read_the_site(url="", timeout=SITE_TIMEOUT) -> dict:
    """
    His services and his photographs, off his own home page.

    Deliberately forgiving. This is decoration for an email, not a fact anyone
    acts on, so a site that will not load costs the pictures and nothing else.
    """
    url = (url or mail.setting("SITE_URL") or "").strip()
    if not url:
        return {"ok": False, "why": "no website address saved", "services": []}
    try:
        from . import fetcher
        r = fetcher.fetch(url.rstrip("/") + "/", timeout=timeout, allow_paid=False)
    except Exception as e:
        return {"ok": False, "why": f"could not read {url}: {e}", "services": []}
    if not r.get("ok") or not r.get("html"):
        return {"ok": False, "why": f"could not read {url}: "
                                    f"{r.get('error') or 'no page came back'}",
                "services": []}
    html = r["html"]

    # Where "see the rest" goes. His service cards all point at the same page,
    # so one link is honest and a per-card one would be the same URL seven times.
    m = re.search(r'href="([^"]*service[^"]*\.(?:php|html))"', html, re.I)
    services_url = urllib.parse.urljoin(url, m.group(1)) if m else url

    out, seen = [], set()
    for src_a, alt_a, alt_b, src_b in _CARD.findall(html):
        src, alt = (src_a or src_b), _text(alt_a or alt_b)
        if not src or not alt:
            continue
        photo = urllib.parse.urljoin(url, src)
        # The same card appears in the desktop menu and again in the mobile
        # one. Keyed on the photo, so the email shows each service once.
        if photo in seen:
            continue
        seen.add(photo)
        out.append((alt, photo))
    if not out:
        return {"ok": False, "why": "no service photographs found on the page",
                "services": []}
    return {"ok": True, "services": out, "photos": len(out), "url": url,
            "services_url": services_url}


# Over this and it is worth saying out loud. Not a hard limit: dropping his
# main service out of his own brochure because the photograph is heavy is not a
# decision this file gets to make. But a 2MB picture in an email is a picture
# that does not arrive on a phone in Al Quoz, and he should know.
HEAVY_BYTES = 400_000

# Every card photograph is cropped to this height so a row of cards lines up.
CARD_H = 170


def check_photos(services, timeout=12) -> tuple:
    """
    Ask his server what each photograph actually is, before putting it in an email.

    TWO THINGS THIS CATCHES, AND BOTH ARE REAL ON HIS SITE TODAY.

    A file the server does not serve AS AN IMAGE is a broken-image icon in the
    email, not a photograph. glass.jfif comes back as text/plain because his
    hosting has no mime type for .jfif — a browser guesses and renders it
    anyway, and an email client does not. That one is dropped to a plain block,
    because a coloured rectangle beats a broken icon on the first service in
    the list.

    A file of two megabytes is a photograph that does not arrive on mobile
    data. That one is KEPT and reported, because it is his picture of his work
    and the fix is to resize it, not to hide the service.

    Returns (services, warnings).
    """
    import urllib.request
    out, warns, heavy = [], [], []
    for label, photo in services:
        if not photo:
            out.append((label, photo))
            continue
        kind, size = "", 0
        try:
            req = urllib.request.Request(photo, method="HEAD",
                                         headers={"User-Agent": "glasswork-offer/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                kind = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                size = int(r.headers.get("Content-Length") or 0)
        except Exception:
            # Cannot tell. Keep it: the picture is probably fine and the check
            # is the thing that failed.
            out.append((label, photo))
            continue
        if kind and not kind.startswith("image/"):
            out.append((label, ""))
            name = photo.rsplit("/", 1)[-1]
            warns.append(f"{name} is served as '{kind}', not as an image, so it "
                         f"would show as a broken picture — '{label}' has been "
                         "left without one")
            file_ticket("bug", "medium",
                        f"{name} is served as '{kind}' instead of an image type. "
                        "Browsers guess and show it anyway; email clients and "
                        "some social previews do not, so the photograph is "
                        "invisible outside the website. Either add a mime type "
                        "for .jfif on the hosting, or re-save the file as .jpg.")
            continue
        if size > HEAVY_BYTES:
            heavy.append((photo.rsplit("/", 1)[-1], size))
        out.append((label, photo))
    if heavy:
        total = sum(s for _, s in heavy) / 1_000_000
        worst = ", ".join(f"{n} ({s / 1_000_000:.1f}MB)"
                          for n, s in sorted(heavy, key=lambda x: -x[1])[:3])
        warns.append(f"{len(heavy)} photograph(s) are large — {worst}. That is "
                     f"{total:.1f}MB the email has to pull down, and on mobile "
                     "data most people will not wait. Resizing them to 1600px "
                     "wide would fix it here and speed up your website too")
        file_ticket("speed", "medium",
                    f"{len(heavy)} service photographs are oversized ({worst}; "
                    f"{total:.1f}MB in total). They slow the home page and they "
                    "are too heavy to email. Resize to 1600px wide and save as "
                    "WebP or JPEG.")
    return out, warns


def file_ticket(kind, severity, detail) -> int:
    """
    A fault this found on his site goes on the board, not only in a warning.

    A warning printed by a command he runs occasionally is a warning he reads
    once and forgets. The agency has a desk for site faults now — the Watcher
    files, the Fixer writes the change, he approves, the Releaser carries it
    out — so anything found here joins that queue instead of dying in a
    terminal.

    Deduplicated on the text: this runs every time the email is built, and
    seven copies of the same ticket is a board nobody reads.
    """
    import sqlite3
    import time
    try:
        board.ensure_schema()
        with sqlite3.connect(board.DB, timeout=30) as c:
            dup = c.execute("SELECT id FROM site_issues WHERE detail=? AND"
                            " status IN ('open','fixing','in_review')",
                            (detail,)).fetchone()
            if dup:
                return dup[0]
            return c.execute(
                "INSERT INTO site_issues(kind,severity,detail,url,status,"
                " found_by,found_at) VALUES(?,?,?,?, 'open','leads-outreach',?)",
                (kind, severity, detail, (mail.setting("SITE_URL") or "").strip(),
                 int(time.time()))).lastrowid
    except Exception:
        return 0


def contact_of(html="") -> dict:
    """His phone, WhatsApp and site — from settings first, his own page second."""
    g = lambda k: (mail.setting(k) or "").strip()      # noqa: E731
    site = g("SITE_URL").rstrip("/")
    phone = g("BUSINESS_PHONE")
    wa = g("BUSINESS_WHATSAPP") or phone
    if html:
        if not phone:
            m = re.search(r'href="tel:([^"]+)"', html)
            phone = m.group(1).strip() if m else ""
        if not wa:
            m = re.search(r'href="https?://(?:wa\.me|api\.whatsapp\.com/send\?phone=)'
                          r'(\+?\d[\d\s\-()]*)', html)
            wa = m.group(1).strip() if m else phone
    return {"site": site, "phone": phone, "whatsapp": wa,
            "wa_digits": re.sub(r"\D", "", wa or "")}


def business() -> dict:
    """Who the email is from. Settings, then sensible defaults."""
    g = lambda k, d="": (mail.setting(k) or d).strip()   # noqa: E731
    return {
        "name": g("BUSINESS_NAME") or g("AGENCY_NAME") or "World Deal Technical Services",
        "tagline": g("BUSINESS_TAGLINE") or "Glass, aluminium and wooden works",
        "areas": [a.strip() for a in
                  (g("BUSINESS_AREAS") or "Dubai, Abu Dhabi, Sharjah").split(",")
                  if a.strip()],
        "years": g("BUSINESS_YEARS") or "10",
    }


# ---------------------------------------------------------------------------
# THE EMAIL
# ---------------------------------------------------------------------------

def build(url="", timeout=SITE_TIMEOUT) -> dict:
    """{subject, preheader, html, text, services, photos, warnings}."""
    biz = business()
    site = read_the_site(url, timeout=timeout)
    services = site.get("services") or FALLBACK_SERVICES
    warnings = []
    if not site.get("ok"):
        warnings.append(site.get("why") or "could not read the website")
        warnings.append("the email was built without photographs — the service "
                        "names are right, there are just no pictures")
    else:
        services, photo_warnings = check_photos(services)
        warnings += photo_warnings

    con = contact_of()
    if not con["site"]:
        warnings.append("no website address saved, so the email has no link to "
                        "your site — set SITE_URL on /setup")
    if not con["wa_digits"]:
        warnings.append("no phone or WhatsApp number saved, so the email has no "
                        "button to press — set BUSINESS_PHONE on /setup")

    where = _join(biz["areas"])
    subject = f"Glass, aluminium and wooden works across {where}"
    preheader = (f"{biz['years']}+ years in the trade. "
                 f"{len(services)} services, photographs of recent work, and "
                 "one number to call.")
    more = site.get("services_url") or con["site"]
    return {"ok": True, "subject": subject, "preheader": preheader,
            "html": _html_email(biz, services, con, preheader, more),
            "text": _text_email(biz, services, con),
            "services": [s for s, _ in services],
            "photos": sum(1 for _, p in services if p),
            "site_read": bool(site.get("ok")),
            "warnings": warnings}


def _join(items):
    items = [i for i in items if i]
    if len(items) <= 1:
        return items[0] if items else ""
    return ", ".join(items[:-1]) + " and " + items[-1]


def _text_email(biz, services, con) -> str:
    """
    The plain-text half of the same message.

    Not an afterthought. A message with no text part scores badly with spam
    filters on its own, and plenty of people read mail with pictures switched
    off — for them this IS the email.
    """
    lines = [
        f"{biz['name']} — {biz['tagline']}",
        "",
        f"{biz['years']}+ years of glass and aluminium work across "
        f"{_join(biz['areas'])}. Supply, fabrication and installation, all "
        "done by our own fitters.",
        "",
        "WHAT WE DO",
    ]
    lines += [f"  - {label}" for label, _ in services]
    lines += ["", "GET A PRICE"]
    if con["wa_digits"]:
        lines.append(f"  WhatsApp: https://wa.me/{con['wa_digits']}")
    if con["phone"]:
        lines.append(f"  Call:     {con['phone']}")
    if con["site"]:
        lines.append(f"  Website:  {con['site']}")
    lines += ["",
              "Send a photo of the opening and we will come back with a price "
              "and a date.", ""]
    return "\n".join(lines)


def _html_email(biz, services, con, preheader, more="") -> str:
    e = _html.escape
    name, tagline = e(biz["name"]), e(biz["tagline"])
    where = e(_join(biz["areas"]))

    # Two per row. A single column of seven photographs is a very long email;
    # three across is unreadable on a phone, where most of these are opened.
    cards = []
    for i in range(0, len(services), 2):
        pair = services[i:i + 2]
        tds = []
        for label, photo in pair:
            link = (lambda inner: f'<a href="{e(more)}" '
                    f'style="text-decoration:none;color:inherit">{inner}</a>'
                    ) if more else (lambda inner: inner)
            if photo:
                # EVERY PHOTOGRAPH THE SAME HEIGHT, or the cards in a row come
                # out ragged — his pictures are all different shapes, and the
                # first build put a tall window next to a short one and looked
                # unfinished. object-fit crops rather than squashes, and the
                # height attribute is there for the older Outlook engine that
                # ignores it.
                inner = (
                    f'<tr><td style="font-size:0;line-height:0">'
                    + link(f'<img src="{e(photo)}" width="260" height="{CARD_H}" '
                           f'alt="{e(label)}" '
                           f'style="display:block;width:100%;max-width:260px;'
                           f'height:{CARD_H}px;object-fit:cover;border:0" />')
                    + f'</td></tr>'
                      f'<tr><td style="padding:10px 12px;font-size:14px;'
                      f'font-weight:600;color:#12313a">{e(label)}</td></tr>')
            else:
                # NO PHOTOGRAPH IS ONE SOLID TILE, NOT AN EMPTY BOX WITH A
                # CAPTION. The first version drew the dark block and then the
                # label underneath it as well, so the card said "Glass
                # Installation" twice — which reads as a mistake rather than as
                # a design. One tile, the name in it, and its height set to the
                # picture plus the caption so the row still lines up.
                inner = (f'<tr><td style="font-size:0;line-height:0">'
                         + link(f'<div style="background:#12313a;'
                                f'height:{CARD_H + 37}px;color:#8fd3e0;'
                                f'font-family:Helvetica,Arial,sans-serif;'
                                f'font-size:15px;font-weight:700;'
                                f'text-align:center;line-height:{CARD_H + 37}px">'
                                f'{e(label)}</div>')
                         + '</td></tr>')
            tds.append(
                f'<td width="50%" valign="top" style="padding:0 6px 12px">'
                f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
                f'border="0" style="background:#ffffff;border:1px solid #e2eaed;'
                f'border-radius:7px;overflow:hidden">'
                f'{inner}</table></td>')
        if len(pair) == 1:
            tds.append('<td width="50%">&nbsp;</td>')
        cards.append("<tr>" + "".join(tds) + "</tr>")
    grid = "".join(cards)

    buttons = []
    if con["wa_digits"]:
        buttons.append(
            f'<a href="https://wa.me/{e(con["wa_digits"])}" '
            f'style="display:inline-block;background:#25d366;color:#ffffff;'
            f'text-decoration:none;font-weight:700;font-size:15px;padding:13px 26px;'
            f'border-radius:6px;margin:0 6px 8px 0">WhatsApp us</a>')
    if con["phone"]:
        buttons.append(
            f'<a href="tel:{e(re.sub(r"[^+0-9]", "", con["phone"]))}" '
            f'style="display:inline-block;background:#12313a;color:#ffffff;'
            f'text-decoration:none;font-weight:700;font-size:15px;padding:13px 26px;'
            f'border-radius:6px;margin:0 6px 8px 0">{e(con["phone"])}</a>')

    site_link = ""
    if con["site"]:
        shown = con["site"].replace("https://", "").replace("http://", "").rstrip("/")
        site_link = (f'<a href="{e(con["site"])}" style="color:#0b6b7a;'
                     f'text-decoration:none">{e(shown)}</a>')

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{name}</title></head>
<body style="margin:0;padding:0;background:#eef2f4">
<!-- The line the inbox shows next to the subject. Hidden in the body, and
     padded so the client does not pull the first heading in after it. -->
<div style="display:none;max-height:0;overflow:hidden;opacity:0">{e(preheader)}
&#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       bgcolor="#eef2f4" style="background:#eef2f4;padding:18px 8px">
<tr><td align="center">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0"
       style="max-width:600px;width:100%;background:#ffffff;border-radius:10px;
              overflow:hidden;font-family:Helvetica,Arial,sans-serif;color:#1f2b30">

  <!-- His logo is an SVG and Gmail strips SVG, so the header is type. A
       wordmark that always renders beats a picture that sometimes does. -->
  <tr><td bgcolor="#12313a" style="background:#12313a;padding:20px 24px">
    <div style="color:#ffffff;font-size:20px;font-weight:700;letter-spacing:.2px">
      {name}</div>
    <div style="color:#8fd3e0;font-size:13px;margin-top:4px">{tagline}</div>
  </td></tr>

  <tr><td style="padding:24px 24px 4px">
    <div style="font-size:19px;font-weight:700;line-height:1.35;color:#12313a">
      {e(biz['years'])}+ years of glass and aluminium work<br>across {where}.</div>
    <p style="margin:12px 0 0;font-size:15px;line-height:1.65;color:#3c4c53">
      Supply, fabrication and installation — all of it by our own fitters, not
      subcontracted out. Villas, offices, shopfronts and fit-outs.</p>
  </td></tr>

  <tr><td style="padding:20px 18px 0">
    <div style="font-size:11.5px;letter-spacing:.1em;text-transform:uppercase;
                color:#0b6b7a;font-weight:700;padding:0 6px 10px">What we do</div>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
           border="0">{grid}</table>
  </td></tr>

  <tr><td align="center" style="padding:8px 24px 4px">
    <div style="font-size:15px;font-weight:600;color:#12313a;margin-bottom:12px">
      Send a photo of the opening and we will come back with a price and a date.</div>
    {''.join(buttons)}
  </td></tr>

  <tr><td bgcolor="#f7fafb" style="background:#f7fafb;border-top:1px solid #e2eaed;
                 padding:16px 24px;font-size:13px;color:#5a6c73">
    <div style="font-weight:600;color:#1f2b30">{name}</div>
    <div style="margin-top:5px">Covering {where}.</div>
    {f'<div style="margin-top:5px">{site_link}</div>' if site_link else ''}
  </td></tr>

</table></td></tr></table></body></html>"""


# ---------------------------------------------------------------------------
# SENDING
# ---------------------------------------------------------------------------

def send(to: str, url="", timeout=SITE_TIMEOUT) -> dict:
    """
    Send it to an address typed by hand. Never to anything out of the leads table.

    Goes through mail.send_plain, which is the boundary for exactly this: mail
    to the owner's own inbox, not counted against the outreach cap and not
    logged as somebody having been contacted. See the note on it — a preview he
    asked for must not cost him a real lead.
    """
    built = build(url, timeout=timeout)
    out = mail.send_plain(to, built["subject"], built["text"],
                          html=built["html"])
    out["subject"] = built["subject"]
    out["warnings"] = built["warnings"]
    out["photos"] = built["photos"]
    out["services"] = built["services"]
    return out


def run(agent="leads-outreach", to="", url="") -> dict:
    with board.agent_run(agent) as run_row:
        if not to:
            r = build(url)
            run_row.note(f"built the offer email — {len(r['services'])} services, "
                         f"{r['photos']} photographs")
            return r
        r = send(to, url)
        run_row.note(f"sent the offer email to {to}" if r.get("ok")
                     else f"could not send: {r.get('error')}")
        return r


def main():
    ap = argparse.ArgumentParser(
        description="The service-offering email, with photographs of real work.")
    ap.add_argument("--to", default="", help="send it to this address")
    ap.add_argument("--send", action="store_true", help="same as --to; kept for habit")
    ap.add_argument("--out", default="", help="write the html here and send nothing")
    ap.add_argument("--url", default="", help="read the services from this site")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.out:
        r = build(a.url)
        from pathlib import Path
        Path(a.out).write_text(r["html"], encoding="utf-8")
        if a.json:
            print(json.dumps({k: v for k, v in r.items() if k != "html"}, indent=2))
        else:
            print(f"Written to {a.out}")
            print(f"  subject   {r['subject']}")
            print(f"  services  {len(r['services'])} — {', '.join(r['services'])}")
            print(f"  photos    {r['photos']}")
            for w in r["warnings"]:
                print(f"  !  {w}")
        return 0

    if not a.to:
        r = build(a.url)
        print(json.dumps({k: v for k, v in r.items() if k != "html"}, indent=2)
              if a.json else
              f"Built it: {r['subject']}\n"
              f"  {len(r['services'])} services, {r['photos']} photographs.\n"
              + "".join(f"  !  {w}\n" for w in r["warnings"])
              + "\nNothing was sent. Add --to you@example.com to send it, or "
                "--out offer.html to look at it first.")
        return 0

    r = send(a.to, a.url)
    if a.json:
        print(json.dumps(r, indent=2))
    elif r.get("ok"):
        print(f"Sent to {r['to']} from {r['from']}.")
        print(f"  subject  {r['subject']}")
        print(f"  {len(r['services'])} services, {r['photos']} photographs.")
        for w in r.get("warnings") or []:
            print(f"  !  {w}")
    else:
        print("Not sent: " + str(r.get("error")))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
