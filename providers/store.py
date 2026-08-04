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

_EMPTY = {"credentials": [], "routing": {}, "usage": {}, "tools": []}

# The shelves the cabinet lends out. These are only STARTING POINTS — a tool can
# name any shelf it likes and the cabinet will create it, so you are never boxed
# into this list. Grouped by what the shelf is actually for:
#
#   reasoning — the extra brains a sector borrows beyond its own: a heavier or
#               multimodal LLM, an image-understanding model, embeddings, speech.
#               This is the heart of the shelf.
#   data      — read-the-world APIs: SEO/keyword data, stock graphics, crawling.
#   delivery  — the outward channels. Email and WhatsApp are configured on the
#               Integrations screen as the PRIMARY route; they sit here too so a
#               second provider can act as a FALLBACK, and so sending takes the
#               shelf lock (which is what enforces turn-taking and the daily cap).
SHELF_KINDS = {
    "llm_extra":       "reasoning",   # a stronger/spare text brain
    "vision":          "reasoning",   # image understanding / reasoning over images
    "embedding":       "reasoning",   # vectors for similarity + dedupe
    "image_gen":       "reasoning",   # generated graphics (decoration only)
    "speech":          "reasoning",   # transcription / voice
    "seo_data":        "data",
    "stock_images":    "data",
    "crawler":         "data",
    "email":           "delivery",
    "whatsapp":        "delivery",
    "other":           "other",
}
DEFAULT_SHELVES = list(SHELF_KINDS)
# kept as an alias so older callers/tests keep working
TOOL_SHELVES = DEFAULT_SHELVES


def shelf_kind(name: str) -> str:
    return SHELF_KINDS.get((name or "").strip().lower(), "custom")


def known_shelves() -> list:
    """Defaults plus any shelf a saved tool actually uses (custom ones included)."""
    names = list(DEFAULT_SHELVES)
    for t in _load().get("tools", []):
        s = (t.get("shelf") or "").strip().lower()
        if s and s not in names:
            names.append(s)
    return names


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


# ---- tools (shelf items — 3rd-party capability APIs) -----------------------
# Same incremental discipline as credentials: a save touches only the one tool,
# and a blank key on an update keeps the stored key (never erases a secret).

def upsert_tool(tool: dict) -> dict:
    data = _load()
    tools = data.setdefault("tools", [])
    tid = tool.get("id") or _new_id("tool")
    existing = next((t for t in tools if t["id"] == tid), None)

    key_in = (tool.get("api_key") or "").strip()
    if existing and not key_in:
        key_in = existing.get("api_key", "")

    record = {
        "id": tid,
        "shelf": (tool.get("shelf") or "other").strip().lower(),
        "name": (tool.get("name") or "").strip() or "Unnamed tool",
        # A tool's URL IS its endpoint (…/v1/search, …/v3/serp, …/models), so it is
        # kept VERBATIM. Only model credentials get normalised back to a base —
        # doing that here would strip real path segments and 404 the call.
        "base_url": (tool.get("base_url") or "").strip().rstrip("/"),
        "api_key": key_in,
        "auth_style": (tool.get("auth_style") or "bearer").strip().lower(),
        "auth_header_name": (tool.get("auth_header_name") or "").strip(),
        "cost_tier": (tool.get("cost_tier") or "unknown").strip(),
        "daily_cap": float(tool.get("daily_cap", 0) or 0),
        "priority": int(tool.get("priority", 100)),
        "enabled": bool(tool.get("enabled", True)),
        "notes": (tool.get("notes") or "").strip(),
        "last_test_result": tool.get("last_test_result",
                                     (existing or {}).get("last_test_result", {})),
        "created_at": existing.get("created_at") if existing else int(time.time()),
    }
    if existing:
        tools[tools.index(existing)] = record
    else:
        tools.append(record)
    _save(data)
    return _redact_tool(record)


def delete_tool(tid: str) -> bool:
    data = _load()
    before = len(data.get("tools", []))
    data["tools"] = [t for t in data.get("tools", []) if t["id"] != tid]
    _save(data)
    return len(data["tools"]) < before


def get_tool(tid: str):
    return next((t for t in _load().get("tools", []) if t["id"] == tid), None)


def list_tools() -> list:
    return list(_load().get("tools", []))


def tools_on_shelf(shelf: str) -> list:
    """Enabled tools on a shelf, best (lowest priority number) first — the
    fallback order the cabinet hands back when an agent borrows that shelf."""
    shelf = (shelf or "").strip().lower()
    ts = [t for t in _load().get("tools", []) if t.get("shelf") == shelf and t.get("enabled", True)]
    return sorted(ts, key=lambda t: t.get("priority", 100))


def resolve_shelf(shelf: str) -> dict:
    """
    The FALLBACK CHAIN for a shelf: try these in order until one works.

    Position 1 is the primary; everything after it is a fallback, exactly like the
    per-sector routing. An agent borrowing 'email' with two providers configured
    gets both, so if the first refuses the second is tried rather than the job
    failing. Nothing secret is returned — the caller looks the key up by id.
    """
    shelf = (shelf or "").strip().lower()
    chain = tools_on_shelf(shelf)
    return {
        "ok": bool(chain),
        "shelf": shelf,
        "kind": shelf_kind(shelf),
        "primary": (chain[0]["name"] if chain else None),
        "chain": [{"id": t["id"], "name": t["name"], "priority": t.get("priority", 100),
                   "cost_tier": t.get("cost_tier", "unknown"),
                   "daily_cap": t.get("daily_cap", 0)} for t in chain],
        "error": None if chain else f"no enabled API on the '{shelf}' shelf",
    }


def shelf_catalogue() -> dict:
    """
    What sits on every shelf, for the dashboard and for the cabinet's fallback
    resolution. Model shelves come from each model's category (llm/image/
    embedding); tool shelves come from the tools list. Nothing secret here.
    """
    data = _load()
    cat = {}
    for c in data["credentials"]:
        for m in c.get("models", []):
            shelf = (m.get("category") or "LLM").strip().lower()
            cat.setdefault(shelf, []).append({
                "kind": "model", "id": m["id"],
                "name": f'{c["name"]} · {m.get("api_model_id", "")}',
                "cost_tier": m.get("cost_tier", "unknown"),
                "enabled": bool(m.get("enabled", True)),
                "priority": int(m.get("priority", 100)),
            })
    for t in data.get("tools", []):
        cat.setdefault(t["shelf"], []).append({
            "kind": "tool", "id": t["id"], "name": t["name"],
            "base_url": t.get("base_url", ""),
            "cost_tier": t.get("cost_tier", "unknown"),
            "daily_cap": t.get("daily_cap", 0),
            "enabled": bool(t.get("enabled", True)),
            "priority": int(t.get("priority", 100)),
        })
    for shelf in cat:
        cat[shelf].sort(key=lambda i: i.get("priority", 100))
    return cat


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


def _redact_tool(t: dict) -> dict:
    return {
        "id": t["id"], "shelf": t.get("shelf", "other"), "name": t["name"],
        "base_url": t.get("base_url", ""), "auth_style": t.get("auth_style", "bearer"),
        "auth_header_name": t.get("auth_header_name", ""),
        "cost_tier": t.get("cost_tier", "unknown"), "daily_cap": t.get("daily_cap", 0),
        "priority": t.get("priority", 100), "enabled": bool(t.get("enabled", True)),
        "notes": t.get("notes", ""), "last_test_result": t.get("last_test_result", {}),
        "key_set": bool(t.get("api_key")), "key_masked": auth.mask(t.get("api_key", "")),
        "created_at": t.get("created_at"),
    }


def redacted_view() -> dict:
    """The whole store with keys masked — safe to send to the UI."""
    data = _load()
    return {
        "credentials": [_redact_cred(c) for c in data["credentials"]],
        "routing": data["routing"],
        "usage": data["usage"],
        "tools": [_redact_tool(t) for t in data.get("tools", [])],
        "tool_shelves": known_shelves(),
        "shelf_kinds": {s: shelf_kind(s) for s in known_shelves()},
    }
