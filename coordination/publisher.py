"""
publisher.py — the developer's own side: the problem reports people send, his
answers, and putting a version out. ON HIS COMPUTER ONLY.

This file, the Reports room (ui/publisher.js), the Home's own code and his
guides are left out of every release and installer (product.PUBLISHER_ONLY),
so a copy he gives out has none of it, and nothing in a copy can reach his
computer: his program goes and collects; nothing ever calls in.

"i do not want to track the user that using my program" — nothing here counts
copies or keeps a list of people. A report says what went wrong, sealed on the
sender's computer so only this one can open it; it carries no number that ties
it to a copy, or to any other report.

    His records (the reports and his answers) are in data/publisher/records.locked,
    locked with a key made from his postbox key. Nothing readable is on his website.

    His Botix Home (publish-to-your-site/glasswork-home) is a postbox on the
    PROGRAM'S OWN website — never one the program looks after for a client
    (setup() refuses those). It holds sealed envelopes until collect() takes
    them home, and then deletes them.

  Three keys, made by setup():
    home key     config/publisher.local.json (the Home holds only a fingerprint)
                 — lets his program collect, answer and publish.
    postbox key  ~/.glasswork-publisher/postbox.key — opens the reports and locks
                 his records. Only its public half is given out.
    signing key  ~/.glasswork-publisher/signing.key — signs every version.
  Back the .glasswork-publisher folder up: without the postbox key nobody, him
  included, can open his records or the reports still waiting.

    python -m coordination.publisher --view           is this copy set up to publish
    python -m coordination.publisher --setup          JSON on stdin: {site, email}
    python -m coordination.publisher --home-zip       the Home folder to upload
    python -m coordination.publisher --check-home     does the Home answer, and know him
    python -m coordination.publisher --collect        take the waiting reports home
    python -m coordination.publisher --inbox          every report
    python -m coordination.publisher --read ID        one report in full
    python -m coordination.publisher --answer         JSON on stdin: {id, status, reply, fixed_in}
    python -m coordination.publisher --remove ID
    python -m coordination.publisher --online         how many use your online office (JSON on stdin {url, key} saves it first)
    python -m coordination.publisher --publish [--installer] [--notes TEXT] [--job FILE]
    python -m coordination.publisher --github         JSON on stdin: {repo, token} — versions go out on GitHub
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
import urllib.error
import urllib.request
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
LICENCE_SOURCE = HOME_SOURCE.with_name("licences.php")
HOME_NEEDED = 3                       # the Home that keeps nothing readable and counts nobody
STATUS_WORDS = support.STATUS_WORDS
NOT_SET_UP = {"ok": False, "error": "This copy is not set up as the publisher yet."}


# ---------------------------------------------------------------------------
# his settings and keys
# ---------------------------------------------------------------------------

def _pub() -> dict:
    return product.publisher()


def _save_pub(d: dict) -> None:
    f = product.publisher_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(d, indent=1), encoding="utf-8")
    try:
        os.chmod(f, 0o600)            # his Home key and GitHub token: his user only
    except OSError:
        pass


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


def _private_file(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def publisher_view() -> dict:
    p, st = _pub(), _load_quiet()
    public = p.get("public") or {}
    return {"ok": True, "publisher": bool(p.get("home_key")), "can_publish": True,
            "ready": bool(p.get("home_key") and _postbox_private() and public.get("postbox_key")),
            "home_url": public.get("home_url") or "", "email": p.get("email") or "",
            "signing_key": bool(p.get("signing_key") and Path(p["signing_key"]).exists()),
            "postbox_key": bool(_postbox_private()), "version": product.version(),
            "home_ok_at": st.get("home_ok_at") or 0, "last_collect": st.get("last_collect") or 0,
            "latest": st.get("latest") or "",
            # GitHub: where versions go out; the token is written, never read back.
            "github_repo": _github(p)["repo"], "github": _github(p)["on"],
            "releases_url": ("https://github.com/" + _github(p)["repo"] + "/releases") if _github(p)["repo"] else ""}


def managed_hosts() -> set:
    """
    The websites this program looks after for a business. "why should i gave my
    website link to others while am managing the website of my client ... the
    program is mine not the website" — the program's own Home never goes on one.
    Their addresses are sealed, so these are the ones the signed-in account opens.
    """
    urls = [s.get("url") for a in workspace.accounts() for s in workspace.sites(a)]
    try:
        from . import site as site_mod
        urls.append(site_mod.from_env_file())
    except Exception:
        pass
    return {h for h in map(workspace._host, urls) if h}


def setup(site, email="") -> dict:
    """Make this copy the publisher's: a Home address and three keys, all kept here."""
    site = str(site or "").strip().rstrip("/")
    if site and not site.lower().startswith(("http://", "https://")):
        site = "https://" + site
    if not site.lower().startswith(("https://", "http://127.0.0.1", "http://localhost")):
        return {"ok": False, "error": "Give your program's own website, starting https://"}
    host = workspace._host(site)
    if host in managed_hosts():
        return {"ok": False, "client_site": True,
                "error": (f"{host} is a website this program looks after for a business, so the program's "
                          "own Home does not go there. Use a website that belongs to the program — for "
                          "example a domain you buy for it.")}
    p = _pub()
    p["home_key"] = p.get("home_key") or secrets.token_urlsafe(36)
    p["email"] = str(email or "").strip()[:200]
    keyfile = _signing_key_path()
    if not keyfile.exists():
        made = new_key(str(keyfile))
        if not made.get("ok"):
            return made
    if not _postbox_private():
        _private_file(_postbox_key_path(), (sealed.keypair()[0] + "\n").encode("utf-8"))
    home = site + "/glasswork-home"
    p["signing_key"] = str(keyfile)
    gh = _github(p)
    p["public"] = {"home_url": home, "update_url": gh_manifest_url(gh["repo"]) if gh["on"] else home + "/index.php?do=latest",
                   "update_key": _public_key(keyfile),
                   "postbox_key": sealed.public_of(_postbox_private()),
                   "support_email": p["email"] or (p.get("public") or {}).get("support_email", "")}
    _save_pub(p)
    return {"ok": True, "home_url": home, "keys_folder": str(keyfile.parent),
            "head": ("Your Botix Home is ready to upload. Download it, unzip it, and put the "
                     "glasswork-home folder in your website's main folder. Then press Check.")}


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

    def php(v):
        return "'" + str(v).replace("\\", "\\\\").replace("'", "\\'") + "'"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("glasswork-home/index.php", HOME_SOURCE.read_text(encoding="utf-8"))
        z.writestr("glasswork-home/licences.php", LICENCE_SOURCE.read_text(encoding="utf-8"))
        # A fingerprint of the key, not the key: someone reading the hosting's
        # files still cannot collect, answer or put a version out.
        z.writestr("glasswork-home/home-key.php",
                   "<?php\nreturn 'sha256:" + hashlib.sha256(p["home_key"].encode("utf-8")).hexdigest() + "';\n")
        z.writestr("glasswork-home/home-config.php", f"<?php\nreturn ['owner_email' => {php(p.get('email') or '')}, "
                                                     f"'product' => {php(product.name())}];\n")
    return {"ok": True, "zip": buf.getvalue(), "filename": "glasswork-home.zip"}


def _ask(do, method="GET", body=None, timeout=support.TIMEOUT) -> dict:
    return support._ask(do, method, body, key=_key(), base=(_pub().get("public") or {}).get("home_url"),
                        timeout=timeout)


# ---------------------------------------------------------------------------
# his records, locked on this computer
# ---------------------------------------------------------------------------

def records_file() -> Path:
    return workspace.data_root() / "publisher" / "records.locked"


def _load() -> dict:
    f = records_file()
    if not f.exists():
        return {"reports": {}}
    priv = _postbox_private()
    if not priv:
        raise ValueError("the postbox key is missing from this computer, so your records cannot be opened")
    d = sealed.unlock(sealed.store_key(priv), f.read_bytes())
    d.pop("copies", None)                 # an older version kept a list of copies: gone at the next save
    d.setdefault("reports", {})
    for rep in d["reports"].values():
        rep.pop("copy", None)
    return d


def _load_quiet() -> dict:
    try:
        return _load()
    except Exception:
        return {"reports": {}}


def _save(d: dict) -> None:
    f = records_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + f".{secrets.token_hex(3)}.tmp")
    _private_file(tmp, sealed.lock(sealed.store_key(_postbox_private()), d))
    os.replace(tmp, f)


def _cut(v, n) -> str:
    return str(v or "").strip()[:n]


def _file(st: dict, e: dict, at: int) -> bool:
    """One opened report into his records. False when it is not a report, or already here."""
    rid = re.sub(r"[^a-z0-9-]", "", str(e.get("id") or "").lower())[:40]
    if e.get("kind") != "report" or not rid or rid in st["reports"]:
        return False
    box = str(e.get("box") or "").lower()
    st["reports"][rid] = {
        "id": rid, "at": at, "title": _cut(e.get("title"), 200) or "A problem", "text": _cut(e.get("text"), 20000),
        "contact": _cut(e.get("contact"), 200), "version": _cut(e.get("version"), 40),
        "system": _cut(e.get("system"), 160), "product": _cut(e.get("product"), 60),
        "support": _cut(e.get("support"), 300000), "status": "new", "reply": "", "fixed_in": "", "updated": at,
        "box": box if re.fullmatch(r"[a-f0-9]{32}", box) else "", "box_pub": _cut(e.get("box_pub"), 64)}
    return True


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
                "error": "Your Home on the website is an older kind. Download it again and upload it over the old one."}
    if not _postbox_private():
        return {"ok": False, "error": "Your Home answers, but this computer has no postbox key yet — press Make it again."}
    try:
        st = _load()
    except ValueError as e:
        return {"ok": False, "error": "Your Home answers, but " + str(e) + "."}
    st["home_ok_at"], st["latest"] = int(time.time()), r.get("latest") or ""
    _save(st)
    return {"ok": True, "latest": st["latest"], "waiting": int(r.get("waiting") or 0),
            "head": "Your Home answers and knows you." + (f" Version {st['latest']} is out." if st["latest"] else "")}


def collect() -> dict:
    """Take every waiting report home, open it here, file it, and only then have the Home delete it."""
    home = check_home()
    if not home.get("ok"):
        return home
    st, priv = _load(), _postbox_private()
    new = unreadable = 0
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
                unreadable += 1          # not sealed to him, or changed on the way: thrown away
                continue
            new += isinstance(e, dict) and _file(st, e, int(item.get("at") or time.time()))
        _save(st)                        # kept here BEFORE the Home is told to delete
        if ids:
            done = _ask("ack", "POST", {"ids": [i for i in ids if i]})
            if not done.get("ok"):
                return {"ok": False, "error": "The Home did not let go of what was collected: "
                                              + str(done.get("error") or "no answer")}
        if not r.get("more"):
            break
    sent = sum(_letter(rep, timeout=15) for rep in st["reports"].values() if rep.get("unsent"))
    st["last_collect"] = int(time.time())
    _save(st)
    return {"ok": True, "new_reports": new, "unreadable": unreadable, "answers_sent": sent,
            "head": f"Collected {new} new report{'s' if new != 1 else ''}." if new else "Nothing new waiting."}


# ---------------------------------------------------------------------------
# answers, sealed to the mailbox the report came with
# ---------------------------------------------------------------------------

def _letter(rep: dict, timeout=support.TIMEOUT) -> bool:
    """Post the report's state to its mailbox. Marks it unsent when the Home does not take it."""
    if not (rep.get("box") and rep.get("box_pub")):
        return False
    # Numbered, so that "read" and "fixed" sent in the same second arrive in the right order.
    rep["seq"] = int(rep.get("seq") or 0) + 1
    try:
        env = sealed.seal(rep["box_pub"], {"kind": "answer", "report": rep["id"], "status": rep.get("status"),
                                           "reply": rep.get("reply") or "", "fixed_in": rep.get("fixed_in") or "",
                                           "seq": rep["seq"], "at": int(time.time())})
        sent = bool(_ask("reply", "POST", {"box": rep["box"], "sealed": env}, timeout=timeout).get("ok"))
    except Exception:
        sent = False
    rep["unsent"] = not sent
    if sent:
        rep.pop("unsent")
    return sent


def _row(rep: dict) -> dict:
    return {"id": rep["id"], "at": rep["at"], "title": rep["title"], "text": rep["text"][:240],
            "contact": rep.get("contact") or "", "version": rep.get("version") or "", "status": rep["status"],
            "status_words": STATUS_WORDS.get(rep["status"], rep["status"]), "fixed_in": rep.get("fixed_in") or "",
            "replied": bool(rep.get("reply")), "has_support": bool(rep.get("support")),
            "can_answer": bool(rep.get("box") and rep.get("box_pub")), "unsent": bool(rep.get("unsent"))}


def _report(rid):
    """(records, the report) — or (None, an error) when there is no such report or no publisher here."""
    if not is_publisher():
        return None, NOT_SET_UP
    st = _load()
    rep = st["reports"].get(str(rid))
    return (st, rep) if rep else (None, {"ok": False, "error": "No such report."})


def inbox() -> dict:
    if not is_publisher():
        return NOT_SET_UP
    rows = sorted((_row(r) for r in _load()["reports"].values()), key=lambda x: x["at"], reverse=True)
    return {"ok": True, "reports": rows[:2000], "new": sum(1 for r in rows if r["status"] == "new")}


def read(rid) -> dict:
    st, rep = _report(rid)
    if st is None:
        return rep
    if rep["status"] == "new":
        # They see "Read by the developer". If the Home does not answer now, it goes with the next collect.
        rep["status"], rep["updated"] = "seen", int(time.time())
        _letter(rep, timeout=8)
        _save(st)
    out = {k: v for k, v in rep.items() if k not in ("box", "box_pub")}
    out["status_words"] = STATUS_WORDS.get(rep["status"], rep["status"])
    return {"ok": True, "report": out}


def answer(rid, status=None, reply=None, fixed_in=None) -> dict:
    st, rep = _report(rid)
    if st is None:
        return rep
    if status and status not in STATUS_WORDS:
        return {"ok": False, "error": "not a status"}
    rep["status"] = status or rep["status"]
    if reply is not None:
        rep["reply"] = _cut(reply, 5000)
    if fixed_in is not None:
        rep["fixed_in"] = _cut(fixed_in, 40)
    rep["updated"] = int(time.time())
    sent = _letter(rep)
    _save(st)
    out = {"ok": True, "id": rep["id"], "status": rep["status"], "sent": sent}
    if not rep.get("box"):
        out["note"] = "Saved. This report came from an older copy, which cannot receive answers."
    elif not sent:
        out["note"] = "Saved here. Your Home did not answer, so it goes to them the next time you collect."
    return out


def remove(rid) -> dict:
    st, rep = _report(rid)
    if st is None:
        return rep if rep is NOT_SET_UP else {"ok": True, "removed": False}
    st["reports"].pop(rep["id"])
    _save(st)
    return {"ok": True, "removed": True}


# ---------------------------------------------------------------------------
# your online office: how many people use it, and nothing else
# ---------------------------------------------------------------------------

def online(url=None, key=None) -> dict:
    """
    "just how many users using the program" — asked of his hosted office with
    its owner key (GLASSWORK_OWNER_KEY on the server). It answers with counts
    alone: accounts, people today, people right now. Given a url and key, they
    are kept first; a key is made for him when he has none.
    """
    p = _pub()
    o = dict(p.get("online") or {})
    if url is not None:
        url = str(url).strip().rstrip("/")
        if url and not url.lower().startswith(("http://", "https://")):
            url = "https://" + url
        o["url"] = url
        o["key"] = str(key or "").strip() or o.get("key") or secrets.token_urlsafe(32)
        p["online"] = o
        _save_pub(p)
    public = p.get("public") or {}
    out = {"ok": True, "url": o.get("url") or "", "key": o.get("key") or "",
           # what the server needs so people online can send him reports, as for a copy he gives out
           "server_settings": {k: v for k, v in (("GLASSWORK_OWNER_KEY", o.get("key")),
                                                 ("GLASSWORK_HOME", public.get("home_url")),
                                                 ("GLASSWORK_POSTBOX_KEY", public.get("postbox_key"))) if v}}
    if not o.get("url"):
        return out
    r = support._ask("", base=o["url"], path="/api/owner/stats", key=o.get("key", ""), timeout=15)
    if r.get("status") == 404:
        return {**out, "ok": False, "error": "Your online office has no owner key yet: add the settings below to it and restart it."}
    if not r.get("ok"):
        return {**out, "ok": False, "error": "Your online office did not answer: " + str(r.get("error") or "no answer")}
    return {**out, "stats": {k: r.get(k) for k in ("accounts", "using_now", "today", "average_a_day_7", "days")}}


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
    _private_file(p, k.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                     serialization.NoEncryption()))
    return {"ok": True, "key_file": str(p), "update_key": _public_key(p),
            "next": "Keep the key file secret and OUT of the program folder."}


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
    at = (base_url.rstrip("/") + "/") if base_url else ""
    with zipfile.ZipFile(out / f"{top}.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            if f == "config/product.json":
                z.writestr(f"{top}/{f}", release_product_json())
            else:
                z.write(ROOT / f, f"{top}/{f}")
    m = {"version": ver, "zip": at + f"{top}.zip", "sha256": hashlib.sha256((out / f"{top}.zip").read_bytes()).hexdigest(),
         "notes": notes, "published": time.strftime("%Y-%m-%d")}
    m["signature"] = sign(m, key_file)
    if installer:
        iname = f"{top}-setup.exe"
        if Path(installer).resolve() != (out / iname).resolve():
            shutil.copy2(installer, out / iname)
        idig = hashlib.sha256((out / iname).read_bytes()).hexdigest()
        m.update(installer=at + iname, installer_sha256=idig,
                 installer_signature=sign({"version": ver, "sha256": idig}, key_file))
    (out / "manifest.json").write_text(json.dumps(m, indent=1), encoding="utf-8")
    return {"ok": True, "zip": str(out / f"{top}.zip"), "manifest": str(out / "manifest.json"), "version": ver,
            "files": len(files), "installer": str(out / f"{top}-setup.exe") if installer else ""}


def upload(path, name=None, chunk=4 * 1024 * 1024, progress=None) -> dict:
    """Send one file to the Home's updates/ folder, in pieces, checked whole at the end."""
    data = Path(path).read_bytes()
    name = name or Path(path).name
    parts = max(1, -(-len(data) // chunk))
    digest = hashlib.sha256(data).hexdigest()
    for i in range(parts):
        body = {"name": name, "part": i, "parts": parts, "data": base64.b64encode(data[i * chunk:(i + 1) * chunk]).decode()}
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
# Botix Home in pieces, and only then put the signed manifest out.

def publish(notes="", installer=False, say=None) -> dict:
    say = say or (lambda stage, **kw: None)
    pub = _pub()
    gh = _github(pub)
    if not (is_publisher() or gh["on"]):
        return {"ok": False, "error": "This copy is not set up to publish. Settings → Publisher: GitHub, or Reports → Make your Home."}
    if not (pub.get("signing_key") and Path(pub["signing_key"]).exists()):
        return {"ok": False, "error": "The signing key is missing, so nothing can be signed. Set GitHub or the Home up again."}
    if is_publisher() and not (pub.get("public") or {}).get("postbox_key"):
        return {"ok": False, "error": "Make your Home again first (Reports → Make it), so copies can seal what they send you."}
    if not updates.is_git_copy():
        return {"ok": False, "error": "A version is put out from the publisher's own git copy of the program."}
    ver = product.version()
    home = {}
    if is_publisher():
        say("Asking your Home which version is out")
        home = check_home()
        if not home.get("ok"):
            return home
        plans = licence_key_from_home()
        if not plans.get("ok"):
            return plans
        if not gh["on"] and home.get("latest") and product.parse_version(home["latest"]) >= product.parse_version(ver):
            return {"ok": False, "error": (f"Version {home['latest']} is already out, and this copy is {ver}. "
                                           "A new version needs a higher number first.")}
    if gh["on"]:
        say("Asking GitHub which version is out")
        out = gh_latest(gh)
        if not out.get("ok"):
            return out
        if out.get("version") and product.parse_version(out["version"]) >= product.parse_version(ver):
            return {"ok": False, "error": (f"Version {out['version']} is already out on GitHub, and this copy is {ver}. "
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
        rel = make_release(tmp / "out", pub["signing_key"], notes=notes, installer=exe)
        if not rel.get("ok"):
            return rel
        manifest = json.loads(Path(rel["manifest"]).read_text(encoding="utf-8"))
        page, where = "", []
        if gh["on"]:
            g = gh_release(gh, rel, manifest, notes, say)
            if not g.get("ok"):
                return g
            page = g.get("url") or ""
            where.append("GitHub")
        if is_publisher():
            for f in [rel["zip"]] + ([rel["installer"]] if exe else []):
                say(f"Sending {Path(f).name} to your Home")
                up = upload(f, progress=lambda n, i, k: say(f"Sending {n}", part=i, of=k))
                if not up.get("ok"):
                    return {**up, "error": ("It is out on GitHub, but the Home did not take it: " if gh["on"] else "")
                            + str(up.get("error"))}
            say("Putting the new version out on your Home")
            r = _ask("release", "POST", {"manifest": manifest})
            if not r.get("ok"):
                return {"ok": False, "error": ("It is out on GitHub, but the Home would not put it out: " if gh["on"]
                                               else "The Home would not put it out: ") + str(r.get("error") or "no answer")}
            page = page or r.get("download_page") or ""
            where.append("your Home")
        return {"ok": True, "version": ver, "installer": bool(exe), "download_page": page,
                "why": (f"Version {ver} is out on {' and '.join(where)}. Everyone using the program is told the next "
                        "time it opens, and presses Update now"
                        + ("; GitHub adds the Windows installer to the release in a few minutes." if gh["on"] and not exe
                           else ".") )}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _build_installer(tmp: Path) -> dict:
    if os.name != "nt" and not os.environ.get("GLASSWORK_CROSS_BUILD"):
        return {"ok": False, "error": "The Windows installer is built on Windows. Publish from your Windows computer, "
                                      "or untick the installer to put out the update alone."}
    p = subprocess.run([sys.executable, str(ROOT / "installer" / "build_windows.py"), "--out", str(tmp),
                        "--product-json", "-"], input=release_product_json(), cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", timeout=3600)
    try:
        return json.loads(((p.stdout or "").strip().splitlines() or [""])[-1])
    except Exception:
        return {"ok": False, "error": "the installer build said: " + ((p.stderr or p.stdout or "nothing")[-400:])}



# ---------------------------------------------------------------------------
# GitHub: where versions go out, and where every copy looks for them
# ---------------------------------------------------------------------------
#
# "make the program as if the user is using the program directly from github
#  and when i will update here the user must be able to get the update
#  notification on the program and user will press update button"
#
# A version is a GitHub Release on the program's own repository: the signed
# update zip, its manifest, and (added by GitHub's own Windows machine, see
# .github/workflows/windows-installer.yml) the installer for new people. Every
# copy asks github.com/<repo>/releases/latest/download/manifest.json, the same
# question it asks a Home, and installs only what HIS key signed.
#
# THE SIGNING KEY NEVER LEAVES THIS COMPUTER. The zip is signed here and only
# then sent; GitHub holds nothing that can sign. The token he gives (a
# fine-grained token, this one repository, Contents: read and write) lets this
# copy make releases and nothing else; it is kept in his publisher file and
# never sent back to a browser.

GH_API = "https://api.github.com"
GH_UPLOADS = "https://uploads.github.com"
_REPO = re.compile(r"^[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}$")


def gh_manifest_url(repo: str) -> str:
    return f"https://github.com/{repo}/releases/latest/download/manifest.json"


def _github(p=None) -> dict:
    p = _pub() if p is None else p
    repo = str(p.get("github_repo") or product.info().get("github") or "").strip()
    repo = repo if _REPO.match(repo) else ""
    token = str(p.get("github_token") or "")
    return {"repo": repo, "token": token, "on": bool(repo and token)}


def _gh_bases():
    """The API and uploads addresses: GitHub's own, or a stand-in on this computer for the tests."""
    test = os.environ.get("GLASSWORK_GITHUB_API", "").rstrip("/")
    if test and test.startswith(("http://127.0.0.1", "http://localhost")):
        return test, test + "/uploads"
    return GH_API, GH_UPLOADS


def _gh(gh, method, path, body=None, data=None, ctype="application/json", uploads=False, timeout=60):
    api, up = _gh_bases()
    url = (up if uploads else api) + path
    payload = data if data is not None else (json.dumps(body).encode("utf-8") if body is not None else None)
    req = urllib.request.Request(url, data=payload, method=method, headers={
        "Authorization": "Bearer " + gh["token"], "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28", "User-Agent": f"{product.name()}-publisher"})
    if payload is not None:
        req.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(8 * 1024 * 1024)
            return r.status, (json.loads(raw.decode("utf-8")) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        try:
            j = json.loads(e.read(65536).decode("utf-8"))
        except Exception:
            j = {}
        return e.code, (j if isinstance(j, dict) else {})
    except Exception as e:
        return 0, {"message": f"GitHub could not be reached ({type(e).__name__})"}


def _gh_error(code, j, what) -> dict:
    if code in (401, 403):
        why = ("GitHub refused the token. Make a fine-grained token for this one repository with "
               "Contents: read and write, and save it again.")
    elif code == 404:
        why = "GitHub does not know that repository, or the token cannot see it."
    else:
        why = str(j.get("message") or f"GitHub answered {code}")[:300]
    return {"ok": False, "error": f"{what}: {why}", "status": code}


def github_setup(repo, token) -> dict:
    """Versions go out on GitHub. Checks the token can write to the repository before keeping it."""
    repo = str(repo or "").strip().removeprefix("https://github.com/").strip("/")
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not _REPO.match(repo):
        return {"ok": False, "error": "Give the repository as owner/name, for example techduo/botix."}
    p = _pub()
    token = str(token or "").strip() or str(p.get("github_token") or "")
    if not token:
        return {"ok": False, "error": "Paste the GitHub token too."}
    code, j = _gh({"token": token}, "GET", f"/repos/{repo}")
    if code != 200:
        return _gh_error(code, j, "Not saved")
    if not (j.get("permissions") or {}).get("push", True):
        return {"ok": False, "error": "Not saved: that token can read the repository but not make releases on it."}
    if j.get("private"):
        return {"ok": False, "error": ("Not saved: that repository is private, so the people using the program "
                                       "could not download its updates. Use a public one — it can be a second "
                                       "repository that holds only the releases, with your code kept private.")}
    keyfile = _signing_key_path()
    if not keyfile.exists():
        made = new_key(str(keyfile))
        if not made.get("ok"):
            return made
    p["github_repo"], p["github_token"], p["signing_key"] = repo, token, str(keyfile)
    public = p.setdefault("public", {})
    public["update_url"] = gh_manifest_url(repo)
    public["update_key"] = _public_key(keyfile)
    _save_pub(p)
    return {"ok": True, "repo": repo, "releases_url": f"https://github.com/{repo}/releases",
            "head": (f"Versions now go out on github.com/{repo}. Every copy installed from the next one looks "
                     "there for updates.")}


def gh_latest(gh) -> dict:
    code, j = _gh(gh, "GET", f"/repos/{gh['repo']}/releases/latest")
    if code == 404:
        return {"ok": True, "version": ""}
    if code != 200:
        return _gh_error(code, j, "GitHub did not say which version is out")
    return {"ok": True, "version": str(j.get("tag_name") or "").lstrip("vV"), "url": j.get("html_url") or ""}


def _head_commit() -> str:
    try:
        r = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", timeout=30)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def release_body(name, ver, notes) -> str:
    """What a person sees on the release page: which file is theirs, and that they need nothing else."""
    lines = [notes.strip(), ""] if notes and notes.strip() else []
    lines += [f"**New to {name}?** Download `{name.lower()}-{ver}-setup.exe` below and run it. "
              "That one file installs everything the program needs.",
              "",
              f"**Already using {name}?** You do not need anything from this page: the program shows "
              "\"Update now\" by itself.",
              "",
              f"The other files are for the program's own updater (`{name.lower()}-{ver}.zip` and "
              "`manifest.json`) and GitHub's copy of the source code. You can ignore them."]
    return "\n".join(lines)


def gh_release(gh, rel, manifest, notes, say) -> dict:
    """A draft release, its files, then published: 'latest' never points at a half-made one."""
    ver = str(manifest.get("version"))
    tag = "v" + ver
    repo = gh["repo"]
    code, j = _gh(gh, "GET", f"/repos/{repo}/releases/tags/{tag}")
    if code == 200:
        if not j.get("draft"):
            return {"ok": False, "error": f"Version {ver} is already out on GitHub. A new version needs a higher number first."}
        _gh(gh, "DELETE", f"/repos/{repo}/releases/{j.get('id')}")      # a draft left by a try that stopped
    say("Making the release on GitHub")
    body = {"tag_name": tag, "name": f"{product.name()} {ver}", "draft": True,
            "body": release_body(product.name(), ver, notes)}
    # On the program's own repository the release is this exact commit. On a
    # separate, public repository kept only for releases (his code private),
    # that commit does not exist: GitHub tags its default branch instead.
    head = _head_commit()
    if head and repo == str(product.info().get("github") or ""):
        body["target_commitish"] = head
    code, made = _gh(gh, "POST", f"/repos/{repo}/releases", body)
    if code not in (200, 201):
        return _gh_error(code, made, "GitHub would not make the release")
    rid = made.get("id")
    base = f"https://github.com/{repo}/releases/download/{tag}/"
    online = dict(manifest)
    online["zip"] = base + Path(rel["zip"]).name
    if rel.get("installer"):
        online["installer"] = base + Path(rel["installer"]).name
    files = [(Path(rel["zip"]).name, Path(rel["zip"]).read_bytes(), "application/zip")]
    if rel.get("installer"):
        files.append((Path(rel["installer"]).name, Path(rel["installer"]).read_bytes(), "application/octet-stream"))
    files.append(("manifest.json", json.dumps(online, indent=1).encode("utf-8"), "application/json"))
    for name, data, ctype in files:
        say(f"Sending {name} to GitHub")
        code, j = _gh(gh, "POST", f"/repos/{repo}/releases/{rid}/assets?name={name}", data=data, ctype=ctype,
                      uploads=True, timeout=600)
        if code not in (200, 201):
            _gh(gh, "DELETE", f"/repos/{repo}/releases/{rid}")
            return _gh_error(code, j, f"GitHub did not take {name}")
    say("Putting the new version out on GitHub")
    code, j = _gh(gh, "PATCH", f"/repos/{repo}/releases/{rid}", {"draft": False, "make_latest": "true"})
    if code != 200:
        return _gh_error(code, j, "GitHub would not publish the release")
    return {"ok": True, "url": j.get("html_url") or f"https://github.com/{repo}/releases/tag/{tag}"}


# ---------------------------------------------------------------------------
# plans: managed in his backend, only carried by a release
# ---------------------------------------------------------------------------
#
# Customers, plans, keys and payments are admin work: his dashboard does it,
# on his backend (the Home's licences.php — docs/BACKEND.md), never this
# program. A version he puts out only needs the PUBLIC half of the key his
# backend signs plans with, so every copy can check its plan. It is picked up
# here when he publishes: plans switched on in the backend means the next
# version asks for a key; plans off means it does not.

def licence_key_from_home() -> dict:
    """The plan key his backend holds now, kept with his public keys. Never changed silently."""
    r = _ask("licence-public")
    if not r.get("ok"):
        return {"ok": False, "error": "Your Home did not say whether plans are on: " + str(r.get("error") or "no answer")}
    p = _pub()
    public = p.setdefault("public", {})
    have, now = public.get("licence_key") or "", (r.get("public_key") or "") if r.get("ready") else ""
    if have and now != have:
        return {"ok": False, "changed": True,
                "error": ("Your backend's plan key is not the one your last version carried, so customers' copies "
                          "would stop accepting their plans. If you replaced it on purpose, delete the line "
                          "\"licence_key\" from config/publisher.local.json and put the version out again.")}
    if now and not have:
        public["licence_key"] = now
        _save_pub(p)
    return {"ok": True, "plans": bool(now)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="The publisher's side: reports, answers, versions.")
    for f in ("view", "setup", "home-zip", "check-home", "collect", "inbox", "answer", "publish", "installer", "online",
              "github"):
        ap.add_argument("--" + f, action="store_true")
    for f in ("read", "remove", "notes", "job", "new-key", "make-release", "key", "base-url"):
        ap.add_argument("--" + f, default=None)
    a = ap.parse_args(argv)
    body = {}
    if a.setup or a.answer or a.online or a.github:
        try:
            body = json.loads(sys.stdin.read() or "{}")
        except Exception:
            pass
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
        elif a.github:
            r = github_setup(body.get("repo"), body.get("token"))
        elif a.online:
            r = online(body["url"], body.get("key")) if "url" in body else online()
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
