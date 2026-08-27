"""
mail.py — the outreach email composer.

The owner's brief: the agency's mail must know WHAT to write, HOW to catch
attention, and WHAT graphics to add — a professional branded banner plus a
short, human, psychologically sharp note (not a ten-page essay), and every
piece must pass the MD, who scans whether the copy / keywords / image actually
land before anything is sent.

This module is deliberately split in two:

  * plan()      — PURE. No network, no DB. It decides the email type from the
                  lead's stage, writes the LLM brief (owner's voice, short,
                  attention hook, honest psychology levers), and specifies the
                  banner. This is the "brain" and it is unit-tested.

  * compose()   — LIVE. It borrows the shelves the plan needs (stock_images for
                  the banner asset, then email to send), asks the sector brain to
                  write the copy, routes the result through the MD-scan gate, and
                  only sends on a pass. Each outside boundary (LLM call, image
                  fetch, SMTP send) is guarded so a missing key degrades to a
                  clear message instead of a crash.

Graphics rule, on purpose: for a glass & aluminium firm the edge is GENUINE
completed work. We never fake project photos with image generation — the banner
uses the owner's real job photos from the library; stock/generated images are
allowed only for abstract decoration. That protects the one real advantage.

Pricing rule: no price, concession or special deal ever goes in a client mail
without the owner's sign-off (see must_avoid, and the red-line hook that
enforces it). This composer only ever drafts on that basis.
"""

import html as _html

from . import services

# --- email type by lead stage ----------------------------------------------
# Grounded in the lead's real stage (schema: leads.stage), never guessed.
_TYPES = {
    "new":       {"type": "cold_intro",      "goal": "earn one reply", "ask": "a quick WhatsApp or a look at recent work"},
    "contacted": {"type": "follow_up",       "goal": "gentle nudge",   "ask": "whether now's a good time"},
    "replied":   {"type": "reply",           "goal": "keep it moving", "ask": "the detail needed to quote"},
    "qualified": {"type": "quote_follow_up", "goal": "win the job",    "ask": "to confirm and book a site visit"},
    "nurture":   {"type": "nurture",         "goal": "stay useful",    "ask": "nothing — share one relevant project"},
    "won":       {"type": "thank_you",       "goal": "earn a review",  "ask": "a Google review"},
}

# Honest attention/psychology levers — persuasion by being genuinely relevant,
# not by tricks. Each maps to something the business actually has.
_LEVERS = [
    "open with the reader's own context (their area + the exact service)",
    "one concrete proof point — a real nearby completed job or a real review",
    "specifics over adjectives (materials, timeline, a real number)",
    "a single, low-friction ask (WhatsApp beats a form in the UAE)",
    "short: aim 90–130 words, one idea, one call to action",
    "the owner's plain British-English voice — no corporate filler",
]


def setting(name: str, default: str = "") -> str:
    """
    One setting, from the environment first and then from .env.

    THE SENDER COULD NEVER SEE HIS SMTP DETAILS.

    Every SMTP lookup in this file read os.environ and nothing else, and
    nothing in the whole program loads .env into the process. The settings he
    saved on /setup → Delivery mail live in that file. So from a terminal —
    which is how he runs everything — the mail sender was looking somewhere
    the details had never been, and reported "SMTP not configured" to a man
    who had configured it. Outreach could not have sent a single mail.

    read_env's own docstring records the mirror image of this bug: it used to
    ignore the environment while the program told him to set variables there.
    Same lesson, other direction. Precedence matches it exactly — an exported
    variable is a deliberate act for one run; the saved file is the standing
    default.
    """
    import os
    v = (os.environ.get(name) or "").strip()
    if v:
        return v
    try:
        from providers import integrations
        return (integrations.read_env().get(name) or "").strip() or default
    except Exception:
        return default


def email_type(stage: str) -> dict:
    return dict(_TYPES.get((stage or "new").strip().lower(), _TYPES["new"]))


def banner_spec(company: dict) -> dict:
    """
    The professional banner: brand block with the firm's real details, and a
    REAL project-photo slot (never AI-generated for project shots).
    """
    return {
        "brand_block": {
            "name": company.get("name", "Glassworks"),
            "tagline": company.get("tagline", "Glass & aluminium, done properly"),
            "services": company.get("services", ["glass work", "aluminium", "doors", "pergolas"]),
            "phone": company.get("phone", ""),
            "whatsapp": company.get("whatsapp", ""),
            "areas": company.get("areas", ["Dubai", "Abu Dhabi", "Sharjah"]),
            "logo_slot": "logo",
        },
        "hero_image": {
            "source": "real_project_library",   # NOT generated — genuine completed work
            "fallback": "brand_colour_block",
            "note": "pick a real job photo matching the lead's service/area if we have one",
        },
        "layout": "banner_top, one-column body, signature with phone + WhatsApp",
    }


def lead_service(lead: dict) -> tuple:
    """
    (service_key, label, related_labels) for this lead.

    The Hunter tags every lead it collects with the service that business most
    likely needs, worked out from its own listing. If that tag is present we use
    it; if the lead came from somewhere else we work it out here rather than
    falling back to a generic pitch. One mail, one job — a hotel gets shower
    glass and mirrors, a warehouse gets aluminium doors. Naming the whole
    catalogue at someone is how an email gets deleted.
    """
    key = (lead.get("service") or "").strip()
    if key not in services.SERVICES:
        key, _ = services.match_service(
            lead.get("company"), lead.get("category"), lead.get("segment"),
            segment=lead.get("segment", ""))
    return key, services.label(key), services.related_labels(key)


def writing_brief(lead: dict, company: dict, owner_voice: str = "") -> dict:
    """The full instruction the sector brain writes against."""
    stage = lead.get("stage", "new")
    et = email_type(stage)
    key, label, related = lead_service(lead)
    return {
        "email_type": et["type"],
        "goal": et["goal"],
        "single_ask": et["ask"],
        "recipient": {"name": lead.get("name"), "company": lead.get("company"),
                      "area": lead.get("area"), "segment": lead.get("segment")},
        # THE one thing this email is about, and the ones that go underneath it
        "lead_service": key,
        "headline_service": label,
        "also_offer": related,
        "language": "British English",
        "length_words": [90, 130],
        "voice": owner_voice or "plain, human, confident, no filler — a tradesman with 10+ years, writing like a person",
        "attention_levers": _LEVERS,
        "must_include": [f"{label} — this is what the email is ABOUT, lead with it",
                         "the reader's area, by name",
                         "one real proof point",
                         "one clear ask"],
        "must_avoid": ["fake project photos", "invented reviews or numbers",
                       "any price or special deal (owner sign-off only)", "walls of text",
                       "listing every service in the body — the related ones belong "
                       "in the footer block, not the paragraph"],
        "footer_block": {
            "also_offer": related,
            "website": company.get("website", ""),
            "phone": company.get("phone", ""),
            "whatsapp": company.get("whatsapp", ""),
            "areas": company.get("areas", ["Dubai", "Abu Dhabi", "Sharjah"]),
        },
        "banner": banner_spec(company),
    }


def plan(lead: dict, company: dict, owner_voice: str = "") -> dict:
    """
    PURE. The whole email plan without side effects — safe to compute, log and
    show. Names the shelves the live path will borrow and the gate it must pass.
    """
    brief = writing_brief(lead, company, owner_voice)
    return {
        "ok": True,
        "email_type": brief["email_type"],
        "brief": brief,
        "shelves_needed": ["stock_images", "email"],  # image asset, then delivery
        "md_scan": {
            "gate": "MD",
            "question": "Does this copy, the chosen proof point and the banner image actually "
                        "make an impact for this lead — or is it filler? Approve, or send back.",
            "checks": ["copy earns a reply", "proof point is real and relevant",
                       "image is a genuine project shot", "one clear ask", "on-voice, short"],
        },
    }


# ---------------------------------------------------------------------------
# RENDER — the actual email, in a form a person can read
# ---------------------------------------------------------------------------
#
# What was missing: everything above produces a PLAN — a nested dict describing
# the email. That is the right thing for the brain to write against and the
# wrong thing to look at. Reading a plan is not reading an email, and an owner
# asked to approve outreach should be looking at what the customer will see.
#
# So render() takes the plan plus the written copy and produces the finished
# thing: a subject line, a plain-text version, and an HTML version with the
# banner, the one service this mail is about, the related services underneath,
# and the website and contact details in the footer. Both versions carry the
# same words — the text one is not an afterthought, because plenty of UAE
# procurement inboxes still strip HTML.


def _split_subject(draft: str) -> tuple:
    """
    The brain returns 'subject line, then body'. Take it apart forgivingly —
    with or without a 'Subject:' prefix, and cope if it only wrote a body.
    """
    lines = [l.rstrip() for l in (draft or "").strip().splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    if not lines:
        return "", ""
    first = lines[0].strip()
    low = first.lower()
    if low.startswith("subject:"):
        return first.split(":", 1)[1].strip(), "\n".join(lines[1:]).strip()
    # a short first line with no full stop is a subject, not a paragraph
    if len(first) <= 90 and not first.endswith((".", "!", "?")) and len(lines) > 1:
        return first, "\n".join(lines[1:]).strip()
    return "", "\n".join(lines).strip()


def _paragraphs(body: str) -> list:
    out, buf = [], []
    for line in (body or "").splitlines():
        if line.strip():
            buf.append(line.strip())
        elif buf:
            out.append(" ".join(buf))
            buf = []
    if buf:
        out.append(" ".join(buf))
    return out


def render(plan_or_brief: dict, draft: str, company: dict, lead: dict) -> dict:
    """
    The finished email. Returns {subject, text, html, service, also_offer}.

    Nothing here invents copy: the words are the ones the brain wrote and the MD
    approved. This only wraps them in the branding, the service block and the
    contact details, which are facts about the business rather than claims about
    the customer.
    """
    brief = plan_or_brief.get("brief", plan_or_brief)
    key, label, related = lead_service(lead)
    fb = brief.get("footer_block", {}) or {}
    site = (fb.get("website") or company.get("website") or "").strip()
    phone = (fb.get("phone") or company.get("phone") or "").strip()
    wa = (fb.get("whatsapp") or company.get("whatsapp") or "").strip()
    areas = fb.get("areas") or company.get("areas") or ["Dubai", "Abu Dhabi", "Sharjah"]
    name = company.get("name", "Glassworks")
    tagline = company.get("tagline", "Glass & aluminium, done properly")

    subject, body = _split_subject(draft)
    if not subject:
        where = (lead.get("area") or "").strip()
        subject = f"{label.capitalize()}" + (f" in {where}" if where else "")
    paras = _paragraphs(body) or [body.strip()]

    # ---- plain text -------------------------------------------------------
    lines = list(paras)
    lines += ["", "— " + name + (f", {tagline}" if tagline else "")]
    if phone:
        lines.append(f"  Phone: {phone}")
    if wa:
        lines.append(f"  WhatsApp: {wa}")
    if site:
        lines.append(f"  {site}")
    lines += ["", f"We also do: {', '.join(related)}.",
              f"Covering {', '.join(areas)}."]
    text = "\n".join(lines).strip() + "\n"

    # ---- html -------------------------------------------------------------
    e = _html.escape
    hero = ""
    photo = (company.get("hero_photo") or "").strip()
    if photo:
        # a REAL project photo or nothing. Never a generated one — see the
        # graphics rule at the top of this file.
        hero = (f'<img src="{e(photo)}" alt="Recent {e(label)} job" '
                f'style="width:100%;max-width:600px;display:block;border:0">')

    svc_chips = "".join(
        f'<span style="display:inline-block;background:#eef6f8;color:#14343d;'
        f'border:1px solid #cfe3e8;border-radius:14px;padding:4px 11px;margin:3px 4px 0 0;'
        f'font-size:12px">{e(s)}</span>' for s in related)

    contact_bits = []
    if phone:
        contact_bits.append(f'<a href="tel:{e(phone)}" style="color:#0b6b7a;'
                            f'text-decoration:none">{e(phone)}</a>')
    if wa:
        digits = "".join(ch for ch in wa if ch.isdigit())
        contact_bits.append(f'<a href="https://wa.me/{digits}" '
                            f'style="color:#0b6b7a;text-decoration:none">WhatsApp</a>')
    if site:
        shown = site.replace("https://", "").replace("http://", "").rstrip("/")
        contact_bits.append(f'<a href="{e(site)}" style="color:#0b6b7a;'
                            f'text-decoration:none">{e(shown)}</a>')

    html_doc = f"""<!doctype html>
<html><body style="margin:0;padding:0;background:#f4f6f7">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
       style="background:#f4f6f7;padding:20px 10px">
<tr><td align="center">
<table role="presentation" width="600" cellpadding="0" cellspacing="0"
       style="max-width:600px;width:100%;background:#ffffff;border-radius:10px;
              overflow:hidden;font-family:Helvetica,Arial,sans-serif;color:#1f2b30">

  <tr><td style="background:#12313a;padding:18px 24px">
    <div style="color:#ffffff;font-size:19px;font-weight:700">{e(name)}</div>
    <div style="color:#8fd3e0;font-size:12.5px;margin-top:3px">{e(tagline)}</div>
  </td></tr>

  {f'<tr><td>{hero}</td></tr>' if hero else ''}

  <tr><td style="padding:24px 24px 6px">
    <div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;
                color:#0b6b7a;font-weight:700">{e(label)}</div>
  </td></tr>

  <tr><td style="padding:6px 24px 18px;font-size:15px;line-height:1.6">
    {''.join(f'<p style="margin:0 0 14px">{e(p)}</p>' for p in paras)}
  </td></tr>

  <tr><td style="padding:0 24px 20px">
    <div style="font-size:13px;color:#4a5c63;margin-bottom:4px">
      We also do:</div>{svc_chips}
  </td></tr>

  <tr><td style="background:#f7fafb;border-top:1px solid #e2eaed;padding:16px 24px;
                 font-size:13px;color:#4a5c63">
    <div style="font-weight:600;color:#1f2b30">{e(name)}</div>
    <div style="margin-top:4px">{' &nbsp;·&nbsp; '.join(contact_bits)}</div>
    <div style="margin-top:6px;color:#7a8b92">Covering {e(', '.join(areas))}.</div>
  </td></tr>

</table></td></tr></table></body></html>"""

    return {"subject": subject, "text": text, "html": html_doc,
            "service": key, "service_label": label, "also_offer": related,
            "website": site}


# ---------------------------------------------------------------------------
# LIVE path — borrows shelves, writes via the brain, MD-scans, then sends.
# Each outside boundary is guarded so a missing key is a message, not a crash.
# ---------------------------------------------------------------------------

def _brain():
    try:
        from providers import call_task
        return call_task
    except Exception:
        return None


def _cabinet():
    try:
        from coordination import cabinet, board
        return cabinet, board
    except Exception:
        return None, None


def _brief_to_text(brief: dict) -> str:
    lines = [f"Email type: {brief['email_type']} — goal: {brief['goal']}",
             f"Recipient: {brief['recipient']}",
             f"Voice: {brief['voice']}",
             f"Length: {brief['length_words'][0]}–{brief['length_words'][1]} words, {brief['language']}",
             "Attention levers:"]
    lines += [f"  - {x}" for x in brief["attention_levers"]]
    lines += ["Must include: " + "; ".join(brief["must_include"]),
              "Must avoid: " + "; ".join(brief["must_avoid"]),
              f"Single ask: {brief['single_ask']}"]
    return "\n".join(lines)


def _write_copy(call_task, brief):
    """Ask the sector brain (an LLM) to write the copy from the brief."""
    if call_task is None:
        return {"ok": False, "error": "no provider layer available"}
    prompt = ("Write a short outreach email from this brief. Return only the "
              "subject line then the body.\n\n" + _brief_to_text(brief))
    r = call_task("leads", [{"role": "user", "content": prompt}], max_tokens=400)
    if not r.get("ok"):
        return {"ok": False, "error": r.get("error", "brain unavailable"), "failovers": r.get("failovers")}
    return {"ok": True, "draft": r["output"], "used": r.get("used")}


def compose(lead: dict, company: dict, *, agent: str, sector: str = "Leads",
            owner_voice: str = "", cost_ceiling: float = 0.0,
            md_timeout: int = 120, send: bool = False) -> dict:
    """
    Run the pipeline for real. Returns a structured trace of every step. It will
    NOT send unless send=True AND the MD-scan passes AND the email shelf resolves.
    """
    p = plan(lead, company, owner_voice)
    trace = {"plan": p, "steps": []}
    cabinet, board = _cabinet()
    if cabinet is None:
        return {**trace, "ok": False, "error": "cabinet unavailable"}

    call_task = _brain()

    # 1) borrow the stock_images shelf to select the banner asset (our library =
    #    internal data; a scraped/client image would be data_class='external').
    img = cabinet.borrow("stock_images", agent, sector,
                         reason=f"banner image for {p['email_type']} to {lead.get('company')}",
                         data_class="internal", cost_ceiling=cost_ceiling, wait=True)
    trace["steps"].append({"borrow": "stock_images", "ok": img["ok"], "approval": img.get("approval")})
    try:
        # (asset selection from the real-project library happens here when wired)
        # 2) write the copy with the sector brain
        drafted = _write_copy(call_task, p["brief"])
        trace["steps"].append({"write": drafted.get("ok"),
                               "error": drafted.get("error"), "used": drafted.get("used")})
    finally:
        cabinet.release("stock_images", agent, borrow_id=img.get("borrow_id"),
                        item="real_project_library")

    if not drafted.get("ok"):
        return {**trace, "ok": False, "error": "copy not written: " + str(drafted.get("error"))}

    # 3) MD-scan gate — the brain never sends its own work; the MD decides impact
    verdict = None
    if board is not None:
        verdict = board.request_verification(
            sector, "MD", subject=f"Scan {p['email_type']} mail before send",
            body=p["md_scan"]["question"] + "\n\n--- draft ---\n" + drafted["draft"],
            blocking=True, timeout=md_timeout)
    trace["steps"].append({"md_scan": "answered" if verdict else "no answer",
                           "verdict": verdict})
    passed = bool(verdict) and "reject" not in (verdict or "").lower()
    if not passed:
        return {**trace, "ok": True, "sent": False, "draft": drafted["draft"],
                "held": "MD did not approve (or did not answer) — not sent"}

    # 4) send — borrow the email shelf; guarded, real SMTP is wired via .env
    if not send:
        return {**trace, "ok": True, "sent": False, "draft": drafted["draft"],
                "note": "MD approved; send=False so held as a draft"}
    em = cabinet.borrow("email", agent, sector,
                        reason=f"send {p['email_type']} to {lead.get('email')}",
                        data_class="internal", cost_ceiling=cost_ceiling, wait=True)
    trace["steps"].append({"borrow": "email", "ok": em["ok"]})
    try:
        sent = _smtp_send(lead, drafted["draft"])
        trace["steps"].append({"send": sent.get("ok"), "error": sent.get("error")})
    finally:
        cabinet.release("email", agent, borrow_id=em.get("borrow_id"))
    return {**trace, "ok": sent.get("ok"), "sent": sent.get("ok"),
            "draft": drafted["draft"], "error": sent.get("error")}


# The daily ceiling on outgoing mail, used when the owner has not set a target.
# A cap that defaults to "unlimited" is not a cap.
DEFAULT_DAILY_CAP = 20


def sent_today(con=None) -> int:
    """How many mails have actually left the building today. Reads the log."""
    from coordination import board as _board, targets as _targets
    own = con is None
    con = con or __import__("sqlite3").connect(_board.DB)
    try:
        r = con.execute("SELECT count(*) FROM outreach_log"
                        " WHERE direction='sent' AND at>=?",
                        (_targets._day_start(),)).fetchone()
        return int(r[0] or 0)
    except Exception:
        return 0
    finally:
        if own:
            con.close()


def daily_cap() -> int:
    """
    The owner's own number if he set one on /setup, else the built-in ceiling.
    Read here rather than passed in: a cap the caller supplies is a cap the
    caller can forget.
    """
    try:
        from coordination import targets as _targets
        for j in (_targets.status().get("jobs") or []):
            if j["name"] == "mails" and j.get("per_day"):
                return int(j["per_day"])
    except Exception:
        pass
    return DEFAULT_DAILY_CAP


def log_outreach(lead_id, body, channel="email", direction="sent", reply_class=None):
    """
    Record that we contacted somebody. THIS IS NOT OPTIONAL.

    Nothing wrote this table before, while eleven places read it, so "mails sent
    today", "replies today" and "people contacted" were all permanently zero and
    the daily cap had nothing to count. Worse, the promise never to approach the
    same firm twice had no record of who had already been approached.
    """
    import sqlite3 as _sq
    from coordination import board as _board
    try:
        with _sq.connect(_board.DB, timeout=30) as c:
            cur = c.execute(
                "INSERT INTO outreach_log(lead_id,channel,direction,body,reply_class,"
                "needs_owner,at) VALUES(?,?,?,?,?,0,?)",
                (lead_id, channel, direction, (body or "")[:4000], reply_class,
                 int(__import__("time").time())))
            return cur.lastrowid
    except Exception:
        return None


def send_plain(to: str, subject: str, body: str, html: str = "") -> dict:
    """
    One mail to an address the OWNER typed. Not outreach, and never treated as
    outreach.

    `html`, when given, rides along as an ALTERNATIVE to the plain body rather
    than replacing it. Both halves go in one message and the client picks: a
    phone shows the pictures, a text-only reader shows the words, and a spam
    filter — which scores a message with no text part badly on sight — finds
    one. Which is why the body is still required even when there is html.

    Kept apart from _smtp_send on purpose, and the difference is the point.
    _smtp_send is the outreach boundary: it checks the daily cap, enforces the
    sending domain, and writes an outreach_log row, because everything going
    through it is a stranger being contacted.

    A preview to his own inbox is none of those things. Counting it against
    the cap would mean reading his own agency's work costs him a real lead;
    logging it as outreach would put a row in the cabinet saying he was
    contacted as a prospect. So this does neither — and in exchange it may
    only send to an address passed in by hand, never to anything read out of
    the leads table.
    """
    import os
    import smtplib
    from email.message import EmailMessage

    to = (to or "").strip()
    if "@" not in to or " " in to:
        return {"ok": False, "error": f"not an email address: {to!r}"}
    host = setting("SMTP_HOST").strip()
    user = setting("SMTP_USER").strip()
    pw = setting("SMTP_PASS")
    if not host or not user:
        return {"ok": False, "error": "SMTP not configured (set it on /setup "
                                      "→ Delivery mail)"}
    sender = (setting("SMTP_FROM") or user).strip()
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject or "(no subject)"
    msg.set_content(body or "")
    if html:
        # add_alternative, not set_content: set_content would throw the plain
        # text away and leave a message with no text part at all.
        msg.add_alternative(html, subtype="html")
    try:
        port = int((setting("SMTP_PORT") or "587").strip() or 587)
    except ValueError:
        port = 587
    try:
        srv = (smtplib.SMTP_SSL(host, port, timeout=30) if port == 465
               else smtplib.SMTP(host, port, timeout=30))
        with srv:
            if port != 465:
                try:
                    srv.starttls()
                except Exception:
                    pass
            if pw:
                srv.login(user, pw)
            srv.send_message(msg)
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return {"ok": True, "to": to, "from": sender}


def _smtp_send(lead: dict, draft: str) -> dict:
    """
    Deliver via SMTP using the credentials saved on /setup (env: SMTP_*). Kept as
    the single send boundary: no other code emails directly.

    Three things happen here and nowhere else, because this is the only place
    that can be sure of them:

      the cap is checked HERE, not by the caller. The handbook red line is a
      daily limit and the right sending domain; a limit enforced by whoever
      happens to call is a limit that gets skipped by the next caller written.

      the send is real, via smtplib, using exactly the keys test_email() proves.

      the send is LOGGED, before we return. An unlogged send is invisible to the
      cap, to the dedup promise, and to every counter the owner reads.
    """
    import os
    import smtplib
    from email.message import EmailMessage

    host = setting("SMTP_HOST").strip()
    user = setting("SMTP_USER").strip()
    pw = setting("SMTP_PASS")
    to = (lead.get("email") or "").strip()
    if not host or not user:
        return {"ok": False, "error": "SMTP not configured (set it on /setup → Integrations)"}
    if not to:
        return {"ok": False, "error": "this lead has no email address"}

    # --- the cap, before anything leaves ------------------------------------
    cap = daily_cap()
    already = sent_today()
    if already >= cap:
        return {"ok": False, "capped": True,
                "error": f"daily outreach cap reached ({already}/{cap}). Nothing sent. "
                         "Raise it on /setup → Daily orders if you really want more."}

    # --- the sending domain, as the handbook requires ------------------------
    sender = (setting("SMTP_FROM") or user).strip()
    domain = setting("OUTREACH_DOMAIN").strip().lower()
    if domain and not sender.lower().endswith("@" + domain):
        return {"ok": False,
                "error": f"refusing to send: {sender} is not on the approved outreach "
                         f"domain ({domain})"}

    subject, body = _split_subject(draft)
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject or "Glass & aluminium work"
    msg.set_content(body or draft)

    try:
        port = int((setting("SMTP_PORT") or "587").strip() or 587)
    except ValueError:
        port = 587
    try:
        if port == 465:
            srv = smtplib.SMTP_SSL(host, port, timeout=30)
        else:
            srv = smtplib.SMTP(host, port, timeout=30)
            srv.ehlo()
            if srv.has_extn("starttls"):
                srv.starttls()
                srv.ehlo()
        try:
            srv.login(user, pw)
            srv.send_message(msg)
        finally:
            try:
                srv.quit()
            except Exception:
                pass
    except Exception as e:
        return {"ok": False, "error": f"SMTP refused the message: {e}"}

    # It has gone. Record it before returning — a send we did not write down is
    # a send the cap cannot see and the Scanner cannot dedup against.
    row = log_outreach(lead.get("id"), draft)
    return {"ok": True, "to": to, "outreach_log_id": row,
            "sent_today": already + 1, "cap": cap}


def preview(lead: dict, company: dict, draft: str = "", write_html_to: str = "") -> dict:
    """
    See the email the way the customer will. Pure — nothing is borrowed, nothing
    is sent, no key needed.

    With no draft it uses a placeholder body so the shape, the service block and
    the footer can be checked; with a real draft it shows the real thing.
    """
    p = plan(lead, company)
    if not draft:
        _, label, _ = lead_service(lead)
        where = lead.get("area") or "your area"
        draft = (f"{label.capitalize()} in {where}\n\n"
                 f"[the written copy goes here — {p['brief']['length_words'][0]}"
                 f"–{p['brief']['length_words'][1]} words, in the owner's voice, "
                 f"about {label} and nothing else]\n\n"
                 "[one real proof point from a nearby finished job]\n\n"
                 "[one clear ask]")
    out = render(p, draft, company, lead)
    if write_html_to:
        from pathlib import Path
        Path(write_html_to).write_text(out["html"], encoding="utf-8")
        out["written_to"] = write_html_to
    return out


if __name__ == "__main__":  # show the EMAIL, not the plan (pure, no side effects)
    import argparse
    ap = argparse.ArgumentParser(description="Preview an outreach email.")
    ap.add_argument("--html", help="also write the HTML version to this file")
    ap.add_argument("--segment", default="hotel")
    ap.add_argument("--plan", action="store_true", help="print the JSON plan instead")
    a = ap.parse_args()

    demo_lead = {"company": "Marina Bay Hotel", "area": "Dubai Marina",
                 "segment": a.segment, "stage": "new", "email": "info@example.ae"}
    demo_company = {"name": "Glassworks Dubai",
                    "tagline": "Glass & aluminium, done properly since 2014",
                    "phone": "+971 4 000 0000", "whatsapp": "+971 50 000 0000",
                    "website": "https://glassworks.ae",
                    "areas": ["Dubai", "Abu Dhabi", "Sharjah"]}

    if a.plan:
        import json
        print(json.dumps(plan(demo_lead, demo_company), indent=2))
    else:
        out = preview(demo_lead, demo_company, write_html_to=a.html or "")
        print("Subject: " + out["subject"])
        print("About:   " + out["service_label"])
        print("Also:    " + ", ".join(out["also_offer"]))
        print("-" * 62)
        print(out["text"])
        if out.get("written_to"):
            print(f"(HTML written to {out['written_to']} — open it in a browser)")

    # tiny self-checks
    pl = plan(demo_lead, demo_company)
    assert pl["email_type"] == "cold_intro"
    assert email_type("won")["type"] == "thank_you"
    assert "email" in pl["shelves_needed"]
