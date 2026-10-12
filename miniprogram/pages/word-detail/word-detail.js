// pages/word-detail/word-detail.js
//
// 单词详情：发音（真实音频地址播放）+ 收藏 + 跳转测试。

const api = require('../../utils/api')
const { debounce } = require('../../utils/util')

Page({
  data: {
    loading: true,
    word: null,
    wordId: 0,
    playing: false,
    audioError: '',
    favPending: false
  },

  onLoad(options) {
    this.setData({ wordId: parseInt(options.id || '0', 10) })
    this.loadWord()
  },

  loadWord() {
    this.setData({ loading: true, audioError: '' })
    return api.request({
      url: `/api/wx/v1/words/${this.data.wordId}`,
      silent: true
    }).then((word) => {
      this.setData({ loading: false, word })
    }).catch(() => {
      this.setData({ loading: false, word: null })
    })
  },

  /** 播放发音：用后端返回的 audio_url（真实本地音频，不调在线 TTS） */
  playAudio() {
    const url = this.data.word && this.data.word.audio_url
    if (!url) {
      this.setData({ audioError: '该单词暂无音频文件' })
      return
    }
    this.setData({ playing: true, audioError: '' })
    const inner = wx.createInnerAudioContext()
    inner.src = url.startsWith('http') ? url : api.BASE_URL + url
    inner.onPlay(() => this.setData({ playing: true }))
    inner.onEnded(() => { this.setData({ playing: false }); inner.destroy() })
    inner.onError((res) => {
      this.setData({ playing: false, audioError: '音频播放失败，请稍后重试' })
      inner.destroy()
    })
    inner.play()
  },

  toggleFavorite: debounce(function () {
    if (this.data.favPending) return
    this.setData({ favPending: true })
    api.request({
      url: `/api/wx/v1/words/${this.data.wordId}/favorite`,
      method: 'POST',
      silent: true
    }).then((data) => {
      this.setData({
        favPending: false,
        'word.fav': data.favorited
      })
      wx.showToast({
        title: data.favorited ? '已加入收藏' : '已取消收藏',
        icon: 'none'
      })
    }).catch(() => {
      this.setData({ favPending: false })
    })
  }),

  goQuizThisWord() {
    wx.switchTab({ url: '/pages/quiz/quiz' })
  },

  goBack() { wx.navigateBack() }
})
