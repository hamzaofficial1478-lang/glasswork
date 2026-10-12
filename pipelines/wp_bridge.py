"""
wp_bridge.py — a WordPress website, reached through WordPress's own REST API
with an Application Password, answering the same calls as the connector file.

"connect with a WordPress Application Password, which is built into
 WordPress, so no file upload is needed" — docs/PUBLIC-LAUNCH-PLAN.md, idea 3

The rest of the agency talks to a website through pipelines/site_api.call,
which was written for paste-into-your-site/agency-api.php: /health, /pages,
/pages/<page>, /head, /rollback, /blog and the rest. A WordPress site has no
such file, and needs none. This module answers each of those calls by asking
WordPress instead, so the Releaser, the Writer and IT carry on unchanged.

WHAT MAPS TO WHAT

  /health          who the password belongs to, and what WordPress lets it do
  /pages           the site's published and draft pages, by their address
  /pages/<page>    one page's content, as WordPress stores it
  /pages  POST     change a page's content (WordPress keeps the old version
                   as a revision — that is the backup), or add a new page
  /head   POST     a marked block at the end of the page's content: WordPress
                   gives no way into a theme's <head>, and structured data
                   works from the body just as well
  /rollback        put back the page's previous revision
  /backups         the revisions WordPress has kept
  /blog            posts: is there one at this address; add one
  /blog-remove     move a post to the bin — only one the agency added
  /pages-remove    move a page to the bin (never the home page); the bin keeps it

WHAT IT CANNOT DO, AND SAYS SO

A theme's header and footer, and anything in a page builder's own storage,
are not page content. A change there is handed to the owner as homework, the
same as on a read-only website. The contact-form test the connector runs is
not available either.

SAFETY

The Application Password is the person's own, made in their WordPress profile,
revocable there at any time, and never their real password. It is kept sealed
in the website's settings. It goes in an Authorization header and nowhere
else — never in an address, never in a log line.
"""
import base64
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

from coordination import workspace

TIMEOUT = 25
LEDGER = "wordpress-posts.local.json"      # the posts this agency added
HOME_SLUGS = {"", "home", "index", "front-page"}
MARK_OPEN = "<!-- wp:html --><!-- agency:{id} -->"
MARK_CLOSE = "<!-- /agency:{id} --><!-- /wp:html -->"


def _auth(s) -> str:
    raw = f"{s['wp_user']}:{s['wp_pass']}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def api_root(site) -> str:
    """The REST address for a website: https://example.com/wp-json/wp/v2"""
    site = (site or "").strip().rstrip("/")
    if not re.match(r"^https?://", site, re.I):
        site = "https://" + site
    return site + "/wp-json/wp/v2"


def normalise_password(p) -> str:
    """WordPress shows them in groups of four with spaces; it accepts either."""
    return re.sub(r"\s+", "", str(p or ""))


# A WordPress host that is busy for a moment, or a line that drops, is asked
# again after these pauses — reads always, and changes only when the request
# never left this computer (a page saved twice is harmless; a post is not).
RETRY_WAITS = (2, 5)
PASSING = {408, 429, 502, 503, 504, 508, 520, 521, 522, 523, 524}


def _req(s, path, method="GET", body=None, query=None, timeout=TIMEOUT):
    """(status, json or None, error text). Never raises."""
    for n in range(len(RETRY_WAITS) + 1):
        status, out, err, unsent = _req_once(s, path, method, body, query, timeout)
        passing = status == 0 or status in PASSING
        if not passing or n == len(RETRY_WAITS) or not (method == "GET" or unsent):
            return status, out, err
        time.sleep(RETRY_WAITS[n])
    return status, out, err


def _req_once(s, path, method, body, query, timeout):
    url = api_root(s["site"]) + path
    if query:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(query)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", _auth(s))
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", "Botix agency (WordPress connection)")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            status = r.status
    except urllib.error.HTTPError as e:
        raw = (e.read() or b"").decode("utf-8", "replace")
        status = e.code
    except Exception as e:
        inner = getattr(e, "reason", e)
        return (0, None, f"could not reach your website: {type(e).__name__}: {e}",
                isinstance(inner, (ConnectionRefusedError, socket.gaierror)))
    try:
        out = json.loads(raw) if raw.strip() else None
    except ValueError:
        out = None
    if 200 <= status < 300:
        return status, out, "", False
    msg = (out or {}).get("message") if isinstance(out, dict) else ""
    return status, out, msg or f"HTTP {status} from your website", False


def _failed(status, err) -> dict:
    out = {"ok": False, "status": status}
    if status == 0:
        out.update(unreachable=True, error=err)
    elif status == 401:
        out["error"] = ("WordPress refused the username and Application Password. Make a "
                        "new one in your WordPress profile (Users → Profile → Application "
                        "Passwords) and paste it again.")
    elif status == 403:
        out["error"] = ("WordPress accepted the password but this user may not do that. "
                        "Use an administrator's username, or an editor's for posts and pages.")
    elif status == 404:
        out["error"] = ("nothing answered at your website's WordPress address. Is it a "
                        "WordPress site, and is the REST API switched on?")
    else:
        out["error"] = err
    return out


def _slug_of(page) -> str:
    """'contact', '/contact/', 'contact.php' and 'about-us/team' all name a page by its slug."""
    p = str(page or "").strip().strip("/")
    p = p.split("/")[-1] if p else ""
    return re.sub(r"\.(php|html?)$", "", p, flags=re.I).lower()


def _find(s, kind, slug):
    """The page or post at this slug, any status, with its raw content — or None."""
    status, out, err = _req(s, f"/{kind}", query={"slug": slug, "context": "edit",
                                                  "status": "publish,draft,private,pending,future"})
    if status != 200 or not isinstance(out, list):
        return None, (status, err)
    return (out[0] if out else None), (status, "")


def _front_page_id(s) -> int:
    status, out, _ = _req(s, "/settings")
    if status == 200 and isinstance(out, dict):
        try:
            return int(out.get("page_on_front") or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def _raw(item) -> str:
    c = (item or {}).get("content") or {}
    return c.get("raw") if isinstance(c, dict) and c.get("raw") is not None else (c.get("rendered") or "")


# ---------------------------------------------------------------------------
# the calls
# ---------------------------------------------------------------------------

def health(s) -> dict:
    status, me, err = _req(s, "/users/me", query={"context": "edit"})
    if status != 200 or not isinstance(me, dict):
        return _failed(status, err)
    caps = me.get("capabilities") or {}
    can = [word for k, word in (("edit_pages", "pages"), ("publish_posts", "blog"),
                                ("upload_files", "pictures"), ("unfiltered_html", "head"))
           if caps.get(k)]
    if caps.get("edit_pages"):
        can.append("rollback")
    warn = []
    if not caps.get("edit_pages"):
        warn.append("this user cannot edit pages — IT can only read your website")
    if not caps.get("unfiltered_html"):
        warn.append("this user cannot add structured data to pages (only an administrator can)")
    return {"ok": True, "status": "up", "mode": "wordpress", "connector": "wordpress",
            "user": me.get("name") or me.get("slug") or "", "can": can, "warnings": warn}


def pages(s) -> dict:
    out, page_no = [], 1
    while page_no <= 5:
        status, rows, err = _req(s, "/pages", query={
            "context": "edit", "per_page": 100, "page": page_no,
            "status": "publish,draft,private", "_fields": "id,slug,link,modified,content,status"})
        if status != 200 or not isinstance(rows, list):
            if out:
                break
            return _failed(status, err)
        for r in rows:
            out.append({"page": r.get("slug") or str(r.get("id")),
                        "bytes": len(_raw(r).encode("utf-8")),
                        "changed": r.get("modified") or "", "status": r.get("status") or "",
                        "link": r.get("link") or ""})
        if len(rows) < 100:
            break
        page_no += 1
    return {"ok": True, "pages": out, "counted": len(out), "capped": page_no > 5}


def read(s, page) -> dict:
    slug = _slug_of(page)
    if slug in HOME_SLUGS:
        fid = _front_page_id(s)
        if not fid:
            return {"ok": False, "status": 404, "home": True,
                    "error": ("your WordPress shows your latest posts as its home page, so "
                              "there is no home page to read or change")}
        status, item, err = _req(s, f"/pages/{fid}", query={"context": "edit"})
        if status != 200 or not isinstance(item, dict):
            return _failed(status, err)
    else:
        item, (status, err) = _find(s, "pages", slug)
        if status != 200:
            return _failed(status, err)
        if item is None:
            return {"ok": False, "status": 404, "error": "no such page"}
    return {"ok": True, "page": item.get("slug") or slug, "html": _raw(item),
            "bytes": len(_raw(item).encode("utf-8")), "changed": item.get("modified") or "",
            "id": item.get("id")}


def write(s, page, html) -> dict:
    slug = _slug_of(page)
    if not slug:
        return {"ok": False, "status": 400, "error": "which page?"}
    if not str(html or "").strip():
        return {"ok": False, "status": 400, "error": "refusing to write an empty page"}
    got = read(s, page)
    if got.get("ok"):
        if got["html"] == html:
            return {"ok": True, "page": got["page"], "action": "unchanged", "backup": ""}
        st, out, err = _req(s, f"/pages/{got['id']}", "POST", {"content": html})
        if st != 200:
            return _failed(st, err)
        return {"ok": True, "page": got["page"], "action": "updated",
                "backup": "WordPress revision", "id": got["id"]}
    if got.get("status") != 404 or got.get("home"):
        return got
    title = slug.replace("-", " ").strip().capitalize() or "New page"
    st, out, err = _req(s, "/pages", "POST", {"slug": slug, "title": title,
                                              "content": html, "status": "publish"})
    if st not in (200, 201):
        return _failed(st, err)
    return {"ok": True, "page": slug, "action": "created", "backup": "",
            "id": (out or {}).get("id")}


def head_block(s, page, mark, block) -> dict:
    """A marked block at the end of the page's content — replaced in place next time."""
    mark = re.sub(r"[^a-z0-9\-]", "", str(mark or ""), flags=re.I)
    if not mark or not str(block or "").strip():
        return {"ok": False, "status": 400, "error": "need page, id and html"}
    got = read(s, page)
    if not got.get("ok"):
        return got
    src = got["html"]
    open_, close = MARK_OPEN.format(id=mark), MARK_CLOSE.format(id=mark)
    wrapped = f"{open_}\n{block}\n{close}"
    pat = re.compile(re.escape(open_) + r".*?" + re.escape(close), re.S)
    new = pat.sub(lambda _m: wrapped, src) if pat.search(src) else src.rstrip() + "\n\n" + wrapped + "\n"
    if new == src:
        return {"ok": True, "page": got["page"], "action": "unchanged"}
    st, out, err = _req(s, f"/pages/{got['id']}", "POST", {"content": new})
    if st != 200:
        return _failed(st, err)
    return {"ok": True, "page": got["page"], "action": "block placed", "where": "end of the page",
            "backup": "WordPress revision"}


def revisions(s, page_id) -> list:
    st, out, _ = _req(s, f"/pages/{page_id}/revisions", query={"context": "edit", "per_page": 20})
    return out if st == 200 and isinstance(out, list) else []


def rollback(s, page="", backup="") -> dict:
    got = read(s, page)
    if not got.get("ok"):
        return got
    revs = revisions(s, got["id"])
    # Newest first. The newest revision is the page as it is now; the one
    # after it is how it was before the last change.
    older = [r for r in revs if _raw(r) != got["html"]]
    if backup:
        older = [r for r in revs if str(r.get("id")) == str(backup)]
    if not older:
        return {"ok": False, "status": 404, "error": "WordPress has no earlier version of this page"}
    back = older[0]
    st, out, err = _req(s, f"/pages/{got['id']}", "POST", {"content": _raw(back)})
    if st != 200:
        return _failed(st, err)
    return {"ok": True, "page": got["page"], "restored": str(back.get("id")),
            "from": back.get("modified") or ""}


def backups(s) -> dict:
    listed = pages(s)
    if not listed.get("ok"):
        return listed
    recent = sorted(listed["pages"], key=lambda p: p.get("changed") or "", reverse=True)[:10]
    out = []
    for p in recent:
        got = read(s, p["page"])
        if got.get("ok"):
            for r in revisions(s, got["id"])[1:4]:
                out.append({"backup": str(r.get("id")), "page": p["page"],
                            "made": r.get("modified") or ""})
    return {"ok": True, "backups": out}


# ---- posts ------------------------------------------------------------------

def _ledger_path():
    return workspace.config_path(LEDGER)


def _ledger() -> list:
    try:
        return json.loads(workspace.read_secret(_ledger_path(), "[]") or "[]")
    except Exception:
        return []


def _remember(post_id, slug):
    rows = [r for r in _ledger() if str(r.get("id")) != str(post_id)]
    rows.append({"id": str(post_id), "slug": slug})
    workspace.write_secret(_ledger_path(), json.dumps(rows[-500:], indent=1))


def _pick(post, *words):
    """The value of the first key containing any of these words."""
    for k, v in (post or {}).items():
        if any(w in str(k).lower() for w in words) and v not in (None, ""):
            return v
    return ""


def blog_lookup(s, slug="") -> dict:
    if not slug:
        return {"ok": True, "wordpress": True, "table": "WordPress posts",
                "columns": ["title", "slug", "content", "excerpt", "status"]}
    item, (status, err) = _find(s, "posts", _slug_of(slug))
    if status != 200:
        return _failed(status, err)
    return {"ok": True, "wordpress": True, "taken": item is not None,
            "id": (item or {}).get("id")}


def blog_add(s, post, image=None) -> dict:
    title = str(_pick(post, "title", "heading", "name") or "").strip()
    content = str(_pick(post, "content", "body", "html", "text") or "")
    slug = _slug_of(_pick(post, "slug") or title.lower().replace(" ", "-"))
    excerpt = str(_pick(post, "excerpt", "summary", "intro", "description") or "")
    if not title or not content.strip():
        return {"ok": False, "status": 400, "error": "a post needs a title and its words"}
    existing, (status, err) = _find(s, "posts", slug)
    if status != 200:
        return _failed(status, err)
    if existing is not None:
        return {"ok": False, "taken": True, "id": existing.get("id"),
                "error": f"there is already a post at /{slug}"}
    body = {"title": title, "slug": slug, "content": content, "status": "publish"}
    if excerpt:
        body["excerpt"] = excerpt
    st, out, err = _req(s, "/posts", "POST", body, timeout=60)
    if st not in (200, 201) or not isinstance(out, dict):
        return _failed(st, err)
    _remember(out.get("id"), slug)
    note = ""
    if image:
        note = "the picture was not added: WordPress pictures go in through the Media Library"
    return {"ok": True, "id": out.get("id"), "slug": slug, "link": out.get("link") or "",
            "note": note}


def blog_remove(s, post_id) -> dict:
    mine = {str(r.get("id")): r for r in _ledger()}
    if str(post_id) not in mine:
        return {"ok": False, "status": 403,
                "error": "that post was not added by the agency, so it is left alone"}
    st, out, err = _req(s, f"/posts/{post_id}", "DELETE")
    if st != 200:
        return _failed(st, err)
    return {"ok": True, "id": str(post_id), "slug": mine[str(post_id)].get("slug", ""),
            "note": "moved to the WordPress bin — restore it there if you change your mind"}


def picture_add(s, data_b64, name="picture") -> dict:
    """A picture for inside a post, into the WordPress Media Library."""
    try:
        raw = base64.b64decode(str(data_b64 or ""), validate=True)
    except Exception:
        return {"ok": False, "status": 400, "error": "the picture was damaged on the way"}
    kinds = {b"\x89PNG": ("png", "image/png"), b"\xff\xd8\xff": ("jpg", "image/jpeg"),
             b"RIFF": ("webp", "image/webp"), b"GIF8": ("gif", "image/gif")}
    kind = next((v for k, v in kinds.items() if raw.startswith(k)), None)
    if not kind:
        return {"ok": False, "status": 400, "error": "that is not a picture"}
    fname = (re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-") or "picture")[:50] + "." + kind[0]
    req = urllib.request.Request(api_root(s["site"]) + "/media", data=raw, method="POST")
    req.add_header("Authorization", _auth(s))
    req.add_header("Content-Type", kind[1])
    req.add_header("Content-Disposition", f'attachment; filename="{fname}"')
    req.add_header("User-Agent", "Botix agency (WordPress connection)")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            out = json.loads(r.read().decode("utf-8", "replace") or "{}")
    except urllib.error.HTTPError as e:
        return _failed(e.code, f"HTTP {e.code} from your website")
    except Exception as e:
        return {"ok": False, "error": f"could not reach your website: {type(e).__name__}"}
    url = (out or {}).get("source_url") or ""
    return {"ok": bool(url), "url": url, "path": url, "id": (out or {}).get("id"),
            **({} if url else {"error": "WordPress did not say where it saved the picture"})}


def page_remove(s, page) -> dict:
    slug = _slug_of(page)
    if slug in HOME_SLUGS:
        return {"ok": False, "status": 403, "error": "your home page is never removed"}
    got = read(s, page)
    if not got.get("ok"):
        if got.get("status") == 404:
            return {"ok": True, "page": slug, "action": "already gone", "backup": ""}
        return got
    if got["id"] == _front_page_id(s):
        return {"ok": False, "status": 403, "error": "your home page is never removed"}
    st, out, err = _req(s, f"/pages/{got['id']}", "DELETE")
    if st != 200:
        return _failed(st, err)
    return {"ok": True, "page": slug, "action": "removed", "backup": "WordPress bin",
            "note": "in the WordPress bin — restore it there to put it back"}


# ---------------------------------------------------------------------------
# the one entrance site_api uses
# ---------------------------------------------------------------------------

def call(path, method="GET", body=None, s=None) -> dict:
    """Answer one connector call by asking WordPress. Never raises."""
    body = body or {}
    route = "/" + str(path or "").lstrip("/")
    try:
        if route == "/health":
            return health(s)
        if route == "/pages" and method == "GET":
            return pages(s)
        if route == "/pages" and method == "POST":
            return write(s, body.get("page") or body.get("slug") or "", body.get("html") or "")
        if route.startswith("/pages/") and method == "GET":
            return read(s, urllib.parse.unquote(route[len("/pages/"):]))
        if route == "/head" and method == "POST":
            return head_block(s, body.get("page") or "", body.get("id") or "", body.get("html") or "")
        if route == "/rollback":
            return rollback(s, body.get("page") or "", body.get("backup") or "")
        if route == "/backups":
            return backups(s)
        if route == "/blog" and method == "GET":
            return blog_lookup(s)
        if route.startswith("/blog/") and method == "GET":
            return blog_lookup(s, urllib.parse.unquote(route[len("/blog/"):]))
        if route == "/blog" and method == "POST":
            return blog_add(s, body.get("post") or {}, body.get("image"))
        if route == "/blog-remove":
            return blog_remove(s, body.get("id") or "")
        if route == "/pages-remove":
            return page_remove(s, body.get("page") or "")
        if route == "/picture" and method == "POST":
            return picture_add(s, body.get("data") or "", body.get("name") or "picture")
        if route == "/contact":
            return {"ok": False, "status": 404, "error": (
                "the contact-form test is not available on a WordPress connection — "
                "send yourself a test enquiry from your contact page instead")}
    except Exception as e:                                    # pragma: no cover
        return {"ok": False, "error": f"WordPress connection error: {type(e).__name__}: {e}"}
    return {"ok": False, "status": 404, "old_connector": True,
            "error": f"a WordPress connection cannot do {route} — it is handed to you instead"}
