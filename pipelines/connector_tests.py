"""
connector_tests.py — run: python -m pipelines.connector_tests   (python3 on macOS/Linux)

    "the main thing is the website connectivity with program i want that thing
     to be more strong more fast more reliable errors free ... so the user will
     not face the error while he will be doing the multiple tasks with webiste"

The connector file (paste-into-your-site/agency-api.php, version 8) on a real
PHP server on this machine, driven through pipelines/site_api exactly as the
agency drives it. What has to hold:

  many pages come back in one request, not one request a page
  a change is written whole or not at all — no half page, no stray files
  a page edited after the agency read it is never overwritten
  two changes to the same page at the same moment both land
  a request too big for the host is refused in words, and nothing changes
  a page in an old encoding is read, and never garbled by a rewrite
  a host that drops the line once does not fail the request

Nothing reaches the internet; the website is a temporary folder.
"""
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from coordination import board
from . import site_api as S

ROOT = Path(__file__).parents[1]
TOKEN = "connector-test-token-" + "x" * 30
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


class Site:
    """A website folder with the connector in it, served by PHP."""

    def __init__(self, post_max="8M"):
        self.post_max = post_max

    def __enter__(self):
        self.dir = Path(tempfile.mkdtemp())
        self.web = self.dir / "public_html"
        (self.web / "agency-api").mkdir(parents=True)
        (self.web / "includes").mkdir()
        shutil.copy(ROOT / "paste-into-your-site" / "agency-api.php", self.web / "agency-api" / "index.php")
        (self.web / "agency-api" / "token.php").write_text(f"<?php\nreturn '{TOKEN}';\n", encoding="utf-8")
        for name, body in (("index.php", "<html><head><title>Home</title></head><body>Home</body></html>"),
                           ("about.php", "<html><head><title>About</title></head><body>About us</body></html>"),
                           ("contact.php", "<html><head><title>Contact</title></head><body>Call us</body></html>"),
                           ("includes/header.php", "<header>Glass and aluminium</header>")):
            (self.web / name).write_text(body, encoding="utf-8")
        self.port = _port()
        env = dict(os.environ, PHP_CLI_SERVER_WORKERS="4")
        self.proc = subprocess.Popen(["php", "-d", f"post_max_size={self.post_max}", "-S",
                                      f"127.0.0.1:{self.port}", "-t", str(self.web)],
                                     env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            try:
                socket.create_connection(("127.0.0.1", self.port), timeout=0.5).close()
                break
            except OSError:
                time.sleep(0.2)
        self.s = {"mode": "full", "base": f"http://127.0.0.1:{self.port}/agency-api", "token": TOKEN,
                  "site": f"http://127.0.0.1:{self.port}", "kind": "php"}
        self.real_db = board.DB
        board.DB = str(self.dir / "agency.db")
        board.ensure_schema()
        S._SEEN.clear()
        S._VERSION.clear()
        return self

    def __exit__(self, *a):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except Exception:
            self.proc.kill()
        board.DB = self.real_db
        shutil.rmtree(self.dir, ignore_errors=True)

    def file(self, name):
        return (self.web / name).read_bytes()


def _counting():
    """Count the requests site_api sends, by wrapping its single sender."""
    real = S._send
    sent = []

    def counted(url, *a, **kw):
        sent.append(url.split("do=", 1)[-1].split("&", 1)[0])
        return real(url, *a, **kw)
    S._send = counted
    return sent, lambda: setattr(S, "_send", real)


def test_one_request_reads_many_pages():
    print("\nMany pages come back in one request:")
    with Site() as w:
        h = S.health(s=w.s)
        check("the connector says it is version 8", h.get("connector"), 8)
        check("  and what the host allows", bool((h.get("limits") or {}).get("post_max_size")), True)
        sent, undo = _counting()
        try:
            got = S.read_pages(["index.php", "about.php", "contact.php", "includes/header.php",
                                "missing.php", "admin/login.php"], s=w.s)
        finally:
            undo()
        check("every page asked for has an answer", len(got), 6)
        check("  the real ones read", [got[n]["ok"] for n in ("index.php", "about.php", "contact.php",
                                                               "includes/header.php")], [True] * 4)
        check("  a missing one says so", (got["missing.php"]["ok"], got["missing.php"].get("status")), (False, 404))
        check("  and an off-limits one is never asked for", "not the website" in got["admin/login.php"]["error"], True)
        check("in one request for the pages (and one to learn the version)", sent, ["health", "pages-many"])
        import hashlib
        check("each comes with a fingerprint of exactly what is on the site",
              got["about.php"]["sha256"], hashlib.sha256(w.file("about.php")).hexdigest())


def test_a_change_is_whole_and_never_overwrites_his():
    print("\nA change is written whole, and never over a page changed since it was read:")
    with Site() as w:
        page = S.read_page("about.php", s=w.s)
        new = page["html"].replace("About us", "About us — fifteen years of glass")
        r = S.put_page("about.php", new, s=w.s, before=page["html"])
        check("the change lands", (r.get("ok"), w.file("about.php").decode()), (True, new))
        check("  with no half-written files left beside it",
              [p.name for p in w.web.rglob(".agency-*.tmp")], [])
        mine = S.read_page("contact.php", s=w.s)
        (w.web / "contact.php").write_text(mine["html"].replace("Call us", "Call us on 04 123 4567"),
                                           encoding="utf-8")
        r = S.put_page("contact.php", mine["html"].replace("<title>Contact", "<title>Contact us"),
                       s=w.s, before=mine["html"])
        check("a page he edited in the meantime is left alone", (r.get("ok"), r.get("changed")), (False, True))
        check("  and his edit is still there", "04 123 4567" in w.file("contact.php").decode(), True)
        check("  and it says why in plain words", "changed on your website" in r.get("error", ""), True)
        again = S.read_page("contact.php", s=w.s)
        r = S.put_page("contact.php", again["html"].replace("<title>Contact", "<title>Contact us"),
                       s=w.s, before=again["html"])
        check("read again, the change goes on top of his", (r.get("ok"), "04 123 4567" in w.file("contact.php").decode(),
                                                           "<title>Contact us" in w.file("contact.php").decode()),
              (True, True, True))


def test_two_changes_at_once_both_land():
    print("\nTwo changes to the same page at the same moment both land:")
    with Site() as w:
        out = []

        def add(block):
            out.append(S.call("/head", "POST", {"page": "index.php", "id": block,
                                                 "html": f'<meta name="{block}" content="1">'}, s=w.s))
        threads = [threading.Thread(target=add, args=(b,)) for b in ("first-block", "second-block", "third-block")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        home = w.file("index.php").decode()
        check("each was accepted", [r.get("ok") for r in out], [True, True, True])
        check("  and every one is on the page",
              all(f'name="{b}"' in home for b in ("first-block", "second-block", "third-block")), True)


def test_too_big_is_said_and_changes_nothing():
    # The web server in front of PHP (LiteSpeed, nginx, Apache) is what refuses
    # a body over the host's limit, with a page of HTML and a 413 — PHP itself
    # never sees it. That is what this host does.
    print("\nA request too big for the host is refused in words:")
    import io
    import urllib.error
    with Site() as w:
        page = S.read_page("about.php", s=w.s)
        real = S.urllib.request.urlopen

        def host_refuses(req, *a, **kw):
            if getattr(req, "data", None) and len(req.data) > 8000:
                raise urllib.error.HTTPError(req.full_url, 413, "Request Entity Too Large", {},
                                             io.BytesIO(b"<html><h1>413 Request Entity Too Large</h1></html>"))
            return real(req, *a, **kw)
        S.urllib.request.urlopen = host_refuses
        try:
            r = S.put_page("about.php", page["html"] + ("<p>more</p>" * 3000), s=w.s, before=page["html"])
        finally:
            S.urllib.request.urlopen = real
        check("refused", (r.get("ok"), r.get("too_big")), (False, True))
        check("  naming the host's limit, in words", "post_max_size" in r.get("error", "")
              and "<h1>" not in r.get("error", ""), True)
        check("  and the page is as it was", w.file("about.php").decode(), page["html"])


def test_an_old_encoding_is_read_but_not_garbled():
    print("\nA page in an old encoding is read, and never garbled by a rewrite:")
    with Site() as w:
        (w.web / "old.html").write_bytes(b"<html><head></head><body>Caf\xe9 glass</body></html>")
        got = S.read_page("old.html", s=w.s)
        check("it reads (it used to come back as an empty answer)", got.get("ok"), True)
        check("  and says it is not clean text", got.get("text_ok"), False)
        r = S.put_page("old.html", got["html"].replace("glass", "glazing"), s=w.s, before=got["html"])
        check("a rewrite is refused", r.get("ok"), False)
        check("  and the page is byte for byte as it was",
              w.file("old.html"), b"<html><head></head><body>Caf\xe9 glass</body></html>")


def test_a_dropped_line_is_tried_again():
    print("\nA host that drops the line once does not fail the request:")
    with Site() as w:
        real, waits = S._send, S.RETRY_WAITS
        tries = []

        def flaky(*a, **kw):
            tries.append(1)
            if len(tries) == 1:
                return {"ok": False, "unreachable": True, "_passing": True,
                        "error": "could not reach your site: ConnectionResetError"}
            return real(*a, **kw)
        S._send, S.RETRY_WAITS = flaky, (0, 0)
        try:
            r = S.read_page("index.php", s=w.s)
        finally:
            S._send, S.RETRY_WAITS = real, waits
        check("it got the page", r.get("ok"), True)
        check("  on the second try", r.get("tries"), 2)

        def busy(*a, **kw):
            return {"ok": False, "status": 503, "_passing": True, "error": "busy"}
        S._send, S.RETRY_WAITS = busy, (0, 0)
        try:
            r = S.call("/blog", "POST", {"post": {"title": "x"}}, s=w.s)
        finally:
            S._send, S.RETRY_WAITS = real, waits
        check("a new blog post is not sent twice blind", (r.get("ok"), r.get("maybe_done"), r.get("tries")),
              (False, True, None))


def main():
    if not shutil.which("php"):
        print("SKIPPED: PHP is not installed here, so the connector cannot be run.")
        return 0
    for t in (test_one_request_reads_many_pages, test_a_change_is_whole_and_never_overwrites_his,
              test_two_changes_at_once_both_land, test_too_big_is_said_and_changes_nothing,
              test_an_old_encoding_is_read_but_not_garbled, test_a_dropped_line_is_tried_again):
        t()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        return 1
    print("connector: all good — whole changes, nothing overwritten, many pages in one go.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
