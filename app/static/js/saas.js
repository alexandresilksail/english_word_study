/* ==========================================================================
   英语单词学习 SaaS 版 —— 前端交互
   纯原生 JS（无框架）：离线发音播放、收藏、四种测试模式、双语 UI、语言切换
   说明：本轮仅调整界面呈现与文案，所有接口、判分逻辑与数据流保持不变。
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

  /* ---------------------------------------------------------- 自制内联 SVG 图标
     与 _macros.html 的 icon() 保持一致（自制线性图标，无第三方依赖与版权风险） */
  var PATHS = {
    speaker: 'M4 9.2v5.6h3.6L12.4 19V5L7.6 9.2H4zM16 9.4a3.8 3.8 0 0 1 0 5.2M18.6 6.8a7.4 7.4 0 0 1 0 10.4',
    check: 'M4.5 12.5l4.8 4.8L19.5 7',
    x: 'M6 6l12 12M18 6 6 18',
    book: 'M12 7.2C10.6 5.7 7.2 5.2 4 5.7V18c3.2-.5 6.6.1 8 1.5 1.4-1.4 4.8-2 8-1.5V5.7c-3.2-.5-6.6 0-8 1.5zM12 7.2V19.5',
    list: 'M8.5 6H20M8.5 12H20M8.5 18H20M4 6h.01M4 12h.01M4 18h.01',
    edit: 'M4.5 19.5h3.2L19 8.2a2.3 2.3 0 0 0-3.2-3.2L4.5 16.3z',
    quiz: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 16.5a4.5 4.5 0 1 0 0-9 4.5 4.5 0 0 0 0 9zM12 13.2a1.2 1.2 0 1 0 0-2.4 1.2 1.2 0 0 0 0 2.4z',
    rotate: 'M20 12a8 8 0 1 1-2.6-5.9M20 4.5V10h-5.5'
  };
  function svg(name, cls) {
    var d = PATHS[name] || '';
    return '<svg class="' + (cls || 'icon') + '" viewBox="0 0 24 24" aria-hidden="true" focusable="false">' +
      '<path d="' + d + '"/></svg>';
  }

  /* ---------------------------------------------------------- 离线发音 */
  var currentAudio = null;
  window.playWord = function (file, btn) {
    if (!file) { toast('该单词暂无音频 / No audio'); return; }
    if (currentAudio) { currentAudio.pause(); currentAudio.currentTime = 0; }
    var a = new Audio('/static/audio/' + encodeURIComponent(file));
    currentAudio = a;
    if (btn) {
      qsa('.speak.playing').forEach(function (b) { b.classList.remove('playing'); });
      btn.classList.add('playing');
      a.onended = a.onerror = function () { btn.classList.remove('playing'); };
    }
    a.play().catch(function () { toast('浏览器阻止了自动播放，请再点一次 / Tap again'); });
  };

  qsa('.speak[data-play]').forEach(function (btn) {
    btn.addEventListener('click', function () { window.playWord(btn.getAttribute('data-play'), btn); });
  });

  /* ---------------------------------------------------------- 语言切换（双语 / 中文 / English） */
  (function language() {
    var KEY = 'ews_lang';
    var order = ['both', 'zh', 'en'];
    function apply(lang) {
      if (lang === 'both') document.documentElement.removeAttribute('data-lang');
      else document.documentElement.setAttribute('data-lang', lang);
      try { localStorage.setItem(KEY, lang); } catch (e) { /* 忽略隐私模式 */ }
    }
    var saved = null;
    try { saved = localStorage.getItem(KEY); } catch (e) { saved = null; }
    if (saved && order.indexOf(saved) >= 0) apply(saved);

    var btn = qs('#lang-toggle');
    if (btn) {
      btn.addEventListener('click', function () {
        var cur = document.documentElement.getAttribute('data-lang') || 'both';
        apply(order[(order.indexOf(cur) + 1) % order.length]);
      });
    }
  })();

  /* ---------------------------------------------------------- 测试 */
  var quizRoot = qs('#quiz-root');
  if (quizRoot) {
    var state = {
      mode: qs('#quiz-root').getAttribute('data-mode') || 'choice',
      scope: 'all', size: 10,
      total: 0, idx: 0, score: 0, locked: false, startedAt: 0, answers: []
    };

    // 双语模式元信息（英文在前，中文在后）
    function modeMeta(m) {
      return {
        choice: { icon: 'book', en: 'English → Chinese', zh: '英文 → 中文', desc: '看英文单词选出中文释义' },
        zh_en: { icon: 'list', en: 'Chinese → English', zh: '中文 → 英文', desc: '看中文释义选出英文单词' },
        listen: { icon: 'speaker', en: 'Listening', zh: '听音测试', desc: '听英式发音选出正确单词' },
        spell: { icon: 'edit', en: 'Spelling', zh: '拼写测试', desc: '根据中文释义拼出英文单词' }
      }[m] || { icon: 'quiz', en: m, zh: m, desc: '' };
    }
    function modeLabel(m) {
      var meta = modeMeta(m);
      return '<span class="bi"><span class="bi-en">' + meta.en + '</span>' +
        '<span class="bi-zh">' + meta.zh + '</span></span>';
    }

    var SCOPES = [
      ['all', '全部单词', 'All Words'],
      ['new', '未学习', 'New'],
      ['learning', '学习中', 'Learning'],
      ['mastered', '已掌握', 'Learned'],
      ['favorites', '我的收藏', 'Favorites'],
      ['wrong', '错题本', 'Mistakes']
    ];
    function biHTML(zh, en) {
      return '<span class="bi"><span class="bi-en">' + en + '</span><span class="bi-zh">' + zh + '</span></span>';
    }

    function renderSetup() {
      var modes = ['choice', 'zh_en', 'listen', 'spell'];

      var html = '<div class="card">';
      html += '<div class="section-title"><h3>' + biHTML('选择测试模式', 'Choose a Mode') + '</h3>' +
        '<span class="muted small">' + biHTML('共四种，覆盖听说读写', 'Four modes') + '</span></div>';
      html += '<div class="setup-grid">';
      modes.forEach(function (m) {
        var meta = modeMeta(m);
        html += '<div class="opt-card' + (m === state.mode ? ' on' : '') + '" data-group="mode" data-value="' + m + '">'
          + '<b>' + svg(meta.icon) + ' ' + biHTML(meta.zh, meta.en) + '</b><span>' + meta.desc + '</span></div>';
      });
      html += '</div>';

      html += '<div class="section-title" style="margin-top:var(--ds-sp-5)"><h3>' +
        biHTML('选择出题范围', 'Choose a Range') + '</h3></div><div class="setup-grid">';
      SCOPES.forEach(function (s) {
        html += '<div class="opt-card' + (s[0] === state.scope ? ' on' : '') + '" data-group="scope" data-value="' + s[0] + '">'
          + '<b>' + biHTML(s[1], s[2]) + '</b><span>' + biHTML('从该范围随机抽题', 'Random questions from this range') + '</span></div>';
      });
      html += '</div>';

      html += '<div class="section-title" style="margin-top:var(--ds-sp-5)"><h3>' +
        biHTML('题目数量', 'Number of Questions') + '</h3></div><div class="setup-grid">';
      [10, 20, 30].forEach(function (n) {
        html += '<div class="opt-card' + (n === state.size ? ' on' : '') + '" data-group="size" data-value="' + n + '">'
          + '<b>' + biHTML(n + ' 题', n + ' questions') + '</b><span>' +
          biHTML('约 ' + Math.ceil(n * 0.6) + ' 分钟', 'About ' + Math.ceil(n * 0.6) + ' min') + '</span></div>';
      });
      html += '</div>';

      html += '<div class="row" style="margin-top:var(--ds-sp-5)">'
        + '<button class="btn btn-primary btn-lg" id="start-quiz">' + svg('quiz') +
        biHTML('开始测试', 'Start Quiz') + '</button></div></div>';

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
        if (!r.ok) { toast(r.error || '无法开始测试 / Cannot start'); return; }
        state.total = r.total; state.idx = 0; state.score = 0;
        state.startedAt = Date.now(); state.answers = [];
        renderQuestion();
      });
    }

    function renderQuestion() {
      get('/api/quiz/item?i=' + state.idx + '&_=' + Date.now()).then(function (r) {
        if (!r.ok) { toast(r.error || '获取题目失败 / Load failed'); return; }
        var q = r.q;
        var meta = modeMeta(q.mode);
        var pct = Math.round(state.idx * 100 / state.total);

        var html = '<div class="quiz-card">';
        html += '<div class="quiz-top"><span class="mode-badge">' + svg(meta.icon) + ' ' + modeLabel(q.mode) + '</span>'
          + '<div class="progress-line"><i style="width:' + pct + '%"></i></div>'
          + '<span class="quiz-count">' + (state.idx + 1) + '/' + state.total + '</span></div>';

        if (q.mode === 'choice') {
          html += '<div class="quiz-prompt"><h2 class="quiz-word">' + q.stem + '</h2>'
            + '<div class="quiz-ipa">' + (q.stem_sub || '') + '</div>'
            + '<div style="margin-top:var(--ds-sp-4)"><button class="speak big" id="choice-play">' + svg('speaker') + '</button></div></div>';
        } else if (q.mode === 'zh_en') {
          html += '<div class="quiz-prompt"><p class="quiz-cn">' + q.stem + '</p>'
            + '<div class="quiz-sub">' + biHTML('选出对应的英文单词', 'Choose the English word') +
            ' · ' + (q.stem_sub || '') + '</div></div>';
        } else if (q.mode === 'listen') {
          html += '<div class="quiz-prompt">'
            + '<button class="speak big" id="listen-play" data-file="' + (q.audio || '') + '">' + svg('speaker') + '</button>'
            + '<div class="quiz-sub" style="margin-top:var(--ds-sp-4)">' +
            biHTML('点击播放英式发音，选出你听到的单词', 'Tap to play the UK pronunciation, then choose the word') +
            '<br><span class="small muted">' + biHTML('没听清？可以再点一次', 'Tap again to replay') + '</span></div></div>';
        } else {
          html += '<div class="quiz-prompt"><p class="quiz-cn">' + q.stem + '</p>'
            + '<div class="quiz-sub">' + biHTML('首字母', 'First letter') + ' <b>' + (q.first || '') + '</b> · ' +
            biHTML('共', 'Total') + ' <b>' + (q.length || 0) + '</b> ' + biHTML('个字母', 'letters') +
            ' · ' + (q.stem_sub || '') + '</div>'
            + '<input class="spell-input" id="spell-box" placeholder="Type the English word / 在这里输入英文单词" autocomplete="off" autocapitalize="off" spellcheck="false">'
            + '<div style="margin-top:var(--ds-sp-3)"><button class="btn btn-primary" id="spell-submit">' +
            biHTML('提交答案', 'Submit') + '</button></div></div>';
        }

        if (q.mode !== 'spell') {
          html += '<div class="options">';
          (q.options || []).forEach(function (opt, i) {
            html += '<button class="option" data-i="' + i + '"><span class="idx">' + 'ABCD'[i] + '</span><span>' + opt + '</span></button>';
          });
          html += '</div>';
        }
        html += '<div class="feedback" id="fb"></div>';
        html += '<div class="row-between" style="margin-top:var(--ds-sp-5)">'
          + '<span class="small muted">' + biHTML('答对进入下一题，答错会自动加入错题本', 'Correct moves on; mistakes are saved to your review list') + '</span>'
          + '<button class="btn btn-primary" id="next-btn" style="display:none"></button></div>';
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
          var speakBtn = qs('#choice-play', quizRoot);
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
        if (!r.ok) { toast(r.error || '提交失败 / Submit failed'); state.locked = false; return; }
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
        fb.innerHTML = '<h4>' + (r.correct ? svg('check') : svg('x')) +
          (r.correct ? biHTML('回答正确', 'Correct') : biHTML('回答错误', 'Incorrect')) +
          '　<b>' + r.word + '</b> <span class="small muted">' + (r.phonetic || '') + '</span>' +
          ' <button class="speak sm" data-file="' + (r.audio || '') + '">' + svg('speaker') + '</button></h4>'
          + '<p class="en">' + (r.correct ? '' : biHTML('正确答案', 'Correct answer') + '：<b>' + r.expected + '</b>　') + r.meaning + '</p>'
          + '<p class="en">' + (r.example_en || '') + '</p>'
          + '<p class="zh">' + (r.example_cn || '') + '</p>';
        var sp = qs('.speak', fb);
        if (sp) sp.addEventListener('click', function () { window.playWord(r.audio, sp); });

        var next = qs('#next-btn', quizRoot);
        next.style.display = '';
        next.innerHTML = (state.idx + 1 >= state.total)
          ? biHTML('查看成绩', 'See Results')
          : biHTML('下一题', 'Next');
        next.addEventListener('click', function () {
          state.locked = false;
          if (state.idx + 1 >= state.total) { finish(); return; }
          state.idx += 1;
          renderQuestion();
        });
        if (!r.correct) {
          window.playWord(r.audio, null);
        }
      });
    }

    function finish() {
      post('/api/quiz/finish', {}).then(function (r) {
        if (!r.ok) { toast(r.error || '提交失败 / Submit failed'); return; }
        var sec = Math.max(1, Math.round((Date.now() - state.startedAt) / 1000));
        var verdicts = {
          outstanding: ['great', '太强了，几乎全对', 'Outstanding — almost perfect'],
          good: ['good', '状态不错，继续保持', 'Good work — keep it up'],
          pass: ['soso', '及格线上，再练几组', 'Just passed — practise more'],
          retry: ['bad', '别灰心，错题本是你的宝库', 'Keep going — your review list helps']
        };
        var v = verdicts[r.comment] || verdicts.pass;

        quizRoot.innerHTML = '<div class="card" style="text-align:center">'
          + '<div class="result-ring" style="--deg:' + (r.percent * 3.6) + 'deg">'
          + '<div><b>' + r.score + '/' + r.total + '</b><span>' + biHTML('答对题数', 'Correct') + '</span></div></div>'
          + '<div style="margin-top:var(--ds-sp-4)"><span class="verdict ' + v[0] + '">' + biHTML(v[1], v[2]) + '</span></div>'
          + '<h2 style="margin:var(--ds-sp-4) 0 4px">' + biHTML('本次得分', 'Your Score') + ' ' + r.percent + '%</h2>'
          + '<p class="muted">' + modeLabel(r.mode) + ' · ' + biHTML('用时', 'Time') + ' ' + sec + ' ' +
          biHTML('秒', 'sec') + ' · ' + biHTML('正确率已写入你的学习档案', 'Saved to your profile') + '</p>'
          + '<div class="row" style="justify-content:center;margin-top:var(--ds-sp-5)">'
          + '<button class="btn btn-primary" id="again-btn">' + svg('rotate') + biHTML('再来一组', 'Try Again') + '</button>'
          + '<a class="btn btn-secondary" href="/wrong">' + biHTML('查看错题本', 'Review Mistakes') + '</a>'
          + '<a class="btn btn-ghost" href="/dashboard">' + biHTML('返回学习中心', 'Back to Dashboard') + '</a></div></div>';
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
      var s = p.get('scope') || quizRoot.getAttribute('data-scope');
      if (m && ['choice', 'zh_en', 'listen', 'spell'].indexOf(m) >= 0) state.mode = m;
      if (s) state.scope = s;
    })();

    renderSetup();
  }

  /* ---------------------------------------------------------- 收藏等按钮反馈 */
  qsa('.wc-actions form').forEach(function (f) {
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
