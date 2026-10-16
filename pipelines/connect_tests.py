"""
connect_tests.py — run: python -m pipelines.connect_tests

"The website connection gets much easier": your own connector with its secret
already inside, a WordPress Application Password, or read-only.

What is protected here:
  - the connector zip is made for this website: the connector, its secret in
    token.php, and plain instructions; the address and secret are saved as it
    is made, and a second download keeps the same secret so a connector
    already on the website keeps working;
  - the zip, extracted on a real PHP server, answers Check — and only then is
    full access switched on;
  - a WordPress site answers every call the agency makes, through WordPress's
    own REST API: pages read and changed (the old version kept as a revision
    and put back on undo), structured data placed once and replaced in place,
    posts added and only the agency's own taken away, the home page never
    removed;
  - the Application Password goes in the Authorization header and nowhere
    else, and never comes back out of the status;
  - read-only is one press, and the office keeps all of this behind the lock.

Nothing here reaches a real website: a stand-in WordPress and PHP's own test
server, both on this computer.
"""
import base64
import io
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ.setdefault("AGENCY_DB", str(Path(tempfile.mkdtemp(prefix="connect-db-")) / "agency.db"))

from coordination import workspace  # noqa: E402

from . import connect, site_api, wp_bridge  # noqa: E402

ROOT = Path(__file__).parents[1]
_fail = []
KEEP = ("AGENCY_DATA", "AGENCY_IGNORE_DOTENV", "AGENCY_KEY", "SITE_URL", "SITE_MODE",
        "SITE_API_BASE", "SITE_API_TOKEN", "SITE_CONNECT", "WP_USER", "WP_APP_PASSWORD",
        "NO_PROXY", "no_proxy")


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


class _Place:
    """A data folder and an account of their own; settings read from its files."""

    def __init__(self, isolated=False):
        self.isolated = isolated

    def __enter__(self):
        self.saved = {k: os.environ.get(k) for k in KEEP}
        self.dir = Path(tempfile.mkdtemp(prefix="connect-"))
        os.environ["AGENCY_DATA"] = str(self.dir / "data")
        for k in KEEP[2:10]:
            os.environ.pop(k, None)
        os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
        if self.isolated:
            os.environ["AGENCY_IGNORE_DOTENV"] = "1"
        else:
            os.environ.pop("AGENCY_IGNORE_DOTENV", None)
        return self

    def __exit__(self, *a):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def account(self, url):
        made = workspace.create_account("Shop")
        acct = made["account"]["id"]
        site = workspace.add_site(acct, "Shop", url)["site"]["id"]
        workspace.open_site(acct, site)
        return acct, site

    def env(self):
        from providers import integrations
        return integrations.read_env()


# ---------------------------------------------------------------------------
# a stand-in WordPress
# ---------------------------------------------------------------------------

class FakeWP:
    USER, PASSWORD = "owner", "abcd efgh ijkl mnop qrst uvwx"

    def __init__(self):
        self.pages = {
            10: {"id": 10, "slug": "home", "status": "publish", "content": "<p>Welcome</p>", "revs": []},
            11: {"id": 11, "slug": "contact", "status": "publish",
                 "content": "<p>Call 04 000 0000</p>", "revs": []},
        }
        self.posts = {50: {"id": 50, "slug": "old-news", "status": "publish", "content": "x"}}
        self.next_id = 100
        self.seen = []
        self.port = _port()

    def _ok_auth(self, header):
        want = "Basic " + base64.b64encode(
            f"{self.USER}:{wp_bridge.normalise_password(self.PASSWORD)}".encode()).decode()
        return header == want

    def start(self):
        wp = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                raw = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def _body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return json.loads(self.rfile.read(n) or b"{}") if n else {}

            def _route(self, method):
                wp.seen.append({"method": method, "path": self.path,
                                "auth": self.headers.get("Authorization") or ""})
                u = urllib.parse.urlsplit(self.path)
                q = dict(urllib.parse.parse_qsl(u.query))
                path = u.path
                if not path.startswith("/wp-json/wp/v2"):
                    return self._send(404, {"code": "rest_no_route", "message": "No route"})
                if not wp._ok_auth(self.headers.get("Authorization") or ""):
                    return self._send(401, {"code": "incorrect_password",
                                            "message": "The provided password is an invalid application password."})
                path = path[len("/wp-json/wp/v2"):]
                if path == "/users/me":
                    return self._send(200, {"id": 1, "name": "Owner", "slug": "owner",
                                            "capabilities": {"edit_pages": True, "publish_posts": True,
                                                             "upload_files": True, "unfiltered_html": True}})
                if path == "/settings":
                    return self._send(200, {"page_on_front": 10, "show_on_front": "page"})
                m = re.match(r"^/(pages|posts)(?:/(\d+))?(/revisions)?$", path)
                if not m:
                    return self._send(404, {"message": "No route"})
                kind, pid, revs = m.group(1), m.group(2), m.group(3)
                store = wp.pages if kind == "pages" else wp.posts
                view = lambda p: {"id": p["id"], "slug": p["slug"], "status": p["status"],
                                  "modified": "2026-10-08T10:00:00",
                                  "link": f"http://127.0.0.1:{wp.port}/{p['slug']}/",
                                  "content": {"raw": p["content"], "rendered": p["content"]}}
                if pid is None:
                    if method == "GET":
                        rows = list(store.values())
                        if q.get("slug"):
                            rows = [p for p in rows if p["slug"] == q["slug"]]
                        return self._send(200, [view(p) for p in rows])
                    if method == "POST":
                        b = self._body()
                        wp.next_id += 1
                        store[wp.next_id] = {"id": wp.next_id, "slug": b.get("slug"),
                                             "status": b.get("status"), "content": b.get("content"),
                                             "revs": []}
                        return self._send(201, view(store[wp.next_id]))
                p = store.get(int(pid))
                if p is None:
                    return self._send(404, {"message": "Invalid post ID."})
                if revs:
                    # newest first: the current content, then each earlier one
                    rows = [{"id": 900 + i, "modified": f"2026-10-0{8 - i}T09:00:00",
                             "content": {"raw": c}} for i, c in enumerate([p["content"]] + p["revs"][::-1])]
                    return self._send(200, rows)
                if method == "GET":
                    return self._send(200, view(p))
                if method == "POST":
                    b = self._body()
                    p.setdefault("revs", []).append(p["content"])
                    p["content"] = b.get("content", p["content"])
                    return self._send(200, view(p))
                if method == "DELETE":
                    p["status"] = "trash"
                    return self._send(200, view(p))
                return self._send(405, {"message": "no"})

            def do_GET(self):
                self._route("GET")

            def do_POST(self):
                self._route("POST")

            def do_DELETE(self):
                self._route("DELETE")

        self.srv = ThreadingHTTPServer(("127.0.0.1", self.port), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self.srv.shutdown()

    @property
    def site(self):
        return f"http://127.0.0.1:{self.port}"


def _s(wp, user=None, password=None):
    return {"mode": "full", "base": "", "token": "", "site": wp.site, "kind": "wordpress",
            "wp_user": user or wp.USER,
            "wp_pass": wp_bridge.normalise_password(password or wp.PASSWORD)}


# ---------------------------------------------------------------------------

def test_your_own_connector():
    print("\nYour own connector: made for this website, its secret already inside:")
    with _Place() as p:
        p.account("https://www.pipes.co.uk/services")
        r = connect.connector_zip()
        check("it is made", (r["ok"], r["filename"]), (True, "connector-www.pipes.co.uk.zip"))
        z = zipfile.ZipFile(io.BytesIO(r["zip"]))
        check("the connector, its secret and the instructions",
              sorted(z.namelist()), ["READ-ME-FIRST.txt", "agency-api/index.php", "agency-api/token.php"])
        env = p.env()
        token = env.get("SITE_API_TOKEN", "")
        check("a long secret, made fresh", len(token) >= 40, True)
        check("token.php hands the connector that secret, and shows a browser nothing",
              z.read("agency-api/token.php").decode(), "<?php\nreturn '" + token + "';\n")
        check("the address is saved: the website plus /agency-api",
              env.get("SITE_API_BASE"), "https://www.pipes.co.uk/agency-api")
        check("  and the connector names this website",
              "FULL ACCESS for www.pipes.co.uk" in z.read("agency-api/index.php").decode(), True)
        check("full access is not switched on until Check proves it", env.get("SITE_MODE") in (None, "", "readonly"),
              True)
        again = connect.connector_zip()
        check("a second download keeps the same secret", (again["reused"], p.env().get("SITE_API_TOKEN")),
              (True, token))
        fresh = connect.connector_zip(new_secret=True)
        check("a new secret only when asked", fresh["ok"] and p.env().get("SITE_API_TOKEN") != token, True)
        out = subprocess.run([sys.executable, "-m", "pipelines.connect", "--zip-json"], cwd=str(ROOT),
                             capture_output=True, text=True, encoding="utf-8", timeout=60,
                             env=dict(os.environ))
        got = json.loads(out.stdout.strip().splitlines()[-1])
        secret = p.env().get("SITE_API_TOKEN")
        check("the office gets the zip, and the secret only inside it",
              (got["ok"], secret in json.dumps({k: v for k, v in got.items() if k != "zip_b64"})),
              (True, False))
    with _Place() as p:
        workspace.create_account("Blank")
        acct = workspace.accounts()[0]["id"]
        site = workspace.add_site(acct, "Blank", "")["site"]["id"]
        workspace.open_site(acct, site)
        r = connect.connector_zip()
        check("with no website address it says so", (r["ok"], "address" in r.get("error", "")), (False, True))


def test_the_zip_on_a_real_php_server():
    print("\nThe zip, extracted on a real PHP server, answers Check:")
    if not shutil.which("php"):
        print("  SKIP  PHP is not installed here")
        return
    with _Place() as p:
        port = _port()
        p.account(f"http://127.0.0.1:{port}")
        r = connect.connector_zip()
        web = p.dir / "public_html"
        web.mkdir()
        (web / "index.php").write_text("<?php echo 'home';", encoding="utf-8")
        zipfile.ZipFile(io.BytesIO(r["zip"])).extractall(web)
        proc = subprocess.Popen(["php", "-S", f"127.0.0.1:{port}", "-t", str(web)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(40):
                try:
                    socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
                    break
                except OSError:
                    time.sleep(0.25)
            got = connect.check()
            check("Check knocks, and the connector answers", (got["ok"], got["kind"]), (True, "php"))
            check("  so full access is switched on", p.env().get("SITE_MODE"), "full")
            check("  and the office says it is connected", connect.status()["connected"], True)
            png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
            pic = site_api.upload_picture(base64.b64encode(png).decode(), name="Shower screen", agent=site_api.AGENT)
            saved = list((web / "agency-pictures").glob("*.png")) if (web / "agency-pictures").exists() else []
            check("a picture for inside a post is saved into agency-pictures/, and its address comes back",
                  (pic.get("ok"), len(saved), pic.get("path", "").startswith("agency-pictures/shower-screen-")), (True, 1, True))
            check("  byte for byte", saved[0].read_bytes() == png if saved else False, True)
            check("  and no other desk may do it", site_api.upload_picture(base64.b64encode(png).decode(),
                                                                          agent="content-writer")["ok"], False)
            (web / "agency-api" / "token.php").write_text("<?php\nreturn 'someone-else-entirely-0000000000';\n",
                                                            encoding="utf-8")
            bad = connect.check()
            check("a connector with a different secret is refused", bad["ok"], False)
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except Exception:
                proc.kill()


def test_wordpress_answers_every_call():
    print("\nA WordPress site answers the agency's calls through its own REST API:")
    wp = FakeWP().start()
    try:
        with _Place(isolated=True) as p:
            p.account(wp.site)
            s = _s(wp)
            h = site_api.call("/health", s=s)
            check("health: who, and what they may do", (h["ok"], h["user"], "pages" in h["can"]),
                  (True, "Owner", True))
            bad = site_api.call("/health", s=_s(wp, password="wrong password here x"))
            check("a wrong Application Password is refused in plain words",
                  (bad["ok"], "Application Password" in bad["error"]), (False, True))
            listed = site_api.pages(s=s)
            check("the pages, by their address", sorted(x["page"] for x in listed["pages"]), ["contact", "home"])
            got = site_api.read_page("contact", s=s)
            check("one page's content", got.get("html"), "<p>Call 04 000 0000</p>")
            check("  a .php name finds the same page", site_api.read_page("contact.php", s=s).get("html"),
                  "<p>Call 04 000 0000</p>")
            check("the home page is the one WordPress shows first",
                  site_api.call("/pages/index.php", s=s).get("html"), "<p>Welcome</p>")
            w = site_api.call("/pages", "POST", {"page": "contact", "html": "<p>Call +971 4 000 0000</p>"}, s=s)
            check("a page is changed, the old version kept by WordPress",
                  (w["ok"], w["action"], wp.pages[11]["revs"]), (True, "updated", ["<p>Call 04 000 0000</p>"]))
            same = site_api.call("/pages", "POST", {"page": "contact", "html": "<p>Call +971 4 000 0000</p>"}, s=s)
            check("writing the same words again is not a change", same["action"], "unchanged")
            undo = site_api.call("/rollback", "POST", {"page": "contact"}, s=s)
            check("undo puts the previous version back", (undo["ok"], wp.pages[11]["content"]),
                  (True, "<p>Call 04 000 0000</p>"))
            hb = site_api.call("/head", "POST", {"page": "contact", "id": "localbusiness",
                                                 "html": '<script type="application/ld+json">{"a":1}</script>'}, s=s)
            hb2 = site_api.call("/head", "POST", {"page": "contact", "id": "localbusiness",
                                                  "html": '<script type="application/ld+json">{"a":2}</script>'}, s=s)
            body = wp.pages[11]["content"]
            check("structured data is placed once, and replaced in place",
                  (hb["ok"], hb2["ok"], body.count("<!-- agency:localbusiness -->"), '{"a":2}' in body,
                   '{"a":1}' in body), (True, True, 1, True, False))
            new = site_api.call("/pages", "POST", {"page": "pergolas.php", "html": "<p>Pergolas</p>"}, s=s)
            check("a new page is added at its address", (new["ok"], new["action"], new["page"]),
                  (True, "created", "pergolas"))
            post = site_api.call("/blog", "POST", {"post": {"post_title": "Shower glass in a villa",
                                                            "post_slug": "shower-glass-villa",
                                                            "post_content": "<p>Real job.</p>"}}, s=s)
            check("a post is added", (post["ok"], post["slug"]), (True, "shower-glass-villa"))
            again = site_api.call("/blog", "POST", {"post": {"title": "Shower glass in a villa",
                                                             "slug": "shower-glass-villa",
                                                             "content": "<p>Real job.</p>"}}, s=s)
            check("  not twice at the same address", again.get("taken"), True)
            check("  and its address is known to be taken",
                  site_api.call("/blog/shower-glass-villa", s=s).get("taken"), True)
            not_ours = site_api.call("/blog-remove", "POST", {"id": "50"}, s=s)
            check("a post the agency did not add is left alone", not_ours["ok"], False)
            ours = site_api.call("/blog-remove", "POST", {"id": str(post["id"])}, s=s)
            check("  its own goes to the WordPress bin", (ours["ok"], wp.posts[post["id"]]["status"]),
                  (True, "trash"))
            home = site_api.call("/pages-remove", "POST", {"page": "index.php"}, s=s)
            check("the home page is never removed", home["ok"], False)
            gone = site_api.call("/pages-remove", "POST", {"page": "pergolas"}, s=s)
            check("another page goes to the bin, where it can be restored",
                  (gone["ok"], gone["backup"]), (True, "WordPress bin"))
            check("the contact-form test says plainly it is not available here",
                  site_api.call("/contact", "POST", {}, s=s)["ok"], False)
            secret = wp_bridge.normalise_password(wp.PASSWORD)
            check("the password never appears in an address", any(secret in x["path"] for x in wp.seen), False)
            check("  only in the Authorization header",
                  all(x["auth"].startswith("Basic ") for x in wp.seen), True)
    finally:
        wp.stop()


def test_connecting_wordpress_and_read_only():
    print("\nConnecting WordPress from the office, and read-only in one press:")
    wp = FakeWP().start()
    try:
        with _Place() as p:
            p.account(wp.site)
            r = connect.use_wordpress("owner", "short")
            check("something that is not an Application Password is refused", r["ok"], False)
            r = connect.use_wordpress("owner", "wrong wrong wrong wrong wrong wron")
            check("a wrong one is checked and refused, in plain words",
                  (r["ok"], "Application Password" in r.get("error", "")), (False, True))
            check("  and full access stays off", p.env().get("SITE_MODE") in (None, "", "readonly"), True)
            r = connect.use_wordpress("owner", FakeWP.PASSWORD)
            check("the right one connects", (r["ok"], r["kind"], r["mode"]), (True, "wordpress", "full"))
            st = connect.status()
            check("the office sees it connected, as whom", (st["connected"], st["wp_user"]), (True, "owner"))
            check("  and never the password", wp_bridge.normalise_password(FakeWP.PASSWORD) in json.dumps(st),
                  False)
            env_text = (workspace.site_dir(*[workspace.active()[k] for k in ("account", "site")])
                        / "site.env").read_text(encoding="utf-8")
            check("the password is the website's setting, not the person's",
                  "WP_APP_PASSWORD=" in env_text, True)
            ro = connect.use_readonly()
            check("read-only is one press", (ro["ok"], p.env().get("SITE_MODE")), (True, "readonly"))
            check("  and the office says so", connect.status()["connected"], False)
    finally:
        wp.stop()


def test_the_office_keeps_it_locked():
    print("\nThe office keeps the connection behind the Setup lock:")
    js = (ROOT / "server.js").read_text(encoding="utf-8")
    m = re.search(r"const SETUP_API = \[(.*?)\];", js, re.S)
    check("the connection routes need the Setup password", '"/api/connect"' in (m.group(1) if m else ""), True)
    check("the download is served as a file, not shown", 'attachment; filename=' in js, True)


def main():
    test_your_own_connector()
    test_the_zip_on_a_real_php_server()
    test_wordpress_answers_every_call()
    test_connecting_wordpress_and_read_only()
    test_the_office_keeps_it_locked()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        return 1
    print("All connection tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
