"""
auditor.py — the last gate before anything written by an agent leaves.

    from coordination import auditor
    v = auditor.check_outreach(draft, lead=lead, company=company)
    if not v["pass"]: ...

THE HANDBOOK RULE THIS IMPLEMENTS

"No content is published externally without passing the Auditor (Overseer QA)."

It was written down and never built. Every other red line in the handbook is
enforced by something — the daily cap is checked inside the send boundary, the
sending domain is checked there too, the deploy rule is a hook. This one was
enforced by good intentions, which the handbook itself says is not enforcement.

WHY IT MATTERS MORE FOR MAIL THAN FOR ANYTHING ELSE

A bad page can be edited. A bad email has been read. The specific ways a
model-written cold mail goes wrong are narrow and completely checkable, and all
of them have happened to somebody:

  a placeholder that never got filled     "Hi [NAME]" — the single most common
                                          way an automated mail announces
                                          itself as automated
  a claim nobody can stand behind         "we have worked with Emaar" when we
                                          have not. The model does not know
                                          what we have done; it fills a gap
  a price or a concession                 a handbook red line, and the model
                                          reaches for one whenever it is asked
                                          to be persuasive
  no way to make it stop                  a cold mail with no opt-out line is
                                          the thing that gets a domain listed
  the wrong service                       offering shower glass to a signage
                                          company reads as a mail-merge, which
                                          is exactly what it is
  a wall of text                          nobody reads eleven paragraphs from a
                                          stranger

None of that needs a model to judge. Every one is a fact about the text, and a
deterministic check that always runs beats a clever check that sometimes does.
Where a model IS useful is tone, and the model is not the gate — see `advise`.

FAIL CLOSED. A check that errors is a FAIL, not a pass. An auditor that waves
things through when it breaks is worse than no auditor, because everyone stops
looking.
"""
import re

# The words that mean a price concession, in one named place — deliberately not
# written out next to the words for sending, for the reason spelled out in
# coordination/desks.py: the red-line hook watches for that pair sitting close
# together and it is right to.
CONCESSION = (
    r"discount", r"free of charge", r"no charge", r"complimentary",
    r"special offer", r"off the price", r"best price", r"cheapest",
    r"price match", r"% off", r"aed\s*\d", r"dhs?\s*\d",
    # any other website's money too: a price in pounds, euros or dollars is
    # still a price the owner has not approved
    r"[£€$]\s*\d", r"\b(?:gbp|eur|usd|sar|qar|inr|pkr)\s*\d",
)

# Placeholders a template leaves behind when something did not get filled in.
PLACEHOLDER = (
    r"\[[A-Z_ ]{2,30}\]",            # [NAME], [COMPANY NAME]
    r"\{\{[^}]{1,40}\}\}",           # {{first_name}}
    r"\bXX+\b", r"\bTBD\b", r"\bTODO\b", r"\blorem ipsum\b",
    r"\bYOUR COMPANY\b", r"\binsert\b.{0,12}\bhere\b",
    r"\bas an ai\b", r"\blanguage model\b",
)

# Claims about our track record that nobody has evidence for. The business's
# real edge is genuine completed projects — naming one we did not do is the
# fastest way to lose the only edge it has.
UNBACKED = (
    r"\bwe (?:have )?(?:worked|partnered) with\b",
    r"\bour clients? include\b",
    r"\bwe are the (?:number one|leading|best|largest)\b",
    r"\bawarded?\b", r"\bcertified by\b", r"\biso[- ]?900\d\b",
    r"\b\d{2,}\s*(?:\+\s*)?(?:projects|clients|jobs completed)\b",
    r"\b(?:100|99)\s*%\b",
)

# Years in the trade are different: he has them, and he has said how many
# (BUSINESS_YEARS, default 10 — the same figure the offer mail uses). The mail
# writer was TOLD to sound like "a tradesman with 10+ years", and this rule then
# refused every mail that said so — seven drafts in a row, none shown to him.
# A number of years up to his real figure is a fact; more than that is not.
YEARS = r"\b(\d{1,2})\s*(?:\+\s*)?(?:years|yrs)\b"


def verified_years() -> int:
    import os
    v = (os.environ.get("BUSINESS_YEARS") or "").strip()
    if not v and os.environ.get("AGENCY_IGNORE_DOTENV") != "1":
        try:
            from providers import integrations
            v = (integrations.read_env().get("BUSINESS_YEARS") or "").strip()
        except Exception:
            v = ""
    try:
        return int(re.sub(r"\D", "", v) or 10)
    except ValueError:
        return 10


def _unbacked(text) -> list:
    """Claims nobody can stand behind — his real years in the trade excepted."""
    out = _hits(text, UNBACKED)
    yours = verified_years()
    for m in re.finditer(YEARS, text or "", re.I):
        if int(m.group(1)) > yours:
            out.append(m.group(0).strip())
    return out

# A cold mail must say how to stop it. Any one of these counts.
OPT_OUT = (
    r"\breply (?:with )?(?:stop|no thanks|unsubscribe)\b",
    r"\bunsubscribe\b", r"\bopt[- ]out\b",
    r"\bnot interested,? (?:just )?(?:reply|let me know|say)\b",
    r"\btell me to stop\b", r"\band I (?:will|'ll) not write again\b",
    r"\bwon'?t (?:write|contact you) again\b",
    r"\bno reply needed\b",
)

# Formatting the writer meant for a chat window, not an inbox. "**Subject:**
# Glass & aluminium…" and a line of "---" went into real drafts and read as
# exactly what they were: a model's output pasted into an email.
MARKUP = (
    (r"\*\*", "'**'"),
    (r"(?m)^\s*-{3,}\s*$", "a line of '---'"),
    (r"(?m)^\s*#{1,6}\s+\S", "a '#' heading"),
    (r"(?im)^\s*[*_]*subject\s*[*_]*\s*:", "a second 'Subject:' line in the body"),
)

# A personal inbox in a mail that goes out from the company. The writer signed
# drafts with the address it knew for the person running it — his own Gmail —
# instead of the business's.
PERSONAL_INBOX = r"[\w.+-]+@(?:gmail|googlemail|hotmail|yahoo|outlook|live|icloud|aol)\.[a-z.]{2,}"

# Speaking as one person. The campaign is from the company ("we"); a draft
# that says "I work in glass…" and is signed by a man's name is not.
FIRST_PERSON = r"(?<![\w'’])(?:I|I'm|I’m|I'll|I’ll|I've|I’ve|I'd|I’d)(?![\w'’])|\b(?i:my|me)\b"

MAX_WORDS = 220          # a stranger's first mail; longer is not read
MIN_WORDS = 25           # shorter than this is not a message, it is a ping
MAX_SUBJECT = 78         # anything longer is cut off in every mail client


def _hits(text, patterns):
    out = []
    for p in patterns:
        m = re.search(p, text, re.I)
        if m:
            out.append(m.group(0).strip()[:60])
    return out


def split_subject(draft):
    """(subject, body) from a draft written as 'Subject: …' then the body.

    Forgiving about the markdown a writer wraps it in — '**Subject:** …' is
    the same subject line, not a subject that starts with two asterisks.
    """
    text = (draft or "").strip()
    m = re.match(r"^\s*[*_#]*\s*subject\s*[*_]*\s*:\s*[*_]*\s*(.+?)\s*[*_]*\s*\n(.*)$",
                 text, re.I | re.S)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    lines = [l for l in text.splitlines()]
    if lines and len(lines[0]) <= 120 and (len(lines) < 2 or not lines[1].strip()):
        return lines[0].strip(), "\n".join(lines[1:]).strip()
    return "", text


def _without(text, phrases):
    """The text with each of these phrases taken out (case and spacing aside)."""
    flat = re.sub(r"\s+", " ", text or "")
    for p in phrases or ():
        p = re.sub(r"\s+", " ", str(p or "")).strip()
        if len(p) >= 3:
            flat = re.sub(re.escape(p), " ", flat, flags=re.I)
    return flat


def check_outreach(draft, lead=None, company=None, service_label="",
                   owner_offer=(), voice="") -> dict:
    """
    Is this mail fit to leave the building?

    Returns {pass, blocking[], warnings[], subject, words, why}. `blocking` is
    what stops it; `warnings` are worth knowing and do not. Every entry says
    what was found, so a refusal can be argued with rather than just obeyed.

    `owner_offer`: wording the owner wrote and approved himself — his offer and
    its closing date. Those exact words may name a price or a concession; the
    handbook's line is that no agent offers one without him, and this is him.
    Anything of the kind ANYWHERE ELSE in the mail is still refused, so the
    writer cannot slip a second offer in beside his.

    `voice="company"`: the mail is from the business, not one man. "I" and a
    personal signature are refused.
    """
    lead = lead or {}
    try:
        subject, body = split_subject(draft)
        text = f"{subject}\n{body}"
        words = len(re.findall(r"\b[\w'’-]+\b", body))
        blocking, warnings = [], []
        not_his = _without(text, owner_offer)

        # --- the things that must never go out --------------------------------
        for h in _hits(text, PLACEHOLDER):
            blocking.append(f"an unfilled placeholder is still in it: {h!r}")
        for h in _hits(not_his, CONCESSION):
            blocking.append(f"it names a price or a concession ({h!r}) — that is "
                            "the owner's call, not an agent's")
        for pat, what in MARKUP:
            if re.search(pat, text if "subject" not in what else body):
                blocking.append(f"formatting meant for a chat window is in it ({what})")
        inbox = re.search(PERSONAL_INBOX, text, re.I)
        if inbox:
            blocking.append(f"a personal email address is in it ({inbox.group(0)}) — "
                            "mail goes out from the company's address")
        if voice == "company":
            who = (lead.get("company") or "").strip()
            # Links and addresses are not words: wa.me/971… is WhatsApp's own
            # address, not the mail talking about "me".
            prose = re.sub(r"(?:https?://|www\.|wa\.me/)\S+|\S+@\S+", " ",
                           _without(not_his, [who]))
            me = re.search(FIRST_PERSON, prose)
            if me:
                blocking.append(f"it speaks as one person ({me.group(0)!r}) — this "
                                "mail is from the company, so it says 'we'")
        for h in _unbacked(text):
            blocking.append(f"it claims something nobody can stand behind: {h!r}")
        if not subject:
            blocking.append("no subject line")
        elif len(subject) > MAX_SUBJECT:
            blocking.append(f"the subject is {len(subject)} characters — every mail "
                            f"client cuts it at about {MAX_SUBJECT}")
        if not _hits(body, OPT_OUT):
            blocking.append("no way to make it stop. A cold mail without an "
                            "opt-out line is what gets a sending domain listed")
        if words < MIN_WORDS:
            blocking.append(f"{words} words — that is a ping, not a message")

        # --- the things worth knowing -----------------------------------------
        if words > MAX_WORDS:
            warnings.append(f"{words} words — a stranger's first mail is not read "
                            f"past about {MAX_WORDS}")
        who = (lead.get("company") or "").strip()
        if who and who.lower() not in text.lower():
            warnings.append(f"it never says who it is to ({who!r} does not appear)")
        if service_label and service_label.split()[0].lower() not in text.lower():
            warnings.append(f"it does not mention {service_label!r}, which is what "
                            "this lead was matched for")
        if re.search(r"\bDear Sir(?:/| or )?Madam\b", text, re.I):
            warnings.append("'Dear Sir/Madam' reads as a circular")
        if body.count("!") > 2:
            warnings.append("too many exclamation marks for a trade enquiry")

        ok = not blocking
        return {"pass": ok, "blocking": blocking, "warnings": warnings,
                "subject": subject, "words": words,
                "why": ("fit to send" if ok else
                        f"{len(blocking)} thing(s) stop it: " + blocking[0])}
    except Exception as e:
        # FAIL CLOSED. A gate that opens when it breaks is not a gate.
        return {"pass": False, "blocking": [f"the Auditor could not read this "
                                            f"draft ({e}) — refusing rather than "
                                            "guessing"],
                "warnings": [], "subject": "", "words": 0,
                "why": "the Auditor errored, so nothing goes out"}


# ---------------------------------------------------------------------------
# THE SAME GATE, FOR A PAGE
# ---------------------------------------------------------------------------
#
# The docstring above says a bad page can be edited and a bad email has been
# read, and that was the right priority — until Content could actually publish.
# A page on his own domain is his shopfront, it is what Google indexes, and it
# is the thing a customer decides from. It gets a gate too.
#
# The failure modes are not the same as an email's, so the checks are not
# either. What actually goes wrong with a model-written service page:
#
#   an invented credential            "ISO 9001 certified", "500+ projects
#                                     delivered". He has ten years in the trade
#                                     and no certificate to point at, and a
#                                     claim like that on his own site is the
#                                     kind of thing a customer checks
#   a price                           handbook red line, same as an email
#   no way to enquire                 a service page with no phone, no WhatsApp
#                                     and no link to the contact page is a
#                                     leaflet, not a page that wins a job
#   script tags                       an agent writing executable code into his
#                                     website is not a content decision, and
#                                     nothing here has any business doing it
#   a stock-photo promise             the brief says real photographs of real
#                                     finished jobs. A page that ships with an
#                                     <img> pointing at somewhere else is worse
#                                     than a page with a gap where one goes
#   thin                              350 words of padding around one keyword
#                                     is what everyone else's page already is

MIN_PAGE_WORDS = 220
MAX_PAGE_WORDS = 1800

# Stock libraries whose photographs may appear — only captioned as an
# illustration, never passed off as his own work (pipelines/post_images).
STOCK_HOSTS = ("images.pexels.com", "cdn.pixabay.com", "pixabay.com")
ILLUSTRATIVE = "Illustrative photo"

# Anything that executes, embeds, or reaches off the page for code. A content
# agent writes prose and markup; it does not write behaviour.
CODE_IN_A_PAGE = (
    r"<script\b", r"<iframe\b", r"<object\b", r"<embed\b", r"<form\b",
    r"\bon(?:click|load|error|mouseover)\s*=", r"javascript\s*:",
    r"<link\b[^>]*stylesheet", r"<style\b",
)

# A way for a reader to get in touch, which is the entire point of the page.
ENQUIRY = (
    r"\bwhatsapp\b", r"wa\.me/", r"\btel:", r"\bhref\s*=\s*[\"']?/?contact",
    r"(?:\+?971|0)\s?\d[\d\s\-()]{6,13}\d",
    r"\+\d{1,3}[\s\-]?\d[\d\s\-()]{6,13}\d",     # a number in any country
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}",
)

# An image the agency has gone and found somewhere. His own finished work is
# the only photography that belongs on these pages — see the brief's do_not.
FOREIGN_IMAGE = (
    r"<img\b[^>]*src\s*=\s*[\"']https?://(?!(?:www\.)?%s)",
)


def _page_text(html):
    """The words a visitor reads, markup and code out of the way."""
    no_code = re.sub(r"<(script|style)\b[^>]*>.*?</\1\s*>", " ", html or "",
                     flags=re.S | re.I)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", no_code)).strip()


def check_page(html, brief=None, site_host="") -> dict:
    """
    Is this page fit to go on his website?

    Same contract as check_outreach — {pass, blocking[], warnings[], ...} — and
    the same two rules behind it: every check is a fact about the text rather
    than a judgement, and a check that errors is a FAIL. An auditor that waves
    things through when it breaks is worse than no auditor.
    """
    brief = brief or {}
    try:
        text = _page_text(html)
        words = len(re.findall(r"\b[\w'’-]+\b", text))
        blocking, warnings = [], []

        for h in _hits(html, PLACEHOLDER):
            blocking.append(f"an unfilled placeholder is still in it: {h!r}")
        for h in _hits(text, CONCESSION):
            blocking.append(f"it names a price or a concession ({h!r}) — that is "
                            "the owner's call, not an agent's")
        for h in _unbacked(text):
            blocking.append(f"it claims something nobody can stand behind: {h!r}. "
                            "Ten years of real finished jobs is the edge here; an "
                            "invented credential is how you lose it")
        for h in _hits(html, CODE_IN_A_PAGE):
            blocking.append(f"it contains code, not content ({h!r}). A writing desk "
                            "does not put behaviour on his website")
        if not re.search(r"<h1\b", html or "", re.I):
            blocking.append("no <h1> — the page never says what it is")
        if not _hits(html, ENQUIRY):
            blocking.append("nothing on it to enquire with. No number, no WhatsApp, "
                            "no link to the contact page — that is a leaflet")
        if words < MIN_PAGE_WORDS:
            blocking.append(f"{words} words. Under {MIN_PAGE_WORDS} it is a stub, "
                            "and a stub on his own domain competes with his real pages")

        mine = (site_host or "").lower().replace("www.", "")
        for src in re.findall(r"<img\b[^>]*src\s*=\s*[\"'](https?://[^\"']+)", html or "", re.I):
            h = (re.match(r"https?://([^/:]+)", src) or [None, ""])[1].lower()
            if mine and h.replace("www.", "") == mine:
                continue
            if h in STOCK_HOSTS:
                if ILLUSTRATIVE.lower() not in (html or "").lower():
                    blocking.append("a stock photograph is not labelled as an "
                                    "illustration — a customer must never be shown "
                                    "somebody else's glass as his work")
                    break
                continue
            blocking.append("it links a photograph from somebody else's site. "
                            "Only his own finished work, or a labelled stock "
                            "illustration, goes on these pages")
            break

        if words > MAX_PAGE_WORDS:
            warnings.append(f"{words} words — long even for an article; check "
                            "it earns every section")
        heads = len(re.findall(r"<h[23]\b", html or "", re.I))
        if heads < 2:
            warnings.append(f"{heads} subheading(s) — a page a person scans needs "
                            "more than that")
        primary = str(brief.get("primary_keyword") or "").strip().lower()
        if primary and primary not in text.lower():
            warnings.append(f"it never says {primary!r}, which is the phrase this "
                            "page was briefed for")
        area = str(brief.get("area") or "").strip()
        if area and area.lower() not in ("uae-wide", "everywhere", "") and area.lower() not in text.lower():
            warnings.append(f"it never mentions {area}, and the page is meant to be "
                            "the one that ranks there")
        if not re.search(r"<img\b", html or "", re.I):
            warnings.append("no photographs. Real finished work is the whole edge — "
                            "this page needs his own pictures before it earns anything")

        ok = not blocking
        return {"pass": ok, "blocking": blocking, "warnings": warnings,
                "words": words, "headings": heads,
                "why": ("fit to publish" if ok else
                        f"{len(blocking)} thing(s) stop it: " + blocking[0])}
    except Exception as e:
        return {"pass": False,
                "blocking": [f"the Auditor could not read this page ({e}) — "
                             "refusing rather than guessing"],
                "warnings": [], "words": 0, "headings": 0,
                "why": "the Auditor errored, so nothing is published"}


def as_text(v) -> str:
    L = [("PASS — " if v["pass"] else "HELD — ") + v["why"]]
    for b in v["blocking"]:
        L.append(f"   stop  {b}")
    for w in v["warnings"]:
        L.append(f"   note  {w}")
    return "\n".join(L)


if __name__ == "__main__":
    import sys
    print(as_text(check_outreach(sys.stdin.read())))
