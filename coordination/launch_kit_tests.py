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
from cryptography.hazmat.primitives import serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

# Making a release is the publisher's side, which a copy he gives out does not
# have; the release test steps aside there. Everything a copy does — check,
# verify, install, undo — is tested everywhere.
try:
    from coordination import publisher  # noqa: E402
except ImportError:
    publisher = None

_fail = []


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


class _Copy:
    """A copy of the program in a folder of its own, with its own product.json."""

    def __init__(self, version="0.6.0", key="", url="https://updates.example/manifest.json"):
        self.version, self.key, self.url = version, key, url

    def __enter__(self):
        self.dir = Path(tempfile.mkdtemp(prefix="copy-"))
        (self.dir / "config").mkdir()
        made = json.dumps({"name": "Glasswork", "version": self.version, "update_url": self.url, "update_key": self.key})
        (self.dir / "config" / "product.json").write_text(made, encoding="utf-8")
        (self.dir / "config" / "mine.local.json").write_text('{"secret": "mine"}', encoding="utf-8")
        (self.dir / "run.py").write_text("print('old')\n", encoding="utf-8")
        (self.dir / "server.js").write_text("// old\n", encoding="utf-8")
        (self.dir / ".env").write_text("ANTHROPIC_API_KEY=keep-me\n", encoding="utf-8")
        (self.dir / "data").mkdir()
        (self.dir / "data" / "account.json").write_text('{"me": 1}', encoding="utf-8")
        self.saved = (updates.ROOT, product.ROOT, product.FILE, publisher.ROOT if publisher else None)
        updates.ROOT = product.ROOT = self.dir
        if publisher:
            publisher.ROOT = self.dir
        product.FILE = self.dir / "config" / "product.json"
        return self

    def __exit__(self, *a):
        updates.ROOT, product.ROOT, product.FILE = self.saved[:3]
        if publisher:
            publisher.ROOT = self.saved[3]
        shutil.rmtree(self.dir, ignore_errors=True)


def _keypair(tmp):
    k = Ed25519PrivateKey.generate()
    (tmp / "signing.key").write_bytes(k.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                                      serialization.NoEncryption()))
    pub = k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(pub).decode(), tmp / "signing.key"


def _sign(m, key_file):
    k = Ed25519PrivateKey.from_private_bytes(Path(key_file).read_bytes())
    return base64.b64encode(k.sign(updates._signed_text(m))).decode()


def _zip(files, top="glasswork-0.7.0/"):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, text in files.items():
            z.writestr(top + name if top is not None else name, text)
    return buf.getvalue()


def _release(data, version, key_file, zip_url="https://updates.example/glasswork-0.7.0.zip"):
    m = {"version": version, "zip": zip_url, "sha256": hashlib.sha256(data).hexdigest(), "notes": "Better floor."}
    m["signature"] = _sign(m, key_file)
    return m


def _fetcher(table):
    def fetch(url, limit, timeout=30):
        if not url.startswith("https://"):
            raise ValueError("updates are only fetched over https://")
        if url not in table:
            raise OSError("not found")
        return table[url]
    return fetch


GOOD_FILES = {"run.py": "print('new')\n", "server.js": "// new\n", "coordination/new_module.py": "X = 1\n",
              ".env": "ANTHROPIC_API_KEY=stolen\n", "data/account.json": '{"me": 0}',
              "config/mine.local.json": '{"secret": "replaced"}', "config/product.json": '{"name": "Glasswork", "version": "0.7.0"}'}


def test_versions():
    print("\nThe version comes from one file, and a bad version never looks newer:")
    check("it has a name and a version", bool(product.name()) and product.parse_version(product.version()) > (0, 0, 0), True)
    check("0.10.0 is newer than 0.9.9", product.parse_version("0.10.0") > product.parse_version("0.9.9"), True)
    check("v1.2 reads as 1.2.0", product.parse_version("v1.2"), (1, 2, 0))
    check("nonsense reads as 0.0.0", product.parse_version("latest!"), (0, 0, 0))


def test_a_signed_update_installs_and_undoes():
    print("\nA signed update installs, keeps what it replaced, and can be undone:")
    tmp = Path(tempfile.mkdtemp(prefix="keys-"))
    pub, key_file = _keypair(tmp)
    data = _zip(GOOD_FILES)
    m = _release(data, "0.7.0", key_file)
    with _Copy(key=pub) as c:
        fetch = _fetcher({c.url: json.dumps(m).encode(), m["zip"]: data})
        found = updates.check(fetch)
        check("it sees the new version", (found["available"], found["latest"], found["signed"], found["can_install"]),
              (True, "0.7.0", True, True))
        check("  with its notes", found["notes"], "Better floor.")
        r = updates.apply(fetch, install_deps=False)
        check("it installs", (r["ok"], r.get("installed"), r.get("version")), (True, True, "0.7.0"))
        check("the program's files are the new ones", (c.dir / "run.py").read_text(encoding="utf-8"), "print('new')\n")
        check("a new file arrives", (c.dir / "coordination" / "new_module.py").exists(), True)
        check("the version moves on", product.version(), "0.7.0")
        check(".env is never touched", (c.dir / ".env").read_text(encoding="utf-8"), "ANTHROPIC_API_KEY=keep-me\n")
        check("nor the person's data", (c.dir / "data" / "account.json").read_text(encoding="utf-8"), '{"me": 1}')
        check("nor any *.local.json", (c.dir / "config" / "mine.local.json").read_text(encoding="utf-8"), '{"secret": "mine"}')
        backup = c.dir / r["backup"]
        check("the old run.py is kept", (backup / "run.py").read_text(encoding="utf-8"), "print('old')\n")
        back = updates.rollback()
        check("it can be undone", (back["ok"], back["version"]), (True, "0.6.0"))
        check("  the old file is back", (c.dir / "run.py").read_text(encoding="utf-8"), "print('old')\n")
        check("  the file it added is gone", (c.dir / "coordination" / "new_module.py").exists(), False)
        check("  and so is the version", product.version(), "0.6.0")
        check("undoing twice finds nothing more to undo", updates.rollback()["ok"], False)


def test_what_is_refused():
    print("\nUnsigned, wrongly signed, tampered, http:// and escaping updates are refused, changing nothing:")
    tmp = Path(tempfile.mkdtemp(prefix="keys-"))
    pub, key_file = _keypair(tmp)
    other_pub, other_key = _keypair(Path(tempfile.mkdtemp(prefix="keys-")))
    data = _zip(GOOD_FILES)
    signed = _release(data, "0.7.0", key_file)
    for label, key, manifest, zipped in (
            ("with no key on this copy", "", signed, data),
            ("signed with another key", pub, _release(data, "0.7.0", other_key), data),
            ("whose download was tampered with", pub, signed, data + b"tampered")):
        with _Copy(key=key) as c:
            fetch = _fetcher({c.url: json.dumps(manifest).encode(), manifest["zip"]: zipped})
            r = updates.apply(fetch, install_deps=False)
            check(f"an update {label} is refused", r["ok"], False)
            check("  and nothing changed", (c.dir / "run.py").read_text(encoding="utf-8"), "print('old')\n")
    _ = other_pub
    try:
        updates._fetch("http://updates.example/manifest.json", 100)
        got = "fetched"
    except ValueError:
        got = "refused"
    check("a plain http:// address is never fetched", got, "refused")
    with _Copy(key=pub) as c:
        for label, files in (("writes outside the program folder", {"../../escaped.txt": "x", "run.py": "y"}),
                             ("is not this program", {"readme.txt": "hello"})):
            evil = _zip(files, top=None)
            try:
                r = updates.install_zip(evil, "0.7.0", install_deps=False)
            except ValueError as e:
                r = {"ok": False, "error": str(e)}
            check(f"a zip that {label} is refused", r["ok"], False)
        check("  nothing escaped", (c.dir.parent / "escaped.txt").exists() or (c.dir / "escaped.txt").exists(), False)
        check("  and run.py is untouched", (c.dir / "run.py").read_text(encoding="utf-8"), "print('old')\n")
    with _Copy(key=pub, version="0.7.0") as c:
        fetch = _fetcher({c.url: json.dumps(signed).encode(), signed["zip"]: data})
        found = updates.check(fetch)
        check("the same version is not offered again", (found["available"], "latest" in found.get("why", "")), (False, True))
    with _Copy(key=pub) as c:
        (c.dir / ".git").mkdir()
        check("a git copy is told it updates from git", "git" in updates.check()["why"], True)
        check("  and the zip installer refuses it", updates.apply()["ok"], False)


def test_a_release_has_only_tracked_files():
    print("\nA release holds only tracked files — never a .env — and verifies:")
    if not shutil.which("git"):
        print("  SKIP  git is not installed here")
        return
    if publisher is None:
        print("  SKIP  releases are made on the publisher's own copy")
        return
    tmp = Path(tempfile.mkdtemp(prefix="keys-"))
    pub, key_file = _keypair(tmp)
    with _Copy(key=pub) as c:
        env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
        for cmd in (["git", "init", "-q"], ["git", "add", "run.py", "server.js", "config/product.json", ".env"],
                    ["git", "commit", "-q", "-m", "x"]):
            subprocess.run(cmd, cwd=c.dir, capture_output=True, text=True, encoding="utf-8", env=env, check=True)
        out = Path(tempfile.mkdtemp(prefix="release-"))
        r = publisher.make_release(out, key_file, "https://updates.example")
        check("it is made", r["ok"], True)
        m = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
        check("its signature verifies with the copy's key", updates.verify(m, pub), True)
        check("its checksum is the zip's", m["sha256"], hashlib.sha256(Path(r["zip"]).read_bytes()).hexdigest())
        names = zipfile.ZipFile(r["zip"]).namelist()
        check("it holds the program", any(n.endswith("/run.py") for n in names), True)
        check("never a .env, even a tracked one", any(n.endswith(".env") for n in names), False)
        check("nor an untracked file", any(n.endswith("mine.local.json") for n in names), False)


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
    test_a_signed_update_installs_and_undoes()
    test_what_is_refused()
    test_a_release_has_only_tracked_files()
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
