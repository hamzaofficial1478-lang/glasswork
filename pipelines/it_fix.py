"""
it_fix.py — the IT Fixer. Takes the tickets the Watcher filed and writes the
actual change, in full, ready for the Releaser to apply. It never touches the
website itself.

    python -m pipelines.it_fix              work the bench
    python -m pipelines.it_fix --list       what is waiting, and what I'd do
    python -m pipelines.it_fix --dry-run    write the patches, queue nothing
    python -m pipelines.it_fix --issue 7    just that one ticket

WHY THIS FILE EXISTS AT ALL

"IT sector's fixer was not working."

It was not working because it did not exist. There is an it-fixer desk in
coordination/desks.py, an it-fixer agent card, an it-fixer avatar on the office
floor — and no pipeline that ever ran as it-fixer. So the Watcher filed tickets
every morning, dispatch correctly routed anything with the word "fix" in it to
a desk with nobody sitting at it, and the board quietly grew. The agency's own
Overseer test says this in as many words:

    "Sending a fix to it-fixer is right — the Watcher cannot write one. But if
     no pipeline runs as it-fixer, that task waits for ever and the board looks
     calm rather than stuck."

That is the bug. This is the desk.

WHAT A "FIX" IS HERE, AND WHAT IT IS NOT

The website is not a repository on this machine. It is a PHP site reached
through the admin endpoint the owner pasted onto his server, so "write the
patch in a branch" has to be translated honestly rather than pretended at. The
translation is: produce the EXACT change — which page, which marked block, the
literal HTML going in, and how to take it back out — and hand it on. Nothing
here calls a write. pipelines/site_api.py refuses this agent by name and queues
instead, which is the mechanism rather than the manners.

A ticket the Fixer cannot honestly patch is not quietly dropped and not
padded out with a plausible-looking change. It is handed up with what a person
would have to decide, because a queue full of confident nonsense is worse than
a queue with three real items in it.

THE RECIPES ARE DELIBERATELY NARROW

Every recipe below is a change where the right answer is a fact, not a
judgement: a viewport tag is the same tag on every site in the world, a
canonical pointing at the wrong hostname has exactly one correct hostname, a
placeholder the generator left behind has one correct value and it is sitting
in .env. Those get patched.

Anything where the right answer is a judgement — what a page's title should
say, whether a slow page needs images shrinking or hosting moving — gets a
recommendation with the reasoning, and stops. The Fixer is allowed to be
uncertain out loud. It is not allowed to guess and call it a patch.
"""
import argparse
import json
import re
import sqlite3
import sys
import time

from coordination import board, deskwork
from . import site_api

AGENT = "it-fixer"
SECTOR = "IT"

# Worst first. A dead enquiry path outranks a cosmetic every time — the site
# keeps attracting people and quietly drops each one, which is worse than not
# ranking at all.
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

# How many tickets one pass will work. Not "all of them": every queued change
# is something the owner has to read and decide, and forty at once is a queue
# he will scroll past rather than a queue he will work.
MAX_PER_PASS = 6


def _now():
    return int(time.time())


# ---------------------------------------------------------------------------
# THE RECIPES
# ---------------------------------------------------------------------------
#
# A recipe answers one question: given this ticket, what EXACTLY changes?
#
# It returns a patch — page, block id, the literal html, and the one-line
# undo — or it returns advice, which is a plan in words for something only a
# person can decide. Both are honest outcomes. A recipe that cannot tell which
# it is returns None and the ticket goes up as unhandled, which is the third
# honest outcome and the one that must never be silently swallowed.

VIEWPORT_TAG = '<meta name="viewport" content="width=device-width, initial-scale=1">'


def _patch(block_id, html, page, why, undoes=""):
    return {"how": "patch", "block_id": block_id, "html": html, "page": page,
            "why": why,
            "undo": undoes or (f"python -m pipelines.site_api --remove-block "
                               f"{block_id} --page {page}")}


def _advice(why, steps, needs=""):
    return {"how": "advice", "why": why, "steps": list(steps), "needs": needs}


def fix_viewport(issue, ctx):
    """
    No viewport meta tag.

    The one tag every phone-shaped visitor needs and the one where there is
    nothing to decide — this is the same line on every site there has ever
    been. It goes in the head block the agency already owns, so it can be
    taken back out again without touching anything else on the page.
    """
    if issue["kind"] != "bug" or "viewport" not in (issue["detail"] or "").lower():
        return None
    page = ctx.get("head_page") or ""
    if not page:
        return _advice(
            "The site has no single file whose <head> the agency can write to, "
            "so this tag has to go in by hand.",
            [f"Paste this into the <head> of your header include: {VIEWPORT_TAG}"],
            needs="somebody with access to the site's files")
    return _patch("viewport", VIEWPORT_TAG, page,
                  "Without it a phone renders the page at desktop width and "
                  "every enquiry button lands off-screen. There is exactly one "
                  "correct value and this is it.")


def fix_canonical(issue, ctx):
    """
    A canonical tag pointing at the wrong hostname.

    site_api works the plan out by reading the pages, so this recipe does not
    invent anything — it asks, and reports what came back. If the plan is empty
    the ticket is stale and says so rather than queueing a change to nothing.
    """
    text = (issue["detail"] or "").lower()
    if "canonical" not in text:
        return None
    try:
        plan = site_api.canonical_plan()
    except Exception as e:
        return _advice(f"Could not read the pages to work out the canonical fix: {e}",
                       ["Check the Admin API address and token on /setup."])
    if not plan.get("ok"):
        return _advice(
            "Could not read the pages to work out the canonical fix: "
            + str(plan.get("error") or "no detail"),
            ["Check the Admin API address and token on /setup."])
    jobs = plan.get("jobs") or []
    if not jobs:
        return {"how": "already_right",
                "why": "Every canonical already points at the right hostname — "
                       "this ticket has been overtaken."}
    return {"how": "call", "call": "fix_canonical",
            "why": (f"{len(jobs)} page(s) name the wrong hostname in their "
                    "canonical tag, which tells search engines the real page is "
                    f"somewhere that does not exist. The right one is "
                    f"{plan.get('want') or 'your own domain'}."),
            "pages": sorted({j.get("page") for j in jobs})[:12],
            "undo": "python -m pipelines.site_api --undo --page <page>"}


def fix_placeholders(issue, ctx):
    """
    'YOUR STREET / UNIT' and friends, still on the site.

    The generator leaves these deliberately. The correct value is not a guess —
    it is in .env, put there by the owner on /setup — so this is a patch, not
    advice. If .env has not been filled in, that is what the ticket needs and
    the Fixer says so instead of writing the placeholder back.
    """
    text = (issue["detail"] or "")
    if not any(p.lower() in text.lower() for p in site_api.PLACEHOLDERS):
        return None
    try:
        found = site_api.find_placeholders()
    except Exception as e:
        return _advice(f"Could not read the pages: {e}",
                       ["Check the Admin API address and token on /setup."])
    if not found.get("ok"):
        return _advice("Could not read the pages: " + str(found.get("error") or ""),
                       ["Check the Admin API address and token on /setup."])
    hits = found.get("found") or []
    if not hits:
        return {"how": "already_right",
                "why": "No placeholder text is on the site any more — this "
                       "ticket has been overtaken."}
    missing = [k for k in set(site_api.PLACEHOLDERS.values())
               if not (ctx.get("env") or {}).get(k)]
    if missing:
        return _advice(
            f"{len(hits)} page(s) still show placeholder text, and the real "
            "value is not saved anywhere yet — so there is nothing to put in "
            "its place.",
            [f"Set {', '.join(sorted(missing))} on /setup, or run: "
             "python -m pipelines.site_api --set-address \"<street>\" --city \"<city>\"",
             "Then run the Fixer again and it becomes an ordinary patch."],
            needs="the real address, from you")
    return {"how": "call", "call": "fix_address",
            "why": (f"{len(hits)} page(s) still show the placeholder text the "
                    "generator left. The real value is already saved, so this "
                    "is a substitution, not a decision."),
            "pages": [h.get("page") for h in hits][:12],
            "undo": "python -m pipelines.site_api --undo --page <page>"}


def fix_wrong_domain(issue, ctx):
    """
    A domain on the pages that is not the one the site is actually on.

    "ask for to change the wrong domain into the website code... that is
     worlddealtechnicalservices.ae to replace with correct domain .com no any
     results at all"

    He asked for this and nothing happened, because the agency could rewrite a
    canonical TAG and had no way to change the same text everywhere else it
    appears — in a link, in a mailto, in a bit of schema markup, in a phone
    number's page. site_api.plan_replace does that now, and this is the recipe
    that spots when it is needed.

    The right domain is not guessed: it is the one his site is served from,
    which is the one saved as SITE_URL. And it is only ever proposed when the
    wrong one is actually ON the pages, with a count per file.
    """
    # NO KEYWORD LIST. The first version fired on the word "canonical", which
    # made it swallow every canonical ticket — including ones that name no
    # domain at all and belong to fix_canonical. The only honest trigger is
    # whether a domain that is NOT his is actually named in the ticket.
    text = (issue["detail"] or "")
    right = _host_of(ctx.get("env", {}).get("SITE_URL") or "")
    if not right:
        return None
    # An instruction from him runs past every recipe, and an ADDRESS is the
    # email recipe's to answer — reading the domain out of it here as well put
    # the same request in front of him twice.
    looked = _EMAIL.sub(" ", text) if issue.get("kind") == "task" else text
    wrong = _wrong_domain_in(looked, right)
    # HIS NAME WITH ANOTHER ENDING, OR HIS WORDS FOR IT. "our website domain is
    # .ae while we have .com" names no domain in full, so the old trigger never
    # fired and his instruction was closed with no recipe. When the wrong one
    # is his own name (or he only describes it), the whole site is read and
    # every mention put right in one change — addresses on the dead domain
    # becoming the one he reads, not a made-up info@ on the right one.
    brand = right.split(".")[0].lower()
    if (wrong and brand and wrong.lower().split(".")[0] == brand) or \
            (not wrong and issue.get("kind") == "task" and _asks_about_domain(looked)):
        return _one_domain_plan(right)
    if not wrong:
        return None
    try:
        plan = site_api.plan_replace(wrong, right)
    except Exception as e:
        return _advice(f"Could not read the pages to count it: {e}",
                       ["Check the Admin API address and token on /setup."])
    if not plan.get("ok"):
        return _advice("Could not work out the change: "
                       + str(plan.get("error") or ""),
                       ["Check the Admin API address and token on /setup."])
    if not plan["times"]:
        return {"how": "already_right",
                "why": f"'{wrong}' is not on any page I can read any more — "
                       "this ticket has been overtaken."}
    where = ", ".join(f"{h['page']} ({h['times']}×)" for h in plan["pages"][:6])
    return {"how": "replace", "find": wrong, "replace": right,
            "pages": [h["page"] for h in plan["pages"]],
            "times": plan["times"],
            "why": (f"'{wrong}' appears {plan['times']} time(s) across "
                    f"{plan['files']} file(s) — {where}. That domain has no DNS "
                    f"record at all, so every link and address using it is "
                    f"broken. Your site is on {right}."),
            "undo": "python -m pipelines.site_api --undo <page>"}


def fix_dead_email(issue, ctx):
    """
    An email address on the site that cannot receive mail.

    info@worlddealtechnicalservices.ae is printed on his live website and the
    domain has no DNS at all, so every enquiry sent to it bounces or vanishes.
    He asked for it swapped for the working address and nothing happened.

    Same shape as the domain: the replacement comes from BUSINESS_EMAIL, never
    from the page, and it is only proposed when the dead one is really there.
    """
    text = (issue["detail"] or "").lower()
    if "mail" not in text and "@" not in text:
        return None
    # The address he reads. BUSINESS_EMAIL if he saved one; otherwise the one he
    # has given us more than once — never "save it on /setup" while we know it.
    right = (ctx.get("env", {}).get("BUSINESS_EMAIL") or "").strip() or site_api.right_email()
    if not right:
        return _advice(
            "There is an address on your site that cannot receive mail, and no "
            "working one saved for me to put in its place.",
            ["Save it on /setup (BUSINESS_EMAIL), then run the Fixer again."],
            needs="the address you actually read")
    named = [a for a in _EMAIL.findall(issue["detail"] or "")
             if a.lower() != right.lower()]
    wrong = named[0] if named else ""
    # "change the dummy mail on the website" names no dummy — often only his
    # real address, as what to put in its place. Reading the first address in
    # the sentence took HIS address for the wrong one and found nothing to do.
    # Named or not, what he means is every address on his own domains that is
    # not the one he reads: the whole site, one change.
    if issue.get("kind") == "task" and _asks_about_email(text) and \
            (not wrong or _his_domain(wrong, right)):
        return _one_email_plan(right)
    if not wrong:
        return None
    try:
        plan = site_api.plan_replace(wrong, right)
    except Exception as e:
        return _advice(f"Could not read the pages to count it: {e}", [])
    if not plan.get("ok"):
        return _advice("Could not work out the change: "
                       + str(plan.get("error") or ""), [])
    if not plan["times"]:
        return {"how": "already_right",
                "why": f"'{wrong}' is not on any page I can read any more."}
    return {"how": "replace", "find": wrong, "replace": right,
            "pages": [h["page"] for h in plan["pages"]],
            "times": plan["times"],
            "why": (f"'{wrong}' is on {plan['files']} page(s), {plan['times']} "
                    f"time(s). That domain has no mail record, so anyone who "
                    f"writes to it gets nothing back — the enquiry is simply "
                    f"lost. Yours is {right}."),
            "undo": "python -m pipelines.site_api --undo <page>"}


_CHANGE = re.compile(r"\b(change|replace|fix|correct|update|swap|wrong|dummy|fake|"
                     r"incorrect|old|remove|instead|while we have)\b", re.I)


def _asks_about_domain(text):
    """His words for 'the domain on my site is wrong', with or without a full name —
    or for the www: "every page must use the address with www"."""
    t = text or ""
    return bool(re.search(r"(?<![a-z0-9])\.(ae|com)\b|\bdomains?\b|(?<![./])\bwww\b(?!\.)"
                          r"|\bone address\b|\bcanonical", t, re.I)
                and (_CHANGE.search(t) or re.search(r"\b(use|must|should|make)\b", t, re.I)))


def _asks_about_email(text):
    t = text or ""
    return bool(re.search(r"\be-?mails?\b|\bmail\b", t, re.I) and _CHANGE.search(t))


def _his_domain(addr, right):
    """Is this address on one of his own domains, whatever the ending?"""
    brand = (right.rsplit("@", 1)[-1].split(".")[0] if "@" in right else "").lower()
    dom = addr.rsplit("@", 1)[-1].lower()
    return bool(brand) and dom.split(".")[0] == brand


def _one_domain_plan(right):
    """Every mention of his name with another ending, made `right` — one change."""
    try:
        plan = site_api.plan_domains()
    except Exception as e:
        return _advice(f"Could not read the pages to count it: {e}",
                       ["Check the Admin API address and token on /setup."])
    if not plan.get("ok"):
        return _advice("Could not work out the change: " + str(plan.get("error") or ""),
                       ["Check the Admin API address and token on /setup."])
    if not plan["times"]:
        return {"how": "already_right",
                "why": f"Your pages already say {right} everywhere I can read."}
    where = ", ".join(f"{h['page']} ({sum(h['wrong'].values())}×)"
                      for h in plan["pages"][:6])
    return {"how": "call", "call": "use_one_domain",
            "pages": [h["page"] for h in plan["pages"]][:12], "times": plan["times"],
            "why": (f"{', '.join(sorted(plan['wrong']))} appears {plan['times']} time(s) "
                    f"across {len(plan['pages'])} file(s) — {where}. That domain has no "
                    f"DNS record, so every link and address using it points at "
                    f"nothing. Your site is on {right}; addresses on the old domain "
                    f"become {site_api.right_email()}."),
            "undo": "python -m pipelines.site_api --undo --page <page>"}


def _one_email_plan(right):
    """Every address on his own domains that is not `right`, made `right` — one change."""
    try:
        plan = site_api.plan_emails()
    except Exception as e:
        return _advice(f"Could not read the pages to count it: {e}",
                       ["Check the Admin API address and token on /setup."])
    if not plan.get("ok"):
        return _advice("Could not work out the change: " + str(plan.get("error") or ""),
                       ["Check the Admin API address and token on /setup."])
    if not plan["times"]:
        return {"how": "already_right",
                "why": f"Every address on your website I can read is already {right}."}
    return {"how": "call", "call": "use_one_email",
            "pages": [h["page"] for h in plan["pages"]][:12], "times": plan["times"],
            "why": (f"{', '.join(sorted(plan['wrong']))} is on {len(plan['pages'])} "
                    f"page(s), {plan['times']} time(s). Anyone who writes to it may "
                    f"never reach you. Yours is {right}, so every one of them "
                    f"becomes that."),
            "undo": "python -m pipelines.site_api --undo --page <page>"}


_HOST = re.compile(r"https?://([A-Za-z0-9.\-]+)")
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def _host_of(url):
    m = _HOST.match((url or "").strip())
    if not m:
        return ""
    h = m.group(1)
    return h[4:] if h.lower().startswith("www.") else h


def _wrong_domain_in(text, right):
    """A domain mentioned in the ticket that is not the site's own."""
    bare = right.lower()
    for host in set(_HOST.findall(text) + re.findall(
            r"\b([a-z0-9\-]+\.(?:ae|com|net|org|co\.ae))\b", text.lower())):
        h = host[4:] if host.lower().startswith("www.") else host
        if h.lower() != bare and "." in h:
            return h
    return ""


def _dead_email_in(text):
    for a in _EMAIL.findall(text or ""):
        return a
    return ""


def fix_title(issue, ctx):
    """
    A page with no <title>.

    A real fix and NOT a patch, on purpose. What the title should say is a
    claim about the business — which service, which emirate, in what order —
    and the Fixer inventing one is how a page ends up titled 'Home | Home'.
    So it drafts a suggestion from what the cabinet actually knows and asks
    him to approve the words.
    """
    text = (issue["detail"] or "").lower()
    if issue["kind"] != "bug" or "title" not in text:
        return None
    suggestion = (ctx.get("business_name") or "").strip()
    trade = "Glass & Aluminium"
    where = (ctx.get("emirate") or "").strip()
    draft = " | ".join([b for b in
                        [suggestion or None, trade, where or None] if b])
    return _advice(
        "The page has no title, so search results and the browser tab show the "
        "URL. What it should SAY is a claim about your business, not something "
        "I should invent for you.",
        [f'Suggested: <title>{draft}</title>' if draft else
         "Suggested shape: <title>Business name | Glass &amp; Aluminium | Emirate</title>",
         "Approve the words and the Releaser puts them in, or send me different ones."],
        needs="your say-so on the wording")


def fix_https(issue, ctx):
    """
    Not on HTTPS.

    Nothing in a page fixes this — it is a certificate on the server and a
    redirect in front of it. Pretending otherwise would queue a change that
    cannot work and waste his time deciding on it.
    """
    if issue["kind"] != "ssl":
        return None
    return _advice(
        "Browsers put 'Not secure' next to your address, which costs enquiries "
        "before anyone reads a word. This is not something in a page — it is a "
        "certificate on the hosting.",
        ["In your hosting panel, turn on the AutoSSL / Let's Encrypt certificate "
         "for the domain (it is normally one button and costs nothing).",
         "Then set the site to send every http:// request on to https://.",
         "Tell me when it is on and IT will re-check the same morning."],
        needs="a change in your hosting panel, not in the site's files")


def fix_speed(issue, ctx):
    """
    A slow page.

    Speed has many causes and this agency has measured exactly one number. A
    recipe here would be a guess dressed as a patch, so it is advice ordered by
    what is usually true of a small PHP site.
    """
    if issue["kind"] != "speed":
        return None
    ms = re.search(r"(\d{3,6})\s*ms", issue["detail"] or "")
    took = f"{ms.group(1)}ms" if ms else "too long"
    return _advice(
        f"The page took {took}. On UAE mobile that is somebody going back to "
        "the search results. I can measure it but I cannot tell you WHY from "
        "one number, so this is what to check, in the order that usually pays.",
        ["Photos of finished jobs are almost always it: anything over about "
         "300KB wants resizing to 1600px wide and saving as WebP.",
         "Turn on caching and gzip in the hosting panel if they are off.",
         "If it is still slow with small images, the hosting itself is the "
         "problem and no change to the site will fix it."],
        needs="a look at the page's images, which I cannot resize from here")


def fix_enquiry_route(issue, ctx):
    """
    A missing WhatsApp link, click-to-call or contact form.

    The most valuable ticket on the bench and the one the Fixer is least
    entitled to patch alone: the number it would write in is a number customers
    ring. Getting it from .env is not the same as him confirming it, and a
    wrong number is worse than a missing link because it looks like it works.

    So the snippet is written out in full, with his own number already in it,
    and it waits for a yes.
    """
    if issue["kind"] != "form_dead":
        return None
    text = (issue["detail"] or "").lower()
    phone = (ctx.get("phone") or "").strip()
    digits = re.sub(r"[^\d]", "", phone)
    if "whatsapp" in text:
        if not digits:
            return _advice(
                "There is no WhatsApp link on the site, and no number saved for "
                "me to put in one. For this trade that is the main way people "
                "get in touch.",
                ["Save your WhatsApp number on /setup (BUSINESS_PHONE).",
                 "Run the Fixer again and it becomes a one-line change."],
                needs="your WhatsApp number")
        snippet = (f'<a href="https://wa.me/{digits}" '
                   f'aria-label="WhatsApp us">WhatsApp us</a>')
        return _advice(
            "There is no WhatsApp link anywhere on the site. For this trade "
            "that is the main way an enquiry arrives.",
            [f"The exact link, with your number already in it: {snippet}",
             "It wants to sit in the header, so it is on every page.",
             "Say yes and the Releaser puts it in; say the number is wrong and "
             "I will not touch it."],
            needs="a yes, because a wrong number looks like it works")
    if "call" in text or "tel:" in text:
        if not digits:
            return _advice(
                "Nothing on the site is tappable as a phone call, and no number "
                "is saved for me to use.",
                ["Save your number on /setup (BUSINESS_PHONE), then run the "
                 "Fixer again."],
                needs="your phone number")
        return _advice(
            "Your number is on the page as text, so on a phone it cannot be "
            "tapped — the visitor has to memorise it and switch apps.",
            [f'Wrap it: <a href="tel:+{digits}">{phone}</a>',
             "Same number, one tap instead of five."],
            needs="a yes")
    return _advice(
        "There is no way to make an enquiry on this page — no form, no email "
        "link, nothing to tap. Everything the site earns in rankings is being "
        "dropped at the last step.",
        ["Decide the one route you want (WhatsApp is usually right for this "
         "trade) and I will write it.",
         "A form is worth having as well, but second — it is slower for the "
         "customer and it needs somewhere for the mail to go."],
        needs="which route you want")


def fix_unreachable(issue, ctx):
    """The site did not answer, or answered 500. Nothing in a page fixes that."""
    text = (issue["detail"] or "").lower()
    if issue["kind"] != "bug":
        return None
    if not ("did not respond" in text or "server error" in text
            or re.search(r"returned 5\d\d", text)):
        return None
    return _advice(
        "The site did not answer when IT looked. Nothing I can change in a page "
        "fixes a server that is not answering, and every hour of this is enquiries "
        "that never happened.",
        ["Open the site yourself — if it loads for you, it may have been a blip "
         "and IT will confirm on the next check.",
         "If it does not load, ring the hosting company. This is theirs.",
         "Ask them for the error log for the last hour; that is what says why."],
        needs="the hosting company, urgently")


# Order matters: the first recipe that recognises a ticket owns it. The narrow
# ones come before the broad ones, or 'bug + the word title' would be swallowed
# by anything matching every bug.
def fix_seo_finding(issue, ctx):
    """
    A ticket from the SEO technical audit (pipelines/seo_audit.py).

    FIRST IN THE LIST, ON PURPOSE. The recipes below match on words in the
    ticket, and fix_canonical does not look at the kind at all. An audit
    finding such as "no canonical declared" would have reached it, found every
    canonical pointing at the right hostname, and closed the ticket as
    "overtaken" — a real finding silently binned by the wrong recipe. Kind
    'seo' stops here.

    The audit already wrote the action and the acceptance check; this hands
    them on as the plan rather than inventing a second opinion. Wording — a
    title, a description, alt text for his photographs — is a claim about his
    business, so it goes to him rather than being made up here.
    """
    if issue.get("kind") != "seo":
        return None
    text = issue.get("detail") or ""
    m = re.search(r"\[seo-audit:([a-z0-9_]+)\]", text)
    code = m.group(1) if m else ""

    def part(label, nxt):
        got = re.search(re.escape(label) + r"\s*(.*?)\s*(?:" + nxt + r"|$)", text, re.S)
        return got.group(1).strip() if got else ""

    why = part("Why it matters:", r"Do:")
    do = part("Do:", r"Fixed when:")
    done_when = part("Fixed when:", r"\[")
    steps = [do] if do else ["See the ticket."]
    needs = "your say-so on the wording"

    if code == "broken_internal_link" and re.search(
            r"/(sharer/sharer\.php|sharing/share-offsite|intent/tweet|pin/create)|/\?text=",
            text):
        # A SHARE BUTTON POINTING AT HIS OWN SITE. Each network's share address
        # has a path nothing else has, so which network it was is not a guess —
        # see site_api.SHARE_HOMES. A fix, not advice.
        try:
            sb = site_api.plan_share_buttons()
        except Exception as e:
            sb = {"ok": False, "error": str(e)}
        if sb.get("ok") and sb.get("times"):
            return {"how": "call", "call": "repair_share_buttons",
                    "pages": [p["page"] for p in sb["pages"]][:12], "times": sb["times"],
                    "why": (f"The share buttons on your posts ({sb['times']} of them) open "
                            "a page on your own site that does not exist, instead of "
                            "Facebook, WhatsApp and LinkedIn. This puts each network's "
                            "address back."),
                    "undo": "python -m pipelines.site_api --undo --page <page>"}
        if sb.get("ok"):
            return {"how": "already_right",
                    "why": "the share buttons already point at Facebook, WhatsApp and LinkedIn"}

    if code == "two_hosts_serve_the_site":
        # HIS SAVED ADDRESS ALREADY DECIDES IT. "this is your decision" left it
        # with him for weeks while the canonicals named the copy WITHOUT www,
        # his site and his Search Console are on www, and Google listed his
        # pages as not indexed. The address he saved is the one he chose: the
        # canonicals and links are made to name it — a change he approves —
        # and only the redirect, which lives in .htaccess, stays his to ask
        # his host for.
        try:
            from urllib.parse import urlparse as _up
            site_host = _up((ctx.get("env", {}).get("SITE_URL") or "").strip()).netloc.lower()
        except Exception:
            site_host = ""
        if site_host:
            plan = _one_domain_plan(site_host[4:] if site_host.startswith("www.") else site_host)
            if plan.get("how") == "call":
                other = (site_host[4:] if site_host.startswith("www.") else "www." + site_host)
                plan["why"] = (
                    f"Your pages answer at two addresses — {site_host} and {other} — and "
                    f"their canonical tags name {other}, while your saved address and "
                    f"Search Console are {site_host}. Google treats the pages you are "
                    f"looking at as a second copy, which is why they show as not indexed. "
                    f"This makes every canonical and link name {site_host}. "
                    + plan["why"] + f" Then ask your host (Hostinger support) to redirect "
                    f"{other} to https://{site_host} permanently (301); that lives in "
                    ".htaccess, which the agency never touches.")
                return plan
        # A redirect lives in the server's .htaccess, and .htaccess is on the
        # agency endpoint's never-touch list by design — so this is his to
        # paste, and here is exactly what to paste.
        hosts = re.findall(r"https://([a-z0-9.-]+)/", text)
        a, b = (hosts + ["", ""])[:2]
        bare = a[4:] if a.startswith("www.") else a
        steps = [
            f"Decide which address to keep: {a or 'www'} or {b or 'non-www'}. Your "
            "canonicals name one and your saved address the other.",
            "To keep the address WITHOUT www, add to the top of .htaccess in your "
            "website folder:\n"
            "    RewriteEngine On\n"
            f"    RewriteCond %{{HTTP_HOST}} ^www\\.{re.escape(bare)}$ [NC]\n"
            f"    RewriteRule ^(.*)$ https://{bare}/$1 [R=301,L]",
            "To keep the address WITH www instead:\n"
            "    RewriteEngine On\n"
            f"    RewriteCond %{{HTTP_HOST}} ^{re.escape(bare)}$ [NC]\n"
            f"    RewriteRule ^(.*)$ https://www.{bare}/$1 [R=301,L]\n"
            "  and then the canonicals must be changed to www to match.",
            "Keep a copy of .htaccess before editing; one wrong line takes the whole "
            "site down, and putting the copy back undoes it.",
        ]
        needs = ("you — .htaccess is deliberately out of the agency's reach, and "
                 "which address to keep is your decision")
    elif code in ("image_dimensions_review", "image_loading_conflict",
                  "jsonld_duplicate_block", "multiple_titles",
                  "multiple_meta_descriptions", "multiple_canonicals",
                  "redirect_chain", "broken_internal_link"):
        needs = "somebody editing the page's HTML — no wording to decide"

    if done_when:
        steps.append(f"It counts as fixed when: {done_when} The next SEO audit "
                     "checks this itself and closes the ticket, or reopens it if "
                     "the change did not take.")
    return _advice(why or "The SEO audit found this on your site.", steps, needs=needs)


def _waiting(kind) -> bool:
    """A change of this kind is already waiting for his yes (or approved and queued)."""
    try:
        with sqlite3.connect(board.DB, timeout=30) as c:
            return bool(c.execute("SELECT 1 FROM site_queue WHERE kind=? AND status IN"
                                  " ('pending','approved') LIMIT 1", (kind,)).fetchone())
    except sqlite3.Error:
        return False


def fix_from_his_words(issue, ctx):
    """
    Two things he can now simply ask IT for, by name: the share buttons and the
    sitemap. Instructions only — a filed ticket has its own recipes. If the same
    change is already waiting for his yes it is not queued twice.
    """
    if issue.get("kind") != "task":
        return None
    t = (issue.get("detail") or "").lower()
    if re.search(r"\bshare\s*buttons?\b|\bsharing buttons?\b", t):
        if _waiting("repair_share_buttons"):
            return {"how": "already_right",
                    "why": "the share-button repair is already waiting for your yes"}
        try:
            sb = site_api.plan_share_buttons()
        except Exception as e:
            return _advice(f"Could not read the pages: {e}", [])
        if sb.get("ok") and sb.get("times"):
            return {"how": "call", "call": "repair_share_buttons",
                    "pages": [p["page"] for p in sb["pages"]][:12], "times": sb["times"],
                    "why": (f"{sb['times']} share button(s) on your posts open a page on your "
                            "own site instead of Facebook, WhatsApp and LinkedIn. This puts "
                            "each network back."),
                    "undo": "python -m pipelines.site_api --undo --page <page>"}
        if sb.get("ok"):
            return {"how": "already_right",
                    "why": "the share buttons already go to Facebook, WhatsApp and LinkedIn"}
    return None


def fix_sitemap_asked(issue, ctx):
    """'Put the sitemap on the website' — its own recipe, so one sentence asking
    for the share buttons AND the sitemap gets both (a recipe gives one plan)."""
    if issue.get("kind") != "task":
        return None
    t = (issue.get("detail") or "").lower()
    if re.search(r"\bsite\s*-?\s*map\b", t):
        if _waiting("publish_sitemap"):
            return {"how": "already_right",
                    "why": "the sitemap is already waiting for your yes"}
        from . import sitemap
        return {"how": "call", "call": "publish_sitemap", "pages": [sitemap.PAGE],
                "why": (f"Puts {sitemap.live_url()} on your website listing every page "
                        "Google should know about, and hands it to Search Console. It is "
                        "kept up to date as posts go up and come down."),
                "undo": f"python -m pipelines.site_api --undo --page {sitemap.PAGE}"}
    return None


RECIPES = (
    # Before everything: an audit ticket must never reach a recipe that only
    # matches on its wording. See fix_seo_finding.
    fix_seo_finding,
    fix_unreachable,
    # Before fix_canonical: a wrong domain in a canonical tag is a wrong domain
    # EVERYWHERE, and rewriting only the canonical leaves it in every link and
    # mailto on the page. He asked for the domain changed, not the tag.
    fix_wrong_domain,
    fix_dead_email,
    fix_from_his_words,
    fix_sitemap_asked,
    fix_viewport,
    fix_canonical,
    fix_placeholders,
    fix_title,
    fix_https,
    fix_speed,
    fix_enquiry_route,
)


def plans_for(issue, ctx) -> list:
    """
    Every change this ticket calls for. A list, because one of them is not
    always enough.

    A TICKET IS ONE PROBLEM. AN INSTRUCTION IS NOT.

    This used to return the first recipe that matched and stop. That is right
    for a ticket the Watcher filed — "no viewport tag" is one problem with one
    fix. It is wrong for something HE typed: he asked, in one sentence, for the
    wrong domain replaced AND the dummy email replaced, the first recipe
    matched, and the second half of what he asked for was dropped on the floor.
    He then asked what the agency had actually done.

    So a filed ticket still stops at the first match, and an instruction from
    him is run past every recipe and gets every change it calls for.
    """
    everything = issue.get("kind") == "task"
    out = []
    for recipe in RECIPES:
        try:
            got = recipe(issue, ctx)
        except Exception as e:                                # pragma: no cover
            return [_advice(f"The recipe for this ticket errored: {e}",
                            ["This is a bug in the Fixer, not in your site. "
                             "It has been filed."])]
        if not got:
            continue
        got["recipe"] = recipe.__name__
        out.append(got)
        if not everything:
            break
    return out


def plan_for(issue, ctx):
    """The first plan for this ticket, or None. Kept for callers that want one."""
    got = plans_for(issue, ctx)
    return got[0] if got else None


# ---------------------------------------------------------------------------
# THE BENCH
# ---------------------------------------------------------------------------

def open_issues(con, limit=MAX_PER_PASS, only=None):
    """
    What is waiting, worst first, oldest first within a severity.

    'fixing' is included alongside 'open' deliberately. A ticket this desk
    picked up and then died on would otherwise be invisible to the only desk
    that can work it — the same shape as the nineteen-day request bug, one
    table over. A ticket already in_review has a change waiting on the owner
    and must NOT be worked twice.
    """
    q = ("SELECT id, kind, severity, detail, url, status, found_by, found_at"
         "  FROM site_issues WHERE status IN ('open','fixing')")
    args = []
    if only:
        q += " AND id=?"
        args.append(int(only))
    rows = [dict(r) for r in con.execute(q, args)]
    rows.sort(key=lambda r: (SEVERITY_ORDER.get(r["severity"], 9),
                             r["found_at"] or 0, r["id"]))
    return rows[:limit] if limit else rows


def _context():
    """Everything a recipe might need, read once rather than per ticket."""
    # THE ENVIRONMENT WINS, THEN .env — the same order the rest of the program
    # uses. Reading .env alone was already the bug that made SMTP look
    # unconfigured when it was configured, and here it made the Fixer say "no
    # working address saved" while BUSINESS_EMAIL was sitting in the
    # environment it was handed.
    env = {}
    try:
        from providers import integrations
        env = dict(integrations.read_env())
    except Exception:
        env = {}
    import os as _os
    for k, v in _os.environ.items():
        if v and (k.startswith("BUSINESS_") or k in ("SITE_URL", "AGENCY_NAME")):
            env[k] = v
    ctx = {"env": env,
           "business_name": env.get("BUSINESS_NAME") or env.get("AGENCY_NAME") or "",
           "phone": env.get("BUSINESS_PHONE") or env.get("BUSINESS_WHATSAPP") or "",
           "emirate": env.get("BUSINESS_EMIRATE") or env.get("BUSINESS_CITY") or "",
           "head_page": ""}
    # Which file's <head> the agency may write to. Worked out by reading the
    # site, not guessed — see site_api.head_holder, which learned the hard way
    # that any complete page contains a </head> and default.php is not the
    # header include.
    try:
        if not site_api.why_not_ready():
            ctx["head_page"] = site_api.head_holder(site_api.live_pages()) or ""
    except Exception:
        ctx["head_page"] = ""
    return ctx


def critic_check(worked, unhandled):
    """
    The Fixer's failure mode is a confident patch nobody asked for.

    Two things get rejected: a patch with no actual content, and a pass that
    claims to have handled everything while quietly having no recipe for most
    of it. The second one is the dangerous one — it reads as a working desk.
    """
    reasons = []
    for w in worked:
        p = w["plan"]
        if p["how"] == "patch" and not (p.get("html") or "").strip():
            reasons.append(f"issue {w['issue']['id']}: a patch with nothing in it")
        if p["how"] == "advice" and not p.get("steps"):
            reasons.append(f"issue {w['issue']['id']}: advice that says nothing to do")
    if worked and unhandled and len(unhandled) > 2 * len(worked):
        reasons.append(
            f"{len(unhandled)} of {len(worked) + len(unhandled)} tickets have no "
            "recipe — reporting that as a working bench would be a lie")
    return (not reasons, reasons)


# ---------------------------------------------------------------------------
# THE PASS
# ---------------------------------------------------------------------------

def run(agent=AGENT, limit=MAX_PER_PASS, only=None, dry_run=False):
    with board.agent_run(agent) as run_row:
        out = _work(agent, limit, only, dry_run)
        run_row.note(out.get("summary") or out.get("error") or "nothing on the bench")
        return out


def _work(agent, limit, only, dry_run):
    # Anything stuck in our own name first — the lesson from Content's nineteen
    # days. A desk that cannot see its own abandoned work reports an empty
    # bench while the Overseer reports it as ignoring the board.
    try:
        board.release_dead_request_claims()
    except Exception:
        pass

    con = sqlite3.connect(board.DB)
    con.row_factory = sqlite3.Row
    try:
        # Claim the board task if the MD or the Watcher posted one for this
        # desk. Not required to work — the tickets are the real bench — but
        # claiming it is what clears the Overseer's 'nobody is picking this up'
        # fault, and leaving that standing while actually doing the work would
        # be the dashboard lying in the opposite direction.
        # NOT THE AGENCY'S OWN FAULTS. "Fix: seo-strategist crashed: …" is a
        # job for IT's self-repair desk (pipelines/it_selfheal), which knows a
        # dropped connection is a passing thing and clears it. This desk fixes
        # the WEBSITE; it ran first in the lane, took the crash ticket, found
        # no website recipe for it, and set off "1 ticket I have no fix for"
        # and a round of escalations about one dropped connection.
        agency_jobs = [r[0] for r in con.execute(
            "SELECT id FROM tasks WHERE sector=? AND status='open'"
            " AND about LIKE 'faults:%'", (SECTOR,))]
        task = board.next_open_task(SECTOR, agent, skip=agency_jobs)

        # WHAT HE ACTUALLY ASKED FOR.
        #
        # This claimed the board task and then never read a word of it. Zero
        # references to its title or its detail: it took the job, worked its
        # own ticket list, and closed the job as done. So "change the .ae
        # domain to .com on the pages" and "replace the dummy email" were
        # typed, claimed, and silently ignored — twice, and he was right to
        # ask what the agency had actually done.
        #
        # A claimed task is now a ticket like any other. It goes through the
        # same recipes, so an instruction that matches one becomes an exact
        # change he can approve, and one that matches none is reported as
        # having no recipe instead of vanishing.
        issues = open_issues(con, limit=limit, only=only)
        if task and not only:
            issues.insert(0, {
                "id": 0, "kind": "task", "severity": "high",
                "detail": f"{task.get('title', '')}. {task.get('detail', '') or ''}",
                "url": "", "status": "open", "found_by": task.get("created_by", "MD"),
                "found_at": task.get("created_at") or _now(), "task_id": task["id"]})
        if not issues:
            total = con.execute("SELECT count(*) FROM site_issues").fetchone()[0]
            waiting = con.execute("SELECT count(*) FROM site_issues"
                                  " WHERE status='in_review'").fetchone()[0]
            why = ("nothing is filed against the site yet — run the Watcher first:"
                   "  python -m pipelines.it_health" if not total else
                   f"every ticket is already dealt with ({waiting} waiting on you"
                   " to approve)" if waiting else
                   "every ticket is closed")
            if task:
                board.finish_task(task["id"], agent,
                                  f"Nothing to patch — {why}", status="done")
            return {"ok": False, "agent": agent, "error": f"Nothing to patch — {why}.",
                    "tickets_total": total, "waiting_on_you": waiting,
                    "claimed_task": (task or {}).get("id")}

        ctx = _context()
        worked, unhandled = [], []
        for issue in issues:
            got = plans_for(issue, ctx)
            if not got:
                unhandled.append(issue)
                continue
            for plan in got:
                worked.append({"issue": issue, "plan": plan})

        ok, reasons = critic_check(worked, unhandled)
        if not ok:
            if task:
                board.finish_task(task["id"], agent,
                                  "Critic rejected: " + "; ".join(reasons),
                                  status="blocked")
            return {"ok": False, "agent": agent, "critic": "rejected",
                    "critic_reasons": reasons,
                    "error": "The Critic stopped this pass: " + "; ".join(reasons),
                    "claimed_task": (task or {}).get("id")}

        queued, overtaken, blocked_by_lock = [], [], []
        if not dry_run:
            for w in worked:
                r = _hand_on(agent, w["issue"], w["plan"], con)
                if r["state"] == "queued":
                    queued.append(r)
                elif r["state"] == "overtaken":
                    overtaken.append(r)
                else:
                    blocked_by_lock.append(r)

        result = {
            "ok": True, "agent": agent, "dry_run": bool(dry_run),
            "claimed_task": (task or {}).get("id"),
            "critic": "passed",
            "tickets_read": len(issues),
            "patched": sum(1 for w in worked
                           if w["plan"]["how"] in ("patch", "call", "replace")),
            "advised": sum(1 for w in worked if w["plan"]["how"] == "advice"),
            "overtaken": len(overtaken),
            "no_recipe": len(unhandled),
            "queued_for_you": len(queued),
            "held_by_someone_else": len(blocked_by_lock),
            "plans": [{"issue": w["issue"]["id"], "severity": w["issue"]["severity"],
                       "how": w["plan"]["how"], "recipe": w["plan"]["recipe"],
                       "why": w["plan"]["why"]} for w in worked],
            "unhandled": [{"issue": i["id"], "kind": i["kind"],
                           "detail": (i["detail"] or "")[:120]} for i in unhandled],
        }

        if unhandled and not dry_run:
            _say_what_it_could_not_do(agent, unhandled)

        if task and not dry_run:
            board.finish_task(task["id"], agent, {
                "tickets_read": len(issues),
                "queued_for_owner": len(queued),
                "no_recipe": len(unhandled)}, status="done")

        if not dry_run:
            result["decision_id"] = board.log_decision(
                "it-fix",
                reasoning=(f"Worked {len(issues)} open ticket(s): "
                           f"{result['patched']} had an exact fix, "
                           f"{result['advised']} needed the owner to decide, "
                           f"{len(unhandled)} had no recipe."),
                expectation=("The tickets the Watcher files stop piling up, and "
                             "each one reaches the owner as a decision he can "
                             "answer rather than a problem he has to diagnose."),
                confidence=0.7,
                evidence={"site_issue_ids": [w["issue"]["id"] for w in worked],
                          "site_queue_ids": [q["change_id"] for q in queued]})

        result["summary"] = (
            f"{len(issues)} ticket(s) worked — {result['patched']} with an exact fix, "
            f"{result['advised']} needing your call, {len(queued)} queued for you"
            + (f", {len(unhandled)} I have no recipe for" if unhandled else "")
            + (" (dry run — nothing queued)" if dry_run else ""))
        return result
    finally:
        con.close()


def _hand_on(agent, issue, plan, con):
    """
    Queue one change and move the ticket on. Nothing here writes to the site.

    THE LOCK. Two agents editing the same page is the thing coordination exists
    to stop, and the Fixer holds the page it is patching for as long as it takes
    to write the change down. Short lease on purpose: this is a database write,
    not an edit session, and a long one would block the Releaser behind a desk
    that has already finished.
    """
    if plan["how"] == "already_right":
        if issue["id"]:
            with sqlite3.connect(board.DB, timeout=30) as c:
                c.execute("UPDATE site_issues SET status='closed' WHERE id=?",
                          (issue["id"],))
        return {"state": "overtaken", "issue": issue["id"], "why": plan["why"]}

    page = plan.get("page") or (plan.get("pages") or [""])[0] or "site"
    resource = f"page:{page}"
    if not board.acquire_lock(resource, agent, wait=False, ttl=120):
        return {"state": "held", "issue": issue["id"], "resource": resource}
    try:
        detail = _for_the_owner(issue, plan)
        change_id = board.queue_site_change(agent, "fix_" + (issue["kind"] or "site"),
                                            detail)
        if issue["id"]:
            with sqlite3.connect(board.DB, timeout=30) as c:
                c.execute("UPDATE site_issues SET status='in_review' WHERE id=?",
                          (issue["id"],))
        return {"state": "queued", "issue": issue["id"], "change_id": change_id,
                "how": plan["how"]}
    finally:
        board.release_lock(resource, agent)


def _for_the_owner(issue, plan):
    """
    What he actually reads in the approval queue.

    Written as a sentence he can answer, not a diff. He is a glazier with ten
    years in the trade and no interest in HTML, so the shape is: what is wrong,
    what I would do, and what happens if you say no. The literal change is
    carried underneath for the Releaser, which is the only reader that needs it.
    """
    head = {"patch": "I can fix this exactly",
            "call": "I can fix this exactly",
            "replace": "I can fix this exactly",
            "advice": "This one needs you"}.get(plan["how"], "")
    body = {
        "issue_id": issue["id"] or None,
        "from_your_instruction": issue.get("kind") == "task",
        "severity": issue["severity"],
        "found": (issue["detail"] or "")[:400],
        "url": issue["url"] or "",
        "verdict": head,
        "why": plan["why"],
    }
    if plan["how"] == "patch":
        body["change"] = {"page": plan["page"], "block": plan["block_id"],
                          "html": plan["html"]}
        body["undo"] = plan["undo"]
    elif plan["how"] == "replace":
        body["change"] = {"replace": plan["find"], "with": plan["replace"],
                          "pages": plan.get("pages") or [],
                          "times": plan.get("times") or 0}
        body["undo"] = plan.get("undo") or ""
    elif plan["how"] == "call":
        body["change"] = {"run": plan["call"], "pages": plan.get("pages") or []}
        body["undo"] = plan.get("undo") or ""
    else:
        body["what_to_do"] = plan["steps"]
        body["needs"] = plan.get("needs") or ""
    return body


def _say_what_it_could_not_do(agent, unhandled):
    """
    Tickets with no recipe go UP, not into a corner.

    The whole reason this desk was missing for so long is that nobody was told
    when work stopped moving. A Fixer that silently skips what it does not
    understand rebuilds that hole one level down, so it says so, once, naming
    the tickets.
    """
    lines = "\n".join(f"  #{i['id']} [{i['severity']}] {(i['detail'] or '')[:120]}"
                      for i in unhandled[:10])
    try:
        board.post_request(
            SECTOR, SECTOR,
            f"{len(unhandled)} ticket(s) I have no fix for",
            "These are open against the site and I have no recipe that "
            "recognises them, so nothing has been queued for them:\n\n" + lines
            + "\n\nThey are not lost and they are not being worked. Either they "
              "need a person, or the Fixer needs a recipe for this shape of "
              "ticket — which is a job for IT, not a job for the owner.",
            urgency="normal")
    except Exception:
        pass                       # saying so must never stop the work


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _as_text(r):
    if not r.get("ok"):
        return "IT FIXER — " + (r.get("error") or "nothing to do")
    lines = ["IT FIXER — " + (r.get("summary") or "")]
    for p in r.get("plans") or []:
        mark = {"patch": "fix ", "call": "fix ", "replace": "fix ",
                "advice": "ask ", "already_right": "gone"}.get(p["how"], "    ")
        lines.append(f"  {mark} #{p['issue']:<4} [{p['severity']}] {p['why'][:100]}")
    for u in r.get("unhandled") or []:
        lines.append(f"  ??   #{u['issue']:<4} no recipe — {u['detail'][:80]}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="The IT Fixer: patch what the Watcher filed.")
    ap.add_argument("--list", action="store_true", help="what is waiting, change nothing")
    ap.add_argument("--dry-run", action="store_true", help="work it out, queue nothing")
    ap.add_argument("--issue", type=int, default=0, help="just this one ticket")
    ap.add_argument("--limit", type=int, default=MAX_PER_PASS)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    r = run(limit=a.limit, only=a.issue or None,
            dry_run=bool(a.dry_run or a.list))
    print(json.dumps(r, indent=2) if a.json else _as_text(r))
    return deskwork.exit_code(r)


if __name__ == "__main__":
    sys.exit(main())
