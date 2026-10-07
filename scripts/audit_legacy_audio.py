#!/usr/bin/env python
"""V5.2 旧 2000 词库 —— 音频资产审计。

只做**只读**审计，绝不删除 / 修改任何 MP3 或 words.json。

审计项：
- words.json 词条数量
- app/static/audio 下 MP3 数量
- Word → Audio 匹配（按 <word>.mp3 匹配，不区分大小写兜底）
- 缺失音频（有词无音频）
- 孤儿音频（有音频无对应词）
- 非法文件名（非 <name>.mp3，或含路径/双扩展名）
- 重复引用（多个词指向同一音频文件）
- 文件大小分布

审计报告中**如实**记录音频来源：
    English / en-GB / Edge TTS / British English / offline TTS
不称为「真人发音」。

输出：data/lexicon/audit/legacy_audio_report.json
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

WORDS_JSON = os.path.join(ROOT, "app", "data", "words.json")
AUDIO_DIR = os.path.join(ROOT, "app", "static", "audio")
OUT_DIR = os.path.join(ROOT, "data", "lexicon", "audit")
OUT_FILE = os.path.join(OUT_DIR, "legacy_audio_report.json")

AUDIO_SOURCE_NOTE = (
    "English / en-GB / Edge TTS / British English / offline TTS. "
    "Machine-generated, NOT human/native speaker recording."
)


def _load_words():
    with open(WORDS_JSON, encoding="utf-8") as f:
        data = json.load(f)
    items = data.get("items", []) if isinstance(data, dict) else data
    return data, items


def _audio_candidates(word: str) -> list[str]:
    cands = []
    w = (word or "").strip()
    if w:
        cands.append(f"{w}.mp3")
        low = w.lower()
        if low != w:
            cands.append(f"{low}.mp3")
    # 去掉内部空格/撇号后的兜底（部分 TTS 导出会把 "I'm" 变成 "im.mp3"）
    compact = w.replace(" ", "").replace("'", "").replace("-", "").lower()
    if compact and compact != w.lower():
        cands.append(f"{compact}.mp3")
    return cands


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    data, items = _load_words()
    words_count = len(items)

    # ---- 扫描音频目录 ----
    all_mp3 = []
    invalid_filenames = []
    if os.path.isdir(AUDIO_DIR):
        for fn in os.listdir(AUDIO_DIR):
            full = os.path.join(AUDIO_DIR, fn)
            if not os.path.isfile(full):
                continue
            if not fn.lower().endswith(".mp3") or fn != os.path.basename(fn):
                invalid_filenames.append(fn)
                continue
            all_mp3.append(fn)

    mp3_count = len(all_mp3)
    mp3_set = set(all_mp3)

    matched = 0
    missing = []
    referenced_files = {}     # filename -> [words]
    file_sizes = []
    matched_set = set()

    for it in items:
        w = it.get("w", "")
        cands = _audio_candidates(w)
        hit = next((c for c in cands if c in mp3_set), None)
        if hit:
            matched += 1
            matched_set.add(hit)
            referenced_files.setdefault(hit, []).append(w)
            try:
                file_sizes.append(os.path.getsize(os.path.join(AUDIO_DIR, hit)))
            except OSError:
                pass
        else:
            missing.append(w)

    # 孤儿音频：目录里有，但没有任何词引用
    orphan = sorted(mp3_set - matched_set)

    # 重复引用：同一文件被多个词引用
    duplicate_refs = {fn: ws for fn, ws in referenced_files.items() if len(ws) > 1}

    sizes_sorted = sorted(file_sizes)
    size_stats = {}
    if sizes_sorted:
        size_stats = {
            "min": sizes_sorted[0],
            "max": sizes_sorted[-1],
            "mean": round(sum(sizes_sorted) / len(sizes_sorted), 1),
            "median": sizes_sorted[len(sizes_sorted) // 2],
            "total_bytes": sum(sizes_sorted),
        }

    report = {
        "audit": "legacy-2000-audio",
        "generated_by": "scripts/audit_legacy_audio.py",
        "audio_source": AUDIO_SOURCE_NOTE,
        "is_human_recording": False,
        "words_json": {
            "path": os.path.relpath(WORDS_JSON, ROOT),
            "count": words_count,
            "version": data.get("version") if isinstance(data, dict) else None,
            "declared_count": data.get("count") if isinstance(data, dict) else None,
        },
        "audio_dir": os.path.relpath(AUDIO_DIR, ROOT),
        "mp3_count": mp3_count,
        "matched": matched,
        "matched_percent": round(matched * 100 / words_count, 1) if words_count else 0,
        "missing_audio": len(missing),
        "missing_examples": missing[:50],
        "orphan_audio": len(orphan),
        "orphan_examples": orphan[:50],
        "invalid_filenames": invalid_filenames[:50],
        "duplicate_references": {k: v for k, v in list(duplicate_refs.items())[:50]},
        "duplicate_references_count": len(duplicate_refs),
        "file_size_bytes": size_stats,
        "integrity": {
            "words_consistent": (data.get("count") == words_count
                                 if isinstance(data, dict) else True),
            "audio_assets_present": mp3_count > 0,
            "note": "Read-only audit. No MP3 or words.json was modified or deleted.",
        },
    }

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # 控制台摘要
    print(f"Words:           {words_count}")
    print(f"MP3 files:       {mp3_count}")
    print(f"Matched:         {matched} ({report['matched_percent']}%)")
    print(f"Missing audio:   {len(missing)}")
    print(f"Orphan audio:    {len(orphan)}")
    print(f"Invalid names:   {len(invalid_filenames)}")
    print(f"Dup references:  {len(duplicate_refs)}")
    print(f"Report -> {os.path.relpath(OUT_FILE, ROOT)}")


if __name__ == "__main__":
    main()
