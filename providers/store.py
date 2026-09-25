"""
store.py — the shelf: the non-AI tools the agency borrows.

Stock photos, the crawler, Google Places, SEO data. Persisted to
config/providers.local.json (git-ignored, never committed, never logged). Every
write loads the current file, changes ONLY the one tool being touched, and
writes back — saving one never disturbs another.

AI models are not kept here any more. Every sector thinks with a Claude model
(providers/claude_team.py), so the credentials, models and per-sector routing
this file used to hold are gone. A file saved by an older build still carries
them; they are left exactly where they are and simply not read, so nothing of
the owner's is deleted behind his back.

Keys are ALWAYS masked/redacted in any value that leaves the server.
"""
import contextlib
import json
import os
import tempfile
import time
import uuid
from pathlib import Path

from . import auth

STORE = Path(__file__).parents[1] / "config" / "providers.local.json"
# The owner's real file, remembered so the suite can tell it apart from a
# temp store a test has pointed STORE at. See _load.
_REAL_STORE = STORE

_EMPTY = {"tools": []}

# The shelves the cabinet lends out. A tool can name any shelf it likes and the
# cabinet will create it, so this list is a starting point, not a cage.
#
#   data      — read-the-world APIs: keyword data, stock graphics, crawling.
#   delivery  — the outward channels.
#
# THERE IS NO llm SHELF, AND NO SPARE BRAINS EITHER. Every sector thinks
# with the Claude model the owner chose for it (providers/claude_team.py). A
# model is not a seat, so nothing needs lending; the thing that runs out is an
# account's allowance, and the Claude team handles that itself.
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
    "other":           "other",       # a tool saved without a shelf lands here
}
DEFAULT_SHELVES = list(SHELF_KINDS)
# kept as an alias so older callers/tests keep working
TOOL_SHELVES = DEFAULT_SHELVES

def shelf_kind(name: str) -> str:
    n = (name or "").strip().lower()
    return SHELF_KINDS.get(n, "custom")


def known_shelves() -> list:
    """Defaults plus any shelf a saved tool actually uses (custom ones included)."""
    names = list(DEFAULT_SHELVES)
    for t in _load().get("tools", []):
        s = (t.get("shelf") or "").strip().lower()
        if s and s not in names:
            names.append(s)
    return names


def _load() -> dict:
    # THE SUITE MUST NOT REACH HIS REAL KEYS.
    #
    # "the test suite did not finish inside 1080s" — on a machine where the
    # same suite takes under two minutes here. The difference is this file. A
    # test that resolves a route and calls it is a stub call on a clean
    # checkout and a LIVE API call on his, complete with timeouts, retries and
    # rate limits, and it_release refuses to touch the website until the suite
    # finishes. So his own configuration made the release gate slow enough to
    # time out, which is the same shape of fault as .env turning it red.
    #
    # Same switch as providers/integrations.read_env, set once by run.py for
    # the whole suite: an empty store, so every provider path takes its
    # not-configured branch and nothing leaves the machine.
    # Only HIS file at the real path is hidden. A suite that builds its own
    # store repoints STORE at a temp file and must still be able to read what
    # it just wrote — blanket-emptying broke eight of them, which is the right
    # answer arriving the wrong way round.
    if STORE == _REAL_STORE and str(
            os.environ.get("AGENCY_IGNORE_DOTENV", "")).strip().lower() in (
            "1", "true", "yes", "on"):
        return json.loads(json.dumps(_EMPTY))
    if not STORE.exists():
        return json.loads(json.dumps(_EMPTY))
    try:
        data = json.loads(STORE.read_text(encoding="utf-8") or "{}")
    except Exception:
        return json.loads(json.dumps(_EMPTY))
    for k, v in _EMPTY.items():
        data.setdefault(k, json.loads(json.dumps(v)))
    return data


def _save(data: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
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


# ---- tools (shelf items — 3rd-party capability APIs) -----------------------
# A save touches only the one tool,
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
        # kept VERBATIM — normalising it to a base would strip real path
        # segments and 404 the call.
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

    Position 1 is the primary; everything after it is a fallback. An agent borrowing 'email' with two providers configured
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


def smtp_configured() -> bool:
    """Is there a real mail route? It lives in .env, not on a shelf."""
    try:
        from . import integrations
        env = integrations.read_env()
    except Exception:
        return False
    import os
    def _g(k):
        return (os.environ.get(k) or env.get(k) or "").strip()
    return all(_g(k) for k in ("SMTP_HOST", "SMTP_USER", "SMTP_PASS"))


def shelf_items(shelf: str) -> list:
    """
    What is ON a shelf, INCLUDING routes that live outside the tools list.

    One function, because there were two. The setup screen asked
    providers.api.cmd_shelves (which knew about SMTP) and the control room
    asked coordination.state (which did not), so the same email shelf read
    "SMTP (Integrations)" on one screen and "no APIs on this shelf yet" on the
    other, on the same machine, at the same moment. Two readings of one fact is
    a bug even when one of them is right.
    """
    shelf = (shelf or "").strip().lower()
    items = list(shelf_catalogue().get(shelf, []))
    if shelf == "email" and smtp_configured():
        items.append({"kind": "route", "id": "smtp", "name": "SMTP (Integrations)",
                      "cost_tier": "free", "enabled": True, "priority": 1,
                      "note": "configured in .env, not on the shelf"})
    return items


def shelf_catalogue() -> dict:
    """
    What sits on every shelf, for the dashboard and for the cabinet's fallback
    resolution. Nothing secret here.
    """
    data = _load()
    cat = {}
    for t in data.get("tools", []):
        cat.setdefault(t["shelf"], []).append({
            "kind": "tool", "id": t["id"], "name": t["name"],
            "base_url": t.get("base_url", ""),
            "cost_tier": t.get("cost_tier", "unknown"),
            "daily_cap": t.get("daily_cap", 0),
            "enabled": bool(t.get("enabled", True)),
            "priority": int(t.get("priority", 100)),
            "last_test_result": t.get("last_test_result") or {},
        })
    for shelf in cat:
        cat[shelf].sort(key=lambda i: i.get("priority", 100))
    return cat


# ---- redaction (nothing secret ever leaves) --------------------------------

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
        "tools": [_redact_tool(t) for t in data.get("tools", [])],
        "tool_shelves": known_shelves(),
        "shelf_kinds": {s: shelf_kind(s) for s in known_shelves()},
    }
