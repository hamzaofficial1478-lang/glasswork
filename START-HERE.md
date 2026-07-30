# START HERE — how to kick this off on Claude Code

You don't hand Claude the files one by one. You put them in one folder, open that
folder in Claude Code, and Claude reads the whole thing itself — `CLAUDE.md` and
every agent load automatically. Then you give it one bootstrap prompt. That's it.

## 1 · Put the files in this shape

```
agency/
├── CLAUDE.md                     ← the entry point. Claude reads this first, on its own.
├── .claude/
│   ├── agents/                   ← all the *.md agent files go here
│   │   ├── managing-director.md
│   │   ├── critic.md
│   │   ├── overseer.md
│   │   ├── security.md
│   │   ├── it-lead.md  it-watcher.md  it-fixer.md  it-releaser.md
│   │   ├── content-lead.md  content-writer.md
│   │   ├── seo-lead.md  seo-strategist.md
│   │   ├── leads-lead.md  leads-scanner.md  leads-hunter.md  leads-responder.md
│   │   └── reporter.md
│   ├── settings.json             ← wires the hooks in
│   └── hooks/                    ← pre_tool_use.py, post_tool_use.py, subagent_stop.py
├── coordination/
│   ├── schema.sql  init_db.py  board.py
├── mcp/site_connector/connector.py   ← the ONE file you fill with your website
├── runner/daily.py  service.py
├── ui/office.html  agency.html
├── server.js  package.json  .gitignore  .env.example
```

The files I've sent you are exactly these. Drop each where the tree shows.

## 2 · Set it up on your Node box (once)

```bash
cd agency
python3 coordination/init_db.py            # build the filing cabinet
npm install                                # for the office server
npm install -g @anthropic-ai/claude-code   # if you don't have it yet
cp .env.example .env                        # then fill .env in with your real keys
```

Fill the one website line in `mcp/site_connector/connector.py` (WordPress or custom).
That's the only blank that stops the agents touching your real site.

## 3 · Open Claude Code in the folder

```bash
cd agency
claude
```

Claude now has the whole company in its head: the handbook, every agent, the rules.

## 4 · Give it the BOOTSTRAP prompt (paste this, once)

> You are setting up the agency defined in CLAUDE.md. Read CLAUDE.md and every
> agent in .claude/agents so you know the full company. Then, acting as the
> Managing Director, do a one-time onboarding — do NOT change the live site or
> send any outreach yet:
> 1. Confirm the filing cabinet works (coordination/board.py, init_db.py).
> 2. Read the website from mcp/site_connector/connector.py: list its pages,
>    baseline its Lighthouse/speed, and log anything broken to site_issues.
> 3. Seed the keywords and competitors tables with the obvious service+area terms
>    for a UAE glass & aluminium business (glass work, aluminium, doors, pergolas
>    across Dubai, Abu Dhabi, Sharjah and their districts), and 3–5 real competitors.
> 4. Post your first day of tasks to the board, each one tied to a real fact you
>    just found. Run the critic subagent over that plan before you post it.
> Then report back to me: what you found, what's weak, and the plan you posted.

That gives you a real baseline and a first plan, with nothing risky done yet.

## 5 · Give it the DAILY prompt (this is the one that repeats)

> Act as the Managing Director and run one full daily cycle exactly as CLAUDE.md
> describes: sense the filing cabinet, read your own recent hit-rate, draft today's
> evidence-based plan, get it past the critic subagent, post the approved tasks to
> the board and log your decisions. Send me anything amber or low-confidence.
> Then let the sector leads claim and work their tasks — respecting the board,
> the locks, and verification between sectors. Don't touch production or send
> outreach without my approval.

You can paste this each morning, or let `runner/daily.py` fire it on a 7am cron so
it runs on its own.

## 6 · Watch it work

```bash
node server.js       # or: pm2 start server.js --name agency
```

Open the office at `/` and the control room at `/control`. As the agents claim
tasks and verify with each other, you'll see the rooms and the wires light up.

---

## The order it all happens in

1. You open the folder → Claude absorbs CLAUDE.md + the agents.
2. Bootstrap prompt → baseline crawl, seeded data, first plan (nothing risky).
3. Daily prompt (or cron) → the MD senses, plans, the critic checks, tasks go out.
4. Sector leads claim tasks; agents work; they verify across sectors on the wire.
5. Hooks gate every action; the Overseer watches spend and stalls; Security screens.
6. Amber items hit your Telegram; you approve or decline.
7. The Reporter scores the day and feeds the MD, so tomorrow is sharper.

Start at step 1. Claude Code handles the rest.
