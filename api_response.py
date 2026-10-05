"""REST API 统一响应结构。

未来 Web / 微信小程序 / Android / macOS 都调用同一套 API，
因此返回结构必须**固定**，让客户端可以无脑解析：

成功::

    {"ok": true, "data": {...}, "error": null}

失败::

    {"ok": false, "data": null, "error": {"code": "unauthorized", "message": "请先登录"}}

约定
----
* HTTP 状态码仍然使用标准语义（401/403/404/422/429/500）
* ``ok`` 与 HTTP 状态保持一致，客户端二选一判断即可
* 业务数据一律放在 ``data`` 里，不额外平铺字段 —— 方便未来加分页元信息
"""
from __future__ import annotations

from typing import Any

from flask import jsonify

API_VERSION = "v1"


def ok(data: Any = None, status: int = 200, meta: dict | None = None):
    body: dict[str, Any] = {"ok": True, "data": data, "error": None}
    if meta:
        body["meta"] = meta
    return jsonify(body), status


def fail(message: str, code: str = "error", status: int = 400, details: Any = None):
    body = {
        "ok": False,
        "data": None,
        "error": {"code": code, "message": message, "details": details},
    }
    return jsonify(body), status


def paged(items: list, page: int, pages: int, total: int, extra: dict | None = None):
    data = {"items": items, "page": page, "pages": pages, "total": total}
    if extra:
        data.update(extra)
    return ok(data)
