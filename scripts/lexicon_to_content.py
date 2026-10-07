#!/usr/bin/env python
"""从 Master Lexicon 筛选生成 ContentItem（仅子集，绝不批量生成全部 7000）。

用法：
  python scripts/lexicon_to_content.py --language en --cefr A1 --limit 20
  python scripts/lexicon_to_content.py --language yue --topic Greetings --limit 10
  python scripts/lexicon_to_content.py --dry-run --limit 5

筛选维度：language / cefr / topic / kind / difficulty。
默认写入「Lexicon Sandbox」沙盒课程（自动创建），便于先验证再推广到正式课程。
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lexicon import make_session, convert_to_content


def main() -> None:
    p = argparse.ArgumentParser(description="Convert LexiconEntry -> ContentItem (subset)")
    p.add_argument("--language")
    p.add_argument("--cefr")
    p.add_argument("--topic")
    p.add_argument("--kind")
    p.add_argument("--difficulty-max", type=int, dest="difficulty_max")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--lesson-id", type=int)
    p.add_argument("--db-uri", default="sqlite:///lexicon_dev.db")
    args = p.parse_args()

    session = make_session(args.db_uri)
    try:
        res = convert_to_content(
            session,
            language_code=args.language, cefr=args.cefr, topic=args.topic,
            kind=args.kind, difficulty_max=args.difficulty_max,
            limit=args.limit, dry_run=args.dry_run, lesson_id=args.lesson_id,
        )
    finally:
        session.close()
    print("[to_content]", res)


if __name__ == "__main__":
    main()
