"""账户设置（Settings）：界面语言偏好、每日目标、邮件通知等。

§24 要求的独立 /settings 页面。语言偏好写入 user.preferred_lang（登录后覆盖 Cookie），
每日目标写入 user_stat.daily_goal。所有提示均走 flash_l，保证 English 版面零中文。
"""
from __future__ import annotations

from flask import Blueprint, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from extensions import db
from forms import EmptyForm
from localization import flash_l, pick
from models import UserStat

settings_bp = Blueprint("settings", __name__, url_prefix="/settings")


LANGS = [("zh", "中文", "Chinese"), ("en", "English", "English"), ("yue", "粵語", "Cantonese")]
GOALS = [10, 20, 30, 50, 100]


@settings_bp.route("", methods=["GET", "POST"])
@settings_bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    stat = UserStat.query.get(current_user.id)
    if request.method == "POST":
        lang = (request.form.get("preferred_lang") or "").strip()
        if lang in ("zh", "en", "yue", "both"):
            current_user.preferred_lang = lang
        try:
            goal = int(request.form.get("daily_goal") or 0)
        except (TypeError, ValueError):
            goal = 0
        goal = max(5, min(goal, 500))
        if stat:
            stat.daily_goal = goal
        else:
            stat = UserStat(user_id=current_user.id, daily_goal=goal)
            db.session.add(stat)
        try:
            notify = bool(request.form.get("notify_email"))
        except Exception:
            notify = True
        current_user.notify_email = notify
        db.session.commit()
        flash_l("设置已保存。", "Settings saved.", "success")
        return redirect(url_for("settings.index"))

    return render_template("settings.html", form=EmptyForm(),
                           langs=LANGS, goals=GOALS,
                           preferred=current_user.preferred_lang or "zh",
                           daily_goal=(stat.daily_goal if stat else 20),
                           notify=bool(getattr(current_user, "notify_email", True)),
                           title_en="Settings", title_zh="账户设置")
