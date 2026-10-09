"""
publish_tests.py — run: python -m coordination.publish_tests   (python3 on macOS/Linux)

    "i want the report section where user will be able to submit the report and
     those reports will get over to me i'll solve those to update program user
     will get update message"

Glasswork Home (publish-to-your-site/glasswork-home/index.php) on a real PHP
server on this machine, with the program on both sides of it: a person sending
a report and reading the answer, and the publisher reading it, answering it and
putting a version out. What has to hold:

  a report reaches the publisher, and his answer reaches the person
  nobody without his key reads a report, answers one, or puts a version out
  a report cannot be read by guessing its address
  one address cannot flood the inbox
  a version put out is signed, carries his Home's address, and cannot go out twice

Nothing reaches the internet. His real settings are never touched: the
publisher file, the signing key and the data folder are all temporary.
"""
import base64
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

from . import product, support, updates

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
                                                   "AGENCY_DATA", "GLASSWORK_HOME")}
        os.environ["GLASSWORK_PUBLISHER_FILE"] = str(self.dir / "publisher.local.json")
        os.environ["GLASSWORK_SIGNING_KEY"] = str(self.dir / "keys" / "signing.key")
        os.environ["AGENCY_DATA"] = str(self.dir / "data")
        os.environ.pop("GLASSWORK_HOME", None)
        self.port = _port()
        self.url = f"http://127.0.0.1:{self.port}"
        made = support.setup(self.url, "owner@example.com")
        assert made.get("ok"), made
        z = support.home_zip()
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

    def raw(self, path):
        try:
            with urllib.request.urlopen(self.url + path, timeout=10) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, (e.read() or b"").decode("utf-8", "replace")


def test_a_report_and_its_answer():
    print("\nA report reaches the publisher, and his answer reaches the person:")
    with Home() as h:
        check("the Home answers and knows him", support.check_home().get("ok"), True)
        r = support.send("The floor does not load", "It spins for ever on the Agency screen.", "me@example.com", True)
        check("the report is sent", r.get("ok"), True)
        rid = r.get("id")
        mine = support.mine()["reports"]
        check("  and the person sees it, waiting to be read", (mine[0]["id"], mine[0]["status"]), (rid, "new"))
        inbox = support.inbox()
        check("he sees it in his inbox, new", (inbox.get("new"), inbox["reports"][0]["title"]), (1, "The floor does not load"))
        full = support.read(rid)["report"]
        check("  in full, with the support report", "Version" in full.get("support", "") or len(full.get("support", "")) > 50, True)
        check("  and the person's own key is never shown to him", "key" in full, False)
        support.answer(rid, "fixed", "Thanks — fixed in the next version.", "99.0.0")
        mine = support.mine()["reports"][0]
        check("the person reads his answer", (mine["status"], mine["reply"]), ("fixed", "Thanks — fixed in the next version."))
        check("  and is told to update to get it", mine.get("update_needed"), True)


def test_nobody_else_gets_in():
    print("\nNobody without his key reads, answers or publishes:")
    with Home() as h:
        r = support.send("Private", "My leads list is empty.", "", False)
        code, body = h.raw("/glasswork-home/index.php?do=inbox")
        check("the inbox is refused to a stranger", code, 401)
        req = urllib.request.Request(h.url + "/glasswork-home/index.php?do=answer", method="POST",
                                     data=json.dumps({"id": r["id"], "reply": "hacked"}).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=10)
            refused = False
        except urllib.error.HTTPError as e:
            refused = e.code == 401
        check("an answer from a stranger is refused", refused, True)
        name = next((h.web / "glasswork-home" / "private" / "reports").glob("*.php")).name
        code, body = h.raw(f"/glasswork-home/private/reports/{name}")
        check("a report asked for by its address shows nothing", "My leads list" in body, False)
        st = support._ask("status", "POST", {"reports": [{"id": r["id"], "key": "wrong"}]})
        check("its answer needs the person's own key", st["reports"][0]["found"], False)

    print("\nOne address cannot flood the inbox:")
    with Home() as h:
        sent = [support.send(f"Report {i}", "text", "", False).get("ok") for i in range(9)]
        check("eight in an hour, then it asks them to wait", sent, [True] * 8 + [False])


def test_putting_a_version_out():
    print("\nA version put out is signed, knows his Home, and cannot go out twice:")
    if not (ROOT / ".git").exists():
        print("  SKIP  a version is put out from a git copy")
        return
    with Home() as h:
        r = updates.publish("Faster screens.")
        check("it goes out", r.get("ok"), True)
        m = json.loads(h.raw("/glasswork-home/index.php?do=latest")[1])
        check("the Home now offers it", m.get("version"), product.version())
        check("  signed with his key", updates.verify(m, product.info()["update_key"]), True)
        files = list((h.web / "glasswork-home" / "updates").glob("*.zip"))
        with zipfile.ZipFile(files[0]) as z:
            shipped = json.loads(z.read(next(n for n in z.namelist() if n.endswith("config/product.json"))))
        check("the release tells every copy where its Home is",
              (shipped.get("home_url"), bool(shipped.get("update_key"))), (h.url + "/glasswork-home", True))
        check("  but his own product.json is untouched",
              "home_url" in json.loads(product.FILE.read_text(encoding="utf-8")), False)
        check("  and nothing private rode along", any(n.endswith((".local.json", ".env")) for n in
                                                      zipfile.ZipFile(files[0]).namelist()), False)
        again = updates.publish("Again.")
        check("the same version cannot go out twice", (again.get("ok"), "already out" in again.get("error", "")), (False, True))
        page = h.raw("/glasswork-home/")[1]
        check("the download page is there for new people", "Glasswork" in page, True)


def main():
    if not shutil.which("php"):
        print("SKIPPED: PHP is not installed here, so the Home cannot be run.")
        return 0
    for t in (test_a_report_and_its_answer, test_nobody_else_gets_in, test_putting_a_version_out):
        t()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        return 1
    print("publish: all good — reports reach you, answers reach them, versions go out signed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
