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


def writing_brief(lead: dict, company: dict, owner_voice: str = "") -> dict:
    """The full instruction the sector brain writes against."""
    stage = lead.get("stage", "new")
    et = email_type(stage)
    return {
        "email_type": et["type"],
        "goal": et["goal"],
        "single_ask": et["ask"],
        "recipient": {"name": lead.get("name"), "company": lead.get("company"),
                      "area": lead.get("area"), "segment": lead.get("segment")},
        "language": "British English",
        "length_words": [90, 130],
        "voice": owner_voice or "plain, human, confident, no filler — a tradesman with 10+ years, writing like a person",
        "attention_levers": _LEVERS,
        "must_include": ["the reader's area + service", "one real proof point", "one clear ask"],
        "must_avoid": ["fake project photos", "invented reviews or numbers",
                       "any price or special deal (owner sign-off only)", "walls of text"],
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


def _smtp_send(lead: dict, draft: str) -> dict:
    """
    Deliver via SMTP using the credentials saved on /setup (env: SMTP_*). Kept as
    the single send boundary: no other code emails directly. Returns a clear
    message when SMTP isn't configured rather than pretending to send.
    """
    import os
    host = os.environ.get("SMTP_HOST", "")
    if not host:
        return {"ok": False, "error": "SMTP not configured (set it on /setup → Integrations)"}
    # A real send goes here (smtplib). Left unbuilt on purpose until the owner
    # adds SMTP + confirms outreach caps — this module must not silently email.
    return {"ok": False, "error": "SMTP send not enabled yet — add SMTP on /setup and turn on outreach"}


if __name__ == "__main__":  # show a plan (pure, no side effects)
    import json
    demo_lead = {"name": "Sara", "company": "Marina Fitout LLC", "area": "Dubai Marina",
                 "segment": "fitout", "stage": "new", "email": "hello@example.ae"}
    demo_company = {"name": "Glassworks Dubai", "phone": "+9715...", "whatsapp": "+9715..."}
    pl = plan(demo_lead, demo_company)
    print(json.dumps(pl, indent=2))
    # tiny self-checks
    assert pl["email_type"] == "cold_intro"
    assert email_type("won")["type"] == "thank_you"
    assert "email" in pl["shelves_needed"]
    print("\nplan self-checks passed")
