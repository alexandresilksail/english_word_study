"""学习中心：六项技能入口（Vocabulary / Listening / Reading / Grammar / Speaking / Writing）。

诚实原则
--------
本阶段只有 Vocabulary 有真实内容。其余技能给的是**漂亮的入口页**，
页面明确标注「Coming soon / 敬请期待」，并说明未来会提供什么 ——
**绝不伪造内容，更不假装有 AI 功能**。

未来接入真实内容时：
1. 在 :data:`models.SKILLS` 里把对应项的 ``ready`` 改成 True
2. 在 CONTENT 里补上该技能的模块清单
3. 页面逻辑无需改动（模板读的就是这两份数据）
"""
from __future__ import annotations

from flask import Blueprint, abort, redirect, render_template, url_for
from flask_login import current_user, login_required

from gamification import overview, skill_stats
from models import SKILLS

learn_bp = Blueprint("learn", __name__, url_prefix="/learn")

# 各技能的展示文案与未来内容规划（集中管理，模板不写死）
SKILL_TEXT = {
    "vocabulary": {
        "zh": "词汇", "en": "Vocabulary",
        "desc_zh": "2000 个高频词 · 英式发音 · 例句",
        "desc_en": "2000 high-frequency words with UK audio",
    },
    "listening": {
        "zh": "听力", "en": "Listening",
        "desc_zh": "播客精听 · 听写 · 变速训练",
        "desc_en": " Podcast study, dictation & speed control",
    },
    "reading": {
        "zh": "阅读", "en": "Reading",
        "desc_zh": "分级短文 · 生词即点即查",
        "desc_en": "Levelled passages with tap-to-lookup",
    },
    "grammar": {
        "zh": "语法", "en": "Grammar",
        "desc_zh": "情景语法 · 例句驱动的规则讲解",
        "desc_en": "Rules explained through real examples",
    },
    "speaking": {
        "zh": "口语", "en": "Speaking",
        "desc_zh": "跟读打分 · 情景对话",
        "desc_en": "Shadowing, scoring & role play",
    },
    "writing": {
        "zh": "写作", "en": "Writing",
        "desc_zh": "句型练习 · 短文批改",
        "desc_en": "Sentence drills & essay feedback",
    },
}

# 未上线技能未来会提供的内容（展示在入口页，让用户知道方向）
SKILL_PLANS = {
    "listening": [
        ("精听播客", "Podcast Study", "由 Podcast 文稿驱动，逐句精听 + 填空"),
        ("听写练习", "Dictation", "播放音频后写出句子，逐词校对"),
        ("变速训练", "Speed Control", "0.6× → 1.4× 无级变速，适应真实语速"),
    ],
    "reading": [
        ("分级短文", "Levelled Texts", "A1–C1 分级，题材覆盖生活 / 商务 / 科技"),
        ("生词即查", "Tap to Look Up", "点任意单词直接出释义与发音"),
        ("读后测验", "Comprehension Quiz", "读完做理解题，检验真实吸收"),
    ],
    "grammar": [
        ("情景语法", "Grammar in Context", "从 Podcast 与阅读材料中抽真实例句"),
        ("专项练习", "Targeted Drills", "按语法点出题，错的自动进复习队列"),
        ("易错对比", "Common Mistakes", "中式英语高频错误对照讲解"),
    ],
    "speaking": [
        ("跟读打分", "Shadow & Score", "录音对比原音，给出流利度反馈"),
        ("情景对话", "Role Play", "点餐、面试、闲聊等真实场景演练"),
        ("发音纠正", "Pronunciation", "针对单个音标的口型与练习"),
    ],
    "writing": [
        ("句型练习", "Sentence Drills", "从仿写开始，逐步过渡到自由表达"),
        ("短文批改", "Essay Feedback", "提交短文获得结构与用词建议"),
        ("常用模板", "Useful Templates", "邮件、评论、报告的高频表达"),
    ],
}

VOCAB_MODULES = [
    {"zh": "今日学习", "en": "Daily Learn", "icon": "book",
     "url": "words.learn", "desc_zh": "按计划推进新词", "desc_en": "Learn new words step by step"},
    {"zh": "单词本", "en": "Word List", "icon": "list",
     "url": "words.browse", "desc_zh": "A–Z 浏览与搜索", "desc_en": "Browse A–Z or search"},
    {"zh": "测试", "en": "Quiz", "icon": "quiz",
     "url": "quiz.test_page", "desc_zh": "四种题型检验掌握度", "desc_en": "Four modes to test yourself"},
    {"zh": "错题本", "en": "Review", "icon": "review",
     "url": "words.wrong", "desc_zh": "专门攻克错的词", "desc_en": "Drill the words you missed"},
    {"zh": "我的收藏", "en": "Favorites", "icon": "star",
     "url": "words.favorites", "desc_zh": "随时回看重难点", "desc_en": "Revisit your saved words"},
]


def _skill_cards():
    """技能卡数据：元信息 + 文案 + 用户进度 + 链接。"""
    stats = skill_stats(current_user.id) if current_user.is_authenticated else {}
    cards = []
    for s in SKILLS:
        txt = SKILL_TEXT.get(s["key"], {})
        row = stats.get(s["key"])
        xp = row.xp if row else 0
        cards.append({
            **s, **txt,
            "xp": xp,
            "items": row.items if row else 0,
            # 进度条：以 1000 XP 为一段，仅作视觉参考
            "percent": min(100, round(xp * 100 / 1000)),
            "url": (url_for("words.learn") if s["key"] == "vocabulary"
                    else url_for("learn.skill", key=s["key"])),
        })
    return cards


@learn_bp.route("/")
@login_required
def hub():
    """学习中心总览：六项技能 + 今日推荐。"""
    return render_template("learn_hub.html",
                           skills=_skill_cards(),
                           gamification=overview(current_user.id))


@learn_bp.route("/<key>")
@login_required
def skill(key: str):
    """单个技能入口。

    vocabulary 直接跳真实内容页；其余渲染「即将上线」入口页，
    把规划说明白，不做假内容。
    """
    if key == "vocabulary":
        return redirect(url_for("words.learn"))
    meta = next((s for s in SKILLS if s["key"] == key), None)
    if not meta:
        abort(404)
    txt = SKILL_TEXT.get(key, {})
    stats = skill_stats(current_user.id).get(key)
    return render_template("skill_page.html", skill={**meta, **txt},
                           plan=SKILL_PLANS.get(key, []),
                           stat=stats,
                           gamification=overview(current_user.id))


@learn_bp.route("/vocabulary")
@login_required
def vocabulary():
    """词汇模块导航页（把词汇相关的入口集中起来）。"""
    modules = []
    for m in VOCAB_MODULES:
        modules.append({**m, "url": url_for(m["url"])})
    return render_template("skill_vocabulary.html", modules=modules,
                           gamification=overview(current_user.id))
