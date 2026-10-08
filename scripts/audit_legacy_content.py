#!/usr/bin/env python
"""V5.2 旧 2000 词库 —— 内容质量 / 许可证 / 可生产性审计。

只做**只读**审计，绝不修改 words.json。

审计项（规格 §5）：
- meaning（释义）
- examples（例句，英文 + 中文）
- CEFR（旧数据只有考试标签 lv，如 cet4/gk/ielts，绝非 CEFR，故 CEFR 记 null）
- POS（词性）
- phonetic（音标）
- source / license / commercial_allowed / verified

输出：data/lexicon/audit/legacy_content_report.json

关键结论（诚实）：
- 旧 2000 词的来源无法从项目现有信息确认 → license = UNKNOWN
- 在许可证经人工核实前：commercial_allowed = false / production_ready = false
- CEFR 不可从 lv 考试标签可靠推导 → cefr = null，绝不编造
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

WORDS_JSON = os.path.join(ROOT, "app", "data", "words.json")
OUT_DIR = os.path.join(ROOT, "data", "lexicon", "audit")
OUT_FILE = os.path.join(OUT_DIR, "legacy_content_report.json")

POS_VALUES = {"n", "v", "vt", "vi", "adj", "adv", "prep", "pron",
              "conj", "num", "art", "int", "aux", "modal"}

# 旧数据的 `lv` 字段是「考试标签」（四六级 / 高考 / 雅思 / 考研 / 托福 / 中考），
# 绝非 CEFR 等级，也不可可靠映射到 CEFR，因此审计里明确记为「不可推导」。
EXAM_LABELS_SEEN = set()


def _completeness(it: dict) -> dict:
    cn = (it.get("cn") or "").strip()
    en = (it.get("en") or "").strip()
    zh = (it.get("zh") or "").strip()
    ipa = (it.get("ipa") or "").strip()
    pos = (it.get("pos") or "").strip().lower()
    lv = (it.get("lv") or "").strip()
    if lv:
        for part in lv.split("|"):
            EXAM_LABELS_SEEN.add(part.strip())
    return {
        "has_meaning": bool(cn),
        "has_example_en": bool(en),
        "has_example_zh": bool(zh),
        "has_phonetic": bool(ipa) and ipa.startswith("/") and ipa.endswith("/"),
        "has_pos": pos in POS_VALUES,
        "has_cefr": False,          # 旧数据无 CEFR，不可推导
        "has_source": True,         # 审计口径：source 固定 legacy-2000
        "has_license": True,        # 审计口径：license 固定 UNKNOWN
    }


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(WORDS_JSON, encoding="utf-8") as f:
        data = json.load(f)
    items = data.get("items", []) if isinstance(data, dict) else data
    total = len(items)

    c_sum = {k: 0 for k in ("has_meaning", "has_example_en", "has_example_zh",
                            "has_phonetic", "has_pos", "has_cefr",
                            "has_source", "has_license")}
    missing_examples = []
    pos_counter = {}
    no_cefr = total  # 全部无 CEFR

    for it in items:
        c = _completeness(it)
        for k, v in c.items():
            if v:
                c_sum[k] += 1
        pos = (it.get("pos") or "").strip().lower()
        pos_counter[pos] = pos_counter.get(pos, 0) + 1
        if not (it.get("en") or "").strip() or not (it.get("zh") or "").strip():
            if len(missing_examples) < 50:
                missing_examples.append(it.get("w", ""))

    pct = lambda n: round(n * 100 / total, 1) if total else 0

    report = {
        "audit": "legacy-2000-content",
        "generated_by": "scripts/audit_legacy_content.py",
        "total": total,
        "completeness": {k: {"count": v, "percent": pct(v)} for k, v in c_sum.items()},
        "cefr": {
            "derivable": False,
            "note": ("旧数据的 `lv` 字段为考试标签（cet4/gk/ielts/ky/toefl/zk 等），"
                     "并非 CEFR 等级，且无法可靠映射。CEFR 一律记 null，绝不编造。"),
            "rows_with_cefr": 0,
        },
        "pos_distribution": dict(sorted(pos_counter.items(), key=lambda kv: -kv[1])),
        "exam_labels_seen": sorted(EXAM_LABELS_SEEN),
        "license": {
            "source": "legacy-2000",
            "license": "UNKNOWN",
            "license_url": "",
            "attribution": "",
            "commercial_allowed": False,
            "redistribution_allowed": False,
            "verified": False,
            "production_ready": False,
            "note": ("旧 2000 词来源无法从项目现有信息确认，许可证标记为 UNKNOWN；"
                     "经人工核实前 commercial_allowed / redistribution_allowed / "
                     "production_ready 一律为 false，绝不可作为生产内容分发或商用。"),
        },
        "audio_source": ("English / en-GB / Edge TTS / British English / offline TTS "
                        "(machine-generated, NOT human recording)"),
        "integrity": {
            "note": "Read-only audit. No words.json field was modified.",
        },
    }

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"Total words:      {total}")
    print(f"Meaning:          {c_sum['has_meaning']} ({pct(c_sum['has_meaning'])}%)")
    print(f"Example EN:       {c_sum['has_example_en']} ({pct(c_sum['has_example_en'])}%)")
    print(f"Example ZH:       {c_sum['has_example_zh']} ({pct(c_sum['has_example_zh'])}%)")
    print(f"Phonetic valid:   {c_sum['has_phonetic']} ({pct(c_sum['has_phonetic'])}%)")
    print(f"POS valid:        {c_sum['has_pos']} ({pct(c_sum['has_pos'])}%)")
    print(f"CEFR derivable:   NO -> cefr = null")
    print(f"Exam labels:      {sorted(EXAM_LABELS_SEEN)}")
    print(f"License:          UNKNOWN (commercial_allowed=false, production_ready=false)")
    print(f"Report -> {os.path.relpath(OUT_FILE, ROOT)}")


if __name__ == "__main__":
    main()
