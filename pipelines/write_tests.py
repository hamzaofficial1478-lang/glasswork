"""
write_tests.py — run: python -m pipelines.write_tests

"how the articles will get published?"

They were briefed and nothing on this machine ever turned a brief into words.
`content_queue.status` had a 'drafted' state nothing could reach and a
'published' state nothing could reach either.

The chain is Writer → Auditor → owner → Releaser, and the tests here are about
the joins rather than the writing. A model wrote the prose in the fixtures; what
has to hold whatever it writes is:

    with no model routed, does it invent a page anyway?       no
    does a page with an invented credential get published?    no
    does a page with a price?                                 no
    does a page with a <script> in it?                        no
    does a page reach the site before he says yes?            no
    does a "new page" overwrite one he already has?           no
    when he does say yes, does it actually land?              yes

No network, no model, throwaway cabinet. The Releaser's site calls are faked at
the site_api boundary — the real thing is proved against a real PHP server
separately, and a suite that needs a web server is a suite that gets skipped.
"""
import json
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest import mock

from coordination import auditor, board
from . import content_write as CW, it_release as IR

_fail = []


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


def _fresh_db():
    d = Path(tempfile.mkdtemp())
    db = d / "agency.db"
    con = sqlite3.connect(db)
    con.executescript((Path(__file__).parents[1] / "coordination"
                       / "schema.sql").read_text(encoding="utf-8"))
    con.commit()
    con.close()
    board.DB = db
    board.ensure_schema()
    return db


BRIEF = {
    "title": "Aluminium Pergolas in Sharjah", "url": "/aluminium-pergolas-sharjah",
    "type": "service_page", "service": "pergola", "area": "Sharjah",
    "primary_keyword": "aluminium pergola sharjah",
    "secondary_keywords": ["pergola installation sharjah"],
    "angle": "They want a price and a date.",
    "must_include": ["photos of real finished jobs"],
    "word_count": "450-700", "do_not": ["No stock photography."],
}

FILLER = ("A pergola here has to take the sun and the shamal, so the survey "
          "comes first and the fixing is checked against the wall. ")

GOOD_BODY = (
    "<h1>Aluminium pergolas in Sharjah</h1>"
    '<p>We build and fit aluminium pergolas across Sharjah. Ring '
    '<a href="tel:+97141234567">+971 4 123 4567</a> or send a photo on '
    '<a href="https://wa.me/971509876543">WhatsApp</a>.</p>'
    "<h2>What the job involves</h2><p>" + FILLER * 12 + "</p>"
    "<h2>How quickly we can measure</h2><p>" + FILLER * 12 + "</p>")


def _brain(body=GOOD_BODY, title="Aluminium Pergolas in Sharjah"):
    """
    A stand-in for the Writer's brain.

    **kw because the Writer climbs the load ladder now rather than calling
    call_task directly, and think() takes agent= and reason= as well. The
    return shape is the same either way — the ladder hands back what the
    underlying call handed back, plus which rung answered.
    """
    def brain(task, messages, max_tokens=512, timeout=0, **kw):
        return {"ok": True, "used": "stand-in", "rung": "own",
                "output": json.dumps({
                    "title": title, "meta": "Aluminium pergolas across Sharjah.",
                    "h1": "Aluminium pergolas in Sharjah", "html": body,
                    "note": "led with the survey time"})}
    return brain


def _queue_a_brief(db, brief=None):
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO content_queue(title,type,brief,status,channel,created_at)"
                  " VALUES(?,?,?,'briefed','website',strftime('%s','now'))",
                  ((brief or BRIEF)["title"], "service_page",
                   json.dumps(brief or BRIEF)))


# One of his own pages, the way his site is built: a head of its own, his
# header and footer pulled in, a banner and sections in between.
HIS_PAGE = """<?php $page_title = 'Glass Work in Dubai | World Deal'; ?>
<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Glass Work in Dubai | World Deal</title>
<meta name="description" content="Glass work across Dubai.">
<link rel="canonical" href="https://www.example.ae/glass-work.php">
<link rel="stylesheet" href="assets/css/style.css">
</head><body>
<?php include 'includes/header.php'; ?>
<div class="page-banner"><h1>Glass Work</h1></div>
<section class="service"><h2>Frameless glass</h2><p>Our glass work across Dubai.</p></section>
<section class="gallery"><img src="assets/img/glass1.jpg" alt="glass"></section>
<?php include 'includes/footer.php'; ?>
<script src="assets/js/main.js"></script>
</body></html>
"""


def _theme():
    from . import site_api
    return {"ok": True, "page": "glass-work.php", "php": True, **site_api._frame_of(HIS_PAGE)}


def _write(body=GOOD_BODY, theme=None, **kw):
    """Run the Writer with a stand-in brain and no site to read."""
    import providers
    # Both seams: the Writer asks the LADDER whether anything could write
    # (own model or a shelf spare) before it starts, then calls think() to do
    # it. Patching only resolve_task left the pre-check reading the real
    # config, which does not exist in a test run.
    with mock.patch.object(providers, "resolve_task",
                           lambda t: {"ok": True, "task": t, "route": {}}), \
         mock.patch.object(providers.ladder, "who_could_answer",
                           lambda s: {"ok": True, "own": True, "own_error": "",
                                      "spare": 0, "why": ""}), \
         mock.patch.object(providers, "think", _brain(body)), \
         mock.patch.object(CW, "_theme_of_his_site", lambda: theme or {}), \
         mock.patch("pipelines.post_images.for_post",
                    kw.pop("images", None) or (lambda brief, site, **_: [])), \
         mock.patch.dict("os.environ", {
             "BUSINESS_NAME": "World Deal Technical Services",
             "BUSINESS_PHONE": "+971 4 123 4567",
             "BUSINESS_WHATSAPP": "+971 50 987 6543",
             "SITE_URL": "https://www.example.ae"}, clear=False):
        return CW.run(**kw)


# ---------------------------------------------------------------------------

def test_no_brain_means_no_page():
    """
    The important refusal. A template with the keyword slotted in four times is
    the page every other glazier in Dubai already has; putting one on his
    domain would cost him rather than earn.
    """
    print("\nWith Claude unable to think for Content:")
    db = _fresh_db()
    _queue_a_brief(db)
    import providers
    with mock.patch.object(providers.ladder, "who_could_answer",
                           lambda s: {"ok": False, "own_error": "Claude Code is not installed"}):
        out = CW.run()
    check("it does not write", out["pages"] if out.get("pages") else [], [])
    check("and it is not called a success", out["ok"], False)
    check("it says what is missing", "Content cannot think right now" in out["error"], True)
    check("and why", "not installed" in out["error"], True)
    check("and that it deliberately did not fake one",
          "would cost him" in out["error"], True)
    with sqlite3.connect(db) as c:
        check("the brief is untouched",
              c.execute("SELECT status FROM content_queue").fetchone()[0], "briefed")


def test_a_written_page_is_queued_and_not_published():
    print("\nA good page, written:")
    db = _fresh_db()
    _queue_a_brief(db)
    out = _write()
    check("one page", len(out["pages"]), 1)
    p = out["pages"][0]
    check("named from the brief's url", p["page"], "aluminium-pergolas-sharjah.html")
    check("and it is queued, not applied", bool(p["queued"]), True)

    with sqlite3.connect(db) as c:
        c.row_factory = sqlite3.Row
        q = dict(c.execute("SELECT kind,status FROM site_queue").fetchone())
        cq = c.execute("SELECT status FROM content_queue").fetchone()[0]
    check("the queue row is a new_page", q["kind"], "new_page")
    check("waiting for him", q["status"], "pending")
    check("and the brief says drafted, not published", cq, "drafted")


def test_it_uses_his_own_header_when_there_is_one():
    """
    "this link that program post it is plain no any layout no any design ...
     the website pages are .php not .html"
    """
    print("\nSo it looks like the rest of his site — built in the frame of his own page:")
    db = _fresh_db()
    _queue_a_brief(db)
    out = _write(theme=_theme())
    p = out["pages"][0]
    check("a php page, not a standalone html one", p["page"].endswith(".php"), True)
    check("and it says which route it took", p["how"], "his-theme")

    with sqlite3.connect(db) as c:
        detail = json.loads(c.execute("SELECT detail FROM site_queue").fetchone()[0])
    html = detail["change"]["html"]
    check("his head and styles come with it", "assets/css/style.css" in html, True)
    check("it pulls his header in", "include 'includes/header.php'" in html, True)
    check("and his footer and scripts", "include 'includes/footer.php'" in html
          and "assets/js/main.js" in html, True)
    check("the page it was copied from stays out of it",
          ("Frameless glass" in html, "page-banner" in html), (False, False))
    check("it carries its own title, not his page's",
          ("<title>Aluminium Pergolas in Sharjah</title>" in html,
           "Glass Work in Dubai" in html), (True, False))
    check("and its own description", "Aluminium pergolas across Sharjah." in html, True)
    check("and does not claim his page's address", "canonical" in html, False)
    check("the new content is in the middle", html.index("includes/header.php")
          < html.index("What the job involves") < html.index("includes/footer.php"), True)
    check("and the approval says whose layout it wears",
          "frame of your own glass-work.php" in detail["why"], True)

    print("\n...and says so plainly when there is not one:")
    db = _fresh_db()
    _queue_a_brief(db)
    out = _write(theme={})
    with sqlite3.connect(db) as c:
        detail = json.loads(c.execute("SELECT detail FROM site_queue").fetchone()[0])
    check("standalone", out["pages"][0]["how"], "standalone")
    check("and he is warned before he approves it",
          "will NOT match your template" in detail["why"], True)


def test_the_photo_slot_is_left_empty_on_purpose():
    print("\nHis own photographs, or a marked gap — never somebody else's:")
    db = _fresh_db()
    _queue_a_brief(db)
    _write()
    with sqlite3.connect(db) as c:
        html = json.loads(c.execute("SELECT detail FROM site_queue")
                          .fetchone()[0])["change"]["html"]
    check("the slot is there", CW.PHOTO_SLOT in html, True)
    check("and the Auditor says the page is not finished without them",
          any("photograph" in w for w in
              auditor.check_page(html, BRIEF)["warnings"]), True)

    print("\n...and a photograph from somebody else's site is refused outright:")
    stolen = GOOD_BODY + '<img src="https://someoneelse.com/pergola.jpg">'
    v = auditor.check_page(stolen, BRIEF, site_host="example.ae")
    check("held", v["pass"], False)
    check("and it says why",
          any("his own finished work" in b for b in v["blocking"]), True)


def test_the_auditor_stops_the_four_things_that_matter():
    print("\nWhat never reaches his website:")
    db = _fresh_db()
    for label, body, needle in [
        ("an invented credential",
         GOOD_BODY + "<p>We are ISO 9001 certified.</p>", "ISO 9001"),
        ("a price",
         GOOD_BODY + "<p>Pergolas from AED 4,500.</p>", "price or a concession"),
        ("code",
         GOOD_BODY + '<script src="x.js"></script>', "code, not content"),
        ("a placeholder left in",
         GOOD_BODY + "<p>Hello [NAME].</p>", "placeholder"),
    ]:
        _fresh_db()
        _queue_a_brief(board.DB)
        out = _write(body=body)
        check(f"{label} — nothing queued", out["pages"], [])
        check(f"{label} — held with the reason",
              any(needle in h["why"] for h in out["held"]), True)
        with sqlite3.connect(board.DB) as c:
            st = c.execute("SELECT status FROM content_queue").fetchone()[0]
            n = c.execute("SELECT count(*) FROM site_queue").fetchone()[0]
        check(f"{label} — the draft is kept for review", st, "in_review")
        check(f"{label} — and never reached the approval queue", n, 0)


def test_a_thin_page_is_not_published_either():
    print("\nA stub on his own domain competes with his real pages:")
    _fresh_db()
    _queue_a_brief(board.DB)
    out = _write(body='<h1>Pergolas</h1><p>Call <a href="tel:+97141234567">us</a>.</p>')
    check("held", out["pages"], [])
    check("for being a stub",
          any("stub" in h["why"] or "words" in h["why"] for h in out["held"]), True)


def test_nothing_reaches_the_site_before_he_says_yes():
    print("\nThe Releaser will not touch a pending page:")
    db = _fresh_db()
    _queue_a_brief(db)
    _write()
    with mock.patch.object(IR.site_api, "why_not_ready", lambda: ""), \
         mock.patch.object(IR.site_api, "put_page") as put:
        r = IR.run(skip_tests=True)
    check("it wrote nothing", put.called, False)
    check("and told him it is his to decide",
          "waiting for your yes" in (r.get("error") or ""), True)


def test_when_he_says_yes_it_lands_and_the_loop_closes():
    print("\nHe approves it:")
    db = _fresh_db()
    _queue_a_brief(db)
    _write(theme=_theme())
    with sqlite3.connect(db) as c:
        qid = c.execute("SELECT id FROM site_queue").fetchone()[0]
    board.decide_approval("site_change", qid, "approved")

    # Faked at the HTTP boundary rather than at put_page, deliberately: the
    # change log is written inside put_page, and mocking put_page out would
    # test the Releaser while skipping the very record this is asserting on.
    wrote = {}

    def fake_call(path, method="GET", body=None, timeout=None, s=None):
        if path == "/pages" and method == "POST":
            wrote["page"] = body["page"]
            wrote["html"] = body["html"]
            return {"ok": True, "page": body["page"], "bytes": len(body["html"])}
        if path == "/pages" and method == "GET":
            name = (body or {}).get("page") or ""
            if wrote and name == wrote["page"]:
                return {"ok": True, "html": wrote["html"]}
            return {"ok": False, "error": "404"}
        return {"ok": True}

    def read_page(page, s=None):
        if wrote and page == wrote["page"]:
            return {"ok": True, "html": wrote["html"]}
        return {"ok": False, "error": "no such page"}

    with mock.patch.object(IR.site_api, "why_not_ready", lambda s=None: ""), \
         mock.patch.object(IR.site_api, "_may_write", lambda a: ""), \
         mock.patch.object(IR.site_api, "read_page", read_page), \
         mock.patch.object(IR.site_api, "call", fake_call):
        r = IR.run(skip_tests=True)

    check("one change applied", r["applied"], 1)
    check("and it is the page", wrote.get("page"), "aluminium-pergolas-sharjah.php")
    with sqlite3.connect(db) as c:
        check("the queue row is applied",
              c.execute("SELECT status FROM site_queue").fetchone()[0], "applied")
        check("and the brief is finally published",
              c.execute("SELECT status FROM content_queue").fetchone()[0], "published")
        log = c.execute("SELECT action, summary FROM site_changes"
                        " ORDER BY id DESC LIMIT 1").fetchone()
    check("the change log calls it a new page, not a rewrite", log[0], "new_page")
    check("in his language", "published a new page" in log[1], True)


def test_a_new_page_never_overwrites_one_he_has():
    """
    The one that would actually hurt. A brief whose url happens to match a page
    he already has must not silently replace it — there is no backup of a page
    the agency has never seen, and losing his home page is not recoverable from
    here.
    """
    print("\nA 'new page' aimed at a page that already exists:")
    db = _fresh_db()
    qid = board.queue_site_change("content-writer", "new_page", {
        "change": {"page": "index.php", "html": "<h1>Replaced</h1>" + ("x " * 300)},
        "why": "a new page named like his home page"})
    board.decide_approval("site_change", qid, "approved")

    with mock.patch.object(IR.site_api, "why_not_ready", lambda: ""), \
         mock.patch.object(IR.site_api, "read_page",
                           lambda p, s=None: {"ok": True, "html": "<h1>His real home page</h1>"}), \
         mock.patch.object(IR.site_api, "put_page") as put:
        r = IR.run(skip_tests=True)
    check("nothing was written", put.called, False)
    check("it is recorded as failed, not quietly dropped", r["failed"], 1)
    with sqlite3.connect(db) as c:
        note = c.execute("SELECT note FROM site_queue WHERE id=?", (qid,)).fetchone()[0]
    check("and the reason is the right one", "already exists" in note, True)


def test_the_queue_refuses_a_payload_it_would_have_to_cut():
    """
    queue_site_change used to do json.dumps(detail)[:2000]. Fine while every
    change was a sentence and a page name; a trap the moment one was a page.
    2000 characters cuts a page mid-tag, the cut leaves invalid JSON, and the
    Releaser then reports HIS approval as malformed.
    """
    print("\nA change too big for the queue:")
    _fresh_db()
    huge = {"change": {"page": "x.html", "html": "y" * (board.MAX_CHANGE_BYTES + 10)}}
    try:
        board.queue_site_change("content-writer", "new_page", huge)
        check("it refused", False, True)
    except ValueError as e:
        check("it refused rather than truncating", "Nothing was queued" in str(e), True)
    with sqlite3.connect(board.DB) as c:
        check("and stored nothing",
              c.execute("SELECT count(*) FROM site_queue").fetchone()[0], 0)

    print("\n...but a real page fits comfortably:")
    ok = board.queue_site_change("content-writer", "new_page",
                                 {"change": {"page": "x.html", "html": "y" * 20000}})
    check("queued", bool(ok), True)


def test_it_only_writes_briefs_that_are_ready():
    print("\nA brief held for another sector is not written:")
    db = _fresh_db()
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO content_queue(title,type,brief,status,channel,created_at)"
                  " VALUES('Held','service_page',?,'blocked_on_seo','website',1)",
                  (json.dumps(BRIEF),))
    out = _write()
    check("nothing written", out.get("pages", []), [])
    check("and it says who it is waiting on",
          "held for another sector" in (out.get("error") or ""), True)
    check("which is not a failure", out.get("idle"), True)


def test_running_out_of_time_banks_what_was_written():
    print("\nTimed out after 420s, six pages still queued, nothing saved:")
    db = _fresh_db()
    for i in range(4):
        _queue_a_brief(db, {**BRIEF, "title": f"Page {i}"})
    real = CW._deadline
    try:
        # Already out of time before the first page is started.
        CW._deadline = lambda now=None: __import__("time").time() - 1
        out = _write()
        check("the pass did not crash", isinstance(out, dict), True)
        check("and it is not reported as a failure", out.get("ok"), True)
        held = out.get("held") or []
        # Content writes ONE page a pass by design — the MD's no-drafting-spree
        # gate — so one is what is in hand when the clock runs out.
        check("the unwritten one is held, not lost", len(held), 1)
        check("it says the time ran out",
              "ran out of time" in (held[0].get("why") or ""), True)
        check("and that it is still queued for next run",
              "still queued" in (held[0].get("why") or ""), True)
        with sqlite3.connect(db) as c:
            left = c.execute("SELECT count(*) FROM content_queue"
                             " WHERE status='briefed'").fetchone()[0]
        check("the queue still holds all four", left, 4)
    finally:
        CW._deadline = real


def test_no_deadline_means_the_old_behaviour():
    print("\nAnd with no budget to read, nothing changes:")
    real = CW._deadline
    try:
        CW._deadline = lambda now=None: None
        db = _fresh_db()
        _queue_a_brief(db)
        out = _write()
        check("it still writes", len(out.get("pages") or []), 1)
    finally:
        CW._deadline = real


def test_a_post_is_full_specific_and_has_pictures():
    """
    "i ask for blog posts but the program is writing like 150 to 160 words of
     too generic posts with out any images any graphics why? ... i want the
     posts to be written as real human written"
    """
    print("\nA thin or machine-sounding draft is sent back, told why:")
    thin = {"html": "<h1>Glass</h1><p>" + "Word " * 150 + "</p>"}
    probs = CW.voice_problems(thin, "blog")
    check("150 words is too short for an article", any(p.startswith("it is 1") for p in probs), True)
    robot = {"html": "<p>In today's fast-paced world, look no further. Let us delve in.</p>"
                     + "<p>" + FILLER * 60 + "</p>"}
    tells = " ".join(CW.voice_problems(robot, "service_page"))
    check("the phrases that read as machine-written are named",
          all(t in tells for t in ("in today's", "look no further", "delve")), True)

    print("\n...and the second, fuller draft is the one kept:")
    db = _fresh_db()
    _queue_a_brief(db)
    long_body = GOOD_BODY + "<h2>What to ask before you buy</h2><p>" + FILLER * 40 + "</p>"
    calls = []

    def two_drafts(task, messages, max_tokens=512, timeout=0, **kw):
        calls.append(messages[-1]["content"])
        body = GOOD_BODY if len(calls) == 1 else long_body
        return {"ok": True, "used": "stand-in", "rung": "own", "output": json.dumps({
            "title": "Choosing an aluminium pergola in Sharjah", "meta": "m",
            "h1": "Choosing an aluminium pergola", "html": body, "note": "n"})}

    class _Job:
        text = "owner's order: write 1 post and publish on website"
        def wants(self, *w):
            return any(x in self.text for x in w)

    import providers
    pics = [{"src": "/assets/img/pages/pergola.jpg", "alt": "Pergola we fitted", "own": True},
            {"src": "https://images.pexels.com/photos/1/x.jpeg", "own": False,
             "alt": "Illustrative photo: pergola", "credit": "Illustrative photo — Pexels"}]
    with mock.patch.object(providers.ladder, "who_could_answer",
                           lambda s: {"ok": True, "own": True, "own_error": "", "spare": 0, "why": ""}), \
         mock.patch.object(providers, "think", two_drafts), \
         mock.patch.object(CW, "_theme_of_his_site", lambda: {}), \
         mock.patch("pipelines.post_images.for_post", lambda brief, site, **_: pics), \
         mock.patch.dict("os.environ", {"BUSINESS_PHONE": "+971 4 123 4567",
                                        "BUSINESS_WHATSAPP": "+971 50 987 6543",
                                        "SITE_URL": "https://www.example.ae"}, clear=False):
        out = CW._write("content-writer", False, None, 1, _Job())
    check("his 'post' order is written as an article", out["pages"][0]["kind"], "blog")
    check("it went back once, with the reason", len(calls), 2)
    check("the revision was told it was too short", "it is " in calls[1] and "words" in calls[1], True)
    with sqlite3.connect(db) as c:
        n_site = c.execute("SELECT count(*) FROM site_queue").fetchone()[0]
        typ, status, draft = c.execute("SELECT type, status, draft FROM content_queue").fetchone()
    blog = json.loads(draft)["_for_blog"]
    html = blog["html"]
    check("the fuller second draft is the one kept", "What to ask before you buy" in html, True)
    check("NOT put on his site as a page of its own", n_site, 0)
    check("but ready for his blog", (status, out["pages"][0].get("for_blog")), ("for_blog", True))
    check("and recorded as an article", typ, "blog")
    check("with the link name his blog uses",
          blog["slug"], "choosing-an-aluminium-pergola-in-sharjah")
    check("and the address it will have there", blog["url"],
          "https://www.example.ae/blog-details.php?slug=choosing-an-aluminium-pergola-in-sharjah")
    check("his post page prints the title, so the article has no second one",
          "<h1" in html, False)
    check("his own photograph is in it, at an address that works from his backend",
          'src="https://www.example.ae/assets/img/pages/pergola.jpg"' in html, True)
    check("and listed to upload", blog["images"][0]["src"],
          "https://www.example.ae/assets/img/pages/pergola.jpg")
    check("the stock one is labelled as an illustration",
          "Illustrative photo — Pexels" in html, True)
    check("no 'photos go here' gap when his own are in",
          CW.PHOTO_SLOT in html, False)
    check("a plain-text copy for a plain box", ("What to ask before you buy" in blog["text"],
                                               "<" in blog["text"]), (True, False))

    print("\nThe Auditor lets a labelled stock photo through, never an unlabelled one:")
    labelled = GOOD_BODY + ('<figure><img src="https://images.pexels.com/p/1.jpeg" alt="a">'
                            '<figcaption>Illustrative photo — Pexels</figcaption></figure>')
    check("labelled: fine", any("photograph" in b for b in
                                auditor.check_page(labelled, BRIEF, site_host="example.ae")["blocking"]),
          False)
    bare = GOOD_BODY + '<img src="https://images.pexels.com/p/1.jpeg" alt="our work">'
    check("unlabelled: refused", any("not labelled" in b for b in
                                     auditor.check_page(bare, BRIEF, site_host="example.ae")["blocking"]),
          True)

    print("\nHis own photographs are found by what they show:")
    from pipelines import post_images
    home = ('<a href="/services.php">Services</a>'
            '<img src="/assets/img/logo.png" alt="logo">'
            '<img src="/assets/img/pages/pergola-villa.jpg" alt="Aluminium pergola">')
    got = post_images.own_photos("https://www.example.ae", post_images._words("aluminium pergola"),
                                 fetch=lambda u, timeout=15: home)
    check("the pergola, not the logo", [g["src"] for g in got], ["/assets/img/pages/pergola-villa.jpg"])


def test_his_theme_is_read_off_a_page_his_site_links_to():
    """
    "the website pages are .php not .html that home.html page is not live the
     home.php page is live and all .php pages are connected to website"
    """
    print("\nHis layout is read off one of his live .php pages:")
    from . import site_api
    inline = ("<!doctype html><html><head><title>About us</title>"
              '<link rel="stylesheet" href="css/site.css"></head><body>'
              '<header class="top"><nav><a href="index.php">Home</a></nav></header>'
              "<section><h1>About</h1><p>" + "Ten years of glass. " * 10 + "</p></section>"
              "<footer>World Deal · Dubai</footer><script src=\"js/app.js\"></script></body></html>")
    f = site_api._frame_of(inline)
    check("a page with its header written inline: the header is kept",
          ('<nav><a href="index.php">Home</a></nav></header>' in f.get("top", ""),
           "<footer>" in f.get("bottom", "")), (True, True))
    check("and its own content is not", "Ten years of glass" in f.get("top", "") + f.get("bottom", ""),
          False)
    check("a page that shows no frame gives none", site_api._frame_of("<p>" + "x " * 200 + "</p>"), {})

    listing = ["home.html", "default.php", "index.php", "about.php", "glass-work.php",
               "blog-glass-railing-uae-cost.html", "agency-api.php"]
    check("the candidates: a service page first, never home.html or default.php",
          site_api._theme_candidates(listing), ["glass-work.php", "about.php", "index.php"])
    read = []

    def read_page(name, s=None):
        read.append(name)
        return {"ok": True, "html": HIS_PAGE} if name == "glass-work.php" else \
            {"ok": False, "error": "no such page"}
    with mock.patch.object(site_api, "why_cannot_read", lambda s=None: ""), \
         mock.patch.object(site_api, "settings", lambda env=None: {"site": "https://www.example.ae"}), \
         mock.patch.object(site_api, "live_pages", lambda s=None: listing), \
         mock.patch.object(site_api, "read_page", read_page):
        got = site_api.theme_of_his_site()
    check("it takes the frame of his service page", (got["ok"], got["page"]),
          (True, "glass-work.php"))
    check("after reading just that one page", read, ["glass-work.php"])


def test_old_blog_pages_move_to_his_blog_and_the_plain_copy_comes_down():
    """
    "the post it wrote was not even showing on website and it was directly
     uploaded with .html format while the website have the theme of showing
     the blog and i had build the BAckend from where the blogs get uploaded"
    """
    print("\nPosts queued as pages before the change go to his blog instead:")
    from coordination import review
    db = _fresh_db()
    body = GOOD_BODY + '<figure><img src="/assets/img/railing.jpg" alt="Railing we fitted"></figure>'
    page_html = CW._standalone({"title": "Glass railing cost in the UAE", "meta": "What it costs.",
                                "html": body}, BRIEF)
    with sqlite3.connect(db) as c:
        for i, st in ((1, "applied"), (2, "pending")):
            c.execute("INSERT INTO content_queue(id,title,type,brief,draft,status,created_at)"
                      " VALUES(?,?,?,?,?,?,1)",
                      (i, "Railing", "blog", json.dumps(BRIEF),
                       json.dumps({"title": f"Glass railing cost in the UAE {i}",
                                   "meta": "What it costs.", "html": body,
                                   "_page_html": page_html}),
                       "published" if st == "applied" else "drafted"))
            c.execute("INSERT INTO site_queue(agent,kind,detail,status,queued_at)"
                      " VALUES('content-writer','new_page',?,?,1)",
                      (json.dumps({"change": {"page": f"blog-glass-railing-{i}.html",
                                              "html": page_html, "content_id": i},
                                   "title": "Railing"}), st))
    with mock.patch.object(review, "_site", lambda: "https://www.example.ae"):
        pages = review.build()["pages"]
        check("both are ready for his blog", sorted(x["id"] for x in pages["for_blog"]), [1, 2])
        check("the one still waiting is no longer offered as a page", pages["waiting"], [])
        with sqlite3.connect(db) as c:
            note = c.execute("SELECT status, note FROM site_queue WHERE id=2").fetchone()
        check("  and says where it went", (note[0], "Ready for your blog" in note[1]),
              ("rejected", True))
        live = next(x for x in pages["for_blog"] if x["id"] == 1)
        check("the one already up is listed with its plain copy",
              live["plain_page"]["page"], "blog-glass-railing-1.html")
        check("its picture works from his backend",
              "https://www.example.ae/assets/img/railing.jpg" in live["html"], True)
        check("moved once, not on every look", review._move_old_blog_pages(), 0)

        print("\n...Take it down points the plain copy at his blog, through IT:")
        r = review.act("take_down", [1])
        check("done", r["ok"], True)
        with sqlite3.connect(db) as c:
            c.row_factory = sqlite3.Row
            row = dict(c.execute("SELECT * FROM site_queue WHERE id=?", (r["change"],)).fetchone())
        plan = IR.plan_of(row)
        check("an approved change for the Releaser — his press is his yes",
              (row["status"], plan["do"], plan["page"]),
              ("approved", "page", "blog-glass-railing-1.html"))
        check("that sends visitors to his blog and tells Google to drop it",
              ("https://www.example.ae/blog.php" in plan["html"], "noindex" in plan["html"]),
              (True, True))
        check("pressed twice is still one change",
              review.act("take_down", [1]).get("change"), None)

        print("\n...and he says when a post is on his blog:")
        r = review.act("blog_posted", [2], {"url": "https://www.example.ae/blog-details.php?slug=x"})
        check("marked", r["ok"], True)
        pages = review.build()["pages"]
        check("off the waiting list", [x["id"] for x in pages["for_blog"]], [1])
        check("and on the 'on your blog' list with his address",
              [(x["id"], x["url"]) for x in pages["on_blog"]],
              [(2, "https://www.example.ae/blog-details.php?slug=x")])
        check("an address that is not one is refused",
              review.act("blog_posted", [1], {"url": "my blog"})["ok"], False)


def main():
    real = board.DB
    _fresh_db()
    try:
        test_no_brain_means_no_page()
        test_a_written_page_is_queued_and_not_published()
        test_running_out_of_time_banks_what_was_written()
        test_no_deadline_means_the_old_behaviour()
        test_it_uses_his_own_header_when_there_is_one()
        test_the_photo_slot_is_left_empty_on_purpose()
        test_the_auditor_stops_the_four_things_that_matter()
        test_a_thin_page_is_not_published_either()
        test_nothing_reaches_the_site_before_he_says_yes()
        test_when_he_says_yes_it_lands_and_the_loop_closes()
        test_a_new_page_never_overwrites_one_he_has()
        test_the_queue_refuses_a_payload_it_would_have_to_cut()
        test_it_only_writes_briefs_that_are_ready()
        test_a_post_is_full_specific_and_has_pictures()
        test_his_theme_is_read_off_a_page_his_site_links_to()
        test_old_blog_pages_move_to_his_blog_and_the_plain_copy_comes_down()
    finally:
        board.DB = real

    print()
    if _fail:
        print(f"{len(_fail)} FAILED: {_fail}")
        sys.exit(1)
    print("all publishing tests passed")


if __name__ == "__main__":
    main()
