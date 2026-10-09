"""
account_tests.py — run: python -m coordination.account_tests

"make the program end to end encrypted for each user so the data of each user
 could not be used by other user using the same program on local host"

What is protected here:
  - a password and a recovery key each open the account; a wrong one does not,
    and nothing else does — another account's keys included;
  - changing the password keeps everything, and the recovery key still works;
  - a website's cabinet on disk is unreadable without its key: signed out, with
    another key, or by plain SQLite — and readable with the right one;
  - settings and config files are sealed, and the server's half (lib/sealed.js)
    reads what Python wrote and the other way round;
  - bringing his current website in seals it on the way, and removing the
    original copy happens only after that and only on his say-so;
  - the real server: sign up, signed out means 401, sign in, Setup asks for the
    ACCOUNT password, add and switch websites, sign out.

The encryption parts are skipped, loudly, on a computer without the libraries
(pip install -r requirements.txt) — the program still runs there, it just
cannot make an account until they are installed.
"""
import http.client
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from . import vault, workspace

ROOT = Path(__file__).parents[1]
_fail = []


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


def _libraries() -> str:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
        from sqlcipher3 import dbapi2  # noqa: F401
    except Exception as e:
        return f"{type(e).__name__}: {e}"
    return ""


class _Place:
    """A data folder of its own; the test run's cabinet and settings switches off."""

    KEEP = ("AGENCY_DATA", "AGENCY_DB", "AGENCY_IGNORE_DOTENV", "AGENCY_KEY")

    def __enter__(self):
        self.saved = {k: os.environ.get(k) for k in self.KEEP}
        self.dir = Path(tempfile.mkdtemp(prefix="accounts-"))
        os.environ["AGENCY_DATA"] = str(self.dir / "data")
        for k in ("AGENCY_DB", "AGENCY_IGNORE_DOTENV", "AGENCY_KEY"):
            os.environ.pop(k, None)
        return self

    def __exit__(self, *a):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_the_keys():
    print("A password and a recovery key each open the account, and nothing else does:")
    keys, master, recovery = vault.create("correct horse", "ammar-1")
    check("the password opens it", vault.open_with(keys, "password", "correct horse", "ammar-1"), master)
    check("a wrong password does not", vault.open_with(keys, "password", "correct horsE", "ammar-1"), None)
    check("the recovery key opens it", vault.open_with(keys, "recovery", recovery, "ammar-1"), master)
    messy = " " + recovery.lower().replace("-", " ") + " "
    check("  typed in lower case with spaces", vault.open_with(keys, "recovery", messy, "ammar-1"), master)
    check("the same keys do not open another account",
          vault.open_with(keys, "password", "correct horse", "someone-2"), None)
    check("nothing about the password is stored in the clear",
          "correct horse" in json.dumps(keys), False)
    check("a short password is refused", vault.password_problem("short") != "", True)

    print("\n...changing the password keeps everything, and the recovery key still works:")
    keys2 = vault.relock(keys, master, "password", "battery staple", "ammar-1")
    check("the new password opens the same account",
          vault.open_with(keys2, "password", "battery staple", "ammar-1"), master)
    check("the old one no longer does",
          vault.open_with(keys2, "password", "correct horse", "ammar-1"), None)
    check("the recovery key still does", vault.open_with(keys2, "recovery", recovery, "ammar-1"), master)

    print("\n...and each thing it opens has its own key:")
    check("two websites' cabinets have different keys",
          vault.cabinet_key(master, "a") != vault.cabinet_key(master, "b"), True)
    sealed = vault.seal(master, b"SMTP_PASS=hunter2\n", "sites/a/site.env")
    check("a sealed file says so", vault.is_sealed(sealed), True)
    check("and holds nothing readable", b"hunter2" in sealed, False)
    check("it opens where it was sealed",
          vault.unseal(master, sealed, "sites/a/site.env"), b"SMTP_PASS=hunter2\n")
    try:
        vault.unseal(master, sealed, "sites/b/site.env")
        moved = "opened"
    except vault.Locked:
        moved = "refused"
    check("moved to another website, it does not", moved, "refused")


def _py(code, env, expect_ok=True):
    r = subprocess.run([sys.executable, "-c", code], cwd=str(ROOT), env=env,
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()


def test_the_cabinet_is_encrypted():
    print("\nA website's cabinet on disk is unreadable without its key:")
    with _Place() as place:
        made = workspace.create_account("Ammar", "correct horse")
        acct = made["account"]["id"]
        site = workspace.add_site(acct, "World Deal", "https://www.worlddealtechnicalservices.com")["site"]["id"]
        workspace.open_site(acct, site)
        env = dict(os.environ, AGENCY_KEY=made["key"])
        code, out, err = _py("from coordination import board\n"
                             "import sqlite3\n"
                             "print(getattr(sqlite3, '_glasswork_cipher', False))\n"
                             "board.post_task('IT', 'Secret order: change the email', 'World Deal only')\n"
                             "print(board.DB)", env)
        check("signed in, the program writes to it", code, 0)
        check("  through the cipher", out.splitlines()[:1], ["True"])
        db = workspace.site_dir(acct, site) / "agency.db"
        check("  into this website's own cabinet", out.splitlines()[-1:], [str(db)])
        check("nothing readable on disk", b"Secret order" in db.read_bytes(), False)
        try:
            sqlite3.connect(db).execute("SELECT count(*) FROM tasks").fetchone()
            plain = "read it"
        except sqlite3.DatabaseError:
            plain = "refused"
        check("plain SQLite cannot read it", plain, "refused")
        out_env = {k: v for k, v in os.environ.items() if k != "AGENCY_KEY"}
        code, out, err = _py("from coordination import board\nboard.post_task('IT', 'x', 'y')", out_env)
        check("signed out, the program cannot open it", code != 0, True)
        check("  and says why in a sentence", "sign in" in err, True)
        other = workspace.create_account("Someone else", "another password")
        code, out, err = _py("from coordination import board\nboard.post_task('IT', 'x', 'y')",
                             dict(os.environ, AGENCY_KEY=other["key"]))
        check("another account's key does not open it", code != 0, True)
        code, out, err = _py("from coordination import board\nimport sqlite3\n"
                             "c = sqlite3.connect(board.DB)\n"
                             "print(c.execute('SELECT title FROM tasks').fetchone()[0])", env)
        check("with the right key it reads back", out, "Secret order: change the email")


def test_the_server_and_python_read_each_others_files():
    print("\nSettings sealed by Python open on the server, and the other way round:")
    if not shutil.which("node"):
        print("  SKIP  Node is not installed here")
        return
    master = bytes(range(32))
    py_sealed = vault.seal(master, "SITE_URL=https://www.example.ae\n".encode("utf-8"), "sites/s1/site.env")
    js = ("const s=require(%s);const m=Buffer.from(%s,'hex');"
          "const fromPy=s.unseal(m,Buffer.from(%s,'base64'),'sites/s1/site.env');"
          "const fromJs=s.seal(m,'TELEGRAM_CHAT_ID=42\\n','account.env');"
          "console.log(JSON.stringify({fromPy,fromJs:fromJs.toString('base64')}));") % (
        json.dumps(str(ROOT / "lib" / "sealed.js")), json.dumps(master.hex()),
        json.dumps(__import__("base64").b64encode(py_sealed).decode()))
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True, encoding="utf-8", timeout=60)
    got = json.loads(r.stdout or "{}")
    check("the server opens what Python sealed", got.get("fromPy"), "SITE_URL=https://www.example.ae\n")
    js_sealed = __import__("base64").b64decode(got.get("fromJs") or "")
    check("Python opens what the server sealed",
          vault.unseal(master, js_sealed, "account.env"), b"TELEGRAM_CHAT_ID=42\n")


def test_bringing_his_website_in():
    print("\nBringing his current website in seals it, and the original goes only on his say-so:")
    from . import accounts
    with _Place() as place:
        old = place.dir / "program"
        (old / "config").mkdir(parents=True)
        (old / "coordination").mkdir()
        env_text = ("SITE_URL=https://www.worlddealtechnicalservices.com\n"
                    "SMTP_PASS=hunter2\nTELEGRAM_CHAT_ID=999\n")
        (old / ".env").write_text(env_text, encoding="utf-8")
        (old / "config" / "gsc-service-account.local.json").write_text('{"client_email": "sa@x"}', encoding="utf-8")
        con = sqlite3.connect(old / "coordination" / "agency.db")
        con.execute("CREATE TABLE leads (company TEXT)")
        con.execute("INSERT INTO leads VALUES ('Marina Hotel')")
        con.commit()
        con.close()
        real = workspace.ROOT
        workspace.ROOT = old
        try:
            made = workspace.create_account("Ammar", "correct horse")
            acct = made["account"]["id"]
            os.environ["AGENCY_KEY"] = made["key"]
            early = accounts.cmd_retire_old({"account": acct, "confirm": True})
            r = accounts.cmd_bring_in({"account": acct})
            again = accounts.cmd_bring_in({"account": acct})
            site = r.get("site")
            sdir = workspace.site_dir(acct, site)
            unasked = accounts.cmd_retire_old({"account": acct})
            survived = (old / ".env").exists()
            done = accounts.cmd_retire_old({"account": acct, "confirm": True})
        finally:
            workspace.ROOT = real
        check("the original cannot be removed before it is brought in", early["ok"], False)
        check("it is brought in", r.get("ok"), True)
        check("  named after his website", [s["name"] for s in r.get("sites") or []],
              ["https://www.worlddealtechnicalservices.com"])
        check("  and opened", workspace.active(), {"account": acct, "site": site})
        check("not twice", again["ok"], False)
        senv = (sdir / "site.env").read_bytes()
        check("his website's settings are sealed", (vault.is_sealed(senv), b"hunter2" in senv), (True, False))
        check("  and open with his key", "SMTP_PASS=hunter2" in workspace.read_secret(sdir / "site.env"), True)
        sa = (sdir / "config" / "gsc-service-account.local.json").read_bytes()
        check("his Search Console key is sealed", vault.is_sealed(sa), True)
        from sqlcipher3 import dbapi2
        db = sdir / "agency.db"
        check("his cabinet came across encrypted", b"Marina Hotel" in db.read_bytes(), False)
        c = dbapi2.connect(str(db))
        c.execute(f"PRAGMA key = \"x'{vault.cabinet_key(bytes.fromhex(made['key']), site)}'\"")
        check("  leads and all", c.execute("SELECT company FROM leads").fetchall(), [("Marina Hotel",)])
        c.close()
        check("removing the original needs his yes", (unasked["ok"], survived), (False, True))
        check("with it, the unencrypted original is gone",
              (done["ok"], (old / ".env").exists(), (old / "coordination" / "agency.db").exists()),
              (True, False, False))


def _port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class _Server:
    def __init__(self, data):
        self.data, self.port = data, _port()

    def __enter__(self):
        env = {k: v for k, v in os.environ.items() if k not in ("AGENCY_DB", "AGENCY_KEY")}
        env.update(PORT=str(self.port), AGENCY_DATA=str(self.data), AGENCY_IGNORE_DOTENV="1")
        self.proc = subprocess.Popen(["node", str(ROOT / "server.js")], cwd=str(ROOT), env=env,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(80):
            try:
                self.req("GET", "/api/ping")
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

    def req(self, method, path, body=None, cookie="", form=False):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=120)
        headers = {"Cookie": cookie} if cookie else {}
        if body is not None:
            if form:
                from urllib.parse import urlencode
                body = urlencode(body)
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            else:
                body = json.dumps(body)
                headers["Content-Type"] = "application/json"
        c.request(method, path, body=body, headers=headers)
        r = c.getresponse()
        text = r.read().decode("utf-8", "replace")
        jar = "; ".join(v.split(";")[0] for k, v in r.getheaders() if k.lower() == "set-cookie")
        c.close()
        try:
            data = json.loads(text)
        except Exception:
            data = {"_text": text}
        return r.status, data, jar


def test_the_real_server():
    print("\nThe real server: sign up, sign in, Setup asks for the account password, switch websites:")
    if not shutil.which("node"):
        print("  SKIP  Node is not installed here")
        return
    with _Place() as place, _Server(place.dir / "data") as s:
        st, j, _ = s.req("GET", "/api/state")
        check("before any account, the office opens as it always has", st, 200)
        st, j, cookie = s.req("POST", "/api/account/sign-up",
                              {"name": "Ammar", "password": "correct horse", "confirm": "correct horse"})
        check("signing up works", (st, j.get("ok")), (200, True))
        check("  and shows the recovery key once", len(j.get("recovery_key") or ""), 29)
        check("  but never the account's key", "key" in j, False)
        st, j, _ = s.req("GET", "/api/state")
        check("now the office is shut to anyone not signed in", st, 401)
        st, j, _ = s.req("GET", "/api/state", cookie=cookie)
        check("signed in with no website yet, it asks for one first", (st, j.get("needsSite")), (409, True))
        st, j, _ = s.req("POST", "/api/account/add-site",
                         {"name": "World Deal", "url": "https://www.worlddealtechnicalservices.com"}, cookie)
        check("a website is added", (st, j.get("ok")), (200, True))
        st, j, _ = s.req("GET", "/api/state", cookie=cookie)
        check("and the office opens on it", st, 200)
        senv = next((place.dir / "data" / "accounts").glob("*/sites/*/site.env")).read_bytes()
        check("its address was saved, sealed", vault.is_sealed(senv), True)
        st, page, _ = s.req("POST", "/api/setup/unlock", {"password": "wrong"}, cookie, form=True)
        check("Setup refuses a wrong password", st, 401)
        st, page, _ = s.req("POST", "/api/setup/unlock", {"password": "correct horse"}, cookie, form=True)
        check("and opens with the account password", st, 302)
        # The Users room and the Publisher card ask for it in place, and want an answer, not a page.
        st, j, _ = s.req("POST", "/api/setup/unlock", {"password": "wrong", "json": True}, cookie)
        check("asked in place, a wrong password gets a plain no", (st, j.get("ok")), (401, False))
        st, j, ticket = s.req("POST", "/api/setup/unlock", {"password": "correct horse", "json": True}, cookie)
        check("  and the right one a plain yes, with the Setup ticket", (st, j.get("ok"), "glasswork_setup=" in ticket),
              (200, True, True))
        st, j, _ = s.req("POST", "/api/account/add-site",
                         {"name": "Second Shop", "url": "https://secondshop.ae"}, cookie)
        second = j.get("site", {}).get("id")
        st, j, _ = s.req("POST", "/api/account/add-site", {"name": "Third", "url": "https://third.ae"}, cookie)
        check("a third website is refused", st, 400)
        st, j, _ = s.req("POST", "/api/account/open-site", {"site": second}, cookie)
        check("switching to the second works", (st, j.get("ok")), (200, True))
        st, j, _ = s.req("GET", "/api/account/me", cookie=cookie)
        check("  and it is the open one", j.get("open"), second)
        st, j, _ = s.req("POST", "/api/account/sign-out", {}, cookie)
        check("signing out works", j.get("ok"), True)
        st, j, _ = s.req("GET", "/api/state", cookie=cookie)
        check("the old session no longer opens anything", st, 401)
        st, j, _ = s.req("POST", "/api/account/sign-in", {"account": "Ammar", "password": "nope"})
        check("a wrong password is refused", st, 401)
        st, j, cookie = s.req("POST", "/api/account/sign-in", {"account": "ammar", "password": "correct horse"})
        check("the right one signs in, whatever the case of the name", (st, j.get("ok")), (200, True))
        check("  back on the website open last time", j.get("site"), second)


def main():
    test_the_keys()
    missing = _libraries()
    if missing:
        print(f"\n  SKIP  the encryption libraries are not installed ({missing}) — "
              "run: pip install -r requirements.txt")
    else:
        for t in (test_the_cabinet_is_encrypted, test_the_server_and_python_read_each_others_files,
                  test_bringing_his_website_in, test_the_real_server):
            t()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        sys.exit(1)
    print("accounts: all good — each person's data opens only with their own key.")


if __name__ == "__main__":
    main()
