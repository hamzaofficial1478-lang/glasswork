/* sw.js — lets a browser install Botix as an app on a computer or a phone.

   It keeps nothing: every page and every answer comes from the server as
   usual, so nobody's data is ever stored on the device. All it adds is a
   plain page, instead of the browser's error, when the connection is down,
   and opening the right screen when an alert is tapped. */
const OFFLINE = '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
  + '<title>Botix — offline</title><body style="margin:0;display:grid;place-items:center;height:100vh;'
  + 'font:16px/1.5 system-ui,sans-serif;background:#F3F5F5;color:#16201c"><main style="max-width:420px;padding:24px">'
  + '<h1 style="font-size:20px">No connection</h1><p>Botix works over the internet. Check your connection, '
  + 'then <a href="/" style="color:#1f5f4f">try again</a>.</p></main>';
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));
// An alert tapped on the phone or the desktop opens Botix at the right screen.
self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const go = (e.notification.data && e.notification.data.go) || '/';
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((all) => {
    const open = all.find((c) => new URL(c.url).origin === self.location.origin);
    if (open) { open.focus(); return open.navigate(new URL(go, self.location.origin).href); }
    return self.clients.openWindow(go);
  }));
});
self.addEventListener('fetch', (e) => {
  if (e.request.mode !== 'navigate') return;
  e.respondWith(fetch(e.request).catch(() => new Response(OFFLINE, { headers: { 'Content-Type': 'text/html; charset=utf-8' } })));
});
