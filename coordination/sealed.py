"""
sealed.py — envelopes only one computer can open.

    "i want the Users to be stored on my pc in program storage not on my
     website hosting ... that should be highly encrypted that no one should
     decode it even AI too"

A copy of the program seals what it sends — a problem report, a registration,
"this copy is in use" — with the publisher's PUBLIC key, which every copy
carries. Only his PRIVATE key, which never leaves his computer, opens it. His
website only ever holds the sealed envelope while it waits to be collected:
the website cannot read it, nor can anyone who gets into the hosting, nor any
copy of the program, nor anyone who reads every line of this code. His answer
goes back the same way, sealed to the person's own copy.

HOW
  X25519 (a fresh throwaway key for every envelope) agrees a secret with the
  recipient's public key; HKDF-SHA256 turns it into a 256-bit key; AES-256-GCM
  seals the contents and refuses to open anything that was changed by a single
  bit. Nobody can open an envelope without the private key — not by guessing,
  not with any computer or AI there is: the key is 2^255 possibilities.

  An envelope is text: "gw1." then base64url(throwaway public key | nonce |
  sealed contents). Nothing about who sent it or what it says is readable.

The publisher's own records (the people using his program and their issues)
are kept on his computer in one file, locked with a key made from the same
private key (lock/unlock below).
"""
import base64
import json
import zlib

MAGIC = "gw1."
INFO = b"glasswork sealed v1"
STORE_INFO = b"glasswork publisher records v1"
STORE_MAGIC = b"GLASSWORK-RECORDS-1\n"
OPENED_MAX = 4 * 1024 * 1024        # what one envelope may unpack to


def _b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _b64d(s: str) -> bytes:
    s = str(s or "").strip()
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _raw(k) -> bytes:
    """A key given as base64 text (either alphabet) or as bytes."""
    if isinstance(k, (bytes, bytearray)):
        return bytes(k)
    s = str(k or "").strip().replace("-", "+").replace("_", "/")
    return base64.b64decode(s + "=" * (-len(s) % 4))


def keypair() -> tuple:
    """(private, public) as base64 text. The private half is kept; the public half is handed out."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    k = X25519PrivateKey.generate()
    priv = k.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    pub = k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(priv).decode(), base64.b64encode(pub).decode()


def public_of(private) -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
    k = X25519PrivateKey.from_private_bytes(_raw(private))
    return base64.b64encode(k.public_key().public_bytes(serialization.Encoding.Raw,
                                                        serialization.PublicFormat.Raw)).decode()


def _key(shared: bytes, eph_pub: bytes, to_pub: bytes) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=eph_pub + to_pub, info=INFO).derive(shared)


def seal(public, obj) -> str:
    """Seal anything JSON can hold so that only the holder of the private key can open it."""
    import os
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    to = _raw(public)
    if len(to) != 32:
        raise ValueError("that is not a sealing key")
    eph = X25519PrivateKey.generate()
    eph_pub = eph.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    shared = eph.exchange(X25519PublicKey.from_public_bytes(to))
    nonce = os.urandom(12)
    body = zlib.compress(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 6)
    box = AESGCM(_key(shared, eph_pub, to)).encrypt(nonce, body, INFO)
    return MAGIC + _b64e(eph_pub + nonce + box)


def open_(private, envelope):
    """What was sealed — or ValueError if it is not for this key, or was changed on the way."""
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    text = str(envelope or "").strip()
    if not text.startswith(MAGIC):
        raise ValueError("not a sealed envelope")
    try:
        raw = _b64d(text[len(MAGIC):])
        eph_pub, nonce, box = raw[:32], raw[32:44], raw[44:]
        k = X25519PrivateKey.from_private_bytes(_raw(private))
        from cryptography.hazmat.primitives import serialization
        mine = k.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        shared = k.exchange(X25519PublicKey.from_public_bytes(eph_pub))
        body = AESGCM(_key(shared, eph_pub, mine)).decrypt(nonce, box, INFO)
        return json.loads(_inflate(body).decode("utf-8"))
    except Exception as e:
        raise ValueError(f"the envelope would not open ({type(e).__name__})") from None


def _inflate(body: bytes, limit=OPENED_MAX) -> bytes:
    # Anyone may seal TO the public key, so an envelope could be a small file
    # that unpacks to gigabytes. It is refused at the limit instead.
    d = zlib.decompressobj()
    out = d.decompress(body, limit + 1)
    if len(out) > limit or d.unconsumed_tail:
        raise ValueError("larger than an envelope may be")
    return out


# ---------------------------------------------------------------------------
# the publisher's records, locked on his computer
# ---------------------------------------------------------------------------

def store_key(private) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=STORE_INFO).derive(_raw(private))


def lock(key: bytes, obj) -> bytes:
    import os
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(12)
    body = zlib.compress(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 6)
    return STORE_MAGIC + base64.b64encode(nonce + AESGCM(key).encrypt(nonce, body, STORE_MAGIC)) + b"\n"


def unlock(key: bytes, data: bytes):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    if not data.startswith(STORE_MAGIC):
        raise ValueError("not a locked records file")
    try:
        raw = base64.b64decode(data[len(STORE_MAGIC):].strip())
        body = AESGCM(key).decrypt(raw[:12], raw[12:], STORE_MAGIC)
        return json.loads(zlib.decompress(body).decode("utf-8"))
    except Exception as e:
        raise ValueError(f"the records would not open ({type(e).__name__})") from None
