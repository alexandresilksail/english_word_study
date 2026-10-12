// pages/words/words.js
//
// 词库页：搜索 + A-Z 索引 + 分页。数据来自 GET /api/wx/v1/words。

const api = require('../../utils/api')

const PAGE_SIZE = 20

Page({
  data: {
    keyword: '',
    activeInitial: '',
    alphabet: ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L', 'M',
               'N', 'O', 'P', 'Q', 'R', 'S', 'T', 'U', 'V', 'W', 'X', 'Y', 'Z'],
    items: [],
    page: 1,
    pages: 1,
    total: 0,
    loading: false,
    searching: false,
    error: ''
  },

  onLoad() { this.fetchWords(1) },
  onShow() { if (this.data.items.length) this.fetchWords(this.data.page, true) },
  onPullDownRefresh() {
    this.fetchWords(1).finally(() => wx.stopPullDownRefresh())
  },

  onKeywordInput(e) {
    this.setData({ keyword: e.detail.value })
    // 清空关键词时立即回到完整列表（不用再点搜索）
    if (!e.detail.value) {
      this.fetchWords(1)
    }
  },

  onSearch() {
    if (this.data.searching) return
    this.fetchWords(1)
  },

  onInitialTap(e) {
    const initial = e.currentTarget.dataset.initial
    const next = this.data.activeInitial === initial ? '' : initial
    this.setData({ activeInitial: next, keyword: '' })
    this.fetchWords(1)
  },

  reload() { this.fetchWords(1) },

  prevPage() {
    if (this.data.page > 1) this.fetchWords(this.data.page - 1)
  },
  nextPage() {
    if (this.data.page < this.data.pages) this.fetchWords(this.data.page + 1)
  },

  goDetail(e) {
    wx.navigateTo({ url: `/pages/word-detail/word-detail?id=${e.currentTarget.dataset.id}` })
  },

  fetchWords(page, quiet = false) {
    this.setData({ loading: true, error: '' })
    return api.request({
      url: '/api/wx/v1/words',
      data: {
        q: this.data.keyword,
        initial: this.data.activeInitial,
        page,
        size: PAGE_SIZE
      },
      silent: true
    }).then((data) => {
      this.setData({
        loading: false,
        items: data.items || [],
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
