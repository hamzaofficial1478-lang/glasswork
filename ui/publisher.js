/* publisher.js — YOUR REPORTS, and putting your program out. On the
   developer's own copy only.

   This file is left out of every copy he gives out (coordination/product.py,
   PUBLISHER_ONLY), and app.html loads it only when the office says this copy
   is the developer's — so the room, its menu entry and its Settings card (the
   "room hooks") do not exist anywhere else.

   The reports and his answers are kept on this computer
   (coordination/publisher.py). Opening the room collects what is waiting in
   his Botix Home: sealed envelopes the website itself cannot read, deleted
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
        <h2 style="margin:0;font-size:18px">${p.publisher ? 'Make your Botix Home again' : 'Make your Botix Home'}</h2>
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
    main.innerHTML = `<div class="page narrow">${head('Reports', 'Your Botix Home is made. One more thing: it has to be on your website before people can reach it.')}
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
    <section id="pubOnline" style="display:flex;flex-direction:column;gap:10px"></section>
    <section class="card">${shown.length ? shown.map((r) => { const lk = REP_LOOK[r.status] || ['plain', r.status];
      return `<button type="button" class="row" data-rep="${esc(r.id)}" style="width:100%;text-align:left;background:none;border:0;border-bottom:1px solid var(--line-soft);cursor:pointer;color:var(--ink)">
        <span class="grow"><b style="font-weight:600">${esc(r.title)}</b><span class="muted" style="font-size:14px">${esc(r.text || '')}</span>
        <span class="muted mono" style="font-size:12.5px">${esc(r.contact || 'no email given')} · ${esc(pubSeen(r.at))} · version ${esc(r.version || '?')}${r.has_support ? ' · support report' : ''}${r.unsent ? ' · answer waiting to go' : ''}</span></span>
        ${pill(lk[0], lk[1])}</button>`; }).join('') : empty('Nothing here', all.length ? 'No report matches this filter.' : 'When someone sends a report, it appears here.')}</section>
  </div>`;
  main.querySelectorAll('[data-pf]').forEach((b) => b.onclick = () => { PUB.filter = b.dataset.pf; reports(main); });
  main.querySelectorAll('[data-rep]').forEach((b) => b.onclick = () => openReport(b.dataset.rep, main));
  $('#pubCollect').onclick = () => collectNow(main, true);
  onlineCard();
  if (!last || Date.now() / 1000 - Number(last) > COLLECT_EVERY_MS / 1000) collectNow(main, false);
}

/* YOUR ONLINE OFFICE: how many people use it — counts, never who. Its address
   and owner key are kept here; the key and the Home's address go into the
   server's settings so people online can send reports too. */
async function onlineCard(shown) {
  const box = $('#pubOnline'); if (!box) return;
  const o = shown || await api('/api/publisher/online');
  const st = (o && o.stats) || null;
  const tile = (label, n, sub) => `<div class="card tile"><span class="label">${label}</span><span class="big mono">${esc(n == null ? '—' : n)}</span><span class="muted" style="font-size:13.5px">${sub}</span></div>`;
  const set = o && o.server_settings ? Object.entries(o.server_settings).map(([k, v]) => `Environment=${k}=${v}`).join('\n') : '';
  box.innerHTML = `<h2 class="label" style="margin:0">Your online office</h2>
    ${st ? `<div class="tiles">${tile('Using it today', st.today, 'people signed in today')}${tile('Right now', st.using_now, 'signed in in the last 15 minutes')}
      ${tile('A day, this week', st.average_a_day_7, 'on average')}${tile('Accounts', st.accounts, 'made in all')}</div>
      <p class="muted" style="margin:0;font-size:13.5px">Counts only: nobody's name, websites or work is ever sent here.</p>` : ''}
    ${o && o.url && !st ? `<div class="note">${esc(o.error || 'No answer yet.')}</div>` : ''}
    <details class="card" style="padding:14px 18px" ${o && o.url ? '' : 'open'}><summary style="cursor:pointer;font-weight:600">${o && o.url ? 'Address and key' : 'Connect your online office'}</summary>
      <div style="display:flex;flex-direction:column;gap:10px;margin-top:10px">
        <label for="onUrl" style="font-weight:600">Its address</label><input id="onUrl" placeholder="https://app.yourprogram.com" value="${esc((o && o.url) || '')}">
        <label for="onKey" style="font-weight:600">Owner key <span class="muted" style="font-weight:400">(leave empty and one is made for you)</span></label><input id="onKey" type="password" autocomplete="off">
        <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap"><button class="btn small" type="button" id="onSave">Save and check</button><span class="muted" id="onSay" style="font-size:14px"></span></div>
        <div id="onUnlock"></div>
        ${set ? `<p class="muted" style="margin:0;font-size:14px">Put these lines in your server's <span class="mono">glasswork.service</span> (docs/HOSTING.md, step 3), then restart it:</p>
          <pre class="mono" style="margin:0;white-space:pre-wrap;font-size:12.5px;background:var(--sunken);padding:10px 12px;border-radius:8px">${esc(set)}</pre>` : ''}
      </div></details>`;
  $('#onSave').onclick = async () => {
    $('#onSay').textContent = 'Checking…';
    const body = {url: $('#onUrl').value.trim(), key: $('#onKey').value.trim()};
    const j = await unlocked(() => api('/api/publisher/online', body), $('#onUnlock'));
    if (j && (j.url || j.ok)) { onlineCard(j); return; }
    $('#onSay').textContent = (j && j.error) || 'Not saved.';
  };
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
      ${p.ready ? `<div><b style="font-weight:600">Your Botix Home</b> <span class="muted mono" style="font-size:13px">${esc(p.home_url)}</span></div>
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

/* ======================= CUSTOMERS — plans, keys and payments =======================
   "the program must have the knowledge who's new which user upgraded the plan
   which user cancel the plan and at which date user buy the plan and what is
   the expiry date of each user's plan". Kept by his Botix Home
   (glasswork-home/licences.php): Stripe tells it the moment a plan is bought,
   changed or cancelled; a plan sold by hand is made here. Behind the Setup
   password — these are his customers' addresses. */
const CUS = {filter: 'all', data: null, settings: null};
const CUS_STATUS = {trial: ['Trial', 'work'], active: ['Active', 'done'], past_due: ['Payment overdue', 'wait'],
  cancelled: ['Cancelled', 'stuck'], expired: ['Ended', 'stuck'], trial_used: ['Trial refused', 'stuck'], pending: ['Being confirmed', 'wait']};
const CUS_EVENTS = {bought: 'Bought', upgraded: 'Upgraded', downgraded: 'Moved to a smaller plan', renewed: 'Renewed',
  cancelled: 'Cancelled', reactivated: 'Started again', payment_failed: 'Payment failed', trial_refused: 'Trial refused — had one before',
  computer_added: 'Computer added', computer_moved: 'Moved to another computer', computer_released: 'A computer gave its place back',
  computers_cleared: 'Computers cleared', key_reissued: 'New key made', new_period: 'New period started'};
const CUS_KINDS = [['leads', 'New leads a day'], ['mails', 'Emails a day'], ['posts', 'Posts a day'], ['checks', 'Website Check reports a month']];
const NUM = 'style="width:100%;min-height:40px;padding:8px 12px;border:1px solid var(--line-strong);border-radius:10px;background:var(--surface);color:var(--ink);font-size:15px"';
const FIELD = 'style="display:flex;flex-direction:column;gap:4px;font-size:14px"';
const cusDay = (ts) => ts ? new Date(Number(ts) * 1000).toLocaleDateString('en-GB', {day: 'numeric', month: 'short', year: 'numeric'}) : '—';
const cusApi = (body, box) => unlocked(() => api('/api/publisher/licences', body), box || $('#cusUnlock'));
function cusSum(obj) {
  const out = {};
  Object.entries(obj || {}).forEach(([kind, per]) => { out[kind] = Object.values(per || {}).reduce((a, b) => a + Number(b || 0), 0); });
  return out;
}

async function customers(main) {
  const p = await api('/api/publisher');
  if (!(p && p.ok && p.ready && p.home_ok_at)) {
    main.innerHTML = `<div class="page narrow">${head('Customers')}<div class="card">${empty('Your Home comes first',
      'Plans are kept by your Botix Home. Set it up and put it on your website in <a href="#reports">Reports</a>, then come back.')}</div></div>`;
    return;
  }
  // Behind the Setup password, like everything else here: asked for in place.
  main.innerHTML = `<div class="page narrow">${head('Customers', 'Who bought which plan, since when and until when — and the key each one uses.')}
    <div id="cusUnlock"></div><p class="muted" id="cusLoading">Opening your customers…</p></div>`;
  const v = await cusApi({do: 'view'});
  if (!(v && v.ok)) { $('#cusLoading').textContent = (v && v.error) || 'Left as it was.'; return; }
  if (!v.on) {
    main.innerHTML = `<div class="page narrow">${head('Customers', 'Who bought which plan, since when and until when — and the key each one uses.')}
      <section class="card" style="padding:20px 22px;display:flex;flex-direction:column;gap:12px">
        <h2 style="margin:0;font-size:18px">Switch plans on</h2>
        <p style="margin:0">Every version you put out from then on asks for a key the first time it opens on a computer, and keeps to the plan bought with it: the leads, emails and posts a day, the words in a post, the websites and the Website Check reports. The same whether a customer uses their own AI key or yours. Your own copy never asks.</p>
        <p class="muted" style="margin:0;font-size:14px">A licence key is made on this computer and given to your Home, which signs each customer's plan with it. Upload your Home again first if you made it before today — the newest one knows about plans.</p>
        <div style="display:flex;gap:10px;flex-wrap:wrap;align-items:center"><button class="btn" type="button" id="cusOn">Switch plans on</button><span class="muted" id="cusOnSay" style="font-size:14px"></span></div>
        <div id="cusUnlock"></div></section></div>`;
    $('#cusOn').onclick = async () => {
      $('#cusOn').disabled = true; $('#cusOnSay').textContent = 'Making the key and giving it to your Home…';
      const j = await cusApi({do: 'setup'});
      $('#cusOn').disabled = false;
      if (!j.ok) { $('#cusOnSay').textContent = j.error || 'Not switched on.'; return; }
      toast(j.head || 'Plans are on.'); customers(main);
    };
    return;
  }
  main.innerHTML = `<div class="page">${head('Customers', 'Who bought which plan, since when and until when. Stripe keeps it up to date by itself; plans sold by hand are made here.')}
    <div id="cusUnlock"></div><p class="muted" id="cusLoading">Opening your customers…</p></div>`;
  const [list, st] = [await cusApi({do: 'list'}), CUS.settings || await cusApi({do: 'settings'})];
  if (!list.ok) { $('#cusLoading').textContent = list.error || 'Your Home did not answer.'; return; }
  CUS.data = list; CUS.settings = st && st.ok ? st : null;
  drawCustomers(main);
}

function drawCustomers(main) {
  const all = (CUS.data && CUS.data.licences) || [], plans = (CUS.data && CUS.data.plans) || {};
  const n = (f) => all.filter(f).length, month = Date.now() / 1000 - 30 * 86400;
  const groups = {all: () => true, active: (l) => l.status === 'active', trial: (l) => l.status === 'trial',
    overdue: (l) => l.status === 'past_due', ended: (l) => ['cancelled', 'expired', 'trial_used'].includes(l.status)};
  const shown = all.filter(groups[CUS.filter] || groups.all);
  const tile = (label, num, sub) => `<div class="card tile"><span class="label">${label}</span><span class="big mono">${num}</span><span class="muted" style="font-size:13.5px">${sub}</span></div>`;
  const rows = shown.map((l) => {
    const [w, c] = CUS_STATUS[l.status] || [l.status, 'plain'];
    const today = cusSum(l.today), until = l.cancel_at || l.period_end;
    return `<tr data-lic="${esc(l.id)}" style="cursor:pointer" tabindex="0">
      <td><b style="font-weight:600">${esc(l.email || l.name || 'No address')}</b>${l.name && l.email ? `<br><span class="muted" style="font-size:13px">${esc(l.name)}</span>` : ''}
        <br><span class="muted mono" style="font-size:12px">…${esc(l.key_hint)} · ${l.source === 'stripe' ? 'Stripe' : 'by hand'}</span></td>
      <td>${esc(l.plan_name || l.plan)}<br><span class="muted" style="font-size:13px">${l.variant === 'ai' ? 'Botix AI' : 'own AI key'}</span></td>
      <td>${pill(c, w)}</td><td style="white-space:nowrap">${esc(cusDay(l.bought_at))}</td><td style="white-space:nowrap">${esc(cusDay(l.plan_since))}</td>
      <td style="white-space:nowrap">${esc(cusDay(until))}${l.cancel_at ? '<br><span class="muted" style="font-size:12px">then stops</span>' : ''}</td>
      <td>${(l.computers || []).length ? esc((l.computers || []).map((x) => x.name).join(', ')) + `<br><span class="muted" style="font-size:12px">seen ${esc(cusDay(Math.max(...l.computers.map((x) => x.seen || 0))))}</span>` : '<span class="muted">none yet</span>'}</td>
      <td class="mono" style="font-size:13px;white-space:nowrap">${['leads', 'mails', 'posts'].map((k) => `${k[0].toUpperCase()}${today[k] || 0}`).join(' · ')}</td></tr>`;
  }).join('');
  main.innerHTML = `<div class="page">${head('Customers', 'Who bought which plan, since when and until when. Stripe keeps it up to date by itself; plans sold by hand are made here.',
    `<span style="display:flex;gap:8px;flex-wrap:wrap"><button class="btn small" type="button" id="cusNew">New customer, paid by hand</button>
     <button class="btn ghost small" type="button" id="cusSet">Plans and payments</button></span>`)}
    <div id="cusUnlock"></div>
    <div class="tiles">${tile('Paying', n(groups.active), 'active plans')}${tile('On trial', n(groups.trial), 'not paying yet')}
      ${tile('Overdue', n(groups.overdue), 'a payment failed')}${tile('New this month', n((l) => l.bought_at > month), 'bought in 30 days')}</div>
    <div role="group" aria-label="Which customers" style="display:flex;gap:8px;flex-wrap:wrap">${[['all', 'All'], ['active', 'Paying'], ['trial', 'Trial'], ['overdue', 'Overdue'], ['ended', 'Cancelled or ended']].map(([k, t]) =>
      `<button type="button" class="seg ${CUS.filter === k ? 'on' : ''}" aria-pressed="${CUS.filter === k}" data-cf="${k}">${t}</button>`).join('')}</div>
    <section class="card scroll">${shown.length ? `<table><thead><tr><th>Customer</th><th>Plan</th><th>Status</th><th>Bought</th><th>Plan since</th><th>Paid until</th><th>Computer</th><th>Today</th></tr></thead><tbody>${rows}</tbody></table>`
      : empty('No customers here', all.length ? 'Nobody matches this filter.' : 'When someone buys a plan through Stripe, or you make one by hand, they appear here.')}</section>
    <p class="muted" style="font-size:13.5px;margin:0">Kept by your Home: each customer's email, plan and dates, their computers' random numbers, and counts of work done. Never the work itself — no lead, post or email reaches it. Today: L leads, M emails, P posts.</p></div>`;
  main.querySelectorAll('[data-cf]').forEach((b) => b.onclick = () => { CUS.filter = b.dataset.cf; drawCustomers(main); });
  main.querySelectorAll('[data-lic]').forEach((r) => { r.onclick = () => openCustomer(r.dataset.lic, main); r.onkeydown = (e) => { if (e.key === 'Enter') openCustomer(r.dataset.lic, main); }; });
  $('#cusNew').onclick = () => newCustomer(main, plans);
  $('#cusSet').onclick = () => cusSettings(main);
}

function cusPlanOptions(plans, pick) {
  return Object.entries(plans || {}).map(([id, p]) => `<option value="${esc(id)}" ${id === pick ? 'selected' : ''}>${esc(p.name || id)}</option>`).join('');
}
function showKey(key, box, line) {
  box.innerHTML = `<div class="note" style="display:flex;flex-direction:column;gap:8px"><b style="font-weight:600">${esc(line)}</b>
    <code class="mono" style="font-size:18px;letter-spacing:.05em;user-select:all">${esc(key)}</code>
    <span class="muted" style="font-size:13.5px">Shown once — your Home keeps only a fingerprint of it. Copy it now and pass it on.</span>
    <span><button class="btn small" type="button" id="keyCopy">Copy</button></span></div>`;
  $('#keyCopy').onclick = () => { navigator.clipboard && navigator.clipboard.writeText(key).then(() => toast('Copied.')); };
}

function openCustomer(id, main) {
  const l = ((CUS.data && CUS.data.licences) || []).find((x) => x.id === id); if (!l) return;
  const plans = (CUS.data && CUS.data.plans) || {}, [w, c] = CUS_STATUS[l.status] || [l.status, 'plain'];
  const period = cusSum(l.period), today = cusSum(l.today);
  const hist = (l.history || []).slice().reverse().map((h) => `<li><span class="mono" style="font-size:12.5px;color:var(--faint)">${esc(cusDay(h.at))}</span>
    <span>${esc(CUS_EVENTS[h.what] || h.what)}${h.plan ? ` <span class="muted">· ${esc((plans[h.plan] || {}).name || h.plan)}</span>` : ''}${h.note ? ` <span class="muted">· ${esc(h.note)}</span>` : ''}</span></li>`).join('');
  const ended = ['cancelled', 'expired'].includes(l.status);
  openPanel(`${panelHead(l.email || l.name || 'A customer', l.plan_name || l.plan)}<div class="dr-body">
    <section class="dr-sec"><div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap">${pill(c, w)}<span class="muted">${esc(l.plan_name || l.plan)} · ${l.variant === 'ai' ? 'Botix AI' : 'own AI key'} · key …${esc(l.key_hint)}</span></div>
      <div class="plan-grid" style="padding:4px 0"><div><span>Bought</span>${esc(cusDay(l.bought_at))}</div><div><span>On this plan since</span>${esc(cusDay(l.plan_since))}</div>
        <div><span>Paid until</span>${esc(cusDay(l.period_end))}</div><div><span>${l.cancel_at ? 'Stops on' : 'Cancelled'}</span>${esc(cusDay(l.cancel_at || l.cancelled_at))}</div></div>
      <span class="muted" style="font-size:13.5px">Today: ${today.leads || 0} leads, ${today.mails || 0} emails, ${today.posts || 0} posts · this period: ${period.checks || 0} Website Check reports · ${Number(l.websites || 0)} website(s) worked on lately · moved ${Number(l.moves || 0)} time(s) this month</span></section>
    <section class="dr-sec"><b style="font-weight:600">Computers</b>${(l.computers || []).length ? `<ul style="margin:0;padding-left:20px">${l.computers.map((x) => `<li>${esc(x.name)} <span class="muted">— since ${esc(cusDay(x.first))}, last seen ${esc(cusDay(x.seen))}</span></li>`).join('')}</ul>` : '<span class="muted">Not entered on any computer yet.</span>'}
      <label style="display:flex;gap:8px;align-items:center"><input type="checkbox" id="cuWeb"> and let them start again on different websites</label>
      <span><button class="btn ghost small" type="button" id="cuUnbind">Clear their computers</button></span></section>
    <section class="dr-sec"><b style="font-weight:600">Change the plan</b>
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center"><select id="cuPlan">${cusPlanOptions(plans, l.plan)}</select>
        <select id="cuVar"><option value="own" ${l.variant !== 'ai' ? 'selected' : ''}>own AI key</option><option value="ai" ${l.variant === 'ai' ? 'selected' : ''}>Botix AI</option></select>
        <button class="btn small" type="button" id="cuPlanGo">Change it</button></div>
      <span class="muted" style="font-size:13.5px">For a Stripe customer, change it in Stripe instead — Stripe tells your Home, and this follows.</span>
      <b style="font-weight:600">Add time</b>
      <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center"><input id="cuDays" type="number" min="1" max="3660" value="30" ${NUM.replace('width:100%', 'width:110px')}> days
        <button class="btn ghost small" type="button" id="cuExtend">Add them</button></div>
      <label for="cuNote" class="muted" style="font-size:13.5px">A note for the history (optional)</label><input id="cuNote" maxlength="200" placeholder="Paid by bank transfer, 16 Oct">
      <div style="display:flex;gap:8px;flex-wrap:wrap">${ended ? '<button class="btn small" type="button" id="cuOn">Start it again</button>' : '<button class="btn quiet small" type="button" id="cuOff">Cancel the plan now</button>'}
        <button class="btn quiet small" type="button" id="cuRekey">Make a new key</button></div>
      <div id="cuSay"></div></section>
    <section class="dr-sec"><b style="font-weight:600">History</b>${hist ? `<ol class="feed" style="padding:0">${hist}</ol>` : '<span class="muted">Nothing yet.</span>'}</section></div>`,
    'A customer', 'customer', id);
  const note = () => $('#cuNote').value.trim();
  const after = async (j, line) => {
    if (!j.ok) { $('#cuSay').innerHTML = `<div class="note">${esc(j.error || 'Not changed.')}</div>`; return; }
    toast(line); const fresh = await cusApi({do: 'list'}); if (fresh.ok) CUS.data = fresh;
    drawCustomers(main); openCustomer(id, main);
  };
  $('#cuPlanGo').onclick = async () => after(await cusApi({do: 'change', id, change: {plan: $('#cuPlan').value, variant: $('#cuVar').value, note: note()}}, $('#cuSay')), 'Plan changed. Their copy follows at its next check.');
  $('#cuExtend').onclick = async () => after(await cusApi({do: 'change', id, change: {extend_days: Number($('#cuDays').value || 30), note: note()}}, $('#cuSay')), 'Time added.');
  const off = $('#cuOff'); if (off) off.onclick = async () => { if (!confirm('Cancel this plan now? Their copy stops at its next check (within a few hours, or straight away when a job asks).')) return;
    after(await cusApi({do: 'change', id, change: {status: 'cancelled', note: note()}}, $('#cuSay')), 'Cancelled.'); };
  const on = $('#cuOn'); if (on) on.onclick = async () => after(await cusApi({do: 'change', id, change: {status: 'active', note: note()}}, $('#cuSay')), 'Started again. Add time if their paid date has passed.');
  $('#cuUnbind').onclick = async () => { if (!confirm('Clear their computers? Their key then works on the next computer it is entered on.')) return;
    after(await cusApi({do: 'unbind', id, websites: $('#cuWeb').checked}, $('#cuSay')), 'Cleared.'); };
  $('#cuRekey').onclick = async () => {
    if (!confirm('Make a new key? The old one stops working at once, on every computer.')) return;
    const j = await cusApi({do: 'rekey', id}, $('#cuSay'));
    if (!j.ok) { $('#cuSay').innerHTML = `<div class="note">${esc(j.error || 'Not made.')}</div>`; return; }
    showKey(j.key, $('#cuSay'), 'Their new key');
    const fresh = await cusApi({do: 'list'}); if (fresh.ok) { CUS.data = fresh; drawCustomers(main); }
  };
}

function newCustomer(main, plans) {
  openPanel(`${panelHead('New customer', 'paid by hand')}<div class="dr-body"><section class="dr-sec">
    <p class="muted" style="margin:0;font-size:14px">For a plan paid outside Stripe — a bank transfer, cash, a friend. A key is made and shown to you once.</p>
    <label for="ncMail" style="font-weight:600">Their email</label><input id="ncMail" type="email" maxlength="200">
    <label for="ncName" style="font-weight:600">Their name or company <span class="muted" style="font-weight:400">(optional)</span></label><input id="ncName" maxlength="80">
    <label for="ncPlan" style="font-weight:600">Plan</label><select id="ncPlan">${cusPlanOptions(plans, 'starter')}</select>
    <select id="ncVar" aria-label="Which AI"><option value="own">With their own AI key</option><option value="ai">With Botix AI</option></select>
    <label for="ncDays" style="font-weight:600">Paid for how many days</label><input id="ncDays" type="number" min="1" max="3660" value="30" ${NUM}>
    <label style="display:flex;gap:8px;align-items:center"><input type="checkbox" id="ncTrial"> It is a trial</label>
    <label for="ncNote" style="font-weight:600">A note for the history <span class="muted" style="font-weight:400">(optional)</span></label><input id="ncNote" maxlength="200">
    <span><button class="btn" type="button" id="ncGo">Make the key</button></span><div id="ncSay"></div></section></div>`, 'New customer', 'customer', 'new');
  $('#ncGo').onclick = async () => {
    $('#ncGo').disabled = true;
    const j = await cusApi({do: 'new', plan: $('#ncPlan').value, variant: $('#ncVar').value, email: $('#ncMail').value.trim(), name: $('#ncName').value.trim(),
      days: Number($('#ncDays').value || 30), trial: $('#ncTrial').checked, note: $('#ncNote').value.trim()}, $('#ncSay'));
    $('#ncGo').disabled = false;
    if (!j.ok) { $('#ncSay').innerHTML = `<div class="note">${esc(j.error || 'Not made.')}</div>`; return; }
    showKey(j.key, $('#ncSay'), 'Their key');
    const fresh = await cusApi({do: 'list'}); if (fresh.ok) { CUS.data = fresh; drawCustomers(main); }
  };
}

/* The plans (as on the website) and how payments reach the Home. */
function cusSettings(main) {
  const s = CUS.settings || {}, plans = (CUS.data && CUS.data.plans) || s.plans || {};
  const prices = Array.isArray(s.prices) ? {} : (s.prices || {});
  const planRows = Object.entries(plans).map(([id, p]) => `<fieldset class="card" data-plan="${esc(id)}" style="padding:12px 14px;display:flex;flex-direction:column;gap:8px;margin:0">
      <legend class="mono" style="font-size:12px;color:var(--faint)">${esc(id)}</legend>
      <label ${FIELD}>Name <input data-f="name" maxlength="40" value="${esc(p.name || id)}"></label>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px 12px;align-items:end">
        <label ${FIELD}>Websites <input data-f="websites" type="number" min="1" max="50" value="${Number(p.websites || 1)}" ${NUM}></label>
        <label ${FIELD}>Words in a post, at most <input data-f="post_words" type="number" min="0" max="10000" value="${Number(p.post_words || 0)}" ${NUM}></label>
        <label ${FIELD}>Computers at once <input data-f="devices" type="number" min="1" max="20" value="${Number(p.devices || 1)}" ${NUM}></label>
        ${CUS_KINDS.map(([k, t]) => `<label ${FIELD}>${t} <input data-k="${k}" type="number" min="0" max="100000" value="${Number(((p.limits || {})[k] || {}).n || 0)}" ${NUM}></label>`).join('')}</div>
      <label style="display:flex;gap:8px;align-items:center"><input type="checkbox" data-f="own_logo" ${(p.features || []).includes('own_logo') ? 'checked' : ''}> Reports with their own logo</label></fieldset>`).join('');
  const priceRows = Object.entries(prices).concat([['', {plan: '', variant: 'own'}]]).map(([pid, v]) => `<div data-price style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
      <input data-p="id" placeholder="price_…" value="${esc(pid)}" style="max-width:260px" class="mono"><select data-p="plan"><option value="">—</option>${cusPlanOptions(plans, v.plan)}</select>
      <select data-p="variant"><option value="own" ${v.variant !== 'ai' ? 'selected' : ''}>own AI key</option><option value="ai" ${v.variant === 'ai' ? 'selected' : ''}>Botix AI</option></select></div>`).join('');
  openPanel(`${panelHead('Plans and payments', 'kept by your Home')}<div class="dr-body">
    <section class="dr-sec"><b style="font-weight:600">The plans</b><span class="muted" style="font-size:13.5px">The limits each plan keeps to, the same whichever AI the customer uses. Leads, emails and posts are counted for each website, each day; Website Check reports for the account, each paid month. 0 means not in that plan.</span>${planRows}</section>
    <section class="dr-sec"><b style="font-weight:600">Stripe</b>
      <span class="muted" style="font-size:13.5px">In Stripe → Developers → Webhooks, add this address and choose the events checkout.session.completed, customer.subscription.created, customer.subscription.updated, customer.subscription.deleted and invoice.payment_failed:</span>
      <code class="mono" style="user-select:all;font-size:13px;word-break:break-all">${esc(s.webhook_url || '')}</code>
      <span class="muted" style="font-size:13.5px">On each Payment Link (After payment → Don't show confirmation page → redirect), use this address, so the buyer sees their key straight away:</span>
      <code class="mono" style="user-select:all;font-size:13px;word-break:break-all">${esc(s.welcome_url || '')}</code>
      <label for="csSecret" style="font-weight:600">Webhook signing secret <span class="muted" style="font-weight:400">${s.has_stripe_secret ? '(saved — type a new one only to replace it)' : '(starts whsec_)'}</span></label><input id="csSecret" type="password" autocomplete="off">
      <b style="font-weight:600">Which Stripe price is which plan</b>${priceRows}</section>
    <section class="dr-sec"><b style="font-weight:600">When a customer cancels</b>
      <label style="display:flex;gap:8px;align-items:center"><input type="radio" name="csCancel" value="now" ${s.cancel_effect !== 'period_end' ? 'checked' : ''}> Stop at once</label>
      <label style="display:flex;gap:8px;align-items:center"><input type="radio" name="csCancel" value="period_end" ${s.cancel_effect === 'period_end' ? 'checked' : ''}> Carry on until the end of the month they paid for</label>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px 12px;align-items:end">
        <label ${FIELD}>Days after a failed payment <input id="csGrace" type="number" min="0" max="60" value="${Number(s.grace_days ?? 3)}" ${NUM}></label>
        <label ${FIELD}>Computer moves a month <input id="csMoves" type="number" min="0" max="60" value="${Number(s.moves_per_month ?? 3)}" ${NUM}></label>
        <label ${FIELD}>Days a copy works offline <input id="csOffline" type="number" min="0" max="60" value="${Number(s.offline_days ?? 3)}" ${NUM}></label></div>
      <label for="csPricing" style="font-weight:600">Your plans page</label><input id="csPricing" placeholder="https://www.yourprogram.com/botix.html#pricing" value="${esc(s.pricing_url || '')}">
      <label for="csPortal" style="font-weight:600">Stripe's customer portal link <span class="muted" style="font-weight:400">(where a customer renews, upgrades or cancels)</span></label><input id="csPortal" placeholder="https://billing.stripe.com/p/login/…" value="${esc(s.portal_url || '')}">
      <label for="csFrom" style="font-weight:600">Address the key goes out from <span class="muted" style="font-weight:400">(optional; your hosting must allow it)</span></label><input id="csFrom" type="email" placeholder="keys@yourprogram.com" value="${esc(s.mail_from || '')}">
      <span><button class="btn" type="button" id="csSave">Save</button></span><div id="csSay"></div></section></div>`, 'Plans and payments', 'customer', 'settings');
  $('#csSave').onclick = async () => {
    const outPlans = {};
    document.querySelectorAll('[data-plan]').forEach((f) => {
      const g = (k) => f.querySelector(`[data-f="${k}"]`);
      const limits = {};
      CUS_KINDS.forEach(([k]) => { limits[k] = {n: Number(f.querySelector(`[data-k="${k}"]`).value || 0), per: k === 'checks' ? 'period' : 'day', scope: k === 'checks' ? 'account' : 'site'}; });
      outPlans[f.dataset.plan] = {name: g('name').value.trim(), websites: Number(g('websites').value || 1), post_words: Number(g('post_words').value || 0),
        devices: Number(g('devices').value || 1), features: g('own_logo').checked ? ['own_logo'] : [], limits};
    });
    const outPrices = {};
    document.querySelectorAll('[data-price]').forEach((r) => { const id = r.querySelector('[data-p="id"]').value.trim(), plan = r.querySelector('[data-p="plan"]').value;
      if (id && plan) outPrices[id] = {plan, variant: r.querySelector('[data-p="variant"]').value}; });
    const changes = {plans: outPlans, prices: outPrices, cancel_effect: (document.querySelector('[name="csCancel"]:checked') || {}).value || 'now',
      grace_days: Number($('#csGrace').value), moves_per_month: Number($('#csMoves').value), offline_days: Number($('#csOffline').value),
      pricing_url: $('#csPricing').value.trim(), portal_url: $('#csPortal').value.trim(), mail_from: $('#csFrom').value.trim()};
    if ($('#csSecret').value.trim()) changes.stripe_secret = $('#csSecret').value.trim();
    const j = await cusApi({do: 'settings', changes}, $('#csSay'));
    if (!j.ok) { $('#csSay').innerHTML = `<div class="note">${esc(j.error || 'Not saved.')}</div>`; return; }
    CUS.settings = j; if (CUS.data) CUS.data.plans = j.plans;
    toast('Saved. Customers\' copies follow at their next check.'); closePanel(); drawCustomers(main);
  };
}

/* The room hooks: a screen, a menu entry, a line on More, a Settings card. */
(function joinTheApp() {
  ROUTES.reports = reports;
  ROUTES.customers = customers;
  NO_REDRAW.add('reports');
  NO_REDRAW.add('customers');
  const help = document.querySelector('nav a[data-r="help"]');
  if (help && !$('#navReports')) {
    help.insertAdjacentHTML('afterend', '<a href="#reports" data-r="reports" id="navReports">Reports <span class="count" id="navRep" hidden title="new reports">0</span></a>'
      + '<a href="#customers" data-r="customers" id="navCustomers">Customers</a>');
  }
  EXTRA.more.push((item) => item('#reports', 'Reports', 'Problem reports from the people using your program'));
  EXTRA.more.push((item) => item('#customers', 'Customers', 'Who bought which plan, and until when'));
  EXTRA.settings.push(publisherCard);
  publisherNav();
  if (['#reports', '#customers'].includes(location.hash) || VIEW.r === 'settings' || VIEW.r === 'more') route();
})();
