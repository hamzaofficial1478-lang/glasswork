"""
store.py — the credential/model/routing store.

Persisted to config/providers.local.json (git-ignored, never committed, never
logged). THIS is where the old "one save wipes the last" bug is fixed: every write
loads the current file, changes ONLY the one credential/model/route being touched,
and writes back. Saving one credential never disturbs another.

Keys are kept in the local file (like a .env) and are ALWAYS masked/redacted in
any value that leaves the server (see redacted_view / auth.mask).
"""
import contextlib
import json
import tempfile
import time
import uuid
from pathlib import Path

from . import auth
from .urls import normalize_base

STORE = Path(__file__).parents[1] / "config" / "providers.local.json"

_EMPTY = {"credentials": [], "routing": {}, "usage": {}, "tools": []}

# The shelves the cabinet lends out. A tool can name any shelf it likes and the
# cabinet will create it, so this list is a starting point, not a cage.
#
#   data      — read-the-world APIs: keyword data, stock graphics, crawling.
#   delivery  — the outward channels.
#
# THERE IS DELIBERATELY NO llm SHELF. A sector's text brain is ROUTED per task
# (providers/routing.py: md, it, content, seo, leads, security, report), never
# borrowed. That distinction is the answer to "will an agent end up using the
# MD's model": it cannot. A shelf is shared and first-come-first-served, so an
# llm shelf would let any sector take whichever model sat on it — including the
# heavy one bought for the MD — and quietly out-reason or override the sector it
# was routed for. Routing gives each sector the brain the owner picked for it and
# no way to reach another's. Models still get GROUPED by category on the board
# so you can see them at a glance; grouping is not lending.
#
# ONLY SHELVES SOMETHING ACTUALLY BORROWS.
#
# There used to be twelve. Five were read by no code at all — vision, speech,
# trends, embedding and a generated-image shelf — and they did real harm by
# sitting there: the setup screen showed "no APIs on this shelf yet" beside each
# one, which reads as a job left undone. The owner went looking for keys to fill
# shelves that nothing would ever open. An empty slot that can never be used is
# not a feature, it is a chore you invented for somebody else.
#
# The rule for adding one back: a pipeline must borrow it. Grep before you add.
SHELF_KINDS = {
    "seo_data":        "data",        # OPTIONAL — pipelines/seo_intel.py does the
                                      # job free; a paid key adds volumes and rank
    "maps":            "data",        # Google Places — real UAE businesses, by area
    # Every picture that is not one of the owner's own job photos. Pexels sits
    # here as the primary, Pixabay under it as the fallback. There is deliberately
    # no image-generation shelf: a stock library holding tens of thousands of real
    # glass, aluminium and construction shots beats a generated picture for this
    # trade every time, and a generated one can never be a project photo — the
    # thing that actually wins the job.
    "stock_images":    "data",
    "crawler":         "data",        # Zyte — rival sites, and JS pages of ours
    # SMTP is the delivery route and it lives in .env, not here. This shelf still
    # exists because sending takes its LOCK: that is what serialises outreach and
    # makes the daily cap enforceable. It is not empty when SMTP is configured,
    # and the board says so rather than showing "no APIs yet" beside a working
    # mail setup — see cmd_shelves.
    "email":           "delivery",
    # SPARE BRAINS, and only spare ones.
    #
    # A model ROUTED to a sector never appears on a shelf — see
    # routed_model_ids() and shelf_catalogue(). The owner's reasoning, and he is
    # right: a shelf is a single-holder lock, so an agent that borrowed the MD's
    # model would BLOCK the MD from using its own brain until it handed it back.
    # Worse, it would be spending the expensive model he bought for planning on
    # whatever the agent felt like.
    #
    # So this shelf is the pool of models he has set aside for exactly that:
    # anything an agent wants beyond its sector's own brain, it takes from here,
    # queues for it like any other shared thing, and hands it back. Put a model
    # in the 'llm_extra' category to add it to the pool.
    "llm_extra":       "reasoning",
    "other":           "other",       # a tool saved without a shelf lands here
}
DEFAULT_SHELVES = list(SHELF_KINDS)
# kept as an alias so older callers/tests keep working
TOOL_SHELVES = DEFAULT_SHELVES

# Model shelves are named after a model's CATEGORY, so they are created by adding
# a model rather than a tool — they must not appear in the tool-shelf dropdown,
# but the dashboard still has to group them under a purpose instead of dumping
# them in "custom". Kept separate from SHELF_KINDS for exactly that reason.
# Model CATEGORIES, not shelves. A model is filed under its category so the
# dashboard can group it; it is never borrowed. 'image' is gone with image
# generation, and 'embedding' with it — nothing embedded anything.
MODEL_SHELF_KINDS = {
    "llm":       "reasoning",   # the text brain each sector routes to
}


def shelf_kind(name: str) -> str:
    n = (name or "").strip().lower()
    return SHELF_KINDS.get(n) or MODEL_SHELF_KINDS.get(n, "custom")


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


@contextlib.contextmanager
def isolated():
    """
    Point the store at a throwaway file for the duration.

    Every suite that asserts "with no model configured, this refuses" was
    reading the OWNER'S store, so those tests only passed on a machine with
    nothing set up. The day he saved his first model, `run.py --tests` began
    reporting three suites red for reasons that had nothing to do with his code
    — which teaches you to stop trusting the test run, and a test suite you
    have stopped trusting is worse than none.

    A test that says "nothing is configured" must be the one deciding that.
    """
    global STORE
    was = STORE
    d = Path(tempfile.mkdtemp(prefix="glasswork-store-"))
    STORE = d / "providers.local.json"
    try:
        yield STORE
    finally:
        STORE = was


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


def routed_model_ids() -> set:
    """
    Every model id any sector is routed to — primary, secondary and fallbacks.

    These are spoken for. They belong to a sector and must not turn up on a
    shelf where another agent could take a lock on them.
    """
    ids = set()
    for route in (_load().get("routing") or {}).values():
        if not isinstance(route, dict):
            continue
        for key in ("primary", "secondary"):
            v = (route.get(key) or "").strip()
            if v:
                ids.add(v)
        for v in (route.get("fallback") or []):
            v = (v or "").strip()
            if v:
                ids.add(v)
    return ids


def shelf_catalogue() -> dict:
    """
    What sits on every shelf, for the dashboard and for the cabinet's fallback
    resolution. Model shelves come from each model's category (llm/image/
    embedding); tool shelves come from the tools list. Nothing secret here.
    """
    data = _load()
    spoken_for = routed_model_ids()
    cat = {}
    for c in data["credentials"]:
        for m in c.get("models", []):
            # A brain that belongs to a sector is not shelf stock. Listing it
            # here is what made the board look like any agent could take the
            # MD's model, and a borrow really would have locked the MD out of
            # its own brain.
            if m["id"] in spoken_for:
                continue
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
