"""
auth.py — the ONE place an auth header is built. Used by probe and production
alike, so a credential authenticates identically everywhere. Never emits two
conflicting auth headers.
"""

STYLES = ("bearer", "x-api-key", "token", "custom")


def auth_header(style: str, api_key: str, custom_name: str = "") -> dict:
    """
    Return the single auth header for a credential.
      bearer    -> Authorization: Bearer <key>
      x-api-key -> x-api-key: <key>
      token     -> Authorization: Token <key>
      custom    -> <custom_name>: <key>
    """
    key = (api_key or "").strip()
    style = (style or "bearer").strip().lower()
    if not key:
        return {}
    if style == "bearer":
        return {"Authorization": f"Bearer {key}"}
    if style == "x-api-key":
        return {"x-api-key": key}
    if style == "token":
        return {"Authorization": f"Token {key}"}
    if style == "custom":
        name = (custom_name or "").strip() or "Authorization"
        return {name: key}
    # unknown style -> safest default, never guess a second header
    return {"Authorization": f"Bearer {key}"}


def redact(api_key: str) -> str:
    """
    Show a key is present without revealing it: prefix + length + a whitespace flag.
    Used in every log / error / UI surface — a raw key must never be printed.
    """
    k = api_key or ""
    if not k:
        return "(none)"
    stray = " ⚠has-stray-whitespace" if k != k.strip() else ""
    shown = k.strip()
    prefix = shown[:4]
    return f"{prefix}…(len {len(shown)}){stray}"


def mask(api_key: str) -> str:
    """UI mask after saving: ****<last 4>."""
    k = (api_key or "").strip()
    if not k:
        return ""
    return "****" + k[-4:] if len(k) >= 4 else "****"
