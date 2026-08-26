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
import json
import sqlite3
import sys
import time

from . import board

AGENCY = "agency"
OUTSIDE = "outside"

# How long after an agency write a page's fingerprint may move and still be
# put down to us. Belt and braces for the case where a write succeeded and the
# process died before it could refresh the fingerprint: without it, the next
# sweep would report the agency's own edit as somebody tampering with the site.
OURS_WINDOW = 900


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
           issue_id=None, ok=True, error="") -> int:
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
                " summary,bytes_before,bytes_after,backup,undo_cmd,issue_id,ok,error)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (_now(), source, (agent or "")[:40], (action or "")[:30],
                 (page or "")[:200], (block_id or "")[:60], (summary or "")[:500],
                 bytes_before, bytes_after, (backup or "")[:120],
                 (undo_cmd or "")[:300], issue_id, 1 if ok else 0,
                 (error or "")[:300]))
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


def sweep(seen) -> dict:
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
    added, edited, gone, ours = [], [], [], []
    for row in seen or []:
        page = row.get("page")
        if not page:
            continue
        html = row.get("html")
        dig = digest_of(html) if html is not None else ""
        size = len(html) if html is not None else row.get("bytes")
        now_pages[page] = {"bytes": size, "changed": row.get("changed") or "",
                           "digest": dig}
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
        edited.append(page)
        delta = ""
        if isinstance(size, int) and isinstance(was["bytes"], int):
            d = size - was["bytes"]
            delta = (f", {abs(d)} bytes {'bigger' if d > 0 else 'smaller'}"
                     if d else ", same size but different content")
        record("edited", page=page, source=OUTSIDE,
               bytes_before=was["bytes"], bytes_after=size,
               summary=f"{page} was changed by somebody other than the agency"
                       + delta)

    for page in known:
        if page in now_pages:
            continue
        if known[page].get("bytes") is None:
            continue
        gone.append(page)
        record("gone", page=page, source=OUTSIDE,
               bytes_before=known[page]["bytes"],
               summary=f"{page} is no longer on the site")

    try:
        with sqlite3.connect(board.DB, timeout=30) as c:
            board.ensure_schema(c)
            for page, v in now_pages.items():
                c.execute(
                    "INSERT INTO site_fingerprints(page,bytes,changed,digest,seen_at)"
                    " VALUES(?,?,?,?,?)"
                    " ON CONFLICT(page) DO UPDATE SET bytes=excluded.bytes,"
                    "   changed=excluded.changed, digest=excluded.digest,"
                    "   seen_at=excluded.seen_at",
                    (page, v["bytes"], v["changed"], v["digest"], _now()))
            for page in gone:
                c.execute("UPDATE site_fingerprints SET bytes=NULL, digest='',"
                          " seen_at=? WHERE page=?", (_now(), page))
    except Exception:
        pass

    return {"ok": True, "pages_seen": len(now_pages), "added": added,
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
    return (f"{total} change(s) on file across {watched} watched page(s) — "
            + " and ".join(bits) + " in this list"
            + (f". Last look {_ago(_now() - last)}." if last else "."))


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
