"""
isolation_tests.py — run: python -m coordination.isolation_tests   (python3 on macOS/Linux)

    "end to end encryption of user using the program so the other user using
     the program may not get access to the other user have or using the same
     program each program on pc is remains to itself"

Two people sharing one computer and one copy of the program. What has to hold:

  nothing either of them puts in can be read from any file on the disk — not
    their websites' names or addresses, not their settings or keys, not their
    cabinet — without their own password
  one person's key opens nothing of the other's
  the sign-in screen shows who can sign in, and nothing about their websites
  a copy installed from Setup asks for an account before anything else, and
    writes nothing in the program's own folder
  signing in never leaves the other person's website open

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

from coordination import accounts, vault, workspace  # noqa: E402

_fail = []
ALPHA = {"host": "alpha-glazing-marker.example", "name": "Alpha Glazing Marker", "secret": "ALPHA-KEY-7Q2X-MARKER"}
BETA = {"host": "beta-joinery-marker.example", "name": "Beta Joinery Marker", "secret": "BETA-KEY-9Z4W-MARKER"}


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


def readable(folder: Path, words) -> list:
    """Every file under the folder in which any of the words can be read as they are."""
    found = []
    for p in folder.rglob("*"):
        if p.is_file():
            raw = p.read_bytes()
            if any(w.encode("utf-8") in raw or w.lower().encode("utf-8") in raw for w in words):
                found.append(p.relative_to(folder).as_posix())
    return found


class _Place:
    def __enter__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="two-people-"))
        self.env = {k: os.environ.get(k) for k in ("AGENCY_DATA", "AGENCY_KEY")}
        os.environ["AGENCY_DATA"] = str(self.dir / "data")
        os.environ.pop("AGENCY_KEY", None)
        return self

    def __exit__(self, *a):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.dir, ignore_errors=True)


def _person(who, password):
    made = workspace.create_account(who["name"].split()[0], password)
    acct_id = made["account"]["id"]
    os.environ["AGENCY_KEY"] = made["key"]
    site = workspace.add_site(acct_id, who["name"], "https://www." + who["host"])["site"]
    workspace.open_site(acct_id, site["id"])
    sdir = workspace.site_dir(acct_id, site["id"])
    workspace.write_secret(sdir / "site.env", f"SITE_URL=https://www.{who['host']}\nSITE_API_TOKEN={who['secret']}\n")
    workspace.write_secret(workspace.account_dir(acct_id) / "account.env", f"ANTHROPIC_API_KEY={who['secret']}\n")
    os.environ.pop("AGENCY_KEY", None)
    return acct_id, made["key"], site["id"]


def test_two_people_one_computer():
    print("\nTwo people, one computer: nothing of either can be read from the disk:")
    with _Place() as place:
        a_id, a_key, a_site = _person(ALPHA, "alpha horse battery")
        b_id, b_key, b_site = _person(BETA, "beta staple correct")
        words = [ALPHA["host"], ALPHA["name"], ALPHA["secret"], BETA["host"], BETA["name"], BETA["secret"]]
        check("no file on the disk shows a website, a name or a key", readable(place.dir, words), [])
        check("a website's folder is not named after it",
              any("alpha" in p.name or "beta" in p.name for p in (place.dir / "data").rglob("site*")), False)
        listed = json.dumps(accounts.cmd_list({}))
        check("the sign-in screen shows who can sign in", all(n in listed for n in ("Alpha", "Beta")), True)
        check("  and nothing about their websites", any(w in listed for w in (ALPHA["host"], BETA["host"],
                                                                             ALPHA["name"], BETA["name"])), False)
        os.environ["AGENCY_KEY"] = b_key
        theirs = workspace.sites(workspace.account(a_id))
        check("Beta's key does not open Alpha's websites", (theirs[0].get("locked"), theirs[0]["url"]), (True, ""))
        try:
            workspace.read_secret(workspace.site_dir(a_id, a_site) / "site.env")
            opened = True
        except Exception:
            opened = False
        check("  nor Alpha's settings", opened, False)
        mine = workspace.sites(workspace.account(b_id))
        check("but opens Beta's own", mine[0]["url"], "https://www." + BETA["host"])
        os.environ["AGENCY_KEY"] = a_key
        check("and Alpha's key opens Alpha's", workspace.sites(workspace.account(a_id))[0]["name"], ALPHA["name"])
        os.environ.pop("AGENCY_KEY", None)
        check("signed out, nobody's websites can be read",
              [s.get("locked") for s in workspace.sites(workspace.account(a_id)) + workspace.sites(workspace.account(b_id))],
              [True, True])


def test_websites_made_before_are_sealed_too():
    print("\nA website kept readable by an older version is sealed the next time its owner signs in:")
    with _Place() as place:
        made = workspace.create_account("Older", "older horse battery")
        acct = workspace.account(made["account"]["id"])
        acct["sites"] = [{"id": "old-site-1", "name": ALPHA["name"], "url": "https://" + ALPHA["host"], "created": 1}]
        (workspace.site_dir(acct["id"], "old-site-1")).mkdir(parents=True)
        workspace._save(acct)
        check("before: readable", bool(readable(place.dir, [ALPHA["host"]])), True)
        r = workspace.sign_in("Older", "older horse battery")
        check("after signing in: sealed", (r.get("ok"), readable(place.dir, [ALPHA["host"], ALPHA["name"]])), (True, []))
        os.environ["AGENCY_KEY"] = r["key"]
        check("  and still theirs to see", workspace.sites(workspace.account(acct["id"]))[0]["url"], "https://" + ALPHA["host"])
        os.environ.pop("AGENCY_KEY", None)


class _Office:
    """A copy staged the way Setup installs it (or a fresh copy from source), with its office running."""

    def __init__(self, installed=True):
        self.installed = installed

    def __enter__(self):
        sys.path.insert(0, str(ROOT / "installer"))
        import build_windows as bw
        self.dir = Path(tempfile.mkdtemp(prefix="installed-"))
        self.app = self.dir / "app"
        self.app.mkdir()
        bw.stage_program(self.app, json.dumps({"name": "Glasswork", "version": "9.9.9"}))
        self.before = sorted(p.relative_to(self.app).as_posix() for p in self.app.rglob("*") if p.is_file())
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        s.close()
        env = {**os.environ, "PORT": str(self.port), "AGENCY_DATA": str(self.dir / "data"), "AGENCY_IGNORE_DOTENV": "1",
               "CLAUDE_TEAM_OFF": "1", "NODE_PATH": str(ROOT / "node_modules")}
        if self.installed:
            env["GLASSWORK_INSTALLED"] = "1"
        for k in ("AGENCY_DB", "AGENCY_KEY") + (() if self.installed else ("GLASSWORK_INSTALLED",)):
            env.pop(k, None)
        self.env = env
        # run.py's own start-up first — the check it makes, and the cabinet it
        # used to build — exactly as the shortcut starts it.
        subprocess.run([sys.executable, "run.py", "--check", "--no-update"], cwd=self.app, env=env,
                       capture_output=True, text=True, encoding="utf-8", timeout=300)
        self.proc = subprocess.Popen(["node", "server.js"], cwd=self.app, env=env,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=0.5).close()
                break
            except OSError:
                time.sleep(0.25)
        return self

    def __exit__(self, *a):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()
        shutil.rmtree(self.dir, ignore_errors=True)

    def req(self, method, path, body=None, cookie=""):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=180)
        headers = {"Cookie": cookie} if cookie else {}
        data = None
        if body is not None:
            data = json.dumps(body)
            headers["Content-Type"] = "application/json"
        c.request(method, path, body=data, headers=headers)
        r = c.getresponse()
        text = r.read().decode("utf-8", "replace")
        jar = "; ".join(v.split(";")[0] for k, v in r.getheaders() if k.lower() == "set-cookie")
        c.close()
        try:
            out = json.loads(text)
        except Exception:
            out = {"_text": text}
        return r.status, out, jar


def test_an_installed_copy():
    print("\nA copy installed from Setup: an account first, nothing in the program's folder, nobody else's website:")
    if not (shutil.which("node") and (ROOT / "node_modules" / "express").exists()):
        print("  SKIP  Node and the office's libraries are not here")
        return
    with _Office() as o:
        st, j, _ = o.req("GET", "/api/state")
        check("before anyone has an account, it asks for one", (st, j.get("needsAccount")), (401, True))
        st, _j, _ = o.req("GET", "/api/account/list")
        check("  the account page itself is open", st, 200)
        st, j, alpha = o.req("POST", "/api/account/sign-up", {"name": "Alpha", "password": "alpha horse battery",
                                                              "confirm": "alpha horse battery"})
        check("Alpha makes an account", (st, j.get("ok")), (200, True))
        st, j, _ = o.req("POST", "/api/account/add-site", {"name": ALPHA["name"], "url": "https://www." + ALPHA["host"]}, alpha)
        check("  and adds a website", (st, j.get("ok")), (200, True))
        st, j, _ = o.req("GET", "/api/state", cookie=alpha)
        check("  and the office opens on it", st, 200)
        o.req("POST", "/api/account/sign-out", {}, alpha)
        st, j, _ = o.req("GET", "/api/account/list")
        check("signed out, the sign-in screen names nobody's website",
              any(w in json.dumps(j) for w in (ALPHA["host"], ALPHA["name"])), False)
        st, j, beta = o.req("POST", "/api/account/sign-up", {"name": "Beta", "password": "beta staple correct",
                                                             "confirm": "beta staple correct"})
        st, me, _ = o.req("GET", "/api/account/me", cookie=beta)
        check("Beta signs up and sees no websites of Alpha's", (me.get("sites"), me.get("open")), ([], None))
        st, j, _ = o.req("GET", "/api/state", cookie=beta)
        check("  and is not let into Alpha's office", (st, j.get("needsSite")), (409, True))
        after = sorted(p.relative_to(o.app).as_posix() for p in o.app.rglob("*")
                       if p.is_file() and "__pycache__" not in p.parts)
        check("nothing was written in the program's own folder", sorted(set(after) - set(o.before)), [])
        check("and nothing of Alpha's can be read in the data folder",
              readable(o.dir / "data", [ALPHA["host"], ALPHA["name"]]), [])


def test_a_fresh_copy_from_source():
    print("\nA fresh copy run from source is just as private: an account first, nothing in its folder:")
    if not (shutil.which("node") and (ROOT / "node_modules" / "express").exists()):
        print("  SKIP  Node and the office's libraries are not here")
        return
    with _Office(installed=False) as o:
        st, j, _ = o.req("GET", "/api/state")
        check("it asks for an account before anything else", (st, j.get("needsAccount")), (401, True))
        after = sorted(p.relative_to(o.app).as_posix() for p in o.app.rglob("*")
                       if p.is_file() and "__pycache__" not in p.parts)
        check("  and starting it wrote nothing in its own folder", sorted(set(after) - set(o.before)), [])


def main():
    for t in (test_two_people_one_computer, test_websites_made_before_are_sealed_too, test_an_installed_copy,
              test_a_fresh_copy_from_source):
        t()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        return 1
    print("isolation: all good — each person's work opens with their own password, and only theirs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
