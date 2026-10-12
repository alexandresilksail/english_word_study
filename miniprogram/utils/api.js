// utils/api.js —— 小程序请求封装
//
// 解决四类真实问题，而不是只会 wx.request 包一层：
// 1. Token 自动附加：Authorization: Bearer <access_token>；
// 2. 401 自动刷新：access 过期时用 refresh 换新，并用**单飞**（single-flight）
//    合并并发刷新，避免页面同时发起多个 refresh 互相把对方轮换掉；
// 3. 统一错误映射：网络错误 / 超时 / 服务端错误码 → 可读文案；
// 4. 加载态与重复点击：调用方只管拿结果，UI 状态由各页面按约定处理。

const auth = require('./utils/auth')

const BASE_URL = getApp().globalData.baseUrl
const TIMEOUT = 15000

// 单飞：同一时刻只允许一个 refresh 在飞
let refreshing = null

/** 原始请求（不做重试） */
function rawRequest(options) {
  return new Promise((resolve, reject) => {
    wx.request({
      url: BASE_URL + options.url,
      method: options.method || 'GET',
      data: options.data || {},
      header: Object.assign({}, options.header),
      timeout: TIMEOUT,
      success(res) {
        resolve(res)
      },
      fail(err) {
        reject(err)
      }
    })
  })
}

/** 带 Authorization 头 */
function withAuth(header) {
  const token = auth.getAccessToken()
  if (token) header.Authorization = 'Bearer ' + token
  return header
}

/** 调 refresh 接口换新令牌（单飞） */
function doRefresh() {
  if (refreshing) return refreshing
  const refreshToken = auth.getRefreshToken()
  if (!refreshToken) {
    refreshing = Promise.reject(new Error('NO_REFRESH'))
    return refreshing
  }
  refreshing = rawRequest({
    url: '/api/wx/v1/auth/refresh',
    method: 'POST',
    data: { refresh_token: refreshToken },
    header: { 'Content-Type': 'application/json' }
  }).then(res => {
    if (res.statusCode === 200 && res.data && res.data.ok) {
      auth.setTokens(res.data.data.tokens)
      return res.data.data.tokens
    }
    throw new Error('REFRESH_FAILED')
  }).finally(() => {
    refreshing = null
  })
  return refreshing
}

/**
 * 统一请求入口。
 *
 * @param {Object} options {url, method, data, header, silent}
 * @param {boolean} options.silent true 时不自动弹错误提示（由调用方处理）
 * @returns {Promise<Object>} 直接 resolve 业务 data（即响应的 data.data）
 */
function request(options) {
  const header = withAuth(Object.assign({ 'Content-Type': 'application/json' },
    options.header || {}))

  return rawRequest(Object.assign({}, options, { header }))
    .then(res => handleResponse(res, options))
    .catch(err => {
      // 网络层失败（超时 / 断网 / DNS）
      if (!options.silent) {
        wx.showToast({ title: '网络异常，请稍后重试', icon: 'none' })
      }
      throw Object.assign(new Error('NETWORK_ERROR'), { code: 'NETWORK_ERROR' })
    })
}

function handleResponse(res, options) {
  const body = res.data || {}

  // access 过期 → 刷新后重放一次
  if (res.statusCode === 401 && body.error && body.error.code === 'token_expired') {
    return doRefresh()
      .then(() => {
        const header = withAuth({ 'Content-Type': 'application/json' })
        return rawRequest(Object.assign({}, options, { header }))
          .then(res2 => handleResponse(res2, options))
      })
      .catch(() => {
        // refresh 也失败：登录态彻底失效
        const app = getApp()
        app.handleAuthExpired()
        throw Object.assign(new Error('AUTH_EXPIRED'), { code: 'AUTH_EXPIRED' })
      })
  }

  // 其它认证失败（伪造 / 已撤销 / 未登录）→ 回登录页
  if (res.statusCode === 401) {
    const app = getApp()
    app.handleAuthExpired()
    if (!options.silent) {
      wx.showToast({ title: body.error?.message || '请先登录', icon: 'none' })
    }
    throw Object.assign(new Error('UNAUTHORIZED'), { code: body.error?.code || 'unauthorized' })
  }

  if (res.statusCode >= 500) {
    if (!options.silent) {
      wx.showToast({ title: '服务器开小差了，请稍后再试', icon: 'none' })
    }
    throw Object.assign(new Error('SERVER_ERROR'), { code: 'SERVER_ERROR' })
  }

  if (!body.ok) {
    const msg = (body.error && body.error.message) || '请求失败'
    if (!options.silent) {
      wx.showToast({ title: msg, icon: 'none' })
    }
    throw Object.assign(new Error(msg), { code: (body.error && body.error.code) || 'error' })
  }

  return body.data
}

/** 微信登录：拿 code 并换取令牌 */
function wxLogin(profile) {
  return new Promise((resolve, reject) => {
    wx.login({
      success(loginRes) {
        if (!loginRes.code) {
          reject(new Error('wx.login 未返回 code'))
          return
        }
        rawRequest({
          url: '/api/wx/v1/auth/wx-login',
          method: 'POST',
          data: Object.assign({ code: loginRes.code }, profile || {}),
          header: { 'Content-Type': 'application/json' }
        }).then(res => {
          const body = res.data || {}
          if (res.statusCode === 200 && body.ok && body.data.tokens) {
            auth.setTokens(body.data.tokens)
            auth.setUser(body.data.user)
            resolve(body.data)
          } else {
            const app = getApp()
            app.globalData.loginState = 'guest'
            reject(new Error((body.error && body.error.message) || '微信登录失败'))
          }
        }).catch(reject)
      },
      fail: reject
    })
  })
}

/** 登出：撤销服务端令牌并清本地 */
function logout() {
  const refreshToken = auth.getRefreshToken()
  return rawRequest({
    url: '/api/wx/v1/auth/logout',
    method: 'POST',
    data: { refresh_token: refreshToken },
    header: withAuth({ 'Content-Type': 'application/json' })
  }).then(() => {
    auth.clearTokens()
    getApp().globalData.loginState = 'guest'
  }).catch(() => {
    // 服务端失败也清本地：本地状态必须立即失效
    auth.clearTokens()
    getApp().globalData.loginState = 'guest'
  })
}

module.exports = { request, wxLogin, logout, doRefresh, BASE_URL, TIMEOUT }
