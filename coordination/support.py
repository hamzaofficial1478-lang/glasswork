"""
support.py — problems reported by the people using the program, and your answers.

    "i want the report section where user will be able to submit the report and
     those reports will get over to me i'll solve those to update program user
     will get update message"

The reports travel to Glasswork Home — one folder you upload once to your own
website (publish-to-your-site/glasswork-home). Nothing goes to anybody else.

    For everyone:
    python -m coordination.support --send         a report; JSON on stdin:
                                                  {title, text, contact, attach}
    python -m coordination.support --mine         the reports sent from here, and your answers
    python -m coordination.support --registration    whether this copy is registered, and counted
    python -m coordination.support --register     JSON on stdin: {name, business, email, phone,
                                                  website, country} — or {withdraw: true}
    python -m coordination.support --counting on|off   say this copy is in use, or not

    For you, the publisher:
    python -m coordination.support --publisher    is this copy set up to publish
    python -m coordination.support --setup        make the Home; JSON on stdin: {site, email}
    python -m coordination.support --home-zip     the Home folder to upload (with your key)
    python -m coordination.support --check-home   does the Home answer, and know you
    python -m coordination.support --inbox        every report
    python -m coordination.support --read ID      one report in full
    python -m coordination.support --answer       JSON on stdin: {id, status, reply, fixed_in}
    python -m coordination.support --remove ID
    python -m coordination.support --users        everyone using the program, with their issues
    python -m coordination.support --user COPY    one of them in full
    python -m coordination.support --forget COPY  remove one (add --with-reports to remove theirs too)

WHO IS USING THE PROGRAM

    "where i will get the user details and their issues? like will i have any
     of my room where i will be able to manage the my users their issues?"

  Every copy has a random number of its own (install_id). When it asks the
  Home for the newest version it says that number, its version and the kind
  of computer — and nothing else. That is how the Home counts copies in use,
  and the person can switch it off in Help and reports. Their name, business,
  email, phone, website and country reach you only if they choose Register,
  and Withdraw takes them back. A report carries the copy's number, so in your
  Users room each person's issues are under their name.

WHAT A REPORT CARRIES

  What they wrote, the email they chose to give for a reply (or none), the
  program's version and the kind of computer — and, only if they tick the box,
  the support report from coordination/diagnostics, which has every key,
  password, address and number taken out before it leaves, and which they can
  read in full first.

YOUR KEY

  The Home knows you by a key made here, kept in config/publisher.local.json on
  your computer and in home-key.php on your website. It is never in a release
  and never sent to anyone using the program.
"""
import argparse
import base64
import io
import json
import os
import platform
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

try:
    from . import product, workspace
except ImportError:  # pragma: no cover - run as a script
    import product
    import workspace

ROOT = product.ROOT
HOME_SOURCE = ROOT / "publish-to-your-site" / "glasswork-home" / "index.php"
TIMEOUT = 30
STATUS_WORDS = {"new": "Sent — waiting to be read", "seen": "Read by the developer",
                "working": "Being worked on", "fixed": "Fixed", "wont-fix": "Not changing this"}


def home_url() -> str:
    """The Home's address, or '' when this copy has none to send reports to."""
    u = (os.environ.get("GLASSWORK_HOME") or product.info().get("home_url") or "").strip().rstrip("/")
    return u


def _copy_file() -> Path:
    return workspace.data_root() / "this-copy.local.json"


def _copy() -> dict:
    try:
        d = json.loads(_copy_file().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_copy(d: dict) -> None:
    f = _copy_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(d, indent=1), encoding="utf-8")


def install_id() -> str:
    """This copy's random number, made the first time it is asked for."""
    d = _copy()
    if not d.get("id"):
        d["id"] = secrets.token_hex(12)
        _save_copy(d)
    return d["id"]


def counting() -> bool:
    return _copy().get("counting", True) is not False


def set_counting(on: bool) -> dict:
    d = _copy()
    d["counting"] = bool(on)
    _save_copy(d)
    return {"ok": True, "counting": bool(on),
            "head": ("This copy says it is in use when it looks for updates." if on else
                     "This copy no longer says it is in use. It still looks for updates.")}


def with_copy(url: str) -> str:
    """The update address, with this copy's number, version and computer — if counting is on."""
    # Only a Glasswork Home counts copies; any other update address is asked plainly.
    if not counting() or not url or "do=latest" not in url:
        return url
    q = urllib.parse.urlencode({"copy": install_id(), "v": product.version(), "os": _system_short()})
    return url + ("&" if "?" in url else "?") + q


def _system_short() -> str:
    try:
        return f"{platform.system()} {platform.release()}"[:100]
    except Exception:
        return ""


def registration() -> dict:
    d = _copy()
    return {"ok": True, "home": bool(home_url()), "registered": bool(d.get("person")),
            "person": d.get("person") or {}, "counting": counting(), "copy": install_id(),
            "publisher": is_publisher()}


def register(person: dict) -> dict:
    """Tell the makers who is using this copy — only what was typed, only when asked."""
    if not home_url():
        return {"ok": False, "error": "This copy has no address to register with yet."}
    if person.get("withdraw"):
        r = _ask("register", "POST", {"copy": install_id(), "withdraw": True})
        if not r.get("ok"):
            return {"ok": False, "error": "Not withdrawn — " + str(r.get("error") or "no answer")}
        d = _copy()
        d.pop("person", None)
        _save_copy(d)
        return {"ok": True, "head": "Withdrawn. The makers no longer have your name or details."}
    clean = {k: str(person.get(k) or "").strip()[:n] for k, n in
             (("name", 120), ("business", 160), ("email", 200), ("phone", 60), ("website", 300), ("country", 80))}
    if not (clean["name"] or clean["business"] or clean["email"]):
        return {"ok": False, "error": "Give at least your name, your business or your email."}
    r = _ask("register", "POST", {"copy": install_id(), "version": product.version(), "system": _system_short(), **clean})
    if not r.get("ok"):
        return {"ok": False, "error": "Not registered — " + str(r.get("error") or "no answer") + ". Try again in a minute."}
    d = _copy()
    d["person"] = clean
    _save_copy(d)
    return {"ok": True, "head": "Registered. When you report a problem, the makers know who it is from."}


def _mine_file() -> Path:
    return workspace.data_root() / "support-reports.local.json"


def _load_mine() -> list:
    try:
        d = json.loads(_mine_file().read_text(encoding="utf-8"))
        return d if isinstance(d, list) else []
    except Exception:
        return []


def _save_mine(rows) -> None:
    f = _mine_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(rows[-200:], indent=1), encoding="utf-8")


def _ask(do, method="GET", body=None, query=None, key="", base=None, timeout=TIMEOUT) -> dict:
    base = (base or home_url()).rstrip("/")
    if not base:
        return {"ok": False, "no_home": True,
                "error": "this copy has no address to send reports to yet"}
    if not base.lower().startswith(("https://", "http://127.0.0.1", "http://localhost")):
        return {"ok": False, "error": "reports only travel over https://"}
    q = {"do": do, **(query or {})}
    url = base + "/index.php?" + urllib.parse.urlencode(q)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", f"{product.name()}/{product.version()}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("Authorization", "Bearer " + key)
        req.add_header("X-Home-Key", key)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raw = (e.read() or b"").decode("utf-8", "replace")
        try:
            out = json.loads(raw)
        except Exception:
            out = {}
        out = out if isinstance(out, dict) else {}
        out.setdefault("ok", False)
        out.setdefault("error", f"the Home answered {e.code}")
        out["status"] = e.code
        if e.code == 404 and "not something this address does" not in str(out.get("error")):
            out["error"] = ("nothing answered at the Home's address — is the glasswork-home folder "
                            "on the website?")
        return out
    except Exception as e:
        return {"ok": False, "unreachable": True,
                "error": f"could not reach the Home: {type(e).__name__}"}
    try:
        out = json.loads(raw)
    except Exception:
        return {"ok": False, "error": "the Home did not answer with JSON — is it the right address?"}
    return out if isinstance(out, dict) else {"ok": False, "error": "an odd answer from the Home"}


# ---------------------------------------------------------------------------
# for everyone
# ---------------------------------------------------------------------------

def _system() -> str:
    try:
        return f"{platform.system()} {platform.release()} · Python {platform.python_version()}"[:160]
    except Exception:
        return ""


def preview(attach=True) -> dict:
    """Exactly what would be sent besides their own words — read before sending."""
    support = ""
    if attach:
        try:
            from . import diagnostics
            support = diagnostics.as_text(diagnostics.gather())
        except Exception as e:
            support = f"(the support report could not be made: {type(e).__name__})"
    return {"ok": True, "product": product.name(), "version": product.version(), "system": _system(),
            "support": support, "home": bool(home_url()),
            "support_email": product.info().get("support_email") or ""}


def send(title="", text="", contact="", attach=True) -> dict:
    title = str(title or "").strip()[:200]
    text = str(text or "").strip()[:20000]
    contact = str(contact or "").strip()[:200]
    if not title and not text:
        return {"ok": False, "error": "Say what went wrong first."}
    p = preview(attach)
    if not home_url():
        mail = p["support_email"]
        return {"ok": False, "no_home": True, "support": p["support"],
                "error": ("This copy cannot send reports by itself yet. "
                          + (f"Email it to {mail}, with the support report below." if mail
                             else "Copy the support report below and send it to whoever gave you the program."))}
    r = _ask("report", "POST", {"title": title, "text": text, "contact": contact, "product": p["product"],
                                "version": p["version"], "system": p["system"], "support": p["support"],
                                "copy": install_id()})
    if not r.get("ok"):
        return {"ok": False, "error": "Not sent — " + str(r.get("error") or "no answer") + ". Try again in a minute."}
    rows = _load_mine()
    rows.append({"id": r.get("id"), "key": r.get("key"), "title": title or "A problem", "at": int(time.time()),
                 "version": p["version"]})
    _save_mine(rows)
    return {"ok": True, "id": r.get("id"),
            "head": f"Sent. Your report is {r.get('id')} — the answer appears here when there is one."}


def mine() -> dict:
    rows = _load_mine()
    out = [{"id": r.get("id"), "title": r.get("title"), "at": r.get("at"), "version": r.get("version"),
            "status": "new", "status_words": STATUS_WORDS["new"], "reply": "", "fixed_in": ""} for r in rows]
    if rows and home_url():
        got = _ask("status", "POST", {"reports": [{"id": r.get("id"), "key": r.get("key")} for r in rows[-50:]]})
        by = {x.get("id"): x for x in (got.get("reports") or []) if x.get("found")}
        for o in out:
            x = by.get(o["id"])
            if x:
                o.update(status=x.get("status") or "new", reply=x.get("reply") or "",
                         fixed_in=x.get("fixed_in") or "", updated=x.get("updated"))
                o["status_words"] = STATUS_WORDS.get(o["status"], o["status"])
                here = product.parse_version(product.version())
                if o["fixed_in"] and product.parse_version(o["fixed_in"]) > here:
                    o["update_needed"] = True
    out.sort(key=lambda o: o.get("at") or 0, reverse=True)
    return {"ok": True, "reports": out, "home": bool(home_url()),
            "answered": sum(1 for o in out if o.get("reply"))}


# ---------------------------------------------------------------------------
# for the publisher
# ---------------------------------------------------------------------------

def _pub() -> dict:
    return product.publisher()


def _save_pub(d: dict) -> None:
    f = product.publisher_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(d, indent=1), encoding="utf-8")


def is_publisher() -> bool:
    return bool(_pub().get("home_key"))


def publisher_view() -> dict:
    p = _pub()
    git = (ROOT / ".git").exists()
    return {"ok": True, "publisher": bool(p.get("home_key")), "can_publish": git or bool(p.get("home_key")),
            "home_url": (p.get("public") or {}).get("home_url") or "", "email": p.get("email") or "",
            "signing_key": bool(p.get("signing_key") and Path(p["signing_key"]).exists()),
            "version": product.version()}


def _signing_key_path() -> Path:
    return Path(os.environ.get("GLASSWORK_SIGNING_KEY") or (Path.home() / ".glasswork-publisher" / "signing.key"))


def setup(site, email="") -> dict:
    """Make this copy the publisher's: a Home address, a key, and a signing key."""
    site = str(site or "").strip().rstrip("/")
    if site and not site.lower().startswith(("http://", "https://")):
        site = "https://" + site
    if not site.lower().startswith(("https://", "http://127.0.0.1", "http://localhost")):
        return {"ok": False, "error": "Give your website's address, starting https://"}
    email = str(email or "").strip()[:200]
    p = _pub()
    p["home_key"] = p.get("home_key") or secrets.token_urlsafe(36)
    p["email"] = email
    home = site + "/glasswork-home"
    keyfile = _signing_key_path()
    made = None
    if not keyfile.exists():
        from . import updates
        keyfile.parent.mkdir(parents=True, exist_ok=True)
        made = updates.new_key(str(keyfile))
        if not made.get("ok"):
            return made
    pub_key = made["update_key"] if made else _public_key(keyfile)
    p["signing_key"] = str(keyfile)
    p["public"] = {"home_url": home, "update_url": home + "/index.php?do=latest", "update_key": pub_key,
                   "support_email": email or (p.get("public") or {}).get("support_email", "")}
    _save_pub(p)
    return {"ok": True, "home_url": home,
            "head": ("Your Glasswork Home is ready to upload. Download it, unzip it, and put the "
                     "glasswork-home folder in your website's main folder. Then press Check."),
            "signing_key_file": str(keyfile)}


def _public_key(keyfile: Path) -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    k = Ed25519PrivateKey.from_private_bytes(keyfile.read_bytes())
    return base64.b64encode(k.public_key().public_bytes(serialization.Encoding.Raw,
                                                        serialization.PublicFormat.Raw)).decode()


def home_zip() -> dict:
    p = _pub()
    if not p.get("home_key"):
        return {"ok": False, "error": "Set up your Home first."}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("glasswork-home/index.php", HOME_SOURCE.read_text(encoding="utf-8"))
        z.writestr("glasswork-home/home-key.php", "<?php\nreturn '" + p["home_key"] + "';\n")
        cfg = {"owner_email": p.get("email") or "", "product": product.name()}
        z.writestr("glasswork-home/home-config.php",
                   "<?php\nreturn " + _php_array(cfg) + ";\n")
    return {"ok": True, "zip": buf.getvalue(), "filename": "glasswork-home.zip"}


def _php_array(d: dict) -> str:
    def q(v):
        return "'" + str(v).replace("\\", "\\\\").replace("'", "\\'") + "'"
    return "[" + ", ".join(f"{q(k)} => {q(v)}" for k, v in d.items()) + "]"


def _key() -> str:
    return _pub().get("home_key") or ""


def check_home() -> dict:
    if not is_publisher():
        return {"ok": False, "error": "Set up your Home first."}
    r = _ask("ping", key=_key())
    if not r.get("ok"):
        return {"ok": False, "error": "The Home did not answer: " + str(r.get("error") or "no answer")}
    if not r.get("owner"):
        return {"ok": False, "error": ("The Home answered, but does not know your key — upload the "
                                       "glasswork-home folder again, from the newest download.")}
    return {"ok": True, "latest": r.get("latest") or "",
            "head": "Your Home answers and knows you." + (f" Version {r['latest']} is out." if r.get("latest") else "")}


def inbox() -> dict:
    if not is_publisher():
        return {"ok": False, "error": "This copy is not set up to read reports."}
    r = _ask("inbox", key=_key())
    if r.get("ok"):
        for x in r.get("reports") or []:
            x["status_words"] = STATUS_WORDS.get(x.get("status"), x.get("status"))
    return r


def read(rid) -> dict:
    if not is_publisher():
        return {"ok": False, "error": "This copy is not set up to read reports."}
    return _ask("read", query={"id": str(rid)}, key=_key())


def answer(rid, status=None, reply=None, fixed_in=None) -> dict:
    if not is_publisher():
        return {"ok": False, "error": "This copy is not set up to answer reports."}
    body = {"id": str(rid)}
    if status:
        body["status"] = status
    if reply is not None:
        body["reply"] = str(reply)[:5000]
    if fixed_in is not None:
        body["fixed_in"] = str(fixed_in)[:40]
    return _ask("answer", "POST", body, key=_key())


def remove(rid) -> dict:
    if not is_publisher():
        return {"ok": False, "error": "This copy is not set up for that."}
    return _ask("remove", "POST", {"id": str(rid)}, key=_key())


def users() -> dict:
    if not is_publisher():
        return {"ok": False, "error": "This copy is not set up to see who uses the program."}
    r = _ask("users", key=_key())
    if r.get("ok"):
        latest = product.parse_version(r.get("latest") or "0")
        for u in r.get("users") or []:
            u["old_version"] = bool(r.get("latest")) and product.parse_version(u.get("version") or "0") < latest
    return r


def user(cid) -> dict:
    if not is_publisher():
        return {"ok": False, "error": "This copy is not set up to see who uses the program."}
    r = _ask("user", query={"id": str(cid)}, key=_key())
    if r.get("ok"):
        for x in r.get("reports") or []:
            x["status_words"] = STATUS_WORDS.get(x.get("status"), x.get("status"))
    return r


def forget(cid, with_reports=False) -> dict:
    if not is_publisher():
        return {"ok": False, "error": "This copy is not set up for that."}
    return _ask("forget", "POST", {"id": str(cid), "reports": bool(with_reports)}, key=_key())


def upload(path, name=None, chunk=4 * 1024 * 1024, progress=None) -> dict:
    """Send one file to the Home's updates/ folder, in pieces, checked whole at the end."""
    import hashlib
    data = Path(path).read_bytes()
    name = name or Path(path).name
    parts = max(1, -(-len(data) // chunk))
    digest = hashlib.sha256(data).hexdigest()
    r = {"ok": False}
    for i in range(parts):
        piece = data[i * chunk:(i + 1) * chunk]
        body = {"name": name, "part": i, "parts": parts, "data": base64.b64encode(piece).decode()}
        if i == parts - 1:
            body["sha256"] = digest
        for attempt in range(3):
            r = _ask("upload", "POST", body, key=_key(), timeout=180)
            if r.get("ok") or not r.get("unreachable"):
                break
            time.sleep(2 + attempt * 3)
        if not r.get("ok"):
            return {"ok": False, "error": f"{name}: " + str(r.get("error") or "the upload stopped")}
        if progress:
            progress(name, i + 1, parts)
    return {"ok": True, "name": name, "bytes": len(data), "sha256": digest}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Problems reported, and the answers.")
    for f in ("send", "mine", "preview", "publisher", "setup", "home-zip", "check-home", "inbox", "answer",
              "registration", "register", "users"):
        ap.add_argument("--" + f, action="store_true")
    ap.add_argument("--read", metavar="ID")
    ap.add_argument("--remove", metavar="ID")
    ap.add_argument("--user", metavar="COPY")
    ap.add_argument("--forget", metavar="COPY")
    ap.add_argument("--with-reports", action="store_true")
    ap.add_argument("--counting", choices=("on", "off"))
    ap.add_argument("--no-attach", action="store_true")
    a = ap.parse_args(argv)
    body = {}
    if a.send or a.setup or a.answer or a.register:
        try:
            body = json.loads(sys.stdin.read() or "{}")
        except Exception:
            body = {}
        body = body if isinstance(body, dict) else {}
    if a.send:
        r = send(body.get("title"), body.get("text"), body.get("contact"), body.get("attach", True) is not False)
    elif a.mine:
        r = mine()
    elif a.preview:
        r = preview(not a.no_attach)
    elif a.publisher:
        r = publisher_view()
    elif a.setup:
        r = setup(body.get("site"), body.get("email"))
    elif a.home_zip:
        z = home_zip()
        r = {**z, "zip": base64.b64encode(z["zip"]).decode()} if z.get("ok") else z
    elif a.check_home:
        r = check_home()
    elif a.inbox:
        r = inbox()
    elif a.read:
        r = read(a.read)
    elif a.answer:
        r = answer(body.get("id"), body.get("status"), body.get("reply"), body.get("fixed_in"))
    elif a.remove:
        r = remove(a.remove)
    elif a.registration:
        r = registration()
    elif a.register:
        r = register(body)
    elif a.counting:
        r = set_counting(a.counting == "on")
    elif a.users:
        r = users()
    elif a.user:
        r = user(a.user)
    elif a.forget:
        r = forget(a.forget, a.with_reports)
    else:
        r = mine()
    print(json.dumps(r, ensure_ascii=False))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
