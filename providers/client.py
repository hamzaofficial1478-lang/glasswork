"""
client.py — actually talk to a provider. Probe/test and production both go through
_execute() with a request built by request.py, so there is one code path.

Every failure returns a full, key-redacted diagnostic (url, headers, payload,
status, body) and a plain-language likely cause — that one feature makes almost
every misconfiguration obvious at a glance.
"""
import json
import random
import time

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


# ---------------------------------------------------------------------------
# COMING BACK AFTER A MOMENT
# ---------------------------------------------------------------------------
#
# "if an api module hit error there is no time increasing thing?"
#
# There was not. One HTTP call per model, and any failure — including a 429,
# which literally means 'you are going too fast, come back shortly' — burned
# that model and moved the cascade to the next one. When a sector's primary and
# fallback sit on the same provider and the same key, which is the normal case,
# one rate limit burned both inside the same second and the sector reported
# 'all providers for this task failed'. The owner read that as the fallback not
# existing. The fallback existed; it was spent in the same breath as the primary.
#
# So: transient failures are retried on the SAME model, with the wait growing
# each time, before the cascade is allowed to give up on it.
#
#   429  rate limited        retry — and honour Retry-After if the provider
#                            sends one, because the provider knows better than
#                            any formula here
#   408/409/425              retry
#   500 502 503 504          provider-side wobble, retry
#   0    timeout / no socket retry
#
# Never retried, because trying again cannot change the answer and only wastes
# the owner's time and quota:
#
#   400 422  malformed request — a bug here
#   401 403  wrong key
#   402      out of credit
#   404      wrong URL or model id
#
# Waits are 1s, 2s, 4s with a little jitter, so two agents that hit the same
# limit in the same second do not come back in the same second either. Total
# added delay in the worst case is about seven seconds — cheap next to a sector
# going dark for the day.

RETRY_STATUSES = {0, 408, 409, 425, 429, 500, 502, 503, 504}
MOST_TRIES = 3
FIRST_WAIT = 1.0
LONGEST_WAIT = 20.0


def _retry_after(r) -> float:
    """Seconds the provider asked us to wait, or 0. Its number beats ours."""
    try:
        v = (r.headers.get("Retry-After") or "").strip()
    except Exception:
        return 0.0
    if not v:
        return 0.0
    try:
        return max(0.0, min(LONGEST_WAIT, float(v)))
    except ValueError:
        pass
    try:                                   # the HTTP-date form
        from email.utils import parsedate_to_datetime
        import datetime
        when = parsedate_to_datetime(v)
        if when.tzinfo is None:
            when = when.replace(tzinfo=datetime.timezone.utc)
        secs = (when - datetime.datetime.now(datetime.timezone.utc)).total_seconds()
        return max(0.0, min(LONGEST_WAIT, secs))
    except Exception:
        return 0.0


def _wait_before(attempt, asked=0.0) -> float:
    """1s, 2s, 4s, with jitter — unless the provider named its own number."""
    if asked > 0:
        return asked
    return min(LONGEST_WAIT,
               FIRST_WAIT * (2 ** attempt) * (0.75 + random.random() * 0.5))


def _execute(cred, url, headers, body, timeout, tries=MOST_TRIES, sleep=None):
    """
    HTTP execution with backoff on the transient statuses. Returns a dict with
    ok/status/text/raw (redacted), plus `attempts` and, when it retried, a
    `waited` trail so the failover screen can show what it actually did.
    """
    raw = {
        "url": url,
        "headers": redact_headers(cred, headers),
        "payload": json.loads(body) if body else None,
        "status": None,
        "response_body": None,
    }
    nap = sleep or time.sleep
    waited = []
    tries = max(1, int(tries))
    for attempt in range(tries):
        pause = None
        try:
            r = requests.post(url, headers=headers, data=body, timeout=timeout)
            raw["status"] = r.status_code
            text = r.text
            raw["response_body"] = text[:4000]
            ok = 200 <= r.status_code < 300
            if ok or r.status_code not in RETRY_STATUSES or attempt == tries - 1:
                return {"ok": ok, "status": r.status_code, "text": text,
                        "cause": None if ok else _likely_cause(r.status_code, text),
                        "raw": raw, "attempts": attempt + 1,
                        "waited": waited}
            pause = _wait_before(attempt, _retry_after(r))
        except requests.exceptions.Timeout:
            raw["status"] = 0
            raw["response_body"] = "timed out"
            if attempt == tries - 1:
                return {"ok": False, "status": 0, "text": "timed out",
                        "cause": _likely_cause(0, "timed out"), "raw": raw,
                        "attempts": attempt + 1, "waited": waited}
            pause = _wait_before(attempt)
        except Exception as e:
            raw["status"] = 0
            raw["response_body"] = str(e)
            # A connection that was refused or reset is worth another go; a
            # malformed URL or a missing library is not, and retrying it three
            # times just triples how long the owner waits for the same message.
            transient = any(w in str(e).lower() for w in
                            ("connection", "reset", "temporarily", "unreachable",
                             "broken pipe", "eof occurred"))
            if not transient or attempt == tries - 1:
                return {"ok": False, "status": 0, "text": str(e),
                        "cause": _likely_cause(0, str(e)), "raw": raw,
                        "attempts": attempt + 1, "waited": waited}
            pause = _wait_before(attempt)
        waited.append(round(pause, 2))
        nap(pause)
    # Unreachable: every branch above returns on the last attempt. Kept so a
    # future edit to the loop cannot fall out of it returning None.
    return {"ok": False, "status": raw["status"] or 0, "text": "",
            "cause": "gave up after retries", "raw": raw,
            "attempts": tries, "waited": waited}


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


def chat(cred, model, messages, max_tokens=512, timeout=180, extra=None,
         tries=MOST_TRIES):
    """
    Production LLM call. Shares build_chat_request with the probe path.

    `tries=1` for a PROBE. A probe asks "what is this model doing right now",
    and retrying that question three times with backoff does not make the
    answer truer — it makes the answer late. Every failing model in the
    routing sweep was costing three attempts plus about seven seconds of
    sleeps, times two calls for the multimodal check, times every shelf: the
    owner watched CHECK EVERYTHING take over fifteen minutes, and this was a
    good part of why. See preflight._ModelCache.
    """
    url, headers, body = build_chat_request(cred, model["api_model_id"], messages, max_tokens, extra)
    res = _execute(cred, url, headers, body, timeout, tries=tries)
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


def probe_model(cred, model, timeout=120, tries=1):
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
                   max_tokens=5, timeout=timeout, tries=tries)
        caps["chat"] = bool(res["ok"])
        result = {"ok": res["ok"], "status": res["status"], "cause": res.get("cause"),
                  "output": res.get("output", ""), "raw": res["raw"]}
        if res["ok"] and caps.get("multimodal") is None:  # Vision = an LLM capability, detected here
            mm = [{"role": "user", "content": [
                {"type": "text", "text": "what colour is this? one word"},
                {"type": "image_url", "image_url": {"url": _TINY_PNG}}]}]
            caps["multimodal"] = bool(chat(cred, model, mm, max_tokens=5,
                                           timeout=timeout, tries=tries)["ok"])
        return {"capabilities": caps, "detect": diag, "test": result}

    if category == "IMAGE":
        url, headers, body = build_request(cred, "images/generations", {
            "model": model["api_model_id"], "prompt": "a small blue circle on a white background",
            "n": 1, "size": "256x256"})
        res = _execute(cred, url, headers, body, timeout, tries=tries)
        diag["endpoint"] = url
        return {"capabilities": {"images": bool(res["ok"])}, "detect": diag,
                "test": {"ok": res["ok"], "status": res["status"], "cause": res.get("cause"),
                         "output": "image generated" if res["ok"] else "", "raw": res["raw"]}}

    if category == "EMBEDDING":
        url, headers, body = build_request(cred, "embeddings", {
            "model": model["api_model_id"], "input": "ping"})
        res = _execute(cred, url, headers, body, timeout, tries=tries)
        diag["endpoint"] = url
        return {"capabilities": {"embeddings": bool(res["ok"])}, "detect": diag,
                "test": {"ok": res["ok"], "status": res["status"], "cause": res.get("cause"),
                         "output": "embedding returned" if res["ok"] else "", "raw": res["raw"]}}

    return {"capabilities": {"supported": None}, "detect": diag,
            "test": {"ok": None, "status": None, "output": "",
                     "cause": f"No live probe for {category}; leaving it as unknown.", "raw": diag}}


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
    from . import auth
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
    from . import auth
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
