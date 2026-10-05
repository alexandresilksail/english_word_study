"""双语（中文 + English）文案层。

设计目标
--------
1. **统一来源**：所有界面文字集中在本文件的 STRINGS 中，避免中英文散落各处造成混乱。
2. **结构固定**：每条文案固定为 ``(中文, English)`` 二元组，渲染时默认同时展示（英文在前）。
3. **面向未来切换**：模板里用 ``{{ bi('key') }}`` 输出双语结构，
   用 ``{{ t('key') }}`` 输出单一语言。只需给 <html> 设置 ``data-lang="zh" / "en"``
   即可由 CSS 隐藏另一种语言，无需改动模板 —— 为真正的语言切换预留好结构。

渲染出的 HTML 结构：::

    <span class="bi"><span class="bi-en">Dashboard</span><span class="bi-zh">学习中心</span></span>

CSS 负责在两段之间加 “/” 分隔，并在切换语言时隐藏其中一段。
"""
from __future__ import annotations

from markupsafe import Markup, escape

# 每条文案固定为 (中文, English)
STRINGS: dict[str, tuple[str, str]] = {
    # ---------------------------------------------------------------- 品牌
    "brand.name": ("英语单词学习", "English Word Study"),
    "brand.tagline": ("系统化掌握 2000 个常用词", "Master 2000 Essential Words"),

    # ---------------------------------------------------------------- 导航
    "nav.home": ("首页", "Home"),
    "nav.dashboard": ("学习中心", "Dashboard"),
    "nav.learn": ("今日学习", "Learn"),
    "nav.browse": ("单词本", "Word List"),
    "nav.quiz": ("测试", "Quiz"),
    "nav.wrong": ("错题本", "Review"),
    "nav.favorites": ("我的收藏", "Favorites"),
    "nav.progress": ("学习进度", "Progress"),
    "nav.profile": ("个人中心", "My Account"),
    "nav.admin": ("管理后台", "Admin"),
    "nav.login": ("登录", "Login"),
    "nav.register": ("注册", "Register"),
    "nav.logout": ("退出登录", "Logout"),

    # ---------------------------------------------------------------- 通用动作
    "act.start": ("开始学习", "Start Learning"),
    "act.continue": ("继续学习", "Continue"),
    "act.take_quiz": ("做一组测试", "Take a Quiz"),
    "act.review": ("复习", "Review"),
    "act.view_detail": ("查看详情", "View Detail"),
    "act.submit": ("提交", "Submit"),
    "act.save": ("保存", "Save"),
    "act.cancel": ("取消", "Cancel"),
    "act.back": ("返回", "Back"),
    "act.next": ("下一题", "Next"),
    "act.finish": ("查看成绩", "See Results"),
    "act.again": ("再来一组", "Try Again"),
    "act.play": ("播放发音", "Play Audio"),
    "act.favorite": ("收藏", "Save"),
    "act.unfavorite": ("取消收藏", "Remove"),
    "act.mastered": ("标记已掌握", "Mark as Learned"),
    "act.unmastered": ("重置状态", "Reset"),
    "act.remove_wrong": ("移出错题本", "Remove"),
    "act.search": ("搜索", "Search"),
    "act.filter": ("筛选", "Filter"),
    "act.all": ("全部", "All"),

    # ---------------------------------------------------------------- 单词卡结构
    "word.word": ("单词", "Word"),
    "word.pronunciation": ("发音", "Pronunciation"),
    "word.meaning": ("释义", "Meaning"),
    "word.example": ("例句", "Example"),
    "word.example_translation": ("例句翻译", "Translation"),
    "word.action": ("学习操作", "Learning Action"),
    "word.part_of_speech": ("词性", "Part of Speech"),

    # ---------------------------------------------------------------- 状态
    "status.new": ("未学习", "New"),
    "status.learning": ("学习中", "Learning"),
    "status.mastered": ("已掌握", "Learned"),
    "status.all": ("全部单词", "All Words"),

    # ---------------------------------------------------------------- 认证
    "auth.login_title": ("欢迎回来", "Welcome Back"),
    "auth.login_sub": ("登录后继续你的学习计划，进度与错题都在等你。",
                       "Sign in to continue your study plan — progress and mistakes are saved."),
    "auth.email": ("邮箱", "Email"),
    "auth.email_placeholder": ("you@example.com", "you@example.com"),
    "auth.password": ("密码", "Password"),
    "auth.password_placeholder": ("请输入密码", "Enter your password"),
    "auth.confirm_password": ("确认密码", "Confirm Password"),
    "auth.username": ("用户名", "Username"),
    "auth.username_placeholder": ("显示在页面顶部", "Shown at the top of the page"),
    "auth.remember": ("记住我（14 天免登录）", "Remember me for 14 days"),
    "auth.forgot": ("忘记密码？", "Forgot password?"),
    "auth.submit_login": ("登录", "Sign In"),
    "auth.no_account": ("还没有账号？", "New here?"),
    "auth.register_link": ("免费注册", "Create an account"),
    "auth.register_title": ("创建学习账号", "Create Your Account"),
    "auth.register_sub": ("一分钟完成注册，立即开始系统化背单词。",
                          "Register in a minute and start building your vocabulary."),
    "auth.submit_register": ("注册并开始学习", "Sign Up & Start"),
    "auth.have_account": ("已有账号？", "Already registered?"),
    "auth.login_link": ("直接登录", "Sign in"),
    "auth.password_hint": ("至少 8 位，需同时包含字母和数字", "At least 8 characters, with letters and numbers"),
    "auth.secure_note": ("密码采用加盐哈希保存，我们无法还原你的明文密码。",
                         "Passwords are stored as salted hashes — your plain password is never recoverable."),
    "auth.reset_title": ("重置密码", "Reset Password"),
    "auth.reset_sub": ("输入注册邮箱，我们会发送重置链接。", "Enter your email and we'll send a reset link."),
    "auth.reset_submit": ("发送重置链接", "Send Reset Link"),
    "auth.reset_sent": ("重置链接已发送（若邮箱已注册）。", "If that email is registered, a reset link has been sent."),
    "auth.set_password": ("设置新密码", "Set a New Password"),
    "auth.back_login": ("返回登录", "Back to Sign In"),
    "auth.verify_title": ("验证邮箱", "Verify Your Email"),
    "auth.verify_pending": ("我们已向你的邮箱发送验证链接，请查收。",
                            "We sent a verification link to your email address."),
    "auth.verified": ("邮箱已验证", "Verified"),
    "auth.unverified": ("邮箱未验证", "Unverified"),
    "auth.resend": ("重新发送验证邮件", "Resend Verification Email"),

    # ---- 邮箱验证码（无密码登录 / 注册）----
    "auth.tab_code": ("邮箱验证码", "Email Code"),
    "auth.tab_password": ("密码登录", "Password"),
    "auth.code_title": ("邮箱验证码登录", "Sign in with Email Code"),
    "auth.code_sub": ("输入邮箱，我们会发送 6 位验证码，无需密码。",
                      "Enter your email and we'll send a 6-digit code. No password needed."),
    "auth.code": ("验证码", "Verification Code"),
    "auth.code_placeholder": ("6 位数字验证码", "6-digit code"),
    "auth.send_code": ("发送验证码", "Send Code"),
    "auth.sending": ("发送中…", "Sending…"),
    "auth.resend_code": ("重新发送", "Resend"),
    "auth.code_sent": ("验证码已发送到你的邮箱，请查收。", "We sent a code to your inbox."),
    "auth.code_dev_hint": ("（未配置邮件服务）本次验证码：", "(Email not configured) Your code: "),
    "auth.code_ttl": ("验证码 10 分钟内有效", "The code is valid for 10 minutes"),
    "auth.submit_code_login": ("验证并登录", "Verify & Sign In"),
    "auth.code_register_title": ("邮箱验证码注册", "Sign up with Email Code"),
    "auth.code_register_sub": ("只需邮箱 + 验证码即可创建账号，无需设置密码。",
                               "Create your account with just an email and a code — no password to remember."),
    "auth.submit_code_register": ("验证并创建账号", "Verify & Create Account"),
    "auth.username_optional": ("用户名（选填）", "Username (optional)"),
    "auth.use_password_register": ("改用密码注册", "Use a password instead"),
    "auth.use_code_register": ("改用邮箱验证码注册", "Use an email code instead"),
    "auth.code_note": ("我们不会保存验证码明文，验证成功后该码立即失效。",
                       "Codes are never stored in plain text and expire immediately after use."),

    # ---------------------------------------------------------------- 学习中心
    "dash.greeting": ("你好", "Hello"),
    "dash.today": ("今日概览", "Today's Overview"),
    "dash.words_learned": ("已学习单词", "Words Studied"),
    "dash.words_mastered": ("已掌握", "Learned"),
    "dash.accuracy": ("总正确率", "Accuracy"),
    "dash.streak": ("连续学习", "Day Streak"),
    "dash.streak_unit": ("天", "days"),
    "dash.today_answers": ("今日答题", "Answers Today"),
    "dash.wrong_count": ("错题本", "Mistakes"),
    "dash.favorites_count": ("收藏", "Saved"),
    "dash.tests_count": ("完成测试", "Quizzes Taken"),
    "dash.chart_title": ("近 7 天答题量", "Answers in the Last 7 Days"),
    "dash.chart_sub": ("柱高为当天答题数", "Bar height = answers that day"),
    "dash.overall": ("总学习进度", "Overall Progress"),
    "dash.recent_tests": ("最近的测试", "Recent Quizzes"),
    "dash.no_tests": ("还没有测试记录", "No quizzes yet"),
    "dash.no_tests_desc": ("做一组测试，看看自己的水平", "Take a quiz to see where you stand"),

    # ---------------------------------------------------------------- 学习页
    "learn.title": ("单词学习", "Word Study"),
    "learn.sub": ("每张卡片包含单词、音标、释义与例句，点击喇叭即可听英式发音。",
                  "Each card shows the word, pronunciation, meaning and an example. Tap the speaker for UK audio."),
    "learn.scope": ("范围", "Range"),
    "learn.total": ("共", "Total"),
    "learn.words_unit": ("个单词", "words"),

    # ---------------------------------------------------------------- 测试
    "quiz.title": ("测试中心", "Quiz"),
    "quiz.sub": ("四种模式轮换练习，覆盖听说读写。", "Four modes covering listening, reading and writing."),
    "quiz.choose_mode": ("选择测试模式", "Choose a Mode"),
    "quiz.choose_scope": ("选择出题范围", "Choose a Range"),
    "quiz.choose_size": ("题目数量", "Number of Questions"),
    "quiz.start": ("开始测试", "Start Quiz"),
    "quiz.mode.choice": ("英文 → 中文", "English → Chinese"),
    "quiz.mode.zh_en": ("中文 → 英文", "Chinese → English"),
    "quiz.mode.listen": ("听音测试", "Listening"),
    "quiz.mode.spell": ("拼写测试", "Spelling"),
    "quiz.mode.choice_desc": ("看英文单词选出中文释义", "Choose the Chinese meaning"),
    "quiz.mode.zh_en_desc": ("看中文释义选出英文单词", "Choose the English word"),
    "quiz.mode.listen_desc": ("听英式发音选出正确单词", "Listen and pick the word"),
    "quiz.mode.spell_desc": ("根据中文释义拼出英文单词", "Type the English word"),
    "quiz.scope.all": ("全部单词", "All Words"),
    "quiz.scope.new": ("未学习", "New"),
    "quiz.scope.learning": ("学习中", "Learning"),
    "quiz.scope.mastered": ("已掌握", "Learned"),
    "quiz.scope.favorites": ("我的收藏", "Favorites"),
    "quiz.scope.wrong": ("错题本", "Mistakes"),
    "quiz.questions": ("题", "questions"),
    "quiz.estimated": ("预计", "About"),
    "quiz.minutes": ("分钟", "min"),
    "quiz.progress": ("第", "Question"),
    "quiz.correct": ("回答正确", "Correct"),
    "quiz.wrong": ("回答错误", "Incorrect"),
    "quiz.correct_answer": ("正确答案", "Correct answer"),
    "quiz.spell_placeholder": ("在这里输入英文单词", "Type the English word here"),
    "quiz.spell_submit": ("提交答案", "Submit"),
    "quiz.spell_hint": ("首字母", "First letter"),
    "quiz.spell_length": ("个字母", "letters"),
    "quiz.listen_hint": ("点击播放英式发音，选出你听到的单词", "Tap to play the UK pronunciation, then choose the word"),
    "quiz.listen_replay": ("没听清？可以再点一次", "Tap again to replay"),
    "quiz.next_tip": ("答对进入下一题，答错会自动加入错题本", "Correct moves on; mistakes are saved to your review list"),
    "quiz.result": ("本次得分", "Your Score"),
    "quiz.score_label": ("答对题数", "Correct"),
    "quiz.time_used": ("用时", "Time"),
    "quiz.seconds": ("秒", "sec"),
    "quiz.saved_note": ("正确率已写入你的学习档案", "Your accuracy has been saved to your profile"),
    "quiz.view_wrong": ("查看错题本", "Review Mistakes"),
    "quiz.back_dashboard": ("返回学习中心", "Back to Dashboard"),

    # ---------------------------------------------------------------- 错题 / 收藏
    "wrong.title": ("错题复习", "Review Mistakes"),
    "wrong.sub": ("答错自动收录，答对自动移出 —— 这里提分最快。",
                  "Added automatically when you slip; removed when you get it right."),
    "wrong.practice": ("专项练习错题", "Practise These"),
    "wrong.study": ("逐个复习", "Review One by One"),
    "wrong.empty": ("错题本是空的", "No mistakes yet"),
    "wrong.empty_desc": ("很棒！答错的单词会自动出现在这里", "Great job! Missed words will appear here"),
    "fav.title": ("我的收藏", "Favorites"),
    "fav.sub": ("重点单词放在这里，随时回来复习。", "Keep important words here and revisit them anytime."),
    "fav.practice": ("用收藏单词测试", "Quiz on Favourites"),
    "fav.browse": ("去单词本继续收藏", "Browse Word List"),
    "fav.empty": ("收藏夹还是空的", "Nothing saved yet"),
    "fav.empty_desc": ("在单词卡片上点击收藏即可加入", "Tap the star on any word card to save it"),

    # ---------------------------------------------------------------- 进度
    "prog.title": ("学习进度", "Progress"),
    "prog.sub": ("掌握度、正确率与测试历史，全部属于你的账号。",
                 "Mastery, accuracy and quiz history — all private to your account."),
    "prog.trend": ("近 7 天答题趋势", "Answers in the Last 7 Days"),
    "prog.distribution": ("掌握度分布", "Mastery Breakdown"),
    "prog.history": ("测试历史", "Quiz History"),
    "prog.no_history": ("还没有测试记录", "No quiz history"),
    "prog.no_history_desc": ("完成第一组测试后，这里会显示成绩曲线", "Your results will appear here after your first quiz"),
    "prog.time": ("时间", "Date"),
    "prog.mode": ("模式", "Mode"),
    "prog.score": ("得分", "Score"),
    "prog.accuracy": ("正确率", "Accuracy"),
    "prog.duration": ("用时", "Duration"),
    "prog.total_answers": ("累计答题", "Total Answers"),

    # ---------------------------------------------------------------- 个人中心
    "prof.title": ("个人中心", "My Account"),
    "prof.sub": ("管理你的账号信息与登录密码", "Manage your profile and password"),
    "prof.basic": ("基本信息", "Account Details"),
    "prof.field_username": ("用户名", "Username"),
    "prof.field_email": ("邮箱（登录账号）", "Email (Login ID)"),
    "prof.field_role": ("账号类型", "Account Type"),
    "prof.field_created": ("注册时间", "Member Since"),
    "prof.field_last_login": ("最近登录", "Last Sign-in"),
    "prof.field_login_count": ("登录次数", "Sign-ins"),
    "prof.role_admin": ("管理员", "Administrator"),
    "prof.role_user": ("普通用户", "Member"),
    "prof.change_username": ("修改用户名", "Change Username"),
    "prof.change_password": ("修改密码", "Change Password"),
    "prof.current_password": ("当前密码", "Current Password"),
    "prof.new_password": ("新密码", "New Password"),
    "prof.confirm_new_password": ("确认新密码", "Confirm New Password"),
    "prof.my_data": ("我的数据", "My Statistics"),
    "prof.recent_answers": ("最近作答", "Recent Answers"),
    "prof.no_answers": ("还没有作答记录", "No answers recorded"),
    "prof.joined_days": ("注册第", "Day"),
    "prof.joined_days_unit": ("天", "since joining"),

    # ---------------------------------------------------------------- 其他
    "common.of": ("/", "/"),
    "common.empty": ("暂无数据", "No data"),
    "common.loading": ("加载中", "Loading"),
    "footer.wordbank": ("词库共", "Word bank"),
    "footer.offline": ("英式发音全部离线提供", "UK audio available offline"),
    "footer.privacy": ("学习数据仅保存在你自己的账号中，用户之间相互隔离",
                       "Your learning data is private to your account"),
    "lang.switch": ("语言", "Language"),
    "lang.zh": ("中文", "Chinese"),
    "lang.en": ("English", "English"),
    "lang.both": ("双语", "Bilingual"),
}


def _pair(key: str) -> tuple[str, str]:
    """取一条文案的 (中文, English)。缺失时返回 key 本身，避免页面报错。"""
    pair = STRINGS.get(key)
    if not pair:
        return (key, key)
    zh, en = pair
    return (zh or key, en or key)


def t(key: str, lang: str = "zh") -> str:
    """返回单一语言的文字（供需要纯文本的场合使用，如 placeholder）。"""
    zh, en = _pair(key)
    return en if lang == "en" else zh


def bi(key: str, sep: str = "/") -> Markup:
    """渲染双语结构：英文在前，中文在后，中间由 CSS 加分隔符。

    输出形如
    ``<span class="bi"><span class="bi-en">Dashboard</span><span class="bi-zh">学习中心</span></span>``

    未来切换语言只需给 <html> 加 ``data-lang="zh|en"``，由 CSS 隐藏另一段。
    """
    zh, en = _pair(key)
    return Markup(
        '<span class="bi">'
        f'<span class="bi-en">{escape(en)}</span>'
        f'<span class="bi-zh">{escape(zh)}</span>'
        "</span>"
    )


def bi_plain(key: str, sep: str = " / ") -> str:
    """返回纯文本双语（用于 title 属性、纯文本提示等）。"""
    zh, en = _pair(key)
    return f"{en}{sep}{zh}"


def has(key: str) -> bool:
    return key in STRINGS
