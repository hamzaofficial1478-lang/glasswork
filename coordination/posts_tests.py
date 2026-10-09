"""
posts_tests.py — run: python -m coordination.posts_tests

"in leads section ... we cannot read the sent mails ... in content section there
 is no delete button ... we cannot select that on which topic today user want to
 write the post ... make two buttons 1 of delete from website ... and 2nd of edit
 ... that will show the seo score of each post"

What is protected here:
  - a sent mail can be read in full — subject, words and the designed version —
    and one deleted from the list says its words were wiped;
  - today's topic comes from SEO's own findings, best first; a typed topic must
    name a service the business sells; choosing makes a brief ready to write;
  - a post can be deleted, but one on the website must be taken off first;
  - the editor's HTML is cut to an allow-list: no scripts, no frames, no event
    handlers, no javascript: links — while bold, links, colour, size and font stay;
  - a picture pasted into the editor waits on this computer; only the IT
    Releaser carries it to the website, with the post;
  - an edit the Auditor stops is kept and goes nowhere; an edit to a live post
    that passes is handed to the Releaser, never written directly;
  - every post is scored: SEO against SEO's plan, copying against the other
    posts and the pages Google ranks, and whether it reads like a person — and
    a page's fingerprint holds numbers, never its text;
  - pictures reach a WordPress site through its Media Library, and only from the
    Releaser.

Nothing here reaches the internet: the website and WordPress are stand-ins.
"""
import base64
import json
import os
import sqlite3
import struct
import sys
import tempfile
import threading
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("AGENCY_DB", str(Path(tempfile.mkdtemp(prefix="posts-db-")) / "agency.db"))
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost,::1"

from coordination import board, posts, review  # noqa: E402
from pipelines import blog_backend, post_quality, site_api, wp_bridge  # noqa: E402

_fail = []


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


class _Cabinet:
    """A cabinet of its own for each test."""

    def __enter__(self):
        self.saved = board.DB
        self.dir = Path(tempfile.mkdtemp(prefix="posts-"))
        board.DB = self.dir / "agency.db"
        c = sqlite3.connect(board.DB)
        c.executescript((ROOT / "coordination" / "schema.sql").read_text(encoding="utf-8"))
        c.commit()
        c.close()
        board.ensure_schema()
        return self

    def __exit__(self, *a):
        board.DB = self.saved

    def run(self, q, args=()):
        c = sqlite3.connect(board.DB)
        cur = c.execute(q, args)
        c.commit()
        rid = cur.lastrowid
        c.close()
        return rid


def _png():
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"\x00" + b"\xff\x00\x00\xff" * 2 + b"\x00" + b"\xff\x00\x00\xff" * 2
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


BODY = ("<p>Frameless shower glass is fitted in a morning. Call 04 123 4567 or <a href='/contact'>ask for a visit</a>.</p>"
        + "".join(f"<h2>{h}</h2><p>{t}</p>" for h, t in (
            ("How long does fitting take?", "Most screens take a morning. A curved or very tall screen can take a day, and we say so before we start."),
            ("Which glass thickness is right?", "Eight millimetre suits most showers. Ten feels heavier and suits a wide panel with no frame at all."),
            ("Looking after it", "Squeegee it after use. Once a month, wipe the seals with warm soapy water and dry them."),
            ("Hinged or sliding?", "A hinged door opens wide and suits a square shower. A sliding door saves space where a door "
             "cannot swing, and the rollers need a wipe now and then. We bring samples of both, so you can see the "
             "difference in your own bathroom before anything is made, and we measure twice before the glass is cut."),
            ("What it costs to run", "Nothing. There is no power and no filter to change. The glass is toughened, so it "
             "takes the knocks of a family bathroom, and the seals are the only part that wears. We replace them in "
             "minutes when they do, and we tell you when they are due rather than waiting for a leak."))))
PACK = {"primary": "frameless shower glass", "secondary": ["glass thickness", "shower screen"],
        "questions": ["how long does fitting take"], "target_words": 120}


def _post(cab, status="for_blog", live=False, html=BODY, title="Frameless shower glass in Dubai"):
    blog = {"title": title, "meta": "Frameless shower glass fitted across Dubai.", "html": html, "slug": "frameless"}
    if live:
        blog.update(backend={"id": "42", "table": "blogs"}, posted_url="https://example.ae/blog/frameless")
    return cab.run("INSERT INTO content_queue(title,type,brief,draft,status,created_at) VALUES(?,?,?,?,?,0)",
                   (title, "blog", json.dumps({"primary_keyword": "frameless shower glass", "seo_pack": PACK}),
                    json.dumps({"_for_blog": blog}), status))


def test_reading_a_sent_mail():
    print("\nA sent mail can be read in full:")
    with _Cabinet() as cab:
        lid = cab.run("INSERT INTO leads(company, email, area, mailed_at) VALUES('Acme FM','info@acme.example','JLT',100)")
        cab.run("INSERT INTO outreach_queue(lead_id, company, to_email, subject, body, html, status, sent_at)"
                " VALUES(?,?,?,?,?,?, 'sent', 100)", (lid, "Acme FM", "info@acme.example", "Glass that lasts",
                                                     "Hello Acme,\nWe fit glass.", "<p>Hello Acme</p>"))
        got = review.sent_mail(lid)
        check("its subject, words and designed version", (got["ok"], got["subject"], got["body"], got["html"]),
              (True, "Glass that lasts", "Hello Acme,\nWe fit glass.", "<p>Hello Acme</p>"))
        check("  who it went to", (got["company"], got["to"]), ("Acme FM", "info@acme.example"))
        review.sent_delete([lid])
        wiped = review.sent_mail(lid)
        check("one deleted from the list says its words were wiped", (wiped["wiped"], wiped["body"]), (True, ""))
        check("a business never written to has no sent mail", review.sent_mail(9999)["ok"], False)


def test_todays_topic():
    print("\nToday's topic comes from SEO, and a typed one must be a service you sell:")
    with _Cabinet() as cab:
        for ph, sc, imp in (("glass railing dubai marina", 9, 60), ("pergola installation dubai", 4, 0)):
            cab.run("INSERT INTO keyword_intel(phrase, area, intent, score, impressions, striking) VALUES(?,?,?,?,?,?)",
                    (ph, "", "buy", sc, imp, 1 if imp else 0))
        t = posts.topics()
        check("SEO's suggestions, best first", [x["primary"] for x in t["topics"]][:2],
              ["glass railing dubai marina", "pergola installation dubai"])
        check("  each says why, from SEO's evidence", "60 times on Google" in t["topics"][0]["why"], True)
        made = posts.choose(key=t["topics"][0]["key"])
        check("choosing one makes a brief ready to write", made["ok"], True)
        row = sqlite3.connect(board.DB).execute("SELECT status, type, brief FROM content_queue WHERE id=?",
                                                (made["id"],)).fetchone()
        brief = json.loads(row[2])
        check("  briefed, as a blog post, chosen by you", (row[0], row[1], brief["chosen_by"]), ("briefed", "blog", "owner"))
        check("  and it leaves SEO's list", any(x["key"] == t["topics"][0]["key"] for x in posts.topics()["topics"]), False)
        own = posts.choose(topic="How long glass railing takes to fit")
        check("a typed topic that names a service is taken", own["ok"], True)
        no = posts.choose(topic="cheap flights to London")
        check("one that names no service you sell is refused, in words", (no["ok"], "sells" in no["error"]), (False, True))
        check("nothing chosen is refused", posts.choose()["ok"], False)


def test_deleting_a_post():
    print("\nA post can be deleted; one on the website is taken off first:")
    with _Cabinet() as cab:
        draft = _post(cab)
        check("a post waiting for you is deleted", posts.delete(draft)["ok"], True)
        st = sqlite3.connect(board.DB).execute("SELECT status FROM content_queue WHERE id=?", (draft,)).fetchone()[0]
        check("  and is not offered again", st, "rejected")
        live = _post(cab, status="published", live=True)
        r = posts.delete(live)
        check("a post on the website is not deleted from here", (r["ok"], "website first" in r["error"]), (False, True))


def test_the_editor_is_cleaned():
    print("\nThe editor's HTML keeps formatting and loses anything that could run:")
    dirty = ('<h1>Big</h1><p onclick="steal()">Hi <script>alert(1)</script><b>bold</b> <u>under</u> '
             '<a href="javascript:alert(1)">bad</a> <a href="https://example.ae" target="_blank">good</a> '
             '<span style="color:#1f5f66;font-size:20px;font-family:Georgia;background:url(x)">styled</span>'
             '<img src="javascript:x"><img src="https://example.ae/a.jpg" onerror="x()" alt="A"></p>'
             '<iframe src="https://evil.example"></iframe><style>body{display:none}</style>')
    out = posts.clean_html(dirty)
    for gone in ("<script", "onclick", "onerror", "javascript:", "<iframe", "<style", "url(", "<h1"):
        check(f"no {gone}", gone in out, False)
    for kept in ("<b>bold</b>", "<u>under</u>", 'href="https://example.ae"', 'rel="noopener"',
                 "color: #1f5f66", "font-size: 20px", "font-family: georgia", '<img src="https://example.ae/a.jpg" alt="A">',
                 "<h2>Big</h2>"):
        check(f"keeps {kept}", kept.lower() in out.lower(), True)


def test_editing_a_post():
    print("\nAn edit is kept, checked by the Auditor, and a live post goes to the Releaser:")
    with _Cabinet() as cab:
        draft = _post(cab)
        got = posts.get(draft)
        check("the editor opens the post with its score", (got["ok"], got["report"]["seo"]["score"] is not None), (True, True))
        img = "data:image/png;base64," + base64.b64encode(_png()).decode()
        saved = posts.save(draft, got["title"], got["meta"], got["html"] + f'<p><img src="{img}" alt="A screen"></p>')
        check("an edit with a picture is saved", saved["ok"], True)
        stored = json.loads(sqlite3.connect(board.DB).execute("SELECT draft FROM content_queue WHERE id=?",
                                                              (draft,)).fetchone()[0])["_for_blog"]["html"]
        check("  the picture waits on this computer, not in the post", (posts.PENDING in stored, "base64" in stored), (True, False))
        check("  one file, kept beside the cabinet", len(list(posts.pending_dir().iterdir())), 1)
        check("  shown back to the editor through the office", posts.PREVIEW in posts.get(draft)["html"], True)
        bad = posts.save(draft, got["title"], got["meta"], "<p>AED 450 a metre, call us.</p>")
        check("an edit the Auditor stops is kept, and goes nowhere", (bad["ok"], bad["saved"], bad["published"]), (True, True, False))
        check("  and it says why", "Auditor stopped it" in bad["head"], True)
        live = _post(cab, status="published", live=True)
        lv = posts.get(live)
        ok = posts.save(live, lv["title"], lv["meta"], lv["html"] + "<p>We also fit sliding screens.</p>")
        change = sqlite3.connect(board.DB).execute(
            "SELECT kind, status, agent FROM site_queue ORDER BY id DESC LIMIT 1").fetchone()
        check("an edit to a live post is handed to the Releaser, tests first",
              (ok["ok"], change), (True, ("blog_repost", "approved", "it-releaser")))
        check("an empty post is refused", posts.save(draft, "T", "", "<p> </p>")["ok"], False)
        check("so is one with no title", posts.save(draft, "", "", "<p>x</p>")["ok"], False)


def test_the_releaser_carries_the_pictures():
    print("\nOnly the Releaser carries the editor's pictures to the website, with the post:")
    with _Cabinet() as cab:
        draft = _post(cab)
        img = "data:image/png;base64," + base64.b64encode(_png()).decode()
        posts.save(draft, "Frameless shower glass in Dubai", "Fitted across Dubai.", BODY + f'<p><img src="{img}" alt="x"></p>')
        d, blog, _st = blog_backend._load(draft)
        sent = []
        real = site_api.upload_picture
        site_api.upload_picture = lambda data, name="", agent="", s=None: (
            sent.append((len(base64.b64decode(data)), agent)) or {"ok": True, "url": "https://example.ae/agency-pictures/x.png"})
        try:
            stopped = blog_backend._upload_pending(draft, d, blog, site_api.AGENT)
        finally:
            site_api.upload_picture = real
        check("it uploads each waiting picture, as the Releaser", (stopped, sent), (None, [(len(_png()), "it-releaser")]))
        after = blog_backend._load(draft)[1]["html"]
        check("  and the post now points at the website's copy",
              ("https://example.ae/agency-pictures/x.png" in after, posts.PENDING in after), (True, False))
        refused = site_api.upload_picture(base64.b64encode(_png()).decode(), agent="content-writer")
        check("no other desk may put a picture on the website", (refused["ok"], "may not change" in refused["error"]),
              (False, True))
        for f in posts.pending_dir().iterdir():
            f.unlink()
        d, blog, _st = blog_backend._load(draft)
        blog["html"] = blog["html"] + f'<img src="{posts.PENDING}{"a" * 24}.png">'
        missing = blog_backend._upload_pending(draft, d, blog, site_api.AGENT)
        check("a picture no longer on this computer stops the post, in words",
              (missing["ok"], "no longer on this computer" in missing["error"]), (False, True))


def test_scores_and_fingerprints():
    print("\nEvery post is scored, and copying is caught without keeping anyone's text:")
    fp = post_quality.fingerprint("the quick brown fox jumps over the lazy dog " * 30)
    check("a fingerprint is numbers, never text", all(isinstance(x, int) for x in fp) and len(fp) > 0, True)
    mine = BODY
    copied = post_quality.originality(mine, {"rival.ae": post_quality.fingerprint(post_quality.text_of(mine))})
    check("a post copied from a ranking page is caught", (copied["ok"], copied["like"]), (False, "rival.ae"))
    fresh = post_quality.originality(mine, {"rival.ae": post_quality.fingerprint("a completely different page about pergolas and shade " * 20)})
    check("an original one passes", fresh["ok"], True)
    same = ". ".join(["We fit the glass in one single day for you"] * 14) + "."
    check("sentences all the same length are flagged", bool(post_quality.sentence_variety(same)), True)
    r = post_quality.report({"title": "Frameless shower glass in Dubai", "meta": "Frameless shower glass in Dubai.",
                             "html": "<h1>Frameless shower glass in Dubai</h1>" + BODY}, PACK, {}, "blog")
    check("the report has all three", sorted(r), ["human", "original", "seo", "verdict"])
    check("  and an SEO score out of 100", 0 <= r["seo"]["score"] <= 100, True)


class _FakeWP:
    """A WordPress Media Library on this computer."""

    def __enter__(self):
        site = self
        self.got = []

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                site.got.append((self.path, self.headers.get("Content-Type"), self.headers.get("Content-Disposition"),
                                 self.headers.get("Authorization", "").startswith("Basic "), self.rfile.read(n)))
                b = json.dumps({"id": 5, "source_url": "http://127.0.0.1/wp-content/uploads/frameless.png"}).encode()
                self.send_response(201)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(b)))
                self.end_headers()
                self.wfile.write(b)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.site = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *a):
        self.srv.shutdown()
        self.srv.server_close()


def test_wordpress_media_library():
    print("\nOn WordPress, a picture goes into the Media Library:")
    with _FakeWP() as wp:
        s = {"site": wp.site, "wp_user": "owner", "wp_pass": "abcd efgh"}
        r = wp_bridge.call("/picture", "POST", {"data": base64.b64encode(_png()).decode(), "name": "Frameless glass"}, s=s)
        check("it is saved and its address comes back", (r["ok"], r["url"].endswith("frameless.png")), (True, True))
        path, kind, disp, authed, body = wp.got[0]
        check("  sent to the media endpoint, signed in, as the picture itself",
              (path, kind, authed, body == _png()), ("/wp-json/wp/v2/media", "image/png", True, True))
        check("  under a tidy file name", disp, 'attachment; filename="frameless-glass.png"')
        bad = wp_bridge.call("/picture", "POST", {"data": base64.b64encode(b"not a picture").decode()}, s=s)
        check("something that is not a picture is refused before it is sent", (bad["ok"], len(wp.got)), (False, 1))


def test_the_connector_saves_only_into_its_folder():
    print("\nThe connector file's picture door saves into agency-pictures/ and nowhere else:")
    php = (ROOT / "paste-into-your-site" / "agency-api.php").read_text(encoding="utf-8")
    block = php[php.index("if ($route === '/picture'"):php.index("if (in_array($route, ['/blog', '/blog-remove']")]
    check("one folder, named in the file, never taken from the request",
          "'dir' => 'agency-pictures'" in block and "$body['dir']" not in block, True)
    check("it says it can, so the office knows", "'connector' => 7" in php and "'pictures'" in php, True)


def main():
    test_reading_a_sent_mail()
    test_todays_topic()
    test_deleting_a_post()
    test_the_editor_is_cleaned()
    test_editing_a_post()
    test_the_releaser_carries_the_pictures()
    test_scores_and_fingerprints()
    test_wordpress_media_library()
    test_the_connector_saves_only_into_its_folder()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        return 1
    print("All post and mail tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
