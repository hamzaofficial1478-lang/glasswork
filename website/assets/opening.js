/* opening.js — what plays when Botix opens.

   "when someone will open the program ... a white background and animations
   playing on it and at front the logo and that words appears BOTIX BY TECH DUO"

   A white screen. Round the edges, the day's work is drawn in, in the order it
   happens: your website, leads found, emails that go out when you say yes, a
   post written, results. In the middle, the BT tile arrives and the name is
   set beneath it. About three and a half seconds, once per visit (a new tab or
   opening the installed app is a new visit), and any tap or key skips it. With
   reduced motion asked for, the name simply shows for a moment.

   The TechDuo website plays the same file inside a frame (BotixOpening.play),
   loaded with data-embed-only so it does not take over the whole page.

   Nothing is fetched except the logo, which the page already has. */
(function () {
  var KEY = 'botix-opened';
  var me = document.currentScript;
  var still = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;

  var css = [
    '.bxo{background:#fff;color:#111318;display:grid;place-items:center;overflow:hidden;',
    "font-family:'Geist',system-ui,-apple-system,'Segoe UI',sans-serif;cursor:pointer;transition:opacity .5s ease}",
    '.bxo.full{position:fixed;inset:0;z-index:2147483000}',
    '.bxo.embed{position:absolute;inset:0;container-type:size;cursor:default}',
    '.bxo.out{opacity:0;pointer-events:none}',
    '.bxo svg.scene{position:absolute;inset:0;width:100%;height:100%}',
    '.bxo .ln{fill:none;stroke:#D3D8E0;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}',
    '.bxo .or{fill:none;stroke:#E8834F;stroke-width:2.5;stroke-linecap:round;stroke-linejoin:round}',
    '.bxo .card{fill:#fff;stroke:#E1E5EB;stroke-width:2}',
    '.bxo .soft{fill:#FBEADF}',
    '.bxo .grey{fill:#ECEFF3}',
    '.bxo .lab{font-size:15px;fill:#7A8291;letter-spacing:.02em}',
    '.bxo .draw{stroke-dasharray:var(--len);stroke-dashoffset:var(--len);animation:bxDraw var(--dur,.8s) ease-out var(--at,0s) forwards}',
    '.bxo .pop{opacity:0;transform-box:fill-box;transform-origin:center;animation:bxPop .45s cubic-bezier(.2,.9,.3,1.3) var(--at,0s) forwards}',
    '.bxo .fade{opacity:0;animation:bxFade .5s ease-out var(--at,0s) forwards}',
    '.bxo .grow{transform-box:fill-box;transform-origin:left center;transform:scaleX(0);animation:bxGrow .35s ease-out var(--at,0s) forwards}',
    '.bxo .fly{offset-rotate:0deg;offset-distance:0%;opacity:0;animation:bxFly 1s ease-in-out var(--at,0s) forwards}',
    '.bxo .run{offset-rotate:0deg;animation:bxRun 3.2s linear .2s forwards}',
    '.bxo .front{position:relative;display:flex;flex-direction:column;align-items:center;gap:14px;text-align:center}',
    '.bxo .tile{width:clamp(84px,14vmin,128px);height:auto;border-radius:22%;box-shadow:0 18px 40px rgba(17,19,24,.16);',
    'opacity:0;transform:scale(.6);animation:bxTile .7s cubic-bezier(.2,.9,.25,1.25) .35s forwards}',
    '.bxo .name{font-size:clamp(30px,6vmin,52px);font-weight:600;letter-spacing:.32em;margin:6px 0 0 .3em;display:flex}',
    '.bxo .name span{opacity:0;transform:translateY(10px);animation:bxUp .45s ease-out forwards}',
    '.bxo .by{font-size:clamp(11px,1.7vmin,14px);font-weight:600;letter-spacing:.34em;color:#7A8291;margin-left:.34em;opacity:0;animation:bxFade .5s ease-out 1.45s forwards}',
    '.bxo .rule{width:64px;height:3px;border-radius:3px;background:#E8834F;transform:scaleX(0);animation:bxGrow .5s ease-out 1.6s forwards}',
    '.bxo .skip{position:absolute;right:16px;bottom:calc(14px + env(safe-area-inset-bottom,0px));border:0;background:none;',
    'color:#7A8291;font:inherit;font-size:13px;padding:8px 10px;cursor:pointer}',
    '.bxo .skip:focus-visible{outline:2px solid #B4501E;outline-offset:2px;border-radius:6px}',
    '@media (max-width:640px){.bxo .lab{display:none}}',
    '@media (max-aspect-ratio:4/5){.bxo.full{place-items:start center}.bxo.full .front{margin-top:19vh}.bxo.full svg.scene{top:auto;bottom:5vh;height:44vh}}',
    '.bxo.embed .tile{width:clamp(44px,17cqmin,116px)}.bxo.embed .name{font-size:clamp(18px,8cqmin,46px)}.bxo.embed .by{font-size:clamp(8px,2.4cqmin,13px)}.bxo.embed .front{gap:clamp(4px,2cqmin,14px)}',
    '@keyframes bxDraw{to{stroke-dashoffset:0}}',
    '@keyframes bxPop{0%{opacity:0;transform:scale(.6)}100%{opacity:1;transform:scale(1)}}',
    '@keyframes bxFade{to{opacity:1}}',
    '@keyframes bxGrow{to{transform:scaleX(1)}}',
    '@keyframes bxUp{to{opacity:1;transform:none}}',
    '@keyframes bxTile{to{opacity:1;transform:none}}',
    '@keyframes bxFly{0%{opacity:0;offset-distance:0%}15%{opacity:1}85%{opacity:1}100%{opacity:0;offset-distance:100%}}',
    '@keyframes bxRun{from{offset-distance:0%}to{offset-distance:100%}}',
    '@media (prefers-reduced-motion:reduce){.bxo *{animation:none!important;opacity:1!important;transform:none!important;stroke-dashoffset:0!important}',
    '.bxo .fly,.bxo .run{display:none}}'
  ].join('');

  // One loose loop round the middle: the work moving from desk to desk.
  var LOOP = 'M600 120 C860 120 1010 230 1010 380 C1010 540 860 650 600 650 C340 650 190 540 190 380 C190 230 340 120 600 120';
  var TRAIL = 'M560 166 C620 128 690 128 745 162';

  function at(s) { return 'style="--at:' + s + 's"'; }
  function line(d, s, len, cls) { return '<path class="' + (cls || 'ln') + ' draw" d="' + d + '" style="--len:' + len + ';--at:' + s + 's"/>'; }

  var scene = [
    '<svg class="scene" viewBox="0 0 1200 760" preserveAspectRatio="xMidYMid meet" aria-hidden="true">',
    // the loop, and a dot travelling round it
    '<path class="ln draw" d="' + LOOP + '" style="--len:2000;--dur:1.4s;--at:.05s;stroke-dasharray:6 10;stroke:#ECEFF3"/>',
    '<circle r="5" fill="#E8834F" class="run" style="offset-path:path(\'' + LOOP + '\')"/>',

    // 1. your website (left)
    '<g ' + at(0.2) + ' class="pop"><rect class="card" x="120" y="285" width="200" height="140" rx="12"/></g>',
    line('M120 312 H320', 0.5, 200),
    '<g class="fade" ' + at(0.55) + '><circle cx="138" cy="299" r="4" class="grey"/><circle cx="152" cy="299" r="4" class="grey"/><circle cx="166" cy="299" r="4" class="grey"/></g>',
    '<rect class="soft grow" x="140" y="328" width="110" height="16" rx="4" ' + at(0.65) + '/>',
    '<rect class="grey grow" x="140" y="354" width="160" height="8" rx="4" ' + at(0.75) + '/>',
    '<rect class="grey grow" x="140" y="370" width="140" height="8" rx="4" ' + at(0.82) + '/>',
    '<rect class="grey grow" x="140" y="386" width="120" height="8" rx="4" ' + at(0.89) + '/>',
    '<text class="lab fade" x="220" y="455" text-anchor="middle" ' + at(0.8) + '>Your website</text>',

    // 2. leads found (top left)
    '<g class="pop" ' + at(0.6) + '><rect class="card" x="400" y="150" width="150" height="38" rx="10"/><circle cx="421" cy="169" r="9" class="soft"/><rect class="grey" x="438" y="160" width="80" height="7" rx="3.5"/><rect class="grey" x="438" y="173" width="52" height="7" rx="3.5"/></g>',
    '<g class="pop" ' + at(0.75) + '><rect class="card" x="380" y="198" width="150" height="38" rx="10"/><circle cx="401" cy="217" r="9" class="soft"/><rect class="grey" x="418" y="208" width="88" height="7" rx="3.5"/><rect class="grey" x="418" y="221" width="46" height="7" rx="3.5"/></g>',
    '<g class="pop" ' + at(0.9) + '><rect class="card" x="400" y="246" width="150" height="38" rx="10"/><circle cx="421" cy="265" r="9" class="soft"/><rect class="grey" x="438" y="256" width="70" height="7" rx="3.5"/><rect class="grey" x="438" y="269" width="58" height="7" rx="3.5"/></g>',
    '<text class="lab fade" x="465" y="132" text-anchor="middle" ' + at(1.0) + '>Leads found</text>',

    // 3. emails, sent when you say yes (top right)
    '<path class="ln draw" d="' + TRAIL + '" style="--len:300;--at:1s;stroke-dasharray:4 8"/>',
    '<g class="fly" style="offset-path:path(\'' + TRAIL + '\');--at:1.05s"><rect x="-17" y="-12" width="34" height="24" rx="4" fill="#fff" stroke="#E8834F" stroke-width="2"/><path d="M-15 -9 L0 3 L15 -9" fill="none" stroke="#E8834F" stroke-width="2"/></g>',
    '<g class="fly" style="offset-path:path(\'' + TRAIL + '\');--at:1.35s"><rect x="-17" y="-12" width="34" height="24" rx="4" fill="#fff" stroke="#F1B48F" stroke-width="2"/><path d="M-15 -9 L0 3 L15 -9" fill="none" stroke="#F1B48F" stroke-width="2"/></g>',
    '<g class="pop" ' + at(1.7) + '><rect class="card" x="745" y="150" width="62" height="44" rx="8"/><path class="or" d="M751 157 L776 176 L801 157"/></g>',
    '<text class="lab fade" x="776" y="132" text-anchor="middle" ' + at(1.3) + '>Emails, when you say yes</text>',

    // 4. a post written (right)
    '<g class="pop" ' + at(1.2) + '><rect class="card" x="890" y="270" width="160" height="190" rx="12"/></g>',
    '<rect class="soft grow" x="910" y="292" width="120" height="44" rx="6" ' + at(1.4) + '/>',
    '<rect class="grey grow" x="910" y="350" width="118" height="8" rx="4" ' + at(1.55) + '/>',
    '<rect class="grey grow" x="910" y="366" width="104" height="8" rx="4" ' + at(1.65) + '/>',
    '<rect class="grey grow" x="910" y="382" width="112" height="8" rx="4" ' + at(1.75) + '/>',
    '<rect class="grey grow" x="910" y="398" width="86" height="8" rx="4" ' + at(1.85) + '/>',
    '<rect class="grey grow" x="910" y="414" width="96" height="8" rx="4" ' + at(1.95) + '/>',
    '<text class="lab fade" x="970" y="492" text-anchor="middle" ' + at(1.6) + '>Posts written</text>',

    // 5. results (bottom left) and your yes (bottom right)
    '<g class="pop" ' + at(1.6) + '><rect class="card" x="300" y="545" width="170" height="100" rx="12"/></g>',
    line('M318 625 L355 606 L390 614 L425 584 L452 570', 1.8, 170, 'or'),
    '<circle cx="452" cy="570" r="5" fill="#E8834F" class="pop" ' + at(2.25) + '/>',
    '<text class="lab fade" x="385" y="675" text-anchor="middle" ' + at(2.0) + '>Results</text>',
    '<g class="pop" ' + at(1.9) + '><rect class="card" x="745" y="530" width="78" height="128" rx="14"/><rect class="grey" x="770" y="540" width="28" height="5" rx="2.5"/></g>',
    '<circle cx="784" cy="596" r="20" class="soft pop" ' + at(2.1) + '/>',
    line('M774 596 L781 603 L795 588', 2.25, 40, 'or'),
    '<text class="lab fade" x="784" y="688" text-anchor="middle" ' + at(2.2) + '>Nothing goes out without your yes</text>',
    '</svg>'
  ].join('');

  var word = 'BOTIX'.split('').map(function (c, i) {
    return '<span style="animation-delay:' + (0.85 + i * 0.08).toFixed(2) + 's">' + c + '</span>';
  }).join('');

  function styles() {
    if (document.getElementById('bxo-style')) return;
    var st = document.createElement('style'); st.id = 'bxo-style'; st.textContent = css; document.head.appendChild(st);
  }
  function front(logo) {
    return '<div class="front"><img class="tile" src="' + logo + '" alt="" width="128" height="128">'
      + '<div class="name" aria-label="Botix">' + word + '</div><div class="by">BY TECH DUO</div><div class="rule"></div></div>';
  }

  /* Inside a frame (the TechDuo website): BotixOpening.play(element, {logo}).
     Calling it again plays it again from the start. */
  function play(box, opts) {
    styles();
    box.innerHTML = '<div class="bxo embed" role="img" aria-label="Botix opening: the day\'s work is drawn in, then BOTIX BY TECH DUO">'
      + scene + front((opts && opts.logo) || '/logo.svg') + '</div>';
  }

  /* The whole screen, when the program opens: once per visit. */
  function opening() {
    try { if (sessionStorage.getItem(KEY)) return; sessionStorage.setItem(KEY, '1'); } catch (e) { /* private window: still play it */ }
    styles();
    var o = document.createElement('div');
    o.className = 'bxo full'; o.setAttribute('role', 'presentation');
    o.innerHTML = scene + front('/logo.svg') + '<button class="skip" type="button">Skip</button>';
    document.body.appendChild(o);
    var done = false;
    function close() {
      if (done) return; done = true;
      o.classList.add('out');
      setTimeout(function () { o.remove(); }, 520);
      document.removeEventListener('keydown', close);
    }
    o.addEventListener('click', close);
    document.addEventListener('keydown', close);
    setTimeout(close, still ? 1200 : 3600);
  }

  window.BotixOpening = { play: play };
  if (me && me.hasAttribute('data-embed-only')) return;
  if (document.body) opening(); else document.addEventListener('DOMContentLoaded', opening);
})();
