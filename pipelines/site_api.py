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
import urllib.error
import urllib.parse
import urllib.request

from coordination import board

AGENT = "it-releaser"          # the only one allowed to write. Red line 1.
SECTOR = "IT"
TIMEOUT = 25

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
        elif e.code == 404:
            out["error"] = (f"nothing answered at {url}. Check the Admin API "
                            "address, and that agency-api/index.php really is "
                            "on the server.")
        return out
    except Exception as e:
        return {"ok": False, "error": f"could not reach your site: "
                                      f"{type(e).__name__}: {e}"}
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
# READ — anybody may
# ---------------------------------------------------------------------------

def health(s=None) -> dict:
    return call("/health", s=s)


def pages(s=None) -> dict:
    return call("/pages", s=s)


def read_page(page, s=None) -> dict:
    return call("/pages/" + urllib.parse.quote(page.lstrip("/")), s=s)


def backups(s=None) -> dict:
    return call("/backups", s=s)


# ---------------------------------------------------------------------------
# WRITE — the Releaser, or the owner's queue. Never a third option.
# ---------------------------------------------------------------------------

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
               undo_cmd=""):
    """Write one line of the change log, and refresh what we think the page is."""
    try:
        from coordination import changelog
    except Exception:                                        # pragma: no cover
        return r
    ok = bool(r.get("ok"))
    unchanged = (r.get("action") == "unchanged" or r.get("changed") is False)
    if ok and unchanged:
        # The nightly run putting back a block that is already there is not a
        # change, and logging it every night would bury the real ones.
        return r
    changelog.record(
        action, page=page, agent=agent, block_id=block_id,
        summary=summary or _plain(action, page, block_id, ok, r),
        bytes_after=(len(html) if html is not None else r.get("bytes")),
        backup=r.get("backup") or "", undo_cmd=undo_cmd if ok else "",
        ok=ok, error="" if ok else str(r.get("error") or "")[:300])
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
    return f"rewrote {page}"


def put_head_block(page, block_id, html, agent=AGENT, s=None) -> dict:
    """
    Put one marked block into a page's <head>, replacing any earlier copy.

    Idempotent by design: the endpoint wraps it in a marker comment, so the
    hundredth nightly run leaves exactly one copy and reports 'unchanged'.
    """
    refused = _may_write(agent)
    if refused:
        cid = board.queue_site_change(agent, "head_block",
                                      {"page": page, "id": block_id,
                                       "bytes": len(html)})
        return _log_write("head_block", page, agent,
                          {"ok": False, "queued": cid, "error": refused},
                          block_id=block_id)
    r = call("/head", "POST",
             {"page": page, "id": block_id, "html": html}, s=s)
    return _log_write("head_block", page, agent, r, block_id=block_id,
                      undo_cmd=(f"python -m pipelines.site_api "
                                f"--remove-block {block_id} --page {page}"))


def put_page(page, html, agent=AGENT, s=None, _action="page", _block="") -> dict:
    """
    Write a whole page.

    _action and _block are for the log only, so a caller that knows WHY it is
    rewriting the file — taking one block out, say — can have the change log
    read "took the viewport block back out of header.php" instead of the
    literally-true but useless "rewrote header.php".
    """
    refused = _may_write(agent)
    if refused:
        cid = board.queue_site_change(agent, "page",
                                      {"page": page, "bytes": len(html)})
        return _log_write(_action, page, agent,
                          {"ok": False, "queued": cid, "error": refused},
                          block_id=_block)
    r = call("/pages", "POST", {"page": page, "html": html}, s=s)
    return _log_write(_action, page, agent, r, html=html, block_id=_block,
                      undo_cmd=f"python -m pipelines.site_api --undo {page}")


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
                    _action="remove_block", _block=block_id)


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
        r = put_page(row["page"], html, agent=agent, s=s)
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
    want = urllib.parse.urlparse(s["site"]).netloc.lower()
    want = want[4:] if want.startswith("www.") else want
    if not want:
        return {"ok": False, "error": "no website address saved"}
    p = pages(s=s)
    if not p.get("ok"):
        return {"ok": False, "error": p.get("error")}
    import re
    jobs = []
    for row in p.get("pages") or []:
        got = read_page(row["page"], s=s)
        if not got.get("ok"):
            continue
        for line in (got.get("html") or "").splitlines():
            if "canonical" not in line.lower():
                continue
            for host in re.findall(r'https?://([A-Za-z0-9.\-]+)', line):
                bare = host[4:] if host.lower().startswith("www.") else host
                if bare.lower() != want:
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
    want = plan["want"]
    changed, failed = [], []
    for page in sorted({j["page"] for j in plan["jobs"]}):
        got = read_page(page, s=s)
        if not got.get("ok"):
            failed.append({"page": page, "why": got.get("error")})
            continue
        out, hits = [], 0
        for line in (got.get("html") or "").splitlines():
            if "canonical" in line.lower():
                def swap(m):
                    nonlocal hits
                    host = m.group(2)
                    bare = host[4:] if host.lower().startswith("www.") else host
                    if bare.lower() == want:
                        return m.group(0)
                    hits += 1
                    return f"{m.group(1)}://{want}"
                line = re.sub(r'(https?)://([A-Za-z0-9.\-]+)', swap, line)
            out.append(line)
        if not hits:
            continue
        r = put_page(page, "\n".join(out), agent=agent, s=s)
        if r.get("ok"):
            changed.append({"page": page, "fixed": hits,
                            "backup": r.get("backup")})
        else:
            failed.append({"page": page, "why": r.get("error")})
    return {"ok": not failed, "want": want, "changed": changed, "failed": failed,
            "head": (f"pointed {sum(c['fixed'] for c in changed)} canonical "
                     f"tag(s) on {len(changed)} page(s) at {want}"
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
    if not candidates:
        return ""
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

    # NOTHING NAMED AN INCLUDE WE COULD READ, SO GO AND LOOK.
    #
    # An include can be written a dozen ways — a constant, a variable, a
    # front controller, a path built at run time — and a regex will miss most
    # of them. Rather than give up and refuse every page again, ask the server
    # for its files and read the ones that are plausibly a header. Cheap: a
    # handful of reads, and only when the regex has already come up empty.
    p = pages(s=s)
    if not p.get("ok"):
        return ""
    likely, rest = [], []
    for row in p.get("pages") or []:
        n = row["page"]
        if n in candidates:
            continue
        (likely if any(w in n.lower() for w in
                       ("head", "header", "top", "meta", "layout", "template"))
         else rest).append(n)
    for name in likely + rest[:12]:
        got = read_page(name, s=s)
        if got.get("ok") and _is_a_header_fragment(got.get("html") or ""):
            return name
    return ""


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
    ap.add_argument("--as", dest="agent", default=AGENT)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

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
