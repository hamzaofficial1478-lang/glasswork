"""
ask.py — a sector working out what it needs from another sector.

WHAT THE OWNER NOTICED, AND HE WAS RIGHT

The sectors do talk to each other, and the traffic on the office floor is real
— every beam is a row on the request board. But he looked at what they were
SAYING and found the same sentences every time, and that was fair. Each request
site in the pipelines looked like this:

    if search_console_failed:
        post_request("SEO", "IT", "Search Console is not answering", <fixed text>)

Three things were true of that, and only two of them were good:

  the TRIGGER was real      it fired because a row said so, never on a timer
  the EVIDENCE was real     the numbers and quotes came out of the cabinet
  the THINKING was not      the subject, the wording and above all the CHOICE
                            of who to ask were written by me, months ago, and
                            the agent contributed nothing

So a sector could not notice that a problem was really Content's and not IT's.
It could not decide that something was too small to be worth interrupting anyone
over. It could not put two findings together into one ask. It filled in a form.

WHAT THIS DOES

The sector's own routed brain is given its situation and asked three questions
it could not previously answer for itself:

  1. is this worth another sector's time at all, right now?
  2. which sector actually owns it?
  3. what is the ask, in its own words?

THE RULE THAT MAKES THIS SAFE

The model writes the ASK. It does not supply the FACTS. Every fact is gathered
from rows by the calling pipeline and appended to the request by this module,
verbatim, after whatever the model wrote. A model that invents a number cannot
remove or alter the evidence block underneath it, and a reader can always check
the wording against the rows it claims to be about. This is the same rule as
everywhere else in the agency: reasoning may be generated, facts may not.

If no brain is routed to that sector, or the call fails, the template is used
and the request says plainly that it was composed from a template because no
model is configured. An agency with no API keys still coordinates; it just does
it in my words instead of its own.
"""
import json
import re

try:
    from . import board
except ImportError:  # pragma: no cover
    import board

# Which brain belongs to which sector — the rows of the Claude team table on
# the Setup page (providers/claude_team.py). The Critic has its own model now,
# a different one from the MD it checks.
BRAIN_OF = {
    "director": "md", "md": "md", "overseer": "md", "critic": "critic",
    "it": "it", "content": "content", "seo": "seo", "leads": "leads",
    "report": "report", "reporter": "report", "security": "security",
}

# Who a sector is allowed to address. Not a style rule — the handbook gives each
# sector a home turf, and a request sent to the wrong room sits unanswered while
# the asker waits. The model may redirect, but only within this list.
SECTORS = ["Director", "IT", "Content", "SEO", "Leads", "Report", "Security"]

WHO_OWNS_WHAT = """\
Director  the day's plan, priorities between sectors, anything needing the owner
IT        the website itself: pages, speed, releases, Search Console, anything
          touching the live site. IT is the ONLY sector that may change it
Content   what the business says it sells, the service catalogue, pages, posts
SEO       keywords, rankings, what is worth targeting, rival movements
Leads     finding and contacting prospects, replies from real people
Report    scoring what happened, marking past decisions worked or failed
Security  screening anything that came from outside before acting on it"""

SYSTEM = """\
You are the {sector} sector of a small agency running the online growth of a
glass and aluminium business in the UAE. You are about to ask another sector for
something, and you decide what to say.

Three questions, in order:

1. IS THIS WORTH IT? Another sector reading this stops what it is doing. A
   thing you can work around, or that will resolve itself, is not worth an
   interruption. Saying "not worth asking" is a real answer and costs nothing.
2. WHO OWNS IT? Send it to the sector whose job it actually is:

{who_owns_what}

3. WHAT IS THE ASK? One short paragraph, plain British English. Say what you
   need and what it unblocks. No greeting, no sign-off, no restating the
   evidence — the evidence is attached underneath automatically.

DO NOT INVENT ANYTHING. You have been given every fact you have. Do not add
numbers, names, dates or causes that are not in them. If the facts are too thin
to make a clear ask, say that instead of padding it.

Reply with JSON and nothing else:
{{"ask": true|false, "to": "<sector>", "subject": "<under 70 characters>",
  "body": "<one paragraph>", "why": "<why you decided this, one line>"}}"""


def _facts_block(facts):
    lines = [f for f in (facts or []) if str(f).strip()]
    if not lines:
        return ""
    return ("\n\n--- the rows this is about (gathered from the cabinet, not written "
            "by a model) ---\n" + "\n".join("  " + str(l) for l in lines))


def _parse(out):
    """The model's JSON, or None. Tolerates a fenced block or stray prose."""
    txt = (out or "").strip()
    if txt.startswith("```"):
        txt = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", txt).strip()
    i, j = txt.find("{"), txt.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        d = json.loads(txt[i:j + 1])
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def _clean_sector(name, fallback):
    """
    The sector a model named, or the caller's own choice if it named nonsense.

    Aliases go through coordination/inbox, so a brain that writes "MD" — the
    obvious thing to write, and what pipelines/mail.py had hardcoded — reaches
    the Director rather than being quietly swapped for the fallback and the
    redirect reported as though the model had chosen it.
    """
    from . import inbox
    try:
        return inbox.to_sector(name)
    except inbox.UnknownSector:
        return fallback


def compose(from_sector, to_sector, situation, facts,
            fallback_subject, fallback_body, max_tokens=420) -> dict:
    """
    Work out what to ask, or decide not to. Writes nothing — see send().

    Returns {ask, to, subject, body, reasoned, why, note}. `reasoned` is False
    when the template was used, and `note` says why, so a reader can always tell
    a sector's own words from mine.
    """
    out = {"ask": True, "to": to_sector, "subject": fallback_subject,
           "body": fallback_body, "reasoned": False, "why": "", "note": ""}

    task = BRAIN_OF.get(str(from_sector).strip().lower())
    if not task:
        out["note"] = (f"composed from a template — '{from_sector}' has no routed "
                       "brain of its own, and borrowing another sector's would put "
                       "this in the wrong voice")
        return out

    try:
        from providers import call_task, resolve_task
    except Exception as e:
        out["note"] = f"composed from a template — the provider shelf did not load ({e})"
        return out

    route = resolve_task(task)
    if not route.get("ok"):
        out["note"] = ("composed from a template — no model is routed to "
                       f"{from_sector} yet, so it cannot word this itself")
        return out

    user = (f"SITUATION: {situation}\n\n"
            "THE FACTS YOU HAVE:\n" + "\n".join("  " + str(f) for f in (facts or []))
            + f"\n\nYour first instinct was to ask {to_sector}. Decide properly.")
    # This only chooses the WORDING of a request another sector will read, and
    # it already degrades honestly: no answer means the template goes instead,
    # marked as a template so nobody mistakes it for the sector's own voice.
    r = call_task(task, [
        {"role": "system",
         "content": SYSTEM.format(sector=from_sector, who_owns_what=WHO_OWNS_WHAT)},
        {"role": "user", "content": user},
    ], max_tokens=max_tokens)

    if not r.get("ok"):
        out["note"] = (f"composed from a template — {from_sector}'s brain did not "
                       f"answer ({r.get('error', 'no reason given')})")
        return out

    d = _parse(r.get("output"))
    if not d:
        out["note"] = ("composed from a template — the brain's reply was not in the "
                       "shape asked for, so it was not trusted to be the message")
        return out

    subject = str(d.get("subject") or "").strip()[:70]
    body = str(d.get("body") or "").strip()
    if not subject or not body:
        out["note"] = "composed from a template — the brain returned an empty ask"
        return out

    out.update({
        "ask": bool(d.get("ask", True)),
        "to": _clean_sector(d.get("to"), to_sector),
        "subject": subject, "body": body,
        "reasoned": True,
        "why": str(d.get("why") or "").strip()[:300],
        "note": "",
        "brain": r.get("used"),
    })
    return out


def send(from_sector, to_sector, situation, facts,
         fallback_subject, fallback_body, force=False) -> dict:
    """
    Decide, then post. Returns what happened, including a decision NOT to ask.

    A sector choosing not to interrupt another is a real outcome and is
    reported, not swallowed — the caller logs it, and the owner can see that a
    sector considered something and judged it not worth anyone's time. `force`
    is for the few asks that must go regardless of what a model thinks: a
    blocking verification another sector is already waiting on.
    """
    d = compose(from_sector, to_sector, situation, facts,
                fallback_subject, fallback_body)

    if not d["ask"] and not force:
        return {"posted": False, "id": None, "to": d["to"], "reasoned": d["reasoned"],
                "why": d.get("why") or "judged not worth another sector's time",
                "subject": d["subject"]}

    # The evidence is attached HERE, by code, from the rows the caller gathered.
    # Whatever the model wrote sits above it and cannot replace it.
    body = d["body"] + _facts_block(facts)
    if d["reasoned"]:
        body += f"\n\n[{from_sector} worded this itself"
        if d.get("why"):
            body += f": {d['why']}"
        if d["to"] != to_sector:
            body += f". It was going to ask {to_sector} and decided this is yours"
        body += "]"
    else:
        body += f"\n\n[{d['note']}]"

    rid = board.post_request(from_sector, d["to"], d["subject"], body)
    return {"posted": True, "id": rid, "to": d["to"], "subject": d["subject"],
            "reasoned": d["reasoned"], "why": d.get("why", ""),
            "note": d.get("note", ""),
            "redirected": d["to"] != to_sector}
