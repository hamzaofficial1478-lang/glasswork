/* scene.js — the products as real objects on the page (three.js r128).

   TD3D.mount(element, 'botix')    the BT tile, with its 21 desks circling it
   TD3D.mount(element, 'techduo')  the TechDuo mark: a graphite bar and an orange one

   The BT tile is built from the same numbers as the program's own logo
   (coordination/mark.py), so the 3D tile is the real logo, not a copy of it.

   What it does: it arrives with a turn, then sways gently; it turns as the
   page scrolls past it; and anyone can drag it round with a finger or the
   mouse. Off the screen it stops drawing. With reduced motion asked for it
   arrives still and only moves when dragged. If the browser has no WebGL,
   the flat picture inside the element stays. */
(function () {
  'use strict';
  var ORANGE = 0xE8834F, GRAPHITE = 0x1B1E25;

  function webgl() {
    try {
      var c = document.createElement('canvas');
      return !!(window.WebGLRenderingContext && (c.getContext('webgl') || c.getContext('experimental-webgl')));
    } catch (e) { return false; }
  }
  function lin(hex) { return new THREE.Color(hex).convertSRGBToLinear(); }
  function ease(x) { x = Math.min(1, Math.max(0, x)); return 1 - Math.pow(1 - x, 3); }

  /* ---- shapes, in the mark's units (y flipped: up is up in 3D) ---- */
  var T = 14, HU = 28, HL = 30, CXU = 42, CXL = 46, RU = T + HU / 2;
  function dOutline(p, x0, top, bottom, cx) {
    var r = (bottom - top) / 2, cy = -(top + r);
    p.moveTo(x0, -top); p.lineTo(cx, -top);
    p.absarc(cx, cy, r, Math.PI / 2, -Math.PI / 2, true);
    p.lineTo(x0, -bottom); p.lineTo(x0, -top);
    return p;
  }
  function rect(x0, y0, x1, y1) {
    var s = new THREE.Shape();
    s.moveTo(x0, -y0); s.lineTo(x1, -y0); s.lineTo(x1, -y1); s.lineTo(x0, -y1); s.lineTo(x0, -y0);
    return s;
  }
  function letterShapes() {
    var up = dOutline(new THREE.Shape(), 0, 0, 2 * RU, CXU);
    up.holes.push(dOutline(new THREE.Path(), T, T, T + HU, CXU));
    var lo = dOutline(new THREE.Shape(), 0, T + HU, 100, CXL);
    lo.holes.push(dOutline(new THREE.Path(), T, 2 * T + HU, 100 - T, CXL));
    return [up, lo, rect(0, 0, 152, T), rect(111, T, 125, 100)];   // one bar across both letters, then the T's stem
  }
  function roundRect(p, x, y, w, h, r) {
    p.moveTo(x + r, y);
    p.lineTo(x + w - r, y); p.absarc(x + w - r, y - r, r, Math.PI / 2, 0, true);
    p.lineTo(x + w, y - h + r); p.absarc(x + w - r, y - h + r, r, 0, -Math.PI / 2, true);
    p.lineTo(x + r, y - h); p.absarc(x + r, y - h + r, r, -Math.PI / 2, -Math.PI, true);
    p.lineTo(x, y - r); p.absarc(x + r, y - r, r, Math.PI, Math.PI / 2, true);
    return p;
  }

  /* ---- a soft studio to reflect, and the light ---- */
  function studio(renderer) {
    var env = new THREE.Scene();
    env.background = lin(0xE9ECF0);
    var white = new THREE.MeshBasicMaterial({ color: 0xFFFFFF, side: THREE.DoubleSide });
    [[0, 6, 2, 10, 4], [-7, 2, 3, 3, 8], [7, 1, 4, 3, 8]].forEach(function (b) {
      var m = new THREE.Mesh(new THREE.PlaneGeometry(b[3], b[4]), white);
      m.position.set(b[0], b[1], b[2]); m.lookAt(0, 0, 0); env.add(m);
    });
    var pm = new THREE.PMREMGenerator(renderer);
    var tex = pm.fromScene(env, 0.03).texture;
    pm.dispose();
    return tex;
  }
  function lights(scene) {
    scene.add(new THREE.HemisphereLight(0xFFFFFF, lin(0xC9CFD8), 0.45));
    var key = new THREE.DirectionalLight(0xFFFFFF, 1.0); key.position.set(3, 6, 7); scene.add(key);
    var fill = new THREE.DirectionalLight(0xFFFFFF, 0.25); fill.position.set(-6, 1, 3); scene.add(fill);
  }
  function materials() {
    var M = {
      tile: new THREE.MeshPhysicalMaterial({ color: GRAPHITE, metalness: 0.1, roughness: 0.38, clearcoat: 0.8, clearcoatRoughness: 0.25, envMapIntensity: 0.35 }),
      orange: new THREE.MeshStandardMaterial({ color: ORANGE, metalness: 0.0, roughness: 0.42, envMapIntensity: 0.15 }),
      graphite: new THREE.MeshPhysicalMaterial({ color: 0x2A2E37, metalness: 0.1, roughness: 0.36, clearcoat: 0.7, clearcoatRoughness: 0.25, envMapIntensity: 0.35 }),
      desk: new THREE.MeshStandardMaterial({ color: 0xBCC3CD, metalness: 0.0, roughness: 0.45, envMapIntensity: 0.3 })
    };
    Object.keys(M).forEach(function (k) { M[k].color.convertSRGBToLinear(); });
    return M;
  }
  // a soft round shadow on the floor under the object
  function shadow(y, size) {
    var c = document.createElement('canvas'); c.width = c.height = 128;
    var g = c.getContext('2d'), grd = g.createRadialGradient(64, 64, 4, 64, 64, 62);
    grd.addColorStop(0, 'rgba(14,17,22,0.34)'); grd.addColorStop(1, 'rgba(14,17,22,0)');
    g.fillStyle = grd; g.fillRect(0, 0, 128, 128);
    var m = new THREE.Mesh(new THREE.PlaneGeometry(size, size * 0.42),
      new THREE.MeshBasicMaterial({ map: new THREE.CanvasTexture(c), transparent: true, depthWrite: false }));
    m.rotation.x = -Math.PI / 2; m.position.y = y;
    return m;
  }

  /* ---- the Botix tile and its desks ---- */
  function botix(M) {
    var inner = new THREE.Group();
    var slab = new THREE.ExtrudeGeometry(roundRect(new THREE.Shape(), 0, 0, 240, 240, 46),
      { depth: 26, bevelEnabled: true, bevelThickness: 7, bevelSize: 6, bevelSegments: 8, curveSegments: 32 });
    inner.add(new THREE.Mesh(slab, M.tile));
    var ringShape = roundRect(new THREE.Shape(), 1, -1, 238, 238, 45);
    ringShape.holes.push(roundRect(new THREE.Path(), 5, -5, 230, 230, 41));
    var ring = new THREE.Mesh(new THREE.ExtrudeGeometry(ringShape, { depth: 2.5, bevelEnabled: false, curveSegments: 32 }), M.orange);
    ring.position.z = 33;
    var back = ring.clone(); back.position.z = -9.5;          // the same ring on the back, so it reads from every side
    inner.add(ring, back);
    var SC = 0.85, letters = new THREE.Group();
    letterShapes().forEach(function (s) {
      letters.add(new THREE.Mesh(new THREE.ExtrudeGeometry(s, { depth: 10, bevelEnabled: true, bevelThickness: 1.8,
        bevelSize: 1.2, bevelSegments: 4, curveSegments: 24 }), M.orange));
    });
    letters.scale.set(SC, SC, 1);
    letters.position.set((240 - 152 * SC) / 2, -(240 - 100 * SC) / 2, 32);
    inner.add(letters);
    inner.position.set(-120, 120, -19);
    var tile = new THREE.Group(); tile.add(inner); tile.scale.setScalar(1 / 80);

    // 21 desks, seven sectors of three, circling the tile
    var orbit = new THREE.Group(), desks = [], glow = lin(ORANGE);
    var box = new THREE.BoxGeometry(0.17, 0.17, 0.17);
    for (var i = 0; i < 21; i++) {
      var d = new THREE.Mesh(box, M.desk.clone());
      var a = (i / 21) * Math.PI * 2;
      d.position.set(Math.cos(a) * 2.5, 0, Math.sin(a) * 2.5);
      d.rotation.set(a, a * 0.5, 0);
      d.userData.phase = (i * 2.399) % (Math.PI * 2);
      d.material.emissive.copy(glow);
      orbit.add(d); desks.push(d);
    }
    orbit.rotation.x = 0.32; orbit.rotation.z = -0.1;
    var group = new THREE.Group(); group.add(tile, orbit);
    return {
      group: group, floor: -2.05, shadowSize: 5,
      tick: function (t, moving) {
        orbit.rotation.y = 0.6 + (moving ? t * 0.14 : 0);
        desks.forEach(function (d) {
          var on = Math.max(0, Math.sin(t * 0.9 + d.userData.phase));
          d.material.emissiveIntensity = moving ? on * on * 0.85 : (d.userData.phase > 4 ? 0.7 : 0);
        });
      }
    };
  }

  /* ---- the TechDuo mark: two slanted bars ---- */
  function techduo(M) {
    function bar(x) {
      var s = new THREE.Shape();
      s.moveTo(x + 14, 26); s.lineTo(x + 24, 26); s.lineTo(x + 10, -26); s.lineTo(x, -26); s.lineTo(x + 14, 26);
      return new THREE.ExtrudeGeometry(s, { depth: 7, bevelEnabled: true, bevelThickness: 2, bevelSize: 1.5, bevelSegments: 6 });
    }
    var a = new THREE.Mesh(bar(-22), M.graphite);
    var b = new THREE.Mesh(bar(-2), M.orange);
    var g = new THREE.Group(); g.add(a, b); g.scale.setScalar(3.2 / 52);

    // a floor of fine dots, fading into the page
    var pts = [];
    for (var x = -7; x <= 7; x += 0.4) for (var z = -6; z <= 3; z += 0.4) pts.push(x, -2.1, z);
    var geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3));
    var dots = new THREE.Points(geo, new THREE.PointsMaterial({ color: lin(0xC4CAD3), size: 0.035, sizeAttenuation: true }));
    var group = new THREE.Group(); group.add(g, dots);
    return {
      group: group, floor: -2.08, shadowSize: 4, bars: [a, b], dots: dots,
      tick: function (t, moving, k) {
        dots.position.z = moving ? (t * 0.25) % 0.4 : 0;
        a.position.x = (1 - k) * -40; b.position.x = (1 - k) * 40;     // the two bars slide together as it arrives
      }
    };
  }

  function mount(el, kind) {
    if (!el || !window.THREE || !webgl()) return;
    var still = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
    var renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.outputEncoding = THREE.sRGBEncoding;
    renderer.toneMapping = THREE.NoToneMapping;      // colours as on the page: the orange stays the BT orange
    el.appendChild(renderer.domElement);

    var scene = new THREE.Scene();
    scene.environment = studio(renderer);
    var camera = new THREE.PerspectiveCamera(30, 1, 0.1, 100);
    camera.position.set(0, 0.7, 10.5); camera.lookAt(0, -0.1, 0);
    lights(scene);
    var obj = kind === 'techduo' ? techduo(materials()) : botix(materials());
    var spin = new THREE.Group(); spin.add(obj.group);
    var floorShadow = shadow(obj.floor, obj.shadowSize);
    scene.add(spin, floorShadow);
    if (obj.dots) { obj.group.remove(obj.dots); scene.add(obj.dots); }     // the floor stays put while the mark turns

    // drag to turn it, with a little weight
    var drag = 0, vel = 0, tiltX = 0, down = null;
    el.addEventListener('pointerdown', function (e) { down = { x: e.clientX, y: e.clientY }; vel = 0; kick(); });
    window.addEventListener('pointermove', function (e) {
      if (!down) return;
      var dx = e.clientX - down.x, dy = e.clientY - down.y;
      down = { x: e.clientX, y: e.clientY };
      vel = dx * 0.012; drag += vel; tiltX = Math.max(-0.5, Math.min(0.5, tiltX + dy * 0.004));
      kick();
    }, { passive: true });
    window.addEventListener('pointerup', function () { down = null; kick(); });
    window.addEventListener('pointercancel', function () { down = null; });

    // how far the page has scrolled past it: -1 below the middle of the screen, +1 above
    function scrolled() {
      var r = el.getBoundingClientRect(), mid = r.top + r.height / 2;
      return Math.max(-1, Math.min(1, (innerHeight / 2 - mid) / innerHeight));
    }
    window.addEventListener('scroll', function () { if (still) kick(); }, { passive: true });

    function size() {
      var w = el.clientWidth || 400, h = el.clientHeight || 400;
      renderer.setSize(w, h, false);
      camera.aspect = w / h;
      camera.position.z = w / h < 0.9 ? 12.5 : 10.5;        // narrow boxes step back so nothing is cut off
      camera.updateProjectionMatrix();
      kick();
    }

    var visible = true, t0 = null, running = false, first = true;
    var base = kind === 'techduo' ? 0.42 : -0.38;
    function frame(ts) {
      running = false;
      if (t0 === null) t0 = ts;
      var t = (ts - t0) / 1000, k = still ? 1 : ease(t / 1.9);
      if (!down) { drag += vel; vel *= 0.94; if (Math.abs(vel) < 0.0004) vel = 0; }
      if (!down && !vel) tiltX *= 0.92;
      var sway = still ? 0 : Math.sin(t * 0.5) * 0.4, p = scrolled();
      spin.rotation.y = base + sway + drag + p * 0.9 - (1 - k) * 2.8;
      spin.rotation.x = 0.06 + tiltX + p * 0.18 + (still ? 0 : Math.sin(t * 0.37) * 0.05);
      spin.position.y = (still ? 0 : Math.sin(t * 0.8) * 0.08) - (1 - k) * 0.6;
      var sc = 0.72 + 0.28 * k; spin.scale.setScalar(sc);
      floorShadow.material.opacity = 0.35 + 0.65 * k;
      obj.tick(t, !still, k);
      renderer.render(scene, camera);
      if (first) { first = false; el.classList.add('live'); }
      if (visible && (!still || down || vel || Math.abs(tiltX) > 0.002)) kick();
    }
    function kick() { if (!running) { running = true; requestAnimationFrame(frame); } }

    size();
    if (window.ResizeObserver) new ResizeObserver(size).observe(el); else window.addEventListener('resize', size);
    if ('IntersectionObserver' in window) {
      new IntersectionObserver(function (es) { visible = es[0].isIntersecting; if (visible) kick(); }).observe(el);
    }
    kick();
  }

  window.TD3D = { mount: mount };
  function boot() {
    document.querySelectorAll('[data-scene]').forEach(function (el) { mount(el, el.getAttribute('data-scene')); });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
