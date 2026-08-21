"""
backend.py — the back office. Open the settings screens without a terminal.

    python backend.py                open the setup screen (starts the server if needed)
    python backend.py --password     set or change the admin password
    python backend.py --no-password  remove it
    python backend.py --lock-down    also refuse anything not from this machine
    python backend.py --where        print what is configured and where it lives
    python backend.py --stop         stop a server this file started

Double-clicking works on Windows and macOS. That is the point of the file: the
owner asked for somewhere to change his API keys without opening PowerShell,
because a settings screen you can only reach through a terminal is a settings
screen that does not get used.

WHAT THIS IS, NEXT TO run.py

  run.py       the agency. Starts the office, runs the day, runs the tests.
  backend.py   the back office. The keys, the connectors, the caps, the
               password. Same server underneath — this one lands you on /setup
               instead of the floor, and it is the only file that touches the
               password.

WHERE YOUR KEYS ACTUALLY LIVE, AND WHY IT MATTERS

  .env                            SMTP password, ntfy topic, site token, caps
  config/providers.local.json     model credentials and shelf API keys
  config/site.json                the website address (public, and git-ignored so
                                  an update can never wipe what you saved)

The first two are git-ignored and never leave the machine. That is why this file
never prints a key back at you and never sends one anywhere: it opens the screen
that writes them and gets out of the way.

ABOUT THE PASSWORD

The server listens on 127.0.0.1 — this computer only — so on a laptop you are
already signed into, a password mostly protects you from someone sitting down at
it. Worth having if the machine is shared, and worth having the moment you host
this anywhere. It is a real gate, not decoration: PBKDF2-SHA256, 240,000 rounds,
random salt, and only the hash is stored. Lose it and you clear it by deleting
ADMIN_PASSWORD_HASH from .env, which is exactly the level of recovery a
single-owner tool should have.
"""
import argparse
import base64
import getpass
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV = ROOT / ".env"
PY = sys.executable or "python3"
PORT = os.environ.get("PORT", "3000")
BASE = f"http://127.0.0.1:{PORT}"
PIDFILE = ROOT / ".backend.pid"

ITERATIONS = 240_000


def say(line=""):
    print(line, flush=True)


def agency_banner() -> str:
    try:
        sys.path.insert(0, str(ROOT))
        from coordination import site
        return site.agency_name().upper() + " — BACK OFFICE"
    except Exception:
        return "WORLD DEAL TECHNICAL AGENCY — BACK OFFICE"


def head(title):
    say()
    say("=" * 62)
    say("  " + title)
    say("=" * 62)


# ---------------------------------------------------------------------------
# .env, read and written a line at a time
# ---------------------------------------------------------------------------

def read_env() -> dict:
    out = {}
    if ENV.exists():
        for line in ENV.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def write_env(key, value):
    """
    Set or remove ONE key, leaving every other line exactly as it was.

    Rewriting the whole file from a parsed dict would drop comments and reorder
    everything, and this file is the one place the owner's secrets live — it
    should come back looking like he left it.
    """
    lines = (ENV.read_text(encoding="utf-8", errors="replace").splitlines()
             if ENV.exists() else [])
    out, done = [], False
    for line in lines:
        if line.split("=", 1)[0].strip() == key and not line.lstrip().startswith("#"):
            if value is not None:
                out.append(f"{key}={value}")
            done = True                      # value None => the line is dropped
        else:
            out.append(line)
    if value is not None and not done:
        out.append(f"{key}={value}")
    ENV.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    try:
        os.chmod(ENV, 0o600)                 # nobody else on the machine, either
    except Exception:
        pass


# ---------------------------------------------------------------------------
# the password
# ---------------------------------------------------------------------------

def hash_password(plain: str) -> str:
    """pbkdf2$<iterations>$<salt-b64>$<hash-b64> — the format server.js reads."""
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"), salt, ITERATIONS, 32)
    return (f"pbkdf2${ITERATIONS}${base64.b64encode(salt).decode()}"
            f"${base64.b64encode(dk).decode()}")


def check_password(plain: str, stored: str) -> bool:
    try:
        tag, iters, salt_b64, hash_b64 = stored.split("$")
        if tag != "pbkdf2":
            return False
        dk = hashlib.pbkdf2_hmac("sha256", plain.encode("utf-8"),
                                 base64.b64decode(salt_b64), int(iters),
                                 len(base64.b64decode(hash_b64)))
    except Exception:
        return False
    return secrets.compare_digest(dk, base64.b64decode(hash_b64))


def set_password():
    head("Admin password")
    current = read_env().get("ADMIN_PASSWORD_HASH", "")
    if current:
        old = getpass.getpass("  Current password: ")
        if not check_password(old, current):
            say("  That is not the current password. Nothing was changed.")
            return False
    say("  Choose a password. Nothing is stored but a salted hash of it —")
    say("  if you forget it, delete ADMIN_PASSWORD_HASH from .env to clear it.")
    a = getpass.getpass("  New password: ")
    if len(a.strip()) < 8:
        say("  Too short — use at least 8 characters. Nothing was changed.")
        return False
    b = getpass.getpass("  Again: ")
    if a != b:
        say("  They do not match. Nothing was changed.")
        return False
    write_env("ADMIN_PASSWORD_HASH", hash_password(a))
    say()
    say("  Set. Restart the back office and it will ask for it.")
    return True


def clear_password():
    head("Admin password")
    if not read_env().get("ADMIN_PASSWORD_HASH"):
        say("  There was no password set.")
        return True
    old = getpass.getpass("  Current password (to confirm it is you): ")
    if not check_password(old, read_env()["ADMIN_PASSWORD_HASH"]):
        say("  That is not the current password. It has been left ON.")
        return False
    write_env("ADMIN_PASSWORD_HASH", None)
    say("  Removed. The back office is open again on this machine.")
    return True


# ---------------------------------------------------------------------------
# the server
# ---------------------------------------------------------------------------

def running() -> bool:
    try:
        with urllib.request.urlopen(BASE + "/api/status", timeout=2):
            return True
    except urllib.error.HTTPError:
        return True                          # answered, even if it said 401
    except Exception:
        return False


def start_server(lock_down=False):
    if running():
        say(f"  Already running on {BASE}")
        return None
    if not (ROOT / "node_modules").exists():
        say("  Installing dependencies (first run only)...")
        subprocess.run(["npm", "install"], cwd=ROOT, timeout=900)
    env = {**os.environ, "PORT": PORT}
    if lock_down:
        env.pop("BIND_HOST", None)           # 127.0.0.1 is the default anyway
    try:
        proc = subprocess.Popen(["node", "server.js"], cwd=ROOT, env=env)
    except FileNotFoundError:
        say("  Node.js is not installed. Get it from nodejs.org, then run this again.")
        return None
    PIDFILE.write_text(str(proc.pid))
    for _ in range(40):
        time.sleep(0.25)
        if running():
            break
    say(f"  Started on {BASE}")
    return proc


def stop_server():
    head("Stopping the back office")
    if not PIDFILE.exists():
        say("  Nothing here says this file started a server.")
        say("  If one is running, it was started another way — close that window.")
        return
    try:
        pid = int(PIDFILE.read_text().strip())
        os.kill(pid, 9 if os.name == "nt" else 15)
        say(f"  Stopped (pid {pid}).")
    except Exception as e:
        say(f"  Could not stop it: {e}")
    finally:
        PIDFILE.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# what is configured
# ---------------------------------------------------------------------------

def where():
    """
    What is set and what is not — WITHOUT printing a single secret.

    A "show me my config" command that echoes keys is how they end up in a
    screenshot, a support chat or a terminal scrollback. Set or not set is all
    anybody needs from a status line.
    """
    head("What is configured")
    e = read_env()
    sets = lambda k: "set" if (e.get(k) or "").strip() else "—"

    say("  .env                       (git-ignored, this machine only)")
    for k, label in [("SITE_URL", "Website address"),
                     ("NTFY_TOPIC", "ntfy topic"),
                     ("SMTP_HOST", "Mail host"),
                     ("SMTP_PASS", "Mail password"),
                     ("OUTREACH_DOMAIN", "Sending domain"),
                     ("DATAFORSEO_LOGIN", "DataForSEO login"),
                     ("SITE_API_TOKEN", "Site admin token"),
                     ("DAILY_SPEND_CAP_USD", "Daily spend cap"),
                     ("OUTREACH_DAILY_CAP", "Daily outreach cap")]:
        say(f"    {label:24} {sets(k)}")
    say(f"    {'Admin password':24} "
        + ("ON" if e.get("ADMIN_PASSWORD_HASH") else "off — anyone at this computer"))

    # These three ARE printed in full, and that is the point. A rate is not a
    # secret, it is the number every cost line in every report is multiplied by
    # — the owner needs to see it to know whether to believe the total.
    say()
    say("  Crawler rates (your own estimates, not a quote from anyone)")
    try:
        from pipelines import fetcher
        say(f"    {'Per plain fetch':24} ${fetcher.cost_per_fetch():.4f}"
            + ("" if (e.get("ZYTE_COST_PER_FETCH") or "").strip() else "   (built-in default)"))
        say(f"    {'Per render':24} ${fetcher.cost_per_render():.4f}"
            + ("" if (e.get("ZYTE_COST_PER_RENDER") or "").strip() else "   (built-in default)"))
    except Exception as ex:
        say(f"    could not be read: {ex}")
    own = (e.get("CRAWLER_ON_OWN_SITE") or "").strip().lower() in ("1", "true", "yes", "on")
    say(f"    {'Paid on your own site':24} "
        + ("ON — your pages may be rendered and billed" if own
           else "off — your own site is read for free"))

    store = ROOT / "config" / "providers.local.json"
    say()
    say("  config/providers.local.json   (git-ignored)")
    if not store.exists():
        say("    nothing saved yet — add your models and shelf keys on /setup")
    else:
        try:
            d = json.loads(store.read_text() or "{}")
            creds = d.get("credentials") or []
            say(f"    {len(creds)} provider(s), "
                f"{sum(len(c.get('models') or []) for c in creds)} model(s), "
                f"{len(d.get('tools') or [])} shelf API(s), "
                f"{len(d.get('routing') or {})} sector(s) routed")
        except Exception as ex:
            say(f"    could not be read: {ex}")

    site = ROOT / "config" / "site.json"
    say()
    say("  config/site.json              (git-ignored — an update cannot wipe it)")
    say(f"    {site.read_text().strip() if site.exists() else 'not written yet'}")
    say()
    say("  No key is printed by this command, on purpose. Open the screen to change one.")


def main():
    ap = argparse.ArgumentParser(description="The back office: keys, connectors, password.")
    ap.add_argument("--password", action="store_true", help="set or change the admin password")
    ap.add_argument("--no-password", action="store_true", help="remove the admin password")
    ap.add_argument("--lock-down", action="store_true",
                    help="start bound to this machine only (the default anyway)")
    ap.add_argument("--where", action="store_true", help="what is configured, no secrets shown")
    ap.add_argument("--usage", action="store_true",
                    help="how much of each paid API you have used, and what is left")
    ap.add_argument("--stop", action="store_true", help="stop a server this file started")
    ap.add_argument("--no-open", action="store_true", help="do not open a browser")
    a = ap.parse_args()

    say(agency_banner())

    if a.stop:
        return stop_server()
    if a.where:
        return where()
    if a.usage:
        from coordination import usage
        print()
        print(usage.as_text())
        return None
    if a.no_password:
        return None if clear_password() else sys.exit(1)
    if a.password:
        return None if set_password() else sys.exit(1)

    head("Opening the back office")
    proc = start_server(lock_down=a.lock_down)
    locked = bool(read_env().get("ADMIN_PASSWORD_HASH"))
    url = BASE + ("/login" if locked else "/setup")
    say(f"  {url}")
    say("  " + ("Locked — it will ask for your password." if locked else
                "Not locked. Set a password with: python backend.py --password"))
    say()
    say("  This is where your API keys, connectors and caps live.")
    say("  Leave this window open while you use it. Ctrl+C to stop.")
    if not a.no_open:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    if proc is None:
        return                               # a server was already up; leave it be
    try:
        proc.wait()
    except KeyboardInterrupt:
        say()
        say("  Stopping.")
        proc.terminate()
    finally:
        PIDFILE.unlink(missing_ok=True)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        say("\nStopped.")
    if os.name == "nt" and not sys.stdin.isatty():
        input("\nPress Enter to close...")
