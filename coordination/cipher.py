"""
cipher.py — `import sqlite3` that can open an encrypted website's cabinet.

The cabinet is opened in well over a hundred places, every one of them with a
plain `sqlite3.connect(...)`. Rewriting each to fetch a key would be a hundred
chances to miss one, and the one missed would be the one that quietly made a
new, UNENCRYPTED cabinet beside the real one.

So it is done once, here. When the open website's cabinet is encrypted,
`sqlite3` becomes SQLCipher — the same SQLite with encryption built in, and
the same Python interface — and every connection to a cabinet inside an
account is given that website's key before it reads a single page. Nothing
else in the program changes.

  NO ACCOUNT OPEN, OR AN UNENCRYPTED ONE   nothing is swapped at all. The
                                           owner's agency runs on the same
                                           sqlite3 it always has.
  SIGNED OUT (no key)                      opening the cabinet fails with a
                                           sentence saying so, not with
                                           "file is not a database".
  ANOTHER ACCOUNT'S CABINET                its key is not here, so it does not
                                           open — even on the same computer.

Installed first thing by the coordination, pipelines and providers packages,
before any of their modules imports sqlite3, so every module gets the same one.
"""
import os
import sys
import types
from pathlib import Path

_KEYS = {}          # (account, site) -> hex key, for this process


def _site_of(database):
    """(account, site) when this file is a website's cabinet inside an account, else None."""
    from . import workspace
    try:
        p = Path(os.fspath(database)).resolve()
        root = (workspace.data_root() / "accounts").resolve()
        rel = p.relative_to(root)
    except Exception:
        return None
    parts = rel.parts
    if len(parts) >= 4 and parts[1] == "sites":
        return parts[0], parts[2]
    return None


def key_for(database):
    """The hex key for this cabinet, None when it is not encrypted, or raises when locked."""
    from . import vault, workspace
    where = _site_of(database)
    if not where or not workspace.encrypted(where[0]):
        return None
    if where in _KEYS:
        return _KEYS[where]
    master = workspace.master_key(where[0])
    if master is None:
        raise vault.Locked("this website's data is locked — sign in on the office "
                           "screen first")
    _KEYS[where] = vault.cabinet_key(master, where[1])
    return _KEYS[where]


def needed() -> bool:
    """Is an account with encrypted cabinets open?"""
    from . import workspace
    a = workspace.active()
    return bool(a) and workspace.encrypted(a["account"])


def install() -> bool:
    """Make `import sqlite3` the cipher-capable one, when an encrypted cabinet is open."""
    current = sys.modules.get("sqlite3")
    if getattr(current, "_glasswork_cipher", False):
        return True
    if not needed():
        return False
    try:
        from sqlcipher3 import dbapi2 as real
    except ImportError:
        import sqlite3 as real                    # the plain one, refusing below
        missing = True
    else:
        missing = False

    shim = types.ModuleType("sqlite3")
    for name in dir(real):
        if not name.startswith("__"):
            setattr(shim, name, getattr(real, name))

    def connect(database, *args, **kwargs):
        from . import vault
        try:
            key = key_for(database)
        except vault.Locked as e:
            raise real.OperationalError(str(e)) from None
        if key and missing:
            raise real.OperationalError(
                "this website's data is encrypted and the encryption library is not "
                "installed — run: pip install -r requirements.txt")
        con = real.connect(database, *args, **kwargs)
        if key:
            # First statement on the connection, before anything reads a page.
            con.execute(f"PRAGMA key = \"x'{key}'\"")
        return con

    shim.connect = connect
    shim.dbapi2 = shim
    shim._glasswork_cipher = True
    sys.modules["sqlite3"] = shim
    sys.modules["sqlite3.dbapi2"] = shim
    return True


def plain_connect(database):
    """An UNENCRYPTED connection, from the cipher library — for copying a plain cabinet in."""
    from sqlcipher3 import dbapi2
    return dbapi2.connect(database)
