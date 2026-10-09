"""
updates.py — is a newer version of the program out? Asked only when the
person presses "Check for a new version"; never on its own.

    python -m coordination.updates --check --json

    "each program on pc is remains to itself and that pc not to anything else"

WHERE NEW VERSIONS ARE

  The program is open source. Each new version is published as a release on
  its GitHub page (config/product.json, "repository"), with the Windows Setup
  attached — built and tested on Windows by .github/workflows/windows-installer.yml
  before anyone can download it.

WHAT ASKING SENDS

  Nothing about the person or their copy: a plain request for the newest
  release, the same one anyone visiting the page makes. No number for the
  copy, no version, no name.

GETTING IT

  A git copy updates itself (run.py pulls each time it starts). An installed
  copy downloads the new Setup and runs it over the old one: the same install
  is updated in place, and the person's work — kept in its own folder — is not
  touched.
"""
import argparse
import json
import os
import sys
import urllib.request

try:
    from . import product
except ImportError:  # pragma: no cover - run as a script
    import product

ROOT = product.ROOT
TIMEOUT = 20
API = "https://api.github.com/repos/{repo}/releases/latest"


def is_git_copy() -> bool:
    return (ROOT / ".git").exists()


def _fetch(url: str, limit: int = 2 * 1024 * 1024, timeout=TIMEOUT) -> bytes:
    if not str(url).lower().startswith("https://"):
        raise ValueError("only https:// is asked")
    req = urllib.request.Request(url, headers={"User-Agent": product.name(),
                                               "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise ValueError("the answer was larger than expected")
    return data


def check(fetch=_fetch) -> dict:
    """Is a newer version out? Never downloads or installs anything."""
    info = product.info()
    here = info["version"]
    out = {"ok": True, "version": here, "name": info["name"], "git": is_git_copy(), "available": False}
    if out["git"]:
        out["why"] = ("This copy updates itself from git each time it starts. Close the program "
                      "and open it again to get the newest version.")
        return out
    repo = (os.environ.get("GLASSWORK_REPOSITORY") or info.get("repository") or "").strip().strip("/")
    if not repo or repo.count("/") != 1:
        out["why"] = "This copy has no place set to look for new versions."
        return out
    page = f"https://github.com/{repo}/releases"
    try:
        rel = json.loads(fetch(API.format(repo=repo)).decode("utf-8"))
    except Exception as e:
        return {**out, "ok": False, "page_url": page,
                "error": f"The program's release page could not be reached ({type(e).__name__}). "
                         f"You can look yourself at {page}"}
    latest = str(rel.get("tag_name") or rel.get("name") or "").lstrip("vV")
    newer = product.parse_version(latest) > product.parse_version(here)
    github = lambda u: str(u or "").startswith("https://github.com/")
    setup = next((a.get("browser_download_url") for a in rel.get("assets") or []
                  if str(a.get("name", "")).lower().endswith("-setup.exe") and github(a.get("browser_download_url"))), "")
    out.update({"available": newer, "latest": latest, "notes": str(rel.get("body") or "")[:2000],
                "page_url": rel["html_url"] if github(rel.get("html_url")) else page,
                "download_url": setup if newer else ""})
    out["why"] = ("Download the new Setup and run it over this one — your work is kept." if newer
                  else f"You have the newest version ({here}).")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Is a newer version of the program out?")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    r = check()
    if a.json:
        print(json.dumps(r, ensure_ascii=False))
    else:
        print(r.get("why") or r.get("error") or json.dumps(r, indent=1))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
