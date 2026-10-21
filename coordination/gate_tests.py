"""
gate_tests.py — run: python -m coordination.gate_tests

The two doors, tested against a real server rather than by reading the code.

DOOR ONE is the front door: a password on the machine. DOOR TWO is Setup, which
holds every API key, the mail password, the site admin token and the spend cap.

The owner found the second door standing open. He set a password, signed in,
clicked Setup and walked straight through — because being signed in had already
satisfied the only check there was. That is the wrong shape: a session is hours
long and follows you around the building; opening the key cupboard should be
"this is me, now". So Setup asks every time, and NAVIGATING to it consumes the
ticket.

The tests that matter most here are the ones about ways round it:

  - /setup.html served straight off the static mount, bypassing /setup
  - a ticket that outlives the page it was issued for, leaving /api/settings
    open for ten minutes after the screen had re-locked (this shipped broken
    once and is why the check exists)
  - the setup APIs answering without any ticket at all

This boots the real server.js on a spare port against a throwaway .env, so it
tests the shipped code path and not a description of it. Skipped, loudly, if
Node is not installed.
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

ROOT = Path(__file__).parents[1]
_fail = []
PASSWORD = "gate-test-password"


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Server:
    """The real server.js, on a spare port, with its own .env and cabinet."""

    def __enter__(self):
        self.dir = Path(tempfile.mkdtemp())
        self.port = _free_port()
        # A copy of the repo's .env is NOT used — this must never touch the
        # owner's real settings, and a test that writes a password into his
        # actual .env would lock him out of his own machine.
        self.env_path = ROOT / ".env"
        self.saved = self.env_path.read_bytes() if self.env_path.exists() else None
        self.env_path.write_text("SITE_MODE=readonly\nSITE_URL=\n", encoding="utf-8")

        env = dict(os.environ, PORT=str(self.port))
        self.proc = subprocess.Popen(
            ["node", str(ROOT / "server.js")], cwd=str(ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        for _ in range(80):                      # wait for it to answer
            try:
                self.get("/api/ping")
                break
            except Exception:
                time.sleep(0.25)
        return self

    def __exit__(self, *a):
        try:
            self.proc.terminate()
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()
        if self.saved is None:
            self.env_path.unlink(missing_ok=True)
        else:
            self.env_path.write_bytes(self.saved)
        shutil.rmtree(self.dir, ignore_errors=True)

    def _req(self, method, path, body=None, ctype=None, cookies=""):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        headers = {}
        if ctype:
            headers["Content-Type"] = ctype
        if cookies:
            headers["Cookie"] = cookies
        c.request(method, path, body=body, headers=headers)
        r = c.getresponse()
        data = r.read().decode("utf-8", "replace")
        # Collect every Set-Cookie so a ticket and a session can both be held.
        jar = []
        for k, v in r.getheaders():
            if k.lower() == "set-cookie":
                jar.append(v.split(";")[0])
        out = (r.status, data, "; ".join(jar), dict(r.getheaders()))
        c.close()
        return out

    def get(self, path, cookies=""):
        return self._req("GET", path, cookies=cookies)

    def post_json(self, path, obj, cookies=""):
        return self._req("POST", path, json.dumps(obj), "application/json", cookies)

    def post_form(self, path, form, cookies=""):
        from urllib.parse import urlencode
        return self._req("POST", path, urlencode(form),
                         "application/x-www-form-urlencoded", cookies)


def _is_unlock(body):
    return 'name="password"' in body and "Open Setup" in body


def run():
    with Server() as s:
        # ---- with no password set, nothing changes -------------------------
        print("\nWith no password set, Setup opens as it always did:")
        st, body, _, _ = s.get("/setup")
        check("the page is served", st, 200)
        check("and it is the real screen, not a prompt", _is_unlock(body), False)
        st, _, _, _ = s.get("/api/settings")
        check("its API answers", st, 200)

        # ---- set one ------------------------------------------------------
        print("\nSetting a password signs everything out:")
        st, body, _, _ = s.post_json("/api/password",
                                     {"password": PASSWORD, "confirm": PASSWORD,
                                      "name": "Hamza"})
        j = json.loads(body)
        check("it is accepted", (st, j.get("ok")), (200, True))
        check("and says he has been signed out", j.get("signedOut"), True)

        st, _, _, _ = s.get("/")
        check("the office now redirects to the door", st, 302)

        # ---- front door ----------------------------------------------------
        print("\nThe front door lets him into the building:")
        st, _, jar, hdr = s.post_form("/api/login", {"password": PASSWORD})
        session = jar
        check("signing in works", st, 302)
        check("and hands back a session", "glasswork_admin=" in session, True)
        st, _, _, _ = s.get("/", cookies=session)
        check("the office opens", st, 200)

        # ---- THE BUG: the building is not the key cupboard -----------------
        print("\nBut being in the building is not being in the key cupboard:")
        st, body, _, _ = s.get("/setup", cookies=session)
        check("Setup asks for the password anyway", _is_unlock(body), True)
        st, body, _, _ = s.get("/setup.html", cookies=session)
        check("and so does the static side door", _is_unlock(body), True)
        st, body, _, _ = s.get("/api/settings", cookies=session)
        check("its API refuses without a ticket", st, 401)
        check("and says what to do about it",
              json.loads(body).get("needsSetupUnlock"), True)

        # ---- unlocking ------------------------------------------------------
        print("\nUnlocking it:")
        st, body, _, _ = s.post_form("/api/setup/unlock", {"password": "wrong"},
                                     cookies=session)
        check("a wrong password is refused", st, 401)
        check("with the prompt again, not a stack trace", _is_unlock(body), True)

        st, _, jar, _ = s.post_form("/api/setup/unlock", {"password": PASSWORD},
                                    cookies=session)
        ticket = jar
        check("the right one is accepted", st, 302)
        both = session + "; " + ticket
        st, body, _, _ = s.get("/setup", cookies=both)
        check("Setup opens", (st, _is_unlock(body)), (200, False))
        st, _, _, _ = s.get("/api/settings", cookies=both)
        check("and its API answers while he is in there", st, 200)

        # ---- and it asks AGAIN ---------------------------------------------
        print("\nComing back asks again — the ticket was for that one visit:")
        st, body, _, _ = s.get("/setup", cookies=both)
        check("the second navigation is stopped", _is_unlock(body), True)
        # This is the hole that shipped once: the page re-locked correctly while
        # the ticket quietly kept the API open for the rest of its ten minutes.
        st, body, _, _ = s.get("/api/settings", cookies=both)
        check("and the API is shut with it, not left open", st, 401)

        # ---- changing the password kills tickets ----------------------------
        print("\nChanging the password invalidates everything:")
        st, _, jar, _ = s.post_form("/api/setup/unlock", {"password": PASSWORD},
                                    cookies=session)
        fresh = session + "; " + jar
        s.get("/setup", cookies=fresh)            # consume the navigation
        st, _, _, _ = s.post_json("/api/password",
                                  {"current": PASSWORD, "password": "another-one-2026",
                                   "confirm": "another-one-2026"}, cookies=fresh)
        check("the change goes through", st, 200)
        st, _, _, _ = s.get("/api/settings", cookies=fresh)
        check("the old setup ticket is dead", st, 401)
        st, _, _, _ = s.get("/", cookies=session)
        check("and so is the session", st, 302)

    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
    else:
        print("all passed")
    return not _fail


def main():
    if not shutil.which("node"):
        print("SKIPPED: Node is not installed, so the server cannot be started.")
        return True
    return run()


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
