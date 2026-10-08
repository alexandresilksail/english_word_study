"""V6.0.1 AI Assessment 路由：生成评估 → 提交判分 → 保存画像 → 展示结果。

所有页面需登录。结果页带 ``aid``，仅本人可见（防 IDOR：非本人 403）。
"""
from __future__ import annotations

import json

from flask import (Blueprint, abort, redirect, render_template, request, url_for)
from flask_login import current_user, login_required

from assessment import generate_assessment, grade_assessment, save_assessment
from models import Assessment, LearnerProfile, SKILL_KEYS

assessment_bp = Blueprint("assessment", __name__, url_prefix="/assessment")


@assessment_bp.route("/", methods=["GET", "POST"])
@login_required
def index():
    if request.method == "POST":
        raw = request.form.get("questions") or "[]"
        try:
            questions = json.loads(raw)
        except (ValueError, TypeError):
            questions = []
        submitted = {}
        for k, v in request.form.items():
            if k == "questions" or not k.startswith("q"):
                continue
            if v in (None, ""):
                continue
            try:
                submitted[k] = int(v)
            except (TypeError, ValueError):
                submitted[k] = v
        result = grade_assessment(questions, submitted)
        native = request.form.get("native_language", "zh")
        target = request.form.get("target_language", "en")
        pref = request.form.get("preferences", "")
        a = save_assessment(current_user.id, result, native, target, pref)
        return redirect(url_for("assessment.result", aid=a.id))

    questions = generate_assessment(current_user.id)
    prof = LearnerProfile.query.filter_by(user_id=current_user.id).first()
    return render_template("assessment.html", questions=questions, profile=prof,
                           skills=SKILL_KEYS)


@assessment_bp.route("/result/<int:aid>", methods=["GET"])
@login_required
def result(aid: int):
    a = Assessment.query.get_or_404(aid)
    if a.user_id != current_user.id:       # IDOR 防护：非本人不可看他人评估
        abort(403)
    levels = {s: getattr(a, f"{s}_level") for s in SKILL_KEYS}
    prof = LearnerProfile.query.filter_by(user_id=a.user_id).first()
    weak = [w for w in (prof.weak_areas or "").split(",") if w] if prof else []
    return render_template("assessment_result.html", a=a, skills=SKILL_KEYS,
                           levels=levels, weak=weak)
