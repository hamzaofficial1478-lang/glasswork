"""
diagnostics.py — a report a beta tester can copy and send when something goes
wrong, with nothing private in it.

    python -m coordination.diagnostics            the report, as text
    python -m coordination.diagnostics --json

WHAT IS IN IT

  The program's version, the computer's system and the versions of Python and
  Node; which optional libraries are installed; which AI company the agency
  thinks with (the company, never the key); every desk that failed or is stuck,
  with the reason it gave; open faults; and what each sector says stops it
  from running.

WHAT IS NEVER IN IT

  Keys, passwords, tokens, email addresses, phone numbers, the website's
  address, lead names or anything a lead wrote. Every line passes through
  scrub() on the way out, which removes anything shaped like a secret or a
  contact, and the person reads the whole report on screen before copying it.
"""
import importlib.util
import json
import platform
import re
import shutil
import subprocess
import sys
import time

try:
    from . import product
except ImportError:  # pragma: no cover
    import product

LIBRARIES = ("requests", "cryptography", "sqlcipher3", "phonenumbers", "anthropic", "google.auth")

_SECRETS = [
    (re.compile(r"\b(sk|pk|rk)-[A-Za-z0-9_\-]{12,}"), "[key removed]"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}"), "[key removed]"),
    (re.compile(r"\b(?:ghp|gho|github_pat)_[A-Za-z0-9_]{12,}"), "[token removed]"),
    (re.compile(r"\b(?:Bearer|Basic)\s+[A-Za-z0-9+/=._\-]{8,}", re.I), "[credential removed]"),
    (re.compile(r"\b[A-Za-z0-9+/_\-]{32,}={0,2}"), "[long code removed]"),
    (re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"), "[email removed]"),
    (re.compile(r"https?://[^\s'\")]+", re.I), "[web address removed]"),
    (re.compile(r"(?:\+|00)?\d[\d\s().\-]{7,}\d"), "[number removed]"),
]


def scrub(text) -> str:
    s = str(text if text is not None else "")
    for pat, repl in _SECRETS:
        s = pat.sub(repl, s)
    return s


def _version_of(cmd) -> str:
    exe = shutil.which(cmd[0])
    if not exe:
        return "not found"
    try:
        p = subprocess.run([exe] + cmd[1:], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=15)
        return (p.stdout or p.stderr).strip().splitlines()[0][:60] if (p.stdout or p.stderr).strip() else "unknown"
    except Exception:
        return "unknown"


def gather() -> dict:
    info = product.info()
    out = {"product": info["name"], "version": info["version"], "made_at": time.strftime("%Y-%m-%d %H:%M"),
           "system": f"{platform.system()} {platform.release()} ({platform.machine()})",
           "python": platform.python_version(), "node": _version_of(["node", "--version"]),
           "libraries": {lib: importlib.util.find_spec(lib.split(".")[0]) is not None for lib in LIBRARIES},
           "engine": "", "desks": [], "faults": [], "cannot_run": []}
    try:
        from providers import engines
        b = engines.brief()
        out["engine"] = f"{b.get('name') or b.get('engine') or 'not chosen'}" + ("" if b.get("ready") else " (not ready)")
    except Exception as e:
        out["engine"] = f"could not read ({type(e).__name__})"
    try:
        from . import state
        snap = state.build()
        for r in snap.get("rooms", []):
            for a in r.get("agents", []):
                if a.get("s") == "blocked":
                    out["desks"].append({"sector": r["key"], "desk": a.get("n", ""), "problem": a.get("problem", "stopped"),
                                         "said": scrub(a.get("t", ""))[:240]})
            if r.get("enabled") is False:
                out["desks"].append({"sector": r["key"], "desk": "(whole sector)", "problem": "switched off", "said": ""})
        for s in (snap.get("readiness") or {}).get("sectors", []):
            if s.get("state") == "blocked":
                out["cannot_run"].append({"sector": s.get("room") or s.get("sector"), "why": scrub(s.get("says", ""))[:240]})
    except Exception as e:
        out["desks_error"] = type(e).__name__
    try:
        from . import faults
        for f in faults.open_faults(limit=15):
            out["faults"].append({"sector": f.get("sector", ""), "kind": f.get("kind", ""), "severity": f.get("severity", ""),
                                  "what": scrub(f.get("what", ""))[:240]})
    except Exception as e:
        out["faults_error"] = type(e).__name__
    return out


def as_text(d: dict) -> str:
    lines = [f"{d['product']} {d['version']} — support report, {d['made_at']}",
             f"System: {d['system']} · Python {d['python']} · Node {d['node']}",
             "Libraries: " + ", ".join(f"{k} {'yes' if v else 'NO'}" for k, v in d["libraries"].items()),
             f"AI engine: {d['engine'] or 'not chosen'}", ""]
    lines.append("Desks that failed or are stuck:" if d["desks"] else "Desks that failed or are stuck: none")
    lines += [f"  - {x['sector']} · {x['desk']} · {x['problem']}: {x['said']}" for x in d["desks"]]
    lines.append("Sectors that cannot run:" if d["cannot_run"] else "Sectors that cannot run: none")
    lines += [f"  - {x['sector']}: {x['why']}" for x in d["cannot_run"]]
    lines.append("Open faults:" if d["faults"] else "Open faults: none")
    lines += [f"  - {x['sector']} · {x['kind']} · {x['severity']}: {x['what']}" for x in d["faults"]]
    lines += ["", "Nothing private is in this report: no keys, passwords, email addresses, phone numbers, "
              "web addresses or lead details."]
    return "\n".join(scrub_line(l) for l in lines)


def scrub_line(line: str) -> str:
    # the version and system lines are ours and safe; everything else is scrubbed again on the way out
    return line if line.startswith(("System:", "Libraries:")) or " — support report, " in line else scrub(line)


def main(argv=None) -> int:
    d = gather()
    if "--json" in (argv if argv is not None else sys.argv[1:]):
        print(json.dumps({"ok": True, "report": d, "text": as_text(d)}, ensure_ascii=False))
    else:
        print(as_text(d))
    return 0


if __name__ == "__main__":
    sys.exit(main())
