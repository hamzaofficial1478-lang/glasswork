"""
store.py — the credential/model/routing store.

Persisted to config/providers.local.json (git-ignored, never committed, never
logged). THIS is where the old "one save wipes the last" bug is fixed: every write
loads the current file, changes ONLY the one credential/model/route being touched,
and writes back. Saving one credential never disturbs another.

Keys are kept in the local file (like a .env) and are ALWAYS masked/redacted in
any value that leaves the server (see redacted_view / auth.mask).
"""
import json
import time
import uuid
from pathlib import Path

from . import auth
from .urls import normalize_base

STORE = Path(__file__).parents[1] / "config" / "providers.local.json"

_EMPTY = {"credentials": [], "routing": {}, "usage": {}}


def _load() -> dict:
    if not STORE.exists():
        return json.loads(json.dumps(_EMPTY))
    try:
        data = json.loads(STORE.read_text() or "{}")
    except Exception:
        return json.loads(json.dumps(_EMPTY))
    for k, v in _EMPTY.items():
        data.setdefault(k, json.loads(json.dumps(v)))
    return data


def _save(data: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(STORE)
    try:
        STORE.chmod(0o600)
    except Exception:
        pass


def _new_id(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


# ---- credentials (incremental — the bug fix) -------------------------------

def upsert_credential(cred: dict) -> dict:
    """
    Add or update ONE credential. Loads the file, replaces just this credential
    (matched by id), leaves every other credential untouched, writes back.
    If api_key is blank on an update, the existing key is kept (so re-saving a
    masked credential never erases the key).
    """
    data = _load()
    creds = data["credentials"]
    cid = cred.get("id") or _new_id("cred")
    existing = next((c for c in creds if c["id"] == cid), None)

    key_in = (cred.get("api_key") or "").strip()
    if existing and not key_in:
        key_in = existing.get("api_key", "")  # keep the stored key on a keyless re-save

    record = {
        "id": cid,
        "name": (cred.get("name") or "").strip() or "Unnamed provider",
        "base_url": normalize_base(cred.get("base_url", "")),
        "api_key": key_in,
        "auth_style": (cred.get("auth_style") or "bearer").strip().lower(),
        "auth_header_name": (cred.get("auth_header_name") or "").strip(),
        "credit_note": (cred.get("credit_note") or "").strip(),
        "created_at": existing.get("created_at") if existing else int(time.time()),
        "models": existing.get("models", []) if existing else [],
    }
    if existing:
        creds[creds.index(existing)] = record
    else:
        creds.append(record)
    _save(data)
    return _redact_cred(record)


def delete_credential(cid: str) -> bool:
    data = _load()
    before = len(data["credentials"])
    data["credentials"] = [c for c in data["credentials"] if c["id"] != cid]
    # drop routing that referenced this credential's models
    gone = {m["id"] for c in data["credentials"] for m in c.get("models", [])}
    _save(data)
    return len(data["credentials"]) < before


def get_credential(cid: str):
    return next((c for c in _load()["credentials"] if c["id"] == cid), None)


# ---- models under a credential ---------------------------------------------

def upsert_model(cid: str, model: dict) -> dict:
    """Add or update ONE model under a credential. Model id is sent verbatim."""
    data = _load()
    cred = next((c for c in data["credentials"] if c["id"] == cid), None)
    if not cred:
        raise KeyError(f"no credential {cid}")
    cred.setdefault("models", [])
    mid = model.get("id") or _new_id("mdl")
    existing = next((m for m in cred["models"] if m["id"] == mid), None)
    record = {
        "id": mid,
        "api_model_id": (model.get("api_model_id") or "").strip(),  # verbatim, never mangled
        "display_name": (model.get("display_name") or "").strip(),
        "category": (model.get("category") or "LLM").strip(),
        "cost_tier": (model.get("cost_tier") or "unknown").strip(),
        "priority": int(model.get("priority", 100)),
        "enabled": bool(model.get("enabled", True)),
        "detected_capabilities": (existing or {}).get("detected_capabilities", {}) if existing else {},
        "last_test_result": (existing or {}).get("last_test_result", {}) if existing else {},
    }
    if "detected_capabilities" in model:
        record["detected_capabilities"] = model["detected_capabilities"]
    if "last_test_result" in model:
        record["last_test_result"] = model["last_test_result"]
    if existing:
        cred["models"][cred["models"].index(existing)] = record
    else:
        cred["models"].append(record)
    _save(data)
    return record


def delete_model(cid: str, mid: str) -> bool:
    data = _load()
    cred = next((c for c in data["credentials"] if c["id"] == cid), None)
    if not cred:
        return False
    before = len(cred.get("models", []))
    cred["models"] = [m for m in cred.get("models", []) if m["id"] != mid]
    _save(data)
    return len(cred["models"]) < before


def find_model(mid: str):
    """Return (credential, model) for a global model id, or (None, None)."""
    for c in _load()["credentials"]:
        for m in c.get("models", []):
            if m["id"] == mid:
                return c, m
    return None, None


# ---- routing ----------------------------------------------------------------

def get_routing() -> dict:
    return _load()["routing"]


def set_route(task: str, route: dict) -> dict:
    data = _load()
    data["routing"][task] = {
        "primary": route.get("primary") or "",
        "secondary": route.get("secondary") or "",
        "secondary_mode": route.get("secondary_mode") or "fallback",  # fallback | fusion
        "fallback": list(route.get("fallback") or []),
        "enabled": bool(route.get("enabled", True)),
        "paid_allowed": bool(route.get("paid_allowed", True)),
        "batch": bool(route.get("batch", False)),
        "timeout": int(route.get("timeout", 120)),
    }
    _save(data)
    return data["routing"][task]


# ---- usage ------------------------------------------------------------------

def record_usage(task: str, cid: str, mid: str, calls=1, spend=0.0):
    data = _load()
    u = data["usage"].setdefault(task, {})
    slot = u.setdefault(f"{cid}:{mid}", {"calls": 0, "spend": 0.0})
    slot["calls"] += calls
    slot["spend"] += spend
    _save(data)


def get_usage() -> dict:
    return _load()["usage"]


# ---- redaction (nothing secret ever leaves) --------------------------------

def _redact_cred(c: dict) -> dict:
    return {
        "id": c["id"], "name": c["name"], "base_url": c["base_url"],
        "auth_style": c["auth_style"], "auth_header_name": c.get("auth_header_name", ""),
        "credit_note": c.get("credit_note", ""),
        "key_set": bool(c.get("api_key")),
        "key_masked": auth.mask(c.get("api_key", "")),
        "created_at": c.get("created_at"),
        "models": c.get("models", []),
    }


def redacted_view() -> dict:
    """The whole store with keys masked — safe to send to the UI."""
    data = _load()
    return {
        "credentials": [_redact_cred(c) for c in data["credentials"]],
        "routing": data["routing"],
        "usage": data["usage"],
    }
