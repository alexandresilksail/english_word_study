#!/usr/bin/env python
"""V5.3 重算 lexicon_entries.production_ready 字段（基于 §36 规则）。

- 只读 LexiconEntry，按规则重算 production_ready；
- 支持 --source 过滤、--dry-run 只统计不写库、--db-uri 指定库；
- 结果写入 data/lexicon/audit/production_ready_report.json。

绝不会删除 / 修改任何词库内容本身（仅更新 production_ready 标志位）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from config import Config
from lexicon import recompute_production_ready, make_session


def _ensure_schema(uri: str):
    """对已有库执行幂等 ALTER（新增 production_ready 列时用）。"""
    from sqlalchemy import create_engine, text
    from models import LexiconEntry
    eng = create_engine(uri, future=True)
    LexiconEntry.__table__.create(bind=eng, checkfirst=True)
    # 列若存在则跳过 ALTER（migration 兼容）；PRAGMA table_info 第 1 列为列名。
    with eng.connect() as conn:
        cols = [r[1] for r in conn.execute(text("PRAGMA table_info(lexicon_entries)")).fetchall()]
        if "production_ready" not in cols:
            conn.execute(text(
                "ALTER TABLE lexicon_entries ADD COLUMN production_ready BOOLEAN NOT NULL DEFAULT 0"
            ))
            conn.commit()
    return eng


def main():
    ap = argparse.ArgumentParser(description="Recompute LexiconEntry.production_ready")
    ap.add_argument("--source", default=None, help="只重算指定 source（如 legacy-2000 / synthetic-dev）")
    ap.add_argument("--dry-run", action="store_true", help="只统计，不写库")
    ap.add_argument("--db-uri", default=Config.DATABASE_URL, help="目标数据库 URI")
    args = ap.parse_args()

    _ensure_schema(args.db_uri)
    session = make_session(args.db_uri)

    report = recompute_production_ready(session, source=args.source, dry_run=args.dry_run)

    out_dir = os.path.join(ROOT, "data", "lexicon", "audit")
    os.makedirs(out_dir, exist_ok=True)
    out_file = os.path.join(out_dir, "production_ready_report.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # 控制台摘要
    mode = "DRY-RUN" if args.dry_run else "APPLIED"
    print(f"[{mode}] total scanned : {report['total']}")
    print(f"[{mode}] changed        : {report['changed']}")
    for src, b in sorted(report["by_source"].items()):
        print(f"  {src:16s} total={b['total']:5d}  production_ready={b['production_ready']:5d}  changed={b['changed']:5d}")
    print(f"Report -> {os.path.relpath(out_file, ROOT)}")
    session.close()


if __name__ == "__main__":
    main()
