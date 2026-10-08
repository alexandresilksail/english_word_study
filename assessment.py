"""V6.0.1 AI Assessment 引擎：从既有 Word / ContentItem 生成分技能小测，
产出 Learner Skill Profile（六维 CEFR 等级 A1–C1）。

诚实原则（与全站一致）
----------------------
- 自动判分技能（vocab / reading / listening / grammar）用真实词库数据构造 MCQ；
- speaking / writing 在 Mock 阶段无法自动语音/写作判分，改为**自评**题（1–5），
  明确标注为自评，不伪装成客观成绩；
- 评估结论写入 ``LearnerProfile``（当前状态）+ ``Assessment``（历史快照），
  供 V6.0.3 学习路径与 V6.0.5 AI Tutor 消费；
- 不引入机器学习框架，等级映射用透明的正确率阈值（见 ``_level_from_accuracy``）。
"""
from __future__ import annotations

import json
import random

from extensions import db
from models import (Assessment, LearnerProfile, SKILL_KEYS, Word)

# CEFR 等级（自评/初测不臆造 C2，最高到 C1）
_CEFR_ORDER = ["A1", "A2", "B1", "B2", "C1"]


def _level_from_accuracy(acc: float) -> str:
    """0..1 正确率 → CEFR 等级（透明阈值，无黑盒）。"""
    if acc < 0.40:
        return "A1"
    if acc < 0.70:
        return "A2"
    if acc < 0.85:
        return "B1"
    if acc < 0.95:
        return "B2"
    return "C1"


def _audio_url(name: str) -> str:
    if not name:
        return ""
    try:
        from media_service import audio_url
        return audio_url(name)
    except Exception:  # pragma: no cover - 缺失 media_service 时回退
        return f"/static/audio/{name}"


def generate_assessment(user_id: int, target_lang: str = "en",
                       n_auto: int = 2) -> list[dict]:
    """生成一份评估题（4 自动技能×n_auto + 2 自评技能）。

    题目随本次会话以 JSON 存于表单 hidden 字段，提交时回传，避免随机重建导致判分错位。
    """
    random.seed((user_id * 1000) ^ 7)  # 稳定可复现，便于重做与测试
    questions: list[dict] = []

    words = (Word.query.filter(Word.audio != "")
             .order_by(db.func.random()).limit(60).all())
    if not words:
        words = Word.query.limit(60).all()
    pool = [w for w in words if (w.meaning_cn or w.word)]
    if not pool:  # 极端兜底：拿不到词就退化为单题，避免空评估
        pool = [type("W", (), {"word": "example", "meaning_cn": "示例",
                               "audio": ""})()]
    meanings = [w.meaning_cn or w.word for w in pool]

    auto_skills = [("vocab", "vocabulary"), ("reading", "reading"),
                   ("listening", "listening"), ("grammar", "grammar")]
    idx = 0
    for skill, _kind in auto_skills:
        for _ in range(n_auto):
            idx += 1
            w = random.choice(pool)
            correct = w.meaning_cn or w.word
            distract = [m for m in meanings if m != correct]
            random.shuffle(distract)
            options = [correct] + distract[:3]
            random.shuffle(options)
            questions.append({
                "id": f"q{idx}",
                "skill": skill,
                "kind": "mcq",
                "prompt": (f"Listen, then choose the meaning of “{w.word}”."
                           if skill == "listening"
                           else f"What does “{w.word}” mean?"),
                "word": w.word,
                "audio_url": _audio_url(w.audio) if skill == "listening" else "",
                "options": options,
                "answer_index": options.index(correct),
            })

    for skill in ("speaking", "writing"):
        idx += 1
        questions.append({
            "id": f"q{idx}",
            "skill": skill,
            "kind": "self",
            "prompt": f"Rate your {skill} confidence (1 = beginner … 5 = fluent)",
            "max": 5,
        })
    return questions


def grade_assessment(questions: list[dict], submitted: dict) -> dict:
    """判分。``submitted``: {qid: int}。返回六维等级 + overall + 弱项 + 作答历史。"""
    per_skill: dict[str, list[float]] = {s: [] for s in SKILL_KEYS}
    history: list[dict] = []
    for q in questions:
        skill = q["skill"]
        val = submitted.get(q["id"])
        if val is None or val == "":
            continue
        if q["kind"] == "mcq":
            correct = int(val) == int(q["answer_index"])
            per_skill[skill].append(1.0 if correct else 0.0)
            history.append({"qid": q["id"], "skill": skill, "kind": "mcq",
                            "correct": correct})
        else:  # self：把 1–5 归一化为正确率代理
            try:
                score = max(0.0, min(1.0, float(val) / float(q.get("max", 5))))
            except (TypeError, ValueError):
                score = 0.0
            per_skill[skill].append(score)
            history.append({"qid": q["id"], "skill": skill, "kind": "self",
                            "value": float(val)})

    levels = {}
    for s in SKILL_KEYS:
        samples = per_skill[s]
        acc = sum(samples) / len(samples) if samples else 0.0
        levels[s] = _level_from_accuracy(acc)

    all_samples = [x for v in per_skill.values() for x in v]
    overall_acc = sum(all_samples) / len(all_samples) if all_samples else 0.0
    overall = _level_from_accuracy(overall_acc)

    weak = [s for s in SKILL_KEYS if levels[s] == "A1"]
    return {
        "overall_level": overall,
        "skills": levels,
        "weak_areas": weak,
        "answers": history,
    }


def save_assessment(user_id: int, result: dict, native_lang: str = "zh",
                   target_lang: str = "en", preferences: str = "") -> Assessment:
    """写入 Assessment 快照，并 upsert LearnerProfile 当前状态。"""
    a = Assessment(
        user_id=user_id,
        overall_level=result["overall_level"],
        vocab_level=result["skills"]["vocab"],
        grammar_level=result["skills"]["grammar"],
        reading_level=result["skills"]["reading"],
        listening_level=result["skills"]["listening"],
        speaking_level=result["skills"]["speaking"],
        writing_level=result["skills"]["writing"],
        answers=json.dumps(result["answers"], ensure_ascii=False),
    )
    db.session.add(a)

    prof = LearnerProfile.query.filter_by(user_id=user_id).first()
    if prof is None:
        prof = LearnerProfile(user_id=user_id)
        db.session.add(prof)
    prof.native_language = native_lang
    prof.target_language = target_lang
    prof.vocab_level = result["skills"]["vocab"]
    prof.grammar_level = result["skills"]["grammar"]
    prof.reading_level = result["skills"]["reading"]
    prof.listening_level = result["skills"]["listening"]
    prof.speaking_level = result["skills"]["speaking"]
    prof.writing_level = result["skills"]["writing"]
    prof.overall_level = result["overall_level"]
    prof.weak_areas = ",".join(result["weak_areas"])
    prof.learning_preferences = preferences
    db.session.commit()
    return a
