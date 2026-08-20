# The Agency — an autonomous growth team on Claude Code

A small company of AI agents that runs the online growth of a glass & aluminium
services business in the UAE. One MD reasons and plans, a Critic challenges the
plan, sector leads (IT, Content, SEO, Leads, Report) run their crews, an Overseer
polices the agents, and Security screens the outside world. They coordinate without
overriding each other, and report to you through one Telegram bridge.

## What's here

```
agency/
├── CLAUDE.md                  the company handbook + the MD's brain and red lines
├── .claude/
│   ├── agents/                every agent's job description (18 of them)
│   ├── settings.json          wires the hooks in
│   └── hooks/                 the automatic guardrails (block / log / enforce)
├── coordination/
│   ├── schema.sql             the filing cabinet (all sectors)
│   ├── init_db.py             build the cabinet
│   └── board.py               claim tasks · resource locks · request board  <-- the anti-override core
├── mcp/site_connector/
│   └── connector.example.py   template for the ONE site adapter (setup writes connector.py)
├── runner/
│   ├── daily.py               the cron clock — wakes the MD each morning
│   └── service.py             the always-on crew (Reception, Watcher, Security)
└── ui/agency.html             the control room — rooms, agents, scores, config
```

## Where it runs

- **Website + the dashboard (ui/agency.html):** your existing Hostinger *shared*
  hosting is fine. The dashboard is one static file.
- **The agents:** NOT shared hosting — it kills long-running processes. Use a
  **Hostinger VPS** (KVM 1, ~$6/mo intro). Ubuntu 24.04, root access, persistent
  processes. Same provider, one extra box sitting next to your site.

## Setup (on the VPS)

```bash
python3 coordination/init_db.py          # build the filing cabinet
npm install -g @anthropic-ai/claude-code # if not already
# put your ANTHROPIC_API_KEY, DataForSEO, Telegram + site creds in a .env
```

## Connect your website (the only per-stack step)

Easiest: open the **setup screen** at `/setup` (served by `server.js`) and fill in
the form — it writes your `.env` and the site connector for you, no hand-editing.

Prefer to do it by hand? The tracked template is
`mcp/site_connector/connector.example.py`. Copy it to `connector.py` and set one line:

```python
SITE = WordPressSite("https://yoursite.ae", "admin", "app-password")   # WordPress
SITE = CustomSite("https://yoursite.ae/api/admin", "YOUR_TOKEN")       # custom backend
```

For a custom backend, expose the small protected endpoints the adapter calls
(`/health`, `/pages`, `/contact`, and the release hooks) and you're wired in.
Every agent that touches the site goes through this one adapter — nothing else changes.
The filled `connector.py` is git-ignored, so your credentials never leave the box.

## Run order

1. `daily.py` on cron at 07:00 — the MD plans the day.
2. `service.py` under pm2/systemd — the always-on crew reacts in real time.
3. Open `ui/agency.html` to watch the rooms and approve amber items.

## The rules that keep it safe (see CLAUDE.md)

- The brain never touches the hands — the MD only ever plans.
- No decision without evidence; every plan is challenged by the Critic.
- One door to production — only the IT Releaser deploys.
- Claim, don't grab. Request, don't reach. Append, don't overwrite.
- Hooks enforce the red lines automatically; the Overseer holds the kill-switch
  and the spend cap; Security treats all scraped text as untrusted data.
