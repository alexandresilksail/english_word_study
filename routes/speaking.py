"""V5.8 口语 / 听力 JSON API。

统一 JSON 约定与其它 /api/* 一致：成功 ``{"ok": true, "data": ...}``，
失败 ``{"ok": false, "error": {...}}``；未登录返回 **401 JSON**。

.. note:: 每日配额

   计数的端点只有 ``/score``、``/transcribe``、``/synthesize``（真正消耗算力的
   三件事），且**仅在成功执行后**计数。``/status`` 与 ``/listening`` 是纯查询，
   不占用额度（``listening`` 本来就是三档通用功能）。
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from api_response import fail, ok
from flask_login import current_user
from speaking import is_practice_mode, listening_items, score, status, synthesize, transcribe

speaking_bp = Blueprint("speaking", __name__, url_prefix="/api/speaking")


@speaking_bp.before_request
def _guard():
    """先判登录（401），再判套餐（403）—— 顺序不能反。"""
    if not (current_user and current_user.is_authenticated):
        return fail("请先登录", code="unauthorized", status=401)
    from entitlements import check
    verdict = check("speaking", current_user.id)
    if not verdict["allowed"]:
        return fail(verdict["hint"], code=verdict["code"] or "plan_required", status=403,
                    details={"plan": verdict["plan"],
                             "required": verdict["required"],
                             "quota": verdict["quota"]})
    return None


def _consume() -> dict:
    """成功执行后计数一次，返回配额快照。旁路失败不影响主结果。"""
    from entitlements import consume, quota_state
    try:
        consume(current_user.id, "speaking")
        return quota_state("speaking", current_user.id)
    except Exception:  # pragma: no cover - 计数是旁路
        return {}


@speaking_bp.route("/status", methods=["GET"])
def api_status():
    """能力状态 + 今日额度（给页面显示「还剩 N 次」）。查询不计数。"""
    from entitlements import quota_state
    payload = status()
    payload["quota"] = quota_state("speaking", current_user.id)
    return ok(payload)


@speaking_bp.route("/score", methods=["POST"])
def api_score():
    """把用户说/写的句子与参考句对比评分。

    body: ``{"text": "...", "reference": "...", "lang": "en"|"zh"}``
    """
    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    reference = (body.get("reference") or "").strip()
    lang = (body.get("lang") or "en").strip().lower()
    if lang not in ("en", "zh", "yue"):
        lang = "en"
    if not reference:
        return fail("缺少参考答案", code="bad_request", status=422)
    if not text:
        return fail("请输入你说的话", code="bad_request", status=422)
    payload = score(text, reference, lang)
    payload["quota"] = _consume()   # 只在成功路径计数
    return ok(payload)


@speaking_bp.route("/transcribe", methods=["POST"])
def api_transcribe():
    """上传录音做语音识别。

    未接 STT 引擎时返回 ``available=false``，前端据此切到手动输入，
    **不假装识别成功**。
    """
    lang = (request.form.get("lang") or request.args.get("lang") or "en").strip().lower()
    fileobj = request.files.get("audio")
    data = fileobj.read() if fileobj else b""
    result = transcribe(data, lang)
    # 与其它端点契约一致：坏输入返回顶层 ok:false（而不是 200 包一个内部失败）
    if not result.get("ok"):
        return fail(result.get("error") or result.get("note") or "识别失败",
                    code="bad_request", status=422)
    result["quota"] = _consume()
    return ok(result)


@speaking_bp.route("/synthesize", methods=["POST"])
def api_synthesize():
    """参考句朗读；未接 TTS 时返回 ``available=false`` + hint=browser-speech。"""
    body = request.get_json(silent=True) or {}
    text = (body.get("text") or "").strip()
    lang = (body.get("lang") or "en").strip().lower()
    if not text:
        return fail("文本为空", code="bad_request", status=422)
    payload = synthesize(text, lang)
    payload["quota"] = _consume()
    return ok(payload)


@speaking_bp.route("/listening", methods=["GET"])
def api_listening():
    """听力素材清单（复用站内既有 MP3，只返回真能播放的条目）。"""
    lang = (request.args.get("lang") or "en").strip().lower()
    limit = request.args.get("limit", "12")
    try:
        limit = max(1, min(50, int(limit)))
    except (TypeError, ValueError):
        limit = 12
    return ok({"items": listening_items(lang, limit),
               "practice_mode": is_practice_mode()})
