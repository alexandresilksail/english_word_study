"""微信小程序 JSON API（``/api/wx/v1``）。

与既有 ``routes/api.py``（``/api/v1``，浏览器 session 认证）**完全独立**：
* 本蓝图只返回 JSON，绝不渲染模板；
* 认证用 :mod:`wx_auth` 的标准 JWT Bearer Token，不碰 Flask-Login session；
* 业务逻辑一律复用 :mod:`services` / :mod:`gamification` / :mod:`media_service`，
  不重复实现学习规则。

接口清单（请求参数 / 响应结构 / 错误码见 docs/api-wx-v1.md）
----------------------------------------------------------
认证
    POST /auth/wx-login          微信 code 换令牌
    POST /auth/refresh           refresh 换新 access（旋转）
    POST /auth/logout            撤销当前令牌
    GET  /auth/me                当前用户信息
    POST /auth/send-code         发送绑定用邮箱验证码
    POST /auth/bind-email        证明邮箱控制权后绑定已有账户

学习
    GET  /home                   首页学习概览
    GET  /words                  单词浏览 / 搜索（分页）
    GET  /words/<id>             单词详情
    POST /words/<id>/favorite    收藏 / 取消收藏
    GET  /favorites              收藏列表
    GET  /wrong                  错题本
    POST /wrong/<id>/remove      移出错题本
    POST /quiz/start             开始测试
    GET  /quiz/item              取第 i 题
    POST /quiz/answer            判题
    POST /quiz/finish            交卷
    GET  /progress               学习进度统计

数据隔离纪律
------------
所有私有数据查询的 user_id 一律来自 **服务端验签后的 Access Token**
（``g.wx_user.id``），绝不读取客户端提交的 ``user_id`` / ``uid`` 字段。
"""
from __future__ import annotations

import logging
import time
from datetime import datetime

from flask import Blueprint, current_app, g, jsonify, request
from itsdangerous import BadSignature

from api_response import fail, ok, paged
from email_code import issue_code, verify_code
from extensions import db
from gamification import overview, set_goal, skill_stats
from media_service import audio_url
from models import (SKILLS, Favorite, TestRecord, User, UserStat, UserWordProgress,
                    Word, WrongAnswer)
from services import (build_question, build_word_view, daily_trend,
                      dashboard_stats, learning_streak, record_answer,
                      touch_word, _pick_word_ids)
from wx_auth import (AuthError, code2session, find_or_create_wx_user,
                     issue_tokens, refresh_access_token, revoke_token,
                     wx_login_optional, wx_login_required)
from wx_quiz_state import QuizStateError, build_item, create_state, expected_text, load_state
from services import judge

logger = logging.getLogger(__name__)

wx_api_bp = Blueprint("wx_api", __name__, url_prefix="/api/wx/v1")

MAX_PAGE_SIZE = 50
# wx-login 的 IP 维度简易限流（内存计数，进程内有效；多 worker 各自计数，
# 与 utils.ratelimit 的 IP 闸门同一思路，但独立实现避免跨蓝图耦合）
_LOGIN_WINDOW_SEC = 60
_LOGIN_MAX_PER_WINDOW = 30
_login_hits: dict[str, list[float]] = {}


# --------------------------------------------------------------------------
# 通用辅助
# --------------------------------------------------------------------------
def _json() -> dict:
    return request.get_json(silent=True) or {}


def _client_ip() -> str:
    return (request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
            or request.remote_addr or "")[:64]


def _too_many_logins() -> bool:
    now = time.time()
    ip = _client_ip() or "unknown"
    hits = [t for t in _login_hits.get(ip, []) if now - t < _LOGIN_WINDOW_SEC]
    if len(hits) >= _LOGIN_MAX_PER_WINDOW:
        _login_hits[ip] = hits
        return True
    hits.append(now)
    _login_hits[ip] = hits
    return False


def _err(exc: Exception, default: str = "请求处理失败"):
    logger.warning("[wx_api] %s: %s", type(exc).__name__, exc)
    return fail(default, code="server_error", status=500)


@wx_api_bp.errorhandler(AuthError)
def _auth_error(exc: AuthError):
    status = 401
    if exc.code == "user_not_found":
        status = 404
    return fail(exc.message, code=exc.code, status=status)


@wx_api_bp.errorhandler(QuizStateError)
def _quiz_state_error(exc: QuizStateError):
    return fail(exc.message, code=exc.code, status=400)


def _user_payload(user: User) -> dict:
    """当前用户信息（小程序「个人中心」用）。"""
    stat = UserStat.query.filter_by(user_id=user.id).first()
    return {
        "id": user.id,
        "username": user.username,
        "email": "" if (user.email or "").endswith("@wx.local") else user.email,
        "email_bound": not (user.email or "").endswith("@wx.local"),
        "is_admin": bool(user.is_admin),
        "level": stat.level if stat else 1,
        "xp": stat.xp if stat else 0,
        "daily_goal": stat.daily_goal if stat else 20,
        "today_done": stat.today_done if stat else 0,
        "streak_days": stat.streak_days if stat else 0,
        "preferred_lang": user.preferred_lang or "",
        "created_at": user.created_at.isoformat() + "Z" if user.created_at else "",
    }


# ==========================================================================
# 认证
# ==========================================================================
@wx_api_bp.post("/auth/wx-login")
def wx_login():
    """微信登录：code → openid → 令牌。

    请求 ``{"code": "wx.login 拿到的 code"}``
    响应 ``{"tokens": {...}, "user": {...}, "is_new_account": bool, "need_bind": bool}``

    ``is_new_account`` 为 True 时客户端应引导用户补全昵称 / 绑定邮箱。
    ``need_bind`` 为 True 表示服务端关闭了自动注册，需走邮箱绑定流程。
    """
    if _too_many_logins():
        return fail("请求过于频繁，请稍后再试", code="rate_limited", status=429)

    code = (_json().get("code") or "").strip()
    if not code:
        return fail("缺少微信登录 code", code="bad_request")

    res = code2session(code)
    if not res.get("ok"):
        # errcode 40029/40163 等属客户端问题（code 无效/已用），提示重新登录
        code_str = "wx_error"
        errcode = res.get("errcode")
        if errcode in (40029, 40163, 40013):
            code_str = "wx_code_invalid"
        return fail(res.get("errmsg") or "微信登录失败", code=code_str, status=401)

    profile = _json()
    user, identity, created = find_or_create_wx_user(
        res["openid"],
        unionid=res.get("unionid", ""),
        nickname=(profile.get("nickname") or "")[:64],
        avatar_url=(profile.get("avatar_url") or "")[:512],
    )

    if user is None:
        return ok({
            "tokens": None,
            "user": None,
            "is_new_account": False,
            "need_bind": True,
        })

    tokens = issue_tokens(user.id)
    logger.info("[wx_api] 微信登录成功 user=%s new=%s", user.id, created)
    return ok({
        "tokens": tokens,
        "user": _user_payload(user),
        "is_new_account": created,
        "need_bind": False,
    })


@wx_api_bp.post("/auth/refresh")
def wx_refresh():
    """用 Refresh Token 换一对新令牌（旧 refresh 立即作废，防重放）。"""
    body = _json()
    token = (body.get("refresh_token") or "").strip()
    if not token:
        header = request.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            token = header[len("Bearer "):].strip()
    if not token:
        return fail("缺少 refresh_token", code="bad_request")
    try:
        tokens = refresh_access_token(token)
    except AuthError as exc:
        return fail(exc.message, code=exc.code, status=401)
    return ok({"tokens": tokens})


@wx_api_bp.post("/auth/logout")
@wx_login_required
def wx_logout():
    """登出：把当前 Access / Refresh 令牌加入撤销黑名单。

    Access 令牌的 claims 已由 ``wx_login_required`` 验签后放入 ``g.wx_claims``；
    Refresh 令牌由客户端在 body 里回传，这里仅解出 jti / exp 用于撤销
    （不解签：它可能已过期，而「把过期的令牌也登记撤销」正是为了立刻失效）。
    """
    import jwt as _jwt

    revoked = []
    access_claims = g.wx_claims or {}
    if access_claims.get("jti"):
        revoke_token(str(access_claims["jti"]), g.wx_user.id, "access",
                     datetime.utcfromtimestamp(int(access_claims["exp"])))
        revoked.append("access")

    refresh_token = (_json().get("refresh_token") or "").strip()
    if refresh_token:
        try:
            rc = _jwt.decode(refresh_token, options={"verify_signature": False})
            if rc.get("typ") == "refresh" and rc.get("jti"):
                revoke_token(str(rc["jti"]), g.wx_user.id, "refresh",
                             datetime.utcfromtimestamp(int(rc["exp"])))
                revoked.append("refresh")
        except Exception:  # noqa: BLE001  解析失败不影响 access 已撤销的事实
            logger.info("[wx_api] 登出时 refresh_token 解析失败，仅撤销 access")

    return ok({"revoked": revoked})


@wx_api_bp.get("/auth/me")
@wx_login_required
def wx_me():
    return ok({"user": _user_payload(g.wx_user)})


@wx_api_bp.post("/auth/send-code")
def wx_send_code():
    """发送绑定用邮箱验证码（复用 email_code 的限流 / 一次性 / TTL 机制）。

    请求 ``{"email": "..."}``
    """
    body = _json()
    email = (body.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return fail("请输入有效的邮箱地址", code="bad_request")
    purpose = "login"
    sent, message, dev_code = issue_code(email, purpose, ip=_client_ip())
    if not sent:
        return fail(message, code="rate_limited", status=429)
    payload = {"message": message}
    if dev_code:
        # 仅未配置 SMTP 时出现（本地自测），生产环境绝不下发
        payload["dev_code"] = dev_code
    return ok(payload)


@wx_api_bp.post("/auth/bind-email")
@wx_login_required
def wx_bind_email():
    """证明邮箱控制权后，把当前微信身份绑定到已有账户。

    两种证明方式（任选其一，都等价于「能控制该邮箱」）：
      A. ``{"email", "password"}``           —— 原账户的登录密码
      B. ``{"email", "code"}``               —— 发到该邮箱的一次性验证码

    **绝不**仅凭邮箱地址自动关联：邮箱谁都能填，必须验证控制权。

    若当前微信身份下是一个「首次登录自动建」的占位账户（邮箱为 @wx.local），
    绑定成功后会将该占位账户已产生的学习数据合并进目标账户，再切换关联。
    """
    body = _json()
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    code = (body.get("code") or "").strip()

    if not email or "@" not in email:
        return fail("请输入有效的邮箱地址", code="bad_request")
    if not password and not code:
        return fail("请提供原账户密码，或该邮箱收到的验证码", code="bad_request")

    target = User.query.filter_by(email=email).first()
    if target is None:
        # 统一提示，避免邮箱枚举
        return fail("邮箱或密码不正确", code="invalid_credentials", status=401)

    # ---- 验证对原账户的控制权 ----
    if code:
        ok_code, msg = verify_code(email, code, "login")
        if not ok_code:
            return fail(msg, code="invalid_code", status=401)
    else:
        if target.is_locked:
            return fail("账户已临时锁定，请稍后再试", code="locked", status=429)
        if not target.check_password(password):
            return fail("邮箱或密码不正确", code="invalid_credentials", status=401)

    if target.id == g.wx_user.id:
        return fail("该邮箱已绑定到当前账户", code="already_bound", status=409)

    # ---- 防止重复绑定 / 恶意抢占 ----
    from models import WeChatIdentity
    appid = (current_app.config.get("WX_APPID") or "").strip()
    existing = WeChatIdentity.query.filter_by(openid=_current_openid(), appid=appid).first()
    if existing is not None and existing.user_id == target.id:
        return fail("该微信已绑定此账户", code="already_bound", status=409)

    current_user_obj = g.wx_user
    placeholder = (current_user_obj.email or "").endswith("@wx.local")

    # 已绑定「真实账户」的微信不允许直接改绑到别的账户：
    # 那等同于任何拿到目标账户凭证的人都能抢走这枚微信身份。
    # 合法换绑必须先显式解绑（/auth/unbind，需再次验证原账户控制权）。
    if not placeholder:
        return fail("该微信已绑定其他账户，请先解绑后再换绑",
                    code="already_bound", status=409)

    try:
        if placeholder and existing is not None:
            _merge_accounts(target, current_user_obj)
        # 切换关联到目标账户（同一 openid 全局唯一）
        if existing is None:
            existing = WeChatIdentity(
                user_id=target.id, openid=_current_openid(), appid=appid,
                created_at=datetime.utcnow(),
            )
            db.session.add(existing)
        existing.user_id = target.id
        existing.updated_at = datetime.utcnow()
        db.session.commit()
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        return _err(exc, "绑定失败，请稍后重试")

    tokens = issue_tokens(target.id)
    logger.info("[wx_api] 微信绑定成功 user=%s merged_placeholder=%s",
                target.id, placeholder)
    return ok({
        "tokens": tokens,
        "user": _user_payload(target),
        "merged": bool(placeholder),
    })


@wx_api_bp.post("/auth/unbind")
@wx_login_required
def wx_unbind():
    """解绑当前微信身份（换绑 / 退出小程序用）。

    请求 ``{"password": "原账户密码"}`` 或 ``{"code": "邮箱验证码"}``。

    为什么需要再次验证：解绑后这枚 openid 就能绑到任意账户，
    若不验证控制权，等于给「抢绑」开了一道门。
    解绑只删 wechat_identities 关联行，**不删除账户、不删除任何学习数据**。
    """
    body = _json()
    password = body.get("password") or ""
    code = (body.get("code") or "").strip()
    user = g.wx_user

    if (user.email or "").endswith("@wx.local"):
        # 纯微信占位账户：没有可验证的密码，只能整号废弃（不删数据，仅断开关联）
        pass
    else:
        if not password and not code:
            return fail("请提供原账户密码，或该邮箱收到的验证码", code="bad_request")
        if code:
            ok_code, msg = verify_code(user.email, code, "login")
            if not ok_code:
                return fail(msg, code="invalid_code", status=401)
        else:
            if user.is_locked:
                return fail("账户已临时锁定，请稍后再试", code="locked", status=429)
            if not user.check_password(password):
                return fail("密码不正确", code="invalid_credentials", status=401)

    from models import WeChatIdentity
    appid = (current_app.config.get("WX_APPID") or "").strip()
    row = WeChatIdentity.query.filter_by(user_id=user.id, appid=appid).first()
    if row is None:
        return fail("当前账户未绑定微信", code="not_bound", status=404)

    try:
        db.session.delete(row)
        db.session.commit()
    except Exception as exc:  # noqa: BLE001
        db.session.rollback()
        return _err(exc, "解绑失败，请稍后重试")

    # 解绑后当前令牌立即失效，强制重新走微信登录
    claims = g.wx_claims or {}
    if claims.get("jti"):
        revoke_token(str(claims["jti"]), user.id, "access",
                     datetime.utcfromtimestamp(int(claims["exp"])))

    logger.info("[wx_api] 微信解绑 user=%s", user.id)
    return ok({"unbound": True})


def _current_openid() -> str:
    from models import WeChatIdentity
    appid = (current_app.config.get("WX_APPID") or "").strip()
    row = WeChatIdentity.query.filter_by(user_id=g.wx_user.id, appid=appid).first()
    return row.openid if row else ""


def _merge_accounts(target: User, source: User) -> None:
    """把占位账户的学习数据合并进目标账户（同一事务内完成）。

    合并规则（全部幂等，唯一约束冲突时合并计数而不是报错）：
    * UserWordProgress：目标已有 → 累加 correct/wrong/view；否则改挂 user_id
    * Favorite / WrongAnswer：目标已有 → 保留目标（不重复插）；否则改挂
    * StudyRecord / TestRecord / GameRecord：直接改挂 user_id（无唯一约束）
    * UserStat：XP / 等级取较高，连续天数取较大，总计累加

    调用方负责 commit；任何异常上抛由调用方 rollback。
    """
    if target.id == source.id:
        return

    # ---- 单词进度 ----
    for sp in UserWordProgress.query.filter_by(user_id=source.id).all():
        tp = UserWordProgress.query.filter_by(user_id=target.id, word_id=sp.word_id).first()
        if tp is None:
            sp.user_id = target.id
            continue
        tp.correct_count = (tp.correct_count or 0) + (sp.correct_count or 0)
        tp.wrong_count = (tp.wrong_count or 0) + (sp.wrong_count or 0)
        tp.view_count = (tp.view_count or 0) + (sp.view_count or 0)
        tp.last_studied_at = max(tp.last_studied_at or sp.last_studied_at,
                                 sp.last_studied_at or tp.last_studied_at)
        if tp.status != "mastered" and sp.status == "mastered":
            tp.status = "mastered"
            tp.mastered_at = tp.mastered_at or sp.mastered_at
        db.session.delete(sp)

    # ---- 收藏 / 错题本（唯一约束 user_id+word_id）----
    for sf in Favorite.query.filter_by(user_id=source.id).all():
        if Favorite.query.filter_by(user_id=target.id, word_id=sf.word_id).first() is None:
            sf.user_id = target.id
        else:
            db.session.delete(sf)
    for sw in WrongAnswer.query.filter_by(user_id=source.id).all():
        tw = WrongAnswer.query.filter_by(user_id=target.id, word_id=sw.word_id).first()
        if tw is None:
            sw.user_id = target.id
        else:
            tw.wrong_count = (tw.wrong_count or 0) + (sw.wrong_count or 0)
            tw.last_wrong_at = max(tw.last_wrong_at or sw.last_wrong_at,
                                   sw.last_wrong_at or tw.last_wrong_at)
            db.session.delete(sw)

    # ---- 流水类（无唯一约束，直接改挂）----
    from models import GameRecord, StudyRecord
    for model in (StudyRecord, TestRecord, GameRecord):
        model.query.filter_by(user_id=source.id).update(
            {"user_id": target.id}, synchronize_session=False)

    # ---- 游戏化统计 ----
    ss = UserStat.query.filter_by(user_id=source.id).first()
    ts = UserStat.query.filter_by(user_id=target.id).first()
    if ss is not None:
        if ts is None:
            ss.user_id = target.id
        else:
            ts.xp = (ts.xp or 0) + (ss.xp or 0)
            ts.level = max(ts.level or 1, ss.level or 1)
            ts.streak_days = max(ts.streak_days or 0, ss.streak_days or 0)
            ts.best_streak = max(ts.best_streak or 0, ss.best_streak or 0)
            ts.total_games = (ts.total_games or 0) + (ss.total_games or 0)
            ts.today_done = (ts.today_done or 0) + (ss.today_done or 0)
            db.session.delete(ss)

    db.session.flush()


# ==========================================================================
# 学习数据
# ==========================================================================
@wx_api_bp.get("/home")
@wx_login_required
def wx_home():
    """首页学习概览（复用 Web 端 Dashboard 同一套业务服务）。"""
    uid = g.wx_user.id
    data = overview(uid)
    data["stats"] = dashboard_stats(uid)
    data["streak_days"] = learning_streak(uid)
    data["skills"] = [
        {"skill": s.skill, "xp": s.xp, "items": s.items, "minutes": s.minutes}
        for s in skill_stats(uid).values()
    ]
    # 待复习 / 错题数量，供首页入口显示角标
    data["wrong_count"] = WrongAnswer.query.filter_by(user_id=uid).count()
    data["favorite_count"] = Favorite.query.filter_by(user_id=uid).count()
    data["skills_meta"] = SKILLS
    return ok(data)


@wx_api_bp.get("/words")
@wx_login_optional
def wx_words():
    """单词浏览 / 搜索（未登录也可查，个性化字段留空）。

    GET /api/wx/v1/words?q=&page=&size=&initial=

    带有效 Bearer Token 时返回收藏 / 学习状态；匿名访问时 status 统一为 new。
    """
    q = (request.args.get("q") or "").strip()
    page = max(1, request.args.get("page", 1, type=int) or 1)
    size = min(MAX_PAGE_SIZE, max(1, request.args.get("size", 20, type=int) or 20))
    initial = (request.args.get("initial") or "").strip()[:1]

    query = Word.query
    if q:
        like = f"%{q.lower()}%"
        query = query.filter(Word.word.like(like) | Word.meaning_cn.like(like))
    if initial:
        query = query.filter(Word.initial == initial.upper())

    total = query.count()
    rows = (query.order_by(Word.freq.desc(), Word.id)
            .offset((page - 1) * size).limit(size).all())

    uid = g.wx_user.id if getattr(g, "wx_user", None) is not None else None
    items = []
    for w in rows:
        if uid:
            view = build_word_view(w, uid)
        else:
            view = {
                "id": w.id, "word": w.word, "phonetic_uk": w.phonetic_uk,
                "pos": w.pos, "meaning_cn": w.meaning_cn, "example_en": w.example_en,
                "example_cn": w.example_cn, "level": w.level, "status": "new",
            }
        view["audio_url"] = audio_url(w.audio)
        items.append(view)
    return paged(items, page, max(1, (total + size - 1) // size), total)


@wx_api_bp.get("/words/<int:word_id>")
@wx_login_optional
def wx_word_detail(word_id: int):
    w = db.session.get(Word, word_id)
    if w is None:
        return fail("单词不存在", code="not_found", status=404)
    uid = g.wx_user.id if getattr(g, "wx_user", None) is not None else None
    if uid:
        view = build_word_view(w, uid)
        touch_word(uid, w.id, "view")     # 记录一次学习活动（与 Web 端一致）
    else:
        view = {
            "id": w.id, "word": w.word, "phonetic_uk": w.phonetic_uk, "pos": w.pos,
            "meaning_cn": w.meaning_cn, "example_en": w.example_en,
            "example_cn": w.example_cn, "level": w.level, "status": "new",
        }
    view["audio_url"] = audio_url(w.audio)
    return ok(view)


@wx_api_bp.post("/words/<int:word_id>/favorite")
@wx_login_required
def wx_toggle_favorite(word_id: int):
    """收藏 / 取消收藏。返回最新状态，客户端据此切换按钮。"""
    uid = g.wx_user.id
    if db.session.get(Word, word_id) is None:
        return fail("单词不存在", code="not_found", status=404)

    fav = Favorite.query.filter_by(user_id=uid, word_id=word_id).first()
    if fav is not None:
        db.session.delete(fav)
        db.session.commit()
        return ok({"word_id": word_id, "favorited": False})
    db.session.add(Favorite(user_id=uid, word_id=word_id))
    db.session.commit()
    touch_word(uid, word_id, "favorite")
    return ok({"word_id": word_id, "favorited": True})


@wx_api_bp.get("/favorites")
@wx_login_required
def wx_favorites():
    """收藏列表（分页）。"""
    uid = g.wx_user.id
    page = max(1, request.args.get("page", 1, type=int) or 1)
    size = min(MAX_PAGE_SIZE, max(1, request.args.get("size", 20, type=int) or 20))
    q = (request.args.get("q") or "").strip()

    query = (Favorite.query.filter_by(user_id=uid)
             .join(Word, Word.id == Favorite.word_id))
    if q:
        like = f"%{q.lower()}%"
        query = query.filter(Word.word.like(like) | Word.meaning_cn.like(like))
    total = query.count()
    rows = (query.order_by(Favorite.created_at.desc())
            .offset((page - 1) * size).limit(size).all())
    items = []
    for fav in rows:
        view = build_word_view(fav.word, uid)
        view["audio_url"] = audio_url(fav.word.audio)
        view["favorited_at"] = fav.created_at.isoformat() + "Z"
        items.append(view)
    return paged(items, page, max(1, (total + size - 1) // size), total)


@wx_api_bp.get("/wrong")
@wx_login_required
def wx_wrong():
    """错题本（分页）。"""
    uid = g.wx_user.id
    page = max(1, request.args.get("page", 1, type=int) or 1)
    size = min(MAX_PAGE_SIZE, max(1, request.args.get("size", 20, type=int) or 20))
    q = (request.args.get("q") or "").strip()

    query = (WrongAnswer.query.filter_by(user_id=uid)
             .join(Word, Word.id == WrongAnswer.word_id))
    if q:
        like = f"%{q.lower()}%"
        query = query.filter(Word.word.like(like) | Word.meaning_cn.like(like))
    total = query.count()
    rows = (query.order_by(WrongAnswer.last_wrong_at.desc())
            .offset((page - 1) * size).limit(size).all())
    items = []
    for wa in rows:
        view = build_word_view(wa.word, uid)
        view["audio_url"] = audio_url(wa.word.audio)
        view["wrong_count"] = wa.wrong_count
        view["last_answer"] = wa.last_answer
        view["last_wrong_at"] = wa.last_wrong_at.isoformat() + "Z" if wa.last_wrong_at else ""
        items.append(view)
    return paged(items, page, max(1, (total + size - 1) // size), total)


@wx_api_bp.post("/wrong/<int:word_id>/remove")
@wx_login_required
def wx_remove_wrong(word_id: int):
    """把单词移出错题本。"""
    uid = g.wx_user.id
    row = WrongAnswer.query.filter_by(user_id=uid, word_id=word_id).first()
    if row is not None:
        db.session.delete(row)
        db.session.commit()
    return ok({"word_id": word_id, "removed": True})


@wx_api_bp.get("/progress")
@wx_login_required
def wx_progress():
    """学习进度统计。"""
    uid = g.wx_user.id
    from services import unlocked_badges
    stats = dashboard_stats(uid)
    return ok({
        "stats": stats,
        "trend": daily_trend(uid, 14),
        "streak": learning_streak(uid),
        "gamification": overview(uid),
        "badges": unlocked_badges(stats),
        "skills": [
            {"skill": s.skill, "xp": s.xp, "items": s.items, "minutes": s.minutes}
            for s in skill_stats(uid).values()
        ],
    })


@wx_api_bp.post("/me/goal")
@wx_login_required
def wx_set_goal():
    try:
        goal = int(_json().get("goal") or 20)
    except (TypeError, ValueError):
        return fail("目标必须是数字", code="bad_request")
    goal = max(1, min(goal, 500))
    return ok({"goal": set_goal(g.wx_user.id, goal)})


# ==========================================================================
# 测试中心（无状态：出题状态由客户端随请求带回，服务端验签还原）
# ==========================================================================
TEST_MODES = ("choice", "zh_en", "listen", "spell")
TEST_SCOPES = ("all", "new", "learning", "mastered", "favorites", "wrong")


@wx_api_bp.post("/quiz/start")
@wx_login_required
def wx_quiz_start():
    """开始一轮测试。

    请求 ``{"mode": "choice", "scope": "all", "size": 10}``
    响应 ``{"state": "<签名状态令牌>", "mode": ..., "total": n}``

    客户端必须把 ``state`` 存下来，后续取题 / 判题 / 交卷都原样带回。
    """
    body = _json()
    mode = (body.get("mode") or "choice").strip()
    scope = (body.get("scope") or "all").strip()
    if mode not in TEST_MODES:
        mode = "choice"
    if scope not in TEST_SCOPES:
        scope = "all"
    try:
        size = int(body.get("size") or 10)
    except (TypeError, ValueError):
        size = 10
    size = max(1, min(size, 50))

    ids = _pick_word_ids(g.wx_user.id, scope, size)
    if not ids:
        return fail("该范围内没有可用的单词，请更换范围", code="no_words", status=400)

    state_token = create_state(mode, ids)
    return ok({"state": state_token, "mode": mode, "total": len(ids)})


@wx_api_bp.get("/quiz/item")
@wx_login_required
def wx_quiz_item():
    """取第 i 题（i 从 0 开始）。

    响应里 ``state`` 是**更新后**的状态令牌（写入了本题正确选项下标），
    客户端必须用它覆盖本地旧值，否则判题会失去依据。
    """
    state_token = (request.args.get("state") or "").strip()
    try:
        idx = int(request.args.get("i") or 0)
    except ValueError:
        idx = 0

    state = load_state(state_token)
    q = build_item(state, idx)
    # listen 模式的题干是音频：把文件名解析成可播放地址一起下发，
    # 否则小程序端只有文件名、无从播放（单词接口均已带 audio_url，此处对齐）
    q["audio_url"] = audio_url(q.get("audio"))
    # 状态已更新（写入本题判题依据），重新签发后随题下发
    return ok({"q": q, "state": _restate(state), "total": state.get("total")})


def _restate(state: dict) -> str:
    """把更新后的状态重新签发。

    **必须原样带回** answers（判题依据）与 started_at（计时起点）——
    早期版本在这里丢了这两个字段，导致判题退化成文本比对、用时被重置。
    """
    return create_state(
        state.get("mode") or "choice",
        state.get("ids") or [],
        seed=state.get("seed"),
        answers=state.get("answers") or {},
        started_at=state.get("started_at"),
        answered=int(state.get("answered") or 0),
    )


@wx_api_bp.post("/quiz/answer")
@wx_login_required
def wx_quiz_answer():
    """判题。

    请求 ``{"state": "...", "i": 0, "answer": 2}``
    ``answer`` 为选项下标（int 或数字字符串）；spell 模式为输入的文本。
    """
    body = _json()
    state_token = (body.get("state") or "").strip()
    try:
        idx = int(body.get("i") or 0)
    except (TypeError, ValueError):
        idx = 0

    state = load_state(state_token)
    ids = state.get("ids") or []
    if idx < 0 or idx >= len(ids):
        raise QuizStateError("out_of_range", "题目序号超出范围")

    mode = state.get("mode") or "choice"
    word = db.session.get(Word, int(ids[idx]))
    if word is None:
        raise QuizStateError("bad_data", "题目数据异常")

    answer = body.get("answer")
    if answer is None:
        answer = ""
    answer = str(answer).strip()
    correct_text = expected_text(word, mode)

    saved_idx = (state.get("answers") or {}).get(str(idx))
    if mode == "spell":
        correct = judge({"mode": mode, "answer": correct_text}, answer)
        user_value = answer
    elif answer.lstrip("-").isdigit():
        opt_idx = int(answer)
        options = _rebuild_options(word, mode, state.get("seed"))
        user_value = options[opt_idx] if 0 <= opt_idx < len(options) else ""
        if isinstance(saved_idx, int) and saved_idx >= 0:
            # 判题依据来自出题时保存的状态，绝不重新生成随机选项
            correct = (opt_idx == saved_idx)
        else:
            correct = judge({"mode": mode, "answer": correct_text}, user_value)
    else:
        # 提交的是选项文本：与正确答案文本直接比对（兼容手工调用）
        user_value = answer
        correct = judge({"mode": mode, "answer": correct_text}, user_value)

    record_answer(g.wx_user.id, word.id, correct, mode)
    state["answered"] = int(state.get("answered") or 0) + 1
    return ok({
        "correct": bool(correct),
        "expected": correct_text,
        "user_answer": user_value,
        "word": word.word,
        "phonetic": word.phonetic_uk,
        "meaning": word.meaning_cn,
        "example_en": word.example_en,
        "example_cn": word.example_cn,
        "audio": word.audio,
        "audio_url": audio_url(word.audio),
        "mode": mode,
        "state": _restate(state),
        "progress": {"answered": state["answered"], "total": state.get("total")},
    })


def _rebuild_options(word: Word, mode: str, seed) -> list[str]:
    """用与出题相同的 seed 确定性复现选项列表（仅用于取回选项文本）。"""
    q = build_question(word, mode, seed)
    return list(q.get("options") or [])


@wx_api_bp.post("/quiz/finish")
@wx_login_required
def wx_quiz_finish():
    """交卷：统计成绩并写入 TestRecord。

    请求 ``{"state": "..."}``
    响应 ``{"total", "score", "percent", "duration", "mode", "comment", "record_id"}``
    """
    body = _json()
    state_token = (body.get("state") or "").strip()
    state = load_state(state_token)

    started = state.get("started_at") or int(datetime.utcnow().timestamp())
    duration = max(1, int(datetime.utcnow().timestamp()) - int(started))
    total = int(state.get("total") or len(state.get("ids") or []))

    from models import StudyRecord
    rows = (StudyRecord.query.filter_by(user_id=g.wx_user.id, action="answer")
            .filter(StudyRecord.created_at >= datetime.utcfromtimestamp(int(started) - 1))
            .all())
    listed = dict.fromkeys(int(i) for i in (state.get("ids") or []))
    mine = [r for r in rows if r.word_id in listed]
    score = sum(1 for r in mine if r.is_correct)

    rec = TestRecord(user_id=g.wx_user.id, mode=state.get("mode") or "choice",
                     total=total, score=score, duration_sec=duration)
    db.session.add(rec)
    db.session.commit()

    pct = round(score * 100 / total) if total else 0
    comment = ("outstanding" if pct >= 90 else "good" if pct >= 70 else
               "pass" if pct >= 50 else "retry")
    return ok({"total": total, "score": score, "percent": pct,
               "duration": duration, "mode": state.get("mode"),
               "comment": comment, "record_id": rec.id})


@wx_api_bp.get("/meta/skills")
def wx_meta_skills():
    return ok({"items": SKILLS})


@wx_api_bp.get("/health")
def wx_health():
    return ok({"status": "ok", "api": "wx/v1"})
