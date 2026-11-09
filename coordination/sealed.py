"""
sealed.py — envelopes only one computer can open.

A copy of the program seals a problem report with the publisher's PUBLIC key,
which every copy carries. Only his PRIVATE key, which never leaves his computer,
opens it. His website holds the sealed envelope only while it waits to be
collected: the website cannot read it, nor can anyone who gets into the
hosting, nor another copy of the program, nor anyone who reads this code. His
answer comes back the same way, sealed to a key made for that one report.

HOW
  X25519 (a fresh throwaway key for every envelope) agrees a secret with the
  recipient's public key; HKDF-SHA256 turns it into a 256-bit key; AES-256-GCM
  seals the contents and refuses to open anything changed by a single bit.

  An envelope is text: "gw1." then base64url(throwaway public key | nonce |
  sealed contents). Nothing about who sent it or what it says is readable.

The publisher's own records (the reports and his answers) are kept on his
computer in one file, locked with a key made from the same private key
(lock/unlock below).
"""
import base64
import json
import os
import zlib

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAGIC = "gw1."
INFO = b"glasswork sealed v1"
STORE_INFO = b"glasswork publisher records v1"
STORE_MAGIC = b"GLASSWORK-RECORDS-1\n"
OPENED_MAX = 4 * 1024 * 1024        # what one envelope may unpack to
RAW = (serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _raw(k) -> bytes:
    """A key given as base64 text (either alphabet) or as bytes."""
    if isinstance(k, (bytes, bytearray)):
        return bytes(k)
    s = str(k or "").strip().replace("-", "+").replace("_", "/")
    return base64.b64decode(s + "=" * (-len(s) % 4))


def _pack(obj) -> bytes:
    return zlib.compress(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 6)


def _unpack(body: bytes, limit=OPENED_MAX):
    # Anyone may seal TO the public key, so an envelope could be a small file
    # that unpacks to gigabytes. It is refused at the limit instead.
    d = zlib.decompressobj()
    out = d.decompress(body, limit + 1)
    if len(out) > limit or d.unconsumed_tail:
        raise ValueError("larger than an envelope may be")
    return json.loads(out.decode("utf-8"))


def _hkdf(secret: bytes, salt, info: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=info).derive(secret)


def keypair() -> tuple:
    """(private, public) as base64 text. The private half is kept; the public half is handed out."""
    k = X25519PrivateKey.generate()
    priv = k.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    return base64.b64encode(priv).decode(), base64.b64encode(k.public_key().public_bytes(*RAW)).decode()


def public_of(private) -> str:
    return base64.b64encode(X25519PrivateKey.from_private_bytes(_raw(private)).public_key().public_bytes(*RAW)).decode()


def seal(public, obj) -> str:
    """Seal anything JSON can hold so that only the holder of the private key can open it."""
    to = _raw(public)
    if len(to) != 32:
        raise ValueError("that is not a sealing key")
    eph = X25519PrivateKey.generate()
    eph_pub = eph.public_key().public_bytes(*RAW)
    key = _hkdf(eph.exchange(X25519PublicKey.from_public_bytes(to)), eph_pub + to, INFO)
    nonce = os.urandom(12)
    raw = eph_pub + nonce + AESGCM(key).encrypt(nonce, _pack(obj), INFO)
    return MAGIC + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def open_(private, envelope):
    """What was sealed — or ValueError if it is not for this key, or was changed on the way."""
    text = str(envelope or "").strip()
    if not text.startswith(MAGIC):
        raise ValueError("not a sealed envelope")
    try:
        body = text[len(MAGIC):]
        raw = base64.urlsafe_b64decode(body + "=" * (-len(body) % 4))
        eph_pub, nonce, box = raw[:32], raw[32:44], raw[44:]
        k = X25519PrivateKey.from_private_bytes(_raw(private))
        key = _hkdf(k.exchange(X25519PublicKey.from_public_bytes(eph_pub)), eph_pub + k.public_key().public_bytes(*RAW), INFO)
        return _unpack(AESGCM(key).decrypt(nonce, box, INFO))
    except Exception as e:
        raise ValueError(f"the envelope would not open ({type(e).__name__})") from None


# ---------------------------------------------------------------------------
# the publisher's records, locked on his computer
# ---------------------------------------------------------------------------

def store_key(private) -> bytes:
    return _hkdf(_raw(private), None, STORE_INFO)


def lock(key: bytes, obj) -> bytes:
    nonce = os.urandom(12)
    return STORE_MAGIC + base64.b64encode(nonce + AESGCM(key).encrypt(nonce, _pack(obj), STORE_MAGIC)) + b"\n"


def unlock(key: bytes, data: bytes):
    if not data.startswith(STORE_MAGIC):
        raise ValueError("not a locked records file")
    try:
        raw = base64.b64decode(data[len(STORE_MAGIC):].strip())
        return _unpack(AESGCM(key).decrypt(raw[:12], raw[12:], STORE_MAGIC), limit=1 << 30)
    except Exception as e:
        raise ValueError(f"the records would not open ({type(e).__name__})") from None
