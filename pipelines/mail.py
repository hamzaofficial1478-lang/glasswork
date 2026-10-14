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
import re

from coordination import uae
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
    "one concrete, true detail — the material, how the job is done, how soon "
    "he can come and measure — never an invented job, client or review",
    "specifics over adjectives (the glass, the frame, the fixing), not claims",
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


def _years() -> int:
    try:
        from coordination import auditor
        return auditor.verified_years()
    except Exception:
        return 10


def revise_copy(brief, draft, problems, agent="") -> dict:
    """
    One second go at a draft the Auditor refused, told exactly why. Cheaper
    than throwing the lead away, and the reasons are specific enough ("no
    opt-out line", "claims '15 years'") that one pass usually fixes them.
    """
    try:
        from providers import think
    except Exception:                                        # pragma: no cover
        return {"ok": False, "error": "no provider layer available"}
    prompt = ("You wrote this outreach email and a checker refused it. Rewrite it "
              "so every problem below is fixed, keep what was good, and return "
              "only the subject line then the body.\n\nPROBLEMS:\n"
              + "\n".join(f"- {p}" for p in problems)
              + "\n\nYOUR DRAFT:\n" + str(draft)
              + "\n\nTHE BRIEF AGAIN:\n" + _brief_to_text(brief))
    r = think("leads", [{"role": "user", "content": prompt}],
              agent=agent or "leads-outreach", max_tokens=400,
              reason="fixing an outreach draft the Auditor refused")
    if not r.get("ok"):
        return {"ok": False, "error": r.get("error", "brain unavailable")}
    return {"ok": True, "draft": r["output"], "used": r.get("used")}


def email_type(stage: str) -> dict:
    return dict(_TYPES.get((stage or "new").strip().lower(), _TYPES["new"]))


def _defaults() -> dict:
    """
    What a mail says when the company record leaves something out: the owner's
    glass-and-aluminium words for his website, the profile's for anyone else's.
    """
    try:
        from coordination import profile
        if not profile.is_starter():
            b = profile.business()
            return {"name": b.get("name") or "",
                    "tagline": b.get("tagline") or profile.trade_line(),
                    "services": [sv.get("label") for sv in services.SERVICES.values()][:4]}
    except Exception:
        pass
    return {"name": "Glassworks", "tagline": "Glass & aluminium, done properly",
            "services": ["glass work", "aluminium", "doors", "pergolas"]}


def _levers() -> list:
    """The attention levers, in the open website's own trade and spelling."""
    try:
        from coordination import profile
        if profile.is_starter():
            return _LEVERS
        return [l.replace("(the glass, the frame, the fixing)", "(the materials, the method)")
                 .replace(" (WhatsApp beats a form in the UAE)", "")
                 .replace("British-English", profile.spelling().replace(" English", "-English"))
                for l in _LEVERS]
    except Exception:
        return _LEVERS


def banner_spec(company: dict) -> dict:
    """
    The professional banner: brand block with the firm's real details, and a
    REAL project-photo slot (never AI-generated for project shots).
    """
    return {
        "brand_block": {
            "name": company.get("name", _defaults()["name"]),
            "tagline": company.get("tagline", _defaults()["tagline"]),
            "services": company.get("services", _defaults()["services"]),
            "phone": company.get("phone", ""),
            "whatsapp": company.get("whatsapp", ""),
            "areas": company.get("areas", uae.served()),
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
        "voice": owner_voice or (f"plain, human, confident, no filler — a tradesman "
                                 f"with {_years()}+ years in the trade, writing like a person"),
        "attention_levers": _levers(),
        # Every line here is one the Auditor checks afterwards. The brief used
        # to ask for "one real proof point" and no opt-out, and the Auditor
        # refused every draft for exactly those — so the two now say the same.
        "must_include": [f"{label} — this is what the email is ABOUT, lead with it",
                         "the reader's area, by name",
                         "one concrete, true detail about how the job is done",
                         "one clear ask",
                         "a last line that lets them opt out, e.g. \"If this is not "
                         "useful, reply stop and I will not write again.\""],
        "must_avoid": ["fake project photos", "invented reviews or numbers",
                       "any number of projects or clients, any client's name, any "
                       "certificate or award — there are none to cite",
                       "'we have worked with…', 'our clients include…', 'leading', 'best'",
                       f"any number of years other than {_years()}",
                       "any price or special deal (owner sign-off only)", "walls of text",
                       "listing every service in the body — the related ones belong "
                       "in the footer block, not the paragraph"],
        "footer_block": {
            "also_offer": related,
            "website": company.get("website", ""),
            "phone": company.get("phone", ""),
            "whatsapp": company.get("whatsapp", ""),
            "areas": company.get("areas", uae.served()),
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
            # "Director", not "MD". The Managing Director's sector is called
            # Director everywhere in the cabinet, and this line said "MD" —
            # so every outreach mail posted its approval request to an inbox
            # no code has ever read, blocked the full two minutes waiting, and
            # was then held unsent under "MD did not approve (or did not
            # answer)". Which read like a refusal. The MD was never asked.
            # board.post_request now rejects an unknown sector outright.
            "gate": "Director",
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
    areas = fb.get("areas") or company.get("areas") or uae.served()
    name = company.get("name", _defaults()["name"])
    tagline = company.get("tagline", _defaults()["tagline"])

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


def _write_copy(call_task, brief, agent=""):
    """
    Ask the sector brain (an LLM) to write the copy from the brief.

    Like Content's page, the mail IS the model's output — there is no template
    that would do — so no answer here means the lead is simply never written
    to. Leads' Claude call steps down the models and over to the backup account
    before it gives up (providers/claude_team.py).

    `call_task` is still the injected argument so the existing tests can hand
    in a stub; when it is the real one, think() is used instead.
    """
    if call_task is None:
        return {"ok": False, "error": "no provider layer available"}
    prompt = ("Write a short outreach email from this brief. Return only the "
              "subject line then the body.\n\n" + _brief_to_text(brief))
    try:
        from providers import call_task as _real, think
    except Exception:                                        # pragma: no cover
        _real, think = None, None
    if think is not None and call_task is _real:
        r = think("leads", [{"role": "user", "content": prompt}],
                  agent=agent or "leads-hunter", max_tokens=400,
                  reason="writing outreach copy for a cleared lead")
    else:
        r = call_task("leads", [{"role": "user", "content": prompt}], max_tokens=400)
    if not r.get("ok"):
        return {"ok": False, "error": r.get("error", "brain unavailable"), "failovers": r.get("failovers")}
    return {"ok": True, "draft": r["output"], "used": r.get("used"),
            "rung": r.get("rung")}


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
        drafted = _write_copy(call_task, p["brief"], agent=agent)
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
            sector, "Director", subject=f"Scan {p['email_type']} mail before send",
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
            row = cur.lastrowid
    except Exception:
        return None
    if direction == "sent" and lead_id:
        # On the lead itself, for good — the log is cleared after thirty days.
        try:
            _board.mark_mailed(lead_id)
        except Exception:
            pass
    return row


# ---------------------------------------------------------------------------
# ONE CONNECTION, A PAUSE BETWEEN MAILS, AND A COPY IN HIS SENT FOLDER
# ---------------------------------------------------------------------------
#
# "it sent the mails but how can i confirm if the mails are sent or not i
#  checked into my mail sent there is nothing and also again it get failed as i
#  think it should sent the mails with delay like ... 5 to 8 seconds"
#
# Both halves were right.
#
#   the failures     every mail opened its own connection and logged in again,
#                    back to back. His mail server (Hostinger, relaying through
#                    MailChannels) answered a burst of logins by timing out the
#                    handshake and resetting connections — "The handshake
#                    operation timed out", "WinError 10054", "Connection
#                    unexpectedly closed". Now a batch logs in once, waits five
#                    to eight seconds between mails, and a dropped connection
#                    is opened again and the mail tried again, twice, a little
#                    later each time.
#
#   the Sent folder  sending through SMTP never puts a copy in Sent — a mail
#                    program does that itself, separately. So the mail went
#                    and nothing showed it. Each mail now also goes into the
#                    Sent folder of his own mailbox (IMAP), where he looks.

PACE = (5.0, 8.0)          # seconds between two mails in one batch
TRIES = 3                  # a dropped connection: this many goes in all
RETRY_AFTER = 10           # seconds before the first retry; doubled for the next
SENT_FOLDERS = ("Sent", "INBOX.Sent", "Sent Items", "Sent Messages", "INBOX/Sent",
                "Sent Mail")

_BATCH = None              # the Mailer every send inside `with batch():` shares


def _transient(e) -> bool:
    """A connection that fell over, or a 4xx "try later" — worth another go."""
    import smtplib
    import socket
    import ssl
    if isinstance(e, smtplib.SMTPRecipientsRefused):
        return all(400 <= int(code) < 500 for code, _m in e.recipients.values())
    if isinstance(e, smtplib.SMTPAuthenticationError):
        return False
    if isinstance(e, smtplib.SMTPResponseException):
        return 400 <= e.smtp_code < 500
    return isinstance(e, (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError,
                          TimeoutError, ConnectionError, socket.timeout, ssl.SSLError,
                          OSError))


def _refused_address(e) -> bool:
    """The receiving server said this address is no good (5xx on the recipient)."""
    import smtplib
    if isinstance(e, smtplib.SMTPRecipientsRefused):
        return any(int(code) >= 500 for code, _m in e.recipients.values())
    return False


def _imap_host() -> str:
    h = setting("IMAP_HOST").strip()
    if h:
        return h
    s = setting("SMTP_HOST").strip().lower()
    return ("imap." + s[5:]) if s.startswith("smtp.") else s


def imap_open(timeout=20):
    """His own mailbox, over IMAP, with the same login as sending."""
    import imaplib
    try:
        port = int((setting("IMAP_PORT") or "993").strip() or 993)
    except ValueError:
        port = 993
    im = imaplib.IMAP4_SSL(_imap_host(), port, timeout=timeout)
    im.login(setting("SMTP_USER").strip(), setting("SMTP_PASS"))
    return im


def sent_folder(im) -> str:
    """The folder his mail program calls Sent — by its flag first, then its name."""
    typ, data = im.list()
    names = []
    for raw in data or []:
        line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw or "")
        m = re.match(r'\((?P<flags>[^)]*)\)\s+(?:"[^"]*"|NIL)\s+(?P<name>.+)$', line.strip())
        if not m:
            continue
        name = m.group("name").strip()
        if len(name) > 1 and name[0] == '"' and name[-1] == '"':
            name = name[1:-1]
        if "\\sent" in m.group("flags").lower():
            return name
        names.append(name)
    for want in SENT_FOLDERS:
        for n in names:
            if n.lower() == want.lower():
                return n
    return ""


class Mailer:
    """
    One SMTP login for a whole batch, a pause between mails, a fresh
    connection when the server drops one, and a copy of each mail in his Sent
    folder. Use as `with Mailer() as m: m.deliver(msg)`, or let _smtp_send
    use the batch opened by `with batch():`.
    """

    def __init__(self, pace=PACE, sleep=None, copies=True):
        import time as _t
        self.pace, self.copies = pace, copies
        self.sleep = sleep or _t.sleep
        self.srv, self.imap, self.folder = None, None, None
        self.last, self.gap = 0.0, 0.0
        self.copy_error = ""
        self.sent = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()

    def _open(self):
        import smtplib
        host = setting("SMTP_HOST").strip()
        user = setting("SMTP_USER").strip()
        pw = setting("SMTP_PASS")
        try:
            port = int((setting("SMTP_PORT") or "587").strip() or 587)
        except ValueError:
            port = 587
        if port == 465:
            srv = smtplib.SMTP_SSL(host, port, timeout=30)
        else:
            srv = smtplib.SMTP(host, port, timeout=30)
            srv.ehlo()
            if srv.has_extn("starttls"):
                srv.starttls()
                srv.ehlo()
        if pw:
            srv.login(user, pw)
        return srv

    def _drop(self):
        if self.srv is not None:
            try:
                self.srv.quit()
            except Exception:
                pass
        self.srv = None

    def close(self):
        self._drop()
        if self.imap is not None:
            try:
                self.imap.logout()
            except Exception:
                pass
        self.imap = None

    def _copy(self, msg) -> bool:
        """A copy in his Sent folder. Never stops a send if it cannot."""
        if not self.copies or self.copy_error:
            return False
        try:
            import imaplib
            import time as _t
            if self.imap is None:
                self.imap = imap_open()
                self.folder = sent_folder(self.imap)
                if not self.folder:
                    self.copy_error = "your mailbox has no Sent folder that could be found"
                    return False
            box = f'"{self.folder}"' if " " in self.folder else self.folder
            typ, _d = self.imap.append(box, "\\Seen", imaplib.Time2Internaldate(_t.time()),
                                       msg.as_bytes())
            if typ != "OK":
                self.copy_error = f"your mailbox would not take the copy ({typ})"
                return False
            return True
        except Exception as e:
            self.copy_error = f"could not save a copy to your Sent folder ({type(e).__name__}: {e})"
            self.imap = None
            return False

    def deliver(self, msg) -> dict:
        """Send one message. {ok, error, transient, refused, copied}."""
        import random
        import time as _t
        if self.last:
            wait = self.gap - (_t.monotonic() - self.last)
            if wait > 0:
                self.sleep(wait)
        err = None
        for attempt in range(TRIES):
            try:
                if self.srv is None:
                    self.srv = self._open()
                self.srv.send_message(msg)
                self.last, self.gap = _t.monotonic(), random.uniform(*self.pace)
                self.sent += 1
                return {"ok": True, "copied": self._copy(msg)}
            except Exception as e:
                err = e
                self._drop()
                if not _transient(e) or attempt == TRIES - 1:
                    break
                self.sleep(RETRY_AFTER * (2 ** attempt))
        self.last, self.gap = _t.monotonic(), random.uniform(*self.pace)
        said = smtp_said(err)
        if _transient(err):
            said = (f"your mail server kept dropping the connection ({said}) — tried "
                    f"{TRIES} times. It is tried again on the next run, or press Try again.")
        return {"ok": False, "error": said, "transient": _transient(err),
                "refused": _refused_address(err)}


class batch:
    """`with batch():` — every _smtp_send inside shares one Mailer (one login,
    paced, with Sent copies). Nested batches share the outer one."""

    def __init__(self, **kw):
        self.kw, self.mine = kw, False

    def __enter__(self):
        global _BATCH
        if _BATCH is None:
            _BATCH, self.mine = Mailer(**self.kw), True
        return _BATCH

    def __exit__(self, *a):
        global _BATCH
        if self.mine and _BATCH is not None:
            _BATCH.close()
            _BATCH = None


def _message(sender, to, subject, body, html="", from_name="", headers=None):
    """The message itself: text, the designed version beside it, and the
    headers a receiving server looks for before it trusts a mail."""
    from email.message import EmailMessage
    from email.utils import formataddr, formatdate, make_msgid
    msg = EmailMessage()
    msg["From"] = formataddr((from_name, sender)) if from_name else sender
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=(sender.rsplit("@", 1)[-1] or None))
    for k, v in (headers or {}).items():
        if k and v and k.lower() not in ("from", "to", "subject", "date", "message-id"):
            msg[k] = v
    msg.set_content(body or "")
    if html:
        # add_alternative, not set_content: set_content would throw the plain
        # text away and leave a message with no text part at all.
        msg.add_alternative(html, subtype="html")
    return msg


def send_plain(to: str, subject: str, body: str, html: str = "",
               from_name: str = "", headers=None, rehearse: bool = False) -> dict:
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

    `rehearse=True` — the campaign's "Send me a test": the same sending-domain
    rule, sender name and headers as the real thing, so a test that arrives
    means the real mail would leave too. His first test arrived through a path
    that skipped the domain rule, and the campaign then did not go at all.
    """
    to = (to or "").strip()
    if "@" not in to or " " in to:
        return {"ok": False, "error": f"not an email address: {to!r}"}
    host = setting("SMTP_HOST").strip()
    user = setting("SMTP_USER").strip()
    if not host or not user:
        return {"ok": False, "error": "SMTP not configured (set it on /setup "
                                      "→ Delivery mail)"}
    sender = sender_address() or user
    if rehearse:
        wrong = domain_problem(sender)
        if wrong:
            return {"ok": False, "wrong_domain": True,
                    "error": "your leads would not get this either: " + wrong}
    msg = _message(sender, to, subject or "(no subject)", body, html, from_name, headers)
    with Mailer(copies=False) as m:
        r = m.deliver(msg)
    if not r["ok"]:
        return {"ok": False, "error": r["error"]}
    return {"ok": True, "to": to, "from": sender}


# ---------------------------------------------------------------------------
# THE SENDING DOMAIN, AND SAYING PLAINLY WHY A MAIL DID NOT GO
# ---------------------------------------------------------------------------
#
# "i approved the compaign and click on sent and it did no sent the mails while
#  when i tested on my own mail it sent me the mail"
#
# The test went to his own inbox through send_plain, which never looks at the
# outreach rules. The campaign went through _smtp_send, which refuses anything
# not sent from the "Outreach domain" saved on Setup — compared letter for
# letter, "@" + the setting. Setup told him to use "a subdomain, not your main
# domain", his mailbox is on his main domain, and a website address typed with
# https:// or www. never matched anything. Seven refusals, each one filed as
# "approved — goes with the next allowance", and the reason on no screen.
#
# The rule stands — cold mail only from the domain he approved. What changed:
# the setting is read the way a person types it, a subdomain of the approved
# domain counts as on it, and when it really does not match, the refusal says
# which address, which domain, and exactly what to change.

def _bare_domain(value) -> str:
    """'https://www.Example.com/contact' → 'example.com'; '@example.com' → 'example.com'."""
    s = (value or "").strip().lower()
    s = re.sub(r"^[a-z][a-z0-9+.-]*://", "", s)
    s = s.split("/", 1)[0].split("?", 1)[0].strip()
    if "@" in s:
        s = s.rsplit("@", 1)[1]
    s = s.strip(" .")
    return s[4:] if s.startswith("www.") else s


def sender_address() -> str:
    """The address mail goes out from — the bare address, even if Setup holds
    'Name <address>'."""
    from email.utils import parseaddr
    raw = (setting("SMTP_FROM") or setting("SMTP_USER") or "").strip()
    return (parseaddr(raw)[1] or raw).strip()


def domain_problem(sender=None) -> str:
    """'' when the sender is on the approved outreach domain; otherwise what is
    wrong and how to put it right, in his words."""
    approved = _bare_domain(setting("OUTREACH_DOMAIN"))
    if not approved:
        return ""
    addr = (sender or sender_address()).strip()
    own = addr.rsplit("@", 1)[1].lower().strip() if "@" in addr else ""
    if own and (own == approved or own.endswith("." + approved)):
        return ""
    return (f"your email sends from {addr or 'another address'}, which is not on the "
            f"approved outreach domain ({approved}) saved on Setup. Change 'Outreach "
            f"domain' on Setup to {own or 'the part after the @ in your address'}, or "
            f"send from an address at {approved}.")


def preflight() -> dict:
    """
    Could outreach be sent right now, and if not, why — checked before a single
    mail is queued, so a refusal is a sentence on the screen instead of seven
    mails parked as "approved".
    """
    problems = []
    if not setting("SMTP_HOST").strip() or not setting("SMTP_USER").strip():
        problems.append("Your email is not set up — add it on Setup → Integrations → "
                        "Email (SMTP).")
    wrong = domain_problem()
    if wrong:
        problems.append(wrong[:1].upper() + wrong[1:])
    addr = sender_address()
    own = addr.rsplit("@", 1)[1].lower() if "@" in addr else ""
    room = max(0, daily_cap() - sent_today())
    return {"ok": not problems, "problems": problems, "sender": addr,
            "fix_domain": own if wrong and own else "", "room": room,
            "cap": daily_cap()}


def smtp_said(e) -> str:
    """What the mail server actually answered, as a sentence he can act on."""
    import smtplib

    def text(v):
        return v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v)
    if isinstance(e, smtplib.SMTPAuthenticationError):
        return (f"your mail server refused the login ({e.smtp_code} "
                f"{text(e.smtp_error)[:160]}) — check the email password on Setup")
    if isinstance(e, smtplib.SMTPRecipientsRefused):
        bits = [f"{to}: {code} {text(msg)[:120]}" for to, (code, msg) in e.recipients.items()]
        return "the mail server would not deliver to " + "; ".join(bits)
    if isinstance(e, smtplib.SMTPSenderRefused):
        return (f"the mail server would not send from {text(e.sender)} "
                f"({e.smtp_code} {text(e.smtp_error)[:160]})")
    if isinstance(e, smtplib.SMTPResponseException):
        return f"the mail server said {e.smtp_code} {text(e.smtp_error)[:200]}"
    return f"could not reach the mail server: {type(e).__name__}: {e}"


def _default_subject() -> str:
    """What a mail with no subject line of its own is about: the business's trade."""
    try:
        from coordination import profile
        if profile.is_starter():
            return "Glass & aluminium work"
        t = profile.business().get("trade_short") or profile.business().get("trade") or ""
        return (t[:1].upper() + t[1:] + " work") if t else "A note from us"
    except Exception:
        return "Glass & aluminium work"


def country_rule(lead=None) -> dict:
    """
    {may_send, rule, why} — may this website write to a business first? A lead
    found in another of his markets is written to under THAT country's rule.
    """
    try:
        from coordination import profile
        cc = str((lead or {}).get("country") or "").upper()
        r = profile.outreach_for(cc) if cc else profile.outreach()
    except Exception:
        return {"may_send": True, "rule": "allowed", "why": ""}
    if r["may_send"]:
        return {"may_send": True, "rule": r["rule"], "why": r.get("law", "")}
    if r["rule"] == "consent":
        why = (r["law"] + " So the agency writes these as drafts for you to send to "
               "people who have agreed to hear from you, and sends nothing itself.")
    else:
        why = r["law"]
    return {"may_send": False, "rule": r["rule"], "why": why}


def _smtp_send(lead: dict, draft: str, html: str = "", from_name: str = "",
               headers=None) -> dict:
    """
    Deliver via SMTP using the credentials saved on /setup (env: SMTP_*). Kept as
    the single send boundary: no other code emails directly.

    `html` rides along as an alternative to the text, as in send_plain — the
    campaign is a designed mail, and it used to leave as bare text because this
    boundary could not carry anything else. `from_name` is who the inbox shows
    it is from (the company, not an address). `headers` adds lines such as
    List-Unsubscribe.

    Three things happen here and nowhere else, because this is the only place
    that can be sure of them:

      the cap is checked HERE, not by the caller. The handbook red line is a
      daily limit and the right sending domain; a limit enforced by whoever
      happens to call is a limit that gets skipped by the next caller written.

      the send is real, via smtplib, using exactly the keys test_email() proves.

      the send is LOGGED, before we return. An unlogged send is invisible to the
      cap, to the dedup promise, and to every counter the owner reads.
    """
    host = setting("SMTP_HOST").strip()
    user = setting("SMTP_USER").strip()
    to = (lead.get("email") or "").strip()
    if not host or not user:
        return {"ok": False, "error": "SMTP not configured (set it on /setup → Integrations)"}
    if not to:
        return {"ok": False, "error": "this lead has no email address"}

    # --- the country's rule on writing to a business first -------------------
    # Not ours to decide, and not the same everywhere (coordination/countries):
    # where it is settled that a business may be written to with an opt-out
    # (the UAE, the UK, the US) this passes; where consent is needed first
    # (Germany, Austria) nothing goes; anywhere else nothing goes until the
    # owner has read his country's rule and confirmed it on Setup.
    rule = country_rule(lead)
    if not rule["may_send"]:
        return {"ok": False, "law": True, "error": "not sent: " + rule["why"]}

    # --- the cap, before anything leaves ------------------------------------
    cap = daily_cap()
    already = sent_today()
    if already >= cap:
        return {"ok": False, "capped": True,
                "error": f"daily outreach cap reached ({already}/{cap}). Nothing sent. "
                         "Raise it on /setup → Daily orders if you really want more."}

    # --- the sending domain, as the handbook requires ------------------------
    sender = sender_address()
    wrong = domain_problem(sender)
    if wrong:
        return {"ok": False, "wrong_domain": True, "error": "refusing to send: " + wrong}

    subject, body = _split_subject(draft)
    msg = _message(sender, to, subject or _default_subject(), body or draft,
                   html, from_name, headers)
    # Inside `with batch():` the batch's connection, pace and Sent copies are
    # shared; on its own, one mail gets a Mailer of its own.
    if _BATCH is not None:
        r = _BATCH.deliver(msg)
    else:
        with Mailer() as m:
            r = m.deliver(msg)
    if not r["ok"]:
        return {"ok": False, "error": r["error"], "transient": r.get("transient"),
                "refused": r.get("refused")}

    # It has gone. Record it before returning — a send we did not write down is
    # a send the cap cannot see and the Scanner cannot dedup against.
    row = log_outreach(lead.get("id"), draft)
    return {"ok": True, "to": to, "outreach_log_id": row,
            "sent_today": already + 1, "cap": cap, "copied": bool(r.get("copied"))}


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

# ---------------------------------------------------------------------------
# BOUNCES — the mails that came back, read out of his own inbox
# ---------------------------------------------------------------------------
#
# "Undelivered Mail Returned to Sender ... <mqasim@uniquerealestate.ae>: host
#  smtp.google.com said: 550-5.1.1 The email account that you tried to reach
#  does not exist."
#
# His mail server accepted the mail and passed it on; the receiving side sent
# it back. That report lands in HIS inbox, not the agency's, so the Mail room
# kept calling the lead "written to" while the address did not exist. The
# reports are read from his inbox — read-only, nothing marked, moved or
# deleted — and the mail and the lead are marked as bounced, with the reason in
# plain words.

_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct",
           "Nov", "Dec")
BOUNCE_FROM = ("MAILER-DAEMON", "postmaster", "Mail Delivery")
_ADDR = r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"


def bounce_words(status="", said="") -> str:
    """What a bounce means, in his words."""
    t = f"{status} {said}".lower()
    if re.search(r"5\.1\.[0-3]|does not exist|no such user|user unknown|unknown user|"
                 r"not found|invalid recipient|address rejected|nosuchuser|"
                 r"mailbox unavailable|recipient.*(?:unknown|rejected)", t):
        return "the address does not exist"
    if re.search(r"5\.2\.2|mailbox full|quota|over quota|insufficient storage", t):
        return "their mailbox is full"
    if re.search(r"5\.1\.2|domain.*(?:not found|does not exist)|host not found|"
                 r"no mx|name service error", t):
        return "that domain does not take mail"
    if re.search(r"5\.7\.|spam|blocked|blacklist|blocklist|policy|rejected due|"
                 r"not authori[sz]ed|dmarc|spf", t):
        return "their server refused it as unwanted mail"
    s = " ".join(str(said or "").split())
    return "it came back: " + (s[:140] if s else (status or "no reason given"))


def parse_bounce(raw) -> list:
    """
    The failed recipients in one bounce report: [{address, status, said, words}].
    Reads the standard delivery-status part when there is one, and the
    "<address>: host ... said: 550 ..." text Postfix writes when there is not.
    Delayed-delivery warnings (4.x.x) are not bounces and are left out.
    """
    import email
    from email import policy
    msg = email.message_from_bytes(raw if isinstance(raw, bytes) else str(raw).encode(),
                                   policy=policy.default)
    found = {}
    for part in msg.walk():
        if part.get_content_type() != "message/delivery-status":
            continue
        blocks = part.get_payload() if part.is_multipart() else []
        if not isinstance(blocks, list):
            blocks = []
        for b in blocks:
            rcpt = str(b.get("Final-Recipient") or b.get("Original-Recipient") or "")
            m = re.search(_ADDR, rcpt)
            if not m:
                continue
            status = str(b.get("Status") or "").strip()
            action = str(b.get("Action") or "").strip().lower()
            if action and action not in ("failed",):
                continue
            if status and not status.startswith("5"):
                continue
            said = " ".join(str(b.get("Diagnostic-Code") or "").split())
            said = re.sub(r"^smtp;\s*", "", said, flags=re.I)
            found[m.group(0).lower()] = {"address": m.group(0).lower(), "status": status,
                                         "said": said[:300]}
    if not found:
        text = ""
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    text += part.get_content() + "\n"
                except Exception:
                    pass
        for m in re.finditer(r"<(" + _ADDR + r")>:\s*(.+?)(?=\n\s*\n|\n<|\Z)", text, re.S):
            said = " ".join(m.group(2).split())
            code = re.search(r"\b([45]\d\d)[ -]([45]\.\d{1,3}\.\d{1,3})?", said)
            if code and code.group(1).startswith("4"):
                continue
            found[m.group(1).lower()] = {
                "address": m.group(1).lower(),
                "status": (code.group(2) or code.group(1)) if code else "",
                "said": said[:300]}
    out = []
    for f in found.values():
        f["words"] = bounce_words(f["status"], f["said"])
        out.append(f)
    return out


def read_bounces(days=10, im=None) -> dict:
    """Bounce reports in his inbox from the last `days` days. Read-only."""
    import time as _t
    own = im is None
    try:
        im = im or imap_open()
    except Exception as e:
        return {"ok": False, "error": f"could not open your mailbox to look for bounces "
                                      f"({type(e).__name__}: {e})", "bounces": []}
    try:
        im.select("INBOX", readonly=True)
        d = _t.localtime(_t.time() - days * 86400)
        since = f"{d.tm_mday:02d}-{_MONTHS[d.tm_mon - 1]}-{d.tm_year}"
        ids = []
        for who in BOUNCE_FROM:
            typ, data = im.search(None, f'(SINCE {since} FROM "{who}")')
            for i in ((data or [b""])[0] or b"").split():
                if i not in ids:
                    ids.append(i)
        out = []
        for i in ids[-200:]:
            typ, parts = im.fetch(i, "(BODY.PEEK[])")
            raw = next((p[1] for p in parts or [] if isinstance(p, tuple)), b"")
            for b in parse_bounce(raw):
                b["message"] = i.decode() if isinstance(i, bytes) else str(i)
                out.append(b)
        return {"ok": True, "bounces": out, "looked_at": len(ids)}
    except Exception as e:
        return {"ok": False, "error": f"could not read the bounce reports "
                                      f"({type(e).__name__}: {e})", "bounces": []}
    finally:
        if own:
            try:
                im.logout()
            except Exception:
                pass


def mark_bounces(bounces) -> int:
    """Mark the mails (and their leads) that came back. Returns how many are new."""
    import sqlite3
    import time as _t
    from coordination import board as _board
    new = 0
    with sqlite3.connect(_board.DB, timeout=30) as c:
        _board._migrate(c)
        for b in bounces or []:
            addr = (b.get("address") or "").strip().lower()
            if not addr:
                continue
            cur = c.execute(
                "UPDATE outreach_queue SET bounced_at=?, bounce_reason=?"
                " WHERE lower(to_email)=? AND status='sent' AND bounced_at IS NULL",
                (int(_t.time()), (b.get("words") or "it came back")[:200], addr))
            new += cur.rowcount or 0
            c.execute("UPDATE leads SET stage='bounced' WHERE lower(email)=?", (addr,))
    return new


def check_bounces(days=10) -> dict:
    """Read his inbox for bounces and mark them. {ok, new, bounces, error}."""
    r = read_bounces(days)
    if not r.get("ok"):
        return {"ok": False, "new": 0, "bounces": [], "error": r.get("error")}
    n = mark_bounces(r["bounces"])
    return {"ok": True, "new": n, "bounces": r["bounces"], "looked_at": r.get("looked_at", 0)}


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
                    "areas": uae.served()}

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
