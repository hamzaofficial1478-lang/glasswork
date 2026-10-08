"""
workspace.py — whose agency this is, which of their websites is open, and
where that website's things are kept.

    python -m coordination.workspace              who is open, and where
    python -m coordination.workspace --json       the same, for server.js

"User can use 2 websites ... the working process will be 1 website at 1 run
 and when the user will want to use the second website the user will switch
 the website" / "there will be different setup for each user"

Going public means one program, several people, each with up to two websites.
Every website keeps its own cabinet, its own settings and its own files: its
leads are its own, its approvals are its own, and a change approved for one can
never be carried out on the other. What belongs to the PERSON rather than the
business — how to reach them, the map and AI keys they pay for, their spending
cap — sits on the account, once, for both websites.

THE LAYOUT

    data/active.json                          who is signed in, which website is open
    data/accounts/<account>/account.json      the account and its websites (no secrets)
    data/accounts/<account>/account.env       settings that belong to the person
    data/accounts/<account>/config/           the same, kept as files
    data/accounts/<account>/sites/<site>/agency.db     that website's cabinet
    data/accounts/<account>/sites/<site>/site.env      that website's settings
    data/accounts/<account>/sites/<site>/config/       that website's files
    data/accounts/<account>/sites/<site>/logs/

BEFORE ANYONE MAKES AN ACCOUNT, NOTHING MOVES. With no account open, every path
below is exactly the one the program has always used (.env, config/,
coordination/agency.db), so the owner's own agency carries on untouched until
he chooses to make his account and bring his website into it.

ONE WEBSITE AT A TIME. Switching is refused while a day's run is going: a run
half on one website and half on the other is how a lead for one business gets
written to on behalf of the other.
"""
import argparse
import json
import os
import re
import secrets
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# "User can use 2 websites". One number, so a bigger plan later is a setting.
MAX_SITES = 2

# Settings that belong to the person, not to either website. Everything else
# in the old single .env is about one business and goes with that website.
ACCOUNT_KEYS = frozenset({
    "AGENCY_NAME", "ADMIN_NAME", "ADMIN_PASSWORD_HASH",
    "NTFY_SERVER", "NTFY_TOPIC", "NTFY_TOKEN",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
    "DAILY_SPEND_CAP_USD", "ZYTE_COST_PER_FETCH", "ZYTE_COST_PER_RENDER",
    "GOOGLE_PLACES_API_KEY", "TOMTOM_API_KEY", "GOOGLE_CSE_ID", "GOOGLE_CSE_KEY",
    "DATAFORSEO_LOGIN", "DATAFORSEO_PASSWORD",
    "PORT",
})

# Files kept on the account (the person's keys and AI team) and on the
# computer (where Claude Code is installed — nobody's in particular). Every
# other config file is about one website.
ACCOUNT_CONFIG = frozenset({"providers.local.json", "claude_team.local.json"})
MACHINE_CONFIG = frozenset({"claude_code.local.json"})


# ---------------------------------------------------------------------------
# where things are
# ---------------------------------------------------------------------------

def data_root() -> Path:
    """Where accounts live. AGENCY_DATA moves it (the test run points it at a temp folder)."""
    return Path(os.environ.get("AGENCY_DATA") or ROOT / "data")


def _read_json(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(p: Path, obj) -> None:
    """Written whole or not at all: a half-written account file locks a person out."""
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)


def _accounts_dir() -> Path:
    return data_root() / "accounts"


def account_dir(account=None) -> Path:
    a = account or (active() or {}).get("account")
    return _accounts_dir() / a if a else ROOT


def site_dir(account=None, site=None) -> Path:
    """The open website's folder — or the program's own folder before any account."""
    if account and site:
        return _accounts_dir() / account / "sites" / site
    a = active()
    if not a:
        return ROOT
    return _accounts_dir() / a["account"] / "sites" / a["site"]


def active():
    """{'account', 'site'} of the website that is open, or None (no account open)."""
    a = _read_json(data_root() / "active.json")
    if not isinstance(a, dict) or not a.get("account") or not a.get("site"):
        return None
    if not (_accounts_dir() / a["account"] / "sites" / a["site"]).is_dir():
        return None
    return {"account": a["account"], "site": a["site"]}


def legacy() -> bool:
    """True until an account is open: every path is the program's original one."""
    return active() is None


def db_path() -> Path:
    """The open website's cabinet. AGENCY_DB (the test run's own) always wins."""
    if os.environ.get("AGENCY_DB"):
        return Path(os.environ["AGENCY_DB"])
    a = active()
    if not a:
        return ROOT / "coordination" / "agency.db"
    return site_dir(a["account"], a["site"]) / "agency.db"


def env_paths():
    """(the person's settings file, the open website's settings file)."""
    a = active()
    if not a:
        return ROOT / ".env", ROOT / ".env"
    return (account_dir(a["account"]) / "account.env",
            site_dir(a["account"], a["site"]) / "site.env")


def env_path_for(key) -> Path:
    """Which file a setting is saved in."""
    mine, site = env_paths()
    return mine if key in ACCOUNT_KEYS else site


def config_path(name) -> Path:
    """Where a config file lives: the computer's, the person's, or the website's."""
    a = active()
    if not a or name in MACHINE_CONFIG:
        return ROOT / "config" / name
    if name in ACCOUNT_CONFIG:
        return account_dir(a["account"]) / "config" / name
    return site_dir(a["account"], a["site"]) / "config" / name


def logs_dir() -> Path:
    a = active()
    return ROOT / "logs" if not a else site_dir(a["account"], a["site"]) / "logs"


# ---------------------------------------------------------------------------
# accounts and websites
# ---------------------------------------------------------------------------

def _slug(text, fallback) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:24].strip("-")
    return f"{s or fallback}-{secrets.token_hex(2)}"


def _host(url) -> str:
    u = (url or "").strip().lower()
    u = re.sub(r"^[a-z]+://", "", u).split("/")[0]
    return u[4:] if u.startswith("www.") else u


def accounts() -> list:
    out = []
    d = _accounts_dir()
    if d.is_dir():
        for p in sorted(d.iterdir()):
            a = _read_json(p / "account.json")
            if isinstance(a, dict) and a.get("id") == p.name:
                out.append(a)
    return out


def account(account_id) -> dict:
    a = _read_json(_accounts_dir() / str(account_id) / "account.json")
    return a if isinstance(a, dict) else {}


def _save(acct: dict) -> None:
    _write_json(_accounts_dir() / acct["id"] / "account.json", acct)


def create_account(name) -> dict:
    """A new account with no website yet. Sign-in and encryption come with it in phase 1b."""
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "an account needs a name"}
    acct = {"id": _slug(name, "account"), "name": name,
            "created": int(time.time()), "sites": []}
    (_accounts_dir() / acct["id"] / "config").mkdir(parents=True, exist_ok=True)
    _save(acct)
    return {"ok": True, "account": acct}


def add_site(account_id, name, url="") -> dict:
    """One more website on the account — never more than MAX_SITES."""
    acct = account(account_id)
    if not acct:
        return {"ok": False, "error": "no such account"}
    if len(acct.get("sites") or []) >= MAX_SITES:
        names = ", ".join(s.get("name") or s["id"] for s in acct["sites"])
        return {"ok": False, "error": f"an account holds {MAX_SITES} websites "
                                      f"({names}) — remove one before adding another"}
    host = _host(url)
    if host and any(_host(s.get("url")) == host for s in acct.get("sites") or []):
        return {"ok": False, "error": f"{host} is already one of this account's websites"}
    name = (name or "").strip() or host or "My website"
    site = {"id": _slug(host or name, "site"), "name": name,
            "url": (url or "").strip(), "created": int(time.time())}
    d = site_dir(acct["id"], site["id"])
    for sub in ("config", "logs"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    acct.setdefault("sites", []).append(site)
    _save(acct)
    return {"ok": True, "site": site, "account": acct}


def _run_going() -> str:
    """The open website's day run, if one is going — '' when it is safe to switch."""
    try:
        from pipelines import day_run
        st = day_run.status()
        run = st.get("run") or {}
        if run.get("status") == "running":
            return run.get("id") or "a run"
    except Exception:
        pass
    return ""


def open_site(account_id, site_id) -> dict:
    """Make this website the open one. Refused while today's run is going."""
    acct = account(account_id)
    site = next((s for s in acct.get("sites") or [] if s["id"] == site_id), None)
    if not site:
        return {"ok": False, "error": "no such website on this account"}
    now = active()
    if now == {"account": account_id, "site": site_id}:
        return {"ok": True, "site": site, "unchanged": True}
    if now and _run_going():
        cur = next((s for s in account(now["account"]).get("sites") or []
                    if s["id"] == now["site"]), {})
        return {"ok": False, "running": True,
                "error": f"today's run on {cur.get('name') or 'the open website'} is still "
                         "going — finish it or press Stop, then switch"}
    _write_json(data_root() / "active.json", {"account": account_id, "site": site_id,
                                              "since": int(time.time())})
    return {"ok": True, "site": site}


def close() -> dict:
    """Nobody's website is open any more (signing out, in phase 1b)."""
    p = data_root() / "active.json"
    if active() and _run_going():
        return {"ok": False, "running": True,
                "error": "today's run is still going — finish it or press Stop first"}
    try:
        p.unlink()
    except FileNotFoundError:
        pass
    return {"ok": True}


# ---------------------------------------------------------------------------
# bringing the owner's current website in
# ---------------------------------------------------------------------------

def _split_env(lines):
    mine, site = [], []
    for line in lines:
        m = re.match(r"^([A-Z0-9_]+)=", line.strip())
        if m:
            (mine if m.group(1) in ACCOUNT_KEYS else site).append(line.rstrip("\n"))
    return mine, site


def bring_in_current(account_id, site_id) -> dict:
    """
    Copy what the program holds today into this account's website: settings
    (split between the person and the website), config files, and the cabinet.
    COPIED, never moved — the originals stay exactly where they are until the
    new place has been proved, so nothing he has can be lost on the way.
    """
    acct = account(account_id)
    if not any(s["id"] == site_id for s in acct.get("sites") or []):
        return {"ok": False, "error": "no such website on this account"}
    adir, sdir = account_dir(account_id), site_dir(account_id, site_id)
    done = []
    env = ROOT / ".env"
    if env.exists():
        mine, site = _split_env(env.read_text(encoding="utf-8").splitlines())
        for p, rows in ((adir / "account.env", mine), (sdir / "site.env", site)):
            if rows:
                p.write_text("\n".join(rows) + "\n", encoding="utf-8")
                try:
                    os.chmod(p, 0o600)
                except OSError:
                    pass
        done.append(".env")
    cfg = ROOT / "config"
    if cfg.is_dir():
        for f in sorted(cfg.iterdir()):
            if not f.is_file() or f.name.endswith(".example.json") or f.name in MACHINE_CONFIG:
                continue
            to = (adir if f.name in ACCOUNT_CONFIG else sdir) / "config" / f.name
            to.parent.mkdir(parents=True, exist_ok=True)
            to.write_bytes(f.read_bytes())
            done.append(f"config/{f.name}")
    db = ROOT / "coordination" / "agency.db"
    if db.exists():
        # The backup API, not a file copy: the cabinet runs in WAL mode and the
        # newest writes live beside the file until a checkpoint.
        src, dst = sqlite3.connect(db), sqlite3.connect(sdir / "agency.db")
        try:
            src.backup(dst)
        finally:
            src.close()
            dst.close()
        done.append("coordination/agency.db")
    acct["brought_in"] = {"site": site_id, "at": int(time.time()), "copied": done}
    _save(acct)
    return {"ok": True, "copied": done}


# ---------------------------------------------------------------------------

def describe() -> dict:
    a = active()
    out = {"legacy": a is None, "data": str(data_root()), "max_sites": MAX_SITES,
           "db": str(db_path()), "env": [str(p) for p in env_paths()],
           "logs": str(logs_dir()), "accounts": len(accounts())}
    if a:
        acct = account(a["account"])
        out.update(account={"id": acct.get("id"), "name": acct.get("name")},
                   sites=acct.get("sites") or [],
                   site=next((s for s in acct.get("sites") or [] if s["id"] == a["site"]), {}),
                   site_dir=str(site_dir()), account_dir=str(account_dir()))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Whose agency, which website, where its files are.")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    d = describe()
    if a.json:
        print(json.dumps(d))
        return 0
    if d["legacy"]:
        print("No account open — the program's original files are in use "
              f"({d['db']}).")
    else:
        print(f"{d['account']['name']} — {d['site'].get('name')} is open "
              f"({len(d['sites'])} of {MAX_SITES} websites).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
