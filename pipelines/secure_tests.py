"""
secure_tests.py — run: python3 -m pipelines.secure_tests

"What of mine can a stranger download right now?"

WHY THE JUDGEMENT MATTERS MORE THAN THE PROBE

Fetching a URL is easy. Deciding what the answer MEANS is where a security
check earns its keep, and where it can do real harm by being wrong in either
direction:

  a false alarm    sends him deleting files that were never exposed, and
                   teaches him to ignore the next report
  a false all-clear is worse, because he stops looking

The awkward cases are all here. An empty 200 from a .php file is the server
running it correctly — the outcome we WANT — and would read as "exposed" to
anything that only checks the status code. A 200 page whose body says "403
Forbidden" is a refusal wearing the wrong hat. A host that cannot be reached
is unknown, and unknown must never be filed as safe.

Nothing here touches the network.
"""
import sys

from . import site_secure as S

_fail = []


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


def _with_web(answers, fn):
    """answers: {path: (status, body)}. Anything unlisted is a 404."""
    real = S._get
    asked = []

    def fake(url, timeout=S.TIMEOUT):
        path = url.split("://", 1)[-1].split("/", 1)[-1]
        path = "/" + path
        asked.append(path)
        st, body = answers.get(path, (404, "Not Found"))
        return st, body, ""
    S._get = fake
    try:
        return fn(), asked
    finally:
        S._get = real


# ---------------------------------------------------------------------------

def test_an_empty_php_file_is_the_right_answer_not_a_finding():
    """
    token.php returning nothing is the whole point of it being a .php file:
    the server RAN it instead of showing it. A check that only reads the status
    code would call that 200 an exposure and send him fixing what works.
    """
    print("\nThe server running a file is success, not a leak:")
    check("empty 200 from a .php", S.judge("/agency-api/token.php", 200, ""),
          ("safe", "ran it and returned nothing — correct"))
    check("whitespace counts as empty too",
          S.judge("/agency-api/token.php", 200, "\n  \n")[0], "safe")

    print("\n...but the same file handing back a token is the real thing:")
    v, why = S.judge("/agency-api/token.txt", 200,
                     "78o5ukqch0y7g2h16l9l0zvoukztc4jkiyzxo6g11unw0vyu")
    check("exposed", v, "exposed")
    check("and it counts what came back", "48 characters" in why, True)


def test_refusals_are_recognised_however_they_are_dressed():
    print("\nA refusal is a refusal:")
    for code in (401, 403, 404, 410):
        check(f"{code}", S.judge("/.env", code, "nope")[0], "safe")

    print("\n...including the host that says no with a 200:")
    check("a 200 page that reads 'Access denied'",
          S.judge("/.env", 200, "<h1>403 Forbidden</h1>Access denied")[0], "safe")

    print("\n...and a folder that answers but lists nothing:")
    check("no listing", S.judge("/agency-api/", 200,
                                "<html><body>Nothing here</body></html>")[0], "safe")
    print("  ...while one that lists its contents is a map for an attacker:")
    v, why = S.judge("/agency-api/", 200,
                     "<title>Index of /agency-api</title><a href='token.php'>x</a>"
                     "<a href='a'>a</a><a href='b'>b</a><a href='c'>c</a>"
                     "Parent Directory")
    check("exposed", v, "exposed")
    check("and says why", "lists its contents" in why, True)


def test_unreachable_is_never_filed_as_safe():
    """
    The dangerous failure mode. A timeout that reads as 'safe' means the report
    says all clear about a file nobody actually checked.
    """
    print("\nCould not reach it is its own answer:")
    check("status 0", S.judge("/.env", 0, "")[0], "unknown")
    check("a 500 is not a refusal either", S.judge("/.env", 500, "boom")[0],
          "unknown")

    print("\n...and the run counts them separately from the clean ones:")
    r, _ = _with_web({"/.env": (0, "")}, lambda: S.scan("https://x.ae"))
    check("counted as unreachable", r["unreachable"] >= 1, True)
    check("and not counted as exposed",
          any(x["path"] == "/.env" for x in r["exposed"]), False)


def test_a_leaked_token_is_called_burned_not_noted():
    """
    A token that has been publicly readable is compromised, full stop — not
    "probably fine because nobody knew the address". Anything softer than that
    invites him to leave it in place.
    """
    print("\nA readable secret is a burned secret:")
    r, _ = _with_web(
        {"/agency-api/token.php.txt":
         (200, "78o5ukqch0y7g2h16l9l0zvoukztc4jkiyzxo6g11unw0vyu")},
        lambda: S.scan("https://x.ae"))
    check("found", len(r["burned"]), 1)
    check("marked serious", r["burned"][0]["severity"], "red")
    check("and the wording leaves no room to shrug",
          "compromised" in r["burned"][0]["detail"], True)

    print("\n...and the report never prints the secret itself:")
    blob = str(r)
    check("the token is not in the report",
          "78o5ukqch0y7g2h16l9l0zvoukztc4jkiyzxo6g11unw0vyu" in blob, False)

    print("\n...a page of ordinary HTML is exposed but not 'burned':")
    r, _ = _with_web({"/composer.json": (200, "<html>a normal page</html>")},
                     lambda: S.scan("https://x.ae"))
    check("exposed", len(r["exposed"]), 1)
    check("but no secret claimed", r["burned"], [])


def test_a_clean_site_says_so_plainly():
    print("\nEverything refused is the answer he wants to see:")
    r, asked = _with_web({}, lambda: S.scan("https://x.ae"))
    check("ok", r["ok"], True)
    check("nothing exposed", r["exposed"], [])
    check("and it says it in one line",
          r["head"], "nothing of yours is publicly readable")
    check("it really checked every one", len(asked), len(S.CHECKS))
    check("including the agency's own token file",
          "/agency-api/token.php" in asked, True)
    check("and the backups folder", "/agency-api/backups/" in asked, True)


def test_it_only_ever_reads():
    """
    This is a check he runs on his own site, and it must stay that. No login
    attempt, no password, no injection — one GET each and nothing else.
    """
    print("\nEvery check is a plain read:")
    seen = []
    real = S.urllib.request.Request

    def spy(url, headers=None, **kw):
        seen.append(kw.get("method") or "GET")
        return real(url, headers=headers or {})
    S.urllib.request.Request = spy
    try:
        S._get("https://x.ae/.env", timeout=1)
    except Exception:
        pass
    finally:
        S.urllib.request.Request = real
    check("nothing but GET", set(seen) <= {"GET"}, True)
    check("no path in the list carries a query or a payload",
          [p for _s, p, _w, _y in S.CHECKS if "?" in p or "=" in p], [])


def test_the_check_is_part_of_the_standing_site_report():
    """
    Run by hand once, this found his API token published on his website. The
    leak that matters is not that one — it is the one introduced next week by
    a file somebody uploads and forgets. A check you have to remember is a
    check that stops happening.
    """
    from . import site_check as C
    print("\nIt is one of the standing checks, not a command to remember:")
    real = S.scan
    S.scan = lambda url=None, **kw: {
        "ok": True, "checked": 13, "exposed": [], "serious": [], "burned": [],
        "unreachable": 0, "rows": [], "head": "clean"}
    try:
        step = C.step_secure("https://x.ae")
    finally:
        S.scan = real
    check("it has a name he will understand", step["name"], "Private files")
    check("clean is clean", step["state"], C.OK)
    check("and it says how many things it looked at",
          "13 known leak points" in step["says"], True)

    print("\n...a leaked secret fails the whole report, not warns:")
    S.scan = lambda url=None, **kw: {
        "ok": False, "checked": 13,
        "exposed": [{"path": "/agency-api/token.php.txt", "severity": "red"}],
        "serious": [{"path": "/agency-api/token.php.txt", "severity": "red"}],
        "burned": [{"path": "/agency-api/token.php.txt", "severity": "red"}],
        "unreachable": 0, "rows": [], "head": "1 thing"}
    try:
        step = C.step_secure("https://x.ae")
    finally:
        S.scan = real
    check("FAIL, not a warning", step["state"], C.FAIL)
    check("it names the file", "/agency-api/token.php.txt" in step["says"], True)
    check("and says the token is burned",
          "compromised" in step["says"], True)

    print("\n...and something merely browsable is a warning, not a failure:")
    S.scan = lambda url=None, **kw: {
        "ok": False, "checked": 13,
        "exposed": [{"path": "/composer.json", "severity": "amber"}],
        "serious": [], "burned": [], "unreachable": 0, "rows": [], "head": "1"}
    try:
        step = C.step_secure("https://x.ae")
    finally:
        S.scan = real
    check("warn", step["state"], C.WARN)

    print("\n...and unreachable is reported, never counted as safe:")
    S.scan = lambda url=None, **kw: {
        "ok": True, "checked": 13, "exposed": [], "serious": [], "burned": [],
        "unreachable": 4, "rows": [], "head": "clean"}
    try:
        step = C.step_secure("https://x.ae")
    finally:
        S.scan = real
    check("said out loud", "not the same as safe" in step["says"], True)


def main():
    for t in (test_an_empty_php_file_is_the_right_answer_not_a_finding,
              test_refusals_are_recognised_however_they_are_dressed,
              test_unreachable_is_never_filed_as_safe,
              test_a_leaked_token_is_called_burned_not_noted,
              test_a_clean_site_says_so_plainly,
              test_it_only_ever_reads,
              test_the_check_is_part_of_the_standing_site_report):
        t()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        sys.exit(1)
    print("all security-check tests passed")


if __name__ == "__main__":
    main()
