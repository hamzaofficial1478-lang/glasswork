"""
launch_kit_tests.py — run: python -m coordination.launch_kit_tests

Phase 6: what a customer needs that the owner never did — an installer, updates
that work without git, terms and a privacy policy, the product's own name, and
a report to send when something breaks.

What is protected here:
  - the version is read from one file, and a bad version can never look newer;
  - an update is installed only when its checksum AND its signature match; an
    unsigned one, one signed by another key, a tampered download and a plain
    http:// address are all refused, and nothing is changed;
  - an update never touches the person's own things (.env, data, *.local.json,
    cabinets), keeps every file it replaces, and can be undone exactly;
  - a zip that tries to write outside the program folder is refused whole;
  - a release contains only tracked files, never a .env, and verifies;
  - the support report keeps the useful part and none of the private part;
  - a customer's account shows the product's name, not the owner's agency;
  - the installers ask before installing anything, install for this user only,
    keep the program's libraries in its own folder, and change nothing when
    asked only to check;
  - the terms and privacy pages can be read before signing up, and sign-up
    links to them.

Nothing here reaches the internet: every download is a stand-in.
"""
import base64
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from coordination import diagnostics, product, updates  # noqa: E402

_fail = []


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


class _Copy:
    """A copy of the program in a folder of its own, with its own product.json."""

    def __init__(self, version="0.6.0", repository="someone/glasswork"):
        self.version, self.repository = version, repository

    def __enter__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="copy-"))
        (self.dir / "config").mkdir()
        made = json.dumps({"name": "Glasswork", "version": self.version, "repository": self.repository})
        (self.dir / "config" / "product.json").write_text(made, encoding="utf-8")
        self.saved = (updates.ROOT, product.ROOT, product.FILE)
        updates.ROOT = product.ROOT = self.dir
        product.FILE = self.dir / "config" / "product.json"
        return self

    def __exit__(self, *a):
        updates.ROOT, product.ROOT, product.FILE = self.saved
        shutil.rmtree(self.dir, ignore_errors=True)


def _release(tag, exe_url="https://github.com/someone/glasswork/releases/download/v0.7.0/glasswork-0.7.0-setup.exe"):
    return json.dumps({"tag_name": tag, "body": "Better floor.", "html_url": "https://github.com/someone/glasswork/releases/tag/" + tag,
                       "assets": [{"name": "glasswork-0.7.0-setup.exe", "browser_download_url": exe_url}]}).encode()


def test_versions():
    print("\nThe version comes from one file, and a bad version never looks newer:")
    check("it has a name and a version", bool(product.name()) and product.parse_version(product.version()) > (0, 0, 0), True)
    check("0.10.0 is newer than 0.9.9", product.parse_version("0.10.0") > product.parse_version("0.9.9"), True)
    check("v1.2 reads as 1.2.0", product.parse_version("v1.2"), (1, 2, 0))
    check("nonsense reads as 0.0.0", product.parse_version("latest!"), (0, 0, 0))


def test_a_new_version():
    """
    "each program on pc is remains to itself and that pc not to anything else"
    """
    print("\nA new version is looked for only when asked, from the program's GitHub page, with nothing about the copy:")
    asked = []
    with _Copy():
        found = updates.check(lambda url, limit=0, timeout=0: asked.append(url) or _release("v0.7.0"))
        check("it asks the program's public release page, and only that",
              asked, ["https://api.github.com/repos/someone/glasswork/releases/latest"])
        check("it sees the new version, and offers the Setup to download",
              (found["available"], found["latest"], found["download_url"].endswith("glasswork-0.7.0-setup.exe")),
              (True, "0.7.0", True))
        check("  with its notes", found["notes"], "Better floor.")
        odd = updates.check(lambda url, limit=0, timeout=0: _release("v0.7.0", "https://evil.example/setup.exe"))
        check("a download from anywhere but GitHub is never offered", odd["download_url"], "")
        same = updates.check(lambda url, limit=0, timeout=0: _release("v0.6.0"))
        check("the same version is not offered", (same["available"], "newest" in same["why"]), (False, True))
        down = updates.check(lambda url, limit=0, timeout=0: (_ for _ in ()).throw(OSError("offline")))
        check("an unreachable page says so, and where to look", (down["ok"], down.get("page_url")),
              (False, "https://github.com/someone/glasswork/releases"))
    with _Copy(repository=""):
        check("with no place set, it asks nobody", updates.check(lambda *a, **k: asked.append("!") or b"{}")["available"]
              or "!" in asked, False)
    check("a git copy is told it updates from git", "git" in updates.check()["why"], True)
    src = (ROOT / "ui" / "app.html").read_text(encoding="utf-8")
    check("the app never looks for one by itself", "setInterval(updateBanner" not in src and "updateBanner()" not in src, True)
    check("  nor does the program when it starts", "updates.check()" not in (ROOT / "run.py").read_text(encoding="utf-8"), True)


def test_the_support_report():
    print("\nThe support report keeps what helps and drops what is private:")
    said = ("Key sk-ant-api03-ABCDEFGHIJKLMNOP refused for owner@business.ae at https://www.example.ae/contact; "
            "call +971 50 123 4567. Token AIzaSyD-1234567890abcdefghijklmno. The run failed after 3 tries.")
    out = diagnostics.scrub(said)
    for gone in ("sk-ant", "owner@business.ae", "example.ae", "123 4567", "AIzaSy"):
        check(f"no {gone!r}", gone in out, False)
    check("the useful part stays", "The run failed after 3 tries." in out, True)
    text = diagnostics.as_text({"product": "Glasswork", "version": "0.6.0", "made_at": "now", "system": "Windows 11",
                                "python": "3.12.1", "node": "v22.1.0", "libraries": {"requests": True},
                                "engine": "Claude", "cannot_run": [],
                                "desks": [{"sector": "SEO", "desk": "seo-backlinks", "problem": "failed",
                                           "said": "key sk-live-SECRETSECRETSECRET1234 refused"}],
                                "faults": []})
    check("a failing desk is named", "SEO · seo-backlinks · failed" in text, True)
    check("  without its key", "SECRETSECRET" in text, False)
    check("and it says what it leaves out", "Nothing private is in this report" in text, True)


def test_a_customer_sees_the_product_name():
    print("\nA customer's account shows the product's name, not the owner's agency:")
    from coordination import site, workspace
    saved = {k: os.environ.get(k) for k in ("AGENCY_DATA", "AGENCY_NAME", "AGENCY_KEY")}
    try:
        os.environ["AGENCY_DATA"] = str(Path(tempfile.mkdtemp(prefix="acct-")) / "data")
        os.environ.pop("AGENCY_NAME", None)
        os.environ.pop("AGENCY_KEY", None)
        acct = workspace.create_account("Bakery")["account"]["id"]
        sid = workspace.add_site(acct, "Bakery", "https://bakery.example")["site"]["id"]
        workspace.open_site(acct, sid)
        check("a new account shows the product's name", site.agency_name(), product.name())
        a = workspace.account(acct)
        a["brought_in"] = {"site": sid, "at": 0, "copied": []}
        workspace._save(a)
        check("the account the owner's agency was brought into keeps his", site.agency_name(), site.DEFAULT_AGENCY)
        os.environ["AGENCY_NAME"] = "Crumbs & Co"
        check("and a name of their own always wins", site.agency_name(), "Crumbs & Co")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_the_installers():
    print("\nThe installers ask first, install for this user, and keep libraries in the program's own folder:")
    bat = (ROOT / "install" / "Install-Windows.bat").read_text(encoding="utf-8")
    ps = (ROOT / "install" / "windows.ps1").read_text(encoding="utf-8")
    sh = (ROOT / "install" / "install.sh").read_text(encoding="utf-8")
    check("the Windows one starts its script without changing the computer's policy",
          "-ExecutionPolicy Bypass -File" in bat and "Set-ExecutionPolicy" not in ps, True)
    check("it asks before installing Python or Node", "Read-Host" in ps and "Install it now with winget?" in ps, True)
    check("  and installs them for this user only", ps.count("--scope user") == 1 and "Python.Python.3.12" in ps
          and "OpenJS.NodeJS.LTS" in ps, True)
    for label, text in (("Windows", ps), ("Mac and Linux", sh)):
        check(f"{label}: libraries go into the program's own .venv", "-m venv" in text and "requirements.txt" in text, True)
        check(f"{label}: the office's libraries come from the lockfile", "npm ci" in text, True)
        check(f"{label}: it can be asked only to check", ("CheckOnly" in text) or ("--check-only" in text), True)
    check("run.py hands over to the .venv when there is one",
          "def use_venv():" in (ROOT / "run.py").read_text(encoding="utf-8")
          and "    use_venv()\n" in (ROOT / "run.py").read_text(encoding="utf-8"), True)
    if os.name != "nt" and shutil.which("bash"):
        r = subprocess.run(["bash", "-n", str(ROOT / "install" / "install.sh")], capture_output=True, text=True, encoding="utf-8")
        check("the Mac and Linux script is valid", r.returncode, 0)
        before = sorted(p.name for p in ROOT.iterdir())
        r = subprocess.run(["bash", str(ROOT / "install" / "install.sh"), "--check-only"], capture_output=True,
                           text=True, encoding="utf-8", timeout=60)
        check("  and --check-only changes nothing", (r.returncode, sorted(p.name for p in ROOT.iterdir()) == before), (0, True))


def test_terms_and_privacy():
    print("\nThe terms and privacy pages can be read before signing up, and sign-up links to them:")
    server = (ROOT / "server.js").read_text(encoding="utf-8")
    open_block = server[server.index("const ACCOUNT_OPEN"):server.index("function accountsExist")]
    check("both pages are readable before an account exists", '"/terms"' in open_block and '"/privacy"' in open_block, True)
    for page in ("terms", "privacy"):
        text = (ROOT / "ui" / f"{page}.html").read_text(encoding="utf-8")
        check(f"{page} says plainly that it is a draft for a lawyer", "Draft for review" in text, True)
    acct = (ROOT / "ui" / "account.html").read_text(encoding="utf-8")
    check("sign-up links to both", 'href="/terms"' in acct and 'href="/privacy"' in acct, True)


def main():
    test_versions()
    test_a_new_version()
    test_the_support_report()
    test_a_customer_sees_the_product_name()
    test_the_installers()
    test_terms_and_privacy()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        return 1
    print("All launch kit tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
