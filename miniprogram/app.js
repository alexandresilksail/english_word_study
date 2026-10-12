// app.js —— 小程序入口
//
// 职责：
// 1. 全局配置（后端 Base URL、Token 存储键名）；
// 2. 启动时恢复登录态（从 Storage 读 Token，校验是否过期）；
// 3. 提供全局 toast / 登录态失效的兜底处理。
//
// 安全约定：Access / Refresh Token 只存 wx.Storage（本地），
// 绝不写入 URL、绝不打印到日志。

const auth = require('./utils/auth')

App({
  globalData: {
    // 后端地址：开发期可用「不校验合法域名」跑 http；
    // 上线前必须改为已备案的 HTTPS 域名，并在小程序后台加入 request 合法域名。
    baseUrl: 'http://127.0.0.1:5000',
    userInfo: null,
    // 登录态：'unknown' 未探测 / 'guest' 未登录 / 'logged' 已登录
    loginState: 'unknown'
  },

  onLaunch() {
    const token = auth.getAccessToken()
    if (!token) {
      this.globalData.loginState = 'guest'
      return
    }
    // 本地先做一次过期判断，避免无谓请求
    if (auth.isTokenExpired(token)) {
      // 过期不急着清：可能还能用 refresh 换回，留给具体页面按需刷新
      this.globalData.loginState = 'guest'
      return
    }
    this.globalData.loginState = 'logged'
  },

  /** 全局轻提示（统一风格，避免各页面各写一套） */
  toast(title, icon = 'none') {
    wx.showToast({ title, icon, duration: 1800 })
  },

  /** 登录态失效的统一处理：清本地 Token 并跳登录页 */
  handleAuthExpired() {
    auth.clearTokens()
    this.globalData.loginState = 'guest'
    const pages = getCurrentPages()
    const current = pages[pages.length - 1]
    if (current && current.route !== 'pages/login/login') {
      wx.reLaunch({ url: '/pages/login/login' })
    }
  }
})
