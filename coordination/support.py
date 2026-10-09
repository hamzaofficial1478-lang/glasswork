"""
support.py — a problem report, registering a copy, and the developer's answers:
the side every copy of the program has.

    "i want the report section where user will be able to submit the report and
     those reports will get over to me"
    "i want the Users to be stored on my pc in program storage not on my
     website hosting"

Everything this copy tells the developer is SEALED here, on this computer,
with his public key (coordination/sealed.py) before it leaves — so only his
own computer can open it. It travels through his Glasswork Home, a postbox on
his website that holds the envelope until his program collects it and cannot
read it. His answers come back the same way: sealed to this copy, waiting in
a mailbox only this copy knows the number of. The developer's own side — his
users room, his inbox, putting versions out — is coordination/publisher.py,
which is on his computer only and is not in any copy he gives out.

    python -m coordination.support --send          a report; JSON on stdin:
                                                   {title, text, contact, attach}
    python -m coordination.support --mine          the reports sent from here, and the answers
    python -m coordination.support --preview       exactly what a report would carry
    python -m coordination.support --registration  whether this copy is registered, and counted
    python -m coordination.support --register      JSON on stdin: {name, business, email, phone,
                                                   website, country} — or {withdraw: true}
    python -m coordination.support --counting on|off   say this copy is in use, or not

WHAT REACHES THE DEVELOPER, AND ONLY HIM

  - Once a day, if "say this copy is in use" is on: a random number for this
    copy, its version and the kind of computer. Switch it off in Help and
    reports and it stops; updates are still looked for.
  - Only if they press Register: the name, business, email, phone, website and
    country they typed. Withdraw takes them back.
  - Only if they send a report: what they wrote, the email they chose to give,
    the copy's number, its version and the kind of computer — and, only if they
    tick the box, the support report, with every key, password, address and
    number taken out first, which they can read in full before sending.

Nothing is ever kept readable on his website, and nothing here can reach his
computer: his program fetches from the postbox; nothing ever calls in.
"""
import argparse
import json
import os
import platform
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

try:
    from . import product, sealed, workspace
except ImportError:  # pragma: no cover - run as a script
    import product
    import sealed
    import workspace

ROOT = product.ROOT
TIMEOUT = 30
STATUS_WORDS = {"new": "Sent — waiting to be read", "seen": "Read by the developer",
                "working": "Being worked on", "fixed": "Fixed", "wont-fix": "Not changing this"}
STATUSES = tuple(STATUS_WORDS)


def home_url() -> str:
    """The Home's address, or '' when this copy has none to send reports to."""
    return (os.environ.get("GLASSWORK_HOME") or product.info().get("home_url") or "").strip().rstrip("/")


def postbox_key() -> str:
    """The developer's PUBLIC sealing key — safe for every copy to carry; it can only seal, never open."""
    return (os.environ.get("GLASSWORK_POSTBOX_KEY") or product.info().get("postbox_key") or "").strip()


def can_send() -> bool:
    return bool(home_url() and postbox_key())


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


def _mailbox() -> dict:
    """This copy's own mailbox for answers: a random number and a key pair, made once."""
    d = _copy()
    if not (d.get("box") and d.get("box_key") and d.get("box_pub")):
        d.setdefault("id", secrets.token_hex(12))
        d["box"] = secrets.token_hex(16)
        d["box_key"], d["box_pub"] = sealed.keypair()
        _save_copy(d)
    return d


def counting() -> bool:
    return _copy().get("counting", True) is not False


def set_counting(on: bool) -> dict:
    d = _copy()
    d["counting"] = bool(on)
    _save_copy(d)
    return {"ok": True, "counting": bool(on),
            "head": ("This copy says it is in use, once a day." if on else
                     "This copy no longer says it is in use. It still looks for updates.")}


def _system_short() -> str:
    try:
        return f"{platform.system()} {platform.release()}"[:100]
    except Exception:
        return ""


def _system() -> str:
    try:
        return f"{platform.system()} {platform.release()} · Python {platform.python_version()}"[:160]
    except Exception:
        return ""


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


def _drop(payload: dict, note="other", timeout=TIMEOUT) -> dict:
    """Seal it to the developer and post it. Nothing readable leaves this computer."""
    if not can_send():
        return {"ok": False, "no_home": True, "error": "this copy has no address to send to yet"}
    try:
        env = sealed.seal(postbox_key(), {**payload, "at": int(time.time()), "product": product.name()})
    except Exception as e:
        return {"ok": False, "error": f"it could not be sealed ({type(e).__name__})"}
    return _ask("drop", "POST", {"sealed": env, "note": note}, timeout=timeout)


# ---------------------------------------------------------------------------
# "this copy is in use" and registering
# ---------------------------------------------------------------------------

def say_in_use() -> dict:
    """Once a day, if counting is on: this copy's number, version and kind of computer — sealed."""
    if not (counting() and can_send()):
        return {"ok": True, "sent": False}
    today = time.strftime("%Y-%m-%d")
    d = _copy()
    if d.get("seen_day") == today and d.get("seen_version") == product.version():
        return {"ok": True, "sent": False}
    r = _drop({"kind": "seen", "copy": install_id(), "version": product.version(), "system": _system_short()},
              timeout=10)
    if r.get("ok"):
        d = _copy()
        d["seen_day"], d["seen_version"] = today, product.version()
        _save_copy(d)
    return {"ok": bool(r.get("ok")), "sent": bool(r.get("ok"))}


def _is_publisher_copy() -> bool:
    return bool(product.publisher().get("home_key"))


def registration() -> dict:
    d = _copy()
    return {"ok": True, "home": can_send(), "registered": bool(d.get("person")),
            "person": d.get("person") or {}, "counting": counting(), "copy": install_id(),
            "publisher": _is_publisher_copy()}


def register(person: dict) -> dict:
    """Tell the developer who is using this copy — only what was typed, only when asked, sealed."""
    if not can_send():
        return {"ok": False, "error": "This copy has no address to register with yet."}
    if person.get("withdraw"):
        r = _drop({"kind": "withdraw", "copy": install_id()})
        if not r.get("ok"):
            return {"ok": False, "error": "Not withdrawn — " + str(r.get("error") or "no answer")}
        d = _copy()
        d.pop("person", None)
        _save_copy(d)
        return {"ok": True, "head": "Withdrawn. The developer's program removes your name and details when it next collects."}
    clean = {k: str(person.get(k) or "").strip()[:n] for k, n in
             (("name", 120), ("business", 160), ("email", 200), ("phone", 60), ("website", 300), ("country", 80))}
    if not (clean["name"] or clean["business"] or clean["email"]):
        return {"ok": False, "error": "Give at least your name, your business or your email."}
    if clean["email"] and ("@" not in clean["email"] or "." not in clean["email"].split("@")[-1]):
        return {"ok": False, "error": "That email address does not look right."}
    r = _drop({"kind": "register", "copy": install_id(), "version": product.version(),
               "system": _system_short(), "person": clean})
    if not r.get("ok"):
        return {"ok": False, "error": "Not registered — " + str(r.get("error") or "no answer") + ". Try again in a minute."}
    d = _copy()
    d["person"] = clean
    _save_copy(d)
    return {"ok": True, "head": "Registered. When you report a problem, the developer knows who it is from."}


# ---------------------------------------------------------------------------
# problem reports, and the answers
# ---------------------------------------------------------------------------

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
            "support": support, "home": can_send(),
            "support_email": product.info().get("support_email") or ""}


def send(title="", text="", contact="", attach=True) -> dict:
    title = str(title or "").strip()[:200]
    text = str(text or "").strip()[:20000]
    contact = str(contact or "").strip()[:200]
    if not title and not text:
        return {"ok": False, "error": "Say what went wrong first."}
    p = preview(attach)
    if not can_send():
        mail = p["support_email"]
        return {"ok": False, "no_home": True, "support": p["support"],
                "error": ("This copy cannot send reports by itself yet. "
                          + (f"Email it to {mail}, with the support report below." if mail
                             else "Copy the support report below and send it to whoever gave you the program."))}
    box = _mailbox()
    rid = time.strftime("%y%m%d") + "-" + secrets.token_hex(3)
    r = _drop({"kind": "report", "id": rid, "copy": install_id(), "title": title or "A problem", "text": text,
               "contact": contact, "version": p["version"], "system": p["system"], "support": p["support"],
               "box": box["box"], "box_pub": box["box_pub"]}, note="report")
    if not r.get("ok"):
        return {"ok": False, "error": "Not sent — " + str(r.get("error") or "no answer") + ". Try again in a minute."}
    rows = _load_mine()
    rows.append({"id": rid, "title": title or "A problem", "at": int(time.time()), "version": p["version"],
                 "status": "new", "reply": "", "fixed_in": ""})
    _save_mine(rows)
    return {"ok": True, "id": rid,
            "head": f"Sent, sealed so only the developer can read it. Your report is {rid} — the answer appears here."}


def fetch_answers() -> dict:
    """Open the sealed answers waiting in this copy's mailbox, keep them here, and clear the mailbox."""
    d = _copy()
    if not (can_send() and d.get("box") and d.get("box_key")):
        return {"ok": True, "got": 0}
    r = _ask("mail", "POST", {"box": d["box"]}, timeout=15)
    if not r.get("ok"):
        return {"ok": False, "got": 0, "error": r.get("error")}
    rows = _load_mine()
    by = {x.get("id"): x for x in rows}
    done, got = [], 0
    for letter in r.get("letters") or []:
        done.append(letter.get("id"))
        try:
            a = sealed.open_(d["box_key"], letter.get("sealed"))
        except ValueError:
            continue                      # not for us, or changed on the way: thrown away
        row = by.get(str(a.get("report") or "")) if isinstance(a, dict) else None
        if not row or a.get("kind") != "answer":
            continue
        if int(a.get("seq") or 0) <= int(row.get("seq") or 0):
            continue                      # an older answer arriving late
        row["seq"] = int(a.get("seq") or 0)
        if a.get("status") in STATUSES:
            row["status"] = a["status"]
        for k, n in (("reply", 5000), ("fixed_in", 40)):
            if k in a:
                row[k] = str(a.get(k) or "")[:n]
        row["updated"] = int(a.get("at") or time.time())
        got += 1
    if got:
        _save_mine(rows)
    if done:
        _ask("mail-done", "POST", {"box": d["box"], "ids": [x for x in done if x]}, timeout=15)
    return {"ok": True, "got": got}


def mine() -> dict:
    fetched = fetch_answers() if _load_mine() else {"ok": True}
    here = product.parse_version(product.version())
    out = []
    for r in _load_mine():
        st = r.get("status") or "new"
        o = {"id": r.get("id"), "title": r.get("title"), "at": r.get("at"), "version": r.get("version"),
             "status": st, "status_words": STATUS_WORDS.get(st, st), "reply": r.get("reply") or "",
             "fixed_in": r.get("fixed_in") or "", "updated": r.get("updated")}
        if o["fixed_in"] and product.parse_version(o["fixed_in"]) > here:
            o["update_needed"] = True
        out.append(o)
    out.sort(key=lambda o: o.get("at") or 0, reverse=True)
    res = {"ok": True, "reports": out, "home": can_send(), "answered": sum(1 for o in out if o.get("reply"))}
    if not fetched.get("ok"):
        res["note"] = "Answers could not be fetched just now: " + str(fetched.get("error") or "no answer")
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Problems reported, and the answers.")
    for f in ("send", "mine", "preview", "registration", "register", "in-use"):
        ap.add_argument("--" + f, action="store_true")
    ap.add_argument("--counting", choices=("on", "off"))
    ap.add_argument("--no-attach", action="store_true")
    a = ap.parse_args(argv)
    body = {}
    if a.send or a.register:
        try:
            body = json.loads(sys.stdin.read() or "{}")
        except Exception:
            body = {}
        body = body if isinstance(body, dict) else {}
    if a.send:
        r = send(body.get("title"), body.get("text"), body.get("contact"), body.get("attach", True) is not False)
    elif a.preview:
        r = preview(not a.no_attach)
    elif a.registration:
        r = registration()
    elif a.register:
        r = register(body)
    elif a.counting:
        r = set_counting(a.counting == "on")
    elif a.in_use:
        r = say_in_use()
    else:
        r = mine()
    print(json.dumps(r, ensure_ascii=False))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
