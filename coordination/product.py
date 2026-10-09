"""
product.py — what the program is called, which version this is, and where its
updates come from. One file, config/product.json, so renaming the product or
pointing it at a new update address is one edit, not a search through every
screen.

    python -m coordination.product            the name and version
    python -m coordination.product --json

This is the PRODUCT, not the business using it. The business's own name lives
in its profile and settings (coordination/site.py); the product name is what
the program itself is called on the install, the terms and the update notice.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILE = ROOT / "config" / "product.json"
# THE PUBLISHER'S OWN COPY. Where his Glasswork Home is and the public half of
# his signing key are written here, on his computer, and not into product.json:
# his copy updates itself from git, and a changed tracked file would stop that.
# A release carries them inside its own product.json (coordination/updates).
PUBLISHER = ROOT / "config" / "publisher.local.json"


def publisher_file() -> Path:
    """Where the publisher's settings are (the tests point it at a temp file)."""
    return Path(os.environ.get("GLASSWORK_PUBLISHER_FILE") or PUBLISHER)
# What a release carries of his: where his Home is, and the PUBLIC halves of
# his keys — one checks his signature on updates, one seals what a copy sends
# him. Neither can be used to sign or to open anything.
PUBLIC_KEYS = ("home_url", "update_url", "update_key", "postbox_key", "support_email")
DEFAULTS = {"name": "Glasswork", "version": "0.0.0", "tagline": "", "support_email": "",
            "update_url": "", "update_key": "", "postbox_key": "", "home_url": ""}

# HIS SIDE OF THE PROGRAM — his users room, his inbox, putting versions out,
# the Home's own code and his guides. On his computer only: never in a
# release, never in the installer (both leave these out), so a copy he
# gives out has none of it. A path ending in "/" is a whole folder.
PUBLISHER_ONLY = ("coordination/publisher.py", "coordination/publish_tests.py", "ui/publisher.js",
                  "publish-to-your-site/", "docs/RELEASING.md", "docs/PUBLIC-LAUNCH-PLAN.md", ".github/")


def publisher_only(rel: str) -> bool:
    rel = str(rel).replace("\\", "/")
    if rel.startswith("./"):
        rel = rel[2:]
    return any(rel == p or (p.endswith("/") and rel.startswith(p)) for p in PUBLISHER_ONLY)


def publisher() -> dict:
    try:
        d = json.loads(publisher_file().read_text(encoding="utf-8"))
    except Exception:
        d = {}
    return d if isinstance(d, dict) else {}


def info() -> dict:
    try:
        d = json.loads(FILE.read_text(encoding="utf-8"))
    except Exception:
        d = {}
    out = {**DEFAULTS, **{k: v for k, v in (d if isinstance(d, dict) else {}).items() if v is not None}}
    mine = publisher().get("public") or {}
    out.update({k: mine[k] for k in PUBLIC_KEYS if mine.get(k)})
    return out


def name() -> str:
    return str(info()["name"] or DEFAULTS["name"])


def version() -> str:
    return str(info()["version"] or DEFAULTS["version"])


def parse_version(v) -> tuple:
    """'0.6.10' -> (0, 6, 10). Anything that is not a number counts as 0, so a bad
    manifest can never look newer than it is."""
    out = []
    for part in str(v or "").strip().lstrip("vV").split(".")[:4]:
        digits = "".join(ch for ch in part if ch.isdigit())
        out.append(int(digits) if digits else 0)
    while len(out) < 3:
        out.append(0)
    return tuple(out)


def main(argv=None) -> int:
    d = info()
    if "--json" in (argv if argv is not None else sys.argv[1:]):
        print(json.dumps({"ok": True, "name": d["name"], "version": d["version"], "tagline": d["tagline"],
                          "support_email": d["support_email"], "updates": bool(d["update_url"])}))
    else:
        print(f"{d['name']} {d['version']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
