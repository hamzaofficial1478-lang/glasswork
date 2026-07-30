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
const app = express();
app.use(express.json());
app.use(express.static(path.join(__dirname, "ui")));

app.get("/", (_q, r) => r.sendFile(path.join(__dirname, "ui", "office.html")));
app.get("/control", (_q, r) => r.sendFile(path.join(__dirname, "ui", "agency.html")));

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

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => console.log(`Agency running on :${PORT}`));
