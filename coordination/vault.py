"""
vault.py — the lock on each person's data.

"make the program end to end encrypted for each user so the data of each user
 could not be used by other user using the same program on local host"

HOW THE KEYS FIT TOGETHER

Each account has one random master key. It is never written down as it is.
It is stored twice, LOCKED: once with a key made from the password, once with
a key made from the recovery key. Either opens it; neither is stored anywhere.

    password ──scrypt──▶ key A ──AES-GCM──▶ master key, locked copy 1
    recovery ──scrypt──▶ key B ──AES-GCM──▶ master key, locked copy 2

From the master key every website gets its own cabinet key, and the account's
settings and files get their own file key (HKDF), so no key is used for two
jobs. Changing the password relocks copy 1 and touches nothing else, which is
why it is instant and loses nothing.

WHILE SIGNED IN the office (server.js) holds the master key in memory and hands
it to each program it starts, in AGENCY_KEY. SIGNED OUT nobody has it: the
cabinet and the settings are unreadable to other people on the computer, and
to anyone who copies the folder.

THE PRICE, SAID PLAINLY. Forget the password and lose the recovery key, and
the data cannot be opened by anyone, including us. That is what makes it real
encryption rather than a password screen.
"""
import base64
import hashlib
import hmac
import json
import os
import re
import secrets

MAGIC = b"GLASSWORK-SEALED-1\n"
MIN_PASSWORD = 8

# scrypt at 32 MiB of memory per guess: a few tenths of a second to sign in,
# and very expensive for anyone trying passwords against a copied folder.
SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1}
_MAXMEM = 64 * 1024 * 1024


class Locked(Exception):
    """The data is encrypted and the key to it is not here."""


def _aes():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as e:                              # pragma: no cover
        raise Locked("the encryption library is missing — run: "
                     "pip install -r requirements.txt") from e
    return AESGCM


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _unb64(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def _stretch(secret: str, salt: bytes, params=None) -> bytes:
    p = params or SCRYPT
    return hashlib.scrypt(secret.encode("utf-8"), salt=salt, n=int(p["n"]),
                          r=int(p["r"]), p=int(p["p"]), maxmem=_MAXMEM, dklen=32)


def _lock(kek: bytes, master: bytes, aad: bytes) -> dict:
    nonce = secrets.token_bytes(12)
    return {"nonce": _b64(nonce), "box": _b64(_aes()(kek).encrypt(nonce, master, aad))}


def _open(kek: bytes, locked: dict, aad: bytes):
    try:
        return _aes()(kek).decrypt(_unb64(locked["nonce"]), _unb64(locked["box"]), aad)
    except Exception:
        return None


def _hkdf(master: bytes, info: str) -> bytes:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
                info=info.encode("utf-8")).derive(master)


def check_of(master: bytes) -> str:
    """A fingerprint that says a key belongs to this account, without revealing it."""
    return _b64(hmac.new(master, b"glasswork key check", hashlib.sha256).digest())


# ---------------------------------------------------------------------------
# the recovery key
# ---------------------------------------------------------------------------

def new_recovery_key() -> str:
    """Twenty-four letters and digits in groups of four, easy to write down."""
    raw = base64.b32encode(secrets.token_bytes(15)).decode("ascii")   # 24 chars, no padding
    return "-".join(raw[i:i + 4] for i in range(0, 24, 4))


def _tidy_recovery(text: str) -> str:
    """What he typed, as the key: case, spaces and dashes do not matter."""
    t = re.sub(r"[\s-]+", "", (text or "")).upper()
    return t.replace("0", "O").replace("1", "I").replace("8", "B")


# ---------------------------------------------------------------------------
# making, opening and relocking an account's keys
# ---------------------------------------------------------------------------

def password_problem(password: str) -> str:
    """Why a new password will not do, or ''."""
    if len(password or "") < MIN_PASSWORD:
        return f"Use at least {MIN_PASSWORD} characters."
    return ""


def create(password: str, account_id: str):
    """(the keys to store on the account, the master key, the recovery key to show once)."""
    bad = password_problem(password)
    if bad:
        raise ValueError(bad)
    master = secrets.token_bytes(32)
    recovery = new_recovery_key()
    aad = account_id.encode("utf-8")
    keys = {"v": 1, "check": check_of(master)}
    for which, secret in (("password", password), ("recovery", _tidy_recovery(recovery))):
        salt = secrets.token_bytes(16)
        keys[which] = {"salt": _b64(salt), "scrypt": dict(SCRYPT),
                       **_lock(_stretch(secret, salt), master, aad)}
    return keys, master, recovery


def open_with(keys: dict, which: str, secret: str, account_id: str):
    """The master key, or None when the password (or recovery key) is wrong."""
    box = (keys or {}).get(which)
    if not box:
        return None
    if which == "recovery":
        secret = _tidy_recovery(secret)
    master = _open(_stretch(secret or "", _unb64(box["salt"]), box.get("scrypt")), box,
                   account_id.encode("utf-8"))
    if master is None or not hmac.compare_digest(check_of(master), keys.get("check", "")):
        return None
    return master


def relock(keys: dict, master: bytes, which: str, secret: str, account_id: str) -> dict:
    """The same master key under a new password (or a new recovery key)."""
    if which == "password":
        bad = password_problem(secret)
        if bad:
            raise ValueError(bad)
    else:
        secret = _tidy_recovery(secret)
    salt = secrets.token_bytes(16)
    out = dict(keys)
    out[which] = {"salt": _b64(salt), "scrypt": dict(SCRYPT),
                  **_lock(_stretch(secret, salt), master, account_id.encode("utf-8"))}
    return out


# ---------------------------------------------------------------------------
# what the master key opens
# ---------------------------------------------------------------------------

def cabinet_key(master: bytes, site_id: str) -> str:
    """One website's cabinet key, as the hex SQLCipher takes."""
    return _hkdf(master, "glasswork cabinet " + site_id).hex()


def file_key(master: bytes) -> bytes:
    return _hkdf(master, "glasswork files")


def is_sealed(data: bytes) -> bool:
    return data.startswith(MAGIC)


def seal(master: bytes, plain: bytes, where: str) -> bytes:
    """
    A settings or config file, encrypted. `where` is the file's place inside the
    account folder, bound in, so a sealed file moved somewhere else stops opening.
    Mirrored in server.js (sealText / unsealText) — same format both sides.
    """
    nonce = secrets.token_bytes(12)
    box = _aes()(file_key(master)).encrypt(nonce, plain, where.encode("utf-8"))
    return MAGIC + base64.b64encode(nonce + box) + b"\n"


def unseal(master: bytes, data: bytes, where: str) -> bytes:
    if not is_sealed(data):
        return data
    raw = base64.b64decode(data[len(MAGIC):].strip())
    try:
        return _aes()(file_key(master)).decrypt(raw[:12], raw[12:], where.encode("utf-8"))
    except Exception as e:
        raise Locked(f"{where} would not open with this account's key") from e


def master_from_env():
    """The master key the office handed this program, or None."""
    k = (os.environ.get("AGENCY_KEY") or "").strip()
    if not re.fullmatch(r"[0-9a-f]{64}", k):
        return None
    return bytes.fromhex(k)


def as_env(master: bytes) -> str:
    return master.hex()


def describe(keys: dict) -> str:
    return json.dumps({"v": keys.get("v"), "has": sorted(k for k in keys if k in ("password", "recovery"))})
