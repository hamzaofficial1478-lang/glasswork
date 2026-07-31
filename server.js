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

// STATE — right now returns a snapshot; swap the body to read coordination/agency.db
app.get("/api/state", (_q, res) => {
  res.json({ ok: true, note: "wire me to the filing cabinet", ts: Date.now() });
});

// CHAT — the office sends {agent, message}; forward to Claude / the Agent SDK
app.post("/api/chat", async (req, res) => {
  const { agent, message } = req.body || {};
  res.json({ agent, reply: `(${agent}) received: ${message}` });
});

// ---- helpers ---------------------------------------------------------------
const ENV_PATH = path.join(__dirname, ".env");
const DB_PATH = path.join(__dirname, "coordination", "agency.db");

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

function dbBuilt() {
  if (!fs.existsSync(DB_PATH)) return false;
  try {
    const buf = fs.readFileSync(DB_PATH);
    return buf.includes("tasks") && buf.includes("decisions") && buf.includes("keywords");
  } catch (_) { return false; }
}

function ensureDb() {
  if (dbBuilt()) return { built: true, ran: false };
  try {
    const r = spawnSync("python3", ["coordination/init_db.py"], { cwd: __dirname, timeout: 20000 });
    return { built: dbBuilt(), ran: true, error: r.status === 0 ? null : String(r.stderr || "") };
  } catch (e) {
    return { built: dbBuilt(), ran: true, error: e.message };
  }
}

// shell into the Python provider module (the single code path for provider ops)
function pyProvider(command, payload) {
  const r = spawnSync("python3", ["-m", "providers.api", command], {
    cwd: __dirname, input: JSON.stringify(payload || {}),
    timeout: 120000, maxBuffer: 8 * 1024 * 1024,
  });
  const out = String(r.stdout || "");
  if (!out) return { ok: false, error: "provider module error: " + String(r.stderr || r.error || "no output") };
  try { return JSON.parse(out); }
  catch (_) { return { ok: false, error: "bad provider output: " + out.slice(0, 300) }; }
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

// ---- AI providers (each credential saves independently) --------------------
app.get("/api/providers", (_q, res) => res.json(pyProvider("view", {})));
app.post("/api/providers/credential", (req, res) => res.json(pyProvider("save_credential", req.body)));
app.post("/api/providers/credential/delete", (req, res) => res.json(pyProvider("delete_credential", req.body)));
app.post("/api/providers/model", (req, res) => res.json(pyProvider("save_model", req.body)));
app.post("/api/providers/model/delete", (req, res) => res.json(pyProvider("delete_model", req.body)));
app.post("/api/providers/fetch-models", (req, res) => res.json(pyProvider("fetch_models", req.body)));
app.post("/api/providers/test", (req, res) => res.json(pyProvider("test_model", req.body)));

// ---- per-task routing ------------------------------------------------------
app.get("/api/routing", (_q, res) => res.json(pyProvider("get_routing", {})));
app.post("/api/routing", (req, res) => res.json(pyProvider("save_route", req.body)));
app.get("/api/tasks", (_q, res) => res.json({ ok: true, tasks: AGENCY_TASKS }));

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
  ];

  res.json({
    ok: true, mode,
    providers: { credentials: (ps && ps.credentials) || 0, models: (ps && ps.models) || 0 },
    brains, brainsReady, brainsTotal, checks,
  });
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`Agency running on :${PORT}`));
