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
    r"\b\d{2,}\s*(?:\+\s*)?(?:years|projects|clients)\b",
    r"\b(?:100|99)\s*%\b",
)

# A cold mail must say how to stop it. Any one of these counts.
OPT_OUT = (
    r"\breply (?:with )?(?:stop|no thanks|unsubscribe)\b",
    r"\bunsubscribe\b", r"\bopt[- ]out\b",
    r"\bnot interested,? (?:just )?(?:reply|let me know|say)\b",
    r"\btell me to stop\b", r"\band I (?:will|'ll) not write again\b",
    r"\bwon'?t (?:write|contact you) again\b",
    r"\bno reply needed\b",
)

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
    """(subject, body) from a draft written as 'Subject: …' then the body."""
    text = (draft or "").strip()
    m = re.match(r"^\s*subject\s*:\s*(.+?)\s*\n(.*)$", text, re.I | re.S)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    lines = [l for l in text.splitlines()]
    if lines and len(lines[0]) <= 120 and (len(lines) < 2 or not lines[1].strip()):
        return lines[0].strip(), "\n".join(lines[1:]).strip()
    return "", text


def check_outreach(draft, lead=None, company=None, service_label="") -> dict:
    """
    Is this mail fit to leave the building?

    Returns {pass, blocking[], warnings[], subject, words, why}. `blocking` is
    what stops it; `warnings` are worth knowing and do not. Every entry says
    what was found, so a refusal can be argued with rather than just obeyed.
    """
    lead = lead or {}
    try:
        subject, body = split_subject(draft)
        text = f"{subject}\n{body}"
        words = len(re.findall(r"\b[\w'’-]+\b", body))
        blocking, warnings = [], []

        # --- the things that must never go out --------------------------------
        for h in _hits(text, PLACEHOLDER):
            blocking.append(f"an unfilled placeholder is still in it: {h!r}")
        for h in _hits(text, CONCESSION):
            blocking.append(f"it names a price or a concession ({h!r}) — that is "
                            "the owner's call, not an agent's")
        for h in _hits(text, UNBACKED):
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
