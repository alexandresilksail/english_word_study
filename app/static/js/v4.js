/* ==========================================================================
   English Learning Platform · V4 interactions
   - 问候语（按本地时间） / 今日日期
   - [data-count] 数字滚动
   - .reveal 滚动显现
   - 纯增强脚本：不触碰任何业务逻辑（判分/学习/账号流程）
   ========================================================================== */
(function () {
  'use strict';
  var $ = function (s, r) { return (r || document).querySelector(s); };

  /* ---------- 问候语 + 日期 ---------- */
  (function () {
    var el = $('[data-greet]');
    if (el) {
      var h = new Date().getHours();
      var en = h < 5 ? 'Good night' : h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening';
      var zh = h < 5 ? '晚上好' : h < 12 ? '早上好' : h < 18 ? '下午好' : '晚上好';
      el.textContent = en;
      el.title = zh;
    }
    var d = $('#today-date');
    if (d) {
      var now = new Date();
      var days = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
      var mon = String(now.getMonth() + 1).padStart(2, '0');
      var day = String(now.getDate()).padStart(2, '0');
      d.textContent = now.getFullYear() + '-' + mon + '-' + day + ' · ' + days[now.getDay()];
    }
  })();

  /* ---------- 数字滚动（data-count） ---------- */
  (function () {
    var els = document.querySelectorAll('[data-count]');
    if (!els.length) return;
    var fmt = function (n) { return n.toLocaleString('en-US'); };
    var run = function (node) {
      var target = parseFloat(node.getAttribute('data-count')) || 0;
      if (node.dataset.done) return;
      node.dataset.done = '1';
      var dur = 900, t0 = null;
      var step = function (ts) {
        if (!t0) t0 = ts;
        var p = Math.min((ts - t0) / dur, 1);
        var eased = 1 - Math.pow(1 - p, 3);
        node.textContent = fmt(Math.round(target * eased)) + (node.dataset.suffix || '');
        if (p < 1) requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    };
    if ('IntersectionObserver' in window) {
      var io = new IntersectionObserver(function (entries) {
        entries.forEach(function (e) { if (e.isIntersecting) { run(e.target); io.unobserve(e.target); } });
      }, { threshold: 0.4 });
      els.forEach(function (n) { io.observe(n); });
    } else {
      els.forEach(run);
    }
  })();

  /* ---------- .reveal 滚动显现 ---------- */
  (function () {
    var els = document.querySelectorAll('.reveal');
    if (!els.length) return;
    if (!('IntersectionObserver' in window)) { els.forEach(function (n) { n.classList.add('in'); }); return; }
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) { e.target.classList.add('in'); io.unobserve(e.target); }
      });
    }, { threshold: 0.15 });
    els.forEach(function (n) { io.observe(n); });
  })();
})();
