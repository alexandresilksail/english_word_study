"""V5.9 运维 CLI 测试（recompute-mastery / purge-usage）。

这两个命令是「遗留项」的收口：

* ``recompute-mastery`` —— 掌握度规则演进后刷平老数据。之前只有 Python 函数
  ``mastery_service.recompute()``，部署到服务器后运维无从下手（要么写临时脚本，
  要么直接改库）。补上 CLI 后，首次部署执行一次即可。
* ``purge-usage`` —— 每日配额引入的 ``usage_counters`` 会持续膨胀
  （用户数 × 功能数 × 天数），没有清理入口就是个慢性炸弹。

重点覆盖：
- **幂等**：第二次执行必须「更新 0 行」，否则说明判定逻辑不稳定；
- **作用域**：``--user-id`` 不能误伤其他用户的数据；
- **CLI 可用性**：命令真的注册上了、参数真的透传了（这类问题跑单测发现不了）。
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_fd, _PATH = tempfile.mkstemp(suffix=".db")
os.close(_fd)

from app import create_app  # noqa: E402
from config import DevelopmentConfig  # noqa: E402
from extensions import db  # noqa: E402
from models import ContentItem, ContentMastery, User  # noqa: E402

EMAIL_A = "clia@example.com"
EMAIL_B = "clib@example.com"
PW = "CliTest123456"


class _CliConfig(DevelopmentConfig):
    DATABASE_URL = f"sqlite:///{_PATH}"
    WTF_CSRF_ENABLED = False


@pytest.fixture(scope="module")
def app():
    application = create_app(_CliConfig)
    with application.app_context():
        for email in (EMAIL_A, EMAIL_B):
            if not User.query.filter_by(email=email).first():
                u = User(email=email, username=email.split("@")[0], is_admin=False)
                u.set_password(PW)
                db.session.add(u)
        db.session.commit()
    yield application
    try:
        os.remove(_PATH)
    except OSError:
        pass


@pytest.fixture
def runner(app):
    return app.test_cli_runner()


def _uid(app, email):
    with app.app_context():
        return User.query.filter_by(email=email).first().id


@pytest.fixture(autouse=True)
def _clean(app):
    """每个用例从干净状态开始。"""
    with app.app_context():
        ContentMastery.query.delete()
        from models import UsageCounter
        UsageCounter.query.delete()
        db.session.commit()
    yield
    with app.app_context():
        ContentMastery.query.delete()
        from models import UsageCounter
        UsageCounter.query.delete()
        db.session.commit()


def _make_mastery(app, email, level_stored: int, **kw) -> int:
    """造一条「level 字段与当前规则不符」的掌握度行，模拟规则升级前的老数据。"""
    with app.app_context():
        uid = _uid(app, email)
        item = ContentItem.query.first()
        assert item is not None, "需要预置的 ContentItem（seed_courses 会生成）"
        m = ContentMastery(user_id=uid, content_id=item.id,
                           level=level_stored, **kw)
        db.session.add(m)
        db.session.commit()
        return m.id


# --------------------------------------------------------------------------
# recompute-mastery
# --------------------------------------------------------------------------
def test_recompute_command_is_registered(app, runner):
    """命令没注册上，运维执行时会报「No such command」—— 必须先验证。"""
    r = runner.invoke(args=["--help"])
    assert r.exit_code == 0
    assert "recompute-mastery" in r.output
    assert "purge-usage" in r.output


def test_recompute_updates_stale_levels(app, runner):
    """老数据：练了 10 次、正确率 100%，却存着 level=1（V5.5 旧规则产物）。

    按 V5.6 统一规则，它满足 acc>=80 且 streak>=3 且 review_count>=3 → level 3。
    """
    mid = _make_mastery(app, EMAIL_A, level_stored=1,
                        correct_count=10, wrong_count=0, review_count=10, streak=10)
    r = runner.invoke(args=["recompute-mastery"])
    assert r.exit_code == 0, r.output
    assert "更新 1 行" in r.output
    with app.app_context():
        assert db.session.get(ContentMastery, mid).level == 3


def test_recompute_is_idempotent(app, runner):
    """第二次执行必须是「更新 0 行」—— 否则规则判定不稳定，每次跑都在抖。"""
    _make_mastery(app, EMAIL_A, level_stored=1,
                  correct_count=10, wrong_count=0, review_count=10, streak=10)
    assert runner.invoke(args=["recompute-mastery"]).exit_code == 0
    r2 = runner.invoke(args=["recompute-mastery"])
    assert r2.exit_code == 0, r2.output
    assert "更新 0 行" in r2.output


def test_recompute_scoped_to_single_user(app, runner):
    """--user-id 只能影响指定用户，不能顺手刷别人的数据。"""
    mid_a = _make_mastery(app, EMAIL_A, level_stored=0,
                          correct_count=10, wrong_count=0, review_count=10, streak=10)
    # B 用户用第二条内容，避免撞唯一约束
    with app.app_context():
        uid_b = _uid(app, EMAIL_B)
        items = ContentItem.query.limit(2).all()
        m_b = ContentMastery(user_id=uid_b, content_id=items[1].id, level=0,
                             correct_count=10, wrong_count=0, review_count=10, streak=10)
        db.session.add(m_b)
        db.session.commit()
        mid_b = m_b.id

    r = runner.invoke(args=["recompute-mastery", "--user-id", str(_uid(app, EMAIL_A))])
    assert r.exit_code == 0, r.output
    assert f"用户 {_uid(app, EMAIL_A)}" in r.output
    assert "更新 1 行" in r.output

    with app.app_context():
        assert db.session.get(ContentMastery, mid_a).level == 3   # 已刷新
        assert db.session.get(ContentMastery, mid_b).level == 0   # 未受影响


def test_recompute_with_empty_table(app, runner):
    """空表执行不能崩，且输出「更新 0 行」。"""
    r = runner.invoke(args=["recompute-mastery"])
    assert r.exit_code == 0, r.output
    assert "更新 0 行" in r.output


# --------------------------------------------------------------------------
# purge-usage
# --------------------------------------------------------------------------
def test_purge_usage_removes_only_old_rows(app, runner):
    with app.app_context():
        from models import UsageCounter
        from entitlements import today
        uid = _uid(app, EMAIL_A)
        db.session.add(UsageCounter(user_id=uid, feature="ai_tutor", day="2000-01-01", count=9))
        db.session.add(UsageCounter(user_id=uid, feature="ai_tutor", day=today(), count=1))
        db.session.commit()

    r = runner.invoke(args=["purge-usage", "--days", "30"])
    assert r.exit_code == 0, r.output
    assert "已清理 1 条" in r.output

    with app.app_context():
        from models import UsageCounter
        assert UsageCounter.query.count() == 1
        from entitlements import today
        assert UsageCounter.query.first().day == today()


def test_purge_usage_keeps_everything_when_fresh(app, runner):
    with app.app_context():
        from models import UsageCounter
        from entitlements import today
        uid = _uid(app, EMAIL_A)
        db.session.add(UsageCounter(user_id=uid, feature="ai_tutor", day=today(), count=2))
        db.session.commit()

    r = runner.invoke(args=["purge-usage"])
    assert r.exit_code == 0, r.output
    assert "已清理 0 条" in r.output
    with app.app_context():
        from models import UsageCounter
        assert UsageCounter.query.count() == 1
