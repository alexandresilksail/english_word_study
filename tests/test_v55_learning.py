"""V5.5 学习内容引擎 + 练习流测试。

覆盖规格：
- 课时渲染出 Learn → Practice → Quiz → Complete 四步学习流；
- ``/practice/submit`` 上报结果后写入 ReviewItem（Leitner）+ ContentMastery
  （计数 / streak / 弱项），并算出初步 level；
- 未登录返回 401 JSON（与 REST API 一致）；
- 表单提交回跳来源页（302），JSON 提交返回 {ok,data}。
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 用临时库，让 create_app 启动时按新代码完整播种。
# 注意：不能只靠 os.environ["DATABASE_URL"]—— config.Config.DATABASE_URL 是
# **类属性**，在 config 模块被首次 import 时就固化了。若先收集了别的测试模块
# （如 test_quiz_grading.py 会 import config），这里设的环境变量将不再生效，
# create_app() 会静默连到真实 instance/ 库上。这里显式构造 config 类，
# 让本模块的库路径与导入顺序完全无关。
_fd, _PATH = tempfile.mkstemp(suffix=".db")
os.close(_fd)

from app import create_app  # noqa: E402
from config import DevelopmentConfig  # noqa: E402
from extensions import db  # noqa: E402
from models import ContentItem, ContentMastery, ReviewItem, User  # noqa: E402


class _V55Config(DevelopmentConfig):
    DATABASE_URL = f"sqlite:///{_PATH}"
    WTF_CSRF_ENABLED = False
    IP_RATELIMIT_ENABLED = False          # 测试共用 127.0.0.1，关闭以免误拦整批登录


EMAIL = "v55student@example.com"
PW = "V55Pass123456"


@pytest.fixture(scope="module")
def app():
    application = create_app(_V55Config)
    application.config["WTF_CSRF_ENABLED"] = False  # 测试内表单/JSON 提交免 CSRF
    with application.app_context():
        if not User.query.filter_by(email=EMAIL).first():
            u = User(email=EMAIL, username="v55", is_admin=False)
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
def test_lesson_view_shows_four_step_flow(app, client):
    """课时页必须渲染 Learn → Practice → Quiz → Complete 四步（语言无关属性断言）。"""
    with app.app_context():
        ci = ContentItem.query.first()
        assert ci is not None
        lid = ci.lesson_id
    html = client.get(f"/lesson/{lid}").get_data(as_text=True)
    assert 'data-step="learn"' in html
    assert 'data-step="practice"' in html
    assert 'data-step="quiz"' in html
    assert 'data-step="complete"' in html


def test_practice_submit_json_updates_mastery_and_review(app, client):
    cid = _content_ids(app, 1)[0]
    r = client.post("/practice/submit",
                    json={"content_id": cid, "correct": True, "kind": "vocabulary", "mode": "recall"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["data"]["content_id"] == cid
    assert body["data"]["correct_count"] == 1
    assert body["data"]["level"] >= 1

    uid = _uid(app)
    with app.app_context():
        m = ContentMastery.query.filter_by(user_id=uid, content_id=cid).first()
        assert m is not None
        assert m.correct_count == 1
        assert m.level >= 1
        rev = ReviewItem.query.filter_by(user_id=uid, content_id=cid).first()
        assert rev is not None  # Leitner 抗遗忘队列已写入


def test_practice_submit_wrong_marks_weak(app, client):
    cid = _content_ids(app, 2)[1]  # 与上一个测试不同的内容，避免累加干扰
    r = client.post("/practice/submit",
                    json={"content_id": cid, "correct": False, "kind": "vocabulary",
                          "mode": "recall", "given": "wrong", "expected": "right"})
    assert r.status_code == 200
    uid = _uid(app)
    with app.app_context():
        m = ContentMastery.query.filter_by(user_id=uid, content_id=cid).first()
        assert m is not None
        assert m.weak is True
        assert m.wrong_count == 1
        assert "wrong" in (m.weak_reasons or "")


def test_practice_submit_accumulates(app, client):
    cid = _content_ids(app, 3)[2]
    for _ in range(2):
        r = client.post("/practice/submit",
                        json={"content_id": cid, "correct": True, "kind": "vocabulary", "mode": "recall"})
        assert r.status_code == 200
    uid = _uid(app)
    with app.app_context():
        m = ContentMastery.query.filter_by(user_id=uid, content_id=cid).first()
        assert m.correct_count == 2
        assert m.streak == 2


def test_practice_submit_batch(app, client):
    ids = _content_ids(app, 4)
    assert len(ids) >= 3
    r = client.post("/practice/submit", json={
        "items": [{"content_id": i, "correct": True, "kind": "vocabulary"} for i in ids[:3]]})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["data"]["count"] == 3


def test_practice_submit_requires_login(app):
    """未登录统一返回 401 JSON（与 REST API 一致）。"""
    cid = _content_ids(app, 1)[0]
    c = app.test_client()  # 全新客户端，无会话
    r = c.post("/practice/submit", json={"content_id": cid, "correct": True})
    assert r.status_code == 401
    body = r.get_json()
    assert body["ok"] is False
    assert body["error"]["code"] == "unauthorized"


def test_practice_submit_form_redirects(app, client):
    """表单提交（非 AJAX）跳回来源页（302），保持渐进增强兼容。"""
    cid = _content_ids(app, 1)[0]
    r = client.post("/practice/submit",
                    data={"content_id": str(cid), "correct": "1", "kind": "vocabulary"},
                    headers={"Referer": f"http://localhost/lesson/{cid}"},
                    follow_redirects=False)
    assert r.status_code in (302, 303)


def test_practice_submit_missing_content_id(app, client):
    r = client.post("/practice/submit", json={"correct": True})
    assert r.status_code == 422
    body = r.get_json()
    assert body["ok"] is False
    assert "content_id" in body["error"]["message"]
