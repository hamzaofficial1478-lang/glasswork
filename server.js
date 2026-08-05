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
const { spawnSync } = require("child_process");
const app = express();
app.use(express.json());
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

// CHAT — the office sends {agent, message}; forward to Claude / the Agent SDK
app.post("/api/chat", async (req, res) => {
  const { agent, message } = req.body || {};
  res.json({ agent, reply: `(${agent}) received: ${message}` });
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
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "OUTREACH_DOMAIN",
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
    { ic: "✈", name: "Telegram bridge", sub: has("TELEGRAM_CHAT_ID") ? "connected" : "not connected", on: has("TELEGRAM_BOT_TOKEN") && has("TELEGRAM_CHAT_ID") },
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
    set("TELEGRAM_BOT_TOKEN", b.telegramToken);
    set("TELEGRAM_CHAT_ID", b.telegramChatId);
    set("OUTREACH_DOMAIN", b.outreachDomain);
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
app.post("/api/shelves/resolve", (req, res) => res.json(pyProvider("resolve_shelf", req.body)));

// ---- channels + the whole-system sweep -------------------------------------
// Both prove credentials without sending anything. The one exception is
// notify:true on Telegram, which posts to the owner's own chat and nowhere else.
app.post("/api/integrations/test", (req, res) =>
  res.json(pyProvider("test_integration", req.body)));
// A full sweep calls every model and every shelf API, so it gets its own,
// longer budget than an ordinary provider command.
app.post("/api/preflight", (req, res) =>
  res.json(pyProvider("preflight", req.body, 900000)));

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
      telegramTokenSet: set("TELEGRAM_BOT_TOKEN"), telegramChatId: v("TELEGRAM_CHAT_ID"),
      outreachDomain: v("OUTREACH_DOMAIN"),
      spendCap: v("DAILY_SPEND_CAP_USD"), outreachCap: v("OUTREACH_DAILY_CAP"),
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
    { label: "Telegram (your approvals)", optional: true, ok: has("TELEGRAM_BOT_TOKEN") && has("TELEGRAM_CHAT_ID"),
      detail: (has("TELEGRAM_BOT_TOKEN") && has("TELEGRAM_CHAT_ID")) ? "Bot token and chat ID saved" : "Not set — you won't get Telegram approvals yet",
      hint: "Bot token from @BotFather, chat ID from @userinfobot." },
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
app.listen(PORT, () => {
  // Upgrade the cabinet on boot. schema.sql is CREATE TABLE IF NOT EXISTS
  // throughout, so this is safe to run every time and adds any table a newer
  // version introduced — an existing database is never left half-upgraded.
  const db = ensureDb();
  if (db.built) console.log("filing cabinet ready");
  else console.log("filing cabinet NOT ready:", db.error || "unknown");
  console.log(`Agency running on :${PORT}`);
});
