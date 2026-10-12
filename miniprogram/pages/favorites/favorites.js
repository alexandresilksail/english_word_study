// pages/favorites/favorites.js
//
// 收藏夹：GET /api/wx/v1/favorites（分页 + 搜索）；
// 取消收藏直接调用 POST /words/<id>/favorite（服务端切换语义），
// 成功后从本地列表移除，保证 UI 与服务端一致。

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

  onLoad() { this.fetchFavorites(1) },
  onShow() { if (this.data.loaded) this.fetchFavorites(this.data.page, true) },
  onPullDownRefresh() {
    this.fetchFavorites(1).finally(() => wx.stopPullDownRefresh())
  },

  onKeywordInput(e) {
    this.setData({ keyword: e.detail.value })
    if (!e.detail.value) this.fetchFavorites(1)
  },
  onSearch() {
    this.fetchFavorites(1)
  },
  reload() { this.fetchFavorites(1) },

  prevPage() {
    if (this.data.page > 1) this.fetchFavorites(this.data.page - 1)
  },
  nextPage() {
    if (this.data.page < this.data.pages) this.fetchFavorites(this.data.page + 1)
  },

  goDetail(e) {
    wx.navigateTo({ url: `/pages/word-detail/word-detail?id=${e.currentTarget.dataset.id}` })
  },
  goWords() {
    wx.switchTab({ url: '/pages/words/words' })
  },

  removeFavorite: debounce(function (e) {
    const id = e.currentTarget.dataset.id
    api.request({
      url: `/api/wx/v1/words/${id}/favorite`,
      method: 'POST',
      silent: true
    }).then(() => {
      // 本地同步移除：服务端已删除，客户端必须跟上
      const items = this.data.items.filter(it => it.id !== id)
      this.setData({
        items,
        total: Math.max(0, this.data.total - 1)
      })
      wx.showToast({ title: '已取消收藏', icon: 'none' })
    }).catch(() => {
      // 请求层已提示；保持列表不变，下次进入会拉取真实数据
    })
  }),

  fetchFavorites(page, quiet = false) {
    this.setData({ loading: true, error: '' })
    return api.request({
      url: '/api/wx/v1/favorites',
      data: { q: this.data.keyword, page, size: PAGE_SIZE },
      silent: true
    }).then((data) => {
      const items = (data.items || []).map(it => Object.assign({}, it, {
        favorited_at_text: fmtTime(it.favorited_at)
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
