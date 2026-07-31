"""
signatures.py — known-vendor signature table.

Matching a credential's base URL (and optionally a model id pattern) here gives
instant, correct capability flags without a live probe. Unknown vendors fall
through to live probing (client.probe_model). Anything still undetermined is
reported as "unknown" and the dependent feature degrades off — it never blocks.

Keep this data-only: no vendor behaviour is hardcoded in the core logic, it is
described here and confirmed by probes.
"""

# each entry: match on a substring of the (lowercased) base url; optional model
# regex; the capabilities it implies. capabilities use True / False / None(unknown).
SIGNATURES = [
    {
        "match_base": "api.anthropic.com",
        "vendor": "Anthropic",
        "auth_style": "x-api-key",
        "caps": {"chat": True, "multimodal": True, "streaming": True},
    },
    {
        "match_base": "api.openai.com",
        "vendor": "OpenAI",
        "auth_style": "bearer",
        "caps": {"chat": True, "multimodal": True, "streaming": True},
    },
    {
        "match_base": "openrouter.ai",
        "vendor": "OpenRouter",
        "auth_style": "bearer",
        "caps": {"chat": True, "multimodal": None, "streaming": True},
    },
    {
        "match_base": "integrate.api.nvidia.com",
        "vendor": "NVIDIA",
        "auth_style": "bearer",
        "caps": {"chat": True, "multimodal": None, "streaming": True},
    },
    {
        "match_base": "generativelanguage.googleapis.com",
        "vendor": "Google",
        "auth_style": "bearer",
        "caps": {"chat": True, "multimodal": True, "streaming": True},
    },
]


def match(base_url: str, model_id: str = ""):
    """Return the first matching signature dict, or None."""
    b = (base_url or "").lower()
    for sig in SIGNATURES:
        if sig["match_base"] in b:
            return sig
    return None


def guess_auth_style(base_url: str) -> str:
    sig = match(base_url)
    return sig["auth_style"] if sig else "bearer"
