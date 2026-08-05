"""
it_health.py — the IT Watcher. Looks at the site and files what it finds.
It fixes nothing, and it cannot touch the site even if it wanted to.

The task sitting on the board says it plainly:

    "Read-only check the enquiry path FIRST — does the contact form submit, is
     WhatsApp/click-to-call present and correct, is the phone number right, is
     it fast on mobile — then general hygiene. File findings to site_issues.
     Any fix is queued via queue_site_change, never applied to the site."

THE ENQUIRY PATH COMES FIRST, AND IT IS NOT A TIE

Rankings, copy and speed are all worth something. A dead enquiry path is worth
negative money: the site keeps attracting people and quietly drops every one of
them. So a broken WhatsApp link outranks a slow page every time, and the
severity ladder here reflects that rather than treating all findings as equal.

WHAT IT WILL NOT DO

It issues GET requests and reads HTML. It does not submit the contact form —
that would put a fake enquiry in the owner's inbox, and an agent that cries
wolf daily is one whose alerts get ignored. Proving the form *delivers* needs a
real submission, so it is a separate, explicitly-invoked check (--submit) that
posts one clearly-labelled test enquiry, never on the automatic path.

Every fix it can imagine goes to queue_site_change for the owner. The Watcher
finds; the Fixer patches in a branch; only the Releaser goes near production.
That split is the whole reason a finder can never silently ship.

    python -m pipelines.it_health        (python3 on macOS/Linux)
"""
import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request

from coordination import board

UA = "Mozilla/5.0 (compatible; glasswork-it-watcher/1.0; read-only health check)"

# How slow is too slow. UAE mobile on 4G is the realistic case, and someone
# comparing three glaziers does not wait around.
SLOW_MS = 2500
VERY_SLOW_MS = 5000

# The enquiry path, in the order it matters for this business.
ENQUIRY_CHECKS = [
    ("whatsapp", r'(wa\.me/|api\.whatsapp\.com|whatsapp://)',
     "WhatsApp link — the main enquiry route for this business"),
    ("phone", r'href=["\']tel:',
     "click-to-call — half of enquiries come from a phone tap"),
    ("form", r'<form[^>]*>|type=["\']email["\']|name=["\']email["\']',
     "a contact form"),
    ("email", r'href=["\']mailto:',
     "a mailto address"),
]


def looks_like_uae_number(tel: str) -> bool:
    """
    Is this actually a UAE number?

    Written as an anchored check on the normalised digits, not a loose search.
    A pattern that merely LOOKS for a UAE-ish run anywhere in the string passes
    +44 20 7123 4567, because '...0 7 1234567' reads as a UAE landline. The
    number has to be a UAE number from its first digit, not contain one.

      mobile    +971 5X XXXXXXX   /  05X XXX XXXX
      landline  +971 X XXXXXXX    /  0X XXX XXXX   (X in 2,3,4,6,7,9)
    """
    n = re.sub(r"[^\d+]", "", tel or "")
    if n.startswith("00971"):
        n = "+" + n[2:]
    elif n.startswith("971"):
        n = "+" + n
    if n.startswith("+971"):
        return bool(re.fullmatch(r"5\d{8}|[234679]\d{7}", n[4:]))
    return bool(re.fullmatch(r"0(?:5\d{8}|[234679]\d{7})", n))


def _fetch(url, timeout=20):
    """One read-only GET. Returns (status, html, ms, error)."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read(400_000).decode("utf-8", "replace")
            return r.status, body, int((time.time() - t0) * 1000), None
    except urllib.error.HTTPError as e:
        return e.code, "", int((time.time() - t0) * 1000), f"HTTP {e.code}"
    except Exception as e:
        return 0, "", int((time.time() - t0) * 1000), str(e)


def check_enquiry_path(html):
    """
    Can a visitor actually reach this business? Findings here are 'critical'
    when nothing at all works, because that is a site quietly losing every job.
    """
    found, missing = [], []
    for key, rx, why in ENQUIRY_CHECKS:
        (found if re.search(rx, html, re.I) else missing).append((key, why))

    out = []
    if not found:
        out.append({"kind": "form_dead", "severity": "critical",
                    "detail": "NO way to make an enquiry found on the page — no WhatsApp, "
                              "no click-to-call, no form, no mailto. Every visitor is lost."})
        return out, found

    for key, why in missing:
        sev = "high" if key in ("whatsapp", "phone") else "medium"
        out.append({"kind": "form_dead", "severity": sev,
                    "detail": f"Missing {why}."})

    # a phone number that is not a UAE number is worse than none — it looks wrong
    tel = re.findall(r'href=["\']tel:([^"\']+)', html, re.I)
    for t in tel:
        if not looks_like_uae_number(t):
            out.append({"kind": "form_dead", "severity": "high",
                        "detail": f"click-to-call number does not look like a UAE number: {t}"})
    return out, found


def check_health(status, ms, url, err):
    """Reachability, TLS and speed. Blunt, because these are blunt problems."""
    out = []
    if err or status == 0:
        out.append({"kind": "bug", "severity": "critical",
                    "detail": f"Site did not respond: {err or 'no response'}"})
        return out
    if status >= 500:
        out.append({"kind": "bug", "severity": "critical",
                    "detail": f"Server error {status} on the home page."})
    elif status >= 400:
        out.append({"kind": "bug", "severity": "high",
                    "detail": f"Home page returned {status}."})
    if not url.lower().startswith("https://"):
        out.append({"kind": "ssl", "severity": "high",
                    "detail": "Site is not on HTTPS. Browsers mark it 'Not secure', "
                              "which costs enquiries before anyone reads a word."})
    if ms >= VERY_SLOW_MS:
        out.append({"kind": "speed", "severity": "high",
                    "detail": f"Home page took {ms}ms. On UAE mobile that is a bounce."})
    elif ms >= SLOW_MS:
        out.append({"kind": "speed", "severity": "medium",
                    "detail": f"Home page took {ms}ms — slower than a visitor will wait."})
    return out


def check_hygiene(html):
    """Only the basics that affect whether the page works at all on a phone."""
    out = []
    if not re.search(r'<meta[^>]+name=["\']viewport["\']', html, re.I):
        out.append({"kind": "bug", "severity": "high",
                    "detail": "No viewport meta tag — the site will render desktop-sized "
                              "on a phone, where most UAE enquiries start."})
    if not re.search(r"<title[^>]*>\s*\S", html, re.I):
        out.append({"kind": "bug", "severity": "medium",
                    "detail": "The page has no title."})
    return out


def critic_check(findings, fetched, enquiry_found):
    """
    IT's failure mode is a green tick on an unchecked site. If nothing was
    actually fetched, no report may be filed at all.
    """
    reasons = []
    if not fetched:
        reasons.append("the site was never fetched — there is nothing to report on")
    if any(f.get("severity") not in ("critical", "high", "medium", "low") for f in findings):
        reasons.append("a finding has no usable severity")
    if fetched and not findings and not enquiry_found:
        reasons.append("clean report on a page with no enquiry path — that cannot be right")
    return (not reasons, reasons)


def file_issues(findings, url, agent):
    """Every finding becomes a ticket. Nothing is fixed here."""
    if not findings:
        return 0
    now = int(time.time())
    with sqlite3.connect(board.DB) as c:
        c.executemany(
            "INSERT INTO site_issues(kind,severity,detail,url,status,found_by,found_at)"
            " VALUES(?,?,?,?, 'open', ?,?)",
            [(f["kind"], f["severity"], f["detail"], url, agent, now) for f in findings])
    return len(findings)


def run(agent="it-watcher", url=None, timeout=20):
    """Wrapped so the run is on the record even when it refuses to report."""
    with board.agent_run(agent) as run_row:
        out = _check(agent, url, timeout, run_row)
        run_row.note(out.get("summary") or out.get("error") or "checked the site")
        return out


def _check(agent, url, timeout, run_row):
    site = (url or os.environ.get("SITE_URL") or "").strip()
    con = sqlite3.connect(board.DB)
    con.row_factory = sqlite3.Row
    try:
        task = board.next_open_task("IT", agent)
        if not task:
            return {"ok": False, "error":
                    "No open IT task on the board to claim. The Watcher works from the "
                    "board, never on its own initiative."}
        run_row.task(task["id"])

        if not site:
            board.finish_task(task["id"], agent,
                              "No SITE_URL configured — cannot check a site that isn't set.",
                              status="blocked")
            return {"ok": False, "claimed_task": task["id"],
                    "error": "No SITE_URL set. Save your website on /setup first, then "
                             "run this again. Nothing was checked and nothing was filed."}

        status, html, ms, err = _fetch(site, timeout)
        fetched = bool(html) or status > 0

        # the enquiry path first, exactly as the MD ordered it
        enquiry, found = check_enquiry_path(html) if html else ([], [])
        findings = enquiry + check_health(status, ms, site, err)
        if html:
            findings += check_hygiene(html)

        ok, reasons = critic_check(findings, fetched, found)
        if not ok:
            board.finish_task(task["id"], agent,
                              "Critic rejected: " + "; ".join(reasons), status="blocked")
            return {"ok": True, "claimed_task": task["id"], "critic": "rejected",
                    "critic_reasons": reasons, "issues_filed": 0}

        filed = file_issues(findings, site, agent)

        # anything worth changing goes to the owner's queue. The Watcher does not
        # get to touch the site, and this is the only route a change may travel.
        queued = []
        for f in findings:
            if f["severity"] in ("critical", "high"):
                cid = board.queue_site_change(
                    agent, kind="fix_" + f["kind"],
                    detail=f'{f["detail"]}  (found on {site}; needs the Fixer in a branch, '
                           f'then the Releaser)')
                queued.append(cid)

        worst = next((s for s in ("critical", "high", "medium", "low")
                      if any(f["severity"] == s for f in findings)), None)
        board.finish_task(task["id"], agent, {
            "url": site, "http_status": status, "response_ms": ms,
            "enquiry_routes_found": [k for k, _ in found],
            "findings": findings, "queued_changes": len(queued)}, status="done")

        did = board.log_decision(
            "it-health",
            reasoning=(f"Read-only check of {site}: HTTP {status} in {ms}ms, "
                       f"{len(found)} enquiry route(s) present, {filed} issue(s) filed."),
            expectation="The owner sees a broken enquiry path the day it breaks, not weeks later.",
            confidence=0.8)

        return {"ok": True, "agent": agent, "claimed_task": task["id"], "critic": "passed",
                "url": site, "http_status": status, "response_ms": ms,
                "enquiry_routes_found": [k for k, _ in found],
                "issues_filed": filed, "worst_severity": worst,
                "changes_queued_for_owner": len(queued),
                "changes_applied": 0, "decision_id": did,
                "summary": (f"checked {site} — HTTP {status} in {ms}ms, "
                            f"{filed} ticket(s) filed, {len(queued)} queued for you"),
                "findings": findings}
    finally:
        con.close()


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
