/* madeby.js — "mention build by tech duo on the program end on each section".
   One quiet line at the foot of every screen: the program, the version this
   copy is on, and who made it. On the office (one page whose screens replace
   each other) it is put back each time a screen is drawn. The text is set
   with textContent: nothing from the server is read as markup. */
(function () {
  const info = { name: 'Botix', version: '', company: 'TECH DUO' };
  const css = document.createElement('style');
  css.textContent = '.madeby{display:flex;flex-wrap:wrap;gap:4px 10px;justify-content:center;align-items:center;'
    + 'margin:44px auto 0;padding:14px 0 0;border-top:1px solid var(--line-soft,rgba(127,127,127,.18));'
    + 'max-width:1080px;font-size:12.5px;line-height:1.4;color:var(--faint,#8a8f98);text-align:center}'
    + '.madeby b{font-weight:600;color:var(--ink-2,inherit);letter-spacing:.06em}'
    + 'body>.madeby{margin:28px auto 18px;padding:12px 16px 0;width:calc(100% - 32px)}';
  document.head.appendChild(css);

  function fill(f) {
    f.textContent = '';
    const a = document.createElement('span');
    a.textContent = info.name + (info.version ? ' ' + info.version : '');
    const dot = document.createElement('span');
    dot.setAttribute('aria-hidden', 'true');
    dot.textContent = '·';
    const by = document.createElement('span');
    by.append('Built by ');
    const b = document.createElement('b');
    b.textContent = info.company;
    by.append(b);
    f.append(a, dot, by);
  }

  function host() {
    return document.getElementById('main') || document.querySelector('main') || document.body;
  }

  function place() {
    const h = host();
    let f = null;
    for (const c of h.children) if (c.classList && c.classList.contains('madeby')) f = c;
    if (!f) {
      f = document.createElement('footer');
      f.className = 'madeby';
      h.appendChild(f);
    }
    fill(f);
  }

  function start() {
    place();
    const m = document.getElementById('main');
    if (m) new MutationObserver(() => {
      if (![...m.children].some((c) => c.classList && c.classList.contains('madeby'))) place();
    }).observe(m, { childList: true });
  }

  fetch('/api/product', { cache: 'no-store', credentials: 'same-origin' })
    .then((r) => r.json())
    .then((p) => {
      if (!p || !p.ok) return;
      info.name = String(p.name || info.name);
      info.version = String(p.version || '');
      info.company = String(p.company || info.company);
      document.querySelectorAll('.madeby').forEach(fill);
    })
    .catch(() => {});

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
  else start();
})();
