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

IT DOES NOT MATCH HIS TEMPLATE BY GUESSING. If his site has a header include it
uses it, so the page comes out looking like his site. If it does not, it says
so plainly in the approval rather than shipping a page that looks foreign.
"""
import argparse
import json
import re
import sqlite3
import sys
import time

from coordination import auditor, board, deskwork, uae
from . import site_api

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

# Room for a real service page and no more. A model given eight thousand tokens
# writes eight thousand tokens.
WRITE_TOKENS = 2200

BRAIN = "content"

# Where his own photographs go. A marker rather than an <img> pointing at
# nothing: the Auditor can see it, the office can list it, and a page that goes
# live with the marker still in it is obviously unfinished rather than subtly
# broken.
PHOTO_SLOT = "<!-- agency:photos — put 3 to 6 of your own finished-job photos here -->"


SYSTEM = """\
You are the Content writer for a glass and aluminium business in the UAE. The \
owner has ten years in the trade and does the work himself. You are writing one \
page for his own website.

HOW HE SOUNDS
British English. Plain. Short sentences. The way a tradesman who is good at his \
job talks to a customer who wants a price and a date. No marketing language, no \
"nestled in the heart of", no "in today's fast-paced world", no exclamation \
marks, no rhetorical questions as headings.

WHAT WINS THESE JOBS
Speed of reply, a real survey date, and finished work the customer can look at. \
Not adjectives. Lead with what the job actually involves and how fast he can be \
there.

WHAT YOU MUST NOT DO — every one of these is checked after you, and a page that \
breaks one is thrown away:
  - Do not invent a fact. No years in business, no number of projects, no client \
    names, no certifications, no awards, no percentages. You have been given \
    everything you are going to be given.
  - Do not name a price, a rate, a discount or an offer. That is the owner's \
    call and it is a hard rule.
  - Do not write any code. No <script>, no <style>, no <form>, no <iframe>, no \
    onclick. Markup for text only.
  - Do not add an image. Photographs are his own and they are added afterwards.
  - Do not leave a placeholder of any kind.

WHAT THE PAGE MUST HAVE
  - One <h1> saying what the page is.
  - Two to five <h2> sections. One of them answers how quickly he can come and \
    measure.
  - The enquiry route in the body, high up: the phone number and the WhatsApp \
    link you are given, as real links.
  - The area named, if you were given one.

Reply with JSON and nothing else:
{"title": "<the browser title, under 60 characters>",
 "meta": "<the search-result description, under 155 characters>",
 "h1": "<the page heading>",
 "html": "<the page body: h1, p, h2, ul, a and strong only>",
 "note": "<one line on what you led with and why>"}"""


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

def _ask_the_writer(brief, facts, tokens=WRITE_TOKENS) -> dict:
    """The Content brain writes the body. Returns {ok, page|error, brain}."""
    # "No brain means no page" is true of Content in a way it is not of the
    # sectors whose brains only word things — the page IS the model's output.
    # Content's own Claude call already steps down the models and over to the
    # backup account when it has to (providers/claude_team.py).
    try:
        from providers import think
    except Exception as e:                                    # pragma: no cover
        return {"ok": False, "error": f"the provider shelf did not load ({e})"}

    lines = [f"THE PAGE: {brief.get('title') or ''}",
             f"WHAT IT IS FOR: {brief.get('angle') or ''}",
             f"THE PHRASE IT IS BRIEFED FOR: {brief.get('primary_keyword') or ''}"]
    sec = brief.get("secondary_keywords") or []
    if sec:
        lines.append("OTHER PHRASES PEOPLE SEARCH: " + ", ".join(sec[:10]))
    area = brief.get("area") or ""
    if area and area != "UAE-wide":
        lines.append(f"THE AREA: {area}")
    else:
        lines.append("THE AREA: the whole UAE — "
                     + ", ".join(facts.get("areas") or []))
    lines.append(f"HOW LONG: {brief.get('word_count') or '450-700 words'}")
    for m in (brief.get("must_include") or []):
        lines.append("MUST COVER: " + str(m))
    for d in (brief.get("do_not") or []):
        lines.append("DO NOT: " + str(d))

    lines.append("")
    lines.append("THE ONLY FACTS YOU HAVE ABOUT THE BUSINESS:")
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
    lines.append("  it works across: " + ", ".join(facts.get("areas") or []))
    lines.append("")
    lines.append("There are no other facts. Anything you would like to say that "
                 "is not on this list, leave out.")

    r = think(BRAIN, [{"role": "system", "content": SYSTEM},
                      {"role": "user", "content": "\n".join(lines)}],
              agent=AGENT, max_tokens=tokens,
              reason=f"writing the page: {brief.get('title') or 'untitled'}")
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
    return d


def _shell(page, brief, facts, include=""):
    """
    The body, wrapped so it comes out looking like his website.

    Two honest routes and no guessing between them. If his site has a header
    include the agency can read, the page is a .php that pulls it in, and it
    inherits his navigation, his styles and his footer. If it does not, the
    page is standalone and the approval says so — a page that quietly looks
    nothing like the rest of his site is worse than one he was warned about.
    """
    title = str(page.get("title") or brief.get("title") or "").strip()[:70]
    meta = str(page.get("meta") or "").strip()[:160]
    body = str(page.get("html") or "").strip()
    if PHOTO_SLOT not in body:
        body = body + "\n\n" + PHOTO_SLOT
    if include:
        foot = include.replace("header", "footer").replace("head", "foot") \
            if "header" in include or "head" in include else ""
        out = [f"<?php include __DIR__ . '/{include}'; ?>",
               "<main>", body, "</main>"]
        if foot and foot != include:
            out.append(f"<?php @include __DIR__ . '/{foot}'; ?>")
        return "\n".join(out), "php-include"
    return ("\n".join([
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{_esc(title)}</title>",
        f'<meta name="description" content="{_esc(meta)}">',
        "</head><body>", "<main>", body, "</main>", "</body></html>",
    ]), "standalone")


def _esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _include_of_his_site():
    """The header include his pages already use, if the agency can read one."""
    try:
        # READING, not writing. why_not_ready would refuse in read-only mode,
        # which is a decision about changing his site and says nothing about
        # looking at it — see site_api.why_cannot_read.
        if site_api.why_cannot_read():
            return ""
        for name in site_api.LIKELY_INCLUDES:
            if "header" not in name and "top" not in name:
                continue
            got = site_api.read_page(name)
            if got.get("ok") and (got.get("html") or "").strip():
                return name
    except Exception:
        pass
    return ""


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

        include = _include_of_his_site()
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
            got = _ask_the_writer(brief, facts)
            if not got.get("ok"):
                held.append({"id": row["id"], "title": row["title"],
                             "why": got.get("error")})
                continue

            page = got["page"]
            ok, reasons = critic_check(page, brief, facts)
            if not ok:
                held.append({"id": row["id"], "title": row["title"],
                             "why": "the Critic rejected it: " + "; ".join(reasons)})
                _store(con, row["id"], page, "in_review", reasons, dry_run)
                continue

            html, how = _shell(page, brief, facts, include)
            host = re.sub(r"^https?://(www\.)?", "", facts.get("site") or "").split("/")[0]
            verdict = auditor.check_page(html, brief, site_host=host)
            if not verdict["pass"]:
                held.append({"id": row["id"], "title": row["title"],
                             "why": "the Auditor held it: " + verdict["blocking"][0]})
                _store(con, row["id"], page, "in_review", verdict["blocking"], dry_run,
                       html=html)
                continue

            path = _slug_path(brief, row["type"])
            page_file = path.lstrip("/") + (".php" if how == "php-include" else ".html")
            queued = None
            if not dry_run:
                _store(con, row["id"], page, "drafted", verdict["warnings"], False,
                       html=html)
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
                            + (f"It uses your own {include}, so it will look like the "
                               "rest of your site."
                               if how == "php-include" else
                               "The agency could not find a header include on your "
                               "site, so this page is standalone — it will NOT match "
                               "your template until somebody wraps it.")
                            + " It has a marked gap where your photographs go; it is "
                              "not finished until they are in."),
                    "auditor": verdict["warnings"],
                })
            pages.append({"id": row["id"], "title": page.get("title") or row["title"],
                          "page": page_file, "how": how, "words": verdict["words"],
                          "queued": queued, "warnings": verdict["warnings"],
                          "brain": got.get("brain")})

        if pages and not dry_run:
            board.log_decision(
                "content-write",
                reasoning=(f"Wrote {len(pages)} page(s) from briefs SEO's gap list "
                           f"produced. Each passed the Auditor and is queued for his "
                           f"approval; none is on the site."),
                expectation=("A service+area page he approves goes live and starts "
                             "taking enquiries for a phrase his site does not "
                             "currently answer."),
                confidence=0.6,
                evidence={"content_queue_ids": [p["id"] for p in pages],
                          "site_queue_ids": [p["queued"] for p in pages if p["queued"]]})

        con.commit()
        summary = (f"{len(pages)} page(s) written and waiting for your yes"
                   if pages else
                   f"nothing written — {len(held)} held")
        return {"ok": True, "agent": agent, "pages": pages, "held": held,
                "shell": ("your own header include" if include else "standalone"),
                "dry_run": bool(dry_run), "summary": summary}
    finally:
        con.close()


def _store(con, qid, page, status, notes, dry_run, html=""):
    if dry_run:
        return
    body = dict(page)
    if html:
        body["_page_html"] = html
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
                 + (f"  queued #{p['queued']}" if p.get("queued") else "  [dry run]"))
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
