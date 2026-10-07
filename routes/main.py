"""首页、控制面板、个人资料、健康检查。"""
from __future__ import annotations

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import func

from extensions import db
from forms import ChangePasswordForm, ProfileForm
from gamification import game_stats, overview, recent_games, skill_stats
from media_service import audio_url
from models import GAMES, SKILLS, StudyRecord, TestRecord, User, Word
from services import (daily_trend, dashboard_stats, learning_streak, today_start,
                      unlocked_badges)

main_bp = Blueprint("main", __name__)


def _skill_cards(uid: int) -> list[dict]:
    """Dashboard 上的六项技能卡（与 /learn 共用同一份数据源）。"""
    from routes.learn import SKILL_TEXT
    rows = skill_stats(uid)
    out = []
    for s in SKILLS:
        txt = SKILL_TEXT.get(s["key"], {})
        row = rows.get(s["key"])
        xp = row.xp if row else 0
        out.append({
            **s, **txt, "xp": xp, "items": row.items if row else 0,
            "percent": min(100, round(xp * 100 / 1000)),
            "url": (url_for("words.daily") if s["key"] == "vocabulary"
                    else url_for("learn.skill", key=s["key"])),
        })
    return out


def _game_cards(uid: int) -> list[dict]:
    from routes.games import GAME_TEXT
    rows = game_stats(uid)
    out = []
    for g in GAMES:
        s = rows.get(g["key"])
        out.append({
            **g, **GAME_TEXT.get(g["key"], {}),
            "url": url_for("games.play", key=g["key"]),
            "best": s.best_score if s else 0,
            "plays": s.plays if s else 0,
        })
    return out


def daily_challenge(uid: int, gam: dict) -> dict:
    """每日挑战：把「今日目标」变成一个具体可点的动作。

    不伪造内容 —— 全部指向真实已有的学习与测试功能。
    """
    goal = gam.get("daily_goal", 20)
    done = gam.get("today_done", 0)
    left = max(0, goal - done)
    if left == 0:
        return {"done": True, "title_zh": "今日目标已完成", "title_en": "Goal completed",
                "desc_zh": "可以去做一组测试或玩个小游戏巩固一下",
                "desc_en": "Take a quiz or play a game to keep the streak alive",
                "url": url_for("games.index"), "cta_zh": "去玩游戏", "cta_en": "Play games",
                "goal": goal, "done_count": done}
    return {"done": False, "goal": goal, "done_count": done, "left": left,
            "title_zh": f"今日目标：{goal} 个词", "title_en": f"Today's goal: {goal} words",
            "desc_zh": f"还差 {left} 个，从今日学习继续", "desc_en": f"{left} to go — continue learning",
            "url": url_for("words.daily"), "cta_zh": "继续学习", "cta_en": "Continue"}


@main_bp.route("/")
def index():
    """未登录：产品落地页。已登录：直接进入学习主页。"""
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    # 落地页推荐第一个课程单元（不是"每日一词"）
    from models import Course, Lesson, Unit
    rec_unit = (Unit.query.join(Course, Course.id == Unit.course_id)
                .filter(Course.language_code == "en", Course.cefr_level == "A1")
                .order_by(Unit.no).first())
    rec_lessons = []
    if rec_unit:
        rec_lessons = Lesson.query.filter_by(unit_id=rec_unit.id).order_by(Lesson.no).limit(4).all()
    return render_template("index.html", rec_unit=rec_unit, rec_lessons=rec_lessons)


@main_bp.route("/set-lang")
def set_lang():
    """界面语言切换：zh / en / yue / both。登录用户写账号，未登录写 Cookie。

    ``en`` 为**纯英文版面**：界面与学习内容一律不出现中文；
    ``both`` 为中文站默认的双语同显模式。
    """
    from localization import UI_LANGS
    allowed = tuple(UI_LANGS) + ("both",)
    lang = (request.args.get("lang") or "").strip().lower()
    nxt = request.args.get("next") or request.referrer or url_for("main.index")
    if lang not in allowed:
        return redirect(nxt)
    resp = redirect(nxt)
    resp.set_cookie("ui_lang", lang, max_age=3600 * 24 * 365, samesite="Lax")
    if current_user.is_authenticated:
        # 双语模式落到账号上记作“未显式选择”，保持既有语义
        current_user.preferred_lang = lang if lang in UI_LANGS else None
        db.session.commit()
    return resp


@main_bp.route("/path")
@main_bp.route("/path/<lang_code>")
@login_required
def learning_path(lang_code: str = "en"):
    """Duolingo 式学习路径：CEFR 阶梯 + 单元节点 + 解锁状态。"""
    from learning_path import LEARNING_LANGUAGES
    from path_service import level_progress
    langs = LEARNING_LANGUAGES
    if lang_code != "en":
        # 其他课程（粤语/日语…）尚未开放内容
        target = next((l for l in langs if l["code"] == lang_code), None)
        return render_template("path_coming.html", langs=langs, target=target)
    levels, current = level_progress(current_user.id)
    return render_template("path.html", langs=langs, levels=levels,
                           current=current, lang_code=lang_code)


@main_bp.route("/dashboard")
@login_required
def dashboard():
    uid = current_user.id
    stats = dashboard_stats(uid)
    stats["streak"] = learning_streak(uid)
    trend = daily_trend(uid, 7)

    recent_tests = (TestRecord.query.filter_by(user_id=uid)
                    .order_by(TestRecord.created_at.desc()).limit(5).all())
    today_new = max(stats["total_words"] - stats["learned"], 0)

    # ---- V3.0：游戏化 / 技能 / 游戏 / 每日挑战 ----
    gam = overview(uid)
    skills = _skill_cards(uid)
    cards = _game_cards(uid)
    challenge = daily_challenge(uid, gam)

    # ---- V5：学习路径（当前位置 / 下一课程 / 锁定等级） ----
    from path_service import level_progress
    path_levels, path_current = level_progress(uid)
    from review_service import due_count_by_kind
    review_today = due_count_by_kind(uid)
    from models import UserOnboarding
    onb = UserOnboarding.query.get(uid)

    return render_template("dashboard.html", stats=stats, trend=trend,
                           recent_tests=recent_tests, badges=unlocked_badges(stats),
                           today_new=today_new, gam=gam, skills=skills,
                           games=cards, challenge=challenge,
                           recent_games=recent_games(uid, 3),
                           path_levels=path_levels, path_current=path_current,
                           review_today=review_today,
                           onboarding_done=bool(onb and onb.completed_at))


@main_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    uid = current_user.id
    profile_form = ProfileForm(username=current_user.username)
    pwd_form = ChangePasswordForm()

    # 同页存在两个表单，用隐藏字段区分提交意图
    if request.method == "POST":
        kind = (request.form.get("form_kind") or "").strip()
        if kind == "profile" and profile_form.validate_on_submit():
            current_user.username = (profile_form.username.data or "").strip()
            db.session.commit()
            flash("用户名已更新 ✅", "success")
            return redirect(url_for("main.profile"))
        if kind == "password" and pwd_form.validate_on_submit():
            if not current_user.check_password(pwd_form.current_password.data):
                flash("当前密码不正确 ❌", "error")
            else:
                current_user.set_password(pwd_form.new_password.data)
                db.session.commit()
                flash("密码修改成功，下次登录请使用新密码 🔐", "success")
                return redirect(url_for("main.profile"))

    stats = dashboard_stats(uid)
    stats["streak"] = learning_streak(uid)
    joined_days = (today_start().date() - current_user.created_at.date()).days + 1

    gam = overview(uid)
    recent = (StudyRecord.query.filter_by(user_id=uid, action="answer")
              .order_by(StudyRecord.created_at.desc()).limit(8).all())
    return render_template("profile.html", form=profile_form, pwd_form=pwd_form,
                           stats=stats, recent=recent, joined_days=joined_days,
                           badges=unlocked_badges(stats), gam=gam,
                           skills=_skill_cards(uid))


@main_bp.route("/ai-tutor")
@login_required
def ai_tutor():
    """AI Tutor 入口页（规格 §14）。

    读取用户的 Level / 学习路径位置 / 薄弱项，生成**真实画像**与针对性建议。

    诚实原则：当前未接入大模型，页面如实展示「基于你的学习数据自动生成的画像」，
    并给出规则化的复习建议，不假装有 AI 在跟你对话。
    """
    import json
    from collections import Counter

    from models import UnitTestAttempt, UserOnboarding
    from path_service import level_progress
    from review_service import due_count_by_kind

    uid = current_user.id

    onb = UserOnboarding.query.get(uid)
    levels, current = level_progress(uid)

    # 薄弱项：聚合全部单元测验的 weak_areas（按出现次数排序）
    SKILL_LABELS = {
        "vocabulary": ("Vocabulary", "词汇"),
        "listening": ("Listening", "听力"),
        "reading": ("Reading", "阅读"),
        "grammar": ("Grammar", "语法"),
        "speaking": ("Speaking", "口语"),
        "writing": ("Writing", "写作"),
        "review": ("Spaced review", "抗遗忘复习"),
    }
    attempts = UnitTestAttempt.query.filter_by(user_id=uid).all()
    weak_counter: Counter = Counter()
    for a in attempts:
        try:
            ws = json.loads(a.weak_areas or "[]")
        except (TypeError, ValueError):
            ws = []
        for w in ws:
            weak_counter[w] += 1
    weak_areas = [{
        "key": k, "count": c,
        "label_en": SKILL_LABELS.get(k, (k, k))[0],
        "label_zh": SKILL_LABELS.get(k, (k, k))[1],
    } for k, c in weak_counter.most_common()]

    review_counts = due_count_by_kind(uid)
    review_total = sum(review_counts.values())

    level_now = (onb.current_level if onb and onb.current_level
                 else (current["code"] if current else "A1"))

    # 推荐优先方向：先攻薄弱项，其次复习队列，否则回到词汇
    if weak_areas:
        focus = weak_areas[0]["key"]
    elif review_total:
        focus = "review"
    else:
        focus = "vocabulary"

    if focus == "vocabulary":
        focus_url = url_for("words.daily")
    elif focus == "review":
        focus_url = url_for("main.review")
    else:
        focus_url = url_for("learn.skill", key=focus)

    focus_label = SKILL_LABELS.get(focus, (focus, focus))

    profile = {
        "level": level_now,
        "goal": (onb.goal if onb else None),
        "age_group": (onb.age_group if onb else None),
        "onboarded": bool(onb and onb.completed_at),
        "path_current": current,
        "levels": levels,
        "weak_areas": weak_areas,
        "review_counts": review_counts,
        "review_total": review_total,
        "attempts": len(attempts),
        "focus": focus,
        "focus_label_en": focus_label[0],
        "focus_label_zh": focus_label[1],
        "focus_url": focus_url,
    }
    return render_template("ai_tutor.html", gam=overview(uid), profile=profile)


@main_bp.route("/healthz")
def healthz():
    """健康检查：Docker / Nginx / 监控探针使用。"""
    try:
        total_words = Word.query.count()
        users = db.session.query(func.count(User.id)).scalar() or 0
        return jsonify(status="ok", words=total_words, users=users)
    except Exception as exc:  # pragma: no cover
        return jsonify(status="error", detail=str(exc)), 500


# ============================================================ V5 学习平台路由
@main_bp.route("/onboarding", methods=["GET", "POST"])
@login_required
def onboarding():
    """首次进入：年龄 / 教育 / 当前水平 / 学习目标 → 个性化路径。"""
    from models import AGE_GROUPS, LEARNING_GOALS, UserOnboarding
    levels = ["Pre-A1", "A1", "A2", "B1", "B2", "C1", "C2"]
    uid = current_user.id
    current = UserOnboarding.query.get(uid)
    if request.method == "POST":
        age_group = (request.form.get("age_group") or "adult").strip()
        education = (request.form.get("education") or "adult").strip()
        current_level = (request.form.get("current_level") or "A1").strip()
        goal = (request.form.get("goal") or "daily").strip()
        if not current:
            current = UserOnboarding(user_id=uid)
            db.session.add(current)
        current.age_group = age_group
        current.education = education
        current.current_level = current_level
        current.goal = goal
        from models import utcnow
        current.completed_at = utcnow()
        db.session.commit()
        return redirect(url_for("main.learning_path"))
    return render_template("onboarding.html", age_groups=AGE_GROUPS,
                           goals=LEARNING_GOALS, levels=levels, current=current)


@main_bp.route("/courses")
@login_required
def courses():
    from models import Course, LearningLanguage
    langs = LearningLanguage.query.order_by(LearningLanguage.sort).all()
    by_lang = {}
    for l in langs:
        by_lang[l.code] = (l, Course.query.filter_by(language_code=l.code).order_by(Course.sort).all())
    return render_template("courses.html", by_lang=by_lang)


@main_bp.route("/learn/<lang_code>")
@login_required
def learn_language(lang_code: str):
    from models import Course, LearningLanguage, Unit
    # 技能 key 已迁到 /learn/skill/<key>，这里做兼容跳转，避免旧书签 404
    SKILL_KEYS = {"vocabulary", "listening", "reading", "grammar", "speaking", "writing", "hub"}
    if lang_code in SKILL_KEYS:
        return redirect(url_for("learn.skill", key=lang_code))
    LearningLanguage.query.filter_by(code=lang_code).first_or_404()
    course = Course.query.filter_by(language_code=lang_code).order_by(Course.sort).first()
    if not course:
        return redirect(url_for("main.courses"))
    unit = Unit.query.filter_by(course_id=course.id).order_by(Unit.no).first()
    if unit:
        return redirect(url_for("main.unit_view", unit_id=unit.id))
    return redirect(url_for("main.course_view", course_id=course.id))


@main_bp.route("/course/<int:course_id>")
@login_required
def course_view(course_id: int):
    from models import Course, Unit
    course = Course.query.get_or_404(course_id)
    units = Unit.query.filter_by(course_id=course.id).order_by(Unit.no).all()
    return render_template("course.html", course=course, units=units)


@main_bp.route("/unit/<int:unit_id>")
@login_required
def unit_view(unit_id: int):
    from models import Course, Lesson, Unit
    unit = Unit.query.get_or_404(unit_id)
    course = Course.query.get(unit.course_id)
    lessons = Lesson.query.filter_by(unit_id=unit.id).order_by(Lesson.no).all()
    return render_template("unit.html", unit=unit, course=course, lessons=lessons)


@main_bp.route("/unit/<int:unit_id>/test", methods=["GET", "POST"])
@login_required
def unit_test(unit_id: int):
    """单元测验（规格 §5）：做题 → 判分 → Mastered 解锁下一单元 / Weak Area 针对性练习。

    GET  渲染题目；POST 判分并展示 ``score / accuracy / time / mistakes / weak_areas``。
    达标（>= pass_score）→ 本单元 Mastered 并解锁下一单元；
    未达标 → 列出薄弱项对应的内容，引导 Targeted Practice 后重测。
    """
    from datetime import datetime

    from forms import EmptyForm
    from models import Course, Unit
    import unit_test_service as uts

    unit = Unit.query.get_or_404(unit_id)
    course = Course.query.get(unit.course_id)
    quiz = uts.ensure_quiz(unit, uts._lang_of(unit))

    if request.method == "POST":
        started = None
        raw = (request.form.get("started_at") or "").strip()
        if raw:
            try:
                started = datetime.fromisoformat(raw)
            except (TypeError, ValueError):
                started = None
        attempt = uts.grade(current_user.id, unit, request.form, started_at=started)
        result = uts.attempt_view(attempt)
        practice = [] if attempt.passed else uts.weak_area_items(current_user.id, unit)
        return render_template(
            "unit_test.html", unit=unit, course=course, quiz=quiz,
            questions=uts.quiz_view(quiz), state=uts.unit_state(current_user.id, unit),
            result=result, practice=practice, show_result=True,
            next_unit=uts._next_unit(unit), form=EmptyForm())

    last = uts.latest_attempt(current_user.id, unit.id)
    return render_template(
        "unit_test.html", unit=unit, course=course, quiz=quiz,
        questions=uts.quiz_view(quiz), state=uts.unit_state(current_user.id, unit),
        result=(uts.attempt_view(last) if last else None), practice=[],
        show_result=False, next_unit=uts._next_unit(unit), form=EmptyForm(),
        started_at=datetime.utcnow().isoformat(timespec="seconds"))


@main_bp.route("/lesson/<int:lesson_id>")
@login_required
def lesson_view(lesson_id: int):
    from models import ContentItem, Lesson, Unit
    lesson = Lesson.query.get_or_404(lesson_id)
    unit = Unit.query.get(lesson.unit_id)
    items = ContentItem.query.filter_by(lesson_id=lesson.id).all()

    # V5.5：为「Quiz」步骤生成四选一自测（正确项 + 同课干扰项），
    # 选项位置按题号轮转，避免正确项永远排第一。
    quiz_pairs = []
    n = len(items)
    if n >= 2:
        for idx, it in enumerate(items):
            correct_en = it.meaning_en or it.meaning_cn
            correct_zh = it.meaning_cn or it.meaning_en
            distractors = []
            for j in range(1, n):
                d = items[(idx + j) % n]
                de = d.meaning_en or d.meaning_cn
                dz = d.meaning_cn or d.meaning_en
                if (de or dz) and (de != correct_en or dz != correct_zh):
                    distractors.append({"en": de, "zh": dz})
                if len(distractors) >= 3:
                    break
            opts = [{"en": correct_en, "zh": correct_zh, "correct": True}] + [
                {"en": d["en"], "zh": d["zh"], "correct": False} for d in distractors
            ]
            pos = idx % len(opts) if opts else 0
            opts = opts[pos:] + opts[:pos]
            quiz_pairs.append({
                "id": it.id, "surface": it.surface, "phonetic": it.phonetic,
                "options": opts,
            })

    return render_template("lesson.html", lesson=lesson, unit=unit, items=items,
                           quiz_pairs=quiz_pairs)


@main_bp.route("/lesson/<int:lesson_id>/complete", methods=["POST"])
@login_required
def lesson_complete(lesson_id: int):
    """交互完成一节课：发 XP + 把内容加入抗遗忘队列。"""
    from gamification import add_xp
    from models import ContentItem, Lesson
    from review_service import ensure_review
    lesson = Lesson.query.get_or_404(lesson_id)
    uid = current_user.id
    items = ContentItem.query.filter_by(lesson_id=lesson.id).all()
    for it in items:
        ensure_review(uid, it.id)
    xp = max(10, len(items) * 2)
    info = add_xp(uid, xp, skill=lesson.kind, items=len(items))
    return {"ok": True, "xp": xp, "level": info.get("level"),
            "leveled_up": info.get("leveled_up", False)}


@main_bp.route("/review")
@login_required
def review():
    from review_service import due_count_by_kind, due_items
    uid = current_user.id
    counts = due_count_by_kind(uid)
    items = due_items(uid, limit=20)
    total = sum(counts.values())
    return render_template("review.html", counts=counts, items=items, total=total)


@main_bp.route("/review/answer", methods=["POST"])
@login_required
def review_answer():
    from review_service import record_result
    cid = request.form.get("content_id", type=int)
    correct = (request.form.get("correct") or "0") == "1"
    if cid:
        record_result(current_user.id, cid, correct)
    return redirect(request.referrer or url_for("main.review"))
