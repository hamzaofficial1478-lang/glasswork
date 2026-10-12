/* scene.js — the objects on the stage, in real 3D (three.js r128).

   TD3D.mount(element, 'botix')    the BT tile, with its 21 desks circling it
   TD3D.mount(element, 'techduo')  the TechDuo mark: two bars, one of them lit

   The BT mark is built from the same numbers as the program's own logo
   (coordination/mark.py), so the 3D tile is the real logo, not a copy of it.
   If the browser has no WebGL, the flat picture inside the element stays. The
   scene pauses when it is off the screen, and with reduced motion it is drawn
   once and left still. */
(function () {
  'use strict';
  var EMBER = 0xE8834F;

  function webgl() {
    try {
      var c = document.createElement('canvas');
      return !!(window.WebGLRenderingContext && (c.getContext('webgl') || c.getContext('experimental-webgl')));
    } catch (e) { return false; }
  }

  /* ---- shapes, in the mark's units (y is flipped: up is up in 3D) ---- */
  var T = 14, HU = 28, HL = 30, CXU = 42, CXL = 46, RU = T + HU / 2, RL = T + HL / 2;

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

  /* ---- materials and light ----
     Colours are written as on the page (sRGB) and turned into light values
     here, so the orange on the stage is the same orange as the buttons. */
  function lin(hex) { return new THREE.Color(hex).convertSRGBToLinear(); }
  function materials() {
    var M = {
      tile: new THREE.MeshPhysicalMaterial({ color: 0x1C1F27, metalness: 0.3, roughness: 0.42, clearcoat: 0.7, clearcoatRoughness: 0.28 }),
      ember: new THREE.MeshStandardMaterial({ color: EMBER, metalness: 0.2, roughness: 0.34, emissive: 0x4A1E0B, emissiveIntensity: 1 }),
      steel: new THREE.MeshPhysicalMaterial({ color: 0xD3D8E1, metalness: 0.35, roughness: 0.3, clearcoat: 0.5, clearcoatRoughness: 0.2 }),
      desk: new THREE.MeshStandardMaterial({ color: 0x2A2F3A, metalness: 0.25, roughness: 0.5 })
    };
    Object.keys(M).forEach(function (k) {
      M[k].color.convertSRGBToLinear();
      if (M[k].emissive) M[k].emissive.convertSRGBToLinear();
    });
    return M;
  }
  function lights(scene) {
    scene.add(new THREE.HemisphereLight(lin(0xDFE6F2), lin(0x0B0D12), 0.9));
    var key = new THREE.DirectionalLight(0xFFFFFF, 1.6); key.position.set(4, 6, 8); scene.add(key);
    var warm = new THREE.DirectionalLight(lin(0xF2AE86), 0.7); warm.position.set(-6, 2, 4); scene.add(warm);
    var rim = new THREE.PointLight(lin(EMBER), 2.2, 30); rim.position.set(0, -1.2, -4); scene.add(rim);
  }

  /* ---- the Botix tile and its desks ---- */
  function botix(M) {
    var outer = new THREE.Group(), inner = new THREE.Group();
    var slab = new THREE.ExtrudeGeometry(roundRect(new THREE.Shape(), 0, 0, 240, 240, 46),
      { depth: 26, bevelEnabled: true, bevelThickness: 6, bevelSize: 5, bevelSegments: 6, curveSegments: 28 });
    inner.add(new THREE.Mesh(slab, M.tile));
    var ringShape = roundRect(new THREE.Shape(), 1, -1, 238, 238, 45);
    ringShape.holes.push(roundRect(new THREE.Path(), 5, -5, 230, 230, 41));
    var ring = new THREE.Mesh(new THREE.ExtrudeGeometry(ringShape, { depth: 2.5, bevelEnabled: false, curveSegments: 28 }), M.ember);
    ring.position.z = 32;
    inner.add(ring);
    var SC = 0.85, letters = new THREE.Group();
    letterShapes().forEach(function (s) {
      letters.add(new THREE.Mesh(new THREE.ExtrudeGeometry(s, { depth: 9, bevelEnabled: true, bevelThickness: 1.6,
        bevelSize: 1.1, bevelSegments: 3, curveSegments: 24 }), M.ember));
    });
    letters.scale.set(SC, SC, 1);
    letters.position.set((240 - 152 * SC) / 2, -(240 - 100 * SC) / 2, 31);
    inner.add(letters);
    inner.position.set(-120, 120, -19);
    outer.add(inner);
    outer.scale.setScalar(1 / 80);

    // 21 desks — seven sectors of three — circling the tile
    var ringG = new THREE.Group(), desks = [], glow = lin(EMBER);
    var box = new THREE.BoxGeometry(0.16, 0.16, 0.16);
    for (var i = 0; i < 21; i++) {
      var m = M.desk.clone();
      var d = new THREE.Mesh(box, m);
      var a = (i / 21) * Math.PI * 2;
      d.position.set(Math.cos(a) * 2.45, 0, Math.sin(a) * 2.45);
      d.rotation.set(a, a * 0.5, 0);
      d.userData.phase = (i * 2.399) % (Math.PI * 2);
      ringG.add(d); desks.push(d);
    }
    ringG.rotation.x = 0.38; ringG.rotation.z = -0.12;
    var group = new THREE.Group();
    group.add(outer, ringG);
    return {
      group: group,
      tick: function (t, still) {
        ringG.rotation.y = still ? 0.6 : t * 0.12;
        desks.forEach(function (d) {
          var on = Math.max(0, Math.sin(t * 0.9 + d.userData.phase));
          d.material.emissive.copy(glow);
          d.material.emissiveIntensity = still ? (d.userData.phase > 4 ? 0.6 : 0) : on * on * 0.9;
        });
      }
    };
  }

  /* ---- the TechDuo mark: two bars ---- */
  function techduo(M) {
    function bar(x) {
      var s = new THREE.Shape();
      s.moveTo(x + 14, 26); s.lineTo(x + 24, 26); s.lineTo(x + 10, -26); s.lineTo(x, -26); s.lineTo(x + 14, 26);
      return new THREE.ExtrudeGeometry(s, { depth: 6, bevelEnabled: true, bevelThickness: 1.8, bevelSize: 1.4, bevelSegments: 5 });
    }
    var g = new THREE.Group();
    var a = new THREE.Mesh(bar(-22), M.steel);
    var b = new THREE.Mesh(bar(-2), M.ember); b.position.z = 4;
    g.add(a, b);
    g.scale.setScalar(3 / 52);

    // a floor of points fading into the dark
    var pts = [], n = 0;
    for (var x = -7; x <= 7; x += 0.35) for (var z = -7; z <= 3; z += 0.35) { pts.push(x, -2.1, z); n++; }
    var geo = new THREE.BufferGeometry();
    geo.setAttribute('position', new THREE.Float32BufferAttribute(pts, 3));
    var floor = new THREE.Points(geo, new THREE.PointsMaterial({ color: lin(0x4A5262), size: 0.035, sizeAttenuation: true }));
    var group = new THREE.Group();
    group.add(g, floor);
    return {
      group: group,
      tick: function (t, still) { floor.position.z = still ? 0 : (t * 0.25) % 0.35; }
    };
  }

  function mount(el, kind) {
    if (!el || !window.THREE || !webgl()) return;
    var still = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
    var renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.outputEncoding = THREE.sRGBEncoding;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.08;
    el.appendChild(renderer.domElement);

    var scene = new THREE.Scene();
    scene.fog = new THREE.Fog(lin(0x0A0C10), 7, 17);
    var camera = new THREE.PerspectiveCamera(30, 1, 0.1, 100);
    camera.position.set(0, 0.25, 10.5);
    lights(scene);
    var M = materials();
    var obj = kind === 'techduo' ? techduo(M) : botix(M);
    scene.add(obj.group);

    var aim = { x: 0, y: 0 }, now = { x: 0, y: 0 };
    window.addEventListener('pointermove', function (e) {
      aim.x = (e.clientX / window.innerWidth) * 2 - 1;
      aim.y = (e.clientY / window.innerHeight) * 2 - 1;
    }, { passive: true });

    function size() {
      var w = el.clientWidth || 400, h = el.clientHeight || 400;
      renderer.setSize(w, h, false);
      camera.aspect = w / h; camera.updateProjectionMatrix();
    }
    size();
    if (window.ResizeObserver) new ResizeObserver(size).observe(el); else window.addEventListener('resize', size);

    var visible = true, t0 = performance.now(), first = true;
    function frame(ts) {
      var t = (ts - t0) / 1000;
      now.x += (aim.x - now.x) * 0.05; now.y += (aim.y - now.y) * 0.05;
      var base = kind === 'techduo' ? 0.35 : -0.45;
      obj.group.rotation.y = still ? base : base + Math.sin(t * 0.45) * 0.28 + now.x * 0.35;
      obj.group.rotation.x = still ? 0.12 : 0.1 + Math.sin(t * 0.35) * 0.06 + now.y * 0.18;
      obj.group.position.y = still ? 0 : Math.sin(t * 0.8) * 0.07;
      obj.tick(t, still);
      renderer.render(scene, camera);
      if (first) { first = false; el.classList.add('live'); }
      if (!still && visible) requestAnimationFrame(frame);
    }
    if ('IntersectionObserver' in window && !still) {
      new IntersectionObserver(function (es) {
        var was = visible; visible = es[0].isIntersecting;
        if (visible && !was) requestAnimationFrame(frame);
      }).observe(el);
    }
    requestAnimationFrame(frame);
  }

  window.TD3D = { mount: mount };
  function boot() {
    document.querySelectorAll('[data-scene]').forEach(function (el) { mount(el, el.getAttribute('data-scene')); });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot); else boot();
})();
