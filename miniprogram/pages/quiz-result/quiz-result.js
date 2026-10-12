// pages/quiz-result/quiz-result.js
//
// 测试结果页。成绩来自交卷接口（POST /quiz/finish 的真实返回值），
// 通过 URL 参数透传，不本地伪造；建议文案按正确率分档给出。

const MODE_NAMES = {
  choice: '英译汉',
  zh_en: '汉译英',
  listen: '听音选词',
  spell: '拼写'
}

Page({
  data: {
    total: 0,
    score: 0,
    percent: 0,
    duration: 0,
    durationText: '',
    mode: 'choice',
    modeName: '英译汉',
    comment: 'pass',
    commentText: '',
    commentClass: 'comment-pass',
    tips: []
  },

  onLoad(options) {
    const total = parseInt(options.total || '0', 10) || 0
    const score = parseInt(options.score || '0', 10) || 0
    const percent = parseInt(options.percent || '0', 10) || 0
    const duration = parseInt(options.duration || '0', 10) || 0
    const mode = options.mode || 'choice'
    const comment = options.comment || 'pass'

    const commentTexts = {
      outstanding: '太棒了，几乎全对！',
      good: '表现不错，继续保持',
      pass: '勉强及格，还需练习',
      retry: '错误较多，建议复习后再测'
    }
    const commentClasses = {
      outstanding: 'comment-outstanding',
      good: 'comment-good',
      pass: 'comment-pass',
      retry: 'comment-retry'
    }

    this.setData({
      total,
      score,
      percent,
      duration,
      durationText: this._fmtDuration(duration),
      mode,
      modeName: MODE_NAMES[mode] || mode,
      comment,
      commentText: commentTexts[comment] || commentTexts.pass,
      commentClass: commentClasses[comment] || 'comment-pass',
      tips: this._buildTips(percent)
    })
  },

  _fmtDuration(sec) {
    if (sec < 60) return `${sec} 秒`
    return `${Math.floor(sec / 60)} 分 ${sec % 60} 秒`
  },

  _buildTips(percent) {
    const tips = []
    if (percent >= 90) {
      tips.push('正确率很高，可以挑战更大题量或更难的单词')
      tips.push('试试「汉译英」和「拼写」模式，检验输出能力')
    } else if (percent >= 70) {
      tips.push('整体掌握不错，错题本里的单词建议重点回顾')
      tips.push('可以针对「未学」范围多练习，扩大词汇量')
    } else if (percent >= 50) {
      tips.push('建议先去单词详情页学习错题，再回来测试')
      tips.push('把每日目标调低一些，先保证质量再追求数量')
    } else {
      tips.push('错误较多，建议从「未学」范围小量开始，循序渐进')
      tips.push('错题已自动收入错题本，消灭错题后再测一轮')
    }
    tips.push('坚持每天学习，连续打卡天数会不断累积')
    return tips
  },

  retry() {
    wx.navigateBack()
  },

  goWrong() {
    wx.navigateTo({ url: '/pages/wrong/wrong' })
  },

  goHome() {
    wx.switchTab({ url: '/pages/home/home' })
  }
})
