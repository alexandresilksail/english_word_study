// utils/auth.js —— 小程序端 Token 管理
//
// 只负责「存 / 取 / 判过期 / 清」，不发起业务请求（那在 api.js）。
// Token 存 wx.Storage：小程序本地存储，随小程序卸载而清除。

const KEY_ACCESS = 'wx_access_token'
const KEY_REFRESH = 'wx_refresh_token'
const KEY_USER = 'wx_user_info'

function setTokens(tokens) {
  if (!tokens) return
  wx.setStorageSync(KEY_ACCESS, tokens.access_token || '')
  wx.setStorageSync(KEY_REFRESH, tokens.refresh_token || '')
}

function getAccessToken() {
  return wx.getStorageSync(KEY_ACCESS) || ''
}

function getRefreshToken() {
  return wx.getStorageSync(KEY_REFRESH) || ''
}

function clearTokens() {
  wx.removeStorageSync(KEY_ACCESS)
  wx.removeStorageSync(KEY_REFRESH)
  wx.removeStorageSync(KEY_USER)
}

function setUser(user) {
  wx.setStorageSync(KEY_USER, user || null)
}

function getUser() {
  return wx.getStorageSync(KEY_USER) || null
}

/**
 * base64url → 字符串（小程序没有 atob，用 wx.base64ToArrayBuffer）。
 */
function b64urlDecode(input) {
  let b64 = input.replace(/-/g, '+').replace(/_/g, '/')
  while (b64.length % 4) b64 += '='
  const buf = wx.base64ToArrayBuffer(b64)
  const bytes = new Uint8Array(buf)
  let out = ''
  for (let i = 0; i < bytes.length; i++) out += String.fromCharCode(bytes[i])
  // 手动做 UTF-8 解码，避免中文/特殊字符乱码
  try {
    return decodeURIComponent(out.split('').map(c =>
      '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2)).join(''))
  } catch (e) {
    return out
  }
}

/**
 * 解析 JWT 的 exp 并判断是否过期（提前 30 秒视为过期，留出网络耗时）。
 * 只做本地预判，真正的有效性由服务端验签决定。
 */
function isTokenExpired(token, skewSec = 30) {
  if (!token) return true
  const parts = token.split('.')
  if (parts.length !== 3) return true
  try {
    const payload = JSON.parse(b64urlDecode(parts[1]))
    if (!payload || typeof payload.exp !== 'number') return true
    const now = Math.floor(Date.now() / 1000)
    return payload.exp - skewSec <= now
  } catch (e) {
    return true
  }
}

module.exports = {
  KEY_ACCESS, KEY_REFRESH, KEY_USER,
  setTokens, getAccessToken, getRefreshToken, clearTokens,
  setUser, getUser, isTokenExpired
}
