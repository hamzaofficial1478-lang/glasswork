"""
posts.py — the Content screen's controls: today's topic, delete, open, edit, score.

    python -m coordination.posts --topics                      what SEO suggests writing about
    echo '{"key": "..."}' | python -m coordination.posts --choose
    echo '{"topic": "..."}' | python -m coordination.posts --choose
    python -m coordination.posts --write ID                    the writer, on that one post, now
    python -m coordination.posts --get ID                      one post, for the editor
    echo '{"title": "...", "meta": "...", "html": "..."}' | python -m coordination.posts --save ID
    echo '{"title": "...", "meta": "...", "html": "..."}' | python -m coordination.posts --score ID
    python -m coordination.posts --delete ID                   a post that is not on the website

"in being written section we cannot select that on which topic today user want
 to write the post ... in the publish posts make two buttons 1 of delete from
 website ... and 2nd of edit that will update the post on website"

THE RULES IT KEEPS

  - A topic comes from SEO. The suggestions are SEO's own keyword findings
    (keyword_intel), grouped the way the briefs are; a topic typed in has to
    name a service the business sells, and SEO builds its keyword plan for it
    when it is written (pipelines/seo_keywords.plan_for). Content never decides
    on its own that something is worth writing.
  - An edit is HTML from the editor, cleaned to an allow-list before anything
    else sees it: no scripts, no frames, no event handlers, no javascript: links.
  - An edited post goes through the Auditor exactly like a written one. If it
    fails, the edit is kept and nothing goes on the website.
  - Only the IT Releaser writes to the website. Saving a post that is already
    live queues the Releaser's re-post (review._repost), tests first; a picture
    added in the editor waits on this computer until the Releaser takes it up
    with the post (blog_backend._upload_pending).
"""
import argparse
import base64
import hashlib
import json
import re
import sqlite3
import sys
import time
from html import escape
from html.parser import HTMLParser
from pathlib import Path

try:
    from . import board
except ImportError:  # pragma: no cover
    import board

PENDING = "agency-pending:"
PREVIEW = "/api/content/pending/"
PICTURE_MAX = 6 * 1024 * 1024
MAGIC = {b"\x89PNG": ("png", "image/png"), b"\xff\xd8\xff": ("jpg", "image/jpeg"),
         b"RIFF": ("webp", "image/webp"), b"GIF8": ("gif", "image/gif")}


def _conn():
    c = sqlite3.connect(str(board.DB), timeout=30)
    c.row_factory = sqlite3.Row
    return c


def _json(raw, default=None):
    try:
        v = json.loads(raw or "")
        return v if v is not None else default
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# today's topic
# ---------------------------------------------------------------------------

def _evidence(rows):
    seen = sum(int(r["impressions"] or 0) for r in rows)
    striking = sum(1 for r in rows if r["striking"])
    asked = sum(1 for r in rows if r["in_enquiries"])
    rivals = max([int(r["rival_pages"] or 0) for r in rows] or [0])
    score = round(sum(float(r["score"] or 0) for r in rows), 1)
    why = []
    if seen:
        why.append(f"your site already showed up {seen} times on Google for these searches")
    if striking:
        why.append(f"{striking} of them {'sits' if striking == 1 else 'sit'} just off the first page, "
                   "where a good post moves them up")
    if asked:
        why.append("your own customers used these words")
    if rivals:
        why.append(f"{rivals} competitor page(s) are written for it")
    return {"searches_seen": seen, "near_first_page": striking, "customers_asked": asked,
            "rival_pages": rivals, "score": score,
            "why": ("; ".join(why) or "SEO found people searching for it") + "."}


def topics(most=12) -> dict:
    """SEO's suggestions, best first, and what is already waiting to be written."""
    from pipelines import content_briefs
    c = _conn()
    try:
        board._migrate(c)
        try:
            kw = [dict(r) for r in c.execute(
                "SELECT phrase, area, intent, score, impressions, striking, in_enquiries, rival_pages"
                " FROM keyword_intel WHERE COALESCE(we_have_page,0)=0 ORDER BY score DESC LIMIT 400")]
        except sqlite3.Error:
            kw = []
        taken = content_briefs.existing_pages(c)
        waiting = []
        for r in c.execute("SELECT id, title, brief, status FROM content_queue WHERE"
                           " ((status='briefed' AND COALESCE(draft,'')='') OR status LIKE 'blocked_on_%')"
                           " ORDER BY id DESC LIMIT 20"):
            b = _json(r["brief"], {}) or {}
            waiting.append({"id": r["id"], "title": r["title"] or "", "keyword": b.get("primary_keyword") or "",
                            "chosen_by_you": b.get("chosen_by") == "owner",
                            "held_for_seo": str(r["status"] or "").startswith("blocked_on_")})
    finally:
        c.close()
    by_phrase = {r["phrase"]: r for r in kw}
    out = []
    for g in content_briefs.cluster([(r["phrase"], r["area"], r["intent"]) for r in kw]):
        b = content_briefs.make_brief(g, len(out) + 1)
        if b["url"] in taken or ("title:" + b["title"]) in taken:
            continue
        rows = [by_phrase[p] for p in g["phrases"] if p in by_phrase]
        ev = _evidence(rows)
        out.append({"key": b["url"].strip("/"), "title": b["title"], "primary": b["primary_keyword"],
                    "phrases": g["phrases"][:6], "buying": g["buy"], **ev})
    out.sort(key=lambda t: (-t["buying"], -t["score"], -t["searches_seen"]))
    try:
        from coordination import profile as _profile
        services = [(v or {}).get("label") or k.replace("_", " ") for k, v in (_profile.services() or {}).items()][:20]
    except Exception:
        services = []
    from coordination import review
    return {"ok": True, "topics": out[:most], "waiting": waiting, "services": [s for s in services if s],
            "writing": review.busy("content-writer"),
            "head": (f"SEO suggests {min(len(out), most)} topic(s), best first." if out else
                     "SEO has no topic waiting yet — run the day so SEO looks, or type one of your services.")}


def choose(key=None, topic=None) -> dict:
    """Make the brief for the topic picked, ready to be written."""
    from pipelines import content_briefs
    topic = re.sub(r"\s+", " ", str(topic or "")).strip()[:120]
    g, chosen_key = None, str(key or "").strip()
    if chosen_key:
        found = topics(most=60)
        pick = next((t for t in found["topics"] if t["key"] == chosen_key), None)
        if not pick:
            return {"ok": False, "error": "That topic is no longer on SEO's list — choose again."}
        g = {"service": content_briefs._service_of(pick["primary"]),
             "area": content_briefs._area_of(pick["primary"]), "phrases": pick["phrases"],
             "buy": pick["buying"], "local": 0, "info": 0}
        evidence = pick["why"]
    elif topic:
        svc = content_briefs._service_of(topic.lower())
        if not svc:
            return {"ok": False, "error": (f"SEO cannot see a service your business sells in “{topic}”. "
                                           "Name the service in it, or choose one of SEO's suggestions.")}
        c = _conn()
        try:
            board._migrate(c)
            words = [w for w in re.findall(r"[a-z]{4,}", svc.lower())]
            try:
                near = [r["phrase"] for r in c.execute(
                    "SELECT phrase FROM keyword_intel ORDER BY score DESC LIMIT 400")
                    if any(w in r["phrase"].lower() for w in words)][:10]
            except sqlite3.Error:
                near = []
        finally:
            c.close()
        g = {"service": svc, "area": content_briefs._area_of(topic.lower()), "phrases": [topic.lower()] + near,
             "buy": 1, "local": 0, "info": 0}
        evidence = ("chosen by you; " + (f"SEO matched {len(near)} search(es) it found" if near else
                                         "SEO builds its keyword plan when the post is written"))
    else:
        return {"ok": False, "error": "Choose a topic, or type one."}
    brief = content_briefs.make_brief(g, 1)
    if topic:
        brief["title"] = topic[:1].upper() + topic[1:]
        brief["primary_keyword"] = topic.lower()
    brief.update(type="blog", chosen_by="owner")
    brief["evidence"] = {**(brief.get("evidence") or {}), "why": evidence, "chosen_at": int(time.time())}
    with board._conn() as w:
        board._migrate(w)
        cur = w.execute("INSERT INTO content_queue (title, type, brief, status, created_at) VALUES (?,?,?,?,?)",
                        (brief["title"], "blog", json.dumps(brief), "briefed", int(time.time())))
        cid = cur.lastrowid
    return {"ok": True, "id": cid, "title": brief["title"],
            "head": f"“{brief['title']}” is next. SEO plans the keywords, then the writer writes it."}


def write(cid) -> dict:
    """The writer, on that one post, now (started in the background by the office)."""
    if not board.sector_enabled("Content"):
        return {"ok": False, "error": "Content is switched off."}
    from coordination import review
    if review.busy("content-writer"):
        return {"ok": False, "error": "Content is already writing."}
    from pipelines import content_write
    return content_write.run(only_id=int(cid), limit=1)


def delete(cid) -> dict:
    """Put away a post that is not on the website. One that is live is taken off first."""
    with board._conn() as c:
        board._migrate(c)
        r = c.execute("SELECT title, status, draft FROM content_queue WHERE id=?", (int(cid),)).fetchone()
        if not r:
            return {"ok": False, "error": "no such post"}
        blog = (_json(r["draft"], {}) or {}).get("_for_blog") or {}
        if r["status"] == "published" and (blog.get("backend") or {}).get("id"):
            return {"ok": False, "error": "It is on your website — press Delete from website first."}
        c.execute("UPDATE content_queue SET status='rejected' WHERE id=?", (int(cid),))
        for s in c.execute("SELECT id, detail FROM site_queue WHERE kind='new_page' AND status='pending'").fetchall():
            ch = (_json(s["detail"], {}) or {}).get("change") or {}
            if ch.get("content_id") == int(cid):
                board.decide_site_change(s["id"], "rejected")
    return {"ok": True, "head": f"Deleted “{r['title'] or 'the post'}”. It will not be offered again."}


# ---------------------------------------------------------------------------
# the editor
# ---------------------------------------------------------------------------

_TAGS = {"p", "br", "h2", "h3", "h4", "strong", "b", "em", "i", "u", "s", "a", "ul", "ol", "li",
         "blockquote", "img", "span", "figure", "figcaption", "table", "thead", "tbody", "tr",
         "th", "td", "hr", "mark", "small", "sub", "sup", "div"}
_VOID = {"br", "img", "hr"}
_DROP = {"script", "style", "iframe", "object", "embed", "form", "input", "button", "select",
         "textarea", "noscript", "template", "svg", "math", "link", "meta", "head", "title"}
_STYLE = {
    "color": r"#[0-9a-f]{3,8}|rgba?\([\d\s.,%]+\)|[a-z]{3,20}",
    "background-color": r"#[0-9a-f]{3,8}|rgba?\([\d\s.,%]+\)|[a-z]{3,20}",
    "font-size": r"\d{1,3}(\.\d)?(px|em|rem|%)|x-small|small|medium|large|x-large|xx-large",
    "font-family": r"[a-z0-9 ,'\"\-]{1,80}",
    "font-weight": r"bold|normal|[1-9]00",
    "font-style": r"italic|normal",
    "text-decoration": r"underline|line-through|none",
    "text-align": r"left|right|center|justify",
}


def _safe_url(v, img=False) -> str:
    v = str(v or "").strip()
    low = v.lower()
    if img and (low.startswith(PENDING) or low.startswith(PREVIEW)):
        return v
    if re.match(r"^(https?:|mailto:|tel:)", low) or (v.startswith("/") and not v.startswith("//")) or v.startswith("#"):
        return v if not img or not low.startswith(("mailto:", "tel:", "#")) else ""
    return ""


def _safe_style(v) -> str:
    keep = []
    for part in str(v or "").split(";"):
        if ":" not in part:
            continue
        k, val = (x.strip().lower() for x in part.split(":", 1))
        pat = _STYLE.get(k)
        if pat and re.fullmatch(pat, val, re.I) and "url(" not in val and "expression" not in val:
            keep.append(f"{k}: {val}")
    return "; ".join(keep)


class _Clean(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.stack, self.drop = [], [], 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in _DROP:
            if tag not in _VOID:
                self.drop += 1
            return
        if self.drop:
            return
        if tag == "h1":
            tag = "h2"            # the title is the post's one heading at the top
        if tag not in _TAGS:
            return
        keep = []
        for k, v in attrs:
            k = (k or "").lower()
            if k.startswith("on"):
                continue
            if tag == "a" and k == "href":
                u = _safe_url(v)
                if u:
                    keep.append(("href", u))
            elif tag == "a" and k == "target" and v == "_blank":
                keep += [("target", "_blank"), ("rel", "noopener")]
            elif tag == "img" and k == "src":
                u = _safe_url(v, img=True)
                if u:
                    keep.append(("src", u))
            elif tag == "img" and k in ("alt", "title"):
                keep.append((k, str(v or "")[:200]))
            elif tag == "img" and k in ("width", "height") and re.fullmatch(r"\d{1,4}", str(v or "")):
                keep.append((k, v))
            elif k == "style":
                st = _safe_style(v)
                if st:
                    keep.append(("style", st))
        if tag == "img" and not any(k == "src" for k, _ in keep):
            return
        self.out.append(f"<{tag}" + "".join(f' {k}="{escape(str(v), quote=True)}"' for k, v in keep) + ">")
        if tag not in _VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag.lower() not in _VOID and tag.lower() in _TAGS and self.stack and self.stack[-1] == tag.lower():
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in _DROP:
            self.drop = max(0, self.drop - 1)
            return
        if self.drop:
            return
        tag = "h2" if tag == "h1" else tag
        if tag in self.stack:
            while self.stack:
                t = self.stack.pop()
                self.out.append(f"</{t}>")
                if t == tag:
                    break

    def handle_data(self, data):
        if not self.drop:
            self.out.append(escape(data, quote=False))

    def result(self):
        while self.stack:
            self.out.append(f"</{self.stack.pop()}>")
        return "".join(self.out).strip()


def clean_html(html) -> str:
    """The editor's HTML, cut down to what a post may hold."""
    p = _Clean()
    p.feed(str(html or ""))
    p.close()
    return p.result()


def pending_dir() -> Path:
    return Path(board.DB).parent / "pictures-pending"


def pending_bytes(name) -> bytes:
    if not re.fullmatch(r"[a-f0-9]{16,40}\.(jpg|png|webp|gif)", str(name or "")):
        return b""
    f = pending_dir() / name
    return f.read_bytes() if f.is_file() else b""


def _keep_pictures(html):
    """Pictures pasted into the editor as data, kept here until the Releaser takes them up."""
    problems = []

    def one(m):
        mime, data = m.group(1).lower(), m.group(2)
        try:
            raw = base64.b64decode(data, validate=True)
        except Exception:
            problems.append("a picture was damaged on the way and was left out")
            return 'src=""'
        kind = next((v for k, v in MAGIC.items() if raw.startswith(k)), None)
        if not kind or len(raw) > PICTURE_MAX:
            problems.append("a picture was not a PNG, JPEG, WebP or GIF under 6 MB, and was left out")
            return 'src=""'
        name = hashlib.sha256(raw).hexdigest()[:24] + "." + kind[0]
        pending_dir().mkdir(parents=True, exist_ok=True)
        f = pending_dir() / name
        if not f.exists():
            f.write_bytes(raw)
        return f'src="{PENDING}{name}"'
    out = re.sub(r'src="data:(image/[a-z+.-]+);base64,([A-Za-z0-9+/=\s]+)"', one, str(html or ""), flags=re.I)
    out = out.replace(f'src="{PREVIEW}', f'src="{PENDING}')
    return out, problems


def _for_editor(html):
    return str(html or "").replace(f'src="{PENDING}', f'src="{PREVIEW}')


def _article(title, meta, html):
    """What the Auditor reads: the post on its own, its title as the heading."""
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            f"<title>{escape(title)}</title><meta name=\"description\" content=\"{escape(meta, quote=True)}\">"
            f"</head><body><main><h1>{escape(title)}</h1>\n{html}\n</main></body></html>")


def _load(cid):
    from pipelines import post_quality
    c = _conn()
    try:
        r = c.execute("SELECT id, title, brief, draft, status FROM content_queue WHERE id=?", (int(cid),)).fetchone()
        if not r:
            return None
        brief = _json(r["brief"], {}) or {}
        others = post_quality.others(c, int(cid), brief.get("seo_pack"))
        return {"row": dict(r), "brief": brief, "draft": _json(r["draft"], {}) or {}, "others": others}
    finally:
        c.close()


def _page_of(got):
    d, blog = got["draft"], got["draft"].get("_for_blog") or {}
    return {"title": blog.get("title") or d.get("title") or got["row"]["title"] or "",
            "meta": blog.get("meta") or d.get("meta") or "",
            "html": blog.get("html") or d.get("_page_html") or d.get("html") or ""}


def _report(page, got):
    from pipelines import post_quality
    pg = {**page, "html": f"<h1>{escape(page['title'])}</h1>" + page["html"], "h1": page["title"]}
    return post_quality.report(pg, got["brief"].get("seo_pack"), got["others"], "blog")


def get(cid) -> dict:
    got = _load(cid)
    if not got:
        return {"ok": False, "error": "no such post"}
    page = _page_of(got)
    blog = got["draft"].get("_for_blog") or {}
    status = got["row"]["status"]
    live = status == "published" and bool((blog.get("backend") or {}).get("id"))
    return {"ok": True, "id": int(cid), "title": page["title"], "meta": page["meta"],
            "html": _for_editor(page["html"]), "status": status, "url": blog.get("posted_url") or "",
            "on_website": live, "can_edit": status in ("for_blog", "published", "in_review", "drafted"),
            "keyword": got["brief"].get("primary_keyword") or "", "report": _report(page, got)}


def score(cid, title, meta, html) -> dict:
    got = _load(cid)
    if not got:
        return {"ok": False, "error": "no such post"}
    body, _p = _keep_pictures(html)
    body = clean_html(body)
    page = {"title": str(title or "").strip()[:120], "meta": str(meta or "").strip()[:160], "html": body}
    return {"ok": True, "report": _report(page, got)}


def save(cid, title, meta, html) -> dict:
    """Keep the edit; put it on the website again if it is live and the Auditor passes it."""
    got = _load(cid)
    if not got:
        return {"ok": False, "error": "no such post"}
    title = re.sub(r"\s+", " ", str(title or "")).strip()[:120]
    meta = re.sub(r"\s+", " ", str(meta or "")).strip()[:160]
    if not title:
        return {"ok": False, "error": "A post needs a title."}
    body, problems = _keep_pictures(html)      # pictures set aside first: the cleaner drops data
    body = clean_html(body)
    if not re.sub(r"<[^>]+>", "", body).strip():
        return {"ok": False, "error": "The post is empty."}
    from coordination import auditor
    from pipelines import content_write
    try:
        from pipelines import site_api
        host = re.sub(r"^https?://(www\.)?", "", site_api.settings().get("site") or "").split("/")[0]
    except Exception:
        host = ""
    verdict = auditor.check_page(_article(title, meta, body), got["brief"], site_host=host)
    draft, row = got["draft"], got["row"]
    blog = draft.get("_for_blog") or {}
    blog.update(title=title, meta=meta, html=body, text=content_write._as_text(body),
                edited_at=int(time.time()))
    draft.update(_for_blog=blog, title=title, meta=meta, html=body, _edited_by_owner=True,
                 _held_because=verdict["blocking"] if not verdict["pass"] else [])
    status = row["status"]
    if status in ("in_review", "drafted") and verdict["pass"]:
        status = "for_blog"                 # the edit put right what held it
    with board._conn() as c:
        board._migrate(c)
        c.execute("UPDATE content_queue SET title=?, draft=?, status=? WHERE id=?",
                  (title, json.dumps(draft, indent=2), status, int(cid)))
    report = _report({"title": title, "meta": meta, "html": body}, got)
    live = row["status"] == "published" and bool((blog.get("backend") or {}).get("id"))
    note = (" " + " ".join(problems)) if problems else ""
    if not verdict["pass"]:
        return {"ok": True, "saved": True, "published": False, "audit": verdict, "report": report,
                "head": "Saved, but not put on your website: the Auditor stopped it — "
                        + verdict["blocking"][0] + note}
    if live:
        from coordination import review
        r = review._repost(int(cid))
        return {"ok": True, "saved": True, "published": bool(r.get("ok")), "audit": verdict, "report": report,
                "head": "Saved. " + (r.get("head") or r.get("error") or "") + note}
    return {"ok": True, "saved": True, "published": False, "audit": verdict, "report": report,
            "head": "Saved." + (" It is ready for your blog — press Put it on my website." if status == "for_blog" else "") + note}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="The Content screen's controls.")
    ap.add_argument("--topics", action="store_true")
    ap.add_argument("--choose", action="store_true", help="read {key} or {topic} on stdin")
    ap.add_argument("--write", type=int, metavar="ID")
    ap.add_argument("--get", type=int, metavar="ID")
    ap.add_argument("--save", type=int, metavar="ID", help="read {title, meta, html} on stdin")
    ap.add_argument("--score", type=int, metavar="ID", help="read {title, meta, html} on stdin")
    ap.add_argument("--delete", type=int, metavar="ID")
    ap.add_argument("--pending-path", metavar="NAME", help="where a picture added in the editor is kept")
    a = ap.parse_args(argv)
    try:
        if a.topics:
            out = topics()
        elif a.choose:
            d = json.loads(sys.stdin.read() or "{}")
            out = choose(d.get("key"), d.get("topic"))
        elif a.write:
            out = write(a.write)
        elif a.get:
            out = get(a.get)
        elif a.save or a.score:
            d = json.loads(sys.stdin.read() or "{}")
            fn = save if a.save else score
            out = fn(a.save or a.score, d.get("title"), d.get("meta"), d.get("html"))
        elif a.delete:
            out = delete(a.delete)
        elif a.pending_path:
            f = pending_dir() / a.pending_path
            out = ({"ok": True, "path": str(f.resolve())} if pending_bytes(a.pending_path)
                   else {"ok": False, "error": "no such picture"})
        else:
            out = topics()
    except Exception as e:
        out = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    print(json.dumps(out, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
