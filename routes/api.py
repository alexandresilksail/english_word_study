"""REST API v1 —— 为 Web / 微信小程序 / Android / macOS 预留的统一接口层。

架构约定
--------
* 路由只做「取参数 → 调 Service → 包响应」，业务逻辑一律在 services / gamification /
  game_service / media_service 中，方便多端复用与单测。
* 返回结构统一由 :mod:`api_response` 封装（``{ok, data, error}``）。
* 认证：浏览器沿用 Flask-Login 的 session；未来客户端可换 token，
  只需替换 :func:`_require_user` 的实现，业务代码不动。
* CSRF：写操作沿用 Flask-WTF 的 CSRFProtect（与站内一致）。
"""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request
from flask_login import current_user

from api_response import fail, ok, paged
from gamification import (game_stats, overview, record_game, set_goal, skill_stats,
                          xp_for_game)
from game_service import GAME_REGISTRY, start_game, submit_game
from media_service import audio_url
from models import GAMES, SKILLS, Word
from services import build_word_view, dashboard_stats, daily_trend, learning_streak

api_bp = Blueprint("api", __name__, url_prefix="/api/v1")


def _json() -> dict:
    """取请求体（容错：空 body / 非 JSON 都返回 {}）。"""
    return request.get_json(silent=True) or {}


def _require_user():
    if not current_user.is_authenticated:
        return fail("请先登录", code="unauthorized", status=401)
    return None


def api_login_required(fn):
    """API 专用登录保护：未登录返回 401 JSON，而非 302 跳 HTML 登录页。

    浏览器走 session 登录后仍可正常调用；小程序 / Android 等客户端拿到的是
    标准 ``{ok:false, error:{code:"unauthorized"}}``，便于按 code 做统一处理。
    """
    from functools import wraps

    @wraps(fn)
    def wrapper(*args, **kwargs):
        denied = _require_user()
        if denied is not None:
            return denied
        return fn(*args, **kwargs)
    return wrapper


# --------------------------------------------------------------------------
# 元信息 / 健康
# --------------------------------------------------------------------------
@api_bp.route("/health")
def health():
    return ok({"status": "ok", "version": current_app.config.get("APP_VERSION", "3.0.0")})


@api_bp.route("/meta/skills")
def meta_skills():
    """六项技能元信息（前端与客户端共用一份，避免各端硬编码）。"""
    return ok({"items": SKILLS})


@api_bp.route("/meta/games")
def meta_games():
    return ok({"items": GAMES})


# --------------------------------------------------------------------------
# 当前用户
# --------------------------------------------------------------------------
@api_bp.get("/me/overview")
@api_login_required
def me_overview():
    """Dashboard 所需的全部数据（Web 端页面与未来客户端共用）。"""
    uid = current_user.id
    data = overview(uid)
    data["stats"] = dashboard_stats(uid)
    data["streak_days"] = learning_streak(uid)
    data["skills"] = [
        {"skill": s.skill, "xp": s.xp, "items": s.items, "minutes": s.minutes}
        for s in skill_stats(uid).values()
    ]
    return ok(data)


@api_bp.get("/me/progress")
@api_login_required
def me_progress():
    uid = current_user.id
    return ok({
        "stats": dashboard_stats(uid),
        "trend": daily_trend(uid, 14),
        "streak": learning_streak(uid),
        "gamification": overview(uid),
    })


@api_bp.post("/me/goal")
@api_login_required
def me_goal():
    goal = (_json().get("goal") or 20)
    try:
        goal = int(goal)
    except (TypeError, ValueError):
        return fail("目标必须是数字", code="bad_request")
    return ok({"goal": set_goal(current_user.id, goal)})


# --------------------------------------------------------------------------
# 单词
# --------------------------------------------------------------------------
@api_bp.get("/words")
def words():
    """单词查询（未登录也可查，个性化字段留空）。

    GET /api/v1/words?q=ability&page=1&size=20
    """
    q = (request.args.get("q") or "").strip()
    page = max(1, request.args.get("page", 1, type=int) or 1)
    size = min(50, max(1, request.args.get("size", 20, type=int) or 20))

    query = Word.query
    if q:
        like = f"%{q.lower()}%"
        query = query.filter(Word.word.like(like) | Word.meaning_cn.like(like))
    total = query.count()
    rows = (query.order_by(Word.freq.desc(), Word.id)
            .offset((page - 1) * size).limit(size).all())

    uid = current_user.id if current_user.is_authenticated else None
    items = []
    for w in rows:
        view = build_word_view(w, uid) if uid else {
            "id": w.id, "word": w.word, "phonetic_uk": w.phonetic_uk,
            "pos": w.pos, "meaning_cn": w.meaning_cn, "example_en": w.example_en,
            "example_cn": w.example_cn, "level": w.level, "status": "new",
        }
        view["audio_url"] = audio_url(w.audio)
        items.append(view)
    return paged(items, page, max(1, (total + size - 1) // size), total)


@api_bp.get("/words/<int:word_id>")
def word_detail(word_id: int):
    w = Word.query.get(word_id)
    if not w:
        return fail("单词不存在", code="not_found", status=404)
    uid = current_user.id if current_user.is_authenticated else None
    view = build_word_view(w, uid) if uid else {
        "id": w.id, "word": w.word, "phonetic_uk": w.phonetic_uk, "pos": w.pos,
        "meaning_cn": w.meaning_cn, "example_en": w.example_en,
        "example_cn": w.example_cn, "status": "new",
    }
    view["audio_url"] = audio_url(w.audio)
    return ok(view)


@api_bp.get("/words/random")
def words_random():
    """随机抽词（首页 / 每日挑战用）。"""
    size = min(20, max(1, request.args.get("size", 5, type=int) or 5))
    rows = Word.query.order_by(Word.freq.desc()).limit(200).all()
    import random
    picked = random.sample(rows, min(size, len(rows))) if rows else []
    return ok({"items": [{"id": w.id, "word": w.word, "meaning": w.meaning_cn,
                          "phonetic": w.phonetic_uk,
                          "audio_url": audio_url(w.audio)} for w in picked]})


# --------------------------------------------------------------------------
# 小游戏
# --------------------------------------------------------------------------
@api_bp.post("/games/<key>/start")
@api_login_required
def game_start(key: str):
    """开局：生成题目并把**判题状态**写进 session（与 Quiz 同一套机制）。"""
    if key not in GAME_REGISTRY:
        return fail("未知游戏", code="not_found", status=404)
    body = _json()
    res = start_game(key, body.get("seed"))
    if not res.get("ok"):
        return fail(res.get("error") or "开局失败", code="game_error", status=500)

    from flask import session
    session["game"] = res["state"]
    session.modified = True
    view = dict(res["view"])
    return ok(view)


@api_bp.post("/games/<key>/submit")
@api_login_required
def game_submit(key: str):
    """提交：用保存的状态判分，绝不重新生成题目。"""
    if key not in GAME_REGISTRY:
        return fail("未知游戏", code="not_found", status=404)

    from flask import session
    state = session.get("game") or {}
    if state.get("game") != key:
        return fail("游戏状态已失效，请重新开始", code="state_lost", status=409)

    body = _json()
    res = submit_game(key, state, body)
    if not res.get("ok"):
        return fail(res.get("error") or "判分失败", code="game_error", status=400)

    uid = current_user.id
    xp = xp_for_game(key, res)
    saved = record_game(
        uid, key, score=res.get("score", 0), correct=res.get("correct", 0),
        total=res.get("total", 0) or 1, max_combo=res.get("max_combo", 0),
        duration_sec=int(body.get("elapsed_sec") or 0), xp=xp,
        skill="listening" if key == "listening_challenge" else "vocabulary",
    )
    session.pop("game", None)
    session.modified = True
    return ok({**res, **saved})


@api_bp.get("/games/stats")
@api_login_required
def games_stats():
    uid = current_user.id
    stats = game_stats(uid)
    items = []
    for g in GAMES:
        s = stats.get(g["key"])
        items.append({**g, "best": s.best_score if s else 0,
                      "plays": s.plays if s else 0,
                      "accuracy": s.accuracy if s else 0,
                      "best_combo": s.best_combo if s else 0,
                      "total_xp": s.total_xp if s else 0})
    return ok({"items": items})


# --------------------------------------------------------------------------
# Podcast（架构预留：当前无内容，返回空列表而不是 404，方便客户端联调）
# --------------------------------------------------------------------------
@api_bp.get("/podcast/channels")
def podcast_channels():
    from models import PodcastChannel
    rows = PodcastChannel.query.filter_by(status="published") \
        if hasattr(PodcastChannel, "status") else PodcastChannel.query
    try:
        rows = rows.order_by(PodcastChannel.id).all()
    except Exception:
        rows = []
    return ok({"items": [{"id": c.id, "slug": c.slug, "title": c.title,
                          "subtitle": c.subtitle, "level": c.level,
                          "language": c.language,
                          "episodes": c.episodes.count()} for c in rows]})


@api_bp.get("/podcast/episodes")
def podcast_episodes():
    from models import PodcastEpisode
    rows = PodcastEpisode.query.order_by(PodcastEpisode.id.desc()).limit(50).all()
    return ok({"items": [{"id": e.id, "slug": e.slug, "title": e.title,
                          "summary": e.summary, "level": e.level,
                          "duration_sec": e.duration_sec,
                          "audio_url": audio_url(_asset_key(e.media_asset_id),
                                                 kind="podcast_audio")}
                         for e in rows]})


def _asset_key(asset_id: int | None) -> str:
    if not asset_id:
        return ""
    from models import MediaAsset
    a = MediaAsset.query.get(asset_id)
    return (a.key or "") if a else ""
