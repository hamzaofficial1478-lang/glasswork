"""
urls.py — the ONE place base URLs are normalized and request URLs are built.

Every caller (the probe/test path AND the production call path) joins URLs
through these functions, so the two can never diverge. That divergence is what
causes "works in the test, fails for real" bugs, so it is deliberately impossible
here: there is a single joiner, unit-tested against the awkward cases.
"""

# endpoint suffixes we know how to strip back to a base
_KNOWN_SUFFIXES = (
    "/chat/completions", "/completions", "/responses",
    "/embeddings", "/audio/speech", "/audio/transcriptions",
    "/images/generations", "/models",
)


def normalize_base(raw: str) -> str:
    """
    Store a clean base URL. Strips trailing slashes, and if the user pasted a full
    endpoint (e.g. .../v1/chat/completions) strips it back to the base (.../v1).
    """
    u = (raw or "").strip()
    if not u:
        return ""
    u = u.rstrip("/")
    low = u.lower()
    for suf in _KNOWN_SUFFIXES:
        if low.endswith(suf):
            u = u[: -len(suf)]
            break
    return u.rstrip("/")


def _path_of(base: str) -> str:
    """The path part of a base URL, '' for a bare host."""
    u = (base or "").strip()
    u = u.split("://", 1)[1] if "://" in u else u
    return ("/" + u.partition("/")[2]).rstrip("/") if "/" in u else ""


def with_version(base: str) -> str:
    """
    A bare host gets /v1. Every OpenAI-compatible API lives under a version
    path; none of them answer at the root.

    THIS COST THE OWNER THE WHOLE AGENCY'S REASONING.

    His store held 'https://api.openai.com' and 'https://api.anthropic.com'
    with no /v1 — which is what you get if you type the provider's website
    address, and it is the obvious thing to type. Every call the agency has
    ever made went to /chat/completions, every one came back 404, and the
    cascade dutifully tried the fallback and got 404 there too. So every desk
    fell back to reading its own rows out, the shelf showed brains configured
    and "not proven yet", and nothing anywhere said "your base URL is missing
    two characters".

    Only a base with NO path is touched. A base that already says /v1, /v1beta,
    /openai/v1 or anything else is the owner's, and is left exactly as he set
    it — the point is to fix the one unambiguous mistake, not to start guessing
    at URLs.
    """
    b = normalize_base(base)
    if not b or _path_of(b):
        return b
    return b + "/v1"


def join_url(base: str, path: str) -> str:
    """Join a normalized base with a path, exactly once, no double/missing slash."""
    b = with_version(base)
    p = "/" + (path or "").lstrip("/")
    return b + p


def chat_endpoint(base: str) -> str:
    return join_url(base, "chat/completions")


def models_endpoint(base: str) -> str:
    return join_url(base, "models")


def derived_note(base: str) -> str:
    """Human-readable line for the UI: what got stored and where calls will go."""
    b = normalize_base(base)
    note = f"Stored base: {b} → chat endpoint {chat_endpoint(b)}"
    if b and not _path_of(b):
        note += "  (no version path given, so /v1 was added)"
    return note
