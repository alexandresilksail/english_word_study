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
            "url": (url_for("words.learn") if s["key"] == "vocabulary"
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
            "url": url_for("words.learn"), "cta_zh": "继续学习", "cta_en": "Continue"}


@main_bp.route("/")
def index():
    """未登录：产品落地页。已登录：直接进入学习主页。"""
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))
    total_words = Word.query.count()
    # 落地页展示每日一词（未登录也能听发音，降低注册门槛）
    daily = None
    if total_words:
        w = Word.query.order_by(Word.freq.desc()).first()
        daily = {"word": w.word, "phonetic": w.phonetic_uk, "meaning": w.meaning_cn,
                 "example_en": w.example_en, "example_cn": w.example_cn,
                 "audio": w.audio, "audio_url": audio_url(w.audio)}
    return render_template("index.html", total_words=total_words, daily=daily)


@main_bp.route("/set-lang")
def set_lang():
    """界面语言切换：zh / en / both。登录用户写账号，未登录写 Cookie。"""
    lang = (request.args.get("lang") or "").strip().lower()
    nxt = request.args.get("next") or request.referrer or url_for("main.index")
    if lang not in ("zh", "en", "both"):
        return redirect(nxt)
    resp = redirect(nxt)
    resp.set_cookie("ui_lang", lang, max_age=3600 * 24 * 365, samesite="Lax")
    if current_user.is_authenticated:
        current_user.preferred_lang = lang if lang in ("zh", "en") else None
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
    """AI Tutor 入口页。

    **明确不做假**：本阶段没有接入任何大模型，页面如实说明「规划中」，
    只展示未来会做什么。等真正接入后，把 ready 改成 True 即可展示真实入口。
    """
    return render_template("ai_tutor.html", gam=overview(current_user.id))


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
    # 与旧技能路由 /learn/<skill> 不冲突：技能 key 仍走原页面
    SKILL_KEYS = {"vocabulary", "listening", "reading", "grammar", "speaking", "writing", "hub"}
    if lang_code in SKILL_KEYS:
        from routes import learn as learn_mod
        return learn_mod.skill(lang_code)
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


@main_bp.route("/lesson/<int:lesson_id>")
@login_required
def lesson_view(lesson_id: int):
    from models import ContentItem, Lesson, Unit
    lesson = Lesson.query.get_or_404(lesson_id)
    unit = Unit.query.get(lesson.unit_id)
    items = ContentItem.query.filter_by(lesson_id=lesson.id).all()
    return render_template("lesson.html", lesson=lesson, unit=unit, items=items)


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
