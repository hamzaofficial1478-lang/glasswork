"""pipelines — cross-sector work products (e.g. the outreach email composer)."""

# The terminal is set up before anything in this package prints. On Windows the
# default codepage is cp1252/cp1256, which has no ● and no ✕ — and this program
# prints both. See coordination/console.py.
try:
    from coordination.console import setup as _console_setup
    _console_setup()
except Exception:                                            # pragma: no cover
    pass
