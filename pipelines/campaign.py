"""
campaign.py — one mail from the company, to every lead he ticks.

    python -m pipelines.campaign --show              the current campaign, as text
    python -m pipelines.campaign --html out.html     the mail as a lead will see it
    python -m pipelines.campaign --create            build one from his website

WHAT HE SAID

  "the mails written are too basic and too generic ... with out any offer with
   out any poster and as a solo worker you can see it saying I word and using
   my own name ... while the message is being sent by the Company ... no any
   marketing design layout no any services intro ... we can use 1 mail for all
   leads no need to write different mails for each lead"

All of it was true, and some of it was worse than it looked:

  one-man voice        the writer was told to sound like "a tradesman" and
                       signed with the name and Gmail address of the person
                       running it — Claude on his PC knows who is signed in
  no design at all     mail.render() built a branded layout, and the sender
                       never used it: every mail left as bare text
  no offer             the Auditor refuses any price or concession, as the
                       handbook says it must, and nothing asked HIM for one
  per-lead writing     one model call per lead, each a fresh chance to get it
                       wrong — two of seven were stopped, for a placeholder
                       ("[number]") the writer invented because nobody had
                       told it his number

WHAT THIS IS INSTEAD

One campaign. The company speaks ("we"), the layout is designed — a poster at
the top with his headline, his services, his offer and three of his own
photographs, then the introduction, what we do, why clients choose us, and two
buttons (WhatsApp, email). Each lead gets the same mail with their own name in
the greeting. He reads it once, exactly as it will arrive, approves it once,
ticks who gets it, and presses Send.

THE OFFER IS HIS, WORD FOR WORD

He types it. It is shown in its own box exactly as he typed it, and the Auditor
lets those words — and only those — through its price-and-concession check
(auditor.check_outreach, owner_offer). An offer anywhere else in the mail is
still refused, so the writer can never add a second one of its own. Approving
the campaign is his sign-off on it, which is what the handbook asks for.

Change a word after approving and the campaign goes back to draft: nothing he
has not read goes out, including mail he ticked before the change
(for_queued refuses it and says why).

THE GATES THAT STILL APPLY, UNCHANGED

  Security       every lead screened again at the moment of sending
  once only      a business already written to is never written to again
  daily limit    and the sending domain — inside mail._smtp_send, as always
  his tick       only the leads he ticks, only after he has approved the mail
"""
import argparse
import html as _html
import json
import re
import sqlite3
import sys
import time
import urllib.parse

from coordination import auditor, board, uae
from . import mail, offer_mail

AGENT = "leads-outreach"
SECTOR = "Leads"

MOST_TILES = 6          # services in "What we do"; the rest are on his site
STRIP = 3               # photographs in the poster
TEXT_FIELDS = {"subject": 70, "preheader": 130, "headline": 80,
               "intro": 900, "cta": 360}
WHY_MOST = 5
FIELDS = tuple(TEXT_FIELDS) + ("why_us", "lines", "focus")

# What each of his services is, in a line. Matched on a word in the service's
# own name, so "Aluminum Installation" and "aluminium works" both find theirs.
# Plain facts about the work, nothing about results nobody can stand behind.
SERVICE_LINES = (
    ("partition", "Framed and frameless glass partitions for offices and fit-outs."),
    ("lamin", "Laminated safety glass for balustrades, skylights and shopfronts."),
    ("folding", "Folding and sliding doors for terraces, shops and offices."),
    ("shower", "Frameless shower screens and enclosures, made to measure."),
    ("mirror", "Made-to-measure mirrors for gyms, salons, hotels and homes."),
    ("pergola", "Aluminium and wooden pergolas for villas, roofs and terraces."),
    ("curtain", "Curtain wall and façade glazing — repairs and replacement."),
    ("window", "Aluminium windows — new, repaired or replaced."),
    ("sign", "Shop signs and boards, made and fixed."),
    ("wood", "Wooden doors, supplied and hung by our own team."),
    ("alumin", "Windows, doors and frames fabricated and installed by our team."),
    ("door", "Glass and aluminium doors, closers and hardware."),
    ("glass", "Toughened and clear glass, cut to size and fitted on site."),
)
ANY_LINE = "Supplied, made to size and installed by our own team."


def _line_for(label) -> str:
    low = (label or "").lower()
    for word, line in SERVICE_LINES:
        if word in low:
            return line
    return ANY_LINE


# ---------------------------------------------------------------------------
# WHO IT IS FROM
# ---------------------------------------------------------------------------

def business() -> dict:
    """The company: name, tagline, years, areas. Never a person."""
    b = offer_mail.business()
    b["areas_phrase"] = uae.served_phrase()
    # For the top of the mail: the emirates by name, or the country in full —
    # "the UAE" on its own reads like the end of a sentence.
    b["areas_label"] = ("United Arab Emirates" if b["areas_phrase"] == "the UAE"
                        else " · ".join(uae.served()))
    return b


def contact(camp=None) -> dict:
    """Phone, WhatsApp, email and site — what he typed first, then what was
    read off his website when the campaign was built."""
    saved = ((camp or {}).get("copy") or {}).get("contact") or {}
    base = offer_mail.contact_of()
    out = {k: (saved.get(k) or base.get(k) or "") for k in
           ("site", "phone", "whatsapp", "email")}
    out["wa_digits"] = re.sub(r"\D", "", out["whatsapp"] or out["phone"] or "")
    if offer_mail.DEAD_DOMAIN in (out["email"] or "").lower():
        out["email"] = offer_mail.DEFAULT_EMAIL
    return out


_SUFFIXES = re.compile(
    r"[\s,.-]*\b(?:l\.?\s?l\.?\s?c\.?|o\.?p\.?c\.?|fz(?:-?llc|e|co)?|f\.?z\.?e\.?|"
    r"sole proprietorship|est\.?|establishment|co\.?|ltd\.?|limited|inc\.?)\s*$", re.I)
_SMALL = {"and", "of", "the", "for", "in", "on", "at", "by", "to", "&"}


def tidy_name(name) -> str:
    """
    'PARADISE PROPERTIES MANAGEMENT LLC OPC' → 'Paradise Properties Management'.

    Map listings are full of shouted names and legal suffixes. "Dear PARADISE
    PROPERTIES MANAGEMENT LLC OPC team" reads as a mail merge; this reads as a
    person who knows who they are writing to.
    """
    n = " ".join(str(name or "").split())
    prev = None
    while n and n != prev:
        prev = n
        n = _SUFFIXES.sub("", n).strip(" ,.-")
    letters = [c for c in n if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.7:
        words = []
        for i, w in enumerate(n.split()):
            low = w.lower()
            if i and low in _SMALL:
                words.append(low)
            elif len(w) <= 3 and not re.search(r"[aeiouy]", low):
                words.append(w.upper())           # FMP, JLT — initials stay initials
            else:
                words.append(low[:1].upper() + low[1:])
        n = " ".join(words)
    return n


def greeting(lead) -> str:
    who = tidy_name((lead or {}).get("company") or (lead or {}).get("name"))
    if not who:
        return "Hello,"
    return f"Dear {who} team," if not who.lower().endswith("team") else f"Dear {who},"


# ---------------------------------------------------------------------------
# THE WORDS
# ---------------------------------------------------------------------------

def standard_copy(biz, tiles) -> dict:
    """
    The campaign as it stands before anyone rewrites it: professional, the
    company speaking, and nothing in it that is not true of the business.
    Always available — no model needed — so the Mail room never opens empty.
    """
    areas = biz.get("areas_phrase") or "the UAE"
    years = str(biz.get("years") or "10")
    return {
        "subject": "Glass & aluminium works for your properties",
        "preheader": ("Supply, fabrication and installation by our own team — "
                      "send a photo on WhatsApp for a quick quote."),
        "headline": f"Glass & aluminium works across {areas}",
        "intro": (
            f"We are {biz['name']}, a glass and aluminium company with {years}+ "
            f"years in the trade. We work with property managers, landlords, "
            f"fit-out contractors and businesses across {areas}.\n\n"
            "Whether it is one cracked pane, a sliding door that no longer runs "
            "or a complete shopfront, our own team measures on site, makes it to "
            "size and installs it — one point of contact from quote to handover, "
            "and nothing passed on to a subcontractor."),
        "why_us": ["Our own fitters — nothing is subcontracted",
                   "Measured on site, made to fit first time",
                   "Quotes from a photo sent on WhatsApp",
                   "Repairs and replacements as well as new work",
                   f"Covering {areas}"],
        "cta": ("Have a job coming up, or a snag list with glass or aluminium on "
                "it? Send us a photo and the rough sizes on WhatsApp and we will "
                "come back with a price and a date."),
        "lines": {t["label"]: _line_for(t["label"]) for t in tiles},
        "focus": "",
    }


def _clean(copy, tiles=()) -> dict:
    """Keep only known fields, as plain text, inside their lengths."""
    out = {}
    for k, most in TEXT_FIELDS.items():
        v = str((copy or {}).get(k) or "").replace("\r", "")
        v = re.sub(r"[*#`]+", "", v)                    # no chat-window markdown
        v = "\n\n".join(" ".join(p.split()) for p in re.split(r"\n\s*\n", v) if p.strip())
        out[k] = v[:most].strip()
    why = (copy or {}).get("why_us") or []
    if isinstance(why, str):
        why = [w for w in why.splitlines()]
    out["why_us"] = [re.sub(r"^[\s\-•*·]+", "", str(w)).strip()[:90]
                     for w in why if str(w).strip()][:WHY_MOST]
    lines = (copy or {}).get("lines") or {}
    labels = [t["label"] for t in tiles] or list(lines)
    out["lines"] = {lab: (str(lines.get(lab) or "").strip()[:140] or _line_for(lab))
                    for lab in labels}
    out["focus"] = str((copy or {}).get("focus") or "").strip()[:200]
    if (copy or {}).get("contact"):
        out["contact"] = {k: str(v or "").strip()[:120]
                          for k, v in copy["contact"].items()
                          if k in ("phone", "whatsapp", "email", "site")}
    return out


# ---------------------------------------------------------------------------
# THE MAIL
# ---------------------------------------------------------------------------

E = _html.escape
INK, TEAL, SKY, AMBER, PAPER = "#12313a", "#0b6b7a", "#8fd3e0", "#f2a93b", "#eef2f4"


def _paras(text):
    return [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]


def _is_stock(url) -> bool:
    host = (urllib.parse.urlsplit(url or "").hostname or "").lower()
    return host in auditor.STOCK_HOSTS


def opt_out_line(lead) -> str:
    who = tidy_name((lead or {}).get("company")) or "your business"
    return (f"We found {who} in a public business listing. If you would rather "
            "not hear from us, reply \"stop\" and we won't write again.")


def render(camp, lead=None) -> dict:
    """
    The campaign as this lead will receive it: {subject, preheader, text, html}.
    Pure — no network, nothing written. The words are the campaign's; the
    greeting and the opt-out line name the lead; the rest is facts about the
    business.
    """
    lead = lead or {}
    biz = business()
    con = contact(camp)
    copy = camp.get("copy") or {}
    tiles = camp.get("tiles") or []
    offer = (camp.get("offer") or "").strip()
    until = (camp.get("offer_until") or "").strip()
    lines = copy.get("lines") or {}
    subject = copy.get("subject") or "Glass & aluminium works"
    hello = greeting(lead)
    stop = opt_out_line(lead)
    shown_site = (con["site"] or "").replace("https://", "").replace("http://", "").rstrip("/")

    # ---- plain text: the whole mail, for inboxes that show no pictures ----
    t = [hello, ""]
    for p in _paras(copy.get("intro")):
        t += [p, ""]
    if offer:
        t += ["OFFER: " + offer + (f" (valid until {until})" if until else ""), ""]
    t.append("WHAT WE DO")
    for tile in tiles[:MOST_TILES]:
        t.append(f"  - {tile['label']}: {lines.get(tile['label']) or _line_for(tile['label'])}")
    if copy.get("why_us"):
        t += ["", "WHY CLIENTS CHOOSE US"] + [f"  - {w}" for w in copy["why_us"]]
    t += ["", copy.get("cta") or "", ""]
    if con["wa_digits"]:
        t.append(f"  WhatsApp: https://wa.me/{con['wa_digits']}")
    if con["phone"]:
        t.append(f"  Call:     {con['phone']}")
    if con["email"]:
        t.append(f"  Email:    {con['email']}")
    if con["site"]:
        t.append(f"  Website:  {con['site']}")
    t += ["", "Kind regards,", f"The team at {biz['name']}",
          f"{biz.get('tagline') or ''} — {biz.get('areas_label') or ''}".strip(" —"),
          "", stop]
    text = "\n".join(t).strip() + "\n"

    # ---- html ------------------------------------------------------------
    photos = poster_photos(tiles)
    tags = " &nbsp;·&nbsp; ".join(E(x["label"]) for x in tiles[:3])
    strip = ""
    if photos:
        w = 548 // len(photos)
        h = 240 if len(photos) == 1 else 140
        cells = "".join(
            f'<td width="{w}" valign="top" style="padding:0 {3 if len(photos) > 1 else 0}px">'
            f'<img src="{E(p["photo"])}" width="{w - 6 if len(photos) > 1 else w}" height="{h}" '
            f'alt="{E(p["label"])}" style="display:block;width:100%;height:{h}px;'
            f'object-fit:cover;border:0;border-radius:6px">'
            f'<div style="font-size:12px;color:{SKY};padding:6px 2px 0">{E(p["label"])}</div></td>'
            for p in photos)
        strip = (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
                 f'border="0" style="margin-top:20px"><tr>{cells}</tr></table>')

    badge = ""
    if offer:
        badge = (
            f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
            f'style="margin-top:20px"><tr><td bgcolor="{AMBER}" style="background:{AMBER};'
            f'border-radius:8px;padding:14px 18px;color:#1c1405">'
            f'<div style="font-size:11px;letter-spacing:.14em;text-transform:uppercase;'
            f'font-weight:800">Current offer</div>'
            f'<div style="font-size:19px;line-height:1.35;font-weight:800;margin-top:4px">{E(offer)}</div>'
            + (f'<div style="font-size:12.5px;margin-top:5px">Valid until {E(until)}</div>' if until else "")
            + '</td></tr></table>')

    svc_rows = "".join(
        f'<tr><td valign="top" width="22" style="padding:9px 0 9px;color:{TEAL};'
        f'font-size:16px;line-height:1.2">&#9632;</td>'
        f'<td style="padding:8px 0;border-bottom:1px solid #e6edf0">'
        f'<div style="font-size:15px;font-weight:700;color:{INK}">{E(x["label"])}</div>'
        f'<div style="font-size:14px;color:#4a5c63;margin-top:2px">'
        f'{E(lines.get(x["label"]) or _line_for(x["label"]))}</div></td></tr>'
        for x in tiles[:MOST_TILES])

    why = "".join(
        f'<tr><td valign="top" width="24" style="color:#1f9d55;font-weight:800;'
        f'padding:4px 0">&#10003;</td><td style="padding:4px 0;font-size:14.5px;'
        f'color:#2b3b41">{E(w)}</td></tr>' for w in copy.get("why_us") or [])

    def button(href, label, bg):
        return (f'<tr><td align="center" style="padding:0 0 10px">'
                f'<a href="{E(href)}" style="display:block;background:{bg};color:#ffffff;'
                f'text-decoration:none;font-weight:700;font-size:16px;padding:14px 18px;'
                f'border-radius:7px;text-align:center">{E(label)}</a></td></tr>')
    buttons = ""
    if con["wa_digits"]:
        buttons += button(f"https://wa.me/{con['wa_digits']}", "WhatsApp us a photo", "#1faa53")
    if con["email"]:
        buttons += button(f"mailto:{con['email']}?subject="
                          + urllib.parse.quote("Quote request"), "Email us", INK)
    if buttons:
        buttons = (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
                   f'border="0" style="max-width:330px;margin:0 auto">{buttons}</table>')
    call = (f'<div style="font-size:14px;color:#3c4c53;margin-top:4px">or call '
            f'<a href="tel:{E(re.sub(r"[^+0-9]", "", con["phone"]))}" style="color:{TEAL};'
            f'text-decoration:none;font-weight:700">{E(con["phone"])}</a></div>'
            if con["phone"] else "")
    stock_note = ('<div style="margin-top:6px">Some photographs are illustrative.</div>'
                  if any(_is_stock(p["photo"]) for p in photos) else "")
    contact_lines = " &nbsp;·&nbsp; ".join(filter(None, [
        E(con["phone"]) if con["phone"] else "",
        f'<a href="mailto:{E(con["email"])}" style="color:{TEAL};text-decoration:none">'
        f'{E(con["email"])}</a>' if con["email"] else "",
        f'<a href="{E(con["site"])}" style="color:{TEAL};text-decoration:none">'
        f'{E(shown_site)}</a>' if con["site"] else ""]))

    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{E(subject)}</title></head>
<body style="margin:0;padding:0;background:{PAPER}">
<div style="display:none;max-height:0;overflow:hidden;opacity:0">{E(copy.get("preheader") or "")}
&#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;&#847;&zwnj;&nbsp;</div>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"
       bgcolor="{PAPER}" style="background:{PAPER};padding:18px 8px">
<tr><td align="center">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" border="0"
       style="max-width:600px;width:100%;background:#ffffff;border-radius:10px;overflow:hidden;
              font-family:Helvetica,Arial,sans-serif;color:#1f2b30">

  <tr><td bgcolor="#0b2229" style="background:#0b2229;padding:16px 26px">
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0"><tr>
      <td style="color:#ffffff;font-size:17px;font-weight:800;letter-spacing:.2px">{E(biz["name"])}</td>
      <td align="right" style="color:{SKY};font-size:12px">{E(biz.get("areas_label") or "")}</td>
    </tr></table>
  </td></tr>

  <tr><td bgcolor="{INK}" style="background:{INK};padding:28px 26px 26px">
    <div style="font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:{SKY};
                font-weight:700">{tags}</div>
    <div style="font-size:28px;line-height:1.2;font-weight:800;color:#ffffff;margin-top:10px">
      {E(copy.get("headline") or "")}</div>
    <div style="font-size:15px;line-height:1.55;color:#cfe6ec;margin-top:10px">
      {E(copy.get("preheader") or "")}</div>
    {badge}
    {strip}
  </td></tr>

  <tr><td style="padding:26px 26px 6px;font-size:15.5px;line-height:1.65;color:#2b3b41">
    <p style="margin:0 0 14px">{E(hello)}</p>
    {"".join(f'<p style="margin:0 0 14px">{E(p)}</p>' for p in _paras(copy.get("intro")))}
  </td></tr>

  <tr><td style="padding:6px 26px 4px">
    <div style="font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:{TEAL};
                font-weight:800;padding-bottom:4px">What we do</div>
    <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">{svc_rows}</table>
  </td></tr>

  {f'''<tr><td style="padding:20px 26px 4px">
    <div style="font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:{TEAL};
                font-weight:800;padding-bottom:6px">Why clients choose us</div>
    <table role="presentation" cellpadding="0" cellspacing="0" border="0">{why}</table>
  </td></tr>''' if why else ""}

  <tr><td align="center" style="padding:24px 26px 8px">
    <div style="font-size:15.5px;line-height:1.55;color:{INK};font-weight:600;margin-bottom:16px;
                max-width:470px">{E(copy.get("cta") or "")}</div>
    {buttons}
    {call}
  </td></tr>

  <tr><td style="padding:18px 26px 22px;font-size:14.5px;line-height:1.6;color:#2b3b41">
    Kind regards,<br><b>The team at {E(biz["name"])}</b><br>
    <span style="color:#5a6c73;font-size:13px">{E(biz.get("tagline") or "")}</span>
  </td></tr>

  <tr><td bgcolor="#f5f8f9" style="background:#f5f8f9;border-top:1px solid #e2eaed;
                 padding:16px 26px;font-size:12.5px;line-height:1.55;color:#5a6c73">
    <div style="font-weight:700;color:#1f2b30">{E(biz["name"])}</div>
    <div style="margin-top:4px">{contact_lines}</div>
    <div style="margin-top:10px">{E(stop)}</div>
    {stock_note}
  </td></tr>

</table></td></tr></table></body></html>"""
    return {"subject": subject, "preheader": copy.get("preheader") or "",
            "text": text, "html": html,
            "draft": f"Subject: {subject}\n\n{text}"}


# ---------------------------------------------------------------------------
# THE AUDITOR, ON THE WHOLE MAIL
# ---------------------------------------------------------------------------

SAMPLE_LEAD = {"company": "Example Properties", "email": "info@example.ae",
               "segment": "property_management", "area": "Dubai"}


def his_words(camp) -> list:
    """What he wrote himself and may say: the offer and its closing date."""
    offer = (camp.get("offer") or "").strip()
    until = (camp.get("offer_until") or "").strip()
    return [w for w in (offer, until, f"valid until {until}" if until else "") if w]


def audit(camp, lead=None) -> dict:
    """The Auditor's verdict on the campaign as a lead would receive it."""
    lead = lead or SAMPLE_LEAD
    r = render(camp, lead)
    v = auditor.check_outreach(r["draft"], lead=lead, owner_offer=his_words(camp),
                               voice="company")
    # A designed mail is scanned, not read line by line; the length note meant
    # for a stranger's plain-text first mail does not apply to it.
    v["warnings"] = [w for w in v["warnings"] if not re.match(r"\d+ words", w)]
    copy = camp.get("copy") or {}
    if not _paras(copy.get("intro")):
        v["blocking"].append("there is no introduction — say who the company is")
    if not (camp.get("tiles") or []):
        v["blocking"].append("no services listed — the mail has to say what we do")
    heavy = [t for t in poster_photos(camp.get("tiles") or [])
             if (t.get("bytes") or 0) > offer_mail.HEAVY_BYTES]
    if heavy:
        mb = sum(t["bytes"] for t in heavy) / 1_000_000
        v["warnings"].append(
            f"{len(heavy)} of the poster's photographs are heavy ({mb:.1f}MB in all) — "
            "slow on a phone. Add a Pexels or Pixabay key on Setup and lighter pictures "
            "are used instead, or resize them on your website (1600px wide)")
    con = contact(camp)
    if not con["wa_digits"]:
        v["warnings"].append("no phone or WhatsApp number — the mail has only the "
                             "email button. Type your number in the campaign below")
    v["pass"] = not v["blocking"]
    v["why"] = ("fit to send" if v["pass"] else
                f"{len(v['blocking'])} thing(s) stop it: " + v["blocking"][0])
    return v


# ---------------------------------------------------------------------------
# THE CABINET
# ---------------------------------------------------------------------------

def _conn():
    c = sqlite3.connect(board.DB, timeout=30)
    c.row_factory = sqlite3.Row
    board._migrate(c)
    return c


def _load(r) -> dict:
    if not r:
        return {}
    d = dict(r)
    for k, default in (("copy", {}), ("tiles", []), ("audit", {}), ("warnings", [])):
        try:
            d[k] = json.loads(d.get(k) or "null") or default
        except (TypeError, ValueError):
            d[k] = default
    return d


def current() -> dict:
    """The campaign he is working on — draft or approved — or {}."""
    c = _conn()
    try:
        return _load(c.execute("SELECT * FROM campaigns WHERE status IN ('draft','approved')"
                               " ORDER BY id DESC LIMIT 1").fetchone())
    finally:
        c.close()


def get(cid) -> dict:
    c = _conn()
    try:
        return _load(c.execute("SELECT * FROM campaigns WHERE id=?", (int(cid),)).fetchone())
    finally:
        c.close()


def _write(camp, **cols) -> dict:
    now = int(time.time())
    fields = {"copy": json.dumps(camp.get("copy") or {}),
              "tiles": json.dumps(camp.get("tiles") or []),
              "offer": (camp.get("offer") or "").strip()[:200],
              "offer_until": (camp.get("offer_until") or "").strip()[:60],
              "audit": json.dumps(camp.get("audit") or {}),
              "warnings": json.dumps(camp.get("warnings") or []),
              "written_by": camp.get("written_by") or "standard",
              "updated_at": now, **cols}
    with board._conn() as c:
        board._migrate(c)
        if camp.get("id"):
            sets = ", ".join(f"{k}=?" for k in fields)
            c.execute(f"UPDATE campaigns SET {sets} WHERE id=?",
                      (*fields.values(), int(camp["id"])))
            cid = int(camp["id"])
        else:
            fields.setdefault("status", "draft")
            fields["created_at"] = now
            cols_ = ", ".join(fields)
            cid = c.execute(f"INSERT INTO campaigns({cols_}) VALUES("
                            + ",".join("?" * len(fields)) + ")",
                            tuple(fields.values())).lastrowid
    return get(cid)


# ---------------------------------------------------------------------------
# WHAT HE DOES WITH IT
# ---------------------------------------------------------------------------

def tiles_from_site(timeout=25) -> tuple:
    """
    His services and photographs off his own website, made light enough for a
    mail (offer_mail: heavy ones swapped for a labelled stock picture where one
    is available). Returns (tiles, contact, warnings). Never raises.
    """
    warnings, html = [], ""
    try:
        site = offer_mail.read_the_site(timeout=timeout)
    except Exception as e:
        site = {"ok": False, "why": str(e)}
    services = site.get("services") or offer_mail.FALLBACK_SERVICES
    sizes = {}
    if site.get("ok"):
        try:
            services, w, originals = offer_mail.check_photos(services, drop_heavy=True,
                                                             sizes=sizes)
            services, w = offer_mail.lighten(services, w, mode="auto", originals=originals)
            # The brochure's notes about every heavy photograph on the site are
            # not this mail's business — only the three in the poster are, and
            # audit() says so about those.
            warnings += [x for x in w if " are large" not in x and "no stock picture" not in x]
        except Exception as e:
            warnings.append(f"could not check the photographs: {e}")
    else:
        warnings.append((site.get("why") or "could not read your website")
                        + " — the services are listed without photographs")
    try:
        from . import fetcher
        url = (mail.setting("SITE_URL") or "").strip()
        if url:
            html = (fetcher.fetch(url.rstrip("/") + "/", timeout=timeout,
                                  allow_paid=False) or {}).get("html") or ""
    except Exception:
        html = ""
    con = offer_mail.contact_of(html)
    tiles = [{"label": _label(lab), "photo": photo or "",
              "own": bool(photo) and not _is_stock(photo),
              "bytes": sizes.get(photo, 0) if photo and not _is_stock(photo) else 0}
             for lab, photo in services if str(lab).strip()]
    return tiles, {k: con.get(k) or "" for k in ("phone", "whatsapp", "email", "site")}, warnings


# His site's own spellings, as a customer should read them in a letter.
_SPELLINGS = {"aluminum": "aluminium", "lamination": "laminated", "wood door": "wooden door"}


def _label(raw) -> str:
    """'Aluminum Installation' → 'Aluminium installation'. Sentence case, UK spelling."""
    s = " ".join(str(raw or "").split()).lower()
    for a, b in _SPELLINGS.items():
        s = re.sub(rf"\b{a}\b", b, s)
    return s[:1].upper() + s[1:]


def poster_photos(tiles, most=STRIP) -> list:
    """
    The photographs for the poster: his own light ones first, then labelled
    stock, then his heavy ones only if nothing else is left. Four of the
    photographs on his site are over a megabyte; three of those at the top of
    a cold mail is five megabytes a stranger's phone has to pull down.
    """
    have = [t for t in tiles if t.get("photo")]
    light = [t for t in have if t.get("own") and 0 < (t.get("bytes") or 0) <= offer_mail.HEAVY_BYTES]
    unknown = [t for t in have if t.get("own") and not t.get("bytes")]
    stock = [t for t in have if not t.get("own")]
    heavy = [t for t in have if t.get("own") and (t.get("bytes") or 0) > offer_mail.HEAVY_BYTES]
    return (light + unknown + stock + heavy)[:most]


def create(offer="", offer_until="", focus="", read_site=True) -> dict:
    """
    A fresh campaign from his website and the standard wording, as a draft.
    Any campaign he was working on is retired — one campaign at a time.
    """
    if read_site:
        tiles, con, warnings = tiles_from_site()
    else:
        tiles = [{"label": lab, "photo": "", "own": False}
                 for lab, _p in offer_mail.FALLBACK_SERVICES]
        con, warnings = {}, []
    copy = standard_copy(business(), tiles)
    copy["focus"] = (focus or "").strip()[:200]
    copy["contact"] = con
    camp = {"copy": _clean(copy, tiles), "tiles": tiles[:MOST_TILES + 2],
            "offer": offer, "offer_until": offer_until,
            "warnings": warnings, "written_by": "standard"}
    camp["audit"] = audit(camp)
    with board._conn() as c:
        board._migrate(c)
        c.execute("UPDATE campaigns SET status='retired', updated_at=?"
                  " WHERE status IN ('draft','approved')", (int(time.time()),))
    return {"ok": True, "campaign": _write(camp)}


def save(changes) -> dict:
    """
    His edits: the offer, its date, his number, any of the wording. Anything
    changed after approval puts it back to draft — he approves what goes out,
    not what used to.
    """
    camp = current()
    if not camp:
        return {"ok": False, "error": "There is no campaign yet — build one first."}
    changes = changes or {}
    copy = dict(camp.get("copy") or {})
    for k in FIELDS:
        if k in changes:
            copy[k] = changes[k]
    if any(k in changes for k in ("phone", "whatsapp")):
        copy["contact"] = {**(copy.get("contact") or {}),
                           **{k: str(changes.get(k) or "").strip()
                              for k in ("phone", "whatsapp") if k in changes}}
    before = (json.dumps(camp.get("copy"), sort_keys=True), camp.get("offer") or "",
              camp.get("offer_until") or "")
    camp["copy"] = _clean(copy, camp.get("tiles") or [])
    if "offer" in changes:
        camp["offer"] = str(changes.get("offer") or "").strip()
    if "offer_until" in changes:
        camp["offer_until"] = str(changes.get("offer_until") or "").strip()
    after = (json.dumps(camp["copy"], sort_keys=True), camp.get("offer") or "",
             camp.get("offer_until") or "")
    if before == after:
        return {"ok": True, "campaign": camp, "head": "Nothing changed."}
    camp["written_by"] = "owner"
    camp["audit"] = audit(camp)
    was = camp.get("status")
    camp = _write(camp, status="draft", approved_at=None)
    return {"ok": True, "campaign": camp,
            "head": ("Saved. It changed after you approved it, so it is back to "
                     "draft — read it and approve it again." if was == "approved"
                     else "Saved.")}


def approve() -> dict:
    """His yes — on the wording and on his offer. Refused while anything stops it."""
    camp = current()
    if not camp:
        return {"ok": False, "error": "There is no campaign to approve yet."}
    v = audit(camp)
    if not v["pass"]:
        _write({**camp, "audit": v})
        return {"ok": False, "error": "The Auditor stops it: " + "; ".join(v["blocking"])}
    camp = _write({**camp, "audit": v}, status="approved", approved_at=int(time.time()))
    with board.agent_run(AGENT) as run_row:
        run_row.note("the owner approved the campaign “" + camp["copy"].get("subject", "")
                     + "”" + (f" with his offer: {camp['offer']}" if camp.get("offer") else ""))
    return {"ok": True, "campaign": camp,
            "head": "Approved. Tick who gets it and press Send."}


def retire() -> dict:
    camp = current()
    if not camp:
        return {"ok": False, "error": "There is no campaign."}
    _write(camp, status="retired")
    return {"ok": True, "head": "Put away. Build a new one whenever you like."}


# ---- the agency rewriting the words -----------------------------------------

WRITER_BRIEF = """You are writing ONE marketing email for {name}, a glass and \
aluminium company in the UAE ({areas}), {years}+ years in the trade. It goes to \
property managers, facilities teams, real-estate firms, fit-out contractors and \
businesses. The same email goes to every one of them.

VOICE: the company speaking — always "we", never "I", never a person's name. \
Professional, confident and warm; plain British English; short sentences. It \
must read like a well-run local firm, not a template and not a chat message.

FACTS YOU MAY USE: we supply, fabricate and install with our own team; we \
measure on site; we give quotes from a photo sent on WhatsApp; we do repairs \
and replacements as well as new work; our services are: {services}.

NEVER: a price, a percentage or any deal (the owner's own offer is shown in \
its own box — do not mention it); invented clients, projects, numbers, awards, \
certificates or guarantees; phone numbers, email addresses or names (the \
design adds the contact details); placeholders like [name]; markdown.

{focus}Return ONLY a JSON object with these keys:
  "subject":   up to 60 characters, specific, no clickbait
  "preheader": up to 110 characters, the line an inbox shows after the subject
  "headline":  up to 60 characters, the big line at the top
  "intro":     two short paragraphs separated by a blank line, 60-90 words in all
  "why_us":    a list of 4 short points, each under 10 words
  "cta":       one or two sentences inviting them to send a photo on WhatsApp for a quote
  "lines":     an object giving one line (under 14 words) for each service: {services}
"""


def _parse_json(text) -> dict:
    s = str(text or "").strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.I | re.M)
    a, b = s.find("{"), s.rfind("}")
    if a < 0 or b <= a:
        return {}
    try:
        v = json.loads(s[a:b + 1])
        return v if isinstance(v, dict) else {}
    except ValueError:
        return {}


def rewrite(focus=None, think=None) -> dict:
    """
    The agency writes fresh wording for the current campaign. The offer, his
    number and his photographs stay as they are. If what comes back is refused
    by the Auditor even after one go at fixing it, his current wording is kept
    and he is told why — a worse campaign never replaces a working one.
    """
    camp = current()
    if not camp:
        return {"ok": False, "error": "There is no campaign yet — build one first."}
    if think is None:
        try:
            from providers import think
        except Exception:
            return {"ok": False, "error": "no model is available to write it"}
    biz = business()
    tiles = camp.get("tiles") or []
    focus = (camp["copy"].get("focus") if focus is None else focus) or ""
    brief = WRITER_BRIEF.format(
        name=biz["name"], areas=biz["areas_phrase"], years=biz.get("years") or "10",
        services=", ".join(t["label"] for t in tiles[:MOST_TILES]) or "glass and aluminium work",
        focus=(f"WHAT THE OWNER WANTS IT TO LEAD WITH: {focus}\n\n" if focus else ""))

    def ask(extra=""):
        r = think("leads", [{"role": "user", "content": brief + extra}], agent=AGENT,
                  max_tokens=1200, reason="writing the campaign mail")
        return r, _parse_json(r.get("output")) if r.get("ok") else {}

    with board.agent_run(AGENT) as run_row:
        r, got = ask()
        if not got:
            run_row.note("could not write the campaign: "
                         + str(r.get("error") or "the answer was not usable"))
            return {"ok": False, "error": "The agency could not write it: "
                                          + str(r.get("error") or "the answer was not usable")}
        trial = {**camp, "copy": _clean({**got, "focus": focus,
                                         "contact": camp["copy"].get("contact")}, tiles)}
        v = audit(trial)
        if not v["pass"]:
            r2, got2 = ask("\n\nYOUR LAST VERSION WAS REFUSED FOR:\n- "
                           + "\n- ".join(v["blocking"])
                           + "\nWrite it again with every one of those fixed.")
            if got2:
                trial2 = {**camp, "copy": _clean({**got2, "focus": focus,
                                                  "contact": camp["copy"].get("contact")}, tiles)}
                v2 = audit(trial2)
                if v2["pass"]:
                    trial, v = trial2, v2
        if not v["pass"]:
            run_row.note("rewrote the campaign; the Auditor refused it, so the "
                         "current wording was kept")
            return {"ok": False, "error": "The agency's version was refused by the Auditor ("
                                          + "; ".join(v["blocking"])
                                          + "). Your current wording is unchanged."}
        trial["audit"], trial["written_by"] = v, "agency"
        out = _write(trial, status="draft", approved_at=None)
        run_row.note("rewrote the campaign — waiting for the owner to read and approve it")
    return {"ok": True, "campaign": out,
            "head": "New wording written. Read it and approve it when you are happy."}


# ---------------------------------------------------------------------------
# WHO GETS IT, AND SENDING
# ---------------------------------------------------------------------------

LIST_MOST = 200


def recipients(ids=None) -> list:
    """
    Every lead, newest first, and where each stands for the campaign:

      ready    can be sent the campaign — tick it
      going    approved and waiting for tomorrow's allowance
      sent     already written to — a business is approached once
      no       cannot be written to, and why (no address, Security…)
    """
    from . import leads_outreach
    c = _conn()
    try:
        leads = [dict(r) for r in c.execute(
            "SELECT id, company, name, email, area, segment, source, stage, found_at"
            " FROM leads ORDER BY found_at DESC, id DESC LIMIT ?", (LIST_MOST,))]
        sent = {r[0] for r in c.execute(
            "SELECT DISTINCT lead_id FROM outreach_log WHERE direction='sent'")}
        rows = [dict(r) for r in c.execute(
            "SELECT lead_id, status FROM outreach_queue WHERE status IN"
            " ('pending','held','approved','sent')")]
    finally:
        c.close()
    if ids:
        want = {int(i) for i in ids}
        leads = [l for l in leads if l["id"] in want]
    if not leads:
        return []
    going = {r["lead_id"] for r in rows if r["status"] == "approved"}
    sent |= {r["lead_id"] for r in rows if r["status"] == "sent"}
    old = {r["lead_id"] for r in rows if r["status"] in ("pending", "held")}
    try:
        _can, cannot = leads_outreach.eligible(limit=LIST_MOST * 2, log=False,
                                               only_ids=[l["id"] for l in leads],
                                               for_campaign=True)
        why_not = {x["id"]: x["why"] for x in cannot}
    except Exception as e:
        why_not = {l["id"]: f"could not be checked ({e})" for l in leads}
    out = []
    for l in leads:
        if l["id"] in sent:
            st, why = "sent", "written to already"
        elif l["id"] in going:
            st, why = "going", "approved — goes with the next allowance"
        elif l["id"] in why_not:
            st, why = "no", why_not[l["id"]]
        else:
            st, why = "ready", ""
        out.append({"id": l["id"], "company": l.get("company") or l.get("name") or "",
                    "greeting": greeting(l), "email": l.get("email") or "",
                    "area": l.get("area") or "", "segment": l.get("segment") or "",
                    "found_at": l.get("found_at") or 0, "state": st, "why": why,
                    "old_draft": l["id"] in old})
    return out


def _extra(camp) -> dict:
    """What a campaign mail carries besides its words: the design, and who it is from."""
    sender = (mail.setting("SMTP_FROM") or mail.setting("SMTP_USER") or "").strip()
    return {"from_name": business()["name"],
            "headers": ({"List-Unsubscribe": f"<mailto:{sender}?subject=unsubscribe>"}
                        if "@" in sender else {})}


def for_queued(q, lead) -> dict:
    """
    A campaign mail approved earlier, about to go on a later pass. Only if the
    campaign is still the one he approved — otherwise he has not said yes to
    what would be sent now.
    """
    camp = get(q.get("campaign_id") or 0)
    if not camp or camp.get("status") != "approved":
        return {"ok": False, "why": "the campaign changed after you ticked this — "
                                    "tick it again to send the new version"}
    r = render(camp, lead)
    v = auditor.check_outreach(r["draft"], lead=lead, owner_offer=his_words(camp),
                               voice="company")
    if not v["pass"]:
        return {"ok": False, "why": "the Auditor stops it: " + v["blocking"][0]}
    return {"ok": True, "draft": r["draft"],
            "extra": {"html": r["html"], **_extra(camp)}}


def send(lead_ids) -> dict:
    """
    The Mail room's Send: the approved campaign to exactly these leads.

    Inside today's allowance they go now; beyond it they are approved and go
    with the next pass (his tick is the yes). Security looks at each one again,
    a business already written to is skipped, and the cap and the sending
    domain are enforced where they always are, in mail._smtp_send.
    """
    from . import leads_outreach, security_screen
    if not board.sector_enabled(SECTOR):
        return {"ok": False, "error": "Leads is switched off — turn it back on to send mail."}
    camp = current()
    if not camp or camp.get("status") != "approved":
        return {"ok": False, "error": "Approve the campaign first — nothing goes out "
                                      "that you have not read and approved."}
    ids = []
    for i in lead_ids or []:
        try:
            ids.append(int(i))
        except (TypeError, ValueError):
            pass
    if not ids:
        return {"ok": False, "error": "Tick at least one lead."}
    can, cannot = leads_outreach.eligible(limit=len(ids), only_ids=ids,
                                          for_campaign=True)
    failed = [{"id": x["id"], "company": x["company"], "why": x["why"]} for x in cannot]
    room = max(0, mail.daily_cap() - mail.sent_today())
    extra = _extra(camp)
    sent, later = [], []
    for lead in can:
        r = render(camp, lead)
        v = auditor.check_outreach(r["draft"], lead=lead, owner_offer=his_words(camp),
                                   voice="company")
        if not v["pass"]:
            failed.append({"id": lead["id"], "company": lead.get("company"),
                           "why": "the Auditor stops it: " + v["blocking"][0]})
            continue
        qid = _queue(camp, lead, r, v)
        if len(sent) >= room:
            later.append({"id": lead["id"], "company": lead.get("company"),
                          "to": lead.get("email")})
            _note(qid, "not sent yet: today's allowance is used up — it goes on the next pass")
            continue
        bad = [f for f in security_screen.scan_lead(lead)
               if f["severity"] in ("high", "critical")]
        if bad:
            board.mark_outreach_sent(qid, False, f"Security: {bad[0]['why']}"[:200])
            failed.append({"id": lead["id"], "company": lead.get("company"),
                           "why": f"Security stopped it: {bad[0]['why']}"})
            continue
        res = mail._smtp_send(lead, r["draft"], html=r["html"], **extra)
        if res.get("ok"):
            board.mark_outreach_sent(qid, True, "the campaign, approved and ticked by the owner")
            leads_outreach._mark_contacted(lead["id"])
            sent.append({"id": lead["id"], "company": lead.get("company"),
                         "to": lead.get("email")})
        elif res.get("capped"):
            later.append({"id": lead["id"], "company": lead.get("company"),
                          "to": lead.get("email")})
            _note(qid, "not sent yet: " + res.get("error", "the daily allowance is used up"))
        else:
            _note(qid, "not sent yet: " + res.get("error", "the mail server refused it"))
            failed.append({"id": lead["id"], "company": lead.get("company"),
                           "why": res.get("error", "the mail server refused it")})
    head = (f"Sent the campaign to {len(sent)} lead(s)." if sent else "Nothing was sent now.")
    if later:
        head += (f" {len(later)} more are approved and go with the next allowance "
                 f"(your limit is {mail.daily_cap()} a day).")
    if failed:
        head += f" {len(failed)} could not go: {failed[0]['company']} — {failed[0]['why']}"
    with board.agent_run(AGENT) as run_row:
        run_row.note(f"sent the campaign to {len(sent)} lead(s)"
                     + (f", {len(later)} for the next pass" if later else "")
                     + (f", {len(failed)} could not go" if failed else ""))
    return {"ok": bool(sent or later), "sent": sent, "later": later, "failed": failed,
            "head": head, "cap": mail.daily_cap(), "sent_today": mail.sent_today()}


def _queue(camp, lead, r, v) -> int:
    """The row that records this mail — approved by him, the campaign's own."""
    now = int(time.time())
    with board._conn() as c:
        board._migrate(c)
        # The campaign takes the place of any one-off draft still waiting.
        c.execute("UPDATE outreach_queue SET status='replaced', decided_at=?,"
                  " note='the campaign went instead' WHERE lead_id=?"
                  " AND status IN ('pending','held')", (now, lead["id"]))
        return c.execute(
            "INSERT INTO outreach_queue(lead_id,company,to_email,subject,body,service,"
            "audit,status,queued_by,queued_at,decided_at,note,campaign_id,html)"
            " VALUES(?,?,?,?,?,'campaign',?, 'approved','owner',?,?,?,?,?)",
            (lead["id"], lead.get("company"), (lead.get("email") or "").strip().lower(),
             r["subject"], r["text"], json.dumps(v), now, now,
             "owner approved the campaign and ticked this lead", camp["id"],
             r["html"])).lastrowid


def _note(qid, why):
    try:
        with board._conn() as c:
            c.execute("UPDATE outreach_queue SET note=? WHERE id=?", (why[:300], qid))
    except Exception:
        pass


def send_test(to) -> dict:
    """The campaign to his own inbox, to see it as a lead will. Not outreach:
    not counted against the limit, not logged as anyone being contacted."""
    camp = current()
    if not camp:
        return {"ok": False, "error": "There is no campaign yet."}
    to = (to or "").strip()
    r = render(camp, {"company": "Your Business"})
    out = mail.send_plain(to, "[TEST] " + r["subject"], r["text"], html=r["html"])
    out["head"] = (f"Test sent to {to} — check your inbox (and the Promotions tab)."
                   if out.get("ok") else "The test did not go: " + str(out.get("error")))
    return out


# ---------------------------------------------------------------------------
# WHAT THE MAIL ROOM SHOWS
# ---------------------------------------------------------------------------

def view(sample=None) -> dict:
    """The campaign, rendered for one lead so he reads exactly what they get."""
    camp = current()
    if not camp:
        return {"exists": False, "business": business()["name"]}
    lead = sample or SAMPLE_LEAD
    r = render(camp, lead)
    con = contact(camp)
    return {"exists": True, "id": camp["id"], "status": camp.get("status"),
            "approved_at": camp.get("approved_at"), "updated_at": camp.get("updated_at"),
            "written_by": camp.get("written_by"), "offer": camp.get("offer") or "",
            "offer_until": camp.get("offer_until") or "", "copy": camp.get("copy") or {},
            "tiles": camp.get("tiles") or [], "warnings": camp.get("warnings") or [],
            "audit": camp.get("audit") or {}, "subject": r["subject"],
            "html": r["html"], "for": greeting(lead),
            "phone": con["phone"], "whatsapp": con["whatsapp"],
            "business": business()["name"],
            "photos": sum(1 for t in camp.get("tiles") or [] if t.get("photo"))}


def main():
    ap = argparse.ArgumentParser(description="The one campaign mail.")
    ap.add_argument("--show", action="store_true")
    ap.add_argument("--html", default="", help="write the mail as a lead sees it here")
    ap.add_argument("--create", action="store_true")
    ap.add_argument("--offer", default="")
    ap.add_argument("--until", default="")
    a = ap.parse_args()
    if a.create:
        r = create(a.offer, a.until)
        print(r["campaign"]["audit"].get("why"))
    camp = current()
    if not camp:
        print("No campaign yet. python -m pipelines.campaign --create")
        return 0
    r = render(camp, SAMPLE_LEAD)
    if a.html:
        from pathlib import Path
        Path(a.html).write_text(r["html"], encoding="utf-8")
        print(f"Written to {a.html}")
    if a.show or not a.html:
        print(f"[{camp['status']}] {r['subject']}\n" + "-" * 62 + "\n" + r["text"])
        print("Auditor:", (camp.get("audit") or {}).get("why"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
