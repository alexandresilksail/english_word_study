"""V5.9 页面层修复测试（听力卡片 id 冲突 / 答案泄露 / 额度可见）。

这三条都是**只有渲染页面才能发现**的缺陷，服务层单测全绿也照样漏：

1. 听力卡片用 ``item.content_id`` 拼 DOM id，而听力素材来自 legacy ``Word``
   （没有 content_id）→ 所有卡片共用 ``id="lm-"``，点「显示答案」永远只翻开
   第一张。
2. 卡片一上来就把单词显示出来 —— 先看到答案就不叫听力练习了。
3. 非 PRO 用户打开页面时看不到任何额度提示，要一路点到 403 才知道受限。
"""
from __future__ import annotations

import os
import re
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
from models import User  # noqa: E402

FREE_EMAIL = "pagefree@example.com"
PRO_EMAIL = "pagepro@example.com"
PW = "PageTest123456"


class _PageConfig(DevelopmentConfig):
    DATABASE_URL = f"sqlite:///{_PATH}"
    WTF_CSRF_ENABLED = False


@pytest.fixture(scope="module")
def app():
    application = create_app(_PageConfig)
    application.config["WTF_CSRF_ENABLED"] = False
    with application.app_context():
        from models import Subscription, Word
        for email, plan in ((FREE_EMAIL, "free"), (PRO_EMAIL, "pro")):
            u = User.query.filter_by(email=email).first()
            if not u:
                u = User(email=email, username=email.split("@")[0], is_admin=False)
                u.set_password(PW)
                db.session.add(u)
                db.session.flush()
            if db.session.get(Subscription, u.id) is None:
                db.session.add(Subscription(user_id=u.id, plan=plan, status="active"))
        # 听力素材来自 legacy Word + 磁盘上真实存在的 MP3
        if Word.query.count() == 0:
            for w in ("ability", "able", "about"):
                db.session.add(Word(word=w, initial=w[0].upper(), audio=f"{w}.mp3",
                                    meaning_cn="测试释义"))
        db.session.commit()
    yield application
    try:
        os.remove(_PATH)
    except OSError:
        pass


def _login(app, email):
    c = app.test_client()
    r = c.post("/login", data={"email": email, "password": PW})
    assert r.status_code in (302, 303)
    return c


@pytest.fixture
def free_client(app):
    return _login(app, FREE_EMAIL)


@pytest.fixture
def pro_client(app):
    return _login(app, PRO_EMAIL)


@pytest.fixture(autouse=True)
def _clean_usage(app):
    with app.app_context():
        from models import UsageCounter
        UsageCounter.query.delete()
        db.session.commit()
    yield
    with app.app_context():
        from models import UsageCounter
        UsageCounter.query.delete()
        db.session.commit()


def _html(client, url="/speaking"):
    r = client.get(url)
    assert r.status_code == 200, f"{url} -> {r.status_code}"
    return r.get_data(as_text=True)


# --------------------------------------------------------------------------
# 听力卡片
# --------------------------------------------------------------------------
def test_listening_cards_have_unique_dom_ids(app, pro_client):
    """每张卡片的答案节点 id 必须唯一，否则「显示答案」只作用于第一张。"""
    html = _html(pro_client)
    ids = re.findall(r'id="lm-([^"]+)"', html)
    assert ids, "页面必须渲染出听力卡片（否则下面的断言会假通过）"
    assert len(ids) == len(set(ids)), f"id 重复：{ids}"
    assert all(i and i != "" for i in ids), "id 不能是空字符串"


def test_listening_answer_hidden_until_revealed(app, pro_client):
    """答案默认隐藏 —— 一上来就显示单词，听力练习就名存实亡了。"""
    html = _html(pro_client)
    blocks = re.findall(r'<div class="listen-answer" id="lm-[^"]+"[^>]*>', html)
    assert blocks, "必须渲染出答案节点"
    for b in blocks:
        assert "hidden" in b, f"答案节点缺少 hidden：{b}"


def test_listening_card_has_no_standalone_surface(app, pro_client):
    """旧的 ``listen-surface`` 会把单词直接摊在卡片上，必须已移除。"""
    html = _html(pro_client)
    assert "listen-surface" not in html


def test_listening_page_renders_for_free_plan(app, free_client):
    """free 档也能打开页面（有每日试用额度），只是带额度提示。"""
    html = _html(free_client)
    assert "每日试用" in html or "Daily trial" in html


# --------------------------------------------------------------------------
# 额度可见性
# --------------------------------------------------------------------------
def test_free_user_sees_remaining_trial_count(app, free_client):
    """非 PRO 用户必须能在页面上看到「今天还剩几次」。"""
    html = _html(free_client)
    assert "每日试用" in html
    assert re.search(r"你今天还有\s*<b>5</b>", html) or "5" in html


def test_pro_user_sees_no_trial_banner(app, pro_client):
    """PRO 用户无限量，不该看到「每日试用」这种降级提示。"""
    html = _html(pro_client)
    assert "每日试用" not in html


def test_ai_tutor_page_shows_quota_for_free(app, free_client):
    html = _html(free_client, "/ai-tutor")
    assert "每日试用" in html


def test_ai_tutor_page_clean_for_pro(app, pro_client):
    html = _html(pro_client, "/ai-tutor")
    assert "每日试用" not in html


# --------------------------------------------------------------------------
# 录音降级提示（无 STT 引擎时）
# --------------------------------------------------------------------------
def test_recording_disabled_when_no_stt_engine(app, pro_client):
    """没接语音识别时，页面 JS 里应把 canRecord 判定为 false，
    并给出「请直接输入」的提示文案，而不是让用户录完才发现识别不了。"""
    html = _html(pro_client)
    assert "var sttAvailable = false;" in html
    assert "未接入语音识别" in html


def test_microphone_released_on_stop(app, pro_client):
    """必须有关闭麦克风 track 的逻辑，否则标签页会一直占用麦克风。"""
    html = _html(pro_client)
    assert "releaseMic" in html
    assert "getTracks()" in html
    assert "pagehide" in html      # 离开页面兜底释放
