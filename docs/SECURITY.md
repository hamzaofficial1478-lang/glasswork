# Security checklist — the 61 points

Checked on 12 October 2026 against the code in this repository. Kept with the
code so each round can re-check it.

**How to read the Status column**

- **Done, tested**: built, with an automated test that fails if it ever breaks
  (mostly `coordination/security_tests.py`, which runs a hosted office with two
  people signed in at once and attacks it).
- **Done**: built and checked by reading the code. No automated test.
- **On the server**: a setting on your VPS, written out step by step in
  `docs/HOSTING.md`.
- **Not used**: the program does not use this technology, so there is nothing
  to secure.

| # | Point | Status | What protects it |
|---|---|---|---|
| 1 | Public .env files | Done, tested | Only the `ui/` folder is ever served; `.env`, the program's code, settings, records and `.git` all answer 404 (tested). Settings live in each account's encrypted folder. |
| 2 | Hardcoded secrets | Done, tested | No key, token or private key is in the code. A test scans every tracked file for key patterns. Every connector token, Home key and signing key is made fresh, at random, on the machine that uses it. |
| 3 | Weak auth | Done, tested | Passwords go through Argon2id (128 MiB per guess), then AES-256-GCM. At least 8 characters; common passwords and passwords containing the person's name are refused. Two-step sign-in is available. |
| 4 | Missing authz check | Done, tested | Every route needs a signed-in session except the sign-in door itself. Setup asks for the password again. The developer's room answers 404 anywhere but his own copy. |
| 5 | Cross-user access | Done, tested | Each request runs inside its own person's session (their key, their website). Programs started for them get only their key. Answers that are kept or shared are scoped to that person. Tested with two people signed in at once. |
| 6 | Open DB permissions | Done / On the server | Each website is its own SQLCipher database, encrypted with a key from its owner's password. Settings files are sealed and written 0600. Hosted mode sets umask 077, and the systemd unit adds `UMask=0077` and its own user. |
| 7 | Cloud service misconfig | On the server | No cloud storage or managed services are used. One VPS: firewall allows only 22/80/443, the app listens on 127.0.0.1 behind Caddy, and backups are already encrypted per account. |
| 8 | Unprotected admin route | Done, tested | Hosted: closing the program, installing updates, the Reports room, the old admin password, bring-in and the Claude Code team all answer 404 (tested). On someone's own computer these need a signed-in session, and Setup asks again. |
| 9 | Logs leak secrets | Done | Passwords and keys reach Python on stdin, never on a command line or in a log. The key is never printed. A hosted office keeps error details in its own server log. |
| 10 | Verbose prod errors | Done, tested | Hosted: people see "Something went wrong on our side"; the detail goes to the server log. A test checks no program's raw error output reaches the browser. |
| 11 | Secrets in git | Done, tested | `.env`, `config/*.local.json`, every database and `data/` are git-ignored (tested), plus the key-pattern scan. The repository is private. |
| 12 | Secrets in JS | Done, tested | The browser never receives a key: settings report "set / not set" and the AI key its last four characters. The scan covers `ui/` too. |
| 13 | Client-only security | Done, tested | Every check is made on the server. Screens hidden in the browser (the Reports room, the Publisher card) are also refused by the server (tested). |
| 14 | Input validation | Done | Ids and names checked against strict patterns, lengths capped, JSON bodies size-limited (100 KB; 16 MB for a post with pictures), every value passed to programs as data. |
| 15 | SQL injection | Done | Every query is parameterised. The few column or table names built into SQL come from the code itself, never from a request (audited). |
| 16 | NoSQL injection | Not used | There is no NoSQL database. |
| 17 | XSS | Done, tested | Everything shown on screen is escaped (a test checks every field in the main screen). The content policy allows scripts only from the office itself and connections only back to it. Mail previews sit in sandboxed frames. |
| 18 | CSRF | Done, tested | Cookies are `SameSite=Lax`, and any change whose browser says it came from another website is refused (tested). |
| 19 | Insecure file uploads | Done | Pictures are checked by their actual bytes (PNG/JPEG/WebP/GIF), capped at 6 MB and renamed by their own hash. The website connector checks them again. Home uploads are owner-only, `.zip`/`.exe` names only, and checked whole by SHA-256. |
| 20 | Path traversal | Done, tested | Ids and file names must match strict patterns. Only `ui/` is served, and `/../` is refused (tested). An update that tries to write outside the program folder is refused whole (`launch_kit_tests`). |
| 21 | SSRF | Done, tested | Hosted: every program's network lookups return public internet addresses only. Loopback, private, link-local and the cloud metadata address are refused at connect time, so DNS rebinding gains nothing (tested). The crawler checks addresses everywhere. |
| 22 | Broken password reset | Done, tested | No email reset to hijack. The 120-bit recovery key, stretched like the password and rate-limited, is the only way back. It is replaced after each use, and the old one stops working (tested). With two-step sign-in on, a reset needs the recovery key and a code; a lost phone needs the password and the recovery key. Always two things, and nobody at TECH DUO can do it for a customer (tested). |
| 23 | Weak session mgmt | Done, tested | 256-bit random tokens, `HttpOnly`, `SameSite`, `Secure` when hosted. A new token on every sign-in; ends after 2 hours untouched hosted (12 on a computer of its own) or a week regardless. Sign-out, password change and turning on two-step end the other sessions (tested). |
| 24 | JWT secrets | Not used | No JWTs. Sessions are random tokens held only in the server's memory. |
| 25 | Permissive CORS | Done, tested | No CORS headers at all, so no other website can read the office (tested). |
| 26 | Rate limits | Done, tested | Per address: sign-in, sign-up, recovery, the Setup password, two-step and problem reports. Per name: 10 wrong passwords, then a 15-minute wait even for the right one (tested). The Home limits reports per address and per day. |
| 27 | Exposed environments | Done | No route returns environment variables or the values of settings. |
| 28 | Default credentials | Done | There are none. No built-in accounts or passwords; every key and token is generated at random when needed. |
| 29 | Unsigned webhooks | Done, tested | Updates are Ed25519-signed and checked before install. The Home's owner actions need his key (only its hash is on the website). Approvals are given only inside the signed-in app (computer or phone); no outside channel can approve anything. |
| 30 | IDOR / BOLA | Done, tested | Every id is looked up in the requesting person's own database, so another person's numbers find nothing. Opening another person's website by its id is refused (tested). |
| 31 | APIs + user input | Done | Validated as in 14. Programs are started with argument lists, never through a shell, so typed text cannot become a command. |
| 32 | Exposed logs | Done | Logs live in the data folder, which is never served. |
| 33 | Exposed source maps | Done, tested | There are none (no build step); tested. |
| 34 | User flow | Done, tested | Browser walkthroughs each round: first run, reports and answers, updates, hosted sign-up with invitation code, two-step sign-in. |
| 35 | PRD | Done | `docs/PUBLIC-LAUNCH-PLAN.md`, with dated decisions. |
| 36 | TDD | Done | 76 test suites, run in full before every commit, and the safety tests run before every deploy. |
| 37 | TCP | On the server | The office listens on 127.0.0.1 only; Caddy serves 443; the firewall allows 22/80/443 and nothing else. |
| 38 | UDP | On the server | Nothing listens on UDP except Caddy's HTTP/3 on 443 (optional); the firewall denies the rest. |
| 39 | QUIC | On the server | Caddy serves HTTP/3 over QUIC with TLS 1.3 automatically. Optional. |
| 40 | TLS | Done / On the server | HTTPS by Caddy with automatic certificates, HSTS for a year, `Secure` cookies. Outbound calls verify certificates; reports and updates travel only over https. |
| 41 | SSH | On the server | Keys only, no root login, fail2ban. The deploy checks the server's host-key fingerprint. |
| 42 | OpenID | Not used | No third-party sign-in. Can be added later. |
| 43 | LDAP | Not used | — |
| 44 | NTP | On the server | `timedatectl set-ntp true`. Certificates and two-step codes both need the right time. |
| 45 | DHCP | Not used | The VPS provider's network; nothing in the program. |
| 46 | IPSec | Not used | — |
| 47 | WireGuard | On the server (optional) | Put SSH behind WireGuard so port 22 is not on the internet at all. |
| 48 | No MFA | Done, tested | Two-step sign-in with any authenticator app (RFC 6238). The secret is sealed with the account's key, and each code is accepted once (tested). Asked for straight after sign-up, with a picture to scan; it can be put off, and added later from the account page. |
| 49 | Account enumeration | Done, tested | Sign-in and recovery give the same answer for a wrong name and a wrong password (tested). The sign-in screen lists nobody, and names are sealed on disk. Sign-up still says a name is taken, as any username system must; it is rate-limited and can be invite-only. |
| 50 | Business logic abuse | Done | Approval before anything goes out; daily spend cap per account; outreach caps; one run per website at a time; invite-only sign-up; on a hosted office each person gets a fair share of the server's workers. |
| 51 | Race conditions | Done | Task claims are atomic in the database. Files are written to a temporary name, then renamed. One run per website. Updates install with a backup and roll back whole. |
| 52 | Insecure CI/CD | Done | Workflows are read-only. The deploy runs the safety tests first, logs in with a key and a checked host fingerprint, and keeps secrets in GitHub's secret store. |
| 53 | Untrusted build actions | Done | Every action is pinned to an exact commit, not a movable tag. The installer's Python and Node downloads are checked against their publishers' SHA-256 sums. |
| 54 | Checks fail open | Done | Signature checks, the network guard, two-step and sign-in all fail closed. A hosted office always requires an account. By design, your own pre-account copy with no admin password stays open on localhost. |
| 55 | Missing timeouts | Done | Every program the office starts and every outbound request has a timeout (audited). Node's own header and request timeouts stand. |
| 56 | Sensitive info leak | Done, tested | Generic errors when hosted. Support reports are scrubbed. Account and website names are sealed. Nobody at the door learns who is signed in (tested). |
| 57 | Invalid AI output | Done | AI output is parsed and checked, shown escaped, and nothing it writes leaves without the Auditor's check and the person's approval. |
| 58 | Excessive AI permissions | Done | The AI returns text only: no tools, no commands (Claude Code runs `--restricted` in a scratch folder). Website changes go through the connector's narrow commands, with a backup of every page. |
| 59 | Sensitive browser storage | Done, tested | The browser stores only the theme, the chosen desk and an update "later". The session is in an `HttpOnly` cookie that scripts cannot read (tested). |
| 60 | Open redirects | Done | Only fixed, internal redirects; none take an address from the request. |
| 61 | Unsecured endpoints | Done, tested | Open without signing in: the account page, sign-in, sign-up and recovery (all rate-limited), `ping`, the product name, terms and privacy, and the four files that let a browser install it as an app (manifest, two icons, a worker that keeps nothing). `/api/owner/stats` answers only to the owner's key and gives counts, never who (tested). Everything else is closed (tested). |

## Known limits, said plainly

- **Code on someone's own computer can be read there.** The licence forbids
  copying it. Hosted, nobody receives the code at all.
- **Hosted, a signed-in person's key is in the server's memory** while they
  work. Someone with root on the server could take it at that moment. At rest,
  on disk and in backups, nobody can read it.
