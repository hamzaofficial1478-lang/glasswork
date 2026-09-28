"""
blog_tests.py — run: python -m pipelines.blog_tests

"yes add the posts to my backend direct"

What has to hold:

  the right table     the one his own blog-details.php reads, worked out on his
                      server — never a table named in the request, never users
  the right columns   title, slug, article, short description ours; dates now;
                      author, category, published flag as his latest post has
                      them, and said so
  the article         as HTML when his post page prints it as it is, as plain
                      text when it escapes it
  the picture         into the folder his blog's pictures live in, stored the way
                      his posts store theirs — never into admin/
  his yes, IT's door  a press queues an approved change; only the Releaser adds
                      the post, and only a post the agency added comes off again
  an old connector    says exactly which file to upload, not "nothing answered"

The first half needs nothing. The second half runs the real connector on PHP's
own server against a throwaway site with a blog table in it, and is skipped
(saying so) on a machine without PHP.
"""
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

from coordination import board
from . import blog_backend as BB, content_write as CW, it_release as IR, site_api

_fail = []
ROOT = Path(__file__).parents[1]
TOKEN = "t0k3n" * 10


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
    con.executescript((ROOT / "coordination" / "schema.sql").read_text(encoding="utf-8"))
    con.commit()
    con.close()
    board.DB = str(db)
    board.ensure_schema()
    return db


BRIEF = {"title": "Glass railing cost in the UAE", "primary_keyword": "glass railing uae cost",
         "secondary_keywords": ["glass balustrade dubai"]}
BODY = ("<h1>Glass railing in the UAE</h1><p>Most people ring us with one question first: what "
        "will a glass railing cost?</p><h2>Which fixing suits you</h2><p>Base channel, spigots "
        "or standoff pins.</p><!-- image 1 --><h2>What to ask</h2><p>Send a photo on WhatsApp.</p>")


def _blog(site="https://www.example.ae", images=None):
    imgs = images if images is not None else [
        {"src": "/assets/img/pergola.png", "alt": "A railing we fitted", "own": True}]
    return CW.for_his_blog({"title": "Glass railing in the UAE: cost and fixing types",
                            "meta": "What decides the cost of a glass railing in the UAE.",
                            "html": BODY}, BRIEF, imgs, site)


def _schema(**over):
    cols = [
        {"name": "id", "type": "int(11)", "null": False, "key": "PRI", "default": None, "auto": True},
        {"name": "title", "type": "varchar(255)", "null": False, "key": "", "default": None, "auto": False},
        {"name": "slug", "type": "varchar(255)", "null": False, "key": "UNI", "default": None, "auto": False},
        {"name": "image", "type": "varchar(255)", "null": False, "key": "", "default": None, "auto": False},
        {"name": "content", "type": "longtext", "null": False, "key": "", "default": None, "auto": False},
        {"name": "short_description", "type": "text", "null": True, "key": "", "default": None, "auto": False},
        {"name": "meta_title", "type": "varchar(255)", "null": True, "key": "", "default": None, "auto": False},
        {"name": "meta_description", "type": "varchar(255)", "null": True, "key": "", "default": None, "auto": False},
        {"name": "status", "type": "tinyint(1)", "null": False, "key": "", "default": "0", "auto": False},
        {"name": "category_id", "type": "int(11)", "null": False, "key": "MUL", "default": None, "auto": False},
        {"name": "author", "type": "varchar(100)", "null": True, "key": "", "default": None, "auto": False},
        {"name": "views", "type": "int(11)", "null": False, "key": "", "default": "0", "auto": False},
        {"name": "created_at", "type": "datetime", "null": False, "key": "", "default": "CURRENT_TIMESTAMP", "auto": False},
    ]
    s = {"ok": True, "table": "blogs", "columns": cols, "key": "id", "slug_column": "slug",
         "latest": {"id": 7, "title": "Custom wooden interior design", "slug": "custom-wooden-interior-design-dubai",
                    "image": "blog/wood.jpg", "status": 1, "category_id": 3,
                    "author": "World Deal Technical Services", "views": 40},
         "count": 7, "via": "config.php", "driver": "mysql",
         "display": {"page": "blog-details.php", "param": "slug",
                     "prefix": {"image": "uploads/"}, "escaped": ["title"]},
         "never_dirs": ["admin", "config", "private"]}
    s.update(over)
    return s


# ---------------------------------------------------------------------------

def test_the_row_is_built_from_his_own_table():
    print("\nThe row for one post, from his table's columns and his latest post:")
    plan = BB.plan_row(_schema(), _blog(), "https://www.example.ae")
    post = plan["post"]
    check("nothing stops it", plan["problems"], [])
    check("title and slug are ours",
          (post["title"], post["slug"]),
          ("Glass railing in the UAE: cost and fixing types",
           "glass-railing-in-the-uae-cost-and-fixing-types"))
    check("the article goes in as HTML, because his page prints it as it is",
          post["content"].startswith("<p>Most people"), True)
    check("the short description and the Google lines", (post["short_description"],
          post["meta_description"]), ("What decides the cost of a glass railing in the UAE.",) * 2)
    check("it shows the way his posts show", post["status"], 1)
    check("filed where his latest post is — and he is told",
          (post["category_id"], any("category_id copied" in n for n in plan["notes"])), (3, True))
    check("dated now", post["created_at"], "__NOW__")
    check("the id and the view count are left to his database",
          ("id" in post, "views" in post), (False, False))
    img = plan["image"]
    check("the picture goes where his blog's pictures are",
          (img["dir"], img["drop"], img["column"]), ("uploads/blog", "uploads/", "image"))
    check("his own photo is copied on his server, not fetched over the internet",
          img["from"], "assets/img/pergola.png")
    check("and the post's address is his blog's",
          plan["url"], "https://www.example.ae/blog-details.php?slug="
                       "glass-railing-in-the-uae-cost-and-fixing-types")

    print("\n...the article as text only when his page's BODY escapes it:")
    s = _schema(display={"page": "blog-details.php", "param": "slug", "prefix": {"image": "uploads/"},
                         "escaped": ["content"], "raw": []})
    post = BB.plan_row(s, _blog(), "https://www.example.ae")["post"]
    check("no tags to show up as words", ("<p>" in post["content"], "Which fixing suits you"
                                         in post["content"]), (False, True))
    s = _schema(display={"page": "blog-details.php", "param": "slug", "prefix": {"image": "uploads/"},
                         "escaped": ["content"]})
    b = {**_blog(), "keywords": "glass railing, base channel"}
    post = BB.plan_row(s, b, "https://www.example.ae")["post"]
    check("an older connector that read the <head> too cannot make it plain — HTML, with headings",
          ("<h2>" in post["content"]), True)
    check("and the key words are bold the first time",
          ("<strong>glass railing</strong>" in post["content"],
           "<strong>Base channel</strong>" in post["content"]), (True, True))

    print("\n...the cover goes where his posts' pictures really are:")
    home = {"column": "image", "value": "wood.jpg", "prefix": "uploads/blog/", "folder": "uploads/blog"}
    img = BB.plan_row(_schema(image_home=home, connector=5), _blog(), "https://www.example.ae")["image"]
    check("beside his latest post's picture, named the way his are",
          (img["dir"], img["drop"]), ("uploads/blog", "uploads/blog/"))
    home = {"column": "image", "value": "wood.jpg", "prefix": "admin/uploads/", "folder": "admin/uploads"}
    plan = BB.plan_row(_schema(image_home=home, connector=5), _blog(), "https://www.example.ae")
    check("his pictures under admin/: saved in a folder of its own and linked out of admin/",
          (plan["image"]["dir"], plan["image"]["climb"]), ("agency-pictures", "../../"))
    check("and he is told", any("agency-pictures" in n for n in plan["notes"]), True)
    plan = BB.plan_row(_schema(image_home=home, connector=4), _blog(), "https://www.example.ae")
    check("an older connector says to upload the new one, rather than a broken picture",
          (plan["image"], any("new connector" in n for n in plan["notes"])), (None, True))

    print("\n...never into his admin folder:")
    s = _schema(display={"page": "blog-details.php", "param": "slug",
                         "prefix": {"image": "admin/uploads/"}, "escaped": []})
    plan = BB.plan_row(s, _blog(), "https://www.example.ae")
    check("no picture is saved", plan["image"], None)
    check("the column his table insists on is left empty, not given another post's picture",
          plan["post"]["image"], "")
    check("and he is told to add the cover himself",
          any("never writes to" in n for n in plan["notes"]), True)

    print("\n...and a table it cannot fill honestly is refused, not guessed:")
    cols = _schema()["columns"]
    cols = [dict(c, type="varchar(255)") if c["name"] == "content" else c for c in cols]
    check("an article column of 255 characters",
          any("too short for an article" in p for p in
              BB.plan_row(_schema(columns=cols), _blog(), "")["problems"]), True)
    cols = _schema()["columns"] + [{"name": "sku", "type": "varchar(20)", "null": False,
                                    "key": "UNI", "default": None, "auto": False}]
    check("a unique column it knows nothing about",
          any("sku" in p for p in BB.plan_row(_schema(columns=cols), _blog(), "")["problems"]), True)


def test_the_key_words_are_bold():
    print("\nThe words that matter are bold, the first time, in running text only:")
    html = ("<h2>Glass railing cost</h2><p>A glass railing costs what the glass costs. "
            "Frameless glass railing needs <a href='/x'>glass railing parts</a>.</p>"
            "<p>Glass railing again.</p>")
    out = BB.bold_keywords(html, ["glass railing", "frameless glass railing"])
    check("once for each keyword", out.count("<strong>"), 2)
    check("never inside a heading", "<h2>Glass railing cost</h2>" in out, True)
    check("never inside a link", "<a href='/x'>glass railing parts</a>" in out, True)


def test_the_post_is_filed_where_it_belongs():
    print("\nA post is filed under the category it is about:")
    cats = [{"id": "1", "name": "Wooden Work"}, {"id": "2", "name": "Glass Work"},
            {"id": "3", "name": "Aluminum Work"}, {"id": "4", "name": "Tips & Guides"}]
    s = _schema(category_column="category_id", categories=cats)
    alu = CW.for_his_blog({"title": "Aluminium Cladding Sheet for Duct, Fitted Across the UAE",
                           "meta": "m", "html": BODY}, {"primary_keyword": "aluminium cladding sheet"})
    plan = BB.plan_row(s, alu, "https://www.example.ae")
    check("aluminium goes under Aluminum Work, not his latest post's Wooden Work",
          (plan["post"]["category_id"], any("Aluminum Work" in n for n in plan["notes"])), ("3", True))
    other = CW.for_his_blog({"title": "Keeping balconies safe in summer", "meta": "m", "html": BODY},
                            {"primary_keyword": "balcony safety"})
    check("and a post that matches none goes under his general one",
          BB.plan_row(s, other, "")["post"]["category_id"], "4")


def test_his_press_goes_through_it():
    print("\nHis press queues an approved change; the Releaser carries it:")
    from coordination import review
    db = _fresh_db()
    blog = _blog()
    with sqlite3.connect(db) as c:
        c.execute("INSERT INTO content_queue(id,title,type,brief,draft,status,created_at)"
                  " VALUES(5,'Railing','blog',?,?,'for_blog',1)",
                  (json.dumps(BRIEF), json.dumps({"title": blog["title"], "_for_blog": blog})))
    old = {"ok": False, "status": 404, "old_connector": True, "error": site_api.OLD_CONNECTOR}
    with mock.patch.object(site_api, "blog_schema", lambda slug="", s=None: old):
        r = review.act("blog_publish", [5])
    check("with the old connector on his site he is told at once, and nothing is queued",
          (r["ok"], "agency-api.php" in r["error"],
           sqlite3.connect(db).execute("SELECT count(*) FROM site_queue").fetchone()[0]),
          (False, True, 0))
    with mock.patch.object(site_api, "blog_schema", lambda slug="", s=None: {"ok": True, **_schema()}):
        r = review.act("blog_publish", [5])
        check("queued", r["ok"], True)
        with sqlite3.connect(db) as c:
            c.row_factory = sqlite3.Row
            row = dict(c.execute("SELECT * FROM site_queue WHERE id=?", (r["change"],)).fetchone())
        check("approved on his press, for the Releaser",
              (row["status"], IR.plan_of(row)["do"]), ("approved", "blog_post"))
        check("pressed twice is one change", review.act("blog_publish", [5])["change"], r["change"])
    with mock.patch.object(site_api, "blog_schema", lambda slug="", s=None: {
            "ok": True, **_schema()} if not slug else {"ok": True, "found": True, "id": "8"}), \
         mock.patch.object(site_api, "call", lambda *a, **k: {"ok": True, "id": "8",
                                                              "table": "blogs", "picture": ""}), \
         mock.patch.object(site_api, "settings", lambda env=None: {
             "mode": "full", "base": "https://x/agency-api", "token": TOKEN,
             "site": "https://www.example.ae"}), \
         mock.patch.object(IR.site_api, "why_not_ready", lambda s=None: ""):
        out = IR.run(skip_tests=True)
    check("it went on", out["applied"], 1)
    with sqlite3.connect(db) as c:
        st, draft = c.execute("SELECT status, draft FROM content_queue WHERE id=5").fetchone()
    blog = json.loads(draft)["_for_blog"]
    check("the post is marked as on his blog, at its address",
          (st, blog["posted_url"]), ("published", "https://www.example.ae/blog-details.php?slug="
                                                  "glass-railing-in-the-uae-cost-and-fixing-types"))
    check("with the row the connector added, so it can come off again",
          blog["backend"]["id"], "8")
    check("a desk other than the Releaser is turned away",
          site_api.add_blog_post({"slug": "x"}, agent="content-writer")["ok"], False)


def test_an_old_connector_says_what_to_upload():
    print("\nThe connector on his site is the old one:")
    import urllib.error
    import io

    def old(req, timeout=0):
        raise urllib.error.HTTPError(req.full_url, 404, "nf", {},
                                     io.BytesIO(b'{"ok":false,"error":"unknown endpoint: /blog"}'))
    db = _fresh_db()
    with mock.patch.object(site_api, "settings", lambda env=None: {
            "mode": "full", "base": "https://x/agency-api", "token": TOKEN, "site": "https://x"}), \
         mock.patch("urllib.request.urlopen", old):
        r = BB.check()
    check("it is named as the old connector", r["old_connector"], True)
    check("and says which file to upload", "paste-into-your-site/agency-api.php" in r["error"], True)
    check("the Posts room remembers it", BB.last_status().get("old_connector"), True)
    del db


# ---------------------------------------------------------------------------
# THE REAL CONNECTOR, ON PHP'S OWN SERVER
# ---------------------------------------------------------------------------

PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")

BLOG_DETAILS = """<?php
include 'includes/db.php';
$stmt = $pdo->prepare("SELECT * FROM blogs WHERE slug = ?");
$stmt->execute([$_GET['slug'] ?? '']);
$blog = $stmt->fetch(PDO::FETCH_ASSOC);
?><!doctype html><html><head><title><?php echo htmlspecialchars($blog['title']); ?></title>
<meta name="description" content="<?php echo htmlspecialchars(substr(strip_tags($blog['content']), 0, 150)); ?>"></head>
<body><img src="uploads/<?php echo $blog['image']; ?>" alt="">
<h1><?php echo htmlspecialchars($blog['title']); ?></h1>
<div class="post"><?php echo $blog['content']; ?></div></body></html>
"""
DB_PHP = """<?php
$pdo = new PDO('sqlite:' . __DIR__ . '/../data/blog.sqlite');
"""


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _site():
    root = Path(tempfile.mkdtemp()) / "site"
    for d in ("includes", "data", "uploads/blog", "assets/img", "agency-api", "admin"):
        (root / d).mkdir(parents=True)
    (root / "blog-details.php").write_text(BLOG_DETAILS, encoding="utf-8")
    (root / "includes" / "db.php").write_text(DB_PHP, encoding="utf-8")
    # A connection file that sends visitors to a login page is never opened.
    admin = ("<?php session_start(); if (empty($_SESSION['u'])) { header('Location: login.php'); exit; }"
             " $pdo = new PDO('sqlite::memory:');")
    (root / "admin" / "config.php").write_text(admin, encoding="utf-8")
    (root / "assets" / "img" / "pergola.png").write_bytes(PNG)
    (root / "uploads" / "blog" / "wood.png").write_bytes(PNG)
    shutil.copy(ROOT / "paste-into-your-site" / "agency-api.php", root / "agency-api" / "index.php")
    (root / "agency-api" / "token.php").write_text(f"<?php return '{TOKEN}';", encoding="utf-8")
    con = sqlite3.connect(root / "data" / "blog.sqlite")
    con.executescript("""
      CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT, password TEXT, slug TEXT, content TEXT);
      INSERT INTO users VALUES (1, 'owner@example.ae', 'secret-hash', 'x', 'x');
      CREATE TABLE blogs (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
        slug TEXT NOT NULL UNIQUE, content TEXT NOT NULL, short_description TEXT,
        image TEXT NOT NULL, status INTEGER NOT NULL DEFAULT 0, category_id INTEGER NOT NULL,
        created_at TEXT NOT NULL);
      INSERT INTO blogs(title,slug,content,short_description,image,status,category_id,created_at)
        VALUES ('Custom wooden interior design','custom-wooden-interior-design-dubai',
                '<p>Wood.</p>','Wood.','blog/wood.png',1,1,'2026-09-01 10:00:00');
      CREATE TABLE blog_categories (id INTEGER PRIMARY KEY, name TEXT);
      INSERT INTO blog_categories VALUES (1,'Wooden Work'),(2,'Glass Work'),(3,'Aluminum Work'),
                                         (4,'Tips & Guides');
    """)
    (root / "aluminium-cladding.php").write_text("<h1>A page of its own</h1>", encoding="utf-8")
    (root / "index.php").write_text("<h1>Home</h1>", encoding="utf-8")
    con.commit()
    con.close()
    return root


def test_the_real_connector_adds_a_post_and_takes_it_away():
    print("\nThe real connector, against a site with a blog table:")
    if not shutil.which("php"):
        print("  --  skipped: PHP is not installed on this machine. The connector itself "
              "runs on your website, where PHP is.")
        return
    # A PHP on this PC (XAMPP and the like) without the SQLite driver, or older
    # than 8, cannot run the stand-in site — that says nothing about the code,
    # and must not turn the suite red and stop a release.
    try:
        mods = subprocess.run(["php", "-r", "echo PHP_MAJOR_VERSION, ' ', "
                               "extension_loaded('pdo_sqlite') ? 'yes' : 'no';"],
                              capture_output=True, text=True, encoding="utf-8",
                              timeout=20).stdout.split()
    except Exception:
        mods = []
    if len(mods) != 2 or int(mods[0] or 0) < 8 or mods[1] != "yes":
        print("  --  skipped: this PC's PHP cannot run the stand-in site (it needs PHP 8 with "
              "SQLite). The connector itself runs on your website.")
        return
    root = _site()
    port = _free_port()
    srv = subprocess.Popen(["php", "-S", f"127.0.0.1:{port}", "-t", str(root)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    s = {"mode": "full", "base": f"http://127.0.0.1:{port}/agency-api", "token": TOKEN,
         "site": f"http://127.0.0.1:{port}"}
    try:
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.1)
        db = _fresh_db()
        with mock.patch.object(site_api, "settings", lambda env=None: s), \
             mock.patch.object(IR.site_api, "why_not_ready", lambda s=None: ""):
            sch = site_api.blog_schema(s=s)
            check("it finds the table his blog page reads — not users",
                  (sch.get("ok"), sch.get("table")), (True, "blogs"))
            check("through the connection his blog page includes, not the admin one",
                  sch.get("via"), "db.php")
            check("and reads how his page shows the picture",
                  sch["display"]["prefix"].get("image"), "uploads/")
            check("a password is never in what comes back", "secret-hash" in json.dumps(sch), False)
            check("the article printed stripped in the <head> for Google does not make it plain text",
                  ("content" in sch["display"]["raw"], "content" in sch["display"]["escaped"]),
                  (True, False))
            check("and it finds where his posts' pictures really are",
                  (sch["image_home"] or {}).get("folder"), "uploads/blog")

            blog = _blog(site=s["site"], images=[{"src": s["site"] + "/assets/img/pergola.png",
                                                  "alt": "A railing we fitted", "own": True}])
            with sqlite3.connect(db) as c:
                c.execute("INSERT INTO content_queue(id,title,type,brief,draft,status,created_at)"
                          " VALUES(9,'Railing','blog',?,?,'for_blog',1)",
                          (json.dumps(BRIEF), json.dumps({"title": blog["title"], "_for_blog": blog})))
            from coordination import review
            review.act("blog_publish", [9])
            out = IR.run(skip_tests=True)
            check("the Releaser put it on", out.get("applied"), 1)

            blogdb = sqlite3.connect(root / "data" / "blog.sqlite")
            row = blogdb.execute("SELECT title, slug, content, image, status, category_id,"
                                 " created_at FROM blogs WHERE id=2").fetchone()
            blogdb.close()
            check("his blog table has the post", row and row[1],
                  "glass-railing-in-the-uae-cost-and-fixing-types")
            check("with the article in it", row and "Which fixing suits you" in row[2], True)
            check("showing like his other posts, and filed under Glass Work — not his latest post's",
                  row and (row[4], row[5]), (1, 2))
            check("dated", bool(row and row[6].startswith("20")), True)
            check("its picture saved in his blog's picture folder, stored his way",
                  bool(row and row[3].startswith("blog/glass-railing")
                       and (root / "uploads" / row[3]).is_file()), True)
            import urllib.request
            page = urllib.request.urlopen(
                f"{s['site']}/blog-details.php?slug=glass-railing-in-the-uae-cost-and-fixing-types",
                timeout=10).read().decode()
            check("and his own post page shows it", "Which fixing suits you" in page, True)
            with sqlite3.connect(db) as c:
                st = c.execute("SELECT status FROM content_queue WHERE id=9").fetchone()[0]
            check("the Posts room has it as on his blog", st, "published")

            print("\n...only a post it added can come off:")
            r = site_api.remove_blog_post(1, s=s)
            check("his own post is refused", (r["ok"], r.get("status")), (False, 403))
            review.act("blog_unpublish", [9])
            out = IR.run(skip_tests=True)
            blogdb = sqlite3.connect(root / "data" / "blog.sqlite")
            left = [x[0] for x in blogdb.execute("SELECT slug FROM blogs")]
            blogdb.close()
            check("the agency's post is gone and his is untouched", left,
                  ["custom-wooden-interior-design-dubai"])
            check("and its picture with it", sorted(p.name for p in (root / "uploads" / "blog").iterdir()),
                  ["wood.png"])
            with sqlite3.connect(db) as c:
                st = c.execute("SELECT status FROM content_queue WHERE id=9").fetchone()[0]
            check("the post is back under Ready for your blog", st, "for_blog")

            print("\n...when his pictures live under admin/, the cover goes in a folder of its own:")
            post = {"title": "Cover test", "slug": "cover-test", "content": "<p>x</p>",
                    "category_id": 1, "created_at": "__NOW__"}
            r = site_api.add_blog_post(post, {"column": "image", "from": "assets/img/pergola.png",
                                              "dir": "agency-pictures", "drop": "", "climb": "../../",
                                              "name": "cover-test"}, s=s)
            blogdb = sqlite3.connect(root / "data" / "blog.sqlite")
            val = (blogdb.execute("SELECT image FROM blogs WHERE slug='cover-test'").fetchone() or [""])[0]
            blogdb.close()
            import posixpath
            check("saved there, and linked out of admin/uploads/ so his page finds it",
                  (r["ok"], val.startswith("../../agency-pictures/cover-test-"),
                   (root / posixpath.normpath("admin/uploads/" + val)).is_file()), (True, True, True))
            site_api.remove_blog_post(r.get("id"), s=s)

            print("\n...Put it up again replaces it in one step:")
            review.act("blog_publish", [9])
            IR.run(skip_tests=True)
            r = review.act("blog_repost", [9])
            out = IR.run(skip_tests=True)
            blogdb = sqlite3.connect(root / "data" / "blog.sqlite")
            slugs = [x[0] for x in blogdb.execute("SELECT slug FROM blogs")]
            content = blogdb.execute("SELECT content FROM blogs WHERE slug=?",
                                     ("glass-railing-in-the-uae-cost-and-fixing-types",)).fetchone()
            blogdb.close()
            check("one step for IT, and the post is back — once",
                  (r["ok"], out.get("applied"),
                   slugs.count("glass-railing-in-the-uae-cost-and-fixing-types")), (True, 1, 1))
            check("with its headings", bool(content and "<h2>" in content[0]), True)
            review.act("blog_unpublish", [9])
            IR.run(skip_tests=True)

            print("\n...and a page put up as a post of its own is deleted, with a copy kept:")
            r = site_api.remove_page("aluminium-cladding.php", s=s)
            check("deleted", (r["ok"], (root / "aluminium-cladding.php").exists()), (True, False))
            check("a copy is kept first",
                  any(b.name.startswith("aluminium-cladding.php.")
                      for b in (root / "agency-api" / "backups").iterdir()), True)
            check("his home page can never be removed",
                  site_api.remove_page("index.php", s=s)["ok"], False)
            real_call = site_api.call

            def old_door(path, method="GET", body=None, timeout=25, s=None):
                if path == "/pages-remove":
                    return {"ok": False, "old_connector": True, "error": site_api.OLD_CONNECTOR}
                return real_call(path, method, body, timeout, s)
            (root / "old-page.php").write_text("<h1>Another</h1>", encoding="utf-8")
            with mock.patch.object(site_api, "call", old_door):
                r = site_api.remove_page("old-page.php", go_to=s["site"] + "/blog.php", s=s)
            left = (root / "old-page.php").read_text(encoding="utf-8")
            check("an older connector replaces it with a pointer to his blog, and says so",
                  (r["ok"], r.get("stubbed"), "blog.php" in left and "noindex" in left),
                  (True, True, True))
    finally:
        srv.terminate()
        try:
            srv.wait(timeout=5)
        except Exception:
            srv.kill()


def main():
    real = board.DB
    try:
        for t in (test_the_row_is_built_from_his_own_table,
                  test_the_post_is_filed_where_it_belongs,
                  test_the_key_words_are_bold,
                  test_his_press_goes_through_it,
                  test_an_old_connector_says_what_to_upload,
                  test_the_real_connector_adds_a_post_and_takes_it_away):
            t()
    finally:
        board.DB = real
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        return 1
    print("blog: all good — posts go into his own blog, through IT, and only ours come off.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
