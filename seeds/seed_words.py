"""把 data/words.json 导入 words 表（幂等）。

用法：
    flask seed-words          # 或
    python seeds/seed_words.py
"""
from __future__ import annotations

import json
import re
import os
import sys
from collections import Counter

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

DEFAULT_JSON = os.path.join(BASE_DIR, "app", "data", "words.json")


def load_items(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data["items"] if isinstance(data, dict) else data


def validate(items: list[dict]) -> list[str]:
    """入库前校验：A-Z 覆盖、无重复、无空字段、无占位符。"""
    problems: list[str] = []

    seen = Counter(i["w"].strip().lower() for i in items)
    dup = [w for w, n in seen.items() if n > 1]
    if dup:
        problems.append(f"存在重复单词：{dup[:10]}")

    letters = {i["w"][0].lower() for i in items}
    missing = sorted(set("abcdefghijklmnopqrstuvwxyz") - letters)
    if missing:
        problems.append(f"缺少首字母：{missing}")

    # 占位符检查只针对释义与例句；word 本身是合法英文单词（如 test）不算占位数据
    placeholders = ("todo", "tbd", "xxx", "placeholder", "待定", "略")
    for i in items:
        for key in ("w", "ipa", "cn", "en", "zh"):
            val = (i.get(key) or "").strip()
            if not val:
                problems.append(f"{i.get('w')} 字段 {key} 为空")
            if key in ("cn", "en", "zh") and val.lower() in placeholders:
                problems.append(f"{i.get('w')} 字段 {key} 含占位符: {val}")
            if "???" in val or val.count("?") >= 3:
                problems.append(f"{i.get('w')} 字段 {key} 疑似未填写: {val}")
            if key == "ipa" and not re.search(r"[a-z\u0250-\u02af\u0300-\u035c]", val):
                problems.append(f"{i.get('w')} 音标格式异常: {val}")
        if not (i.get("ipa") or "").strip("/"):
            problems.append(f"{i.get('w')} 缺少音标")
    return problems


def seed_from_json(path: str = DEFAULT_JSON, strict: bool = False) -> tuple[int, int]:
    from extensions import db
    from models import Word

    items = load_items(path)
    problems = validate(items)
    if problems:
        for p in problems[:20]:
            print("⚠ " + p)
        if strict:
            raise ValueError(f"词库校验未通过，共 {len(problems)} 项")
    if not problems:
        print(f"✔ 词库校验通过：{len(items)} 条，A-Z 完整，无重复，无空数据")

    db.create_all()
    created = updated = 0
    for it in items:
        word = (it["w"] or "").strip().lower()
        if not word:
            continue
        audio = it.get("audio") or f"{word}.mp3"
        row = Word.query.filter_by(word=word).first()
        payload = dict(
            initial=word[0].lower(),
            phonetic_uk=(it.get("ipa") or "").strip(),
            pos=(it.get("pos") or "").strip(),
            meaning_cn=(it.get("cn") or "").strip(),
            example_en=(it.get("en") or "").strip(),
            example_cn=(it.get("zh") or "").strip(),
            audio=audio,
            level=(it.get("lv") or "").strip(),
            freq=int(it.get("freq") or 0),
        )
        if row is None:
            db.session.add(Word(word=word, **payload))
            created += 1
        else:
            changed = False
            for k, v in payload.items():
                if getattr(row, k) != v:
                    setattr(row, k, v)
                    changed = True
            updated += 1 if changed else 0
    db.session.commit()
    return created, updated


if __name__ == "__main__":
    from app import create_app

    application = create_app()
    with application.app_context():
        c, u = seed_from_json()
        print(f"✔ 导入完成：新增 {c} 条 / 更新 {u} 条")
