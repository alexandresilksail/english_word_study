#!/usr/bin/env python
"""校验 Master Lexicon（数据库或 JSON）。

用法：
  python scripts/validate_lexicon.py --db-uri sqlite:///lexicon_dev.db
  python scripts/validate_lexicon.py --json data/lexicon/master/en.json

退出码：0 = 无错误；1 = 存在校验错误。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lexicon import make_session, validate_entry
from models import LexiconEntry


def main() -> None:
    p = argparse.ArgumentParser(description="Validate Master Lexicon")
    p.add_argument("--db-uri", default="sqlite:///lexicon_dev.db")
    p.add_argument("--json")
    args = p.parse_args()

    if args.json:
        with open(args.json, encoding="utf-8") as f:
            entries = json.load(f)
        src = f"json:{args.json}"
    else:
        session = make_session(args.db_uri)
        cols = [c.name for c in LexiconEntry.__table__.columns]
        entries = [{c: getattr(e, c) for c in cols} for e in session.query(LexiconEntry).all()]
        session.close()
        src = args.db_uri

    total = len(entries)
    errs = 0
    warns = 0
    by_lang = {}
    commercial = 0
    for e in entries:
        e_errs, e_warns = validate_entry(e)
        errs += len(e_errs)
        warns += len(e_warns)
        lang = e.get("language_code", "?")
        by_lang[lang] = by_lang.get(lang, 0) + 1
        if e.get("commercial_allowed"):
            commercial += 1
        if e_errs:
            print("ERROR", e.get("source_id") or e.get("surface"), e_errs)

    print(f"[validate] src={src} total={total} errors={errs} warnings={warns}")
    print(f"[validate] by_language={by_lang} commercial_allowed={commercial}")
    sys.exit(1 if errs else 0)


if __name__ == "__main__":
    main()
