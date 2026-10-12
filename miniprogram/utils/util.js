// utils/util.js —— 通用小工具

/** 格式化时间戳为「MM-DD HH:mm」 */
function fmtTime(iso) {
  if (!iso) return ''
  const d = new Date(iso.replace('Z', '+08:00'))
  if (isNaN(d.getTime())) return ''
  const p = n => (n < 10 ? '0' + n : '' + n)
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
}

/** 百分比（防除零） */
function percent(part, total) {
  if (!total) return 0
  return Math.round(part * 100 / total)
}

/** 防止重复点击：返回的函数在 800ms 内重复调用直接忽略 */
function debounce(fn, wait = 800) {
  let last = 0
  return function (...args) {
    const now = Date.now()
    if (now - last < wait) return
    last = now
    return fn.apply(this, args)
  }
}

/**
 * 空状态配置：统一「插画 + 文案 + 操作」三件套，
 * 避免每个页面各写一套空状态导致风格不一致。
 */
const EMPTY_PRESETS = {
  words: { image: '/images/empty.png', text: '没有找到相关单词' },
  favorites: { image: '/images/favorites.png', text: '还没有收藏单词' },
  wrong: { image: '/images/wrong.png', text: '错题本是空的，继续保持！' },
  quiz: { image: '/images/quiz.png', text: '当前范围内没有可测试的单词' }
}

module.exports = { fmtTime, percent, debounce, EMPTY_PRESETS }
