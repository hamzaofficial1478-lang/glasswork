"""
build_windows.py — makes the one-click Windows installer, Setup.exe.

    python installer/build_windows.py --out build
    python installer/build_windows.py --out build --product-json -    (product.json on stdin)
    python installer/build_windows.py --out build --stage-only         (everything but the compile)

    "i want to build the program as a software as the softwares have their
     files and user install the whole program with just 1 click and that
     install all the needed data and files on it's own"

WHAT GOES IN

  the program          every file git tracks (so nothing private rides along),
                       with the release's product.json — the publisher's Home
                       address and public key — in place of this copy's
  its own Python       a complete, private Python 3.12 (python-build-standalone,
                       the build uv uses), with the program's libraries installed
                       into it — nothing is added to the computer's own Python
  its own Node         the official Node.js LTS for Windows, and the office's
                       Node libraries (npm ci, production only)
  an icon, a licence   made here, so the installer and the shortcuts look like
                       the program

Then Inno Setup turns that folder into Setup.exe (installer/glasswork.iss),
which asks where to install, where to keep the person's work, puts the program
on the desktop and in the Start menu, and opens it.

WHERE IT RUNS

  On Windows — the publisher's own computer (Settings → Publisher → Put it out,
  with the installer ticked) or the GitHub check that builds and tests it. It
  needs Inno Setup 6; on Windows it offers to install it with winget.
  Elsewhere, --stage-only still gathers everything (the libraries are fetched
  as Windows wheels), which is how the tests check the folder without Windows.

Downloads are checked against the checksums their publishers give, and kept
in a cache so a second build fetches nothing.
"""
import argparse
import hashlib
import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY_SERIES = "3.12"
NODE_MAJOR = "22"
PBS_API = "https://api.github.com/repos/astral-sh/python-build-standalone/releases/latest"
NODE_INDEX = "https://nodejs.org/dist/index.json"
LEFT_OUT = (".git", "data", "logs", "node_modules", ".venv", "venv", "updates", "runtime", "build",
            "__pycache__")


def say(text):
    print("  " + text, file=sys.stderr, flush=True)


def fetch(url, limit=400 * 1024 * 1024) -> bytes:
    headers = {"User-Agent": "glasswork-installer-build", "Accept": "application/octet-stream, application/json"}
    # GitHub limits anonymous questions per address, and build machines share
    # addresses; the GitHub check passes its own token so it is never refused.
    if url.startswith("https://api.github.com/") and os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=300) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError(f"{url} is larger than expected")
    return data


def cached(cache: Path, name: str, url: str, sha256: str = "") -> bytes:
    cache.mkdir(parents=True, exist_ok=True)
    f = cache / name
    if f.exists() and (not sha256 or hashlib.sha256(f.read_bytes()).hexdigest() == sha256):
        return f.read_bytes()
    say(f"downloading {name}")
    data = fetch(url)
    if sha256 and hashlib.sha256(data).hexdigest() != sha256:
        raise ValueError(f"{name} did not match the checksum its publisher gives — not used")
    f.write_bytes(data)
    return data


# ---------------------------------------------------------------------------
# the program
# ---------------------------------------------------------------------------

def program_files() -> list:
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, timeout=60)
        files = [f for f in out.stdout.decode("utf-8").split("\0") if f]
    except Exception:
        files = []
    if files:
        return files
    found = []
    for p in ROOT.rglob("*"):
        rel = p.relative_to(ROOT).as_posix()
        if p.is_file() and rel.split("/")[0] not in LEFT_OUT and "__pycache__" not in rel:
            found.append(rel)
    return found


def stage_program(app: Path, product_json: str) -> str:
    for rel in program_files():
        top = rel.split("/")[0]
        if top in LEFT_OUT or rel.endswith((".local.json", ".db")) or rel == ".env":
            continue
        src = ROOT / rel
        if not src.is_file():
            continue
        dst = app / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    if product_json:
        (app / "config" / "product.json").write_text(product_json, encoding="utf-8")
    return json.loads((app / "config" / "product.json").read_text(encoding="utf-8")).get("version", "0.0.0")


# ---------------------------------------------------------------------------
# its own Python
# ---------------------------------------------------------------------------

def python_runtime(app: Path, cache: Path) -> str:
    rel = json.loads(fetch(PBS_API, 8 * 1024 * 1024).decode("utf-8"))
    assets = {a["name"]: a["browser_download_url"] for a in rel.get("assets") or []}
    want = sorted(n for n in assets
                  if n.startswith(f"cpython-{PY_SERIES}.") and n.endswith("-x86_64-pc-windows-msvc-install_only_stripped.tar.gz"))
    if not want:
        raise ValueError("no Windows build of Python " + PY_SERIES + " in the latest python-build-standalone release")
    name = want[-1]
    sums = {}
    if "SHA256SUMS" in assets:
        for line in fetch(assets["SHA256SUMS"], 4 * 1024 * 1024).decode("utf-8").splitlines():
            bits = line.split()
            if len(bits) == 2:
                sums[bits[1].lstrip("*")] = bits[0]
    data = cached(cache, name, assets[name], sums.get(name, ""))
    dest = app / "runtime"
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as t:
        for m in t.getmembers():
            if m.name.startswith("/") or ".." in Path(m.name).parts or m.issym() or m.islnk():
                raise ValueError("the Python download has an unsafe path in it: " + m.name)
        t.extractall(dest)
    if not (dest / "python" / "python.exe").exists():
        raise ValueError("the Python download did not hold python.exe where expected")
    return name


def python_libraries(app: Path) -> None:
    req = str(ROOT / "requirements.txt")
    py = app / "runtime" / "python" / "python.exe"
    if os.name == "nt":
        cmd = [str(py), "-m", "pip", "install", "--no-warn-script-location", "--disable-pip-version-check", "-r", req]
    else:
        # Not on Windows: fetch the Windows wheels into the runtime's own folder.
        cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--platform", "win_amd64",
               "--python-version", PY_SERIES, "--implementation", "cp", "--only-binary=:all:",
               "--target", str(app / "runtime" / "python" / "Lib" / "site-packages"), "-r", req]
    say("installing the program's Python libraries")
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
    if p.returncode != 0:
        raise ValueError("the Python libraries would not install: " + (p.stderr or p.stdout)[-600:])


# ---------------------------------------------------------------------------
# its own Node
# ---------------------------------------------------------------------------

def node_runtime(app: Path, cache: Path) -> str:
    index = json.loads(fetch(NODE_INDEX, 16 * 1024 * 1024).decode("utf-8"))
    lts = [v for v in index if v.get("lts") and str(v.get("version", "")).startswith("v" + NODE_MAJOR + ".")
           and "win-x64-zip" in (v.get("files") or [])]
    if not lts:
        raise ValueError("no Node " + NODE_MAJOR + " LTS build for Windows found")
    ver = lts[0]["version"]
    name = f"node-{ver}-win-x64.zip"
    sums = fetch(f"https://nodejs.org/dist/{ver}/SHASUMS256.txt", 1024 * 1024).decode("utf-8")
    sha = next((l.split()[0] for l in sums.splitlines() if l.strip().endswith(name)), "")
    if not sha:
        raise ValueError("Node's checksum list does not name " + name)
    data = cached(cache, name, f"https://nodejs.org/dist/{ver}/{name}", sha)
    dest = app / "runtime" / "node"
    dest.mkdir(parents=True, exist_ok=True)
    top = f"node-{ver}-win-x64/"
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            n = info.filename.replace("\\", "/")
            if not n.startswith(top) or ".." in n.split("/"):
                continue
            rel = n[len(top):]
            if not rel or info.is_dir():
                continue
            out = dest / rel
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(z.read(info))
    return ver


def node_libraries(app: Path) -> None:
    say("installing the office's Node libraries")
    if os.name == "nt":
        node = app / "runtime" / "node" / "node.exe"
        cli = app / "runtime" / "node" / "node_modules" / "npm" / "bin" / "npm-cli.js"
        cmd = [str(node), str(cli), "ci", "--omit=dev", "--no-audit", "--no-fund"]
    else:
        cmd = ["npm", "ci", "--omit=dev", "--no-audit", "--no-fund"]
    p = subprocess.run(cmd, cwd=app, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=900)
    if p.returncode != 0:
        raise ValueError("the Node libraries would not install: " + (p.stderr or p.stdout)[-600:])


# ---------------------------------------------------------------------------
# an icon and a licence
# ---------------------------------------------------------------------------

def _png(size: int) -> bytes:
    """The program's mark: four panes of glass in a deep green tile."""
    bg, glass, edge = (31, 95, 79), (214, 236, 228), (31, 95, 79)
    r = size * 0.18
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            cx = min(x, size - 1 - x)
            cy = min(y, size - 1 - y)
            inside = not (cx < r and cy < r and (r - cx) ** 2 + (r - cy) ** 2 > r * r)
            if not inside:
                row += bytes((0, 0, 0, 0))
                continue
            pad, gap = size * 0.2, max(1, size // 24)
            mid = size / 2
            in_frame = pad <= x < size - pad and pad <= y < size - pad
            in_gap = abs(x - mid) < gap / 2 + 0.5 or abs(y - mid) < gap / 2 + 0.5
            c = glass if in_frame and not in_gap else (edge if in_frame else bg)
            row += bytes((*c, 255))
        rows.append(bytes(row))
    raw = zlib.compress(b"".join(rows), 9)

    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", raw) + chunk(b"IEND", b""))


def write_icon(path: Path) -> None:
    sizes = (16, 32, 48, 256)
    pngs = [_png(s) for s in sizes]
    head = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    entries = b""
    for s, data in zip(sizes, pngs):
        entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(head + entries + b"".join(pngs))


# ---------------------------------------------------------------------------
# Inno Setup
# ---------------------------------------------------------------------------

def find_iscc() -> str:
    cands = [os.environ.get("ISCC", "")]
    for base in (os.environ.get("ProgramFiles(x86)", ""), os.environ.get("ProgramFiles", ""),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
        if base:
            cands.append(os.path.join(base, "Inno Setup 6", "ISCC.exe"))
    cands.append(shutil.which("iscc") or "")
    for c in cands:
        if c and os.path.exists(c):
            return c
    return ""


def ensure_iscc() -> str:
    iscc = find_iscc()
    if iscc or os.name != "nt" or not shutil.which("winget"):
        return iscc
    say("Inno Setup is not installed — installing it with winget (for this user only)")
    subprocess.run(["winget", "install", "-e", "--id", "JRSoftware.InnoSetup", "--scope", "user",
                    "--accept-package-agreements", "--accept-source-agreements"], timeout=900)
    return find_iscc()


def compile_setup(app: Path, out: Path, version: str, name: str) -> Path:
    iscc = ensure_iscc()
    if not iscc:
        raise ValueError("Inno Setup 6 is needed to make Setup.exe — install it from jrsoftware.org "
                         "(or with: winget install JRSoftware.InnoSetup) and build again")
    base = f"{name.lower()}-{version}-setup"
    cmd = [iscc, f"/DAppName={name}", f"/DAppVersion={version}", f"/DSourceDir={app}", f"/DOutputDir={out}",
           f"/DOutputBase={base}", str(ROOT / "installer" / "glasswork.iss")]
    say("compiling Setup.exe")
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=1800)
    exe = out / (base + ".exe")
    if p.returncode != 0 or not exe.exists():
        raise ValueError("Inno Setup could not make it: " + (p.stdout + p.stderr)[-800:])
    return exe


def build(out_dir, product_json="", stage_only=False, cache=None) -> dict:
    out = Path(out_dir).resolve()
    app = out / "stage" / "app"
    if app.exists():
        shutil.rmtree(app)
    app.mkdir(parents=True)
    cache = Path(cache or (Path.home() / ".glasswork-build-cache"))
    version = stage_program(app, product_json)
    name = json.loads((app / "config" / "product.json").read_text(encoding="utf-8")).get("name") or "Glasswork"
    write_icon(app / "installer" / "glasswork.ico")
    py = python_runtime(app, cache)
    python_libraries(app)
    node = node_runtime(app, cache)
    node_libraries(app)
    result = {"ok": True, "version": version, "python": py, "node": node, "stage": str(app)}
    if stage_only:
        return result
    exe = compile_setup(app, out, version, name)
    result.update(installer=str(exe), bytes=exe.stat().st_size,
                  sha256=hashlib.sha256(exe.read_bytes()).hexdigest())
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Make the one-click Windows installer.")
    ap.add_argument("--out", default=str(ROOT / "build"))
    ap.add_argument("--product-json", default="", help="a product.json to ship, or - to read it from stdin")
    ap.add_argument("--stage-only", action="store_true", help="gather everything, do not compile")
    ap.add_argument("--cache", default="")
    a = ap.parse_args(argv)
    pj = sys.stdin.read() if a.product_json == "-" else (Path(a.product_json).read_text(encoding="utf-8")
                                                          if a.product_json else "")
    try:
        r = build(a.out, pj, a.stage_only, a.cache or None)
    except Exception as e:
        r = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    print(json.dumps(r))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
