"""coordination — the shared filing cabinet and the turn-taking layer.

Making this a package lets other modules import the board and the cabinet cleanly
(`from coordination import board, cabinet`) while each file still runs as a script
(`python3 coordination/init_db.py`) via the defensive imports inside them.
"""

# The terminal is set up before anything in this package prints. On Windows the
# default codepage is cp1252/cp1256, which has no ● and no ✕ — and this program
# prints both. See coordination/console.py.
try:
    from .console import setup as _console_setup
    _console_setup()
except Exception:                                            # pragma: no cover
    pass
