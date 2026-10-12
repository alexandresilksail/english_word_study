// pages/home/home.js
//
// 首页：学习概览 + 功能入口。数据全部来自 GET /api/wx/v1/home（真实后端）。

const api = require('../../utils/api')
const auth = require('../../utils/auth')
const { percent } = require('../../utils/util')

Page({
  data: {
    loading: true,
    error: '',
    greeting: '',
    nickname: '同学',
    streak: 0,
    level: 1,
    dailyGoal: 20,
    todayDone: 0,
    goalPercent: 0,
    stats: {}
  },

  onLoad() {
    this.updateGreeting()
  },

  onShow() {
    // 每次显示都刷新：从其它页面（答题 / 收藏）返回时数据是最新的
    if (auth.getAccessToken()) {
      this.loadData()
    } else {
      this.setData({ loading: false, error: '请先登录' })
    }
  },

  onPullDownRefresh() {
    this.loadData().finally(() => wx.stopPullDownRefresh())
  },

  updateGreeting() {
    const h = new Date().getHours()
    this.setData({
      greeting: h < 6 ? '夜深了' : h < 12 ? '早上好' : h < 14 ? '中午好' : h < 18 ? '下午好' : '晚上好'
    })
  },

  loadData() {
    this.setData({ loading: true, error: '' })
    return api.request({ url: '/api/wx/v1/home', silent: true })
      .then((data) => {
        const user = auth.getUser() || {}
        const stats = data.stats || {}
        this.setData({
          loading: false,
          nickname: user.username || '同学',
          streak: data.streak_days || 0,
          level: data.level || 1,
          dailyGoal: data.daily_goal || 20,
          todayDone: data.today_done || 0,
          goalPercent: percent(data.today_done || 0, data.daily_goal || 20),
          stats
        })
      })
      .catch((err) => {
        this.setData({
          loading: false,
          error: err.code === 'NETWORK_ERROR' ? '网络连接失败，请检查网络后重试' : '加载失败，请稍后重试'
        })
      })
  },

  // ---------- 入口跳转 ----------
  goWords() { wx.switchTab({ url: '/pages/words/words' }) },
  goQuiz() { wx.switchTab({ url: '/pages/quiz/quiz' }) },
  goProgress() { wx.switchTab({ url: '/pages/progress/progress' }) },
  goFavorites() { wx.navigateTo({ url: '/pages/favorites/favorites' }) },
  goWrong() { wx.navigateTo({ url: '/pages/wrong/wrong' }) },
  goCourses() { wx.switchTab({ url: '/pages/words/words' }) },
  goListening() { wx.switchTab({ url: '/pages/words/words' }) },
  goSpeaking() { wx.switchTab({ url: '/pages/words/words' }) }
})
