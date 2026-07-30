/*
 * server.js — the one Node app you host. Serves both UIs and the shared state.
 *   /          -> the Virtual Office (front of house)
 *   /control   -> the Control Room dashboard (back office)
 *   /api/state -> the live state both UIs read (wire this to the filing cabinet)
 *   /api/chat  -> talk to an agent (wire to Claude / the Agent SDK)
 *
 * Run on your Node box under pm2:  pm2 start server.js --name agency
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
  // e.g. read tasks, leads, site_issues, decisions from SQLite and shape them here
  res.json({ ok: true, note: "wire me to the filing cabinet", ts: Date.now() });
});

// CHAT — the office sends {agent, message}; forward to Claude / the Agent SDK
app.post("/api/chat", async (req, res) => {
  const { agent, message } = req.body || {};
  // TODO: route to the matching agent via the board; return its reply
  res.json({ agent, reply: `(${agent}) received: ${message}` });
});

// ---- helpers ---------------------------------------------------------------
const ENV_PATH = path.join(__dirname, ".env");
const DB_PATH = path.join(__dirname, "coordination", "agency.db");

// read .env into a { KEY: value } map. Values never leave the server as-is;
// the status endpoint only reports whether each key is filled.
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

// the cabinet counts as "built" if the file exists and carries the core tables
function dbBuilt() {
  if (!fs.existsSync(DB_PATH)) return false;
  try {
    const buf = fs.readFileSync(DB_PATH);
    return buf.includes("tasks") && buf.includes("decisions") && buf.includes("keywords");
  } catch (_) { return false; }
}

// build the cabinet from schema.sql if it isn't built yet (runs init_db.py)
function ensureDb() {
  if (dbBuilt()) return { built: true, ran: false };
  try {
    const r = spawnSync("python3", ["coordination/init_db.py"], { cwd: __dirname, timeout: 20000 });
    return { built: dbBuilt(), ran: true, error: r.status === 0 ? null : String(r.stderr || "") };
  } catch (e) {
    return { built: dbBuilt(), ran: true, error: e.message };
  }
}

// SETUP — the /setup screen posts the config here; we write .env and the
// connector's SITE line, then build the cabinet, so the owner edits nothing.
app.post("/api/setup", (req, res) => {
  try {
    const b = req.body || {};
    const mode = String(b.mode || "readonly").toLowerCase() === "full" ? "full" : "readonly";
    const siteUrl = String(b.siteUrl || "").trim();
    if (!siteUrl)
      return res.status(400).json({ ok: false, error: "Enter your website URL." });

    // JSON.stringify yields a safely-escaped double-quoted string that is also a
    // valid Python string literal — so odd characters in a token can't break the file.
    const py = (v) => JSON.stringify(String(v == null ? "" : v));
    let siteLine, apiBase = "", apiToken = "";
    if (mode === "full") {
      apiBase = String(b.apiBase || "").trim();
      apiToken = String(b.apiToken || "");
      if (!apiBase || !apiToken)
        return res.status(400).json({ ok: false, error: "Full access needs your site's admin API URL and token." });
      siteLine = `SITE = CustomSite(${py(apiBase)}, ${py(apiToken)})`;
    } else {
      siteLine = `SITE = ReadOnlySite(${py(siteUrl)})`;
    }

    // one clean line per value; strip newlines so nobody can inject extra keys.
    const one = (v) => String(v == null ? "" : v).replace(/[\r\n]+/g, " ").trim();
    const envText = [
      "# Written by the setup screen (/setup). Secrets are stored here only — keep it out of git.",
      `SITE_MODE=${mode}`,
      `SITE_URL=${one(siteUrl)}`,
      `ANTHROPIC_API_KEY=${one(b.anthropicKey)}`,
      `DATAFORSEO_LOGIN=${one(b.dfsLogin)}`,
      `DATAFORSEO_PASSWORD=${one(b.dfsPassword)}`,
      `TELEGRAM_BOT_TOKEN=${one(b.telegramToken)}`,
      `TELEGRAM_CHAT_ID=${one(b.telegramChatId)}`,
      `SITE_API_BASE=${one(mode === "full" ? apiBase : "")}`,
      `SITE_API_TOKEN=${one(mode === "full" ? apiToken : "")}`,
      `OUTREACH_DOMAIN=${one(b.outreachDomain)}`,
      `DAILY_SPEND_CAP_USD=${one(b.spendCap) || "15"}`,
      `OUTREACH_DAILY_CAP=${one(b.outreachCap) || "25"}`,
      `PORT=${process.env.PORT || "3000"}`,
      "",
    ].join("\n");
    fs.writeFileSync(ENV_PATH, envText, { mode: 0o600 });
    try { fs.chmodSync(ENV_PATH, 0o600); } catch (_) {}

    // Rewrite the single active SITE = line in the connector (never the # examples).
    // connector.py is git-ignored and generated from connector.example.py.
    const dir = path.join(__dirname, "mcp", "site_connector");
    const connPath = path.join(dir, "connector.py");
    const examplePath = path.join(dir, "connector.example.py");
    let conn = fs.existsSync(connPath) ? fs.readFileSync(connPath, "utf8")
             : fs.existsSync(examplePath) ? fs.readFileSync(examplePath, "utf8")
             : null;
    if (conn === null)
      return res.status(500).json({ ok: false, error: "Connector template not found." });
    conn = /^SITE\s*=.*$/m.test(conn)
      ? conn.replace(/^SITE\s*=.*$/m, siteLine)
      : conn.replace(/\s*$/, "\n") + siteLine + "\n";
    fs.writeFileSync(connPath, conn);

    // build the filing cabinet if it isn't already
    const db = ensureDb();

    // never echo secrets back to the screen
    return res.json({
      ok: true, mode, siteUrl,
      wrote: [".env", "mcp/site_connector/connector.py"],
      database: db.built ? "ready" : "not built",
    });
  } catch (e) {
    return res.status(500).json({ ok: false, error: "Could not save: " + e.message });
  }
});

// STATUS — plain-language read of what's connected and what's still missing.
// Reports only whether each field is filled; it never returns a secret value.
app.get("/api/status", (_q, res) => {
  const e = readEnv();
  const has = (k) => !!(e[k] && e[k].trim());
  const mode = String(e.SITE_MODE || "readonly").toLowerCase() === "full" ? "full" : "readonly";
  const fullReady = has("SITE_API_BASE") && has("SITE_API_TOKEN");
  const checks = [
    { label: "Website", ok: has("SITE_URL"),
      detail: has("SITE_URL")
        ? (mode === "full"
            ? "Full access — agents can read and update your site"
            : "Read-only — agents plan and track; any change is queued for your approval")
        : "No website address saved yet",
      hint: "Set this on the setup screen under Website." },
    { label: "Site access (only for full mode)", ok: mode !== "full" ? true : fullReady,
      detail: mode !== "full"
        ? "Not needed — you're in read-only mode"
        : (fullReady ? "Admin API address and token saved" : "Full access is on, but the admin API address/token is missing"),
      hint: "Only needed if you switch Website mode to Full access." },
    { label: "Anthropic API key", ok: has("ANTHROPIC_API_KEY"),
      detail: has("ANTHROPIC_API_KEY") ? "Saved" : "Missing — the agents can't think without this",
      hint: "console.anthropic.com → API Keys." },
    { label: "DataForSEO (rank tracking)", ok: has("DATAFORSEO_LOGIN") && has("DATAFORSEO_PASSWORD"),
      detail: (has("DATAFORSEO_LOGIN") && has("DATAFORSEO_PASSWORD")) ? "Login and password saved" : "Missing — rankings won't be tracked",
      hint: "Login + API password from your DataForSEO dashboard." },
    { label: "Telegram (your approvals)", ok: has("TELEGRAM_BOT_TOKEN") && has("TELEGRAM_CHAT_ID"),
      detail: (has("TELEGRAM_BOT_TOKEN") && has("TELEGRAM_CHAT_ID")) ? "Bot token and chat ID saved" : "Missing — the agency can't reach you to approve things",
      hint: "Bot token from @BotFather, chat ID from @userinfobot." },
    { label: "Sending domain", ok: has("OUTREACH_DOMAIN"),
      detail: has("OUTREACH_DOMAIN") ? e.OUTREACH_DOMAIN : "Not set — outreach stays off until this is set",
      hint: "A subdomain you send cold email from." },
    { label: "Spend & outreach caps", ok: has("DAILY_SPEND_CAP_USD") && has("OUTREACH_DAILY_CAP"),
      detail: (has("DAILY_SPEND_CAP_USD") && has("OUTREACH_DAILY_CAP"))
        ? `$${e.DAILY_SPEND_CAP_USD}/day spend, ${e.OUTREACH_DAILY_CAP} messages/day`
        : "Using safe defaults",
      hint: "Safety limits the Overseer enforces." },
    { label: "Filing cabinet (database)", ok: dbBuilt(),
      detail: dbBuilt() ? "Built and ready" : "Not built yet — it builds itself when you save the setup form",
      hint: "No action needed — created for you on first save." },
  ];
  const done = checks.filter((c) => c.ok).length;
  res.json({ ok: true, mode, done, total: checks.length, checks });
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`Agency running on :${PORT}`));
