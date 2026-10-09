"""
connect.py — how the agency reaches a website, set up in a few minutes.

    python -m pipelines.connect                       how this website is connected
    python -m pipelines.connect --zip connector.zip   make this website's own connector
    python -m pipelines.connect --check               knock on it; full access if it answers
    echo '{"user": "…", "password": "…"}' | python -m pipelines.connect --wordpress
    python -m pipelines.connect --readonly            read-only: changes are handed to you

"The website connection gets much easier" — docs/PUBLIC-LAUNCH-PLAN.md, idea 3.

Before this, connecting a website meant: make a folder, upload a file, rename
it, make a second file, generate a long random line in PowerShell, paste it in,
then copy the folder's address and the line into two boxes on Setup. Every step
was a chance to get one character wrong, and the error said so in a way only a
programmer could read.

THE THREE WAYS

  YOUR OWN CONNECTOR   for any website with a File Manager (most hosts). The
                       download is a zip made for THIS website: the connector
                       and its secret, already together. Upload it to the
                       website's main folder, extract it, press Check. Nothing
                       to copy or paste. The address and secret are saved here
                       as the zip is made; full access starts only when Check
                       proves it answers.
  WORDPRESS            a username and an Application Password, made inside
                       WordPress (pipelines/wp_bridge.py). No file at all.
  READ-ONLY            anything else, or not now. The agency does everything
                       but change the website; each change is handed over.

SAFETY

The secret is made fresh for each website, with Python's `secrets`, and kept
in the website's own settings — sealed when the account is encrypted. It is
reused when the connector is downloaded again, so a second download never
breaks a connector already on the website; `new_secret` makes a new one on
purpose. The download carries the secret, so the office keeps it behind the
same password lock as Setup. Check only reads /health: it never changes the
website.
"""
import argparse
import base64
import io
import json
import secrets
import sys
import time
import urllib.parse
import zipfile
from pathlib import Path

from coordination import workspace

ROOT = Path(__file__).resolve().parents[1]
CONNECTOR = ROOT / "paste-into-your-site" / "agency-api.php"
FOLDER = "agency-api"
STATE = "connect.local.json"
KINDS = ("php", "wordpress", "readonly")


def _env() -> dict:
    from providers import integrations
    try:
        return integrations.read_env()
    except Exception:
        return {}


def _write(values: dict) -> dict:
    from providers import integrations
    return integrations.write_env(values)


def _origin(url) -> str:
    """https://www.example.com from anything the person typed."""
    u = (url or "").strip()
    if not u:
        return ""
    if "://" not in u:
        u = "https://" + u
    p = urllib.parse.urlparse(u)
    return f"{p.scheme or 'https'}://{p.netloc}" if p.netloc else ""


def _state() -> dict:
    try:
        d = json.loads(workspace.read_secret(workspace.config_path(STATE), "{}") or "{}")
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_state(**kw):
    d = {**_state(), **kw}
    workspace.write_secret(workspace.config_path(STATE), json.dumps(d, indent=2))


# ---------------------------------------------------------------------------
# your own connector
# ---------------------------------------------------------------------------

README = """\
YOUR WEBSITE'S CONNECTOR — made for {host} on {day}

1. Open your host's File Manager and go into your website's main folder
   (usually public_html — the folder your home page is in).
2. Upload this zip there, then right-click it and choose Extract.
   You should now have a folder called agency-api with two files in it.
   Delete the zip afterwards.
3. Back in the agency, press Check.

The folder holds the connector (index.php) and its secret (token.php).
Nobody can use it without that secret, and a browser asking for token.php
gets a blank page. It never opens your admin or config folders, never runs
a command, and copies every page before it changes it.

To end the connection at any time, delete the agency-api folder.
"""


def connector_zip(new_secret=False) -> dict:
    """
    This website's connector as a zip, with the address and secret saved.
    Returns {ok, filename, zip (bytes), base, host, reused}. Never the secret
    on its own.
    """
    env = _env()
    origin = _origin(env.get("SITE_URL"))
    if not origin:
        return {"ok": False, "error": "save your website's address first, so the connector "
                                      "knows where it lives"}
    if not CONNECTOR.exists():
        return {"ok": False, "error": "the connector file is missing from this copy of the program"}
    token = "" if new_secret else (env.get("SITE_API_TOKEN") or "").strip()
    reused = bool(token)
    if not token:
        token = secrets.token_urlsafe(36)
    host = urllib.parse.urlparse(origin).netloc
    base = origin + "/" + FOLDER
    saved = _write({"SITE_API_TOKEN": token, "SITE_API_BASE": base, "SITE_CONNECT": "php"})
    if not saved.get("ok"):
        return {"ok": False, "error": saved.get("error") or "could not save the connection"}

    src = CONNECTOR.read_text(encoding="utf-8")
    src = src.replace("FULL ACCESS for worlddealtechnicalservices.com", f"FULL ACCESS for {host}", 1)
    day = time.strftime("%d %B %Y")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{FOLDER}/index.php", src)
        # The connector reads token.php by including it: a PHP file that
        # returns the secret, so a browser that asks for it sees nothing.
        z.writestr(f"{FOLDER}/token.php", "<?php\nreturn '" + token + "';\n")
        z.writestr("READ-ME-FIRST.txt", README.format(host=host, day=day))
    _save_state(kind="php", made_at=int(time.time()), checked_ok=False)
    return {"ok": True, "filename": f"connector-{host}.zip", "zip": buf.getvalue(),
            "base": base, "host": host, "reused": reused}


# ---------------------------------------------------------------------------
# checking, and the other two ways
# ---------------------------------------------------------------------------

def check() -> dict:
    """Knock on the website the chosen way. On an answer, full access is switched on."""
    from . import site_api
    s = site_api.settings()
    kind = _state().get("kind") or ("wordpress" if s["kind"] == "wordpress" else "php")
    if kind == "readonly":
        return {"ok": True, "kind": "readonly", "mode": "readonly",
                "head": "Read-only: the agency changes nothing on your website."}
    trial = dict(s, mode="full", kind="wordpress" if kind == "wordpress" else "php")
    t0 = time.time()
    r = site_api.call("/health", s=trial)
    took = int((time.time() - t0) * 1000)
    if not r.get("ok"):
        _save_state(kind=kind, checked_at=int(time.time()), checked_ok=False, latency_ms=took)
        why = r.get("error") or "your website did not answer"
        if kind == "php" and r.get("status") == 404:
            why = ("nothing answered at " + (s["base"] or "your website") + ". Check that the "
                   "agency-api folder is in the same folder as your home page, with index.php "
                   "and token.php inside it.")
        return {"ok": False, "kind": kind, "error": why}
    saved = _write({"SITE_MODE": "full", "SITE_CONNECT": "wordpress" if kind == "wordpress" else "php"})
    try:
        version = int(r.get("connector") or 0) if kind == "php" else 0
    except (TypeError, ValueError):
        version = 0
    _save_state(kind=kind, checked_at=int(time.time()), checked_ok=True,
                can=list(r.get("can") or []), connector=version, latency_ms=took,
                limits=r.get("limits") if isinstance(r.get("limits"), dict) else {})
    who = f" as {r['user']}" if r.get("user") else ""
    return {"ok": bool(saved.get("ok")), "kind": kind, "mode": "full",
            "connector": version, "newest": site_api.CONNECTOR_NEWEST, "latency_ms": took,
            "old_connector": kind == "php" and version < site_api.CONNECTOR_NEWEST,
            "can": list(r.get("can") or []), "warnings": list(r.get("warnings") or []),
            "head": (f"Connected{who}. IT can now fix what it finds — on a copy first, and only "
                     "after your yes for anything that matters.")}


def use_wordpress(user, password, site="") -> dict:
    from . import wp_bridge
    user = (user or "").strip()
    password = wp_bridge.normalise_password(password)
    if not user:
        return {"ok": False, "error": "type the WordPress username the password belongs to"}
    if len(password) < 16:
        return {"ok": False, "error": ("that does not look like an Application Password — it is "
                                       "24 letters and numbers, shown once when you press Add New")}
    values = {"WP_USER": user, "WP_APP_PASSWORD": password, "SITE_CONNECT": "wordpress"}
    origin = _origin(site)
    if origin and not (_env().get("SITE_URL") or "").strip():
        values["SITE_URL"] = origin
    saved = _write(values)
    if not saved.get("ok"):
        return {"ok": False, "error": saved.get("error") or "could not save it"}
    _save_state(kind="wordpress", checked_ok=False)
    return check()


def use_readonly() -> dict:
    saved = _write({"SITE_MODE": "readonly"})
    _save_state(kind="readonly", checked_ok=False)
    return {"ok": bool(saved.get("ok")), "kind": "readonly", "mode": "readonly",
            "head": "Read-only. The agency does everything else and hands each website change to you."}


def status() -> dict:
    """How this website is connected. Never the secret or the password."""
    from . import site_api
    s = site_api.settings()
    st = _state()
    kind = st.get("kind") or ("wordpress" if s["kind"] == "wordpress"
                              else "php" if s["base"] and s["token"] else "readonly")
    connected = s["mode"] == "full" and not site_api.why_not_ready(s)
    return {"ok": True, "kind": kind, "mode": s["mode"], "connected": connected,
            "site": s["site"], "base": s["base"] if kind == "php" else "",
            "has_secret": bool(s["token"]), "wp_user": s["wp_user"] if kind == "wordpress" else "",
            "has_wp_password": bool(s["wp_pass"]), "checked_at": st.get("checked_at"),
            "checked_ok": bool(st.get("checked_ok")), "can": st.get("can") or [],
            "connector": st.get("connector") or 0, "newest": site_api.CONNECTOR_NEWEST,
            "latency_ms": st.get("latency_ms"),
            "old_connector": kind == "php" and bool(st.get("checked_ok"))
                             and int(st.get("connector") or 0) < site_api.CONNECTOR_NEWEST}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="How the agency reaches this website.")
    ap.add_argument("--zip", metavar="OUT", help="write this website's connector zip here")
    ap.add_argument("--zip-json", action="store_true",
                    help="the connector zip as base64 JSON, for the office")
    ap.add_argument("--new-secret", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--wordpress", action="store_true", help="read {user, password, site} on stdin")
    ap.add_argument("--readonly", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    if a.zip or a.zip_json:
        r = connector_zip(new_secret=a.new_secret)
        if r.get("ok") and a.zip:
            Path(a.zip).write_bytes(r["zip"])
        out = {k: v for k, v in r.items() if k != "zip"}
        if r.get("ok") and a.zip_json:
            out["zip_b64"] = base64.b64encode(r["zip"]).decode("ascii")
        print(json.dumps(out))
        return 0 if r.get("ok") else 1
    if a.check:
        r = check()
    elif a.wordpress:
        d = json.loads(sys.stdin.read() or "{}")
        r = use_wordpress(d.get("user"), d.get("password"), d.get("site") or "")
    elif a.readonly:
        r = use_readonly()
    else:
        r = status()
    if a.json or not sys.stdout.isatty():
        print(json.dumps(r))
    else:
        print(r.get("head") or r.get("error") or json.dumps(r, indent=2))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
