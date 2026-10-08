"""V5.4 粤语（Cantonese, language_code=yue）课程完整性测试。

覆盖规格 §15：
- 一门粤语入门课程（Cantonese Beginner, A1），6 个单元
  （Greetings / Numbers / Time / Family / Daily Life / Basic Conversation）；
- 每单元 7 类课时：Vocabulary / Phrase / Sentence / Listening / Practice / Quiz / Review；
- 每条内容含 繁体中文 surface + Jyutping(phonetic) + 中文义(meaning_yue) + 英文解释(meaning_en) + 例句(example_en)；
- 绝不把 synthetic-dev / 其它英文源混入粤语课程（language_code 恒为 yue）。
"""
from __future__ import annotations

import os
import sys
import tempfile

import pytest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# 用临时库，让 create_app 启动时按新代码完整播种粤语课程。
# 显式 config 类，避免依赖 os.environ 与模块导入顺序（Config.DATABASE_URL
# 是类属性，config 首次被 import 时即固化）。
_fd, _PATH = tempfile.mkstemp(suffix=".db")
os.close(_fd)

from app import create_app  # noqa: E402
from config import DevelopmentConfig  # noqa: E402
from models import Course, Unit, Lesson, ContentItem  # noqa: E402
from seed_courses import YUE_UNITS, YUE_VOCAB, YUE_PHRASE, YUE_SENTENCE  # noqa: E402


class _V54Config(DevelopmentConfig):
    DATABASE_URL = f"sqlite:///{_PATH}"
    IP_RATELIMIT_ENABLED = False          # 测试共用 127.0.0.1，关闭以免误拦整批登录


EXPECTED = {
    1: dict(vocabulary=5, phrase=3, sentence=3, listening=3, practice=6, quiz=11, review=8),
    2: dict(vocabulary=6, phrase=3, sentence=3, listening=3, practice=6, quiz=12, review=9),
    3: dict(vocabulary=5, phrase=3, sentence=3, listening=3, practice=6, quiz=11, review=8),
    4: dict(vocabulary=5, phrase=3, sentence=3, listening=3, practice=6, quiz=11, review=8),
    5: dict(vocabulary=5, phrase=3, sentence=3, listening=3, practice=6, quiz=11, review=8),
    6: dict(vocabulary=5, phrase=3, sentence=3, listening=3, practice=6, quiz=11, review=8),
}

LESSON_KINDS = ["vocabulary", "phrase", "sentence", "listening", "practice", "quiz", "review"]


@pytest.fixture(scope="module")
def app():
    application = create_app(_V54Config)
    yield application
    try:
        os.remove(_PATH)
    except OSError:
        pass


def test_yue_course_exists(app):
    with app.app_context():
        c = Course.query.filter_by(language_code="yue").first()
        assert c is not None
        assert c.cefr_level == "A1"
        assert c.title_en == "Cantonese Beginner"
        # 6 个单元且标题与 YUE_UNITS 一致
        units = Unit.query.filter_by(course_id=c.id).order_by(Unit.no).all()
        assert len(units) == 6
        for u, (zh, en, _) in zip(units, YUE_UNITS):
            assert u.title_en == en


def test_yue_seven_lesson_kinds_per_unit(app):
    with app.app_context():
        c = Course.query.filter_by(language_code="yue").first()
        for u in Unit.query.filter_by(course_id=c.id).order_by(Unit.no):
            kinds = sorted(l.kind for l in Lesson.query.filter_by(unit_id=u.id))
            assert kinds == sorted(LESSON_KINDS)


def test_yue_content_counts_match_seed(app):
    with app.app_context():
        c = Course.query.filter_by(language_code="yue").first()
        for u in Unit.query.filter_by(course_id=c.id).order_by(Unit.no):
            lessons = {l.kind: l for l in Lesson.query.filter_by(unit_id=u.id)}
            exp = EXPECTED[u.no]
            for kind, want in exp.items():
                got = ContentItem.query.filter_by(lesson_id=lessons[kind].id).count()
                assert got == want, f"U{u.no} {kind}: expected {want}, got {got}"


def test_yue_items_carry_jyutping_and_english(app):
    with app.app_context():
        c = Course.query.filter_by(language_code="yue").first()
        vocab = (ContentItem.query.join(Lesson).join(Unit)
                 .filter(Unit.course_id == c.id, ContentItem.kind == "vocabulary"))
        assert vocab.count() > 0
        for ci in vocab.limit(20):
            assert ci.phonetic.strip(), "Jyutping (phonetic) must be present"
            assert ci.meaning_en.strip(), "English explanation (meaning_en) must be present"
            assert ci.meaning_yue.strip(), "Traditional Chinese gloss (meaning_yue) must be present"
            assert ci.example_en.strip(), "English example must be present"
            # 粤语课程绝不混入英文源
            assert ci.lesson.unit.course_id == c.id


def test_yue_upgrade_idempotent_on_existing(app):
    """增量升级对已完整课程应无副作用（need_kinds 为空）。"""
    from seed_courses import _upgrade_cantonese
    with app.app_context():
        before = Lesson.query.join(Unit).join(Course).filter(
            Course.language_code == "yue").count()
        _upgrade_cantonese()
        after = Lesson.query.join(Unit).join(Course).filter(
            Course.language_code == "yue").count()
        assert before == after
