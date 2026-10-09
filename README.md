# Glasswork

**Your online growth agency, on your own computer.**

Glasswork runs a small digital agency for a business: a team of AI desks that
look after its website, find leads, write content, watch how it does on Google,
and ask before anything leaves the building. It is open source (GPL-3.0) and
runs entirely on the computer it is installed on.

- **Everything stays on your computer.** Your account, websites, leads, posts,
  keys and settings are kept on your own machine, encrypted with your password.
- **Nobody else on the same computer can open it.** Each person has their own
  account; another account — even on the same computer, in the same copy of the
  program — cannot read yours. Before you sign in, the program shows only the
  names of the accounts, never which websites they look after.
- **Nothing is sent to the people who make it.** No accounts on a server, no
  usage counting, no reports sent anywhere. The program only talks to the
  services *you* connect: your AI company, your website, Google, your email.
- **You approve what goes out.** An email, a published page or a change to your
  website waits for your yes.

## Install it (Windows)

1. Open the [release page](https://github.com/hamzaofficial1478-lang/glasswork/releases/latest)
   and download `glasswork-x.y.z-setup.exe`.
2. Open it. If Windows says *"Windows protected your PC"*, choose **More info →
   Run anyway** — the Setup is not signed with a paid certificate.
3. Choose where to install the program and where to keep your work. That is
   all: the Setup carries its own Python, Node and every library, and needs no
   administrator password.

Every Setup on the release page was built, installed, opened and tested on a
real Windows machine before it was put there (`.github/workflows/windows-installer.yml`).

**A new version:** *Settings → This program → Check for a new version*. Run the
new Setup over the old one; your work is kept. The program never looks for one
on its own.

## Run it from source (Windows, macOS, Linux)

You need Python 3.10+ and Node.js 18+.

```bash
git clone https://github.com/hamzaofficial1478-lang/glasswork.git
cd glasswork
python run.py          # installs what it needs the first time, then opens the office
```

Or use the scripts in `install/` (`Install-Windows.bat`, `Install-Mac.command`,
`install.sh`). A copy run from git updates itself with `git pull` each time
`run.py` starts.

## First run

The office walks you through it, and **Today** shows what is left:

1. **Make your account** — your name and a password. Write down the recovery
   key it shows you once: without your password and that key, nobody can open
   your data again.
2. **Add your website** — its name and address (an account holds two).
3. **Your business** — what you do, where, and who buys from you. *Draft it*
   reads your own website and fills it in for you to check.
4. **How your agency thinks** — Claude, OpenAI or Google Gemini, with your own
   API key. You pay them directly; a daily spending limit keeps it in check.
5. **Connect your website** — a hand-built (PHP/HTML) website: download your
   connector from Setup and upload its `agency-api` folder next to your home
   page. WordPress: make an Application Password (Users → Profile) and paste it.
   No connection yet? The agency still works read-only and hands you each change.
6. **Tell your agency what to do today**, then press **Run today**.

## How it is built

```
server.js              the office: one local web server (listens on this computer only)
ui/                    the screens (app.html is the main one)
coordination/          accounts and encryption (vault.py, workspace.py), the cabinet
                       (board.py, schema.sql), the day's plan, approvals, reports
pipelines/             what each desk does: website health and fixes, SEO, leads,
                       outreach, content, Google indexing, Website Check
providers/             the AI engines (Claude, OpenAI, Gemini) and paid tools
paste-into-your-site/  the website connector (agency-api.php)
installer/             the Windows Setup (build_windows.py, glasswork.iss)
```

**Encryption.** Each account has a random master key, locked with the password
and with the recovery key (scrypt + AES-256-GCM). Each website's cabinet is a
SQLCipher database; settings and config files are sealed with AES-256-GCM; the
account file keeps each website's name and address sealed too. The key lives in
memory only while the person is signed in.

**Tests.** `python run.py --tests` runs every suite. The website releaser runs
the same suite before any change goes on a website.

**Developing with Claude Code.** `CLAUDE.md` and `.claude/` hold the agency
handbook and guard-rail hooks used when working on the program with Claude Code;
see `START-HERE.md`.

## Licence

GNU General Public Licence v3.0 — see `LICENSE`. The program is provided as it
is, without warranty. AI makes mistakes: check its work before it reaches anyone.
