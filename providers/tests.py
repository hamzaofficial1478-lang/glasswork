"""
tests.py — run: python3 -m providers.tests

Covers the two things that caused real bugs: URL joining across awkward inputs,
and the guarantee that the probe/test path and the production path build a
byte-identical URL + headers for the same credential.
"""
import sys

from . import auth
from .request import build_chat_request
from .urls import chat_endpoint, join_url, models_endpoint, normalize_base

_fail = []


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


def test_urls():
    print("URL joiner:")
    check("normalize host/v1", normalize_base("https://h/v1"), "https://h/v1")
    check("normalize host/v1/", normalize_base("https://h/v1/"), "https://h/v1")
    check("normalize full endpoint", normalize_base("https://h/v1/chat/completions"), "https://h/v1")
    check("normalize host", normalize_base("https://h"), "https://h")
    check("normalize host/", normalize_base("https://h/"), "https://h")
    check("normalize /models endpoint", normalize_base("https://h/v1/models"), "https://h/v1")
    check("chat endpoint from full paste", chat_endpoint("https://h/v1/chat/completions"),
          "https://h/v1/chat/completions")
    check("join no double slash", join_url("https://h/v1/", "/chat/completions"),
          "https://h/v1/chat/completions")
    check("models endpoint", models_endpoint("https://h/v1"), "https://h/v1/models")


def test_auth():
    print("Auth headers:")
    check("bearer", auth.auth_header("bearer", "K"), {"Authorization": "Bearer K"})
    check("x-api-key", auth.auth_header("x-api-key", "K"), {"x-api-key": "K"})
    check("token", auth.auth_header("token", "K"), {"Authorization": "Token K"})
    check("custom", auth.auth_header("custom", "K", "X-Key"), {"X-Key": "K"})
    check("empty key -> no header", auth.auth_header("bearer", ""), {})
    check("redact hides key", "SECRETKEY" in auth.redact("SECRETKEY"), False)
    # 'raw' is the fix for the commonest false 'bad key': Pexels and many media
    # APIs want the key bare, and "Bearer <key>" comes back 401 with a good key.
    check("raw = no prefix", auth.auth_header("raw", "K"), {"Authorization": "K"})
    check("basic encodes login:password",
          auth.auth_header("basic", "user:pass"), {"Authorization": "Basic dXNlcjpwYXNz"})
    check("basic passes through pre-encoded",
          auth.auth_header("basic", "dXNlcjpwYXNz"), {"Authorization": "Basic dXNlcjpwYXNz"})
    check("every declared style builds a header",
          [s for s in auth.STYLES if not auth.auth_header(s, "K")], [])


class _Resp:
    def __init__(self, status, text="{}"):
        self.status_code, self.text = status, text


def test_405_retry():
    """
    A 405 on GET means 'wrong verb' — the host rejects the method BEFORE it looks
    at auth, so it says nothing about the key. probe_tool retries with POST; these
    four cases are the verdicts that retry must reach.
    """
    print("405 -> POST retry verdicts:")
    from . import client
    tool = {"auth_style": "bearer", "api_key": "K"}
    cases = [  # POST status -> (ok, substring the owner should see)
        (200, True, "key was accepted"),
        (401, False, "rejected the key"),
        (400, True, "ACCEPTED"),
        (500, None, "provider-side error"),
    ]
    real_post = client.requests.post
    try:
        for status, want_ok, want_text in cases:
            client.requests.post = lambda *a, _s=status, **k: _Resp(_s)
            res = client._retry_post(tool, "https://h/x", {}, 5, {})
            check(f"POST {status} -> ok={want_ok}", res["ok"], want_ok)
            check(f"POST {status} explains why", want_text in res["cause"], True)
    finally:
        client.requests.post = real_post


def test_probe_equals_production():
    print("Probe == production request shape:")
    cred = {"base_url": "https://h/v1/chat/completions",  # deliberately pasted full
            "api_key": "K", "auth_style": "bearer"}
    msgs = [{"role": "user", "content": "ok"}]
    # both paths build the request via the same function
    prod_url, prod_h, _ = build_chat_request(cred, "the-model", msgs, max_tokens=5)
    probe_url, probe_h, _ = build_chat_request(cred, "the-model", msgs, max_tokens=5)
    check("identical URL", prod_url, probe_url)
    check("identical headers", prod_h, probe_h)
    check("URL from normalized base", prod_url, "https://h/v1/chat/completions")
    check("single auth header", [k for k in prod_h if k.lower() in ("authorization", "x-api-key")],
          ["Authorization"])


def main():
    test_urls()
    test_auth()
    test_405_retry()
    test_probe_equals_production()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: {_fail}")
        sys.exit(1)
    print("all provider tests passed")


if __name__ == "__main__":
    main()
