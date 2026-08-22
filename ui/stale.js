/* stale.js — "the program running is older than this screen"
 *
 * WHY THIS FILE EXISTS
 *
 * The screens in ui/ are files on disk. The browser re-reads them every time you
 * refresh. server.js is read ONCE, when the process starts. So there is a state
 * this app can be in that nothing about it used to explain: a brand-new screen
 * talking to an old server, offering controls whose routes do not exist yet.
 *
 * It cost the owner three separate afternoons before this was written:
 *
 *   - the password form said "Could not reach the server" about a server that
 *     was up and answering, because /api/password 404'd in HTML;
 *   - the sector switches appeared to work and silently changed nothing;
 *   - the website name he typed into setup was never saved and never appeared.
 *
 * Every one of those was the same fact, and in every case he had to be told in
 * conversation to restart the server. That is not a fix. The software has to
 * say it, on the screen, at the moment it matters.
 *
 * HOW IT WORKS
 *
 * /api/ping is cheap — no Python, no database — and returns the list of things
 * the running process can do. A server too old to have the route at all answers
 * 404, which is itself the answer. Each page declares what it needs; anything
 * missing puts a banner at the top of the window and, where the page asks for
 * it, disables the controls that cannot work.
 *
 * Usage:
 *   Stale.check(['sectors','site-name'], () => refreshMyControls())
 *   Stale.blocked('sectors')   -> true if that capability is missing
 */
(function (global) {
  var HAVE = null;          // null = not asked yet, [] = old server, [...] = fine
  var REACHED = false;
  var NEED = [];

  function banner(msg) {
    var el = document.getElementById('staleBanner');
    if (!el) {
      el = document.createElement('div');
      el.id = 'staleBanner';
      el.setAttribute('role', 'alert');
      el.style.cssText =
        'position:fixed;left:0;right:0;top:0;z-index:9999;padding:11px 18px;' +
        'background:#4a2f10;border-bottom:1px solid #e8a23d;color:#f4d9a8;' +
        'font:500 13px/1.55 system-ui,sans-serif;display:flex;gap:14px;' +
        'align-items:center;justify-content:center;text-align:center;' +
        'box-shadow:0 2px 14px rgba(0,0,0,.45)';
      document.body.appendChild(el);
      // Push the page down so the banner does not sit on top of the header.
      document.body.style.paddingTop = '46px';
    }
    el.innerHTML = msg;
  }

  function report() {
    if (!REACHED) return;                       // server down is a different problem
    var missing = NEED.filter(function (c) { return HAVE.indexOf(c) < 0; });
    if (!missing.length) return;
    banner(
      '<b>The program running on this machine is older than this screen.</b>' +
      '<span>Restart it &mdash; <code style="background:rgba(0,0,0,.3);padding:2px 6px;' +
      'border-radius:4px">pm2 restart all</code>, or close the window it is running ' +
      'in and start it again. Until you do, some controls here will do nothing when ' +
      'you press them.</span>');
  }

  var Stale = {
    /* Declare what this page needs. Safe to call more than once. */
    check: function (needs, onKnown) {
      NEED = NEED.concat(needs || []);
      if (HAVE !== null) { report(); if (onKnown) onKnown(); return; }
      fetch('/api/ping')
        .then(function (r) {
          REACHED = true;
          // A 404 IS the answer: this server predates the route.
          if (!r.ok) { HAVE = []; return null; }
          return r.json();
        })
        .then(function (j) { HAVE = (j && j.api) || []; })
        .catch(function () { HAVE = []; REACHED = false; })   // genuinely unreachable
        .then(function () { report(); if (onKnown) onKnown(); });
    },
    /* Is this specific capability missing from the running server? */
    blocked: function (cap) {
      return HAVE !== null && REACHED && HAVE.indexOf(cap) < 0;
    },
    /* Known-good yet? Pages use this to avoid disabling things prematurely. */
    known: function () { return HAVE !== null; }
  };

  global.Stale = Stale;
})(window);
