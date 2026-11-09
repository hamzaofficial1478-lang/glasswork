"""
publish_tests.py — run: python -m coordination.publish_tests   (python3 on macOS/Linux)

    "how user will send us reports about the program failure and how user will
     get the updates ... i do not want to track the user that using my program"

Glasswork Home (publish-to-your-site/glasswork-home/index.php) on a real PHP
server on this machine, with the program on both sides of it: a person sending
a report and reading the answer, and the developer collecting, answering and
putting a version out. What has to hold:

  the website only ever holds SEALED envelopes — no name, email or report
    can be read from any file on it — and collecting empties it
  nothing counts copies: a report carries no number for the copy, two reports
    from one person cannot be tied together, and looking for an update sends
    nothing at all
  his records are on his computer, locked
  his answer reaches the person, sealed to their report, and is then gone
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
import re
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
        # The person's own sent reports: kept in this test's folder, never in
        # the program's config/ (a copy with no account keeps them there).
        self.mine = support.MINE
        support.MINE = str(self.dir / "support-reports.local.json")
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
        support.MINE = self.mine
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


def test_nothing_tracks_anyone():
    """ "i do not want to track the user that using my program" """
    print("\nNothing counts or follows the people using the program:")
    with Home() as h:
        for title in ("First problem", "Second problem"):
            support.send(title, "words", "", False)
        priv = publisher._postbox_private()
        opened = [sealed.open_(priv, json.loads(f.read_text(encoding="utf-8").split("\n", 1)[1])["sealed"])
                  for f in sorted((h.web / "glasswork-home" / "private" / "postbox").glob("*.php"))]
        check("a report says what went wrong, and nothing that names the copy",
              sorted(opened[0]), sorted(["kind", "id", "title", "text", "contact", "version", "system", "support",
                                         "box", "box_pub", "at", "product"]))
        check("  two reports from one person share no number", (opened[0]["box"] != opened[1]["box"],
                                                                 opened[0]["box_pub"] != opened[1]["box_pub"]), (True, True))
        asked = []
        real_git = updates.is_git_copy
        updates.is_git_copy = lambda: False
        try:
            updates.check(lambda url, limit, timeout=30: asked.append(url) or b'{"version": "0.0.1"}')
        finally:
            updates.is_git_copy = real_git
        check("looking for an update asks the plain address, with nothing added",
              asked, [product.info()["update_url"]])
        check("  and posts nothing", h.waiting(), 2)
        check("the program has no way to register or count anyone",
              [f for f in ("install_id", "register", "say_in_use", "set_counting") if hasattr(support, f)], [])
        publisher.collect()
        st = publisher._load()
        check("his records hold the reports and nothing about copies", ("copies" in st, len(st["reports"])), (False, 2))
        st["copies"] = {"abc": {"person": {"name": "Old Timer"}}}
        publisher._save(st)
        publisher.collect()
        check("  a list of copies kept by an older version is dropped", "copies" in publisher._load(), False)


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
        code, body = h.raw("/glasswork-home/index.php?do=mail", {"boxes": ["0" * 32]})
        check("guessing a mailbox number finds nothing", json.loads(body).get("letters"), {})
        stolen = (h.web / "glasswork-home" / "private" / "postbox" / name).read_text(encoding="utf-8").split("\n", 1)[1]
        thief, _ = sealed.keypair()
        try:
            sealed.open_(thief, json.loads(stolen)["sealed"])
            opened = True
        except ValueError:
            opened = False
        check("someone who copies the website's files cannot open an envelope", opened, False)
        index = h.web / "glasswork-home" / "index.php"
        older = re.sub(r"const HOME_VERSION = \d+;", "const HOME_VERSION = 2;", index.read_text(encoding="utf-8"))
        index.write_text(older, encoding="utf-8")
        old = publisher.check_home()
        check("a Home older than this is not trusted with anything", (old.get("ok"), old.get("old_home")), (False, True))

    print("\nOne address cannot flood the postbox:")
    with Home() as h:
        sent = [support.send(f"Report {i}", "text", "", False).get("ok") for i in range(11)]
        check("ten in an hour, then it asks them to wait", sent, [True] * 10 + [False])


def test_never_on_a_clients_website():
    """
    "why should i gave my website link to others while am managing the website
     of my clinet that is not my setup website the program is mine not the website"
    """
    print("\nThe program's Home never goes on a website the program looks after for a client:")
    with Home():
        made = workspace.create_account("Client office", "maple river lantern")
        os.environ["AGENCY_KEY"] = made["key"]          # signed in, as the office runs it
        try:
            workspace.add_site(made["account"]["id"], "A client", "https://www.client-glazing.example")
            r = publisher.setup("https://client-glazing.example", "")
        finally:
            os.environ.pop("AGENCY_KEY", None)
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
        check("his side of the program is not in it (Reports room, publishing, the Home, his guides)", his, [])
        check("  but everything a copy needs to send him a report is",
              all(n in inner for n in ("coordination/support.py", "coordination/sealed.py", "server.js", "ui/app.html")),
              True)
        check("  his own product.json is untouched",
              "home_url" in json.loads(product.FILE.read_text(encoding="utf-8")), False)
        check("  and nothing private rode along", any(n.endswith((".local.json", ".env", ".key")) for n in names), False)
        again = publisher.publish("Again.")
        check("the same version cannot go out twice", (again.get("ok"), "already out" in again.get("error", "")), (False, True))
        page = h.raw("/glasswork-home/")[1]
        check("the download page is there for new people", product.name() in page, True)


class FakeGitHub:
    """GitHub's release API, on this computer: enough to make, fill and publish a release."""

    def __enter__(self):
        import http.server
        import threading
        from urllib.parse import urlparse, parse_qs
        me = self
        self.releases, self.seen_auth, self.next_id = {}, set(), 100
        self.token, self.private = "ghp_good", False

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body=None):
                raw = json.dumps(body if body is not None else {}).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _go(self):
                me.seen_auth.add(self.headers.get("Authorization", ""))
                if self.headers.get("Authorization") != "Bearer " + me.token:
                    return self._send(401, {"message": "Bad credentials"})
                u = urlparse(self.path)
                parts = [x for x in u.path.split("/") if x]
                n = int(self.headers.get("Content-Length") or 0)
                data = self.rfile.read(n) if n else b""
                if parts[:1] == ["uploads"]:
                    rel = me.releases.get(int(parts[5]))
                    rel["assets"][parse_qs(u.query)["name"][0]] = data
                    return self._send(201, {"ok": True})
                if parts[:1] != ["repos"]:
                    return self._send(404, {"message": "Not Found"})
                rest = parts[3:]
                if not rest:
                    return self._send(200, {"permissions": {"push": True}, "private": me.private})
                if rest == ["releases", "latest"]:
                    out = [r for r in me.releases.values() if not r["draft"]]
                    plain = lambda r: {k: v for k, v in r.items() if k != "assets"}
                    return self._send(200, plain(out[-1])) if out else self._send(404, {"message": "Not Found"})
                if rest[:2] == ["releases", "tags"]:
                    r = next((r for r in me.releases.values() if r["tag_name"] == rest[2]), None)
                    return self._send(200, {k: v for k, v in r.items() if k != "assets"}) if r \
                        else self._send(404, {"message": "Not Found"})
                if rest == ["releases"] and self.command == "POST":
                    b = json.loads(data)
                    me.next_id += 1
                    r = {**b, "id": me.next_id, "assets": {}, "html_url": "https://github.com/x/y/releases/tag/" + b["tag_name"]}
                    me.releases[me.next_id] = r
                    return self._send(201, {k: v for k, v in r.items() if k != "assets"})
                if rest[:1] == ["releases"] and len(rest) == 2:
                    rid = int(rest[1])
                    if self.command == "DELETE":
                        me.releases.pop(rid, None)
                        return self._send(204)
                    if self.command == "PATCH":
                        me.releases[rid].update(json.loads(data))
                        return self._send(200, {k: v for k, v in me.releases[rid].items() if k != "assets"})
                return self._send(404, {"message": "Not Found"})

            do_GET = do_POST = do_PATCH = do_DELETE = _go

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.dir = Path(tempfile.mkdtemp(prefix="gh-"))
        self.env = {k: os.environ.get(k) for k in ("GLASSWORK_PUBLISHER_FILE", "GLASSWORK_SIGNING_KEY",
                                                   "AGENCY_DATA", "GLASSWORK_GITHUB_API")}
        os.environ["GLASSWORK_PUBLISHER_FILE"] = str(self.dir / "publisher.local.json")
        os.environ["GLASSWORK_SIGNING_KEY"] = str(self.dir / "keys" / "signing.key")
        os.environ["AGENCY_DATA"] = str(self.dir / "data")
        os.environ["GLASSWORK_GITHUB_API"] = self.url
        return self

    def __exit__(self, *a):
        self.server.shutdown()
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.dir, ignore_errors=True)


def test_versions_go_out_on_github():
    print("\nA version goes out on GitHub, signed here, and every copy finds it there:")
    if not (ROOT / ".git").exists():
        print("  SKIP  a version is put out from a git copy")
        return
    with FakeGitHub() as gh:
        r = publisher.github_setup("techduo/botix", "ghp_wrong")
        check("a token GitHub refuses is not kept", (r.get("ok"), "token" in r.get("error", "")), (False, True))
        check("  and nothing was saved", publisher._pub().get("github_token"), None)
        gh.private = True
        r = publisher.github_setup("techduo/botix", "ghp_good")
        check("a private repository is refused: people could not download from it",
              (r.get("ok"), "private" in r.get("error", "")), (False, True))
        gh.private = False
        r = publisher.github_setup("https://github.com/techduo/botix.git", "ghp_good")
        check("a good token and a public repository are kept", (r.get("ok"), r.get("repo")), (True, "techduo/botix"))
        view = publisher.publisher_view()
        check("the token is never shown back", "ghp_good" in json.dumps(view), False)
        check("  the screen knows versions go out on GitHub", (view.get("github"), view.get("github_repo")),
              (True, "techduo/botix"))
        check("every copy made from now on looks for updates on GitHub",
              product.info()["update_url"], "https://github.com/techduo/botix/releases/latest/download/manifest.json")

        # A draft left by a try that stopped half way is replaced, not tripped over.
        gh.releases[1] = {"id": 1, "tag_name": "v" + product.version(), "draft": True, "assets": {}}
        r = publisher.publish("Faster screens.")
        check("it goes out", (r.get("ok"), "GitHub" in r.get("why", "")), (True, True))
        out = [x for x in gh.releases.values() if not x["draft"]]
        check("one release, published, the stale draft gone", (len(out), 1 in gh.releases), (1, False))
        rel = out[0]
        check("  named and tagged by the version", (rel["tag_name"], rel["name"]),
              ("v" + product.version(), f"{product.name()} {product.version()}"))
        check("  made as a draft and filled before it was published (latest never points at half a release)",
              rel.get("make_latest"), "true")
        check("  it carries the update zip and the manifest",
              sorted(rel["assets"]), sorted([f"{product.name().lower()}-{product.version()}.zip", "manifest.json"]))
        m = json.loads(rel["assets"]["manifest.json"])
        check("the manifest is signed with his key", updates.verify(m, product.info()["update_key"]), True)
        check("  and points at this release's own zip, not whatever is latest by then",
              m["zip"], f"https://github.com/techduo/botix/releases/download/v{product.version()}/"
                        f"{product.name().lower()}-{product.version()}.zip")
        import hashlib as _h
        zdata = rel["assets"][f"{product.name().lower()}-{product.version()}.zip"]
        check("  whose checksum matches", _h.sha256(zdata).hexdigest(), m["sha256"])
        with zipfile.ZipFile(io.BytesIO(zdata)) as z:
            names = z.namelist()
            shipped = json.loads(z.read(next(n for n in names if n.endswith("config/product.json"))))
        check("a copy installed from it asks GitHub for its updates, with his public key",
              (shipped.get("update_url"), shipped.get("update_key")),
              ("https://github.com/techduo/botix/releases/latest/download/manifest.json", product.info()["update_key"]))
        check("  and his side of the program is not in it", sorted({n for n in ("/".join(x.split("/")[1:]) for x in names)
                                                                     if product.publisher_only(n)}), [])
        check("  nor the signing key, nor the token",
              any(n.endswith((".key", ".local.json")) for n in names) or b"ghp_good" in zdata, False)
        check("the release page tells a new person which one file to download",
              f"{product.name().lower()}-{product.version()}-setup.exe" in rel["body"] and "Update now" in rel["body"], True)
        check("only his token was ever sent", gh.seen_auth - {"Bearer ghp_good", "Bearer ghp_wrong"}, set())

        # A copy one version behind sees it and can install it.
        real_git, real_info = updates.is_git_copy, product.info
        updates.is_git_copy = lambda: False
        product.info = lambda: {**real_info(), "version": "0.0.1"}
        try:
            seen = updates.check(lambda url, limit, timeout=30: rel["assets"]["manifest.json"])
        finally:
            updates.is_git_copy, product.info = real_git, real_info
        check("a copy on an older version is told, and may install it",
              (seen.get("available"), seen.get("signed"), seen.get("latest")), (True, True, product.version()))

        again = publisher.publish("Again.")
        check("the same version cannot go out twice", (again.get("ok"), "already out" in again.get("error", "")), (False, True))


def test_redirects_stay_on_https():
    print("\nAn update download is only followed to another https:// address:")
    import http.server
    import threading

    class R(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", "http://example.com/evil.zip")
            self.end_headers()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), R)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        try:
            updates._fetch(f"http://127.0.0.1:{srv.server_address[1]}/manifest.json", 1024)
            got = "followed"
        except ValueError as e:
            got = "https" in str(e)
        check("a redirect to plain http:// is refused", got, True)
    finally:
        srv.shutdown()


def main():
    if not shutil.which("php"):
        print("SKIPPED: PHP is not installed here, so the Home cannot be run.")
        return 0
    stray = ROOT / "config" / "support-reports.local.json"
    was_there = stray.exists()
    for t in (test_a_report_and_its_answer, test_nothing_tracks_anyone, test_nobody_else_gets_in,
              test_never_on_a_clients_website, test_putting_a_version_out, test_versions_go_out_on_github,
              test_redirects_stay_on_https):
        t()
    check("the tests wrote nothing into the program's own config folder", stray.exists() and not was_there, False)
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        return 1
    print("publish: all good — sealed on their computer, kept on his, nothing readable on the website, nobody counted.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
