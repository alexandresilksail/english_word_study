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
#: 粤语课程 6 大单元（规格 §15：Greetings/Numbers/Time/Family/Daily Life/Conversation）
YUE_UNITS = [
    ("问候", "Greetings", "🇭🇰"),
    ("数字", "Numbers", "🔢"),
    ("时间", "Time", "🕐"),
    ("家庭", "Family", "👨‍👩‍👧"),
    ("日常", "Daily Life", "🍜"),
    ("基础对话", "Basic Conversation", "💬"),
]

#: 每单元 7 类课时，顺序即学习流（规格 §15 / V5.5）：
#: Vocabulary → Phrase → Sentence → Listening → Practice → Quiz → Review
YUE_LESSON_PLAN = [
    ("vocabulary", "📚 词汇", "📚 Vocabulary"),
    ("phrase", "💬 词组", "💬 Phrases"),
    ("sentence", "✏️ 句子", "✏️ Sentences"),
    ("listening", "🎧 听力", "🎧 Listening"),
    ("practice", "🗣️ 练习", "🗣️ Practice"),
    ("quiz", "📝 小测", "📝 Quiz"),
    ("review", "🔁 复习", "🔁 Review"),
]

#: 每条目：(surface 繁体, jyutping, 中文义, English explanation, 例句粤, 例句英)
YUE_VOCAB = {
    1: [
        ("你好", "nei5 hou2", "你好", "Hello", "你好！", "Neih hou!"),
        ("早晨", "zou2 san4", "早上好", "Good morning", "早晨！", "Zou san!"),
        ("唔該", "m4 goi1", "谢谢；麻烦你", "Thanks; excuse me", "唔該你。", "Mh goi nei."),
        ("多謝", "do1 ze6", "多谢", "Thank you", "多謝晒。", "Do ze saai."),
        ("再見", "zoi3 gin3", "再见", "Goodbye", "再見啦。", "Zoi gin laa."),
    ],
    2: [
        ("一", "jat1", "一", "one", "一。", "Jat go."),
        ("二", "ji6", "二", "two", "二。", "Ji go."),
        ("三", "saam1", "三", "three", "三。", "Saam go."),
        ("四", "sei3", "四", "four", "四。", "Sei go."),
        ("五", "ng5", "五", "five", "五。", "Ng go."),
        ("十", "sap6", "十", "ten", "十。", "Sap go."),
    ],
    3: [
        ("今日", "gam1 jat6", "今天", "today", "今日。", "Gam jat."),
        ("聽日", "ting1 jat6", "明天", "tomorrow", "聽日。", "Ting jat."),
        ("昨日", "zok6 jat6", "昨天", "yesterday", "昨日。", "Zok jat."),
        ("朝早", "ziu2 zou2", "早上", "morning", "朝早。", "Ziu zou."),
        ("夜晚", "je6 maan5", "晚上", "night", "夜晚。", "Je maan."),
    ],
    4: [
        ("爸爸", "baa1 baa1", "爸爸", "father", "爸爸。", "Baa baa."),
        ("媽媽", "maa1 maa1", "妈妈", "mother", "媽媽。", "Maa maa."),
        ("哥哥", "go1 go1", "哥哥", "older brother", "哥哥。", "Go go."),
        ("妹妹", "mui6 mui6", "妹妹", "younger sister", "妹妹。", "Mui mui."),
        ("屋企", "uk1 kei2", "家", "home", "屋企。", "Uk kei."),
    ],
    5: [
        ("食飯", "sik6 faan6", "吃饭", "eat (a meal)", "食飯。", "Sihk faan."),
        ("飲茶", "jam2 caa4", "喝茶", "drink tea", "飲茶。", "Jam caa."),
        ("瞓覺", "fan3 gaau3", "睡觉", "sleep", "瞓覺。", "Fan gaau."),
        ("返工", "faan1 gung1", "上班", "go to work", "返工。", "Faan gung."),
        ("買嘢", "maai5 je5", "买东西", "go shopping", "買嘢。", "Maai je."),
    ],
    6: [
        ("係", "hai6", "是", "yes / to be", "係。", "Hai."),
        ("唔係", "m4 hai6", "不是", "no / not", "唔係。", "Mh hai."),
        ("邊個", "bin1 go3", "谁", "who", "邊個？", "Bin go?"),
        ("點解", "dim2 gaai2", "为什么", "why", "點解？", "Dim gaai?"),
        ("唔該借借", "m4 goi1 ze3 ze3", "借过", "excuse me (to pass)", "唔該借借。", "Mh goi ze ze."),
    ],
}

YUE_PHRASE = {
    1: [
        ("你好嗎", "nei5 hou2 maa3", "你好吗", "How are you", "你好嗎？", "Neih hou maa?"),
        ("噉好呀", "gam2 hou2 aa3", "那很好", "That's good", "噉好呀。", "Gam hou aa."),
        ("唔該晒", "m4 goi1 saai3", "非常感谢", "Thanks a lot", "唔該晒你。", "Mh goi saai nei."),
    ],
    2: [
        ("幾多錢", "gei2 do1 cin2", "多少钱", "How much", "幾多錢？", "Gei do cin?"),
        ("一個", "jat1 go3", "一个", "one (classifier)", "一個。", "Jat go."),
        ("兩個", "loeng5 go3", "两个", "two (classifier)", "兩個。", "Loeng go."),
    ],
    3: [
        ("而家幾點", "ji4 gaa1 gei2 dim2", "现在几点", "What time is it", "而家幾點？", "Yi gaa gei dim?"),
        ("食咗飯未", "sik6 zo2 faan6 mei6", "吃饭了吗", "Have you eaten", "食咗飯未？", "Sihk zo faan mei?"),
        ("幾時見", "gei2 si4 gin3", "什么时候见", "See you when", "幾時見？", "Gei si gin?"),
    ],
    4: [
        ("屋企人", "uk1 kei2 jan4", "家人", "family members", "屋企人。", "Uk kei jan."),
        ("我屋企", "ngo5 uk1 kei2", "我家", "my home", "我屋企。", "Ngo uk kei."),
        ("幾多兄弟姐妹", "gei2 do1 hi1 dai6 zi2 mui6", "多少兄弟姐妹", "how many siblings",
         "幾多兄弟姐妹？", "Gei do hi dai zi mui?"),
    ],
    5: [
        ("去邊度", "heoi3 bin1 dou6", "去哪里", "where are you going", "去邊度？", "Heoi bin dou?"),
        ("做緊咩", "zou6 gan2 me1", "在做什么", "what are you doing", "做緊咩？", "Zou gan me?"),
        ("休息一下", "jau1 sik1 jat1 haa5", "休息一下", "take a rest", "休息一下。", "Jau sik jat haa."),
    ],
    6: [
        ("你係邊度人", "nei5 hai6 bin1 dou6 jan4", "你是哪里人", "where are you from",
         "你係邊度人？", "Nei hai bin dou jan?"),
        ("我唔明", "ngo5 m4 ming4", "我不懂", "I don't understand", "我唔明。", "Ngo mh ming."),
        ("可唔可以", "ho2 m4 ho2 ji5", "可不可以", "may I", "可唔可以？", "Ho m ho yi?"),
    ],
}

YUE_SENTENCE = {
    1: [
        ("我係Alexander", "ngo5 hai6 Alexander", "我是 Alexander", "I am Alexander",
         "我係 Alexander。", "Ngo hai6 Alexander."),
        ("你叫咩名", "nei5 giu3 me1 meng2", "你叫什么名字", "What is your name",
         "你叫咩名？", "Nei giu me meng?"),
        ("好高興識你", "hou2 gou1 hing3 sik1 nei5", "很高兴认识你", "Nice to meet you",
         "好高興識你。", "Hou gou hing sik nei."),
    ],
    2: [
        ("我有三個蘋果", "ngo5 jau5 saam1 go3 ping4 gwo2", "我有三个苹果", "I have three apples",
         "我有三個蘋果。", "Ngo jau saam go ping gwo."),
        ("呢度有幾多人", "ne1 dou6 jau5 gei2 do1 jan4", "这里有多少人", "How many people are here",
         "呢度有幾多人？", "Ne dou jau gei do jan?"),
        ("我要五本書", "ngo5 jiu3 ng5 bun2 syu1", "我要五本书", "I want five books",
         "我要五本書。", "Ngo jiu ng bun syu."),
    ],
    3: [
        ("我朝早返工", "ngo5 ziu2 zou2 faan1 gung1", "我早上上班", "I go to work in the morning",
         "我朝早返工。", "Ngo ziu zou faan gung."),
        ("佢聽日嚟", "keoi5 ting1 jat6 lai4", "他明天来", "He is coming tomorrow",
         "佢聽日嚟。", "Keoi ting jat lai."),
        ("我今日好忙", "ngo5 gam1 jat6 hou2 mong4", "我今天很忙", "I am busy today",
         "我今日好忙。", "Ngo gam jat hou mong."),
    ],
    4: [
        ("我爸爸係老師", "ngo5 baa1 baa1 hai6 lou5 si1", "我爸爸是老师", "My father is a teacher",
         "我爸爸係老師。", "Ngo baa baa hai lou si."),
        ("佢有兩個妹妹", "keoi5 jau5 loeng5 go3 mui6 mui6", "她有两个妹妹", "She has two younger sisters",
         "佢有兩個妹妹。", "Keoi jau loeng go mui mui."),
        ("我愛我屋企", "ngo5 oi3 ngo5 uk1 kei2", "我爱我的家", "I love my family",
         "我愛我屋企。", "Ngo oi ngo uk kei."),
    ],
    5: [
        ("我每日飲茶", "ngo5 mui5 jat6 jam2 caa4", "我每天喝茶", "I drink tea every day",
         "我每日飲茶。", "Ngo mui jat jam caa."),
        ("佢夜晚瞓覺早", "keoi5 je6 maan5 fan3 gaau3 zou2", "他晚上早睡", "He sleeps early at night",
         "佢夜晚瞓覺早。", "Keoi je maan fan gaau zou."),
        ("我哋去買嘢", "ngo5 dei6 heoi3 maai5 je5", "我们去买东西", "We go shopping",
         "我哋去買嘢。", "Ngo dei heoi maai je."),
    ],
    6: [
        ("我係學生", "ngo5 hai6 hok6 saang1", "我是学生", "I am a student",
         "我係學生。", "Ngo hai hok saang."),
        ("你講乜嘢", "nei5 gong2 mat1 je5", "你说什么", "what are you saying",
         "你講乜嘢？", "Nei gong mat je?"),
        ("唔該幫我", "m4 goi1 bong1 ngo5", "请帮我", "please help me",
         "唔該幫我。", "Mh goi bong ngo."),
    ],
}


def _yue_unit_items(unit_no: int):
    """返回某单元的 (vocab, phrase, sentence) 三元组。"""
    return (YUE_VOCAB.get(unit_no, []), YUE_PHRASE.get(unit_no, []),
            YUE_SENTENCE.get(unit_no, []))


def _add_cantonese_item(lesson_id, kind, item, topic="cantonese", audio=""):
    """把一条 (surface, jyutping, 中文义, English, 例句粤, 例句英) 写入 ContentItem。

    粤语数据特征（规格 §15）：
    - surface = 繁体中文词面
    - phonetic = Jyutping（粤拼）
    - meaning_cn / meaning_yue = 中文义（繁体）
    - meaning_en = 英文解释（保证 English 版面零中文依赖）
    - example_yue / example_en = 例句（粤 / 英）
    - audio：粤语目前无本地录音资产，留空，由 V5.8 的 TTS/STT 适配层在运行时提供
    """
    surface, jp, cn, en, ex_yue, ex_en = item
    return ContentItem(
        lesson_id=lesson_id, kind=kind, surface=surface, phonetic=jp,
        meaning_cn=cn, meaning_yue=cn, meaning_en=en,
        example_yue=ex_yue, example_en=ex_en, topic=topic, audio=audio,
    )


def _fill_cantonese_lesson(lesson: Lesson, vocab, phrase, sentence) -> int:
    """给单个粤语课时按 kind 填入内容（幂等：调用方负责跳过已有内容的课时）。"""
    kind = lesson.kind
    items, added = [], 0
    if kind == "vocabulary":
        items = vocab
    elif kind == "phrase":
        items = phrase
    elif kind == "sentence":
        items = sentence
    elif kind == "listening":
        items = sentence
    elif kind == "practice":
        items = phrase + sentence
    elif kind == "quiz":
        for surface, jp, cn, en, ex_yue, ex_en in vocab + phrase + sentence:
            db.session.add(ContentItem(
                lesson_id=lesson.id, kind="quiz",
                surface=f"Choose the Cantonese for: {en or cn}",
                phonetic=jp, meaning_cn=surface,
                meaning_en=f"The Cantonese for “{en or cn}” is “{surface}”.",
                example_yue=ex_yue, example_en=f"{ex_yue} — {ex_en}", topic="cantonese"))
            added += 1
        return added
    elif kind == "review":
        items = vocab + phrase
    else:
        return 0

    for it in items:
        db.session.add(_add_cantonese_item(lesson.id, kind, it))
        added += 1
    return added


def _seed_cantonese():
    if Course.query.filter_by(language_code="yue").count():
        return
    course = Course(language_code="yue", cefr_level="A1",
                    title_zh="粤语入门", title_en="Cantonese Beginner",
                    description="Cantonese with Jyutping · 粤语拼音起步", color="rose", sort=1)
    db.session.add(course); db.session.flush()

    for u_no, (u_zh, u_en, emoji) in enumerate(YUE_UNITS, start=1):
        unit = Unit(course_id=course.id, no=u_no, title_zh=u_zh, title_en=u_en, emoji=emoji)
        db.session.add(unit); db.session.flush()
        vocab, phrase, sentence = _yue_unit_items(u_no)
        for l_no, (kind, l_zh, l_en) in enumerate(YUE_LESSON_PLAN, start=1):
            lesson = Lesson(unit_id=unit.id, no=l_no, kind=kind,
                            title_zh=l_zh, title_en=l_en)
            db.session.add(lesson); db.session.flush()
            _fill_cantonese_lesson(lesson, vocab, phrase, sentence)
    db.session.commit()
    logger.info("Cantonese course seeded (7 lessons/unit)")


def _upgrade_cantonese():
    """对已存在的粤语课程做**增量**升级（绝不删除既有课时/内容，避免误伤用户复习计划）。

    - 补齐缺失的课时类型（phrase/listening/practice/review），重排 no 至学习流顺序；
    - 仅给「尚无内容」的课时补内容（已有内容的 vocabulary/sentence/quiz 原样保留）。
    """
    course = Course.query.filter_by(language_code="yue").first()
    if not course:
        return
    target_no = {kind: i + 1 for i, (kind, _, _) in enumerate(YUE_LESSON_PLAN)}
    have_kinds = {l.kind for l in Lesson.query.join(Unit).filter(Unit.course_id == course.id).all()}
    need_kinds = [k for k in target_no if k not in have_kinds]
    if not need_kinds:
        return

    units = Unit.query.filter_by(course_id=course.id).order_by(Unit.no).all()
    for unit in units:
        lessons = {l.kind: l for l in Lesson.query.filter_by(unit_id=unit.id).all()}
        vocab, phrase, sentence = _yue_unit_items(unit.no)

        # 1) 现有课时先挪到 +100 的临时编号，释放 1..7 以免唯一约束冲突
        for les in lessons.values():
            les.no = (target_no.get(les.kind) or les.no) + 100
        db.session.commit()

        # 2) 创建缺失课时（占用目标编号 1..7）
        for kind in need_kinds:
            _, l_zh, l_en = next(p for p in YUE_LESSON_PLAN if p[0] == kind)
            les = Lesson(unit_id=unit.id, no=target_no[kind], kind=kind,
                         title_zh=l_zh, title_en=l_en)
            db.session.add(les); db.session.flush()
            lessons[kind] = les

        # 3) 现有课时落回目标编号
        for les in lessons.values():
            les.no = target_no[les.kind]
        db.session.commit()

        # 4) 仅给尚无内容的课时补内容
        for les in lessons.values():
            if ContentItem.query.filter_by(lesson_id=les.id).count():
                continue
            _fill_cantonese_lesson(les, vocab, phrase, sentence)
            db.session.commit()
    logger.info("Cantonese course upgraded: added lesson kinds %s", need_kinds)


def _fill_lesson(lesson: Lesson, lang_code: str, topic: str,
                 words=None, yue_items=None) -> int:
    """给单个课时填入内容（幂等入口：只处理当前还没有内容的课时）。"""
    kind = lesson.kind
    words = words or []
    yue_items = yue_items or []

    if lang_code == "yue":
        # 委托给粤语专用填充逻辑（按 7 类课时生成 jyutping/繁体/英文/例句）
        vocab, phrase, sentence = _yue_unit_items(lesson.unit.no)
        return _fill_cantonese_lesson(lesson, vocab, phrase, sentence)

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

            topic = (unit.title_en or "general").lower().replace(" ", "_")
            lessons = (Lesson.query.filter_by(unit_id=unit.id)
                       .order_by(Lesson.no).all())
            for lesson in lessons:
                if ContentItem.query.filter_by(lesson_id=lesson.id).count():
                    continue
                n = _fill_lesson(lesson, lang, topic, words)
                if n:
                    filled_items += n
                    filled_lessons += 1

            # 补齐**缺失的课时类型**（如 §4 新增的 reading / writing）。
            # 老库里已存在的 Unit 不会自动长出新课时，这里按需追加 ——
            # 只追加内容，不动已有课时，用户的进度与复习计划完全不受影响。
            # 粤语课程按自己的 7 课结构走（由 _upgrade_cantonese 负责补齐）。
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
                    n = _fill_lesson(lesson, lang, topic, words)
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
    _upgrade_cantonese()  # 增量补齐粤语缺失课时（phrase/listening/practice/review）
