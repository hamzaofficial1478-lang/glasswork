"""
blog_backend.py — a written post, straight into his own blog.

    python -m pipelines.blog_backend --check        what the agency can see of his blog
    python -m pipelines.blog_backend --plan 12      the row it would add for post #12

"yes add the posts to my backend direct"

His posts live in his database and blog-details.php shows them in his theme.
The connector on his site (agency-api.php, its blog section) adds a row to the
table his own blog page reads — worked out on his server from blog-details.php's
query, never named from here — and takes away only a row it added itself.

THIS FILE decides what goes in each column, from the table's own columns and his
latest post:

  title, slug, article, short description, keywords, meta title   ours
  the picture    saved into the folder his blog's pictures already live in, and
                 stored the way his other posts store theirs
  dates          now
  anything else his table insists on — author, category, published flag —
                 copied from his latest post and SAID SO, so he can change it

The article goes in as HTML when his post page prints it as it is, and as plain
text when his page escapes it (it would otherwise show the tags as words).

Only the Releaser calls publish() and remove() — the one door to his site —
after the tests, and only after he pressed the button in the Posts room.
"""
import argparse
import json
import re
import sqlite3
import sys
import time
import urllib.parse

from coordination import board
from . import site_api

# Column names by what they hold, best first. Matched against the whole name.
TITLE = ("title", "post_title", "blog_title", "heading", "name", "blog_name", "post_name")
SLUG = ("slug", "post_slug", "blog_slug", "permalink", "seo_url", "url_slug", "url")
CONTENT = ("content", "post_content", "blog_content", "body", "details", "detail",
           "long_description", "full_description", "blog_description", "post_description",
           "article", "text", "description")
EXCERPT = ("short_description", "short_desc", "excerpt", "summary", "intro", "short_content",
           "short_text", "small_description", "brief", "subtitle", "sub_title")
META_TITLE = ("meta_title", "seo_title", "page_title")
META_DESC = ("meta_description", "meta_desc", "seo_description")
KEYWORDS = ("meta_keywords", "meta_keyword", "keywords", "keyword", "tags", "meta_tags",
            "seo_keywords")
IMAGE = ("image", "featured_image", "cover_image", "main_image", "blog_image", "post_image",
         "thumbnail", "thumb", "img", "photo", "picture", "banner", "cover", "image_url")
IMAGE_ALT = ("image_alt", "alt", "alt_text")
DATES = re.compile(r"(created|updated|modified|added|published|publish|post|blog)?_?"
                   r"(at|on|date|time)|date|created|modified|updated", re.I)
# His table's own say on whether a post shows. Copied from his latest post, so
# ours shows the same way his do — a default of "draft" would hide it.
SHOWN = re.compile(r"status|is_published|published|is_active|active|visible|is_visible|"
                   r"state|enabled|approved|show|display|publish", re.I)
# Who wrote it and where it is filed: copied from his latest post, and said so.
FILED = re.compile(r"author|author_name|author_id|posted_by|written_by|user_id|admin_id|"
                   r"created_by|category|category_id|cat_id|blog_category|category_name", re.I)
COUNTS = re.compile(r"views?|likes?|hits|comments?|count|shares?", re.I)
SHORT = re.compile(r"sub|heading|caption|intro|short|summary|excerpt|desc", re.I)


def _cols(schema):
    return [c for c in schema.get("columns") or [] if isinstance(c, dict) and c.get("name")]


def _pick(cols, names, taken):
    by = {c["name"].lower(): c for c in cols}
    for n in names:
        c = by.get(n)
        if c and c["name"] not in taken and not c.get("auto"):
            return c
    return None


def _room(col, value):
    """(value, problem) — cut a short text to fit its column, refuse to cut an article."""
    m = re.match(r"(?:var)?char\((\d+)\)", str(col.get("type") or ""))
    if m and len(value) > int(m.group(1)):
        return value[:int(m.group(1))], f"{col['name']} only takes {m.group(1)} characters"
    return value, ""


def _norm(path):
    p = str(path or "").replace("\\", "/").strip()
    while p.startswith("./"):
        p = p[2:]
    return p.lstrip("/")


def _host(url):
    return (urllib.parse.urlsplit(url or "").hostname or "").lower().replace("www.", "")


def post_url(schema, slug, site):
    d = schema.get("display") or {}
    page, param = d.get("page") or "blog-details.php", d.get("param") or "slug"
    return f"{(site or '').rstrip('/')}/{page}?{param}={urllib.parse.quote(slug)}"


def _picture(schema, col, blog, site, notes):
    """The image instruction for the connector, or None with a note saying why."""
    imgs = blog.get("images") or []
    if not imgs:
        notes.append("the post has no picture, so its cover is empty — add one in your "
                     "backend if you like")
        return None
    img = next((i for i in imgs if i.get("own")), imgs[0])
    latest = str((schema.get("latest") or {}).get(col["name"]) or "").strip()
    prefix = _norm(((schema.get("display") or {}).get("prefix") or {}).get(col["name"]) or "")
    url_base = ""
    if re.match(r"https?://", latest, re.I):
        folder = _norm(urllib.parse.urlsplit(latest).path).rsplit("/", 1)[0] \
            if "/" in _norm(urllib.parse.urlsplit(latest).path) else ""
        url_base, drop = (site or "").rstrip("/"), ""
    elif latest or prefix:
        folder = "/".join(x for x in (prefix.rstrip("/"),
                                      latest.rsplit("/", 1)[0] if "/" in latest else "") if x)
        drop = prefix if prefix.endswith("/") or not prefix else prefix + "/"
    else:
        folder = ""
    if not folder and not url_base:
        # A bare file name and no folder in front of it on his post page: the
        # folder is set somewhere the agency cannot see, so do not guess it.
        notes.append("the agency could not tell which folder your blog keeps its pictures "
                     "in, so this post went up without a cover — add it in your backend. "
                     "The pictures inside the article are there already")
        return None
    never = [d.lower() for d in schema.get("never_dirs") or ("admin", "config", "private")]
    if any(part.lower() in never for part in folder.split("/") if part):
        notes.append(f"your blog keeps its pictures in {folder}/, which the agency never "
                     "writes to, so the post went up without its cover picture — add it in "
                     "your backend (the pictures are listed in the Posts room). The "
                     "pictures inside the article are there already")
        return None
    src = str(img.get("src") or "")
    if site and _host(src) and _host(src) == _host(site):
        src = _norm(urllib.parse.urlsplit(src).path)     # already on his server: copied, not fetched
    return {"column": col["name"], "from": src, "dir": folder, "drop": drop,
            "name": blog.get("slug") or "post", "url_base": url_base}


def plan_row(schema, blog, site="") -> dict:
    """
    The row for one post: {post, image, notes, problems, slug_column, url}.
    Nothing here talks to his site.
    """
    cols = _cols(schema)
    notes, problems = [], []
    taken, post = set(), {}
    latest = schema.get("latest") or {}
    shown_escaped = set((schema.get("display") or {}).get("escaped") or [])

    def put(col, value):
        if col is None or value in (None, ""):
            return
        if isinstance(value, str) and value != "__NOW__":
            value, why = _room(col, value)
            if why and col.get("_article"):
                problems.append(why + " — too short for an article, so nothing was added")
                return
            if why:
                notes.append(why + ", so it was shortened")
        post[col["name"]] = value
        taken.add(col["name"])

    slug_col = next((c for c in cols if c["name"] == schema.get("slug_column")), None) \
        or _pick(cols, SLUG, taken)
    if not slug_col:
        return {"ok": False, "post": {}, "image": None, "notes": notes,
                "problems": ["your posts table has no slug column, so a post could not be "
                             "given its address"], "slug_column": "", "url": ""}
    put(slug_col, blog.get("slug"))
    put(_pick(cols, TITLE, taken), blog.get("title"))
    content = _pick(cols, CONTENT, taken)
    if not content:
        problems.append("your posts table has no column for the article")
    else:
        content = {**content, "_article": True}
        if content["name"] in shown_escaped:
            notes.append("your post page shows the article as plain text, so it went in "
                         "as plain text")
            put(content, blog.get("text"))
        else:
            put(content, blog.get("html"))
    # With the article in "content", a "description" column is the short one.
    put(_pick(cols, EXCERPT, taken) or _pick(cols, ("description",), taken), blog.get("meta"))
    put(_pick(cols, META_TITLE, taken), (blog.get("title") or "")[:60])
    put(_pick(cols, META_DESC, taken), blog.get("meta"))
    put(_pick(cols, KEYWORDS, taken), blog.get("keywords"))
    alt = _pick(cols, IMAGE_ALT, taken)
    image_col = _pick(cols, IMAGE, taken)
    image = None
    if image_col:
        image = _picture(schema, image_col, blog, site, notes)
        taken.add(image_col["name"])
        if image and alt:
            put(alt, next((i.get("alt") for i in blog.get("images") or [] if i.get("alt")), ""))
        elif not image and not image_col.get("null") and image_col.get("default") is None:
            post[image_col["name"]] = ""

    for c in cols:
        name = c["name"]
        if name in taken or c.get("auto") or name == schema.get("key"):
            continue
        required = not c.get("null") and c.get("default") is None
        have = latest.get(name)
        if DATES.fullmatch(name) or re.search(r"date|time", str(c.get("type") or ""), re.I):
            post[name] = "__NOW__"
        elif SHOWN.search(name) and have not in (None, "", "(hidden)"):
            post[name] = have
            notes.append(f"{name} set to {have!r}, the same as your latest post, so it shows "
                         "the same way")
        elif FILED.search(name) and have not in (None, "", "(hidden)"):
            post[name] = have
            notes.append(f"{name} copied from your latest post ({str(have)[:40]!r}) — change "
                         "it in your backend if this post belongs elsewhere")
        elif required and COUNTS.search(name):
            post[name] = 0
        elif required and SHORT.search(name) and re.search(r"char|text", str(c.get("type") or "")):
            # A second line of words his table insists on: the short
            # description, never another post's text.
            post[name] = _room(c, blog.get("meta") or "")[0]
        elif required:
            if c.get("key") == "UNI" or have in (None, "(hidden)"):
                problems.append(f"your posts table needs a value for {name} and the agency "
                                "cannot tell what to put there")
            else:
                post[name] = have
                notes.append(f"{name} copied from your latest post ({str(have)[:40]!r})")
        else:
            continue
        taken.add(name)

    slug = str(post.get(slug_col["name"]) or "")
    return {"ok": not problems, "post": post, "image": image, "notes": notes,
            "problems": problems, "slug_column": slug_col["name"],
            "url": post_url(schema, slug, site)}


# ---------------------------------------------------------------------------
# WHAT THE POSTS ROOM SHOWS
# ---------------------------------------------------------------------------

def _save_status(ok, summary):
    try:
        with board._conn() as c:
            board._migrate(c)
            c.execute("INSERT INTO blog_backend(id, checked_at, ok, summary) VALUES(1,?,?,?)"
                      " ON CONFLICT(id) DO UPDATE SET checked_at=excluded.checked_at,"
                      " ok=excluded.ok, summary=excluded.summary",
                      (int(time.time()), 1 if ok else 0, json.dumps(summary)))
    except Exception:
        pass


def last_status() -> dict:
    """What the last look at his blog found. {} if it has never been looked at."""
    try:
        c = sqlite3.connect(board.DB, timeout=5)
        try:
            r = c.execute("SELECT checked_at, ok, summary FROM blog_backend WHERE id=1").fetchone()
        finally:
            c.close()
    except sqlite3.Error:
        return {}
    if not r:
        return {}
    try:
        s = json.loads(r[2] or "{}")
    except ValueError:
        s = {}
    return {**s, "ok": bool(r[1]), "checked_at": r[0]}


def _summary(schema):
    cols = [c["name"] for c in _cols(schema)]
    return {"table": schema.get("table"), "posts": schema.get("count"),
            "via": schema.get("via"), "columns": cols[:40],
            "page": (schema.get("display") or {}).get("page") or ""}


def check(s=None) -> dict:
    """Look at his blog through the connector, and remember what was found."""
    sch = site_api.blog_schema(s=s)
    if not sch.get("ok"):
        why = sch.get("error") or "your site did not answer"
        if not sch.get("unreachable"):
            _save_status(False, {"error": why, "old_connector": bool(sch.get("old_connector"))})
        return {"ok": False, "error": why, "old_connector": bool(sch.get("old_connector")),
                "unreachable": bool(sch.get("unreachable"))}
    summary = _summary(sch)
    _save_status(True, summary)
    return {"ok": True, **summary,
            "head": (f"Connected to your blog: the {summary['table']} table, "
                     f"{summary['posts']} post(s), through {summary['via']}.")}


# ---------------------------------------------------------------------------
# THE RELEASER'S TWO CALLS
# ---------------------------------------------------------------------------

def _load(content_id):
    c = sqlite3.connect(board.DB, timeout=30)
    try:
        r = c.execute("SELECT draft, status FROM content_queue WHERE id=?",
                      (int(content_id),)).fetchone()
    finally:
        c.close()
    if not r:
        return None, None, None
    try:
        draft = json.loads(r[0] or "{}")
    except ValueError:
        draft = {}
    return draft, draft.get("_for_blog") or {}, r[1]


def _store(content_id, draft, status):
    with board._conn() as c:
        board._migrate(c)
        c.execute("UPDATE content_queue SET draft=?, status=? WHERE id=?",
                  (json.dumps(draft, indent=2), status, int(content_id)))


def _site(s=None):
    return ((s or site_api.settings()).get("site") or "").rstrip("/")


def publish(content_id, agent=site_api.AGENT, s=None) -> dict:
    """Put one post on his blog. {ok, url, id, head} or {ok: False, error}."""
    draft, blog, status = _load(content_id)
    if not blog:
        return {"ok": False, "error": f"post #{content_id} is not ready for your blog any more"}
    back = blog.get("backend") or {}
    if status == "published" and back.get("id"):
        return {"ok": True, "url": blog.get("posted_url"), "id": back["id"], "already": True,
                "head": f"“{blog.get('title')}” is already on your blog"}
    sch = site_api.blog_schema(s=s)
    if not sch.get("ok"):
        if not sch.get("unreachable"):
            _save_status(False, {"error": sch.get("error"),
                                 "old_connector": bool(sch.get("old_connector"))})
        return sch
    _save_status(True, _summary(sch))
    site = _site(s)
    plan = plan_row(sch, blog, site)
    if plan["problems"]:
        return {"ok": False, "error": "; ".join(plan["problems"])}
    undo = f"python -m pipelines.blog_backend --remove {int(content_id)}"
    r = site_api.add_blog_post(plan["post"], plan["image"], agent=agent, s=s,
                               title=blog.get("title"), undo_cmd=undo)
    slug = plan["post"][plan["slug_column"]]
    if r.get("taken"):
        seen = site_api.blog_schema(slug=slug, s=s)
        if seen.get("ours"):
            # Added on an earlier try whose answer never arrived. It is there.
            r = {"ok": True, "id": seen.get("id"), "slug": slug, "already": True}
        else:
            slug = f"{slug}-2"
            plan["post"][plan["slug_column"]] = slug
            if plan["image"]:
                plan["image"]["name"] = slug
            notes = plan["notes"] + [f"your blog already had a post at {slug[:-2]}, so this "
                                     f"one is at {slug}"]
            plan["notes"] = notes
            r = site_api.add_blog_post(plan["post"], plan["image"], agent=agent, s=s,
                                       title=blog.get("title"), undo_cmd=undo)
    if not r.get("ok"):
        return r
    seen = site_api.blog_schema(slug=slug, s=s)
    url = post_url(r.get("display") and {"display": r["display"]} or sch, slug, site)
    blog.update(posted_url=url, posted_at=int(time.time()), slug=slug,
                backend={"table": r.get("table") or sch.get("table"), "id": str(r.get("id") or ""),
                         "picture": r.get("picture") or "", "notes": plan["notes"][:8]})
    blog.pop("posting", None)
    draft["_for_blog"] = blog
    _store(content_id, draft, "published")
    checked = bool(seen.get("found"))
    return {"ok": True, "url": url, "id": r.get("id"), "notes": plan["notes"],
            "checked": checked,
            "head": (f"“{blog.get('title')}” is on your blog at {url}"
                     + ("" if checked else " (added; the read-back did not answer, so check it "
                                           "opens)"))}


def remove(content_id, agent=site_api.AGENT, s=None) -> dict:
    """Take a post the agency added off his blog. It goes back to Ready for your blog."""
    draft, blog, _status = _load(content_id)
    back = (blog or {}).get("backend") or {}
    if not back.get("id"):
        return {"ok": False, "error": "that post was not added to your blog by the agency, so "
                                      "it cannot take it off — do it in your backend"}
    r = site_api.remove_blog_post(back["id"], slug=blog.get("slug"), agent=agent, s=s,
                                  title=blog.get("title"))
    if not r.get("ok"):
        return r
    for k in ("posted_url", "posted_at", "backend", "removing"):
        blog.pop(k, None)
    draft["_for_blog"] = blog
    _store(content_id, draft, "for_blog")
    return {"ok": True, "head": f"“{blog.get('title')}” is off your blog. It is back under "
                                "Ready for your blog."}


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Posts into his own blog backend.")
    ap.add_argument("--check", action="store_true", help="what the agency can see of his blog")
    ap.add_argument("--plan", type=int, metavar="ID", help="the row it would add for a post")
    ap.add_argument("--remove", type=int, metavar="ID", help="take a post it added back off")
    a = ap.parse_args()
    if a.check:
        r = check()
        print(r.get("head") or r.get("error"))
        return 0 if r.get("ok") else 1
    if a.plan:
        _d, blog, _st = _load(a.plan)
        sch = site_api.blog_schema()
        if not sch.get("ok"):
            print(sch.get("error"))
            return 1
        print(json.dumps(plan_row(sch, blog or {}, _site()), indent=2)[:6000])
        return 0
    if a.remove:
        r = remove(a.remove)
        print(r.get("head") or r.get("error"))
        return 0 if r.get("ok") else 1
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
