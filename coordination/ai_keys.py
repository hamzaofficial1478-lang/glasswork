"""
ai_keys.py — resolve which AI provider / model / key each agent should use.

The owner sets these on the /setup screen; they land in .env as one slot per brain
(provider, base URL, model, key). The wiring:

  - Managing Director, Critic, Overseer  -> MD slot (the most advanced model)
  - IT / Content / SEO / Leads / Report / Security agents -> that sector's slot
  - If a slot has no key, fall back to the FALLBACK slot, then to the MD slot.

This module is the single source of truth for that mapping; the runner uses it to
launch each agent with the right endpoint and key. (Kept in sync with the AI_SLOTS
list in server.js.)
"""
import os
from pathlib import Path

SLOTS = ["MD", "FALLBACK", "IT", "CONTENT", "SEO", "LEADS", "REPORT", "SECURITY"]

# agent name (matches .claude/agents/*.md) -> slot
AGENT_SLOT = {
    "managing-director": "MD", "critic": "MD", "overseer": "MD",
    "it-lead": "IT", "it-watcher": "IT", "it-fixer": "IT", "it-releaser": "IT",
    "content-lead": "CONTENT", "content-writer": "CONTENT",
    "seo-lead": "SEO", "seo-strategist": "SEO",
    "leads-lead": "LEADS", "leads-hunter": "LEADS",
    "leads-scanner": "LEADS", "leads-responder": "LEADS",
    "reporter": "REPORT",
    "security": "SECURITY",
}

_ENV = Path(__file__).parents[1] / ".env"


def _load_env():
    """Real process env wins; fall back to the .env file the setup screen wrote."""
    vals = {}
    if _ENV.exists():
        for line in _ENV.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip()
    vals.update({k: v for k, v in os.environ.items() if k in vals or k.split("_")[0] in SLOTS})
    return vals


def _slot(env, slot):
    return {
        "slot": slot,
        "provider": env.get(f"{slot}_PROVIDER", ""),
        "base_url": env.get(f"{slot}_BASE_URL", ""),
        "model": env.get(f"{slot}_MODEL", ""),
        "api_key": env.get(f"{slot}_API_KEY", ""),
    }


def slot_for_agent(agent):
    return AGENT_SLOT.get(agent, "FALLBACK")


def resolve(agent):
    """
    Return the provider/base_url/model/api_key an agent should use, applying the
    fallback chain: the agent's own slot -> FALLBACK -> MD. `used_slot` is None if
    nothing is configured yet (so the caller can surface a clear error, not crash).
    """
    env = _load_env()
    primary = slot_for_agent(agent)
    seen = []
    for slot in (primary, "FALLBACK", "MD"):
        if slot in seen:
            continue
        seen.append(slot)
        s = _slot(env, slot)
        if s["api_key"]:
            s.update(requested_slot=primary, used_slot=slot, fell_back=(slot != primary))
            return s
    s = _slot(env, primary)
    s.update(requested_slot=primary, used_slot=None, fell_back=False,
             error="no key configured for this agent or any fallback")
    return s


def as_claude_env(agent):
    """
    Environment variables to launch the `claude` CLI (or any Anthropic-compatible
    client) for this agent, and the resolved slot alongside. Returns (env, slot).
    """
    s = resolve(agent)
    env = {}
    if s.get("api_key"):
        env["ANTHROPIC_API_KEY"] = s["api_key"]
        env["ANTHROPIC_AUTH_TOKEN"] = s["api_key"]
    if s.get("base_url"):
        env["ANTHROPIC_BASE_URL"] = s["base_url"]
    if s.get("model"):
        env["ANTHROPIC_MODEL"] = s["model"]
    return env, s


if __name__ == "__main__":
    for a in ["managing-director", "critic", "it-fixer", "seo-strategist",
              "leads-hunter", "reporter", "security", "overseer"]:
        s = resolve(a)
        used = s["used_slot"] or "(none)"
        note = " [fallback]" if s.get("fell_back") else ""
        print(f"{a:18} -> {used:9}{note}  model={s['model'] or '-'}  "
              f"key={'set' if s.get('api_key') else 'MISSING'}")
