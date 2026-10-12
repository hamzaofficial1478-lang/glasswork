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
      + '<button class="btn ghost small" type="button" id="mailCopy">Copy</button>'
      + '<a class="btn primary small" id="mailGo">Write to us</a>';
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

  // The menu on a phone
  var nav = document.querySelector('.nav');
  var mb = document.querySelector('.menu-btn');
  if (nav && mb) {
    mb.addEventListener('click', function () {
      var open = nav.classList.toggle('open');
      mb.setAttribute('aria-expanded', String(open));
    });
    $$('.nav nav a').forEach(function (a) { a.addEventListener('click', function () { nav.classList.remove('open'); }); });
  }

  // Sections ease up a little as they arrive (they are readable before that)
  if (!still && 'IntersectionObserver' in window) {
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); } });
    }, { rootMargin: '0px 0px -8% 0px' });
    $$('.rise').forEach(function (el) {
      if (el.getBoundingClientRect().top < innerHeight) el.classList.add('in'); else io.observe(el);
    });
  } else $$('.rise').forEach(function (el) { el.classList.add('in'); });

  // Cards that lean towards the pointer
  if (!still && window.matchMedia && matchMedia('(hover: hover)').matches) {
    $$('.tilt').forEach(function (el) {
      var max = parseFloat(el.getAttribute('data-tilt') || '6');
      el.addEventListener('pointermove', function (e) {
        var r = el.getBoundingClientRect();
        var x = (e.clientX - r.left) / r.width - 0.5, y = (e.clientY - r.top) / r.height - 0.5;
        el.style.transform = 'perspective(1200px) rotateX(' + (-y * max).toFixed(2) + 'deg) rotateY(' + (x * max).toFixed(2) + 'deg)';
      });
      el.addEventListener('pointerleave', function () { el.style.transform = ''; });
    });
  }

  // Pricing: with your own AI key, or with Botix AI
  $$('[data-price-switch] button').forEach(function (b) {
    b.addEventListener('click', function () {
      var mode = b.getAttribute('data-mode');
      $$('[data-price-switch] button').forEach(function (o) { o.setAttribute('aria-pressed', String(o === b)); });
      $$('[data-own]').forEach(function (el) { el.textContent = el.getAttribute(mode === 'ai' ? 'data-ai' : 'data-own'); });
      $$('[data-ai-note]').forEach(function (el) { el.hidden = mode !== 'ai'; });
      $$('[data-own-note]').forEach(function (el) { el.hidden = mode === 'ai'; });
    });
  });

  // The opening animation, played in its screen when it comes into view
  var tv = document.querySelector('[data-opening]');
  if (tv && window.BotixOpening) {
    var logo = tv.getAttribute('data-logo') || 'assets/botix-logo.svg';
    var played = false;
    var go = function () { window.BotixOpening.play(tv, { logo: logo }); played = true; };
    var again = document.querySelector('[data-replay]');
    if (again) again.addEventListener('click', go);
    if ('IntersectionObserver' in window) {
      new IntersectionObserver(function (es) { if (es[0].isIntersecting && !played) go(); }, { threshold: 0.4 }).observe(tv);
    } else go();
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
