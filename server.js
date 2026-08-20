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
   then it is the only thing between a passer-by and his API keys. Set it with
   `python backend.py --password`.
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
<title>Glassworks Agency</title>
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
  <h1>Glassworks Agency</h1>
  <p>This machine's copy is locked. Enter the admin password you set with
     <code>python backend.py --password</code>.</p>
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

app.post("/api/logout", (req, res) => {
  SESSIONS.delete(cookieToken(req));
  res.json({ ok: true });
});

// Past the gate, and only past it.
app.use(express.static(path.join(__dirname, "ui")));

app.get("/", (_q, r) => r.sendFile(path.join(__dirname, "ui", "office.html")));
app.get("/control", (_q, r) => r.sendFile(path.join(__dirname, "ui", "agency.html")));
app.get("/setup", (_q, r) => r.sendFile(path.join(__dirname, "ui", "setup.html")));
app.get("/status", (_q, r) => r.sendFile(path.join(__dirname, "ui", "status.html")));

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
function readEnv() {
  const map = {};
  if (fs.existsSync(ENV_PATH)) {
    for (const line of fs.readFileSync(ENV_PATH, "utf8").split(/\r?\n/)) {
      const m = line.match(/^([A-Z0-9_]+)=(.*)$/);
      if (m) map[m[1]] = m[2];
    }
  }
  return map;
}

// merge keys into .env WITHOUT disturbing the rest — this is why saving one
// section never wipes another (the bug the owner hit with the old single button).
function updateEnv(partial) {
  const merged = { ...readEnv(), ...partial };
  if (!merged.PORT) merged.PORT = process.env.PORT || "3000";
  const order = [
    "SITE_MODE", "SITE_URL", "DATAFORSEO_LOGIN", "DATAFORSEO_PASSWORD",
    "NTFY_SERVER", "NTFY_TOPIC", "NTFY_TOKEN",
    "ALGOLIA_APP_ID", "ALGOLIA_SEARCH_KEY", "ALGOLIA_WRITE_KEY", "ALGOLIA_INDEX",
    // Telegram keys are kept in the file if they're already there — an owner
    // mid-switch shouldn't lose his fallback — but nothing writes them now.
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "OUTREACH_DOMAIN",
    "ZYTE_COST_PER_FETCH", "ZYTE_COST_PER_RENDER", "CRAWLER_ON_OWN_SITE",
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

function ensureDb() {
  if (dbBuilt()) return { built: true, ran: false };
  const py = resolvePython();
  if (!py) return { built: false, ran: false, error: NO_PY };
  try {
    const r = spawnSync(py.cmd, [...py.prefix, "coordination/init_db.py"], { cwd: __dirname, timeout: 20000 });
    return { built: dbBuilt(), ran: true, error: r.status === 0 ? null : String(r.stderr || "") };
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

// the outside-world connections, from .env — what's wired and what isn't
function readConnections() {
  const e = readEnv();
  const has = (k) => !!(e[k] && e[k].trim());
  return [
    { ic: "◧", name: "Website", sub: e.SITE_URL || "not set", on: has("SITE_URL") },
    { ic: "⌕", name: "DataForSEO", sub: "rank tracking", on: has("DATAFORSEO_LOGIN") && has("DATAFORSEO_PASSWORD") },
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
    updateEnv({
      SITE_MODE: mode, SITE_URL: siteUrl,
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
        JSON.stringify({ url: siteUrl, mode }, null, 2) + "\n");
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
    return res.json({ ok: true, mode, siteUrl, database: db.built ? "ready" : "not built" });
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
    set("DATAFORSEO_LOGIN", b.dfsLogin);
    set("DATAFORSEO_PASSWORD", b.dfsPassword);
    // ntfy is the owner's route. Telegram keys are still READ if they exist in
    // .env, so an older setup keeps working, but they are no longer offered.
    set("NTFY_TOPIC", b.ntfyTopic);
    set("NTFY_SERVER", b.ntfyServer);
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
app.get("/api/activity", (req, res) => {
  const n = Math.max(1, Math.min(300, parseInt(req.query.limit, 10) || 60));
  res.json(pyModule("coordination.activity", ["--json", "--limit", String(n)], 60000));
});

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
      apiBase: v("SITE_API_BASE"),
      apiTokenSet: set("SITE_API_TOKEN"),
    },
    optional: {
      dfsLogin: v("DATAFORSEO_LOGIN"), dfsPasswordSet: set("DATAFORSEO_PASSWORD"),
      ntfyTopic: v("NTFY_TOPIC"), ntfyServer: v("NTFY_SERVER"),
      algoliaAppId: v("ALGOLIA_APP_ID"), algoliaSearchKey: v("ALGOLIA_SEARCH_KEY"),
      // the write key is reported as set / not set, never echoed back
      algoliaWriteKeySet: set("ALGOLIA_WRITE_KEY"), algoliaIndex: v("ALGOLIA_INDEX"),
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
    { label: "DataForSEO (rank tracking)", optional: true, ok: has("DATAFORSEO_LOGIN") && has("DATAFORSEO_PASSWORD"),
      detail: (has("DATAFORSEO_LOGIN") && has("DATAFORSEO_PASSWORD")) ? "Login and password saved" : "Not set — rankings aren't tracked",
      hint: "Login + API password from your DataForSEO dashboard." },
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
    ok: true, mode,
    providers: { credentials: (ps && ps.credentials) || 0, models: (ps && ps.models) || 0 },
    brains, brainsReady, brainsTotal, checks,
  });
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
      "Set an admin password (python backend.py --password) or put it behind a proxy.");
  }
  if (adminHash()) console.log("Admin password is ON.");
});
