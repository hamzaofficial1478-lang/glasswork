"""
run.py — the one file you start the agency from.

    python run.py                 open the office (updates first, then the UI)
    python run.py --day           run a full day's cycle in the terminal
    python run.py --day --force   run it even though the check found problems
    python run.py --check         health check: what is configured, what is not
    python run.py --site          test your real website end to end (changes nothing)
    python run.py --site --index  the same, and build the Algolia search index
    python run.py --tests         run every test suite
    python run.py --no-update     skip the git pull

Double-clicking this file works too, on Windows and on a Mac.

WHAT IT DOES, IN ORDER

  1. Pulls the latest code, so you are never running yesterday's build. It only
     pulls when your own work is committed — an uncommitted change is never
     thrown away to make an update fit.
  2. Builds the filing cabinet if this is a fresh copy.
  3. Tells you plainly what is not configured yet, instead of letting a sector
     fail later with an error you have to go and read.
  4. Starts the server and opens the office.

A DAY, IF YOU RUN --day

The MD reads the cabinet, has its plan challenged by the Critic, and posts the
surviving work to the board. Each sector then claims its own task. Report scores
what happened. That order matters: nothing here calls a pipeline directly out of
turn, so the coordination layer — claims, locks, the red-line hooks — stays in
the path exactly as it is when the agency runs itself.
"""
import argparse
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = sys.executable or "python3"
PORT = os.environ.get("PORT", "3000")


def say(line=""):
    print(line, flush=True)


def agency_banner() -> str:
    """The agency's own name, from .env if he has set one."""
    try:
        sys.path.insert(0, str(ROOT))
        from coordination import site
        return site.agency_name().upper()
    except Exception:
        return "WORLD DEAL TECHNICAL AGENCY"


def head(title):
    say()
    say("=" * 62)
    say("  " + title)
    say("=" * 62)


def sh(args, **kw):
    """Run a command, return (code, output). Never raises."""
    try:
        p = subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                           timeout=kw.pop("timeout", 300), **kw)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return 127, f"{args[0]} is not installed or not on PATH"
    except subprocess.TimeoutExpired:
        return 124, "timed out"


# ---------------------------------------------------------------------------
# 1. UPDATE
# ---------------------------------------------------------------------------

def update():
    """
    Pull the latest code. Refuses if you have uncommitted work, because losing
    the owner's own edits to save him a git command is not a trade worth making.
    """
    head("Checking for updates")
    code, out = sh(["git", "rev-parse", "--is-inside-work-tree"])
    if code != 0:
        say("  Not a git checkout — skipping the update.")
        return

    code, dirty = sh(["git", "status", "--porcelain"])
    if dirty.strip():
        say("  You have uncommitted changes, so nothing was pulled:")
        for line in dirty.strip().splitlines()[:8]:
            say("    " + line)
        say("  Commit or stash them, then run this again to update.")
        return

    code, out = sh(["git", "pull", "--ff-only"], timeout=120)
    if code != 0:
        say("  Could not update (that is fine, carrying on with what you have):")
        say("    " + out.strip().splitlines()[-1] if out.strip() else "    no detail")
        return
    say("  " + ("Already up to date." if "up to date" in out.lower()
                else "Updated to the latest version."))
    if "requirements" in out or "package.json" in out:
        say("  Dependencies changed — installing.")
        sh([PY, "-m", "pip", "install", "-r", "requirements.txt"], timeout=600)
        sh(["npm", "install"], timeout=600)


# ---------------------------------------------------------------------------
# 2 + 3. CABINET AND HEALTH
# ---------------------------------------------------------------------------

def ensure_cabinet():
    db = ROOT / "coordination" / "agency.db"
    if db.exists():
        return
    head("First run — building the filing cabinet")
    code, out = sh([PY, "-m", "coordination.init_db"])
    say("  Cabinet built." if code == 0 else "  Could not build it:\n" + out)


def check():
    """What is ready and what is not. Honest, and it never guesses."""
    head("Health check")
    sys.path.insert(0, str(ROOT))
    ready, missing = [], []

    try:
        from coordination import site
        s = site.status()
        if s["url"]:
            ready.append(f"Website: {s['url']} (from {s['source']})")
        else:
            # Naming the drawers matters here. The owner HAD saved his website;
            # a tracked config file was being reset by every pull and nothing
            # read the copy in .env, so "not set" was both true and useless.
            missing.append("Website: not set. Looked in "
                           + ", ".join(s.get("looked_in", [])) + ". " + s.get("hint", ""))
    except Exception as e:
        missing.append(f"Website: could not read ({e})")

    try:
        from providers import integrations as ig
        env = ig.read_env()
        if (env.get("NTFY_TOPIC") or "").strip():
            ready.append("Notifications: ntfy topic set")
        elif (env.get("TELEGRAM_BOT_TOKEN") or "").strip():
            ready.append("Notifications: Telegram")
        else:
            missing.append("Notifications: none — set NTFY_TOPIC in .env, "
                           "or nobody hears from the agency")
        if (env.get("SMTP_HOST") or "").strip():
            ready.append("Outreach: SMTP configured")
        else:
            missing.append("Outreach: no SMTP — the agency cannot email anyone")
    except Exception as e:
        missing.append(f"Channels: could not read ({e})")

    try:
        from coordination import state
        shelves = state.build().get("shelf", [])
        stocked = [s["shelf"] for s in shelves if s.get("items")]
        if stocked:
            ready.append(f"Shelf: {len(stocked)} of {len(shelves)} stocked "
                         f"({', '.join(stocked)})")
        else:
            missing.append(f"Shelf: all {len(shelves)} empty — no model, no maps key. "
                           "Agents will read their records out rather than reasoning.")
    except Exception as e:
        missing.append(f"Shelf: could not read ({e})")

    for line in ready:
        say("  ok       " + line)
    for line in missing:
        say("  NOT SET  " + line)
    say()
    say(f"  {len(ready)} ready, {len(missing)} still to configure.")
    return not missing


# ---------------------------------------------------------------------------
# A DAY'S WORK
# ---------------------------------------------------------------------------

def day(force=False, quick=False):
    """
    A full day, run through the SAME code the Run button in the office uses.

    This used to keep its own copy of the step list and shell each module itself.
    Two copies of the running order is one copy too many: the button gained a
    preflight, a run record and the owner's per-sector orders, and none of that
    reached anyone who started the day from the terminal. There is one runner
    now (pipelines/day_run.py) and both doors open onto it.
    """
    head("A full day, in the order the handbook sets")
    sys.path.insert(0, str(ROOT))
    from pipelines import day_run

    say("  Checking everything first…")
    pre = day_run.preflight(quick=quick)
    for b in pre["blocking"]:
        say("  BLOCKED  " + b)
    for w in pre["warnings"]:
        say("  warning  " + w)
    if not pre["ready"] and not force:
        say()
        say("  Nothing was run. Fix the blocked items above, or run again with --force.")
        return False

    r = day_run.start(force=force, quick=quick, skip_check=True)
    if not r.get("ran"):
        say("  " + r.get("why", "the day did not start"))
        return False

    st = day_run.status(r["run_id"])
    say()
    for s in st["steps"]:
        mark = {"ok": "ok  ", "idle": "--  ", "failed": "FAIL"}.get(s["status"], "    ")
        say(f"  {mark} {s['label']:34} {(s['summary'] or '')[:110]}")
    say()
    say("  " + r["why"] + " Open the office to see what happened.")
    return r["failed_steps"] == 0


def tests():
    head("Every test suite")
    mods = ["coordination.cabinet_tests", "coordination.floor_tests",
            "coordination.targets_tests", "coordination.hook_tests",
            "coordination.faults_tests", "coordination.comms_tests",
            "coordination.switch_tests", "coordination.gate_tests",
            "coordination.ask_tests", "coordination.triage_tests",
            "coordination.dispatch_tests",
            "providers.tests", "pipelines.md_tests", "pipelines.competitor_tests",
            "pipelines.content_tests",
            "pipelines.leads_tests", "pipelines.security_tests",
            "pipelines.it_tests", "pipelines.site_tests", "pipelines.seo_tests",
            "pipelines.answers_tests", "pipelines.intel_tests",
            "pipelines.report_tests", "pipelines.run_tests"]
    bad = []
    for m in mods:
        code, _ = sh([PY, "-m", m], timeout=420)
        say(f"  {'PASS' if code == 0 else 'FAIL'}  {m}")
        if code:
            bad.append(m)
    say()
    say("All green." if not bad else f"{len(bad)} suite(s) failing: {', '.join(bad)}")
    return not bad


# ---------------------------------------------------------------------------
# 4. THE OFFICE
# ---------------------------------------------------------------------------

def office():
    head("Starting the office")
    if not (ROOT / "node_modules").exists():
        say("  Installing dependencies (first run only)...")
        sh(["npm", "install"], timeout=900)
    url = f"http://localhost:{PORT}/office.html"
    say(f"  {url}")
    say("  Leave this window open. Press Ctrl+C to stop.")
    say()
    try:
        proc = subprocess.Popen(["node", "server.js"], cwd=ROOT)
    except FileNotFoundError:
        say("  Node.js is not installed. Get it from nodejs.org, then run this again.")
        return
    time.sleep(2)
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        proc.wait()
    except KeyboardInterrupt:
        say()
        say("  Stopping.")
        proc.terminate()


def main():
    ap = argparse.ArgumentParser(description="Run the agency.")
    ap.add_argument("--day", action="store_true", help="run a full day's cycle")
    ap.add_argument("--check", action="store_true", help="what is configured")
    ap.add_argument("--site", action="store_true",
                    help="test your real website end to end (read-only)")
    ap.add_argument("--index", action="store_true",
                    help="with --site: also build the Algolia index for real")
    ap.add_argument("--url", default="",
                    help="with --site: test this address instead of the saved one")
    ap.add_argument("--tests", action="store_true", help="run every test suite")
    ap.add_argument("--no-update", action="store_true", help="skip the git pull")
    ap.add_argument("--force", action="store_true",
                    help="with --day: run even though the check found problems")
    ap.add_argument("--quick", action="store_true",
                    help="with --day: skip the live API calls in the check")
    a = ap.parse_args()

    say(agency_banner())
    if not a.no_update:
        update()
    ensure_cabinet()

    if a.tests:
        sys.exit(0 if tests() else 1)
    if a.site:
        # One command, the whole website: can we read it, is it healthy, what
        # does it say you sell, what would the search index hold, how does it
        # score for AEO and GEO, is Search Console connected. Read-only unless
        # --index is given.
        sys.path.insert(0, str(ROOT))
        from pipelines import site_check
        out = site_check.run(url=a.url or None, do_index=a.index)
        say(site_check.as_text(out))
        sys.exit(0 if out.get("ok") else 1)
    if a.check:
        check()
        return
    if a.day:
        check()
        day(force=a.force, quick=a.quick)
        return

    check()
    office()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        say("\nStopped.")
    if os.name == "nt" and not sys.stdin.isatty():
        input("\nPress Enter to close...")
