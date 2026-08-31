"""
retry_tests.py — run: python -m providers.retry_tests

The promise: a transient error costs a few seconds, not a sector's day.

The owner's question was "if an api module hit error there is no time
increasing thing?" — and there was not. One HTTP call per model, and a 429,
which means 'come back in a moment', permanently burned that model and moved
the cascade on. Primary and fallback on the same key both died to one rate
limit in the same second, and the sector reported that every provider had
failed.

Nothing here touches the network. A stub stands in for requests.post, so the
tests can say exactly what the provider did and check exactly what we did back,
and the sleeps are recorded rather than slept.
"""
from . import client

_fail = []


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


class _Reply:
    def __init__(self, status, text="{}", headers=None):
        self.status_code = status
        self.text = text
        self.headers = headers or {}


class _Provider:
    """Answers with a scripted sequence, and remembers how often it was asked."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = 0

    def __call__(self, url, headers=None, data=None, timeout=None):
        self.calls += 1
        r = self.replies[min(self.calls - 1, len(self.replies) - 1)]
        if isinstance(r, Exception):
            raise r
        return r


class _Bench:
    """Swap in the stub provider and a sleep that only takes notes."""

    def __init__(self, provider):
        self.provider = provider
        self.slept = []

    def __enter__(self):
        self.real = client.requests.post
        client.requests.post = self.provider
        return self

    def __exit__(self, *a):
        client.requests.post = self.real

    def run(self, tries=client.MOST_TRIES, timeout=5):
        return client._execute({"name": "test", "api_key": "sk-secret"},
                               "https://example.test/v1/chat/completions",
                               {"Authorization": "Bearer sk-secret"},
                               '{"model":"m"}', timeout,
                               tries=tries, sleep=self.slept.append)


CRED = {"name": "test", "api_key": "sk-secret"}


def test_a_rate_limit_is_retried_not_burned():
    print("\n429 means 'come back shortly', so we come back:")
    p = _Provider(_Reply(429), _Reply(429), _Reply(200, '{"choices":[{"message":{"content":"hi"}}]}'))
    with _Bench(p) as b:
        res = b.run()
    check("asked three times", p.calls, 3)
    check("and got there in the end", res["ok"], True)
    check("the attempt count is reported", res["attempts"], 3)
    check("two waits, not three", len(b.slept), 2)
    check("and each wait is longer than the last", b.slept[1] > b.slept[0], True)


def test_the_waits_grow():
    print("\nThe time-increasing thing:")
    p = _Provider(_Reply(503))
    with _Bench(p) as b:
        b.run(tries=4)
    check("three waits before giving up on the fourth try", len(b.slept), 3)
    check("roughly doubling", b.slept[2] > b.slept[1] > b.slept[0], True)
    check("and the first is about a second", 0.7 <= b.slept[0] <= 1.6, True)


def test_the_provider_s_own_retry_after_wins():
    print("\nThe provider knows better than any formula here:")
    p = _Provider(_Reply(429, headers={"Retry-After": "7"}), _Reply(200))
    with _Bench(p) as b:
        b.run()
    check("waited exactly what it asked for", b.slept, [7.0])


def test_an_absurd_retry_after_is_capped():
    print("\nBut a provider asking for an hour does not get one:")
    p = _Provider(_Reply(429, headers={"Retry-After": "3600"}), _Reply(200))
    with _Bench(p) as b:
        b.run()
    check("capped", b.slept, [client.LONGEST_WAIT])


def test_a_nonsense_retry_after_falls_back_to_our_own_wait():
    print("\nAnd a header we cannot read is not a crash:")
    p = _Provider(_Reply(429, headers={"Retry-After": "soon-ish"}), _Reply(200))
    with _Bench(p) as b:
        b.run()
    check("one ordinary wait", len(b.slept), 1)
    check("of about a second", 0.7 <= b.slept[0] <= 1.6, True)


def test_a_wrong_key_is_not_retried():
    print("\nTrying a wrong key three times is three ways of being wrong:")
    for status in (400, 401, 402, 403, 404, 422):
        p = _Provider(_Reply(status))
        with _Bench(p) as b:
            res = b.run()
        check(f"{status} asked once", p.calls, 1)
        check(f"{status} never slept", b.slept, [])
        check(f"{status} and the cause still explains itself",
              bool(res["cause"]), True)


def test_a_timeout_is_retried():
    print("\nA slow moment is not a dead model:")
    import requests as _r
    p = _Provider(_r.exceptions.Timeout(), _Reply(200))
    with _Bench(p) as b:
        res = b.run()
    check("tried again", p.calls, 2)
    check("and succeeded", res["ok"], True)


def test_a_connection_reset_is_retried_but_a_bad_url_is_not():
    print("\nOnly the errors that another go could fix:")
    p = _Provider(Exception("Connection reset by peer"), _Reply(200))
    with _Bench(p) as b:
        res = b.run()
    check("reset: tried again", p.calls, 2)
    check("reset: succeeded", res["ok"], True)

    p2 = _Provider(Exception("Invalid URL 'nonsense': No scheme supplied"))
    with _Bench(p2) as b2:
        b2.run()
    check("bad URL: asked once", p2.calls, 1)
    check("bad URL: never slept", b2.slept, [])


def test_it_gives_up_and_says_what_it_tried():
    print("\nWhen it really is down, the report is honest about the effort:")
    p = _Provider(_Reply(429))
    with _Bench(p) as b:
        res = b.run()
    check("three attempts", res["attempts"], 3)
    check("failed", res["ok"], False)
    check("the waits are on the record", len(res["waited"]), 2)
    check("and the cause is the provider's, not ours",
          res["cause"], "429 — rate limited or out of credit.")


def test_the_key_never_appears_in_the_diagnostic():
    print("\nA retry trail must not leak the key:")
    p = _Provider(_Reply(429))
    with _Bench(p) as b:
        res = b.run()
    check("redacted", "sk-secret" in repr(res["raw"]["headers"]), False)


def test_one_attempt_is_still_allowed():
    print("\nAnd a caller that wants a single shot gets one:")
    p = _Provider(_Reply(429))
    with _Bench(p) as b:
        res = b.run(tries=1)
    check("asked once", p.calls, 1)
    check("no waits", b.slept, [])
    check("reported as one attempt", res["attempts"], 1)


def main():
    for fn in list(globals().values()):
        if callable(fn) and getattr(fn, "__name__", "").startswith("test_"):
            fn()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        raise SystemExit(1)
    print("retry: all good — a transient error costs seconds, not the day.")


if __name__ == "__main__":
    main()
