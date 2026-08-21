"""
providers — a self-contained provider / API management module.

The rest of the app consumes AI through this clean interface and never builds an
API client itself:

    from providers import resolve_task, call_task
    call_task("md", [{"role": "user", "content": "…"}])

Configuration (credentials, models, per-task routing) lives in
config/providers.local.json (git-ignored) and is managed via the /setup UI or the
store functions here. Keys are always masked/redacted before they leave the server.
"""
from .routing import call_task, call_spare, deliberate, resolve_task
from . import store, client, urls, auth

__all__ = ["resolve_task", "call_task", "deliberate", "store", "client", "urls", "auth"]
