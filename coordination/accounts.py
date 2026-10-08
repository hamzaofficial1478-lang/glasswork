"""
accounts.py — signing up, signing in, and the websites on an account, for the
office (server.js) to call.

    echo '{"name": "Ammar", "password": "…"}' | python -m coordination.accounts sign-up

Everything comes in as JSON on stdin and goes out as JSON on stdout: a
password on the command line would be readable by every other program on the
computer through the process list.

THE KEY. Signing up and signing in hand back the account's master key, as hex.
The office keeps it in memory for as long as he is signed in and gives it to
each program it starts (AGENCY_KEY). It is never written to disk by anything
here.
"""
import json
import sys

from . import workspace


def _cipher_ready() -> str:
    """'' when encryption can work on this computer, else what to install."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # noqa: F401
        from sqlcipher3 import dbapi2  # noqa: F401
    except Exception:
        return ("the encryption libraries are not installed yet — run: "
                "pip install -r requirements.txt, then try again")
    return ""


def _sites(acct) -> list:
    return [{"id": s["id"], "name": s.get("name"), "url": s.get("url", "")}
            for s in (acct or {}).get("sites") or []]


def _legacy_site():
    """What the program's original files say his website is."""
    url = name = ""
    try:
        cfg = json.loads((workspace.ROOT / "config" / "site.json").read_text(encoding="utf-8"))
        url, name = cfg.get("url") or "", cfg.get("name") or ""
    except Exception:
        pass
    try:
        for line in (workspace.ROOT / ".env").read_text(encoding="utf-8").splitlines():
            k, _, v = line.partition("=")
            if k.strip() == "SITE_URL" and not url:
                url = v.strip()
            if k.strip() == "SITE_NAME" and not name:
                name = v.strip()
    except Exception:
        pass
    return url, name


def cmd_list(_):
    a = workspace.active()
    url, name = _legacy_site()
    return {"ok": True,
            "accounts": [{"id": x["id"], "name": x.get("name"), "sites": _sites(x)}
                         for x in workspace.accounts()],
            "active": a, "max_sites": workspace.MAX_SITES,
            "legacy_present": workspace.legacy_present(),
            "legacy_site": {"url": url, "name": name},
            "cipher_problem": _cipher_ready()}


def cmd_sign_up(d):
    bad = _cipher_ready()
    if bad:
        return {"ok": False, "error": bad}
    if (d.get("password") or "") != (d.get("confirm") or d.get("password") or ""):
        return {"ok": False, "error": "the two passwords do not match"}
    return workspace.create_account(d.get("name"), d.get("password") or "")


def _open_first(acct_id):
    """The website to open on signing in: the one open last time, else the first."""
    acct = workspace.account(acct_id)
    a = workspace.active()
    if a and a["account"] == acct_id:
        return a["site"]
    sites = acct.get("sites") or []
    if not sites:
        return None
    r = workspace.open_site(acct_id, sites[0]["id"])
    return sites[0]["id"] if r.get("ok") else None


def cmd_sign_in(d):
    r = workspace.sign_in(d.get("account"), d.get("password"))
    if not r.get("ok"):
        return r
    acct_id = r["account"]["id"]
    r["site"] = _open_first(acct_id)
    r["sites"] = _sites(workspace.account(acct_id))
    return r


def cmd_check(d):
    """Is this the password for the signed-in account? (Setup's second door.)"""
    r = workspace.sign_in(d.get("account"), d.get("password"))
    return {"ok": bool(r.get("ok")), "error": r.get("error")}


def cmd_recover(d):
    r = workspace.recover(d.get("account"), d.get("recovery_key"), d.get("password"))
    if r.get("ok"):
        r["site"] = _open_first(r["account"]["id"])
        r["sites"] = _sites(workspace.account(r["account"]["id"]))
    return r


def cmd_change_password(d):
    return workspace.change_password(d.get("account"), d.get("current"), d.get("password"))


def cmd_add_site(d):
    acct = d.get("account")
    r = workspace.add_site(acct, d.get("name"), d.get("url"))
    if r.get("ok") and not workspace.active():
        workspace.open_site(acct, r["site"]["id"])
    return r


def cmd_open_site(d):
    return workspace.open_site(d.get("account"), d.get("site"))


def cmd_bring_in(d):
    """His current website becomes one of the account's — copied in, sealed."""
    acct = d.get("account")
    if not workspace.legacy_present():
        return {"ok": False, "error": "there is no website in the program's original files to bring in"}
    if workspace.account(acct).get("brought_in"):
        return {"ok": False, "error": "your original website has already been brought in"}
    url, name = _legacy_site()
    site = d.get("site")
    if not site:
        made = workspace.add_site(acct, d.get("name") or name or url, d.get("url") or url)
        if not made.get("ok"):
            return made
        site = made["site"]["id"]
    r = workspace.bring_in_current(acct, site)
    if r.get("ok"):
        workspace.open_site(acct, site)
        r["site"] = site
        r["sites"] = _sites(workspace.account(acct))
    return r


def cmd_retire_old(d):
    """
    Once his website has been brought in and checked: take the ORIGINAL,
    unencrypted copy out of the program folder, so the encryption is not
    sitting beside a plain copy of the same thing. Only on his say-so.
    """
    acct = workspace.account(d.get("account"))
    if not acct.get("brought_in"):
        return {"ok": False, "error": "bring your website in first — this removes the original copy"}
    if not d.get("confirm"):
        return {"ok": False, "error": "say yes to remove the original copy"}
    root = workspace.ROOT
    gone = []
    targets = [root / ".env", root / "config" / "site.json",
               root / "coordination" / "agency.db",
               root / "coordination" / "agency.db-wal", root / "coordination" / "agency.db-shm"]
    cfg = root / "config"
    if cfg.is_dir():
        targets += [f for f in cfg.iterdir()
                    if f.name.endswith(".local.json") and f.name not in workspace.MACHINE_CONFIG]
    for p in targets:
        if p.is_file():
            p.unlink()
            gone.append(str(p.relative_to(root)))
    acct["retired_original"] = gone
    workspace._save(acct)
    return {"ok": True, "removed": gone}


def cmd_status(_):
    d = workspace.describe()
    d["locked"] = workspace.locked()
    d["run_going"] = bool(workspace._run_going()) if workspace.active() else False
    return {"ok": True, **d}


COMMANDS = {
    "list": cmd_list, "sign-up": cmd_sign_up, "sign-in": cmd_sign_in, "check": cmd_check,
    "recover": cmd_recover, "change-password": cmd_change_password,
    "add-site": cmd_add_site, "open-site": cmd_open_site, "bring-in": cmd_bring_in,
    "retire-old": cmd_retire_old, "status": cmd_status,
}


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in COMMANDS:
        print(json.dumps({"ok": False, "error": "one of: " + ", ".join(COMMANDS)}))
        return 2
    try:
        raw = sys.stdin.read() if not sys.stdin.isatty() else ""
        data = json.loads(raw or "{}")
    except Exception:
        print(json.dumps({"ok": False, "error": "the request was not JSON"}))
        return 2
    try:
        out = COMMANDS[argv[0]](data)
    except Exception as e:
        out = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
