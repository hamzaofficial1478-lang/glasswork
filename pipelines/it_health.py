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

from coordination import board, dispatch, inbox, site as _site

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


def _country_word() -> str:
    """'UAE' for the owner's website; the open website's country otherwise."""
    from coordination import countries, profile
    return "UAE" if profile.country("AE") == "AE" else countries.name(profile.country())


def looks_like_local_number(tel) -> bool:
    """
    A real number in the open website's country — the UAE rules above for the
    owner's website, the country's own numbering rules anywhere else.
    """
    from coordination import countries, profile
    cc = profile.country("AE")
    if cc == "AE":
        return looks_like_uae_number(tel)
    e = countries.normalise_phone(tel, cc)
    return bool(e) and e.startswith("+" + countries.calling_code(cc))


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


def check_enquiry_path(html, more_html=""):
    """
    Can a visitor actually reach this business? Findings here are 'critical'
    when nothing at all works, because that is a site quietly losing every job.

    `more_html` is the CONTACT PAGE, and leaving it out was a bug that produced
    a flatly wrong finding about the owner's own site. This read the home page
    and nothing else, so it reported "Missing a contact form" for a business
    whose contact form is at /contact.php — one click away, on the page called
    Contact, which is where a contact form is supposed to be. A route is only
    missing if it is missing everywhere a visitor would sensibly look.
    """
    blob = (html or "") + "\n" + (more_html or "")
    found, missing = [], []
    for key, rx, why in ENQUIRY_CHECKS:
        (found if re.search(rx, blob, re.I) else missing).append((key, why))

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
    tel = set(re.findall(r'href=["\']tel:([^"\']+)', blob, re.I))
    for t in sorted(tel):
        if not looks_like_local_number(t):
            out.append({"kind": "form_dead", "severity": "high",
                        "detail": f"click-to-call number does not look like a {_country_word()} "
                                  f"number: {t}"})
    return out, found


def _contact_page(site, home_html, timeout=15):
    """
    Fetch the site's own contact page, if it has one. Returns (html, url).

    Follows the link the site itself offers rather than guessing at /contact —
    the owner's own site calls it /contact.php, which no guess list contained.
    The link-finding is pipelines/harvest.py's, so there is one idea in the
    building about "which page is the contact page" rather than two.

    Best-effort by design: no contact page, or an unreachable one, simply means
    the home page is all we judge on. It must never fail the health check.
    """
    try:
        from . import harvest
        ranked = sorted(((harvest.worth_a_click(u, t), u)
                         for u, t in harvest.links(home_html, site)), reverse=True)
        for score, u in ranked[:2]:
            if score <= 0:
                break
            _s, h, _ms, _e = _fetch(u, timeout)
            if h:
                return h, u
    except Exception:
        pass
    return "", ""


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
                    "detail": f"Home page took {ms}ms. On a phone on mobile data that is a bounce."})
    elif ms >= SLOW_MS:
        out.append({"kind": "speed", "severity": "medium",
                    "detail": f"Home page took {ms}ms — slower than a visitor will wait."})
    return out


# A domain or an address printed on the site whose domain has no DNS at all is
# not a cosmetic problem: every link to it is broken and every enquiry sent to
# it is lost. The Watcher can settle that with a lookup, so it should.
_HOSTS = re.compile(r"https?://([A-Za-z0-9.\-]+)")
_MAILS = re.compile(r"mailto:([A-Za-z0-9._%+\-]+@([A-Za-z0-9.\-]+\.[A-Za-z]{2,}))")


def _resolves(host) -> bool:
    import socket
    try:
        socket.gethostbyname(host)
        return True
    except Exception:
        return False


def check_dead_addresses(html, site):
    """
    Links and email addresses on the page whose domain does not exist.

    HE ASKED FOR THIS TWICE AND NOTHING HAPPENED. The .ae domain printed all
    over his site has no DNS record — the links go nowhere and the address on
    it cannot receive mail — and nothing in the agency was looking, so nothing
    ever filed it, so the Fixer never had a ticket to work.

    One DNS lookup per distinct domain, and only for domains that are close to
    his own: checking every outbound link on the page would be slow and would
    file tickets about somebody else's website.
    """
    out = []
    own = _bare_host(site)
    if not own:
        return out
    stem = own.split(".")[0]
    seen = set()
    for host in set(_HOSTS.findall(html or "")):
        bare = _bare_host("https://" + host)
        if not bare or bare == own or bare in seen:
            continue
        # Only ones that look like his: same first label, different ending.
        if not bare.startswith(stem):
            continue
        seen.add(bare)
        if not _resolves(bare):
            out.append({"kind": "bug", "severity": "high",
                        "detail": f"The site links to {bare}, and that domain "
                                  f"has no DNS record at all — every one of "
                                  f"those links is broken. Your site is on "
                                  f"{own}."})
    for addr, dom in set(_MAILS.findall(html or "")):
        bare = _bare_host("https://" + dom)
        if not bare or bare in seen or not bare.startswith(stem):
            continue
        seen.add(bare)
        if not _resolves(bare):
            out.append({"kind": "form_dead", "severity": "critical",
                        "detail": f"The email address on the site is {addr}, and "
                                  f"{bare} has no DNS record — mail sent to it "
                                  f"cannot be delivered. Every enquiry anyone "
                                  f"sends to that address is lost."})
    return out


def _bare_host(url):
    m = _HOSTS.match((url or "").strip())
    if not m:
        return ""
    h = m.group(1).lower()
    return h[4:] if h.startswith("www.") else h


def check_hygiene(html):
    """Only the basics that affect whether the page works at all on a phone."""
    out = []
    if not re.search(r'<meta[^>]+name=["\']viewport["\']', html, re.I):
        out.append({"kind": "bug", "severity": "high",
                    "detail": "No viewport meta tag — the site will render desktop-sized "
                              "on a phone, where most enquiries start."})
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
    """
    Every finding becomes a ticket. Nothing is fixed here.

    Returns the ticket ids, not just a count. Report has to be able to ask "were
    the urgent issues THIS check filed ever resolved?", and it can only do that
    if the check says which tickets were its own.
    """
    if not findings:
        return []
    # One ticket per problem. The same broken address found on every run is
    # the SAME ticket, refreshed — see board.file_site_issue. The id comes back
    # either way, so Report can still ask whether this check's issues cleared.
    try:
        board.merge_duplicate_issues()      # the copies filed before this existed
    except Exception:
        pass
    ids = []
    with sqlite3.connect(board.DB) as c:
        for f in findings:
            iid, _new = board.file_site_issue(f["kind"], f["severity"], f["detail"],
                                              url, agent, con=c)
            ids.append(iid)
    return ids


def run(agent="it-watcher", url=None, timeout=20):
    """Wrapped so the run is on the record even when it refuses to report."""
    with board.agent_run(agent) as run_row:
        # IT READS ITS POST BEFORE IT DOES ANYTHING ELSE.
        #
        # Nothing in this sector ever opened the inbox. SEO's schema-and-markup
        # fixes and the Fixer's own "I have no patch for this" tickets were
        # written every day into a room with no door. This is IT's one reader,
        # and it runs first in IT's lane so anything it files as work is
        # claimable by it_fix, it_selfheal and it_release later the same day.
        #
        # No handler: IT answers by DOING, not by replying. Every request
        # becomes a task on IT's board and the asker is told the task id.
        post = inbox.read_post("IT", agent)
        out = _check(agent, url, timeout, run_row)
        out["post"] = post
        note = out.get("summary") or out.get("error") or "checked the site"
        if post.get("read"):
            note = post["head"] + "; " + note
        run_row.note(note)
        return out


def _check(agent, url, timeout, run_row):
    site = _site.site_url(url)
    con = sqlite3.connect(board.DB)
    con.row_factory = sqlite3.Row
    try:
        task = board.next_open_task("IT", agent)
        if not task:
            # Not just "nothing to do". Since the board started routing work to
            # the desk that can do it, an empty-handed Watcher usually means the
            # opposite — there IS work, and it is somebody else's. Say which.
            return dispatch.nothing_here("IT", agent)
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

        # the enquiry path first, exactly as the MD ordered it — and on the
        # contact page too, because that is where a contact form lives
        contact_html, contact_url = _contact_page(site, html, timeout)
        enquiry, found = (check_enquiry_path(html, contact_html) if html
                          else ([], []))
        findings = enquiry + check_health(status, ms, site, err)
        if html:
            findings += check_hygiene(html)

        findings += check_dead_addresses(html, site)

        ok, reasons = critic_check(findings, fetched, found)
        if not ok:
            board.finish_task(task["id"], agent,
                              "Critic rejected: " + "; ".join(reasons), status="blocked")
            return {"ok": True, "claimed_task": task["id"], "critic": "rejected",
                    "critic_reasons": reasons, "issues_filed": 0}

        issue_ids = file_issues(findings, site, agent)
        filed = len(issue_ids)

        # THE WATCHER NO LONGER QUEUES ANYTHING, AND THAT IS THE FIX.
        #
        # It used to put every critical and high finding straight into the
        # owner's approval queue, with a note saying it "needs the Fixer in a
        # branch, then the Releaser". That was honest at the time and it was a
        # workaround: there was an it-fixer desk in the routing table and no
        # pipeline that ever ran under that name, so the Watcher was standing
        # in for a desk that did not exist.
        #
        # pipelines/it_fix.py is that desk now. It reads these tickets, works
        # out the actual change, and puts THAT to the owner — a decision he can
        # answer rather than a problem he has to diagnose. Leaving the line
        # below in place meant he got both: seven items in the queue for four
        # problems, half of them saying nothing more than "something is wrong".
        #
        # So the split is the one the handbook describes and nothing more: the
        # Watcher finds and files, the Fixer decides and queues, the Releaser
        # is the only one who acts. A finding with no ticket behind it is the
        # thing to worry about, and file_issues above is what guarantees there
        # is always one.
        queued = []

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
            confidence=0.8,
            # name the exact tickets, so Report checks THESE and cannot mistake a
            # missed lookup for "nothing was wrong"
            evidence={"site_issue_ids": issue_ids})

        return {"ok": True, "agent": agent, "claimed_task": task["id"], "critic": "passed",
                "url": site, "http_status": status, "response_ms": ms,
                "enquiry_routes_found": [k for k, _ in found],
                "issues_filed": filed, "worst_severity": worst,
                "changes_queued_for_owner": len(queued),
                "for_the_fixer": sum(1 for f in findings
                                     if f["severity"] in ("critical", "high")),
                "changes_applied": 0, "decision_id": did,
                "summary": (f"checked {site} — HTTP {status} in {ms}ms, "
                            f"{filed} ticket(s) filed for the Fixer"),
                "findings": findings}
    finally:
        con.close()


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
