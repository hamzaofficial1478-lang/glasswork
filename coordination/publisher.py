"""
publisher.py — the developer's side: who uses his program, their issues, his
answers, and putting a version out. ON HIS COMPUTER ONLY.

    "i want the details to be on the program ... I want the Users to be stored
     on my pc in program storage not on my website hosting"
    "the users should not have the program back end they should only have the
     program that will be working not the backend that i will be managing"

This file, the users room (ui/publisher.js), the Home's own code and his guides
are left out of every release and every installer (product.PUBLISHER_ONLY), so
a copy he gives out has none of it — and nothing in a copy can reach his
computer: his program goes and collects; nothing ever calls in.

WHERE THINGS ARE

  His records — every copy in use, who registered, every report and his answers
  — are kept in data/publisher/records.locked on his computer, locked with a key
  made from his postbox key. They are not on his website at all.

  His Glasswork Home (publish-to-your-site/glasswork-home) is a postbox on his
  website. Copies post envelopes SEALED with his public postbox key; the Home
  cannot read them; collect() takes them home, opens them here, files them in
  his records and has the Home delete them. His answers go back sealed to the
  person's own copy.

  Three keys, made here by setup():
    home key     (config/publisher.local.json; a fingerprint of it in the Home's
                 home-key.php) — lets his program collect, answer and publish.
    postbox key  (~/.glasswork-publisher/postbox.key) — opens what copies send
                 him and locks his records. Only its public half is ever given out.
    signing key  (~/.glasswork-publisher/signing.key) — signs every version.
  Back the .glasswork-publisher folder up somewhere safe: without the postbox
  key his records and waiting envelopes cannot be opened by anyone, him included.

    python -m coordination.publisher --view           is this copy set up to publish
    python -m coordination.publisher --setup          JSON on stdin: {site, email}
    python -m coordination.publisher --home-zip       the Home folder to upload
    python -m coordination.publisher --check-home     does the Home answer, and know him
    python -m coordination.publisher --collect        take the waiting envelopes home
    python -m coordination.publisher --users          everyone using the program
    python -m coordination.publisher --user COPY      one of them, with their issues
    python -m coordination.publisher --forget COPY    remove one (--with-reports: theirs too)
    python -m coordination.publisher --inbox          every report
    python -m coordination.publisher --read ID        one report in full
    python -m coordination.publisher --answer         JSON on stdin: {id, status, reply, fixed_in}
    python -m coordination.publisher --remove ID
    python -m coordination.publisher --publish [--installer] [--notes TEXT] [--job FILE]
    python -m coordination.publisher --new-key KEYFILE
    python -m coordination.publisher --make-release DIR --key KEYFILE [--base-url URL]
"""
import argparse
import base64
import hashlib
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

try:
    from . import product, sealed, support, updates, workspace
except ImportError:  # pragma: no cover - run as a script
    import product
    import sealed
    import support
    import updates
    import workspace

ROOT = product.ROOT
HOME_SOURCE = ROOT / "publish-to-your-site" / "glasswork-home" / "index.php"
HOME_NEEDED = 3                       # the Home that keeps nothing readable
STATUS_WORDS = support.STATUS_WORDS
STATUSES = support.STATUSES


# ---------------------------------------------------------------------------
# his settings and keys
# ---------------------------------------------------------------------------

def _pub() -> dict:
    return product.publisher()


def _save_pub(d: dict) -> None:
    f = product.publisher_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(d, indent=1), encoding="utf-8")


def _key() -> str:
    return _pub().get("home_key") or ""


def is_publisher() -> bool:
    return bool(_key())


def _signing_key_path() -> Path:
    return Path(os.environ.get("GLASSWORK_SIGNING_KEY") or (Path.home() / ".glasswork-publisher" / "signing.key"))


def _postbox_key_path() -> Path:
    return _signing_key_path().parent / "postbox.key"


def _postbox_private() -> str:
    try:
        return _postbox_key_path().read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _private_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def publisher_view() -> dict:
    p = _pub()
    st = _load_quiet()
    return {"ok": True, "publisher": bool(p.get("home_key")), "can_publish": True,
            "ready": bool(p.get("home_key") and _postbox_private() and (p.get("public") or {}).get("postbox_key")),
            "home_url": (p.get("public") or {}).get("home_url") or "", "email": p.get("email") or "",
            "signing_key": bool(p.get("signing_key") and Path(p["signing_key"]).exists()),
            "postbox_key": bool(_postbox_private()), "version": product.version(),
            "home_ok_at": st.get("home_ok_at") or 0, "last_collect": st.get("last_collect") or 0,
            "latest": st.get("latest") or ""}


def setup(site, email="") -> dict:
    """Make this copy the publisher's: a Home address and three keys, all kept here."""
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
        keyfile.parent.mkdir(parents=True, exist_ok=True)
        made = new_key(str(keyfile))
        if not made.get("ok"):
            return made
    pub_key = made["update_key"] if made else _public_key(keyfile)
    if not _postbox_private():
        priv, _pub_half = sealed.keypair()
        _private_file(_postbox_key_path(), priv + "\n")
    p["signing_key"] = str(keyfile)
    p["public"] = {"home_url": home, "update_url": home + "/index.php?do=latest", "update_key": pub_key,
                   "postbox_key": sealed.public_of(_postbox_private()),
                   "support_email": email or (p.get("public") or {}).get("support_email", "")}
    _save_pub(p)
    return {"ok": True, "home_url": home,
            "head": ("Your Glasswork Home is ready to upload. Download it, unzip it, and put the "
                     "glasswork-home folder in your website's main folder. Then press Check."),
            "keys_folder": str(keyfile.parent)}


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
        # A fingerprint of the key, not the key: someone reading the hosting's
        # files still cannot collect, answer or put a version out.
        z.writestr("glasswork-home/home-key.php",
                   "<?php\nreturn 'sha256:" + hashlib.sha256(p["home_key"].encode("utf-8")).hexdigest() + "';\n")
        cfg = {"owner_email": p.get("email") or "", "product": product.name()}
        z.writestr("glasswork-home/home-config.php", "<?php\nreturn " + _php_array(cfg) + ";\n")
    return {"ok": True, "zip": buf.getvalue(), "filename": "glasswork-home.zip"}


def _php_array(d: dict) -> str:
    def q(v):
        return "'" + str(v).replace("\\", "\\\\").replace("'", "\\'") + "'"
    return "[" + ", ".join(f"{q(k)} => {q(v)}" for k, v in d.items()) + "]"


def _ask(do, method="GET", body=None, query=None, timeout=support.TIMEOUT) -> dict:
    return support._ask(do, method, body, query, key=_key(), base=(_pub().get("public") or {}).get("home_url"),
                        timeout=timeout)


# ---------------------------------------------------------------------------
# his records, on this computer
# ---------------------------------------------------------------------------

def records_file() -> Path:
    return workspace.data_root() / "publisher" / "records.locked"


def _blank() -> dict:
    return {"copies": {}, "reports": {}, "collected": 0}


def _load() -> dict:
    f = records_file()
    if not f.exists():
        return _blank()
    priv = _postbox_private()
    if not priv:
        raise ValueError("the postbox key is missing from this computer, so your records cannot be opened")
    d = sealed.unlock(sealed.store_key(priv), f.read_bytes())
    for k, v in _blank().items():
        d.setdefault(k, v)
    return d


def _load_quiet() -> dict:
    try:
        return _load()
    except Exception:
        return _blank()


def _save(d: dict) -> None:
    f = records_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + f".{secrets.token_hex(3)}.tmp")
    tmp.write_bytes(sealed.lock(sealed.store_key(_postbox_private()), d))
    os.replace(tmp, f)


def _cut(v, n) -> str:
    return str(v or "").strip()[:n]


def _copy_id(v) -> str:
    v = str(v or "").strip().lower()
    return v if re.fullmatch(r"[a-f0-9]{16,40}", v) else ""


def _touch(st: dict, cid: str, at: int, version="", system="") -> dict:
    c = st["copies"].setdefault(cid, {"id": cid, "first": at, "last": 0, "version": "", "system": "", "person": None})
    c["first"] = min(int(c.get("first") or at), at)
    if at >= int(c.get("last") or 0):
        c["last"] = at
        if version:
            c["version"] = _cut(version, 40)
        if system:
            c["system"] = _cut(system, 120)
    return c


def _file_envelope(st: dict, e: dict, at: int) -> str:
    """One opened envelope into his records. Returns what it was."""
    kind = str(e.get("kind") or "")
    cid = _copy_id(e.get("copy"))
    if cid:
        # A report's "system" is the long form; the short one comes from the daily "in use".
        _touch(st, cid, at, _cut(e.get("version"), 40), "" if kind == "report" else _cut(e.get("system"), 120))
    if kind == "register" and cid:
        p = e.get("person") if isinstance(e.get("person"), dict) else {}
        st["copies"][cid]["person"] = {k: _cut(p.get(k), n) for k, n in
                                       (("name", 120), ("business", 160), ("email", 200), ("phone", 60),
                                        ("website", 300), ("country", 80))} | {"at": at}
        return "register"
    if kind == "withdraw" and cid:
        st["copies"][cid]["person"] = None
        return "withdraw"
    if kind == "report":
        rid = re.sub(r"[^a-z0-9-]", "", str(e.get("id") or "").lower())[:40] or (
            time.strftime("%y%m%d", time.gmtime(at)) + "-" + secrets.token_hex(3))
        if rid in st["reports"]:
            return "again"
        box = str(e.get("box") or "").lower()
        st["reports"][rid] = {
            "id": rid, "copy": cid, "at": at, "title": _cut(e.get("title"), 200) or "A problem",
            "text": _cut(e.get("text"), 20000), "contact": _cut(e.get("contact"), 200),
            "version": _cut(e.get("version"), 40), "system": _cut(e.get("system"), 160),
            "product": _cut(e.get("product"), 60), "support": _cut(e.get("support"), 300000),
            "status": "new", "reply": "", "fixed_in": "", "updated": at,
            "box": box if re.fullmatch(r"[a-f0-9]{32}", box) else "", "box_pub": _cut(e.get("box_pub"), 64)}
        return "report"
    return "seen" if kind == "seen" else "other"


def _take_old_records(st: dict) -> int:
    """An older Home kept reports and people readable on the website: bring them here, then remove them there."""
    r = _ask("old-records")
    if not r.get("ok"):
        return 0
    n = 0
    for x in r.get("records") or []:
        d = x.get("record") if isinstance(x.get("record"), dict) else {}
        if x.get("kind") == "copies" and _copy_id(d.get("id")):
            c = _touch(st, _copy_id(d["id"]), int(d.get("last") or time.time()), d.get("version", ""), d.get("system", ""))
            if isinstance(d.get("person"), dict):
                c["person"] = d["person"]
            n += 1
        elif x.get("kind") == "reports" and d.get("id"):
            got = _file_envelope(st, {**d, "kind": "report", "box": "", "box_pub": ""}, int(d.get("at") or time.time()))
            if got == "report":
                st["reports"][re.sub(r"[^a-z0-9-]", "", str(d["id"]).lower())[:40]].update(
                    {k: d[k] for k in ("status", "reply", "fixed_in") if k in d})
            n += 1
    _save(st)
    _ask("old-records-done", "POST", {})
    return n


def check_home() -> dict:
    if not is_publisher():
        return {"ok": False, "error": "Set up your Home first."}
    r = _ask("ping")
    if not r.get("ok"):
        return {"ok": False, "error": "The Home did not answer: " + str(r.get("error") or "no answer")}
    if not r.get("owner"):
        return {"ok": False, "error": ("The Home answered, but does not know your key — upload the "
                                       "glasswork-home folder again, from the newest download.")}
    if int(r.get("home") or 0) < HOME_NEEDED:
        return {"ok": False, "old_home": True,
                "error": ("Your Home on the website is an older kind, which kept reports readable there. "
                          "Download it again and upload it in place of the old glasswork-home folder.")}
    try:
        st = _load()
    except ValueError as e:
        return {"ok": False, "error": "Your Home answers, but " + str(e) + "."}
    if not _postbox_private():
        return {"ok": False, "error": "Your Home answers, but this computer has no postbox key yet — press Make it again."}
    st["home_ok_at"], st["latest"] = int(time.time()), r.get("latest") or ""
    _save(st)
    return {"ok": True, "latest": r.get("latest") or "", "waiting": int(r.get("waiting") or 0),
            "old_records": int(r.get("old_records") or 0),
            "head": "Your Home answers and knows you." + (f" Version {r['latest']} is out." if r.get("latest") else "")}


def collect() -> dict:
    """Take every waiting envelope home, open it here, file it, and have the Home delete it."""
    if not (is_publisher() and _postbox_private()):
        return {"ok": False, "error": "Set up your Home first."}
    home = check_home()
    if not home.get("ok"):
        return home
    st = _load()
    old = _take_old_records(st) if home.get("old_records") else 0
    got = {"report": 0, "register": 0, "withdraw": 0, "seen": 0, "again": 0, "other": 0}
    unreadable, people_before = 0, len(st["copies"])
    priv = _postbox_private()
    for _round in range(25):
        r = _ask("collect", timeout=60)
        if not r.get("ok"):
            return {"ok": False, "error": "Collecting stopped: " + str(r.get("error") or "no answer")}
        ids = []
        for item in r.get("envelopes") or []:
            ids.append(item.get("id"))
            try:
                e = sealed.open_(priv, item.get("sealed"))
            except ValueError:
                unreadable += 1          # not sealed to you, or changed on the way: thrown away
                continue
            if isinstance(e, dict):
                got[_file_envelope(st, e, int(item.get("at") or time.time()))] += 1
        st["collected"] = int(st.get("collected") or 0) + len(ids)
        _save(st)                        # kept here BEFORE the Home is told to delete
        if ids:
            done = _ask("ack", "POST", {"ids": [i for i in ids if i]})
            if not done.get("ok"):
                return {"ok": False, "error": "The Home did not let go of what was collected: "
                                              + str(done.get("error") or "no answer")}
        if not r.get("more"):
            break
    sent = _send_unsent(st)
    st["last_collect"] = int(time.time())
    _save(st)
    new_people = len(st["copies"]) - people_before
    bits = []
    if got["report"]:
        bits.append(f"{got['report']} new report" + ("s" if got["report"] != 1 else ""))
    if new_people:
        bits.append(f"{new_people} new cop" + ("ies" if new_people != 1 else "y"))
    if got["register"]:
        bits.append(f"{got['register']} registration" + ("s" if got["register"] != 1 else ""))
    return {"ok": True, "new_reports": got["report"], "new_people": new_people, "registered": got["register"],
            "unreadable": unreadable, "old_records": old, "answers_sent": sent,
            "head": ("Collected: " + ", ".join(bits) + ".") if bits else "Nothing new waiting."}


# ---------------------------------------------------------------------------
# answers, sealed back to the person's own copy
# ---------------------------------------------------------------------------

def _letter(rep: dict, timeout=support.TIMEOUT) -> bool:
    if not (rep.get("box") and rep.get("box_pub")):
        return False
    # Numbered, so that "read" and "fixed" sent in the same second arrive in the right order.
    rep["seq"] = int(rep.get("seq") or 0) + 1
    try:
        env = sealed.seal(rep["box_pub"], {"kind": "answer", "report": rep["id"], "status": rep.get("status"),
                                           "reply": rep.get("reply") or "", "fixed_in": rep.get("fixed_in") or "",
                                           "seq": rep["seq"], "at": int(time.time())})
    except Exception:
        return False
    return bool(_ask("reply", "POST", {"box": rep["box"], "sealed": env}, timeout=timeout).get("ok"))


def _send_unsent(st: dict) -> int:
    n = 0
    for rep in st["reports"].values():
        if rep.get("unsent") and _letter(rep, timeout=15):
            rep.pop("unsent", None)
            n += 1
    return n


def _row(rep: dict) -> dict:
    return {"id": rep["id"], "at": rep["at"], "title": rep["title"], "text": rep["text"][:240],
            "contact": rep.get("contact") or "", "version": rep.get("version") or "", "status": rep["status"],
            "status_words": STATUS_WORDS.get(rep["status"], rep["status"]), "fixed_in": rep.get("fixed_in") or "",
            "replied": bool(rep.get("reply")), "has_support": bool(rep.get("support")), "copy": rep.get("copy") or "",
            "can_answer": bool(rep.get("box") and rep.get("box_pub")), "unsent": bool(rep.get("unsent"))}


def _not_set_up() -> dict:
    return {"ok": False, "error": "This copy is not set up as the publisher yet."}


def inbox() -> dict:
    if not is_publisher():
        return _not_set_up()
    st = _load()
    rows = sorted((_row(r) for r in st["reports"].values()), key=lambda x: x["at"], reverse=True)
    return {"ok": True, "reports": rows[:2000], "new": sum(1 for r in rows if r["status"] == "new")}


def read(rid) -> dict:
    if not is_publisher():
        return _not_set_up()
    st = _load()
    rep = st["reports"].get(str(rid))
    if not rep:
        return {"ok": False, "error": "No such report."}
    if rep["status"] == "new":
        rep["status"], rep["updated"] = "seen", int(time.time())
        # They see "Read by the developer". If the Home does not answer now, it goes with the next collect.
        if not _letter(rep, timeout=8):
            rep["unsent"] = bool(rep.get("box"))
        _save(st)
    out = {k: v for k, v in rep.items() if k not in ("box", "box_pub")}
    out["status_words"] = STATUS_WORDS.get(rep["status"], rep["status"])
    return {"ok": True, "report": out}


def answer(rid, status=None, reply=None, fixed_in=None) -> dict:
    if not is_publisher():
        return _not_set_up()
    st = _load()
    rep = st["reports"].get(str(rid))
    if not rep:
        return {"ok": False, "error": "No such report."}
    if status:
        if status not in STATUSES:
            return {"ok": False, "error": "not a status"}
        rep["status"] = status
    if reply is not None:
        rep["reply"] = _cut(reply, 5000)
    if fixed_in is not None:
        rep["fixed_in"] = _cut(fixed_in, 40)
    rep["updated"] = int(time.time())
    sent = _letter(rep)
    rep["unsent"] = not sent and bool(rep.get("box"))
    if not rep["unsent"]:
        rep.pop("unsent", None)
    _save(st)
    out = {"ok": True, "id": rep["id"], "status": rep["status"], "sent": sent}
    if not rep.get("box"):
        out["note"] = "Saved. This report came from an older copy, which cannot receive answers."
    elif not sent:
        out["note"] = "Saved here. Your Home did not answer, so it goes to them the next time you collect."
    return out


def remove(rid) -> dict:
    if not is_publisher():
        return _not_set_up()
    st = _load()
    gone = st["reports"].pop(str(rid), None)
    _save(st)
    return {"ok": True, "removed": bool(gone)}


def users() -> dict:
    if not is_publisher():
        return _not_set_up()
    st = _load()
    by = {}
    for rep in st["reports"].values():
        k = rep.get("copy") or ""
        if k:
            b = by.setdefault(k, {"all": 0, "open": 0})
            b["all"] += 1
            if rep["status"] not in ("fixed", "wont-fix"):
                b["open"] += 1
    latest = st.get("latest") or ""
    lv = product.parse_version(latest or "0")
    week = time.time() - 7 * 86400
    rows = []
    for c in st["copies"].values():
        rows.append({**c, "reports": by.get(c["id"], {"all": 0, "open": 0}),
                     "old_version": bool(latest) and product.parse_version(c.get("version") or "0") < lv})
    rows.sort(key=lambda c: c.get("last") or 0, reverse=True)
    return {"ok": True, "users": rows[:5000], "latest": latest, "last_collect": st.get("last_collect") or 0,
            "counts": {"copies": len(rows), "this_week": sum(1 for c in rows if (c.get("last") or 0) >= week),
                       "registered": sum(1 for c in rows if c.get("person")),
                       "open_issues": sum(1 for r in st["reports"].values() if r["status"] not in ("fixed", "wont-fix")),
                       "on_latest": sum(1 for c in rows if latest and c.get("version") == latest)}}


def user(cid) -> dict:
    if not is_publisher():
        return _not_set_up()
    st = _load()
    c = st["copies"].get(str(cid))
    if not c:
        return {"ok": False, "error": "No such copy."}
    mine = sorted((_row(r) for r in st["reports"].values() if r.get("copy") == c["id"]),
                  key=lambda x: x["at"], reverse=True)
    return {"ok": True, "user": c, "reports": mine}


def forget(cid, with_reports=False) -> dict:
    if not is_publisher():
        return _not_set_up()
    st = _load()
    st["copies"].pop(str(cid), None)
    gone = 0
    if with_reports:
        for rid in [k for k, r in st["reports"].items() if r.get("copy") == str(cid)]:
            st["reports"].pop(rid)
            gone += 1
    _save(st)
    return {"ok": True, "reports_removed": gone}


# ---------------------------------------------------------------------------
# signing and putting a version out
# ---------------------------------------------------------------------------

def new_key(path) -> dict:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    p = Path(path)
    if p.exists():
        return {"ok": False, "error": f"{p} already exists — not overwriting a signing key"}
    k = Ed25519PrivateKey.generate()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(k.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                  serialization.NoEncryption()))
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass
    pub = k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return {"ok": True, "key_file": str(p), "update_key": base64.b64encode(pub).decode(),
            "next": "Put update_key into config/product.json. Keep the key file secret and OUT of the program folder."}


def sign(manifest: dict, key_file) -> str:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    k = Ed25519PrivateKey.from_private_bytes(Path(key_file).read_bytes())
    return base64.b64encode(k.sign(updates._signed_text(manifest))).decode()


def release_product_json() -> str:
    """
    config/product.json as a release carries it: his Home's address and the
    PUBLIC halves of his keys, so every copy installed from it knows where its
    updates come from and can seal what it sends him. His own product.json is
    never edited for this.
    """
    try:
        d = json.loads(product.FILE.read_text(encoding="utf-8"))
    except Exception:
        d = {}
    mine = _pub().get("public") or {}
    d.update({k: mine[k] for k in product.PUBLIC_KEYS if mine.get(k)})
    return json.dumps(d, indent=2) + "\n"


def release_files() -> list:
    """What a copy he gives out is made of: every tracked file, less anything private or his alone."""
    try:
        listed = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, timeout=60)
        files = [f for f in listed.stdout.decode("utf-8").split("\0") if f]
    except Exception:
        files = []
    return [f for f in files if not updates._kept(f) and not product.publisher_only(f) and (ROOT / f).is_file()]


def make_release(out_dir, key_file, base_url="", notes="", installer=None) -> dict:
    """Zip the TRACKED files only (so nothing private can ride along), sign, write the manifest."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    files = release_files()
    if not files:
        return {"ok": False, "error": "a release is made from a git copy, so only tracked files go in"}
    ver = product.version()
    top = f"{product.name().lower()}-{ver}"
    zname = f"{top}.zip"
    with zipfile.ZipFile(out / zname, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            if f == "config/product.json":
                z.writestr(f"{top}/{f}", release_product_json())
            else:
                z.write(ROOT / f, f"{top}/{f}")
    digest = hashlib.sha256((out / zname).read_bytes()).hexdigest()
    m = {"version": ver, "zip": (base_url.rstrip("/") + "/" + zname) if base_url else zname, "sha256": digest,
         "notes": notes, "published": time.strftime("%Y-%m-%d")}
    m["signature"] = sign(m, key_file)
    if installer:
        exe = Path(installer)
        iname = f"{product.name().lower()}-{ver}-setup.exe"
        if exe.resolve() != (out / iname).resolve():
            shutil.copy2(exe, out / iname)
        idig = hashlib.sha256((out / iname).read_bytes()).hexdigest()
        m.update(installer=(base_url.rstrip("/") + "/" + iname) if base_url else iname, installer_sha256=idig)
        m["installer_signature"] = sign({"version": ver, "sha256": idig}, key_file)
    (out / "manifest.json").write_text(json.dumps(m, indent=1), encoding="utf-8")
    return {"ok": True, "zip": str(out / zname), "manifest": str(out / "manifest.json"), "version": ver,
            "files": len(files), "installer": str(out / m["installer"]) if installer else ""}


def upload(path, name=None, chunk=4 * 1024 * 1024, progress=None) -> dict:
    """Send one file to the Home's updates/ folder, in pieces, checked whole at the end."""
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
            r = _ask("upload", "POST", body, timeout=180)
            if r.get("ok") or not r.get("unreachable"):
                break
            time.sleep(2 + attempt * 3)
        if not r.get("ok"):
            return {"ok": False, "error": f"{name}: " + str(r.get("error") or "the upload stopped")}
        if progress:
            progress(name, i + 1, parts)
    return {"ok": True, "name": name, "bytes": len(data), "sha256": digest}


# "when ever i will update the program here the users will get the update
#  message on their program at their end"
#
# One button on his own copy: zip this version, sign it, build the Windows
# installer if asked (installer/build_windows.py — on Windows), send them to his
# Glasswork Home in pieces, and only then put the signed manifest out.

def publish(notes="", installer=False, say=None) -> dict:
    say = say or (lambda stage, **kw: None)
    if not is_publisher():
        return {"ok": False, "error": "This copy is not set up to publish. Your users → Make it, first."}
    pub = _pub()
    key = pub.get("signing_key") or ""
    if not key or not Path(key).exists():
        return {"ok": False, "error": "The signing key is missing, so nothing can be signed. Set up the Home again."}
    if not (pub.get("public") or {}).get("postbox_key"):
        return {"ok": False, "error": "Make your Home again first (Your users → Make it), so copies can seal what they send you."}
    if not updates.is_git_copy():
        return {"ok": False, "error": "A version is put out from the publisher's own git copy of the program."}
    say("Asking your Home which version is out")
    home = check_home()
    if not home.get("ok"):
        return home
    ver = product.version()
    if home.get("latest") and product.parse_version(home["latest"]) >= product.parse_version(ver):
        return {"ok": False, "error": (f"Version {home['latest']} is already out, and this copy is {ver}. "
                                       "A new version needs a higher number first.")}
    tmp = Path(tempfile.mkdtemp(prefix="release-"))
    try:
        exe = None
        if installer:
            say("Building the Windows installer — a few minutes")
            built = _build_installer(tmp)
            if not built.get("ok"):
                return built
            exe = built["installer"]
        say("Zipping and signing this version")
        rel = make_release(tmp / "out", key, notes=notes, installer=exe)
        if not rel.get("ok"):
            return rel
        m = json.loads(Path(rel["manifest"]).read_text(encoding="utf-8"))
        for f in [rel["zip"]] + ([rel["installer"]] if rel.get("installer") else []):
            say(f"Sending {Path(f).name} to your Home")
            up = upload(f, progress=lambda n, i, k: say(f"Sending {n}", part=i, of=k))
            if not up.get("ok"):
                return up
        say("Putting the new version out")
        r = _ask("release", "POST", {"manifest": m})
        if not r.get("ok"):
            return {"ok": False, "error": "The Home would not put it out: " + str(r.get("error") or "no answer")}
        return {"ok": True, "version": ver, "installer": bool(exe), "download_page": r.get("download_page"),
                "why": (f"Version {ver} is out. Everyone using the program is told the next time it opens"
                        + (", and new people can download the installer from your Home." if exe else "."))}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _build_installer(tmp: Path) -> dict:
    script = ROOT / "installer" / "build_windows.py"
    if os.name != "nt" and not os.environ.get("GLASSWORK_CROSS_BUILD"):
        return {"ok": False, "error": "The Windows installer is built on Windows. Publish from your Windows computer, "
                                      "or untick the installer to put out the update alone."}
    p = subprocess.run([sys.executable, str(script), "--out", str(tmp), "--product-json", "-"],
                       input=release_product_json(), cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=3600)
    last = (p.stdout or "").strip().splitlines()[-1:] or [""]
    try:
        r = json.loads(last[0])
    except Exception:
        r = {"ok": False, "error": "the installer build said: " + ((p.stderr or p.stdout or "nothing")[-400:])}
    return r


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="The publisher's side: users, issues, answers, versions.")
    for f in ("view", "setup", "home-zip", "check-home", "collect", "inbox", "answer", "users", "publish",
              "installer", "with-reports"):
        ap.add_argument("--" + f, action="store_true")
    for f in ("read", "remove", "user", "forget", "notes", "job", "new-key", "make-release", "key", "base-url"):
        ap.add_argument("--" + f, default=None)
    a = ap.parse_args(argv)
    body = {}
    if a.setup or a.answer:
        try:
            body = json.loads(sys.stdin.read() or "{}")
        except Exception:
            body = {}
        body = body if isinstance(body, dict) else {}
    try:
        if a.publish:
            job = Path(a.job) if a.job else None

            def say(stage, **kw):
                if job:
                    job.write_text(json.dumps({"running": True, "stage": stage, "at": int(time.time()), **kw}),
                                   encoding="utf-8")
            try:
                r = publish(a.notes or "", a.installer, say)
            except Exception as e:
                r = {"ok": False, "error": f"publishing stopped: {type(e).__name__}: {e}"}
            if job:
                job.write_text(json.dumps({"running": False, "done": True, "at": int(time.time()), "result": r}),
                               encoding="utf-8")
        elif a.setup:
            r = setup(body.get("site"), body.get("email"))
        elif a.home_zip:
            z = home_zip()
            r = {**z, "zip": base64.b64encode(z["zip"]).decode()} if z.get("ok") else z
        elif a.check_home:
            r = check_home()
        elif a.collect:
            r = collect()
        elif a.inbox:
            r = inbox()
        elif a.read:
            r = read(a.read)
        elif a.answer:
            r = answer(body.get("id"), body.get("status"), body.get("reply"), body.get("fixed_in"))
        elif a.remove:
            r = remove(a.remove)
        elif a.users:
            r = users()
        elif a.user:
            r = user(a.user)
        elif a.forget:
            r = forget(a.forget, a.with_reports)
        elif a.new_key:
            r = new_key(a.new_key)
        elif a.make_release:
            r = make_release(a.make_release, a.key, a.base_url or "", a.notes or "") if a.key else \
                {"ok": False, "error": "--key is needed"}
        else:
            r = publisher_view()
    except ValueError as e:
        r = {"ok": False, "error": str(e)}
    print(json.dumps(r, ensure_ascii=False))
    return 0 if r.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
