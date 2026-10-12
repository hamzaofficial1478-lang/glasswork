/*
 * server.js — the one Node app you host. Serves the UIs and the shared state.
 *   /          -> the Virtual Office (front of house)
 *   /control   -> the Control Room dashboard (back office)
 *   /setup     -> the setup screen (website, the Claude team, integrations, shelf)
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
const http = require("http");
const os = require("os");
const { spawn, spawnSync } = require("child_process");
const app = express();

// A route that waits on Python is async; an error thrown inside one would
// otherwise never reach Express — the request would hang, or the server stop.
// Each async handler is given its error path back.
for (const m of ["get", "post", "put", "delete"]) {
  const orig = app[m].bind(app);
  app[m] = (route, ...handlers) => {
    if (!handlers.length) return orig(route);          // app.get("setting")
    return orig(route, ...handlers.map((h) =>
      (typeof h === "function" && h.constructor && h.constructor.name === "AsyncFunction")
        ? (req, res, next) => h(req, res, next).catch((e) => {
            if (!res.headersSent) res.status(500).json({ ok: false, error: String(e && e.message || e) });
            else next(e);
          })
        : h));
  };
}
process.on("unhandledRejection", (e) => console.error("unhandled:", e && e.message || e));

/* ---------------------------------------------------------------------------
   THE CODE ON DISK, AGAINST THE CODE RUNNING
   ---------------------------------------------------------------------------
   "clicked on try again button ... the message appear unknow action"

   The screens are re-read from disk on every refresh; this file is read once,
   at start. He pulled an update, refreshed, got the new Mail room with its new
   "Try again now" button — and the server still running was the one from
   before, which had never heard of it. The fourth time a new screen has met an
   old server (see ui/stale.js for the other three).

   So the server watches its own file. Started by run.py (OFFICE_SUPERVISED=1),
   it finishes whatever it is answering and exits with 75, and run.py starts
   the new version straight away — nothing for him to do. Started any other
   way, it cannot restart itself safely, so /api/ping says it is stale and
   every screen puts up a banner saying to restart it. */
const OWN_CODE = [__filename];
function codeStamp() {
  try {
    const h = crypto.createHash("sha1");
    for (const f of OWN_CODE) h.update(fs.readFileSync(f));
    return h.digest("hex");
  } catch (_) { return ""; }
}
const BOOT_CODE = codeStamp();
function codeIsNewer() { const now = codeStamp(); return !!now && !!BOOT_CODE && now !== BOOT_CODE; }
const SUPERVISED = process.env.OFFICE_SUPERVISED === "1";
const RESTART_CODE = 75;
let ACTIVE = 0;                                  // requests being answered right now
app.use((req, res, next) => {
  ACTIVE++;
  let done = false;
  const end = () => { if (!done) { done = true; ACTIVE--; } };
  res.on("finish", end); res.on("close", end);
  next();
});
// ANY WRITE MAKES EVERY KEPT ANSWER OLD (see ANSWERS KEPT below). Mounted this
// early so it sees every route, the account and setup ones included. Bumped
// when the write starts, so a read already under way cannot keep what it read
// before it; and again when it ends, so a read that ran during it cannot either.
let KEPT_GEN = 0;
const READS_BY_POST = [/^\/api\/content\/post\/\d+\/score$/, /^\/api\/run\/check$/];
app.use((req, res, next) => {
  if (req.method !== "GET" && req.method !== "HEAD" && req.path.startsWith("/api/")
      && !READS_BY_POST.some((r) => r.test(req.path))) {
    KEPT_GEN++;
    res.on("finish", () => { KEPT_GEN++; });
  }
  next();
});
if (SUPERVISED) {
  setInterval(() => {
    if (ACTIVE > 0 || !codeIsNewer()) return;
    console.log("The office was updated — restarting to load the new version.");
    process.exit(RESTART_CODE);
  }, 4000).unref();
}

// Request bodies are small everywhere but one place: a report's logo, sent as
// a picture, may be a few hundred KB. Only that path gets the larger limit.
const JSON_BODY = express.json();
const JSON_LOGO = express.json({ limit: "1mb" });
// A post saved from the editor carries the pictures added to it.
const JSON_POST = express.json({ limit: "16mb" });
app.use((req, res, next) => (req.path === "/api/check/brand" ? JSON_LOGO
  : /^\/api\/content\/post\/\d+\/(save|score)$/.test(req.path) ? JSON_POST : JSON_BODY)(req, res, next));
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

/* ---------------------------------------------------------------------------
   ACCOUNTS — once anyone has made one, the office is theirs to sign into.
   ---------------------------------------------------------------------------
   Each person's data is encrypted (coordination/vault.py). Signing in hands
   this server the account's key; it is kept HERE, in memory, for as long as
   they are signed in, and given to each program the office starts
   (AGENCY_KEY). It is never written to disk. Signed out, nothing is readable.

   One person signed in at a time on a computer: signing in replaces whoever
   was before. Until the first account exists, the front door below behaves
   exactly as it always has. --------------------------------------------- */
const ACCOUNT_COOKIE = "glasswork_account";
const ACCOUNT_SESSIONS = new Set();
let SIGNED_IN = null;                // { account, name, key: Buffer } while signed in
const ACCOUNT_OPEN = new Set(["/account", "/api/account/list", "/api/account/sign-in",
  "/api/account/sign-up", "/api/account/recover", "/api/ping",
  // Read before an account exists: the terms are agreed at sign-up.
  "/terms", "/privacy", "/api/product"]);

function accountsExist() {
  try {
    const dir = path.join(process.env.AGENCY_DATA || path.join(__dirname, "data"), "accounts");
    return fs.readdirSync(dir).some((d) => fs.existsSync(path.join(dir, d, "account.json")));
  } catch (_) { return false; }
}
// "the other user using the program may not get access to the other user" —
// every copy installed from Setup, and any fresh copy, asks for an account
// before anything else: each person's work is then encrypted with their own
// password from the very first thing they do, and nothing is written in the
// program's folder. Only a copy that was already in use before accounts (his
// own, with its original files) opens as it always has, until he brings it in.
// Looked at once, here, before the office makes a cabinet of its own.
const LEGACY_AT_START = fs.existsSync(path.join(__dirname, "coordination", "agency.db"))
  || fs.existsSync(path.join(__dirname, ".env")) || !!process.env.AGENCY_DB;
function accountRequired() {
  return accountsExist() || process.env.GLASSWORK_INSTALLED === "1" || !LEGACY_AT_START;
}
function accountToken(req) {
  const raw = req.headers.cookie || "";
  for (const part of raw.split(";")) {
    const i = part.indexOf("=");
    if (i > 0 && part.slice(0, i).trim() === ACCOUNT_COOKIE) return part.slice(i + 1).trim();
  }
  return "";
}
function signedIn(req) { return !!SIGNED_IN && ACCOUNT_SESSIONS.has(accountToken(req)); }

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
  if (accountRequired()) {
    // Accounts exist, or must: the account sign-in is the front door, and the
    // old single admin password no longer applies.
    if (ACCOUNT_OPEN.has(req.path)) return next();
    if (signedIn(req)) {
      // Signed in with no website open yet: nothing but the account page, or
      // the office would be reading the program's ORIGINAL files — someone
      // else's agency, before it has been brought into an account.
      if (activeSite() || req.path.startsWith("/api/account/")) return next();
      if (req.path.startsWith("/api/")) {
        return res.status(409).json({ ok: false, needsSite: true,
          error: "Add a website to your account first, at /account." });
      }
      return res.redirect("/account");
    }
    if (req.path.startsWith("/api/")) {
      return res.status(401).json({ ok: false, signin: true, needsAccount: !accountsExist(),
        error: accountsExist() ? "Signed out. Sign in at /account." : "Make your account first, at /account." });
    }
    return res.redirect("/account");
  }
  if (!adminHash()) return next();                       // no password set
  if (req.path === "/login" || req.path === "/api/login") return next();
  if (SESSIONS.has(cookieToken(req))) return next();
  if (req.path.startsWith("/api/")) {
    return res.status(401).json({ ok: false, error: "Locked. Sign in at /login." });
  }
  return res.redirect("/login");
});

app.get("/login", async (_q, res) => {
  res.type("html").send(`<!doctype html><meta charset="utf-8">
<title>Glasswork</title>
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
  <h1>Glasswork</h1>
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

app.post("/api/logout", async (req, res) => {
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
   and the SMTP user, /api/team changes which Claude model a sector uses (and
   its test spends allowance), and /api/providers lists the shelf. ---------- */
const SETUP_COOKIE = "glasswork_setup";
const SETUP_TICKETS = new Map();      // token -> { expires, navUsed }
const SETUP_TTL_MS = 10 * 60 * 1000;  // long enough to finish, short enough to matter

// Everything the Setup screen reads or writes. Prefix match, so
// /api/providers/tool is covered by /api/providers and /api/team/set by /api/team.
const SETUP_API = [
  "/api/settings", "/api/website", "/api/optional", "/api/integration",
  "/api/providers", "/api/team", "/api/preflight", "/api/shelves",
  // The Engine page: the AI keys and the spending cap. /api/engine-state (no
  // keys, just which engine and today's spend) is a different path, not gated.
  "/api/engine",
  // The website connection: the connector download carries the website's secret.
  "/api/connect",
  // The developer's Home: making it and its download carry his key.
  "/api/publisher/setup", "/api/publisher/home.zip", "/api/publisher/publish",
];
// Two connection calls carry no secret — how the website is connected, and a
// knock to test it — so the Website screen may use them without the lock. The
// connector download, which holds the secret, stays behind it.
const SETUP_OPEN = new Set(["GET /api/connect", "POST /api/connect/check"]);
// The pages behind the same lock. Unlocking returns you to the one you asked for.
const SETUP_PAGES = new Set(["/setup", "/setup.html", "/engine", "/engine.html"]);

function setupToken(req) {
  const raw = req.headers.cookie || "";
  for (const part of raw.split(";")) {
    const i = part.indexOf("=");
    if (i > 0 && part.slice(0, i).trim() === SETUP_COOKIE) return part.slice(i + 1).trim();
  }
  return "";
}

// A fresh ticket, as a cookie. Also handed out at sign-up: the person typed
// their new password a moment ago, and the next screen asks them to choose
// their AI engine — asking for the password again there helps nobody.
function setupCookie() {
  const tok = crypto.randomBytes(32).toString("hex");
  SETUP_TICKETS.set(tok, { expires: Date.now() + SETUP_TTL_MS, navUsed: false });
  return `${SETUP_COOKIE}=${tok}; HttpOnly; SameSite=Lax; Path=/; Max-Age=${SETUP_TTL_MS / 1000}`;
}

function setupTicket(req) {
  const t = SETUP_TICKETS.get(setupToken(req));
  if (!t) return null;
  if (Date.now() > t.expires) { SETUP_TICKETS.delete(setupToken(req)); return null; }
  return t;
}

function unlockPage(err, next) {
  const engine = String(next || "").startsWith("/engine");
  const back = engine ? "/engine" : "/setup";
  return `<!doctype html><meta charset="utf-8">
<title>${engine ? "AI engine" : "Setup"} — password required</title>
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
  <h1>&#128274; ${engine ? "AI engine" : "Setup"}</h1>
  <p>This screen holds ${engine ? "your AI keys" : "every API key in the agency, your model routing"} and your
     spend cap. Being signed in is not enough &mdash; it asks each time you open it.</p>
  <input type="hidden" name="next" value="${back}">
  <input type="password" name="password" placeholder="Your password" autofocus required>
  <button>Open Setup</button>
  ${err ? `<div class="err">${err}</div>` : ""}
  <a href="/">&larr; Back to the office</a>
</form>`;
}

app.use((req, res, next) => {
  if (!adminHash() && !accountsExist()) return next();   // no password: nothing to ask
  const isPage = req.method === "GET" && SETUP_PAGES.has(req.path);
  const isApi = SETUP_API.some((p) => req.path === p || req.path.startsWith(p + "/"))
    && !SETUP_OPEN.has(req.method + " " + req.path);
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
    return res.type("html").send(unlockPage("", req.path));
  }
  if (t) return next();
  return res.status(401).json({ ok: false, needsSetupUnlock: true,
    error: "Setup is locked. Open the Setup screen and enter your password." });
});

app.post("/api/setup/unlock", express.urlencoded({ extended: false }), JSON_BODY, async (req, res) => {
  const supplied = (req.body && req.body.password) || "";
  const ok = accountsExist()
    ? !!(SIGNED_IN && (await pyAccounts("check", { account: SIGNED_IN.account, password: supplied })).ok)
    : passwordOk(supplied);
  const next = (req.body && req.body.next) === "/engine" ? "/engine" : "/setup";
  // The app asks for the password in place (the Users room, the Publisher
  // card) and wants a plain answer, not a page to follow.
  const wantsJson = req.is("application/json") || (req.body && req.body.json === true);
  if (!ok) {
    if (wantsJson) return res.status(401).json({ ok: false, error: "That is not the password." });
    return res.status(401).type("html").send(unlockPage("That is not the password.", next));
  }
  res.setHeader("Set-Cookie", setupCookie());
  if (wantsJson) return res.json({ ok: true });
  res.redirect(next);
});

// Past the gate, and only past it.
app.use(express.static(path.join(__dirname, "ui")));

/* ---- ANSWERS KEPT, WHILE THEY ARE STILL TRUE ---------------------------------
   "the program lags while changing sections or buttons it should be fast"

   Every screen is a Python program started for the asking, and on Windows
   starting one is the slow part. Most of the time nothing has changed since the
   last time the same screen asked — so the answer is kept, and given again at
   once, for as long as it is provably the same answer:

     - the cabinet file has not changed (its size and time, read each time —
       any agent writing anything changes them),
     - nothing has been saved, approved or pressed since (any POST, above),
     - and it is younger than its limit, because some lines are about time
       ("2 min ago", a run that went quiet) rather than about the cabinet.

   Only answers that worked are kept; an error is always asked again. */
const KEEP = [
  ["/api/state", 15000], ["/api/activity", 15000], ["/api/run/status", 8000],
  ["/api/review", 20000], ["/api/leads", 30000], ["/api/content/topics", 60000],
  ["/api/comms", 20000], ["/api/leftovers", 20000], ["/api/index-status", 60000],
  ["/api/orders", 30000], ["/api/results", 30000], ["/api/site/changes", 30000],
  ["/api/check/list", 15000], ["/api/brand", 60000], ["/api/engine-state", 20000],
  ["/api/sectors", 30000], ["/api/readiness", 30000], ["/api/faults", 30000],
  ["/api/sector/", 20000], ["/api/mail/sent/", 60000], ["/api/content/post/", 30000],
];
const KEPT = new Map();
function keepFor(p) {
  for (const [k, ms] of KEEP) if (k.endsWith("/") ? p.startsWith(k) && p.length > k.length : p === k) return ms;
  return 0;
}
function cabinetStamp() {
  const db = dbPath();
  let stamp = db;
  for (const f of [db, db + "-wal"]) {
    try { const st = fs.statSync(f); stamp += `|${st.mtimeMs}|${st.size}`; } catch (_) { stamp += "|-"; }
  }
  return stamp;
}
app.use((req, res, next) => {
  if (req.method !== "GET") return next();
  const ms = keepFor(req.path);
  if (!ms) return next();
  const key = req.originalUrl, at = Date.now(), gen = KEPT_GEN, stamp = cabinetStamp();
  const hit = KEPT.get(key);
  if (hit && hit.gen === gen && hit.stamp === stamp && at - hit.at < ms) {
    res.set("X-Kept", "1");
    return res.json(hit.body);
  }
  const send = res.json.bind(res);
  res.json = (body) => {
    if (res.statusCode < 300 && body && typeof body === "object" && body.ok !== false
        && gen === KEPT_GEN) {
      if (KEPT.size > 300) for (const [k, v] of KEPT) if (v.gen !== KEPT_GEN) KEPT.delete(k);
      KEPT.set(key, { at, gen, stamp, body });
    }
    return send(body);
  };
  next();
});

// THE NEW DESIGN IS THE FRONT DOOR (ui/app.html) — "the ui was still same not
// as you showed here". The previous office keeps every panel at /classic while
// the remaining screens move across.
app.get("/", async (_q, r) => r.sendFile(path.join(__dirname, "ui", "app.html")));
app.get("/app", async (_q, r) => r.sendFile(path.join(__dirname, "ui", "app.html")));
app.get("/classic", async (_q, r) => r.sendFile(path.join(__dirname, "ui", "office.html")));
app.get("/control", async (_q, r) => r.sendFile(path.join(__dirname, "ui", "agency.html")));
app.get("/setup", async (_q, r) => r.sendFile(path.join(__dirname, "ui", "setup.html")));
app.get("/status", async (_q, r) => r.sendFile(path.join(__dirname, "ui", "status.html")));
app.get("/review", async (_q, r) => r.sendFile(path.join(__dirname, "ui", "review.html")));
app.get("/account", async (_q, r) => r.sendFile(path.join(__dirname, "ui", "account.html")));
app.get("/business", async (_q, r) => r.sendFile(path.join(__dirname, "ui", "business.html")));
app.get("/engine", async (_q, r) => r.sendFile(path.join(__dirname, "ui", "engine.html")));
app.get("/terms", async (_q, r) => r.sendFile(path.join(__dirname, "ui", "terms.html")));
app.get("/privacy", async (_q, r) => r.sendFile(path.join(__dirname, "ui", "privacy.html")));

/* ---- YOUR BUSINESS (coordination/profile.py) ------------------------------
   What this website's business is, sells, who buys it and where. Read and
   saved through Python, sealed like every other setting. The draft reads the
   business's own website and saves NOTHING: the page shows it, he corrects it,
   and only Save writes. */
app.get("/api/business", async (_q, res) => {
  res.json(await pyModule("coordination.profile", ["--json"], 60000));
});
app.get("/api/business/options", async (_q, res) => {
  res.json(await pyModule("coordination.profile", ["--options"], 60000));
});
app.post("/api/business", async (req, res) => {
  const py = resolvePython();
  if (!py) return res.json({ ok: false, error: NO_PY });
  const r = await spawnAsync(py.cmd, [...py.prefix, "-m", "coordination.profile", "--save"], {
    cwd: __dirname, input: JSON.stringify(req.body || {}), timeout: 60000,
  });
  try { return res.json(JSON.parse(String(r.stdout || "").trim().split("\n").pop())); }
  catch (_) { return res.json({ ok: false, error: "could not save: " + String(r.stderr || "").slice(-300) }); }
});
app.post("/api/business/draft", async (req, res) => {
  const url = String((req.body || {}).url || "").trim();
  res.json(await pyModule("pipelines.business_draft", url ? ["--url", url] : [], 300000));
});

/* ---- THE AI ENGINE (providers/engines.py) ---------------------------------
   Which company does the thinking — Claude, OpenAI or Gemini by the person's
   own API key, or Claude Code on his own copy — its models, the daily cap and
   today's spend. A key goes to Python on stdin and is never sent back: the
   page only ever learns whether one is saved, and its last four characters. */
app.get("/api/engine", async (_q, res) => res.json(await pyProvider("engine", {}, 60000)));
app.post("/api/engine", async (req, res) =>
  res.json(await pyProvider("engine_save", req.body || {}, 60000)));
app.post("/api/engine/test", async (req, res) =>
  res.json(await pyProvider("engine_test", req.body || {}, 600000)));
// Cheap and key-free, for the office's top bar and the first-run check.
app.get("/api/engine-state", async (_q, res) => res.json(await pyProvider("engine_brief", {}, 30000)));

/* ---- THE WEBSITE CONNECTION (pipelines/connect.py) -------------------------
   Your own connector (a zip made for this website, its secret inside), a
   WordPress Application Password, or read-only. The password goes to Python
   on stdin; nothing secret comes back except inside the zip itself. */
async function pyConnect(args, payload, timeoutMs) {
  const py = resolvePython();
  if (!py) return { ok: false, error: NO_PY };
  const r = await spawnAsync(py.cmd, [...py.prefix, "-m", "pipelines.connect", ...args, "--json"], {
    cwd: __dirname, input: payload ? JSON.stringify(payload) : "", timeout: timeoutMs || 120000,
    maxBuffer: 16 * 1024 * 1024,
  });
  try { return JSON.parse(String(r.stdout || "").trim().split("\n").pop()); }
  catch (_) { return { ok: false, error: "could not read the answer: " + String(r.stderr || "").slice(-300) }; }
}
app.get("/api/connect", async (_q, res) => res.json(await pyConnect([], null, 60000)));
app.get("/api/connect/connector.zip", async (req, res) => {
  const r = await pyConnect(["--zip-json"].concat(req.query.new ? ["--new-secret"] : []), null, 60000);
  if (!r.ok || !r.zip_b64) return res.status(400).json({ ok: false, error: r.error || "could not make it" });
  const name = String(r.filename || "connector.zip").replace(/[^A-Za-z0-9._-]/g, "_");
  res.setHeader("Content-Type", "application/zip");
  res.setHeader("Content-Disposition", `attachment; filename="${name}"`);
  res.setHeader("Cache-Control", "no-store");
  res.send(Buffer.from(r.zip_b64, "base64"));
});
app.post("/api/connect/check", async (_q, res) => res.json(await pyConnect(["--check"], null, 120000)));
app.post("/api/connect/wordpress", async (req, res) => {
  const b = req.body || {};
  res.json(await pyConnect(["--wordpress"], { user: b.user, password: b.password, site: b.site }, 120000));
});
app.post("/api/connect/readonly", async (_q, res) => res.json(await pyConnect(["--readonly"], null, 60000)));

/* ---- THE PRODUCT, ITS UPDATES, AND A REPORT FOR SUPPORT -------------------
   What the program is called and which version this is (config/product.json);
   signed updates for a copy that has no git (coordination/updates.py); and a
   support report with nothing private in it (coordination/diagnostics.py). */
function productInfo() {
  try { return JSON.parse(fs.readFileSync(path.join(__dirname, "config", "product.json"), "utf8")) || {}; }
  catch (_) { return {}; }
}
app.get("/api/product", async (_q, res) => {
  const p = productInfo();
  res.json({ ok: true, name: p.name || "Glasswork", version: p.version || "", tagline: p.tagline || "",
             support_email: p.support_email || "", installed: process.env.GLASSWORK_INSTALLED === "1" });
});
// CLOSE THE PROGRAM. An installed copy runs with no window to close, so the
// office closes itself: it answers, then stops; the launcher stops with it.
// Never while a day is running — stopping the office would orphan its work.
app.post("/api/quit", async (_q, res) => {
  const st = await pyModule("pipelines.day_run", ["--status", "--json"], 60000);
  if (st && st.run && st.run.running) {
    return res.json({ ok: false, error: "A day is running. Stop it first (Today → Stop the run), then close the program." });
  }
  res.json({ ok: true, why: "Closing. Open it again from the Start menu or the desktop whenever you like." });
  setTimeout(() => process.exit(0), 400);
});
// A new version: asked plainly, installed only when signed with the developer's key.
app.get("/api/update", async (_q, res) => res.json(await pyModule("coordination.updates", ["--check", "--json"], 60000)));
app.post("/api/update/apply", async (_q, res) =>
  res.json(await pyModule("coordination.updates", ["--apply", "--json"], 900000)));
app.post("/api/update/rollback", async (_q, res) =>
  res.json(await pyModule("coordination.updates", ["--rollback", "--json"], 120000)));
app.get("/api/diagnostics", async (_q, res) => res.json(await pyModule("coordination.diagnostics", ["--json"], 90000)));

/* ---- PROBLEM REPORTS, AND THE ANSWERS (coordination/support.py) ---------------
   "how user will send us reports about the program failure" — sealed on this
   computer so only the developer's own computer can open it. Nothing else is
   sent: no count, no registration, no number for this copy. */
const textField = (v, n) => String(v || "").slice(0, n);
const reportId = (v) => String(v || "").replace(/[^a-z0-9-]/gi, "").slice(0, 40);
app.get("/api/support/mine", async (_q, res) => res.json(await pyModule("coordination.support", ["--mine"], 60000)));
app.get("/api/support/preview", async (_q, res) => res.json(await pyModule("coordination.support", ["--preview"], 90000)));
app.post("/api/support/send", async (req, res) => {
  const b = req.body || {};
  res.json(await pyStdin("coordination.support", ["--send"], { title: textField(b.title, 200),
    text: textField(b.text, 20000), contact: textField(b.contact, 200), attach: b.attach !== false }, 120000));
});

/* ---- HIS SIDE: the reports, his answers, putting a version out -----------------
   coordination/publisher.py and ui/publisher.js are on his computer only, never
   in a copy he gives out. Without them none of this exists: /api/publisher says
   so, and everything under it answers 404. */
const PUBLISHER_HERE = fs.existsSync(path.join(__dirname, "coordination", "publisher.py"));
const pub = (args, ms = 60000) => pyModule("coordination.publisher", args, ms);
app.get("/api/publisher", async (_q, res) => res.json(PUBLISHER_HERE ? await pub(["--view"], 30000)
  : { ok: true, publisher: false, can_publish: false }));
app.use("/api/publisher", (_q, res, next) => PUBLISHER_HERE ? next()
  : res.status(404).json({ ok: false, error: "That is not part of this copy of the program." }));
app.post("/api/publisher/setup", async (req, res) => {
  const b = req.body || {};
  res.json(await pyStdin("coordination.publisher", ["--setup"], { site: textField(b.site, 300), email: textField(b.email, 200) }, 60000));
});
app.get("/api/publisher/home.zip", async (_q, res) => {
  const r = await pub(["--home-zip"]);
  if (!r.ok || !r.zip) return res.status(400).json({ ok: false, error: r.error || "could not make it" });
  res.setHeader("Content-Type", "application/zip");
  res.setHeader("Content-Disposition", 'attachment; filename="glasswork-home.zip"');
  res.setHeader("Cache-Control", "no-store");
  res.send(Buffer.from(r.zip, "base64"));
});
app.post("/api/publisher/check", async (_q, res) => res.json(await pub(["--check-home"])));
app.post("/api/publisher/collect", async (_q, res) => res.json(await pub(["--collect"], 300000)));
app.get("/api/publisher/inbox", async (_q, res) => res.json(await pub(["--inbox"])));
app.get("/api/publisher/read/:id", async (req, res) => res.json(await pub(["--read", reportId(req.params.id)])));
app.post("/api/publisher/remove", async (req, res) => res.json(await pub(["--remove", reportId((req.body || {}).id)])));
app.post("/api/publisher/answer", async (req, res) => {
  const b = req.body || {};
  const body = { id: reportId(b.id) };
  if (typeof b.status === "string") body.status = b.status.slice(0, 20);
  if (typeof b.reply === "string") body.reply = b.reply.slice(0, 5000);
  if (typeof b.fixed_in === "string") body.fixed_in = b.fixed_in.slice(0, 40);
  res.json(await pyStdin("coordination.publisher", ["--answer"], body, 60000));
});
// PUTTING A VERSION OUT takes minutes when the installer is built, longer than
// a request may be held open — so it runs on its own and leaves its progress in
// a file this route reads.
const publishJob = () => path.join(DATA_ROOT, "publish-job.local.json");
app.post("/api/publisher/publish", async (req, res) => {
  const py = resolvePython();
  if (!py) return res.json({ ok: false, error: NO_PY });
  try {
    const now = JSON.parse(fs.readFileSync(publishJob(), "utf8"));
    if (now && now.running && Date.now() / 1000 - (now.at || 0) < 1800)
      return res.json({ ok: false, busy: true, error: "A version is being put out already — " + (now.stage || "working") });
  } catch (_) { /* no job yet */ }
  const b = req.body || {};
  const args = [...py.prefix, "-m", "coordination.publisher", "--publish", "--job", publishJob(),
    "--notes", textField(b.notes, 2000)].concat(b.installer === true ? ["--installer"] : []);
  try {
    fs.mkdirSync(path.dirname(publishJob()), { recursive: true });
    fs.writeFileSync(publishJob(), JSON.stringify({ running: true, stage: "Starting", at: Math.floor(Date.now() / 1000) }));
    const child = spawn(py.cmd, args, { cwd: __dirname, detached: true, stdio: "ignore", windowsHide: true });
    child.on("error", () => {});
    child.unref();
  } catch (e) {
    return res.json({ ok: false, error: "Could not start: " + e.message });
  }
  res.json({ ok: true, started: true });
});
app.get("/api/publisher/job", async (_q, res) => {
  try { res.json({ ok: true, ...JSON.parse(fs.readFileSync(publishJob(), "utf8")) }); }
  catch (_) { res.json({ ok: true, running: false, none: true }); }
});

/* ---- READING A SENT MAIL, AND THE CONTENT SCREEN'S CONTROLS -------------------
   "we cannot read the sent mails" — coordination/review.py --sent-mail.
   Today's topic, delete, open, edit and score a post — coordination/posts.py.
   Nothing here writes to the website: a live post's edit is handed to the IT
   Releaser, tests first, like every other change. */
async function pyStdin(mod, args, payload, timeoutMs) {
  const py = resolvePython();
  if (!py) return { ok: false, error: NO_PY };
  const r = await spawnAsync(py.cmd, [...py.prefix, "-m", mod, ...args], {
    cwd: __dirname, input: JSON.stringify(payload || {}), timeout: timeoutMs || 120000,
    maxBuffer: 32 * 1024 * 1024,
  });
  try { return JSON.parse(String(r.stdout || "").trim().split("\n").pop()); }
  catch (_) { return { ok: false, error: "could not read the answer: " + String(r.stderr || "").slice(-300) }; }
}
const numId = (v) => (/^\d{1,9}$/.test(String(v || "")) ? String(v) : "");
app.get("/api/mail/sent/:lead", async (req, res) => {
  const id = numId(req.params.lead);
  if (!id) return res.status(404).json({ ok: false, error: "which mail?" });
  res.json(await pyModule("coordination.review", ["--sent-mail", id], 30000));
});
app.get("/api/content/topics", async (_q, res) => res.json(await pyModule("coordination.posts", ["--topics"], 60000)));
app.post("/api/content/choose", async (req, res) => {
  const b = req.body || {};
  res.json(await pyStdin("coordination.posts", ["--choose"],
    { key: String(b.key || "").slice(0, 120), topic: String(b.topic || "").slice(0, 120) }, 60000));
});
app.post("/api/content/write/:id", async (req, res) => {
  const id = numId(req.params.id);
  const py = resolvePython();
  if (!id) return res.status(404).json({ ok: false, error: "which post?" });
  if (!py) return res.json({ ok: false, error: NO_PY });
  try {
    const child = spawn(py.cmd, [...py.prefix, "-m", "coordination.posts", "--write", id], {
      cwd: __dirname, detached: true, stdio: "ignore", windowsHide: true,
    });
    child.on("error", () => {});
    child.unref();
  } catch (e) {
    return res.json({ ok: false, error: "Could not start the writer: " + e.message });
  }
  res.json({ ok: true, started: true, id });
});
app.get("/api/content/post/:id", async (req, res) => {
  const id = numId(req.params.id);
  if (!id) return res.status(404).json({ ok: false, error: "which post?" });
  res.json(await pyModule("coordination.posts", ["--get", id], 60000));
});
for (const what of ["save", "score"]) {
  app.post(`/api/content/post/:id/${what}`, async (req, res) => {
    const id = numId(req.params.id);
    if (!id) return res.status(404).json({ ok: false, error: "which post?" });
    const b = req.body || {};
    res.json(await pyStdin("coordination.posts", [`--${what}`, id],
      { title: String(b.title || ""), meta: String(b.meta || ""), html: String(b.html || "") }, 120000));
  });
}
app.post("/api/content/post/:id/delete", async (req, res) => {
  const id = numId(req.params.id);
  if (!id) return res.status(404).json({ ok: false, error: "which post?" });
  res.json(await pyModule("coordination.posts", ["--delete", id], 60000));
});
// A picture added in the editor, shown in the editor until the Releaser takes it up.
app.get("/api/content/pending/:name", async (req, res) => {
  const name = String(req.params.name || "");
  if (!/^[a-f0-9]{16,40}\.(jpg|png|webp|gif)$/.test(name)) return res.status(404).end();
  const r = await pyModule("coordination.posts", ["--pending-path", name], 30000);
  if (!r || !r.ok || !r.path) return res.status(404).end();
  res.setHeader("Cache-Control", "private, max-age=3600");
  res.sendFile(r.path);
});

/* ---- WEBSITE CHECK (pipelines/website_check.py) ----------------------------
   A report on any website, every desk's view of it. Starting one makes its
   record and hands the reading to a detached process, so the page can be
   closed and the check carries on; the page asks for its progress. Only ever
   GET and HEAD requests to the website, robots.txt followed — see the module. */
const CHECK_MOD = "pipelines.website_check";
const checkId = (req) => (/^\d{1,9}$/.test(String(req.params.id || "")) ? String(req.params.id) : "");
app.get("/api/check/list", async (_q, res) => res.json(await pyModule(CHECK_MOD, ["--list"], 30000)));
app.get("/api/check/brand", async (_q, res) => res.json(await pyModule(CHECK_MOD, ["--brand"], 30000)));
app.post("/api/check/brand", async (req, res) => {
  const py = resolvePython();
  if (!py) return res.json({ ok: false, error: NO_PY });
  const b = req.body || {};
  const r = await spawnAsync(py.cmd, [...py.prefix, "-m", CHECK_MOD, "--save-brand"], {
    cwd: __dirname, input: JSON.stringify({ name: b.name, logo: b.logo, remove_logo: !!b.remove_logo }),
    timeout: 30000,
  });
  try { return res.json(JSON.parse(String(r.stdout || "").trim().split("\n").pop())); }
  catch (_) { return res.json({ ok: false, error: "could not save: " + String(r.stderr || "").slice(-300) }); }
});
app.post("/api/check/start", async (req, res) => {
  const b = req.body || {};
  const url = String(b.url || "").trim().slice(0, 300);
  if (!url) return res.json({ ok: false, error: "Type the website's address." });
  const made = await pyModule(CHECK_MOD, ["--new", url, "--depth", b.depth === "full" ? "full" : "quick"], 60000);
  if (!made.ok) return res.json(made);
  const py = resolvePython();
  let child;
  try {
    child = spawn(py.cmd, [...py.prefix, "-m", CHECK_MOD, "--run", String(made.id), "--json"], {
      cwd: __dirname, detached: true, stdio: "ignore", windowsHide: true,
    });
  } catch (e) {
    await pyModule(CHECK_MOD, ["--delete", String(made.id)], 30000);
    return res.json({ ok: false, error: "Could not start the check: " + e.message });
  }
  child.on("error", () => { pyModule(CHECK_MOD, ["--stop", String(made.id)], 30000).catch(() => {}); });
  child.unref();
  res.json({ ok: true, id: made.id, url: made.url, depth: made.depth });
});
app.get("/api/check/:id", async (req, res) => {
  const id = checkId(req);
  if (!id) return res.status(404).json({ ok: false, error: "no such check" });
  res.json(await pyModule(CHECK_MOD, ["--get", id], 30000));
});
app.post("/api/check/:id/stop", async (req, res) => {
  const id = checkId(req);
  if (!id) return res.status(404).json({ ok: false, error: "no such check" });
  res.json(await pyModule(CHECK_MOD, ["--stop", id], 30000));
});
app.post("/api/check/:id/delete", async (req, res) => {
  const id = checkId(req);
  if (!id) return res.status(404).json({ ok: false, error: "no such check" });
  res.json(await pyModule(CHECK_MOD, ["--delete", id], 30000));
});
// The report as a page of its own: read on screen, printed, or saved as PDF.
app.get("/check/:id/report", async (req, res) => {
  const id = checkId(req);
  const py = resolvePython();
  if (!id || !py) return res.status(404).type("text/plain").send("No such report.");
  const r = await spawnAsync(py.cmd, [...py.prefix, "-m", CHECK_MOD, "--html", id], {
    cwd: __dirname, timeout: 60000, maxBuffer: 16 * 1024 * 1024,
  });
  const out = String(r.stdout || "");
  if (!out.startsWith("<!DOCTYPE html>")) return res.status(404).type("text/plain").send("That check has no report yet.");
  res.setHeader("Cache-Control", "no-store");
  res.type("html").send(out);
});
// And as a PDF, printed by the Edge or Chrome already on this computer.
app.get("/api/check/:id/pdf", async (req, res) => {
  const id = checkId(req);
  const py = resolvePython();
  if (!id) return res.status(404).json({ ok: false, error: "no such check" });
  if (!py) return res.json({ ok: false, error: NO_PY });
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "website-check-"));
  const out = path.join(dir, "report.pdf");
  const r = await spawnAsync(py.cmd, [...py.prefix, "-m", CHECK_MOD, "--pdf", id, out], {
    cwd: __dirname, timeout: 150000,
  });
  let ans = {};
  try { ans = JSON.parse(String(r.stdout || "").trim().split("\n").pop()); } catch (_) {}
  if (!ans.ok || !fs.existsSync(out)) {
    fs.rm(dir, { recursive: true, force: true }, () => {});
    return res.status(ans.no_browser ? 404 : 500).json({ ok: false, no_browser: !!ans.no_browser,
      error: ans.error || "the PDF could not be made" });
  }
  const name = String(ans.filename || "website-check.pdf").replace(/[^A-Za-z0-9._-]/g, "_");
  res.download(out, name, () => fs.rm(dir, { recursive: true, force: true }, () => {}));
});

/* ---- ACCOUNTS AND WEBSITES (coordination/accounts.py) ---------------------
   Passwords and recovery keys go to Python on stdin, never on a command line,
   and never into a log. What comes back with a key keeps it here, in memory. */
async function pyAccounts(command, payload) {
  const py = resolvePython();
  if (!py) return { ok: false, error: NO_PY };
  const r = await spawnAsync(py.cmd, [...py.prefix, "-m", "coordination.accounts", command], {
    cwd: __dirname, input: JSON.stringify(payload || {}), timeout: 180000,
  });
  if (r.error) return { ok: false, error: "Could not run Python: " + r.error.message };
  try { return JSON.parse(String(r.stdout || "").trim().split("\n").pop()); }
  catch (_) { return { ok: false, error: "accounts: " + String(r.stderr || r.stdout || "no answer").slice(-300) }; }
}

function startSession(res, account, name, keyHex) {
  SIGNED_IN = { account, name, key: Buffer.from(keyHex, "hex") };
  process.env.AGENCY_KEY = keyHex;            // every program the office starts gets it
  ACCOUNT_SESSIONS.clear();                  // one person at a time on this computer
  SESSIONS.clear(); SETUP_TICKETS.clear();
  const tok = crypto.randomBytes(32).toString("hex");
  ACCOUNT_SESSIONS.add(tok);
  res.setHeader("Set-Cookie", `${ACCOUNT_COOKIE}=${tok}; HttpOnly; SameSite=Lax; Path=/`);
}
function endSession() {
  SIGNED_IN = null;
  delete process.env.AGENCY_KEY;
  ACCOUNT_SESSIONS.clear(); SETUP_TICKETS.clear();
}
// After signing in or switching website: build or upgrade that website's cabinet.
async function cabinetReady() { try { await ensureDb(); } catch (_) {} }

function strip(r) {                          // the key never goes to the browser
  const out = { ...(r || {}) };
  delete out.key;
  return out;
}

// Open to anyone at the door, so it says nothing about anybody: not who is
// signed in (only that it is you, with your own cookie), not what is open.
app.get("/api/account/list", async (req, res) => {
  const { active: _open, ...r } = await pyAccounts("list", {});
  res.json({ ...r, signedIn: signedIn(req) ? { account: SIGNED_IN.account, name: SIGNED_IN.name } : null,
             someoneElse: !!SIGNED_IN && !signedIn(req) });
});

app.post("/api/account/sign-up", async (req, res) => {
  const b = req.body || {};
  const r = await pyAccounts("sign-up", { name: b.name, password: b.password, confirm: b.confirm });
  if (!r.ok) return res.status(400).json(r);
  startSession(res, r.account.id, r.account.name, r.key);
  res.setHeader("Set-Cookie", [res.getHeader("Set-Cookie"), setupCookie()].flat());
  // The recovery key is shown ONCE, here, and kept nowhere.
  res.json(strip(r));
});

app.post("/api/account/sign-in", async (req, res) => {
  const b = req.body || {};
  const r = await pyAccounts("sign-in", { account: b.account, password: b.password });
  if (!r.ok) return res.status(401).json(r);
  startSession(res, r.account.id, r.account.name, r.key);
  await cabinetReady();
  res.json(strip(r));
});

app.post("/api/account/recover", async (req, res) => {
  const b = req.body || {};
  const r = await pyAccounts("recover", { account: b.account, recovery_key: b.recovery_key,
                                         password: b.password });
  if (!r.ok) return res.status(401).json(r);
  startSession(res, r.account.id, r.account.name, r.key);
  await cabinetReady();
  res.json(strip(r));
});

app.post("/api/account/sign-out", async (req, res) => {
  if (!signedIn(req)) return res.json({ ok: true });     // only the person signed in can sign out
  const st = await pyAccounts("status", {});
  if (st.run_going) {
    return res.status(409).json({ ok: false,
      error: "Today's run is still going. Let it finish or press Stop, then sign out." });
  }
  endSession();
  res.json({ ok: true });
});

app.get("/api/account/me", async (_q, res) => {
  if (!SIGNED_IN) return res.json({ ok: true, signedIn: null });
  const [list, st] = await Promise.all([pyAccounts("list", {}), pyAccounts("status", {})]);
  // The websites come from the signed-in account itself, opened with its key.
  const own = (st.account || {}).id === SIGNED_IN.account;
  const mine = own ? (st.sites || []) : [];
  res.json({ ok: true, signedIn: { account: SIGNED_IN.account, name: SIGNED_IN.name },
             sites: mine.map((x) => ({ id: x.id, name: x.name, url: x.url })), open: own ? (st.site || {}).id || null : null,
             maxSites: list.max_sites,
             runGoing: !!st.run_going, legacyPresent: !!list.legacy_present,
             legacySite: list.legacy_site || {}, broughtIn: !!st.brought_in });
});

app.post("/api/account/password", async (req, res) => {
  if (!SIGNED_IN) return res.status(401).json({ ok: false, error: "Sign in first." });
  const b = req.body || {};
  if ((b.password || "") !== (b.confirm || "")) {
    return res.status(400).json({ ok: false, error: "The two passwords do not match." });
  }
  const r = await pyAccounts("change-password", { account: SIGNED_IN.account,
                                                  current: b.current, password: b.password });
  res.status(r.ok ? 200 : 400).json(r);
});

app.post("/api/account/add-site", async (req, res) => {
  if (!SIGNED_IN) return res.status(401).json({ ok: false, error: "Sign in first." });
  const b = req.body || {};
  const r = await pyAccounts("add-site", { account: SIGNED_IN.account, name: b.name, url: b.url });
  if (r.ok) await cabinetReady();
  res.status(r.ok ? 200 : 400).json(r);
});

app.post("/api/account/open-site", async (req, res) => {
  if (!SIGNED_IN) return res.status(401).json({ ok: false, error: "Sign in first." });
  const r = await pyAccounts("open-site", { account: SIGNED_IN.account, site: (req.body || {}).site });
  if (r.ok) await cabinetReady();
  res.status(r.ok ? 200 : (r.running ? 409 : 400)).json(r);
});

app.post("/api/account/bring-in", async (req, res) => {
  if (!SIGNED_IN) return res.status(401).json({ ok: false, error: "Sign in first." });
  const b = req.body || {};
  const r = await pyAccounts("bring-in", { account: SIGNED_IN.account, name: b.name, url: b.url });
  if (r.ok) await cabinetReady();
  res.status(r.ok ? 200 : 400).json(r);
});

app.post("/api/account/retire-old", async (req, res) => {
  if (!SIGNED_IN) return res.status(401).json({ ok: false, error: "Sign in first." });
  const r = await pyAccounts("retire-old", { account: SIGNED_IN.account,
                                            confirm: !!(req.body || {}).confirm });
  res.status(r.ok ? 200 : 400).json(r);
});
// The two rooms, one page: /mail opens on the mail, /posts on the written pages.
app.get("/mail", async (_q, r) => r.sendFile(path.join(__dirname, "ui", "review.html")));
app.get("/posts", async (_q, r) => r.sendFile(path.join(__dirname, "ui", "review.html")));

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
  "stuck", "release", "campaign-retry", "campaign-domain", "sent-csv",
  "test-gsc", "test-tomtom", "index-status", "index-check", "orders", "site-find",
  "account", "business", "engine", "connect", "check", "product", "update", "diagnostics",
  "support", "publisher", "quit",
];
const SERVER_STARTED = Math.floor(Date.now() / 1000);
// Which copy of the program this is, without saying where it lives: run.py
// computes the same from its own folder, so it can tell this office from an
// older copy in another folder, or from somebody else's program entirely.
const HOME_ID = crypto.createHash("sha256")
  .update(path.resolve(__dirname).replace(/\\/g, "/").toLowerCase()).digest("hex").slice(0, 16);
app.get("/api/ping", async (_q, res) => {
  res.json({ ok: true, api: API_CAPABILITIES, started: SERVER_STARTED,
             stale: codeIsNewer(), supervised: SUPERVISED, office: true, home: HOME_ID,
             version: productInfo().version || "" });
});

/* THE BRAND, AND NOTHING ELSE.
   The office header needs the agency's name and what the website is called.
   It used to get those from /api/settings, which also carries the ntfy topic,
   the SMTP user and the model routing — and /api/settings is behind the setup
   password now. A front-of-house screen should not be asking for the key
   cupboard to learn what the firm is called, so it asks for exactly this. */
app.get("/api/brand", async (_q, res) => {
  const p = productInfo();
  res.json({ ok: true, agencyName: (readEnv().AGENCY_NAME || "").trim(),
             website: await siteLabel(), product: p.name || "Glasswork", version: p.version || "" });
});

// STATE — the Control Room's snapshot. Reads the filing cabinet via Python
// (coordination/state.py), then merges in the .env-derived connections here.
app.get("/api/state", async (_q, res) => {
  const st = await pyState();
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
app.post("/api/chat", async (req, res) => {
  const b = req.body || {};
  const agent = String(b.agent || "").trim();
  const message = String(b.message || "").slice(0, 4000);
  if (!agent) return res.status(400).json({ ok: false, error: "Which agent?" });
  const py = resolvePython();
  if (!py) return res.json({ ok: false, error: NO_PY });
  const r = await spawnAsync(py.cmd, [...py.prefix, "-m", "coordination.agent_chat"], {
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
// ---- whose agency, which website (mirrors coordination/workspace.py) -------
// Until an account is open every path is the program's original one, so his
// agency carries on exactly as before. Once one is open, the person's settings
// and each website's settings, files and cabinet live under data/accounts/.
const DATA_ROOT = process.env.AGENCY_DATA || path.join(__dirname, "data");
// Settings that belong to the person, not to either website. Kept in step
// with ACCOUNT_KEYS in coordination/workspace.py by coordination/workspace_tests.
const ACCOUNT_KEYS = new Set([
  "AGENCY_NAME", "ADMIN_NAME", "ADMIN_PASSWORD_HASH",
  "NTFY_SERVER", "NTFY_TOPIC", "NTFY_TOKEN",
  "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
  "DAILY_SPEND_CAP_USD", "ZYTE_COST_PER_FETCH", "ZYTE_COST_PER_RENDER",
  "GOOGLE_PLACES_API_KEY", "TOMTOM_API_KEY", "GOOGLE_CSE_ID", "GOOGLE_CSE_KEY",
  "DATAFORSEO_LOGIN", "DATAFORSEO_PASSWORD",
  "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY",
  "PORT",
]);
const ACCOUNT_CONFIG = new Set(["providers.local.json", "claude_team.local.json", "engine.local.json",
  "report-brand.local.json", "support-reports.local.json"]);
const MACHINE_CONFIG = new Set(["claude_code.local.json"]);
const LEGACY_ENV = path.join(__dirname, ".env");

function activeSite() {
  try {
    const a = JSON.parse(fs.readFileSync(path.join(DATA_ROOT, "active.json"), "utf8"));
    if (!a || !a.account || !a.site) return null;
    // Only ever the signed-in person's own website: another account's, left
    // open from before, is not theirs to work in.
    if (SIGNED_IN && a.account !== SIGNED_IN.account) return null;
    const dir = path.join(DATA_ROOT, "accounts", a.account, "sites", a.site);
    return fs.existsSync(dir) ? { account: a.account, site: a.site } : null;
  } catch (_) { return null; }
}
function accountDir(a) { return path.join(DATA_ROOT, "accounts", a.account); }
function siteDir(a) { return path.join(accountDir(a), "sites", a.site); }
// [the person's settings file, the open website's] — or .env alone.
function envFiles() {
  const a = activeSite();
  return a ? [path.join(accountDir(a), "account.env"), path.join(siteDir(a), "site.env")]
           : [LEGACY_ENV];
}
function envFileFor(key) {
  const files = envFiles();
  return files.length === 1 ? files[0] : (ACCOUNT_KEYS.has(key) ? files[0] : files[1]);
}
function dbPath() {
  if (process.env.AGENCY_DB) return process.env.AGENCY_DB;
  const a = activeSite();
  return a ? path.join(siteDir(a), "agency.db") : path.join(__dirname, "coordination", "agency.db");
}
function configPath(name) {
  const a = activeSite();
  if (!a || MACHINE_CONFIG.has(name)) return path.join(__dirname, "config", name);
  return path.join(ACCOUNT_CONFIG.has(name) ? accountDir(a) : siteDir(a), "config", name);
}
const gscSaPath = () => configPath("gsc-service-account.local.json");

// Sealed settings and files: lib/sealed.js, the same format as coordination/vault.py.
const sealed = require("./lib/sealed");
function accountOfFile(file) {
  const root = path.resolve(DATA_ROOT, "accounts");
  const rel = path.relative(root, path.resolve(file));
  if (!rel || rel.startsWith("..") || path.isAbsolute(rel)) return null;
  const parts = rel.split(path.sep);
  return parts.length >= 2 ? { account: parts[0], where: parts.slice(1).join("/") } : null;
}
function accountEncrypted(id) {
  try {
    const a = JSON.parse(fs.readFileSync(path.join(DATA_ROOT, "accounts", id, "account.json"), "utf8"));
    return !!(a && a.keys);
  } catch (_) { return false; }
}
function masterFor(account) {
  if (!SIGNED_IN || SIGNED_IN.account !== account) {
    throw new Error("this account's settings are locked — sign in first");
  }
  return SIGNED_IN.key;
}
function readSecret(file) {
  const data = fs.readFileSync(file);
  if (!sealed.isSealed(data)) return data.toString("utf8");
  const owner = accountOfFile(file);
  if (!owner) throw new Error(path.basename(file) + " is sealed outside any account");
  return sealed.unseal(masterFor(owner.account), data, owner.where);
}
function writeSecret(file, text) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const owner = accountOfFile(file);
  const data = owner && accountEncrypted(owner.account)
    ? sealed.seal(masterFor(owner.account), text, owner.where)
    : Buffer.from(String(text), "utf8");
  const tmp = file + ".tmp";
  fs.writeFileSync(tmp, data, { mode: 0o600 });
  try { fs.chmodSync(tmp, 0o600); } catch (_) {}
  fs.renameSync(tmp, file);
}
// What Google said the last time it was actually asked (pipelines/seo_intel
// gsc_note). "Connected" used to mean only that two fields were filled in.
const gscStatusPath = () => configPath("gsc-status.local.json");
function gscStatus() {
  try { return JSON.parse(readSecret(gscStatusPath())); } catch (_) { return null; }
}
function gscLine(e) {
  const saved = !!(e.GSC_PROPERTY && String(e.GSC_PROPERTY).trim()) && fs.existsSync(gscSaPath());
  if (!saved) return { ok: false, text: "Not set — no owned-site search data" };
  const st = gscStatus();
  if (!st || (st.property && st.property !== String(e.GSC_PROPERTY).trim()))
    return { ok: true, text: `Saved (${e.GSC_PROPERTY}) — press Test on Setup to check Google accepts it` };
  if (st.ok) return { ok: true, text: `Working (${e.GSC_PROPERTY}) — Google answered` };
  return { ok: false, text: `Saved, but Google refused: ${String(st.why || "").slice(0, 220)}` };
}

// external service integrations stored in .env (secrets flagged for keyless re-save).
// WhatsApp used to sit here; it was dropped because enquiries come through the
// widget on the site and the owner answers them himself — no agent read it.
const INTEGRATIONS = {
  email: { fields: ["SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASS", "MAIL_FROM", "MAIL_FROM_NAME"], secret: ["SMTP_PASS"] },
  // Google Programmable Search: who ranks for a keyword, before a post is written.
  serp: { fields: ["GOOGLE_CSE_ID", "GOOGLE_CSE_KEY"], secret: ["GOOGLE_CSE_KEY"] },
  // TomTom: businesses on the map, the alternative to Google Places.
  maps: { fields: ["TOMTOM_API_KEY"], secret: ["TOMTOM_API_KEY"] },
};

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
  "GOOGLE_PLACES_API_KEY", "TOMTOM_API_KEY", "GSC_PROPERTY", "GOOGLE_CSE_ID", "GOOGLE_CSE_KEY",
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
  for (const file of envFiles()) {
    if (!fs.existsSync(file)) continue;
    let text = "";
    try { text = readSecret(file); } catch (_) { continue; }   // locked: reads as empty
    for (const line of text.split(/\r?\n/)) {
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
  // Once an account is open each setting goes to its own file: the person's
  // or the open website's (see envFileFor).
  for (const file of [...new Set(envFiles())]) {
    const mine = keys.filter((k) => envFileFor(k) === file);
    if (!mine.length && fs.existsSync(file)) continue;
    const body = [
      "# Written by the setup screen — secrets stored here only, never committed.",
      ...mine.map((k) => `${k}=${String(merged[k] == null ? "" : merged[k]).replace(/[\r\n]+/g, " ")}`),
      "",
    ].join("\n");
    writeSecret(file, body);     // sealed when the account is encrypted
  }
}

// Every table the app expects. A cabinet built by an OLDER version is missing the
// newer ones (that's what made the shelf board fail), so this list is what decides
// whether the schema needs applying again — not just "does the file exist".
const REQUIRED_TABLES = ["tasks", "decisions", "keywords", "shelf_queue", "borrows",
                         "targets", "daily_orders"];

function dbBuilt() {
  const DB_PATH = dbPath();
  if (!fs.existsSync(DB_PATH)) return false;
  try {
    const buf = fs.readFileSync(DB_PATH);
    // An encrypted cabinet has no readable table names; Python built it.
    if (!buf.subarray(0, 15).equals(Buffer.from("SQLite format 3"))) return buf.length > 0;
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

// ---- PYTHON WITHOUT FREEZING THE OFFICE ------------------------------------
// "clicked on the mails that was written it just keep loading ... and at
// sudden everything get stuck. no matter in which section i am doing anything
// the work in progress should not get stop"
//
// Every Python call here used to be spawnSync, which stops the whole server
// until Python finishes. One slow answer — "Check everything" calling each
// model, a Claude team test, mail going out, the clear-up, or any call waiting
// on the filing cabinet while a day run writes to it — and every other screen
// waited behind it: the office showed "Loading…", the Mail room never opened.
//
// spawnAsync has the same shape as spawnSync (same options, same result) and
// never blocks: the server keeps answering every other request while Python
// works. At most MAX_PY run at once, the rest queue, so a burst of polls
// cannot swamp the laptop; and identical read calls already in flight are
// shared (see pyModule) rather than started twice.
const MAX_PY = 8;
let PY_RUNNING = 0;
const PY_WAITING = [];
function _pySlot() {
  if (PY_RUNNING < MAX_PY) { PY_RUNNING++; return Promise.resolve(); }
  return new Promise((resolve) => PY_WAITING.push(resolve));
}
function _pyFree() {
  const next = PY_WAITING.shift();
  if (next) next(); else PY_RUNNING = Math.max(0, PY_RUNNING - 1);
}
async function spawnAsync(cmd, args, opts) {
  opts = opts || {};
  await _pySlot();
  return new Promise((resolve) => {
    let child, done = false, timer = null;
    const out = [], err = [];
    let size = 0;
    const cap = opts.maxBuffer || 8 * 1024 * 1024;
    const finish = (r) => {
      if (done) return;
      done = true;
      if (timer) clearTimeout(timer);
      _pyFree();
      resolve(r);
    };
    try {
      child = spawn(cmd, args, { cwd: opts.cwd, env: opts.env, windowsHide: true });
    } catch (e) {
      return finish({ error: e, status: null, stdout: "", stderr: "" });
    }
    child.on("error", (e) => finish({ error: e, status: null,
      stdout: Buffer.concat(out).toString(), stderr: Buffer.concat(err).toString() }));
    child.stdout.on("data", (d) => { size += d.length; if (size <= cap) out.push(d); });
    child.stderr.on("data", (d) => { if (err.length < 200) err.push(d); });
    child.on("close", (code) => finish({ error: null, status: code,
      stdout: Buffer.concat(out).toString(), stderr: Buffer.concat(err).toString() }));
    if (opts.timeout) {
      timer = setTimeout(() => {
        try { child.kill(); } catch (_) {}
        finish({ error: new Error(`took longer than ${Math.round(opts.timeout / 1000)}s and was stopped`),
                 status: null, stdout: Buffer.concat(out).toString(),
                 stderr: Buffer.concat(err).toString() });
      }, opts.timeout);
    }
    try {
      if (opts.input !== undefined) child.stdin.end(opts.input); else child.stdin.end();
    } catch (_) {}
  });
}

// Identical read calls already running are shared, not started again: the
// office and the Mail room poll, and a slow answer must not stack up copies.
const PY_INFLIGHT = new Map();
function shared(key, make) {
  if (PY_INFLIGHT.has(key)) return PY_INFLIGHT.get(key);
  const p = make().finally(() => PY_INFLIGHT.delete(key));
  PY_INFLIGHT.set(key, p);
  return p;
}

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
function ensureDbSync() {
  // Accounts exist (or must) and nobody is signed in: the cabinets are locked,
  // and a fresh copy makes none in the program's folder. Each one is built and
  // upgraded when its owner signs in (see signInDone).
  if (accountRequired() && !SIGNED_IN) return { built: true, ran: false, error: null };
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

// The same, without holding the server up — for use while it is serving.
async function ensureDb() {
  const py = resolvePython();
  if (!py) return { built: dbBuilt(), ran: false, error: NO_PY };
  try {
    const r = await spawnAsync(py.cmd, [...py.prefix, "coordination/init_db.py"],
                               { cwd: __dirname, timeout: 20000 });
    return { built: dbBuilt(), ran: true,
             error: r.status === 0 ? null : String(r.stderr || "") };
  } catch (e) {
    return { built: dbBuilt(), ran: true, error: e.message };
  }
}

// shell into the Python provider module (the single code path for provider ops)
async function pyProvider(command, payload, timeoutMs) {
  const py = resolvePython();
  if (!py) return { ok: false, error: NO_PY };
  const r = await spawnAsync(py.cmd, [...py.prefix, "-m", "providers.api", command], {
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
  return shared("m:" + mod + ":" + JSON.stringify(args || []),
                () => _pyModule(mod, args, timeoutMs));
}
async function _pyModule(mod, args, timeoutMs) {
  const py = resolvePython();
  if (!py) return { ok: false, error: NO_PY };
  const r = await spawnAsync(py.cmd, [...py.prefix, "-m", mod, ...(args || [])], {
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
  return shared("state", _pyState);
}
async function _pyState() {
  const py = resolvePython();
  if (!py) return { ok: false, error: NO_PY };
  const r = await spawnAsync(py.cmd, [...py.prefix, "-m", "coordination.state"], {
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
async function siteLabel() {
  const out = await pyModule("coordination.site", ["--json"], 15000);
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
    { ic: "◧", name: "Website", sub: e.SITE_URL || "not set", on: has("SITE_URL"),
      // Read-only or full access, and how — never the token or password.
      mode: String(e.SITE_MODE || "readonly").toLowerCase() === "full" ? "full" : "readonly",
      kind: String(e.SITE_CONNECT || "").toLowerCase() === "wordpress" ? "wordpress" : "php" },
    { ic: "🔔", name: "ntfy", sub: has("NTFY_TOPIC") ? "topic set" : "not set", on: has("NTFY_TOPIC") },
    ...(has("TELEGRAM_BOT_TOKEN")
        ? [{ ic: "✈", name: "Telegram (legacy)", sub: "still configured — ntfy is the route now", on: true }]
        : []),
    { ic: "🔎", name: "Algolia site search",
      sub: has("ALGOLIA_APP_ID") ? (e.ALGOLIA_INDEX || "glasswork_site") : "not set",
      on: has("ALGOLIA_APP_ID") && has("ALGOLIA_SEARCH_KEY") },
    { ic: "✉", name: "Email (SMTP)", sub: e.SMTP_HOST || "not set", on: has("SMTP_HOST") && has("SMTP_USER") && has("SMTP_PASS") },
    { ic: "⌗", name: "Search Console", sub: gscLine(e).text, on: gscLine(e).ok },
    { ic: "⌖", name: "Businesses on the map",
      sub: [has("GOOGLE_PLACES_API_KEY") ? "Google Places" : "", has("TOMTOM_API_KEY") ? "TomTom" : ""]
        .filter(Boolean).join(" + ") || "OpenStreetMap only (free) — add TomTom on Setup for more",
      on: has("GOOGLE_PLACES_API_KEY") || has("TOMTOM_API_KEY") },
  ];
}

// ---- website (its own Save button) -----------------------------------------
app.post("/api/website", async (req, res) => {
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
      writeSecret(configPath("site.json"),
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

    const db = await ensureDb();
    return res.json({ ok: true, mode, siteUrl, siteName,
                      database: db.built ? "ready" : "not built" });
  } catch (e) {
    return res.status(500).json({ ok: false, error: "Could not save: " + e.message });
  }
});

// ---- optional extras (its own Save button; blank fields are left untouched) --
app.post("/api/optional", async (req, res) => {
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
app.post("/api/integration", async (req, res) => {
  try {
    const b = req.body || {};
    const group = String(b.group || "");
    const vals = b.values || {};
    const one = (v) => String(v == null ? "" : v).replace(/[\r\n]+/g, " ").trim();

    if (group === "email" || group === "serp" || group === "maps") {
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
        writeSecret(gscSaPath(), sa);      // sealed when the account is encrypted
      }
      return res.json({ ok: true });
    }
    return res.status(400).json({ ok: false, error: "Unknown integration." });
  } catch (e) {
    return res.status(500).json({ ok: false, error: "Could not save: " + e.message });
  }
});

// Every order he gave and what became of it (coordination/orders_view).
// "there is no any room where i can see that the commands i gave how much work
//  get done what remains and why"
app.get("/api/orders", async (_q, res) =>
  res.json(await pyModule("coordination.orders_view", ["--json"], 30000)));

// Which of his pages Google has listed, and why not (pipelines/seo_index).
app.get("/api/index-status", async (_q, res) =>
  res.json(await pyModule("pipelines.seo_index", ["--status"], 30000)));
// Ask Google again now, about every page — one inspection and one page read each.
app.post("/api/index-check", async (_q, res) =>
  res.json(await pyModule("pipelines.seo_index", ["--now", "--json"], 300000)));

// ---- Search Console: ask Google now --------------------------------------
// "i had integrated the Google search console then why i see the same message
//  again and again" — Setup never asked Google; this does, the same way the
// keyword pass does, and says what Google answered.
app.post("/api/integrations/test-gsc", async (_q, res) =>
  res.json(await pyModule("pipelines.seo_intel", ["--check-gsc"], 120000)));

// ---- TomTom: one small real search -----------------------------------------
// "add tom tom as alternate" — the map the Leads collector uses when Google
// Places has no key or refuses it. Test asks TomTom for glass firms near Dubai.
app.post("/api/integrations/test-tomtom", async (_q, res) =>
  res.json(await pyModule("pipelines.leads_hunt", ["--test-tomtom"], 60000)));

// ---- the Claude team: which Claude model each sector thinks with ----------
// Every sector's brain is a Claude model (providers/claude_team.py). One row
// saves on its own; the test makes one tiny real call per model, so it gets
// the long timeout — Opus can take a while to say "OK" on a cold start.
app.get("/api/team", async (_q, res) => res.json(await pyProvider("team", {})));
app.post("/api/team/set", async (req, res) => res.json(await pyProvider("team_set", req.body)));
app.post("/api/team/test", async (req, res) => res.json(await pyProvider("team_test", req.body, 600000)));
app.post("/api/team/forget", async (req, res) => res.json(await pyProvider("team_forget", {})));
// The shelf's saved tools (keys masked) — the Setup page's shelf section reads it.
app.get("/api/providers", async (_q, res) => res.json(await pyProvider("view", {})));

// ---- the shelf (shared API cabinet) ----------------------------------------
// Tool APIs (SEO data, stock graphics, email, crawler…) are shelf items; the
// shelf board shows who holds each shelf, who's queued, and today's spend.
app.post("/api/providers/tool", async (req, res) => res.json(await pyProvider("save_tool", req.body)));
app.post("/api/providers/tool/delete", async (req, res) => res.json(await pyProvider("delete_tool", req.body)));
app.post("/api/providers/tool/test", async (req, res) => res.json(await pyProvider("test_tool", req.body)));
app.get("/api/shelves", async (_q, res) => res.json(await pyProvider("shelves", {})));
// How much of each third-party allowance is gone. Counts are facts; the money
// column is the owner's own rate from .env, never a quote from the provider.
app.get("/api/usage", async (_q, res) =>
  res.json(await pyModule("coordination.usage", ["--json"], 20000)));
// What would reach the owner's phone right now, and what would not. Reads only
// — the whole point is that he can see the list WITHOUT being sent it.
app.get("/api/notify/preview", async (_q, res) =>
  res.json(await pyModule("coordination.notify", ["--json"], 25000)));

// One sector in full: its score with the reasoning behind it, what it actually
// produced, its tasks, its runs, and the requests either way. This is what a
// room opens into when it is clicked — the owner asked to be able to read what
// a sector made, not just how many rows it made.
app.get("/api/sector/:key", async (req, res) => {
  const key = String(req.params.key || "").replace(/[^A-Za-z]/g, "").slice(0, 20);
  res.json(await pyModule("coordination.state", ["--sector", key], 25000));
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
app.get("/api/activity", async (req, res) => {
  const since = parseInt(req.query.since, 10);
  const args = ["--json"];
  if (Number.isFinite(since) && since > 0) args.push("--since", String(since));
  res.json(await pyModule("coordination.activity", args, 25000));
});

// ---- the leads behind the number ---------------------------------------------
// "Leads today: 0" with nothing behind it is a score, not a tool. This is who
// they are, where they came from, and what is holding each one up.
app.get("/api/leads", async (req, res) => {
  const scope = req.query.scope === "all" ? "all" : "today";
  res.json(await pyModule("coordination.leads_view", ["--json", "--scope", scope], 25000));
});

// ---- the owner's per-sector on/off switches ----------------------------------
// His agency, his call. A sector he switches off keeps its agents, its history
// and its score — it simply does no work: the MD cannot post to it, nothing on
// the board can be claimed by it, and its step in the day is skipped and says
// so. Enforced in coordination/board.py, not here, so a switch means the same
// thing whether it was flipped on this screen or from a terminal.
app.get("/api/sectors", async (_q, res) => {
  res.json(await pyModule("coordination.state", ["--switches"], 25000));
});

app.post("/api/sectors", async (req, res) => {
  const b = req.body || {};
  const sector = String(b.sector || "").replace(/[^A-Za-z]/g, "").slice(0, 20);
  if (!sector) return res.status(400).json({ ok: false, error: "Which sector?" });
  const on = b.enabled === true || b.enabled === "true" || b.enabled === 1;
  const args = ["--switch", `${sector}=${on ? "on" : "off"}`];
  if (typeof b.note === "string" && b.note.trim()) args.push(b.note.trim().slice(0, 300));
  res.json(await pyModule("coordination.state", args, 25000));
});

// ---- what the sectors have actually said to each other -----------------------
// The office floor used to animate eight hardcoded phrases at random, which
// looked like the agency working on a morning when nothing had run. Both the
// beams and the readable panel come from here now — the real request board.
app.get("/api/comms", async (req, res) => {
  const n = Math.max(1, Math.min(200, parseInt(req.query.limit, 10) || 40));
  res.json(await pyModule("coordination.comms", ["--json", "--limit", String(n)], 25000));
});

// ---- faults in the agency itself --------------------------------------------
// Not the website: the AGENTS. A stall, a crash, a claimed task nobody
// finished, a sector ignoring another sector. Read-only here — the scan that
// files and routes them runs as a step of the day, not off a page load.
app.get("/api/faults", async (req, res) => {
  const args = ["--json", "--list"];
  if (req.query.sector) args.push("--sector", String(req.query.sector).slice(0, 40));
  res.json(await pyModule("coordination.faults", args, 25000));
});

// Opening the panel IS reading it. The screen posts the timestamp of the newest
// line it actually showed him — not its own clock, which can be wrong — and the
// mark only ever moves forward, so a second tab holding an older read cannot
// resurrect a count he has just cleared.
app.post("/api/activity/seen", async (req, res) => {
  const b = req.body || {};
  const to = parseInt(b.seen_to, 10);
  // Three things the buttons on the panel can ask for. `clear` HIDES what has
  // been read — it never deletes, because every event is derived from a row the
  // work itself wrote, and `showAll` puts it straight back.
  const flag = b.showAll ? "--show-all" : b.clear ? "--clear" : "--mark-read";
  const args = ["--json", flag];
  if (flag !== "--show-all" && Number.isFinite(to) && to > 0) args.push(String(to));
  res.json(await pyModule("coordination.activity", args, 25000));
});
app.post("/api/shelves/resolve", async (req, res) => res.json(await pyProvider("resolve_shelf", req.body)));

// ---- channels + the whole-system sweep -------------------------------------
// Both prove credentials without sending anything. The one exception is
// notify:true, which posts to the owner's own ntfy topic and nowhere else.
app.post("/api/integrations/test", async (req, res) =>
  res.json(await pyProvider("test_integration", req.body)));
// A full sweep calls every model and every shelf API, so it gets its own,
// longer budget than an ordinary provider command.
app.post("/api/preflight", async (req, res) =>
  res.json(await pyProvider("preflight", req.body, 900000)));

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
app.post("/api/run/check", async (req, res) => {
  const quick = !!(req.body && req.body.quick);
  res.json(await pyModule("pipelines.day_run",
    ["--check", "--json", ...(quick ? ["--quick"] : [])], 900000));
});

app.get("/api/run/status", async (_q, res) =>
  res.json(await pyModule("pipelines.day_run", ["--status", "--json"], 60000)));

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
app.post("/api/run/stop", async (req, res) => {
  const why = String((req.body && req.body.why) || "").slice(0, 300);
  res.json(await pyModule("pipelines.day_run",
    ["--stop", "--json", ...(why ? ["--why", why] : [])], 120000));
});

app.post("/api/run/start", async (req, res) => {
  const py = resolvePython();
  if (!py) return res.json({ ok: false, error: NO_PY });
  const b = req.body || {};

  // Refuse a second press here as well as in Python. Python holds the real
  // guard (the day_run resource lock); this one exists so the owner gets a
  // plain answer instead of a process that starts, loses the lock race and
  // dies silently a second later.
  const st = await pyModule("pipelines.day_run", ["--status", "--json"], 60000);
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
  // This run only: which sectors work. A short list of plain names or nothing;
  // Python always adds the essential four and ignores anything it does not know.
  const only = Array.isArray(b.only)
    ? b.only.filter((x) => typeof x === "string").map((x) => x.slice(0, 20)).slice(0, 10)
    : [];

  // NO ORDERS, NO DAY. "with out any order the program will not start the
  // day" — nothing saved for today and nothing in this press means the check
  // runs instead: every sector and desk, and nothing starts. The same rule is
  // in day_run.start for the terminal; it is asked here as well because the
  // day runs detached, and an answer given there would never reach him.
  const gate = await pyStdin("pipelines.day_run", ["--gate"], { orders }, 300000);
  if (gate && gate.no_orders) {
    return res.json({ ok: false, started: false, no_orders: true, check: gate.check,
      why: gate.why || "No orders for today, so the day was not started." });
  }
  // The quick check (config and loading, no model calls) here too, so a
  // refusal reaches him instead of dying quietly in the background.
  const strict = b.force === true;
  const pre = await pyModule("pipelines.day_run", ["--check", "--quick", "--json"], 300000);
  if (pre && pre.ok && !pre.ready && !strict) {
    return res.json({ ok: false, refused: true, preflight: pre,
      blocking: pre.blocking || [],
      why: "Not started. " + (pre.blocking || []).slice(0, 3).join("; ")
         + ". Fix these, or press Run anyway." });
  }
  const payload = JSON.stringify({
    orders,
    only,
    note: String(b.note || "").slice(0, 4000),
    // Strictly true, not merely truthy. force overrides a check that said a
    // sector cannot run, so the string "no" must not be enough to trip it.
    force: strict,
    quick: true,
    // Checked a moment ago, just above — once is enough. A forced run checks
    // again so the run row records, in writing, that it was forced.
    skip_check: !!(pre && pre.ok && pre.ready),
  });

  let child;
  try {
    child = spawn(py.cmd, [...py.prefix, "-m", "pipelines.day_run", "--stdin", "--json"], {
      cwd: __dirname, detached: true, stdio: ["pipe", "ignore", "ignore"], windowsHide: true,
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

// ---- WHAT THE LAST SESSION LEFT, AND PICKING UP FROM IT ----------------------
// "add a button that should say resume or start from where you left ... it will
//  finish that unanswered and queued tasks first". coordination/leftovers.py
// reads and clears up; day_run.start(resume=True) runs only what is left.
app.get("/api/leftovers", async (_q, res) =>
  res.json(await pyModule("coordination.leftovers", ["--json"], 30000)));
app.post("/api/leftovers/tidy", async (_q, res) =>
  res.json(await pyModule("coordination.leftovers", ["--tidy", "--json"], 120000)));
app.post("/api/leftovers/put-away", async (_q, res) =>
  res.json(await pyModule("coordination.leftovers", ["--put-away", "--json"], 120000)));

app.post("/api/run/resume", async (_req, res) => {
  const py = resolvePython();
  if (!py) return res.json({ ok: false, error: NO_PY });
  const st = await pyModule("pipelines.day_run", ["--status", "--json"], 60000);
  if (st && st.run && st.run.running) {
    return res.json({ ok: false, busy: true,
      why: "A day is already running — wait for it to finish." });
  }
  // Nothing to pick up is answered here, with the clear-up done, rather than
  // by a background process whose "nothing to do" he would never see.
  const left = await pyModule("coordination.leftovers", ["--json"], 30000);
  if (left && left.ok && !left.resumable) {
    const t = await pyModule("coordination.leftovers", ["--tidy", "--json"], 120000);
    return res.json({ ok: true, started: false, nothing: true, tidied: t,
      why: "Nothing to pick up — the last run finished and no sector has work waiting. "
         + ((t && t.head) || "") });
  }
  // The quick check (config only, no model calls) here, so a refusal reaches
  // him; the run itself then skips it rather than checking twice.
  const pre = await pyModule("pipelines.day_run", ["--check", "--quick", "--json"], 300000);
  if (pre && pre.ok && !pre.ready) {
    return res.json({ ok: false, refused: true, preflight: pre,
      why: "Not resumed. " + (pre.blocking || []).slice(0, 3).join("; ")
         + ". Fix these first — the Run panel's check shows the detail." });
  }
  const payload = JSON.stringify({ resume: true, quick: true, skip_check: true,
                                   orders: {}, only: [] });
  try {
    const child = spawn(py.cmd, [...py.prefix, "-m", "pipelines.day_run", "--stdin", "--json"], {
      cwd: __dirname, detached: true, stdio: ["pipe", "ignore", "ignore"], windowsHide: true,
    });
    child.on("error", () => {});
    try { child.stdin.end(payload); } catch (_) {}
    child.unref();
  } catch (e) {
    return res.json({ ok: false, error: "Could not start it: " + e.message });
  }
  res.json({ ok: true, started: true,
    why: "Clearing up, then picking up where the last run left off." });
});

// ---- WHAT EACH SECTOR ACTUALLY PRODUCED -------------------------------------
// "there is no any section that shows the results of the sectors."
//
// The floor shows who is working, the run panel shows which steps ran, the
// control room lists the last forty rows of all time. None of the three answers
// "I pressed Run — what came out of it?". This does, and for a sector that
// produced nothing it says which of the six reasons it was.
app.get("/api/results", async (q, res) => {
  const args = ["--json"];
  const days = parseInt(q.query.days, 10);
  if (Number.isFinite(days) && days > 0) args.push("--days", String(Math.min(days, 365)));
  if (q.query.sector) args.push("--sector", String(q.query.sector).slice(0, 40));
  res.json(await pyModule("coordination.results", args, 60000));
});

// ---- THROWING AWAY WHAT IS OLDER THAN THE WINDOW ----------------------------
// "make a button to clear the communication older then 30 days... the data
//  deleted must not get stored in the Db or any other place it must be deleted
//  permanently"
//
// GET says what would go and changes nothing. POST does it, and 'permanently'
// is the hard half — see coordination/purge.py: a DELETE leaves the bytes in
// the file, so it ends by rewriting the database and emptying the write-ahead
// log. Given a longer budget because rewriting the file takes as long as it
// takes and being interrupted half way is the one thing it must not be.
app.get("/api/purge", async (q, res) => {
  const d = parseInt(q.query.days, 10);
  res.json(await pyModule("coordination.purge",
    ["--json", ...(Number.isFinite(d) && d > 0 ? ["--days", String(d)] : [])], 60000));
});

app.post("/api/purge", async (req, res) => {
  const d = parseInt((req.body || {}).days, 10);
  res.json(await pyModule("coordination.purge",
    ["--run", "--json", ...(Number.isFinite(d) && d > 0 ? ["--days", String(d)] : [])],
    300000));
});

// ---- WHAT CHANGED ON THE WEBSITE --------------------------------------------
// "there should be a section in program where i can see whats changed in
//  website... that's totally blind."
//
// Two endpoints because they cost very different amounts. Reading the log is a
// database read and the panel does it on open. Looking at the site is a GET per
// page, so it happens on the daily run and when he presses the button — never
// on a poll.
app.get("/api/site/changes", async (q, res) => {
  const args = ["--json"];
  if (q.query.all) args.push("--all");
  if (q.query.outside) args.push("--outside");
  if (q.query.page) args.push("--page", String(q.query.page).slice(0, 200));
  res.json(await pyModule("coordination.changelog", args, 30000));
});

// FIND ON HIS WEBSITE: every file holding this exact text, with the line. Reads
// only. "it cannot even check into the code" — this is the agency checking, in
// front of him.
app.get("/api/site/find", async (q, res) => {
  const text = String(q.query.text || "").slice(0, 300);
  if (text.trim().length < 6) return res.json({ ok: false, error: "give at least 6 characters to look for" });
  res.json(await pyModule("pipelines.site_api", ["--find", text, "--json"], 180000));
});

app.post("/api/site/look", async (_q, res) =>
  res.json(await pyModule("pipelines.site_watch", ["--json"], 180000)));

// ---- the owner's yes or no on one amber item --------------------------------
// The Control Room's Approve button used to change an opacity and say "sent to
// the agent". This is where it now actually goes.
app.post("/api/approve", async (req, res) => {
  const py = resolvePython();
  if (!py) return res.json({ ok: false, error: NO_PY });
  const b = req.body || {};
  const r = await spawnAsync(py.cmd, [...py.prefix, "-m", "coordination.approve"], {
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

// ---- THE REVIEW PAGE: written pages and drafted mail, read before they leave --
// "made a section where i can see if it write the post to publish and mail to
//  sent i will approve both of them". coordination/review.py does the reading
// and the deciding; these only carry it.
app.get("/api/review", async (_q, res) =>
  res.json(await pyModule("coordination.review", ["--json"], 30000)));

function campaignData(d) {
  // Short strings under plain names — whichever names review.py reads. Listing
  // them here meant a field added later was silently dropped by a server
  // started before it existed.
  const out = {};
  if (!d || typeof d !== "object") return out;
  for (const [k, v] of Object.entries(d).slice(0, 40)) {
    if (/^[a-z_]{1,40}$/.test(k) && typeof v === "string") out[k] = v.slice(0, 2000);
  }
  return out;
}
// How long each may take: building reads his website for photographs, and
// sending goes through his mail server one message at a time.
const REVIEW_TIMEOUT = { send: 300000, campaign_send: 300000, campaign_create: 180000,
                         campaign_test: 90000, campaign_retry: 300000 };
// "a button to delete the mails sent or to download them in CSV file" — every
// business ever mailed, as a spreadsheet. A byte-order mark first so Excel on
// Windows reads Arabic and accented names as they are.
app.get("/api/mail/sent.csv", async (_req, res) => {
  const j = await pyModule("coordination.review", ["--sent-csv"], 60000);
  if (!j || !j.ok) return res.status(500).type("text/plain").send((j && j.error) || "could not build the file");
  res.setHeader("Content-Type", "text/csv; charset=utf-8");
  res.setHeader("Content-Disposition", `attachment; filename="${String(j.name || "sent-mails.csv").replace(/[^\w.-]/g, "")}"`);
  res.send("\ufeff" + String(j.csv || ""));
});

app.post("/api/review/act", async (req, res) => {
  const py = resolvePython();
  if (!py) return res.json({ ok: false, error: NO_PY });
  const b = req.body || {};
  const act = String(b.do || "");
  // A name, not a list. The list lived here AND in coordination/review.py, and
  // a server started before an update did not know the new names — "unknown
  // action" on a button that worked. review.py refuses anything it does not
  // do, so the shape is all this needs to check.
  if (!/^[a-z_]{2,40}$/.test(act)) return res.json({ ok: false, error: "unknown action" });
  const ids = (Array.isArray(b.ids) ? b.ids : [])
    .map((x) => parseInt(x, 10)).filter(Number.isFinite).slice(0, 200);
  // Sending goes through his mail server one message at a time, so it gets
  // longer than a database write — but not unbounded.
  const r = await spawnAsync(py.cmd, [...py.prefix, "-m", "coordination.review", "--act"], {
    cwd: __dirname, input: JSON.stringify({ do: act, ids, data: campaignData(b.data) }),
    timeout: REVIEW_TIMEOUT[act] || (/send|retry|create/.test(act) ? 300000 : 60000),
    maxBuffer: 8 * 1024 * 1024,
  });
  if (r.error) return res.json({ ok: false, error: "Could not run Python: " + r.error.message });
  const out = String(r.stdout || "");
  if (!out) return res.json({ ok: false, error: "review error: " + String(r.stderr || "no output") });
  try { return res.json(JSON.parse(out)); }
  catch (_) { return res.json({ ok: false, error: "bad reply: " + out.slice(0, 300) }); }
});

// Writing a mail per lead, writing a page, and the Releaser's test run all take
// minutes, so they are started in the background the way the day run is, and
// the page watches the busy flags in /api/review.
const REVIEW_JOBS = { "draft-mails": "--draft-mails", "write-page": "--write-page",
                      "publish-now": "--publish-now", "campaign-rewrite": "--campaign-rewrite" };
app.post("/api/review/start/:job", async (req, res) => {
  const flag = REVIEW_JOBS[req.params.job];
  if (!flag) return res.json({ ok: false, error: "unknown job" });
  const py = resolvePython();
  if (!py) return res.json({ ok: false, error: NO_PY });
  // The Mail room's ticks: which leads to write for. Numbers only.
  const ids = (Array.isArray((req.body || {}).ids) ? req.body.ids : [])
    .map((x) => parseInt(x, 10)).filter(Number.isFinite).slice(0, 25);
  let extra = (req.params.job === "draft-mails" && ids.length) ? ["--ids", ids.join(",")] : [];
  // What he wants the campaign to lead with. One argument, never a shell.
  const focus = (req.body || {}).focus;
  if (req.params.job === "campaign-rewrite" && typeof focus === "string" && focus.trim())
    extra = ["--focus", focus.trim().slice(0, 200)];
  try {
    const child = spawn(py.cmd, [...py.prefix, "-m", "coordination.review", flag, ...extra], {
      cwd: __dirname, detached: true, stdio: "ignore", windowsHide: true,
    });
    child.on("error", () => {});
    child.unref();
  } catch (e) {
    return res.json({ ok: false, error: "Could not start it: " + e.message });
  }
  res.json({ ok: true, started: req.params.job });
});

// ---- put a stuck job back on the board --------------------------------------
// A claimed task belongs to one agent and nobody else may touch it. The cost of
// that rule: if the holder never comes back the job is held for ever, and the
// office draws a red desk the owner can do nothing about. The Overseer clears
// dead claims on its own pass; this is the manual door for when he is looking
// at the desk. board.release_claim does the judging — it refuses a claim that
// is merely slow unless he forces it, and records a forced one as forced.
app.get("/api/stuck", async (_q, res) =>
  res.json(await pyModule("coordination.release", ["--json"], 60000)));

app.post("/api/release", async (req, res) => {
  const py = resolvePython();
  if (!py) return res.json({ ok: false, error: NO_PY });
  const b = req.body || {};
  if (!b.id) return res.status(400).json({ ok: false, error: "Send { id }." });
  const r = await spawnAsync(py.cmd, [...py.prefix, "-m", "coordination.release", "--stdin"], {
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
// runs, so there is no second place for the two to disagree. (Reading them is
// GET /api/orders above, with what became of each.)
app.post("/api/orders", async (req, res) => {
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
    const r = await pyModule("coordination.proposals", ["--order", sector, text, "--json"], 60000);
    (r && r.ok !== false ? set : failed).push(sector);
  }
  res.json({ ok: failed.length === 0, set, failed,
    why: failed.length ? `No such sector: ${failed.join(", ")}.` : "Saved." });
});

// ---- what every sector and agent is doing right now -------------------------
// Both are also folded into /api/state so the office gets them in one poll;
// these exist for anything that wants just the one thing.
app.get("/api/readiness", async (_q, res) =>
  res.json(await pyModule("coordination.readiness", ["--json"], 60000)));
// (There was a second /api/activity handler here. Express answers with the
// first route that matches, so it had never run once — but it took no `since`
// and ignored the read mark, and the next person to reorder this file would
// have got a badge that never cleared and no idea why. Deleted, not moved.)

// ---- competitors -----------------------------------------------------------
// He asked where to add them and the honest answer was "a command line" — this
// module has always had the functions, nothing had ever put them in the office.
// Everything goes through pipelines.competitors so the normalising, the dedup
// on domain and the suggested/confirmed split live in one place.
app.get("/api/competitors", async (_q, res) =>
  res.json(await pyModule("pipelines.competitors", ["--json"], 60000)));

app.post("/api/competitors", async (req, res) => {
  const b = req.body || {};
  const act = String(b.action || "").trim();
  const args = ["--json"];
  if (act === "add") {
    const name = String(b.name || "").trim().slice(0, 120);
    const domain = String(b.domain || "").trim().slice(0, 200);
    if (!name || !domain)
      return res.status(400).json({ ok: false, error: "A rival needs a name and a website." });
    args.push("--add", name, domain);
  } else if (act === "remove") {
    const which = String(b.which || "").trim().slice(0, 120);
    if (!which) return res.status(400).json({ ok: false, error: "Which one?" });
    args.push("--remove", which);
  } else if (act === "confirm_all") {
    args.push("--confirm-all");
  } else if (act === "find") {
    args.push("--find");
  } else if (act === "reject") {
    const name = String(b.name || "").trim().slice(0, 120);
    if (!name) return res.status(400).json({ ok: false, error: "Which one?" });
    args.push("--not-a-rival", name);
  } else {
    return res.status(400).json({ ok: false, error: "Unknown action." });
  }
  // --find goes out to OpenStreetMap across the emirates, so it gets
  // longer than the rest. The others are cabinet writes and are instant.
  res.json(await pyModule("pipelines.competitors", args,
                    act === "find" ? 300000 : 60000));
});

// ---- the owner's daily targets ---------------------------------------------
// The numbers the agency works to. Read and written through coordination.targets
// so the ceiling checks and the measurement live in one place — the server does
// not know what a safe outreach volume is, and should not have an opinion.
app.get("/api/targets", async (_q, res) => res.json(await pyModule("coordination.targets", ["--json"])));

app.post("/api/targets", async (req, res) => {
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
  return res.json(await pyModule("coordination.targets", args));
});

// ---- what our own website says we sell --------------------------------------
// Reads SITE_URL and reconciles it against the service catalogue. Slow (it
// fetches several pages), so it is only ever run on demand from the button.
app.post("/api/site-services", async (_q, res) =>
  res.json(await pyModule("pipelines.site_services", ["--json"], 180000)));


// ---- current settings (to pre-fill the setup form) -------------------------
// Returns only non-secret values, plus a boolean for each secret so the form can
// show "saved — leave blank to keep" instead of an empty box.
app.get("/api/settings", async (_q, res) => {
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
      gsc: { property: v("GSC_PROPERTY"), serviceAccountSet: fs.existsSync(gscSaPath()) },
      serp: { id: v("GOOGLE_CSE_ID"), keySet: set("GOOGLE_CSE_KEY") },
      tomtom: { keySet: set("TOMTOM_API_KEY") },
    },
  });
});

// ---- status ----------------------------------------------------------------
app.get("/api/status", async (_q, res) => {
  const e = readEnv();
  const has = (k) => !!(e[k] && e[k].trim());
  // Whether the machine is locked, so the Setup gate can tell the truth rather
  // than warning about a password that is already set. Boolean only — the hash
  // never leaves here.
  const locked = has("ADMIN_PASSWORD_HASH");
  const adminName = (e.ADMIN_NAME || "").trim();
  const mode = String(e.SITE_MODE || "readonly").toLowerCase() === "full" ? "full" : "readonly";
  const fullReady = has("SITE_API_BASE") && has("SITE_API_TOKEN");

  // which Claude model each sector thinks with, and whether Claude is ready
  const team = await pyProvider("team", {});
  const signedIn = !!(team && team.ready && (team.accounts || []).some((a) => a.ok));
  const apiEngine = !!(team && team.engine && team.engine !== "claude_code");
  const brains = ((team && team.team) || []).map((t) => ({
    key: t.sector, label: t.label, ok: signedIn,
    detail: signedIn ? `${t.model_label} on ${apiEngine ? team.engine_name : "Claude"}`
      : ((team && team.why) || (apiEngine ? "The AI engine is not ready — open the Engine page"
        : "Claude is not signed in on this machine — run  claude  once and sign in")),
  }));
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
      hint: "The domain of the address your mail is sent from — the part after the @." },
    { label: "Spend & outreach caps", optional: true, ok: has("DAILY_SPEND_CAP_USD") && has("OUTREACH_DAILY_CAP"),
      detail: (has("DAILY_SPEND_CAP_USD") && has("OUTREACH_DAILY_CAP"))
        ? `$${e.DAILY_SPEND_CAP_USD}/day spend, ${e.OUTREACH_DAILY_CAP} messages/day` : "Using safe defaults (15/day, 25/day)",
      hint: "Safety limits the Overseer enforces." },
    { label: "Email sending", optional: true, ok: has("SMTP_HOST") && has("SMTP_USER") && has("SMTP_PASS"),
      detail: (has("SMTP_HOST") && has("SMTP_USER") && has("SMTP_PASS"))
        ? `SMTP ready (${e.SMTP_HOST})` : "Not set — outreach and graphical emails can't be sent yet",
      hint: "Your mail SMTP host, username and password, plus a from-address." },
    { label: "Google Search Console", optional: true, ok: gscLine(e).ok,
      detail: gscLine(e).text,
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
    // The old provider shelf is gone (every sector thinks with Claude), and the
    // variable this read went with it — so this route threw "ps is not
    // defined" and every screen asking for status got an error instead.
    providers: { credentials: 0, models: brainsTotal || 0 },
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

// THE PORT IS SOMEBODY ELSE'S.
//
// "git pull the program into folder and run the program and shows this error
//  {"error":"Page not found."}"
//
// That sentence is not this program's. Another program on his computer was
// answering on port 3000. Two ways that happens, and both ended with his
// browser talking to the other program while he was told the office was up:
//
//   1. The other program had 127.0.0.1:3000. This server could not listen,
//      crashed with a stack trace, and run.py — which had already opened the
//      browser — said nothing.
//   2. The other program had [::1]:3000, the IPv6 side. Windows let this
//      server take 127.0.0.1:3000 as well, but a browser asked for
//      "localhost" tries ::1 first — and reached the other program.
//
// So a port that is taken is said in words and exits with PORT_TAKEN, which
// run.py answers by moving to the next free port. And the office listens on
// ::1 too, so "localhost" reaches it whichever way the browser asks; if
// someone else already has ::1 at this port, that is PORT_TAKEN as well.
const PORT_TAKEN = 76;
function portTaken(where) {
  console.error(`\n!! Port ${PORT} is already in use on this computer (${where}) by another ` +
    "program, so the office cannot start there.\n   run.py moves to a free port by itself. " +
    `Started by hand? Set another port first, e.g.  set PORT=${Number(PORT) + 1}  then start again.\n`);
  process.exit(PORT_TAKEN);
}
const mainServer = http.createServer(app);
mainServer.on("error", (e) => {
  if (e && e.code === "EADDRINUSE") return portTaken(HOST);
  throw e;
});
if (!process.env.BIND_HOST) {
  const v6 = http.createServer(app);
  v6.on("error", (e) => {
    if (e && e.code === "EADDRINUSE") return portTaken("::1, which is what \"localhost\" often means");
    // No IPv6 on this computer: nothing to do, 127.0.0.1 is the only way in.
  });
  v6.listen(PORT, "::1");
}
mainServer.listen(PORT, HOST, () => {
  // Upgrade the cabinet on boot. schema.sql is CREATE TABLE IF NOT EXISTS
  // throughout, so this is safe to run every time and adds any table a newer
  // version introduced — an existing database is never left half-upgraded.
  const db = ensureDbSync();
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
