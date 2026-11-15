/*
 * KiraAI 新手开始页。
 * 1. 开场“点亮数字生命灵魂”：一条心电线画过屏幕并收成一个光点，光点跳两下，
 *    四周的光点飞来连成星座，KiraAI 图标在中间亮起。
 * 2. 星座收拢成三个光标：心智、语言、感知。它们用 cursor-swarm.js 把引导页逐块搭出来。
 * 3. 按钮通过 window.KiraOnboarding（原生 WebView 注入）通知应用；在普通浏览器里打开时只是关闭动画。
 * 用户开了“减少动态效果”时，两段动画都不播放，直接显示内容。
 */
(function () {
  'use strict';

  var html = document.documentElement;
  var bridge = window.KiraOnboarding || null;
  var reduce = window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
  var params = new URLSearchParams(location.search);
  var speed = Math.max(.25, Math.min(4, parseFloat(params.get('speed')) || 1)); // 预览出图用，应用内始终为 1
  var swarm = null;
  var finished = false;

  var ROSE = [184, 68, 132], LILAC = [118, 82, 163], PEACH = [236, 123, 141];
  var AGENTS = [
    { name: '心智', color: '#b84484', role: 'layout' },
    { name: '语言', color: '#7652a3', role: 'type' },
    { name: '感知', color: '#e0697f', role: 'media' }
  ];

  function call(method) {
    try { if (bridge && typeof bridge[method] === 'function') bridge[method](); }
    catch (error) { /* 原生端已关闭时忽略 */ }
  }

  /* ---------- 按钮 ---------- */
  document.getElementById('begin').addEventListener('click', begin);
  document.getElementById('battery').addEventListener('click', function () { call('battery'); });
  document.getElementById('skip').addEventListener('click', skipAll);

  /* ---------- 开场画布 ---------- */
  var canvas = document.getElementById('life');
  var ctx = canvas.getContext('2d');
  var W = 0, H = 0, CX = 0, CY = 0, R = 0, dpr = 1;
  var motes = [];
  var running = true;
  var started = 0;

  function resize() {
    dpr = Math.min(3, window.devicePixelRatio || 1);
    W = innerWidth; H = innerHeight;
    canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    CX = W / 2; CY = H * .42; R = Math.min(W, H) * .29;
    html.style.setProperty('--ring', Math.round(R * 1.2) + 'px'); // 标语放在星座外圈之下
  }

  function seedMotes() {
    motes = [];
    var count = 30;
    for (var i = 0; i < count; i++) {
      var angle = -Math.PI / 2 + i / count * Math.PI * 2;
      var ring = R * (i % 3 === 0 ? 1.18 : i % 3 === 1 ? 1 : .84);
      var side = i % 4;
      var t = (i * 37 % 100) / 100;
      var from = [[-30, H * t], [W + 30, H * t], [W * t, -30], [W * t, H + 30]][side];
      motes.push({
        sx: from[0], sy: from[1],
        angle: angle + ((i * 53) % 7 - 3) * .03,
        ring: ring,
        size: 1.8 + (i * 29 % 10) / 6,
        color: i % 3 === 0 ? ROSE : i % 3 === 1 ? LILAC : PEACH,
        agent: i % 3,
        delay: (i * 17 % 30) / 30 * .45
      });
    }
  }

  function clamp(value) { return value < 0 ? 0 : value > 1 ? 1 : value; }
  function span(t, a, b) { return clamp((t - a) / (b - a)); }
  function easeOut(x) { return 1 - Math.pow(1 - x, 3); }
  function easeInOut(x) { return x < .5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2; }
  function rgba(c, a) { return 'rgba(' + c[0] + ',' + c[1] + ',' + c[2] + ',' + a + ')'; }

  /* 心电线的形状：平线，在中心附近起一个 P-QRS-T 波。x 为 -1..1。 */
  function ecg(x) {
    var y = 0;
    y += -.08 * Math.exp(-Math.pow((x + .2) / .035, 2));
    y += .1 * Math.exp(-Math.pow((x + .045) / .012, 2));
    y += -1 * Math.exp(-Math.pow(x / .014, 2));
    y += .32 * Math.exp(-Math.pow((x - .04) / .016, 2));
    y += -.14 * Math.exp(-Math.pow((x - .22) / .05, 2));
    return y;
  }

  function agentPoint(index) {
    return { x: CX + (index - 1) * 58, y: CY + R * .05 };
  }

  var STAGE = { line: [0, .75], retract: [.75, 1.1], beat1: 1.15, beat2: 1.47, gather: [1.6, 2.7], mark: 2.15, hold: 3.15, leave: [3.15, 3.75] };

  function frame(now) {
    if (!running) return;
    if (!started) started = now;
    var t = (now - started) / 1000 * speed;
    ctx.clearRect(0, 0, W, H);

    var amp = Math.min(70, H * .09);
    /* 1. 心电线从左画到右。 */
    var drawn = easeInOut(span(t, STAGE.line[0], STAGE.line[1]));
    var retract = easeInOut(span(t, STAGE.retract[0], STAGE.retract[1]));
    if (drawn > 0 && retract < 1) {
      var left = -1 + retract, right = Math.min(-1 + drawn * 2, 1 - retract);
      ctx.beginPath();
      for (var x = left; x <= right; x += .004) {
        var px = CX + x * W * .5, py = CY + ecg(x) * amp * (1 - retract);
        if (x === left) ctx.moveTo(px, py); else ctx.lineTo(px, py);
      }
      ctx.lineWidth = 2; ctx.lineJoin = 'round'; ctx.lineCap = 'round';
      ctx.strokeStyle = rgba(ROSE, .85);
      ctx.stroke();
      if (drawn < 1) { /* 笔尖 */
        var tipX = -1 + drawn * 2;
        ctx.beginPath(); ctx.arc(CX + tipX * W * .5, CY + ecg(tipX) * amp, 3, 0, Math.PI * 2);
        ctx.fillStyle = rgba(ROSE, 1); ctx.fill();
      }
    }

    /* 2. 光点与两次心跳。 */
    var core = span(t, STAGE.retract[0] + .2, STAGE.retract[1]);
    var leave = easeInOut(span(t, STAGE.leave[0], STAGE.leave[1]));
    if (core > 0 && leave < 1) {
      var pulse = 0;
      [STAGE.beat1, STAGE.beat2].forEach(function (beat, i) {
        var p = span(t, beat, beat + .5);
        if (p > 0 && p < 1) {
          pulse = Math.max(pulse, Math.sin(p * Math.PI) * (i ? .6 : 1));
          ctx.beginPath(); ctx.arc(CX, CY, 8 + easeOut(p) * R * 1.05, 0, Math.PI * 2);
          ctx.lineWidth = 1.5; ctx.strokeStyle = rgba(ROSE, .38 * (1 - p)); ctx.stroke();
        }
      });
      var radius = (3 + core * 3) * (1 + pulse * .9) * (1 - leave);
      ctx.beginPath(); ctx.arc(CX, CY, radius * 3.2, 0, Math.PI * 2);
      ctx.fillStyle = rgba(ROSE, .1 * core * (1 - leave)); ctx.fill();
      ctx.beginPath(); ctx.arc(CX, CY, radius, 0, Math.PI * 2);
      ctx.fillStyle = rgba(ROSE, core * (1 - leave)); ctx.fill();
    }

    /* 3. 四周光点飞来，连成星座；最后收拢成三个光标的位置。 */
    var spin = t * .12;
    var points = motes.map(function (m) {
      var g = easeOut(span(t, STAGE.gather[0] + m.delay, STAGE.gather[1] + m.delay * .4));
      var breathe = 1 + Math.sin(t * 2.2 + m.angle * 3) * .025;
      var tx = CX + Math.cos(m.angle + spin) * m.ring * breathe;
      var ty = CY + Math.sin(m.angle + spin) * m.ring * breathe;
      var x = m.sx + (tx - m.sx) * g, y = m.sy + (ty - m.sy) * g;
      if (leave > 0) {
        var home = agentPoint(m.agent);
        x += (home.x - x) * leave; y += (home.y - y) * leave;
      }
      return { x: x, y: y, g: g, m: m };
    });
    var lines = span(t, STAGE.gather[0] + .4, STAGE.gather[1] + .2) * (1 - leave);
    if (lines > 0) {
      ctx.lineWidth = 1;
      for (var i = 0; i < points.length; i++) {
        var a = points[i], b = points[(i + 1) % points.length], c = points[(i + 7) % points.length];
        ctx.strokeStyle = rgba(a.m.color, .22 * lines);
        ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke();
        if (i % 3 === 0) {
          ctx.strokeStyle = rgba(a.m.color, .1 * lines);
          ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(c.x, c.y); ctx.stroke();
        }
      }
    }
    points.forEach(function (p) {
      if (p.g <= 0) return;
      var size = p.m.size * (1 - leave * .4);
      ctx.beginPath(); ctx.arc(p.x, p.y, size * 2.6, 0, Math.PI * 2);
      ctx.fillStyle = rgba(p.m.color, .12 * p.g); ctx.fill();
      ctx.beginPath(); ctx.arc(p.x, p.y, size, 0, Math.PI * 2);
      ctx.fillStyle = rgba(p.m.color, .9 * p.g); ctx.fill();
    });

    if (t >= STAGE.mark && !html.classList.contains('mark-on')) lightUp();
    if (t >= STAGE.hold && !html.classList.contains('intro-leaving')) leaveIntro();
    if (t >= STAGE.leave[1] + .15) { running = false; ctx.clearRect(0, 0, W, H); return; }
    requestAnimationFrame(frame);
  }

  /* 图标亮起，标语逐字点亮。 */
  function lightUp() {
    html.classList.add('mark-on');
    var slogan = document.getElementById('slogan');
    var text = slogan.textContent;
    slogan.textContent = '';
    Array.prototype.forEach.call(text, function (ch, i) {
      var s = document.createElement('span');
      s.textContent = ch;
      slogan.appendChild(s);
      setTimeout(function () { s.classList.add('lit', 'glow'); }, (120 + i * 75) / speed);
      setTimeout(function () { s.classList.remove('glow'); }, (520 + i * 75) / speed);
    });
  }

  /* 开场淡出的同一帧里启动光标群：引导内容先被它藏起，再逐块搭出来。 */
  function leaveIntro() {
    html.classList.remove('is-intro');
    startSwarm();
    html.classList.add('intro-leaving');
    setTimeout(function () { html.classList.add('intro-gone'); }, 700 / speed);
  }

  function startSwarm() {
    if (typeof CursorSwarm !== 'function') { ready(); return; }
    var starts = AGENTS.map(function (_, i) { return agentPoint(i); });
    swarm = new CursorSwarm({
      root: '#guide',
      agents: AGENTS,
      startPosition: starts,
      hud: false,
      deconstruct: false,
      exitAfterBuild: 'edge',
      idle: 'dim',
      speed: 1.7 * speed,
      maxSpeed: 5,
      trigger: 1,
      typeDuration: .9,
      copyPaste: true,
      relocation: .25,
      sparks: true,
      guides: true,
      zIndex: 2147483450
    });
    var pct = document.getElementById('pct');
    var last = -1, still = 0;
    /* 进度写在“跳过”按钮上。屏幕内的部分搭完、进度停住时，页面自己往下滚，光标接着搭下面的内容。 */
    var tick = function (now) {
      if (!swarm || finished) return;
      var value = swarm.progress || 0;
      pct.textContent = Math.round(value * 100) + '%';
      if (value !== last) { last = value; still = now; }
      else if (now - still > 650 / speed && value < 1
               && innerHeight + scrollY < document.documentElement.scrollHeight - 2) {
        still = now + 900 / speed;
        scrollBy({ top: innerHeight * .55, behavior: 'smooth' });
      }
      requestAnimationFrame(tick);
    };
    swarm.on('done', function () {
      setTimeout(ready, 500 / speed);
      /* 光标离场后把页面还原成原始结构：逐字拆开的文字合回整句，屏幕阅读器按句朗读。 */
      setTimeout(function () { if (swarm.state !== 'destroyed') swarm.destroy(); }, 1600 / speed);
    });
    swarm.init();
    if (swarm.state === 'stopped') { ready(); return; }
    html.classList.add('is-building');
    requestAnimationFrame(tick);
    /* 保险：任何原因卡住时，14 秒后直接显示全部内容。 */
    setTimeout(function () { if (!finished) skipAll(); }, 14000 / speed);
  }

  function ready() {
    if (finished) return;
    finished = true;
    document.getElementById('pct').textContent = '';
    html.classList.remove('is-building', 'is-intro');
    html.classList.add('is-ready', 'intro-gone');
    document.getElementById('begin').focus({ preventScroll: true });
  }

  function skipAll() {
    if (mainSwarm) { finishMain(); return; }
    running = false;
    if (swarm && swarm.state !== 'destroyed') swarm.destroy(); // 停下并原样还原页面
    html.classList.add('mark-on', 'intro-leaving');
    ready();
    scrollTo(0, 0);
  }

  /* ---------- 引导之后：同一批光标把主界面搭出来（只有第一次） ----------
   * 主界面是原生控件，光标搭不了。做法是：
   * 1. 原生端（KiraOnboarding.layout）把屏幕上可见的主界面各块的位置交给网页，并先把它们隐藏，网页底色变透明；
   * 2. 网页在相同位置放上透明占位块，由 cursor-swarm 去画框、点击；
   * 3. 每搭完一块，通知原生端（reveal）把那块真实控件淡入；搭完或跳过时（done）全部显示并关闭。
   * layout 返回空表示主界面已经搭过（例如从菜单重新打开引导），这时直接关闭。 */
  var mainSwarm = null, mainDone = false, began = false;

  function begin() {
    if (began) return;
    began = true;
    var button = document.getElementById('begin').getBoundingClientRect();
    var from = { x: button.left + button.width / 2, y: button.top + button.height / 2 };
    if (reduce || !bridge || typeof bridge.layout !== 'function' || typeof CursorSwarm !== 'function') { call('done'); return; }
    html.classList.add('guide-leaving');
    setTimeout(function () {
      var plan = null;
      try { plan = JSON.parse(bridge.layout() || 'null'); } catch (error) { plan = null; }
      if (!plan || !plan.items || !plan.items.length) { call('done'); return; }
      buildMain(plan, from);
    }, 320);
  }

  function buildMain(plan, from) {
    html.classList.add('main-build');
    scrollTo(0, 0);
    var layer = document.createElement('div');
    layer.id = 'main';
    plan.items.forEach(function (item) {
      var slot = document.createElement('div');
      slot.className = 'slot';
      slot.setAttribute('data-cs', item.type === 'frame' ? 'frame' : 'pop');
      slot.setAttribute('data-cs-label', item.label || '');
      slot.dataset.id = String(item.id);
      slot.style.left = item.x + 'px'; slot.style.top = item.y + 'px';
      slot.style.width = item.w + 'px'; slot.style.height = item.h + 'px';
      layer.appendChild(slot);
    });
    document.body.appendChild(layer);
    var spread = [-34, 0, 34];
    mainSwarm = new CursorSwarm({
      root: '#main',
      agents: AGENTS,
      startPosition: spread.map(function (dx) { return { x: from.x + dx, y: from.y }; }),
      hud: false, deconstruct: false, exitAfterBuild: 'edge', idle: 'dim',
      speed: 1.3 * speed, maxSpeed: 4, trigger: 1,
      copyPaste: false, relocation: false, sparks: true, guides: true,
      zIndex: 2147483450
    });
    mainSwarm.on('build', function (el) {
      try { if (el && el.dataset && el.dataset.id) bridge.reveal(el.dataset.id); } catch (error) { /* 已关闭 */ }
    });
    var pct = document.getElementById('pct');
    var tick = function () {
      if (mainDone) return;
      pct.textContent = Math.round((mainSwarm.progress || 0) * 100) + '%';
      requestAnimationFrame(tick);
    };
    mainSwarm.on('done', function () { setTimeout(finishMain, 900 / speed); });
    mainSwarm.init();
    if (mainSwarm.state === 'stopped') { finishMain(); return; }
    requestAnimationFrame(tick);
    setTimeout(finishMain, 12000 / speed); // 保险：卡住时 12 秒后全部显示
  }

  function finishMain() {
    if (mainDone) return;
    mainDone = true;
    if (mainSwarm && mainSwarm.state !== 'destroyed') mainSwarm.destroy();
    call('done'); // 原生端显示全部剩余控件并关闭
  }

  if (reduce) { ready(); return; }
  resize();
  seedMotes();
  addEventListener('resize', function () { resize(); seedMotes(); });
  requestAnimationFrame(frame);
})();
