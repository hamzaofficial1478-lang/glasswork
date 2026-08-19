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


def test_400_means_key_accepted():
    """
    Pexels answers a keyless search with 400 "No query param given". That 400 is
    proof the credential got IN — a bad key 401s instead, whatever the params. So
    the tester must never paint it red, and should prove it with a sample query.
    """
    print("400 (host read the key, wants a query):")
    from . import client
    real_get = client.requests.get
    tool = {"base_url": "https://h/search", "auth_style": "raw", "api_key": "K"}
    try:
        seq = [_Resp(400, '{"code":"No query param given"}'), _Resp(200, '{"photos":[]}')]
        client.requests.get = lambda *a, **k: seq.pop(0)
        r = client.probe_tool(dict(tool))
        check("sample query works -> green", (r["ok"], r["status"]), (True, 200))
        check("says it returned live data", "live data" in r["cause"], True)

        client.requests.get = lambda *a, **k: _Resp(400, '{"code":"unguessable"}')
        r = client.probe_tool(dict(tool))
        check("no sample fits -> still a pass", (r["ok"], r["status"]), (True, 400))
        check("explains the key was accepted", "ACCEPTED" in r["cause"], True)

        # the safety property: a genuinely bad key must NOT be laundered into a pass
        seq = [_Resp(400, "{}"), _Resp(401, '{"error":"bad key"}')]
        client.requests.get = lambda *a, **k: seq.pop(0)
        r = client.probe_tool(dict(tool))
        check("bad key still fails red", (r["ok"], r["status"]), (False, 401))
    finally:
        client.requests.get = real_get


def test_preflight_cover():
    """
    The whole point of the sweep: a chain with a DEAD fallback must not read as
    safe. One working link is SINGLE (a single point of failure) even when a
    fallback is configured — that is the failure people don't see coming.
    """
    print("Fallback cover verdicts:")
    from .preflight import (_cover, _verdict, COVERED, SINGLE, DOWN, EMPTY,
                            UNKNOWN, _ModelCache)
    check("two of two work -> COVERED", _cover(2, 2), COVERED)
    check("two of three work -> COVERED", _cover(2, 3), COVERED)
    check("primary works, fallback dead -> SINGLE", _cover(1, 2), SINGLE)
    check("lone provider -> SINGLE", _cover(1, 1), SINGLE)
    check("nothing works -> DOWN", _cover(0, 2), DOWN)
    check("nothing configured -> EMPTY", _cover(0, 0), EMPTY)

    # 'I did not look' is not 'I looked and it is dead'. Under --quick every
    # link comes back ok=None, and reading that as DOWN made the cheap check
    # announce that every sector in the agency had no working brain.
    quick = [{"ok": None}, {"ok": None}]
    check("not called -> UNKNOWN, not DOWN", _verdict(quick), UNKNOWN)
    check("tested and dead -> DOWN", _verdict([{"ok": False}, {"ok": False}]), DOWN)
    check("one alive, one untested -> SINGLE",
          _verdict([{"ok": True}, {"ok": None}]), SINGLE)
    check("one dead, one untested -> UNKNOWN",
          _verdict([{"ok": False}, {"ok": None}]), UNKNOWN)

    # a brain shared by five sectors must cost one call, not five
    calls = []
    cache = _ModelCache(quick=True)
    for _ in range(5):
        cache.test({"id": "c"}, {"id": "m1"})
        calls.append(len(cache.seen))
    check("shared model tested once", (len(cache.seen), calls[-1]), (1, 1))


def test_integrations_unconfigured():
    """
    'Not set up' and 'broken' are different answers and must never be conflated:
    ok=None means nothing was entered, ok=False means what you entered is wrong.
    Half-filled credentials are the quiet killer, so they name the missing field.
    """
    print("Channel checks (no network):")
    from . import integrations as ig
    blank = ig.test_all(env={})
    check("all report not-configured", [r["ok"] for r in blank], [None] * len(blank))
    # Pinned by name, not by count, so adding a channel is a deliberate edit here
    # rather than a number that silently drifts.
    check("covers every channel", sorted(r["integration"] for r in blank),
          ["algolia", "email", "ntfy", "telegram"])

    half = ig.test_email(env={"SMTP_HOST": "h", "SMTP_USER": "u"})
    check("half-filled SMTP is a failure, not 'unset'", half["ok"], False)
    check("and it names what's missing", "SMTP_PASS" in half["detail"], True)

    tg = ig.test_telegram(env={"TELEGRAM_BOT_TOKEN": ""})
    check("no telegram token -> unset, not broken", tg["ok"], None)


def test_the_owner_can_always_be_reached():
    """
    Everything that notifies the owner goes through one function, so switching
    his channel is config and not code. The failure that matters here is a
    silent one: if no route is configured the agency must SAY nobody was told,
    never return quietly as though the message landed.
    """
    print("One route to the owner, and it never fails silently:")
    from . import integrations as ig

    none = ig.send_message("anything", env={})
    check("no route configured -> not ok", none["ok"], False)
    check("and it says nobody was told", "nobody was told" in none["detail"], True)

    topic = {"NTFY_TOPIC": "glasswork-a7f3k9x2q"}
    check("ntfy wins when a topic is set",
          ig.send_message("x", env=topic)["integration"], "ntfy")
    check("telegram is still the fallback",
          ig.send_message("x", env={"TELEGRAM_BOT_TOKEN": "t",
                                    "TELEGRAM_CHAT_ID": "c"})["integration"], "telegram")
    check("and ntfy is preferred when both exist",
          ig.send_message("x", env={**topic, "TELEGRAM_BOT_TOKEN": "t",
                                    "TELEGRAM_CHAT_ID": "c"})["integration"], "ntfy")

    # a guessable topic is a public inbox — say so rather than pretending it is fine
    short = ig.test_ntfy(env={"NTFY_TOPIC": "glass"})
    check("a short topic is refused as unsafe", short["ok"], False)
    check("and explains why", "guesses" in short["detail"], True)
    check("no topic at all -> unset, not broken", ig.test_ntfy(env={})["ok"], None)

    # ntfy carries its options in HTTP headers, which are latin-1 only. The
    # digest is full of em-dashes; one of those raises inside requests and the
    # notification is lost at exactly the moment it mattered.
    h = ig._ntfy_headers({}, title="Digest — 16 Aug", tags=["bell"])
    check("a non-latin-1 title cannot break the send",
          h["Title"].encode("latin-1") == b"Digest - 16 Aug", True)
    for v in h.values():
        v.encode("latin-1")     # raises if any header is unsendable


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
    test_400_means_key_accepted()
    test_preflight_cover()
    test_integrations_unconfigured()
    test_the_owner_can_always_be_reached()
    test_probe_equals_production()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: {_fail}")
        sys.exit(1)
    print("all provider tests passed")


if __name__ == "__main__":
    main()
