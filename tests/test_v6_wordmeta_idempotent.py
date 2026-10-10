"""V6 回归测试：word_meta.word_id 唯一约束冲突与初始化幂等性。

覆盖（规格见修复任务单）：
1. 首次初始化：全新隔离 SQLite → create_app 完整初始化 + 播种词库后
   ensure_word_meta 恰好插入 2000 条，无重复 word_id；
2. 重复初始化：再次调用 ensure_word_meta / 再次 create_app（模拟容器二次启动）
   → 0 新增，总数不变，无重复；
3. 异常回滚后再次调用：模拟并发 IntegrityError 与中断的 Session，
   验证 ensure_word_meta 回滚重读补集、create_app 各步骤不被连带跳过；
4. 并发初始化：多线程（多 session）同时 ensure_word_meta，
   不产生重复 word_id、不抛唯一约束异常；
5. 课程 / 课时内容 / 等级 / 商品数据在重复与并发初始化后均保持正常。

全部使用全新隔离 SQLite 文件库（tempfile），不触碰生产 instance/ 库。
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 显式构造 config 类：Config.DATABASE_URL 是导入期固化的类属性，
# 只有把路径写进子类属性，库路径才与模块导入顺序无关（同 test_v55 的写法）。
_fd, _PATH = tempfile.mkstemp(suffix="_v6_wordmeta.db")
os.close(_fd)

from app import create_app  # noqa: E402
from config import DevelopmentConfig  # noqa: E402
from extensions import db  # noqa: E402
from models import ContentItem, Course, Lesson, Level, Product, Word, WordMeta  # noqa: E402
from path_service import ensure_word_meta  # noqa: E402
from seeds.seed_words import seed_from_json  # noqa: E402


class _V6Config(DevelopmentConfig):
    TESTING = True
    WTF_CSRF_ENABLED = False
    IP_RATELIMIT_ENABLED = False
    DATABASE_URL = f"sqlite:///{_PATH}"


@pytest.fixture(scope="module")
def app():
    application = create_app(_V6Config)
    with application.app_context():
        db.create_all()
        seed_from_json(application.config["WORDS_JSON"])
    yield application
    try:
        os.remove(_PATH)
    except OSError:
        pass


def _dup_word_ids() -> list:
    rows = db.session.execute(db.text(
        "select word_id, count(*) c from word_meta group by word_id having c > 1"
    )).fetchall()
    return [tuple(r) for r in rows]


def _dup_word_lang() -> list:
    rows = db.session.execute(db.text(
        "select word_id, learning_language, count(*) c from word_meta "
        "group by word_id, learning_language having c > 1"
    )).fetchall()
    return [tuple(r) for r in rows]


def _reset_word_meta():
    """把 word_meta 清空，构造「词库已播、分级为空」的初始前置。"""
    db.session.execute(db.text("delete from word_meta"))
    db.session.commit()


# --------------------------------------------------------------------------
def test_first_init_seeds_all_words_once(app):
    """首次初始化：恰好 2000 条 word_meta，无重复 word_id。"""
    with app.app_context():
        _reset_word_meta()
        n = ensure_word_meta()
        assert n == 2000
        assert WordMeta.query.count() == 2000
        assert Word.query.count() == 2000
        assert _dup_word_ids() == []
        assert _dup_word_lang() == []


def test_repeated_init_is_idempotent(app):
    """重复初始化：再次调用不新增、不重复。"""
    with app.app_context():
        _reset_word_meta()
        n = ensure_word_meta()          # 首次
        assert n == 2000
        n2 = ensure_word_meta()         # 重复
        assert n2 == 0
        n3 = ensure_word_meta()         # 再次重复
        assert n3 == 0
        assert WordMeta.query.count() == 2000
        assert _dup_word_ids() == []


def test_create_app_twice_same_db_full_init(app):
    """模拟「容器连续启动两次」：同一文件库上二次 create_app，全部初始化收敛。"""
    with app.app_context():
        _reset_word_meta()
        assert ensure_word_meta() == 2000
        before = WordMeta.query.count()
    app2 = create_app(_V6Config)        # 二次启动：完整初始化链路再跑一遍
    with app2.app_context():
        assert WordMeta.query.count() == before
        assert _dup_word_ids() == []
        # 课程 / 课时内容 / 等级 / 商品数据必须保持完整
        assert Course.query.filter_by(language_code="en").count() == 7
        assert Lesson.query.count() >= 200
        assert Level.query.count() == 7
        assert Product.query.count() == 3
        assert ContentItem.query.count() >= 1000
        # words 表一行不动
        assert Word.query.count() == 2000


def test_retry_after_integrity_error_recovers(app):
    """异常回滚后再次调用：模拟并发方已先插入部分行，本函数只补缺失、不报错。"""
    with app.app_context():
        _reset_word_meta()
        # 模拟另一进程已抢先提交了前 500 个词的分级
        pre_ids = [w.id for w in Word.query.order_by(Word.id).limit(500).all()]
        assert len(pre_ids) == 500
        from models import WordMeta as _WM
        db.session.add_all(
            _WM(word_id=wid, learning_language="en", cefr_level="A1",
                unit_no=1, difficulty=1, category="general")
            for wid in pre_ids
        )
        db.session.commit()
        assert WordMeta.query.count() == 500

        n = ensure_word_meta()          # 应只补缺失的 1500 条
        assert n == 1500
        assert WordMeta.query.count() == 2000
        assert _dup_word_ids() == []
        assert _dup_word_lang() == []


def test_poisoned_session_rollback_then_reinit(app):
    """Session 处于 pending-rollback 后，create_app 级回滚让后续步骤恢复。"""
    from sqlalchemy.exc import IntegrityError
    with app.app_context():
        _reset_word_meta()
        assert ensure_word_meta() == 2000
        # 人为制造一次 IntegrityError：插入重复 word_id（不提交，会话被污染）
        dup = WordMeta(word_id=1, learning_language="en", cefr_level="A1",
                       unit_no=1, difficulty=1, category="general")
        db.session.add(dup)
        with pytest.raises(IntegrityError):
            db.session.flush()
        # 此刻 Session 处于 pending-rollback，直接查询会失败
        with pytest.raises(Exception):
            WordMeta.query.count()
        # 修复路径：rollback 后重新调用 ensure_word_meta 应正常收敛
        db.session.rollback()
        n = ensure_word_meta()
        assert n == 0
        assert WordMeta.query.count() == 2000
        assert _dup_word_ids() == []


def test_concurrent_ensure_word_meta_no_duplicate(app):
    """并发初始化：多线程（独立 session）同时 ensure_word_meta，无重复、无异常。"""
    with app.app_context():
        # 复位到「词库已播、分级为空」的竞态前置状态
        db.session.execute(db.text("delete from word_meta"))
        db.session.commit()
        assert WordMeta.query.count() == 0

    n_threads = 4
    barrier = threading.Barrier(n_threads)
    errors: list[BaseException] = []
    results: list[int] = []

    def _run():
        try:
            # 每个线程使用独立的 app context / session，模拟多进程并发
            with app.app_context():
                barrier.wait(timeout=30)
                results.append(ensure_word_meta())
        except BaseException as exc:  # noqa: BLE001 - 收集断言用
            errors.append(exc)

    threads = [threading.Thread(target=_run) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(120)
        assert not t.is_alive(), "并发 ensure_word_meta 线程超时"

    assert errors == [], f"并发初始化出现异常: {errors}"
    with app.app_context():
        assert WordMeta.query.count() == 2000
        assert _dup_word_ids() == []
        assert _dup_word_lang() == []
        # 课程 / 等级 / 商品不受影响
        assert Level.query.count() == 7
        assert Product.query.count() == 3


def test_no_duplicate_after_full_suite_style_repeats(app):
    """长时间重复初始化（如多轮启动）后仍无重复 word_id。"""
    with app.app_context():
        for _ in range(5):
            ensure_word_meta()
        assert WordMeta.query.count() == 2000
        assert _dup_word_ids() == []
        assert _dup_word_lang() == []
