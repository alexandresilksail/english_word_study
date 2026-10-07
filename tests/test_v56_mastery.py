"""V5.6 间隔重复 + 统一掌握度 0-4 测试。

覆盖规格：
- 掌握度综合 **练习证据**（acc / streak / review_count）与 **测验证据**
  （quiz_score / unit_test_score）得出 0-4；
- 未测验用 0 表示，因此测验分数**只作正向证据**，绝不倒扣（没测过 ≠ 测很差）；
- Review Today 只返回 ``next_review_at <= now`` 的到期项；
- ReviewItem 与 ContentMastery 的 next_review_at 恒一致（复习时刻单一真源）；
- ``recompute`` 能把 V5.5 规则算出的老行升级到 V5.6 规则，且幂等；
- 单元测验判分会写回统一掌握度，且失败不影响判分主链路。
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 显式 config 类替代 os.environ：Config.DATABASE_URL 是导入期固化的类属性，
# 用环境变量会被先收集的其他测试模块抢先占用（详见 V5.5 修复说明）。
_fd, _PATH = tempfile.mkstemp(suffix=".db")
os.close(_fd)

from app import create_app  # noqa: E402
from config import DevelopmentConfig  # noqa: E402
from extensions import db  # noqa: E402
from models import ContentItem, ContentMastery, ReviewItem, User  # noqa: E402


class _V56Config(DevelopmentConfig):
    DATABASE_URL = f"sqlite:///{_PATH}"
    WTF_CSRF_ENABLED = False


EMAIL = "v56student@example.com"
PW = "V56Pass123456"


@pytest.fixture(scope="module")
def app():
    application = create_app(_V56Config)
    application.config["WTF_CSRF_ENABLED"] = False
    with application.app_context():
        if not User.query.filter_by(email=EMAIL).first():
            u = User(email=EMAIL, username="v56", is_admin=False)
            u.set_password(PW)
            db.session.add(u)
            db.session.commit()
    yield application
    try:
        os.remove(_PATH)
    except OSError:
        pass


@pytest.fixture
def client(app):
    c = app.test_client()
    r = c.post("/login", data={"email": EMAIL, "password": PW}, follow_redirects=False)
    assert r.status_code in (302, 303), f"login failed: {r.status_code}"
    return c


def _uid(app):
    with app.app_context():
        return User.query.filter_by(email=EMAIL).first().id


def _content_ids(app, n):
    with app.app_context():
        return [c.id for c in ContentItem.query.limit(n).all()]


# --------------------------------------------------------------------------
# 统一 level 规则（纯函数，直接用模型对象验证）
# --------------------------------------------------------------------------
def test_level_zero_when_no_record():
    from mastery_service import level_from_record
    assert level_from_record(ContentMastery()) == 0


def test_level_new_slate():
    from mastery_service import level_from_record
    m = ContentMastery(correct_count=0, wrong_count=0, review_count=0,
                       streak=0, quiz_score=0, unit_test_score=0)
    assert level_from_record(m) == 0


def test_level_learning_on_first_practice():
    from mastery_service import level_from_record
    m = ContentMastery(correct_count=1, wrong_count=0, review_count=1, streak=1)
    assert level_from_record(m) == 1


def test_level_familiar_with_steady_practice():
    from mastery_service import level_from_record
    m = ContentMastery(correct_count=2, wrong_count=0, review_count=2, streak=2)
    assert level_from_record(m) == 2


def test_level_strong_with_solid_practice():
    from mastery_service import level_from_record
    m = ContentMastery(correct_count=5, wrong_count=0, review_count=5, streak=5)
    assert level_from_record(m) == 3


def test_level_mastered_needs_practice_and_test_evidence():
    """光是练习到位还不够，必须有测验佐证才算 Mastered（V5.6 新增要求）。"""
    from mastery_service import level_from_record
    base = dict(correct_count=10, wrong_count=0, review_count=10, streak=10)
    assert level_from_record(ContentMastery(**base)) == 3          # 无测验 → 停在 Strong
    m = ContentMastery(**base, unit_test_score=90)
    assert level_from_record(m) == 4                                # 有测验佐证 → Mastered


def test_zero_score_means_not_tested_never_penalises():
    """0 分表示「没测过」，因此不能把掌握度压下去（对比有 practise 证据的行）。"""
    from mastery_service import level_from_record
    good = dict(correct_count=5, wrong_count=0, review_count=5, streak=5)
    assert level_from_record(ContentMastery(**good, unit_test_score=0)) == 3
    assert level_from_record(ContentMastery(**good, unit_test_score=80)) == 3


def test_high_unit_test_alone_is_authoritative():
    from mastery_service import level_from_record
    assert level_from_record(ContentMastery(review_count=3, unit_test_score=96)) == 4
    assert level_from_record(ContentMastery(quiz_score=90)) == 2


def test_weakness_is_reflected_by_practice_not_by_missing_test():
    """负向证据只来自练习错误，不来自「没测验」。"""
    from mastery_service import level_from_record
    m = ContentMastery(correct_count=1, wrong_count=4, review_count=5, streak=0)
    assert level_from_record(m) == 1
    assert level_from_record(ContentMastery(correct_count=0, wrong_count=3,
                                            review_count=3, streak=0)) == 1


# --------------------------------------------------------------------------
# 间隔重复：复习时刻单一真源 + Review Today
# --------------------------------------------------------------------------
def test_next_review_at_mirrors_review_item(app, client):
    """ContentMastery.next_review_at 必须与 ReviewItem 完全一致（无分叉）。"""
    cid = _content_ids(app, 1)[0]
    client.post("/practice/submit",
                json={"content_id": cid, "correct": True, "kind": "vocabulary"})
    uid = _uid(app)
    with app.app_context():
        rev = ReviewItem.query.filter_by(user_id=uid, content_id=cid).first()
        m = ContentMastery.query.filter_by(user_id=uid, content_id=cid).first()
        assert rev is not None and m is not None
        assert m.next_review_at == rev.next_review_at


def test_wrong_answer_shortens_interval(app, client):
    cid = _content_ids(app, 2)[1]
    client.post("/practice/submit",
                json={"content_id": cid, "correct": False, "kind": "vocabulary",
                      "given": "x", "expected": "y"})
    uid = _uid(app)
    with app.app_context():
        rev = ReviewItem.query.filter_by(user_id=uid, content_id=cid).first()
        assert rev.box == 1                 # 答错降回第 1 格
        assert rev.next_review_at is not None


def test_review_today_only_returns_due_items(app, client):
    """刚练过的内容会被推到未来，不该出现在今日复习里。"""
    cid = _content_ids(app, 1)[0]
    client.post("/practice/submit",
                json={"content_id": cid, "correct": True, "kind": "vocabulary"})
    uid = _uid(app)
    with app.app_context():
        from mastery_service import review_today, review_today_count
        due_ids = [d["content_id"] for d in review_today(uid, limit=50)]
        assert cid not in due_ids, "刚复习过的内容不应立即再次到期"
        assert review_today_count(uid) == len(due_ids)


def test_review_today_includes_mastery_context(app, client):
    """到期项要带上掌握度上下文（level / level_label），前端要显示。"""
    cid = _content_ids(app, 1)[0]
    client.post("/practice/submit",
                json={"content_id": cid, "correct": True, "kind": "vocabulary"})
    uid = _uid(app)
    with app.app_context():
        # 手动把这条拉回到过去，使其到期
        from datetime import timedelta
        from models import utcnow
        rev = ReviewItem.query.filter_by(user_id=uid, content_id=cid).first()
        rev.next_review_at = utcnow() - timedelta(hours=1)
        m = ContentMastery.query.filter_by(user_id=uid, content_id=cid).first()
        m.next_review_at = rev.next_review_at
        db.session.commit()

        from mastery_service import review_today
        items = {d["content_id"]: d for d in review_today(uid, limit=50)}
        assert cid in items
        assert items[cid]["level_label"] in ("未学", "学习中", "熟悉", "扎实", "已掌握")
        assert "surface" in items[cid]


# --------------------------------------------------------------------------
# recompute：老行升级 + 幂等
# --------------------------------------------------------------------------
def test_recompute_upgrades_and_is_idempotent(app, client):
    uid = _uid(app)
    cid = _content_ids(app, 3)[2]
    with app.app_context():
        m = ContentMastery.query.filter_by(user_id=uid, content_id=cid).first()
        if m is None:
            from mastery_service import get_or_create
            m = get_or_create(uid, cid)
            db.session.commit()
        # 人为塞一个「V5.5 规则可能算错」的老行：练习到位但缺测验
        m.correct_count, m.wrong_count = 10, 0
        m.review_count, m.streak = 10, 10
        m.unit_test_score = 0
        m.level = 1                    # 假装是旧规则留下的偏低结果
        db.session.commit()
        old_level = m.level

        from mastery_service import recompute
        changed = recompute(uid)
        assert changed >= 1
        db.session.expire_all()
        m = ContentMastery.query.filter_by(user_id=uid, content_id=cid).first()
        assert m.level != old_level
        assert m.level >= 1

        assert recompute(uid) == 0     # 幂等：再跑一次无变化


# --------------------------------------------------------------------------
# 掌握度汇总（Dashboard）
# --------------------------------------------------------------------------
def test_mastery_overview_shape(app, client):
    uid = _uid(app)
    with app.app_context():
        from mastery_service import mastery_overview
        ov = mastery_overview(uid)
        assert set(ov["distribution"]) == {0, 1, 2, 3, 4}
        assert isinstance(ov["weak_areas"], list)
        assert isinstance(ov["due_today"], int)
        assert 0 <= ov["coverage_pct"] <= 100


def test_weak_areas_lists_only_wrong_items(app, client):
    cid = _content_ids(app, 4)[3]
    client.post("/practice/submit",
                json={"content_id": cid, "correct": False, "kind": "vocabulary",
                      "given": "abc", "expected": "xyz"})
    uid = _uid(app)
    with app.app_context():
        from mastery_service import weak_areas
        ids = [w["content_id"] for w in weak_areas(uid, limit=20)]
        assert cid in ids
        entry = [w for w in weak_areas(uid, limit=20) if w["content_id"] == cid][0]
        assert entry["reasons"], "弱项必须给出错误原因（前端要展示错在哪）"


# --------------------------------------------------------------------------
# 单元测验 → 统一掌握度
# --------------------------------------------------------------------------
def test_unit_test_results_feed_mastery(app, client):
    cid = _content_ids(app, 5)[4]
    uid = _uid(app)
    with app.app_context():
        from mastery_service import record_unit_test_results
        n = record_unit_test_results(uid, [
            {"content_id": cid, "correct": True, "kind": "vocabulary",
             "given": "a", "expected": "a"},
            {"content_id": cid, "correct": True, "kind": "vocabulary",
             "given": "b", "expected": "b"},
        ], accuracy=100)
        assert n == 2
        m = ContentMastery.query.filter_by(user_id=uid, content_id=cid).first()
        assert m.unit_test_score == 100
        assert m.correct_count == 2


def test_unit_test_results_ignore_items_without_content_id(app, client):
    uid = _uid(app)
    with app.app_context():
        from mastery_service import record_unit_test_results
        before = ContentMastery.query.filter_by(user_id=uid).count()
        n = record_unit_test_results(uid, [
            {"content_id": None, "correct": True},
            {"content_id": "not-an-int", "correct": True},
        ], accuracy=50)
        assert n == 0
        assert ContentMastery.query.filter_by(user_id=uid).count() == before


def test_unit_test_grade_writes_mastery_end_to_end(app, client):
    """走真实判分链路（unit_test_service.grade）：答题 → 判分 → 掌握度入账。

    grade() 里掌握度写入是次要路径且包了 try，若这里静默失败很容易漏掉，
    因此必须有一个端到端断言确认它真的落库了。
    """
    uid = _uid(app)
    with app.app_context():
        import unit_test_service as uts
        from models import Unit
        unit = Unit.query.first()
        assert unit is not None
        quiz = uts.ensure_quiz(unit, uts._lang_of(unit))
        assert quiz.questions, "单元测验必须有题目"

        before = ContentMastery.query.filter_by(user_id=uid).count()
        answers = {str(q.id): q.answer_index for q in quiz.questions}  # 全部答对
        attempt = uts.grade(uid, unit, answers)

        assert attempt.total > 0
        assert attempt.score == attempt.total      # 判分主链路未被破坏
        assert attempt.accuracy == 100
        assert attempt.passed

        after = ContentMastery.query.filter_by(user_id=uid).count()
        assert after > before, "单元测验结果必须写入统一掌握度"

        rows = ContentMastery.query.filter_by(user_id=uid).all()
        assert any(r.unit_test_score == 100 for r in rows)


def test_unit_test_grade_survives_mastery_write_failure(app, client):
    """掌握度写入失败不能影响已经算好的判分结果。"""
    uid = _uid(app)
    with app.app_context():
        import unit_test_service as uts
        from models import Unit
        unit = Unit.query.first()
        quiz = uts.ensure_quiz(unit, uts._lang_of(unit))
        answers = {str(q.id): q.answer_index for q in quiz.questions}

        import mastery_service
        original = mastery_service.record_unit_test_results

        def boom(*a, **kw):
            raise RuntimeError("模拟掌握度写入失败")

        # unit_test_service 内部是 from ... import，需在两个模块同时打桩
        mastery_service.record_unit_test_results = boom
        try:
            attempt = uts.grade(uid, unit, answers)
        finally:
            mastery_service.record_unit_test_results = original

        assert attempt.score == attempt.total   # 判分照样正确


# --------------------------------------------------------------------------
# Dashboard 渲染
# --------------------------------------------------------------------------
def test_dashboard_renders_mastery_and_weak_areas(app, client):
    html = client.get("/dashboard").get_data(as_text=True)
    assert "Mastery" in html
    for label in ("New", "Learning", "Familiar", "Strong", "Mastered"):
        assert label in html
