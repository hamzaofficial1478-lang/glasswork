"""
providers — how the agency thinks, and the shelf of non-AI tools it borrows.

Every sector's brain is a Claude model the owner chose for it
(providers/claude_team.py), reached through the Claude Code command on this
machine (providers/local_cli.py). The rest of the app asks through these doors
and never builds a client itself:

    from providers import call_task, deliberate, think
    call_task("md", [{"role": "user", "content": "…"}])

The shelf in providers/store.py holds the tools that are not AI models — stock
photos, the crawler, maps, SEO data — and providers/integrations.py the routes
to the outside world (ntfy, Telegram, SMTP).
"""
from .routing import call_task, deliberate, resolve_task
from .ladder import think, spread
from . import store, integrations

__all__ = ["resolve_task", "call_task", "deliberate", "think", "spread",
           "store", "integrations"]

# The terminal is set up before anything in this package prints. On Windows the
# default codepage is cp1252/cp1256, which has no ● and no ✕ — and this program
# prints both. See coordination/console.py.
try:
    from coordination.console import setup as _console_setup
    _console_setup()
except Exception:                                            # pragma: no cover
    pass
