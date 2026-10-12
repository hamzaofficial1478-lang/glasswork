"""
totp.py — the six-digit codes of an authenticator app (RFC 6238), for
two-step sign-in.

    secret = new_secret()                 # shown once, typed into the app
    ok, step = check(secret, "123456")    # this 30-second code, or the one either side

The app and this file share a secret; each 30 seconds both turn it and the
time into the same six digits. A stolen password alone no longer signs anyone
in. The step of a code that worked is kept, so the same code cannot be used
twice.
"""
import base64
import hashlib
import hmac
import secrets
import struct
import time
import urllib.parse

STEP = 30


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii")


def code_at(secret: str, step: int) -> str:
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    mac = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    n = struct.unpack(">I", mac[mac[-1] & 15:][:4])[0] & 0x7FFFFFFF
    return f"{n % 1_000_000:06d}"


def check(secret: str, code, last_step=0, now=None) -> tuple:
    """(True, step) when the code is right for now (or 30 seconds either side) and not used before."""
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(code) != 6 or not secret:
        return False, 0
    here = int((now if now is not None else time.time()) // STEP)
    for step in (here - 1, here, here + 1):
        if step > int(last_step or 0) and hmac.compare_digest(code_at(secret, step), code):
            return True, step
    return False, 0


def uri(secret: str, label: str, issuer="") -> str:
    """What an authenticator app reads: otpauth://totp/… The issuer is the program's name."""
    if not issuer:
        try:
            from . import product
            issuer = product.name()
        except Exception:
            issuer = "Botix"
    return (f"otpauth://totp/{urllib.parse.quote(issuer)}:{urllib.parse.quote(label)}"
            f"?secret={secret}&issuer={urllib.parse.quote(issuer)}&period={STEP}&digits=6")
