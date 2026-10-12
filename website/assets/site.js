/* site.js — the small things the pages do.

   ── FILL THESE IN ────────────────────────────────────────────────────────
   botix:  the address where Botix runs online (where people make an
           account), for example 'https://app.yourdomain.com'. While it is
           empty, the "Start your 14-day trial" buttons lead to Contact.

   email:  the address people write to, for example 'hello@yourdomain.com'.
           While it is empty, the contact section says it is coming soon.
   ──────────────────────────────────────────────────────────────────────── */
var SITE = {
  botix: '',
  email: ''
};

(function () {
  'use strict';
  var still = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
  var $$ = function (q, el) { return Array.prototype.slice.call((el || document).querySelectorAll(q)); };

  // The address of Botix online, and the contact address
  $$('[data-trial]').forEach(function (a) {
    if (SITE.botix) { a.href = SITE.botix.replace(/\/$/, '') + '/account'; a.rel = 'noopener'; }
    else a.href = (a.getAttribute('data-page') === 'home' ? '' : 'index.html') + '#contact';
  });
  $$('[data-botix-privacy]').forEach(function (a) { if (SITE.botix) a.href = SITE.botix.replace(/\/$/, '') + '/privacy'; });
  $$('[data-contact]').forEach(function (box) {
    if (!SITE.email) { box.innerHTML = '<p>Our address goes up here very soon.</p>'; return; }
    box.innerHTML = '<code id="mailText"></code>'
      + '<button class="btn line small" type="button" id="mailCopy">Copy</button>'
      + '<a class="btn dark small" id="mailGo">Write to us</a>';
    document.getElementById('mailText').textContent = SITE.email;
    document.getElementById('mailGo').href = 'mailto:' + SITE.email;
    var b = document.getElementById('mailCopy');
    b.addEventListener('click', function () {
      var done = function () { b.textContent = 'Copied'; setTimeout(function () { b.textContent = 'Copy'; }, 1600); };
      if (navigator.clipboard) navigator.clipboard.writeText(SITE.email).then(done, pick);
      else pick();
    });
    function pick() {
      var r = document.createRange(); r.selectNodeContents(document.getElementById('mailText'));
      var s = getSelection(); s.removeAllRanges(); s.addRange(r);
    }
  });

  // The top bar: a line under it once the page moves, and the menu on a phone
  var nav = document.querySelector('.nav');
  var mb = document.querySelector('.menu-btn');
  if (nav && mb) {
    mb.addEventListener('click', function () {
      var open = nav.classList.toggle('open');
      mb.setAttribute('aria-expanded', String(open));
    });
    $$('.nav nav a').forEach(function (a) { a.addEventListener('click', function () { nav.classList.remove('open'); }); });
  }

  // A screen that tips up into place as it comes up the page
  var tips = $$('.rise3d');
  var ticking = false;
  function onScroll() {
    ticking = false;
    if (nav) nav.classList.toggle('edge', scrollY > 4);
    tips.forEach(function (el) {
      var r = el.getBoundingClientRect();
      var tip = Math.max(0, Math.min(1, (r.top - innerHeight * 0.18) / (innerHeight * 0.62)));
      el.style.setProperty('--tip', still ? 0 : tip.toFixed(3));
    });
  }
  window.addEventListener('scroll', function () { if (!ticking) { ticking = true; requestAnimationFrame(onScroll); } }, { passive: true });
  onScroll();

  // The opening animation, the one the program plays, in its window
  var screen = document.querySelector('[data-opening]');
  if (screen && window.BotixOpening) {
    var logo = screen.getAttribute('data-logo') || 'assets/botix-logo.svg';
    var cover = document.querySelector('[data-play]');
    var play = function (asked) {
      if (cover) cover.hidden = true;
      window.BotixOpening.play(screen, { logo: logo, asked: asked });
    };
    if (cover) cover.addEventListener('click', function () { play(true); });
    $$('[data-replay]').forEach(function (b) { b.addEventListener('click', function () { play(true); }); });
    $$('[data-full]').forEach(function (b) { b.addEventListener('click', function () { window.BotixOpening.full({ logo: logo }); }); });
    if (still) {                                              // with reduced motion: the last frame, and a Play button
      window.BotixOpening.play(screen, { logo: logo, asked: false });
      if (cover) { screen.appendChild(cover); cover.hidden = false; }
    }
    else if ('IntersectionObserver' in window) {
      var seen = false;
      new IntersectionObserver(function (es) { if (es[0].isIntersecting && !seen) { seen = true; play(false); } }, { threshold: 0.45 }).observe(screen);
    } else play(false);
  }

  // The floor of sectors: desks light up in turn, as on a working day
  var lit = $$('.iso .desks i');
  if (lit.length && !still) {
    var k = 0;
    setInterval(function () {
      lit.forEach(function (i) { i.classList.remove('lit'); });
      $$('.iso .blk').forEach(function (b) { b.classList.remove('on'); });
      for (var j = 0; j < 4; j++) {
        var el = lit[(k + j * 5) % lit.length];
        el.classList.add('lit');
        el.closest('.blk').classList.add('on');
      }
      k = (k + 1) % lit.length;
    }, 1400);
  }

  var y = document.getElementById('year');
  if (y) y.textContent = String(new Date().getFullYear());
})();
