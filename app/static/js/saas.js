/* ==========================================================================
   英语单词学习 SaaS 版 —— 前端交互
   纯原生 JS：发音播放（本地 MP3）、收藏、测试四种模式、彩带与吐司
   ========================================================================== */
(function () {
  'use strict';

  /* ---------------------------------------------------------- 基础工具 */
  function qs(sel, root) { return (root || document).querySelector(sel); }
  function qsa(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  function csrfToken() {
    var m = qs('meta[name="csrf-token"]');
    return m ? m.getAttribute('content') : '';
  }
  function post(url, body) {
    return fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
      credentials: 'same-origin',
      body: JSON.stringify(body || {})
    }).then(function (r) { return r.json(); });
  }
  function get(url) {
    return fetch(url, { credentials: 'same-origin' }).then(function (r) { return r.json(); });
  }
  function toast(msg) {
    var t = qs('#toast');
    if (!t) return;
    t.textContent = msg;
    t.classList.add('show');
    clearTimeout(window.__toastTimer);
    window.__toastTimer = setTimeout(function () { t.classList.remove('show'); }, 2200);
  }
  function confetti() {
    if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    var wrap = document.createElement('div');
    wrap.className = 'confetti';
    var colors = ['#4a6cf7', '#8c63ff', '#ff6f9c', '#22b07d', '#ffb648', '#38bdf8'];
    for (var i = 0; i < 70; i++) {
      var b = document.createElement('i');
      var dur = 2 + Math.random() * 1.6;
      b.style.left = (Math.random() * 100) + '%';
      b.style.background = colors[i % colors.length];
      b.style.animationDuration = dur + 's';
      b.style.animationDelay = (Math.random() * 0.7) + 's';
      b.style.setProperty('--dx', (Math.random() * 220 - 110) + 'px');
      b.style.setProperty('--rot', (Math.random() * 720 - 360) + 'deg');
      if (Math.random() < 0.3) b.style.borderRadius = '50%';
      wrap.appendChild(b);
    }
    document.body.appendChild(wrap);
    setTimeout(function () { wrap.remove(); }, 4200);
  }

  /* ---------------------------------------------------------- 离线发音 */
  var currentAudio = null;
  window.playWord = function (file, btn) {
    if (!file) { toast('该单词暂无音频'); return; }
    if (currentAudio) { currentAudio.pause(); currentAudio.currentTime = 0; }
    var a = new Audio('/static/audio/' + encodeURIComponent(file));
    currentAudio = a;
    if (btn) {
      qsa('.speak.playing').forEach(function (b) { b.classList.remove('playing'); });
      btn.classList.add('playing');
      a.onended = a.onerror = function () { btn.classList.remove('playing'); };
    }
    a.play().catch(function () { toast('浏览器阻止了自动播放，请再点一次 🔊'); });
  };

  qsa('.speak[data-play]').forEach(function (btn) {
    btn.addEventListener('click', function () { window.playWord(btn.getAttribute('data-play'), btn); });
  });

  /* ---------------------------------------------------------- 测试 */
  var quizRoot = qs('#quiz-root');
  if (quizRoot) {
    var state = {
      mode: qs('#quiz-root').getAttribute('data-mode') || 'choice',
      scope: 'all', size: 10,
      total: 0, idx: 0, score: 0, locked: false, startedAt: 0, answers: []
    };

    function modeMeta(m) {
      return {
        choice: { icon: '🇬🇧', name: '英文 → 中文', desc: '看英文单词选出中文释义' },
        zh_en: { icon: '🇨🇳', name: '中文 → 英文', desc: '看中文释义选出英文单词' },
        listen: { icon: '🎧', name: '听音测试', desc: '听英式发音选出正确单词' },
        spell: { icon: '⌨️', name: '拼写测试', desc: '根据中文释义拼出英文单词' }
      }[m] || { icon: '🎯', name: m, desc: '' };
    }

    function renderSetup() {
      var modes = ['choice', 'zh_en', 'listen', 'spell'];
      var scopes = [['all', '全部单词'], ['new', '未学习'], ['learning', '学习中'],
                    ['mastered', '已掌握'], ['favorites', '我的收藏'], ['wrong', '错题本']];

      var html = '<div class="card" style="padding:26px">';
      html += '<div class="section-title"><h3>🎯 选择测试模式</h3><span class="muted small">共四种，覆盖听说读写</span></div>';
      html += '<div class="setup-grid" style="grid-template-columns:repeat(4,1fr)">';
      modes.forEach(function (m) {
        var meta = modeMeta(m);
        html += '<div class="opt-card' + (m === state.mode ? ' on' : '') + '" data-group="mode" data-value="' + m + '">'
          + '<b>' + meta.icon + ' ' + meta.name + '</b><span>' + meta.desc + '</span></div>';
      });
      html += '</div>';

      html += '<div class="section-title" style="margin-top:20px"><h3>📚 选择出题范围</h3></div><div class="setup-grid" style="grid-template-columns:repeat(3,1fr)">';
      scopes.forEach(function (s) {
        html += '<div class="opt-card' + (s[0] === state.scope ? ' on' : '') + '" data-group="scope" data-value="' + s[0] + '">'
          + '<b>' + s[1] + '</b><span>从该范围随机抽题</span></div>';
      });
      html += '</div>';

      html += '<div class="section-title" style="margin-top:20px"><h3>🔢 题目数量</h3></div><div class="setup-grid">';
      [10, 20, 30].forEach(function (n) {
        html += '<div class="opt-card' + (n === state.size ? ' on' : '') + '" data-group="size" data-value="' + n + '">'
          + '<b>' + n + ' 题</b><span>预计 ' + Math.ceil(n * 0.6) + ' 分钟内完成</span></div>';
      });
      html += '</div>';

      html += '<div style="margin-top:22px;display:flex;gap:10px;align-items:center;flex-wrap:wrap">'
        + '<button class="btn btn-primary btn-lg" id="start-quiz">🚀 开始测试</button>'
        + '<img src="/static/img/mascot-owl.svg" alt="" style="height:56px;opacity:.9">'
        + '</div></div>';

      quizRoot.innerHTML = html;

      qsa('.opt-card', quizRoot).forEach(function (card) {
        card.addEventListener('click', function () {
          var g = card.getAttribute('data-group');
          qsa('.opt-card[data-group="' + g + '"]', quizRoot).forEach(function (c) { c.classList.remove('on'); });
          card.classList.add('on');
          var v = card.getAttribute('data-value');
          state[g] = (g === 'size') ? parseInt(v, 10) : v;
        });
      });
      qs('#start-quiz', quizRoot).addEventListener('click', start);
    }

    function start() {
      post('/api/quiz/start', { mode: state.mode, scope: state.scope, size: state.size }).then(function (r) {
        if (!r.ok) { toast(r.error || '无法开始测试'); return; }
        state.total = r.total; state.idx = 0; state.score = 0;
        state.startedAt = Date.now(); state.answers = [];
        renderQuestion();
      });
    }

    function renderQuestion() {
      get('/api/quiz/item?i=' + state.idx + '&_=' + Date.now()).then(function (r) {
        if (!r.ok) { toast(r.error || '获取题目失败'); return; }
        var q = r.q;
        var meta = modeMeta(q.mode);
        var pct = Math.round(state.idx * 100 / state.total);

        var html = '<div class="quiz-card">';
        html += '<div class="quiz-top"><span class="mode-badge">' + meta.icon + ' ' + meta.name + '</span>'
          + '<div class="progress-line"><i style="width:' + pct + '%"></i></div>'
          + '<span class="quiz-count">' + (state.idx + 1) + '/' + state.total + '</span></div>';

        if (q.mode === 'choice') {
          html += '<div class="quiz-prompt"><h2 class="quiz-word">' + q.stem + '</h2>'
            + '<div class="quiz-ipa">' + (q.stem_sub || '') + '</div>'
            + '<div style="margin-top:12px"><button class="speak big" data-file="' + '' + '">🔊</button></div></div>';
        } else if (q.mode === 'zh_en') {
          html += '<div class="quiz-prompt"><p class="quiz-cn">' + q.stem + '</p>'
            + '<div class="quiz-sub">选出对应的英文单词 · ' + (q.stem_sub || '') + '</div></div>';
        } else if (q.mode === 'listen') {
          html += '<div class="quiz-prompt">'
            + '<button class="speak big" id="listen-play" data-file="' + (q.audio || '') + '">🔊</button>'
            + '<div class="quiz-sub" style="margin-top:14px">点击播放英式发音，选出你听到的单词<br>'
            + '<span class="small muted">没听清？可以再点一次</span></div></div>';
        } else {
          html += '<div class="quiz-prompt"><p class="quiz-cn">' + q.stem + '</p>'
            + '<div class="quiz-sub">首字母 <b>' + (q.first || '') + '</b> · 共 <b>' + (q.length || 0) + '</b> 个字母 · ' + (q.stem_sub || '') + '</div>'
            + '<input class="spell-input" id="spell-box" placeholder="在这里输入英文单词" autocomplete="off" autocapitalize="off" spellcheck="false">'
            + '<div style="margin-top:12px"><button class="btn btn-primary" id="spell-submit">提交答案</button></div></div>';
        }

        if (q.mode !== 'spell') {
          html += '<div class="options">';
          (q.options || []).forEach(function (opt, i) {
            html += '<button class="option" data-i="' + i + '"><span class="idx">' + 'ABCD'[i] + '</span><span>' + opt + '</span></button>';
          });
          html += '</div>';
        }
        html += '<div class="feedback" id="fb"></div>';
        html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-top:18px;gap:10px">'
          + '<span class="small muted">答对进入下一题，答错会自动加入错题本</span>'
          + '<button class="btn btn-primary" id="next-btn" style="display:none">下一题 →</button></div>';
        html += '</div>';

        quizRoot.innerHTML = html;

        if (q.mode === 'listen') {
          var playBtn = qs('#listen-play', quizRoot);
          if (playBtn) {
            playBtn.addEventListener('click', function () { window.playWord(q.audio, playBtn); });
            setTimeout(function () { window.playWord(q.audio, playBtn); }, 260);
          }
        }
        if (q.mode === 'choice') {
          var speakBtn = qs('.speak.big', quizRoot);
          if (speakBtn) speakBtn.addEventListener('click', function () {
            get('/api/audio/' + q.word_id).then(function (r) {
              if (r.ok) window.playWord(r.audio, speakBtn);
            });
          });
        }
        if (q.mode === 'spell') {
          var box = qs('#spell-box', quizRoot);
          box.focus();
          qs('#spell-submit', quizRoot).addEventListener('click', function () { submit(box.value); });
          box.addEventListener('keydown', function (e) { if (e.key === 'Enter') submit(box.value); });
        } else {
          qsa('.option', quizRoot).forEach(function (btn) {
            btn.addEventListener('click', function () {
              if (state.locked) return;
              submit(btn.getAttribute('data-i'), btn);
            });
          });
        }
      });
    }

    function submit(answer, btn) {
      if (state.locked) return;
      state.locked = true;
      post('/api/quiz/answer', { i: state.idx, answer: answer }).then(function (r) {
        if (!r.ok) { toast(r.error || '提交失败'); state.locked = false; return; }
        state.answers.push({ correct: r.correct, expected: r.expected });
        if (r.correct) state.score += 1;

        if (r.mode !== 'spell') {
          qsa('.option', quizRoot).forEach(function (b) {
            b.disabled = true;
            var val = b.textContent.replace(/^\s*[ABCD]/, '').trim();
            if (val === r.expected) b.classList.add('right');
          });
          if (btn && !r.correct) btn.classList.add('wrong');
        } else {
          var box = qs('#spell-box', quizRoot);
          box.classList.add(r.correct ? 'ok' : 'no');
          box.disabled = true;
        }

        var fb = qs('#fb', quizRoot);
        fb.className = 'feedback show ' + (r.correct ? 'ok' : 'no');
        fb.innerHTML = '<h4>' + (r.correct ? '✅ 回答正确' : '❌ 回答错误')
          + '　<b>' + r.word + '</b> <span class="small muted">' + (r.phonetic || '') + '</span>'
          + ' <button class="speak sm" data-file="' + (r.audio || '') + '">🔊</button></h4>'
          + '<p class="en">' + (r.correct ? '' : '正确答案：<b>' + r.expected + '</b>　') + r.meaning + '</p>'
          + '<p class="en">' + (r.example_en || '') + '</p>'
          + '<p class="zh">' + (r.example_cn || '') + '</p>';
        var sp = qs('.speak', fb);
        if (sp) sp.addEventListener('click', function () { window.playWord(r.audio, sp); });

        var next = qs('#next-btn', quizRoot);
        next.style.display = '';
        next.textContent = (state.idx + 1 >= state.total) ? '查看成绩 →' : '下一题 →';
        next.addEventListener('click', function () {
          state.locked = false;
          if (state.idx + 1 >= state.total) { finish(); return; }
          state.idx += 1;
          renderQuestion();
        });
        if (r.correct) {
          setTimeout(function () { }, 0);
        } else {
          window.playWord(r.audio, null);
        }
      });
    }

    function finish() {
      post('/api/quiz/finish', {}).then(function (r) {
        if (!r.ok) { toast(r.error || '提交失败'); return; }
        var sec = Math.max(1, Math.round((Date.now() - state.startedAt) / 1000));
        var verdicts = {
          outstanding: ['great', '🏆 太强了！几乎全对'],
          good: ['good', '👍 状态不错，继续保持'],
          pass: ['soso', '🙂 及格线上，再练几组'],
          retry: ['bad', '💪 别灰心，错题本是你的宝库']
        };
        var v = verdicts[r.comment] || verdicts.pass;
        if (r.percent >= 85) confetti();

        quizRoot.innerHTML = '<div class="card" style="padding:30px;text-align:center">'
          + '<div class="result-ring" style="--deg:' + (r.percent * 3.6) + 'deg">'
          + '<div><b>' + r.score + '/' + r.total + '</b><span>答对题数</span></div></div>'
          + '<div style="margin-top:16px"><span class="verdict ' + v[0] + '">' + v[1] + '</span></div>'
          + '<h2 style="margin:16px 0 4px">本次得分 ' + r.percent + '%</h2>'
          + '<p class="muted">' + modeMeta(r.mode).name + ' · 用时 ' + sec + ' 秒 · 正确率已写入你的学习档案</p>'
          + '<img class="result-art" src="/static/img/' + (r.percent >= 85 ? 'celebrate.svg' : 'mascot-owl.svg') + '" alt="">'
          + '<div style="display:flex;gap:10px;justify-content:center;margin-top:20px;flex-wrap:wrap">'
          + '<button class="btn btn-primary" id="again-btn">再来一组</button>'
          + '<a class="btn btn-outline" href="/wrong">查看错题本</a>'
          + '<a class="btn btn-ghost" href="/dashboard">返回学习主页</a></div></div>';
        qs('#again-btn', quizRoot).addEventListener('click', function () {
          state.locked = false;
          renderSetup();
        });
      });
    }

    // 预选 mode/scope
    (function preset() {
      var p = new URLSearchParams(location.search);
      var m = p.get('mode');
      var s = p.get('scope');
      if (m && ['choice', 'zh_en', 'listen', 'spell'].indexOf(m) >= 0) state.mode = m;
      if (s) state.scope = s;
    })();

    renderSetup();
  }

  /* ---------------------------------------------------------- 收藏等按钮反馈 */
  qsa('.card-actions form').forEach(function (f) {
    f.addEventListener('submit', function (e) {
      e.preventDefault();
      var body = new FormData(f);
      fetch(f.getAttribute('action'), {
        method: 'POST', body: body, headers: { 'X-CSRFToken': csrfToken() },
        credentials: 'same-origin', redirect: 'follow'
      }).then(function () { location.reload(); });
    });
  });

  /* ---------------------------------------------------------- 表单防重复提交 */
  qsa('form').forEach(function (f) {
    f.addEventListener('submit', function () {
      var btn = qs('button[type=submit]', f);
      if (btn) { btn.disabled = true; setTimeout(function () { btn.disabled = false; }, 3000); }
    });
  });
})();
