"""
request.py — the ONE request builder. Probe/test and production both call these,
so URL + headers + payload are byte-identical for the same credential. (See
providers/tests.py, which asserts exactly that.)
"""
import json

from . import auth
from .urls import join_url


def build_request(cred: dict, path: str, payload=None, extra_headers=None):
    """Return (url, headers, body_str). The single source of request shape."""
    url = join_url(cred.get("base_url", ""), path)
    headers = {"Content-Type": "application/json"}
    headers.update(auth.auth_header(
        cred.get("auth_style"), cred.get("api_key", ""), cred.get("auth_header_name", "")))
    if extra_headers:
        headers.update(extra_headers)
    body = json.dumps(payload) if payload is not None else None
    return url, headers, body


def build_chat_request(cred: dict, api_model_id: str, messages, max_tokens=512, extra=None):
    """The chat/completions request, shared by probe and production."""
    payload = {"model": api_model_id, "messages": messages, "max_tokens": max_tokens}
    if extra:
        payload.update(extra)
    return build_request(cred, "chat/completions", payload)


def redact_headers(cred: dict, headers: dict) -> dict:
    """Copy of headers with the auth value replaced by a redaction — safe to show."""
    out = dict(headers)
    key = cred.get("api_key", "")
    for name, val in list(out.items()):
        if key and key in val:
            out[name] = val.replace(key, auth.redact(key))
        elif name.lower() in ("authorization", "x-api-key") or name == cred.get("auth_header_name"):
            out[name] = auth.redact(key)
    return out
