// pages/progress/progress.js
//
// 学习进度：GET /api/wx/v1/progress。
// 包含等级/XP、核心统计、14 天趋势、徽章、技能分布——
// 全部为后端真实统计值，前端只做展示映射（趋势高度、技能中文名）。

const api = require('../../utils/api')

// 技能 key → 中文名（与 models.SKILLS 的 key 对应）
const SKILL_NAMES = {
  vocabulary: '词汇',
  listening: '听力',
  reading: '阅读',
  grammar: '语法',
  speaking: '口语',
  writing: '写作'
}

Page({
  data: {
    loading: false,
    loaded: false,
    error: '',
    stats: {},
    game: {},
    streak: 0,
    trend: [],
    badges: [],
    badgeUnlocked: 0,
    skills: []
  },

  onLoad() { this.loadData() },
  onShow() { if (this.data.loaded) this.loadData(true) },
  onPullDownRefresh() {
    this.loadData().finally(() => wx.stopPullDownRefresh())
  },

  reload() { this.loadData() },

  loadData(quiet = false) {
    if (!quiet) this.setData({ loading: true, error: '' })
    return api.request({ url: '/api/wx/v1/progress', silent: true })
      .then((data) => {
        const stats = data.stats || {}
        const game = data.gamification || {}

        // 趋势：把答题数映射为柱高百分比（最高值做基准，防除零）
        const trendRows = data.trend || []
        const maxTotal = Math.max(1, ...trendRows.map(t => t.total || 0))
        const trend = trendRows.map(t => ({
          day: t.day,
          label: t.label || (t.day || '').slice(5),
          total: t.total || 0,
          height: Math.max(4, Math.round((t.total || 0) * 100 / maxTotal))
        }))

        // 技能：补中文名 + 组内占比
        const skillRows = data.skills || []
        const maxXp = Math.max(1, ...skillRows.map(s => s.xp || 0))
        const skills = skillRows.map(s => ({
          skill: s.skill,
          skillName: SKILL_NAMES[s.skill] || s.skill,
          xp: s.xp || 0,
          percent: Math.round((s.xp || 0) * 100 / maxXp)
        }))

        const badges = data.badges || []
        this.setData({
          loading: false,
          loaded: true,
          stats,
          game,
          streak: data.streak || 0,
          trend,
          badges,
          badgeUnlocked: badges.filter(b => b.unlocked).length,
          skills
        })
      })
      .catch((err) => {
        this.setData({
          loading: false,
          error: err.code === 'NETWORK_ERROR' ? '网络连接失败，请检查网络后重试' : '加载失败，请稍后重试'
        })
      })
  }
})
