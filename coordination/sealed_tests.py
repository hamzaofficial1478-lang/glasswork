"""
sealed_tests.py — run: python -m coordination.sealed_tests   (python3 on macOS/Linux)

    "that should be highly encrypted that no one should decode it even AI too"

What has to hold for an envelope sealed to the publisher:

  only his private key opens it; any other key is refused
  a single changed character is refused, not half-read
  the envelope shows nothing of what is inside, and no two look alike
  a small envelope that would unpack to something huge is refused
  his records file opens with his key alone
"""
import base64
import json
import zlib

from . import sealed

_fail = []


def check(name, got, want):
    if got == want:
        print(f"  ok  {name}")
    else:
        print(f"FAIL  {name}\n        got:  {got!r}\n        want: {want!r}")
        _fail.append(name)


def refused(fn) -> bool:
    try:
        fn()
        return False
    except ValueError:
        return True


def test_only_his_key_opens_it():
    print("\nOnly the private key opens an envelope:")
    priv, pub = sealed.keypair()
    report = {"kind": "report", "title": "Leads will not open", "email": "aisha@example.com",
              "text": "It spins — ثم يتوقف", "support": "x" * 200000}
    env = sealed.seal(pub, report)
    check("it opens with his key, word for word", sealed.open_(priv, env), report)
    other, _ = sealed.keypair()
    check("any other key is refused", refused(lambda: sealed.open_(other, env)), True)
    check("the public key alone cannot open it", refused(lambda: sealed.open_(pub, env)), True)
    check("the public key is the private key's own", sealed.public_of(priv), pub)


def test_nothing_shows_and_nothing_changes():
    print("\nAn envelope shows nothing, and cannot be changed on the way:")
    priv, pub = sealed.keypair()
    msg = {"title": "Leads will not open", "email": "aisha@example.com"}
    a, b = sealed.seal(pub, msg), sealed.seal(pub, msg)
    raw = base64.urlsafe_b64decode(a[4:] + "=" * (-len(a[4:]) % 4))
    check("nothing inside can be read from it", any(w in raw for w in (b"Leads", b"aisha", b"example")), False)
    check("  nor from the text of it", "aisha" in a or "Leads" in a, False)
    check("the same words sealed twice look nothing alike", a != b, True)
    i = len(a) // 2
    bent = a[:i] + ("A" if a[i] != "A" else "B") + a[i + 1:]
    check("one changed character is refused", refused(lambda: sealed.open_(priv, bent)), True)
    check("a cut-short envelope is refused", refused(lambda: sealed.open_(priv, a[:-10])), True)
    check("something that is not an envelope is refused", refused(lambda: sealed.open_(priv, "hello")), True)


def test_a_bomb_is_refused():
    print("\nA small envelope that would unpack to something huge is refused:")
    import os
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    priv, pub = sealed.keypair()
    to = base64.b64decode(pub)
    eph = X25519PrivateKey.generate()
    eph_pub = eph.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    key = sealed._key(eph.exchange(X25519PublicKey.from_public_bytes(to)), eph_pub, to)
    nonce = os.urandom(12)
    body = zlib.compress(json.dumps({"x": "a" * (sealed.OPENED_MAX + 10)}).encode(), 9)
    env = sealed.MAGIC + sealed._b64e(eph_pub + nonce + AESGCM(key).encrypt(nonce, body, sealed.INFO))
    check(f"a {len(env) // 1024} KB envelope that unpacks past {sealed.OPENED_MAX // 1048576} MB is refused",
          refused(lambda: sealed.open_(priv, env)), True)


def test_his_records():
    print("\nHis records file opens with his key alone:")
    priv, _ = sealed.keypair()
    other, _ = sealed.keypair()
    rec = {"copies": {"abc": {"person": {"name": "Aisha Khan"}}}}
    data = sealed.lock(sealed.store_key(priv), rec)
    check("it opens with his key", sealed.unlock(sealed.store_key(priv), data), rec)
    check("  and with no other", refused(lambda: sealed.unlock(sealed.store_key(other), data)), True)
    check("  and no name can be read from the file", b"Aisha" in data, False)


def main():
    for t in (test_only_his_key_opens_it, test_nothing_shows_and_nothing_changes, test_a_bomb_is_refused,
              test_his_records):
        t()
    print()
    if _fail:
        print(f"{len(_fail)} FAILED: " + ", ".join(_fail))
        return 1
    print("sealed: all good — only his key opens what the copies send.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
