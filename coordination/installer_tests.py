"""
installer_tests.py — run: python -m coordination.installer_tests   (python3 on macOS/Linux)

    "user install the whole program with just 1 click and that install all the
     needed data and files on it's own ... the program asks user to where user
     want to place the content"

The parts of the one-click Windows installer that can be checked anywhere,
without downloading anything. The installer itself is built, installed, opened
and removed again on a real Windows machine by .github/workflows/windows-installer.yml.

  the program folder it packs holds the program and nothing private
  the icon is a real icon
  Setup asks where to install AND where to keep the person's work, needs no
    administrator, and removing the program never takes the work without a yes
  an installed copy finds its data folder, its own Python and its own Node
  the shortcuts open it with no console window, and a failure is said in a box
"""
import importlib.machinery
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "installer"))
import build_windows as bw  # noqa: E402
from coordination import product  # noqa: E402

_fail = []


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


def test_what_goes_in():
    print("\nThe program folder Setup packs holds the program, and nothing private:")
    app = Path(tempfile.mkdtemp()) / "app"
    app.mkdir()
    shipped = json.dumps({"name": "Glasswork", "version": "9.9.9", "home_url": "https://example.com/glasswork-home"})
    ver = bw.stage_program(app, shipped)
    check("it carries the release's version", ver, "9.9.9")
    check("  and the release's product card, with the Home's address",
          json.loads((app / "config" / "product.json").read_text(encoding="utf-8")).get("home_url"),
          "https://example.com/glasswork-home")
    for must in ("run.py", "launch.pyw", "server.js", "ui/app.html", "requirements.txt", "installer/glasswork.iss"):
        check(f"  {must} is in it", (app / must).exists(), True)
    private = [p.relative_to(app).as_posix() for p in app.rglob("*")
               if p.name == ".env" or p.name.endswith((".local.json", ".db")) or p.relative_to(app).parts[0] in ("data", ".git")]
    check("  and no settings, keys, records or history ride along", private, [])
    his = sorted(p.relative_to(app).as_posix() for p in app.rglob("*")
                 if p.is_file() and product.publisher_only(p.relative_to(app).as_posix()))
    check("  nor the publisher's own side: his users room, publishing, the Home's code, his guides", his, [])
    check("  but a copy can still seal a report to him", (app / "coordination" / "sealed.py").exists()
          and (app / "coordination" / "support.py").exists(), True)


def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, (e.read() or b"").decode("utf-8", "replace")


def test_a_copy_given_out_has_no_users_room():
    """
    "will the users will see that section YOUR USERS Section? ... the users
     should not have the program back end"
    """
    print("\nA copy given out, started as it would be on their computer, has no users room at all:")
    if not (shutil.which("node") and (ROOT / "node_modules" / "express").exists()):
        print("  SKIP  Node and the office's libraries are not here")
        return
    place = Path(tempfile.mkdtemp(prefix="given-out-"))
    app = place / "app"
    app.mkdir()
    bw.stage_program(app, json.dumps({"name": "Glasswork", "version": "9.9.9"}))
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    env = {**os.environ, "PORT": str(port), "AGENCY_DATA": str(place / "data"), "AGENCY_IGNORE_DOTENV": "1",
           "CLAUDE_TEAM_OFF": "1", "NODE_PATH": str(ROOT / "node_modules")}
    env.pop("AGENCY_DB", None)
    proc = subprocess.Popen(["node", "server.js"], cwd=app, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(80):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
                break
            except OSError:
                time.sleep(0.25)
        code, body = _get(base + "/api/publisher")
        check("the office there says it is not the publisher's copy",
              (code, json.loads(body).get("can_publish")), (200, False))
        check("  the list of users is not there to ask for", _get(base + "/api/publisher/users")[0], 404)
        check("  nor collecting, nor putting a version out",
              (_get(base + "/api/publisher/inbox")[0], _get(base + "/api/publisher/job")[0]), (404, 404))
        check("  nor the room's own screen", _get(base + "/publisher.js")[0], 404)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        shutil.rmtree(place, ignore_errors=True)


def test_the_icon():
    print("\nThe icon is a real Windows icon:")
    f = Path(tempfile.mkdtemp()) / "g.ico"
    bw.write_icon(f)
    raw = f.read_bytes()
    reserved, kind, count = struct.unpack("<HHH", raw[:6])
    check("an icon file with four sizes", (reserved, kind, count), (0, 1, 4))
    first = struct.unpack("<BBBBHHII", raw[6:22])
    check("  each picture inside is a PNG", raw[first[7]:first[7] + 8], b"\x89PNG\r\n\x1a\n")


def test_the_setup_script():
    print("\nSetup asks the two questions, needs no administrator, and keeps the work safe:")
    iss = (ROOT / "installer" / "glasswork.iss").read_text(encoding="utf-8")
    check("it installs for this Windows user, with no administrator password", "PrivilegesRequired=lowest" in iss, True)
    check("it asks where to install the program", "DisableDirPage=yes" not in iss and "DefaultDirName=" in iss, True)
    check("it asks where to keep the person's work", "Where should your work be kept?" in iss, True)
    check("  and tells the program, in install.json", "install.json" in iss and '"data": "' in iss, True)
    check("  which can be given on the command line for a silent install", "{param:DATADIR|}" in iss, True)
    check("the shortcuts open it with no console window", iss.count("pythonw.exe") >= 2 and "launch.pyw" in iss, True)
    check("a new Setup.exe updates the one installed", "AppId={{" in iss and "UsePreviousAppDir=yes" in iss, True)
    check("removing it asks before deleting the work, with No first",
          "Also delete your work" in iss and "MB_DEFBUTTON2" in iss, True)
    wf_file = ROOT / ".github" / "workflows" / "windows-installer.yml"
    if not wf_file.exists():
        print("  SKIP  the installer's own check lives on the publisher's copy")
        return
    wf = wf_file.read_text(encoding="utf-8")
    check("every installer is built, installed, opened and removed on Windows before anyone gets it",
          all(w in wf for w in ("build_windows.py", "/VERYSILENT", "api/ping", "unins000.exe")), True)


def test_an_installed_copy_finds_its_things():
    print("\nAn installed copy finds its data folder, its own Python and its own Node:")
    import run
    real_root, real_env = run.ROOT, {k: os.environ.get(k) for k in ("AGENCY_DATA", "PATH", "GLASSWORK_INSTALLED")}
    place = Path(tempfile.mkdtemp())
    data = place / "my work"
    (place / "runtime" / "node").mkdir(parents=True)
    (place / "install.json").write_text(json.dumps({"data": str(data)}), encoding="utf-8")
    run.ROOT = place
    os.environ.pop("AGENCY_DATA", None)
    try:
        run.use_install()
        check("the data folder he chose is the one used", os.environ.get("AGENCY_DATA"), str(data))
        check("  and it is made if it is not there", data.is_dir(), True)
        check("its own Node comes first", os.environ["PATH"].split(os.pathsep)[0], str(place / "runtime" / "node"))
        check("and the office is told it was installed", os.environ.get("GLASSWORK_INSTALLED"), "1")
    finally:
        run.ROOT = real_root
        for k, v in real_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    src = (ROOT / "run.py").read_text(encoding="utf-8")
    check("the office runs every agent with the program's own Python", '"PYTHON_BIN": PY' in src, True)

    print("\nThe shortcut's launcher writes to a log, and says a failure in a box:")
    launch = importlib.machinery.SourceFileLoader("launch_pyw", str(ROOT / "launch.pyw")).load_module()
    real = launch.ROOT
    launch.ROOT = place
    try:
        check("its log is in the data folder he chose", launch._log_path(), data / "logs" / "office.log")
    finally:
        launch.ROOT = real
    text = (ROOT / "launch.pyw").read_text(encoding="utf-8")
    check("  and a failure is said in an ordinary Windows message box", "MessageBoxW" in text, True)


def main():
    for t in (test_what_goes_in, test_a_copy_given_out_has_no_users_room, test_the_icon, test_the_setup_script, test_an_installed_copy_finds_its_things):
        t()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        return 1
    print("installer: all good — one folder, two questions, nothing private, the work kept safe.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
