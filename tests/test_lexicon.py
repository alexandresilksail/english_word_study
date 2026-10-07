"""V5.1 Master Lexicon 测试套件。

覆盖：归一化、去重、许可证过滤、Jyutping、CEFR、批量导入、dry-run、回滚、
LexiconEntry -> ContentItem，以及 5000 英文 / 2000 粤语规模导入。

不依赖 Flask app 启动（独立 session），且数据用 synthetic-dev 源，绝不触碰生产数据。
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
    normalize, dedup_key, dedup_entries, compute_difficulty, is_valid_cefr,
    apply_license, validate_entry, get_source, run_pipeline,
    make_session, import_entries, convert_to_content, build_entry,
)
from models import LexiconEntry, ContentItem


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


# ---------- normalize ----------
def test_normalize_en():
    assert normalize("en", "  Running! ") == "running"
    assert normalize("en", "Don't") == "don't"


def test_normalize_yue_keeps_han():
    assert normalize("yue", " 你好 ") == "你好"
    assert normalize("yue", "ABC") == "abc"


# ---------- dedup ----------
def test_dedup_key_distinguishes_pos_and_kind():
    k1 = dedup_key("en", "run", "vocabulary", "v")
    k2 = dedup_key("en", "run", "vocabulary", "n")
    assert k1 != k2
    # 不按 surface 去重：不同 surface 但同 normalized+kind+pos 视为同一（符合规格）
    k3 = dedup_key("en", "ran", "vocabulary", "v")
    assert k1 != k3  # normalized 不同


def test_dedup_entries_removes_duplicates():
    e1 = {"language_code": "en", "normalized": "go", "kind": "vocabulary", "pos": "v"}
    e2 = dict(e1)
    e3 = {"language_code": "en", "normalized": "go", "kind": "vocabulary", "pos": "n"}
    out = dedup_entries([e1, e2, e3])
    assert len(out) == 2


# ---------- license filter ----------
def test_license_filter_unknown_source_false():
    e = {"source": "unknown-x", "commercial_allowed": True}
    apply_license(e)
    assert e["commercial_allowed"] is False


def test_license_filter_does_not_assume_commercial():
    e = {"source": "synthetic-dev", "commercial_allowed": True}
    apply_license(e)
    # 注册表对 synthetic-dev 明确为 false，即便条目声明 true 也要降级
    assert e["commercial_allowed"] is False


# ---------- jyutping / cefr / validation ----------
def test_jyutping_required_for_yue():
    e = {"language_code": "yue", "surface": "你好", "normalized": "你好",
         "kind": "vocabulary", "source": "synthetic-dev", "cefr": "", "cefr_source": ""}
    errs, _ = validate_entry(e)
    assert any("jyutping" in x for x in errs)


def test_cefr_source_required_when_cefr_set():
    e = {"language_code": "en", "surface": "x", "normalized": "x",
         "kind": "vocabulary", "source": "synthetic-dev", "cefr": "A1", "cefr_source": ""}
    errs, _ = validate_entry(e)
    assert any("cefr_source" in x for x in errs)
    e["cefr_source"] = "test"
    errs, _ = validate_entry(e)
    assert not any("cefr_source" in x for x in errs)


def test_cefr_invalid_rejected():
    assert is_valid_cefr("A1")
    assert not is_valid_cefr("Z9")


def test_difficulty_range_and_monotonic():
    assert 1 <= compute_difficulty("A1") <= 7
    assert compute_difficulty("A1") <= compute_difficulty("C2")


# ---------- import: batch / dry-run / rollback ----------
def test_batch_import_counts_and_batches(session):
    raw = list(get_source("en", "synthetic-dev", 300))
    entries, _ = run_pipeline(raw, strict=True)
    res = import_entries(session, entries, batch_size=50)
    assert res["inserted"] == 300
    assert res["batches"] == 6
    assert session.query(LexiconEntry).count() == 300


def test_dry_run_writes_nothing(session):
    raw = list(get_source("en", "synthetic-dev", 100))
    entries, _ = run_pipeline(raw, strict=True)
    before = session.query(LexiconEntry).count()
    res = import_entries(session, entries, dry_run=True)
    assert res["inserted"] == 0 and res["would_insert"] == 100
    assert session.query(LexiconEntry).count() == before


def test_rollback_on_integrity_error(session):
    raw = next(get_source("en", "synthetic-dev", 1))
    a = build_entry(raw)
    b = dict(a)
    b["source_id"] = "dup-clone"  # 与 a 同 unique key -> 触发 IntegrityError
    raised = False
    try:
        import_entries(session, [a, b], batch_size=5)
    except Exception:
        raised = True
    assert raised
    # 单批失败 -> 整批回滚，0 行
    assert session.query(LexiconEntry).count() == 0


# ---------- scale: 5000 EN / 2000 YUE ----------
def test_import_5000_english(session):
    raw = list(get_source("en", "synthetic-dev", 5000))
    entries, stats = run_pipeline(raw, strict=True)
    assert stats["errors"] == 0
    assert len(entries) == 5000
    res = import_entries(session, entries, batch_size=250)
    assert res["inserted"] == 5000
    assert session.query(LexiconEntry).filter_by(language_code="en").count() == 5000


def test_import_2000_cantonese(session):
    raw = list(get_source("yue", "synthetic-dev", 2000))
    entries, stats = run_pipeline(raw, strict=True)
    assert stats["errors"] == 0
    assert len(entries) == 2000
    res = import_entries(session, entries, batch_size=250)
    assert res["inserted"] == 2000
    yue = session.query(LexiconEntry).filter_by(language_code="yue")
    assert yue.count() == 2000
    # 粤语必须保留 Jyutping
    assert yue.filter(LexiconEntry.jyutping == "").count() == 0


# ---------- LexiconEntry -> ContentItem ----------
def test_lexicon_to_content_creates_items(session):
    raw = list(get_source("en", "synthetic-dev", 200))
    entries, _ = run_pipeline(raw, strict=True)
    import_entries(session, entries, batch_size=250)
    dry = convert_to_content(session, language_code="en", limit=10, dry_run=True)
    assert dry["would_create"] > 0
    res = convert_to_content(session, language_code="en", limit=10)
    assert res["created"] > 0
    assert session.query(ContentItem).count() == res["created"]
    # 生成的 ContentItem 必须有英文释义（English 版面不落空）
    assert session.query(ContentItem).filter(ContentItem.meaning_en == "").count() == 0


def test_to_content_does_not_generate_all(session):
    raw = list(get_source("en", "synthetic-dev", 5000))
    entries, _ = run_pipeline(raw, strict=True)
    import_entries(session, entries, batch_size=250)
    # limit 显式限制，绝不把 5000 条全灌进课程内容
    res = convert_to_content(session, language_code="en", limit=20)
    assert res["created"] <= 20
    assert session.query(ContentItem).count() <= 20
