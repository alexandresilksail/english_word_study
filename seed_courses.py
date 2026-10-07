"""V5 课程内容播种（幂等）。

English：Pre-A1 → C2 七门课程，每门 3 Unit；词汇内容来自现有词库（按 CEFR 分级），
词组/句子/语法/听力/口语/小测/复习为手写起步内容（不复制任何商业词典）。
Cantonese：入门 6 Unit（Greetings/Numbers/Time/Family/Daily Life/Conversation），
每条都带 **Chinese / Jyutping / English explanation / Example**（规格 §15）。

每个 ContentItem 都同时写入 ``meaning_en``，这是「English 版面零中文」的数据前提；
释义来源见 :mod:`translation_service`（原创手写，非商业词典抓取）。
"""
from __future__ import annotations

import logging

from extensions import db
from models import (ContentItem, Course, Lesson, LearningLanguage, Unit,
                    Word, WordMeta)
from translation_service import lookup

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

#: 每个 Unit 的课时类型（规格 §4：听说读写 + 练测复习）
#: reading / writing 是 §4 明确要求、此前缺失的两类，现已补齐为 10 类。
LESSON_PLAN = [
    ("vocabulary", "词汇", "Vocabulary", "📚"),
    ("phrase", "词组", "Phrases", "💬"),
    ("sentence", "句子", "Sentences", "✏️"),
    ("grammar", "语法", "Grammar", "🧩"),
    ("listening", "听力", "Listening", "🎧"),
    ("reading", "阅读", "Reading", "📖"),
    ("speaking", "口语", "Speaking", "🗣️"),
    ("writing", "写作", "Writing", "✍️"),
    ("quiz", "小测", "Quiz", "📝"),
    ("review", "复习", "Review", "🔁"),
]

# ---------------------------------------------------------------- 英语起步内容
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
# 听力训练小节：听同一组句子，做填空/辨义（复用句子内容 + audio 标记）
LISTENING_HINT = [
    ("听音辨义：选出你听到的句子", "Listen and choose the sentence you hear"),
    ("填空：补全句子中的关键词", "Fill in the missing keyword"),
    ("跟读：先听后读，注意语调", "Listen then repeat, mind the intonation"),
]
# 口语训练小节
SPEAKING_HINT = [
    ("跟读句子，注意重音", "Shadow the sentence, mind the stress"),
    ("替换主语再说一遍", "Say it again with a different subject"),
    ("用该句型自造一句", "Make your own sentence with this pattern"),
]
# 阅读训练小节（§4 补齐）
READING_HINT = [
    ("读句子，说出它的意思", "Read the sentence and say what it means"),
    ("读后找出关键词", "Read and spot the key words"),
    ("读一遍并复述大意", "Read once, then retell the main idea"),
]
# 写作训练小节（§4 补齐）
WRITING_HINT = [
    ("仿写句子：替换关键词", "Copy the pattern, swap in your own words"),
    ("用本单元词组写一句话", "Write a sentence using this unit's phrases"),
    ("扩写：给句子加一个从句", "Extend the sentence with a clause"),
]
#: 技能型课时（听/读/说/写）共用同一套素材（本单元句子），只是训练指令不同
SKILL_HINTS = {
    "listening": LISTENING_HINT,
    "reading": READING_HINT,
    "speaking": SPEAKING_HINT,
    "writing": WRITING_HINT,
}
# 单元小测（题型引导；正式判分由 Unit Test 引擎承担，见 unit_test_service）
QUIZ_HINT = [
    ("选择题：选出正确释义", "Multiple choice: pick the right meaning"),
    ("填空：补全词组", "Fill in the blank: complete the phrase"),
    ("排序：把单词组成句子", "Reorder the words into a sentence"),
]
# 复习小节
REVIEW_HINT = [
    ("复习本单元词汇", "Review this unit's vocabulary"),
    ("复习本单元词组与句子", "Review this unit's phrases & sentences"),
    ("复习本单元语法点", "Review this unit's grammar"),
]


def _pick_words(cefr_level: str, unit_no: int, limit: int = 10) -> list:
    """为某个「CEFR 等级 × 单元」挑选词汇 —— 三级兜底，保证**永不返回空列表**。

    为什么需要兜底
    --------------
    word_meta 是分级数据的唯一来源，但 ``create_app`` 里的 ``ensure_word_meta()``
    可能在词库导入**之前**就已经跑过一次（那时 words 表还是空的），导致分级为空；
    课程结构却已经建好了 —— 于是「课时建好了，词却取不到」，词汇课变成空壳。

    兜底顺序（越靠前越贴合，越靠后越通用）：
    1. 精确匹配：该等级 + 该单元
    2. 同级兜底：该等级任意单元（按单元序号错开，避免三个单元拿到同一批词）
    3. 全局兜底：全库最常用的词
    """
    def _q(unit=None, offset: int = 0) -> list:
        q = Word.query.join(WordMeta, WordMeta.word_id == Word.id)
        q = q.filter(WordMeta.cefr_level == cefr_level)
        if unit is not None:
            q = q.filter(WordMeta.unit_no == unit)
        return (q.order_by(Word.freq.asc(), Word.id.asc())
                .offset(offset).limit(limit).all())

    words = _q(unit_no)
    if words:
        return words
    words = _q(None, offset=(max(1, unit_no) - 1) * limit)
    if words:
        return words
    return (Word.query.order_by(Word.freq.asc(), Word.id.asc())
            .offset((max(1, unit_no) - 1) * limit).limit(limit).all())


def _fields(lang_code: str, kind: str, surface: str, **kw) -> dict:
    """把 search()] ``translation_service`` 的英文释义合并进 ContentItem 字段。

    保证每条内容都带上 ``meaning_en``；查不到时留空（**不塞中文**）。
    """
    pack = lookup(lang_code, kind, surface)
    out = dict(kw)
    for f in ("meaning_en", "example_en", "phonetic"):
        if not (out.get(f) or "").strip() and (pack.get(f) or "").strip():
            out[f] = pack[f]
    out.setdefault("meaning_en", "")
    out.setdefault("meaning_yue", "")
    out.setdefault("example_yue", "")
    return out


def _add(lesson_id: int, kind: str, surface: str, topic: str, **kw):
    db.session.add(ContentItem(lesson_id=lesson_id, kind=kind, surface=surface,
                               topic=topic, **kw))


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


def _build_unit_lessons(unit: Unit, words, topic: str, lang_code: str = "en"):
    """为一个 Unit 生成 8 类课时，并给**每一类**都写入真实可用内容。"""
    for l_no, (kind, l_zh, l_en, emoji) in enumerate(LESSON_PLAN, start=1):
        lesson = Lesson(unit_id=unit.id, no=l_no, kind=kind,
                        title_zh=f"{emoji} {l_zh}", title_en=f"{emoji} {l_en}")
        db.session.add(lesson)
        db.session.flush()

        if kind == "vocabulary":
            for w in words:
                _add(lesson.id, "vocabulary", w.word, topic,
                     **_fields(lang_code, "vocabulary", w.word,
                               phonetic=w.phonetic_uk, pos=w.pos,
                               meaning_cn=w.meaning_cn, example_en=w.example_en,
                               example_cn=w.example_cn, audio=w.audio))
        elif kind == "phrase":
            for s, m, ex, excn in PHRASE_SEED:
                _add(lesson.id, "phrase", s, topic,
                     **_fields(lang_code, "phrase", s,
                               meaning_cn=m, example_en=ex, example_cn=excn))
        elif kind == "sentence":
            for s, cn, gr in SENTENCE_SEED:
                _add(lesson.id, "sentence", s, topic,
                     **_fields(lang_code, "sentence", s,
                               meaning_cn=cn, example_en=s, pos=gr))
        elif kind == "grammar":
            for t, cn, ex in GRAMMAR_SEED:
                _add(lesson.id, "grammar", t, topic,
                     **_fields(lang_code, "grammar", t,
                               meaning_cn=cn, example_en=ex))
        elif kind in SKILL_HINTS:
            # 听说读写四课：共用本单元句子作素材，只是训练指令不同
            hints = SKILL_HINTS[kind]
            for i, (s, cn, gr) in enumerate(SENTENCE_SEED):
                hint_zh, hint_en = hints[i % len(hints)]
                _add(lesson.id, kind, s, topic,
                     **_fields(lang_code, "sentence", s,
                               meaning_cn=f"{hint_zh}｜{cn}",
                               example_en=f"{hint_en} — {s}"))
        elif kind == "quiz":
            for hint_zh, hint_en in QUIZ_HINT:
                for t, cn, ex in GRAMMAR_SEED[:1]:
                    _add(lesson.id, "quiz", f"{hint_en} · {t}", topic,
                         meaning_cn=f"{hint_zh}｜{cn}", example_en=ex)
        elif kind == "review":
            for hint_zh, hint_en in REVIEW_HINT:
                _add(lesson.id, "review", hint_en, topic,
                     meaning_cn=hint_zh, example_en="")


def _seed_english():
    if Course.query.filter_by(language_code="en").count():
        return
    for sort, (code, _, zh, en, color) in enumerate(LEVELS_EN):
        course = Course(language_code="en", cefr_level=code,
                        title_zh=f"{code} {zh}", title_en=f"{code} {en}",
                        description=f"English course · {en}", color=color, sort=sort)
        db.session.add(course); db.session.flush()
        for u_no, (u_zh, u_en) in enumerate(UNIT_THEMES[code], start=1):
            # 三级兜底取词：精确(等级+单元) → 同等级 → 全库最常用，绝不返回空
            words = _pick_words(code, u_no)
            unit = Unit(course_id=course.id, no=u_no, title_zh=u_zh, title_en=u_en,
                        emoji="📘")
            db.session.add(unit); db.session.flush()
            _build_unit_lessons(unit, words, u_en.lower().replace(" ", "_"), "en")
    db.session.commit()
    logger.info("English courses seeded")


#: 粤语：Chinese(字面) / Jyutping / 中文义 / English explanation / 例句
YUE_SEED = [
    ("问候", "Greetings", "🇭🇰", [
        ("你好", "néih hóu", "你好", "Neih hou!", "Hello!"),
        ("早晨", "zóu sàhn", "早上好", "Zou sahn!", "Good morning!"),
        ("唔该", "m̀h gōi", "谢谢/麻烦你", "Mh goi!", "Thanks!"),
        ("再见", "zoi gin", "再见", "Zoi gin!", "Goodbye!"),
    ]),
    ("数字", "Numbers", "🔢", [
        ("一", "yāt", "一", "Yat go", "one (item)"),
        ("二", "yih", "二", "Yih go", "two (items)"),
        ("三", "sāam", "三", "Saam go", "three (items)"),
        ("五", "ńgh", "五", "Ngh go", "five (items)"),
    ]),
    ("时间", "Time", "🕐", [
        ("而家几点", "yìh gā géi dím", "现在几点", "Yih ga gei dim?", "What time is it now?"),
        ("听日", "tīng yaht", "明天", "Ting yaht", "tomorrow"),
        ("今日", "gām yaht", "今天", "Gam yaht", "today"),
    ]),
    ("家庭", "Family", "👨‍👩‍👧", [
        ("爸爸", "bàh-bà", "爸爸", "Bah-ba hou!", "Hi, Dad!"),
        ("妈妈", "màh-mà", "妈妈", "Mah-ma hou!", "Hi, Mum!"),
        ("我阿妹", "ngóh ā mui", "我妹妹", "Ngo aa mui", "my younger sister"),
    ]),
    ("日常", "Daily Life", "🍜", [
        ("食饭未", "sihk faahn meih", "吃饭了吗", "Sihk faahn meih?", "Have you eaten yet?"),
        ("饮茶", "yám chàh", "喝茶/早茶", "Yam cha", "go for yum cha"),
        ("行街", "hàhng gaai", "逛街", "Haang gaai", "go shopping"),
    ]),
    ("基础对话", "Basic Conversation", "💬", [
        ("你系边个", "néih haih bīn-go", "你是谁", "Neih haih bin-go?", "Who are you?"),
        ("我系学生", "ngóh haih hohk sāang", "我是学生", "Ngo haih hohk saang.", "I am a student."),
        ("唔识听", "m̀h sīk téng", "听不懂", "Mh sik teng.", "I can't follow you."),
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

        # 词汇：Chinese + Jyutping + Chinese gloss + **English explanation** + Example
        v = Lesson(unit_id=unit.id, no=1, kind="vocabulary",
                   title_zh="📚 词汇", title_en="📚 Vocabulary")
        s = Lesson(unit_id=unit.id, no=2, kind="sentence",
                   title_zh="✏️ 句子", title_en="✏️ Sentences")
        q = Lesson(unit_id=unit.id, no=3, kind="quiz",
                   title_zh="📝 小测", title_en="📝 Quiz")
        for lesson in (v, s, q):
            db.session.add(lesson)
        db.session.flush()

        for surface, jp, cn, ex_yue, ex_en in items:
            pack = lookup("yue", "vocabulary", surface) or {}
            meaning_en = pack.get("meaning_en", "")
            phonetic = pack.get("phonetic", jp)
            for lesson in (v, s):
                _add(lesson.id, lesson.kind, surface, "cantonese",
                     phonetic=phonetic, meaning_cn=cn, meaning_en=meaning_en,
                     example_en=ex_en, example_yue=ex_yue or ex_yue)

            # 粤语单元小测：给题干（英文）+ 答案（粤语），保证 English 版可用
            _add(q.id, "quiz", f"Choose the Cantonese for: {meaning_en or cn}",
                 "cantonese", phonetic=phonetic, meaning_cn=surface,
                 meaning_en=f"The Cantonese word for “{meaning_en or cn}” is “{surface}”.",
                 example_en=f"{ex_yue} — {ex_en}", example_yue=ex_yue)
    db.session.commit()
    logger.info("Cantonese course seeded")


def _fill_lesson(lesson: Lesson, lang_code: str, topic: str,
                 words=None, yue_items=None) -> int:
    """给单个课时填入内容（幂等入口：只处理当前还没有内容的课时）。"""
    kind = lesson.kind
    words = words or []
    yue_items = yue_items or []

    if lang_code == "yue":
        added = 0
        for surface, jp, cn, ex_yue, ex_en in yue_items:
            pack = lookup("yue", kind, surface) or {}
            meaning_en = pack.get("meaning_en", "")
            phonetic = pack.get("phonetic", jp)
            if kind == "quiz":
                _add(lesson.id, "quiz",
                     f"Choose the Cantonese for: {meaning_en or cn}", topic,
                     phonetic=phonetic, meaning_cn=surface,
                     meaning_en=f"The Cantonese word for “{meaning_en or cn}” is “{surface}”.",
                     example_en=f"{ex_yue} — {ex_en}")
            else:
                _add(lesson.id, kind, surface, topic, phonetic=phonetic,
                     meaning_cn=cn, meaning_en=meaning_en,
                     example_en=ex_en, example_yue=ex_yue)
            added += 1
        return added

    # English
    if kind == "vocabulary":
        for w in words:
            _add(lesson.id, "vocabulary", w.word, topic,
                 **_fields("en", "vocabulary", w.word, phonetic=w.phonetic_uk,
                           pos=w.pos, meaning_cn=w.meaning_cn,
                           example_en=w.example_en, example_cn=w.example_cn,
                           audio=w.audio))
        return len(words)
    if kind == "phrase":
        for s, m, ex, excn in PHRASE_SEED:
            _add(lesson.id, "phrase", s, topic,
                 **_fields("en", "phrase", s, meaning_cn=m, example_en=ex,
                           example_cn=excn))
        return len(PHRASE_SEED)
    if kind == "sentence":
        for s, cn, gr in SENTENCE_SEED:
            _add(lesson.id, "sentence", s, topic,
                 **_fields("en", "sentence", s, meaning_cn=cn,
                           example_en=s, pos=gr))
        return len(SENTENCE_SEED)
    if kind == "grammar":
        for t, cn, ex in GRAMMAR_SEED:
            _add(lesson.id, "grammar", t, topic,
                 **_fields("en", "grammar", t, meaning_cn=cn, example_en=ex))
        return len(GRAMMAR_SEED)
    if kind in SKILL_HINTS:
        # 听说读写四课：同一套句子素材 + 各自的训练指令
        hints = SKILL_HINTS[kind]
        for i, (s, cn, gr) in enumerate(SENTENCE_SEED):
            hz, he = hints[i % len(hints)]
            _add(lesson.id, kind, s, topic,
                 **_fields("en", "sentence", s, meaning_cn=f"{hz}｜{cn}",
                           example_en=f"{he} — {s}"))
        return len(SENTENCE_SEED)
    if kind == "quiz":
        t, cn, ex = GRAMMAR_SEED[0]
        for hz, he in QUIZ_HINT:
            _add(lesson.id, "quiz", f"{he} · {t}", topic,
                 meaning_cn=f"{hz}｜{cn}", example_en=ex)
        return len(QUIZ_HINT)
    if kind == "review":
        for hz, he in REVIEW_HINT:
            _add(lesson.id, "review", he, topic, meaning_cn=hz, example_en="")
        return len(REVIEW_HINT)
    return 0


def fill_missing_content() -> dict:
    """给「已经有结构但没有内容」的课时补内容（幂等，可每次启动调用）。

    解决老数据里 111/186 课时为空壳的问题：不重建课程结构，只补内容，
    用户的进度与复习计划完全不受影响。
    """
    filled_lessons = 0
    filled_items = 0

    # 分级可能晚于词库导入才补齐（create_app 早期 words 表还是空的），
    # 取词前先幂等补一次，保证下面的 vocabulary 查询拿得到分级数据。
    try:
        from path_service import ensure_word_meta
        ensure_word_meta()
    except Exception as exc:  # pragma: no cover
        logger.warning("word_meta 补齐跳过：%s", exc)

    for course in Course.query.all():
        lang = course.language_code
        for unit in Unit.query.filter_by(course_id=course.id).all():
            words = []
            if lang == "en":
                words = _pick_words(course.cefr_level, unit.no)
            yue_items = []
            if lang == "yue" and 1 <= unit.no <= len(YUE_SEED):
                yue_items = YUE_SEED[unit.no - 1][3]

            topic = (unit.title_en or "general").lower().replace(" ", "_")
            lessons = (Lesson.query.filter_by(unit_id=unit.id)
                       .order_by(Lesson.no).all())
            for lesson in lessons:
                if ContentItem.query.filter_by(lesson_id=lesson.id).count():
                    continue
                n = _fill_lesson(lesson, lang, topic, words, yue_items)
                if n:
                    filled_items += n
                    filled_lessons += 1

            # 补齐**缺失的课时类型**（如 §4 新增的 reading / writing）。
            # 老库里已存在的 Unit 不会自动长出新课时，这里按需追加 ——
            # 只追加内容，不动已有课时，用户的进度与复习计划完全不受影响。
            # 粤语课程按自己的 3 课结构走，不套用英语的 10 类模板。
            if lang == "en" and lessons:
                have = {l.kind for l in lessons}
                next_no = max(l.no for l in lessons)
                for kind, l_zh, l_en, emoji in LESSON_PLAN:
                    if kind in have:
                        continue
                    next_no += 1
                    lesson = Lesson(unit_id=unit.id, no=next_no, kind=kind,
                                    title_zh=f"{emoji} {l_zh}", title_en=f"{emoji} {l_en}")
                    db.session.add(lesson)
                    db.session.flush()
                    n = _fill_lesson(lesson, lang, topic, words, yue_items)
                    if n:
                        filled_items += n
                        filled_lessons += 1
            db.session.commit()
    if filled_lessons:
        logger.info("已为 %s 个空课时补齐 %s 条内容", filled_lessons, filled_items)
    return {"lessons": filled_lessons, "items": filled_items}


def seed_courses():
    _seed_languages()
    _seed_english()
    _seed_cantonese()
