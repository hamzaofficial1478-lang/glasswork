"""
client.py — actually talk to a provider. Probe/test and production both go through
_execute() with a request built by request.py, so there is one code path.

Every failure returns a full, key-redacted diagnostic (url, headers, payload,
status, body) and a plain-language likely cause — that one feature makes almost
every misconfiguration obvious at a glance.
"""
import json

import requests

from . import auth, signatures
from .request import build_chat_request, build_request, redact_headers
from .urls import chat_endpoint, models_endpoint

# a 1x1 transparent PNG, for the multimodal probe
_TINY_PNG = ("data:image/png;base64,"
             "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


def _likely_cause(status, err_text):
    if status == 401 or status == 403:
        return "401/403 — auth problem: check the API key and auth style."
    if status == 404:
        return "404 — wrong URL: check the base URL and that this model id exists here."
    if status == 402:
        return "402 — the provider needs payment/credit for this model. A billing issue on their side, not your setup; sort it out with the provider."
    if status == 429:
        return "429 — rate limited or out of credit."
    if status == 0 and "timed out" in (err_text or "").lower():
        return "Timed out — raise the timeout, or the endpoint is slow/unreachable."
    if status == 0:
        return "Could not connect — check the base URL is reachable."
    if status >= 500:
        return f"{status} — provider-side error, try again."
    return f"HTTP {status}."


def _execute(cred, url, headers, body, timeout):
    """Single HTTP execution. Returns a dict with ok/status/text/raw (redacted)."""
    raw = {
        "url": url,
        "headers": redact_headers(cred, headers),
        "payload": json.loads(body) if body else None,
        "status": None,
        "response_body": None,
    }
    try:
        r = requests.post(url, headers=headers, data=body, timeout=timeout)
        raw["status"] = r.status_code
        text = r.text
        raw["response_body"] = text[:4000]
        ok = 200 <= r.status_code < 300
        return {"ok": ok, "status": r.status_code, "text": text,
                "cause": None if ok else _likely_cause(r.status_code, text), "raw": raw}
    except requests.exceptions.Timeout:
        raw["status"] = 0
        return {"ok": False, "status": 0, "text": "timed out",
                "cause": _likely_cause(0, "timed out"), "raw": raw}
    except Exception as e:
        raw["status"] = 0
        raw["response_body"] = str(e)
        return {"ok": False, "status": 0, "text": str(e),
                "cause": _likely_cause(0, str(e)), "raw": raw}


def _content_text(body_text):
    """Pull the assistant text out of an OpenAI/Anthropic-ish chat response."""
    try:
        d = json.loads(body_text)
    except Exception:
        return ""
    try:
        return d["choices"][0]["message"]["content"]
    except Exception:
        pass
    try:  # anthropic-style
        return d["content"][0]["text"]
    except Exception:
        return ""


def chat(cred, model, messages, max_tokens=512, timeout=180, extra=None):
    """Production LLM call. Shares build_chat_request with the probe path."""
    url, headers, body = build_chat_request(cred, model["api_model_id"], messages, max_tokens, extra)
    res = _execute(cred, url, headers, body, timeout)
    res["output"] = _content_text(res["text"]) if res["ok"] else ""
    return res


def fetch_models(cred, timeout=90):
    """GET <base>/models. Convenience only — failure must never block manual entry."""
    url, headers, _ = build_request(cred, "models")
    try:
        r = requests.get(url, headers=headers, timeout=timeout)
        ids = []
        if 200 <= r.status_code < 300:
            try:
                d = r.json()
                rows = d.get("data", d if isinstance(d, list) else [])
                ids = [m.get("id") for m in rows if isinstance(m, dict) and m.get("id")]
            except Exception:
                ids = []
        return {"ok": 200 <= r.status_code < 300, "status": r.status_code,
                "models": ids, "raw": {"url": url, "response_body": r.text[:2000]}}
    except Exception as e:
        return {"ok": False, "status": 0, "models": [], "error": str(e),
                "raw": {"url": url, "response_body": str(e)}}


def probe_model(cred, model, timeout=120):
    """
    Test & detect for a model, using a request VALID FOR ITS CATEGORY:
      LLM       -> a tiny chat completion (+ a multimodal image probe)
      Image     -> a tiny image-generation request
      Embedding -> a tiny embeddings request
    Signature table gives instant flags for known vendors; the live probe confirms.
    Anything undetectable is reported as None (unknown) and degrades off — never blocks.
    """
    category = (model.get("category") or "LLM").upper()
    sig = signatures.match(cred.get("base_url", ""), model.get("api_model_id", ""))
    diag = {"category": category, "vendor": sig["vendor"] if sig else "unknown",
            "endpoint": chat_endpoint(cred.get("base_url", ""))}

    if category == "LLM":
        caps = {"chat": None, "multimodal": None}
        if sig:
            caps.update({k: v for k, v in sig["caps"].items() if k in caps})
        res = chat(cred, model, [{"role": "user", "content": "Reply with the single word: ok"}],
                   max_tokens=5, timeout=timeout)
        caps["chat"] = bool(res["ok"])
        result = {"ok": res["ok"], "status": res["status"], "cause": res.get("cause"),
                  "output": res.get("output", ""), "raw": res["raw"]}
        if res["ok"] and caps.get("multimodal") is None:  # Vision = an LLM capability, detected here
            mm = [{"role": "user", "content": [
                {"type": "text", "text": "what colour is this? one word"},
                {"type": "image_url", "image_url": {"url": _TINY_PNG}}]}]
            caps["multimodal"] = bool(chat(cred, model, mm, max_tokens=5, timeout=timeout)["ok"])
        return {"capabilities": caps, "detect": diag, "test": result}

    if category == "IMAGE":
        url, headers, body = build_request(cred, "images/generations", {
            "model": model["api_model_id"], "prompt": "a small blue circle on a white background",
            "n": 1, "size": "256x256"})
        res = _execute(cred, url, headers, body, timeout)
        diag["endpoint"] = url
        return {"capabilities": {"images": bool(res["ok"])}, "detect": diag,
                "test": {"ok": res["ok"], "status": res["status"], "cause": res.get("cause"),
                         "output": "image generated" if res["ok"] else "", "raw": res["raw"]}}

    if category == "EMBEDDING":
        url, headers, body = build_request(cred, "embeddings", {
            "model": model["api_model_id"], "input": "ping"})
        res = _execute(cred, url, headers, body, timeout)
        diag["endpoint"] = url
        return {"capabilities": {"embeddings": bool(res["ok"])}, "detect": diag,
                "test": {"ok": res["ok"], "status": res["status"], "cause": res.get("cause"),
                         "output": "embedding returned" if res["ok"] else "", "raw": res["raw"]}}

    return {"capabilities": {"supported": None}, "detect": diag,
            "test": {"ok": None, "status": None, "output": "",
                     "cause": f"No live probe for {category}; leaving it as unknown.", "raw": diag}}


def probe_tool(tool, timeout=90):
    """
    Test a SHELF tool (DataForSEO, Pexels, a crawler…). These aren't chat APIs, so
    we can't send a prompt — instead we make a real authenticated request to the
    tool's own base URL and report exactly what came back, key-redacted.

    A 2xx means the key works. 401/403 means the key is wrong. 404 usually means
    the URL is right but the path isn't — which still proves the host is reachable
    and is reported as such rather than as a failure of the key.
    """
    from . import auth
    url = (tool.get("base_url") or "").strip()
    if not url:
        return {"ok": False, "status": 0, "cause": "No base URL saved for this tool."}
    headers = {"Accept": "application/json"}
    headers.update(auth.auth_header(tool.get("auth_style"), tool.get("api_key", ""),
                                    tool.get("auth_header_name", "")))
    raw = {"url": url, "headers": redact_headers(tool, headers), "method": "GET",
           "status": None, "response_body": None}
    try:
        r = requests.get(url, headers=headers, timeout=timeout)
        raw["status"] = r.status_code
        raw["response_body"] = r.text[:600]
        ok = 200 <= r.status_code < 300
        if ok:
            cause = "Reachable and the key was accepted."
        elif r.status_code in (401, 403):
            cause = f"{r.status_code} — the host answered but rejected the key."
        elif r.status_code == 404:
            cause = "404 — host reachable, but this exact path doesn't exist. The key may still be fine; check the base URL."
        elif r.status_code == 405:
            # 405 only says "wrong method" — it proves NOTHING about the key, because
            # the server rejects the verb before it ever checks auth. So try the same
            # URL with POST and report THAT, which does exercise the credential.
            return _retry_post(tool, url, headers, timeout, raw)
        else:
            cause = _likely_cause(r.status_code, r.text)
        if r.status_code == 401 or r.status_code == 403:
            cause += _auth_hint(tool)
        return {"ok": ok, "status": r.status_code, "cause": cause, "raw": raw}
    except requests.exceptions.Timeout:
        raw["status"] = 0
        return {"ok": False, "status": 0, "cause": f"Timed out after {timeout}s.", "raw": raw}
    except Exception as e:
        raw["status"] = 0
        raw["response_body"] = str(e)[:300]
        return {"ok": False, "status": 0,
                "cause": "Could not connect — check the base URL is reachable.", "raw": raw}


def _auth_hint(tool):
    """
    The single most common false 'bad key': sending "Bearer <key>" to an API that
    wants the bare key. Point at it instead of leaving the owner guessing.
    """
    style = (tool.get("auth_style") or "").lower()
    if style == "bearer":
        return ("  → Likely the PREFIX, not the key: this host may want the bare key with no "
                "'Bearer '. Switch Auth style to 'raw (no prefix)' and test again. "
                "(Pexels, and many media APIs, work this way.)")
    if style == "raw":
        return ("  → If the key itself is right, try 'bearer' — some hosts do want the prefix.")
    if style == "basic":
        return ("  → For Basic auth enter the key as login:password (it is base64-encoded for you).")
    return ""


def _retry_post(tool, url, headers, timeout, raw):
    """A 405 on GET means 'wrong verb', not 'bad key'. POST actually tests auth."""
    try:
        r = requests.post(url, headers={**headers, "Content-Type": "application/json"},
                          data="{}", timeout=timeout)
    except Exception as e:
        raw["method"] = "GET, then POST"
        raw["status"] = 0
        raw["response_body"] = str(e)[:300]
        return {"ok": False, "status": 0,
                "cause": "GET returned 405; the POST retry could not connect.", "raw": raw}
    raw["method"] = "GET returned 405, retried with POST"
    raw["status"] = r.status_code
    raw["response_body"] = r.text[:600]
    if 200 <= r.status_code < 300:
        return {"ok": True, "status": r.status_code,
                "cause": "Reachable and the key was accepted (via POST).", "raw": raw}
    if r.status_code in (401, 403):
        return {"ok": False, "status": r.status_code,
                "cause": f"{r.status_code} — the host answered but rejected the key."
                         + _auth_hint(tool), "raw": raw}
    if r.status_code in (400, 422):
        # auth passed; it only objected to our deliberately empty test body
        return {"ok": True, "status": r.status_code,
                "cause": (f"{r.status_code} — the key was ACCEPTED; the host only rejected the empty "
                          "test body, which is expected. This API is wired correctly."), "raw": raw}
    return {"ok": None, "status": r.status_code,
            "cause": f"POST returned {r.status_code}. " + _likely_cause(r.status_code, r.text),
            "raw": raw}
