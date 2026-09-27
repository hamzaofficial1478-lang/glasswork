"""
run.py — the one file you start the agency from.

    python run.py                 open the office (updates first, then the UI)
    python run.py --day           run a full day's cycle in the terminal
    python run.py --day --force   run it even though the check found problems
    python run.py --check         health check: what is configured, what is not
    python run.py --site          test your real website end to end (changes nothing)
    python run.py --site --index  the same, and build the Algolia search index
    python run.py --why           why is a desk red? what its last run said
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
        # encoding is named — see the note in pipelines/day_run.py. Leaving it
        # to the machine is why five suites "failed" on the owner's Windows box
        # while passing everywhere else.
        p = subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           timeout=kw.pop("timeout", 300), **kw)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except FileNotFoundError:
        return 127, f"{args[0]} is not installed or not on PATH"
    except subprocess.TimeoutExpired:
        return 124, "timed out"


def npm_install(timeout=600):
    """
    Install the Node dependencies WITHOUT rewriting the lockfile.

    `npm install` is the command everyone reaches for, and it is the wrong one
    here: it recomputes package-lock.json against whatever npm version happens
    to be on the machine and writes it back. Run from inside this program, that
    left the owner's checkout permanently dirty and made every later `git pull`
    refuse — over a file he had never opened.

    `npm ci` is the command for "install exactly what the project pins". It
    reads the lockfile and does not write it. It is stricter, so it fails when
    the lockfile and package.json genuinely disagree — and THAT is a real
    problem worth falling back for, and worth saying out loud when it happens.
    """
    if (ROOT / "package-lock.json").exists():
        code, out = sh(["npm", "ci"], timeout=timeout)
        if code == 0:
            return True
        say("  npm ci could not use the lockfile, so falling back to npm install.")
        say("  (That means package.json and package-lock.json disagree — the "
            "lockfile will be rewritten and is worth committing.)")
    code, out = sh(["npm", "install"], timeout=timeout)
    if code != 0:
        say("  npm install failed: " + (out.strip().splitlines()[-1][:120]
                                        if out.strip() else "no detail"))
    return code == 0


# ---------------------------------------------------------------------------
# 1. UPDATE
# ---------------------------------------------------------------------------

# Files this program rewrites BY ITSELF and the owner never edits by hand.
#
# THE UPDATE THAT BLOCKED ITSELF. run.py runs `npm install`, npm rewrites
# package-lock.json with whatever npm version is on the machine, the tree is
# now dirty — and the NEXT run refuses to pull because "you have uncommitted
# changes". The owner had not changed anything. This program made the mess and
# then cited it as a reason not to update itself.
#
# Two halves to the fix. `npm ci` below installs from the lockfile without
# rewriting it, which stops the churn at source. This list handles the copies
# already dirtied on machines that have been running the old command for weeks.
SELF_WRITTEN = ("package-lock.json", "package.json.lock", "npm-shrinkwrap.json",
                # Drafts the agency GENERATES from his own site and settings.
                # --set-address rewrote the first of these, and that one save
                # blocked every later `git pull` — the identical accident npm
                # caused above, made by this program's own code the second
                # time round. They are output, not his work: the address lives
                # in .env and the block is rebuilt from it whenever it is
                # used, so restoring these costs nothing at all.
                "paste-into-your-site/1-localbusiness-every-page.html",
                "paste-into-your-site/2-faq-schema-faq-page.html",
                "paste-into-your-site/HEAD-BLOCK-paste-once.php")


def _only_self_written(dirty: str) -> list:
    """
    The dirty files, if EVERY one of them is a file we wrote ourselves.
    Returns [] the moment anything is genuinely the owner's, so one hand-edit
    still stops the pull exactly as before.
    """
    names = []
    for line in (dirty or "").splitlines():
        if not line.strip():
            continue
        # Porcelain is exactly 'XY<space>path'. X and Y are single columns and
        # either may be a SPACE — ' M package-lock.json' is the common one — so
        # this reads by position. Splitting on whitespace put the 'M' in the
        # path and matched nothing, which is the sort of bug that makes a guard
        # silently never fire.
        status, path = line[:2], line[3:].strip().strip('"')
        # '??' is untracked. Never ours to throw away: the owner put it there.
        if "?" in status or path not in SELF_WRITTEN:
            return []
        names.append(path)
    return names


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
        mine = _only_self_written(dirty)
        if mine:
            # Ours, not his. Put them back and carry on rather than making him
            # run a git command to undo something he did not do.
            say("  " + ", ".join(mine) + " was rewritten by npm on the last run"
                " — restoring it and carrying on.")
            sh(["git", "checkout", "--"] + mine)
            code, dirty = sh(["git", "status", "--porcelain"])
        if dirty.strip():
            say("  You have uncommitted changes, so nothing was pulled:")
            for line in dirty.strip().splitlines()[:8]:
                say("    " + line)
            say("  Commit or stash them, then run this again to update.")
            say("  (An untracked file you want to keep is fine — pull with"
                " 'git pull' yourself, git will not touch it.)")
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
        npm_install(timeout=600)


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


def sweep_stale():
    """
    Close anything left 'running' by a program that is no longer here.

    THE SEVENTEEN-HOUR WAIT. He closed the window; the cabinet went on saying
    seo-answers was working, that a lock was held, that the Director owed a
    reply. All of it recorded, none of it true — the processes had gone the
    instant he shut the program, and nothing wrote that down.

    Every reaper in the agency was time-based: twenty minutes for a run, three
    hours for a lock lease. That suits an agent stuck on a slow call and does
    nothing for a process that no longer exists, which is the case that
    actually happens. Runs now carry the process id that owns them, so this
    can ask "is that still running" instead of "how long has it been".

    First thing after the cabinet is open, before any of today's work, because
    the alternative is a fresh run planning its day around yesterday's ghosts.
    """
    try:
        sys.path.insert(0, str(ROOT))
        from coordination import board
        out = board.sweep_dead_processes()
    except Exception as e:
        say(f"  (could not check for leftovers: {e})")
        return
    if not (out["runs_closed"] or out["locks_freed"] or out["days_closed"]):
        return
    head("Tidying up after the last time you closed the program")
    for r in out["runs_closed"]:
        say(f"  {r['agent']} was marked as still working — its program is gone.")
    for l in out["locks_freed"]:
        say(f"  released {l['resource']}, which {l['was']} was holding.")
    for d in out["days_closed"]:
        say(f"  closed day run {d}, which never finished.")
    say("  None of that was really running. The agency starts from the truth.")


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
    # THE SUITE RUNS AGAINST THE PROGRAM, NOT AGAINST HIS SETTINGS.
    #
    # it_release runs this and refuses to touch the website unless it is green,
    # so anything that makes it red on his machine and green on a clean
    # checkout permanently blocks every change to his site. His own .env did
    # exactly that: a test proving "with no SMTP it refuses to send" pops the
    # variable out of the environment, .env supplies it again underneath, and
    # the assertion fails on the one machine where it matters.
    #
    # Set here rather than in each suite so a test written next month cannot
    # forget it. Tests that exist to prove .env behaviour pass an explicit path
    # and are unaffected. See providers/integrations.read_env.
    os.environ["AGENCY_IGNORE_DOTENV"] = "1"
    # And the suite never spends his Claude allowance. On his machine Claude
    # Code is installed and signed in, so a test proving "with no brain this
    # refuses" would otherwise make a real call — slow, and paid for out of the
    # plan the agency needs for real work. Tests of the Claude team stub the
    # call itself. See providers/local_cli.available.
    os.environ["CLAUDE_TEAM_OFF"] = "1"
    # And never through his proxy. Several suites stand up a tiny server on
    # 127.0.0.1 and talk to it. On Windows, Python takes the SYSTEM proxy from
    # the registry for every address — a VPN or an antivirus web shield sets
    # one — and a request to 127.0.0.1 sent through it never arrives, so a
    # suite green everywhere else goes red on his laptop for no reason in the
    # code. NO_PROXY makes Python ignore the registry proxy for the run.
    local = "127.0.0.1,localhost,::1"
    os.environ["NO_PROXY"] = ",".join(x for x in (os.environ.get("NO_PROXY"), local) if x)
    os.environ["no_proxy"] = os.environ["NO_PROXY"]
    mods = ["coordination.cabinet_tests", "coordination.floor_tests",
            "coordination.targets_tests", "coordination.hook_tests",
            "coordination.faults_tests", "coordination.comms_tests",
            "coordination.switch_tests", "coordination.gate_tests",
            "coordination.ask_tests", "coordination.triage_tests",
            "coordination.dispatch_tests",
            "coordination.changelog_tests",
            "coordination.results_tests",
            "coordination.purge_tests",
            "coordination.deskwork_tests",
            "coordination.uae_tests",
            "coordination.inbox_tests",
            "coordination.review_tests", "coordination.chat_actions_tests",
            "coordination.leftovers_tests",
            "coordination.sitepace_tests",
            "coordination.daygate_tests",
            "coordination.honest_tests",
            "coordination.clock_tests",
            "providers.tests", "providers.team_tests",
            "providers.local_tests",
            "pipelines.md_tests", "pipelines.competitor_tests",
            "pipelines.content_tests",
            "pipelines.write_tests", "pipelines.blog_tests",
            "pipelines.leads_tests", "pipelines.harvest_tests",
            "pipelines.outreach_tests", "pipelines.campaign_tests", "pipelines.mailer_tests",
            "pipelines.osm_tests",
            "pipelines.offer_tests",
            "pipelines.find_site_tests", "pipelines.site_api_tests",
            "pipelines.secure_tests",
            "pipelines.security_tests",
            "pipelines.it_tests", "pipelines.fix_tests",
            "pipelines.heal_tests",
            "pipelines.release_tests",
            "pipelines.site_tests", "pipelines.seo_tests", "pipelines.audit_tests",
            "pipelines.answers_tests", "pipelines.intel_tests",
            "pipelines.report_tests", "pipelines.run_tests"]
    # SAY WHAT ACTUALLY FAILED, NOT JUST WHICH FILE.
    #
    # "why program not giving the actaul fatal reason?"
    #
    # Because of the underscore on the line below. Each suite prints the exact
    # check that broke — the name, what it got, what it wanted — and this threw
    # every line of it away and printed the module name. So the release refusal
    # he has been staring at for days could only ever say "FAIL
    # coordination.hook_tests", which tells him a file is unhappy and nothing
    # about why. On a machine I cannot reach, that difference is the whole
    # difference between him being able to fix it and not.
    bad, detail = [], {}
    for m in mods:
        code, out = sh([PY, "-m", m], timeout=420)
        say(f"  {'PASS' if code == 0 else 'FAIL'}  {m}")
        if not code:
            continue
        bad.append(m)
        lines = [l.rstrip() for l in (out or "").splitlines()]
        # The check name, then the got/want pair under it, plus any traceback
        # tail — the three things that let somebody act without re-running it.
        picked, i = [], 0
        while i < len(lines) and len(picked) < 12:
            s = lines[i].strip()
            if s.startswith("FAIL") or s.startswith("Traceback") \
                    or s.startswith("got:") or s.startswith("want:") \
                    or "Error:" in s:
                picked.append(lines[i])
                # carry the two indented lines that follow a FAIL
                for j in (i + 1, i + 2):
                    if j < len(lines) and lines[j].startswith("    "):
                        picked.append(lines[j])
                i += 2
            i += 1
        if not picked:
            picked = [l for l in lines[-6:] if l.strip()]
        detail[m] = picked
        for l in picked:
            say("        " + l.strip()[:160])
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
        npm_install(timeout=900)
    url = f"http://localhost:{PORT}/office.html"
    say(f"  {url}")
    say("  Leave this window open. Press Ctrl+C to stop.")
    say()
    # SUPERVISED: when an update changes server.js on disk, the server finishes
    # what it is answering and exits with RESTART; this loop starts the new
    # version at once. Without it, a pulled update gave him new screens talking
    # to the old server — "unknown action" on a button that existed.
    RESTART = 75
    env = {**os.environ, "OFFICE_SUPERVISED": "1"}
    opened = False
    while True:
        try:
            proc = subprocess.Popen(["node", "server.js"], cwd=ROOT, env=env)
        except FileNotFoundError:
            say("  Node.js is not installed. Get it from nodejs.org, then run this again.")
            return
        if not opened:
            time.sleep(2)
            try:
                webbrowser.open(url)
            except Exception:
                pass
            opened = True
        try:
            code = proc.wait()
        except KeyboardInterrupt:
            say()
            say("  Stopping.")
            proc.terminate()
            return
        if code != RESTART:
            return
        say("  The office was updated — started the new version. Refresh the page.")


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
    ap.add_argument("--why", nargs="?", const="", metavar="SECTOR",
                    help="why is that desk red? reads the last run of every desk")
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
    sweep_stale()

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
    if a.why is not None:
        # A red lamp on the floor already knew why — the run wrote it
        # down. It just needed a mouse and a page that had not
        # reloaded. This is the same rows, as a command.
        from coordination import whyred
        print(whyred.as_text(whyred.look(sector=a.why)))
        return 0

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
