# Security audit — Botix, November 2026

Run with Cloudflare's security-audit method (`.claude/skills/security-audit/`,
MIT). Source review plus small, local proofs on dummy data; nothing was sent to
a live website, a customer's hosting, Stripe or GitHub. The run's working files
(architecture, coverage, `findings.json`, the proofs) were kept outside the
repository, as the method asks.

**What kind of pass this was.** One auditor, working through the method's six
phases in order. The method also asks for separate, fresh reviewers to try to
knock each finding down and a second reviewer for every final record; that was
not done (no extra agents were started). Every confirmed result was instead
re-run after the fix. Treat this as a careful first pass, not a certified one.

## The short version

The program's doors are well built. Three things needed fixing, and all three
are fixed in this version:

| | What | How serious | Status |
|---|---|---|---|
| 1 | Text the AI writes could carry a hidden script attribute onto your website and into the office | **Medium** (confirmed) | Fixed, tested |
| 2 | The website connector's picture download followed redirects without re-checking where they went | Needs checking on real hosting | Fixed anyway |
| 3 | The Home's download button was built from the address the visitor's browser sent | Needs checking on real hosting | Fixed anyway |

## 1. AI-written HTML could carry behaviour (confirmed, medium)

**What happened.** The writer's answer was stored and published as it came.
The Auditor's check for code looked for four "on…=" attributes (`onclick`,
`onload`, `onerror`, `onmouseover`). There are dozens more — `ontoggle`,
`onfocus`, `onanimationstart`, `onpointerenter` — and they run just as surely.

**How it was shown.** On a throwaway office with dummy data, a draft holding
`<details open ontoggle="document.title='MARKER-RAN'">` was opened in the post
editor: the page title changed, so the attribute ran inside the signed-in
office. The Auditor passed four of five such attributes, and the body handed to
the website publisher still held them.

**Who could cause it.** The writer reads text gathered from the open web
(keyword research, competitors' pages). A page written to steer an AI could get
it to add such an attribute. Not shown end to end, which is why this is medium
and not high.

**The fix.**
- Everything the AI writes is cut down to content the moment it arrives, and
  again after polishing: only the tags and attributes a post may hold, nothing
  that runs (`pipelines/content_write.safe_page`, using `coordination/posts.clean_html`).
  This happens before the connector and WordPress paths split, so both get it.
- A draft that had code in it is also **held** on Waiting on you, saying so:
  code in the writer's answer is a sign something it read tried to steer it,
  so it is never quietly published, even cleaned.
- The post editor only ever shows cleaned HTML.
- The Auditor now refuses **any** `on…=`, `formaction=` or `srcdoc=` attribute.
- `coordination/hardening_tests.py` holds all of it in place.

**Still worth doing.** The office's content-security policy allows inline
scripts (the screens are written that way). Moving to per-page nonces would
stop a whole class of mistakes like this one. It is a larger change, left for
its own round.

## 2. The connector's picture download (needs checking, fixed anyway)

`fetch_picture()` in `agency-api.php` checked that a picture's address was on
the public internet, then let the download follow up to three redirects
anywhere — including into the hosting's own network. It now follows each
redirect itself, checks every hop, and connects to the exact address it checked.
The connector is now **version 11**; every website updates itself to it the next
time Botix talks to it.

Whether it mattered depends on two things this audit could not see: whether
anyone but you and the program can choose a picture's address (normally the
program sends the picture itself), and what each customer's hosting can reach
inside its own network. Only real images are ever saved, so the worst case was
a blind request. WordPress websites were never affected: the helper plugin
does not download pictures.

## 3. The Home's download button (needs checking, fixed anyway)

The download page made its button's link from the Host header of the request.
A forged header gave that one visitor a button pointing elsewhere; only a cache
in front of your website could hand that page to other people. The link is now
relative to the page, and the page tells caches not to keep it.
**Upload the Home folder again** (Reports → Download it again) to put this live.

## What was looked at, and held

- **The office** (`server.js`): answers only to this computer's own names (no
  DNS rebinding), refuses changes sent from other websites, strict security
  headers, rate limits on sign-in and recovery, session cookies HttpOnly and
  SameSite, and on a hosted office Secure too. File names in addresses are
  checked before any file is opened. No command is ever built from input.
- **Each person's data**: every request runs with that person's own key and
  website; caches and running jobs are kept per account.
- **The Home and plan server**: the owner key compared in constant time;
  reports sealed before they leave a customer's computer; Stripe messages
  checked against your webhook secret with a five-minute window; plan keys are
  100 bits of randomness, kept only as fingerprints; every change under a lock.
- **Updates**: only a version signed with your key installs; a zip that tries
  to write outside the program folder, or carries a link, is refused whole;
  downloads stay on https, redirects included (added this round).
- **Fetching from the internet** on a hosted office: no address inside the
  server's own network can be reached, by any part of the program.
- **What is shown on screen**: everything from outside is escaped; sent mails
  are shown in sandboxed frames; the Website Check report escapes every value
  it read from another website.
- **Builds**: GitHub Actions pinned to exact commits; the release job that
  writes to the repository runs only after the installer passed every check.

## Hardening notes (not findings)

- Python libraries are installed by minimum version (`>=`) when the installer is
  built and when the hosted office deploys. Pinning exact versions with hashes
  would stop a poisoned new release of a library from riding in.
- The Claude agents on your own copy that may run commands read pages from the
  web. The hooks in `.claude/hooks/` are what stops a steered agent; keep them on.
- `licence-check`, `licence-use` and `licence-release` are not rate-limited.
  With 100-bit keys this does not let anyone guess a key; a limit would still
  be tidy.
- The connector's own update is trusted because it carries the website's token,
  which can already change pages. The token is the key to the website: it is
  kept sealed in the office and never shown.

## The website security factors Botix now measures

From the same method's guides for browsers and web protocols, Website Check's
security section now also measures, from the answer a website already gives:
cookies missing Secure, HttpOnly or SameSite; a content-security policy that
still lets any script run; HSTS shorter than six months; other websites
allowed to read answers with a visitor's login (CORS with credentials); and
scripts from public CDNs without an integrity pin. It still never tries a
login, an injection or a load test.
