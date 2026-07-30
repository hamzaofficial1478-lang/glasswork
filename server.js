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
const app = express();
app.use(express.json());
app.use(express.static(path.join(__dirname, "ui")));

app.get("/", (_q, r) => r.sendFile(path.join(__dirname, "ui", "office.html")));
app.get("/control", (_q, r) => r.sendFile(path.join(__dirname, "ui", "agency.html")));
app.get("/setup", (_q, r) => r.sendFile(path.join(__dirname, "ui", "setup.html")));

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

// SETUP — the /setup screen posts the config here; we write .env and the
// connector's SITE line so the owner never edits files by hand.
app.post("/api/setup", (req, res) => {
  try {
    const b = req.body || {};
    const platform = String(b.platform || "").toLowerCase(); // 'wordpress' | 'custom'
    const siteUrl = String(b.siteUrl || "").trim();

    if (platform !== "wordpress" && platform !== "custom")
      return res.status(400).json({ ok: false, error: "Choose WordPress or Custom backend." });
    if (!siteUrl)
      return res.status(400).json({ ok: false, error: "Enter your website URL." });

    // JSON.stringify yields a safely-escaped double-quoted string that is also a
    // valid Python string literal — so odd characters in a token can't break the file.
    const py = (v) => JSON.stringify(String(v == null ? "" : v));
    let siteLine;
    if (platform === "wordpress") {
      if (!b.wpUser || !b.wpPassword)
        return res.status(400).json({ ok: false, error: "WordPress needs a username and application password." });
      siteLine = `SITE = WordPressSite(${py(siteUrl)}, ${py(b.wpUser)}, ${py(b.wpPassword)})`;
    } else {
      const apiBase = String(b.apiBase || "").trim();
      if (!apiBase || !b.apiToken)
        return res.status(400).json({ ok: false, error: "Custom backend needs an API base URL and token." });
      siteLine = `SITE = CustomSite(${py(apiBase)}, ${py(b.apiToken)})`;
    }

    // one clean line per value; strip newlines so nobody can inject extra keys.
    const one = (v) => String(v == null ? "" : v).replace(/[\r\n]+/g, " ").trim();
    const isCustom = platform === "custom";
    const envText = [
      "# Written by the setup screen (/setup). Secrets are stored here only — keep it out of git.",
      `ANTHROPIC_API_KEY=${one(b.anthropicKey)}`,
      `DATAFORSEO_LOGIN=${one(b.dfsLogin)}`,
      `DATAFORSEO_PASSWORD=${one(b.dfsPassword)}`,
      `TELEGRAM_BOT_TOKEN=${one(b.telegramToken)}`,
      `TELEGRAM_CHAT_ID=${one(b.telegramChatId)}`,
      `SITE_API_BASE=${one(isCustom ? b.apiBase : "")}`,
      `SITE_API_TOKEN=${one(isCustom ? b.apiToken : "")}`,
      `OUTREACH_DOMAIN=${one(b.outreachDomain)}`,
      `DAILY_SPEND_CAP_USD=${one(b.spendCap) || "15"}`,
      `OUTREACH_DAILY_CAP=${one(b.outreachCap) || "25"}`,
      `PORT=${process.env.PORT || "3000"}`,
      "",
    ].join("\n");
    const envPath = path.join(__dirname, ".env");
    fs.writeFileSync(envPath, envText, { mode: 0o600 });
    try { fs.chmodSync(envPath, 0o600); } catch (_) {}

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

    // never echo secrets back to the screen
    return res.json({ ok: true, platform, siteUrl, wrote: [".env", "mcp/site_connector/connector.py"] });
  } catch (e) {
    return res.status(500).json({ ok: false, error: "Could not save: " + e.message });
  }
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`Agency running on :${PORT}`));
