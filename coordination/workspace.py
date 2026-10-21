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
    data/accounts/<account>/account.json      the account; its websites' names and addresses sealed
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
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
    "DAILY_SPEND_CAP_USD", "ZYTE_COST_PER_FETCH", "ZYTE_COST_PER_RENDER",
    "GOOGLE_PLACES_API_KEY", "TOMTOM_API_KEY", "GOOGLE_CSE_ID", "GOOGLE_CSE_KEY",
    "DATAFORSEO_LOGIN", "DATAFORSEO_PASSWORD",
    "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY",
    "PORT",
})

# Files kept on the account (the person's keys and AI team) and on the
# computer (where Claude Code is installed — nobody's in particular). Every
# other config file is about one website.
ACCOUNT_CONFIG = frozenset({"providers.local.json", "claude_team.local.json",
                            "engine.local.json", "report-brand.local.json",
                            "support-reports.local.json", "licence.local.json"})
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


HOSTED = os.environ.get("GLASSWORK_HOSTED") == "1"
_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


def active():
    """
    {'account', 'site'} of the website that is open, or None (no account open).

    The office says which, for the person it is answering (AGENCY_ACCOUNT and
    AGENCY_SITE) — on a hosted office many people are signed in at once, and
    each program works on its own person's website alone. Run on its own, a
    program reads the website last opened on this computer.
    """
    if "AGENCY_ACCOUNT" in os.environ:
        a = {"account": os.environ.get("AGENCY_ACCOUNT"), "site": os.environ.get("AGENCY_SITE")}
    else:
        a = _read_json(data_root() / "active.json")
    if not isinstance(a, dict) or not all(_ID.fullmatch(str(a.get(k) or "")) for k in ("account", "site")):
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


# "the users should not be able to track other users" — an account's name is
# kept sealed with its own key, beside a salted fingerprint that lets the name
# its owner types find it. Nothing on the disk, and nothing on the sign-in
# screen, says who has an account on this computer.

def _norm(name) -> str:
    return " ".join(str(name or "").split()).casefold()


def _name_tag(name, salt) -> str:
    import hashlib
    import hmac
    return hmac.new(str(salt).encode("ascii"), _norm(name).encode("utf-8"), hashlib.sha256).hexdigest()


def _seal_name(acct: dict, master: bytes, name: str) -> None:
    from . import vault
    acct["name_salt"] = secrets.token_hex(16)
    acct["name_tag"] = _name_tag(name, acct["name_salt"])
    acct["name_sealed"] = vault.seal(master, name.encode("utf-8"), "account.json#name").decode("ascii")
    acct.pop("name", None)


def account_name(acct: dict, master=None) -> str:
    """The account's name as its owner sees it ('' to anyone without its key)."""
    if not acct.get("name_sealed"):
        return acct.get("name") or ""
    from . import vault
    m = master if master is not None else master_key(acct.get("id"))
    try:
        return vault.unseal(m, acct["name_sealed"].encode("ascii"), "account.json#name").decode("utf-8") if m else ""
    except Exception:
        return ""


def find_account(who) -> dict:
    """An account by its id, or by the name its owner types (any case)."""
    import hmac
    who = (who or "").strip()
    for a in accounts():
        if a["id"] == who or (a.get("name") and _norm(a["name"]) == _norm(who)) or (
                a.get("name_tag") and hmac.compare_digest(a["name_tag"], _name_tag(who, a.get("name_salt", "")))):
            return a
    return {}


def create_account(name, password=None) -> dict:
    """
    A new account with no website yet. With a password (every real account),
    its data is encrypted: the result carries the master key for the office to
    hold, and the recovery key to show him ONCE.
    """
    from . import vault
    name = (name or "").strip()
    if not name:
        return {"ok": False, "error": "an account needs a name"}
    if find_account(name):
        return {"ok": False, "error": f"there is already an account called {name} on this computer"}
    # The folder is a random number, not the name.
    acct = {"id": "acct-" + secrets.token_hex(5), "name": name, "created": int(time.time()), "sites": []}
    out, master = {"ok": True}, None
    if password is not None:
        try:
            keys, master, recovery = vault.create(password, acct["id"], name)
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        acct["keys"] = keys
        _seal_name(acct, master, name)
        out.update(key=vault.as_env(master), recovery_key=recovery)
    (_accounts_dir() / acct["id"] / "config").mkdir(parents=True, exist_ok=True)
    _save(acct)
    out["account"] = public(acct, master)
    return out


SECRET_FIELDS = ("keys", "name_tag", "name_salt", "name_sealed", "mfa")


def public(acct: dict, master=None) -> dict:
    """An account as it may be shown: never its keys, and its name only to its owner."""
    out = {k: v for k, v in (acct or {}).items() if k not in SECRET_FIELDS}
    out["name"] = account_name(acct or {}, master)
    return out


# ---------------------------------------------------------------------------
# a website's name and address, sealed with its own account's key
# ---------------------------------------------------------------------------
#
# "the other user using the program may not get access to the other user have
#  or using the same program"
#
# account.json is read before anyone signs in (it is how the sign-in screen
# knows who can sign in), so a website's name and address are kept in it
# SEALED with the account's own key. Someone else on the same computer can see
# that an account exists — its name, as on any sign-in screen — and never which
# websites it looks after. The website's folder is named by a random number for
# the same reason.

def _site_where(site_id) -> str:
    return f"account.json#site/{site_id}"


def _seal_site(master: bytes, site: dict) -> dict:
    from . import vault
    plain = json.dumps({"name": site.get("name") or "", "url": site.get("url") or ""}).encode("utf-8")
    out = {k: v for k, v in site.items() if k not in ("name", "url")}
    out["sealed"] = vault.seal(master, plain, _site_where(site["id"])).decode("ascii")
    return out


def site_info(acct: dict, site: dict, master=None) -> dict:
    """A website as its owner sees it: name and address opened with the account's key."""
    out = {"id": site.get("id"), "created": site.get("created"),
           "name": site.get("name") or "", "url": site.get("url") or ""}
    if site.get("sealed"):
        from . import vault
        m = master if master is not None else master_key((acct or {}).get("id"))
        try:
            if m is None:
                raise ValueError("no key")
            d = json.loads(vault.unseal(m, site["sealed"].encode("ascii"), _site_where(site["id"])))
            out.update(name=d.get("name") or "", url=d.get("url") or "")
        except Exception:
            out["locked"] = True
    return out


def sites(acct: dict, master=None) -> list:
    return [site_info(acct, s, master) for s in (acct or {}).get("sites") or []]


def seal_sites(acct: dict, master) -> bool:
    """Seal any website of this account still kept readable (made before this). True when one was."""
    if not acct.get("keys") or master is None:
        return False
    changed, out = False, []
    for s in acct.get("sites") or []:
        if not s.get("sealed") and (s.get("name") or s.get("url")):
            s, changed = _seal_site(master, s), True
        out.append(s)
    if changed:
        acct["sites"] = out
    return changed


def encrypted(account_id) -> bool:
    return bool(account(account_id).get("keys"))


def master_key(account_id=None):
    """The master key handed to this program (AGENCY_KEY), if it is this account's."""
    from . import vault
    a = account_id or (active() or {}).get("account")
    keys = account(a).get("keys") if a else None
    m = vault.master_from_env()
    if not keys or m is None:
        return None
    import hmac
    return m if hmac.compare_digest(vault.check_of(m), keys.get("check", "")) else None


def locked() -> bool:
    """An encrypted account is open and its key is not here."""
    a = active()
    return bool(a) and encrypted(a["account"]) and master_key(a["account"]) is None


NO_SUCH = "That name and password do not open an account on this computer."


def check_password(account_id, password) -> bool:
    """Is this the signed-in person's password? (Setup asks again; the second step was done at sign-in.)"""
    from . import vault
    acct = account(account_id)
    return bool(acct.get("keys")) and vault.open_with(acct["keys"], "password", password or "", acct["id"]) is not None


def sign_in(who, password, code=None) -> dict:
    """The master key for this account and password (and its two-step code, if it has one), or why not."""
    from . import vault
    acct = find_account(who)
    master = vault.open_with(acct["keys"], "password", password or "", acct["id"]) if acct.get("keys") else None
    if master is None:
        # One answer for both, so nobody can find out by trying who has an account here.
        return {"ok": False, "error": NO_SUCH}
    if acct.get("mfa"):
        if not code:
            return {"ok": False, "mfa": True, "error": "Enter the six-digit code from your authenticator app."}
        if not _mfa_ok(acct, master, code):
            return {"ok": False, "mfa": True, "error": "That code is not right, or was used already. Try the next one."}
    changed = seal_sites(acct, master) or bool(acct.get("mfa"))
    if acct.get("name"):
        _seal_name(acct, master, acct["name"])          # kept readable by an older version
        changed = True
    if vault.outdated(acct["keys"], "password"):
        try:
            acct["keys"] = vault.relock(acct["keys"], master, "password", password, acct["id"])
            changed = True                               # moved up to the stronger key stretch
        except ValueError:
            pass                                         # a password from before the length rule
    if changed:
        _save(acct)
    return {"ok": True, "account": public(acct, master), "key": vault.as_env(master)}


def recover(who, recovery_key, new_password, code=None) -> dict:
    """
    Forgotten password: the recovery key opens the account and a new password is
    set. With two-step sign-in on, the authenticator's code is needed as well —
    always two things, so a recovery key found in a drawer is not enough alone.
    """
    from . import vault
    acct = find_account(who)
    master = vault.open_with(acct["keys"], "recovery", recovery_key or "", acct["id"]) if acct.get("keys") else None
    if master is None:
        return {"ok": False, "error": "That name and recovery key do not open an account on this computer."}
    bad = vault.password_problem(new_password, account_name(acct, master))
    if bad:
        return {"ok": False, "error": bad}
    if acct.get("mfa"):
        if not code:
            return {"ok": False, "mfa": True, "error": "Two-step sign-in is on: type the code from your authenticator app too."}
        if not _mfa_ok(acct, master, code):
            return {"ok": False, "mfa": True, "error": "That code is not right, or was used already. Try the next one."}
    return _reopen(acct, master, new_password)


def lost_phone(who, password, recovery_key) -> dict:
    """
    Two-step sign-in on and the phone lost: the password AND the recovery key
    together turn it off and open the account. The recovery key is replaced.
    """
    from . import vault
    acct = find_account(who)
    keys, aid = (acct or {}).get("keys"), (acct or {}).get("id", "")
    by_pw = vault.open_with(keys, "password", password or "", aid) if keys else None
    by_rk = vault.open_with(keys, "recovery", recovery_key or "", aid) if keys else None
    if by_pw is None or by_rk is None or by_pw != by_rk:
        return {"ok": False, "error": "That name, password and recovery key do not open an account on this computer."}
    acct.pop("mfa", None)
    return _reopen(acct, by_pw)


def _reopen(acct, master, new_password=None) -> dict:
    """After a recovery: a new recovery key (whoever saw the old one cannot use it again), and the account opened."""
    from . import vault
    fresh = vault.new_recovery_key()
    keys = acct["keys"]
    if new_password is not None:
        keys = vault.relock(keys, master, "password", new_password, acct["id"])
    acct["keys"] = vault.relock(keys, master, "recovery", fresh, acct["id"])
    seal_sites(acct, master)
    if acct.get("name"):
        _seal_name(acct, master, acct["name"])
    _save(acct)
    return {"ok": True, "account": public(acct, master), "key": vault.as_env(master), "recovery_key": fresh}


def change_password(who, current, new_password) -> dict:
    from . import vault
    acct = find_account(who)
    master = vault.open_with((acct or {}).get("keys"), "password", current or "",
                             (acct or {}).get("id", ""))
    if not acct or master is None:
        return {"ok": False, "error": "that is not the current password"}
    bad = vault.password_problem(new_password, account_name(acct, master))
    if bad:
        return {"ok": False, "error": bad}
    acct["keys"] = vault.relock(acct["keys"], master, "password", new_password, acct["id"])
    _save(acct)
    return {"ok": True}


# ---------------------------------------------------------------------------
# two-step sign-in: a code from an authenticator app as well as the password
# ---------------------------------------------------------------------------
#
# The app's secret is sealed with the account's own key, so it is no use to
# anyone who copies the folder; and a code that worked is never accepted again.

MFA_WHERE = "account.json#mfa"


def _mfa_ok(acct: dict, master: bytes, code) -> bool:
    from . import totp, vault
    try:
        secret = vault.unseal(master, acct["mfa"]["sealed"].encode("ascii"), MFA_WHERE).decode("ascii")
    except Exception:
        return False
    ok, step = totp.check(secret, code, acct["mfa"].get("last", 0))
    if ok:
        acct["mfa"]["last"] = step
    return ok


def mfa_on(account_id, secret, code) -> dict:
    """Turn two-step sign-in on, once the app has shown it gives the right code."""
    from . import totp, vault
    acct, master = account(account_id), master_key(account_id)
    if master is None:
        return {"ok": False, "error": "sign in first"}
    ok, step = totp.check(secret, code)
    if not ok:
        return {"ok": False, "error": "That code is not right. Check the time on your phone, and type the newest code."}
    acct["mfa"] = {"sealed": vault.seal(master, secret.encode("ascii"), MFA_WHERE).decode("ascii"), "last": step}
    _save(acct)
    return {"ok": True}


def mfa_off(account_id, code) -> dict:
    acct, master = account(account_id), master_key(account_id)
    if master is None or not acct.get("mfa"):
        return {"ok": False, "error": "two-step sign-in is not on"}
    if not _mfa_ok(acct, master, code):
        return {"ok": False, "error": "That code is not right."}
    acct.pop("mfa")
    _save(acct)
    return {"ok": True}


# ---------------------------------------------------------------------------
# settings and files, sealed when the account is encrypted
# ---------------------------------------------------------------------------

def _account_of(path):
    """(account id, the file's place inside the account folder) — or (None, None)."""
    try:
        rel = Path(path).resolve().relative_to(_accounts_dir().resolve())
    except Exception:
        return None, None
    if len(rel.parts) < 2:
        return None, None
    return rel.parts[0], Path(*rel.parts[1:]).as_posix()


def read_secret(path, default=None):
    """A settings or config file's text, opened with the account's key when sealed."""
    from . import vault
    p = Path(path)
    if not p.exists():
        return default
    data = p.read_bytes()
    if vault.is_sealed(data):
        acct, where = _account_of(p)
        master = master_key(acct) if acct else None
        if master is None:
            raise vault.Locked(f"{p.name} is locked — sign in on the office screen first")
        data = vault.unseal(master, data, where)
    return data.decode("utf-8")


def write_secret(path, text) -> None:
    """Write a settings or config file — sealed when it belongs to an encrypted account."""
    from . import vault
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    acct, where = _account_of(p)
    data = text.encode("utf-8")
    if acct and encrypted(acct):
        master = master_key(acct)
        if master is None:
            raise vault.Locked(f"{p.name} cannot be saved while signed out")
        data = vault.seal(master, data, where)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_bytes(data)
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, p)


def add_site(account_id, name, url="") -> dict:
    """One more website on the account — never more than MAX_SITES."""
    acct = account(account_id)
    if not acct:
        return {"ok": False, "error": "no such account"}
    master = master_key(account_id)
    if acct.get("keys") and master is None:
        return {"ok": False, "error": "sign in again first — this account's key is not open"}
    have = sites(acct, master)
    if len(have) >= MAX_SITES:
        names = ", ".join(s.get("name") or s["id"] for s in have)
        return {"ok": False, "error": f"an account holds {MAX_SITES} websites "
                                      f"({names}) — remove one before adding another"}
    # The plan's websites (coordination/licence): one on Starter, two on Growth and Pro.
    try:
        from . import licence
        most = licence.websites()
    except Exception:
        most = None
    if most is not None and len(have) >= most:
        return {"ok": False, "plan": True,
                "error": f"Your plan covers {most} website{'s' if most != 1 else ''}. "
                         "Upgrade to add another, or remove one first."}
    host = _host(url)
    if host and any(_host(s.get("url")) == host for s in have):
        return {"ok": False, "error": f"{host} is already one of this account's websites"}
    name = (name or "").strip() or host or "My website"
    # A random name for its folder: the folder says nothing about which website it is.
    site = {"id": "site-" + secrets.token_hex(5), "name": name,
            "url": (url or "").strip(), "created": int(time.time())}
    d = site_dir(acct["id"], site["id"])
    for sub in ("config", "logs"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    if site["url"]:
        # The address goes in the website's own settings straight away, where
        # every desk reads it — or the new website reads as "no website saved".
        clean = lambda v: re.sub(r"[\r\n]+", " ", v).strip()
        try:
            write_secret(d / "site.env", f"SITE_URL={clean(site['url'])}\n"
                                         f"SITE_NAME={clean(name)}\n")
        except Exception:
            pass                       # signed out: Setup asks for it instead
    acct.setdefault("sites", []).append(_seal_site(master, site) if acct.get("keys") else site)
    _save(acct)
    return {"ok": True, "site": {k: site[k] for k in ("id", "name", "url", "created")}, "account": public(acct)}


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
    site = site_info(acct, site)
    if now == {"account": account_id, "site": site_id}:
        return {"ok": True, "site": site, "unchanged": True}
    if now and _run_going():
        cur = next((site_info(account(now["account"]), s) for s in account(now["account"]).get("sites") or []
                    if s["id"] == now["site"]), {})
        return {"ok": False, "running": True,
                "error": f"today's run on {cur.get('name') or 'the open website'} is still "
                         "going — finish it or press Stop, then switch"}
    acct["last_site"] = site_id                       # opened first next time they sign in
    _save(acct)
    if not HOSTED:                                    # one computer, one person: remember it here too
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
                write_secret(p, "\n".join(rows) + "\n")
        done.append(".env")
    cfg = ROOT / "config"
    if cfg.is_dir():
        for f in sorted(cfg.iterdir()):
            if not f.is_file() or f.name.endswith(".example.json") or f.name in MACHINE_CONFIG:
                continue
            to = (adir if f.name in ACCOUNT_CONFIG else sdir) / "config" / f.name
            write_secret(to, f.read_text(encoding="utf-8"))
            done.append(f"config/{f.name}")
    # His website keeps what it is: the glass-and-aluminium-in-the-UAE starter
    # profile (coordination/profile.py). A website added fresh starts blank.
    prof = sdir / "config" / "business.local.json"
    if not prof.exists():
        write_secret(prof, json.dumps({"starter": "glass_uae"}))
    db = ROOT / "coordination" / "agency.db"
    if db.exists():
        _copy_cabinet(db, sdir / "agency.db", account_id, site_id)
        done.append("coordination/agency.db")
    acct["brought_in"] = {"site": site_id, "at": int(time.time()), "copied": done}
    _save(acct)
    return {"ok": True, "copied": done}


def _copy_cabinet(src, dst, account_id, site_id):
    """
    The old cabinet into the website's — encrypted on the way when the account
    is. Never a file copy: the cabinet runs in WAL mode, and its newest writes
    live beside the file until a checkpoint.
    """
    dst = Path(dst)
    if not encrypted(account_id):
        import sqlite3 as plain
        a, b = plain.connect(src), plain.connect(dst)
        try:
            a.backup(b)
        finally:
            a.close()
            b.close()
        return
    from . import cipher, vault
    master = master_key(account_id)
    if master is None:
        raise vault.Locked("sign in before bringing a website in")
    key = vault.cabinet_key(master, site_id)
    fresh = dst.with_name(dst.name + ".incoming")
    for p in (fresh, Path(str(fresh) + "-wal"), Path(str(fresh) + "-shm")):
        if p.exists():
            p.unlink()
    con = cipher.plain_connect(str(src))
    try:
        con.execute("PRAGMA wal_checkpoint(FULL)")
        con.execute(f"ATTACH DATABASE ? AS sealed KEY \"x'{key}'\"", (str(fresh),))
        con.execute("SELECT sqlcipher_export('sealed')")
        con.execute("DETACH DATABASE sealed")
    finally:
        con.close()
    for p in (Path(str(dst) + "-wal"), Path(str(dst) + "-shm")):
        if p.exists():
            p.unlink()
    os.replace(fresh, dst)


def legacy_present() -> bool:
    """Is there a website in the program's original files to bring in? Never on a hosted office."""
    return not HOSTED and ((ROOT / "coordination" / "agency.db").exists() or (ROOT / ".env").exists())


def is_original_site() -> bool:
    """
    Is the website open the one this copy looked after before accounts — his
    own? Defaults that are his (his working email, his drafts) apply there and
    nowhere else, so nobody else's website ever carries them.
    """
    a = active()
    if not a:
        return legacy_present()
    return (account(a["account"]).get("brought_in") or {}).get("site") == a["site"]


# ---------------------------------------------------------------------------

def describe() -> dict:
    a = active()
    out = {"legacy": a is None, "data": str(data_root()), "max_sites": MAX_SITES,
           "db": str(db_path()), "env": [str(p) for p in env_paths()],
           "logs": str(logs_dir()), "accounts": len(accounts())}
    if a:
        acct = account(a["account"])
        out.update(brought_in=bool(acct.get("brought_in")),
                   retired_original=bool(acct.get("retired_original")),
                   encrypted=bool(acct.get("keys")))
        out.update(account={"id": acct.get("id"), "name": account_name(acct)},
                   sites=sites(acct),
                   site=next((s for s in sites(acct) if s["id"] == a["site"]), {}),
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
