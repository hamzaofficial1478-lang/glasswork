"""
product.py — what the program is called, which version this is, and where its
new versions are published. One file, config/product.json, so renaming the
product or moving where it is published is one edit, not a search through
every screen.

    python -m coordination.product            the name and version
    python -m coordination.product --json

This is the PRODUCT, not the business using it. The business's own name lives
in its profile and settings (coordination/site.py); the product name is what
the program itself is called on the install, the terms and the Help screen.

"repository" is the program's open-source home on GitHub (owner/name). New
versions are published there as releases; asking whether one is out sends
nothing about the person or their copy (coordination/updates.py).
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILE = ROOT / "config" / "product.json"
DEFAULTS = {"name": "Glasswork", "version": "0.0.0", "tagline": "", "support_email": "",
            "support_url": "", "repository": ""}


def info() -> dict:
    try:
        d = json.loads(FILE.read_text(encoding="utf-8"))
    except Exception:
        d = {}
    return {**DEFAULTS, **{k: v for k, v in (d if isinstance(d, dict) else {}).items() if v is not None}}


def name() -> str:
    return str(info()["name"] or DEFAULTS["name"])


def version() -> str:
    return str(info()["version"] or DEFAULTS["version"])


def parse_version(v) -> tuple:
    """'0.6.10' -> (0, 6, 10). Anything that is not a number counts as 0, so a bad
    answer can never look newer than it is."""
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
                          "support_email": d["support_email"], "support_url": d["support_url"]}))
    else:
        print(f"{d['name']} {d['version']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
