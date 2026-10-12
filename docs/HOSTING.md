# Running Glasswork from your own server — the owner's guide

"the program brain to be in my control fully ... the user will only be able to
use the program nothing else"

Hosted, the program runs on **your** server. People open it in their browser at
your address (for example `https://app.yourprogram.com`), sign in, and use it.
They never receive the program's files, so nobody can copy it, change it, or
stop taking your updates: you update it once, on the server, and everyone has
the new version at once.

Each person still brings their own AI key (Claude, OpenAI or Gemini) and pays
that company directly. You supply no AI.

## What it needs

- A Linux VPS (Ubuntu 24.04 LTS is a good choice): 2 vCPU and 4 GB of memory
  for a first handful of customers, 40 GB of disk. Shared PHP hosting cannot
  run it — the program is Node and Python, and runs long jobs.
- A domain or subdomain for it, such as `app.yourprogram.com`, pointed at the
  VPS (an A record). Never a client's website.
- Your Glasswork Home stays where it is, for problem reports.

## Privacy, said plainly

Every account is encrypted with its owner's password (Argon2id, then
AES-256-GCM; each website in its own encrypted database). On your server's
disk, nobody — you included — can read a customer's websites, leads, settings
or AI keys without their password. While a person is signed in, their key is
held in the server's memory so the agency can work; it is never written down,
and it is gone when they sign out or after two hours untouched.

Two things follow from that, and both are deliberate:

1. **You cannot reset a customer's password.** Their recovery key is the only
   way back in. Tell them to keep it safe.
2. **The agency works while its owner is signed in** (a run that was started
   finishes even if they close the browser). It cannot start a day on its own
   at 3 a.m., because nobody's key is on the server then.

You become the custodian of your customers' (encrypted) data. Under the UAE's
data protection law (PDPL), and the GDPR for any EU customer, you need a
privacy notice naming you, a contact, and a way to delete an account. Have a
lawyer look at the terms and privacy pages before you charge anyone.

## Setting the server up (once, about an hour)

Everything below is typed on the server, over SSH.

### 1. A safe front door

```bash
# Updates, a firewall that lets in only SSH and the website, automatic security fixes
sudo apt update && sudo apt -y upgrade
sudo apt -y install ufw fail2ban unattended-upgrades git python3-venv
sudo ufw default deny incoming && sudo ufw default allow outgoing
sudo ufw allow OpenSSH && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp && sudo ufw enable
sudo dpkg-reconfigure -plow unattended-upgrades

# Time kept right (TLS certificates and the two-step codes both depend on it)
timedatectl status          # "System clock synchronized: yes"; if not: sudo timedatectl set-ntp true
```

**SSH:** sign in with a key, never a password. In `/etc/ssh/sshd_config` set
`PasswordAuthentication no` and `PermitRootLogin no`, then
`sudo systemctl restart ssh`. fail2ban (installed above) blocks addresses that
keep guessing. Optional: put SSH behind WireGuard so port 22 is not on the
internet at all.

### 2. The program, as its own user

```bash
sudo adduser --disabled-password --gecos "" glasswork
sudo -iu glasswork
git clone https://github.com/hamzaofficial1478-lang/glasswork.git ~/glasswork   # private: use a deploy key
cd ~/glasswork
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt -y install nodejs   # once, as your admin user
npm ci --omit=dev
mkdir -p ~/data && chmod 700 ~/data
exit
```

### 3. Run it as a service

`/etc/systemd/system/glasswork.service`:

```ini
[Unit]
Description=Glasswork (hosted)
After=network-online.target

[Service]
User=glasswork
WorkingDirectory=/home/glasswork/glasswork
Environment=GLASSWORK_HOSTED=1
Environment=GLASSWORK_HOST=app.yourprogram.com
Environment=GLASSWORK_INVITE=choose-a-long-invitation-code
Environment=AGENCY_DATA=/home/glasswork/data
Environment=PORT=3000
Environment=PYTHON_BIN=/home/glasswork/glasswork/.venv/bin/python
Environment=GLASSWORK_OWNER_KEY=the-owner-key-your-reports-room-made
Environment=GLASSWORK_HOME=https://www.yourprogram.com/glasswork-home
Environment=GLASSWORK_POSTBOX_KEY=the-postbox-key-your-reports-room-shows
ExecStart=/usr/bin/node server.js
Restart=on-failure
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=/home/glasswork/data /home/glasswork/glasswork
UMask=0077

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now glasswork
sudo systemctl status glasswork        # "active (running)"
```

- `GLASSWORK_HOSTED=1` turns on hosted mode: many people at once, each in
  their own room; the parts that belong to one computer (closing the program,
  installing updates, the Reports room, the Claude Code team) switched off; no
  program can reach the server's own network.
- `GLASSWORK_INVITE` — only people you give this code to can sign up. Leave it
  out to let anyone sign up.
- `GLASSWORK_OWNER_KEY`, `GLASSWORK_HOME`, `GLASSWORK_POSTBOX_KEY` — you do
  not make these up. In the Reports room on your own computer, open **Your
  online office**, give the server's address and press **Save and check**: it
  shows these three lines, ready to paste. The first lets your Reports room
  read how many people use the office (counts only); the other two let people
  online send you problem reports, sealed so only your computer can open them.
- It listens on `127.0.0.1:3000` only; the next step puts HTTPS in front.

### 4. HTTPS in front (Caddy — certificates are automatic)

```bash
sudo apt -y install caddy
```

`/etc/caddy/Caddyfile`:

```
app.yourprogram.com {
    reverse_proxy 127.0.0.1:3000
    encode gzip
}
```

```bash
sudo systemctl reload caddy
```

Caddy gets and renews a TLS certificate by itself, redirects http to https,
and serves HTTP/2 and HTTP/3 (QUIC). Open `https://app.yourprogram.com` — the
account page appears.

### 5. Backups

The whole of everyone's data is `/home/glasswork/data`, already encrypted
account by account. Copy it off the server every night (for example with
`restic` to another provider). A backup cannot be read without each person's
password, so it is safe to store elsewhere.

## Updating the program

On the server: `cd ~/glasswork && git pull && npm ci --omit=dev && .venv/bin/pip install -r requirements.txt && sudo systemctl restart glasswork`.

Or let GitHub do it: `.github/workflows/deploy.yml` runs the safety tests on
every push to `main` and, only if they pass, logs into the server and does
exactly that. It needs four repository secrets: `SSH_HOST`, `SSH_USER`,
`SSH_KEY` (a key that may only log in as the deploy user) and `SSH_FINGERPRINT`
(the server's host-key fingerprint: `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`).
Merging to `main` is then the same as publishing.

## What the person sees

A web address — and, if they like, an app. In Chrome or Edge (and on Android)
the address bar offers **Install Glasswork**; Settings → *Install the app* says
where. It then opens in its own window with its own icon, on the desktop or the
phone's home screen, and they sign in with the same account. Nothing of the
program is downloaded: the app is a window onto your server, so it is always
the newest version and your code stays with you. (On an iPhone: Share → *Add to
Home Screen*.)

The Windows Setup.exe still exists for your own computer. Do not hand it to
customers: it contains the whole program, which is exactly what hosting keeps
away from them.

## What you learn about the people using it

Two things, and nothing else:

1. **How many.** Your Reports room shows people signed in today, right now, a
   day on average this week, and how many accounts exist. On the server,
   `data/usage.json` holds one number a day; `data/usage-today.json` holds
   today's sign-ins as scrambled tags (so a restart does not count anyone
   twice) and is replaced the next day. No names, websites or work.
2. **Their problem reports**, when they choose to send one, sealed on the way
   so only your computer can read it.
