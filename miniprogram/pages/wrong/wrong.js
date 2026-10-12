// pages/wrong/wrong.js
//
// 错题本：GET /api/wx/v1/wrong（分页 + 搜索）；
// 移除走 POST /wrong/<id>/remove，成功后本地同步删除。
// 另提供「练这些」入口：跳到测试中心按 wrong 范围出题
// （范围选择由测试中心记住本次意图，见 pages/quiz）。

const api = require('../../utils/api')
const { fmtTime, debounce } = require('../../utils/util')

const PAGE_SIZE = 20

Page({
  data: {
    keyword: '',
    items: [],
    page: 1,
    pages: 1,
    total: 0,
    loading: false,
    loaded: false,
    error: ''
  },

  onLoad() { this.fetchWrong(1) },
  onShow() { if (this.data.loaded) this.fetchWrong(this.data.page, true) },
  onPullDownRefresh() {
    this.fetchWrong(1).finally(() => wx.stopPullDownRefresh())
  },

  onKeywordInput(e) {
    this.setData({ keyword: e.detail.value })
    if (!e.detail.value) this.fetchWrong(1)
  },
  onSearch() { this.fetchWrong(1) },
  reload() { this.fetchWrong(1) },

  prevPage() {
    if (this.data.page > 1) this.fetchWrong(this.data.page - 1)
  },
  nextPage() {
    if (this.data.page < this.data.pages) this.fetchWrong(this.data.page + 1)
  },

  goDetail(e) {
    wx.navigateTo({ url: `/pages/word-detail/word-detail?id=${e.currentTarget.dataset.id}` })
  },
  goQuiz() {
    wx.switchTab({ url: '/pages/quiz/quiz' })
  },
  goQuizWrong() {
    // 带上意图：测试中心读取后自动选中「错题」范围并开始
    wx.setStorageSync('quiz_pending_scope', 'wrong')
    wx.switchTab({ url: '/pages/quiz/quiz' })
  },

  removeWrong: debounce(function (e) {
    const id = e.currentTarget.dataset.id
    api.request({
      url: `/api/wx/v1/wrong/${id}/remove`,
      method: 'POST',
      silent: true
    }).then(() => {
      const items = this.data.items.filter(it => it.id !== id)
      this.setData({
        items,
        total: Math.max(0, this.data.total - 1)
      })
      wx.showToast({ title: '已移除', icon: 'none' })
    }).catch(() => {
      // 服务端失败时保持现状，下次进入重新拉取
    })
  }),

  fetchWrong(page, quiet = false) {
    this.setData({ loading: true, error: '' })
    return api.request({
      url: '/api/wx/v1/wrong',
      data: { q: this.data.keyword, page, size: PAGE_SIZE },
      silent: true
    }).then((data) => {
      const items = (data.items || []).map(it => Object.assign({}, it, {
        last_wrong_at_text: fmtTime(it.last_wrong_at)
      }))
      this.setData({
        loading: false,
        loaded: true,
        items,
        page: data.page || page,
        pages: data.pages || 1,
        total: data.total || 0
      })
    }).catch((err) => {
      this.setData({
        loading: false,
        error: err.code === 'NETWORK_ERROR' ? '网络连接失败，请检查网络后重试' : '加载失败，请稍后重试'
      })
    })
  }
})
