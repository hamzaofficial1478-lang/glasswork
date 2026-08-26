/*
 * server.js — the one Node app you host. Serves the UIs and the shared state.
 *   /          -> the Virtual Office (front of house)
 *   /control   -> the Control Room dashboard (back office)
 *   /setup     -> the setup screen (website, AI providers, routing, optional)
 *   /status    -> plain-language "what's connected" view
 *
 * AI provider config is owned by the Python module in providers/*.py. This server
 * NEVER builds an API client itself — it shells into `python3 -m providers.api`
 * so the UI and the agents share ONE code path. Run under pm2: pm2 start server.js
 */
const express = require("express");
const path = require("path");
const fs = require("fs");
const crypto = require("crypto");
const { spawn, spawnSync } = require("child_process");
const app = express();
app.use(express.json());
// NOTE: express.static is mounted AFTER the front-door middleware below, not
// here. Mounted first it serves ui/setup.html at /setup.html to anybody, gate
// or no gate — the lock would be on the door and the window left open.

/* ---------------------------------------------------------------------------
   THE FRONT DOOR
   ---------------------------------------------------------------------------
   Two guards, and they defend against different things.

   HOST CHECK. A page on the open web cannot read localhost directly, but it can
   point a domain it controls at 127.0.0.1 and let your browser do the reading
   for it — DNS rebinding, and same-origin policy does not stop it because to
   the browser it is all one origin. Requests are answered only when the Host
   header really is this machine. Costs nothing, closes the hole.

   PASSWORD. Optional, and off until the owner sets one, deliberately: the
   server already listens on this computer alone, so a forced login on a laptop
   he is signed into is a nuisance that teaches him to click through prompts.
   It earns its keep the moment he shares the machine or opens BIND_HOST — and
   then it is the only thing between a passer-by and his API keys. It is set on
   the Setup screen, which posts to /api/password below.
--------------------------------------------------------------------------- */
const LOCAL_HOSTS = new Set(["localhost", "127.0.0.1", "[::1]", "::1"]);

function adminHash() {
  return (readEnv().ADMIN_PASSWORD_HASH || "").trim();
}

// pbkdf2$<iterations>$<salt-b64>$<hash-b64> — written by backend.py, read here.
// Same algorithm on both sides so either can set it and either can check it.
function passwordOk(supplied) {
  const stored = adminHash();
  if (!stored) return true;
  const bits = stored.split("$");
  if (bits.length !== 4 || bits[0] !== "pbkdf2") return false;
  const iter = parseInt(bits[1], 10);
  if (!Number.isFinite(iter) || iter < 1000) return false;
  let want, got;
  try {
    const salt = Buffer.from(bits[2], "base64");
    want = Buffer.from(bits[3], "base64");
    got = crypto.pbkdf2Sync(String(supplied || ""), salt, iter, want.length, "sha256");
  } catch (_) { return false; }
  // constant time: a length mismatch must not answer faster than a value one
  return got.length === want.length && crypto.timingSafeEqual(got, want);
}

const SESSIONS = new Set();          // in memory: restarting the server logs you out
const COOKIE = "glasswork_admin";

function cookieToken(req) {
  const raw = req.headers.cookie || "";
  for (const part of raw.split(";")) {
    const i = part.indexOf("=");
    if (i > 0 && part.slice(0, i).trim() === COOKIE) return part.slice(i + 1).trim();
  }
  return "";
}

app.use((req, res, next) => {
  const host = String(req.headers.host || "").split(":")[0].toLowerCase();
  if (host && !LOCAL_HOSTS.has(host) && !process.env.BIND_HOST) {
    return res.status(403).type("text/plain").send(
      "Refused: this server answers to localhost only. If you meant to reach it " +
      "over the network, start it with BIND_HOST set and put a password on it.");
  }
  if (!adminHash()) return next();                       // no password set
  if (req.path === "/login" || req.path === "/api/login") return next();
  if (SESSIONS.has(cookieToken(req))) return next();
  if (req.path.startsWith("/api/")) {
    return res.status(401).json({ ok: false, error: "Locked. Sign in at /login." });
  }
  return res.redirect("/login");
});

app.get("/login", (_q, res) => {
  res.type("html").send(`<!doctype html><meta charset="utf-8">
<title>World Deal Technical Agency</title>
<style>
 body{margin:0;height:100vh;display:grid;place-items:center;background:#0c1216;
   color:#e7eff3;font-family:system-ui,sans-serif}
 form{background:#131d23;border:1px solid #25343c;border-radius:14px;padding:28px;width:320px}
 h1{font-size:16px;margin:0 0 6px} p{font-size:12.5px;color:#7d919c;margin:0 0 18px;line-height:1.5}
 input{width:100%;padding:11px;border-radius:9px;border:1px solid #3a515b;background:#182530;
   color:#e7eff3;font-size:14px;box-sizing:border-box}
 button{width:100%;margin-top:12px;padding:11px;border-radius:9px;border:1px solid #54cbd9;
   background:rgba(84,203,217,.15);color:#54cbd9;font-weight:600;font-size:14px;cursor:pointer}
 .err{color:#e5565b;font-size:12.5px;margin-top:10px;min-height:16px}
</style>
<form method="POST" action="/api/login">
  <h1>World Deal Technical Agency</h1>
  <p>This machine's copy is locked. Enter your admin password.</p>
  <input type="password" name="password" placeholder="Password" autofocus required>
  <button>Unlock</button>
</form>`);
});

app.post("/api/login", express.urlencoded({ extended: false }), (req, res) => {
  const supplied = (req.body && req.body.password) || "";
  if (!passwordOk(supplied)) {
    return res.status(401).type("html").send(
      `<!doctype html><meta charset="utf-8"><body style="font-family:system-ui;background:#0c1216;color:#e5565b;padding:40px">
       Wrong password. <a href="/login" style="color:#54cbd9">Try again</a>.</body>`);
  }
  const tok = crypto.randomBytes(32).toString("hex");
  SESSIONS.add(tok);
  res.cookie
    ? res.cookie(COOKIE, tok, { httpOnly: true, sameSite: "lax" })
    : res.setHeader("Set-Cookie", `${COOKIE}=${tok}; HttpOnly; SameSite=Lax; Path=/`);
  res.redirect("/");
});

// SET OR CHANGE THE ADMIN PASSWORD, from the screen.
//
// It used to say "run python backend.py --password", which is two problems in
// one sentence: it asks a person who came to a settings screen to go and use a
// terminal, and it names the file the credential lives in, to anyone reading
// over their shoulder. Neither is necessary — the same PBKDF2 hash can be
// written from here.
//
// Only the HASH is ever stored (PBKDF2-SHA256, 240k rounds, random salt), and
// changing an existing password requires the current one, so the fact that the
// gate is open right now is not by itself permission to move the lock.
app.post("/api/password", express.json(), (req, res) => {
  const b = req.body || {};
  const current = adminHash();
  if (current && !passwordOk(b.current || "")) {
    return res.status(401).json({ ok: false, error: "That is not the current password." });
  }
  const next = String(b.password || "");
  if (b.clear) {
    if (!current) return res.json({ ok: true, locked: false, note: "There was no password set." });
    try { updateEnv({ ADMIN_PASSWORD_HASH: "" }); }
    catch (err) {
      return res.status(500).json({ ok: false,
        error: "Could not write the settings file: " + String(err.message || err) });
    }
    SESSIONS.clear(); SETUP_TICKETS.clear();
    return res.json({ ok: true, locked: false, note: "Removed. This machine is open again." });
  }
  if (next.trim().length < 8) {
    return res.status(400).json({ ok: false, error: "Use at least 8 characters." });
  }
  if (next !== String(b.confirm || "")) {
    return res.status(400).json({ ok: false, error: "The two passwords do not match." });
  }
  const iter = 240000;
  const salt = crypto.randomBytes(16);
  const dk = crypto.pbkdf2Sync(next, salt, iter, 32, "sha256");
  const stored = `pbkdf2$${iter}$${salt.toString("base64")}$${dk.toString("base64")}`;
  const partial = { ADMIN_PASSWORD_HASH: stored };
  if (String(b.name || "").trim()) partial.ADMIN_NAME = String(b.name).trim().slice(0, 60);
  // If the settings file cannot be written — read-only, owned by another user,
  // a full disk — say THAT. Letting it throw hands the browser an HTML error
  // page, and a screen expecting JSON reads an HTML page as "no server".
  try { updateEnv(partial); }
  catch (err) {
    return res.status(500).json({ ok: false,
      error: "Could not write the settings file: " + String(err.message || err) });
  }
  // Every existing session dies with the old password, including this one.
  SESSIONS.clear(); SETUP_TICKETS.clear();
  return res.json({ ok: true, locked: true, signedOut: true,
    note: "Set. You have been signed out — sign in with it now." });
});

app.post("/api/logout", (req, res) => {
  SESSIONS.delete(cookieToken(req));
  SETUP_TICKETS.delete(setupToken(req));
  res.json({ ok: true });
});

/* ---------------------------------------------------------------------------
   THE SECOND DOOR — SETUP ASKS EVERY TIME
   ---------------------------------------------------------------------------
   Signing in gets you into the agency. It does not get you into the room with
   every API key in it.

   The owner set a password, signed in, clicked Setup and walked straight
   through — because the front door had already been satisfied and nothing else
   was asking. That is the wrong shape for this screen: the session is hours
   long and follows him around the building, while Setup holds the mail
   password, the site admin token, the Algolia write key and the spend cap. A
   session is "this is my machine". Opening the key cupboard should be "this is
   me, now".

   So Setup has its own ticket, and NAVIGATING to Setup consumes it. Unlock,
   read, change what you came for, leave — come back and it asks again. The
   ticket lives a few minutes only so the page's own API calls work while you
   are on it, and it dies with the session and with any password change.

   The API routes below are gated too, not just the page. A lock on the door
   with the window open is not a lock: /api/settings hands back the ntfy topic
   and the SMTP user, and /api/providers hands back the model routing. --------- */
const SETUP_COOKIE = "glasswork_setup";
const SETUP_TICKETS = new Map();      // token -> { expires, navUsed }
const SETUP_TTL_MS = 10 * 60 * 1000;  // long enough to finish, short enough to matter

// Everything the Setup screen reads or writes. Prefix match, so
// /api/providers/credential is covered by /api/providers.
const SETUP_API = [
  "/api/settings", "/api/website", "/api/optional", "/api/integration",
  "/api/providers", "/api/routing", "/api/preflight", "/api/shelves",
];

function setupToken(req) {
  const raw = req.headers.cookie || "";
  for (const part of raw.split(";")) {
    const i = part.indexOf("=");
    if (i > 0 && part.slice(0, i).trim() === SETUP_COOKIE) return part.slice(i + 1).trim();
  }
  return "";
}

function setupTicket(req) {
  const t = SETUP_TICKETS.get(setupToken(req));
  if (!t) return null;
  if (Date.now() > t.expires) { SETUP_TICKETS.delete(setupToken(req)); return null; }
  return t;
}

function unlockPage(err) {
  return `<!doctype html><meta charset="utf-8">
<title>Setup — password required</title>
<style>
 body{margin:0;height:100vh;display:grid;place-items:center;background:#0c1216;
   color:#e7eff3;font-family:system-ui,sans-serif}
 form{background:#131d23;border:1px solid #25343c;border-radius:14px;padding:28px;width:360px}
 h1{font-size:16px;margin:0 0 6px} p{font-size:12.5px;color:#7d919c;margin:0 0 18px;line-height:1.6}
 input{width:100%;padding:11px;border-radius:9px;border:1px solid #3a515b;background:#182530;
   color:#e7eff3;font-size:14px;box-sizing:border-box}
 button{width:100%;margin-top:12px;padding:11px;border-radius:9px;border:1px solid #54cbd9;
   background:rgba(84,203,217,.15);color:#54cbd9;font-weight:600;font-size:14px;cursor:pointer}
 a{color:#7d919c;font-size:12px;display:block;margin-top:14px;text-align:center}
 .err{color:#e5565b;font-size:12.5px;margin-top:10px;min-height:16px}
</style>
<form method="POST" action="/api/setup/unlock">
  <h1>&#128274; Setup</h1>
  <p>This screen holds every API key in the agency, your model routing and your
     spend cap. Being signed in is not enough &mdash; it asks each time you open it.</p>
  <input type="password" name="password" placeholder="Your admin password" autofocus required>
  <button>Open Setup</button>
  ${err ? `<div class="err">${err}</div>` : ""}
  <a href="/">&larr; Back to the office</a>
</form>`;
}

app.use((req, res, next) => {
  if (!adminHash()) return next();               // no password set: nothing to ask
  const isPage = req.method === "GET" &&
    (req.path === "/setup" || req.path === "/setup.html");
  const isApi = SETUP_API.some((p) => req.path === p || req.path.startsWith(p + "/"));
  if (!isPage && !isApi) return next();
  if (req.path === "/api/setup/unlock") return next();

  const t = setupTicket(req);
  if (isPage) {
    // Navigating here CONSUMES the ticket. That is what makes it ask every
    // time rather than once an hour.
    if (t && !t.navUsed) { t.navUsed = true; return next(); }
    // Showing the unlock page means THIS visit is not authorised — so the old
    // ticket dies here. Without this the page re-locked correctly while the
    // ticket quietly kept /api/settings open for the rest of its ten minutes,
    // which is the lock on the door and the window left open again.
    SETUP_TICKETS.delete(setupToken(req));
    return res.type("html").send(unlockPage(""));
  }
  if (t) return next();
  return res.status(401).json({ ok: false, needsSetupUnlock: true,
    error: "Setup is locked. Open the Setup screen and enter your password." });
});

app.post("/api/setup/unlock", express.urlencoded({ extended: false }), (req, res) => {
  const supplied = (req.body && req.body.password) || "";
  if (!passwordOk(supplied)) {
    return res.status(401).type("html").send(unlockPage("That is not the password."));
  }
  const tok = crypto.randomBytes(32).toString("hex");
  SETUP_TICKETS.set(tok, { expires: Date.now() + SETUP_TTL_MS, navUsed: false });
  res.setHeader("Set-Cookie",
    `${SETUP_COOKIE}=${tok}; HttpOnly; SameSite=Lax; Path=/; Max-Age=${SETUP_TTL_MS / 1000}`);
  res.redirect("/setup");
});

// Past the gate, and only past it.
app.use(express.static(path.join(__dirname, "ui")));

app.get("/", (_q, r) => r.sendFile(path.join(__dirname, "ui", "office.html")));
app.get("/control", (_q, r) => r.sendFile(path.join(__dirname, "ui", "agency.html")));
app.get("/setup", (_q, r) => r.sendFile(path.join(__dirname, "ui", "setup.html")));
app.get("/status", (_q, r) => r.sendFile(path.join(__dirname, "ui", "status.html")));

/* IS THE RUNNING PROGRAM OLDER THAN THE PAGE READING IT?
   The screens are files on disk, re-read on every refresh. This file is read
   ONCE, when the process starts. Pull an update, refresh the browser, and you
   are looking at a new screen driven by an old server — a form whose route does
   not exist, a switch whose endpoint 404s. That has now cost the owner three
   separate afternoons: the password screen said "could not reach the server"
   about a server that was up, then the sector switches silently did nothing,
   then the website name he typed never appeared.

   Telling him to restart in a chat message is not a fix; the software has to
   say it. Every screen calls this on load and shows a banner if the answer is
   404 (a server too old to have this route at all) or if something it needs is
   missing from `api`.

   Deliberately cheap: no Python, no database, no .env parsing. It is called on
   every page load and must never be the slow thing. --------------------------- */
const API_CAPABILITIES = [
  "password", "activity", "activity-seen", "activity-clear", "sector", "usage",
  "notify-preview", "chat", "faults", "comms", "sectors", "site-name",
  "setup-unlock", "brand", "ping", "leads", "triage", "ntfy-replies",
  "stuck", "release",
];
const SERVER_STARTED = Math.floor(Date.now() / 1000);
app.get("/api/ping", (_q, res) => {
  res.json({ ok: true, api: API_CAPABILITIES, started: SERVER_STARTED });
});

/* THE BRAND, AND NOTHING ELSE.
   The office header needs the agency's name and what the website is called.
   It used to get those from /api/settings, which also carries the ntfy topic,
   the SMTP user and the model routing — and /api/settings is behind the setup
   password now. A front-of-house screen should not be asking for the key
   cupboard to learn what the firm is called, so it asks for exactly this. */
app.get("/api/brand", (_q, res) => {
  res.json({ ok: true, agencyName: (readEnv().AGENCY_NAME || "").trim(),
             website: siteLabel() });
});

// STATE — the Control Room's snapshot. Reads the filing cabinet via Python
// (coordination/state.py), then merges in the .env-derived connections here.
app.get("/api/state", (_q, res) => {
  const st = pyState();
  if (!st || !st.ok) return res.json({ ok: false, error: (st && st.error) || "state unavailable" });
  st.connections = readConnections();
  res.json(st);
});

// CHAT — talk to one agent about its own work.
//
// This used to echo the message back with the agent's name in brackets, which
// looked like a reply and said nothing. It now goes to coordination.agent_chat,
// which assembles that desk's real record from the cabinet and asks the sector's
// routed brain to explain it. Facts come from rows; the model only does the
// wording. With no brain configured it still answers, from the record alone.
app.post("/api/chat", (req, res) => {
  const b = req.body || {};
  const agent = String(b.agent || "").trim();
  const message = String(b.message || "").slice(0, 4000);
  if (!agent) return res.status(400).json({ ok: false, error: "Which agent?" });
  const py = resolvePython();
  if (!py) return res.json({ ok: false, error: NO_PY });
  const r = spawnSync(py.cmd, [...py.prefix, "-m", "coordination.agent_chat"], {
    cwd: __dirname, input: JSON.stringify({ agent, message }),
    timeout: 180000, maxBuffer: 8 * 1024 * 1024,
  });
  if (r.error) return res.json({ ok: false, error: "Could not run Python: " + r.error.message });
  const out = String(r.stdout || "");
  if (!out) return res.json({ ok: false, error: "agent_chat error: " + String(r.stderr || "no output") });
  try { return res.json(JSON.parse(out)); }
  catch (_) { return res.json({ ok: false, error: "bad reply: " + out.slice(0, 300) }); }
});

// ---- helpers ---------------------------------------------------------------
const ENV_PATH = path.join(__dirname, ".env");
const DB_PATH = path.join(__dirname, "coordination", "agency.db");
const GSC_SA_PATH = path.join(__dirname, "config", "gsc-service-account.local.json");

// external service integrations stored in .env (secrets flagged for keyless re-save).
// WhatsApp used to sit here; it was dropped because enquiries come through the
// widget on the site and the owner answers them himself — no agent read it.
const INTEGRATIONS = {
  email: { fields: ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "MAIL_FROM", "MAIL_FROM_NAME"], secret: ["SMTP_PASS"] },
};

// the agency's tasks — each binds to a provider on the Routing screen
const AGENCY_TASKS = [
  { task: "md", label: "Managing Director & Critic" },
  { task: "it", label: "IT sector" },
  { task: "content", label: "Content sector" },
  { task: "seo", label: "SEO sector" },
  { task: "leads", label: "Leads sector" },
  { task: "report", label: "Report sector" },
  { task: "security", label: "Security sector" },
];

// read .env into a { KEY: value } map (used only to report what's filled)
// The settings the agency reads by name. Only these may be overridden from the
// real environment — copying the whole of process.env in would put PATH and
// every CI variable into what the office renders as the owner's settings.
// Mirrors ENV_KEYS in providers/integrations.py.
const ENV_KEYS = new Set([
  "SITE_URL", "SITE_MODE", "SITE_NAME", "AGENCY_NAME",
  "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "SMTP_FROM",
  "OUTREACH_DOMAIN", "OUTREACH_DAILY_CAP", "WHATSAPP_DAILY_CAP",
  "NTFY_SERVER", "NTFY_TOPIC", "NTFY_TOKEN",
  "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
  "ALGOLIA_APP_ID", "ALGOLIA_SEARCH_KEY", "ALGOLIA_WRITE_KEY", "ALGOLIA_INDEX",
  "GOOGLE_PLACES_API_KEY", "GSC_PROPERTY",
  "DAILY_SPEND_CAP_USD", "ZYTE_COST_PER_FETCH", "ZYTE_COST_PER_RENDER",
  "CRAWLER_ON_OWN_SITE", "LEADS_ALLOW_INDIVIDUALS",
  "SEO_SEEDS", "SEO_AREAS", "SEO_STRIKING_TO", "SEO_W_AREA", "SEO_W_ENQUIRIES",
  "PORT",
]);

// .env, with the real environment on top. The environment used to be ignored
// here and in providers/integrations.py, while run.py --check told the owner to
// "set SITE_URL=... for one run" and coordination/site.py honoured it — so one
// setting was set and not set at the same time depending on who asked, and the
// preflight that decides whether the day may start was the one saying not set.
// What is actually written down. Kept separate from readEnv() so that a value
// exported for ONE RUN cannot be written permanently into .env the next time
// the owner presses Save on a completely unrelated section of /setup.
function readEnvFile() {
  const map = {};
  if (fs.existsSync(ENV_PATH)) {
    for (const line of fs.readFileSync(ENV_PATH, "utf8").split(/\r?\n/)) {
      const m = line.match(/^([A-Z0-9_]+)=(.*)$/);
      if (m) map[m[1]] = m[2];
    }
  }
  return map;
}

function readEnv() {
  const map = readEnvFile();
  for (const k of new Set([...Object.keys(map), ...ENV_KEYS])) {
    const v = process.env[k];
    if (v !== undefined && String(v).trim() !== "") map[k] = String(v).trim();
  }
  return map;
}

// merge keys into .env WITHOUT disturbing the rest — this is why saving one
// section never wipes another (the bug the owner hit with the old single button).
function updateEnv(partial) {
  const merged = { ...readEnvFile(), ...partial };
  if (!merged.PORT) merged.PORT = process.env.PORT || "3000";
  const order = [
    "SITE_MODE", "SITE_URL", "DATAFORSEO_LOGIN", "DATAFORSEO_PASSWORD",
    "NTFY_SERVER", "NTFY_TOPIC", "NTFY_TOKEN",
    "ALGOLIA_APP_ID", "ALGOLIA_SEARCH_KEY", "ALGOLIA_WRITE_KEY", "ALGOLIA_INDEX",
    // Telegram keys are kept in the file if they're already there — an owner
    // mid-switch shouldn't lose his fallback — but nothing writes them now.
    "AGENCY_NAME", "SITE_NAME",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "OUTREACH_DOMAIN",
    "ZYTE_COST_PER_FETCH", "ZYTE_COST_PER_RENDER", "CRAWLER_ON_OWN_SITE",
    "ADMIN_NAME", "ADMIN_PASSWORD_HASH",
    "DAILY_SPEND_CAP_USD", "OUTREACH_DAILY_CAP", "SITE_API_BASE", "SITE_API_TOKEN", "PORT",
  ];
  const keys = [...order.filter((k) => k in merged),
                ...Object.keys(merged).filter((k) => !order.includes(k))];
  const body = [
    "# Written by the setup screen — secrets stored here only, never committed.",
    ...keys.map((k) => `${k}=${String(merged[k] == null ? "" : merged[k]).replace(/[\r\n]+/g, " ")}`),
    "",
  ].join("\n");
  fs.writeFileSync(ENV_PATH, body, { mode: 0o600 });
  try { fs.chmodSync(ENV_PATH, 0o600); } catch (_) {}
}

// Every table the app expects. A cabinet built by an OLDER version is missing the
// newer ones (that's what made the shelf board fail), so this list is what decides
// whether the schema needs applying again — not just "does the file exist".
const REQUIRED_TABLES = ["tasks", "decisions", "keywords", "shelf_queue", "borrows",
                         "targets", "daily_orders"];

function dbBuilt() {
  if (!fs.existsSync(DB_PATH)) return false;
  try {
    const buf = fs.readFileSync(DB_PATH);
    return REQUIRED_TABLES.every((t) => buf.includes(t));
  } catch (_) { return false; }
}

// find a working Python 3, once. Tries PYTHON_BIN, then python3 / python / py -3,
// so it works on Linux, macOS and Windows without the owner configuring anything.
let PY; // {cmd, prefix} once resolved, or null if none found
function resolvePython() {
  if (PY !== undefined) return PY;
  const cands = [];
  if (process.env.PYTHON_BIN) cands.push({ cmd: process.env.PYTHON_BIN, prefix: [] });
  cands.push({ cmd: "python3", prefix: [] }, { cmd: "python", prefix: [] }, { cmd: "py", prefix: ["-3"] });
  for (const c of cands) {
    try {
      const r = spawnSync(c.cmd, [...c.prefix, "--version"], { timeout: 5000 });
      if (!r.error && r.status === 0 && /Python\s+3\./.test(String(r.stdout || "") + String(r.stderr || ""))) {
        PY = c;
        return PY;
      }
    } catch (_) { /* try next */ }
  }
  PY = null;
  return PY;
}
const NO_PY = "Python 3 wasn't found on this computer. Install Python 3 (python.org) and " +
  "restart the server, or set PYTHON_BIN to the full path of your python.exe / python3.";

// APPLY THE SCHEMA ON EVERY BOOT, not only when the cabinet looks missing.
//
// This used to skip the whole thing when dbBuilt() said yes — and dbBuilt() is
// a byte-scan of the database file for a handful of table NAMES from an old
// version. Any cabinet built at any point in the past passes that test, so a
// release that added a table shipped a database that never got it. Three
// tables reached the owner that way: sector_switches (which crashed when he
// tried to switch a sector off), read_marks (which swallowed every write, so
// his activity count reset on every poll) and faults (which killed the
// Overseer's pass on its first write).
//
// schema.sql is CREATE ... IF NOT EXISTS throughout, so running it every time
// costs a few milliseconds and closes the hole permanently. Python's own
// board._migrate does the same on its side; either route now heals a cabinet.
function ensureDb() {
  const py = resolvePython();
  if (!py) return { built: dbBuilt(), ran: false, error: NO_PY };
  try {
    const r = spawnSync(py.cmd, [...py.prefix, "coordination/init_db.py"],
                        { cwd: __dirname, timeout: 20000 });
    return { built: dbBuilt(), ran: true,
             error: r.status === 0 ? null : String(r.stderr || "") };
  } catch (e) {
    return { built: dbBuilt(), ran: true, error: e.message };
  }
}

// shell into the Python provider module (the single code path for provider ops)
function pyProvider(command, payload, timeoutMs) {
  const py = resolvePython();
  if (!py) return { ok: false, error: NO_PY };
  const r = spawnSync(py.cmd, [...py.prefix, "-m", "providers.api", command], {
    cwd: __dirname, input: JSON.stringify(payload || {}),
    timeout: timeoutMs || 300000, maxBuffer: 8 * 1024 * 1024,
  });
  if (r.error) return { ok: false, error: "Could not run Python: " + r.error.message };
  const out = String(r.stdout || "");
  if (!out) return { ok: false, error: "provider module error: " + String(r.stderr || "no output") };
  try { return JSON.parse(out); }
  catch (_) { return { ok: false, error: "bad provider output: " + out.slice(0, 300) }; }
}

// Shell into any agency module the same way. Arguments go through as an ARRAY,
// never a shell string, so an owner typing a quote into their daily orders
// cannot turn into a command.
function pyModule(mod, args, timeoutMs) {
  const py = resolvePython();
  if (!py) return { ok: false, error: NO_PY };
  const r = spawnSync(py.cmd, [...py.prefix, "-m", mod, ...(args || [])], {
    cwd: __dirname, timeout: timeoutMs || 120000, maxBuffer: 8 * 1024 * 1024,
  });
  if (r.error) return { ok: false, error: "Could not run Python: " + r.error.message };
  const out = String(r.stdout || "");
  if (!out) return { ok: false, error: mod + " error: " + String(r.stderr || "no output") };
  try { return JSON.parse(out); }
  catch (_) { return { ok: false, error: "bad output from " + mod + ": " + out.slice(0, 300) }; }
}

// shell into the state reader (same single code path — the server reads no DB itself)
function pyState() {
  const py = resolvePython();
  if (!py) return { ok: false, error: NO_PY };
  const r = spawnSync(py.cmd, [...py.prefix, "-m", "coordination.state"], {
    cwd: __dirname, timeout: 30000, maxBuffer: 8 * 1024 * 1024,
  });
  if (r.error) return { ok: false, error: "Could not run Python: " + r.error.message };
  const out = String(r.stdout || "");
  if (!out) return { ok: false, error: "state module error: " + String(r.stderr || "no output") };
  try { return JSON.parse(out); }
  catch (_) { return { ok: false, error: "bad state output: " + out.slice(0, 300) }; }
}

// WHAT THE OWNER'S WEBSITE IS CALLED.
//
// Worked out by coordination/site.py and only there, because the office, the
// control room and the status page each used to answer this for themselves —
// and one of them answered with "Glassworks Dubai / glassworks.ae" written into
// the HTML as a constant. No amount of saving on the setup screen could change
// a name that was never read from anywhere.
//
// Falls back to the raw .env values if Python cannot be reached, so the header
// degrades to the real address rather than to somebody else's business.
function siteLabel() {
  const out = pyModule("coordination.site", ["--json"], 15000);
  if (out && out.ok) return {
    siteName: out.siteName, siteHost: out.siteHost, siteInitials: out.siteInitials,
    siteSource: out.siteSource, siteConfigured: out.siteConfigured,
  };
  const e = readEnv();
  const url = (e.SITE_URL || "").trim();
  const host = url.replace(/^[a-z]+:\/\//i, "").split("/")[0].replace(/^www\./i, "");
  const name = (e.SITE_NAME || "").trim() || host;
  return { siteName: name, siteHost: host, siteConfigured: !!url,
           siteInitials: name ? name.split(".")[0].slice(0, 2).toUpperCase() : "—",
           siteSource: url ? ".env" : null };
}

// the outside-world connections, from .env — what's wired and what isn't
function readConnections() {
  const e = readEnv();
  const has = (k) => !!(e[k] && e[k].trim());
  return [
    { ic: "◧", name: "Website", sub: e.SITE_URL || "not set", on: has("SITE_URL") },
    { ic: "🔔", name: "ntfy", sub: has("NTFY_TOPIC") ? "topic set" : "not set", on: has("NTFY_TOPIC") },
    ...(has("TELEGRAM_BOT_TOKEN")
        ? [{ ic: "✈", name: "Telegram (legacy)", sub: "still configured — ntfy is the route now", on: true }]
        : []),
    { ic: "🔎", name: "Algolia site search",
      sub: has("ALGOLIA_APP_ID") ? (e.ALGOLIA_INDEX || "glasswork_site") : "not set",
      on: has("ALGOLIA_APP_ID") && has("ALGOLIA_SEARCH_KEY") },
    { ic: "✉", name: "Email (SMTP)", sub: e.SMTP_HOST || "not set", on: has("SMTP_HOST") && has("SMTP_USER") && has("SMTP_PASS") },
    { ic: "⌗", name: "Search Console", sub: e.GSC_PROPERTY || "not set", on: has("GSC_PROPERTY") && fs.existsSync(GSC_SA_PATH) },
  ];
}

// ---- website (its own Save button) -----------------------------------------
app.post("/api/website", (req, res) => {
  try {
    const b = req.body || {};
    const cur = readEnv();
    const mode = String(b.mode || "readonly").toLowerCase() === "full" ? "full" : "readonly";
    const siteUrl = String(b.siteUrl || "").trim();
    if (!siteUrl) return res.status(400).json({ ok: false, error: "Enter your website URL." });

    // JSON.stringify -> a safely-escaped string that is also a valid Python literal
    const py = (v) => JSON.stringify(String(v == null ? "" : v));
    let siteLine, apiBase = "", apiToken = "";
    if (mode === "full") {
      apiBase = String(b.apiBase || "").trim() || cur.SITE_API_BASE || "";
      apiToken = String(b.apiToken || "").trim() || cur.SITE_API_TOKEN || "";  // keyless re-save keeps it
      if (!apiBase || !apiToken)
        return res.status(400).json({ ok: false, error: "Full access needs your site's admin API URL and token." });
      siteLine = `SITE = CustomSite(${py(apiBase)}, ${py(apiToken)})`;
    } else {
      siteLine = `SITE = ReadOnlySite(${py(siteUrl)})`;
    }
    // What to CALL the site on screen. Optional — left blank, every screen
    // falls back to the domain itself, which is at least his. The office used
    // to have a name and a domain written into the HTML as constants, so it
    // showed a business that was not his no matter what he saved here.
    const siteName = String(b.siteName || "").trim().slice(0, 80);
    updateEnv({
      SITE_MODE: mode, SITE_URL: siteUrl, SITE_NAME: siteName,
      SITE_API_BASE: mode === "full" ? apiBase : "",
      SITE_API_TOKEN: mode === "full" ? apiToken : "",
    });

    // The address also goes in a TRACKED file. .env is gitignored because it
    // holds the API token and the mail password, so a website address saved
    // only there never reaches a fresh clone -- the owner sets his site, then
    // every agent elsewhere reports "no SITE_URL set" as if he never had.
    // A public URL is not a credential, so it lives in config/site.json and
    // travels with the repo. Secrets stay above, in .env, and only there.
    try {
      const cfgDir = path.join(__dirname, "config");
      fs.mkdirSync(cfgDir, { recursive: true });
      fs.writeFileSync(path.join(cfgDir, "site.json"),
        JSON.stringify({ url: siteUrl, mode, name: siteName }, null, 2) + "\n");
    } catch (e) {
      console.error("could not write config/site.json:", e.message);
    }

    const dir = path.join(__dirname, "mcp", "site_connector");
    const connPath = path.join(dir, "connector.py");
    const examplePath = path.join(dir, "connector.example.py");
    let conn = fs.existsSync(connPath) ? fs.readFileSync(connPath, "utf8")
             : fs.existsSync(examplePath) ? fs.readFileSync(examplePath, "utf8") : null;
    if (conn === null) return res.status(500).json({ ok: false, error: "Connector template not found." });
    conn = /^SITE\s*=.*$/m.test(conn)
      ? conn.replace(/^SITE\s*=.*$/m, siteLine)
      : conn.replace(/\s*$/, "\n") + siteLine + "\n";
    fs.writeFileSync(connPath, conn);

    const db = ensureDb();
    return res.json({ ok: true, mode, siteUrl, siteName,
                      database: db.built ? "ready" : "not built" });
  } catch (e) {
    return res.status(500).json({ ok: false, error: "Could not save: " + e.message });
  }
});

// ---- optional extras (its own Save button; blank fields are left untouched) --
app.post("/api/optional", (req, res) => {
  try {
    const b = req.body || {};
    const cur = readEnv();
    const one = (v) => String(v == null ? "" : v).replace(/[\r\n]+/g, " ").trim();
    const partial = {};
    const set = (k, v) => { const x = one(v); if (x) partial[k] = x; };  // only overwrite when given
    // DataForSEO no longer has fields on the setup screen. A login already in
    // .env is left alone — updateEnv merges over what is there — so nobody who
    // was using it loses it; there is simply no longer a box implying he should.
    // ntfy is the owner's route. Telegram keys are still READ if they exist in
    // .env, so an older setup keeps working, but they are no longer offered.
    set("NTFY_TOPIC", b.ntfyTopic);
    set("NTFY_SERVER", b.ntfyServer);
    set("AGENCY_NAME", b.agencyName);
    set("OUTREACH_DOMAIN", b.outreachDomain);
    // Algolia: search on the owner's OWN website. Three values with three very
    // different rules — the app id and the search key are meant to be public
    // (the search box queries from the visitor's browser), the write key is not
    // and never leaves the server. Kept as separate fields so the two keys can
    // never be confused for one "the Algolia key".
    set("ALGOLIA_APP_ID", b.algoliaAppId);
    set("ALGOLIA_SEARCH_KEY", b.algoliaSearchKey);
    set("ALGOLIA_WRITE_KEY", b.algoliaWriteKey);
    set("ALGOLIA_INDEX", b.algoliaIndex);
    // Crawler rates. Blank means "use the built-in cheapest-tier default", so a
    // blank field must CLEAR the key rather than keep an old number the owner
    // has just deleted on purpose — the one place in this handler where empty
    // is an instruction, not an omission. A number that will not parse is
    // dropped rather than saved, so a typo can never become his spend rate.
    const rate = (k, v) => {
      if (v === undefined) return;                       // field not sent at all
      const x = one(v);
      if (!x) { partial[k] = ""; return; }
      const n = Number(x);
      if (Number.isFinite(n) && n >= 0) partial[k] = String(n);
    };
    rate("ZYTE_COST_PER_FETCH", b.zyteCostFetch);
    rate("ZYTE_COST_PER_RENDER", b.zyteCostRender);
    // A select always has a definite value, so "off" is a real answer here.
    if (b.crawlerOwnSite !== undefined) {
      partial.CRAWLER_ON_OWN_SITE = one(b.crawlerOwnSite) ? "1" : "";
    }
    partial.DAILY_SPEND_CAP_USD = one(b.spendCap) || cur.DAILY_SPEND_CAP_USD || "15";
    partial.OUTREACH_DAILY_CAP = one(b.outreachCap) || cur.OUTREACH_DAILY_CAP || "25";
    updateEnv(partial);
    return res.json({ ok: true });
  } catch (e) {
    return res.status(500).json({ ok: false, error: "Could not save: " + e.message });
  }
});

// ---- external integrations (each saves on its own; secrets kept on blank) ---
app.post("/api/integration", (req, res) => {
  try {
    const b = req.body || {};
    const group = String(b.group || "");
    const vals = b.values || {};
    const one = (v) => String(v == null ? "" : v).replace(/[\r\n]+/g, " ").trim();

    if (group === "email") {
      const map = INTEGRATIONS[group];
      const partial = {};
      for (const f of map.fields) {
        const x = one(vals[f]);
        if (map.secret.includes(f)) { if (x) partial[f] = x; }  // keyless re-save keeps it
        else partial[f] = x;
      }
      updateEnv(partial);
      return res.json({ ok: true });
    }

    if (group === "gsc") {
      updateEnv({ GSC_PROPERTY: one(vals.GSC_PROPERTY) });
      const sa = String(vals.GSC_SERVICE_ACCOUNT_JSON || "").trim();
      if (sa) {  // big JSON blob -> its own git-ignored file, not .env
        try { JSON.parse(sa); } catch (_) {
          return res.status(400).json({ ok: false, error: "The service-account key must be the full JSON file (it isn't valid JSON)." });
        }
        fs.mkdirSync(path.dirname(GSC_SA_PATH), { recursive: true });
        fs.writeFileSync(GSC_SA_PATH, sa, { mode: 0o600 });
        try { fs.chmodSync(GSC_SA_PATH, 0o600); } catch (_) {}
      }
      return res.json({ ok: true });
    }
    return res.status(400).json({ ok: false, error: "Unknown integration." });
  } catch (e) {
    return res.status(500).json({ ok: false, error: "Could not save: " + e.message });
  }
});

// ---- AI providers (each credential saves independently) --------------------
app.get("/api/providers", (_q, res) => res.json(pyProvider("view", {})));
app.post("/api/providers/credential", (req, res) => res.json(pyProvider("save_credential", req.body)));
app.post("/api/providers/credential/delete", (req, res) => res.json(pyProvider("delete_credential", req.body)));
app.post("/api/providers/model", (req, res) => res.json(pyProvider("save_model", req.body)));
app.post("/api/providers/model/delete", (req, res) => res.json(pyProvider("delete_model", req.body)));
app.post("/api/providers/fetch-models", (req, res) => res.json(pyProvider("fetch_models", req.body)));
app.post("/api/providers/test", (req, res) => res.json(pyProvider("test_model", req.body)));

// ---- the shelf (shared API cabinet) ----------------------------------------
// Tool APIs (SEO data, stock graphics, email, crawler…) are shelf items; the
// shelf board shows who holds each shelf, who's queued, and today's spend.
app.post("/api/providers/tool", (req, res) => res.json(pyProvider("save_tool", req.body)));
app.post("/api/providers/tool/delete", (req, res) => res.json(pyProvider("delete_tool", req.body)));
app.post("/api/providers/tool/test", (req, res) => res.json(pyProvider("test_tool", req.body)));
app.get("/api/shelves", (_q, res) => res.json(pyProvider("shelves", {})));
// How much of each third-party allowance is gone. Counts are facts; the money
// column is the owner's own rate from .env, never a quote from the provider.
app.get("/api/usage", (_q, res) =>
  res.json(pyModule("coordination.usage", ["--json"], 20000)));
// What would reach the owner's phone right now, and what would not. Reads only
// — the whole point is that he can see the list WITHOUT being sent it.
app.get("/api/notify/preview", (_q, res) =>
  res.json(pyModule("coordination.notify", ["--json"], 25000)));

// One sector in full: its score with the reasoning behind it, what it actually
// produced, its tasks, its runs, and the requests either way. This is what a
// room opens into when it is clicked — the owner asked to be able to read what
// a sector made, not just how many rows it made.
app.get("/api/sector/:key", (req, res) => {
  const key = String(req.params.key || "").replace(/[^A-Za-z]/g, "").slice(0, 20);
  res.json(pyModule("coordination.state", ["--sector", key], 25000));
});

// ACTIVITY, with an unread count. `since` is the timestamp the browser last
// opened the panel; everything newer is unread. The count lives here rather
// than in the page so it survives a refresh and agrees across tabs.
// ---- the activity log, and how far he has read ------------------------------
// The read mark is kept in the cabinet, not the browser. localStorage is
// per-origin, so the office on localhost and the control room on 127.0.0.1 held
// two different marks and clearing one left the other showing the same number.
// `since` is still accepted for a caller asking a hypothetical; left off, the
// stored mark is used, which is what both screens now do.
app.get("/api/activity", (req, res) => {
  const since = parseInt(req.query.since, 10);
  const args = ["--json"];
  if (Number.isFinite(since) && since > 0) args.push("--since", String(since));
  res.json(pyModule("coordination.activity", args, 25000));
});

// ---- the leads behind the number ---------------------------------------------
// "Leads today: 0" with nothing behind it is a score, not a tool. This is who
// they are, where they came from, and what is holding each one up.
app.get("/api/leads", (req, res) => {
  const scope = req.query.scope === "all" ? "all" : "today";
  res.json(pyModule("coordination.leads_view", ["--json", "--scope", scope], 25000));
});

// ---- the owner's per-sector on/off switches ----------------------------------
// His agency, his call. A sector he switches off keeps its agents, its history
// and its score — it simply does no work: the MD cannot post to it, nothing on
// the board can be claimed by it, and its step in the day is skipped and says
// so. Enforced in coordination/board.py, not here, so a switch means the same
// thing whether it was flipped on this screen or from a terminal.
app.get("/api/sectors", (_q, res) => {
  res.json(pyModule("coordination.state", ["--switches"], 25000));
});

app.post("/api/sectors", (req, res) => {
  const b = req.body || {};
  const sector = String(b.sector || "").replace(/[^A-Za-z]/g, "").slice(0, 20);
  if (!sector) return res.status(400).json({ ok: false, error: "Which sector?" });
  const on = b.enabled === true || b.enabled === "true" || b.enabled === 1;
  const args = ["--switch", `${sector}=${on ? "on" : "off"}`];
  if (typeof b.note === "string" && b.note.trim()) args.push(b.note.trim().slice(0, 300));
  res.json(pyModule("coordination.state", args, 25000));
});

// ---- what the sectors have actually said to each other -----------------------
// The office floor used to animate eight hardcoded phrases at random, which
// looked like the agency working on a morning when nothing had run. Both the
// beams and the readable panel come from here now — the real request board.
app.get("/api/comms", (req, res) => {
  const n = Math.max(1, Math.min(200, parseInt(req.query.limit, 10) || 40));
  res.json(pyModule("coordination.comms", ["--json", "--limit", String(n)], 25000));
});

// ---- faults in the agency itself --------------------------------------------
// Not the website: the AGENTS. A stall, a crash, a claimed task nobody
// finished, a sector ignoring another sector. Read-only here — the scan that
// files and routes them runs as a step of the day, not off a page load.
app.get("/api/faults", (req, res) => {
  const args = ["--json", "--list"];
  if (req.query.sector) args.push("--sector", String(req.query.sector).slice(0, 40));
  res.json(pyModule("coordination.faults", args, 25000));
});

// Opening the panel IS reading it. The screen posts the timestamp of the newest
// line it actually showed him — not its own clock, which can be wrong — and the
// mark only ever moves forward, so a second tab holding an older read cannot
// resurrect a count he has just cleared.
app.post("/api/activity/seen", (req, res) => {
  const b = req.body || {};
  const to = parseInt(b.seen_to, 10);
  // Three things the buttons on the panel can ask for. `clear` HIDES what has
  // been read — it never deletes, because every event is derived from a row the
  // work itself wrote, and `showAll` puts it straight back.
  const flag = b.showAll ? "--show-all" : b.clear ? "--clear" : "--mark-read";
  const args = ["--json", flag];
  if (flag !== "--show-all" && Number.isFinite(to) && to > 0) args.push(String(to));
  res.json(pyModule("coordination.activity", args, 25000));
});
app.post("/api/shelves/resolve", (req, res) => res.json(pyProvider("resolve_shelf", req.body)));

// ---- channels + the whole-system sweep -------------------------------------
// Both prove credentials without sending anything. The one exception is
// notify:true, which posts to the owner's own ntfy topic and nowhere else.
app.post("/api/integrations/test", (req, res) =>
  res.json(pyProvider("test_integration", req.body)));
// A full sweep calls every model and every shelf API, so it gets its own,
// longer budget than an ordinary provider command.
app.post("/api/preflight", (req, res) =>
  res.json(pyProvider("preflight", req.body, 900000)));

// ---- THE RUN BUTTON ---------------------------------------------------------
// Three endpoints, one for each beat of "check, ask me, then work".
//
//   /api/run/check   the whole preflight — cabinet, sectors, pipelines, APIs.
//                    Blocking, so the owner watches it finish before deciding.
//   /api/run/start   the day itself. NOT blocking: a day takes many minutes and
//                    a request held open that long dies to a proxy timeout, so
//                    this hands off to a detached process and answers at once.
//                    Everything it does lands in the cabinet, which is what
//                    /api/run/status and the office floor read.
//   /api/run/status  where that process has got to.
app.post("/api/run/check", (req, res) => {
  const quick = !!(req.body && req.body.quick);
  res.json(pyModule("pipelines.day_run",
    ["--check", "--json", ...(quick ? ["--quick"] : [])], 900000));
});

app.get("/api/run/status", (_q, res) =>
  res.json(pyModule("pipelines.day_run", ["--status", "--json"], 60000)));

// ---- STOP ------------------------------------------------------------------
// "there is no any stop button that makes the agents or overseer to understand
// that the task is stopped by the user."
//
// The run is a DETACHED process — /api/run/start hands off and answers at once,
// so there is no child handle here to signal. The stop goes through the cabinet
// instead: day_run.stop() writes the request, waits for the run to close its
// own books, and ends the process it recorded if it will not. That means this
// works on a run started before the server was last restarted, which a child
// handle would not have.
//
// The budget is the grace period plus room to kill and tidy up. It blocks on
// purpose: he pressed Stop and is owed an answer about whether it stopped, not
// an "asked it to" that leaves him watching the same screen.
app.post("/api/run/stop", (req, res) => {
  const why = String((req.body && req.body.why) || "").slice(0, 300);
  res.json(pyModule("pipelines.day_run",
    ["--stop", "--json", ...(why ? ["--why", why] : [])], 120000));
});

app.post("/api/run/start", (req, res) => {
  const py = resolvePython();
  if (!py) return res.json({ ok: false, error: NO_PY });
  const b = req.body || {};

  // Refuse a second press here as well as in Python. Python holds the real
  // guard (the day_run resource lock); this one exists so the owner gets a
  // plain answer instead of a process that starts, loses the lock race and
  // dies silently a second later.
  const st = pyModule("pipelines.day_run", ["--status", "--json"], 60000);
  if (st && st.run && st.run.running) {
    return res.json({ ok: false, busy: true,
      why: "A day is already running — wait for it to finish. Two at once means two MDs planning the same day." });
  }

  // `orders` must be a plain object of {sector: text}. Object.entries happily
  // takes a string and hands back index/character pairs, and an array turns
  // into "0","1","2" — either way you get orders addressed to sectors that do
  // not exist. There are seven sectors; anything past a couple of dozen entries
  // is not a person typing.
  const raw = b.orders;
  const orders = {};
  if (raw && typeof raw === "object" && !Array.isArray(raw)) {
    for (const [k, v] of Object.entries(raw).slice(0, 24)) {
      if (typeof v !== "string" && typeof v !== "number") continue;
      const text = String(v).slice(0, 4000).trim();
      if (text) orders[String(k).slice(0, 40)] = text;
    }
  }
  const payload = JSON.stringify({
    orders,
    note: String(b.note || "").slice(0, 4000),
    // Strictly true, not merely truthy. force overrides a check that said a
    // sector cannot run, so the string "no" must not be enough to trip it.
    force: b.force === true,
    // The owner just watched the full sweep finish in /api/run/check, so the
    // re-check here is the cheap one: it catches a key deleted while the form
    // was open without paying for every model call a second time.
    quick: true,
    skip_check: false,
  });

  let child;
  try {
    child = spawn(py.cmd, [...py.prefix, "-m", "pipelines.day_run", "--stdin", "--json"], {
      cwd: __dirname, detached: true, stdio: ["pipe", "ignore", "ignore"],
    });
  } catch (e) {
    return res.json({ ok: false, error: "Could not start the run: " + e.message });
  }
  child.on("error", () => { /* the cabinet stays empty; status says so */ });
  try { child.stdin.end(payload); } catch (_) {}
  child.unref();
  res.json({ ok: true, started: true, orders,
    why: "Running. Watch the office floor — the lamps come on as each sector picks up." });
});

// ---- the owner's yes or no on one amber item --------------------------------
// The Control Room's Approve button used to change an opacity and say "sent to
// the agent". This is where it now actually goes.
app.post("/api/approve", (req, res) => {
  const py = resolvePython();
  if (!py) return res.json({ ok: false, error: NO_PY });
  const b = req.body || {};
  const r = spawnSync(py.cmd, [...py.prefix, "-m", "coordination.approve"], {
    cwd: __dirname,
    input: JSON.stringify({ kind: String(b.kind || ""), id: b.id,
                            decision: String(b.decision || "") }),
    timeout: 60000, maxBuffer: 4 * 1024 * 1024,
  });
  if (r.error) return res.json({ ok: false, error: "Could not run Python: " + r.error.message });
  const out = String(r.stdout || "");
  if (!out) return res.json({ ok: false, error: "approve error: " + String(r.stderr || "no output") });
  try { return res.json(JSON.parse(out)); }
  catch (_) { return res.json({ ok: false, error: "bad reply: " + out.slice(0, 300) }); }
});

// ---- put a stuck job back on the board --------------------------------------
// A claimed task belongs to one agent and nobody else may touch it. The cost of
// that rule: if the holder never comes back the job is held for ever, and the
// office draws a red desk the owner can do nothing about. The Overseer clears
// dead claims on its own pass; this is the manual door for when he is looking
// at the desk. board.release_claim does the judging — it refuses a claim that
// is merely slow unless he forces it, and records a forced one as forced.
app.get("/api/stuck", (_q, res) =>
  res.json(pyModule("coordination.release", ["--json"], 60000)));

app.post("/api/release", (req, res) => {
  const py = resolvePython();
  if (!py) return res.json({ ok: false, error: NO_PY });
  const b = req.body || {};
  if (!b.id) return res.status(400).json({ ok: false, error: "Send { id }." });
  const r = spawnSync(py.cmd, [...py.prefix, "-m", "coordination.release", "--stdin"], {
    cwd: __dirname,
    input: JSON.stringify({ id: String(b.id).slice(0, 64),
                            by: String(b.by || "owner").slice(0, 40),
                            reason: String(b.reason || "").slice(0, 300),
                            force: !!b.force }),
    timeout: 60000, maxBuffer: 4 * 1024 * 1024,
  });
  if (r.error) return res.json({ ok: false, error: "Could not run Python: " + r.error.message });
  const out = String(r.stdout || "");
  if (!out) return res.json({ ok: false, error: "release error: " + String(r.stderr || "no output") });
  try { return res.json(JSON.parse(out)); }
  catch (_) { return res.json({ ok: false, error: "bad reply: " + out.slice(0, 300) }); }
});

// ---- the owner's standing orders, per sector --------------------------------
// The same boxes the Run panel shows, settable without starting a day. One
// form, one store: an order saved here is the order the MD reads when the day
// runs, so there is no second place for the two to disagree.
app.get("/api/orders", (_q, res) =>
  res.json(pyModule("coordination.proposals", ["--json"], 60000)));

app.post("/api/orders", (req, res) => {
  const py = resolvePython();
  if (!py) return res.json({ ok: false, error: NO_PY });
  const raw = (req.body || {}).orders;
  if (!raw || typeof raw !== "object" || Array.isArray(raw))
    return res.status(400).json({ ok: false, error: "Send { orders: { Sector: text } }." });
  const set = [], failed = [];
  for (const [k, v] of Object.entries(raw).slice(0, 24)) {
    if (typeof v !== "string" && typeof v !== "number") continue;
    const sector = String(k).slice(0, 40);
    const text = String(v).slice(0, 4000).trim();
    // Arguments go through as an ARRAY, never a shell string, so an owner
    // typing a quote into his orders cannot turn into a command.
    const r = pyModule("coordination.proposals", ["--order", sector, text, "--json"], 60000);
    (r && r.ok !== false ? set : failed).push(sector);
  }
  res.json({ ok: failed.length === 0, set, failed,
    why: failed.length ? `No such sector: ${failed.join(", ")}.` : "Saved." });
});

// ---- what every sector and agent is doing right now -------------------------
// Both are also folded into /api/state so the office gets them in one poll;
// these exist for anything that wants just the one thing.
app.get("/api/readiness", (_q, res) =>
  res.json(pyModule("coordination.readiness", ["--json"], 60000)));
// (There was a second /api/activity handler here. Express answers with the
// first route that matches, so it had never run once — but it took no `since`
// and ignored the read mark, and the next person to reorder this file would
// have got a badge that never cleared and no idea why. Deleted, not moved.)

// ---- the owner's daily targets ---------------------------------------------
// The numbers the agency works to. Read and written through coordination.targets
// so the ceiling checks and the measurement live in one place — the server does
// not know what a safe outreach volume is, and should not have an opinion.
app.get("/api/targets", (_q, res) => res.json(pyModule("coordination.targets", ["--json"])));

app.post("/api/targets", (req, res) => {
  const b = req.body || {};
  const args = [];
  const JOBS = ["leads", "mails", "posts", "site_checks"];
  for (const k of JOBS) {
    if (b[k] === undefined || b[k] === null || b[k] === "") continue;
    const n = parseInt(b[k], 10);
    if (Number.isNaN(n) || n < 0)
      return res.status(400).json({ ok: false, error: `${k} must be a whole number, 0 or more.` });
    if (n > 100000)
      return res.status(400).json({ ok: false, error: `${k} of ${n} is not a real target.` });
    args.push("--set", `${k}=${n}`);
  }
  if (typeof b.orders === "string") args.push("--orders", b.orders.slice(0, 4000));
  if (!args.length) return res.status(400).json({ ok: false, error: "Nothing to save." });
  args.push("--json");
  return res.json(pyModule("coordination.targets", args));
});

// ---- what our own website says we sell --------------------------------------
// Reads SITE_URL and reconciles it against the service catalogue. Slow (it
// fetches several pages), so it is only ever run on demand from the button.
app.post("/api/site-services", (_q, res) =>
  res.json(pyModule("pipelines.site_services", ["--json"], 180000)));

// ---- per-task routing ------------------------------------------------------
app.get("/api/routing", (_q, res) => res.json(pyProvider("get_routing", {})));
app.post("/api/routing", (req, res) => res.json(pyProvider("save_route", req.body)));
app.get("/api/tasks", (_q, res) => res.json({ ok: true, tasks: AGENCY_TASKS }));

// ---- current settings (to pre-fill the setup form) -------------------------
// Returns only non-secret values, plus a boolean for each secret so the form can
// show "saved — leave blank to keep" instead of an empty box.
app.get("/api/settings", (_q, res) => {
  const e = readEnv();
  const v = (k) => e[k] || "";
  const set = (k) => !!(e[k] && e[k].trim());
  res.json({
    ok: true,
    configured: set("SITE_URL"),
    website: {
      mode: String(e.SITE_MODE || "readonly").toLowerCase() === "full" ? "full" : "readonly",
      siteUrl: v("SITE_URL"),
      // Name, bare host and initials for the switcher, worked out in ONE place
      // (coordination/site.py) so the office, the control room and the agents
      // cannot disagree about what the owner's website is called.
      ...siteLabel(),
      apiBase: v("SITE_API_BASE"),
      apiTokenSet: set("SITE_API_TOKEN"),
    },
    optional: {
      ntfyTopic: v("NTFY_TOPIC"), ntfyServer: v("NTFY_SERVER"),
      algoliaAppId: v("ALGOLIA_APP_ID"), algoliaSearchKey: v("ALGOLIA_SEARCH_KEY"),
      // the write key is reported as set / not set, never echoed back
      algoliaWriteKeySet: set("ALGOLIA_WRITE_KEY"), algoliaIndex: v("ALGOLIA_INDEX"),
      agencyName: v("AGENCY_NAME"),
      outreachDomain: v("OUTREACH_DOMAIN"),
      spendCap: v("DAILY_SPEND_CAP_USD"), outreachCap: v("OUTREACH_DAILY_CAP"),
      zyteCostFetch: v("ZYTE_COST_PER_FETCH"), zyteCostRender: v("ZYTE_COST_PER_RENDER"),
      crawlerOwnSite: ["1", "true", "yes", "on"].includes(
        String(e.CRAWLER_ON_OWN_SITE || "").trim().toLowerCase()),
    },
    integrations: {
      email: { host: v("SMTP_HOST"), port: v("SMTP_PORT"), user: v("SMTP_USER"),
               from: v("MAIL_FROM"), fromName: v("MAIL_FROM_NAME"), passSet: set("SMTP_PASS") },
      gsc: { property: v("GSC_PROPERTY"), serviceAccountSet: fs.existsSync(GSC_SA_PATH) },
    },
  });
});

// ---- status ----------------------------------------------------------------
app.get("/api/status", (_q, res) => {
  const e = readEnv();
  const has = (k) => !!(e[k] && e[k].trim());
  // Whether the machine is locked, so the Setup gate can tell the truth rather
  // than warning about a password that is already set. Boolean only — the hash
  // never leaves here.
  const locked = has("ADMIN_PASSWORD_HASH");
  const adminName = (e.ADMIN_NAME || "").trim();
  const mode = String(e.SITE_MODE || "readonly").toLowerCase() === "full" ? "full" : "readonly";
  const fullReady = has("SITE_API_BASE") && has("SITE_API_TOKEN");

  // which provider + model each task (MD + sectors) resolves to
  const ps = pyProvider("status", {});
  const tasks = (ps && ps.tasks) || {};
  const brains = AGENCY_TASKS.map((t) => {
    const s = tasks[t.task];
    const ok = !!(s && s.ok);
    return {
      key: t.task, label: t.label, ok,
      detail: ok ? s.detail : ((s && s.detail) || "No provider chosen yet — pick one on the Routing screen"),
    };
  });
  const brainsReady = brains.filter((b) => b.ok).length;
  const brainsTotal = brains.length;

  const checks = [
    { label: "Website", optional: false, ok: has("SITE_URL"),
      detail: has("SITE_URL")
        ? (mode === "full" ? "Full access — agents can read and update your site"
                           : "Read-only — agents plan and track; any change is queued for your approval")
        : "No website address saved yet",
      hint: "Set this on the setup screen under Website." },
    { label: "Site access (full mode only)", optional: false, ok: mode !== "full" ? true : fullReady,
      detail: mode !== "full" ? "Not needed — you're in read-only mode"
        : (fullReady ? "Admin API address and token saved" : "Full access is on, but the admin API address/token is missing"),
      hint: "Only needed if you switch Website mode to Full access." },
    { label: "Filing cabinet (database)", optional: false, ok: dbBuilt(),
      detail: dbBuilt() ? "Built and ready" : "Not built yet — it builds itself when you save the website form",
      hint: "No action needed — created for you on first save." },
    // The DataForSEO row is gone. A paid keyword API is not a setup step for
    // this agency any more — SEO runs on Search Console, autocomplete, his own
    // enquiries and rival pages. If he ever wants volumes or rank tracking it
    // goes on the seo_data shelf like any other API, and shows up there.
    { label: "ntfy (your approvals)", optional: true, ok: has("NTFY_TOPIC"),
      detail: has("NTFY_TOPIC")
        ? `Sending to ${e.NTFY_TOPIC} on ${e.NTFY_SERVER || "https://ntfy.sh"}`
        : (has("TELEGRAM_BOT_TOKEN") && has("TELEGRAM_CHAT_ID")
            ? "Not set — falling back to your old Telegram bot"
            : "Not set — nothing will reach your phone for approval"),
      hint: "Pick a long, private topic name and subscribe to it in the ntfy app." },
    { label: "Sending domain", optional: true, ok: has("OUTREACH_DOMAIN"),
      detail: has("OUTREACH_DOMAIN") ? e.OUTREACH_DOMAIN : "Not set — outreach stays off",
      hint: "A subdomain you send cold email from." },
    { label: "Spend & outreach caps", optional: true, ok: has("DAILY_SPEND_CAP_USD") && has("OUTREACH_DAILY_CAP"),
      detail: (has("DAILY_SPEND_CAP_USD") && has("OUTREACH_DAILY_CAP"))
        ? `$${e.DAILY_SPEND_CAP_USD}/day spend, ${e.OUTREACH_DAILY_CAP} messages/day` : "Using safe defaults (15/day, 25/day)",
      hint: "Safety limits the Overseer enforces." },
    { label: "Email sending", optional: true, ok: has("SMTP_HOST") && has("SMTP_USER") && has("SMTP_PASS"),
      detail: (has("SMTP_HOST") && has("SMTP_USER") && has("SMTP_PASS"))
        ? `SMTP ready (${e.SMTP_HOST})` : "Not set — outreach and graphical emails can't be sent yet",
      hint: "Your mail SMTP host, username and password, plus a from-address." },
    { label: "Google Search Console", optional: true, ok: has("GSC_PROPERTY") && fs.existsSync(GSC_SA_PATH),
      detail: (has("GSC_PROPERTY") && fs.existsSync(GSC_SA_PATH))
        ? `Connected (${e.GSC_PROPERTY})` : "Not set — no owned-site search data",
      hint: "Property (e.g. sc-domain:yoursite.ae) + a service-account JSON key." },
  ];

  res.json({
    locked, adminName,
    // WHAT THIS PROCESS CAN DO. The screens are static files re-read on every
    // request; this file is read once, at boot. Pull an update and refresh the
    // browser and you get the new screen talking to the old server, which
    // answers a route it has never heard of with an HTML 404 — and a screen
    // parsing JSON reads that as "the server is down". The screens check this
    // list before offering a control, so a stale process is named as a stale
    // process. Add the name here when you add a route a screen depends on.
    api: API_CAPABILITIES,
    ok: true, mode,
    providers: { credentials: (ps && ps.credentials) || 0, models: (ps && ps.models) || 0 },
    brains, brainsReady, brainsTotal, checks,
  });
});

/* ---------------------------------------------------------------------------
   AN API PATH ANSWERS IN JSON, ALWAYS — even when it fails.
   ---------------------------------------------------------------------------
   Express's built-in 404 and error handler both send HTML. Every screen in this
   app does `await r.json()`, so an HTML answer throws in the same catch block a
   dead socket does, and the screen says "could not reach the server" about a
   server that answered perfectly clearly. That cost the owner an afternoon on
   the password screen: he had pulled a new UI, the old Node process was still
   running, /api/password 404'd in HTML, and the screen blamed the network.

   These two go last, after every real route, so they only catch what nothing
   else claimed. Anything under /api/ now fails in the language the caller
   speaks. Pages are untouched — a browser typing a wrong URL still wants HTML.
--------------------------------------------------------------------------- */
app.use("/api", (req, res) => {
  res.status(404).json({ ok: false, notFound: true,
    error: `This server has no ${req.method} /api${req.path}. If the screen ` +
           "offering it is newer than the running server, restart the server." });
});

app.use((err, req, res, _next) => {
  console.error("unhandled:", err && err.stack ? err.stack : err);
  const body = { ok: false, error: String((err && err.message) || err || "server error") };
  if (res.headersSent) return res.end();
  if (String(req.path || "").startsWith("/api/")) return res.status(500).json(body);
  res.status(500).type("text/plain").send(body.error);
});

const PORT = process.env.PORT || 3000;

// LISTEN ON THIS MACHINE ONLY.
//
// app.listen(PORT) with no host binds every interface, which on a laptop means
// the setup screen is served to the whole network. On café, hotel, co-working
// or office wifi, anyone who guessed the address could open /setup and read the
// DataForSEO login, the ntfy topic, the site admin token and the sending
// domain, then press Run. Nothing about the app said so.
//
// BIND_HOST=0.0.0.0 still works for anyone deliberately hosting this on a VPS
// behind a proxy — but it has to be asked for, in writing, and it says loudly
// what it just did.
const HOST = process.env.BIND_HOST || "127.0.0.1";
app.listen(PORT, HOST, () => {
  // Upgrade the cabinet on boot. schema.sql is CREATE TABLE IF NOT EXISTS
  // throughout, so this is safe to run every time and adds any table a newer
  // version introduced — an existing database is never left half-upgraded.
  const db = ensureDb();
  if (db.built) console.log("filing cabinet ready");
  else console.log("filing cabinet NOT ready:", db.error || "unknown");
  console.log(`Agency running on http://${HOST}:${PORT}`);
  if (HOST === "127.0.0.1" || HOST === "localhost") {
    console.log("Reachable from this computer only.");
  } else {
    console.log(`!! BIND_HOST=${HOST} — this is reachable from the network. ` +
      "Anyone who can reach this port can read your keys on /setup. " +
      "Set an admin password on the Setup screen, or put it behind a proxy.");
  }
  if (adminHash()) console.log("Admin password is ON.");
});
