"""Pytest 入口：把原有 main() 风格套件转为可被 pytest 收集的用例（规格 §28）。

每个模块作为一个 `test_*` 函数，调用其 `main()` 并断言无失败；
这样 `pytest -q` 能真正运行全部 300+ 检查，而非收集到 0 个用例。

若要新增原生 pytest 用例，直接在本目录写 `test_*.py` 即可，
conftest 提供的 `app` / `client` fixture 可直接使用。
"""
from __future__ import annotations

import importlib

import pytest

MODULES = [
    "test_email_code",
    "test_flow",
    "test_quiz_grading",
    "test_ui_smoke",
    "test_v3_games",
    "test_api_v1",
    "test_v3_pages",
]


def _run(modname: str):
    mod = importlib.import_module(modname)
    mod.PASS = mod.FAIL = 0
    try:
        mod.main()
    except SystemExit:
        # 各套件末尾用 sys.exit 报告失败数，这里吃掉，改用断言判定
        pass
    assert mod.FAIL == 0, f"{modname}: {mod.FAIL} 项失败"
    assert mod.PASS > 0, f"{modname}: 没有执行任何检查"


@pytest.mark.parametrize("modname", MODULES)
def test_suite_module(modname: str):
    _run(modname)
