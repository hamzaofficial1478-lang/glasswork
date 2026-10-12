/* publisher.js — YOUR REPORTS, and putting your program out. On the
   developer's own copy only.

   This file is left out of every copy he gives out (coordination/product.py,
   PUBLISHER_ONLY), and app.html loads it only when the office says this copy
   is the developer's — so the room, its menu entry and its Settings card (the
   "room hooks") do not exist anywhere else.

   The reports and his answers are kept on this computer
   (coordination/publisher.py). Opening the room collects what is waiting in
   his Glasswork Home: sealed envelopes the website itself cannot read, deleted
   there once they are here. Nothing counts or lists the people using it. */

const PUB = {filter: 'open', collecting: false, lastSay: ''};
const COLLECT_EVERY_MS = 30 * 1000;      // opening the room collects, but not twice in half a minute

function pubSeen(ts) {
  if (!ts) return 'never';
  const d = Math.floor((Date.now() / 1000 - Number(ts)) / 86400);
  return d <= 0 ? 'today ' + when(ts) : d === 1 ? 'yesterday' : d < 30 ? d + ' days ago'
    : new Date(Number(ts) * 1000).toLocaleDateString('en-GB', {day: 'numeric', month: 'short', year: 'numeric'});
}

async function publisherNav() {
  const p = await api('/api/publisher');
  S.publisher = !!(p && p.ok && p.ready);
  if (!S.publisher || !p.home_ok_at) return;
  const inb = await api('/api/publisher/inbox');
  const n = Number((inb && inb.new) || 0), c = $('#navRep');
  if (c) { c.textContent = n; c.hidden = !n; }
}

/* Some publisher settings sit behind the Setup password. Asked for here, in
   place — the same password he signs in with — rather than sending him off to
   another page and back. */
function askPassword(box, why) {
  return new Promise((resolve) => {
    box.innerHTML = `<div class="note" style="display:flex;flex-direction:column;gap:8px"><span>${esc(why)}</span>
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center"><input type="password" id="unlockPw" autocomplete="current-password" placeholder="Your password" style="max-width:260px">
      <button class="btn small" type="button" id="unlockGo">Confirm</button><button class="btn quiet small" type="button" id="unlockNo">Cancel</button></div></div>`;
    $('#unlockGo').onclick = () => resolve($('#unlockPw').value);
    $('#unlockPw').onkeydown = (e) => { if (e.key === 'Enter') resolve($('#unlockPw').value); };
    $('#unlockNo').onclick = () => resolve(null);
    $('#unlockPw').focus();
  });
}
async function unlocked(call, box) {
  let j = await call();
  for (let i = 0; i < 3 && j && j.needsSetupUnlock; i++) {
    const pw = await askPassword(box, i ? 'That was not the password — try again.' : 'To change publisher settings, confirm it is you: the password you sign in with.');
    if (pw === null) { box.innerHTML = ''; return {ok: false, error: 'Left as it was.'}; }
    const u = await api('/api/setup/unlock', {password: pw, json: true});
    if (u && u.ok) { box.innerHTML = ''; j = await call(); }
  }
  return j || {ok: false};
}
async function downloadHome(box) {
  const get = async () => {
    try {
      const r = await fetch('/api/publisher/home.zip', {cache: 'no-store'});
      if (r.ok) return {ok: true, blob: await r.blob()};
      try { return await r.json(); } catch (e) { return {ok: false, error: 'It could not be made.'}; }
    } catch (e) { return {ok: false, error: 'The office did not answer.'}; }
  };
  const j = await unlocked(get, box);
  if (!j.ok) { toast(j.error || 'Not downloaded.'); return false; }
  const a = document.createElement('a'); a.href = URL.createObjectURL(j.blob); a.download = 'glasswork-home.zip';
  document.body.appendChild(a); a.click(); a.remove();
  toast('Downloaded glasswork-home.zip — now unzip it and upload the folder to your website.');
  return true;
}
function homeSteps(home) {
  return `<ol class="steps">
    <li><b>Download it.</b> <button class="btn small" type="button" id="homeGet">Download glasswork-home.zip</button></li>
    <li><b>Unzip it.</b> Right-click the file → <i>Extract All</i>. You get a folder called <span class="mono">glasswork-home</span>.</li>
    <li><b>Put it on your website.</b> In your hosting's <i>File Manager</i>, open your website's main folder — the one with your home page in it, usually <span class="mono">public_html</span> — and upload the whole <span class="mono">glasswork-home</span> folder there. If one is there from before, replace it.</li>
    <li><b>Check it.</b> <button class="btn ghost small" type="button" id="homeCheck">Check it</button> <span class="muted" id="homeSay" style="font-size:14px"></span>
      ${home ? `<br><span class="muted" style="font-size:13.5px">It should answer at <span class="mono">${esc(home)}/</span> — your download page for new people.</span>` : ''}</li>
  </ol><div id="homeBox"></div>`;
}
function wireHomeSteps(main) {
  const g = $('#homeGet'); if (g) g.onclick = () => downloadHome($('#homeBox'));
  const c = $('#homeCheck'); if (c) c.onclick = async () => {
    c.disabled = true; $('#homeSay').textContent = 'Knocking on your website…';
    const j = await api('/api/publisher/check', {}); c.disabled = false;
    if (j.ok) { toast(j.head || 'Your Home answers.'); reports(main); publisherNav(); return; }
    $('#homeSay').textContent = j.error || 'It did not answer.';
  };
}

const KEPT_HERE = 'Kept on this computer — not on your website.';

async function reports(main) {
  const p = await api('/api/publisher');
  if (!(p && p.ok && p.can_publish)) {
    main.innerHTML = `<div class="page narrow">${head('Reports')}<div class="card">${empty('Not on this copy',
      'The Reports room is on the copy the program is made from — the developer\'s own.')}</div></div>`;
    return;
  }
  if (!p.ready) {
    // Never filled in with the website the agency looks after: that is a client's, not the program's.
    const site = p.home_url ? p.home_url.replace(/\/glasswork-home$/, '') : '';
    main.innerHTML = `<div class="page narrow">${head('Reports',
      'Problem reports from the people using your program, kept here on this computer. Your program\'s own website only carries a postbox and the download page: a report is sealed on their computer so only this one can open it, and it leaves the website as soon as you collect it.')}
      <section class="card" style="padding:20px 22px;display:flex;flex-direction:column;gap:12px">
        <h2 style="margin:0;font-size:18px">${p.publisher ? 'Make your Glasswork Home again' : 'Make your Glasswork Home'}</h2>
        <label for="homeSite" style="font-weight:600">Your program's own website</label>
        <span class="muted" style="font-size:14px;margin-top:-6px">A website that belongs to you and the program — not a client's website the agency looks after. People download the program from it.</span>
        <input id="homeSite" placeholder="https://www.yourprogram.com" value="${esc(site)}">
        <label for="homeMail" style="font-weight:600">Email me when a report is waiting <span class="muted" style="font-weight:400">(optional — the email says only that one is waiting)</span></label><input id="homeMail" type="email" placeholder="you@yourwebsite.com" value="${esc(p.email || '')}">
        <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center"><button class="btn" type="button" id="homeMake">Make it</button><span class="muted" id="homeMakeSay" style="font-size:14px"></span></div>
        <div id="homeMakeBox"></div>
        <p class="muted" style="margin:0;font-size:14px">Then you upload one folder to your website, once. The same folder carries your updates to people and is their download page.</p>
      </section></div>`;
    $('#homeMake').onclick = async () => {
      const b = $('#homeMake'); b.disabled = true; $('#homeMakeSay').textContent = 'Making it…';
      const body = {site: $('#homeSite').value.trim(), email: $('#homeMail').value.trim()};
      const j = await unlocked(() => api('/api/publisher/setup', body), $('#homeMakeBox'));
      b.disabled = false;
      if (!j.ok) { $('#homeMakeSay').textContent = j.error || 'Not made.'; return; }
      VIEW.dirty = false; toast('Made. Now put it on your website — the steps are on this page.');
      await publisherNav(); reports(main);
    };
    return;
  }
  if (!p.home_ok_at) {
    main.innerHTML = `<div class="page narrow">${head('Reports', 'Your Glasswork Home is made. One more thing: it has to be on your website before people can reach it.')}
      <section class="card" style="padding:20px 22px;display:flex;flex-direction:column;gap:12px">
        <h2 style="margin:0;font-size:18px">Put your Home on your website</h2>
        ${homeSteps(p.home_url)}</section></div>`;
    wireHomeSteps(main);
    return;
  }
  const inb = await api('/api/publisher/inbox');
  const all = (inb && inb.reports) || [];
  const shown = all.filter((r) => PUB.filter === 'all' || (PUB.filter === 'open' ? !['fixed', 'wont-fix'].includes(r.status) : r.status === PUB.filter));
  const last = p.last_collect;
  main.innerHTML = `<div class="page">${head('Reports', 'What the people using your program told you, and your answers. Each report stands alone: nothing says who sent it beyond what they chose to write.',
    `<div role="group" aria-label="Which reports" style="display:flex;gap:8px;flex-wrap:wrap">${[['open', 'Still open'], ['new', 'New'], ['fixed', 'Fixed'], ['all', 'All']].map(([k, n]) =>
      `<button type="button" class="seg ${PUB.filter === k ? 'on' : ''}" aria-pressed="${PUB.filter === k}" data-pf="${k}">${n}</button>`).join('')}</div>`)}
    <div class="card" style="padding:12px 18px;display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center;justify-content:space-between">
      <span class="muted" style="font-size:14px"><span class="lamp done"></span> Kept on this computer — not on your website. <span id="pubLast">${last ? 'Last collected ' + esc(pubSeen(last)) + '.' : 'Not collected yet.'}</span></span>
      <span style="display:flex;gap:10px;align-items:center"><span class="muted" id="pubColSay" style="font-size:14px">${esc(PUB.lastSay)}</span><button class="btn ghost small" type="button" id="pubCollect">Check for new</button></span></div>
    ${inb && inb.ok === false ? `<div class="note">${esc(inb.error || 'Your records could not be opened.')}</div>` : ''}
    <section class="card">${shown.length ? shown.map((r) => { const lk = REP_LOOK[r.status] || ['plain', r.status];
      return `<button type="button" class="row" data-rep="${esc(r.id)}" style="width:100%;text-align:left;background:none;border:0;border-bottom:1px solid var(--line-soft);cursor:pointer;color:var(--ink)">
        <span class="grow"><b style="font-weight:600">${esc(r.title)}</b><span class="muted" style="font-size:14px">${esc(r.text || '')}</span>
        <span class="muted mono" style="font-size:12.5px">${esc(r.contact || 'no email given')} · ${esc(pubSeen(r.at))} · version ${esc(r.version || '?')}${r.has_support ? ' · support report' : ''}${r.unsent ? ' · answer waiting to go' : ''}</span></span>
        ${pill(lk[0], lk[1])}</button>`; }).join('') : empty('Nothing here', all.length ? 'No report matches this filter.' : 'When someone sends a report, it appears here.')}</section>
  </div>`;
  main.querySelectorAll('[data-pf]').forEach((b) => b.onclick = () => { PUB.filter = b.dataset.pf; reports(main); });
  main.querySelectorAll('[data-rep]').forEach((b) => b.onclick = () => openReport(b.dataset.rep, main));
  $('#pubCollect').onclick = () => collectNow(main, true);
  if (!last || Date.now() / 1000 - Number(last) > COLLECT_EVERY_MS / 1000) collectNow(main, false);
}

/* Take home what is waiting in the postbox. The room is drawn from his records
   first, so it opens at once; it is drawn again only if something new came. */
async function collectNow(main, asked) {
  if (PUB.collecting) return;
  PUB.collecting = true;
  const b = $('#pubCollect'), say = $('#pubColSay');
  if (b) b.disabled = true;
  if (say) say.textContent = 'Collecting from your Home…';
  const j = await api('/api/publisher/collect', {});
  PUB.collecting = false;
  PUB.lastSay = j.ok ? (j.head || 'Collected.') : (j.error || 'Your Home did not answer.');
  const fresh = j.ok && j.new_reports;
  if (VIEW.r === 'reports' && (fresh || asked)) { reports(main); publisherNav(); return; }
  if (b) b.disabled = false;
  if (say) say.textContent = PUB.lastSay;
  const l = $('#pubLast'); if (l && j.ok) l.textContent = 'Last collected just now.';
}

async function openReport(id, main) {
  openPanel(`${panelHead('A report', id)}<div class="dr-body"><section class="dr-sec"><p class="muted">Opening…</p></section></div>`, 'A report', 'report', id);
  const j = await api('/api/publisher/read/' + encodeURIComponent(id));
  const r = (j && j.report) || null;
  const body = $('#drawer .dr-body'); if (!body) return;
  if (!r) { body.innerHTML = `<section class="dr-sec"><p>${esc((j && j.error) || 'It could not be read.')}</p></section>`; return; }
  body.innerHTML = `<section class="dr-sec"><h3 style="font-size:17px">${esc(r.title)}</h3>
      <span class="muted mono" style="font-size:12.5px">${esc(when(r.at))} · version ${esc(r.version || '?')} · ${esc(r.system || '')}</span>
      <p style="white-space:pre-wrap">${esc(r.text || '(no words)')}</p>
      ${r.contact ? `<p>Reply by email: <a href="mailto:${esc(r.contact)}">${esc(r.contact)}</a></p>` : ''}</section>
    ${r.support ? `<section class="dr-sec"><b style="font-weight:600">Their support report</b><pre class="mono" style="margin:0;white-space:pre-wrap;font-size:12.5px;background:var(--sunken);padding:10px 12px;border-radius:8px;max-height:280px;overflow:auto">${esc(r.support)}</pre></section>` : ''}
    <section class="dr-sec"><label for="ansStatus" style="font-weight:600">Where it stands</label>
      <select id="ansStatus">${Object.entries(REP_LOOK).map(([k, v]) => `<option value="${k}" ${r.status === k ? 'selected' : ''}>${esc(v[1])}</option>`).join('')}</select>
      <label for="ansFixed" style="font-weight:600">Fixed in version <span class="muted" style="font-weight:400">(they are told to update to it)</span></label>
      <input id="ansFixed" maxlength="40" value="${esc(r.fixed_in || '')}" placeholder="${esc((S.brand && S.brand.version) || '0.8.0')}">
      <label for="ansReply" style="font-weight:600">Your answer — sealed to their report; they read it in their Help screen</label>
      <textarea id="ansReply" maxlength="5000" style="min-height:110px">${esc(r.reply || '')}</textarea>
      <div style="display:flex;gap:10px;flex-wrap:wrap"><button class="btn" type="button" id="ansSave">Save and send</button>
        <button class="btn quiet" type="button" id="ansDel">Delete the report</button></div></section>`;
  $('#ansSave').onclick = async () => {
    const x = await api('/api/publisher/answer', {id, status: $('#ansStatus').value, reply: $('#ansReply').value, fixed_in: $('#ansFixed').value.trim()});
    toast(!x.ok ? (x.error || 'Not saved.') : x.sent ? 'Sent. They see it next time they open Help.' : (x.note || 'Saved.'));
    if (x.ok) { closePanel(); reports(main); publisherNav(); }
  };
  $('#ansDel').onclick = async () => {
    if (!confirm('Delete this report from your records? It cannot be brought back.')) return;
    const x = await api('/api/publisher/remove', {id}); toast(x.ok ? 'Deleted.' : (x.error || 'Not deleted.'));
    if (x.ok) { closePanel(); reports(main); publisherNav(); }
  };
}

/* Settings → Publisher: make the Home, check it, put a version out. */
async function publisherCard() {
  const box = $('#pubBox'); if (!box) return;
  const p = await api('/api/publisher');
  if (!(p && p.ok && p.can_publish)) return;
  box.hidden = false;
  box.innerHTML = `<h2 class="label" style="margin:0">Publisher — for whoever makes this program</h2>
    <div class="card" style="padding:18px 20px;display:flex;flex-direction:column;gap:12px">
      ${p.ready ? `<div><b style="font-weight:600">Your Glasswork Home</b> <span class="muted mono" style="font-size:13px">${esc(p.home_url)}</span></div>
        <p class="muted" style="margin:0;font-size:14px">A postbox and a download page on your program's own website. The reports are kept on this computer, not there. Every version you put out goes there for people to update to.</p>
        <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center"><button class="btn ghost small" type="button" id="pubCheck">Check it</button>
          <button class="btn quiet small" type="button" id="pubAgain">Download it again</button><a class="btn quiet small" href="#reports">Open Reports</a>
          <span class="muted" id="pubSay" style="font-size:14px"></span></div>
        <div style="border-top:1px solid var(--line-soft);padding-top:12px;display:flex;flex-direction:column;gap:10px">
          <b style="font-weight:600">Put out version ${esc(p.version)}</b>
          <label for="pubNotes" class="muted" style="font-size:14px">What is new, in a line or two — people read this in the update message</label>
          <textarea id="pubNotes" maxlength="2000" style="min-height:70px" placeholder="Faster screens, and a stronger connection to your website."></textarea>
          <label style="display:flex;gap:10px;align-items:center;cursor:pointer"><input type="checkbox" id="pubExe"> Build the Windows installer too (on a Windows computer, a few minutes)</label>
          <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center"><button class="btn small" type="button" id="pubGo">Put it out</button><span class="muted" id="pubJob" style="font-size:14px"></span></div>
        </div>`
      : `<p style="margin:0">Problem reports kept on this computer; your updates and a download page on your program's own website — never a client's. One folder, uploaded once.</p>
        <a class="btn small" href="#reports" style="align-self:flex-start">Set it up in Reports</a>`}
      <div id="pubUnlock"></div>
    </div>`;
  const say = (t) => { const x = $('#pubSay'); if (x) x.textContent = t; };
  const again = $('#pubAgain'); if (again) again.onclick = () => downloadHome($('#pubUnlock'));
  const ck = $('#pubCheck'); if (ck) ck.onclick = async () => { ck.disabled = true; say('Knocking…'); const j = await api('/api/publisher/check', {}); ck.disabled = false; say(j.ok ? j.head : (j.error || 'It did not answer.')); };
  const go = $('#pubGo'); if (go) go.onclick = async () => {
    const exe = $('#pubExe').checked;
    if (!confirm(`Put out version ${p.version}${exe ? ', with the Windows installer' : ''}? Everyone using the program is told the next time it opens.`)) return;
    go.disabled = true;
    const body = {notes: $('#pubNotes').value.trim(), installer: exe};
    const j = await unlocked(() => api('/api/publisher/publish', body), $('#pubUnlock'));
    if (!j.ok) { go.disabled = false; $('#pubJob').textContent = j.error || 'It did not start.'; return; }
    VIEW.dirty = false;
    const tick = async () => {
      const s = await ask('/api/publisher/job');
      if (s && s.running) { $('#pubJob').textContent = (s.stage || 'Working') + (s.of ? ` (${s.part} of ${s.of})` : '') + '…'; setTimeout(tick, 1500); return; }
      go.disabled = false;
      const r = (s && s.result) || {};
      $('#pubJob').textContent = r.ok ? r.why : (r.error || 'It stopped.');
      if (r.ok) toast(r.why);
    };
    tick();
  };
}

/* The room hooks: a screen, a menu entry, a line on More, a Settings card. */
(function joinTheApp() {
  ROUTES.reports = reports;
  NO_REDRAW.add('reports');
  const help = document.querySelector('nav a[data-r="help"]');
  if (help && !$('#navReports')) {
    help.insertAdjacentHTML('afterend', '<a href="#reports" data-r="reports" id="navReports">Reports <span class="count" id="navRep" hidden title="new reports">0</span></a>');
  }
  EXTRA.more.push((item) => item('#reports', 'Reports', 'Problem reports from the people using your program'));
  EXTRA.settings.push(publisherCard);
  publisherNav();
  if (location.hash === '#reports' || VIEW.r === 'settings' || VIEW.r === 'more') route();
})();
