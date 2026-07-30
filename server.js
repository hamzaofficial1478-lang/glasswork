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

// AI key slots — one brain per slot. Each is provider / base URL / model / key
// in .env. MD runs the Managing Director + Critic; FALLBACK backs up everyone;
// the rest are one per sector. (Kept in sync with coordination/ai_keys.py.)
const AI_SLOTS = [
  { key: "MD", label: "Managing Director & Critic" },
  { key: "FALLBACK", label: "Fallback (backup for all agents)" },
  { key: "IT", label: "IT sector" },
  { key: "CONTENT", label: "Content sector" },
  { key: "SEO", label: "SEO sector" },
  { key: "LEADS", label: "Leads sector" },
  { key: "REPORT", label: "Report sector" },
  { key: "SECURITY", label: "Security sector" },
];
const SECTOR_SLOTS = ["IT", "CONTENT", "SEO", "LEADS", "REPORT", "SECURITY"];

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

    // AI key slots (provider / base URL / model / key per brain)
    const ai = (b.ai && typeof b.ai === "object") ? b.ai : {};
    let aiKeysSet = 0;
    const aiLines = [];
    for (const slot of AI_SLOTS) {
      const s = ai[slot.key] || {};
      const key = one(s.key);
      if (key) aiKeysSet++;
      aiLines.push(
        `${slot.key}_PROVIDER=${one(s.provider)}`,
        `${slot.key}_BASE_URL=${one(s.baseUrl)}`,
        `${slot.key}_MODEL=${one(s.model)}`,
        `${slot.key}_API_KEY=${key}`,
      );
    }

    const envText = [
      "# Written by the setup screen (/setup). Secrets are stored here only — keep it out of git.",
      `SITE_MODE=${mode}`,
      `SITE_URL=${one(siteUrl)}`,
      "",
      "# AI providers — one slot per brain (provider / base URL / model / key)",
      ...aiLines,
      "",
      "# Optional extras (not required to start)",
      `DATAFORSEO_LOGIN=${one(b.dfsLogin)}`,
      `DATAFORSEO_PASSWORD=${one(b.dfsPassword)}`,
      `TELEGRAM_BOT_TOKEN=${one(b.telegramToken)}`,
      `TELEGRAM_CHAT_ID=${one(b.telegramChatId)}`,
      `OUTREACH_DOMAIN=${one(b.outreachDomain)}`,
      `DAILY_SPEND_CAP_USD=${one(b.spendCap) || "15"}`,
      `OUTREACH_DAILY_CAP=${one(b.outreachCap) || "25"}`,
      "",
      "# Website full-access (only used in full mode)",
      `SITE_API_BASE=${one(mode === "full" ? apiBase : "")}`,
      `SITE_API_TOKEN=${one(mode === "full" ? apiToken : "")}`,
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
      aiKeysSet, aiKeysTotal: AI_SLOTS.length,
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

  // which brain (provider + model) each sector and the MD is using
  const fallbackKeyed = has("FALLBACK_API_KEY");
  const fallbackPM = [e.FALLBACK_PROVIDER, e.FALLBACK_MODEL].filter(Boolean).join(" · ");
  const brains = AI_SLOTS.map((slot) => {
    const keyed = has(slot.key + "_API_KEY");
    const provider = e[slot.key + "_PROVIDER"] || "";
    const model = e[slot.key + "_MODEL"] || "";
    const pm = [provider, model].filter(Boolean).join(" · ");
    let ok, detail;
    if (keyed) {
      ok = true;
      detail = pm || "key set (add the provider + model for a clearer view)";
    } else if (slot.key === "FALLBACK") {
      ok = false;
      detail = "No fallback key — if a sector key fails, that agent stops";
    } else if (fallbackKeyed) {
      ok = false; // still a red warning per sector, but note the safety net
      detail = "No key set — will fall back to the Fallback key" + (fallbackPM ? " (" + fallbackPM + ")" : "");
    } else {
      ok = false;
      detail = "No key set — this agent can't run yet";
    }
    return { key: slot.key, label: slot.label, provider, model, ok, detail };
  });
  const mdKeyed = has("MD_API_KEY");
  const sectorsKeyed = SECTOR_SLOTS.filter((k) => has(k + "_API_KEY")).length;
  const brainsReady = (mdKeyed ? 1 : 0) + sectorsKeyed;   // MD + 6 sectors
  const brainsTotal = 1 + SECTOR_SLOTS.length;

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
      detail: dbBuilt() ? "Built and ready" : "Not built yet — it builds itself when you save the setup form",
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

  res.json({ ok: true, mode, brains, brainsReady, brainsTotal, checks });
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`Agency running on :${PORT}`));
