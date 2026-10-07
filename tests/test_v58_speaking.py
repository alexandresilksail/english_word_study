"""V5.8 口语 / 听力测试（mock 路径 + 评分口径 + API 契约）。

覆盖规格：
- STT / TTS / 发音评分三条通道，默认 practice_mode；
- 评分维度 accuracy / fluency / grammar / vocabulary；
- **诚实原则**：无评分引擎时 fluency / grammar 必须是 ``None``（不是 0）——
  0 会被读成「很差」，而真相是「没测」；
- mock 的 STT **不编造识别结果**，明确返回 available=False；
- 听力素材复用站内既有 MP3，且只返回**真能播放**的条目；
- 未登录 401 JSON。
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
from models import User  # noqa: E402


class _V58Config(DevelopmentConfig):
    DATABASE_URL = f"sqlite:///{_PATH}"
    WTF_CSRF_ENABLED = False


EMAIL = "v58student@example.com"
PW = "V58Pass123456"


@pytest.fixture(scope="module")
def app():
    application = create_app(_V58Config)
    application.config["WTF_CSRF_ENABLED"] = False
    with application.app_context():
        u = User.query.filter_by(email=EMAIL).first()
        if not u:
            u = User(email=EMAIL, username="v58", is_admin=False)
            u.set_password(PW)
            db.session.add(u)
            db.session.flush()
        # 播几个带音频的单词：听力素材必须真能命中磁盘上的 MP3，
        # 否则 test_listening_items_* 会因为列表为空而「假通过」。
        from models import Word
        if Word.query.count() == 0:
            for w, meaning in (("ability", "能力"), ("able", "能够的"),
                               ("about", "关于")):
                db.session.add(Word(word=w, initial=w[0].upper(), audio=f"{w}.mp3",
                                    meaning_cn=meaning))
        # Speaking 评分是 PRO 功能，测试用户需具备相应套餐
        from models import Subscription
        if db.session.get(Subscription, u.id) is None:
            db.session.add(Subscription(user_id=u.id, plan="pro", status="active"))
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
    assert r.status_code in (302, 303)
    return c


# --------------------------------------------------------------------------
# 状态：默认必须是练习模式
# --------------------------------------------------------------------------
def test_default_is_practice_mode():
    import importlib

    import speaking
    importlib.reload(speaking)
    st = speaking.status()
    if os.environ.get("SPEECH_API_KEY", "").strip():
        pytest.skip("环境里配了真实语音密钥")
    assert st["practice_mode"] is True
    assert st["mock"] is True
    assert speaking.is_practice_mode() is True
    assert "fluency" in st["unavailable_dimensions"]
    assert "accuracy" in st["measurable_dimensions"]


# --------------------------------------------------------------------------
# 评分口径
# --------------------------------------------------------------------------
def test_perfect_match_scores_high():
    from speaking import score
    r = score("good morning everyone", "Good morning, everyone!")
    assert r["ok"] is True
    assert r["accuracy"] == 100
    assert r["overall"] >= 90


def test_missing_words_are_reported():
    from speaking import score
    r = score("I am fine", "I am fine, thank you")
    assert r["accuracy"] < 100
    assert any("thank" in f for f in r["feedback"]), "漏读的词必须反馈出来"


def test_wrong_word_is_reported_as_correction():
    from speaking import score
    r = score("I is fine", "I am fine")
    wrong = [x for x in r["alignment"] if x["status"] == "wrong"]
    assert wrong, "替换的词必须标成 wrong"
    assert wrong[0]["expected"] == "am"
    assert any("is→am" in f for f in r["feedback"])


def test_extra_words_detected():
    from speaking import score
    r = score("I am very very fine", "I am fine")
    assert any(x["status"] == "extra" for x in r["alignment"])


def test_unmeasurable_dimensions_are_none_not_zero():
    """诚实原则：没测就是没测，不能给 0 —— 0 会被理解成「流利度极差」。"""
    from speaking import score
    r = score("good morning", "Good morning")
    assert r["fluency"] is None
    assert r["grammar"] is None
    assert r["fluency"] is not 0   # noqa: F632  明确禁止用 0 代替 None
    assert r["practice_mode"] is True
    assert r["note"]


def test_overall_only_averages_measured_dimensions():
    """overall 只对可用维度求平均，避免被 None 稀释或当成 0。"""
    from speaking import score
    r = score("I am fine", "I am fine, thank you")
    measured = [r["accuracy"], r["vocabulary"]]
    assert r["overall"] == round(sum(measured) / len(measured))


def test_score_requires_reference():
    from speaking import score
    r = score("hello", "")
    assert r["ok"] is False


def test_score_is_deterministic():
    from speaking import score
    a = score("I is fine", "I am fine, thank you")
    b = score("I is fine", "I am fine, thank you")
    assert a["overall"] == b["overall"]
    assert a["alignment"] == b["alignment"]


def test_tokenize_handles_punctuation_and_case():
    from speaking import align_words
    rows = align_words("Hello, WORLD!", "hello world")
    assert all(r["status"] in ("ok", "missing", "wrong", "extra") for r in rows)
    expects = [r["expected"] for r in rows]
    assert expects == ["hello", "world"]


# --------------------------------------------------------------------------
# STT / TTS 的诚实降级
# --------------------------------------------------------------------------
def test_stt_does_not_fabricate_transcription():
    """没有识别引擎时必须说明不可用，而不是编一段像样的识别结果。"""
    from speaking import transcribe
    res = transcribe(b"\x00" * 1024, lang="en")
    assert res["available"] is False
    assert res["text"] is None
    assert res["note"]


def test_stt_rejects_oversized_audio():
    from speaking import MAX_AUDIO_BYTES, transcribe
    res = transcribe(b"a" * (MAX_AUDIO_BYTES + 1))
    assert res["ok"] is False


def test_stt_rejects_empty_audio():
    from speaking import transcribe
    res = transcribe(b"")
    assert res["ok"] is False


def test_tts_falls_back_to_browser_speech():
    from speaking import synthesize
    res = synthesize("hello there")
    assert res["available"] is False
    assert res["hint"] == "browser-speech"   # 前端改用浏览器内置合成
    assert res["audio"] is None


def test_tts_rejects_empty_text():
    from speaking import synthesize
    assert synthesize("   ")["ok"] is False


def test_speech_provider_failure_is_explicit(app, monkeypatch):
    """真实 provider 出错要显式失败，不能悄悄退化成 mock 冒充成功。"""
    monkeypatch.setenv("SPEECH_PROVIDER", "openai")
    monkeypatch.setenv("SPEECH_API_KEY", "sk-unit-test")
    import importlib

    import speaking
    importlib.reload(speaking)
    try:
        def boom(*a, **kw):
            raise RuntimeError("network down")

        monkeypatch.setattr(speaking, "_call_stt", boom)
        res = speaking.transcribe(b"12345678", lang="en")
        assert res["ok"] is False
        assert res["available"] is True      # 引擎确实配了
        assert res["mock"] is False
    finally:
        monkeypatch.delenv("SPEECH_PROVIDER", raising=False)
        monkeypatch.delenv("SPEECH_API_KEY", raising=False)
        importlib.reload(speaking)


# --------------------------------------------------------------------------
# 听力素材（复用既有 MP3）
# --------------------------------------------------------------------------
def test_listening_items_only_returns_playable(app):
    """只返回磁盘上真存在音频的条目，避免给前端一个 404 链接。"""
    with app.app_context():
        from speaking import STATIC_AUDIO_DIR, listening_items
        items = listening_items("en", limit=5)
        assert items, "必须真的取到听力素材（否则下面的断言会假通过）"
        for it in items:
            assert it["audio_url"], "必须有可播放地址"
            assert it["surface"]
            assert os.path.isfile(os.path.join(
                STATIC_AUDIO_DIR, os.path.basename(it["audio_url"])))


def test_listening_items_skips_words_without_audio_file(app):
    """audio 字段写了但文件不在，也必须被剔除。"""
    with app.app_context():
        from extensions import db
        from models import Word
        from speaking import listening_items
        if Word.query.filter_by(word="ghostword").first() is None:
            db.session.add(Word(word="ghostword", initial="G",
                                audio="ghostword-does-not-exist.mp3",
                                meaning_cn="幽灵词"))
            db.session.commit()
        surfaces = [i["surface"] for i in listening_items("en", limit=50)]
        assert "ghostword" not in surfaces


def test_listening_non_english_returns_empty(app):
    """MP3 只有英文词库一份，其余语种返回空列表由前端展示空态。"""
    with app.app_context():
        from speaking import listening_items
        assert listening_items("yue", limit=5) == []


def test_audio_for_word_maps_to_existing_mp3(app):
    with app.app_context():
        from models import Word
        from speaking import STATIC_AUDIO_DIR, audio_for_word
        w = Word.query.filter(Word.audio != "").first()
        if w is None:
            pytest.skip("词库为空")
        name = audio_for_word(w)
        assert name, f"{w.word} 应能映射到 MP3"
        assert os.path.isfile(os.path.join(STATIC_AUDIO_DIR, name))


@pytest.mark.parametrize("bad", [
    "../../etc/passwd", "..\\evil.mp3", "sub/dir.mp3", "x.wav", "",
])
def test_audio_filename_is_safety_checked(bad):
    """路径穿越 / 非 mp3 后缀一律拒绝，不能被拼进静态目录。"""
    from speaking import _safe_name
    assert _safe_name(bad) is False


def test_audio_for_word_returns_none_for_unknown():
    class _Fake:
        audio = ""
        word = "definitely-not-a-real-word-zzz999"

    from speaking import audio_for_word
    assert audio_for_word(_Fake()) is None


# --------------------------------------------------------------------------
# API 契约
# --------------------------------------------------------------------------
def test_api_status(app, client):
    r = client.get("/api/speaking/status")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert "practice_mode" in body["data"]


def test_api_requires_login(app):
    fresh = app.test_client()
    r = fresh.post("/api/speaking/score", json={"text": "a", "reference": "a"})
    assert r.status_code == 401
    assert r.get_json()["error"]["code"] == "unauthorized"


def test_api_score(app, client):
    r = client.post("/api/speaking/score",
                    json={"text": "good morning", "reference": "Good morning!",
                          "lang": "en"})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["data"]["accuracy"] == 100


def test_api_score_missing_reference(app, client):
    r = client.post("/api/speaking/score", json={"text": "hi"})
    assert r.status_code == 422


def test_api_score_missing_text(app, client):
    r = client.post("/api/speaking/score", json={"reference": "hi"})
    assert r.status_code == 422


def test_api_transcribe_missing_audio_returns_422(app, client):
    """没有音频文件应返回 422（与其它端点的坏输入契约一致）。"""
    r = client.post("/api/speaking/transcribe", data={"lang": "en"})
    assert r.status_code == 422
    assert r.get_json()["ok"] is False


def test_api_transcribe_no_engine_is_explicit(app, client):
    """没接识别引擎时返回 200 + available=false，让前端切到手动输入。"""
    from io import BytesIO
    r = client.post("/api/speaking/transcribe",
                    data={"lang": "en",
                          "audio": (BytesIO(b"12345678"), "speech.webm")},
                    content_type="multipart/form-data")
    assert r.status_code == 200
    data = r.get_json()["data"]
    assert data["available"] is False
    assert data["text"] is None


def test_api_listening(app, client):
    r = client.get("/api/speaking/listening?lang=en&limit=3")
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert "items" in body["data"]


def test_speaking_page_renders(app, client):
    """页面结构与关键交互点必须都在。

    断言刻意用 **element id 而不是文案**：``pick()`` 会按当前语言只渲染一种
    文案，写死中/英文字符串会让这个测试随语言设置无谓失败。
    """
    html = client.get("/speaking").get_data(as_text=True)
    assert "spk-card" in html
    assert 'id="btn-score"' in html
    assert 'id="btn-rec"' in html
    assert 'id="spk-input"' in html
    assert 'id="ref-text"' in html
    # 默认无引擎 → 必须出现练习模式提示块
    assert "练习模式" in html or "Practice Mode" in html
