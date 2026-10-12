"""
security_tests.py — run: python -m coordination.security_tests   (python3 on macOS/Linux)

    "each user's detail, user end will be private from other users ... no one
     can dig into other users room"

A hosted office (GLASSWORK_HOSTED=1, the program on the developer's own
server) with two people signed in at the same time, and everything a
stranger, or the other person, might try. What has to hold:

  two people at once, each answered from their own website and nothing else
  a change sent from another website is refused (CSRF); so is another address (DNS rebinding)
  every answer carries the protective headers; cookies are HttpOnly, SameSite and Secure
  the parts that belong to one computer are not there (quit, updates, the developer's room)
  guessing is slow: sign-in is limited per address and per name
  a wrong name and a wrong password get the same answer
  weak passwords are refused; sign-up needs the invitation code when one is set
  two-step sign-in: a code from the app, never the same code twice
  a used recovery key is replaced; changing the password signs out everywhere else
  no program on a hosted office can be pointed at the server's own network (SSRF)
  phone approvals need a fresh code when two-step sign-in is on

Nothing reaches the internet; everything is in temporary folders.
"""
import http.client
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from coordination import totp  # noqa: E402

_fail = []
INVITE = "glass-invite-2026"
OWNER_KEY = "owner-key-for-the-tests-0123456789abcdef"
A_PW, B_PW = "violet harbour drum", "copper lantern mist"


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


class Hosted:
    """The office as it runs on the developer's server."""

    def __enter__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="hosted-"))
        return self._start()

    def _start(self):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        s.close()
        env = {**os.environ, "PORT": str(self.port), "AGENCY_DATA": str(self.dir / "data"),
               "GLASSWORK_HOSTED": "1", "GLASSWORK_HOST": "app.example.com", "GLASSWORK_INVITE": INVITE,
               "GLASSWORK_OWNER_KEY": OWNER_KEY,
               "AGENCY_IGNORE_DOTENV": "1", "CLAUDE_TEAM_OFF": "1", "NODE_PATH": str(ROOT / "node_modules")}
        for k in ("AGENCY_DB", "AGENCY_KEY", "AGENCY_ACCOUNT", "AGENCY_SITE", "GLASSWORK_INSTALLED", "BIND_HOST"):
            env.pop(k, None)
        self.proc = subprocess.Popen(["node", "server.js"], cwd=ROOT, env=env,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=0.5).close()
                break
            except OSError:
                time.sleep(0.25)
        return self

    def _stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()

    def restart(self):
        """What every update does to the server: everyone signed out, everything on disk kept."""
        self._stop()
        return self._start()

    def __exit__(self, *a):
        self._stop()
        shutil.rmtree(self.dir, ignore_errors=True)

    def req(self, method, path, body=None, cookie="", host="app.example.com", origin=None, auth=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=180)
        headers = {"Host": host}
        if cookie:
            headers["Cookie"] = cookie
        if origin:
            headers["Origin"] = origin
        if auth:
            headers["Authorization"] = "Bearer " + auth
        data = None
        if body is not None:
            data = json.dumps(body)
            headers["Content-Type"] = "application/json"
        c.request(method, path, body=data, headers=headers)
        r = c.getresponse()
        text = r.read().decode("utf-8", "replace")
        raw = [v for k, v in r.getheaders() if k.lower() == "set-cookie"]
        jar = "; ".join(v.split(";")[0] for v in raw)
        heads = {k.lower(): v for k, v in r.getheaders()}
        heads["_set_cookie"] = raw
        c.close()
        try:
            out = json.loads(text)
        except Exception:
            out = {"_text": text}
        return r.status, out, jar, heads

    def person(self, name, pw, site):
        st, j, jar, heads = self.req("POST", "/api/account/sign-up",
                                     {"name": name, "password": pw, "confirm": pw, "invite": INVITE})
        assert st == 200, (st, j)
        st, j, _, _ = self.req("POST", "/api/account/add-site", {"name": site, "url": f"https://www.{site}.example"}, jar)
        assert st == 200, (st, j)
        return jar, j, heads


def test_two_people_at_once():
    print("\nA hosted office: two people signed in at the same time, each in their own room:")
    if not (shutil.which("node") and (ROOT / "node_modules" / "express").exists()):
        print("  SKIP  Node and the office's libraries are not here")
        return
    with Hosted() as h:
        st, j, _, heads = h.req("GET", "/account")
        check("every answer carries the protective headers",
              ("frame-ancestors 'none'" in heads.get("content-security-policy", ""), heads.get("x-frame-options"),
               heads.get("x-content-type-options"), "max-age" in heads.get("strict-transport-security", "")),
              (True, "DENY", "nosniff", True))
        check("  and says nothing about what it runs on", "x-powered-by" in heads, False)
        st, j, _, _ = h.req("GET", "/api/state", host="evil.example")
        check("another address pointed at it is refused (DNS rebinding)", st, 403)
        st, j, _, _ = h.req("GET", "/api/state")
        check("signed out, nothing is answered", (st, j.get("signin")), (401, True))
        st, j, _, _ = h.req("POST", "/api/account/sign-up", {"name": "Mallory", "password": "silver otter lamp",
                                                             "confirm": "silver otter lamp"})
        check("signing up needs the invitation code", (st, j.get("invite")), (400, True))
        st, j, _, _ = h.req("POST", "/api/account/sign-up", {"name": "Weak", "password": "password123",
                                                             "confirm": "password123", "invite": INVITE})
        check("a password anyone would guess is refused", (st, "guess" in j.get("error", "")), (400, True))

        alpha, made, heads = h.person("Alpha", A_PW, "alpha-glass")
        cookie = next((c for c in heads["_set_cookie"] if c.startswith("glasswork_account=")), "")
        check("the session cookie is HttpOnly, SameSite and Secure",
              all(f in cookie for f in ("HttpOnly", "SameSite=Lax", "Secure")), True)
        beta, _, _ = h.person("Beta", B_PW, "beta-joinery")
        st_a, me_a, _, _ = h.req("GET", "/api/account/me", cookie=alpha)
        st_b, me_b, _, _ = h.req("GET", "/api/account/me", cookie=beta)
        check("both are signed in at once", (st_a, st_b), (200, 200))
        check("  each sees their own website and nobody else's",
              ([s["name"] for s in me_a["sites"]], [s["name"] for s in me_b["sites"]]), (["alpha-glass"], ["beta-joinery"]))
        # How many use it: counts for the developer, and nothing else
        check("the count needs the owner's key",
              (h.req("GET", "/api/owner/stats")[0], h.req("GET", "/api/owner/stats", auth="a-wrong-key-of-some-length")[0]), (401, 401))
        st, stats, _, _ = h.req("GET", "/api/owner/stats", auth=OWNER_KEY)
        check("  with it: how many accounts, and how many used it today",
              (st, stats.get("accounts"), stats.get("today"), stats.get("using_now")), (200, 2, 2, 2))
        check("  and nothing that says who", any(w in json.dumps(stats) for w in ("Alpha", "Beta", "alpha-glass", made["site"]["id"], "acct-")), False)
        on_disk = (h.dir / "data" / "usage.json").read_text(encoding="utf-8")
        check("  what is written down is a number for each day, no one's name or account",
              ("acct-" in on_disk or "Alpha" in on_disk, '"days"' in on_disk), (False, True))
        pubfile = h.dir / "publisher.local.json"
        os.environ["GLASSWORK_PUBLISHER_FILE"] = str(pubfile)
        try:
            from coordination import publisher
            o = publisher.online(f"http://127.0.0.1:{h.port}", OWNER_KEY)
            check("the developer's own program reads the same counts", (o.get("ok"), (o.get("stats") or {}).get("accounts")), (True, 2))
            check("  and gives him the line for his server's settings", o["server_settings"].get("GLASSWORK_OWNER_KEY"), OWNER_KEY)
        finally:
            os.environ.pop("GLASSWORK_PUBLISHER_FILE", None)
        app_files = [h.req("GET", f)[0] for f in ("/manifest.webmanifest", "/sw.js", "/icon-192.png", "/icon-512.png")]
        check("it can be installed as an app before signing in (manifest, icons, a worker that keeps nothing)", app_files, [200] * 4)
        check("  named after the program", h.req("GET", "/manifest.webmanifest")[1].get("display"), "standalone")
        h.req("POST", "/api/orders", {"orders": {"Leads": "ALPHA-ORDER-MARKER find facilities firms"}}, alpha)
        h.req("POST", "/api/orders", {"orders": {"Leads": "BETA-ORDER-MARKER find bakeries"}}, beta)
        a_orders = json.dumps(h.req("GET", "/api/orders", cookie=alpha)[1])
        b_orders = json.dumps(h.req("GET", "/api/orders", cookie=beta)[1])
        check("Alpha's orders are in Alpha's room only",
              ("ALPHA-ORDER-MARKER" in a_orders, "ALPHA-ORDER-MARKER" in b_orders), (True, False))
        check("  and Beta's in Beta's", ("BETA-ORDER-MARKER" in b_orders, "BETA-ORDER-MARKER" in a_orders), (True, False))
        st, _, _, _ = h.req("POST", "/api/account/open-site", {"site": made["site"]["id"]}, beta)
        check("Beta cannot open Alpha's website by its number", st, 400)

        st, j, _, _ = h.req("POST", "/api/orders", {"orders": {"Leads": "x"}}, alpha, origin="https://evil.example")
        check("a change sent from another website is refused (CSRF)", st, 403)
        st, j, _, _ = h.req("POST", "/api/orders", {"orders": {"Leads": "from the office itself"}}, alpha,
                            origin="https://app.example.com")
        check("  but from the office's own page it goes through", st, 200)

        gone = [h.req(m, p, {} if m == "POST" else None, alpha)[0] for m, p in
                (("POST", "/api/quit"), ("POST", "/api/update/apply"), ("GET", "/api/publisher"),
                 ("GET", "/publisher.js"), ("GET", "/api/team"), ("POST", "/api/account/bring-in"),
                 ("POST", "/api/password"))]
        check("closing the office, updates, the developer's room and other one-computer parts are not there",
              gone, [404] * 7)
        files = [h.req("GET", f, cookie=alpha)[0] for f in ("/.env", "/coordination/agency.db", "/server.js",
                                                             "/coordination/vault.py", "/config/product.json",
                                                             "/data/accounts", "/.git/config", "/../server.js")]
        check("the program's own files, settings and records are never served", [x for x in files if x != 404], [])
        open_doors = [p for p in ("/api/state", "/api/settings", "/api/leads", "/api/orders", "/api/account/me",
                                  "/api/support/mine", "/api/engine", "/setup", "/classic")
                      if h.req("GET", p)[0] == 200]
        check("signed out, only the door itself answers", open_doors, [])
        st, j, _, _ = h.req("GET", "/api/update", cookie=alpha)
        check("  and the program says it is kept up to date for them", j.get("hosted"), True)

        st1, j1, _, _ = h.req("POST", "/api/account/sign-in", {"account": "Nobody At All", "password": A_PW})
        st2, j2, _, _ = h.req("POST", "/api/account/sign-in", {"account": "Alpha", "password": "wrong but long"})
        check("a wrong name and a wrong password get the very same answer", (st1, j1.get("error")), (st2, j2.get("error")))

        # Two-step sign-in
        st, j, _, _ = h.req("POST", "/api/account/mfa/start", {}, alpha)
        secret = j.get("secret", "")
        code = totp.code_at(secret, int(time.time() // 30)) if secret else ""
        st, j, _, _ = h.req("POST", "/api/account/mfa/on", {"code": code}, alpha)
        check("two-step sign-in turns on with a code from the app", (st, j.get("ok")), (200, True))
        st, j, _, _ = h.req("POST", "/api/account/sign-in", {"account": "alpha", "password": A_PW})
        check("  then the password alone is not enough", (st, j.get("mfa")), (401, True))
        nxt = totp.code_at(secret, int(time.time() // 30) + 1)
        st, j, alpha2, _ = h.req("POST", "/api/account/sign-in", {"account": "alpha", "password": A_PW, "code": nxt})
        check("  the password and a fresh code sign in", st, 200)
        st, j, _, _ = h.req("POST", "/api/account/sign-in", {"account": "alpha", "password": A_PW, "code": nxt})
        check("  and the same code is never accepted twice", (st, j.get("mfa")), (401, True))

        # A used recovery key is replaced
        st, j, _, _ = h.req("POST", "/api/account/sign-up", {"name": "Gamma", "password": "amber falcon road",
                                                             "confirm": "amber falcon road", "invite": INVITE})
        old_key = j.get("recovery_key")
        st, j, gamma, _ = h.req("POST", "/api/account/recover", {"account": "Gamma", "recovery_key": old_key,
                                                                 "password": "willow copper stream"})
        check("the recovery key opens the account and is replaced by a new one",
              (st, bool(j.get("recovery_key")), j.get("recovery_key") != old_key), (200, True, True))
        st, j, _, _ = h.req("POST", "/api/account/recover", {"account": "Gamma", "recovery_key": old_key,
                                                             "password": "another long phrase"})
        check("  the old one opens nothing any more", st, 401)

        # With two-step sign-in on, every way back in takes two things
        st, j, delta, _ = h.req("POST", "/api/account/sign-up", {"name": "Delta", "password": "harbour violet kite",
                                                                 "confirm": "harbour violet kite", "invite": INVITE})
        d_key = j.get("recovery_key")
        st, j, _, _ = h.req("POST", "/api/account/mfa/start", {}, delta)
        check("the authenticator step offers a picture to scan", str(j.get("qr", "")).startswith("data:image/svg+xml"), True)
        d_secret, now = j.get("secret", ""), int(time.time() // 30)
        h.req("POST", "/api/account/mfa/on", {"code": totp.code_at(d_secret, now)}, delta)
        st, j, _, _ = h.req("POST", "/api/account/recover", {"account": "Delta", "recovery_key": d_key, "password": "linen cobalt tide"})
        check("forgot password with two-step on: the recovery key alone is not enough", (st, j.get("mfa")), (401, True))
        st, j, _, _ = h.req("POST", "/api/account/recover", {"account": "Delta", "recovery_key": d_key,
                                                             "password": "linen cobalt tide", "code": totp.code_at(d_secret, now + 1)})
        check("  the recovery key and a code set a new password", (st, bool(j.get("recovery_key"))), (200, True))
        d_key = j.get("recovery_key")
        st, j, _, _ = h.req("POST", "/api/account/lost-phone", {"account": "Delta", "password": "wrong but long", "recovery_key": d_key})
        check("lost phone: a wrong password with the recovery key opens nothing", st, 401)
        st, j, _, _ = h.req("POST", "/api/account/lost-phone", {"account": "Delta", "password": "linen cobalt tide", "recovery_key": d_key})
        check("  the password and the recovery key together let you in, with a new key", (st, j.get("recovery_key") != d_key), (200, True))
        st, j, _, _ = h.req("POST", "/api/account/sign-in", {"account": "Delta", "password": "linen cobalt tide"})
        check("  and the authenticator is off until it is added again", st, 200)

        # Changing the password signs out everywhere else
        st, j, beta2, _ = h.req("POST", "/api/account/sign-in", {"account": "Beta", "password": B_PW})
        h.req("POST", "/api/account/password", {"current": B_PW, "password": "copper lantern fog",
                                                 "confirm": "copper lantern fog"}, beta2)
        check("changing the password signs out every other session of that person",
              (h.req("GET", "/api/state", cookie=beta)[0], h.req("GET", "/api/state", cookie=beta2)[0]), (401, 200))
        check("  and nobody else's", h.req("GET", "/api/state", cookie=alpha2)[0], 200)
        h.req("POST", "/api/account/sign-out", {}, alpha2)
        check("signing out ends that session", h.req("GET", "/api/state", cookie=alpha2)[0], 401)
        check("  and leaves the other person signed in", h.req("GET", "/api/state", cookie=beta2)[0], 200)

        # Guessing is slow
        tries = [h.req("POST", "/api/account/sign-in", {"account": "Beta", "password": f"guess number {i}"})[0]
                 for i in range(11)]
        check("ten wrong passwords for one name, then it waits", (tries[:10].count(401), tries[10]), (10, 429))
        st, _, _, _ = h.req("POST", "/api/account/sign-in", {"account": "Beta", "password": "copper lantern fog"})
        check("  even the right password, until the wait is over", st, 429)


def test_a_restart_counts_nobody_twice():
    print("\nThe count of people using it survives a restart without counting anyone twice:")
    if not (shutil.which("node") and (ROOT / "node_modules" / "express").exists()):
        print("  SKIP  Node and the office's libraries are not here")
        return
    with Hosted() as h:
        h.person("Gamma", "amber falcon reed", "gamma-glazing")
        h.person("Omega", "teal compass dune", "omega-doors")
        check("two people today", h.req("GET", "/api/owner/stats", auth=OWNER_KEY)[1].get("today"), 2)
        h.restart()
        st, _, _, _ = h.req("POST", "/api/account/sign-in", {"account": "Gamma", "password": "amber falcon reed"})
        check("  after a restart one of them signs in again", st, 200)
        check("  still two people today, not three", h.req("GET", "/api/owner/stats", auth=OWNER_KEY)[1].get("today"), 2)
        kept = (h.dir / "data" / "usage-today.json").read_text(encoding="utf-8")
        check("  what is kept for today is scrambled tags, no name or account",
              any(w in kept for w in ("Gamma", "Omega", "acct-", "gamma-glazing")), False)


def test_no_program_reaches_the_servers_own_network():
    print("\nOn a hosted office no program can be pointed at the server's own network (SSRF):")
    code = ("import coordination, urllib.request\n"
            "out = []\n"
            "for u in ['http://127.0.0.1:9/', 'http://169.254.169.254/latest/meta-data/', 'http://localhost/',\n"
            "          'http://10.0.0.1/', 'http://[::1]/', 'http://192.168.1.1/']:\n"
            "    try:\n"
            "        urllib.request.urlopen(u, timeout=3); out.append('reached')\n"
            "    except Exception as e:\n"
            "        out.append('refused' if 'not a public internet address' in str(e) else 'other: ' + str(e))\n"
            "print(','.join(out))\n")
    env = {**os.environ, "GLASSWORK_HOSTED": "1"}
    p = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=60)
    check("loopback, cloud metadata, private and link-local addresses are all refused",
          p.stdout.strip().split(","), ["refused"] * 6)


def test_phone_approvals_need_a_code():
    print("\nWith two-step sign-in on, an approval from the phone needs a fresh code:")
    from coordination import ntfy_replies, workspace
    place = Path(tempfile.mkdtemp(prefix="phone-"))
    saved = {k: os.environ.get(k) for k in ("AGENCY_DATA", "AGENCY_KEY", "AGENCY_ACCOUNT", "AGENCY_SITE")}
    try:
        os.environ["AGENCY_DATA"] = str(place)
        made = workspace.create_account("Delta", "lemon granite bay")
        os.environ.update(AGENCY_KEY=made["key"], AGENCY_ACCOUNT=made["account"]["id"])
        site = workspace.add_site(made["account"]["id"], "Delta shop", "https://delta.example")["site"]["id"]
        os.environ["AGENCY_SITE"] = site
        check("without two-step sign-in, phone replies work as before", ntfy_replies._code_ok(None), True)
        secret = totp.new_secret()
        step = int(time.time() // 30)
        workspace.mfa_on(made["account"]["id"], secret, totp.code_at(secret, step))
        check("with it, a reply without a code is refused", ntfy_replies._code_ok(None), False)
        good = totp.code_at(secret, step + 1)
        check("  a fresh code is accepted", ntfy_replies._code_ok(good), True)
        check("  and the same code twice is not", ntfy_replies._code_ok(good), False)
        check("a reply reads its number and code", ntfy_replies.parse_coded("ok 3 123456"), ("approved", 3, "123456"))
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(place, ignore_errors=True)


def test_nothing_leaks_in_the_code():
    print("\nWhat the code itself shows:")
    server = (ROOT / "server.js").read_text(encoding="utf-8")
    check("no program's raw error output is sent to the browser", "String(r.stderr" in server.split("function ensureDbSync")[0]
          .split("const NO_PY")[1], False)
    check("no browser is given another site's permission to read the office (CORS)",
          "Access-Control-Allow-Origin" in server, False)
    ui = "".join(p.read_text(encoding="utf-8") for p in (ROOT / "ui").glob("*.html"))
    check("nothing secret is kept in the browser's storage",
          any(w in ui for w in ("localStorage.setItem('token", "localStorage.setItem('key", "sessionStorage.setItem('token")), False)
    check("no source maps are served", list((ROOT / "ui").glob("*.map")), [])
    import re
    listed = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8").stdout.split()
    pat = re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}|sk-proj-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{30,}|"
                     r"AIza[0-9A-Za-z_-]{35}|-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----|xox[bp]-[0-9A-Za-z-]{20,}")
    found = [f for f in listed if not f.endswith("_tests.py") and (ROOT / f).is_file()
             and (ROOT / f).stat().st_size < 2_000_000 and pat.search((ROOT / f).read_text(encoding="utf-8", errors="ignore"))]
    check("no key, token or private key is written into the program's files", found, [])
    check("settings, keys and records are kept out of git",
          all(x in (ROOT / ".gitignore").read_text(encoding="utf-8") for x in (".env", "*.local.json", "data/")), True)


def main():
    for t in (test_two_people_at_once, test_a_restart_counts_nobody_twice, test_no_program_reaches_the_servers_own_network,
              test_phone_approvals_need_a_code, test_nothing_leaks_in_the_code):
        t()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        return 1
    print("security: all good — each person in their own room, and the doors shut to everyone else.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
