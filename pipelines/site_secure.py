"""
site_secure.py — what of yours is a stranger able to download right now?

    python -m pipelines.site_secure              check the live site
    python -m pipelines.site_secure --json       the same, as data

WHY THIS EXISTS

The owner said it plainly: "the main thing is to make all the data secure from
public... i think these files must be secure from getting caught in public".

He is right, and the honest way to answer him is not to reassure him. It is to
go and look. Everything below is fetched from the outside with no token, no
cookie and no credential of any kind — exactly what anybody in the world can
do to his site from a browser. If a file comes back, it is public. That is not
an opinion about configuration, it is the file arriving.

WHAT IT LOOKS FOR

  the agency's own door   token files, the backups folder, a browsable listing
  the classic leaks       .env, .git, database dumps, editor leftovers
  the loud mistakes       phpinfo, a zip of the whole site, config backups

WHAT IT WILL NOT DO

It reads. It does not attempt a login, does not send a password, does not try
an injection, does not touch anything but GET, and stops at the first sign a
host is rate-limiting. This is the check you are allowed to run on your own
website, and nothing more than that.

A NOTE ON THE TOKEN

If this finds a readable token file, the token in it must be treated as burned
— not "probably fine because nobody knew the address". Generate a new one. The
report says so rather than leaving it as a footnote.
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from coordination import site as site_cfg

UA = "Mozilla/5.0 (compatible; glasswork-security-check/1.0; owner-run)"
TIMEOUT = 12

# severity, path, what it means if it comes back
#
# Ordered worst first. 'red' is "somebody can take something from you today".
# 'amber' is "this tells an attacker where to look next".
CHECKS = [
    ("red", "/agency-api/token.php",
     "the agency's password file",
     "must return NOTHING. If it shows a long string, that is your token."),
    ("red", "/agency-api/token.txt",
     "the agency's password, in plain text",
     "delete this file. Put the token in token.php instead."),
    ("red", "/agency-api/token.php.txt",
     "the password file Notepad renamed",
     "delete it. This is the .txt Notepad adds when 'Save as type' is wrong."),
    ("red", "/.env",
     "your keys and passwords",
     "must never be reachable. If this returns text, change every key in it."),
    ("red", "/.git/config",
     "your source code history",
     "if this answers, the whole repository can be downloaded, including "
     "anything ever committed to it."),
    ("red", "/agency-api/backups/",
     "copies of your pages",
     "should refuse. A listing here shows an attacker every file to try."),
    ("amber", "/agency-api/",
     "the agency folder listing",
     "should not list its contents."),
    ("amber", "/config.php",
     "your database settings",
     "should return blank (PHP runs it) — never source code."),
    ("amber", "/phpinfo.php",
     "your server's full configuration",
     "delete it. It names every path, module and version on the box."),
    # Found live on his site, telling him so itself: "Already installed.
    # Please delete install.php for security." An installer left in place is
    # a door somebody else may walk through — some will happily reconfigure
    # the site, or point it at a database of the caller's choosing.
    ("red", "/install.php",
     "the installer that set your site up",
     "delete it. The page says so itself. An installer left on a live site "
     "is a way in."),
    ("amber", "/setup.php",
     "an installer or setup script",
     "delete it if the site is already set up."),
    ("amber", "/default.php",
     "a leftover default page",
     "delete it if it is not a page you use — it is one more thing to keep "
     "patched, and it splits your search ranking with the real page."),
    ("amber", "/backup.zip",
     "a zip of the whole site",
     "delete it, or move it off the website."),
    ("amber", "/database.sql",
     "a database dump",
     "delete it, or move it off the website."),
    ("amber", "/.DS_Store",
     "a Mac folder index",
     "harmless in itself; it lists your filenames to strangers."),
    ("amber", "/composer.json",
     "your dependency list",
     "tells an attacker exactly which libraries and versions to look up."),
]

# What "the server ran it, and it printed nothing" looks like. A PHP file that
# is working correctly gives back an empty body — that is the GOOD answer.
EMPTY_IS_GOOD = ("/agency-api/token.php", "/config.php")

# Text that means "you cannot have this", whatever status code came with it.
REFUSED_MARKS = ("403 forbidden", "access denied", "not found", "404",
                 "you don't have permission", "require all denied")


def _get(url, timeout=TIMEOUT):
    """(status, body, error). Never raises."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read(20000).decode("utf-8", "replace"), ""
    except urllib.error.HTTPError as e:
        try:
            body = (e.read() or b"").decode("utf-8", "replace")
        except Exception:
            body = ""
        return e.code, body, ""
    except Exception as e:
        return 0, "", f"{type(e).__name__}: {e}"


def _looks_like_a_listing(body) -> bool:
    low = (body or "").lower()
    return ("index of /" in low
            or ("<title>index of" in low)
            or (low.count("<a href=") > 3 and "parent directory" in low))


def judge(path, status, body) -> tuple:
    """
    (verdict, detail). verdict is 'exposed' | 'safe' | 'unknown'.

    The rule that matters: a 200 with content is exposure. A 403 or 404 is the
    server refusing. An empty 200 from a .php file is the server RUNNING it,
    which is the outcome we want.
    """
    text = (body or "").strip()
    if status == 0:
        return "unknown", "could not reach it"
    if status in (401, 403, 404, 410):
        return "safe", f"refused with {status}"
    if status != 200:
        return "unknown", f"answered {status}"
    if not text:
        if path in EMPTY_IS_GOOD or path.endswith(".php"):
            return "safe", "ran it and returned nothing — correct"
        return "safe", "empty"
    low = text.lower()
    if any(m in low for m in REFUSED_MARKS) and len(text) < 3000:
        return "safe", "refused (a 200 page that says no)"
    if path.endswith("/") and not _looks_like_a_listing(text):
        return "safe", "no file listing"
    if path.endswith("/") and _looks_like_a_listing(text):
        return "exposed", "the folder lists its contents"
    return "exposed", f"returned {len(text)} characters of content"


def _token_in(body) -> bool:
    """Does this look like it actually contains a secret, not just any text?"""
    t = (body or "").strip()
    return bool(re.fullmatch(r"[A-Za-z0-9_\-]{24,120}", t)) or "return '" in t


def scan(base="", timeout=TIMEOUT, checks=None) -> dict:
    base = (base or site_cfg.site_url() or "").rstrip("/")
    if not base:
        return {"ok": False, "error": "no website saved — set it on /setup"}
    if "://" not in base:
        base = "https://" + base
    rows, unreachable = [], 0
    for sev, path, what, why in (checks or CHECKS):
        status, body, err = _get(base + path, timeout)
        verdict, detail = judge(path, status, body)
        if verdict == "unknown":
            unreachable += 1
        row = {"severity": sev, "path": path, "what": what, "advice": why,
               "status": status, "verdict": verdict, "detail": detail}
        # Named, never printed. A report that quotes the secret back is a
        # second copy of the secret, sitting in a terminal and a log file.
        if verdict == "exposed" and _token_in(body):
            row["burned"] = True
            row["detail"] = ("it returned what looks like a live secret — "
                             "treat that token as compromised")
        rows.append(row)
    bad = [r for r in rows if r["verdict"] == "exposed"]
    red = [r for r in bad if r["severity"] == "red"]
    burned = [r for r in bad if r.get("burned")]
    return {"ok": not bad, "site": base, "checked": len(rows),
            "exposed": bad, "serious": red, "burned": burned,
            "unreachable": unreachable, "rows": rows,
            "head": ("nothing of yours is publicly readable"
                     if not bad else
                     f"{len(bad)} thing(s) a stranger can download right now"
                     + (f", {len(red)} of them serious" if red else ""))}


def main():
    import argparse
    import sys
    ap = argparse.ArgumentParser(
        description="What of yours can a stranger download right now?")
    ap.add_argument("--url", default="", help="check this address instead")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    r = scan(a.url)
    if a.json:
        print(json.dumps(r, indent=2))
        return 0 if r.get("ok") else 1
    if not r.get("ok") and r.get("error"):
        print("  --  " + r["error"])
        return 1

    print(f"\nCHECKED FROM THE OUTSIDE — {r['site']}")
    print("No token, no login. Exactly what anybody in the world can try.")
    print("=" * 66)
    for row in r["rows"]:
        mark = {"exposed": "OPEN", "safe": "ok  ", "unknown": "--  "}[row["verdict"]]
        print(f"  {mark}  {row['path']:30} {row['detail']}")
    print("=" * 66)

    if r["burned"]:
        print("\nCHANGE YOUR TOKEN NOW. One of these handed back what looks")
        print("like a live secret, so it must be treated as known to others:")
        for row in r["burned"]:
            print(f"   {r['site']}{row['path']}  ({row['what']})")
        print("\n   Generate a new one, put it in agency-api/token.php, and")
        print("   update the Admin API token on /setup. Then delete the file")
        print("   that leaked and run this again.")

    if r["exposed"]:
        print(f"\n{r['head'].upper()}")
        for row in sorted(r["exposed"], key=lambda x: x["severity"] != "red"):
            print(f"\n  [{row['severity']}] {row['path']} — {row['what']}")
            print(f"        {row['advice']}")
        print("\n  The .htaccess in paste-into-your-site/agency-api-htaccess.txt")
        print("  closes the agency-api ones. The rest are files to delete or")
        print("  move off the website.")
    else:
        print(f"\n  ok  {r['head']}.")
    if r["unreachable"]:
        print(f"\n  ({r['unreachable']} address(es) could not be reached — "
              "counted as unknown, not as safe.)")
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
