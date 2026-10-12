// pages/profile/profile.js
//
// 个人中心：用户信息（GET /auth/me）、每日目标（POST /me/goal）、
// 绑定邮箱（POST /auth/bind-email，验证控制权后才关联）、
// 解绑微信（POST /auth/unbind，同样需再次验证）、登出（撤销令牌）。

const api = require('../../utils/api')
const auth = require('../../utils/auth')
const { debounce } = require('../../utils/util')

Page({
  data: {
    user: {},
    goalOptions: [10, 20, 30, 50, 100],
    customGoal: '',

    // 绑定邮箱
    showBind: false,
    binding: false,
    sendingCode: false,
    useCode: false,
    bindEmail: '',
    bindPassword: '',
    bindCode: '',
    devCode: '',

    // 解绑微信
    showUnbind: false,
    unbinding: false,
    unbindUseCode: false,
    unbindPassword: '',
    unbindCode: ''
  },

  onLoad() { this.loadMe() },
  onShow() { if (auth.getAccessToken()) this.loadMe(true) },

  // ---------- 用户信息 ----------
  loadMe(quiet = false) {
    return api.request({ url: '/api/wx/v1/auth/me', silent: true })
      .then((data) => {
        auth.setUser(data.user)
        this.setData({ user: data.user || {} })
      })
      .catch(() => {
        if (!quiet) wx.showToast({ title: '加载用户信息失败', icon: 'none' })
      })
  },

  // ---------- 每日目标 ----------
  onGoalInput(e) { this.setData({ customGoal: e.detail.value }) },
  setGoal(e) { this.applyGoal(e.currentTarget.dataset.goal) },
  submitCustomGoal() {
    const goal = parseInt(this.data.customGoal || '0', 10)
    if (!goal || goal < 1 || goal > 500) {
      wx.showToast({ title: '请输入 1-500 之间的数字', icon: 'none' })
      return
    }
    this.applyGoal(goal)
  },
  applyGoal(goal) {
    api.request({
      url: '/api/wx/v1/me/goal',
      method: 'POST',
      data: { goal },
      silent: true
    }).then((data) => {
      this.setData({ 'user.daily_goal': data.goal, customGoal: '' })
      wx.showToast({ title: `每日目标已设为 ${data.goal} 词`, icon: 'none' })
    }).catch(() => {
      // 请求层已提示
    })
  },

  // ---------- 绑定邮箱 ----------
  openBind() {
    this.setData({
      showBind: true,
      bindEmail: '',
      bindPassword: '',
      bindCode: '',
      devCode: '',
      useCode: false
    })
  },
  onBindEmailInput(e) { this.setData({ bindEmail: e.detail.value.trim() }) },
  onBindPasswordInput(e) { this.setData({ bindPassword: e.detail.value }) },
  onBindCodeInput(e) { this.setData({ bindCode: e.detail.value.trim() }) },
  toggleUseCode() { this.setData({ useCode: !this.data.useCode, devCode: '' }) },

  sendCode() {
    const email = this.data.bindEmail
    if (!email || email.indexOf('@') < 0) {
      wx.showToast({ title: '请先填写邮箱', icon: 'none' })
      return
    }
    this.setData({ sendingCode: true })
    api.request({
      url: '/api/wx/v1/auth/send-code',
      method: 'POST',
      data: { email },
      silent: true
    }).then((data) => {
      this.setData({ sendingCode: false, useCode: true, devCode: data.dev_code || '' })
      wx.showToast({ title: data.message || '验证码已发送', icon: 'none' })
    }).catch(() => {
      this.setData({ sendingCode: false })
    })
  },

  submitBind() {
    if (this.data.binding) return
    const { bindEmail, bindPassword, bindCode } = this.data
    if (!bindEmail || bindEmail.indexOf('@') < 0) {
      wx.showToast({ title: '请填写有效邮箱', icon: 'none' })
      return
    }
    if (!bindPassword && !bindCode) {
      wx.showToast({ title: '请提供密码或验证码', icon: 'none' })
      return
    }
    this.setData({ binding: true })
    const payload = { email: bindEmail }
    if (bindCode) payload.code = bindCode
    else payload.password = bindPassword

    api.request({
      url: '/api/wx/v1/auth/bind-email',
      method: 'POST',
      data: payload,
      silent: true
    }).then((data) => {
      this.setData({ binding: false, showBind: false })
      // 绑定成功会换发新令牌（身份已切换到目标账户），必须保存
      auth.setTokens(data.tokens)
      auth.setUser(data.user)
      this.setData({ user: data.user || {} })
      wx.showToast({
        title: data.merged ? '绑定成功，学习记录已合并' : '绑定成功',
        icon: 'success'
      })
    }).catch(() => {
      this.setData({ binding: false })
    })
  },

  // ---------- 解绑微信 ----------
  openUnbind() {
    this.setData({
      showUnbind: true,
      unbindPassword: '',
      unbindCode: '',
      unbindUseCode: false
    })
  },
  onUnbindPasswordInput(e) { this.setData({ unbindPassword: e.detail.value }) },
  onUnbindCodeInput(e) { this.setData({ unbindCode: e.detail.value.trim() }) },
  toggleUnbindUseCode() {
    this.setData({ unbindUseCode: !this.data.unbindUseCode })
  },

  sendUnbindCode() {
    const email = this.data.user.email
    if (!email) {
      wx.showToast({ title: '当前账户未绑定邮箱，无法用验证码解绑', icon: 'none' })
      return
    }
    this.setData({ sendingCode: true })
    api.request({
      url: '/api/wx/v1/auth/send-code',
      method: 'POST',
      data: { email },
      silent: true
    }).then((data) => {
      this.setData({ sendingCode: false, unbindUseCode: true })
      wx.showToast({ title: data.message || '验证码已发送', icon: 'none' })
    }).catch(() => {
      this.setData({ sendingCode: false })
    })
  },

  submitUnbind() {
    if (this.data.unbinding) return
    const { unbindPassword, unbindCode, user } = this.data
    // 占位账户（@wx.local）没有密码可验证，服务端允许直接解绑；
    // 真实账户必须提供密码或验证码
    const isPlaceholder = !user.email_bound
    if (!isPlaceholder && !unbindPassword && !unbindCode) {
      wx.showToast({ title: '请提供密码或验证码', icon: 'none' })
      return
    }
    this.setData({ unbinding: true })
    const payload = {}
    if (unbindCode) payload.code = unbindCode
    else if (unbindPassword) payload.password = unbindPassword

    api.request({
      url: '/api/wx/v1/auth/unbind',
      method: 'POST',
      data: payload,
      silent: true
    }).then(() => {
      this.setData({ unbinding: false, showUnbind: false })
      wx.showToast({ title: '已解绑', icon: 'success' })
      // 解绑后服务端已撤销当前令牌，本地也必须立即清理
      setTimeout(() => {
        api.logout().then(() => {
          wx.reLaunch({ url: '/pages/login/login' })
        })
      }, 800)
    }).catch(() => {
      this.setData({ unbinding: false })
    })
  },

  // ---------- 导航 ----------
  goFavorites() { wx.navigateTo({ url: '/pages/favorites/favorites' }) },
  goWrong() { wx.navigateTo({ url: '/pages/wrong/wrong' }) },
  closeModals() {
    this.setData({ showBind: false, showUnbind: false })
  },
  noop() {},

  // ---------- 登出 ----------
  logout() {
    wx.showModal({
      title: '退出登录',
      content: '退出后需重新登录，学习数据不会丢失',
      success: (res) => {
        if (!res.confirm) return
        api.logout().then(() => {
          wx.reLaunch({ url: '/pages/login/login' })
        })
      }
    })
  }
})
