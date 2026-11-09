/* tour.js — THE GUIDED TOUR.

   "when user will create a new account the program should have a tour that
    will teach the new user on how to use the program a complete tour on each
    section that will teach user"

   It starts by itself the first time the office opens after an account is
   made (ui/account.html leaves a note for it), and can be taken again any
   time from Help and reports, or Settings. Each step opens a section, marks
   it in the menu, points at the one thing on it that matters most, and says
   in two or three lines what the section is for and what to do there.

   Nothing in it changes anything: it only opens screens and points. It never
   presses a button for the person. Esc, or "Skip the tour", ends it at once.
   The text is the program's own, set as text — nothing read from the server
   is put into it. */
(function () {
  const STEPS = [
    {r: 'today', spot: '#gettingStarted', t: 'Today',
     s: 'Your first screen every day: what the agency did, what needs you, and how today’s run is going. Until everything is set up, Getting started lists the four steps left — do them in order.'},
    {r: 'today', spot: '#runNow', t: 'Run today',
     s: 'Once your orders are in, press Run today. Every desk does its part, and anything that would go out — an email, a post, a change to your website — waits for your yes.'},
    {r: 'orders', spot: '#orderText', t: 'Your orders',
     s: 'Tell a desk what you want, in your own words: “find 20 facilities firms in JLT”, “write about pergolas this week”. Without an order, the day only checks the desks.'},
    {r: 'agency', t: 'Your agency',
     s: 'The team at work: every sector and desk, and whether each is working, done or stopped. A red number means a desk failed or is stuck — open it and it says why.'},
    {r: 'waiting', spot: '#apprList', t: 'Waiting on you',
     s: 'Nothing goes out, gets published or touches your website until you say yes here. Read each one, change it if you like, then approve or turn it down.'},
    {r: 'leads', spot: '#moreLeads', t: 'Leads',
     s: 'Businesses that might buy from you. Each one is checked before anyone writes to it, and nobody is ever emailed twice. Find more leads asks the Leads desk for a fresh batch.'},
    {r: 'content', spot: '#jobOrder', t: 'Content',
     s: 'Posts for your website, written in your voice and polished so they read like a person wrote them. Pick a topic, edit any post, and choose what goes up. Tell Content about a real job you finished — those make the best posts.'},
    {r: 'website', spot: '#lookNow', t: 'Website',
     s: 'Your website, checked every day: up or down, how fast, what is broken. Fixes are tried on a copy first and only go live after the tests pass and you agree. To connect your website, use Connection settings here (or Getting started on Today): it gives you your own connection file to upload, or the WordPress helper.'},
    {r: 'google', spot: '#askAgain', t: 'On Google',
     s: 'Which of your pages Google lists, and how they are doing, read from your Search Console every morning.'},
    {r: 'activity', t: 'Activity',
     s: 'Everything every desk has done, in order. Nothing happens in the dark: if you wonder why something happened, the answer is here.'},
    {r: 'results', t: 'Results',
     s: 'What each sector produced today, this week or this month — leads found, emails sent, posts written, fixes made. If one made nothing, it says why.'},
    {r: 'check', spot: '#checkUrl', t: 'Website Check',
     s: 'A full report on any website — yours, a rival’s, or one you would like to win as a client. It reads the pages the way a visitor would and changes nothing.'},
    {r: 'settings', spot: '#updCheck', t: 'Settings',
     s: 'Your business details, the AI your agency thinks with, email sending, your plan and your password. At the bottom: this program’s version, and the button to look for a new one. When a new version is out, Botix tells you and updating is one press.'},
    {r: 'help', spot: '#tourAgain', t: 'Help and reports',
     s: 'Something not working? Tell us here — only we can read it, and our answer comes back on this screen. You can take this tour again from here any time.'},
  ];

  const css = document.createElement('style');
  css.textContent = `
.tour-card{position:fixed;right:24px;bottom:24px;z-index:60;width:min(400px,calc(100vw - 32px));background:var(--surface);color:var(--ink);
  border:1px solid var(--line-strong);border-radius:14px;box-shadow:var(--shadow),0 18px 48px rgba(17,19,24,.18);padding:18px 20px;
  display:flex;flex-direction:column;gap:10px}
.tour-card .tour-step{font-size:12.5px;color:var(--faint);letter-spacing:.04em;text-transform:uppercase}
.tour-card h2{margin:0;font-size:18px;letter-spacing:-.01em}
.tour-card p{margin:0;font-size:14.5px;line-height:1.55;color:var(--ink-2)}
.tour-card .tour-bar{height:3px;border-radius:3px;background:var(--line-soft);overflow:hidden}
.tour-card .tour-bar i{display:block;height:100%;background:var(--accent);transition:width .25s}
.tour-card .tour-btns{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.tour-on{box-shadow:inset 0 0 0 2px var(--accent)!important}
.tour-spot{outline:3px solid var(--accent)!important;outline-offset:4px;border-radius:10px;transition:outline-offset .2s}
@media (max-width:760px){.tour-card{left:16px;right:16px;bottom:84px;width:auto}}
@media (prefers-reduced-motion:reduce){.tour-card .tour-bar i,.tour-spot{transition:none}}`;
  document.head.appendChild(css);

  let at = -1, card = null, waitT = null;
  const KEY = 'botix-tour';
  const store = (v) => { try { if (v) localStorage.setItem(KEY, v); else localStorage.removeItem(KEY); } catch (e) { /* private window */ } };
  const read = () => { try { return localStorage.getItem(KEY) || ''; } catch (e) { return ''; } };

  function unmark() {
    document.querySelectorAll('.tour-on').forEach((x) => x.classList.remove('tour-on'));
    document.querySelectorAll('.tour-spot').forEach((x) => x.classList.remove('tour-spot'));
    clearTimeout(waitT);
  }

  function el(tag, cls, text) {
    const x = document.createElement(tag);
    if (cls) x.className = cls;
    if (text != null) x.textContent = text;
    return x;
  }

  function draw() {
    const step = STEPS[at];
    if (!card) {
      card = el('aside', 'tour-card');
      card.setAttribute('role', 'dialog');
      card.setAttribute('aria-label', 'Guided tour');
      card.setAttribute('aria-live', 'polite');
      document.body.appendChild(card);
    }
    card.textContent = '';
    const bar = el('div', 'tour-bar'), fill = el('i');
    fill.style.width = Math.round(((at + 1) / STEPS.length) * 100) + '%';
    bar.appendChild(fill);
    const btns = el('div', 'tour-btns');
    const back = el('button', 'btn ghost small', 'Back');
    back.type = 'button'; back.disabled = at === 0; back.onclick = () => go(at - 1);
    const next = el('button', 'btn small', at === STEPS.length - 1 ? 'Finish' : 'Next');
    next.type = 'button'; next.onclick = () => (at === STEPS.length - 1 ? end(true) : go(at + 1));
    const skip = el('button', 'btn quiet small', 'Skip the tour');
    skip.type = 'button'; skip.onclick = () => end(false);
    btns.append(back, next, skip);
    card.append(el('span', 'tour-step', `Step ${at + 1} of ${STEPS.length}`), el('h2', '', step.t), el('p', '', step.s), bar, btns);
    next.focus({preventScroll: true});
  }

  function mark(step, mine) {
    // The menu entry (or, on a phone, More for the sections not on the tab bar).
    const nav = document.querySelectorAll(`.nav a[data-r="${step.r}"], .tabs a[data-r="${step.r}"]`);
    nav.forEach((x) => x.classList.add('tour-on'));
    if (!document.querySelector(`.tabs a[data-r="${step.r}"]`)) {
      const more = document.querySelector('.tabs a[data-r="more"]'); if (more) more.classList.add('tour-on');
    }
    if (!step.spot) return;
    // The screen draws itself a moment after it opens: look for the thing to point at for a few seconds.
    let tries = 0;
    const look = () => {
      if (mine !== at) return;
      const target = document.querySelector(step.spot);
      if (target && target.offsetParent !== null) {
        target.classList.add('tour-spot');
        const calm = matchMedia('(prefers-reduced-motion: reduce)').matches;
        target.scrollIntoView({block: 'center', behavior: calm ? 'auto' : 'smooth'});
        return;
      }
      if (++tries < 30) waitT = setTimeout(look, 150);
    };
    look();
  }

  function go(i) {
    if (i < 0 || i >= STEPS.length) return;
    unmark();
    at = i;
    store('step:' + i);
    const step = STEPS[i];
    const here = (location.hash || '#today').slice(1).split('?')[0];
    if (here !== step.r) location.hash = '#' + step.r;
    draw();
    mark(step, i);
  }

  function end(finished) {
    unmark();
    at = -1;
    if (card) { card.remove(); card = null; }
    store('done');
    if (typeof toast === 'function') toast(finished ? 'That is the tour. Help and reports has it again any time.' : 'Tour closed. Help and reports has it again any time.');
  }

  document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && at >= 0) end(false); });
  // A screen redrawn under the tour keeps its pointer.
  window.addEventListener('hashchange', () => {
    if (at < 0) return;
    const step = STEPS[at];
    const here = (location.hash || '#today').slice(1).split('?')[0];
    if (here !== step.r) { end(false); return; }      // he went somewhere else: the tour steps aside
    setTimeout(() => { unmark(); mark(step, at); }, 50);
  });

  window.startTour = () => go(0);

  // Started once, by itself, the first time the office opens after sign-up.
  // A tour left half way (the program was closed) carries on where it was.
  function autostart() {
    if (typeof needsKey === 'function' && needsKey()) return;     // the plan key first
    const v = read();
    if (v === 'start') return setTimeout(() => go(0), 700);
    const m = /^step:(\d+)$/.exec(v);
    if (m) setTimeout(() => go(Math.min(Number(m[1]), STEPS.length - 1)), 700);
  }
  if (document.readyState === 'complete') autostart(); else window.addEventListener('load', autostart);
})();
