#!/usr/bin/env python
"""V5.1 词库导入 CLI。

用法：
  python scripts/import_lexicon.py --language en --source wordfreq
  python scripts/import_lexicon.py --language yue --source cc-canto
  python scripts/import_lexicon.py --language en --source synthetic-dev --limit 0
  python scripts/import_lexicon.py --language yue --source synthetic-dev --limit 0

选项：
  --language {en,yue}                                 必填
  --source   {wordfreq,cc-canto,words-hk,synthetic-dev}  必填
  --limit N       导入条数上限（默认 500；0 = 不限制，导入全部可用数据）
  --batch-size N  每批提交条数（默认 250）
  --dry-run       只统计、不写库
  --validate      严格校验（遇到错误立即中止）
  --db-uri URI    SQLite 连接（默认 sqlite:///lexicon_dev.db）

说明：7000 条需分批导入，用 --limit 0 放开上限即可；每批约 250 条由 --batch-size 控制。
"""
from __future__ import annotations

import argparse
import os
import sys
import time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lexicon import get_source, run_pipeline, make_session, import_entries


def main() -> None:
    p = argparse.ArgumentParser(description="V5.1 Lexicon import")
    p.add_argument("--language", required=True, choices=["en", "yue"])
    p.add_argument("--source", required=True,
                   choices=["wordfreq", "cc-canto", "words-hk", "synthetic-dev"])
    p.add_argument("--limit", type=int, default=500, help="0 = unlimited")
    p.add_argument("--batch-size", type=int, default=250)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--validate", action="store_true", help="strict validation")
    p.add_argument("--db-uri", default="sqlite:///lexicon_dev.db")
    args = p.parse_args()

    limit = None if args.limit == 0 else args.limit
    t0 = time.time()
    try:
        raw = get_source(args.language, args.source, limit)
    except RuntimeError as e:
        print("SOURCE_ERROR:", e)
        sys.exit(2)

    entries, stats = run_pipeline(raw, strict=args.validate)
    dt = time.time() - t0
    print(f"[pipeline] total={stats['total']} kept={stats['kept']} "
          f"dedup_removed={stats['dedup_removed']} errors={stats['errors']} "
          f"warnings={stats['warnings']} ({dt:.2f}s)")

    if not entries:
        print("NOTHING_TO_IMPORT")
        return

    session = make_session(args.db_uri)
    try:
        res = import_entries(session, entries, batch_size=args.batch_size, dry_run=args.dry_run)
    finally:
        session.close()
    print("[import]", res)
    if not args.dry_run:
        print(f"DONE: imported {res.get('inserted')} entries in {res.get('batches')} batches "
              f"-> {args.db_uri}")


if __name__ == "__main__":
    main()
