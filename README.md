# Glasswork

**Your online growth agency, on your own computer.**

Glasswork runs a small digital agency for a business: a team of AI desks that
look after its website, find leads, write content, watch how it does on Google,
and ask before anything leaves the building. It runs entirely on the computer
it is installed on.

**Private and closed source.** This repository is private and the program
belongs to its publisher — see `LICENSE`. Nobody may copy, share or change it
without written permission. This README, `docs/`, the agency handbook and the
developer's own side of the program are left out of every copy given to people
(`coordination/product.py`, `PUBLISHER_ONLY`).

- **Everything stays on the person's computer**, encrypted with their password:
  account, websites, leads, posts, keys and settings.
- **Nobody else on the same computer can open it.** Each person has their own
  account, locked with their own password. The sign-in screen lists nobody:
  you type your own name and password.
- **Nobody is tracked.** No copy has a number, nothing counts the copies in
  use, and nobody registers. The program talks only to the services the person
  connects — and, when they choose, sends a problem report to the developer,
  sealed so only the developer's computer can open it.
- **They approve what goes out.** An email, a published page or a change to
  their website waits for their yes.

## How people get it, and new versions

From the developer's **Glasswork Home** — one folder on the program's own
website (never a client's): a sealed postbox for reports, the signed updates,
and the download page people install from. Setting it up and putting a version
out are in `docs/RELEASING.md`.

- **Installing (Windows):** the download page's Setup carries its own Python,
  Node and every library, and needs no administrator password.
- **New versions:** the program offers each signed version on its own
  (*"Version 0.x.0 is ready — Update now"*); work and settings are kept, and
  *Settings → Go back to the previous version* undoes it.
- **Problem reports:** *Help and reports* in the program. The developer reads
  and answers them in **Reports**, on his own copy only.

## Run it from source (the developer's own copy)

You need Python 3.10+ and Node.js 18+.

```bash
git clone https://github.com/hamzaofficial1478-lang/glasswork.git   # private: needs access
cd glasswork
python run.py          # installs what it needs the first time, then opens the office
```

A copy run from git brings itself up to date with `git pull` each time `run.py`
starts. When it moves to a server later, the same command runs it there.

## First run

The office walks the person through it, and **Today** shows what is left:

1. **Make your account** — a name and a password. Write down the recovery key
   it shows once: without the password and that key, nobody can open the data.
2. **Add your website** — its name and address (an account holds two).
3. **Your business** — what you do, where, and who buys from you.
4. **How your agency thinks** — Claude, OpenAI or Google Gemini, with your own
   API key and a daily spending limit.
5. **Connect your website** — a connector folder to upload (PHP/HTML), or a
   WordPress Application Password. Until then the agency works read-only.
6. **Tell your agency what to do today**, then press **Run today**.

## How it is built

```
server.js              the office: one local web server (listens on this computer only)
ui/                    the screens (app.html is the main one; publisher.js is the Reports room)
coordination/          accounts and encryption (vault.py, workspace.py), the cabinet
                       (board.py, schema.sql), reports (support.py, sealed.py),
                       updates (updates.py), the developer's side (publisher.py)
pipelines/             what each desk does: website health and fixes, SEO, leads,
                       outreach, content, Google indexing, Website Check
providers/             the AI engines (Claude, OpenAI, Gemini) and paid tools
paste-into-your-site/  the website connector (agency-api.php)
publish-to-your-site/  the Glasswork Home (developer only)
installer/             the Windows Setup (build_windows.py, glasswork.iss)
```

**Encryption.** Each account has a random master key, locked twice — with the
password and with the recovery key — each through a memory-hard key
stretch (Argon2id where the encryption library has it, scrypt otherwise) and
AES-256-GCM. From the master key each website gets its own cabinet key
(SQLCipher) and the settings get a file key (HKDF-SHA256, AES-256-GCM, each
file bound to its own place). Account names, website names and addresses are
sealed too; folders are named by random numbers. The key exists only in
memory while the person is signed in. Reports to the developer are sealed with
X25519 + HKDF-SHA256 + AES-256-GCM to his public key, and his answer to a key
made for that one report.

**Tests.** `python run.py --tests` runs every suite. The website releaser runs
the same suite before any change goes on a website.

**Working on it with Claude Code.** `CLAUDE.md` and `.claude/` hold the agency
handbook and the guard-rail hooks; see `START-HERE.md`.
