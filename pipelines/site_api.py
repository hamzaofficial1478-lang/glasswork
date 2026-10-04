"""
site_api.py — the wire between the agency and the owner's website.

    python -m pipelines.site_api --health          can we reach it at all?
    python -m pipelines.site_api --pages           what pages are there
    python -m pipelines.site_api --read index.php  read one page
    python -m pipelines.site_api --plan-schema     what the agency WOULD change
    python -m pipelines.site_api --apply-schema    do it (Releaser only)
    python -m pipelines.site_api --backups         every restore point
    python -m pipelines.site_api --undo index.php  put a page back

WHY THIS FILE HAD TO BE WRITTEN

The setup screen has always had two boxes — "Admin API address" and "Admin API
token" — and saved them into .env. Nothing read them. `mcp/site_connector/
connector.py` described the right shape and nothing imported it. So the owner
could switch to Full access, fill both boxes in, press save, and get exactly
the read-only agency he had before, with no message telling him so.

He asked "how will i give access... i want to test the agents". The honest
answer was that there was nothing on the other end of those two boxes. There
is now.

THE RED LINE STILL HOLDS

The handbook is clear that only the IT Releaser may change the website
customers see, and only after tests pass and — for anything risky — the
owner's yes.

So this module reads for anybody and writes for one agent. Every other caller
asking for a write gets the change written to the owner's approval queue and a
clear answer saying so — never a silent failure, and never a quiet write.

WHAT IS ON THE OTHER END

paste-into-your-site/agency-api.php, which he uploads once. It refuses to
delete anything, refuses to touch a file that is not a page, refuses anything
outside the website folder, and copies the old file before every change. See
that file for the whole list.
"""
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from coordination import board

AGENT = "it-releaser"          # the only one allowed to write. Red line 1.
SECTOR = "IT"
TIMEOUT = 25
OLD_CONNECTOR = ("the connector file on your website is the older one, which cannot add "
                 "blog posts. Upload paste-into-your-site/agency-api.php from the agency "
                 "folder to your site as agency-api/index.php, replacing the old one "
                 "(leave token.php where it is), then press the button again.")

# The marker the endpoint wraps a block in, so re-running replaces rather than
# stacks. One id per kind of block, stable forever.
SCHEMA_ID = "localbusiness"
FAQ_ID = "faqpage"


def _env():
    try:
        from providers import integrations
        return integrations.read_env()
    except Exception:
        return {}


def settings(env=None) -> dict:
    """
    Where the site is, how we may talk to it, and whether we may write.

    'mode' is the owner's own choice on /setup and this module never overrides
    it: read-only means read-only even if a token happens to be present.
    """
    e = env if env is not None else _env()
    g = lambda k: (os.environ.get(k) or e.get(k) or "").strip()
    mode = (g("SITE_MODE") or "readonly").lower()
    base = g("SITE_API_BASE").rstrip("/")
    return {"mode": "full" if mode == "full" else "readonly",
            "base": base,
            "token": g("SITE_API_TOKEN"),
            "site": g("SITE_URL").rstrip("/")}


def why_cannot_read(s=None) -> str:
    """
    '' when the agency can READ his site through the endpoint.

    Separate from why_not_ready on purpose. Read-only mode is a decision about
    WRITING — he is saying agents may not change his site — and it says nothing
    about looking. Conflating the two made a reader refuse to read: the Writer
    checks whether his site has a header include so a new page comes out
    looking like the rest of it, and in read-only mode it was told the agency
    could not reach the site at all. It then produced a standalone page that
    would have looked foreign, for a reason that had nothing to do with the
    question it asked.
    """
    s = s or settings()
    if not s["base"]:
        return ("there is no Admin API address saved. It is your site plus "
                "/agency-api, e.g. https://www.yoursite.com/agency-api")
    if not s["token"]:
        return "there is no Admin API token saved"
    return ""


def why_not_ready(s=None) -> str:
    """'' when full access is usable, otherwise the one thing missing."""
    s = s or settings()
    if s["mode"] != "full":
        return ("the agency is in read-only mode — choose Full access on "
                "/setup if you want agents to change the site themselves")
    if not s["base"]:
        return ("Full access is chosen but there is no Admin API address. It "
                "is your site plus /agency-api, e.g. "
                "https://www.yoursite.com/agency-api")
    if not s["token"]:
        return ("Full access is chosen but there is no Admin API token. It is "
                "the long random string inside agency-api/token.php on your "
                "server — just the string, not the <?php or the quotes.")
    return ""


# ---------------------------------------------------------------------------
# THE CALL
# ---------------------------------------------------------------------------

def call(path, method="GET", body=None, timeout=TIMEOUT, s=None) -> dict:
    """
    One request to the site's admin API. Never raises — a website that is down
    is an ordinary Tuesday, not a crash.
    """
    s = s or settings()
    why = why_not_ready(s)
    if why:
        return {"ok": False, "error": why, "not_configured": True}
    # index.php?do=health, NOT /agency-api/health.
    #
    # The pretty form needs mod_rewrite and an .htaccess. Apache without one
    # looks for a FILE called 'health', does not find it, and 404s — so every
    # call would have failed on his shared host while passing on the PHP test
    # server, which quietly falls back to index.php. A query string needs no
    # server configuration anywhere and cannot be switched off by a host.
    bits = path.lstrip("/").split("/", 1)
    q = {"do": bits[0]}
    if len(bits) > 1 and bits[1]:
        q["page"] = urllib.parse.unquote(bits[1])
    # He may have renamed the file to index.php or left it as agency-api.php.
    # Both are fine and both are documented, so accept an address that already
    # names the script instead of gluing '/index.php' onto the end of it and
    # asking his server for agency-api.php/index.php.
    root = s["base"]
    target = root if root.lower().endswith(".php") else root + "/index.php"
    url = target + "?" + urllib.parse.urlencode(q)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + s["token"])
    # Some Apache setups drop Authorization before PHP sees it. The endpoint
    # accepts either, so send both and stop caring which host this is.
    req.add_header("X-Agency-Token", s["token"])
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raw = (e.read() or b"").decode("utf-8", "replace")
        try:
            out = json.loads(raw)
        except Exception:
            out = None
        # A bare number or a string is valid JSON and is NOT an answer from
        # this API. Calling .setdefault on it crashed the whole run — which is
        # exactly what a misaddressed request produced.
        if not isinstance(out, dict):
            out = {"ok": False,
                   "error": f"HTTP {e.code} from your site"
                            + (f" — it said: {str(raw)[:120]}" if raw else "")}
        out.setdefault("ok", False)
        out["status"] = e.code
        if e.code == 401:
            out["error"] = ("your site refused the token. Check the Admin API "
                            "token on /setup matches the string inside "
                            "agency-api/token.php exactly — one trailing space "
                            "is enough to break it.")
        elif e.code == 404 and str(out.get("error") or "").startswith("unknown endpoint"):
            # The connector answered — it just predates this call.
            out["old_connector"] = True
            out["error"] = OLD_CONNECTOR
        elif e.code == 404:
            out["error"] = (f"nothing answered at {url}. Check the Admin API "
                            "address, and that agency-api/index.php really is "
                            "on the server.")
        return out
    except Exception as e:
        # NOT AN ANSWER — THE SITE WAS NOT THERE TO GIVE ONE. Marked, so the
        # Releaser keeps his approval and tries again, instead of calling a
        # dropped line or a resting pause a failed change and throwing his yes
        # away (pipelines/it_release, _wait_for_site).
        from coordination import sitepace
        resting = isinstance(e, sitepace.SiteResting)
        return {"ok": False, "unreachable": True, "resting": resting,
                "error": (str(e) if resting else
                          f"could not reach your site: {type(e).__name__}: {e}")}
    try:
        out = json.loads(raw)
    except Exception:
        out = None
    if isinstance(out, dict):
        return out
    # Not an answer from this API. A PHP notice printed above the JSON is the
    # classic shared-hosting symptom, and "expecting value: line 1 column 1"
    # would send him hunting in entirely the wrong place — so show him what
    # actually came back.
    return {"ok": False, "error": "your site did not answer with JSON — "
                                  "the first 200 characters were: "
                                  + raw[:200].replace("\n", " ")}


# ---------------------------------------------------------------------------
# NOT OURS
# ---------------------------------------------------------------------------
#
# His site has an admin panel and a config folder holding database credentials.
# None of it is a page a visitor reads and none of it is the agency's business,
# so this end refuses it too — not just the server end.
#
# Two reasons for the belt as well as the braces. The obvious one: the server
# file only stops what it knows about, and it is a file he has to upload by
# hand, so there is always a window where his server is running the old one.
# The less obvious one, and the one that actually bit: a listing that includes
# admin/ hands seventeen files to the change-watcher, which then spends the
# sector's whole daily allowance re-reading pages nobody will ever edit.
#
# Kept deliberately in step with NEVER_TOUCH and OFF_LIMIT_DIRS in
# paste-into-your-site/agency-api.php. If you add to one, add to the other.

NOT_OURS_DIRS = {"admin", "config", "private", "logs", "tmp", "agency-api",
                 ".git", ".svn", "node_modules", "vendor", "wp-content",
                 "wp-includes", "wp-admin", "cache", "uploads", "backups",
                 "assets", "images", "img", "fonts", "css", "js"}

NOT_OURS_FILES = {"config.php", "db.php", "wp-config.php", ".htaccess",
                  "token.php", "token.txt", "index.php"}


def not_ours(page) -> str:
    """
    Why the agency must not touch this page — empty string if it may.

    Returns the reason rather than True/False so a refusal can say which rule
    caught it. 'admin/login.php is off limits' is a fact he can check; a bare
    False is something he has to come and ask me about.
    """
    rel = str(page or "").strip().lstrip("/").replace("\\", "/")
    if not rel:
        return "no page named"
    bits = [b.lower() for b in rel.split("/") if b not in ("", ".")]
    if ".." in bits:
        return f"{rel} climbs out of the website folder"
    for folder in bits[:-1]:
        if folder in NOT_OURS_DIRS:
            return f"{rel} is inside {folder}/, which is not the website"
    # index.php at the top level is his home page and perfectly ours; it is
    # only off limits inside agency-api/, and the folder rule above has that.
    if len(bits) > 1 and bits[-1] in NOT_OURS_FILES:
        return f"{rel} is not a page — it is configuration"
    if len(bits) == 1 and bits[-1] in (NOT_OURS_FILES - {"index.php"}):
        return f"{rel} is not a page — it is configuration"
    return ""


# ---------------------------------------------------------------------------
# READ — anybody may
# ---------------------------------------------------------------------------

def health(s=None) -> dict:
    return call("/health", s=s)


def pages(s=None) -> dict:
    """
    What pages are on the site — his pages, not his server's insides.

    The filter here is what keeps an old copy of agency-api.php on the server
    from feeding admin/ and config/ into the watcher.
    """
    r = call("/pages", s=s)
    if not r.get("ok") or not isinstance(r.get("pages"), list):
        return r
    keep, dropped = [], []
    for row in r["pages"]:
        name = row.get("page") if isinstance(row, dict) else row
        (dropped if not_ours(name) else keep).append(name)
    if dropped:
        r = dict(r)
        r["pages"] = [x for x in r["pages"]
                      if (x.get("page") if isinstance(x, dict) else x) in keep]
        r["counted"] = len(r["pages"])
        # Named, not silently swallowed. If a page he expects to see is missing
        # this line is where he finds out why.
        r["not_ours"] = sorted(dropped)
    return r


def read_page(page, s=None) -> dict:
    why = not_ours(page)
    if why:
        return {"ok": False, "page": page, "error": why}
    return call("/pages/" + urllib.parse.quote(page.lstrip("/")), s=s)


def backups(s=None) -> dict:
    return call("/backups", s=s)


# ---------------------------------------------------------------------------
# WRITE — the Releaser, or the owner's queue. Never a third option.
# ---------------------------------------------------------------------------

def _queue_for_him(agent, kind, page, detail):
    """
    Queue a blocked write for his yes, WITH the change in it.

    THE FIVE RED "REFUSED" LINES. This used to queue {"page", "bytes"} — the
    page's name and the size of what was going to be written, and not the
    thing itself. He approved five of them, the Releaser opened each and found
    nothing to carry out, and his change log filled with "this approval carries
    no change and no instruction". The approval was real; the note it was
    written on was empty. It now carries exactly what the desk wanted to write,
    in the shape the Releaser reads (it_release.plan_of).

    Too big for the queue is said out loud, never saved as an empty note.
    """
    try:
        return board.queue_site_change(agent, kind, detail)
    except ValueError as e:
        return {"not_queued": str(e), "page": page}


def _may_write(agent) -> str:
    if agent != AGENT:
        return (f"'{agent}' may not change the website. Only {AGENT} goes "
                "through that door — the change has been queued for the owner "
                "instead.")
    return ""


# ---------------------------------------------------------------------------
# EVERY WRITE IS RECORDED, AT THE MOMENT IT HAPPENS
# ---------------------------------------------------------------------------
#
# "there should be a section in program where i can see whats changed in
#  website... that's totally blind."
#
# He was, and the reason is here: three functions below are the ONLY way
# anything in this agency reaches his files, and none of them wrote anything
# down. A change left a .bak file on the server with a timestamp in the name
# and nothing else — no record of what it was, who did it, or how to reverse it.
#
# Recording is done at the choke point rather than in each of the callers.
# fix_address, fix_canonical, apply_schema and undo_schema all funnel through
# put_page or put_head_block, so a new caller added next year is logged without
# anybody remembering to log it. That is the whole reason to have a choke point.
#
# A REFUSED write is recorded too. "The Fixer tried to change the header and was
# not allowed" is exactly the sort of thing that is invisible until you go
# looking for why nothing happened.

def _log_write(action, page, agent, r, html=None, block_id="", summary="",
               undo_cmd="", before=None, added=""):
    """Write one line of the change log, and refresh what we think the page is."""
    try:
        from coordination import changelog
    except Exception:                                        # pragma: no cover
        return r
    ok = bool(r.get("ok"))
    unchanged = (r.get("action") == "unchanged" or r.get("changed") is False)
    if not ok and r.get("unreachable"):
        # Nothing reached the site, so nothing was tried on it. The change is
        # still waiting, and the Posts room says so — a "tried and could not"
        # line here read as the change having been refused.
        return r
    if ok and unchanged:
        # The nightly run putting back a block that is already there is not a
        # change, and logging it every night would bury the real ones.
        return r
    detail = None
    if ok:
        try:
            if before is not None and html is not None:
                detail = changelog.what_changed(before, html)
            elif added:
                detail = ["added to the top of the page: " + changelog.one_line(added)]
        except Exception:
            detail = None
    changelog.record(
        action, page=page, agent=agent, block_id=block_id,
        summary=((summary + (f" in {page}" if page and page not in summary else ""))
                 if summary and ok else _plain(action, page, block_id, ok, r)),
        bytes_before=(len(before) if before is not None else None),
        bytes_after=(len(html) if html is not None else r.get("bytes")),
        backup=r.get("backup") or "", undo_cmd=undo_cmd if ok else "",
        ok=ok, error="" if ok else str(r.get("error") or "")[:300],
        detail=detail)
    if ok and html is not None:
        changelog.note_page(page, html=html)
    elif ok:
        # We changed it but do not hold the result. Read it back rather than
        # storing a digest of something we are guessing at — otherwise the next
        # sweep reports our own edit as somebody tampering with the site.
        try:
            got = read_page(page)
            if got.get("ok"):
                changelog.note_page(page, html=got.get("html") or "")
        except Exception:
            pass
    return r


def _plain(action, page, block_id, ok, r):
    """
    One line, in his language, not the API's.

    A FAILED WRITE MUST SAY WHY IT FAILED. The first version said "tried to
    change index.php and was not allowed" for every unsuccessful write, which
    was true for a refused agent and a lie for everything else — the first real
    run of this logged a plain 400 from the server ("that page has no </head>")
    as a permissions problem, which would have sent him looking in exactly the
    wrong place.
    """
    if not ok:
        if r.get("queued"):
            return (f"wanted to change {page} and is not allowed to — "
                    "it is in your approval queue instead")
        why = str(r.get("error") or "no reason given")
        return f"tried to change {page} and could not: {why}"
    if action == "head_block":
        return f"added the {block_id} block to the top of {page}"
    if action == "remove_block":
        return f"took the {block_id} block back out of {page}"
    if action == "restore":
        return f"put {page} back to how it was before"
    if action == "remove_page":
        return f"took {page} off your website (a copy is kept)"
    if action == "new_page":
        # A page that did not exist before is not a rewrite, and calling it one
        # on his change screen would hide the single most significant thing the
        # agency does to his website.
        return f"published a new page: {page}"
    return f"rewrote {page}"


def put_head_block(page, block_id, html, agent=AGENT, s=None) -> dict:
    """
    Put one marked block into a page's <head>, replacing any earlier copy.

    Idempotent by design: the endpoint wraps it in a marker comment, so the
    hundredth nightly run leaves exactly one copy and reports 'unchanged'.
    """
    # Off-limits first, and it is NOT queued for the owner. A refusal on
    # grounds of who you are means "ask him"; a refusal on grounds of what the
    # file is means nobody may, including him via the approval queue.
    why = not_ours(page)
    if why:
        return _log_write("head_block", page, agent,
                          {"ok": False, "error": why}, block_id=block_id)
    refused = _may_write(agent)
    if refused:
        cid = _queue_for_him(agent, "head_block", page, {
            "change": {"page": page, "block": block_id, "html": html},
            "why": (f"{agent} wants to add the '{block_id}' block to the head of "
                    f"{page}. Only the IT Releaser writes to your site, so it "
                    "waits for your yes."),
            "bytes": len(html)})
        return _log_write("head_block", page, agent,
                          {"ok": False, "queued": cid, "error": refused},
                          block_id=block_id)
    r = call("/head", "POST",
             {"page": page, "id": block_id, "html": html}, s=s)
    return _log_write("head_block", page, agent, r, block_id=block_id,
                      undo_cmd=(f"python -m pipelines.site_api "
                                f"--remove-block {block_id} --page {page}"),
                      added=html)


def put_page(page, html, agent=AGENT, s=None, _action="page", _block="",
             before=None, summary="") -> dict:
    """
    Write a whole page.

    _action and _block are for the log only, so a caller that knows WHY it is
    rewriting the file — taking one block out, say — can have the change log
    read "took the viewport block back out of header.php" instead of the
    literally-true but useless "rewrote header.php".

    `before` is the page as it was, and `summary` the change in his words.
    "i want to know what the program had changed" — his Website changes panel
    said "rewrote includes/footer.php" and nothing else, three times. With the
    old page in hand the log records the lines that actually went and came
    (changelog.what_changed); every caller here reads the page before it
    writes, so it costs nothing to pass on.
    """
    why = not_ours(page)
    if why:
        return _log_write(_action, page, agent,
                          {"ok": False, "error": why}, block_id=_block)
    refused = _may_write(agent)
    if refused:
        cid = _queue_for_him(agent, "page", page, {
            "change": {"page": page, "html": html, "whole_page": True},
            "why": (f"{agent} wants to rewrite {page}"
                    + (f" ({_action.replace('_', ' ')})" if _action != "page" else "")
                    + ". Only the IT Releaser writes to your site, so it waits for "
                      "your yes. The old copy is kept on the server and can be put "
                      "back."),
            "bytes": len(html)})
        return _log_write(_action, page, agent,
                          {"ok": False, "queued": cid, "error": refused},
                          block_id=_block)
    r = call("/pages", "POST", {"page": page, "html": html}, s=s)
    return _log_write(_action, page, agent, r, html=html, block_id=_block,
                      undo_cmd=f"python -m pipelines.site_api --undo {page}",
                      before=before, summary=summary)


def remove_head_block(page, block_id, agent=AGENT, s=None) -> dict:
    """
    Take one marked block back out of a page, leaving everything else alone.

    Read, cut, write — deliberately NOT a rollback. Rollback restores whatever
    the newest backup happens to be, which is right after a bad edit and wrong
    a week later when the page has changed for other reasons. This removes
    exactly what the agency put there and touches nothing else.
    """
    got = read_page(page, s=s)
    if not got.get("ok"):
        return {"ok": False, "page": page, "error": got.get("error")}
    src = got.get("html") or ""
    open_, close = f"<!-- agency:{block_id} -->", f"<!-- /agency:{block_id} -->"
    import re
    pat = re.compile(re.escape(open_) + ".*?" + re.escape(close) + r"\s*", re.S)
    if not pat.search(src):
        return {"ok": True, "page": page, "action": "nothing there"}
    return put_page(page, pat.sub("", src), agent=agent, s=s,
                    _action="remove_block", _block=block_id, before=src)


# The exact strings the generator leaves for him to fill in. Kept here so the
# fixer and the generator cannot drift apart.
PLACEHOLDERS = {
    "YOUR STREET / UNIT": "BUSINESS_ADDRESS",
    "YOUR EMIRATE e.g. Dubai": "BUSINESS_CITY",
    "YOUR PHONE, IN FULL INTERNATIONAL FORM e.g. +9715XXXXXXXX": "",
    "YOUR REGISTERED BUSINESS NAME": "",
    "YOUR EMAIL": "",
}

# Where a shared header tends to live when it is not in the top folder. The
# /pages listing only covers the top level, so a block pasted into
# includes/header.php is invisible to it — which is exactly where his went.
LIKELY_INCLUDES = (
    "includes/header.php", "includes/head.php", "includes/functions.php",
    "includes/top.php", "includes/meta.php", "includes/layout.php",
    "inc/header.php", "inc/head.php", "inc/functions.php",
    "partials/header.php", "template/header.php", "templates/header.php",
    "header.php", "head.php", "top.php", "common/header.php",
)


def find_placeholders(s=None) -> dict:
    """
    Where an unfilled placeholder is sitting on the live site.

    HE PASTED THE BLOCK BEFORE SAVING HIS ADDRESS, and the markup went live
    reading "YOUR STREET / UNIT". Google reads that as his address and checks
    it against his Business Profile, so it is actively worse than no address
    at all — which is the exact thing the whole placeholder design was meant
    to prevent, defeated by the order he happened to do two steps in.

    Looks in the top-level pages AND in the usual include locations, because
    the /pages listing only covers the top level and his header is under
    includes/.
    """
    found, looked = [], []
    p = pages(s=s)
    names = [x["page"] for x in (p.get("pages") or [])] if p.get("ok") else []
    for name in list(names) + list(LIKELY_INCLUDES):
        if name in looked:
            continue
        looked.append(name)
        got = read_page(name, s=s)
        if not got.get("ok"):
            continue
        html = got.get("html") or ""
        hits = [ph for ph in PLACEHOLDERS if ph in html]
        if hits:
            found.append({"page": name, "placeholders": hits})
    return {"ok": True, "found": found, "looked_in": len(looked),
            "head": (f"{len(found)} file(s) still carry an unfilled placeholder"
                     if found else
                     f"no placeholders left in {len(looked)} file(s) checked")}


def fix_address(agent=AGENT, s=None) -> dict:
    """
    Replace the placeholder address with the one he saved, wherever it sits.

    Only the exact placeholder strings, and only where they appear. Nothing
    else in the file is touched — the block he pasted stays exactly as it is
    apart from the two fields that were never his to begin with.
    """
    from . import seo_answers
    street, city = seo_answers._saved_address()
    if not street:
        return {"ok": False, "error":
                "no address saved yet. Run:  python -m pipelines.site_api "
                '--set-address "Warehouse 12, Al Quoz Industrial 3" --city Dubai'}
    refused = _may_write(agent)
    if refused:
        return {"ok": False, "error": refused}
    where = find_placeholders(s=s)
    swaps = {"YOUR STREET / UNIT": street,
             "YOUR EMIRATE e.g. Dubai": city or "Dubai"}
    changed, failed, left = [], [], []
    for row in where["found"]:
        got = read_page(row["page"], s=s)
        if not got.get("ok"):
            failed.append({"page": row["page"], "why": got.get("error")})
            continue
        html = got["html"]
        did = []
        for ph, real in swaps.items():
            if ph in html:
                html = html.replace(ph, real)
                did.append(ph)
        # Anything left is a field we have no saved value for. Named, not
        # silently overwritten with a guess.
        still = [ph for ph in PLACEHOLDERS if ph in html]
        if still:
            left.append({"page": row["page"], "placeholders": still})
        if not did:
            continue
        r = put_page(row["page"], html, agent=agent, s=s, before=got["html"],
                     summary="put your saved address where the placeholder was")
        if r.get("ok"):
            changed.append({"page": row["page"], "fixed": did,
                            "backup": r.get("backup")})
        else:
            failed.append({"page": row["page"], "why": r.get("error")})
    return {"ok": not failed, "changed": changed, "failed": failed,
            "still_unfilled": left, "address": street, "city": city,
            "head": (f"put '{street}' into {len(changed)} file(s)"
                     if changed else
                     "no placeholder address found to replace")}


def _stem(host) -> str:
    """'worlddealtechnicalservices' for any of his hosts, www or not, any ending."""
    h = (host or "").lower()
    h = h[4:] if h.startswith("www.") else h
    return h.split(".")[0]


# WHICH LINES ARE THE CANONICAL — and which only mention the word.
#
# "Only on lines that mention canonical" was the rule, and on his blog page the
# share buttons are built from a PHP value called $canonical:
#     <a href="https://www.facebook.com/sharer/sharer.php?u=<?= urlencode($canonical) ?>">
# so the fixer rewrote Facebook, WhatsApp and LinkedIn to his own domain and
# every share button on every post led to a page that does not exist. Now a
# line counts only when it IS the canonical: the <link rel="canonical"> tag, or
# the line that sets a canonical value — never a link to anywhere — and only
# his own name is ever changed on it.
def _canonical_line(line) -> bool:
    low = (line or "").lower()
    if "canonical" not in low:
        return False
    if re.search(r"<a\b", low):
        return False
    return bool(re.search(r"rel\s*=\s*[\"']?canonical", low)
                or re.search(r"\$\w*canonical\w*\s*=(?!=)", low)
                or re.search(r"define\s*\(\s*[\"'][a-z_]*canonical", low))


def canonical_plan(s=None) -> dict:
    """
    Which of his source files name the wrong domain in a canonical tag.

    Read from the SOURCE, not from the served page, because a canonical built
    by PHP shows a finished address in the browser while the file holds the
    code that made it. Fixing the served output is not something you can do;
    fixing the file is.
    """
    s = s or settings()
    import urllib.parse
    # THE EXACT ADDRESS, WWW INCLUDED. This used to strip the www and treat the
    # two as one site — so when it pointed his eight .ae canonicals home it
    # wrote https://worlddealtechnicalservices.com/, while the site, his saved
    # address and his Search Console property are all https://www.… . Google
    # was told the real pages were a second copy of the site, and the www pages
    # he was looking at sat in Search Console as not indexed. With www and
    # without are two addresses to Google; the canonical names the one he saved.
    want = urllib.parse.urlparse(s["site"]).netloc.lower()
    if not want:
        return {"ok": False, "error": "no website address saved"}
    p = pages(s=s)
    if not p.get("ok"):
        return {"ok": False, "error": p.get("error")}
    import re
    jobs = []
    stem = _stem(want)
    for row in p.get("pages") or []:
        got = read_page(row["page"], s=s)
        if not got.get("ok"):
            continue
        for line in (got.get("html") or "").splitlines():
            if not _canonical_line(line):
                continue
            for host in re.findall(r'https?://([A-Za-z0-9.\-]+)', line):
                if host.lower() != want and _stem(host) == stem:
                    jobs.append({"page": row["page"], "wrong_host": host,
                                 "line": " ".join(line.split())[:120]})
    return {"ok": True, "want": want, "jobs": jobs,
            "head": (f"{len(jobs)} canonical tag(s) naming a domain that is not "
                     f"{want}" if jobs else
                     f"every canonical already points at {want}")}


def fix_canonical(agent=AGENT, s=None, dry_run=False) -> dict:
    """
    Point every canonical tag at the site it is actually on.

    THE BIGGEST SINGLE PROBLEM ON HIS SITE. All eight pages said their real
    home was worlddealtechnicalservices.ae — a domain with no DNS record. A
    canonical tag tells Google "index that address instead of this one", so
    every page was asking not to be indexed, in favour of nothing.

    Surgical on purpose: only the hostname, only on lines that mention
    canonical. A page can legitimately link to another domain elsewhere, and a
    blanket find-and-replace across the file would rewrite those too.
    """
    plan = canonical_plan(s=s)
    if not plan.get("ok") or not plan["jobs"]:
        return {**plan, "changed": []}
    if dry_run:
        return {**plan, "dry_run": True}
    refused = _may_write(agent)
    if refused:
        return {"ok": False, "error": refused}
    import re
    import urllib.parse
    want = plan["want"]
    scheme = urllib.parse.urlparse((s or settings())["site"]).scheme or "https"
    changed, failed = [], []
    for page in sorted({j["page"] for j in plan["jobs"]}):
        got = read_page(page, s=s)
        if not got.get("ok"):
            failed.append({"page": page, "why": got.get("error")})
            continue
        out, hits = [], 0
        stem = _stem(want)
        for line in (got.get("html") or "").splitlines():
            if _canonical_line(line):
                def swap(m):
                    nonlocal hits
                    host = m.group(2)
                    if host.lower() == want or _stem(host) != stem:
                        return m.group(0)            # right already, or somebody else's
                    hits += 1
                    return f"{scheme}://{want}"
                line = re.sub(r'(https?)://([A-Za-z0-9.\-]+)', swap, line)
            out.append(line)
        if not hits:
            continue
        r = put_page(page, "\n".join(out), agent=agent, s=s,
                     before=got.get("html") or "",
                     summary=f"pointed the canonical tag at {want}")
        if r.get("ok"):
            changed.append({"page": page, "fixed": hits,
                            "backup": r.get("backup")})
        else:
            failed.append({"page": page, "why": r.get("error")})
    return {"ok": not failed, "want": want, "changed": changed, "failed": failed,
            "head": (f"pointed {sum(c['fixed'] for c in changed)} canonical "
                     f"tag(s) on {len(changed)} page(s) at {want}"
                     + (f", {len(failed)} refused" if failed else ""))}


# ---------------------------------------------------------------------------
# FIND AND REPLACE, ACROSS EVERY PAGE
# ---------------------------------------------------------------------------
#
# "ask for to change the wrong domain into the website code... that is
#  worlddealtechnicalservices.ae to replace with correct domain .com no any
#  results at all, asks to replace the dummy mail that is showing on the
#  website with orignal mail did not did that"
#
# He asked for the same shape of thing twice and got nothing both times,
# because the agency had no way to do it. It could put a block in a <head>, it
# could rewrite a canonical tag, it could swap a placeholder address — three
# special cases — and it could not do the general thing those are all instances
# of: change this exact text to that exact text, everywhere it appears.
#
# So this is that. Deliberately dumb and deliberately exact: no regular
# expression, no case folding, no clever matching of things that look similar.
# The owner sees the count per file before anything is written, and what is
# written is exactly what he was shown.

# Below this length a find-and-replace across a whole website is not a fix, it
# is an accident waiting to be approved. 'ae' would hit every 'ae' in every
# word on the site.
MIN_FIND = 6


def plan_replace(find, replace, s=None) -> dict:
    """
    Where this exact text appears, and how many times, changing nothing.

    Reads the top-level pages AND the usual include locations, for the same
    reason find_placeholders does: /pages lists the top level only, and on his
    site the header — the file every page pulls in — lives under includes/.
    """
    find = find or ""
    if len(find) < MIN_FIND:
        return {"ok": False, "error":
                f"'{find}' is too short to replace safely across a whole site. "
                f"Give me at least {MIN_FIND} characters."}
    if replace is None:
        return {"ok": False, "error": "what should it be replaced with?"}
    if find == replace:
        return {"ok": False, "error": "those are the same text"}

    # EVERY FILE, THE SAME WAY EVERYTHING ELSE LOOKS. This read the page list
    # and a list of likely HEADER files — and no footer. His dummy email sits in
    # includes/footer.php (and includes/header.php, and contact.php); with a
    # connector that lists the top level only, the footer was never opened. The
    # shared walk reads the pages, the usual header and footer files, and every
    # file a page includes.
    hits, looked, unread = [], [], []
    for name, html in _walk(s, unread=unread):
        if name is None:
            return {"ok": False, "unreachable": html.get("unreachable"),
                    "error": html.get("error") or "could not list your pages"}
        looked.append(name)
        html = html or ""
        n = html.count(find)
        if not n:
            continue
        # One line of context per file, so what he is approving is legible
        # rather than a number.
        sample = ""
        for line in html.splitlines():
            if find in line:
                sample = " ".join(line.split())[:160]
                break
        hits.append({"page": name, "times": n, "sample": sample})

    total = sum(h["times"] for h in hits)
    return {"ok": True, "find": find, "replace": replace, "pages": hits,
            "files": len(hits), "times": total, "looked_in": len(looked),
            "looked": looked, "unreadable": [u["page"] for u in unread],
            "unread": unread,
            "head": (f"'{find}' appears {total} time(s) in {len(hits)} file(s)"
                     if total else
                     f"'{find}' does not appear in any of the {len(looked)} "
                     "files I can read")
                    + (f" — {len(unread)} file(s) could not be read: "
                       + ", ".join(u["page"] for u in unread[:4]) if unread else "")}


# ---------------------------------------------------------------------------
# ONE EMAIL ADDRESS ON HIS WEBSITE — HIS
# ---------------------------------------------------------------------------
#
# "the mail on webiste is dummy while our orignal contact mail is
#  teamworlddeal@worlddealtechnicalservices.com so i want you to fix that where
#  ever the dummy mail is showing"
#
# His footer carried info@worlddealtechnicalservices.ae — a domain with no mail
# at all, so every enquiry sent to it vanished — and the business details for
# Google said info@worlddealtechnicalservices.com. Nobody needs to know which
# dummy is where: any address on his own domains that is not the one he reads
# is swapped for it, on every page and the shared header and footer, in one
# pass — each page read once and written once, gently, like everything else.
# Other people's addresses are never touched.

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}")


def right_email() -> str:
    """The address he actually reads: BUSINESS_EMAIL, or the one he gave us."""
    try:
        from . import offer_mail
        from .mail import setting
        return (setting("BUSINESS_EMAIL") or "").strip() or offer_mail.DEFAULT_EMAIL
    except Exception:
        return "teamworlddeal@worlddealtechnicalservices.com"


def _brand(s=None) -> str:
    """'worlddealtechnicalservices' — the name his domains share, whatever the ending."""
    try:
        from coordination import site
        host = site.site_host()
    except Exception:
        host = ""
    host = (host or "").lower()
    host = host[4:] if host.startswith("www.") else host
    return host.split(".")[0] if host else ""


def _his_other_addresses(html, right, brand) -> dict:
    """{address: times} for addresses on his own domains that are not `right`."""
    out = {}
    if not brand:
        return out
    for m in _EMAIL_RE.finditer(html or ""):
        addr = m.group(0)
        domain = addr.rsplit("@", 1)[1].lower()
        if addr.lower() == right.lower():
            continue
        if brand in domain.split("."):
            out[addr] = out.get(addr, 0) + 1
    return out


# The files a page pulls in, read off the page's own source — his contact
# details live in the shared footer, which is in no list of guesses.
_INCLUDE_RE = re.compile(r"""(?:include|require)(?:_once)?\s*\(?\s*(?:__DIR__\s*\.\s*)?['"]([^'"]+\.php)['"]""", re.I)
FOOTER_GUESSES = ("includes/footer.php", "inc/footer.php", "partials/footer.php",
                  "footer.php")


def _walk(s=None, unread=None):
    """
    (name, html) for every page, then every file those pages include — each once.

    `unread`, if given, collects the files that are there but could not be read,
    so a search never says "it is in none of your files" when the truth is that
    one of them was never looked at.
    """
    p = pages(s=s)
    if not p.get("ok"):
        yield None, p
        return
    listed = [x["page"] for x in (p.get("pages") or [])]
    todo = list(listed) + [n for n in LIKELY_INCLUDES + FOOTER_GUESSES if n not in listed]
    seen = set()
    while todo:
        name = todo.pop(0)
        if name in seen:
            continue
        seen.add(name)
        got = read_page(name, s=s)
        if not got.get("ok"):
            if got.get("unreachable"):
                yield None, got
                return
            if unread is not None and (name in listed or got.get("status") not in (404, None)):
                unread.append({"page": name, "why": str(got.get("error") or "")[:120]})
            continue
        html = got.get("html") or ""
        for inc in _INCLUDE_RE.findall(html):
            inc = inc.replace("\\", "/").lstrip("./").lstrip("/")
            if inc and inc not in seen and inc not in todo and ".." not in inc:
                todo.insert(0, inc)
        yield name, html


def plan_emails(s=None) -> dict:
    """Where the addresses that are not his appear. Changes nothing."""
    right, brand = right_email(), _brand(s)
    if not brand:
        return {"ok": False, "error": "no website is saved, so there is nothing to look at"}
    found, per_page, unread, looked = {}, [], [], []
    for name, html in _walk(s, unread=unread):
        if name is None:
            return {"ok": False, "unreachable": html.get("unreachable"),
                    "error": html.get("error") or "could not list your pages"}
        looked.append(name)
        wrong = _his_other_addresses(html, right, brand)
        if wrong:
            per_page.append({"page": name, "wrong": wrong})
            for a, n in wrong.items():
                found[a] = found.get(a, 0) + n
    return {"ok": True, "right": right, "wrong": found, "pages": per_page,
            "times": sum(found.values()), "looked": looked, "unread": unread}


def use_one_email(agent=AGENT, s=None, dry_run=False) -> dict:
    """Every address on his own domains, on every page, made the one he reads."""
    right, brand = right_email(), _brand(s)
    refused = _may_write(agent)
    if refused and not dry_run:
        cid = _queue_for_him(agent, "use_one_email", "site", {
            "change": {"call": "use_one_email"},
            "why": f"{agent} wants every email address on your website to be {right}. "
                   "Only the IT Releaser writes to your site, so it waits for your yes."})
        return {"ok": False, "queued": cid, "error": refused}
    changed, failed, swapped = [], [], {}
    for name, html in _walk(s):
        if name is None:
            return {"ok": False, "unreachable": html.get("unreachable"),
                    "error": html.get("error") or "could not list your pages",
                    "changed": changed}
        wrong = _his_other_addresses(html, right, brand)
        if not wrong:
            continue
        new = html
        for addr in sorted(wrong, key=len, reverse=True):
            new = new.replace(addr, right)
        times = sum(wrong.values())
        if dry_run:
            changed.append({"page": name, "times": times, "wrong": wrong})
            continue
        r = put_page(name, new, agent=agent, s=s, _action="replace", before=html,
                     summary=(f"changed the email address to {right} "
                              f"({times} time{'s' if times != 1 else ''}: "
                              + ", ".join(sorted(wrong)) + ")"))
        if r.get("ok"):
            changed.append({"page": name, "times": times, "backup": r.get("backup")})
            for a, n in wrong.items():
                swapped[a] = swapped.get(a, 0) + n
        elif r.get("unreachable"):
            return {"ok": False, "unreachable": True, "error": r.get("error"),
                    "changed": changed}
        else:
            failed.append({"page": name, "why": r.get("error")})
    total = sum(c["times"] for c in changed)
    head = (f"every email address on your website is now {right} — changed {total} "
            f"time(s) on {len(changed)} page(s): " + ", ".join(sorted(swapped))
            if changed and not dry_run else
            (f"would change {total} address(es) on {len(changed)} page(s)" if dry_run
             else f"every email address on your website was already {right}"))
    return {"ok": not failed, "changed": changed, "failed": failed, "head": head,
            "right": right, **({"error": f"{len(failed)} page(s) could not be written: "
                                         + str(failed[0]["why"])} if failed else {})}


# ---------------------------------------------------------------------------
# ONE DOMAIN — his .com, wherever an old ending was left behind
# ---------------------------------------------------------------------------
#
# "wrong seo wording like in code our website domain is .ae while we have .com
#  domain"
#
# The site was built for worlddealtechnicalservices.ae and lives on the .com.
# The .ae has no DNS at all, so every link, canonical, bit of markup and mailto
# still naming it points at nothing — and tells Google the business lives
# somewhere that does not exist. Same shape as the email: nobody needs to know
# where each one is. Any mention of his own name with a different ending is
# given the ending his site is actually served from (SITE_URL), on every page
# and the shared header and footer, each read once and written once. An
# ADDRESS on the dead domain becomes the address he reads, never a made-up
# info@ on the .com. Other people's domains are never touched.

def _right_host(s=None) -> str:
    """'worlddealtechnicalservices.com' — where his site is served from, no www."""
    try:
        from coordination import site
        host = site.site_host()
    except Exception:
        host = ""
    host = (host or "").lower().strip(".")
    return host[4:] if host.startswith("www.") else host


def _domain_re(brand):
    return re.compile(r"(?<![A-Za-z0-9-])" + re.escape(brand)
                      + r"\.((?:co|com|net|org)\.[a-z]{2}|[a-z]{2,6})(?![A-Za-z0-9-])",
                      re.I)


def _site_host() -> str:
    """
    The host exactly as he saved it — 'www.worlddealtechnicalservices.com'.

    Not site.site_host(), which is the bare domain FOR SHOWING and takes the
    www off: built on that, the www half of the one-address fix could never
    switch on outside the tests.
    """
    try:
        from coordination import site
        u = site.site_url() or ""
    except Exception:
        return ""
    return u.split("://", 1)[-1].split("/", 1)[0].split("?", 1)[0].strip().lower().strip(".")


def _one_domain(html, brand, right_host, right_mail, site_host=None):
    """(html with every other ending made his, {wrong domain: times}).

    And when his site lives at www, a link to the bare domain — https://
    worlddealtechnicalservices.com/… — is given the www too. Both answer, so
    nothing looks broken, but every such link and canonical tells Google there
    is a second copy of the site; one address is what gets the pages indexed.
    """
    rx = _domain_re(brand)
    hits = {}
    site_host = (site_host if site_host is not None else _site_host()) or right_host
    www = site_host.startswith("www.") and site_host[4:] == right_host

    def count(dom):
        hits[dom.lower()] = hits.get(dom.lower(), 0) + 1

    def addr(m):
        a = m.group(0)
        dom = a.rsplit("@", 1)[1]
        if rx.fullmatch(dom) and dom.lower() != right_host:
            count(dom)
            return right_mail
        return a

    def host(m):
        dom = m.group(0)
        if dom.lower() == right_host:
            return dom
        count(dom)
        # www.<old> keeps its www; a bare <old> becomes the address he uses
        before = m.string[max(0, m.start() - 4):m.start()].lower()
        return right_host if before == "www." or not www else site_host

    new = _EMAIL_RE.sub(addr, html or "")
    new = rx.sub(host, new)
    if www:
        bare_rx = re.compile(r"(?<=//)" + re.escape(right_host) + r"(?![A-Za-z0-9.-])", re.I)

        def add_www(m):
            hits[f"{right_host} (without www)"] = hits.get(f"{right_host} (without www)", 0) + 1
            return site_host
        new = bare_rx.sub(add_www, new)
    return new, hits


def plan_domains(s=None) -> dict:
    """Where his name appears with the wrong ending. Changes nothing."""
    right, brand = _right_host(s), _brand(s)
    if not right or not brand:
        return {"ok": False, "error": "no website is saved, so there is nothing to look at"}
    found, per_page = {}, []
    for name, html in _walk(s):
        if name is None:
            return {"ok": False, "unreachable": html.get("unreachable"),
                    "error": html.get("error") or "could not list your pages"}
        _new, wrong = _one_domain(html, brand, right, right_email(), _site_host())
        if wrong:
            per_page.append({"page": name, "wrong": wrong})
            for d, n in wrong.items():
                found[d] = found.get(d, 0) + n
    return {"ok": True, "right": right, "wrong": found, "pages": per_page,
            "times": sum(found.values())}


def use_one_domain(agent=AGENT, s=None, dry_run=False) -> dict:
    """Every mention of his name with another ending, on every page, made his real one."""
    right, brand, mail = _right_host(s), _brand(s), right_email()
    shown = _site_host() or right
    if not right or not brand:
        return {"ok": False, "error": "no website is saved, so there is nothing to change"}
    refused = _may_write(agent)
    if refused and not dry_run:
        cid = _queue_for_him(agent, "use_one_domain", "site", {
            "change": {"call": "use_one_domain"},
            "why": f"{agent} wants every mention of your old domain on your website "
                   f"to be {right}. Only the IT Releaser writes to your site, so it "
                   "waits for your yes."})
        return {"ok": False, "queued": cid, "error": refused}
    changed, failed, swapped = [], [], {}
    for name, html in _walk(s):
        if name is None:
            return {"ok": False, "unreachable": html.get("unreachable"),
                    "error": html.get("error") or "could not list your pages",
                    "changed": changed}
        new, wrong = _one_domain(html, brand, right, mail, _site_host())
        if not wrong or new == html:
            continue
        times = sum(wrong.values())
        if dry_run:
            changed.append({"page": name, "times": times, "wrong": wrong})
            continue
        r = put_page(name, new, agent=agent, s=s, _action="replace", before=html,
                     summary=(f"changed the address to {shown} ({times} "
                              f"time{'s' if times != 1 else ''}: "
                              + ", ".join(sorted(wrong)) + ")"))
        if r.get("ok"):
            changed.append({"page": name, "times": times, "backup": r.get("backup")})
            for d, n in wrong.items():
                swapped[d] = swapped.get(d, 0) + n
        elif r.get("unreachable"):
            return {"ok": False, "unreachable": True, "error": r.get("error"),
                    "changed": changed}
        else:
            failed.append({"page": name, "why": r.get("error")})
    total = sum(c["times"] for c in changed)
    head = (f"your website now says {shown} everywhere — changed {total} time(s) on "
            f"{len(changed)} page(s): " + ", ".join(sorted(swapped))
            if changed and not dry_run else
            (f"would change {total} mention(s) on {len(changed)} page(s)" if dry_run
             else f"your website already says {shown} everywhere"))
    return {"ok": not failed, "changed": changed, "failed": failed, "head": head,
            "right": right, **({"error": f"{len(failed)} page(s) could not be written: "
                                         + str(failed[0]["why"])} if failed else {})}


# ---------------------------------------------------------------------------
# THE SHARE BUTTONS ON HIS POSTS
# ---------------------------------------------------------------------------
#
# The old canonical fixer (see _canonical_line) turned
#     https://www.facebook.com/sharer/sharer.php?u=…
#     https://wa.me/?text=…
#     https://www.linkedin.com/sharing/share-offsite/?url=…
# into his own domain, so all three buttons on every post opened a 404 on his
# site. Each network's share address has a path nothing else has, so the
# network is known from the path alone and put back.
SHARE_HOMES = (
    (r"sharer/sharer\.php", "https://www.facebook.com/sharer/sharer.php"),
    (r"sharing/share-offsite/?", "https://www.linkedin.com/sharing/share-offsite/"),
    (r"intent/tweet", "https://twitter.com/intent/tweet"),
    (r"pin/create/button/?", "https://pinterest.com/pin/create/button/"),
    (r"send(?=\?text=)", "https://api.whatsapp.com/send"),
    (r"(?=\?text=)", "https://wa.me/"),
)


def _share_fixed(src, stem):
    """(src with each broken share address put back, how many)."""
    n = 0
    for path, home in SHARE_HOMES:
        rx = re.compile(r"https?://(?:www\.)?" + re.escape(stem) + r"\.[a-z.]+/" + path, re.I)
        src, k = rx.subn(home, src)
        n += k
    return src, n


def plan_share_buttons(s=None) -> dict:
    """Which files carry share buttons pointing at his own site. Changes nothing."""
    stem = _stem(_right_host(s))
    if not stem:
        return {"ok": False, "error": "no website is saved, so there is nothing to look at"}
    found = []
    for name, html in _walk(s):
        if name is None:
            return {"ok": False, "unreachable": html.get("unreachable"),
                    "error": html.get("error") or "could not list your pages"}
        _new, n = _share_fixed(html, stem)
        if n:
            found.append({"page": name, "times": n})
    return {"ok": True, "pages": found, "times": sum(f["times"] for f in found)}


def repair_share_buttons(agent=AGENT, s=None, dry_run=False) -> dict:
    """Put Facebook, WhatsApp and LinkedIn back in the share buttons. Releaser only."""
    stem = _stem(_right_host(s))
    if not stem:
        return {"ok": False, "error": "no website is saved, so there is nothing to change"}
    refused = _may_write(agent)
    if refused and not dry_run:
        cid = _queue_for_him(agent, "repair_share_buttons", "site", {
            "change": {"call": "repair_share_buttons"},
            "why": "The share buttons on your posts open a page on your own site "
                   "instead of Facebook, WhatsApp and LinkedIn."})
        return {"ok": False, "queued": cid, "error": refused}
    changed, failed = [], []
    for name, html in _walk(s):
        if name is None:
            return {"ok": False, "unreachable": html.get("unreachable"),
                    "error": html.get("error") or "could not list your pages",
                    "changed": changed}
        new, n = _share_fixed(html, stem)
        if not n:
            continue
        if dry_run:
            changed.append({"page": name, "times": n})
            continue
        r = put_page(name, new, agent=agent, s=s, _action="replace", before=html,
                     summary=f"put Facebook, WhatsApp and LinkedIn back in {n} share button(s)")
        if r.get("ok"):
            changed.append({"page": name, "times": n, "backup": r.get("backup")})
        elif r.get("unreachable"):
            return {"ok": False, "unreachable": True, "error": r.get("error"),
                    "changed": changed}
        else:
            failed.append({"page": name, "why": r.get("error")})
    total = sum(c["times"] for c in changed)
    return {"ok": not failed, "changed": changed, "failed": failed,
            "head": (f"the share buttons go to Facebook, WhatsApp and LinkedIn again — "
                     f"{total} fixed on {len(changed)} page(s)" if changed else
                     "no broken share buttons found"),
            **({"error": f"{len(failed)} page(s) could not be written: "
                         + str(failed[0]["why"])} if failed else {})}


# ---------------------------------------------------------------------------
# THE SITEMAP — see pipelines/sitemap.py
# ---------------------------------------------------------------------------

def publish_sitemap(agent=AGENT, s=None, dry_run=False) -> dict:
    """Put the sitemap on his website and give it to Search Console. Releaser only."""
    from . import sitemap
    d = sitemap.discover()
    if not d.get("ok"):
        return {"ok": False, "error": "the sitemap could not be made: "
                                      + str(d.get("error") or "no page could be read")}
    url = sitemap.live_url()
    if dry_run:
        return {"ok": True, "dry_run": True, "pages": d["pages"], "url": url,
                "head": f"would put {len(d['pages'])} page(s) in {url}"}
    refused = _may_write(agent)
    if refused:
        cid = _queue_for_him(agent, "publish_sitemap", "site", {
            "change": {"call": "publish_sitemap"},
            "why": "A sitemap tells Google every page your website has."})
        return {"ok": False, "queued": cid, "error": refused}
    was = read_page(sitemap.PAGE, s=s)
    r = put_page(sitemap.PAGE, sitemap.as_php(d["pages"]), agent=agent, s=s,
                 _action="new_page" if not was.get("ok") else "replace",
                 before=(was.get("html") if was.get("ok") else None),
                 summary=f"put a sitemap of {len(d['pages'])} page(s) on your website")
    if not r.get("ok"):
        return {"ok": False, "unreachable": r.get("unreachable"),
                "error": r.get("error") or "the sitemap could not be written"}
    try:
        from . import seo_intel
        sub = seo_intel.gsc_submit_sitemap(url)
    except Exception as e:
        sub = {"ok": False, "why": f"{type(e).__name__}: {e}"}
    head = (f"your sitemap is on your website at {url}, listing {len(d['pages'])} page(s)"
            + (", and Search Console has been given it" if sub.get("ok") else
               f" — Search Console could not be given it automatically ({sub.get('why')}); "
               f"in Search Console open Sitemaps and add  sitemap.php"))
    return {"ok": True, "url": url, "pages": d["pages"], "submitted": bool(sub.get("ok")),
            "head": head}


# ---------------------------------------------------------------------------
# WHAT A VISITOR STILL SEES — checked on the live site, not in the files
# ---------------------------------------------------------------------------
#
# "almost 10th time i asked to change the dummy mail ... it did not change".
# Each time, the routine changed every file it could read that held the dummy
# address, found the rest of the files clean, and the Releaser marked the job
# done — while his footer went on showing info@worlddealtechnicalservices.ae,
# because the footer does not get it from a file the agency can edit (his admin
# panel's settings, most likely). Done is now decided by the page a visitor
# sees. What is still showing is named, with where it comes from, and the job
# goes back to him with the one thing only he can do.

LIVE_PATHS = ("", "contact.php", "about.php")
# A wider look after a find-and-replace: the pages a visitor is most likely on.
LIVE_LOOK = ("", "contact.php", "about.php", "service.php", "services.php", "faq.php",
             "blog.php", "gallery.php")


def live_has(text, get=None) -> list:
    """The live pages (by address) still showing this exact text."""
    try:
        from coordination import site
        base = (site.site_url() or "").rstrip("/")
    except Exception:
        base = ""
    if not base or not text:
        return []
    if get is None:
        if os.environ.get("AGENCY_IGNORE_DOTENV") == "1":
            return []
        import requests

        def get(u):
            r = requests.get(u, timeout=20, headers={"User-Agent": "Mozilla/5.0 (glasswork-check)"})
            return r.status_code, r.text or ""
    out = []
    for path in LIVE_LOOK:
        try:
            status, html = get(base + "/" + path)
        except Exception:
            continue
        if status == 200 and text in (html or ""):
            out.append(base + "/" + path)
    return out


def still_showing(s=None, get=None) -> dict:
    """{ok, emails: {address: times}, domains: {domain: times}, read: [...]} on his live pages."""
    try:
        from coordination import site
        base = (site.site_url() or "").rstrip("/")
    except Exception:
        base = ""
    if not base:
        return {"ok": False, "error": "no website address saved"}
    right, brand, rhost = right_email(), _brand(s), _right_host(s)
    if get is None:
        if os.environ.get("AGENCY_IGNORE_DOTENV") == "1":
            # The test suite never reaches a real website.
            return {"ok": False, "error": "not read during the test run"}
        import requests

        def get(u):
            r = requests.get(u, timeout=20, headers={"User-Agent": "Mozilla/5.0 (glasswork-check)"})
            return r.status_code, r.text or ""
    emails, domains, read = {}, {}, []
    for path in LIVE_PATHS:
        url = base + "/" + path
        try:
            status, html = get(url)
        except Exception:
            continue
        if status != 200:
            continue
        read.append(url)
        for a, n in _his_other_addresses(html, right, brand).items():
            emails[a] = emails.get(a, 0) + n
        without = _EMAIL_RE.sub(" ", html)
        for m in _domain_re(brand).finditer(without):
            d = m.group(0).lower()
            if d != rhost:
                domains[d] = domains.get(d, 0) + 1
    return {"ok": bool(read), "emails": emails, "domains": domains, "read": read,
            "error": "" if read else "none of the pages could be read"}


def why_still_showing(left, s=None) -> str:
    """The plain sentence for what is still showing, and where it must come from."""
    try:
        plan = plan_emails(s=s)
    except Exception:
        plan = {}
    in_files = plan.get("wrong") or {}
    unread = plan.get("unread") or []
    where = {}
    for pg in plan.get("pages") or []:
        for a in pg.get("wrong") or {}:
            where.setdefault(a, []).append(pg["page"])
    bits = []
    right = right_email()
    for a in sorted(left.get("emails") or {}):
        if a in in_files:
            bits.append(f"{a} is still written in " + ", ".join(where.get(a, [])[:5])
                        + " — the change did not take there; press Try again, and if it "
                          "fails again the reason is on the change")
        elif unread:
            bits.append(f"your live site still shows {a}, and "
                        + ", ".join(u["page"] for u in unread[:4])
                        + " could not be read (" + (unread[0].get("why") or "no reason")
                        + ") — it is most likely in there; press Try again")
        else:
            bits.append(
                f"your live site still shows {a}, but it is not written in any website file "
                f"the agency is allowed to edit. So it comes from your admin panel's settings "
                f"(most likely) — open your admin panel, find the contact or site settings, "
                f"and change the email to {right}. If your admin panel has no email setting, "
                f"it is in a file inside a folder the agency never opens (admin, config, "
                f"assets): in Hostinger's File Manager, search your website folder for {a} "
                f"and change it there. The agency is not allowed into your admin panel, "
                "database or those folders, by design")
    for d in sorted(left.get("domains") or {}):
        bits.append(f"your live site still mentions {d} — most likely from your admin "
                    "panel's settings or a post's own text; change it there")
    return "; ".join(bits)


def replace_text(find, replace, agent=AGENT, s=None, dry_run=False) -> dict:
    """
    Change this exact text to that, everywhere it appears. Releaser only.

    Every write goes through put_page, so each file is backed up by the server
    and each change is recorded in the change log with the command to undo it —
    the same road every other change takes. Nothing special-cased here.
    """
    plan = plan_replace(find, replace, s=s)
    if not plan.get("ok") or not plan["pages"]:
        return {**plan, "changed": []}
    if dry_run:
        return {**plan, "dry_run": True}
    refused = _may_write(agent)
    if refused:
        cid = _queue_for_him(agent, "replace_text", "site", {
            "change": {"replace": find, "with": replace,
                       "pages": plan["files"], "times": plan["times"]},
            "why": (f"{agent} wants to change {find!r} to {replace!r} in "
                    f"{len(plan['files'])} file(s). Only the IT Releaser writes "
                    "to your site, so it waits for your yes.")})
        return {"ok": False, "queued": cid, "error": refused}

    changed, failed = [], []
    for h in plan["pages"]:
        got = read_page(h["page"], s=s)
        if not got.get("ok"):
            failed.append({"page": h["page"], "why": got.get("error")})
            continue
        html = got["html"]
        if find not in html:
            continue                      # changed under us since the plan
        r = put_page(h["page"], html.replace(find, replace), agent=agent, s=s,
                     _action="replace", before=html,
                     summary=(f"changed \u201c{find[:60]}\u201d to \u201c{replace[:60]}\u201d "
                              f"({h['times']} time{'s' if h['times'] != 1 else ''})"))
        if r.get("ok"):
            changed.append({"page": h["page"], "times": h["times"],
                            "backup": r.get("backup")})
        else:
            failed.append({"page": h["page"], "why": r.get("error")})
    return {"ok": not failed, "find": find, "replace": replace,
            "changed": changed, "failed": failed,
            "unread": plan.get("unread") or [], "looked_in": plan.get("looked_in"),
            "head": (f"changed '{find}' to '{replace}' "
                     f"{sum(c['times'] for c in changed)} time(s) across "
                     f"{len(changed)} file(s)"
                     + (f", {len(failed)} refused" if failed else ""))}


def blocks_on(page, s=None) -> list:
    """Which agency blocks a page is currently carrying."""
    got = read_page(page, s=s)
    if not got.get("ok"):
        return []
    import re
    return sorted(set(re.findall(r"<!-- agency:([a-z0-9\-]+) -->",
                                 got.get("html") or "")))


def undo_schema(agent=AGENT, s=None) -> dict:
    """
    Take the agency's markup back off every page that has it.

    Needed because the first real run put it on the wrong thirteen files: an
    old duplicate copy of the site in .html, plus install.php and default.php,
    while refusing every page he actually serves. Undoing that had to be one
    command, not thirteen.
    """
    p = pages(s=s)
    if not p.get("ok"):
        return {"ok": False, "error": p.get("error")}
    cleaned, failed = [], []
    for row in p.get("pages") or []:
        name = row["page"]
        for bid in blocks_on(name, s=s):
            r = remove_head_block(name, bid, agent=agent, s=s)
            if r.get("ok") and r.get("action") != "nothing there":
                cleaned.append({"page": name, "id": bid})
            elif not r.get("ok"):
                failed.append({"page": name, "id": bid, "why": r.get("error")})
    return {"ok": not failed, "cleaned": cleaned, "failed": failed,
            "head": (f"removed the agency markup from {len(cleaned)} page(s)"
                     + (f", {len(failed)} refused" if failed else ""))}


def undo(page="", backup="", agent=AGENT, s=None) -> dict:
    """
    Put a page back. Deliberately NOT gated behind the Releaser.

    Undoing is the one write safer to allow than to delay: if an agent has put
    something wrong on the website at two in the morning, the last thing
    anybody wants is the fix sitting in an approval queue until breakfast. It
    cannot invent content — it can only restore a file this system already
    wrote down.
    """
    body = {}
    if backup:
        body["backup"] = backup
    if page:
        body["page"] = page
    if not body:
        return {"ok": False, "error": "which page, or which backup?"}
    r = call("/rollback", "POST", body, s=s)
    # Recorded like any other change, because it IS one. A log that shows the
    # edit and not the reversal is a log that tells him the wrong thing is
    # still on his site.
    return _log_write("restore", page or r.get("page") or "", agent, r,
                      summary=(f"put {page or r.get('page') or 'a page'} back "
                               "to how it was before"
                               + (f" (from {backup})" if backup else "")))


def test_enquiry_path(s=None) -> dict:
    """The daily proof that his contact form still delivers."""
    return call("/contact", "POST", {}, s=s)


# ---------------------------------------------------------------------------
# THE FIRST REAL JOB: THE MARKUP THAT IS FAILING HIS AUDIT
# ---------------------------------------------------------------------------

def save_address(street, city="") -> dict:
    """
    Write the workshop address into .env, and rebuild the drafts that use it.

    Saved rather than hand-edited into the generated file, because a hand-edit
    is undone the next time the draft is regenerated — and then --apply-schema
    refuses again with no obvious reason why.
    """
    street = (street or "").strip()
    city = (city or "").strip()
    if len(street) < 6:
        return {"ok": False, "error": "that is too short to be an address"}
    if street.upper().startswith("YOUR "):
        return {"ok": False, "error": "that is still the placeholder"}
    try:
        from providers import integrations
        integrations.write_env({"BUSINESS_ADDRESS": street,
                                **({"BUSINESS_CITY": city} if city else {})})
    except Exception as e:
        return {"ok": False, "error": f"could not save it: {e}"}
    # Rebuild the LocalBusiness draft straight away, so the very next
    # --apply-schema uses the real address instead of asking again.
    rebuilt = ""
    try:
        from . import seo_answers
        from coordination import site as site_cfg
        base = site_cfg.site_url()
        snippet, _missing = seo_answers.localbusiness_jsonld(base, [])
        p = _drafts_dir() / "1-localbusiness-every-page.html"
        if p.exists() and snippet:
            old = p.read_text(encoding="utf-8")
            import re
            new = re.sub(r'<script type="application/ld\+json">.*?</script>',
                         snippet, old, count=1, flags=re.S)
            p.write_text(new, encoding="utf-8")
            rebuilt = p.name
    except Exception as e:
        return {"ok": True, "saved": street,
                "head": f"saved '{street}', but the draft could not be "
                        f"rebuilt ({e}). Run: python -m pipelines.seo_answers "
                        "--localbusiness"}
    return {"ok": True, "saved": street, "city": city,
            "head": (f"saved: {street}" + (f", {city}" if city else "")
                     + (f" — and rebuilt {rebuilt}" if rebuilt else "")
                     + ". Now run: python -m pipelines.site_api --apply-schema")}


def _drafts_dir():
    from pathlib import Path
    return Path(__file__).parents[1] / "paste-into-your-site"


def _one_block(text) -> str:
    """Just the <script>…</script>, without the explanatory comment above it."""
    import re
    m = re.search(r'<script type="application/ld\+json">.*?</script>',
                  text or "", re.S)
    return m.group(0) if m else ""


def _draft(name) -> str:
    """
    The block to put on the site, built fresh where it can be.

    THE FILE IS A SAMPLE; .env IS THE TRUTH.

    --set-address used to write the real address into this tracked file, and
    that was a trap of my own making: run.py refuses to pull while anything is
    modified, so saving his address quietly blocked every future update — the
    same accident npm caused with package-lock.json, which this program
    already had to learn once.

    Rebuilding from BUSINESS_ADDRESS at the moment of use means the file can be
    reverted by any pull, at any time, and the right address still goes on the
    site. Falls back to the file when the live build cannot run, so a broken
    import degrades to yesterday's answer rather than to nothing.
    """
    if "localbusiness" in name.lower():
        try:
            from . import seo_answers
            from coordination import site as site_cfg
            snippet, _missing = seo_answers.localbusiness_jsonld(
                site_cfg.site_url(), [])
            if snippet and "YOUR STREET" not in snippet:
                return _one_block(snippet)
        except Exception:
            pass
    p = _drafts_dir() / name
    return _one_block(p.read_text(encoding="utf-8")) if p.exists() else ""


def schema_plan(s=None) -> dict:
    """
    What the agency would put on which page, and what is stopping it.

    Read-only. Run this first, always — it is the difference between "I think
    this will work" and knowing which seven files are about to change.
    """
    lb = _draft("1-localbusiness-every-page.html")
    faq = _draft("2-faq-schema-faq-page.html")
    problems = []
    if not lb:
        problems.append("the LocalBusiness draft is missing — run "
                        "`python -m pipelines.seo_answers --localbusiness`")
    if not faq:
        problems.append("the FAQ draft is missing — run "
                        "`python -m pipelines.seo_answers --faq /faq.php`")
    # The two placeholders are left in CAPITALS on purpose by the generator. A
    # wrong address costs the map-pack ranking the block exists to win, so it
    # is better to refuse than to publish "YOUR STREET / UNIT" to the world.
    if lb and ("YOUR STREET" in lb or "YOUR EMIRATE" in lb):
        problems.append(
            "your workshop address is not saved yet, and it is nowhere on your "
            "website for the agency to read. A wrong address here costs the "
            "map ranking this markup exists to win, so it will not guess. "
            "Save it once with:  python -m pipelines.site_api --set-address "
            '"Warehouse 12, Al Quoz Industrial 3" --city Dubai   '
            "then run --apply-schema again.")
    p = pages(s=s)
    on_disk = [x["page"] for x in (p.get("pages") or [])] if p.get("ok") else []
    if not p.get("ok"):
        problems.append("could not list your pages: " + str(p.get("error")))

    # WHAT IS ON DISK IS NOT WHAT HE SERVES, AND THE FIRST REAL RUN PROVED IT.
    #
    # The folder held an old duplicate of the whole site in .html alongside the
    # live .php, plus install.php and default.php. Working from the directory
    # listing, this put the markup on thirteen files — the duplicates, the
    # installer — and refused every page he actually serves.
    #
    # The site itself knows which pages are real: they are the ones its own
    # navigation links to. Ask the site, not the disk.
    live = live_pages(s=s)
    known = [pg for pg in on_disk if pg in live] if live else on_disk
    ignored = [pg for pg in on_disk if pg not in known]

    # AND THE HEAD MAY NOT BE IN THE PAGE AT ALL.
    #
    # His pages share one header include, so </head> lives in that file and
    # nowhere else — which is why every real page was refused with "no </head>
    # to insert before". One block in the include is one block on every page,
    # which is what he wanted in the first place.
    holder = head_holder(known, s=s)
    # Which of his real pages can actually take a block on their own. Asked
    # BEFORE choosing targets, so "no page has a head and no include was
    # found" is a state this can recognise instead of quietly writing
    # somewhere else.
    own_head = [pg for pg in known if _has_own_head(pg, s=s)]
    if holder:
        jobs = [{"page": holder, "id": SCHEMA_ID, "what": "LocalBusiness",
                 "bytes": len(lb), "why": "your shared header — one paste, "
                                          "every page"}]
    elif own_head:
        jobs = [{"page": pg, "id": SCHEMA_ID, "what": "LocalBusiness",
                 "bytes": len(lb)} for pg in own_head]
    else:
        # NO TARGET. SAY SO; DO NOT FIND ONE.
        #
        # The plan offered to write into default.php — a leftover from his
        # host, not linked from anywhere, and not part of his site. It was
        # picked because it contains </head>, which every complete page does.
        # Writing there would have put the markup somewhere nobody visits and
        # left every real page without it, while reporting success.
        jobs = []
        problems.append(
            "none of your pages carries its own </head>, and no shared header "
            "file could be found to put one block in. Rather than guess at a "
            "file, this is the one to do by hand: open the header your pages "
            "include and paste paste-into-your-site/HEAD-BLOCK-paste-once.php "
            "just above </head>. If you tell me what that file is called, the "
            "agency can take it from there.")
    # The FAQ block is the exception: it must appear on the FAQ page only, so
    # it never goes in a shared header. If the head is in an include there is
    # nowhere page-specific to put it, and saying so beats putting FAQ markup
    # on all seven pages, which can get the lot ignored by Google.
    faq_page = next((pg for pg in known if pg.startswith("faq.")), "")
    if faq and faq_page and not holder:
        jobs.append({"page": faq_page, "id": FAQ_ID, "what": "FAQPage",
                     "bytes": len(faq)})
    elif faq and faq_page and holder:
        problems.append(
            f"the FAQ markup cannot go in a shared header — it belongs on "
            f"{faq_page} alone, or Google may ignore all of it. Paste "
            f"paste-into-your-site/2-faq-schema-faq-page.html into {faq_page} "
            f"by hand, or wrap it in the if-statement in "
            f"paste-into-your-site/HEAD-BLOCK-paste-once.php.")

    return {"ok": not problems, "problems": problems, "jobs": jobs,
            "pages_seen": known, "ignored": ignored, "head_include": holder,
            "head": (f"{len(jobs)} block(s)"
                     + (f" in {holder}, which every page includes" if holder
                        else f" across {len(known)} page(s)")
                     + (f" — {len(ignored)} file(s) on the server ignored, "
                        "they are not pages your site links to" if ignored else "")
                     if not problems else problems[0])}


def live_pages(s=None) -> list:
    """
    The filenames the SITE links to, as opposed to what is sitting on disk.

    Reads his home page and follows its own navigation, the same way
    site_search does. Returns [] if the site cannot be read, and the caller
    then falls back to the directory listing rather than doing nothing.
    """
    base = (s or settings())["site"]
    if not base:
        return []
    try:
        from . import fetcher, site_search
        r = fetcher.fetch(base, timeout=20, allow_paid=False)
        if not r.get("ok") or not r.get("html"):
            return []
        out = {"index.php"}
        for u in site_search.own_pages(r["html"], base.rstrip("/")):
            name = urllib.parse.urlparse(u).path.strip("/")
            if name:
                out.add(name)
        return sorted(out)
    except Exception:
        return []


def head_holder(candidates, s=None) -> str:
    """
    The one file that actually contains </head>, when the pages do not.

    A trade site built with PHP includes has one header file and seven pages
    that pull it in. The markup belongs in the header, once.
    """
    # NO CANDIDATES IS NOT NO ANSWER.
    #
    # candidates comes from live_pages(), which reads the site over the public
    # internet and follows its own navigation. That fails for reasons that have
    # nothing to do with the question being asked: the site is briefly down, an
    # outbound fetch is blocked, the home page is behind something. Returning ""
    # then made the Fixer downgrade a fact it could have patched — the viewport
    # tag — into advice, and he would have been told to paste it in by hand for
    # no reason at all.
    #
    # The admin endpoint can list the files whatever the public side is doing,
    # and the search below already ends there. So with nothing to go on, start
    # from the listing instead of giving up.
    if not candidates:
        return _from_the_listing([], s=s)
    # If a real page has its own </head>, there is no include to find.
    for pg in candidates[:4]:
        got = read_page(pg, s=s)
        if got.get("ok") and "</head" in (got.get("html") or "").lower():
            return ""
    # No page has one, so find the file they all include.
    seen = set()
    for pg in candidates:
        got = read_page(pg, s=s)
        if not got.get("ok"):
            continue
        import re
        for m in re.findall(r"""include(?:_once)?\s*\(?\s*['"]([^'"]+\.php)['"]""",
                            got.get("html") or "", re.I):
            seen.add(m.lstrip("./"))
        for m in re.findall(r"""require(?:_once)?\s*\(?\s*['"]([^'"]+\.php)['"]""",
                            got.get("html") or "", re.I):
            seen.add(m.lstrip("./"))
    for name in sorted(seen):
        got = read_page(name, s=s)
        if got.get("ok") and "</head" in (got.get("html") or "").lower():
            return name

    return _from_the_listing(candidates, s=s)


def _from_the_listing(candidates, s=None) -> str:
    """
    NOTHING NAMED AN INCLUDE WE COULD READ, SO GO AND LOOK.

    An include can be written a dozen ways — a constant, a variable, a front
    controller, a path built at run time — and a regex will miss most of them.
    Rather than give up and refuse every page again, ask the server for its
    files and read the ones that are plausibly a header. Cheap: a handful of
    reads, and only when the regex has already come up empty.

    The usual include locations are searched too. The listing only covers the
    top level, and on his site the header is under includes/ — so a search that
    trusted the listing alone would look everywhere except where it is.
    """
    names, seen = [], set()
    p = pages(s=s)
    for row in (p.get("pages") or []) if p.get("ok") else []:
        n = row["page"]
        if n in candidates or n in seen:
            continue
        seen.add(n)
        names.append(n)
    for n in LIKELY_INCLUDES:
        if n not in seen and n not in candidates:
            seen.add(n)
            names.append(n)
    if not names:
        return ""
    likely = [n for n in names if any(w in n.lower() for w in
              ("head", "header", "top", "meta", "layout", "template"))]
    rest = [n for n in names if n not in likely]
    for name in likely + rest[:12]:
        got = read_page(name, s=s)
        if got.get("ok") and _is_a_header_fragment(got.get("html") or ""):
            return name
    return ""


# ---------------------------------------------------------------------------
# HIS THEME, READ OFF ONE OF HIS OWN PAGES
# ---------------------------------------------------------------------------
#
# "this link that program post it is plain no any layout no any design no any
#  setting ... the website pages are .php not .html that home.html page is not
#  live the home.php page is live"
#
# A new page used to look for a header file by guessing its name. His site did
# not match any guess, so the page went up as bare HTML: no menu, no styles, no
# footer. Guessing was the wrong question. His own pages already know how to
# put his header and footer round their content — so read one of them, keep
# everything above its content and everything below, and put the new content
# in between. Whatever the site is built from (include lines, inline markup, a
# PHP variable for the title) comes along as written, because it IS his page.
#
# Only a page his site actually links to, and only a .php one. home.html and
# default.php are on his server and not on his site; copying their frame would
# copy a page nobody visits.

_THEME_SKIP = ("default", "test", "old", "backup", "copy", "blog-", "agency", "404",
               "thank", "admin", "login")
_THEME_LIKE = ("glass", "alumin", "door", "pergola", "partition", "shower", "window",
               "railing", "facade", "cladding", "service", "work")
_THEME_THEN = ("about", "contact", "services", "gallery", "projects")
_INCLUDE_BLOCK = re.compile(r"<\?php(?:(?!\?>).)*?\b(?:include|require)(?:_once)?\b(?:(?!\?>).)*\?>",
                            re.S | re.I)


def _frame_of(src) -> dict:
    """
    {top, bottom} of one of his pages — everything above its content and
    everything below — or {} when the page does not show where they are.
    """
    src = src or ""
    if len(src) < 200:
        return {}
    incs = [(m.start(), m.end(), m.group(0)) for m in _INCLUDE_BLOCK.finditer(src)]
    first = re.search(r"<(?:main|section|article|h1)\b", src, re.I)
    last = None
    for m in re.finditer(r"</(?:main|section|article)\s*>", src, re.I):
        last = m
    head_end = re.search(r"</header\s*>", src, re.I)
    feet = [m.start() for m in re.finditer(r"<footer\b", src, re.I)]

    if first:
        tops = [e for s_, e, _t in incs if s_ < first.start()]
        if head_end and head_end.end() <= first.start():
            tops.append(head_end.end())
        top_end = max(tops) if tops else first.start()
    else:
        tops = [e for s_, e, t in incs if re.search(r"head|nav|menu|top", t, re.I)]
        if head_end:
            tops.append(head_end.end())
        top_end = max(tops) if tops else None
    if last:
        bottoms = [s_ for s_, _e, _t in incs if s_ >= last.end()]
        bottoms += [f for f in feet if f >= last.end()]
        bottom_start = min(bottoms) if bottoms else last.end()
    else:
        bottoms = [s_ for s_, _e, t in incs if re.search(r"foot|bottom", t, re.I)]
        bottoms += feet[-1:]
        bottom_start = min(bottoms) if bottoms else None
    if top_end is None or bottom_start is None or top_end > bottom_start:
        return {}
    top, bottom = src[:top_end], src[bottom_start:]
    # The frame must actually bring his look with it: a <head> or an include
    # above, and a footer, an include or the end of the document below.
    if not (re.search(r"<head\b", top, re.I) or _INCLUDE_BLOCK.search(top)):
        return {}
    if not (re.search(r"<footer\b|</html", bottom, re.I) or _INCLUDE_BLOCK.search(bottom)):
        return {}
    return {"top": top, "bottom": bottom}


def _theme_candidates(names) -> list:
    """His .php pages, best frame first: a service page, then an inner page, then home."""
    ok = []
    for n in names:
        low = str(n or "").lower().lstrip("/")
        if not low.endswith(".php") or "/" in low or any(w in low for w in _THEME_SKIP):
            continue
        if not_ours(low):
            continue
        ok.append(low)
    like = [n for n in ok if any(w in n for w in _THEME_LIKE)]
    then = [n for n in ok if n not in like and any(n.startswith(w) for w in _THEME_THEN)]
    home = [n for n in ("index.php", "home.php") if n in ok]
    return list(dict.fromkeys(like + then + home))


def theme_of_his_site(s=None, most=3) -> dict:
    """
    {ok, page, top, bottom, php} — the frame of one of his own pages, to put a
    new page's content inside. {ok: False, why} when none could be read.
    Reads only; a handful of requests at most.
    """
    s = s or settings()
    why = why_cannot_read(s)
    if why:
        return {"ok": False, "why": why}
    names = live_pages(s)
    if not names:
        p = pages(s=s)
        if not p.get("ok"):
            return {"ok": False, "why": p.get("error") or "could not list your pages",
                    "unreachable": p.get("unreachable")}
        names = [x.get("page") if isinstance(x, dict) else x for x in p.get("pages") or []]
    tried = []
    for name in _theme_candidates(names)[:most]:
        got = read_page(name, s=s)
        if got.get("unreachable"):
            return {"ok": False, "why": got.get("error") or "your site stopped answering",
                    "unreachable": True}
        tried.append(name)
        frame = _frame_of(got.get("html") or "") if got.get("ok") else {}
        if frame:
            return {"ok": True, "page": name, "php": True, **frame}
    return {"ok": False, "why": ("none of your pages showed where its content starts "
                                 "and ends" + (f" (read {', '.join(tried)})" if tried else
                                               " — no .php page your site links to was found"))}


# ---------------------------------------------------------------------------
# HIS BLOG — the connector's blog section, and nothing else (blog_backend.py
# decides what goes in each column; this only carries it)
# ---------------------------------------------------------------------------

def blog_schema(slug="", s=None) -> dict:
    """His posts table as the connector sees it; with a slug, is a post there."""
    return call("/blog" + ("/" + urllib.parse.quote(slug) if slug else ""), s=s, timeout=40)


def _log_blog(action, slug, agent, r, summary, undo_cmd=""):
    if not r.get("ok") and r.get("unreachable"):
        return r
    try:
        from coordination import changelog
        changelog.record(action, page=f"blog: {slug}", agent=agent,
                         summary=summary if r.get("ok") else "",
                         undo_cmd=undo_cmd if r.get("ok") else "", ok=bool(r.get("ok")),
                         error="" if r.get("ok") else str(r.get("error") or "")[:300])
    except Exception:
        pass
    return r


def add_blog_post(post, image=None, agent=AGENT, s=None, title="", undo_cmd="") -> dict:
    """One row into his blog's own posts table. The Releaser's job, and nobody else's."""
    refused = _may_write(agent)
    if refused:
        return {"ok": False, "error": refused}
    slug = next((str(v) for k, v in (post or {}).items() if "slug" in k.lower()), "")
    r = call("/blog", "POST", {"post": post, "image": image}, s=s, timeout=90)
    if r.get("taken"):
        return r                          # not a change, and not a failure to log
    return _log_blog("blog_post", slug, agent, r,
                     f"added the post “{title or slug}” to your blog", undo_cmd=undo_cmd)


def remove_page(page, go_to="", agent=AGENT, s=None) -> dict:
    """
    Take a page off his website — one the agency put up as a post of its own.
    The connector keeps a copy first (/rollback restores it). An older connector
    that cannot remove files gets the page replaced by a pointer to his blog,
    kept out of Google, and says so.
    """
    why = not_ours(page)
    if why:
        return {"ok": False, "error": why}
    refused = _may_write(agent)
    if refused:
        return {"ok": False, "error": refused}
    r = call("/pages-remove", "POST", {"page": page}, s=s)
    if r.get("old_connector"):
        from . import content_write
        was = read_page(page, s=s)
        r = put_page(page, content_write.take_down_page(page, go_to or "/"), agent=agent, s=s,
                     _action="take_down",
                     before=(was.get("html") if was.get("ok") else None),
                     summary=f"replaced {page} with a pointer to your blog")
        if r.get("ok"):
            r["stubbed"] = True
            r["head"] = (f"{page} now sends visitors to your blog and tells Google to drop it. "
                         "Your connector file is the older one, which cannot remove files — "
                         "upload the new agency-api.php and it removes pages outright.")
        return r
    if r.get("ok"):
        r["head"] = (f"{page} is off your website" if r.get("action") != "already gone"
                     else f"{page} was already gone from your website")
        try:
            from coordination import changelog
            changelog.forget_page(page)
        except Exception:
            pass
    return _log_write("remove_page", page, agent, r,
                      summary=f"took {page} off your website (a copy is kept)",
                      undo_cmd=f"python -m pipelines.site_api --undo {page}")


def remove_blog_post(post_id, slug="", agent=AGENT, s=None, title="") -> dict:
    """Take away a post the agency added. The connector refuses any other."""
    refused = _may_write(agent)
    if refused:
        return {"ok": False, "error": refused}
    r = call("/blog-remove", "POST", {"id": str(post_id)}, s=s, timeout=40)
    return _log_blog("blog_remove", slug or r.get("slug") or str(post_id), agent, r,
                     f"took the post “{title or slug}” off your blog")


def _has_own_head(page, s=None) -> bool:
    """Can this page take a block directly, without an include?"""
    got = read_page(page, s=s)
    return got.get("ok") and "</head" in (got.get("html") or "").lower()


def _is_a_header_fragment(html: str) -> bool:
    """
    Is this the TOP HALF of a page, or a whole page that happens to have a head?

    A header include opens the document and stops: it has </head> and never
    closes </html>, because the page that includes it does that. A complete
    page has both.

    THIS RULE EXISTS BECAUSE THE PLAN OFFERED TO EDIT default.php. Left over
    from his host, titled "Default page", 17KB, not linked from anywhere — and
    a complete HTML document, so "contains </head>" was true and it was picked
    as his header. Home.html would have qualified on the same test. Neither is
    an include; both are pages nobody visits.

    Getting this wrong is not a near miss. It writes markup into a file that
    is not part of the site, and leaves every real page without it.
    """
    low = (html or "").lower()
    if "</head" not in low:
        return False
    return "</html" not in low and "</body" not in low


def apply_schema(agent=AGENT, s=None, dry_run=False) -> dict:
    """Put the markup on the site. The Releaser's job, and nobody else's."""
    plan = schema_plan(s=s)
    if not plan["ok"]:
        return {"ok": False, "error": plan["problems"][0],
                "problems": plan["problems"]}
    if dry_run:
        return {"ok": True, "dry_run": True, **plan}
    lb = _draft("1-localbusiness-every-page.html")
    faq = _draft("2-faq-schema-faq-page.html")
    done, failed = [], []
    for job in plan["jobs"]:
        html = faq if job["id"] == FAQ_ID else lb
        r = put_head_block(job["page"], job["id"], html, agent=agent, s=s)
        if r.get("ok"):
            done.append({"page": job["page"], "what": job["what"],
                         "action": r.get("action"), "backup": r.get("backup")})
        else:
            failed.append({"page": job["page"], "why": r.get("error"),
                           "queued": r.get("queued")})
    changed = [x for x in done if x["action"] != "unchanged"]
    return {"ok": not failed, "applied": done, "failed": failed,
            "head": (f"{len(changed)} page(s) changed, "
                     f"{len(done) - len(changed)} already correct"
                     + (f", {len(failed)} refused" if failed else ""))}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _say(r):
    if r.get("ok"):
        print("  ok  " + str(r.get("head") or r.get("note") or "done"))
    else:
        print("  --  " + str(r.get("error") or "failed"))
    return 0 if r.get("ok") else 1


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Talk to the owner's website.")
    ap.add_argument("--health", action="store_true")
    ap.add_argument("--pages", action="store_true")
    ap.add_argument("--read", metavar="PAGE")
    ap.add_argument("--plan-schema", action="store_true",
                    help="what would change, changing nothing")
    ap.add_argument("--apply-schema", action="store_true",
                    help="put the markup on the site (Releaser)")
    ap.add_argument("--undo-schema", action="store_true",
                    help="take the agency's markup back off every page")
    ap.add_argument("--find-placeholders", action="store_true",
                    help="where an unfilled YOUR-something is still live")
    ap.add_argument("--fix-address", action="store_true",
                    help="put your saved address into the block on the site")
    ap.add_argument("--plan-canonical", action="store_true",
                    help="which pages name the wrong domain, changing nothing")
    ap.add_argument("--fix-canonical", action="store_true",
                    help="point every canonical tag at this site (Releaser)")
    ap.add_argument("--backups", action="store_true")
    ap.add_argument("--undo", metavar="PAGE", help="restore a page's last version")
    # The undo for one block the agency put in, as opposed to --undo, which
    # restores whatever the newest backup happens to be. The Fixer prints this
    # command alongside every block it proposes, so it has to exist: an undo
    # line that names a flag nobody implemented is worse than no undo line,
    # because he only finds out at the moment he needs it.
    ap.add_argument("--remove-block", metavar="ID", dest="remove_block",
                    help="take one block the agency added back out of a page")
    ap.add_argument("--page", default="", help="which page --remove-block acts on")
    ap.add_argument("--set-address", metavar="STREET",
                    help="save your workshop address, once, so every rebuild "
                         "of the markup carries it")
    ap.add_argument("--city", default="", help="the emirate, e.g. Dubai")
    ap.add_argument("--test-enquiry", action="store_true")
    ap.add_argument("--find", metavar="TEXT",
                    help="every file on the website holding this exact text, and the line")
    ap.add_argument("--as", dest="agent", default=AGENT)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.find:
        r = plan_replace(a.find, "")
        print(json.dumps(r) if a.json else (r.get("head") or r.get("error")))
        return 0 if r.get("ok") else 1

    # Before the mode banner: saving an address needs no site access at all,
    # and refusing it because full access is not configured would be silly.
    if a.set_address:
        r = save_address(a.set_address, a.city)
        return _say(r)

    s = settings()
    why = why_not_ready(s)
    print(f"Website : {s['site'] or '(not set)'}")
    print(f"Mode    : {s['mode']}"
          + ("  (agents may change the site)" if s["mode"] == "full"
             else "  (agents may only look)"))
    print(f"API     : {s['base'] or '(not set)'}")
    print(f"Token   : {'set' if s['token'] else 'NOT SET'}")
    if why:
        print("\n  --  " + why)
        return 1
    print()

    if a.health:
        r = health(s)
        if a.json:
            print(json.dumps(r, indent=2))
            return 0 if r.get("ok") else 1
        if r.get("ok"):
            print(f"  ok  your site answered. PHP {r.get('php')}, "
                  f"folder {'writable' if r.get('writable') else 'NOT writable'}")
            for w in r.get("warnings") or []:
                print("\n  !!  " + w)
            return 0
        return _say(r)
    if a.pages:
        r = pages(s)
        if not r.get("ok"):
            return _say(r)
        for p in r["pages"]:
            print(f"   {p['page']:22} {p['bytes']:>8} bytes   {p['changed']}")
        # Say what was left out, so a page he expects and cannot see has a
        # visible reason rather than being quietly absent.
        if r.get("not_ours"):
            print(f"\n   walked past {len(r['not_ours'])} file(s) that are not "
                  "the website — your admin panel, your config, and the like:")
            for name in r["not_ours"][:12]:
                print(f"     {name}")
            if len(r["not_ours"]) > 12:
                print(f"     ...and {len(r['not_ours']) - 12} more")
            print("\n   These are still listed by your server, which means it "
                  "is running the\n   old agency-api.php. See "
                  "paste-into-your-site/RE-UPLOAD-agency-api.md.")
        return 0
    if a.read:
        r = read_page(a.read, s)
        if not r.get("ok"):
            return _say(r)
        print(r["html"][:4000])
        return 0
    if a.plan_schema:
        r = schema_plan(s)
        if a.json:
            print(json.dumps(r, indent=2))
            return 0 if r.get("ok") else 1
        for p in r["problems"]:
            print("  --  " + p)
        for j in r["jobs"]:
            print(f"   would put {j['what']:14} into {j['page']}")
        if r["ok"]:
            print(f"\n  ok  {r['head']}. Nothing changed — add --apply-schema.")
        return 0 if r["ok"] else 1
    if a.apply_schema:
        r = apply_schema(agent=a.agent, s=s)
        if a.json:
            print(json.dumps(r, indent=2))
            return 0 if r.get("ok") else 1
        for x in r.get("applied") or []:
            print(f"   {x['action']:10} {x['what']:14} on {x['page']}")
        for x in r.get("failed") or []:
            print(f"   REFUSED    {x['page']}: {x['why']}")
        return _say(r)
    if a.find_placeholders:
        r = find_placeholders(s)
        if a.json:
            print(json.dumps(r, indent=2))
            return 0
        for f in r["found"]:
            print(f"   {f['page']:28} {', '.join(f['placeholders'])}")
        return _say(r)
    if a.fix_address:
        r = fix_address(agent=a.agent, s=s)
        if a.json:
            print(json.dumps(r, indent=2))
            return 0 if r.get("ok") else 1
        for c in r.get("changed") or []:
            print(f"   fixed {c['page']}")
        for l in r.get("still_unfilled") or []:
            print(f"   STILL BLANK in {l['page']}: {', '.join(l['placeholders'])}")
        for f in r.get("failed") or []:
            print(f"   REFUSED  {f['page']}: {f['why']}")
        return _say(r)
    if a.plan_canonical:
        r = canonical_plan(s)
        if a.json:
            print(json.dumps(r, indent=2))
            return 0 if r.get("ok") else 1
        for j in r.get("jobs") or []:
            print(f"   {j['page']:22} says {j['wrong_host']}")
        return _say(r)
    if a.fix_canonical:
        r = fix_canonical(agent=a.agent, s=s)
        if a.json:
            print(json.dumps(r, indent=2))
            return 0 if r.get("ok") else 1
        for c in r.get("changed") or []:
            print(f"   fixed {c['fixed']} tag(s) in {c['page']}")
        for f in r.get("failed") or []:
            print(f"   REFUSED  {f['page']}: {f['why']}")
        return _say(r)
    if a.undo_schema:
        r = undo_schema(agent=a.agent, s=s)
        if a.json:
            print(json.dumps(r, indent=2))
            return 0 if r.get("ok") else 1
        for x in r.get("cleaned") or []:
            print(f"   removed  {x['id']:16} from {x['page']}")
        for x in r.get("failed") or []:
            print(f"   REFUSED  {x['page']}: {x['why']}")
        return _say(r)
    if a.backups:
        r = backups(s)
        if not r.get("ok"):
            return _say(r)
        if not r["backups"]:
            print("   (none yet — nothing has been changed)")
        for b in r["backups"][:30]:
            print(f"   {b['backup']:52} {b['made']}")
        return 0
    if a.undo:
        return _say(undo(page=a.undo, s=s))
    if a.remove_block:
        if not a.page:
            print("  --  which page? Add --page <file>, e.g. "
                  "--remove-block viewport --page includes/header.php")
            return 1
        return _say(remove_head_block(a.page, a.remove_block, agent=a.agent, s=s))
    if a.test_enquiry:
        return _say(test_enquiry_path(s))

    ap.print_help()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
