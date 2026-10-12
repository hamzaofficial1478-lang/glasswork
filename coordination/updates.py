"""
updates.py — keeping a copy of the program up to date, signed and reversible.

    python -m coordination.updates --check --json        is there a newer version?
    python -m coordination.updates --apply --json        download, verify, install
    python -m coordination.updates --rollback --json     put the previous version back

Making, signing and putting out a version is the publisher's side
(coordination/publisher.py), which only his own copy has.

TWO KINDS OF COPY

  A git copy (the owner's own) updates the way it always has: run.py pulls the
  latest code each time it starts, and refuses if he has changes of his own.

  A downloaded copy has no git. It asks an address it was given for a small
  manifest — the newest version, where its zip is, the zip's SHA-256 and a
  signature over both — and installs only a zip that matches them all.

THE RULES IT KEEPS

  - Nothing unsigned is ever installed. The signature is checked with the
    public key in config/product.json; without a key, the check still says a
    version is out, but installing it is refused. A download that does not
    match its checksum, or a signature that does not match the key, is thrown
    away untouched.
  - Only https:// addresses are fetched (and a Home on this computer itself).
  - The person's own things are never touched: their accounts and data, .env,
    every *.local.json, the website cabinets, logs, node_modules and .venv.
  - Every file it replaces is kept first, so --rollback puts the previous
    version back exactly, files it added included.
  - A zip that tries to write outside the program folder is refused whole.
"""
import argparse
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

try:
    from . import product
except ImportError:  # pragma: no cover - run as a script
    import product

ROOT = product.ROOT
MAX_ZIP = 300 * 1024 * 1024
TIMEOUT = 30
BACKUPS = "updates"

# Never written by an update: the person's own data, settings, keys and the
# things their computer built. Compared as folder-relative POSIX paths.
KEEP_DIRS = (".git", "data", "logs", "node_modules", ".venv", "venv", BACKUPS, "__pycache__",
             "runtime")                      # the installer's own Python and Node
KEEP_FILES = (".env", "config/site.json", "mcp/site_connector/connector.py",
              "install.json")                # where the installer was told to keep the data


def _kept(rel: str) -> bool:
    p = PurePosixPath(rel)
    if not p.parts:
        return True
    if p.parts[0] in KEEP_DIRS or "__pycache__" in p.parts:
        return True
    if rel in KEEP_FILES:
        return True
    name = p.name
    if name.endswith(".local.json") or name.endswith((".db", ".db-wal", ".db-shm", ".pyc")):
        return True
    return False


def is_git_copy() -> bool:
    return (ROOT / ".git").exists()


LOCAL = ("http://127.0.0.1", "http://localhost")         # a Home tried out on this computer


def _fetch(url: str, limit: int, timeout=TIMEOUT) -> bytes:
    if not str(url).lower().startswith(("https://",) + LOCAL):
        raise ValueError("updates are only fetched over https://")
    req = urllib.request.Request(url, headers={"User-Agent": f"{product.name()}/{product.version()} updater"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError("the download is larger than an update can be")
    return data


def _signed_text(m: dict) -> bytes:
    """Exactly what the signature covers: the version and the zip's checksum."""
    return f"{m.get('version', '')}\n{str(m.get('sha256', '')).lower()}".encode("utf-8")


def verify(manifest: dict, key_b64: str) -> bool:
    """True only if the manifest's signature was made by the publisher's key."""
    if not key_b64 or not manifest.get("signature"):
        return False
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        pub = Ed25519PublicKey.from_public_bytes(base64.b64decode(key_b64))
        pub.verify(base64.b64decode(manifest["signature"]), _signed_text(manifest))
        return True
    except Exception:
        return False


def check(fetch=_fetch) -> dict:
    """Is a newer version out? Never installs anything."""
    info = product.info()
    here = info["version"]
    out = {"ok": True, "version": here, "name": info["name"], "git": is_git_copy(),
           "available": False, "can_install": False}
    if out["git"]:
        out["why"] = ("This copy updates itself from git each time it starts. Close the program "
                      "and open it again to get the latest version.")
        return out
    url = (os.environ.get("UPDATE_URL") or info["update_url"] or "").strip()
    if not url:
        out["why"] = "No update address is set for this copy, so it cannot look for updates."
        return out
    try:
        # Asked plainly: nothing about this copy is in the question.
        m = json.loads(fetch(url, 1024 * 1024).decode("utf-8"))
    except Exception as e:
        return {**out, "ok": False, "error": f"could not reach the update address ({type(e).__name__})"}
    newer = product.parse_version(m.get("version")) > product.parse_version(here)
    signed = verify(m, info["update_key"])
    out.update({"available": newer, "latest": str(m.get("version", "")), "notes": str(m.get("notes", ""))[:2000],
                "signed": signed, "can_install": newer and signed})
    if newer and not signed:
        out["why"] = ("Version " + str(m.get("version")) + " is out, but it is not signed with this copy's key, "
                      "so it will not be installed automatically.")
    elif not newer:
        out["why"] = f"You have the latest version ({here})."
    return out


def _safe_members(z: zipfile.ZipFile) -> list:
    names = []
    for info in z.infolist():
        n = info.filename.replace("\\", "/")
        p = PurePosixPath(n)
        if n.startswith("/") or ".." in p.parts or (len(n) > 1 and n[1] == ":"):
            raise ValueError(f"the update tries to write outside the program folder ({n})")
        mode = (info.external_attr >> 16) & 0o170000
        if mode == 0o120000:
            raise ValueError(f"the update contains a link ({n})")
        names.append((info, n))
    return names


def apply(fetch=_fetch, install_deps=True) -> dict:
    """Download, verify, back up, install. Returns what happened."""
    if is_git_copy():
        return {"ok": False, "error": "This copy updates itself from git when it starts — close it and open it again."}
    found = check(fetch)
    if not found.get("ok"):
        return found
    if not found.get("available"):
        return {"ok": True, "installed": False, "why": found.get("why", "Nothing to install.")}
    if not found.get("signed"):
        return {"ok": False, "error": found.get("why") or "The update is not signed with this copy's key."}
    info = product.info()
    url = (os.environ.get("UPDATE_URL") or info["update_url"]).strip()
    m = json.loads(fetch(url, 1024 * 1024).decode("utf-8"))
    if not verify(m, info["update_key"]):
        return {"ok": False, "error": "The update's signature did not match — nothing was installed."}
    zip_url = str(m.get("zip") or "")
    if zip_url and not zip_url.lower().startswith(("https://",) + LOCAL):
        zip_url = url.rsplit("/", 1)[0] + "/" + zip_url
    data = fetch(zip_url, MAX_ZIP, timeout=300)
    if hashlib.sha256(data).hexdigest() != str(m.get("sha256", "")).lower():
        return {"ok": False, "error": "The download did not match its checksum — nothing was installed."}
    return install_zip(data, str(m.get("version")), install_deps=install_deps)


def install_zip(data: bytes, new_version: str, install_deps=True) -> dict:
    """Unpack a VERIFIED zip over the program, keeping everything it replaces."""
    tmp = Path(tempfile.mkdtemp(prefix="update-"))
    try:
        zpath = tmp / "update.zip"
        zpath.write_bytes(data)
        with zipfile.ZipFile(zpath) as z:
            members = _safe_members(z)
            files = [(i, n) for i, n in members if not i.is_dir()]
            # A zip made by GitHub or by hand wraps everything in one folder.
            tops = {PurePosixPath(n).parts[0] for _i, n in files}
            strip = len(tops) == 1 and not any(len(PurePosixPath(n).parts) == 1 for _i, n in files)
            if not any(PurePosixPath(n).parts[strip:] == ("run.py",) for _i, n in files):
                return {"ok": False, "error": "That zip is not this program (there is no run.py in it)."}
            old = product.version()
            backup = ROOT / BACKUPS / f"before-{new_version}-{time.strftime('%Y%m%d-%H%M%S')}"
            backup.mkdir(parents=True, exist_ok=True)
            added, replaced, changed = [], [], set()
            for info, n in files:
                rel = "/".join(PurePosixPath(n).parts[1 if strip else 0:])
                if not rel or _kept(rel):
                    continue
                dest = ROOT / rel
                new = z.read(info)
                if dest.exists():
                    if dest.read_bytes() == new:
                        continue
                    (backup / rel).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(dest, backup / rel)
                    replaced.append(rel)
                else:
                    added.append(rel)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(new)
                changed.add(rel)
            record = json.dumps({"from": old, "to": new_version, "at": int(time.time()), "added": added,
                                 "replaced": replaced}, indent=1)
            (backup / "update-record.json").write_text(record, encoding="utf-8")
        deps = []
        if install_deps and "requirements.txt" in changed:
            deps.append(_run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"]))
        if install_deps and ("package.json" in changed or "package-lock.json" in changed):
            deps.append(_run(["npm", "ci", "--no-audit", "--no-fund"]))
        return {"ok": True, "installed": True, "from": old, "version": new_version, "added": len(added),
                "replaced": len(replaced), "backup": str(backup.relative_to(ROOT)), "deps": deps,
                "why": f"Updated from {old} to {new_version}. The office restarts itself in a moment."}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run(cmd) -> dict:
    try:
        p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=900, shell=(os.name == "nt" and cmd[0] == "npm"))
        return {"cmd": " ".join(cmd[:3]), "ok": p.returncode == 0}
    except Exception as e:
        return {"cmd": " ".join(cmd[:3]), "ok": False, "error": type(e).__name__}


def rollback() -> dict:
    """Put back the version before the most recent update."""
    root = ROOT / BACKUPS
    done = sorted([p for p in root.glob("before-*") if (p / "update-record.json").exists()]) if root.exists() else []
    if not done:
        return {"ok": False, "error": "There is no earlier version kept to go back to."}
    last = done[-1]
    rec = json.loads((last / "update-record.json").read_text(encoding="utf-8"))
    for rel in rec.get("replaced", []):
        src = last / rel
        if src.exists():
            (ROOT / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, ROOT / rel)
    for rel in rec.get("added", []):
        p = ROOT / rel
        if p.is_file() and not _kept(rel):
            p.unlink()
    last.rename(last.with_name("undone-" + last.name))
    return {"ok": True, "version": rec.get("from"), "why": f"Back to version {rec.get('from')}. "
            "The office restarts itself in a moment."}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Keep this copy of the program up to date.")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--rollback", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.apply:
        r = apply()
    elif a.rollback:
        r = rollback()
    else:
        r = check()
    if a.json:
        print(json.dumps(r, ensure_ascii=False))
    else:
        print(r.get("why") or r.get("error") or json.dumps(r, indent=1))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
