"""coordination — the shared filing cabinet and the turn-taking layer.

Making this a package lets other modules import the board and the cabinet cleanly
(`from coordination import board, cabinet`) while each file still runs as a script
(`python3 coordination/init_db.py`) via the defensive imports inside them.
"""

# FIRST, before any module here imports sqlite3: when the open website's
# cabinet is encrypted, `sqlite3` becomes the cipher-capable one, so every
# module gets the same one. Nothing changes until an encrypted account exists.
# See coordination/cipher.py.
try:
    from .cipher import install as _cipher_install
    _cipher_install()
except Exception:                                            # pragma: no cover
    pass

# The terminal is set up before anything in this package prints. On Windows the
# default codepage is cp1252/cp1256, which has no ● and no ✕ — and this program
# prints both. See coordination/console.py.
try:
    from .console import setup as _console_setup
    _console_setup()
except Exception:                                            # pragma: no cover
    pass

# Every request any desk makes to HIS website is paced and stops when the site
# says stop — his host's firewall blocked his own address once, because the
# agency read the site from three desks at once. See coordination/sitepace.py.
try:
    from .sitepace import install as _pace_install
    _pace_install()
except Exception:                                            # pragma: no cover
    pass
