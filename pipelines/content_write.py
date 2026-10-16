"""
content_write.py — the Writer. Turns a brief into a page, and stops there.

    python -m pipelines.content_write              draft the next briefed page
    python -m pipelines.content_write --list       what is waiting to be written
    python -m pipelines.content_write --show 4     read one draft
    python -m pipelines.content_write --dry-run    write it, store nothing

WHY THIS FILE EXISTS

"how the articles will get published?"

They did not. The chain was built to the last link and stopped one short of his
website, exactly as the Releaser did before it was written. SEO found the gaps,
Content clustered them into briefs, the briefs went into content_queue with a
title, an angle and the phrases behind them — and nothing on this machine ever
turned a brief into words. `status` had a 'drafted' state that nothing could
ever reach.

THE FOUR STEPS, AND WHY THEY ARE FOUR DESKS AND NOT ONE

  1. THIS FILE writes the page. It never publishes.
  2. THE AUDITOR reads it — coordination/auditor.check_page. Handbook red line:
     nothing an agent wrote leaves the building unchecked. A page that fails is
     stored with the reasons and goes no further.
  3. THE OWNER approves it. A new page on his domain is not a green decision.
  4. THE RELEASER puts it there. IT is the only door to production, here as
     everywhere.

WHAT IT WILL NOT DO, AND THIS IS MOST OF THE FILE

IT DOES NOT WRITE WITHOUT A BRAIN. With no model routed to Content, this writes
nothing and says so. The alternative — a template with the keyword slotted in
four times — is the exact page every other glazier in Dubai already has, and
putting one on his domain would cost him rather than earn.

IT DOES NOT INVENT FACTS. The model is given the evidence and told, in the
system prompt and again in the Auditor, that it has everything it is going to
get. No years in business, no client names, no project counts, no prices, no
certifications. Every one of those is checkable by a customer and none of them
is ours to make up. The Auditor is the enforcement; the prompt is the courtesy.

IT DOES NOT SUPPLY PHOTOGRAPHS. The brief says real photographs of real
finished jobs, and it is right — that is the business's actual edge. So the
page ships with a marked, empty slot where his pictures go, and the Auditor
warns that the page is not finished until they are in. An agent going and
finding a stock photograph of somebody else's balustrade is worse than a gap.

IT DOES NOT MATCH HIS TEMPLATE BY GUESSING. It reads one of his own pages and
puts the new content in that page's frame, so it comes out looking like his
site. If no page of his can be read, it says so plainly in the approval rather
than shipping a page that looks foreign.

A BLOG POST IS NOT A PAGE. His posts live in his own blog backend and show
through blog-details.php in his theme; a post goes to the Posts room, and from
there into his blog's own table (pipelines/blog_backend) — never onto his site
as a file of its own.
"""
import argparse
import json
import re
import sqlite3
import sys
import time

from coordination import auditor, board, deskwork, uae
from . import post_quality, site_api

AGENT = "content-writer"

# Headroom left at the end of the step's slot, so the last page has somewhere
# to be written down before the day's stopwatch takes the process away.
LEAVE_SPARE = 75


def _deadline(now=None):
    """
    The wall-clock moment this pass must stop starting new pages, or None.

    Read from day_run's own budget for this step rather than guessed at, so
    changing the step timeout in one place changes this too. None when the
    budget cannot be read — better to behave exactly as before than to invent
    a limit and stop early for no reason.
    """
    try:
        from . import day_run
        slot = int(day_run.step_timeout_for("pipelines.content_write"))
    except Exception:                                        # pragma: no cover
        return None
    if slot <= LEAVE_SPARE:
        return None
    return (now if now is not None else time.time()) + (slot - LEAVE_SPARE)
SECTOR = "Content"

# One page per pass. Writing is the expensive step and a spree of six
# half-considered pages is the failure mode the MD's gate exists to stop — see
# the draft_allowance note in content_briefs.
MAX_PER_PASS = 1

# Room for the piece and no more. A model given eight thousand tokens writes
# eight thousand tokens — but a 1,200-word article in HTML, inside JSON, needs
# far more than the 2,200 this used to allow, and the pages came back at 160
# words partly because there was no room to write more.
WRITE_TOKENS = 4000
# A post planned by SEO runs to 1,200-2,000 words with a table and a FAQ; in
# HTML inside JSON that is well past 6,500 tokens, and a reply cut off half way
# is not JSON at all — the post is lost.
TOKENS = {"blog": 9000, "service_page": 5000}

# How long each kind is, and the least it may be before it is sent back to be
# made fuller. "the program is writing like 150 to 160 words of too generic
# posts" — the brief said "450-700 — short. Anything longer is padding", and a
# model told to be short and to state nothing it was not given writes 160.
LENGTH = {"blog": "1,000 to 1,400 words", "service_page": "600 to 900 words"}
FEWEST = {"blog": 850, "service_page": 450}

# What gives writing away as machine-made, and what no tradesman says. Found in
# a draft, it goes back once with the phrases named. Not a trick to fool a
# detector — these are the phrases that make a reader stop trusting a page.
AI_TELLS = (
    "delve", "in today's", "fast-paced", "whether you're", "whether you are",
    "look no further", "elevate", "seamless", "unlock", "robust", "comprehensive",
    "navigate the", "landscape", "testament to", "in conclusion", "to sum up",
    "it's important to note", "it is important to note", "it's worth noting",
    "game-changer", "game changer", "cutting-edge", "state-of-the-art", "nestled",
    "boasts", "in the realm of", "a myriad of", "plethora", "furthermore",
    "moreover", "embark", "tailored to your needs", "transform your space",
    "look and feel", "second to none", "one-stop", "top-notch", "unparalleled",
    "when it comes to", "rest assured", "we pride ourselves", "dive into",
    "ever-evolving", "in this article", "this guide will",
)

BRAIN = "content"

# Every piece the writer produces goes into his blog, never onto a page of its
# own. The page route is kept, off, for the day he asks for service pages.
EVERYTHING_IS_A_POST = True

# Where his own photographs go. A marker rather than an <img> pointing at
# nothing: the Auditor can see it, the office can list it, and a page that goes
# live with the marker still in it is obviously unfinished rather than subtly
# broken.
PHOTO_SLOT = "<!-- agency:photos — put 3 to 6 of your own finished-job photos here -->"


SYSTEM = """\
You write for the website of a glass and aluminium business in the UAE — glass \
work, aluminium work, glass and wooden doors, pergolas, fabrication and \
installation. The owner has __YEARS__ years in the trade and does the work \
himself. What you write goes on his own site, under his name, and customers \
read it before they ring him.

WRITE LIKE THE MAN WHO DOES THE WORK
British English. Write as the business, "we", the way a good tradesman talks a \
customer through a job over the phone: plain, specific, a little opinionated, \
never salesy. Vary the length of your sentences; some short, some longer when \
you are explaining how something is done. Open with the situation the reader \
is in, not a definition. One idea to a paragraph. Contractions are fine. No \
exclamation marks. No rhetorical questions as headings. Do not finish with a \
summary of what you already said.

USE WHAT THE TRADE KNOWS — THAT IS WHAT MAKES IT READ AS REAL
You may and should use accurate, general trade knowledge: types of glass \
(toughened/tempered, laminated, double-glazed units, low-E, tinted, frosted, \
back-painted), usual thicknesses and where each is used, aluminium profiles \
and finishes (powder coating, anodising, thermal break), fittings and systems \
(patch fittings, spigots, channel systems, frameless and framed), how a job \
runs (site visit and measuring, fabrication, fitting, silicone and finishing, \
snagging), what the UAE heat, sand, humidity and AC do to glass, seals and \
frames, what to check before choosing a contractor, what goes wrong with \
cheap work, and how to look after it afterwards. Explain the trade-offs a \
customer is actually weighing. That detail is what a generic page lacks.

WHAT YOU MUST NOT CLAIM ABOUT THE BUSINESS — checked after you, and a page \
that breaks one is thrown away:
  - No number of projects or clients, no client or building names, no \
    certificates, awards, ratings or percentages, no guarantees or warranty \
    terms. Years in the trade: only __YEARS__, if you mention it at all.
  - No price, rate, discount or offer — no AED amount, no price range, no \
    rate per metre or square foot, no "cheapest" or "best price". That is the \
    owner's call. A post about cost explains what the cost depends on and how \
    a quote is worked out, never the figure.
  - No code: no <script>, <style>, <form>, <iframe> or event attributes.
  - No placeholder of any kind.

PICTURES
You are given numbered pictures. Put the marker <!--image 1-->, <!--image 2--> \
and so on, on its own line, where each picture helps — after the paragraph it \
illustrates. Do not write <img> yourself. If no pictures are listed, use none.

WHAT IT MUST HAVE
  - One <h1> saying what it is.
  - Subheadings a reader can scan: an <h2> every few paragraphs, and <h3> under \
    them where natural. Never more than three paragraphs in a row without one.
  - The words that matter in <strong>: the main keyword the first time it \
    appears, and two or three key terms in each section — a material, a \
    thickness, a step, the thing to check. Words and short phrases, never \
    whole sentences.
  - Short paragraphs — two to four sentences.
  - The enquiry route high up and again at the end: the phone number and the \
    WhatsApp link you are given, as real links.
  - The area named, if you were given one.
  - Where it genuinely helps, a small <table> comparing options, or a <ul> \
    checklist — the kind of thing a customer screenshots.

Reply with JSON and nothing else:
{"title": "<the browser title, under 60 characters>",
 "meta": "<the search-result description, under 155 characters>",
 "h1": "<the heading>",
 "html": "<the body: h1, h2, h3, p, ul, ol, li, table, thead, tbody, tr, th, \
td, strong, em, a, blockquote, and the image markers>",
 "note": "<one line on what you led with and why>"}"""

# For any website that is not the owner's: the same rules, written for its own
# trade and country (coordination/profile.py). His keeps SYSTEM, word for word.
GENERIC_HEAD = """\
You write for the website of __BUSINESS__ — __WHAT__. What you write goes on \
the business's own site, under its name, and customers read it before they \
get in touch.

WRITE LIKE THE PEOPLE WHO DO THE WORK
__SPELLING__. Write as the business, "we", the way a good tradesperson talks a \
customer through a job: plain, specific, a little opinionated, never salesy. \
Vary the length of your sentences. Open with the situation the reader is in, \
not a definition. One idea to a paragraph. Contractions are fine. No \
exclamation marks. No rhetorical questions as headings. Do not finish with a \
summary of what you already said.

USE WHAT THE TRADE KNOWS — THAT IS WHAT MAKES IT READ AS REAL
You may and should use accurate, general knowledge of this trade: the \
materials and options, how a job runs from first visit to finish, what local \
conditions in __WHERE__ do to the work, what to check before choosing who to \
hire, what goes wrong with cheap work, and how to look after it afterwards. \
Explain the trade-offs a customer is actually weighing. That detail is what a \
generic page lacks.

"""


def system_prompt() -> str:
    """The writer's instructions for the open website's business."""
    from coordination import countries, profile
    years = str(_years())
    if profile.is_starter():
        return SYSTEM.replace("__YEARS__", years)
    b = profile.business()
    cur = (profile.current().get("currency") or "").strip()
    rest = SYSTEM[SYSTEM.index("WHAT YOU MUST NOT CLAIM"):]
    rest = rest.replace("no AED amount", f"no {cur} amount" if cur else "no money amount")
    return (GENERIC_HEAD
            .replace("__BUSINESS__", profile.describe())
            .replace("__WHAT__", b.get("description") or b.get("trade") or "its services")
            .replace("__WHERE__", profile.market_phrase())
            .replace("__SPELLING__", profile.spelling())
            + rest).replace("__YEARS__", years)


def _local_conditions() -> str:
    from coordination import countries, profile
    if profile.worldwide() or len(profile.markets()) > 1:
        return "the conditions where each customer is (" + profile.market_phrase() + ")"
    return ("UAE conditions" if profile.is_starter()
            else countries.in_a_sentence(profile.country()) + "'s conditions")


KIND_NOTES = {
    "blog": ("THIS IS A BLOG ARTICLE WRITTEN TO RANK, not a sales page. SEO has "
             "given you a keyword plan below; the piece is built on it. Title: the "
             "main keyword near the start, then what the reader gets, under 60 "
             "characters. Open by answering what the searcher wants in the first two "
             "or three sentences, with the main keyword in the first one. Then <h2> "
             "sections that follow what people searching this want to know — cost "
             "and what drives it, the types and which suits what, how the job is "
             "done, how to choose a contractor, looking after it in UAE conditions — "
             "each heading carrying a keyword where it reads naturally. Short "
             "paragraphs. One comparison <table>. A FAQ section answering the "
             "questions in the plan, each as an <h3>. End with how to get the job "
             "looked at. Teach them something they did not know."),
    "service_page": ("THIS IS A SERVICE PAGE. Lead with what the job involves "
                     "and how quickly he can come and measure. Then the options "
                     "and trade-offs, how the work is done, and what to expect."),
}


def _now():
    return int(time.time())


# ---------------------------------------------------------------------------
# THE EVIDENCE
# ---------------------------------------------------------------------------

def _settings():
    """
    Everything the writer is allowed to state as fact.

    Environment first, then .env — the same order as everywhere else, and the
    order this program has got wrong three separate times.
    """
    env = {}
    try:
        from providers import integrations
        env = dict(integrations.read_env())
    except Exception:
        env = {}
    import os
    for k, v in os.environ.items():
        if v and (k.startswith("BUSINESS_") or k in ("SITE_URL", "AGENCY_NAME")):
            env[k] = v
    phone = (env.get("BUSINESS_PHONE") or env.get("BUSINESS_WHATSAPP") or "").strip()
    return {
        "name": (env.get("BUSINESS_NAME") or env.get("AGENCY_NAME") or "").strip(),
        "phone": phone,
        "whatsapp": (env.get("BUSINESS_WHATSAPP") or phone).strip(),
        "email": (env.get("BUSINESS_EMAIL") or "").strip(),
        "site": (env.get("SITE_URL") or "").strip(),
        "areas": uae.served(),
    }


def _wa_link(number):
    digits = re.sub(r"\D", "", number or "")
    return f"https://wa.me/{digits}" if len(digits) >= 9 else ""


def next_brief(con, limit=MAX_PER_PASS, only_id=None) -> list:
    """
    Briefs that are finished and not yet written.

    'briefed' only. A row at 'blocked_on_seo' is done work waiting on somebody
    else's call, and writing it anyway would be Content overruling SEO on SEO's
    home turf — which the handbook forbids and which would waste the writing.
    """
    q = ("SELECT id, title, type, brief, status FROM content_queue"
         " WHERE status='briefed' AND (draft IS NULL OR draft='')")
    args = []
    if only_id:
        q = ("SELECT id, title, type, brief, status FROM content_queue"
             " WHERE id=?")
        args = [only_id]
    q += " ORDER BY id LIMIT ?"
    args.append(int(limit))
    out = []
    for r in con.execute(q, args):
        try:
            brief = json.loads(r[3] or "{}")
        except Exception:
            brief = {}
        out.append({"id": r[0], "title": r[1], "type": r[2],
                    "brief": brief if isinstance(brief, dict) else {},
                    "status": r[4]})
    return out


# ---------------------------------------------------------------------------
# THE PAGE
# ---------------------------------------------------------------------------

def _ask_the_writer(brief, facts, tokens=None, kind="service_page", images=(),
                    fix=None, previous=None, pack=None) -> dict:
    """
    The Content brain writes the body. Returns {ok, page|error, brain}.

    `fix` and `previous`: a second pass on a draft that came back too short,
    or with the phrases that give machine writing away, or missing something
    the Critic needs — told exactly what, with its own draft in front of it.
    """
    # "No brain means no page" is true of Content in a way it is not of the
    # sectors whose brains only word things — the page IS the model's output.
    # Content's own Claude call already steps down the models and over to the
    # backup account when it has to (providers/claude_team.py).
    try:
        from providers import think
    except Exception as e:                                    # pragma: no cover
        return {"ok": False, "error": f"the provider shelf did not load ({e})"}

    lines = [KIND_NOTES.get(kind, KIND_NOTES["service_page"])
             .replace("in UAE conditions", "in " + _local_conditions()), "",
             f"THE SUBJECT: {brief.get('title') or ''}",
             f"WHAT IT IS FOR: {brief.get('angle') or ''}"]
    if pack:
        from . import seo_keywords
        lines += seo_keywords.for_the_writer(pack, kind)
        lines.append("")
    else:
        lines.append(f"THE PHRASE IT IS BRIEFED FOR: {brief.get('primary_keyword') or ''}")
        sec = brief.get("secondary_keywords") or []
        if sec:
            lines.append("OTHER PHRASES PEOPLE SEARCH: " + ", ".join(sec[:10]))
    area = brief.get("area") or ""
    if area and area not in ("UAE-wide", "everywhere"):
        lines.append(f"THE AREA: {area}")
    else:
        from coordination import countries, profile
        # Every market he sells in, or the world — not only the home country.
        whole = (profile.market_phrase() if profile.worldwide() or len(profile.markets()) > 1
                 else "the whole UAE" if profile.is_starter()
                 else "all of " + countries.in_a_sentence(profile.country()))
        lines.append(f"THE AREA: {whole} — " + ", ".join(facts.get("areas") or []))
    lines.append("HOW LONG: " + (f"about {pack['target_words']} words" if pack and
                                 pack.get("target_words") else LENGTH.get(kind, LENGTH['service_page']))
                 + " — full, specific and useful; never padded")
    for m in (brief.get("must_include") or []):
        # The photographs are handled below, from real files; the old brief
        # line about them would only make the writer leave a gap.
        if "photo" in str(m).lower():
            continue
        lines.append("MUST COVER: " + str(m))
    for d in (brief.get("do_not") or []):
        if "stock photograph" in str(d).lower():
            continue
        lines.append("DO NOT: " + str(d))

    lines.append("")
    lines.append("PICTURES YOU CAN PLACE:" if images else "PICTURES: none this time.")
    for i, img in enumerate(images or [], 1):
        lines.append(f"  <!--image {i}--> — "
                     + ("his own photograph: " if img.get("own") else "an illustrative photo: ")
                     + (img.get("alt") or "a photograph"))

    lines.append("")
    lines.append("THE ONLY FACTS YOU HAVE ABOUT THE BUSINESS ITSELF:")
    if facts.get("name"):
        lines.append(f"  it is called {facts['name']}")
    if facts.get("phone"):
        lines.append(f"  its phone number is {facts['phone']} — link it as "
                     f"<a href=\"tel:{re.sub(r'[^0-9+]', '', facts['phone'])}\">")
    wa = _wa_link(facts.get("whatsapp"))
    if wa:
        lines.append(f"  its WhatsApp link is {wa} — link it as <a href=\"{wa}\">")
    if facts.get("email"):
        lines.append(f"  its email is {facts['email']}")
    lines.append(f"  the owner has {_years()} years in the trade")
    lines.append("  it works across: " + ", ".join(facts.get("areas") or []))
    lines.append("")
    lines.append("Nothing else about the business itself may be stated. General "
                 "trade knowledge — materials, methods, the climate, what to ask a "
                 "contractor — you should use freely and accurately.")

    if fix and previous:
        lines += ["", "YOU WROTE THIS ALREADY, AND IT CAME BACK FOR THESE REASONS:"]
        lines += [f"  - {x}" for x in fix]
        lines += ["Rewrite it so every reason is dealt with. Keep what was good. "
                  "Same JSON shape.", "", "YOUR DRAFT:",
                  json.dumps({k: previous.get(k) for k in ("title", "meta", "h1", "html")})]

    r = think(BRAIN, [{"role": "system", "content": system_prompt()},
                      {"role": "user", "content": "\n".join(lines)}],
              agent=AGENT, max_tokens=tokens or TOKENS.get(kind, WRITE_TOKENS),
              timeout=540,
              reason=(("revising" if fix else "writing") + f" the {kind.replace('_', ' ')}: "
                      + (brief.get("title") or "untitled")))
    if not r.get("ok"):
        return {"ok": False,
                "error": f"Content's brain did not answer ({r.get('error') or 'no reason given'})"}
    page = _parse(r.get("output"))
    if not page:
        return {"ok": False,
                "error": "the brain's reply was not in the shape asked for, so it "
                         "was not trusted to be a page"}
    return {"ok": True, "page": page, "brain": r.get("used"),
            "rung": r.get("rung"), "borrowed": r.get("why") if r.get("rung") != "own" else ""}


def _years() -> int:
    try:
        return auditor.verified_years()
    except Exception:
        return 10


def words_in(page) -> int:
    text = re.sub(r"<[^>]+>", " ", str((page or {}).get("html") or ""))
    return len(re.findall(r"\b[\w'’-]+\b", text))


def voice_problems(page, kind="service_page") -> list:
    """Why a draft reads thin or machine-made, in words the writer can act on."""
    out = []
    n = words_in(page)
    least = FEWEST.get(kind, FEWEST["service_page"])
    if n < least:
        out.append(f"it is {n} words; this should be {LENGTH.get(kind)}. Add real "
                   "substance — how the job is done, the options and their "
                   "trade-offs, what goes wrong, what to ask — not padding")
    text = re.sub(r"<[^>]+>", " ", str((page or {}).get("html") or "")).lower()
    tells = [t for t in AI_TELLS if t in text]
    if tells:
        out.append("these phrases make it read as machine-written; say it plainly "
                   "instead: " + ", ".join(f"'{t}'" for t in tells[:10]))
    body = str((page or {}).get("html") or "")
    heads = len(re.findall(r"<h[23]\b", body, re.I))
    if n >= 300 and heads < max(3, n // 250):
        out.append(f"it has {heads} subheading(s) for {n} words; give it an <h2> every few "
                   "paragraphs, with <h3> under them where natural, so a reader can scan it")
    if n >= 300 and len(re.findall(r"<strong\b", body, re.I)) < 3:
        out.append("put the words that matter in <strong> — the main keyword the first time, "
                   "and two or three key terms in each section")
    if text.count("—") > max(6, n // 120):
        out.append("too many dashes — use full stops and commas the way a person "
                   "writing quickly would")
    return out


def _parse(out):
    """The JSON out of a model reply, however it wrapped it."""
    s = (out or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-z]*\s*|\s*```$", "", s, flags=re.S)
    try:
        d = json.loads(s)
    except Exception:
        m = re.search(r"\{.*\}", s, re.S)
        if not m:
            return None
        try:
            d = json.loads(m.group(0))
        except Exception:
            return None
    if not isinstance(d, dict) or not str(d.get("html") or "").strip():
        return None
    # Characters nobody can see come out before anything else reads it (pipelines/scrub).
    from .scrub import scrub, scrub_html
    return {k: (scrub_html(v) if k == "html" else scrub(v)) if isinstance(v, str) else v for k, v in d.items()}


def _place_images(body, images):
    """Put each picture where the writer marked it; any unplaced go after a heading."""
    from . import post_images
    body = str(body or "")
    used = set()
    for i, img in enumerate(images or [], 1):
        pat = re.compile(r"<!--\s*image\s*%d\s*-->" % i, re.I)
        if pat.search(body):
            body = pat.sub(post_images.figure(img), body, count=1)
            used.add(i)
    body = re.sub(r"<!--\s*image\s*\d+\s*-->", "", body, flags=re.I)
    left = [img for i, img in enumerate(images or [], 1) if i not in used]
    if left:
        parts = re.split(r"(?=<h2\b)", body, flags=re.I)
        for n, img in enumerate(left):
            at = min(len(parts) - 1, 2 + n * 2) if len(parts) > 1 else 0
            parts[at] = parts[at] + "\n" + post_images.figure(img)
        body = "".join(parts)
    return body


def _body(page, images=()):
    body = _place_images(str(page.get("html") or "").strip(), images)
    # The marker for his own photographs stays only when none of his own are
    # in the piece — then it is still unfinished without them.
    if PHOTO_SLOT not in body and not any(i.get("own") for i in images or ()):
        body = body + "\n\n" + PHOTO_SLOT
    return body


def _standalone(page, brief, images=()):
    """The article on its own — what the Auditor reads, and the last resort."""
    title = str(page.get("title") or brief.get("title") or "").strip()[:70]
    meta = str(page.get("meta") or "").strip()[:160]
    return "\n".join([
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{_esc(title)}</title>",
        f'<meta name="description" content="{_esc(meta)}">',
        "</head><body>", "<main>", _body(page, images), "</main>", "</body></html>",
    ])


def _shell(page, brief, facts, theme=None, images=()):
    """
    The body, wrapped so it comes out looking like his website.

    "this link that program post it is plain no any layout no any design"

    Two honest routes and no guessing between them. With the frame of one of
    his own pages (site_api.theme_of_his_site) the new page is that page with
    its content swapped for this one: his menu, his styles, his footer, and a
    .php like the rest of his site. Without one, the page is standalone and
    the approval says so — a page that quietly looks nothing like the rest of
    his site is worse than one he was warned about.
    """
    theme = theme or {}
    if theme.get("top") and theme.get("bottom"):
        title = str(page.get("title") or brief.get("title") or "").strip()[:70]
        meta = str(page.get("meta") or "").strip()[:160]
        return ("\n".join([
            _retitle(theme["top"], title, meta),
            '<main class="agency-page" style="max-width:920px;margin:40px auto;'
            'padding:0 16px;line-height:1.7">',
            _body(page, images), "</main>", theme["bottom"]]), "his-theme")
    return _standalone(page, brief, images), "standalone"


def _php_str(s):
    return "'" + str(s or "").replace("\\", "\\\\").replace("'", "\\'") + "'"


def _retitle(top, title, meta):
    """His page's top half, saying this page's title and description, not its own."""
    said = [False]

    def swap_title(m):
        said[0] = True
        return f"<title>{_esc(title)}</title>"
    top = re.sub(r"<title\b[^>]*>.*?</title\s*>", swap_title, top, count=1, flags=re.I | re.S)
    top, n = re.subn(r'(<meta\s+name=["\']description["\']\s+content=)(["\']).*?\2',
                     lambda m: f'{m.group(1)}"{_esc(meta)}"', top, count=1, flags=re.I | re.S)
    if not n and meta and re.search(r"</head\s*>", top, re.I):
        top = re.sub(r"</head\s*>", f'<meta name="description" content="{_esc(meta)}">\n</head>',
                     top, count=1, flags=re.I)
    for prop, val in (("og:title", title), ("og:description", meta)):
        top = re.sub(r'(<meta\s+property=["\']%s["\']\s+content=)(["\']).*?\2' % prop,
                     lambda m, v=val: f'{m.group(1)}"{_esc(v)}"', top, flags=re.I | re.S)
    # The page it was copied from has its own address; this one must not claim it.
    top = re.sub(r'<link\b[^>]*rel=["\']canonical["\'][^>]*>\s*', "", top, flags=re.I)
    top = re.sub(r'<meta\b[^>]*property=["\']og:url["\'][^>]*>\s*', "", top, flags=re.I)
    # A title kept in a PHP variable, the way many PHP sites pass it to the header.
    for names, val in ((r"page_?title|title|pageTitle|meta_?title", title),
                       (r"meta_?description|page_?description|description|metaDesc", meta)):
        top, k = re.subn(r"\$(%s)\s*=\s*(['\"])(?:(?!\2).)*\2\s*;" % names,
                         lambda m, v=val: f"${m.group(1)} = {_php_str(v)};", top, flags=re.S)
        said[0] = said[0] or (k > 0 and val == title)
    if not said[0]:
        # The title comes from an include; give it the usual names to read.
        top = (f"<?php $page_title = {_php_str(title)}; $title = $page_title; "
               f"$meta_description = {_php_str(meta)}; ?>\n" + top)
    return top


def _esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _theme_of_his_site():
    """The frame of one of his own pages, if the agency can read one. {} if not."""
    try:
        got = site_api.theme_of_his_site()
        return got if got.get("ok") else {}
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# A BLOG POST GOES INTO HIS BLOG, NOT ONTO A PAGE OF ITS OWN
# ---------------------------------------------------------------------------
#
# "i had build the BAckend from where the blogs get uploaded and the blog is
#  blog.php ... https://worlddealtechnicalservices.com/blog-details.php?slug=
#  custom-wooden-interior-design-dubai and this have a complete layout theme"
#
# His posts live in his own backend, which stores them in his database and
# shows them through blog.php and blog-details.php in his theme. A post put up
# as a file of its own sits outside all of that: no layout, not in his blog
# list, not at his blog's address. So a written post is made ready for his
# blog — title, link name, short description, the article and its pictures —
# and waits in the Posts room.
#
# "yes add the posts to my backend direct" — from there one press sends it,
# through the Releaser, into the table his blog page reads (blog_backend.py).
# His admin area is still never touched: the connector adds the row itself.
# Each piece also has a Copy button, for pasting it in by hand.

BLOG_POST_URL = "{site}/blog-details.php?slug={slug}"


def blog_slug(title) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(title or "").lower()).strip("-")[:70].strip("-")


def _absolute(src, site):
    src = str(src or "")
    if re.match(r"https?://", src, re.I) or not site:
        return src
    return site.rstrip("/") + "/" + src.lstrip("/")


def _as_text(html) -> str:
    """The article as plain text, for a backend with a plain box."""
    t = str(html or "")
    t = re.sub(r"<!--.*?-->", "", t, flags=re.S)
    t = re.sub(r"<figure\b.*?</figure>", "", t, flags=re.I | re.S)
    t = re.sub(r"<li\b[^>]*>", "\n- ", t, flags=re.I)
    t = re.sub(r"</(p|h[1-6]|ul|ol|div|blockquote)>", "\n\n", t, flags=re.I)
    t = re.sub(r"<br\s*/?>", "\n", t, flags=re.I)
    t = re.sub(r"<[^>]+>", "", t)
    for a, b in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'),
                 ("&#39;", "'"), ("&nbsp;", " ")):
        t = t.replace(a, b)
    t = re.sub(r"[ \t]+", " ", t)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", t).strip()


def for_his_blog(page, brief, images=(), site="", pack=None) -> dict:
    """Everything that goes into his blog backend, each piece on its own."""
    title = str(page.get("title") or brief.get("title") or "").strip()
    # The link name is the main keyword — short, and saying what the post is
    # about to anyone reading the address, Google included.
    slug = blog_slug((pack or {}).get("primary")) or blog_slug(title) \
        or blog_slug(brief.get("primary_keyword")) or "post"
    imgs = [{**i, "src": _absolute(i.get("src"), site)} for i in images or []]
    body = _place_images(str(page.get("html") or "").strip(), imgs)
    body = body.replace(PHOTO_SLOT, "").strip()
    # Pasted into his backend, a picture's path must still find his server.
    if site:
        body = re.sub(r'(<img\b[^>]*\bsrc=["\'])(/?[^"\':]+)(["\'])',
                      lambda m: m.group(1) + _absolute(m.group(2), site) + m.group(3),
                      body, flags=re.I)
        if not imgs:
            host = re.sub(r"^https?://(www\.)?", "", site).split("/")[0].lower()
            for tag in re.findall(r"<img\b[^>]*>", body, flags=re.I):
                src = re.search(r'\bsrc=["\']([^"\']+)["\']', tag, re.I)
                alt = re.search(r'\balt=["\']([^"\']*)["\']', tag, re.I)
                if src:
                    imgs.append({"src": src.group(1), "alt": alt.group(1) if alt else "",
                                 "own": bool(host) and host in src.group(1).lower()})
    # His post page prints the title itself; a second one in the article is a
    # page with two headlines.
    body = re.sub(r"^\s*<h1\b[^>]*>.*?</h1>\s*", "", body, count=1, flags=re.I | re.S)
    words = ([pack.get("primary")] + list(pack.get("secondary") or [])[:7] if pack else
             [brief.get("primary_keyword")] + list(brief.get("secondary_keywords") or [])[:5])
    return {"title": title[:120], "slug": slug,
            "meta": str(page.get("meta") or "").strip()[:160],
            "keywords": ", ".join(w for w in words if w),
            "html": body, "text": _as_text(body),
            "images": [{"src": i["src"], "alt": i.get("alt") or "",
                        "own": bool(i.get("own")), "credit": i.get("credit") or ""}
                       for i in imgs],
            "url": BLOG_POST_URL.format(site=(site or "").rstrip("/"), slug=slug)}


def pages_to_his_blog(c, site="", dry=False) -> int:
    """
    "the program is making a new page everytime for each post while the main
     thing is the new blog post ... not a new page"

    Every page the agency queued or put up as a post of its own becomes a post
    for his blog. One still waiting is moved; one already up stays listed with
    its page, so the page can be deleted once the post is in his blog. A post
    he dropped stays dropped. Once each; `dry` only counts. Returns how many.
    """
    moved = 0
    rows = c.execute("SELECT id, status, detail FROM site_queue WHERE kind='new_page'"
                     " AND status IN ('pending','approved','applied')").fetchall()
    for sq_id, status, detail in rows:
        try:
            d = json.loads(detail or "{}")
        except ValueError:
            continue
        ch = d.get("change") or {}
        name, cid = str(ch.get("page") or ""), ch.get("content_id")
        if not (name and cid):
            continue
        got = c.execute("SELECT draft, brief, status FROM content_queue WHERE id=?",
                        (int(cid),)).fetchone()
        if not got:
            continue
        try:
            draft = json.loads(got[0] or "{}")
            brief = json.loads(got[1] or "{}")
        except ValueError:
            continue
        was_status = got[2] or ""
        if was_status == "rejected":
            continue
        blog = draft.get("_for_blog") or {}
        changed = False
        if not blog:
            html = str(ch.get("html") or draft.get("_page_html") or draft.get("html") or "")
            m = (re.search(r'<main class="agency-page"[^>]*>(.*?)</main>', html, re.I | re.S)
                 or re.search(r"<main\b[^>]*>(.*)</main>", html, re.I | re.S))
            inner = m.group(1) if m else re.sub(r"<\?php.*?\?>", "", html, flags=re.S)
            # "Aluminium Cladding Sheet for Duct | UAE Wide" is a browser title;
            # a post in his blog list is headed by the part before the bar.
            title = str(draft.get("title") or d.get("title") or "").split(" | ")[0].strip()
            blog = for_his_blog({"title": title, "meta": draft.get("meta"), "html": inner},
                                brief, (), site, pack=brief.get("seo_pack"))
            changed = True
        if status == "applied" and (blog.get("plain_page") or {}).get("page") != name:
            blog["plain_page"] = {"page": name, "site_queue": sq_id}
            changed = True
        on_blog = was_status == "published" and (blog.get("backend") or {}).get("id")
        new_status = "published" if on_blog else "for_blog"
        if status in ("pending", "approved"):
            changed = True
            if not dry:
                c.execute("UPDATE site_queue SET status='rejected', decided_at=?, note=?"
                          " WHERE id=?",
                          (int(time.time()), "moved to Ready for your blog — a post goes into "
                           "your own blog, not onto a page of its own", sq_id))
        if not changed and new_status == was_status:
            continue
        moved += 1
        if dry:
            continue
        draft["_for_blog"] = blog
        c.execute("UPDATE content_queue SET draft=?, status=?, type='blog' WHERE id=?",
                  (json.dumps(draft, indent=2), new_status, int(cid)))
    return moved


# The old name, for anything still calling it.
blogs_off_the_site_queue = pages_to_his_blog


def take_down_page(name, go_to) -> str:
    """What a plain copy is replaced with: a pointer to the real post, kept out of Google."""
    go = _esc(go_to)
    return "\n".join([
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        "<title>This article has moved to our blog</title>",
        '<meta name="robots" content="noindex, follow">',
        f'<link rel="canonical" href="{go}">',
        f'<meta http-equiv="refresh" content="0; url={go}">',
        "</head><body>",
        "<p>This article has moved to our blog, with the rest of our guides and "
        f'finished projects. If you are not taken there, <a href="{go}">open it here</a>.</p>',
        "</body></html>"])


def _slug_path(brief, kind="service_page"):
    url = str(brief.get("url") or "").strip("/")
    if not url:
        url = re.sub(r"[^a-z0-9]+", "-", str(brief.get("title") or "page").lower()).strip("-")
    return url


# ---------------------------------------------------------------------------
# THE PASS
# ---------------------------------------------------------------------------

def critic_check(page, brief, facts) -> tuple:
    """
    Content's own check, before the Auditor's.

    The Auditor asks "is this fit to leave the building". This asks the
    narrower question the Auditor cannot: is it the page that was BRIEFED. A
    perfectly clean page about the wrong thing passes every rule in auditor.py
    and is still a waste of his domain.
    """
    reasons = []
    html = str(page.get("html") or "")
    text = re.sub(r"<[^>]+>", " ", html).lower()
    if not text.strip():
        reasons.append("the brain returned no body")
    svc = str(brief.get("service") or "").split()[0].lower() if brief.get("service") else ""
    if svc and svc not in text:
        reasons.append(f"it never mentions {brief.get('service')!r}, which is the "
                       "service this page is meant to sell")
    # THE WHOLE MARKUP, not the stripped text. The number's proper home is
    # inside href="tel:+9714..." — a page that does it right has it only in the
    # attribute, and searching the visible words alone failed exactly the pages
    # that were correct.
    if facts.get("phone"):
        want = re.sub(r"\D", "", facts["phone"])[-7:]
        if want and want not in re.sub(r"\D", "", html):
            reasons.append("his phone number is not on the page")
    return (len(reasons) == 0, reasons)


def run(agent=AGENT, dry_run=False, only_id=None, limit=MAX_PER_PASS) -> dict:
    with board.agent_run(agent) as run_row:
        with deskwork.at_the_desk(
                SECTOR, agent, run_row,
                # content_briefs runs at this desk too. It turns keywords into
                # briefs; this turns briefs into pages. Only take a job that is
                # about the writing.
                takes=lambda j: j.wants("write", "draft", "publish", "page",
                                        "article", "case study", "rewrite",
                                        "copy", "post", "blog")) as job:
            out = _write(agent, dry_run, only_id, limit, job)
            deskwork.settle(job, out, made=len(out.get("pages") or []),
                            what="page drafted|pages drafted")
        run_row.note(out.get("summary") or out.get("error") or "wrote nothing")
        return out


def _write(agent, dry_run, only_id, limit, job=None):
    board.ensure_schema()
    facts = _settings()
    con = sqlite3.connect(board.DB, timeout=30)
    try:
        waiting = next_brief(con, limit=limit, only_id=only_id)
        if not waiting:
            n = con.execute("SELECT count(*) FROM content_queue"
                            " WHERE status LIKE 'blocked_on_%'").fetchone()[0]
            return {"ok": False, "idle": True, "agent": agent, "pages": [],
                    "error": ("Nothing is briefed and waiting to be written."
                              + (f" {n} brief(s) are held for another sector's "
                                 "call — SEO clears volume before Content builds "
                                 "the page." if n else
                                 " Run the SEO strategist and then the briefs "
                                 "desk, and this will have something to write."))}

        # THE BRAIN IS CHECKED BEFORE ANY WORK IS DONE, not after. Finding out
        # there is no writer AFTER borrowing a shelf and reading the cabinet is
        # how a pass burns a minute to say nothing. The check itself is cheap:
        # is Claude Code ready on this machine. No call is made.
        try:
            from providers import ladder
            route = ladder.who_could_answer(BRAIN)
        except Exception as e:                                # pragma: no cover
            route = {"ok": False, "why": str(e), "own_error": str(e)}
        if not route.get("ok"):
            return {"ok": False, "agent": agent, "pages": [],
                    "waiting": len(waiting),
                    "error": ("Content cannot think right now, so there is nothing "
                              "here that can write a page: "
                              + str(route.get("own_error") or route.get("why") or "")
                              + ". Nothing was written — a template with the "
                                "keyword slotted in four times is the page every "
                                "other glazier already has, and it would cost him "
                                "rather than earn. Once Claude is signed in on "
                                "this machine it writes on the next run.")}

        theme = None                      # read once, and only if a page needs it
        pages, held = [], []
        # STOP BEFORE THE STEP IS KILLED, AND KEEP WHAT IS FINISHED.
        #
        # "its work failed: Content writes the pages — timed out after 420s"
        # and six pages still sitting in the queue afterwards.
        #
        # Each page is a model call, and six of them at a minute or two each
        # does not fit in one step. Being killed by the day's stopwatch is the
        # worst possible way to end: the pages already written are lost with
        # everything else, so the next run starts from six again and times out
        # again, for ever. The queue never drains and the step never succeeds.
        #
        # A page is banked the moment it is finished (each _store commits), so
        # the only thing missing was knowing when to stop. This leaves a
        # minute's headroom, writes as many as fit, and reports the rest as
        # still queued — which is a partial success and reads like one, instead
        # of a failure that threw away real work.
        deadline = _deadline()
        for row in waiting:
            if deadline and time.time() > deadline:
                held.append({"id": row["id"], "title": row["title"],
                             "why": (f"ran out of time this pass — {len(pages)} "
                                     f"page(s) were written and banked. This one "
                                     f"is still queued and is first up next run.")})
                continue
            brief = row["brief"]
            # A BLOG POST when that is what he asked for. "i ask for blog
            # posts" — his order reaches this desk as the job, and the brief
            # underneath it is the subject.
            # EVERYTHING IS A POST. "the program is making a new page everytime
            # for each post while ... the blogs are stored in DB and called from
            # there". A service brief is written as a post for his blog too; a
            # page of its own is only ever made if EVERYTHING_IS_A_POST is off.
            kind = ("blog" if EVERYTHING_IS_A_POST or row.get("type") == "blog" or (job and job.wants(
                "blog", "post", "posts", "article", "articles")) else "service_page")
            try:
                from . import post_images
                images = post_images.for_post(brief, facts.get("site") or "")
            except Exception:
                images = []
            # SEO'S KEYWORD PLAN FIRST. "the seo sector finds the keywords ... then
            # it gives those keywords to the post room". Made by the SEO desk and
            # kept on the brief, so a rewrite uses the same plan.
            try:
                from . import seo_keywords
                pack = seo_keywords.plan_for(row["id"], brief, kind=kind,
                                             site=facts.get("site") or "")
            except Exception:
                pack = {}
            seo_check = (lambda pg: seo_keywords.check(pg, pack, kind)) if pack else \
                (lambda pg: {"problems": [], "score": None})
            got = _ask_the_writer(brief, facts, kind=kind, images=images, pack=pack)
            if not got.get("ok"):
                held.append({"id": row["id"], "title": row["title"],
                             "why": got.get("error")})
                continue

            page = got["page"]
            # ONE SECOND GO, TOLD WHY: too short, phrases that read as
            # machine-written, or something the Critic needs. Kept only if it
            # is better than the first.
            # ORIGINAL, AND READING LIKE A PERSON: nothing lifted word for word
            # from his other posts or from the pages Google ranks for the same
            # search, and sentences of different lengths (post_quality).
            not_this = post_quality.others(con, row["id"], pack)
            first = (voice_problems(page, kind) + critic_check(page, brief, facts)[1]
                     + seo_check(page)["problems"] + post_quality.extra(page, not_this))
            if first:
                again = _ask_the_writer(brief, facts, kind=kind, images=images,
                                        fix=first, previous=page, pack=pack)
                if again.get("ok"):
                    second = (voice_problems(again["page"], kind)
                              + critic_check(again["page"], brief, facts)[1]
                              + seo_check(again["page"])["problems"]
                              + post_quality.extra(again["page"], not_this))
                    if len(second) < len(first) or (
                            len(second) == len(first)
                            and words_in(again["page"]) > words_in(page)):
                        page, got = again["page"], again
            ok, reasons = critic_check(page, brief, facts)
            short = [v for v in voice_problems(page, kind) if v.startswith("it is ")]
            if short and kind == "blog":
                ok, reasons = False, reasons + short
            copied = post_quality.originality(page.get("html"), not_this)
            if not copied["ok"]:
                ok, reasons = False, reasons + [
                    f"{copied['share']}% of it is word for word from {copied['like'] or 'another page'}"]
            if not ok:
                held.append({"id": row["id"], "title": row["title"],
                             "why": "the Critic rejected it: " + "; ".join(reasons)})
                _store(con, row["id"], page, "in_review", reasons, dry_run)
                continue

            # The Auditor reads what the agency WROTE — the article on its own.
            # His theme is his own code (menus, scripts) and is not the
            # writer's to answer for; it goes round the article afterwards.
            article = _standalone(page, brief, images)
            host = re.sub(r"^https?://(www\.)?", "", facts.get("site") or "").split("/")[0]
            verdict = auditor.check_page(article, brief, site_host=host)
            if not verdict["pass"]:
                # ONE MORE GO, TOLD EXACTLY WHY. "nothing written — 1 held": a
                # post on "glass railing price in dubai" quoted an AED figure and
                # was thrown away whole for one sentence. The Auditor's reason,
                # word for word, is usually all a rewrite needs.
                fixed = _ask_the_writer(brief, facts, kind=kind, images=images, pack=pack,
                                        previous=page,
                                        fix=["the Auditor held it: " + b for b in verdict["blocking"]])
                if fixed.get("ok"):
                    art2 = _standalone(fixed["page"], brief, images)
                    v2 = auditor.check_page(art2, brief, site_host=host)
                    if v2["pass"] and critic_check(fixed["page"], brief, facts)[0]:
                        page, got, article, verdict = fixed["page"], fixed, art2, v2
            if not verdict["pass"]:
                held.append({"id": row["id"], "title": row["title"],
                             "why": "the Auditor held it: " + verdict["blocking"][0]})
                _store(con, row["id"], page, "in_review", verdict["blocking"], dry_run,
                       html=article)
                continue

            report = seo_check(page)
            seo = seo_keywords.summary(pack, report) if pack else {}
            if kind == "blog":
                blog = for_his_blog(page, brief, images, facts.get("site") or "",
                                    pack=pack)
                if not dry_run:
                    _store(con, row["id"], page, "for_blog", verdict["warnings"], False,
                           html=article, blog=blog, seo=seo)
                    if kind != (row.get("type") or ""):
                        con.execute("UPDATE content_queue SET type=? WHERE id=?",
                                    (kind, row["id"]))
                        con.commit()
                pages.append({"id": row["id"], "title": blog["title"], "kind": kind,
                              "images": len(images or []), "page": blog["url"],
                              "how": "for-your-blog", "words": verdict["words"],
                              "queued": None, "for_blog": True, "seo_score": report.get("score"),
                              "warnings": verdict["warnings"], "brain": got.get("brain")})
                continue

            if theme is None:
                theme = _theme_of_his_site()
            html, how = _shell(page, brief, facts, theme, images=images)
            path = _slug_path(brief, kind)
            page_file = path.lstrip("/") + (".php" if how == "his-theme" and theme.get("php")
                                            else ".html")
            queued = None
            if not dry_run:
                _store(con, row["id"], page, "drafted", verdict["warnings"], False,
                       html=html, seo=seo)
                # Nested under 'change', because that is the shape the Releaser
                # reads — see it_release.plan_of. A payload the Releaser cannot
                # parse comes back to him as "this approval does not carry a
                # change anyone can carry out", which is the program blaming
                # him for its own gap. He has had that once already.
                queued = board.queue_site_change(agent, "new_page", {
                    "change": {"page": page_file, "html": html,
                               "content_id": row["id"]},
                    "bytes": len(html),
                    "title": page.get("title") or row["title"],
                    "why": (f"A new page for {brief.get('primary_keyword') or row['title']!r}. "
                            + (f"It is built in the frame of your own {theme.get('page')} "
                               "— your menu, styles and footer — so it will look like "
                               "the rest of your site."
                               if how == "his-theme" else
                               "The agency could not read the layout of any of your "
                               "pages, so this page is standalone — it will NOT match "
                               "your template until somebody wraps it.")
                            + " It has a marked gap where your photographs go; it is "
                              "not finished until they are in."),
                    "auditor": verdict["warnings"],
                })
            if not dry_run and kind != (row.get("type") or ""):
                con.execute("UPDATE content_queue SET type=? WHERE id=?", (kind, row["id"]))
                con.commit()
            pages.append({"id": row["id"], "title": page.get("title") or row["title"],
                          "kind": kind, "images": len(images or []),
                          "page": page_file, "how": how, "words": verdict["words"],
                          "queued": queued, "warnings": verdict["warnings"],
                          "brain": got.get("brain")})

        posts = [p for p in pages if p.get("for_blog")]
        if pages and not dry_run:
            board.log_decision(
                "content-write",
                reasoning=(f"Wrote {len(pages)} piece(s) from briefs SEO's gap list "
                           f"produced. Each passed the Auditor. "
                           + (f"{len(pages) - len(posts)} page(s) are queued for his "
                              "approval; " if len(pages) > len(posts) else "")
                           + (f"{len(posts)} post(s) are ready for him to add in his "
                              "own blog backend; " if posts else "")
                           + "none is on the site yet."),
                expectation=("A service+area page he approves, or a post he adds to "
                             "his blog, starts taking enquiries for a phrase his site "
                             "does not currently answer."),
                confidence=0.6,
                evidence={"content_queue_ids": [p["id"] for p in pages],
                          "site_queue_ids": [p["queued"] for p in pages if p["queued"]]})

        con.commit()
        bits = []
        if len(pages) > len(posts):
            bits.append(f"{len(pages) - len(posts)} page(s) written and waiting for your yes")
        if posts:
            bits.append(f"{len(posts)} post(s) ready for your blog — put them on it from "
                        "the Posts room")
        summary = "; ".join(bits) if bits else f"nothing written — {len(held)} held"
        return {"ok": True, "agent": agent, "pages": pages, "held": held,
                "shell": (f"the frame of your own {theme.get('page')}" if theme
                          else "standalone" if theme is not None else "not needed"),
                "dry_run": bool(dry_run), "summary": summary}
    finally:
        con.close()


def _store(con, qid, page, status, notes, dry_run, html="", blog=None, seo=None):
    if dry_run:
        return
    body = dict(page)
    if html:
        body["_page_html"] = html
    if blog:
        body["_for_blog"] = blog
    if seo:
        body["_seo"] = seo
    if notes:
        body["_held_because"] = list(notes)[:8]
    con.execute("UPDATE content_queue SET draft=?, status=? WHERE id=?",
                (json.dumps(body, indent=2), status, qid))
    con.commit()


# ---------------------------------------------------------------------------
# THE SCREEN
# ---------------------------------------------------------------------------

def as_text(r) -> str:
    if not r.get("ok"):
        return "CONTENT WRITER — " + (r.get("error") or "nothing to do")
    L = [f"CONTENT WRITER — {r['summary']} ({r['shell']})"]
    for p in r.get("pages") or []:
        L.append(f"   wrote   {p['page']}  ({p['words']} words)"
                 + (f"  queued #{p['queued']}" if p.get("queued") else
                    "  ready for your blog" if p.get("for_blog") else "  [dry run]"))
        for w in p.get("warnings") or []:
            L.append(f"      note {w}")
    for h in r.get("held") or []:
        L.append(f"   held    {h['title'][:50]} — {h['why']}")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="Write the pages Content has briefed.")
    ap.add_argument("--list", action="store_true", help="what is waiting to be written")
    ap.add_argument("--show", type=int, metavar="ID", help="read one stored draft")
    ap.add_argument("--dry-run", action="store_true", help="write it, store nothing")
    ap.add_argument("--id", type=int, help="write one specific brief")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.list:
        board.ensure_schema()
        con = sqlite3.connect(board.DB)
        try:
            rows = next_brief(con, limit=50)
            if not rows:
                print("Nothing briefed and waiting. Run the SEO strategist, then "
                      "python -m pipelines.content_briefs")
                return 0
            for r in rows:
                b = r["brief"]
                print(f'#{r["id"]}  {r["title"][:52]:54} {b.get("primary_keyword", "")}')
        finally:
            con.close()
        return 0

    if a.show:
        board.ensure_schema()
        con = sqlite3.connect(board.DB)
        try:
            row = con.execute("SELECT title, status, draft FROM content_queue"
                              " WHERE id=?", (a.show,)).fetchone()
        finally:
            con.close()
        if not row:
            print(f"no draft #{a.show}")
            return 1
        print(f"{row[0]}  [{row[1]}]\n")
        print(row[2] or "(nothing written yet)")
        return 0

    r = run(dry_run=a.dry_run, only_id=a.id)
    print(json.dumps(r, indent=2) if a.json else as_text(r))
    return deskwork.exit_code(r)


if __name__ == "__main__":
    sys.exit(main())
