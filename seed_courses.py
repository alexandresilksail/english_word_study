"""V5 课程内容播种（幂等）。

English：Pre-A1 → C2 七门课程，每门 3 Unit；词汇内容来自现有词库（按 CEFR 分级），
词组/句子/语法为手写起步内容（不复制任何商业词典）。
Cantonese：入门 6 Unit（Greetings/Numbers/Time/Family/Daily Life/Conversation），
带粤拼 jyutping。
"""
from __future__ import annotations

import logging

from extensions import db
from models import (ContentItem, Course, Lesson, LearningLanguage, Unit,
                    Word, WordMeta)

logger = logging.getLogger(__name__)

LEVELS_EN = [
    ("Pre-A1", "Pre-A1", "入门预备", "Starter", "gray"),
    ("A1", "A1", "入门", "Beginner", "violet"),
    ("A2", "A2", "初级", "Elementary", "sky"),
    ("B1", "B1", "中级", "Intermediate", "emerald"),
    ("B2", "B2", "中高级", "Upper Intermediate", "amber"),
    ("C1", "C1", "高级", "Advanced", "rose"),
    ("C2", "C2", "精通", "Proficiency", "indigo"),
]

UNIT_THEMES = {
    "Pre-A1": [("发音与字母", "Sounds & Alphabet"), ("打招呼", "Greetings"), ("数字 1-20", "Numbers")],
    "A1": [("开始学习", "Getting Started"), ("日常生活", "Daily Life"), ("家人与朋友", "Family & Friends")],
    "A2": [("时间与日期", "Time & Numbers"), ("购物与餐饮", "Shopping & Food"), ("工作与学习", "Work & Study")],
    "B1": [("观点表达", "Opinions"), ("文化与节日", "Culture"), ("健康与运动", "Health")],
    "B2": [("社会话题", "Society"), ("职场进阶", "Career"), ("媒体与科技", "Media & Tech")],
    "C1": [("抽象讨论", "Abstraction"), ("文学艺术", "Arts"), ("时事辩论", "Debates")],
    "C2": [("精通运用", "Mastery"), ("学术写作", "Academic"), ("跨文化交流", "Cross-culture")],
}

LESSON_PLAN = [
    ("vocabulary", "词汇", "Vocabulary", "📚"),
    ("phrase", "词组", "Phrases", "💬"),
    ("sentence", "句子", "Sentences", "✏️"),
    ("grammar", "语法", "Grammar", "🧩"),
    ("listening", "听力", "Listening", "🎧"),
    ("speaking", "口语", "Speaking", "🗣️"),
    ("quiz", "小测", "Quiz", "📝"),
    ("review", "复习", "Review", "🔁"),
]

PHRASE_SEED = [
    ("good morning", "早上好", "Good morning! Nice to see you.", "早上好！很高兴见到你。"),
    ("how are you", "你好吗；近况如何", "How are you today?", "你今天好吗？"),
    ("thank you very much", "非常感谢", "Thank you very much for your help.", "非常感谢你的帮助。"),
    ("see you later", "回头见", "See you later at the cafe.", "咖啡馆回头见。"),
]
SENTENCE_SEED = [
    ("I would like a cup of tea.", "我想要一杯茶。", "主语 + would like + 名词"),
    ("Where is the nearest station?", "最近的车站在哪里？", "Where is the …?"),
    ("She is reading a book in the park.", "她正在公园里看书。", "现在进行时 be + V-ing"),
]
GRAMMAR_SEED = [
    ("Verb to be", "be 动词", "I am / You are / He is … 表达身份与状态。"),
    ("Present Simple", "一般现在时", "表示习惯与事实：I work every day."),
    ("Present Continuous", "现在进行时", "正在发生：She is working now."),
]


def _seed_languages():
    if LearningLanguage.query.count():
        return
    for code, zh, en, flag, sort in [
        ("en", "英语", "English", "🇬🇧", 0),
        ("yue", "粤语", "Cantonese", "🇭🇰", 1),
    ]:
        db.session.add(LearningLanguage(code=code, name_zh=zh, name_en=en, flag=flag,
                                        sort=sort, ready=True))
    db.session.commit()


def _seed_english():
    if Course.query.filter_by(language_code="en").count():
        return
    for sort, (code, _, zh, en, color) in enumerate(LEVELS_EN):
        course = Course(language_code="en", cefr_level=code,
                        title_zh=f"{code} {zh}", title_en=f"{code} {en}",
                        description=f"English course · {en}", color=color, sort=sort)
        db.session.add(course); db.session.flush()
        for u_no, (u_zh, u_en) in enumerate(UNIT_THEMES[code], start=1):
            unit = Unit(course_id=course.id, no=u_no, title_zh=u_zh, title_en=u_en,
                        emoji="📘")
            db.session.add(unit); db.session.flush()
            # 词汇：取该 CEFR 档前 10 词
            words = (Word.query.join(WordMeta, WordMeta.word_id == Word.id)
                     .filter(WordMeta.cefr_level == code, WordMeta.unit_no == u_no)
                     .order_by(Word.freq.desc()).limit(10).all())
            for l_no, (kind, l_zh, l_en, emoji) in enumerate(LESSON_PLAN, start=1):
                lesson = Lesson(unit_id=unit.id, no=l_no, kind=kind,
                                title_zh=f"{emoji} {l_zh}", title_en=f"{emoji} {l_en}")
                db.session.add(lesson); db.session.flush()
                if kind == "vocabulary":
                    for w in words:
                        db.session.add(ContentItem(
                            lesson_id=lesson.id, kind="vocabulary",
                            surface=w.word, phonetic=w.phonetic_uk, pos=w.pos,
                            meaning_cn=w.meaning_cn, example_en=w.example_en,
                            example_cn=w.example_cn, topic=u_en.lower().replace(" ", "_"),
                            audio=w.audio))
                elif kind == "phrase":
                    for i, (s, m, ex, excn) in enumerate(PHRASE_SEED):
                        db.session.add(ContentItem(lesson_id=lesson.id, kind="phrase",
                                                  surface=s, meaning_cn=m,
                                                  example_en=ex, example_cn=excn))
                elif kind == "sentence":
                    for s, cn, gr in SENTENCE_SEED:
                        db.session.add(ContentItem(lesson_id=lesson.id, kind="sentence",
                                                   surface=s, meaning_cn=cn, topic=gr))
                elif kind == "grammar":
                    for t, cn, ex in GRAMMAR_SEED:
                        db.session.add(ContentItem(lesson_id=lesson.id, kind="grammar",
                                                  surface=t, meaning_cn=cn, example_en=ex))
    db.session.commit()
    logger.info("English courses seeded")


YUE_SEED = [
    ("问候", "Greetings", "🇭🇰", [
        ("你好", "néih hóu", "你好", "Neih hóu!", "你好！"),
        ("早晨", "zóu sàhn", "早上好", "Zóu sàhn!", "早上好！"),
        ("唔该", "m̀h gōi", "谢谢/麻烦你", "M̀h gōi!", "谢谢！"),
        ("再见", "zoi gin", "再见", "Zoi gin!", "再见！"),
    ]),
    ("数字", "Numbers", "🔢", [
        ("一", "yāt", "一", "Yāt go", "一个"),
        ("二", "yih", "二", "Yih go", "两个"),
        ("三", "sāam", "三", "Sāam go", "三个"),
        ("五", "ǵh̆", "五", "ǵh̆ go", "五个"),
    ]),
    ("时间", "Time", "🕐", [
        ("而家几点", "yìh gā géi dím", "现在几点", "Yìh gā géi dím?", "现在几点？"),
        ("听日", "tīng yaht", "明天", "Tīng yaht", "明天"),
        ("今日", "gām yaht", "今天", "Gām yaht", "今天"),
    ]),
    ("家庭", "Family", "👨‍👩‍👧", [
        ("爸爸", "bàh-bà", "爸爸", "Bàh-bà hóu!", "爸爸好！"),
        ("妈妈", "màh-mà", "妈妈", "Màh-mà hóu!", "妈妈好！"),
        ("我阿妹", "ngóh ā mui", "我妹妹", "Ngóh ā mui", "我妹妹"),
    ]),
    ("日常", "Daily Life", "🍜", [
        ("食饭未", "sihk faahn meih", "吃饭了吗", "Sihk faahn meih?", "吃饭了吗？"),
        ("饮茶", "yám chàh", "喝茶/早茶", "Yám chàh", "去喝早茶"),
        ("行街", "hàhng gaai", "逛街", "Hàhng gaai", "逛街"),
    ]),
    ("基础对话", "Basic Conversation", "💬", [
        ("你系边个", "néih haih bīn-go", "你是谁", "Néih haih bīn-go?", "你是谁？"),
        ("我系学生", "ngóh haih hohk sāang", "我是学生", "Ngóh haih hohk sāang.", "我是学生。"),
        ("唔识听", "m̀h sīk téng", "听不懂", "M̀h sīk téng.", "听不懂。"),
    ]),
]


def _seed_cantonese():
    if Course.query.filter_by(language_code="yue").count():
        return
    course = Course(language_code="yue", cefr_level="A1",
                    title_zh="粤语入门", title_en="Cantonese Beginner",
                    description="Cantonese with Jyutping · 粤语拼音起步", color="rose", sort=1)
    db.session.add(course); db.session.flush()
    for u_no, (u_zh, u_en, emoji, items) in enumerate(YUE_SEED, start=1):
        unit = Unit(course_id=course.id, no=u_no, title_zh=u_zh, title_en=u_en, emoji=emoji)
        db.session.add(unit); db.session.flush()
        # 每单元：词汇 lesson + 句子 lesson + quiz lesson
        for l_no, (kind, l_zh, l_en) in enumerate(
                [("vocabulary", "词汇", "Vocabulary"), ("sentence", "句子", "Sentences"),
                 ("quiz", "小测", "Quiz")], start=1):
            lesson = Lesson(unit_id=unit.id, no=l_no, kind=kind, title_zh=l_zh, title_en=l_en)
            db.session.add(lesson); db.session.flush()
            if kind in ("vocabulary", "sentence"):
                for surface, jp, meaning, ex, excn in items:
                    db.session.add(ContentItem(lesson_id=lesson.id, kind=kind, surface=surface,
                                                phonetic=jp, meaning_cn=meaning,
                                                example_en=ex, example_cn=excn,
                                                topic="cantonese"))
    db.session.commit()
    logger.info("Cantonese course seeded")


def seed_courses():
    _seed_languages()
    _seed_english()
    _seed_cantonese()
