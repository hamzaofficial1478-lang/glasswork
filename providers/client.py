"""
client.py — test a shelf tool's key against its own endpoint.

Every failure returns a key-redacted diagnostic (url, headers, method, status,
body) and a plain-language likely cause — the one feature that makes almost
every misconfiguration obvious at a glance.

This used to be the client for every AI provider too: chat, model lists,
retries with backoff. Every sector thinks with Claude now
(providers/claude_team.py), so only the tool half is left.
"""
import json

import requests

from . import auth


def _likely_cause(status, err_text):
    if status == 401 or status == 403:
        return "401/403 — auth problem: check the API key and auth style."
    if status == 404:
        return "404 — wrong URL: check the address you saved for this tool."
    if status == 402:
        return "402 — the provider needs payment or credit. A billing issue on their side, not your setup."
    if status == 429:
        return "429 — rate limited or out of credit."
    if status == 0 and "timed out" in (err_text or "").lower():
        return "Timed out — raise the timeout, or the endpoint is slow/unreachable."
    if status == 0:
        return "Could not connect — check the base URL is reachable."
    if status >= 500:
        return f"{status} — provider-side error, try again."
    return f"HTTP {status}."


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


def _is_extract_api(tool) -> bool:
    """
    A crawler/fetch API — Zyte today, the same shape for the others. Recognised
    by the shelf it sits on or by its own address, so it works whether the owner
    added it through the crawler dropdown or typed the URL by hand.
    """
    blob = ((tool.get("base_url") or "") + " " + (tool.get("name") or "")).lower()
    return ((tool.get("shelf") or "").strip().lower() == "crawler"
            or "zyte" in blob or "/v1/extract" in blob)


def _probe_extract_api(tool, timeout):
    """
    Test a Zyte-style extract API for real: Basic auth, a POST, a body naming a
    stable public URL. httpResponseBody (not browserHtml) because a plain fetch
    is the cheaper of the two and proving the KEY does not need a browser render.

    The verdicts are Zyte's, not a search API's:
      200  the key works.
      401  the key is wrong — and the hint says Basic, never 'raw'.
      429  the key WORKS; it is only rate-limited or out of quota.
    """
    url = (tool.get("base_url") or "https://api.zyte.com/v1/extract").strip()
    key = (tool.get("api_key") or "").strip()
    if not key:
        return {"ok": False, "status": 0, "cause": "No API key saved for this tool."}
    # The key is the Basic USERNAME with an empty password. Owner pastes it bare;
    # the colon is ours. Same rule as pipelines/fetcher.py — kept in step by both
    # going through auth.auth_header('basic', ...).
    probe_key = key if key.endswith(":") else key + ":"
    headers = {"Content-Type": "application/json", "Accept": "application/json",
               **auth.auth_header("basic", probe_key)}
    # Zyte's own starter snippet points at this sandbox, so it is guaranteed an
    # allowed target — a target-side problem cannot masquerade as a bad key.
    body = json.dumps({"url": "https://books.toscrape.com/", "httpResponseBody": True})
    raw = {"url": url, "headers": redact_headers(tool, headers), "method": "POST",
           "status": None, "response_body": None}
    try:
        r = requests.post(url, headers=headers, data=body, timeout=timeout)
    except requests.exceptions.Timeout:
        raw["status"] = 0
        return {"ok": False, "status": 0, "cause": f"Timed out after {timeout}s.", "raw": raw}
    except Exception as e:
        raw["status"] = 0
        raw["response_body"] = str(e)[:300]
        return {"ok": False, "status": 0,
                "cause": "Could not connect — check the base URL is reachable.", "raw": raw}

    raw["status"] = r.status_code
    raw["response_body"] = r.text[:600]
    if 200 <= r.status_code < 300:
        return {"ok": True, "status": r.status_code,
                "cause": "Reachable, the key was accepted, and a test page came back. "
                         "This crawler is working.", "raw": raw}
    if r.status_code in (401, 403):
        return {"ok": False, "status": r.status_code,
                "cause": (f"{r.status_code} — Zyte rejected the key. Check it is the "
                          "Zyte API key (not the Zyte Proxy password), copied whole. "
                          "The auth style does not matter here — the code always uses "
                          "HTTP Basic for Zyte, so there is no prefix to switch."),
                "raw": raw}
    if r.status_code == 429:
        return {"ok": True, "status": 429,
                "cause": "The key WORKS — this is a rate-limit or an empty quota, not a "
                         "bad key. It will fetch once the limit resets or the plan has "
                         "credit.", "raw": raw}
    return {"ok": False, "status": r.status_code,
            "cause": _likely_cause(r.status_code, r.text), "raw": raw}


def probe_tool(tool, timeout=90):
    """
    Test a SHELF tool (DataForSEO, Pexels, a crawler…). These aren't chat APIs, so
    we can't send a prompt — instead we make a real authenticated request to the
    tool's own base URL and report exactly what came back, key-redacted.

    Only 401/403 actually means a bad key. The other codes are routinely mistaken
    for one, so each gets a verdict that reflects what it really proves:
      2xx      the key works.
      401/403  the key was rejected (+ a hint, since a wrong PREFIX looks identical).
      404      host reachable, wrong path — says nothing about the key.
      405      wrong VERB, checked before auth — proves nothing, so we retry POST.
      400/422  the host READ the key and objected to the REQUEST. Auth passed; we
               retry with a sample query so a working API shows live data.
    """
    url = (tool.get("base_url") or "").strip()
    if not url:
        return {"ok": False, "status": 0, "cause": "No base URL saved for this tool."}

    # A fetch/extract API cannot be tested like a search API. It answers a POST
    # whose JSON body names a URL, with HTTP Basic auth (the key as username).
    # A GET, or a Bearer header, comes back 401 that looks exactly like a bad
    # key — which is what had the generic probe telling Zyte owners to "switch
    # to raw", the one style that is also wrong. So these get their own probe.
    if _is_extract_api(tool):
        return _probe_extract_api(tool, timeout)

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
        elif r.status_code in (400, 422):
            # A 400 means the host READ the credential and then objected to the
            # REQUEST (Pexels: "No query param given"). Auth already succeeded —
            # reporting this as a failure sends the owner hunting a key that works.
            return _retry_with_params(tool, url, headers, timeout, raw, r)
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


# Search-style APIs need a term before they will answer. These are the common
# spellings. We only ever ADD a parameter to the URL the owner saved — the path
# is never touched — and only after a 400/422 has already proved the key got in.
_SAMPLE_PARAMS = ({"query": "test", "per_page": 1},
                  {"q": "test", "limit": 1},
                  {"keyword": "test"},
                  {"search": "test"})


def _retry_with_params(tool, url, headers, timeout, raw, first):
    """
    Turn "400 — no query given" into a real answer.

    The 400 alone already proves the credential was accepted, but telling an owner
    "your error is actually a pass" is a poor substitute for showing them live data.
    So try a sample search first; only fall back to explaining the 400 if none fit.
    """
    sample_timeout = min(timeout, 30)
    for params in _SAMPLE_PARAMS:
        try:
            r = requests.get(url, headers=headers, params=params, timeout=sample_timeout)
        except Exception:
            break  # network went away mid-test; the original 400 still stands
        if 200 <= r.status_code < 300:
            raw["method"] = f"GET (retried with a sample query: {params})"
            raw["status"] = r.status_code
            raw["response_body"] = r.text[:600]
            return {"ok": True, "status": r.status_code,
                    "cause": "Reachable, key accepted, and a sample query returned live data. "
                             "This API is fully working.", "raw": raw}
        if r.status_code in (401, 403):
            raw["method"] = f"GET (retried with a sample query: {params})"
            raw["status"] = r.status_code
            raw["response_body"] = r.text[:600]
            return {"ok": False, "status": r.status_code,
                    "cause": f"{r.status_code} — the host answered but rejected the key."
                             + _auth_hint(tool), "raw": raw}
    # Nothing matched this API's parameter names — but auth still demonstrably passed.
    return {"ok": True, "status": first.status_code,
            "cause": (f"{first.status_code} — the key was ACCEPTED. The host only rejected the test "
                      "request itself, because it wants a search parameter this generic test "
                      "cannot guess. Your credential is wired correctly."), "raw": raw}


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
