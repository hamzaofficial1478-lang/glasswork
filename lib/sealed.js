/* sealed.js — the server's half of coordination/vault.py's sealed files.

   A sealed settings or config file is: the line GLASSWORK-SEALED-1, then
   base64(nonce | ciphertext | tag) — AES-256-GCM under the account's file key
   (HKDF-SHA256 of the master key, "glasswork files"), with the file's place
   inside the account folder bound in so a sealed file moved elsewhere stops
   opening. Exactly vault.seal()/unseal(), so either side reads what the other
   wrote; coordination/account_tests.py proves it both ways. */
"use strict";
const crypto = require("crypto");

const SEAL_MAGIC = "GLASSWORK-SEALED-1\n";

function fileKey(master) {
  return Buffer.from(crypto.hkdfSync("sha256", master, Buffer.alloc(0),
                                     Buffer.from("glasswork files"), 32));
}

function isSealed(data) {
  return Buffer.from(data).subarray(0, SEAL_MAGIC.length).equals(Buffer.from(SEAL_MAGIC));
}

function seal(master, text, where) {
  const nonce = crypto.randomBytes(12);
  const c = crypto.createCipheriv("aes-256-gcm", fileKey(master), nonce);
  c.setAAD(Buffer.from(where));
  const box = Buffer.concat([c.update(Buffer.from(String(text), "utf8")), c.final(), c.getAuthTag()]);
  return Buffer.from(SEAL_MAGIC + Buffer.concat([nonce, box]).toString("base64") + "\n");
}

function unseal(master, data, where) {
  data = Buffer.from(data);
  if (!isSealed(data)) return data.toString("utf8");
  const raw = Buffer.from(data.subarray(SEAL_MAGIC.length).toString("ascii").trim(), "base64");
  const d = crypto.createDecipheriv("aes-256-gcm", fileKey(master), raw.subarray(0, 12));
  d.setAAD(Buffer.from(where));
  d.setAuthTag(raw.subarray(raw.length - 16));
  return Buffer.concat([d.update(raw.subarray(12, raw.length - 16)), d.final()]).toString("utf8");
}

module.exports = { SEAL_MAGIC, fileKey, isSealed, seal, unseal };
