"""
changelog.py — what actually changed on the website, and when.

    python -m coordination.changelog            the last 30 changes
    python -m coordination.changelog --all      everything on file
    python -m coordination.changelog --json

"there should be a section in program where i can see whats changed in
 website... that's totally blind."

He was right, and it was worse than he thought. Nothing recorded a change to
his site at all. site_queue held what an agent PROPOSED. site_issues held what
was WRONG. Neither said what had actually been written, when, by whom, or how
to put it back — the only trace was a .bak file on his server with a timestamp
in the name, which you have to know exists to go looking for.

THIS MODULE TOUCHES NO WEBSITE.

It reads and writes two tables and nothing else, on purpose. pipelines/site_api
calls record() at the moment it writes, and pipelines/site_watch hands it the
page listing it fetched. Keeping the fetching out means the recording cannot
fail because a site was down, and it means the whole thing is testable without
a network — which matters, because the one thing a change log must never do is
lose a change.

WHY 'OUTSIDE' ENTRIES ARE THE POINT

A log of what the agency did would be easy and half useless. He edits pages in
his file manager, his hosting company restores backups, a plugin rewrites
something overnight. He asked what changed on his WEBSITE. So every page is
fingerprinted, and anything that moved without us moving it is recorded as
somebody else's change — which is also the fastest way he will ever find out
that something got overwritten.
"""
import argparse
import hashlib
import re
from html import unescape as _unescape
import json
import sqlite3
import sys
import time

from . import board

AGENCY = "agency"

# How much a page may drift in size, with nothing visible changing, before it
# stops being a counter ticking over and starts being a real edit. Two bytes is
# a number going from 99 to 100; a person editing a page moves more than this
# or moves something describable. See sweep().
NOISE_BYTES = 24
OUTSIDE = "outside"

# How long after an agency write a page's fingerprint may move and still be
# put down to us. Belt and braces for the case where a write succeeded and the
# process died before it could refresh the fingerprint: without it, the next
# sweep would report the agency's own edit as somebody tampering with the site.
OURS_WINDOW = 900


# ---------------------------------------------------------------------------
# WHAT ACTUALLY CHANGED
# ---------------------------------------------------------------------------
#
# "there is section about website changes but it did not gave the detailed
#  berief about that ... either what is changed is any article is posted on
#  webiste is any code is changed"
#
# The log knew a page had moved and how many bytes by. That is the difference
# between a smoke alarm and a fire report. "home.html was changed by somebody
# other than the agency, 340 bytes bigger" tells him something happened and
# nothing about what, so he still has to go and open the file — which is the
# job the panel existed to save him.
#
# Answering it properly needs the OLD page, so the sweep now keeps a snapshot
# of each page beside its digest. Two passes later it can say: the title
# changed from this to that, two headings appeared, a script from a new host
# was added, the phone number in the footer is different.
#
# Deliberately shape and text, not a diff. A unified diff of minified HTML is
# unreadable and he would never look at it twice.

_SNAP_CHARS = 200_000     # a page far bigger than this is not a page

_TAG = re.compile(r"<[^>]+>")
_SCRIPTY = re.compile(r"<(script|style|noscript)\b[^>]*>.*?</\1\s*>", re.S | re.I)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.S | re.I)
_HEADING = re.compile(r"<h([1-3])[^>]*>(.*?)</h\1\s*>", re.S | re.I)
_IMG = re.compile(r"<img\b[^>]*>", re.I)
_SRC = re.compile(r"<script\b[^>]*\bsrc\s*=\s*[\"']([^\"']+)", re.I)
_HREF = re.compile(r"<a\b[^>]*\bhref\s*=\s*[\"']([^\"']+)", re.I)
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?:\+?971|0)\s?\d[\d\s\-()]{6,13}\d")
_WS = re.compile(r"\s+")


def _plain(s) -> str:
    """Markup off and entities decoded. A title written &amp; is an ampersand;
    comparing the raw source would report a change every time somebody's editor
    re-escaped it."""
    return _WS.sub(" ", _unescape(_TAG.sub(" ", s or ""))).strip()


def _visible(html) -> str:
    """The words a visitor reads, with the markup and the code taken out."""
    return _plain(_SCRIPTY.sub(" ", html or ""))


def shape_of(html) -> dict:
    """
    A page reduced to the handful of things worth comparing.

    Small enough to store beside the fingerprint, specific enough that the
    difference between two of them reads as a sentence.
    """
    h = (html or "")[:_SNAP_CHARS]
    title = _TITLE.search(h)
    return {
        "title": _plain(title.group(1)) if title else "",
        "headings": [_plain(m.group(2)) for m in _HEADING.finditer(h)][:60],
        "images": len(_IMG.findall(h)),
        "scripts": sorted({s.strip() for s in _SRC.findall(h)})[:40],
        "links": sorted({l.strip() for l in _HREF.findall(h)
                         if l.strip().startswith("http")})[:80],
        "emails": sorted({e.lower() for e in _EMAIL.findall(_visible(h))})[:20],
        "phones": sorted({re.sub(r"\D", "", p)[-9:] for p in _PHONE.findall(_visible(h))})[:20],
        "words": len(_visible(h).split()),
        "code_bytes": sum(len(m.group(0)) for m in _SCRIPTY.finditer(h)),
        # A digest of what a VISITOR actually reads, markup stripped out.
        # This is what makes "the file moved but the page reads the same" an
        # exact statement rather than a guess: if this matches, the words on
        # the page are identical and whatever moved was not words. Without it
        # the noise test had to lean on the structural shape, which cannot see
        # a sentence being rewritten. See sweep().
        "text": digest_of(" ".join(_visible(h).split())),
    }


def _named(items, limit=3):
    got = [i for i in items if i][:limit]
    more = len(items) - len(got)
    return ", ".join(f"“{g[:60]}”" for g in got) + (f" and {more} more" if more > 0 else "")


def describe(before, after) -> list:
    """
    What changed between two shapes, in plain lines he can read at a glance.

    Ordered by what he would care about: the words on the page first, then the
    things that break trust (a changed phone number, a new script), then the
    cosmetic. Returns [] when the shapes are the same, which is itself an
    answer — "the file moved but the page did not".
    """
    if not isinstance(before, dict) or not isinstance(after, dict):
        return []
    out = []

    b_h, a_h = list(before.get("headings") or []), list(after.get("headings") or [])
    gained = [h for h in a_h if h not in b_h]
    lost = [h for h in b_h if h not in a_h]
    words = (after.get("words") or 0) - (before.get("words") or 0)

    # An article looks like this: new headings and a big block of new words.
    if gained and words >= 120:
        out.append(f"Looks like new writing was published — {len(gained)} new "
                   f"heading(s) and about {words} more words. New: {_named(gained)}")
    else:
        if gained:
            out.append(f"{len(gained)} heading(s) added: {_named(gained)}")
        if abs(words) >= 25:
            out.append(f"About {abs(words)} words {'added' if words > 0 else 'removed'}.")
    if lost:
        out.append(f"{len(lost)} heading(s) removed: {_named(lost)}")

    if (before.get("title") or "") != (after.get("title") or ""):
        out.append(f"Page title changed from “{before.get('title') or '(none)'}” "
                   f"to “{after.get('title') or '(none)'}”.")

    b_p, a_p = set(before.get("phones") or []), set(after.get("phones") or [])
    if b_p - a_p:
        out.append(f"A phone number was removed: ...{', ...'.join(sorted(b_p - a_p))}")
    if a_p - b_p:
        out.append(f"A phone number was added: ...{', ...'.join(sorted(a_p - b_p))}")
    b_e, a_e = set(before.get("emails") or []), set(after.get("emails") or [])
    if b_e - a_e:
        out.append(f"An email address was removed: {', '.join(sorted(b_e - a_e))}")
    if a_e - b_e:
        out.append(f"An email address was added: {', '.join(sorted(a_e - b_e))}")

    b_s, a_s = set(before.get("scripts") or []), set(after.get("scripts") or [])
    if a_s - b_s:
        out.append("Code was added — new script(s): " + _named(sorted(a_s - b_s)))
    if b_s - a_s:
        out.append("Code was removed — script(s) gone: " + _named(sorted(b_s - a_s)))
    inline = (after.get("code_bytes") or 0) - (before.get("code_bytes") or 0)
    if abs(inline) >= 200 and not (a_s - b_s) and not (b_s - a_s):
        out.append(f"About {abs(inline)} bytes of inline code "
                   f"{'added' if inline > 0 else 'removed'}.")

    imgs = (after.get("images") or 0) - (before.get("images") or 0)
    if imgs:
        out.append(f"{abs(imgs)} image(s) {'added' if imgs > 0 else 'removed'}.")

    b_l, a_l = set(before.get("links") or []), set(after.get("links") or [])
    if a_l - b_l:
        out.append(f"{len(a_l - b_l)} outgoing link(s) added: "
                   + _named([_host(u) for u in sorted(a_l - b_l)]))
    if b_l - a_l:
        out.append(f"{len(b_l - a_l)} outgoing link(s) removed.")

    return out


def _host(url):
    s = str(url or "")
    s = re.sub(r"^[a-z]+://", "", s).split("/")[0]
    return s[4:] if s.startswith("www.") else s


def _now():
    return int(time.time())


def digest_of(html) -> str:
    """The content, not the timestamp. A touch moves mtime and changes nothing."""
    return hashlib.sha1((html or "").encode("utf-8", "replace")).hexdigest()


# ---------------------------------------------------------------------------
# WRITING
# ---------------------------------------------------------------------------

def record(action, page="", agent="", summary="", source=AGENCY, block_id="",
           bytes_before=None, bytes_after=None, backup="", undo_cmd="",
           issue_id=None, ok=True, error="", detail=None) -> int:
    """
    One line in the log. Never raises.

    Bookkeeping must not be the thing that breaks the work — a change that
    happened and was not recorded is bad, and a change that was refused because
    the log was unwritable is worse. So every failure here is swallowed and the
    caller carries on; the sweep will catch the change on its next pass anyway,
    which is exactly what the fingerprints are for.
    """
    try:
        with sqlite3.connect(board.DB, timeout=30) as c:
            board.ensure_schema(c)
            cur = c.execute(
                "INSERT INTO site_changes(at,source,agent,action,page,block_id,"
                " summary,bytes_before,bytes_after,backup,undo_cmd,issue_id,ok,"
                " error,detail)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (_now(), source, (agent or "")[:40], (action or "")[:30],
                 (page or "")[:200], (block_id or "")[:60], (summary or "")[:500],
                 bytes_before, bytes_after, (backup or "")[:120],
                 (undo_cmd or "")[:300], issue_id, 1 if ok else 0,
                 (error or "")[:300],
                 json.dumps(list(detail)[:20]) if detail else None))
            return cur.lastrowid
    except Exception:
        return 0


def note_page(page, html=None, bytes_=None, changed="") -> None:
    """
    Remember what a page looks like now, so the next sweep does not report our
    own edit as somebody else's.

    Called straight after an agency write. If the caller has the html it just
    wrote, the digest is exact; if it only knows the size, the digest is left
    empty and the sweep falls back to comparing bytes and mtime — worse, but
    honest about being worse rather than storing a digest of the wrong thing.
    """
    try:
        with sqlite3.connect(board.DB, timeout=30) as c:
            board.ensure_schema(c)
            c.execute(
                "INSERT INTO site_fingerprints(page,bytes,changed,digest,seen_at)"
                " VALUES(?,?,?,?,?)"
                " ON CONFLICT(page) DO UPDATE SET bytes=excluded.bytes,"
                "   changed=excluded.changed, digest=excluded.digest,"
                "   seen_at=excluded.seen_at",
                (page, (len(html) if html is not None else bytes_),
                 changed or "", digest_of(html) if html is not None else "",
                 _now()))
    except Exception:
        pass


def fingerprints() -> dict:
    try:
        with sqlite3.connect(board.DB, timeout=30) as c:
            c.row_factory = sqlite3.Row
            board.ensure_schema(c)
            return {r["page"]: dict(r) for r in
                    c.execute("SELECT * FROM site_fingerprints")}
    except Exception:
        return {}


def _ours_after(page, seen_at):
    """
    Is this difference our own write that never got written down?

    THE VERSION THAT SHIPPED FIRST WAS TOO BLUNT AND HID THE WORST CASE.
    It asked "did the agency write this page in the last few minutes", and if
    so put any difference down to us. So: the Releaser fixes the header at
    09:00, something overwrites the whole file at 09:05, and the sweep at 09:06
    says "that was us" and tells him nothing. The one event this panel exists to
    catch — the agency's careful fix being wiped — was the one event it swallowed.

    The precise question is narrower. After every successful write the fingerprint
    is refreshed, so in the normal case the stored fingerprint ALREADY holds what
    we wrote, and anything different from it is somebody else by definition. Our
    write only goes unrecorded in the fingerprint if the process died between the
    two. That is what this detects: an agency change stamped LATER than the
    fingerprint was taken.

    Same-second writes count as recorded, not as lost, because record() and
    note_page() run back to back — so `>` and not `>=`.
    """
    try:
        with sqlite3.connect(board.DB, timeout=30) as c:
            r = c.execute("SELECT 1 FROM site_changes WHERE page=? AND source=?"
                          "  AND ok=1 AND at>? AND at>=? LIMIT 1",
                          (page, AGENCY, int(seen_at or 0),
                           _now() - OURS_WINDOW)).fetchone()
            return bool(r)
    except Exception:
        return False


def sweep(seen, confirmed_gone=None) -> dict:
    """
    Compare what the site looks like now against what we last saw.

    `seen` is [{page, bytes, changed, html?}, ...] — whatever the caller could
    get. html is optional and better: with it the comparison is on content, and
    a page touched but not edited correctly reads as unchanged.

    Anything that moved is written to the log as an 'outside' change unless the
    agency wrote that page in the last few minutes, in which case it is our own
    edit catching up with us.

    A page that has VANISHED is recorded and its fingerprint kept, deliberately.
    Dropping the row would mean the file reappearing later reads as brand new
    rather than as restored, and "your services page came back" is a thing he
    would want to know.
    """
    known = fingerprints()
    # THE FIRST LOOK IS A BASELINE, NOT NEWS.
    #
    # With nothing to compare against, every page on the site is technically
    # "new" — so the very first sweep would fill his change log with nine
    # entries saying his own website exists. That is the fastest way to teach
    # somebody to ignore a screen. Nothing is recorded on the first pass; the
    # fingerprints are taken and the reporting starts from the next one.
    baseline = not known
    now_pages = {}
    added, edited, gone, ours, noisy = [], [], [], [], []
    for row in seen or []:
        page = row.get("page")
        if not page:
            continue
        html = row.get("html")
        dig = digest_of(html) if html is not None else ""
        size = len(html) if html is not None else row.get("bytes")
        shape = shape_of(html) if html is not None else None
        now_pages[page] = {"bytes": size, "changed": row.get("changed") or "",
                           "digest": dig, "shape": shape}
        was = known.get(page)
        if was is None:
            if baseline:
                continue
            added.append(page)
            record("added", page=page, source=OUTSIDE, bytes_after=size,
                   summary=f"{page} appeared on the site")
            continue
        # Content first, because it is the only comparison that cannot lie. Fall
        # back to size and the server's own timestamp when we have no digest on
        # one side or the other — an older cabinet, or a write that only knew
        # the byte count.
        if dig and was["digest"]:
            moved = dig != was["digest"]
        else:
            moved = (size != was["bytes"]
                     or (row.get("changed") or "") != (was["changed"] or ""))
        if not moved:
            continue
        if _ours_after(page, was.get("seen_at")):
            ours.append(page)
            continue

        # BYTES THAT MOVED WITH NOTHING VISIBLE BEHIND THEM.
        #
        # "blog-details.php was changed by somebody other than the agency, 2
        # bytes smaller; the file moved but the page reads the same" — and two
        # bytes bigger the pass before. That is a view counter, a timestamp or
        # a rotating token, not a person editing his site, and reporting it
        # every pass buries the one change that would matter.
        #
        # The test is deliberately strict: the SHAPE — headings, links, blocks,
        # the things describe() compares — has to be identical, AND the size
        # move has to be tiny. A real edit changes something describable or
        # changes real length. The fingerprint is still updated, so a genuine
        # change on top of the noise is caught next pass.
        if shape is not None and was.get("snapshot"):
            try:
                before = json.loads(was["snapshot"]) or {}
            except Exception:
                before = {}
            # Both sides must carry a text digest. An older fingerprint taken
            # before this existed cannot prove the words are the same, and
            # "cannot prove" must fall through to reporting it — a missed real
            # edit is worse than one more line in the log.
            reads_same = bool(before.get("text")) and before.get("text") == shape.get("text")
            drift = (abs(int(size) - int(was["bytes"]))
                     if isinstance(size, int) and isinstance(was["bytes"], int)
                     else None)
            if reads_same and not describe(before, shape) \
                    and drift is not None and drift <= NOISE_BYTES:
                # Recorded, but as COSMETIC — not in `edited`, so it never
                # becomes "somebody changed your site" on his screen and never
                # earns a ticket. The row is still there if he goes looking,
                # because a file genuinely did move and hiding that outright
                # would be its own small lie.
                noisy.append(page)
                record("cosmetic", page=page, source=OUTSIDE,
                       bytes_before=was["bytes"], bytes_after=size,
                       summary=(f"{page} moved by {drift} byte(s) and reads "
                                f"exactly the same — a counter, a timestamp or "
                                f"a re-saved file, not an edit. Noted and not "
                                f"raised."))
                continue

        edited.append(page)
        # WHAT changed, not merely THAT it changed. Needs the previous
        # snapshot, which is why the sweep keeps one — see describe().
        lines = []
        if shape is not None:
            try:
                lines = describe(json.loads(was.get("snapshot") or "null"), shape)
            except Exception:
                lines = []
        delta = ""
        if isinstance(size, int) and isinstance(was["bytes"], int):
            d = size - was["bytes"]
            delta = (f", {abs(d)} bytes {'bigger' if d > 0 else 'smaller'}"
                     if d else ", same size but different content")
        record("edited", page=page, source=OUTSIDE,
               bytes_before=was["bytes"], bytes_after=size, detail=lines,
               summary=f"{page} was changed by somebody other than the agency"
                       + delta
                       + (" — " + lines[0] if lines else
                          "; the file moved but the page reads the same"
                          if shape is not None else ""))

    # A PAGE WE COULD NOT READ IS NOT A PAGE THAT WAS DELETED.
    #
    # "includes/header.php is no longer on the site" — an hour later, "changed
    # by somebody other than the agency". He had touched nothing. A read that
    # timed out simply left the page out of this pass's list, and absence from
    # the list was being read as absence from the site. So a guessed include
    # that answers nine times out of ten flaps between "gone" and "restored"
    # for ever, and every flap is a fresh alarm about a file nobody touched.
    #
    # `confirmed_gone` is the caller saying "I asked, and the site told me it
    # is not there". Anything else missing from `seen` is simply unknown this
    # pass, and unknown is not news.
    told_gone = {str(p) for p in (confirmed_gone or [])}
    for page in known:
        if page in now_pages:
            continue
        if known[page].get("bytes") is None:
            continue
        if page not in told_gone:
            continue
        gone.append(page)
        record("gone", page=page, source=OUTSIDE,
               bytes_before=known[page]["bytes"],
               summary=f"{page} is no longer on the site")

    try:
        with sqlite3.connect(board.DB, timeout=30) as c:
            board.ensure_schema(c)
            for page, v in now_pages.items():
                snap = json.dumps(v["shape"]) if v.get("shape") is not None else None
                c.execute(
                    "INSERT INTO site_fingerprints(page,bytes,changed,digest,"
                    " seen_at,snapshot) VALUES(?,?,?,?,?,?)"
                    " ON CONFLICT(page) DO UPDATE SET bytes=excluded.bytes,"
                    "   changed=excluded.changed, digest=excluded.digest,"
                    "   seen_at=excluded.seen_at,"
                    # Keep the last snapshot when this pass could not read the
                    # page. Overwriting it with nothing would mean the next
                    # real change has nothing to be compared against.
                    "   snapshot=COALESCE(excluded.snapshot, site_fingerprints.snapshot)",
                    (page, v["bytes"], v["changed"], v["digest"], _now(), snap))
            for page in gone:
                c.execute("UPDATE site_fingerprints SET bytes=NULL, digest='',"
                          " seen_at=? WHERE page=?", (_now(), page))
    except Exception:
        pass

    return {"ok": True, "pages_seen": len(now_pages), "added": added,
            "noisy": noisy,
            "edited": edited, "gone": gone, "was_us": ours,
            "first_look": baseline,
            "head": (f"{len(now_pages)} page(s) fingerprinted for the first "
                     "time — from now on anything that moves is reported"
                     if baseline else
                     f"{len(added) + len(edited) + len(gone)} change(s) since "
                     "the last look" if (added or edited or gone) else
                     "nothing on the site has changed since the last look")}


# ---------------------------------------------------------------------------
# READING
# ---------------------------------------------------------------------------

def recent(limit=30, page="", source="") -> dict:
    """The log, newest first, in the shape the office panel renders."""
    q = "SELECT * FROM site_changes WHERE 1=1"
    args = []
    if page:
        q += " AND page=?"
        args.append(page)
    if source:
        q += " AND source=?"
        args.append(source)
    q += " ORDER BY at DESC, id DESC"
    if limit:
        q += " LIMIT ?"
        args.append(int(limit))
    try:
        with sqlite3.connect(board.DB, timeout=30) as c:
            c.row_factory = sqlite3.Row
            board.ensure_schema(c)
            rows = [dict(r) for r in c.execute(q, args)]
            total = c.execute("SELECT count(*) FROM site_changes").fetchone()[0]
            watched = c.execute("SELECT count(*) FROM site_fingerprints"
                                " WHERE bytes IS NOT NULL").fetchone()[0]
            last = c.execute("SELECT max(seen_at) FROM site_fingerprints").fetchone()[0]
    except Exception as e:
        return {"ok": False, "error": str(e), "changes": []}

    for r in rows:
        r["when"] = _ago(_now() - (r["at"] or _now()))
        # The line-by-line answer to "what changed". Stored as JSON so the
        # office can render it as a list rather than one run-on sentence.
        try:
            r["detail"] = json.loads(r.get("detail") or "[]")
        except Exception:
            r["detail"] = [str(r.get("detail"))] if r.get("detail") else []
        r["by"] = ("the agency — " + (r["agent"] or "an agent")
                   if r["source"] == AGENCY else "somebody else")
    return {"ok": True, "changes": rows, "total": total,
            "pages_watched": watched, "last_look": last,
            "head": _head(total, watched, last, rows)}


def _head(total, watched, last, rows):
    if not watched:
        return ("Nothing is being watched yet. The agency fingerprints your "
                "pages the first time it can reach the site — set up Full "
                "access on /setup and it starts from the next run.")
    if not total:
        return (f"{watched} page(s) watched. Nothing has changed since the "
                "agency started looking"
                + (f", {_ago(_now() - last)}." if last else "."))
    mine = sum(1 for r in rows if r["source"] == AGENCY)
    theirs = len(rows) - mine
    bits = []
    if mine:
        bits.append(f"{mine} by the agency")
    if theirs:
        bits.append(f"{theirs} by somebody else")
    # BOTH SCREENS WERE RIGHT AND THEY READ AS A CONTRADICTION.
    #
    # "nothing on the site has changed since the last look" from the Watcher,
    # and "8 change(s) on file" from here, on the same screen. The Watcher is
    # reporting THIS LOOK; this is the whole history since the agency started
    # watching. Two different questions, two true answers, and nothing in
    # either sentence said which question it was answering.
    #
    # So this one now says the period out loud, and — the part that actually
    # resolves it — says how many are new since the last look. Nothing new is
    # the normal, healthy state of a site nobody has edited, and it should read
    # that way rather than looking like the two halves disagree.
    fresh = sum(1 for r in rows if last and int(r.get("at") or 0) >= int(last))
    since = (f"{fresh} of them at the last look" if fresh
             else "none of them new — nothing changed at the last look")
    return (f"{total} change(s) recorded since the agency started watching, "
            f"across {watched} page(s) — " + " and ".join(bits)
            + f". {since}"
            + (f", {_ago(_now() - last)}." if last else "."))


def _ago(secs):
    secs = max(0, int(secs or 0))
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{secs // 60} min ago"
    if secs < 172800:
        return f"{secs // 3600}h ago"
    return f"{secs // 86400} days ago"


def as_text(r) -> str:
    if not r.get("ok"):
        return "WEBSITE CHANGES — " + (r.get("error") or "could not read the log")
    out = ["WEBSITE CHANGES — " + r["head"], ""]
    for c in r["changes"]:
        mark = "agency" if c["source"] == AGENCY else "SOMEONE"
        out.append(f"  {c['when']:>12}  [{mark}]  {c['summary'] or c['action']}")
        if c["undo_cmd"]:
            out.append(f"                 undo:  {c['undo_cmd']}")
    if not r["changes"]:
        out.append("  (nothing yet)")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="What changed on the website.")
    ap.add_argument("--all", action="store_true", help="everything on file")
    ap.add_argument("--page", default="", help="just this page")
    ap.add_argument("--outside", action="store_true",
                    help="only changes the agency did not make")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    r = recent(limit=0 if a.all else 30, page=a.page,
               source=OUTSIDE if a.outside else "")
    print(json.dumps(r, indent=2) if a.json else as_text(r))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
