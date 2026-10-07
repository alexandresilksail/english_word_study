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
    Level("Pre-A1", 0, "入门预备", "Starter", "gray", "🌰", 0),
    Level("A1", 1, "入门", "Beginner", "violet", "🌱", 0),
    Level("A2", 2, "初级", "Elementary", "sky", "🌿", 0),
    Level("B1", 3, "中级", "Intermediate", "emerald", "🌳", 0),
    Level("B2", 4, "中高级", "Upper Intermediate", "amber", "🏔️", 0),
    Level("C1", 5, "高级", "Advanced", "rose", "🌋", 0),
    Level("C2", 6, "精通", "Proficiency", "indigo", "🏆", 0),
]
LEVEL_BY_CODE = {lv.code: lv for lv in LEVELS}

#: CEFR 等级沿「词频降序」的比例切片。
#:
#: 早期版本用的是绝对阈值（A1 ≥ 5000 分），但本库 Word.freq 的实际上限只有
#: 三位数，结果「绝大多数词都被挤到 B1 以下、Pre-A1/A1 一个词都没有」，
#: 课程取词时只能拿到 x/y/z 开头的低频陈列品。
#:
#: 改成**分位数**：把按词频降序的词库切成固定的百分比区间。
#: 越靠前（越常用）→ 越初级，这与直觉一致，且与词频量纲无关。
LEVEL_WEIGHTS: list[tuple[str, float]] = [
    ("Pre-A1", 0.06),   # 最常用的 6%：入门预备
    ("A1", 0.14),       # 6%–20%
    ("A2", 0.20),       # 20%–40%
    ("B1", 0.20),       # 40%–60%
    ("B2", 0.18),       # 60%–78%
    ("C1", 0.12),       # 78%–90%
    ("C2", 0.10),       # 90%–100%
]


def assign_by_quantile(sorted_words) -> dict[int, str]:
    """把已按词频排序好的词（**最常用的在前**），按 :data:`LEVEL_WEIGHTS` 切片成 CEFR 等级。

    传入的序列应由调用方按 ``Word.freq asc`` 排好（freq 越小＝越常用）。
    返回 ``{word_id: cefr_level}``；空列表返回空 dict。
    """
    out: dict[int, str] = {}
    n = len(sorted_words)
    if not n:
        return out
    cursor = 0
    for idx, (code, weight) in enumerate(LEVEL_WEIGHTS):
        if idx == len(LEVEL_WEIGHTS) - 1:
            take = n - cursor          # 最后一档吃下剩余，避免取整漏词
        else:
            take = max(1, int(round(n * weight)))
            take = min(take, n - cursor)
        for w in sorted_words[cursor:cursor + take]:
            out[w.id] = code
        cursor += take
        if cursor >= n:
            break
    # 极端小数据兜底：还有剩余就全部归到最后一档
    if cursor < n:
        for w in sorted_words[cursor:]:
            out[w.id] = LEVEL_WEIGHTS[-1][0]
    return out

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
