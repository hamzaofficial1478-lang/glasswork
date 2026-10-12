/* sw.js — lets a browser install Botix as an app on a computer or a phone.

   It keeps nothing: every page and every answer comes from the server as
   usual, so nobody's data is ever stored on the device. All it adds is a
   plain page, instead of the browser's error, when the connection is down. */
const OFFLINE = '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
  + '<title>Botix — offline</title><body style="margin:0;display:grid;place-items:center;height:100vh;'
  + 'font:16px/1.5 system-ui,sans-serif;background:#F3F5F5;color:#16201c"><main style="max-width:420px;padding:24px">'
  + '<h1 style="font-size:20px">No connection</h1><p>Botix works over the internet. Check your connection, '
  + 'then <a href="/" style="color:#1f5f4f">try again</a>.</p></main>';
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', (e) => {
  if (e.request.mode !== 'navigate') return;
  e.respondWith(fetch(e.request).catch(() => new Response(OFFLINE, { headers: { 'Content-Type': 'text/html; charset=utf-8' } })));
});
