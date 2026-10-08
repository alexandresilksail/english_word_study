#!/usr/bin/env python
"""V5.2 旧 2000 词库 → LexiconEntry 导入。

把 app/data/words.json（旧 2000 词）导入主词库表 LexiconEntry，来源固定为
``legacy-2000``。这是**真实**历史数据，绝不伪装成 ``synthetic-dev``。

安全 / 诚实约束（规格 §3 / §5 / §36）：
- license = UNKNOWN（来源无法从项目现有信息确认）
- commercial_allowed = False / redistribution_allowed = False
- verified = False / production_ready = False（许可证核实前绝不可进入生产内容）
- cefr = ''（旧数据只含考试标签 lv，绝非 CEFR，绝不编造）
- meaning_en 留空（旧数据仅有中文释义，不伪造英文释义）
- 默认值 batch-size = 250（2C2G / SQLite 友好）

幂等：按 (language_code, normalized, kind, pos) 去重，已存在的条目跳过，可重复运行。

用法：
  python scripts/import_legacy_lexicon.py --dry-run
  python scripts/import_legacy_lexicon.py --limit 100
  python scripts/import_legacy_lexicon.py --limit 2000 --batch-size 250
  python scripts/import_legacy_lexicon.py --db-uri sqlite:///path/to/app.db
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from sqlalchemy import inspect, text  # noqa: E402

from config import Config  # noqa: E402
from lexicon import import_entries, make_session, normalize  # noqa: E402
from models import LexiconEntry  # noqa: E402

DATABASE_URL = Config.DATABASE_URL

WORDS_JSON = os.path.join(ROOT, "app", "data", "words.json")
SOURCE = "legacy-2000"
LICENSE = "UNKNOWN"


def _ensure_production_ready(engine) -> None:
    """对**已存在**的 lexicon_entries 表补 production_ready 列（幂等）。

    make_session 的 create_all 只能建新表；老库需要 ALTER 补列，否则插入会因
    缺列失败。逻辑与 schema_compat 一致。
    """
    insp = inspect(engine)
    if "lexicon_entries" not in insp.get_table_names():
        return
    cols = {c["name"] for c in insp.get_columns("lexicon_entries")}
    if "production_ready" in cols:
        return
    dialect = engine.dialect.name
    ddl = "BOOLEAN DEFAULT 0 NOT NULL" if dialect != "postgresql" else "BOOLEAN DEFAULT FALSE NOT NULL"
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE lexicon_entries ADD COLUMN production_ready {ddl}"))


def _difficulty_from_rank(rank: int, total: int) -> int:
    """按频率排名折算难度 1..6（排名越小越常用 → 越简单）。

    纯规则计算，不引入机器学习，也不编造 CEFR。
    """
    if not rank or not total:
        return 1
    ratio = min(1.0, rank / total)
    return max(1, min(6, int(ratio * 6) + 1))


def build_entries(items: list, limit: int | None) -> list[dict]:
    out = []
    total = len(items)
    seen = set()
    for it in items:
        w = (it.get("w") or "").strip()
        if not w:
            continue
        norm = normalize("en", w)
        if not norm:
            continue
        kind = "vocabulary"
        pos = (it.get("pos") or "").strip().lower()
        key = ("en", norm, kind, pos)
        if key in seen:
            continue
        seen.add(key)
        cn = (it.get("cn") or "").strip()
        en = (it.get("en") or "").strip()
        zh = (it.get("zh") or "").strip()
        ipa = (it.get("ipa") or "").strip()
        try:
            freq = int(it.get("freq") or 0)
        except (TypeError, ValueError):
            freq = 0
        out.append({
            "language_code": "en",
            "surface": w,
            "lemma": w,
            "normalized": norm,
            "kind": kind,
            "pos": pos,
            "pronunciation": ipa,
            "jyutping": "",
            "meaning_en": "",
            "meaning_zh": cn,
            "example_en": en,
            "example_zh": zh,
            "frequency": None,
            "frequency_rank": freq or None,
            "cefr": "",
            "cefr_source": "",
            "difficulty": _difficulty_from_rank(freq, total),
            "topic": "general",
            "source": SOURCE,
            "source_id": w,
            "license": LICENSE,
            "license_url": "",
            "attribution": "",
            "commercial_allowed": False,
            "redistribution_allowed": False,
            "verified": False,
            "production_ready": False,
        })
        if limit and len(out) >= limit:
            break
    return out


def _existing_keys(session) -> set:
    rows = session.query(
        LexiconEntry.language_code, LexiconEntry.normalized,
        LexiconEntry.kind, LexiconEntry.pos).all()
    return {(r[0], r[1], r[2], r[3]) for r in rows}


def main() -> None:
    p = argparse.ArgumentParser(description="Import legacy-2000 words into LexiconEntry")
    p.add_argument("--dry-run", action="store_true", help="只统计、不写库")
    p.add_argument("--limit", type=int, default=2000, help="导入条数上限（默认 2000 = 全部）")
    p.add_argument("--batch-size", type=int, default=250, help="每批提交条数（默认 250）")
    p.add_argument("--db-uri", default=DATABASE_URL, help="目标 SQLite（默认应用实例库）")
    args = p.parse_args()

    with open(WORDS_JSON, encoding="utf-8") as f:
        data = json.load(f)
    items = data.get("items", []) if isinstance(data, dict) else data

    limit = args.limit if args.limit and args.limit > 0 else None
    entries = build_entries(items, limit)
    print(f"[build] words.json items={len(items)} -> candidate entries={len(entries)}")

    if args.dry_run:
        print(f"[dry-run] would import {len(entries)} legacy-2000 entries "
              f"(source={SOURCE}, license={LICENSE}, production_ready=false)")
        return

    session = make_session(args.db_uri)
    _ensure_production_ready(session.get_bind())
    existing = _existing_keys(session)
    new_entries = [e for e in entries
                   if (e["language_code"], e["normalized"], e["kind"], e["pos"]) not in existing]
    skipped = len(entries) - len(new_entries)
    print(f"[dedup] existing={len(existing)} skipped={skipped} to_insert={len(new_entries)}")

    t0 = time.time()
    res = import_entries(session, new_entries, batch_size=args.batch_size, dry_run=False)
    session.close()
    dt = time.time() - t0
    print(f"[import] inserted={res.get('inserted')} batches={res.get('batches')} "
          f"({dt:.2f}s) -> {args.db_uri}")
    print(f"[verify] all legacy-2000 entries are production_ready=false "
          f"(license UNKNOWN, not verified).")


if __name__ == "__main__":
    main()
