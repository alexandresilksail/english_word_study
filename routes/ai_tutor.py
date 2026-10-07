"""V5.7 AI Tutor JSON API。

所有端点返回统一结构（与 api_response 一致）：

* 成功 ``{"ok": true, "data": {...}}``
* 失败 ``{"ok": false, "error": {"code": ..., "message": ...}}``

鉴权与其它 /api/* 保持一致：未登录返回 **401 JSON**（不是 302 跳登录页），
便于前端区分处理。

.. note:: Mock 标注

   ``is_mock()`` 为真时，返回值里必定带 ``mock: true``。前端必须据此显示
   「本地规则模式」，**不得**让用户误以为这是大模型生成的回答。
"""
from __future__ import annotations

from flask import Blueprint, request

from ai_tutor import ACTIONS, is_mock, run, sanitize, status
from api_response import fail, ok
from flask_login import current_user

ai_tutor_bp = Blueprint("ai_tutor", __name__, url_prefix="/api/ai-tutor")


def _require_login():
    return current_user is not None and getattr(current_user, "is_authenticated", False)


@ai_tutor_bp.before_request
def _guard():
    """蓝图级鉴权 + 套餐校验。

    顺序很重要：先判登录（401），再判套餐（403）——
    未登录用户不该看到「请升级 PRO」这种与被踢原因不符的提示。
    """
    if not _require_login():
        return fail("请先登录", code="unauthorized", status=401)
    from entitlements import check
    verdict = check("ai_tutor", current_user.id)
    if not verdict["allowed"]:
        return fail(verdict["hint"], code="plan_required", status=403,
                    details={"plan": verdict["plan"],
                             "required": verdict["required"]})
    return None


@ai_tutor_bp.route("/status", methods=["GET"])
def api_status():
    """当前 AI 能力状态（是否接了真模型、可用能力清单）。"""
    st = status()
    return ok({"provider": st["provider"], "model": st["model"],
               "mock": is_mock(), "enabled": st["enabled"],
               "actions": list(ACTIONS)})


@ai_tutor_bp.route("/<action>", methods=["POST"])
def api_run(action: str):
    """执行一类 AI 能力。

    body: ``{"text": "...", "lang": "zh"|"en"}``
    """
    body = request.get_json(silent=True) or {}
    text = sanitize(body.get("text") or body.get("q") or "")
    lang = (body.get("lang") or "zh").strip().lower()
    if lang not in ("zh", "en"):
        lang = "zh"

    result = run(action, text, lang)
    if not result.get("ok"):
        return fail(result.get("error", "请求失败"),
                    code=result.get("code", "bad_request"), status=422)
    # 前端据此显示「本地规则模式」，保证不误导用户
    result["mock"] = bool(result.get("mock"))
    return ok(result)
