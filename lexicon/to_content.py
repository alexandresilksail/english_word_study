"""LexiconEntry -> ContentItem 转换桥（按筛选条件生成子集，绝不批量生成全部 7000）。

master lexicon 与课程内容解耦：
- 词库只负责「词条本身」；
- 课程由本模块按 CEFR / 频率 / 主题 / 难度 / 语言 / kind 筛选后生成；
- 默认只在「Lexicon Sandbox」沙盒课程下创建少量条目，便于先验证再推广。
"""
from __future__ import annotations

from sqlalchemy import or_

from models import (
    Course, Unit, Lesson, ContentItem, LexiconEntry,
)

_SANDBOX = "Lexicon Sandbox"


def _ensure_sandbox_lesson(session):
    course = session.query(Course).filter_by(title_en=_SANDBOX).first()
    if course is None:
        course = Course(
            language_code="en", cefr_level="A1",
            title_zh="词库沙盒", title_en=_SANDBOX,
            description="Auto-created sandbox for lexicon->content conversion (V5.1).",
        )
        session.add(course)
        session.flush()
    unit = session.query(Unit).filter_by(course_id=course.id, title_en=_SANDBOX).first()
    if unit is None:
        unit = Unit(course_id=course.id, no=1, title_zh="词库沙盒", title_en=_SANDBOX)
        session.add(unit)
        session.flush()
    lesson = session.query(Lesson).filter_by(unit_id=unit.id, title_en=_SANDBOX).first()
    if lesson is None:
        lesson = Lesson(unit_id=unit.id, no=1, kind="vocabulary",
                       title_zh="词库沙盒", title_en=_SANDBOX)
        session.add(lesson)
        session.flush()
    return lesson


def convert_to_content(session, *, language_code=None, cefr=None, topic=None,
                       kind=None, difficulty_max=None, limit: int = 50,
                       dry_run: bool = False, lesson_id=None,
                       only_production_ready: bool = False):
    """把筛选后的 LexiconEntry 转成 ContentItem。

    only_production_ready=True 时只挑选 production_ready 为真的词条，
    用于正式课程内容（绝不使用 synthetic-dev / 未核实 license 的词条）。
    默认 False 以兼容开发期 / 沙盒转换。
    """
    q = session.query(LexiconEntry)
    if language_code:
        q = q.filter(LexiconEntry.language_code == language_code)
    if cefr:
        q = q.filter(LexiconEntry.cefr == cefr)
    if topic:
        q = q.filter(LexiconEntry.topic == topic)
    if kind:
        q = q.filter(LexiconEntry.kind == kind)
    if difficulty_max is not None:
        q = q.filter(LexiconEntry.difficulty <= difficulty_max)
    if only_production_ready:
        q = q.filter(LexiconEntry.production_ready == True)  # noqa: E712
    # 只挑有释义的条目，避免空内容
    q = q.filter(or_(LexiconEntry.meaning_en != "", LexiconEntry.meaning_zh != ""))
    q = q.limit(limit)
    entries = q.all()

    if dry_run:
        return {"would_create": len(entries), "dry_run": True}

    lesson = (session.query(Lesson).filter_by(id=lesson_id).first()
              if lesson_id else _ensure_sandbox_lesson(session))
    created = 0
    for le in entries:
        exists = session.query(ContentItem).filter_by(
            lesson_id=lesson.id, surface=le.surface, kind=le.kind).first()
        if exists:
            continue
        ci = ContentItem(
            lesson_id=lesson.id, kind=le.kind, surface=le.surface,
            phonetic=le.jyutping or le.pronunciation, pos=le.pos,
            meaning_en=le.meaning_en, meaning_cn=le.meaning_zh, meaning_yue=le.meaning_zh,
            example_en=le.example_en, example_cn=le.example_zh, example_yue=le.example_zh,
            topic=le.topic, difficulty=le.difficulty,
        )
        session.add(ci)
        created += 1
    session.commit()
    return {"created": created, "lesson_id": lesson.id}
