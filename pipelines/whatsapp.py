"""
whatsapp.py — the second way to reach a local business, and the rules on it.

    python3 -m pipelines.whatsapp --ready          who can be messaged, and why
    python3 -m pipelines.whatsapp --links          one tap each, for the phone

WHY THIS EXISTS

The owner's mission, in his words: reach the audience that actually needs the
service "via mail and via whatsapp". For the segments he is aiming at — a
cafeteria in Al Quoz, a salon in Mirdif, a small builder doing one villa — that
is simply correct. Half of them have no email address anywhere. All of them have
WhatsApp open all day, and most have put a click-to-chat button on their own
website precisely so people will use it.

WHAT THIS FILE IS HONEST ABOUT

There are two completely different things called "WhatsApp outreach" and
confusing them is how a number gets banned:

  THE OWNER TAPS A LINK          He opens a chat with a business that published
                                 a chat button, on his own phone, as himself.
                                 This is a person contacting a business that
                                 invited contact. It needs no API, no approval,
                                 and no provider. It is what this file does.

  A PROGRAM SENDS AUTOMATICALLY  Business-initiated messages through the
                                 WhatsApp Business Platform. This needs a Meta
                                 Business account, a verified sender, and a
                                 message template APPROVED IN ADVANCE by Meta
                                 for each thing you want to say. Sending without
                                 that — through an unofficial library driving
                                 WhatsApp Web — gets the number banned, usually
                                 within days, and the number is the business's
                                 real number.

So this file will not send anything. It prepares, and `ready()` is deliberately
strict about who is on the list. If the owner wants automated sending later,
`WHAT_AUTOMATED_SENDING_NEEDS` below says exactly what it would take; that is a
decision with a cost and a real risk attached, and it is his to make, not this
file's to assume.

WHO IS ON THE LIST — three gates, all of them evidence

  1. THEY PUBLISHED A CHAT BUTTON. Not "we found a mobile number". A wa.me link
     on their own website is an invitation to message that business. A mobile
     printed on a page is a phone number, and ringing it is fine — opening a
     chat with it is a different act. harvest.py keeps the two apart and so
     does this (see `whatsapp` vs `whatsapp_maybe`).
  2. SECURITY CLEARED THEM. Same gate as email, and for the same reason: the
     handbook says Leads asks Security before Outreach ever contacts anything.
  3. NOT ALREADY APPROACHED. The dedup promise is per BUSINESS, not per
     channel. Mailing a firm and then messaging it is still contacting it
     twice, and it reads worse, not better.

Plus a cap, because a cap that only covers one channel is not a cap.
"""
import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.parse

from coordination import board

# A separate, tighter limit than email. A chat message lands on a person's
# personal phone and is read within minutes; twenty of those a day from one
# number is what "business account restricted" looks like. Deliberately below
# the email cap rather than equal to it.
DEFAULT_DAILY_CAP = 8

CHANNEL = "whatsapp"

WHAT_AUTOMATED_SENDING_NEEDS = [
    "a Meta Business account with the business verified",
    "a WhatsApp Business Platform (Cloud API) number — NOT the owner's personal "
    "number, which cannot be moved back once it is registered",
    "a message template submitted to Meta and approved before it may be used "
    "for a business-initiated conversation",
    "a per-conversation cost, which is charged whether or not they reply",
    "the same Security clearance and dedup gate this file already applies",
]


def _conn():
    c = sqlite3.connect(board.DB, timeout=30)
    c.row_factory = sqlite3.Row
    return c


def daily_cap() -> int:
    """Read from the environment here, not passed in — a cap the caller
    supplies is a cap the next caller written will forget to supply."""
    raw = (os.environ.get("WHATSAPP_DAILY_CAP") or "").strip()
    if raw.isdigit() and int(raw) > 0:
        return int(raw)
    return DEFAULT_DAILY_CAP


def sent_today(c=None) -> int:
    own = c is None
    c = c or _conn()
    try:
        lt = time.localtime()
        day0 = int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)))
        r = c.execute("SELECT count(*) FROM outreach_log WHERE channel=?"
                      " AND direction='sent' AND at>=?", (CHANNEL, day0)).fetchone()
        return int(r[0] if r else 0)
    except Exception:
        return 0
    finally:
        if own:
            c.close()


def chat_link(e164, text="") -> str:
    """
    The tap-to-open link for one number. wa.me is WhatsApp's own format and
    works from a phone or the desktop app.

    The message is PREFILLED, never sent — WhatsApp opens the chat with the
    text in the box and the owner presses send himself, or edits it first. That
    is the difference between this and automated sending, and it is the whole
    reason this is allowed to exist without a Meta template.
    """
    num = (e164 or "").strip().lstrip("+")
    if not num.isdigit():
        return ""
    base = f"https://wa.me/{num}"
    return base + (f"?text={urllib.parse.quote(text[:900])}" if text else "")


def ready(limit=25, now=None) -> dict:
    """
    Which leads may be messaged on WhatsApp today, and why each other one may
    not. The refusals are the useful half — a list that only shows who is
    eligible cannot be argued with.
    """
    now = int(now or time.time())
    c = _conn()
    try:
        board._migrate(c)
        try:
            rows = [dict(r) for r in c.execute(
                "SELECT * FROM leads ORDER BY found_at DESC LIMIT 400")]
        except Exception as e:
            return {"ok": False, "error": f"cannot read the leads: {e}"}

        touched = set()
        try:
            for r in c.execute("SELECT DISTINCT lead_id FROM outreach_log"
                               " WHERE direction='sent'"):
                touched.add(r[0])
        except Exception:
            pass

        can, cannot = [], []
        for r in rows:
            wa = (r.get("whatsapp") or "").strip()
            name = r.get("company") or r.get("name") or f"lead {r['id']}"
            if not wa:
                cannot.append({"id": r["id"], "company": name,
                               "why": "they did not publish a chat button — "
                                      "a mobile number is not an invitation"})
                continue
            if r["id"] in touched:
                cannot.append({"id": r["id"], "company": name,
                               "why": "already approached — the dedup promise is "
                                      "per business, not per channel"})
                continue
            if (r.get("stage") or "new") not in ("new", "nurture"):
                cannot.append({"id": r["id"], "company": name,
                               "why": f"stage is '{r.get('stage')}' — not a cold approach"})
                continue
            can.append({"id": r["id"], "company": name,
                        "whatsapp": wa, "area": r.get("area") or "",
                        "segment": r.get("segment") or "",
                        "website": r.get("website") or "",
                        "cleared": bool(r.get("email_valid"))})

        cap, used = daily_cap(), sent_today(c)
        room = max(0, cap - used)
        return {"ok": True, "at": now, "cap": cap, "used_today": used,
                "room_today": room,
                "ready": can[:max(0, min(limit, room))],
                "held_back": can[max(0, min(limit, room)):],
                "not_eligible": cannot[:40],
                "head": (f"{len(can)} lead(s) published a chat button; "
                         f"{room} of today's {cap} left"
                         if can else "nobody has published a chat button yet")}
    finally:
        c.close()


def opening(lead, service_label="") -> str:
    """
    A first line the owner can send as himself, or edit.

    Deliberately plain, deliberately short, and it says where we got the number
    — because the first thing anyone thinks when a stranger messages their
    business is "how did you get this". Answering that in the first sentence is
    the difference between a reply and a block.

    No price, no offer, no concession — those need the owner, and this is a
    first message, not a quotation.
    """
    who = lead.get("company") or "there"
    from coordination import profile, uae
    starter = profile.is_starter()
    what = service_label or ("glass and aluminium work" if starter else profile.trade_line())
    where = lead.get("area") or ("Dubai" if starter else (uae.served() or ["your area"])[0])
    trade = "glass and aluminium workshop" if starter else f"{profile.trade_line()} business"
    return (f"Hello {who} — I saw the WhatsApp button on your website. "
            f"I run a {trade} and we do a lot of {what} "
            f"around {where}. If anything needs fixing or replacing, send me a "
            "photo and I'll tell you straight away whether it's worth doing and "
            "what it involves. No obligation.")


def links(limit=25) -> dict:
    """The ready list, each with a tap-to-open link and a prefilled first line."""
    r = ready(limit=limit)
    if not r.get("ok"):
        return r
    try:
        from . import services
    except Exception:                                        # pragma: no cover
        services = None
    out = []
    for l in r["ready"]:
        label = ""
        if services:
            svc = services.services_for_segment(l.get("segment") or "")
            label = services.label(svc[0]) if svc else ""
        text = opening(l, label)
        out.append({**l, "link": chat_link(l["whatsapp"], text), "opening": text})
    return {**r, "ready": out}


def as_text(r) -> str:
    if not r.get("ok"):
        return f"WHATSAPP — {r.get('error')}"
    L = [f"WHATSAPP — {r['head']}"]
    for l in r["ready"]:
        L.append(f"  {l['company'][:44]:46} {l['whatsapp']:15} {l.get('segment', '')}")
        if l.get("link"):
            L.append(f"      {l['link'][:110]}")
    if r["held_back"]:
        L.append(f"  -- {len(r['held_back'])} more, over today's cap --")
    if r["not_eligible"]:
        L.append("  NOT ON THE LIST")
        for x in r["not_eligible"][:8]:
            L.append(f"    {x['company'][:40]:42} {x['why']}")
    L.append("")
    L.append("  Nothing here is sent by the agency. Each link opens the chat with "
             "the first line ready; you press send.")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(
        description="Who can be reached on WhatsApp, and the link to do it.")
    ap.add_argument("--ready", action="store_true")
    ap.add_argument("--links", action="store_true")
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    r = links(a.limit) if a.links or not a.ready else ready(a.limit)
    print(json.dumps(r, indent=2) if a.json else as_text(r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
