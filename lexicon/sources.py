"""数据源适配器。每个适配器 yield 原始条目 dict（字段尽量贴近 LexiconEntry）。

可用 source：
- wordfreq       : 英文频率（需 pip install wordfreq，且仅在可联网取数环境使用）
- cc-canto       : 粤语 CC-Canto（数据置于 data/lexicon/raw/cc-canto/*.jsonl）
- words-hk       : 粤语 words.hk（数据置于 data/lexicon/raw/words-hk/*.jsonl；许可需确认）
- synthetic-dev  : 仅开发 / 性能 / 测试用，明确为非生产数据（commercial_allowed=False）
"""
from __future__ import annotations

import json
import os

_RAW_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "lexicon", "raw"))

# 粤语 6 大主题（规格要求至少覆盖）
_YUE_TOPICS = ["Greetings", "Numbers", "Time", "Family", "Daily Life", "Conversation"]
# 真实风格的粤语种子（自撰 / 公开可核对，Jyutping 保留）
_YUE_SAMPLES = [
    ("你好", "nei5 hou2", "Greetings", "Hello", "你好", "你好，早晨。", "你好，早晨。"),
    ("早晨", "zou2 san4", "Greetings", "Good morning", "早晨", "早晨，你食咗早餐未？", "早晨，你吃了早餐没有？"),
    ("多谢", "do1 ze6", "Greetings", "Thank you", "多谢", "多谢你嘅帮忙。", "多谢你的帮忙。"),
    ("唔該", "m4 goi1", "Greetings", "Thanks / Excuse me", "唔该", "唔該，借借。", "唔该，借过。"),
    ("再见", "zoi3 gin3", "Greetings", "Goodbye", "再见", "下次再见啦。", "下次再见啦。"),
    ("一", "jat1", "Numbers", "one", "一", "我有一个苹果。", "我有一个苹果。"),
    ("二", "ji6", "Numbers", "two", "二", "佢哋有两个细路。", "他们有两个小孩。"),
    ("三", "saam1", "Numbers", "three", "三", "三本书喺枱上面。", "三本书在桌上。"),
    ("十", "sap6", "Numbers", "ten", "十", "十个人入嚟。", "十个人进来。"),
    ("今日", "gam1 jat6", "Time", "today", "今日", "今日天氣好好。", "今天天气很好。"),
    ("聽日", "ting1 jat6", "Time", "tomorrow", "明天", "聽日去邊度？", "明天去哪里？"),
    ("朝早", "ziu2 zou2", "Time", "morning", "早上", "朝早我要返工。", "早上我要上班。"),
    ("夜晚", "je6 maan5", "Time", "night", "晚上", "夜晚睇書。", "晚上看书。"),
    ("爸", "baa1", "Family", "father", "爸", "我爸係老師。", "我爸是老师。"),
    ("媽", "maa1", "Family", "mother", "妈", "我媽煮飯好好食。", "我妈做饭很好吃。"),
    ("哥", "go1", "Family", "older brother", "哥", "我哥喺英國讀書。", "我哥在英国读书。"),
    ("妹", "mui6", "Family", "younger sister", "妹", "我妹好叻。", "我妹很厉害。"),
    ("食飯", "sik6 faan6", "Daily Life", "eat (a meal)", "吃饭", "我哋一齊食飯啦。", "我们一起吃饭吧。"),
    ("瞓覺", "fan3 gaau3", "Daily Life", "sleep", "睡觉", "夜晚早啲瞓覺。", "晚上早点睡觉。"),
    ("返工", "faan1 gung1", "Daily Life", "go to work", "上班", "朝早返工。", "早上上班。"),
    ("傾偈", "king4 gai2", "Conversation", "chat", "聊天", "我哋傾偈好開心。", "我们聊天很开心。"),
    ("講嘢", "gong2 je5", "Conversation", "speak", "说话", "佢講嘢好快。", "他说话很快。"),
]


def iter_wordfreq(language_code="en", limit=None):
    try:
        import wordfreq  # type: ignore
    except ImportError:
        raise RuntimeError(
            "wordfreq 未安装或无法导入。请在可联网环境执行 `pip install wordfreq` 后使用本源；"
            "当前沙箱禁止联网，请改用 --source synthetic-dev 做开发验证。"
        )
    if language_code != "en":
        raise ValueError("wordfreq 仅支持英文（en）")
    n = limit or 5000
    words = wordfreq.top(n, "en")
    for i, w in enumerate(words):
        freq = wordfreq.zipf_frequency(w, "en")
        yield {
            "language_code": "en", "surface": w, "lemma": w, "kind": "vocabulary",
            "pos": "", "pronunciation": "", "jyutping": "",
            "meaning_en": "", "meaning_zh": "", "example_en": "", "example_zh": "",
            "frequency": freq, "frequency_rank": i + 1,
            "cefr": "", "cefr_source": "", "topic": "general",
            "source": "wordfreq", "source_id": f"wf-{i + 1}",
        }


def iter_cc_canto(language_code="yue", limit=None):
    if language_code != "yue":
        raise ValueError("cc-canto 仅支持粤语（yue）")
    d = os.path.join(_RAW_DIR, "cc-canto")
    if not os.path.isdir(d):
        raise RuntimeError(
            "未找到 CC-Canto 数据目录 data/lexicon/raw/cc-canto/。"
            "请按 CC-Canto（CC BY 4.0）许可放置 .jsonl 数据后再导入。"
        )
    count = 0
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".jsonl"):
            continue
        with open(os.path.join(d, fn), "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                yield _cc_canto_entry(row)
                count += 1
                if limit and count >= limit:
                    return


def _cc_canto_entry(row):
    surface = row.get("word") or row.get("surface") or ""
    return {
        "language_code": "yue", "surface": surface, "lemma": surface,
        "kind": "vocabulary", "pos": (row.get("pos") or "").strip(),
        "pronunciation": "", "jyutping": row.get("jyutping", ""),
        "meaning_en": row.get("gloss_en", ""), "meaning_zh": row.get("gloss_zh", ""),
        "example_en": row.get("example_en", ""), "example_zh": row.get("example_zh", ""),
        "frequency": None, "frequency_rank": None,
        "cefr": "", "cefr_source": "", "topic": row.get("topic", "general"),
        "source": "cc-canto", "source_id": str(row.get("id") or row.get("source_id") or ""),
    }


def iter_words_hk(language_code="yue", limit=None):
    if language_code != "yue":
        raise ValueError("words.hk 仅支持粤语（yue）")
    d = os.path.join(_RAW_DIR, "words-hk")
    if not os.path.isdir(d):
        raise RuntimeError(
            "未找到 words.hk 数据目录 data/lexicon/raw/words-hk/。"
            "words.hk 许可需确认（部分为 CC BY-NC），未经确认前 commercial_allowed 一律 False。"
        )
    count = 0
    for fn in sorted(os.listdir(d)):
        if not fn.endswith(".jsonl"):
            continue
        with open(os.path.join(d, fn), "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                yield {
                    "language_code": "yue",
                    "surface": row.get("word") or row.get("surface") or "",
                    "lemma": row.get("word") or row.get("surface") or "",
                    "kind": "vocabulary", "pos": (row.get("pos") or "").strip(),
                    "pronunciation": "", "jyutping": row.get("jyutping", ""),
                    "meaning_en": row.get("explanation_en", ""),
                    "meaning_zh": row.get("explanation_zh", row.get("explanation", "")),
                    "example_en": row.get("example_en", ""),
                    "example_zh": row.get("example_zh", ""),
                    "frequency": None, "frequency_rank": None,
                    "cefr": "", "cefr_source": "", "topic": row.get("topic", "general"),
                    "source": "words-hk", "source_id": str(row.get("id") or ""),
                }
                count += 1
                if limit and count >= limit:
                    return


def iter_synthetic_dev(language_code, limit=None):
    limit = limit or (5000 if language_code == "en" else 2000)
    if language_code == "en":
        yield from _iter_synthetic_en(limit)
    elif language_code == "yue":
        yield from _iter_synthetic_yue(limit)
    else:
        raise ValueError(f"unsupported language_code: {language_code}")


def _iter_synthetic_en(limit):
    cefr_bands = ["Pre-A1", "A1", "A1", "A2", "A2", "B1", "B1", "B2", "C1", "C2"]
    for i in range(limit):
        idx = i + 1
        w = f"enword{idx}"
        yield {
            "language_code": "en", "surface": w, "lemma": w, "kind": "vocabulary",
            "pos": "n", "pronunciation": f"/ɛnwɜːd{idx}/", "jyutping": "",
            "meaning_en": f"synthetic English entry #{idx} (DEV PLACEHOLDER — not for production)",
            "meaning_zh": f"合成英文词条 #{idx}（开发占位，非生产数据）",
            "example_en": f"She used {w} in a sentence.", "example_zh": f"她在一个句子里用了 {w}。",
            "frequency": round(1000.0 / (idx + 1), 4), "frequency_rank": idx,
            "cefr": cefr_bands[i % len(cefr_bands)], "cefr_source": "synthetic-dev-heuristic",
            "topic": "general", "source": "synthetic-dev", "source_id": f"dev-en-{idx}",
        }


def _iter_synthetic_yue(limit):
    n = len(_YUE_SAMPLES)
    for i in range(limit):
        base = _YUE_SAMPLES[i % n]
        surface, jyut, topic_en, mean_en, mean_zh, ex_en, ex_zh = base
        suffix = "" if i < n else f"-{i}"
        kind = "vocabulary" if topic_en in ("Greetings", "Numbers", "Time", "Family", "Daily Life") else "phrase"
        if i % 7 == 0:
            kind = "sentence"
        yield {
            "language_code": "yue",
            "surface": surface + suffix, "lemma": surface,
            "kind": kind, "pos": "n" if kind == "vocabulary" else "",
            "pronunciation": "", "jyutping": jyut + suffix,
            "meaning_en": mean_en, "meaning_zh": mean_zh,
            "example_en": ex_en, "example_zh": ex_zh,
            "frequency": round(1000.0 / (i + 1), 4), "frequency_rank": i + 1,
            "cefr": "A1", "cefr_source": "synthetic-dev-heuristic",
            "topic": topic_en, "source": "synthetic-dev", "source_id": f"dev-yue-{i + 1}",
        }


def get_source(language_code, source, limit=None):
    source = (source or "").lower()
    if source == "wordfreq":
        return iter_wordfreq(language_code, limit)
    if source == "cc-canto":
        return iter_cc_canto(language_code, limit)
    if source == "words-hk":
        return iter_words_hk(language_code, limit)
    if source == "synthetic-dev":
        return iter_synthetic_dev(language_code, limit)
    raise ValueError(f"unknown source: {source}")
