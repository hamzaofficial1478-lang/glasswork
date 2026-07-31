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


def join_url(base: str, path: str) -> str:
    """Join a normalized base with a path, exactly once, no double/missing slash."""
    b = normalize_base(base)
    p = "/" + (path or "").lstrip("/")
    return b + p


def chat_endpoint(base: str) -> str:
    return join_url(base, "chat/completions")


def models_endpoint(base: str) -> str:
    return join_url(base, "models")


def derived_note(base: str) -> str:
    """Human-readable line for the UI: what got stored and where calls will go."""
    b = normalize_base(base)
    return f"Stored base: {b} → chat endpoint {chat_endpoint(b)}"
