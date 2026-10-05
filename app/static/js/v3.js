/* ==========================================================================
 * English Learning Platform · V3.0 前端交互
 * --------------------------------------------------------------------------
 * 架构原则（与后端严格一致）：
 *   - 判分、计分、XP 全部在后端 game_service / gamification 完成
 *   - 前端只负责「渲染题目 + 收集用户输入 + 提交 + 展示结果」
 *   - 前端**不保存正确答案**（答案只在服务端会话 state 里），因此游戏中只显示
 *     进度/用时，真正的对错与分数以服务端返回为准
 *   - 音频 URL 经后端 media_service 解析，前端只拿 audio_base + 文件名拼接
 * 依赖：base.html 提供 <meta name="csrf-token">
 * ========================================================================== */
(function () {
  'use strict';

  /* ----------------------------- 语言切换 ------------------------------ */
  var html = document.documentElement;
  try {
    var saved = localStorage.getItem('elp_lang');
    if (saved === 'zh' || saved === 'en' || saved === 'both') {
      html.setAttribute('data-lang', saved);
    }
  } catch (e) {}
  var toggle = document.getElementById('lang-toggle');
  if (toggle) {
    toggle.addEventListener('click', function () {
      var order = ['both', 'zh', 'en'];
      var cur = html.getAttribute('data-lang') || 'both';
      var next = order[(order.indexOf(cur) + 1) % order.length];
      html.setAttribute('data-lang', next);
      try { localStorage.setItem('elp_lang', next); } catch (e) {}
    });
  }

  /* ------------------------------- 工具 -------------------------------- */
  function $(sel, root) { return (root || document).querySelector(sel); }
  function el(tag, cls, txt) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (txt != null) n.textContent = txt;
    return n;
  }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); }

  var csrfMeta = document.querySelector('meta[name="csrf-token"]');
  var CSRF = csrfMeta ? csrfMeta.getAttribute('content') : '';

  function apiPost(url, data) {
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': CSRF },
      body: JSON.stringify(data || {}),
      credentials: 'same-origin'
    }).then(function (r) { return r.json(); });
  }

  function audioUrl(base, file) {
    if (!file) return '';
    if (/^https?:\/\//.test(file)) return file;
    return (base || '/static/audio').replace(/\/+$/, '') + '/' + file;
  }
  function fmtTime(sec) {
    sec = Math.max(0, Math.ceil(sec));
    var m = Math.floor(sec / 60), s = sec % 60;
    return m + ':' + (s < 10 ? '0' + s : s);
  }
  function toast(msg) {
    var t = document.getElementById('toast');
    if (!t) return;
    t.textContent = msg;
    t.classList.add('show');
    setTimeout(function () { t.classList.remove('show'); }, 2200);
  }
  function playAudio(url) {
    if (!url) return;
    var a = new Audio(url);
    a.play().catch(function () {});
  }

  /* ============================ 游戏主流程 ============================ */
  var GAME = window.GAME_KEY;
  if (GAME) initGame(GAME);

  function initGame(key) {
    var stage = document.getElementById('game-stage');
    var hud = document.getElementById('game-hud');
    if (!stage || !hud) return;

    hud.innerHTML =
      '<div class="hud-item"><b id="hud-prog">0/0</b><small>进度 Progress</small></div>' +
      '<div class="hud-item"><b id="hud-elapsed">0:00</b><small>用时 Time</small></div>' +
      '<div class="hud-item hud-timer" id="hud-timer"><b>--</b><small>剩余 Time left</small></div>';

    clear(stage);
    var card = el('div', 'q-card');
    card.style.maxWidth = '520px';
    card.style.margin = '0 auto';
    card.innerHTML =
      '<div style="font-size:46px;line-height:1;margin-bottom:10px">🎮</div>' +
      '<h2 style="margin:0 0 6px">准备好了吗？</h2>' +
      '<p class="muted" style="margin:0 0 18px">点击开始，系统会即时出题。</p>';
    var btn = el('button', 'btn btn-primary', '开始游戏 Start');
    btn.style.fontSize = '16px';
    btn.addEventListener('click', function () { startGame(key); });
    card.appendChild(btn);
    stage.appendChild(card);
  }

  var current = null;
  var timerId = null;
  var t0 = 0;

  function startGame(key) {
    var stage = document.getElementById('game-stage');
    clear(stage);
    stage.appendChild(el('div', 'muted', '出题中… Loading…'));
    apiPost('/games/api/' + key + '/start', {}).then(function (res) {
      if (!res || !res.ok) {
        clear(stage);
        stage.appendChild(el('div', 'muted', (res && res.error) || '开局失败'));
        return;
      }
      current = res.q;
      t0 = Date.now();
      if (key === 'word_match') renderWordMatch(res.q);
      else if (key === 'speed_quiz') renderSpeedQuiz(res.q);
      else if (key === 'listening_challenge') renderListening(res.q);
      else if (key === 'word_builder') renderWordBuilder(res.q);
    }).catch(function () {
      clear(stage);
      stage.appendChild(el('div', 'muted', '网络错误，请重试'));
    });
  }

  /* ------------------------------ 计时器 -------------------------------- */
  function startTimer(limitSec, onEnd) {
    if (timerId) { clearInterval(timerId); timerId = null; }
    var left = limitSec;
    var tEl = document.getElementById('hud-timer');
    var b = tEl ? tEl.querySelector('b') : null;
    if (b) b.textContent = fmtTime(left);
    if (tEl) tEl.classList.remove('warn');
    timerId = setInterval(function () {
      left -= 1;
      if (b) b.textContent = fmtTime(left);
      if (tEl && left <= 10) tEl.classList.add('warn');
      var e = document.getElementById('hud-elapsed');
      if (e) e.textContent = fmtTime((Date.now() - t0) / 1000);
      if (left <= 0) { clearInterval(timerId); timerId = null; if (onEnd) onEnd(); }
    }, 1000);
  }
  function stopTimer() { if (timerId) { clearInterval(timerId); timerId = null; } }
  function setProg(done, total) {
    var p = document.getElementById('hud-prog');
    if (p) p.textContent = done + '/' + total;
  }

  function makeResultActions() {
    var wrap = el('div', '');
    wrap.style.textAlign = 'center'; wrap.style.marginTop = '18px';
    var again = el('button', 'btn btn-primary', '再来一局 Play Again');
    again.addEventListener('click', function () { startGame(GAME); });
    var back = el('a', 'btn btn-secondary', '返回列表');
    back.href = '/games/';
    back.style.marginLeft = '10px';
    wrap.appendChild(again); wrap.appendChild(back);
    return wrap;
  }

  /* ----------------------------- 1) Word Match ------------------------- */
  function renderWordMatch(q) {
    var stage = document.getElementById('game-stage');
    clear(stage);
    setProg(0, q.left.length);
    startTimer(q.limit_sec || 90, function () { finishWordMatch(); });

    var pairs = [];          // 用户实际配对 [[i, j], ...]
    var matched = {};        // 已完成的格（避免重复）
    var picked = null;       // 当前选中的左块

    var leftCol = el('div', 'match-col');
    var rightCol = el('div', 'match-col');
    var grid = el('div', 'match-grid');
    grid.appendChild(leftCol); grid.appendChild(rightCol);

    q.left.forEach(function (L) {
      var t = el('div', 'match-tile', L.text);
      t.dataset.i = L.i;
      t.addEventListener('click', function () {
        if (matched['L' + L.i]) return;
        if (picked && picked !== t) picked.classList.remove('sel');
        if (picked === t) { picked = null; t.classList.remove('sel'); return; }
        picked = t; t.classList.add('sel');
      });
      leftCol.appendChild(t);
    });
    q.right.forEach(function (R) {
      var t = el('div', 'match-tile', R.text);
      t.dataset.j = R.j;
      t.addEventListener('click', function () {
        if (!picked) { toast('先选一个英文单词'); return; }
        if (matched['R' + R.j] != null) return;
        var i = parseInt(picked.dataset.i, 10), j = R.j;
        picked.classList.add('done'); t.classList.add('done');
        matched['L' + i] = true; matched['R' + j] = true;
        pairs.push([i, j]);
        picked.classList.remove('sel'); picked = null;
        setProg(pairs.length, q.left.length);
        if (pairs.length === q.left.length) finishWordMatch(pairs);
      });
      rightCol.appendChild(t);
    });

    var hint = el('p', 'muted', '点击左侧单词，再点击右侧释义完成配对。Tap a word, then its meaning.');
    hint.style.textAlign = 'center';
    stage.appendChild(hint);
    stage.appendChild(grid);
  }
  function finishWordMatch(pairs) {
    stopTimer();
    submitGame('word_match', { pairs: pairs || [] });
  }

  /* ----------------------------- 2) Speed Quiz ------------------------- */
  function renderSpeedQuiz(q) {
    var stage = document.getElementById('game-stage');
    clear(stage);
    setProg(0, q.questions.length);
    var idx = 0, answers = [];
    startTimer(q.limit_sec || 60, function () { finish(); });

    function showQuestion() {
      clear(stage);
      if (idx >= q.questions.length) { finish(); return; }
      var qu = q.questions[idx];
      var card = el('div', 'q-card');
      card.style.maxWidth = '560px'; card.style.margin = '0 auto';
      var head = el('div', 'muted', '第 ' + (idx + 1) + ' / ' + q.questions.length + ' 题');
      card.appendChild(head);
      var word = el('div', '', qu.word);
      word.style.cssText = 'font-size:30px;font-weight:800;letter-spacing:-.5px;margin:4px 0';
      card.appendChild(word);
      if (qu.phonetic) card.appendChild(el('div', 'muted', qu.phonetic));
      var audioBtn = el('button', 'btn btn-secondary btn-sm', '🔊 听发音');
      audioBtn.style.margin = '8px 0 14px';
      audioBtn.addEventListener('click', function () { playAudio(audioUrl(q.audio_base, qu.audio)); });
      card.appendChild(audioBtn);

      var opts = el('div', '');
      opts.style.cssText = 'display:grid;grid-template-columns:1fr 1fr;gap:10px';
      qu.options.forEach(function (opt, oi) {
        var b = el('button', 'btn btn-secondary', opt);
        b.style.padding = '14px'; b.style.fontWeight = '700';
        b.addEventListener('click', function () {
          answers.push([qu.i, oi]);
          idx++; setProg(answers.length, q.questions.length);
          showQuestion();
        });
        opts.appendChild(b);
      });
      card.appendChild(opts);
      stage.appendChild(card);
    }
    function finish() { stopTimer(); submitGame('speed_quiz', { answers: answers, elapsed_sec: (Date.now() - t0) / 1000 }); }
    showQuestion();
  }

  /* --------------------------- 3) Listening --------------------------- */
  function renderListening(q) {
    var stage = document.getElementById('game-stage');
    clear(stage);
    setProg(0, q.questions.length);
    var idx = 0, answers = [];
    startTimer(q.limit_sec || 120, function () { finish(); });

    function showQuestion() {
      clear(stage);
      if (idx >= q.questions.length) { finish(); return; }
      var qu = q.questions[idx];
      var card = el('div', 'q-card');
      card.style.maxWidth = '560px'; card.style.margin = '0 auto';
      card.appendChild(el('div', 'muted', '第 ' + (idx + 1) + ' / ' + q.questions.length + ' 题 · 听音选词'));
      var big = el('button', 'btn btn-primary', '🔊 播放发音 Play');
      big.style.cssText = 'font-size:18px;padding:16px 24px;margin:14px auto;display:block';
      big.addEventListener('click', function () { playAudio(audioUrl(q.audio_base, qu.audio)); });
      card.appendChild(big);
      if (qu.phonetic) card.appendChild(el('div', 'muted', '（音标：' + qu.phonetic + '）'));
      var opts = el('div', '');
      opts.style.cssText = 'display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px';
      qu.options.forEach(function (opt, oi) {
        var b = el('button', 'btn btn-secondary', opt);
        b.style.padding = '14px'; b.style.fontWeight = '700';
        b.addEventListener('click', function () {
          answers.push([qu.i, oi]);
          idx++; setProg(answers.length, q.questions.length);
          showQuestion();
        });
        opts.appendChild(b);
      });
      card.appendChild(opts);
      stage.appendChild(card);
      playAudio(audioUrl(q.audio_base, qu.audio));
    }
    function finish() { stopTimer(); submitGame('listening_challenge', { answers: answers }); }
    showQuestion();
  }

  /* --------------------------- 4) Word Builder ------------------------- */
  function renderWordBuilder(q) {
    var stage = document.getElementById('game-stage');
    clear(stage);
    setProg(0, q.questions.length);
    var idx = 0, answers = [];
    startTimer(q.limit_sec || 120, function () { finish(); });

    function showQuestion() {
      clear(stage);
      if (idx >= q.questions.length) { finish(); return; }
      var qu = q.questions[idx];
      var card = el('div', 'q-card');
      card.style.maxWidth = '560px'; card.style.margin = '0 auto';
      card.appendChild(el('div', 'muted', '第 ' + (idx + 1) + ' / ' + q.questions.length + ' 题 · 拼出单词'));

      var letters = el('div', '');
      letters.style.cssText = 'display:flex;gap:8px;justify-content:center;flex-wrap:wrap;margin:14px 0';
      qu.letters.forEach(function (ch) {
        var c = el('span', '');
        c.style.cssText = 'width:42px;height:42px;display:grid;place-items:center;border-radius:12px;' +
          'background:linear-gradient(135deg,#6d5efc,#22d3ee);color:#fff;font-weight:900;font-size:20px;' +
          'box-shadow:0 6px 16px rgba(109,94,252,.3)';
        c.textContent = ch;
        letters.appendChild(c);
      });
      card.appendChild(letters);
      card.appendChild(el('div', 'muted', '释义：' + qu.meaning + (qu.phonetic ? '  ' + qu.phonetic : '')));

      var input = el('input', 'form-control');
      input.type = 'text';
      input.placeholder = '输入正确单词 Type the word';
      input.style.cssText = 'text-align:center;font-size:18px;letter-spacing:2px;text-transform:lowercase;padding:12px';
      input.autocomplete = 'off';
      card.appendChild(input);

      var go = el('button', 'btn btn-primary', '确定 ✓');
      go.style.cssText = 'margin-top:12px;font-size:16px';
      function commit() {
        var val = input.value.trim();
        if (!val) { toast('请输入单词'); return; }
        answers.push([qu.i, val]);
        idx++; setProg(answers.length, q.questions.length);
        showQuestion();
      }
      go.addEventListener('click', commit);
      input.addEventListener('keydown', function (e) { if (e.key === 'Enter') commit(); });
      card.appendChild(go);
      stage.appendChild(card);
      input.focus();
    }
    function finish() { stopTimer(); submitGame('word_builder', { answers: answers }); }
    showQuestion();
  }

  /* ----------------------------- 提交 / 结果 --------------------------- */
  function submitGame(key, payload) {
    var stage = document.getElementById('game-stage');
    clear(stage);
    stage.appendChild(el('div', 'muted', '判分中… Scoring…'));
    apiPost('/games/api/' + key + '/submit', payload).then(function (res) {
      stopTimer();
      if (!res || !res.ok) {
        clear(stage);
        stage.appendChild(el('div', 'muted', (res && res.error) || '提交失败'));
        return;
      }
      showResult(res.result || {});
    }).catch(function () {
      clear(stage);
      stage.appendChild(el('div', 'muted', '网络错误，请重试'));
    });
  }

  function showResult(r) {
    var stage = document.getElementById('game-stage');
    clear(stage);
    var card = el('div', 'q-card');
    card.style.maxWidth = '460px'; card.style.margin = '0 auto'; card.style.textAlign = 'center';
    card.appendChild(el('h2', '', '本局成绩 Result'));
    var score = el('div', '');
    score.style.cssText = 'font-size:48px;font-weight:900;background:linear-gradient(135deg,#6d5efc,#22d3ee);' +
      '-webkit-background-clip:text;background-clip:text;color:transparent;line-height:1.1';
    score.textContent = r.score != null ? r.score : 0;
    card.appendChild(score);

    var grid = el('div', 'stat-grid');
    grid.style.cssText = 'margin-top:16px;grid-template-columns:repeat(2,1fr)';
    [
      ['正确 Correct', (r.correct || 0) + ' / ' + (r.total || 0)],
      ['正确率 Accuracy', (r.accuracy != null ? r.accuracy : 0) + '%'],
      ['最高连击 Combo', r.max_combo || 0],
      ['获得 XP', '+' + (r.xp_earned != null ? r.xp_earned : 0)]
    ].forEach(function (row) {
      var s = el('div', 'stat-card'); s.style.textAlign = 'left';
      s.appendChild(el('div', 'stat-val', row[1]));
      s.appendChild(el('div', 'stat-label', row[0]));
      grid.appendChild(s);
    });
    card.appendChild(grid);
    if (r.best != null) card.appendChild(el('p', 'muted', '历史最佳 Best：' + r.best));
    card.appendChild(makeResultActions());
    stage.appendChild(card);
  }
})();
