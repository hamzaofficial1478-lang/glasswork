"""
chat_actions.py — what an agent may put on the board from the chat, and the
checks each one passes first.

"firstly asked to MD and get the same response then askled to IT fxer and get
 the same response why they cannot record ticket from chat? ... make all sector
 and their agents like this so they can post and record the ticket from the chat"

The chat was built to explain and never to act — its instructions said "You
cannot take actions in this conversation" — so when he told the Director and
then the Fixer that the email on his website was a dummy, each explained that
no ticket existed and asked him to go and make one. The owner is the one who
makes tickets. Telling him to make one somewhere else is the chat failing.

Now any desk can record what he asks for. The model writes one ACTION line per
thing at the end of its reply; this module reads the lines, checks each, does
it, and replaces them with a plain line saying what was recorded. The model
never touches the board itself, and nothing here goes around the red lines:

  ticket          something wrong or to change on his website — a site issue,
                  for the Fixer. The Fixer writes the change; the Releaser, the
                  only thing that writes to his site, carries it out.
  order           work for a sector, as his order — the board puts his orders
                  first (dispatch._owners_order).
  replace         "change this text on my website to that". A site change for
                  the Releaser. Approved at once only when the new text is
                  in his own message — he asked for exactly that — otherwise
                  it waits for his yes like any other change.
  use_one_email   "the email on my website is wrong, ours is X". Every address
                  on his own domains made X, by the Releaser's use_one_email.
                  X is saved as his business address if he gave a new one.

Nothing here sends a mail, spends money or offers a price.
"""
import json
import re

from . import board

ACTION_LINE = re.compile(r"^[ \t>*-]*ACTION:[ \t]*(\{.*\})[ \t]*$", re.M)
SECTORS = ("Director", "IT", "Content", "SEO", "Leads", "Report", "Security")
MOST = 4                   # things one message may record
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}")

HOW = """YOU CAN RECORD WORK ON THE BOARD. When the owner asks for something to be
done — fixed, changed, added, removed, looked into — record it. Never tell him
to make a ticket somewhere else or that you cannot act: he is the one who makes
tickets, and this chat is where he makes them. End your reply with one line per
thing, exactly like these (plain JSON on one line, nothing after it):

ACTION: {"do": "ticket", "title": "short title", "detail": "what is wrong or wanted, and where"}
ACTION: {"do": "order", "sector": "IT|Content|SEO|Leads|Security|Report|Director", "title": "short title", "detail": "what to do"}
ACTION: {"do": "replace", "find": "exact text now on his website", "with": "exact new text he gave"}
ACTION: {"do": "use_one_email", "to": "the address he says is his"}

- ticket: something wrong or to change on his website. It goes to IT's Fixer.
- order: work for a sector. Use the right sector, not always your own.
- replace: only when you know the exact text on the site and the exact new text.
- use_one_email: when he says an email address on his website is wrong or a
  dummy. Every address on his own domains becomes the one he gives.
Only record what he actually asked for, once. In your reply say, in one line,
what you recorded and what happens next. Do not invent ticket numbers — the
system adds them."""


def split(reply):
    """(the reply without ACTION lines, [actions])."""
    actions = []
    for m in ACTION_LINE.finditer(reply or ""):
        try:
            a = json.loads(m.group(1))
        except ValueError:
            continue
        if isinstance(a, dict) and a.get("do"):
            actions.append(a)
    text = ACTION_LINE.sub("", reply or "")
    return re.sub(r"\n{3,}", "\n\n", text).strip(), actions[:MOST]


def _flat(s):
    return " ".join(str(s or "").lower().split())


def _his(text, message) -> bool:
    """Is this exact text in his own message? Then he asked for exactly it."""
    t = _flat(text)
    return bool(t) and t in _flat(message)


def _clip(s, n):
    return " ".join(str(s or "").split())[:n]


def do_ticket(a, agent, message):
    title, detail = _clip(a.get("title"), 120), _clip(a.get("detail"), 900)
    if not (title or detail):
        return None, "a ticket needs something to say"
    kind = a.get("kind") if a.get("kind") in ("bug", "content", "speed", "seo") else "bug"
    iid, new = board.file_site_issue(
        kind, "high",
        f"{title}. {detail} (the owner asked for this in the chat, to {agent}: "
        f"\"{_clip(message, 200)}\")".replace(". .", "."),
        url=_clip(a.get("page"), 200), found_by=agent)
    return (f"ticket #{iid} for IT — {title or detail[:80]}"
            + ("" if new else " (it was already on file; brought up to date)")), None


def do_order(a, agent, message):
    sector = str(a.get("sector") or "").strip()
    match = next((s for s in SECTORS if s.lower() == sector.lower()), None)
    if not match:
        return None, f"'{sector}' is not a sector"
    title = _clip(a.get("title"), 120)
    detail = _clip(a.get("detail"), 900)
    if not title:
        return None, "an order needs a title"
    try:
        from . import desks
        desk = desks.for_owner_order(match, f"{title} {detail}")
    except Exception:
        desk = None
    try:
        tid = board.post_task(match, title,
                              f"{detail}\n\nThe owner asked for this in the chat, to {agent}: "
                              f"\"{_clip(message, 300)}\"",
                              created_by="owner", desk=desk)
    except Exception as e:
        return None, f"the {match} board would not take it: {e}"
    if not tid:
        return None, f"{match} is switched off, so it cannot take work"
    return f"order #{tid} on the {match} board — {title}", None


def _approve_and_go(cid):
    board.decide_site_change(cid, "approved")
    start_release()


def do_replace(a, agent, message):
    find, to = str(a.get("find") or "").strip(), str(a.get("with") or "").strip()
    if len(find) < 6 or not to or find == to:
        return None, "a text change needs the exact old text (6+ characters) and the new text"
    if any(ch in find + to for ch in "<>"):
        return None, "only plain text can be changed from the chat, not page code"
    if len(find) > 300 or len(to) > 300:
        return None, "that is too long to change from the chat — record it as a ticket"
    cid = board.queue_site_change(agent, "replace_text", {
        "change": {"replace": find, "with": to},
        "title": f"Change “{find[:60]}” to “{to[:60]}” on your website",
        "why": f"The owner asked for this in the chat, to {agent}: \"{_clip(message, 200)}\""})
    if _his(to, message):
        _approve_and_go(cid)
        return (f"change #{cid}: “{find[:50]}” → “{to[:50]}” — approved on "
                "your word; IT runs the tests and puts it on your site now"), None
    return (f"change #{cid}: “{find[:50]}” → “{to[:50]}” — waiting for "
            "your yes under Waiting on you"), None


def do_use_one_email(a, agent, message):
    from pipelines import site_api
    given = [e for e in _EMAIL.findall(message or "")]
    to = str(a.get("to") or "").strip()
    if to and to.lower() not in {e.lower() for e in given}:
        to = ""                           # only an address he typed himself
    to = to or (given[0] if len(given) == 1 else "")
    saved = ""
    if to and to.lower() != site_api.right_email().lower():
        try:
            from providers import integrations
            if integrations.write_env({"BUSINESS_EMAIL": to}).get("ok"):
                saved = f" (saved {to} as your business address)"
        except Exception:
            pass
    right = site_api.right_email()
    cid = board.queue_site_change(agent, "use_one_email", {
        "change": {"call": "use_one_email"},
        "title": f"Make every email address on your website {right}",
        "why": f"The owner asked for this in the chat, to {agent}: \"{_clip(message, 200)}\""})
    asked = bool(re.search(r"\b(e-?mails?|mail)\b", message or "", re.I))
    if asked:
        _approve_and_go(cid)
        return (f"change #{cid}: every email address on your website becomes {right}"
                f"{saved} — approved on your word; IT runs the tests and puts it on "
                "your site now, and the change log will show each page"), None
    return (f"change #{cid}: every email address on your website becomes {right}{saved} "
            "— waiting for your yes under Waiting on you"), None


DO = {"ticket": do_ticket, "order": do_order, "replace": do_replace,
      "use_one_email": do_use_one_email}


# The one instruction he has had to give three times. If he plainly asks for
# the email on his website to be put right and the model forgets to record it,
# it is recorded anyway — his words are the instruction, not the model's.
_EMAIL_FIX = (re.compile(r"\b(e-?mails?|mail)\b", re.I),
              re.compile(r"\b(web ?site|site|page|footer|contact)\b", re.I),
              re.compile(r"\b(dummy|wrong|fake|incorrect|old|replace|change|fix|update)\b", re.I))


def asks_for_email_fix(message) -> bool:
    return all(p.search(message or "") for p in _EMAIL_FIX)


def take(reply, agent, message) -> dict:
    """Carry out the ACTION lines in a reply. {reply, done, refused}."""
    text, actions = split(reply)
    if asks_for_email_fix(message) and not any(a.get("do") == "use_one_email"
                                               for a in actions):
        actions.append({"do": "use_one_email"})
    done, refused = [], []
    for a in actions:
        fn = DO.get(str(a.get("do")))
        if not fn:
            refused.append(f"'{a.get('do')}' is not something the chat can record")
            continue
        try:
            ok, why = fn(a, agent, message)
        except Exception as e:
            ok, why = None, f"{type(e).__name__}: {e}"
        (done if ok else refused).append(ok or why)
    if done:
        text += "\n\nRecorded:\n" + "\n".join(f"  ✓ {d}" for d in done)
    if refused:
        text += "\n\nNot recorded:\n" + "\n".join(f"  ✕ {r}" for r in refused)
    return {"reply": text.strip(), "done": done, "refused": refused}


# ---------------------------------------------------------------------------
# PUTTING AN APPROVED CHANGE ON HIS SITE NOW
# ---------------------------------------------------------------------------

def start_release() -> bool:
    """The Releaser, in the background — tests first, then the change. Once."""
    import os
    import subprocess
    import sys
    from pathlib import Path
    if os.environ.get("AGENCY_IGNORE_DOTENV") == "1" and \
            os.environ.get("CHAT_ACTIONS_TEST") != "1":
        return False                      # the test suite never starts processes
    try:
        from coordination import review
        if review.busy("it-releaser"):
            return False
    except Exception:
        pass
    kw = {"cwd": str(Path(__file__).parents[1]), "stdin": subprocess.DEVNULL,
          "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        from coordination import console
        kw["creationflags"] = console.background_flags()
    else:
        kw["start_new_session"] = True
    try:
        subprocess.Popen([sys.executable, "-m", "pipelines.it_release"], **kw)
        return True
    except Exception:
        return False
