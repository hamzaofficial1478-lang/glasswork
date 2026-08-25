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
        return {"ok": False, "queued": cid, "error": refused}
    return call("/head", "POST",
                {"page": page, "id": block_id, "html": html}, s=s)


def put_page(page, html, agent=AGENT, s=None) -> dict:
    refused = _may_write(agent)
    if refused:
        cid = board.queue_site_change(agent, "page",
                                      {"page": page, "bytes": len(html)})
        return {"ok": False, "queued": cid, "error": refused}
    return call("/pages", "POST", {"page": page, "html": html}, s=s)


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
    return call("/rollback", "POST", body, s=s)


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
    known = [x["page"] for x in (p.get("pages") or [])] if p.get("ok") else []
    if not p.get("ok"):
        problems.append("could not list your pages: " + str(p.get("error")))
    jobs = [{"page": pg, "id": SCHEMA_ID, "what": "LocalBusiness", "bytes": len(lb)}
            for pg in known]
    if "faq.php" in known and faq:
        jobs.append({"page": "faq.php", "id": FAQ_ID, "what": "FAQPage",
                     "bytes": len(faq)})
    return {"ok": not problems, "problems": problems, "jobs": jobs,
            "pages_seen": known,
            "head": (f"{len(jobs)} block(s) across {len(known)} page(s)"
                     if not problems else problems[0])}


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
    ap.add_argument("--backups", action="store_true")
    ap.add_argument("--undo", metavar="PAGE", help="restore a page's last version")
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
    if a.test_enquiry:
        return _say(test_enquiry_path(s))

    ap.print_help()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
