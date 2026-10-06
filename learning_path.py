"""V5 多语言学习平台核心架构。

设计目标（对齐产品方向：从 English Vocabulary Website → Multilingual Learning Platform）
------------------------------------------------------------------------------------
    Language → Learning Path → Level → Unit → Lesson → Practice → Quiz / Game → XP / Streak

本模块目前只落地 English 课程（A1–C2），但：
1. LEARNING_LANGUAGES 是未来课程目录（粤语 / 日本語 / Español …）的唯一入口；
2. 词库分级写入 WordMeta.learning_language，新语言直接加行，不改引擎；
3. 路由保留 /path/<lang> 形式，下一步扩展粤语课程时只加数据。

学习阶梯（CEFR）
----------------
    A1 Beginner → A2 Elementary → B1 Intermediate →
    B2 Upper Intermediate → C1 Advanced → C2 Proficiency

词库分级
--------
基于 Word.freq（词频分）程序化映射到 CEFR 等级，再按词频顺序均分到单元：
不改动 2000 词任何原有字段，分级结果写入 word_meta 表（新增表，幂等回填）。
"""
from __future__ import annotations

from dataclasses import dataclass

# --------------------------------------------------------------------------
# 课程目录（未来扩展：yue 粤语 / ja 日语 / es 西语 …）
# --------------------------------------------------------------------------
LEARNING_LANGUAGES = [
    {"code": "en", "name_zh": "英语", "name_en": "English", "flag": "🇬🇧", "ready": True},
    {"code": "yue", "name_zh": "粤语", "name_en": "Cantonese", "flag": "🇭🇰", "ready": False},
    {"code": "ja", "name_zh": "日语", "name_en": "Japanese", "flag": "🇯🇵", "ready": False},
    {"code": "es", "name_zh": "西班牙语", "name_en": "Spanish", "flag": "🇪🇸", "ready": False},
    {"code": "fr", "name_zh": "法语", "name_en": "French", "flag": "🇫🇷", "ready": False},
    {"code": "de", "name_zh": "德语", "name_en": "German", "flag": "🇩🇪", "ready": False},
    {"code": "ko", "name_zh": "韩语", "name_en": "Korean", "flag": "🇰🇷", "ready": False},
]

# --------------------------------------------------------------------------
# CEFR 等级阶梯
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Level:
    code: str           # A1..C2
    order: int          # 0..5
    name_zh: str
    name_en: str
    color: str         # v4.css 调色板里的技能色
    emoji: str
    # 词频分阈值（>= 该分进入本档；按降序：越常用越初级）
    freq_min: int

LEVELS: list[Level] = [
    Level("A1", 0, "入门", "Beginner", "violet", "🌱", 5000),
    Level("A2", 1, "初级", "Elementary", "sky", "🌿", 2000),
    Level("B1", 2, "中级", "Intermediate", "emerald", "🌳", 800),
    Level("B2", 3, "中高级", "Upper Intermediate", "amber", "🏔️", 300),
    Level("C1", 4, "高级", "Advanced", "rose", "🌋", 100),
    Level("C2", 5, "精通", "Proficiency", "indigo", "🏆", 0),
]
LEVEL_BY_CODE = {lv.code: lv for lv in LEVELS}

# 每个等级 3 个单元（Duolingo 式主题）
UNIT_THEMES = {
    "A1": [("问候与介绍", "Greetings"), ("日常生活", "Daily Life"), ("家人与朋友", "Family")],
    "A2": [("旅行出行", "Travel"), ("购物饮食", "Shopping & Food"), ("工作与学习", "Work & Study")],
    "B1": [("观点表达", "Opinions"), ("文化与节日", "Culture"), ("健康与运动", "Health")],
    "B2": [("社会话题", "Society"), ("职场进阶", "Career"), ("媒体与科技", "Media")],
    "C1": [("抽象讨论", "Abstraction"), ("文学艺术", "Arts"), ("时事辩论", "Debates")],
    "C2": [("精通运用", "Mastery"), ("学术写作", "Academic"), ("跨文化交流", "Cross-culture")],
}

UNITS_PER_LEVEL = 3

# 解锁门槛：上一等级掌握率 >= 此值才解锁下一级
UNLOCK_RATIO = 0.5


def cefr_from_freq(freq: int) -> str:
    """词频分 → CEFR 等级。越常用 → 越初级。"""
    for lv in LEVELS:
        if freq >= lv.freq_min:
            return lv.code
    return "C2"


def level_word_count(word_rows) -> dict[str, int]:
    """统计每个 CEFR 等级的单词数。word_rows: Iterable[Word]"""
    counts = {lv.code: 0 for lv in LEVELS}
    for w in word_rows:
        counts[cefr_from_freq(w.freq)] += 1
    return counts
