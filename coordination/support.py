"""
support.py — a problem report to the people who make the program, and their
answer. The side every copy has.

    python -m coordination.support --send      a report; JSON on stdin: {title, text, contact, attach}
    python -m coordination.support --mine      the reports sent from this account, and the answers
    python -m coordination.support --preview   exactly what a report would carry besides the words

    "i do not want to track the user that using my program" — so nothing here
    counts copies, registers anyone, or gives a copy a number.

A report is SEALED here with the publisher's public key (coordination/sealed.py)
before it leaves, so only his own computer can open it. It waits in his
Glasswork Home — a postbox on his website that cannot read it — until his
program collects it. Each report carries a mailbox number and key of its own,
made for that report alone: his answer comes back sealed to it, and nothing
links one report to another or to the person's other reports.

The reports a person sent are kept in their own account, sealed with their key
(workspace ACCOUNT_CONFIG), so another account on the same computer never sees
them.
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

try:
    from . import product, sealed, workspace
except ImportError:  # pragma: no cover - run as a script
    import product
    import sealed
    import workspace

TIMEOUT = 30
MINE = "support-reports.local.json"
STATUS_WORDS = {"new": "Sent — waiting to be read", "seen": "Read by the developer",
                "working": "Being worked on", "fixed": "Fixed", "wont-fix": "Not changing this"}
STATUSES = tuple(STATUS_WORDS)


def home_url() -> str:
    return (os.environ.get("GLASSWORK_HOME") or product.info().get("home_url") or "").strip().rstrip("/")


def postbox_key() -> str:
    """The publisher's PUBLIC sealing key — it can only seal, never open."""
    return (os.environ.get("GLASSWORK_POSTBOX_KEY") or product.info().get("postbox_key") or "").strip()


def can_send() -> bool:
    return bool(home_url() and postbox_key())


def _ask(do, method="GET", body=None, key="", base=None, timeout=TIMEOUT, path=None) -> dict:
    """One request to a Glasswork Home (or, with path, to another page of a server of his). Always a dict."""
    base = (base or home_url()).rstrip("/")
    if not base:
        return {"ok": False, "no_home": True, "error": "this copy has no address to send reports to yet"}
    if not base.lower().startswith(("https://", "http://127.0.0.1", "http://localhost")):
        return {"ok": False, "error": "reports only travel over https://"}
    data = json.dumps(body).encode("utf-8") if body is not None else None
    where = path or "/index.php?" + urllib.parse.urlencode({"do": do})
    req = urllib.request.Request(base + where, data=data, method=method)
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", f"{product.name()}/{product.version()}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if key:                              # some hosts drop Authorization, so it goes twice
        req.add_header("Authorization", "Bearer " + key)
        req.add_header("X-Home-Key", key)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            out = json.loads((e.read() or b"{}").decode("utf-8", "replace"))
        except Exception:
            out = {}
        out = out if isinstance(out, dict) else {}
        out.update(ok=False, status=e.code)
        out.setdefault("error", f"the Home answered {e.code}")
        if e.code == 404 and "not something this address does" not in str(out["error"]):
            out["error"] = "nothing answered at the Home's address — is the glasswork-home folder on the website?"
        return out
    except Exception as e:
        return {"ok": False, "unreachable": True, "error": f"could not reach the Home: {type(e).__name__}"}
    try:
        out = json.loads(raw)
    except Exception:
        return {"ok": False, "error": "the Home did not answer with JSON — is it the right address?"}
    return out if isinstance(out, dict) else {"ok": False, "error": "an odd answer from the Home"}


def _system() -> str:
    try:
        return f"{platform.system()} {platform.release()} · Python {platform.python_version()}"[:160]
    except Exception:
        return ""


def _load() -> list:
    try:
        rows = json.loads(workspace.read_secret(workspace.config_path(MINE), "[]"))
        return rows if isinstance(rows, list) else []
    except Exception:
        return []


def _save(rows) -> None:
    workspace.write_secret(workspace.config_path(MINE), json.dumps(rows[-200:]))


def preview(attach=True) -> dict:
    """Exactly what would be sent besides their own words — read before sending."""
    support = ""
    if attach:
        try:
            from . import diagnostics
            support = diagnostics.as_text(diagnostics.gather())
        except Exception as e:
            support = f"(the support report could not be made: {type(e).__name__})"
    return {"ok": True, "version": product.version(), "system": _system(), "support": support,
            "home": can_send(), "support_email": product.info().get("support_email") or ""}


def send(title="", text="", contact="", attach=True) -> dict:
    title, text, contact = str(title or "").strip()[:200], str(text or "").strip()[:20000], str(contact or "").strip()[:200]
    if not title and not text:
        return {"ok": False, "error": "Say what went wrong first."}
    p = preview(attach)
    if not can_send():
        mail = p["support_email"]
        return {"ok": False, "no_home": True, "support": p["support"],
                "error": "This copy cannot send reports by itself yet. " + (
                    f"Email it to {mail}, with the support report below." if mail
                    else "Copy the support report below and send it to whoever gave you the program.")}
    rid = time.strftime("%y%m%d") + "-" + secrets.token_hex(3)
    box, (box_key, box_pub) = secrets.token_hex(16), sealed.keypair()
    try:
        env = sealed.seal(postbox_key(), {"kind": "report", "id": rid, "title": title or "A problem", "text": text,
                                          "contact": contact, "version": p["version"], "system": p["system"],
                                          "support": p["support"], "box": box, "box_pub": box_pub,
                                          "at": int(time.time()), "product": product.name()})
    except Exception as e:
        return {"ok": False, "error": f"Not sent — it could not be sealed ({type(e).__name__})."}
    r = _ask("drop", "POST", {"sealed": env})
    if not r.get("ok"):
        return {"ok": False, "error": "Not sent — " + str(r.get("error") or "no answer") + ". Try again in a minute."}
    rows = _load()
    rows.append({"id": rid, "title": title or "A problem", "at": int(time.time()), "version": p["version"],
                 "status": "new", "reply": "", "fixed_in": "", "box": box, "box_key": box_key})
    _save(rows)
    return {"ok": True, "id": rid,
            "head": f"Sent, sealed so only the developer can read it. Your report is {rid} — the answer appears here."}


def fetch_answers() -> dict:
    """Open the sealed answers waiting for this account's reports, keep them, and clear them off the Home."""
    rows = _load()
    waiting = {r["box"]: r for r in rows[-50:] if r.get("box") and r.get("box_key")}
    if not (waiting and can_send()):
        return {"ok": True, "got": 0}
    r = _ask("mail", "POST", {"boxes": list(waiting)}, timeout=15)
    if not r.get("ok"):
        return {"ok": False, "got": 0, "error": r.get("error")}
    got, done = 0, {}
    for box, letters in (r.get("letters") or {}).items():
        row = waiting.get(box)
        if not row or not isinstance(letters, list):
            continue
        done[box] = [x.get("id") for x in letters if x.get("id")]
        for letter in letters:
            try:
                a = sealed.open_(row["box_key"], letter.get("sealed"))
            except ValueError:
                continue                     # not for this report, or changed on the way: thrown away
            if not isinstance(a, dict) or a.get("kind") != "answer" or a.get("report") != row["id"]:
                continue
            if int(a.get("seq") or 0) <= int(row.get("seq") or 0):
                continue                     # an older answer arriving late
            row["seq"] = int(a.get("seq") or 0)
            if a.get("status") in STATUSES:
                row["status"] = a["status"]
            row["reply"], row["fixed_in"] = str(a.get("reply") or "")[:5000], str(a.get("fixed_in") or "")[:40]
            got += 1
    if got:
        _save(rows)
    if done:
        _ask("mail-done", "POST", {"done": done}, timeout=15)
    return {"ok": True, "got": got}


def mine() -> dict:
    fetched = fetch_answers()
    here = product.parse_version(product.version())
    out = []
    for r in sorted(_load(), key=lambda x: x.get("at") or 0, reverse=True):
        st = r.get("status") or "new"
        out.append({"id": r.get("id"), "title": r.get("title"), "at": r.get("at"), "version": r.get("version"),
                    "status": st, "status_words": STATUS_WORDS.get(st, st), "reply": r.get("reply") or "",
                    "fixed_in": r.get("fixed_in") or "",
                    "update_needed": bool(r.get("fixed_in")) and product.parse_version(r["fixed_in"]) > here})
    res = {"ok": True, "reports": out, "home": can_send()}
    if not fetched.get("ok"):
        res["note"] = "Answers could not be fetched just now: " + str(fetched.get("error") or "no answer")
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="A problem report, and the answer.")
    for f in ("send", "mine", "preview", "no-attach"):
        ap.add_argument("--" + f, action="store_true")
    a = ap.parse_args(argv)
    if a.send:
        try:
            b = json.loads(sys.stdin.read() or "{}")
        except Exception:
            b = {}
        b = b if isinstance(b, dict) else {}
        r = send(b.get("title"), b.get("text"), b.get("contact"), b.get("attach", True) is not False)
    elif a.preview:
        r = preview(not a.no_attach)
    else:
        r = mine()
    print(json.dumps(r, ensure_ascii=False))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
