#!/usr/bin/env python
"""V5.1 词库导入性能 / 资源基线测量（开发验证用）。

目标环境：2 vCPU / 2GB RAM / SQLite。本脚本在开发机测量并上报：
  - 导入耗时（wall time）
  - 最大 RAM（tracemalloc 峰值，作为 Python 堆上限代理）
  - 最大 CPU（os.times 用户+系统时间）
  - 数据库文件大小

用法：
  python scripts/perf_lexicon_import.py --db-uri sqlite:///lexicon_dev.db
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import tracemalloc

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lexicon import get_source, run_pipeline, make_session, import_entries  # noqa: E402


def run_one(db_uri, language, source, limit):
    print(f"\n=== {language} / {source} (limit={limit}) ===")
    t0 = time.time()
    cpu0 = os.times()
    tracemalloc.start()

    raw = list(get_source(language, source, limit))
    entries, stats = run_pipeline(raw, strict=False)
    peak_snap = tracemalloc.get_traced_memory()[1]
    session = make_session(db_uri)
    try:
        res = import_entries(session, entries, batch_size=250)
    finally:
        session.close()
    tracemalloc.stop()

    dt = time.time() - t0
    cpu1 = os.times()
    cpu = (cpu1.user - cpu0.user) + (cpu1.system - cpu0.system)
    print(f"  raw={len(raw)} kept={stats['kept']} dedup_removed={stats['dedup_removed']} "
          f"errors={stats['errors']} warnings={stats['warnings']}")
    print(f"  inserted={res.get('inserted')} batches={res.get('batches')}")
    print(f"  wall={dt:.2f}s  cpu={cpu:.2f}s  peak_ram={peak_snap / 1024 / 1024:.1f} MB")
    return res.get("inserted", 0), dt, peak_snap, cpu


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--db-uri", default="sqlite:///lexicon_dev.db")
    args = p.parse_args()

    # 全新的开发 DB，避免与生产/预览库混淆
    db_path = args.db_uri.replace("sqlite:///", "")
    if os.path.exists(db_path):
        os.remove(db_path)

    total_ins = 0
    peak_all = 0
    cpu_all = 0.0
    t_total = 0.0

    for lang, src, lim in [("en", "synthetic-dev", 5000), ("yue", "synthetic-dev", 2000)]:
        ins, dt, peak, cpu = run_one(args.db_uri, lang, src, lim)
        total_ins += ins
        peak_all = max(peak_all, peak)
        cpu_all += cpu
        t_total += dt

    if os.path.exists(db_path):
        size = os.path.getsize(db_path)
        size_mb = size / 1024 / 1024
    else:
        size_mb = 0.0
        size = 0

    print("\n========== SUMMARY ==========")
    print(f"总词条数 (inserted): {total_ins}")
    print(f"English: 5000 目标 / Cantonese: 2000 目标")
    print(f"导入耗时 (wall total): {t_total:.2f}s")
    print(f"最大 RAM (tracemalloc peak): {peak_all / 1024 / 1024:.1f} MB")
    print(f"最大 CPU ( accumulated): {cpu_all:.2f}s")
    print(f"数据库大小: {size} bytes ({size_mb:.2f} MB)")
    print("==============================")


if __name__ == "__main__":
    main()
