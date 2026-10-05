"""一次性运行全部测试套件（原有 4 个 + V3.0 新增 3 个）。

每个套件各自 ``build()`` 独立内存库，互不污染；本脚本捕获各自的
``sys.exit`` 并把 PASS/FAIL 汇总，最后以「总失败数」作为退出码。
"""
from __future__ import annotations

import importlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..")))

MODULES = [
    "test_email_code",
    "test_flow",
    "test_quiz_grading",
    "test_ui_smoke",
    "test_v3_games",
    "test_api_v1",
    "test_v3_pages",
]


def run(name: str):
    print("\n" + "#" * 70)
    print(f"# 运行测试套件：{name}")
    print("#" * 70)
    try:
        mod = importlib.import_module(name)
        mod.PASS = mod.FAIL = 0
        mod.main()
    except SystemExit as e:
        code = e.code or 0
        p, f = getattr(mod, "PASS", 0), getattr(mod, "FAIL", 0)
        return p, f
    except Exception as exc:  # 套件自身崩溃也算失败
        print(f"  !! {name} 运行异常：{exc}")
        return 0, 1
    p, f = getattr(mod, "PASS", 0), getattr(mod, "FAIL", 0)
    return p, f


def main():
    total_p = total_f = 0
    for m in MODULES:
        p, f = run(m)
        total_p += p
        total_f += f
        print(f">> {m}: 通过 {p} / 失败 {f}")

    print("\n" + "=" * 70)
    print(f"全部套件汇总：通过 {total_p} / 失败 {total_f}")
    print("=" * 70)
    sys.exit(1 if total_f else 0)


if __name__ == "__main__":
    main()
