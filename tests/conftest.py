"""Pytest 配置（规格 §28）。

让 `pytest -q` 能够真正收集并运行测试套件，而不是像以前那样收集到 0 个用例。

- 把仓库根目录与 tests/ 目录加入 sys.path，使 `from app import create_app`
  以及 `importlib.import_module("test_flow")` 都能成功；
- 提供一个 session 级 `app` / `client` fixture 供未来编写原生 test_* 用例使用。
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pytest  # noqa: E402


@pytest.fixture(scope="session")
def app():
    from app import create_app  # noqa: E402
    from config import TestingConfig  # noqa: E402
    from extensions import db  # noqa: E402

    application = create_app(TestingConfig)
    application.config["WTF_CSRF_ENABLED"] = False
    with application.app_context():
        db.create_all()
        from seeds.seed_words import seed_from_json
        seed_from_json(application.config["WORDS_JSON"])
    yield application


@pytest.fixture
def client(app):
    return app.test_client()
