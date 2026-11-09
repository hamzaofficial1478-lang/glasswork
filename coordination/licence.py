"""
licence.py — the plan this copy was bought with, and whether a job may run.

    python -m coordination.licence --status --json     the plan, as the screens show it
    python -m coordination.licence --activate          a key; JSON on stdin: {key, move, name}
    python -m coordination.licence --check [--beat]    ask the Home how the plan stands now
    python -m coordination.licence --release           give this computer's place back
    python -m coordination.licence --working           may jobs run now ({ok, error})

A PLAN IS A PLAN, WHOEVER'S AI DOES THE WORK

"even user will use his own llm or will use our llm the user will be limitated
with the plan he selected". The limits are on the work — leads found, emails
sent and posts written each day for each website, the words in a post, the
websites on the account, Website Check reports a month — never on the AI. A
person on their own key is held to the same plan as one on Botix AI.

WHO KNOWS WHAT

The plan lives in Botix Home, on the publisher's own website (licences.php):
which plan, bought when, changed when, paid until, cancelled or not — told by
Stripe the moment it happens, or set by the publisher by hand. This copy asks
it, and keeps the answer signed by the Home's licence key, so the answer
cannot be edited here and a copy can carry on for a few days without the
internet. Every job asks before it runs (take()); the Home counts, so the
count is the same on any computer the key moves to.

ONE PERSON, ONE KEY

The key is entered once on each computer. A computer is a random number made
when the program is installed — not an IP address, which changes every day and
is shared by everyone in an office. A key works on as many computers as its
plan allows (one), moves to a new one when entered there (a few times a
month), and runs on one at a time.

The publisher's own copy — the one holding the key that signs the program's
updates — has no plan to keep to.
"""
import argparse
import base64
import datetime
import hashlib
import json
import os
import platform
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

try:
    from . import product, workspace
except ImportError:  # pragma: no cover - run as a script
    import product
    import workspace

STATE = "licence.local.json"          # the account's own, sealed with its key (workspace.ACCOUNT_CONFIG)
CHECK_EVERY = 6 * 3600                 # how often a job's answer is refreshed from the Home
BEAT_EVERY = 240                       # an open office says it is still the one running
TIMEOUT = 20
_DOWN_UNTIL = 0                        # after a failed request, this process works offline for a while
ACTIVE = ("trial", "active", "past_due")
KINDS = {"leads": ("new lead", "new leads"), "mails": ("email", "emails"),
         "posts": ("post", "posts"), "checks": ("Website Check report", "Website Check reports")}
FEATURES = {"own_logo": "Reports with your own logo"}


class NotAllowed(Exception):
    """A job the plan does not allow right now. str() is the sentence to show."""


# ---------------------------------------------------------------------------
# is there a plan to keep to?
# ---------------------------------------------------------------------------

def public_key() -> str:
    return (os.environ.get("GLASSWORK_LICENCE_KEY") or product.info().get("licence_key") or "").strip()


def home() -> str:
    url = os.environ.get("GLASSWORK_LICENCE_HOME") or os.environ.get("GLASSWORK_HOME") or product.info().get("home_url")
    return str(url or "").strip().rstrip("/")


def owner_copy() -> bool:
    """
    The publisher's own copy: it holds the private half of the key the
    program's updates are signed with. A copy cannot fake that — the public
    half is in every release — so writing a settings file does not make
    someone the owner. Never on a hosted office: there everyone is a customer.
    """
    if workspace.HOSTED:
        return False
    pub = product.publisher()
    want = product.info().get("update_key") or ""
    path = pub.get("signing_key") or ""
    if not (pub.get("home_key") and want and path):
        return False
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        k = Ed25519PrivateKey.from_private_bytes(Path(path).read_bytes())
        raw = k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        return base64.b64encode(raw).decode() == want
    except Exception:
        return False


def enabled() -> bool:
    """True when this copy was given out with plans (a licence key and a Home) and is not the owner's."""
    return bool(public_key() and home()) and not owner_copy()


# ---------------------------------------------------------------------------
# this computer, this account
# ---------------------------------------------------------------------------

def _install_id() -> str:
    f = workspace.data_root() / "install-id"
    try:
        v = f.read_text(encoding="utf-8").strip()
        if re.fullmatch(r"[a-f0-9]{32}", v):
            return v
    except Exception:
        pass
    v = secrets.token_hex(16)
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(v + "\n", encoding="utf-8")
    except Exception:
        pass
    return v


def device() -> str:
    """This computer, for this account: a random number, nothing about the person or the machine."""
    acct = (workspace.active() or {}).get("account") or os.environ.get("AGENCY_ACCOUNT") or "main"
    return hashlib.sha256(f"{_install_id()}:{acct}".encode("utf-8")).hexdigest()[:32]


def device_name() -> str:
    if workspace.HOSTED:
        return "Botix online"
    kind = {"Windows": "Windows", "Darwin": "Mac", "Linux": "Linux"}.get(platform.system(), "")
    return f"{kind} computer" if kind else "A computer"


def _run() -> str:
    """The office that is running now (server.js makes one number each time it starts)."""
    v = (os.environ.get("BOTIX_RUN") or "").strip().lower()
    return v if re.fullmatch(r"[a-f0-9]{8,64}", v) else ""


def today() -> str:
    """This computer's own calendar: a day's limit starts again at its midnight."""
    return datetime.date.today().isoformat()


def _next_midnight() -> int:
    d = datetime.datetime.combine(datetime.date.today() + datetime.timedelta(days=1), datetime.time())
    return int(d.timestamp())


def site_key(site=None) -> str:
    """
    Which website a count belongs to: a fingerprint of its address, so the same
    website is the same website after it is removed and added again, and a
    third website is a third website. The address itself never leaves.
    """
    if site:
        return re.sub(r"[^a-z0-9_-]", "", str(site).lower())[:64] or "main"
    url = ""
    try:
        from . import site as site_mod
        url = site_mod.from_env_file() or ""
    except Exception:
        pass
    host = workspace._host(url)
    if host:
        return hashlib.sha256(("botix-site:" + host).encode("utf-8")).hexdigest()[:16]
    a = workspace.active() or {}
    return re.sub(r"[^a-z0-9_-]", "", str(a.get("site") or "main").lower())[:64] or "main"


# ---------------------------------------------------------------------------
# what this copy keeps: the key, the Home's last signed answer, work done offline
# ---------------------------------------------------------------------------

def _path() -> Path:
    return workspace.config_path(STATE)


def _load() -> dict:
    try:
        d = json.loads(workspace.read_secret(_path(), "{}") or "{}")
    except ValueError:
        d = {}
    return d if isinstance(d, dict) else {}


def _save(d: dict) -> None:
    workspace.write_secret(_path(), json.dumps(d, indent=1))


def _b64u(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def verify(token) -> dict:
    """The plan inside a token, if the Home signed it for this computer; {} otherwise."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        body, sig = str(token or "").split(".")
        payload = _b64u(body)
        Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key())).verify(_b64u(sig), payload)
        d = json.loads(payload.decode("utf-8"))
    except Exception:
        return {}
    if not isinstance(d, dict) or d.get("v") != 1 or d.get("device") != device():
        return {}
    return d


def norm_key(key) -> str:
    k = re.sub(r"[^A-Za-z0-9]", "", str(key or "")).upper()
    k = k[3:] if k.startswith("BTX") else k
    return "BTX-" + "-".join(k[i:i + 5] for i in range(0, len(k), 5)) if k else ""


# ---------------------------------------------------------------------------
# talking to the Home
# ---------------------------------------------------------------------------

def _ask(do, body) -> dict:
    """
    One request to the Home. {'reached': False} when it could not be reached
    (no internet, the website down, a server error) — the copy then works from
    the last signed answer for a few days. An answer, even a refusal, has
    reached True.
    """
    global _DOWN_UNTIL
    base = home()
    if not base.lower().startswith(("https://", "http://127.0.0.1", "http://localhost")):
        return {"reached": False, "error": "the plan is checked over https:// only"}
    if time.time() < _DOWN_UNTIL:
        return {"reached": False, "error": "it did not answer a moment ago"}
    out = _send(base, do, body)
    if not out.get("reached"):
        _DOWN_UNTIL = time.time() + 120          # not a 20-second wait on every lead while offline
    return out


def _send(base, do, body) -> dict:
    req = urllib.request.Request(base + "/index.php?" + urllib.parse.urlencode({"do": do}),
                                 data=json.dumps(body).encode("utf-8"), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "application/json")
    req.add_header("User-Agent", f"{product.name()}/{product.version()}")
    code = 200
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            raw = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        code = e.code
        raw = (e.read() or b"").decode("utf-8", "replace")
        if code >= 500 or code == 408:
            return {"reached": False, "status": code, "error": f"the plan server answered {code}"}
    except Exception as e:
        return {"reached": False, "error": f"could not reach the plan server ({type(e).__name__})"}
    try:
        out = json.loads(raw)
    except Exception:
        return {"reached": False, "status": code, "error": "the plan server gave an odd answer"}
    if not isinstance(out, dict):
        return {"reached": False, "status": code, "error": "the plan server gave an odd answer"}
    out.update(reached=True, status=code)
    return out


def _body(st: dict, **more) -> dict:
    b = {"key": st.get("key") or "", "device": device(), "day": today(), "run": _run()}
    if st.get("run") and st.get("run") != b["run"]:
        b["prev_run"] = st["run"]       # the same copy, started again: not a second one
    b.update(more)
    return b


def _absorb(st: dict, r: dict) -> dict:
    """Keep what the Home said: a new signed plan, a key moved away, a key it does not know."""
    tok = verify(r.get("token"))
    if tok:
        st["token"], st["checked_at"] = r["token"], int(time.time())
        st.pop("moved", None)
        st.pop("unknown", None)
    if _run():
        st["run"] = _run()
    if r.get("moved"):
        st["moved"] = True
    if r.get("unknown_key"):
        st["unknown"] = True
    return tok


# ---------------------------------------------------------------------------
# the plan, now
# ---------------------------------------------------------------------------

def _token(st=None) -> dict:
    return verify((st if st is not None else _load()).get("token"))


def _counted(tok: dict, st: dict, kind: str, scope: str, per: str) -> int:
    """Used so far, as this copy knows it: the Home's count at its last answer, and work done since offline."""
    usage = tok.get("usage") or {}
    if per == "day":
        n = int(((usage.get("today") or {}).get(kind) or {}).get(scope) or 0) if usage.get("day") == today() else 0
    else:
        n = int(((usage.get("period") or {}).get(kind) or {}).get(scope) or 0)
    for p in st.get("pending") or []:
        if p.get("kind") == kind and (scope == "*" or p.get("site") == scope) and (per != "day" or p.get("day") == today()):
            n += int(p.get("n") or 0)
    return n


def _ended(tok: dict) -> bool:
    return bool(tok.get("ends_at")) and time.time() >= int(tok["ends_at"])


def _stop_words(tok: dict, st: dict) -> str:
    """Why nothing can run, in the person's words — '' when the plan is working."""
    if not st.get("key"):
        return "Enter your Botix key to start. It is in the email you got when you paid, and on the page you saw after."
    if st.get("unknown"):
        return "That key is not known any more. Enter the newest key you were given."
    if st.get("moved"):
        return "Your key is now in use on another computer. Enter it again here to move it back."
    if not tok:
        return "Botix has not been able to check your plan yet. Connect to the internet and press Check again."
    if tok.get("status") not in ACTIVE:
        return tok.get("why") or "Your Botix plan is not active. Renew it to carry on."
    if _ended(tok):
        return "Your Botix plan's paid time has ended. Your work is safe — renew your plan to carry on."
    if int(tok.get("valid_until") or 0) < time.time():
        return ("Botix has not been able to reach its plan server for a few days, so it has paused. "
                "Connect to the internet and press Check again.")
    return ""


def check(force=False, within=CHECK_EVERY) -> dict:
    """Ask the Home how the plan stands (when the last answer is older than `within`), and send work done offline."""
    if not enabled():
        return {"ok": True, "off": True}
    st = _load()
    if not st.get("key"):
        return {"ok": False, "need_key": True, "error": _stop_words({}, st)}
    tok = _token(st)
    fresh = tok and time.time() - int(st.get("checked_at") or 0) < within and not _ended(tok)
    reached = True
    if force or not fresh:
        r = _ask("licence-check", _body(st, pending=st.get("pending") or []))
        reached = bool(r.get("reached"))
        if reached:
            if r.get("status") in (200, 402):           # the Home counted the offline work
                st["pending"] = []
            tok = _absorb(st, r) or (_token(st) if not r.get("unknown_key") else {})
            if r.get("busy"):
                _save(st)
                return {"ok": False, "busy": True, "error": r.get("error")}
            _save(st)
        elif not tok:
            return {"ok": False, "offline": True, "error": "Botix could not reach its plan server: " + str(r.get("error"))}
    why = _stop_words(tok, st)
    return {"ok": not why, "error": why, "offline": not reached, "moved": bool(st.get("moved"))}


def working() -> dict:
    """{'ok': True} when jobs may run; else {'ok': False, 'error': the sentence to show}."""
    if not enabled():
        return {"ok": True, "off": True}
    try:
        return check()
    except Exception as e:                       # a locked account, an unreadable file
        return {"ok": False, "error": f"Botix could not read your plan: {e}"}


def need_working() -> None:
    w = working()
    if not w.get("ok"):
        raise NotAllowed(w.get("error") or "Your Botix plan is not active.")


def _why(kind: str, r: dict, tok: dict) -> str:
    one, many = KINDS.get(kind, (kind, kind))
    plan = tok.get("plan_name") or "your"
    limit = r.get("limit")
    why = r.get("why")
    if why == "not_in_plan":
        return f"{many[:1].upper() + many[1:]} are not part of the {plan} plan. Upgrade to get them."
    if why == "websites":
        n = int(tok.get("websites") or 1)
        when = _date(r.get("resets_at"))
        return (f"Your {plan} plan covers {n} website{'s' if n != 1 else ''}, and Botix has worked on "
                f"{n} this month. Upgrade for more" + (f", or carry on with this one from {when}." if when else "."))
    if r.get("per") == "day":
        return (f"Today's {many} are used up: {r.get('used', limit)} of {limit} on the {plan} plan. "
                "More from midnight — or upgrade for more each day.")
    when = _date(r.get("resets_at"))
    return (f"This month's {many} are used up: {r.get('used', limit)} of {limit} on the {plan} plan. "
            + (f"More on {when}, when your plan renews" if when else "More when your plan renews") + " — or upgrade.")


def _date(ts) -> str:
    try:
        ts = int(ts or 0)
        d = datetime.date.fromtimestamp(ts)
    except (TypeError, ValueError, OSError, OverflowError):
        return ""
    return f"{d.day} {d.strftime('%b %Y')}" if ts > 0 else ""


def take(kind, n=1, site=None, dry=False) -> dict:
    """
    May this job run? Counted by the Home, so a plan is a plan on every
    computer. dry=True only looks. {'ok': True, ...} or {'ok': False, 'error': ...}.
    """
    if not enabled():
        return {"ok": True, "off": True}
    w = working()
    if not w.get("ok"):
        return w
    st = _load()
    scope_site = site_key(site)
    if st.get("pending"):
        check(force=True)                       # offline work first, so the count is right
        st = _load()
    r = _ask("licence-use", _body(st, kind=kind, n=int(n), site=scope_site, dry=bool(dry)))
    if r.get("reached"):
        tok = _absorb(st, r) or _token(st)
        _save(st)
        if r.get("status") != 200 or not r.get("ok"):
            return {"ok": False, "error": r.get("error") or _stop_words(tok, st) or "Your plan does not allow that."}
        if not r.get("allowed"):
            return {"ok": False, "used_up": r.get("why") == "used_up", "why": r.get("why"),
                    "used": r.get("used"), "limit": r.get("limit"), "per": r.get("per"),
                    "resets_at": _next_midnight() if r.get("per") == "day" else r.get("resets_at"),
                    "error": _why(kind, r, tok)}
        return {"ok": True, "used": r.get("used"), "limit": r.get("limit"), "per": r.get("per")}
    # The Home could not be reached: the last signed answer, and what has been done since.
    tok = _token(st)
    why = _stop_words(tok, st)
    if why:
        return {"ok": False, "offline": True, "error": why}
    lim = (tok.get("limits") or {}).get(kind)
    if not lim:
        return {"ok": True, "offline": True}
    cap = int(lim.get("n") or 0)
    per = lim.get("per") if lim.get("per") in ("day", "period", "month") else "day"
    scope = "*" if lim.get("scope") == "account" else scope_site
    if cap <= 0:
        return {"ok": False, "why": "not_in_plan", "error": _why(kind, {"why": "not_in_plan"}, tok)}
    used = _counted(tok, st, kind, scope, per)
    if used + int(n) > cap:
        r = {"why": "used_up", "used": used, "limit": cap, "per": per,
             "resets_at": _next_midnight() if per == "day" else tok.get("period_end")}
        return {"ok": False, "offline": True, **r, "error": _why(kind, r, tok)}
    if not dry:
        st.setdefault("pending", []).append({"kind": kind, "n": int(n), "site": scope, "day": today(),
                                             "at": int(time.time())})
        st["pending"] = st["pending"][-500:]
        _save(st)
    return {"ok": True, "offline": True, "used": used + (0 if dry else int(n)), "limit": cap, "per": per}


def need(kind, n=1, site=None, dry=False) -> dict:
    """take(), raising NotAllowed with the sentence to show when the plan says no."""
    r = take(kind, n, site, dry)
    if not r.get("ok"):
        raise NotAllowed(r.get("error") or "Your plan does not allow that.")
    return r


def cap(kind):
    """The plan's number for a kind of work (e.g. emails a day), or None when there is none."""
    if not enabled():
        return None
    lim = (_token().get("limits") or {}).get(kind)
    return int(lim.get("n") or 0) if lim else None


def feature(name) -> bool:
    return True if not enabled() else name in (_token().get("features") or [])


def post_words() -> int:
    """The most words a post may have on this plan; 0 when there is no limit."""
    return 0 if not enabled() else int(_token().get("post_words") or 0)


def websites():
    """How many websites the plan covers, or None when there is no plan."""
    return None if not enabled() else int(_token().get("websites") or 1)


# ---------------------------------------------------------------------------
# a key entered, a computer given back, and what the screens show
# ---------------------------------------------------------------------------

def activate(key, move=False, name="") -> dict:
    if not enabled():
        return {"ok": True, "off": True}
    k = norm_key(key)
    if len(re.sub(r"[^A-Z0-9]", "", k)) != 23:
        return {"ok": False, "error": "A Botix key looks like BTX-XXXXX-XXXXX-XXXXX-XXXXX. Check it and try again."}
    st = _load()
    old = st.get("key")
    st["key"] = k
    r = _ask("licence-activate", _body(st, move=bool(move), name=(name or device_name())[:60]))
    if not r.get("reached"):
        st["key"] = old
        return {"ok": False, "offline": True, "error": "Botix could not reach its plan server: " + str(r.get("error"))}
    if r.get("in_use") or r.get("moves_left") == 0 and not r.get("token"):
        return {"ok": False, "in_use": bool(r.get("in_use")), "computers": r.get("computers") or [],
                "moves_left": r.get("moves_left"), "error": r.get("error")}
    if r.get("unknown_key") or r.get("status") == 429 or r.get("busy") or not verify(r.get("token")):
        return {"ok": False, "busy": bool(r.get("busy")), "error": r.get("error") or "That key did not work."}
    if old and old != k:
        st["pending"] = []                       # another key's offline work is not this key's
    st.pop("moved", None)
    st.pop("unknown", None)
    _absorb(st, r)
    _save(st)
    out = view()
    out["moves_left"] = r.get("moves_left")
    return out


def release() -> dict:
    """This computer gives its place back (before moving to a new one), and forgets the key."""
    if not enabled():
        return {"ok": True, "off": True}
    st = _load()
    if not st.get("key"):
        return {"ok": True}
    if st.get("pending"):
        check(force=True)
        st = _load()
    r = _ask("licence-release", _body(st))
    if not r.get("reached"):
        return {"ok": False, "error": "Botix could not reach its plan server, so this computer still holds its place. Try again online."}
    _save({"run": st.get("run") or ""})
    return {"ok": True, "head": "This computer has given its place back. Enter your key on the new one."}


def view(beat=False) -> dict:
    """Everything the 'Your plan' card shows."""
    if not enabled():
        return {"ok": True, "on": False, "owner": owner_copy()}
    try:
        c = check(within=BEAT_EVERY if beat else CHECK_EVERY)
        st = _load()
    except Exception as e:
        return {"ok": False, "on": True, "working": False, "error": f"Botix could not read your plan: {e}"}
    tok = _token(st)
    key = st.get("key") or ""
    out = {"ok": True, "on": True, "has_key": bool(key), "key_hint": key[-5:] if key else "",
           "working": bool(c.get("ok")), "error": c.get("error") or "", "offline": bool(c.get("offline")),
           "busy": bool(c.get("busy")), "moved": bool(st.get("moved")), "checked_at": int(st.get("checked_at") or 0),
           "pending": sum(int(p.get("n") or 0) for p in st.get("pending") or []), "computer": device_name(),
           "need_key": not key or bool(st.get("unknown") or st.get("moved")),
           "support_email": product.info().get("support_email") or ""}
    if not tok:
        return out
    meters = []
    for kind, lim in (tok.get("limits") or {}).items():
        if not isinstance(lim, dict):
            continue
        per = lim.get("per") if lim.get("per") in ("day", "period", "month") else "day"
        scope = "*" if lim.get("scope") == "account" else site_key()
        one, many = KINDS.get(kind, (kind, kind))
        meters.append({"kind": kind, "label": many[:1].upper() + many[1:], "limit": int(lim.get("n") or 0),
                       "used": _counted(tok, st, kind, scope, per), "per": per,
                       "each": "website" if scope != "*" else "account"})
    out.update({k: tok.get(k) for k in ("plan", "plan_name", "variant", "status", "bought_at", "plan_since",
                                        "period_start", "period_end", "cancel_at", "ends_at", "websites",
                                        "post_words", "devices", "pricing_url", "portal_url", "email",
                                        "valid_until")})
    out["features"] = [{"id": f, "label": FEATURES.get(f, f.replace("_", " "))} for f in tok.get("features") or []]
    out["meters"] = meters
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--activate", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--beat", action="store_true")
    ap.add_argument("--release", action="store_true")
    ap.add_argument("--working", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        if a.activate:
            b = json.loads(sys.stdin.read() or "{}")
            out = activate(b.get("key"), bool(b.get("move")), str(b.get("name") or ""))
        elif a.check:
            c = check(force=not a.beat, within=BEAT_EVERY)
            out = {**view(), "checked": c}
        elif a.release:
            out = release()
        elif a.working:
            out = working()
        else:
            out = view(beat=a.beat)
    except Exception as e:
        out = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
