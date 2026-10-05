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

    return render_template("dashboard.html", stats=stats, trend=trend,
                           recent_tests=recent_tests, badges=unlocked_badges(stats),
                           today_new=today_new, gam=gam, skills=skills,
                           games=cards, challenge=challenge,
                           recent_games=recent_games(uid, 3))


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
