// pages/quiz/quiz.js
//
// 测试中心：设置 → 答题 → 交卷，四接口串行闭环。
//
//    POST /api/wx/v1/quiz/start   开始一轮测试，下发签名状态令牌 state
//    GET  /api/wx/v1/quiz/item    取第 i 题，同时下发**更新后**的 state
//    POST /api/wx/v1/quiz/answer  判题，同样返回更新后的 state
//    POST /api/wx/v1/quiz/finish  交卷，写 TestRecord，返回成绩
//
// state 保管纪律：服务端每次返回的 state 都必须覆盖本地旧值再发下一个请求，
// 否则判题依据（正确选项下标）与计时起点会丢失。

const api = require('../../utils/api')
const { debounce } = require('../../utils/util')

const MODES = [
  { key: 'choice', name: '英译汉', desc: '看英文选中文释义' },
  { key: 'zh_en', name: '汉译英', desc: '看中文选英文单词' },
  { key: 'listen', name: '听音选词', desc: '听发音选单词' },
  { key: 'spell', name: '拼写', desc: '看释义拼写英文' }
]

const SCOPES = [
  { key: 'all', name: '全部' },
  { key: 'new', name: '未学' },
  { key: 'learning', name: '学习中' },
  { key: 'mastered', name: '已掌握' },
  { key: 'favorites', name: '收藏' },
  { key: 'wrong', name: '错题' }
]

Page({
  data: {
    phase: 'setup',            // setup | playing
    modes: MODES,
    scopes: SCOPES,
    mode: 'choice',
    scope: 'all',
    size: 10,
    sizeOptions: [5, 10, 20, 30],

    starting: false,
    state: '',                 // 签名状态令牌（itsdangerous，非 JWT）
    q: null,
    total: 0,
    index: 0,
    progressPercent: 0,
    modeName: '英译汉',

    selected: -1,
    spellInput: '',
    submitting: false,
    feedback: null,            // 判题结果

    playingAudio: false,
    audioError: ''
  },

  onLoad() {
    this._audio = null
  },

  onShow() {
    // 接收其它页面传来的出题范围意图（如错题本「练这些」）
    let pending
    try { pending = wx.getStorageSync('quiz_pending_scope') } catch (e) { pending = '' }
    if (pending) {
      wx.removeStorageSync('quiz_pending_scope')
      if (this.data.phase === 'setup' && this.data.scopes.some(s => s.key === pending)) {
        const name = (this.data.scopes.find(s => s.key === pending) || {}).name || ''
        this.setData({ scope: pending })
        wx.showToast({ title: `已选择「${name}」范围，点击开始测试`, icon: 'none' })
      }
    }
  },

  onUnload() {
    if (this._audio) { this._audio.destroy(); this._audio = null }
  },

  // ---------- 设置 ----------
  selectMode(e) {
    this.setData({ mode: e.currentTarget.dataset.key })
  },
  selectScope(e) {
    this.setData({ scope: e.currentTarget.dataset.key })
  },
  selectSize(e) {
    this.setData({ size: e.currentTarget.dataset.size })
  },

  // ---------- 开始测试 ----------
  startQuiz() {
    if (this.data.starting) return
    this.setData({ starting: true })
    api.request({
      url: '/api/wx/v1/quiz/start',
      method: 'POST',
      data: { mode: this.data.mode, scope: this.data.scope, size: this.data.size },
      silent: true
    }).then((data) => {
      this.setData({
        starting: false,
        phase: 'playing',
        state: data.state,
        total: data.total || 0,
        index: 0,
        progressPercent: 0
      })
      this.loadItem(0)
    }).catch((err) => {
      this.setData({ starting: false })
      // no_words：范围内没有单词 —— 明确告知，不静默
      if (err.code === 'no_words') {
        wx.showModal({
          title: '无法开始测试',
          content: '当前范围内没有可测试的单词，请更换范围或先去学习一些单词',
          showCancel: false
        })
      }
    })
  },

  // ---------- 取题 ----------
  loadItem(i) {
    this.setData({
      q: null,
      selected: -1,
      spellInput: '',
      feedback: null,
      audioError: ''
    })
    return api.request({
      url: '/api/wx/v1/quiz/item',
      data: { state: this.data.state, i },
      silent: true
    }).then((data) => {
      // 必须覆盖本地 state：服务端在此写入了本题的正确选项下标
      this.setData({
        q: data.q,
        state: data.state,
        total: data.total || this.data.total,
        index: i,
        progressPercent: Math.round(i * 100 / (this.data.total || 1)),
        modeName: (MODES.find(m => m.key === data.q.mode) || {}).name || ''
      })
      // listen 模式自动播放一次发音
      if (data.q.mode === 'listen' && data.q.audio_url) {
        this.playQuestionAudio()
      }
    }).catch((err) => {
      if (err.code === 'invalid_state' || err.code === 'state_expired' ||
          err.code === 'missing_state' || err.code === 'out_of_range') {
        wx.showModal({
          title: '测试状态失效',
          content: '测试已超时或状态无效，请重新开始',
          showCancel: false,
          success: () => this.resetToSetup()
        })
      } else {
        wx.showToast({ title: '加载题目失败，请重试', icon: 'none' })
      }
    })
  },

  // ---------- 选项 ----------
  selectOption(e) {
    // 已判题 / 判题中：忽略重复点击
    if (this.data.feedback || this.data.submitting) return
    const index = e.currentTarget.dataset.index
    this.setData({ selected: index })
    this.submitAnswer(index)
  },

  // ---------- 拼写 ----------
  onSpellInput(e) {
    this.setData({ spellInput: e.detail.value })
  },
  submitSpell() {
    if (this.data.feedback || this.data.submitting) return
    const text = (this.data.spellInput || '').trim()
    if (!text) return
    this.submitAnswer(text)
  },

  // ---------- 判题 ----------
  submitAnswer(answer) {
    this.setData({ submitting: true })
    api.request({
      url: '/api/wx/v1/quiz/answer',
      method: 'POST',
      data: { state: this.data.state, i: this.data.index, answer },
      silent: true
    }).then((data) => {
      this.setData({
        submitting: false,
        feedback: {
          correct: data.correct,
          expected: data.expected,
          word: data.word,
          phonetic: data.phonetic,
          meaning: data.meaning,
          example_en: data.example_en
        },
        // 覆盖 state：服务端写入了本题的判题记录
        state: data.state
      })
    }).catch((err) => {
      this.setData({ submitting: false, selected: -1 })
      if (err.code === 'invalid_state' || err.code === 'state_expired' ||
          err.code === 'missing_state' || err.code === 'out_of_range') {
        wx.showModal({
          title: '测试状态失效',
          content: '测试已超时或状态无效，请重新开始',
          showCancel: false,
          success: () => this.resetToSetup()
        })
      } else {
        wx.showToast({ title: '提交失败，请重试', icon: 'none' })
      }
    })
  },

  // ---------- 下一题 / 交卷 ----------
  nextQuestion: debounce(function () {
    if (this.data.index + 1 < this.data.total) {
      this.loadItem(this.data.index + 1)
    } else {
      this.finishQuiz()
    }
  }),

  finishQuiz() {
    api.request({
      url: '/api/wx/v1/quiz/finish',
      method: 'POST',
      data: { state: this.data.state },
      silent: true
    }).then((data) => {
      const q = [
        `total=${encodeURIComponent(data.total)}`,
        `score=${encodeURIComponent(data.score)}`,
        `percent=${encodeURIComponent(data.percent)}`,
        `duration=${encodeURIComponent(data.duration)}`,
        `mode=${encodeURIComponent(this.data.mode)}`,
        `comment=${encodeURIComponent(data.comment || '')}`
      ].join('&')
      wx.redirectTo({ url: `/pages/quiz-result/quiz-result?${q}` })
    }).catch((err) => {
      if (err.code === 'invalid_state' || err.code === 'state_expired' ||
          err.code === 'missing_state') {
        wx.showModal({
          title: '测试状态失效',
          content: '测试已超时或状态无效，请重新开始',
          showCancel: false,
          success: () => this.resetToSetup()
        })
      } else {
        wx.showToast({ title: '交卷失败，请重试', icon: 'none' })
      }
    })
  },

  // ---------- 退出 ----------
  quitQuiz() {
    wx.showModal({
      title: '退出测试',
      content: '当前进度将不会保存，确定退出吗？',
      success: (res) => { if (res.confirm) this.resetToSetup() }
    })
  },
  resetToSetup() {
    if (this._audio) { this._audio.destroy(); this._audio = null }
    this.setData({ phase: 'setup', state: '', q: null, feedback: null })
  },

  // ---------- 题干发音 ----------
  playQuestionAudio() {
    const url = this.data.q && this.data.q.audio_url
    if (!url) {
      this.setData({ audioError: '该单词暂无音频文件' })
      return
    }
    if (this._audio) { this._audio.destroy(); this._audio = null }
    this.setData({ playingAudio: true, audioError: '' })
    const inner = wx.createInnerAudioContext()
    this._audio = inner
    inner.src = url.startsWith('http') ? url : api.BASE_URL + url
    inner.onEnded(() => { this.setData({ playingAudio: false }) })
    inner.onError(() => {
      this.setData({ playingAudio: false, audioError: '音频播放失败，请稍后重试' })
    })
    inner.play()
  }
})
