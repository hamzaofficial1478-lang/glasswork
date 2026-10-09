"""
publish_tests.py — run: python -m coordination.publish_tests   (python3 on macOS/Linux)

    "i do not want to make the users to be listed on my webiste i want the
     details to be on the program ... I want the Users to be stored on my pc in
     program storage not on my website hosting"

Glasswork Home (publish-to-your-site/glasswork-home/index.php) on a real PHP
server on this machine, with the program on both sides of it: a person sending
a report, registering and reading the answer, and the publisher collecting,
answering and putting a version out. What has to hold:

  the website only ever holds SEALED envelopes — no name, email or report
    can be read from any file on it — and collecting empties it
  his records are on his computer, locked
  his answer reaches the person, sealed to their copy, and is then gone
  nobody without his key collects, answers or puts a version out
  one address cannot flood the postbox
  a version put out is signed, carries his Home's address and the public
    halves of his keys, leaves his side of the program out, and cannot go
    out twice

Nothing reaches the internet. His real settings are never touched: the
publisher file, the keys and the data folder are all temporary.
"""
import io
import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

from . import product, publisher, sealed, support, updates, workspace

ROOT = Path(__file__).parents[1]
_fail = []


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


def _port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


class Home:
    """His website with a Glasswork Home on it, and this program set up as the publisher."""

    def __enter__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="home-"))
        self.web = self.dir / "public_html"
        self.web.mkdir()
        self.env = {k: os.environ.get(k) for k in ("GLASSWORK_PUBLISHER_FILE", "GLASSWORK_SIGNING_KEY",
                                                   "AGENCY_DATA", "GLASSWORK_HOME", "GLASSWORK_POSTBOX_KEY")}
        os.environ["GLASSWORK_PUBLISHER_FILE"] = str(self.dir / "publisher.local.json")
        os.environ["GLASSWORK_SIGNING_KEY"] = str(self.dir / "keys" / "signing.key")
        os.environ["AGENCY_DATA"] = str(self.dir / "data")
        os.environ.pop("GLASSWORK_HOME", None)
        os.environ.pop("GLASSWORK_POSTBOX_KEY", None)
        self.port = _port()
        self.url = f"http://127.0.0.1:{self.port}"
        made = publisher.setup(self.url, "owner@example.com")
        assert made.get("ok"), made
        z = publisher.home_zip()
        zipfile.ZipFile(io.BytesIO(z["zip"])).extractall(self.web)
        self.proc = subprocess.Popen(["php", "-S", f"127.0.0.1:{self.port}", "-t", str(self.web)],
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=0.5).close()
                break
            except OSError:
                time.sleep(0.2)
        return self

    def __exit__(self, *a):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.dir, ignore_errors=True)

    def raw(self, path, body=None):
        req = urllib.request.Request(self.url + path, method="POST" if body is not None else "GET",
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, (e.read() or b"").decode("utf-8", "replace")

    def website_text(self) -> str:
        """Every byte the Home keeps on the website, as one text."""
        return "".join(p.read_text(encoding="utf-8", errors="replace")
                       for p in (self.web / "glasswork-home").rglob("*") if p.is_file())

    def waiting(self) -> int:
        return len(list((self.web / "glasswork-home" / "private" / "postbox").glob("*.php")))

    def letters(self) -> int:
        return len(list((self.web / "glasswork-home" / "private").glob("mail/*/*.php")))


def test_a_report_and_its_answer():
    print("\nA report reaches him sealed, lives on his computer, and his answer reaches the person:")
    with Home() as h:
        check("the Home answers and knows him", publisher.check_home().get("ok"), True)
        r = support.send("The floor does not load", "It spins for ever on the Agency screen.", "me@example.com", True)
        check("the report is sent", r.get("ok"), True)
        rid = r.get("id")
        check("  the person sees it, waiting to be read", support.mine()["reports"][0]["status"], "new")
        site = h.website_text()
        check("on the website it is one sealed envelope", h.waiting(), 1)
        check("  and nothing of it can be read there — not the words, not the email",
              any(w in site for w in ("floor does not load", "It spins", "me@example.com")), False)
        got = publisher.collect()
        check("his program collects it", (got.get("ok"), got.get("new_reports")), (True, 1))
        check("  and the website keeps nothing afterwards", h.waiting(), 0)
        locked = publisher.records_file()
        check("it is kept on his computer, in his program's own data folder",
              locked.exists() and locked.is_relative_to(Path(os.environ["AGENCY_DATA"])), True)
        check("  locked: the file shows nothing of it", b"floor does not load" in locked.read_bytes(), False)
        inbox = publisher.inbox()
        check("he sees it in his inbox, new", (inbox.get("new"), inbox["reports"][0]["title"]), (1, "The floor does not load"))
        full = publisher.read(rid)["report"]
        check("  in full, with the support report", "Version" in full.get("support", "") or len(full.get("support", "")) > 50, True)
        check("  and the person's mailbox details are not shown to him", ("box" in full, "box_pub" in full), (False, False))
        a = publisher.answer(rid, "fixed", "Thanks — fixed in the next version.", "99.0.0")
        check("his answer goes out", (a.get("ok"), a.get("sent")), (True, True))
        check("  sealed on the website: his words cannot be read there",
              "fixed in the next version" in h.website_text(), False)
        mine = support.mine()["reports"][0]
        check("the person reads his answer", (mine["status"], mine["reply"]), ("fixed", "Thanks — fixed in the next version."))
        check("  and is told to update to get it", mine.get("update_needed"), True)
        check("  and the answer is gone from the website once read", h.letters(), 0)


def test_nobody_else_gets_in():
    print("\nNobody without his key collects, answers or publishes — and the website holds nothing usable:")
    with Home() as h:
        support.send("Private", "My leads list is empty.", "", False)
        code, _ = h.raw("/glasswork-home/index.php?do=collect")
        check("collecting is refused to a stranger", code, 401)
        code, _ = h.raw("/glasswork-home/index.php?do=ack", {"ids": ["0000000000-000000000000"]})
        check("so is telling the Home to delete", code, 401)
        code, _ = h.raw("/glasswork-home/index.php?do=reply", {"box": "a" * 32, "sealed": "gw1." + "A" * 80})
        check("so is posting an answer", code, 401)
        name = next((h.web / "glasswork-home" / "private" / "postbox").glob("*.php")).name
        code, body = h.raw(f"/glasswork-home/private/postbox/{name}")
        check("an envelope asked for by its address shows nothing", "gw1." in body, False)
        code, _ = h.raw("/glasswork-home/index.php?do=drop", {"sealed": "My name is Aisha and my email is a@b.c"})
        check("anything that is not sealed is refused", code, 400)
        key = product.publisher()["home_key"]
        check("the website holds a fingerprint of his key, not the key", key in h.website_text(), False)
        code, body = h.raw("/glasswork-home/index.php?do=mail", {"box": "0" * 32})
        check("guessing a mailbox number finds nothing", json.loads(body).get("letters"), [])
        stolen = (h.web / "glasswork-home" / "private" / "postbox" / name).read_text(encoding="utf-8").split("\n", 1)[1]
        thief, _ = sealed.keypair()
        try:
            sealed.open_(thief, json.loads(stolen)["sealed"])
            opened = True
        except ValueError:
            opened = False
        check("someone who copies the website's files cannot open an envelope", opened, False)

    print("\nOne address cannot flood the postbox:")
    with Home() as h:
        sent = [support.send(f"Report {i}", "text", "", False).get("ok") for i in range(11)]
        check("ten in an hour, then it asks them to wait", sent, [True] * 10 + [False])


def test_your_users():
    """
    "where i will get the user details and their issues? like will i have any
     of my room where i will be able to manage the my users their issues?"
    """
    print("\nHis Users room, from his own records: every copy in use, who registered, their issues:")
    with Home() as h:
        asked = []
        real_git = updates.is_git_copy
        updates.is_git_copy = lambda: False
        try:
            updates.check(lambda url, limit, timeout=30: asked.append(url) or b'{"version": "0.0.1"}')
        finally:
            updates.is_git_copy = real_git
        check("the update check asks plainly, with nothing about the copy in it",
              (len(asked), support.install_id() in asked[0], "copy=" in asked[0]), (1, False, False))
        check("  and 'this copy is in use' went sealed to the postbox instead", h.waiting(), 1)
        check("  once a day, not every time", support.say_in_use().get("sent"), False)
        publisher.collect()
        us = publisher.users()
        check("the copy is counted", (us["counts"]["copies"], us["users"][0]["id"]), (1, support.install_id()))
        check("  but nobody's name is known until they register", us["users"][0].get("person"), None)
        r = support.register({"name": "Aisha Khan", "business": "Khan Glazing", "email": "aisha@example.com",
                              "phone": "+971 4 000 0000", "website": "khanglazing.ae", "country": "UAE"})
        check("a person registers", r.get("ok"), True)
        support.send("Leads will not open", "It spins.", "", False)
        check("  and their name is not on the website", any(w in h.website_text() for w in ("Aisha", "aisha@example.com",
                                                                                         "Khan Glazing")), False)
        publisher.collect()
        us = publisher.users()
        person = us["users"][0]
        check("he sees who they are", ((person.get("person") or {}).get("name"), (person.get("person") or {}).get("email")),
              ("Aisha Khan", "aisha@example.com"))
        check("  with their open issue counted against them", person["reports"], {"all": 1, "open": 1})
        one = publisher.user(person["id"])
        check("opening them shows their issues", [x["title"] for x in one["reports"]], ["Leads will not open"])
        check("the tiles add up", (us["counts"]["registered"], us["counts"]["open_issues"]), (1, 1))
        w = support.register({"withdraw": True})
        publisher.collect()
        check("a person can withdraw, and his records forget their details",
              (w.get("ok"), publisher.users()["users"][0].get("person")), (True, None))
        check("  and their copy says it is not registered any more", support.registration()["registered"], False)
        support.set_counting(False)
        d = support._copy()
        d.pop("seen_day", None)
        support._save_copy(d)
        check("with counting switched off, the copy says nothing", support.say_in_use().get("sent"), False)
        support.set_counting(True)
        f = publisher.forget(person["id"], with_reports=True)
        check("he can remove someone, and their issues", (f.get("ok"), f.get("reports_removed"),
                                                           publisher.users()["counts"]["copies"]), (True, 1, 0))


def test_an_older_home():
    print("\nA Home of the older kind is found out, and its readable records come home and leave the website:")
    with Home() as h:
        priv = h.web / "glasswork-home" / "private"
        for sub, name, rec in (("copies", "abcdef0123456789abcd.php",
                                {"id": "abcdef0123456789abcd", "first": 1, "last": 2, "version": "0.6.0", "system": "",
                                 "person": {"name": "Old Timer", "email": "old@example.com"}}),
                               ("reports", "250101-abcdef.php",
                                {"id": "250101-abcdef", "at": 3, "title": "Old problem", "text": "From before.",
                                 "status": "seen", "reply": "", "fixed_in": "", "copy": "abcdef0123456789abcd"})):
            (priv / sub).mkdir(parents=True, exist_ok=True)
            (priv / sub / name).write_text("<?php exit; ?>\n" + json.dumps(rec), encoding="utf-8")
        publisher.collect()
        check("the old records are now on his computer",
              ((publisher.users()["users"][0].get("person") or {}).get("name"), publisher.inbox()["reports"][0]["title"]),
              ("Old Timer", "Old problem"))
        check("  and no longer on the website", "Old Timer" in h.website_text() or "Old problem" in h.website_text(), False)
        index = h.web / "glasswork-home" / "index.php"
        index.write_text(index.read_text(encoding="utf-8").replace("const HOME_VERSION = 3;", "const HOME_VERSION = 2;"),
                         encoding="utf-8")
        old = publisher.check_home()
        check("a Home older than this is not trusted with anything", (old.get("ok"), old.get("old_home")), (False, True))


def test_never_on_a_clients_website():
    """
    "why should i gave my website link to others while am managing the website
     of my clinet that is not my setup website the program is mine not the website"
    """
    print("\nThe program's Home never goes on a website the program looks after for a client:")
    with Home():
        acct = workspace.create_account("Client office")["account"]
        workspace.add_site(acct["id"], "A client", "https://www.client-glazing.example")
        r = publisher.setup("https://client-glazing.example", "")
        check("a client's website is refused for the Home", (r.get("ok"), r.get("client_site")), (False, True))
        check("  and nothing about the Home changed", product.publisher()["public"]["home_url"].startswith("http://127.0.0.1"),
              True)
        check("the program's own website is still fine", publisher.setup(product.publisher()["public"]["home_url"]
                                                                          .rsplit("/glasswork-home", 1)[0]).get("ok"), True)


def test_putting_a_version_out():
    print("\nA version put out is signed, knows his Home, and leaves his side of the program out:")
    if not (ROOT / ".git").exists():
        print("  SKIP  a version is put out from a git copy")
        return
    with Home() as h:
        r = publisher.publish("Faster screens.")
        check("it goes out", r.get("ok"), True)
        m = json.loads(h.raw("/glasswork-home/index.php?do=latest")[1])
        check("the Home now offers it", m.get("version"), product.version())
        check("  signed with his key", updates.verify(m, product.info()["update_key"]), True)
        files = list((h.web / "glasswork-home" / "updates").glob("*.zip"))
        with zipfile.ZipFile(files[0]) as z:
            names = z.namelist()
            shipped = json.loads(z.read(next(n for n in names if n.endswith("config/product.json"))))
        check("the release tells every copy where its Home is, and how to seal for him",
              (shipped.get("home_url"), bool(shipped.get("update_key")), shipped.get("postbox_key")),
              (h.url + "/glasswork-home", True, product.publisher()["public"]["postbox_key"]))
        check("  only the PUBLIC halves: his private postbox key is nowhere in it",
              publisher._postbox_private() in json.dumps(shipped), False)
        inner = ["/".join(n.split("/")[1:]) for n in names]
        his = sorted({n for n in inner if product.publisher_only(n)})
        check("his side of the program is not in it (users room, publishing, the Home, his guides)", his, [])
        check("  but everything a copy needs to send him a report is",
              all(n in inner for n in ("coordination/support.py", "coordination/sealed.py", "server.js", "ui/app.html")),
              True)
        check("  his own product.json is untouched",
              "home_url" in json.loads(product.FILE.read_text(encoding="utf-8")), False)
        check("  and nothing private rode along", any(n.endswith((".local.json", ".env", ".key")) for n in names), False)
        again = publisher.publish("Again.")
        check("the same version cannot go out twice", (again.get("ok"), "already out" in again.get("error", "")), (False, True))
        page = h.raw("/glasswork-home/")[1]
        check("the download page is there for new people", "Glasswork" in page, True)


def main():
    if not shutil.which("php"):
        print("SKIPPED: PHP is not installed here, so the Home cannot be run.")
        return 0
    for t in (test_a_report_and_its_answer, test_nobody_else_gets_in, test_your_users, test_an_older_home,
              test_never_on_a_clients_website, test_putting_a_version_out):
        t()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        return 1
    print("publish: all good — sealed on their computer, kept on his, nothing readable on the website.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
