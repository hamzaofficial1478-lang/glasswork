"""pipelines — cross-sector work products (e.g. the outreach email composer)."""

# The coordination package first: it decides which sqlite3 every module gets
# (coordination/cipher.py), before any pipeline imports one.
try:
    import coordination  # noqa: F401
except Exception:                                            # pragma: no cover
    pass

# The terminal is set up before anything in this package prints. On Windows the
# default codepage is cp1252/cp1256, which has no ● and no ✕ — and this program
# prints both. See coordination/console.py.
try:
    from coordination.console import setup as _console_setup
    _console_setup()
except Exception:                                            # pragma: no cover
    pass
