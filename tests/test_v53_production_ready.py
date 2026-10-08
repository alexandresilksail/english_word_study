"""V5.3 生产就绪（production_ready）判定测试。

覆盖 §36 规则：
- synthetic-dev 永远 False；
- legacy-2000 在注册表 verified 前永远 False；
- 其它来源需 verified + commercial_allowed + 必填字段有效 才为 True；
- 流水线导入时自动写入 production_ready；
- recompute 能按规则回写数据库。
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from lexicon import (
    compute_production_ready, required_fields_valid, recompute_production_ready,
    run_pipeline,
)
from lexicon.db_io import make_session
from models import LexiconEntry


def _tmp_session():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    uri = f"sqlite:///{path}"
    s = make_session(uri)
    s._tmp_path = path
    return s


@pytest.fixture
def session():
    s = _tmp_session()
    yield s
    s.close()
    try:
        os.remove(s._tmp_path)
    except OSError:
        pass


# ---------- 规则：synthetic-dev 永远 False ----------
def test_synthetic_dev_always_false_even_if_flags_true():
    e = {
        "source": "synthetic-dev", "language_code": "en", "surface": "x", "normalized": "x",
        "meaning_en": "e", "license": "DEV-PLACEHOLDER", "verified": True,
        "commercial_allowed": True,
    }
    assert compute_production_ready(e) is False


# ---------- 规则：legacy-2000 在核实前永远 False ----------
def test_legacy_2000_false_until_registry_verified():
    e = {
        "source": "legacy-2000", "language_code": "en", "surface": "ability", "normalized": "ability",
        "meaning_zh": "能力", "license": "UNKNOWN", "verified": True, "commercial_allowed": True,
    }
    # 即便条目自带 verified/commercial=True，仍受注册表约束 -> 当前 False
    assert compute_production_ready(e) is False


def test_legacy_2000_required_fields_gating():
    # 字段不完整也应 False（双保险）
    e = {"source": "legacy-2000", "language_code": "en", "surface": "x", "normalized": "x"}
    assert compute_production_ready(e) is False


# ---------- 规则：其它来源需 verified + commercial + 字段有效 ----------
def test_curated_original_verified_true():
    e = {
        "source": "curated-original", "language_code": "en", "surface": "hello",
        "normalized": "hello", "meaning_en": "a greeting", "license": "CC0-1.0",
        "verified": True, "commercial_allowed": True,
    }
    assert compute_production_ready(e) is True


def test_other_source_false_when_not_verified():
    e = {
        "source": "curated-original", "language_code": "en", "surface": "hello",
        "normalized": "hello", "meaning_en": "a greeting", "license": "CC0-1.0",
        "verified": False, "commercial_allowed": True,
    }
    assert compute_production_ready(e) is False


def test_other_source_false_when_not_commercial():
    e = {
        "source": "curated-original", "language_code": "en", "surface": "hello",
        "normalized": "hello", "meaning_en": "a greeting", "license": "CC0-1.0",
        "verified": True, "commercial_allowed": False,
    }
    assert compute_production_ready(e) is False


# ---------- required_fields_valid ----------
def test_required_fields_valid_ok():
    e = {"language_code": "en", "surface": "x", "normalized": "x",
         "meaning_en": "e", "license": "CC0-1.0"}
    assert required_fields_valid(e) is True


def test_required_fields_valid_missing_meaning():
    e = {"language_code": "en", "surface": "x", "normalized": "x",
         "meaning_en": "", "license": "CC0-1.0"}
    assert required_fields_valid(e) is False


def test_required_fields_valid_yue_missing_jyutping():
    e = {"language_code": "yue", "surface": "你好", "normalized": "你好",
         "meaning_en": "hello", "license": "CC BY 4.0"}
    assert required_fields_valid(e) is False


def test_required_fields_valid_yue_with_jyutping():
    e = {"language_code": "yue", "surface": "你好", "normalized": "你好",
         "meaning_en": "hello", "license": "CC BY 4.0", "jyutping": "nei5 hou2"}
    assert required_fields_valid(e) is True


def test_required_fields_valid_cefr_needs_source():
    e = {"language_code": "en", "surface": "x", "normalized": "x",
         "meaning_en": "e", "license": "CC0-1.0", "cefr": "A1", "cefr_source": ""}
    assert required_fields_valid(e) is False


# ---------- 流水线自动写入 production_ready ----------
def test_pipeline_sets_production_ready_false_for_synthetic():
    from lexicon import get_source
    raw = list(get_source("en", "synthetic-dev", 50))
    entries, stats = run_pipeline(raw, strict=True)
    assert stats["errors"] == 0
    # synthetic-dev 一律生产就绪 False
    assert all(e.get("production_ready") is False for e in entries)


# ---------- recompute 回写数据库 ----------
def test_recompute_sets_legacy_false(session):
    # 插入一条 legacy-2000 但强行把 verified/commercial 标 True 的行，
    # recompute 应依据规则（注册表）改回 False。
    row = LexiconEntry(
        language_code="en", surface="ability", normalized="ability", kind="vocabulary",
        pos="n", meaning_zh="能力", license="UNKNOWN", source="legacy-2000",
        verified=True, commercial_allowed=True, production_ready=True,
    )
    session.add(row)
    session.commit()

    report = recompute_production_ready(session, source="legacy-2000")
    assert report["total"] == 1
    assert report["changed"] == 1  # True -> False
    assert session.query(LexiconEntry).first().production_ready is False


def test_recompute_keeps_curated_true(session):
    row = LexiconEntry(
        language_code="en", surface="hello", normalized="hello", kind="vocabulary",
        pos="n", meaning_en="a greeting", license="CC0-1.0", source="curated-original",
        verified=True, commercial_allowed=True, production_ready=False,
    )
    session.add(row)
    session.commit()

    report = recompute_production_ready(session, source="curated-original")
    assert report["total"] == 1
    assert report["changed"] == 1  # False -> True
    assert session.query(LexiconEntry).first().production_ready is True
