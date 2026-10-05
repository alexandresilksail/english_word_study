"""媒体服务层：音频 / 图片的**地址解析**。

为什么需要这一层
----------------
需求明确：音频文件将来不进 GitHub、不进 Docker 镜像、不进 SQLite。
正确做法是业务表里只存「逻辑标识」，真实下载地址由本模块解析：

    本地阶段   : /static/audio/word.mp3
    对象存储期 : https://cdn.example.com/audio/word.mp3

切换时只改 :func:`audio_url` 与环境变量（``MEDIA_BASE_URL`` / ``MEDIA_PROVIDER``），
业务代码与模板零改动 —— 这是「不为未来扩展埋坑」的关键。

同时提供 :func:`register_asset` 把资产登记进 media_assets 表，
未来批量迁移到 OSS/COS/S3 时可直接遍历这张表生成迁移清单。
"""
from __future__ import annotations

import os

from flask import current_app, url_for

# 支持的 provider（本阶段只真正实现 local）
PROVIDERS = ("local", "oss", "cos", "s3")


def media_base_url() -> str:
    """对象存储 / CDN 的根地址（未配置则为空 → 走本地静态文件）。"""
    return (current_app.config.get("MEDIA_BASE_URL")
            or os.environ.get("MEDIA_BASE_URL") or "").rstrip("/")


def provider() -> str:
    return (current_app.config.get("MEDIA_PROVIDER")
            or os.environ.get("MEDIA_PROVIDER") or "local").strip().lower()


def audio_url(filename: str | None, kind: str = "word_audio") -> str:
    """把音频文件名解析成可播放地址。

    - 未配置 MEDIA_BASE_URL：返回站内静态地址（当前生产行为，保持不变）
    - 已配置：返回 CDN / 对象存储地址（未来切过去时无需改任何模板）
    """
    name = (filename or "").strip()
    if not name:
        return ""
    if name.startswith(("http://", "https://", "/")):
        return name

    base = media_base_url()
    if base:
        # 目录按 kind 分桶，便于对象存储里做生命周期与缓存策略
        return f"{base}/{kind}s/{name}"
    try:
        return url_for("static", filename=f"audio/{name}")
    except RuntimeError:  # 无请求上下文（CLI / 测试）
        return f"/static/audio/{name}"


def cover_url(key: str | None) -> str:
    """封面图地址（Podcast / 技能卡用），解析逻辑与音频一致。"""
    if not key:
        return ""
    if key.startswith(("http://", "https://", "/")):
        return key
    base = media_base_url()
    if base:
        return f"{base}/covers/{key}"
    try:
        return url_for("static", filename=f"img/{key}")
    except RuntimeError:
        return f"/static/img/{key}"


def register_asset(kind: str, key: str, **kwargs) -> int | None:
    """登记一条媒体资产，返回 asset_id。

    失败时返回 None（登记只是为将来迁移做准备，不能阻断业务流程）。
    """
    try:
        from extensions import db
        from models import MediaAsset, utcnow

        row = MediaAsset.query.filter_by(kind=kind, key=key).first()
        if row:
            return row.id
        row = MediaAsset(kind=kind, key=key, provider=provider(),
                         mime=kwargs.get("mime"), bytes=kwargs.get("bytes"),
                         duration_sec=kwargs.get("duration_sec"),
                         checksum=kwargs.get("checksum"), created_at=utcnow())
        db.session.add(row)
        db.session.commit()
        return row.id
    except Exception:  # pragma: no cover - 登记失败不影响主流程
        try:
            from extensions import db
            db.session.rollback()
        except Exception:
            pass
        return None
