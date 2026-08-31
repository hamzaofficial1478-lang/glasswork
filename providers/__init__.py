"""
providers — a self-contained provider / API management module.

The rest of the app consumes AI through this clean interface and never builds an
API client itself:

    from providers import resolve_task, call_task
    call_task("md", [{"role": "user", "content": "…"}])

For work that must happen even when a sector's own model is down, use the
load ladder instead — it climbs to the shelf, and to the Director's model as a
last resort, and reports which rung answered:

    from providers import think, spread
    think("content", messages, agent="content-writer")

Configuration (credentials, models, per-task routing) lives in
config/providers.local.json (git-ignored) and is managed via the /setup UI or the
store functions here. Keys are always masked/redacted before they leave the server.
"""
from .routing import call_task, call_spare, deliberate, resolve_task
from .ladder import think, spread
from . import store, client, urls, auth

__all__ = ["resolve_task", "call_task", "deliberate", "think", "spread",
           "store", "client", "urls", "auth"]

# The terminal is set up before anything in this package prints. On Windows the
# default codepage is cp1252/cp1256, which has no ● and no ✕ — and this program
# prints both. See coordination/console.py.
try:
    from coordination.console import setup as _console_setup
    _console_setup()
except Exception:                                            # pragma: no cover
    pass
