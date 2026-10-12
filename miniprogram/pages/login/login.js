// pages/login/login.js
//
// 两条登录路径：
// 1. 微信一键登录：wx.login() → code → 服务端 code2Session → 令牌；
// 2. 绑定已有账户：先微信登录拿到身份，再验证邮箱控制权后把 openid 挂到该账户。
//
// 注意顺序：绑定必须先完成微信登录（否则服务端不知道「哪个 openid 要绑」）。

const api = require('../../utils/api')
const auth = require('../../utils/auth')

Page({
  data: {
    logging: false,
    binding: false,
    sendingCode: false,
    useCode: false,
    bindEmail: '',
    bindPassword: '',
    bindCode: '',
    devCode: ''
  },

  onEmailInput(e) { this.setData({ bindEmail: e.detail.value.trim() }) },
  onPasswordInput(e) { this.setData({ bindPassword: e.detail.value }) },
  onCodeInput(e) { this.setData({ bindCode: e.detail.value.trim() }) },

  toggleUseCode() { this.setData({ useCode: !this.data.useCode, devCode: '' }) },

  /** 微信一键登录 */
  onWxLogin() {
    if (this.data.logging) return
    this.setData({ logging: true })
    wx.login({
      success: (res) => {
        if (!res.code) {
          this.setData({ logging: false })
          wx.showToast({ title: '微信登录失败，请重试', icon: 'none' })
          return
        }
        api.request({
          url: '/api/wx/v1/auth/wx-login',
          method: 'POST',
          data: { code: res.code },
          silent: true
        }).then((data) => {
          this.setData({ logging: false })
          if (!data.tokens) {
            // 服务端关闭了自动注册，必须走绑定流程
            wx.showToast({ title: '请先绑定已有账户', icon: 'none' })
            return
          }
          auth.setTokens(data.tokens)
          auth.setUser(data.user)
          getApp().globalData.loginState = 'logged'
          getApp().globalData.userInfo = data.user
          if (data.is_new_account) {
            wx.showToast({ title: '欢迎加入！已为你创建账户', icon: 'none' })
          } else {
            wx.showToast({ title: '登录成功', icon: 'none' })
          }
          wx.reLaunch({ url: '/pages/home/home' })
        }).catch(() => {
          this.setData({ logging: false })
        })
      },
      fail: () => {
        this.setData({ logging: false })
        wx.showToast({ title: '微信登录失败，请重试', icon: 'none' })
      }
    })
  },

  /** 发送绑定用邮箱验证码 */
  onSendCode() {
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
      this.setData({
        sendingCode: false,
        useCode: true,
        devCode: data.dev_code || ''
      })
      wx.showToast({ title: data.message || '验证码已发送', icon: 'none' })
    }).catch(() => {
      this.setData({ sendingCode: false })
    })
  },

  /** 绑定已有账户 */
  onBind() {
    if (this.data.binding) return
    const { bindEmail, bindPassword, bindCode } = this.data
    if (!bindEmail) {
      wx.showToast({ title: '请填写邮箱', icon: 'none' })
      return
    }
    if (!bindPassword && !bindCode) {
      wx.showToast({ title: '请提供密码或验证码', icon: 'none' })
      return
    }

    this.setData({ binding: true })

    // 第一步：先完成微信登录，让服务端知道要绑哪个 openid
    wx.login({
      success: (loginRes) => {
        if (!loginRes.code) {
          this.setData({ binding: false })
          wx.showToast({ title: '微信登录失败，请重试', icon: 'none' })
          return
        }
        api.request({
          url: '/api/wx/v1/auth/wx-login',
          method: 'POST',
          data: { code: loginRes.code },
          silent: true
        }).then((loginData) => {
          if (!loginData.tokens) {
            // 自动注册被关闭：仍需先有微信身份才能绑，服务端此时不认。
            // 这里直接提示改用一键登录入口由管理员开启自动注册。
            this.setData({ binding: false })
            wx.showToast({ title: '请使用微信一键登录', icon: 'none' })
            return
          }
          auth.setTokens(loginData.tokens)

          // 第二步：验证邮箱控制权并绑定
          const payload = { email: bindEmail }
          if (bindCode) payload.code = bindCode
          else payload.password = bindPassword

          return api.request({
            url: '/api/wx/v1/auth/bind-email',
            method: 'POST',
            data: payload,
            silent: true
          })
        }).then((bindData) => {
          this.setData({ binding: false })
          if (!bindData) return   // 上一步已 return
          auth.setTokens(bindData.tokens)
          auth.setUser(bindData.user)
          getApp().globalData.loginState = 'logged'
          getApp().globalData.userInfo = bindData.user
          wx.showToast({
            title: bindData.merged ? '绑定成功，学习记录已合并' : '绑定成功',
            icon: 'success'
          })
          wx.reLaunch({ url: '/pages/home/home' })
        }).catch(() => {
          this.setData({ binding: false })
        })
      },
      fail: () => {
        this.setData({ binding: false })
        wx.showToast({ title: '微信登录失败，请重试', icon: 'none' })
      }
    })
  }
})
