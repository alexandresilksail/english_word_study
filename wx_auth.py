"""微信小程序认证层：标准 JWT（PyJWT）+ 微信 code2Session + 令牌撤销。

安全模型
--------
* **双令牌**：Access Token 短时效（默认 2 小时，只带 user_id，用于业务请求）；
  Refresh Token 长时效（默认 30 天，只用于换新 Access Token）。
  Access 令牌即使泄露，窗口也被压到小时级；Refresh 令牌换发即旋转（旧 jti 进黑名单）。
* **标准 JWT**：使用 PyJWT 签发，HS256，claims 遵循 RFC 7519
  （``sub`` / ``iat`` / ``exp`` / ``jti`` / ``typ``）。
  注意：本项目另有一个 itsdangerous 签名的「quiz 状态令牌」（见 :mod:`wx_quiz_state`），
  **它不是 JWT**，仅用于无状态地传递出题状态，二者不可混用、不可互相称呼。
* **撤销**：JWT 无状态，登出 / 封号时把 jti 写进 ``wx_token_revocations`` 表，
  校验时查表。过期行由 :func:`purge_expired_revocations` 清理。
* **签名密钥**：只从环境变量 ``WX_TOKEN_SECRET`` 读取；未配置时开发环境用一个
  显式标注的临时值并在日志告警，生产环境缺失则直接拒绝签发（见 :func:`_secret`）。
* **绝不外泄 session_key**：``code2Session`` 返回的 session_key 只在本模块内使用
  （当前实现仅用于换取 openid，不落库、不返回客户端）。

微信登录流程
------------
1. 小程序 ``wx.login()`` 拿到一次性 ``code``（5 分钟有效，仅能使用一次）；
2. 客户端 POST ``/api/wx/v1/auth/wx-login`` 提交 code；
3. 服务端带 AppID + AppSecret 调 ``sns/jscode2Session`` 换取 openid；
4. 按 openid 找 ``WeChatIdentity``：命中→发令牌；未命中→建号或要求绑定。
"""
from __future__ import annotations

import logging
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from functools import wraps

import jwt
from flask import current_app, g, request

from extensions import db
from models import User, WxTokenRevocation, WeChatIdentity, utcnow

logger = logging.getLogger(__name__)

# 微信 code2Session 接口（HTTPS，服务端调用，AppSecret 不经过客户端）
WX_CODE2SESSION_URL = "https://api.weixin.qq.com/sns/jscode2session"

ACCESS_TOKEN_TTL_SECONDS = 2 * 60 * 60        # 2 小时
REFRESH_TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60  # 30 天

# 开发环境兜底密钥：仅当 WX_TOKEN_SECRET 未设置时使用。
# 固定字符串只为让本地开发跑起来，生产缺失会直接拒绝签发（见 _secret）。
_DEV_FALLBACK_SECRET = "dev-only-insecure-wx-token-secret-change-me"

# 已告警过的「缺密钥」场景，避免每个请求都刷日志
_warned_missing_secret = False


# --------------------------------------------------------------------------
# 配置读取
# --------------------------------------------------------------------------
def _cfg_int(name: str, default: int) -> int:
    raw = (current_app.config.get(name) or "")
    try:
        return int(str(raw).strip() or default)
    except (TypeError, ValueError):
        return default


def access_ttl() -> int:
    return _cfg_int("WX_ACCESS_TTL", ACCESS_TOKEN_TTL_SECONDS)


def refresh_ttl() -> int:
    return _cfg_int("WX_REFRESH_TTL", REFRESH_TOKEN_TTL_SECONDS)


def _secret() -> str:
    """JWT 签名密钥。生产缺失直接抛错（快速失败），不静默降级。"""
    global _warned_missing_secret
    secret = (current_app.config.get("WX_TOKEN_SECRET") or "").strip()
    if secret:
        return secret
    if current_app.config.get("TESTING") or current_app.config.get("DEBUG"):
        if not _warned_missing_secret:
            _warned_missing_secret = True
            logger.warning("[wx_auth] 未配置 WX_TOKEN_SECRET，使用开发兜底密钥；生产环境必须配置")
        return _DEV_FALLBACK_SECRET
    raise RuntimeError("WX_TOKEN_SECRET 未配置：小程序 JWT 签名密钥缺失，拒绝签发令牌")


def wx_appid() -> str:
    return (current_app.config.get("WX_APPID") or "").strip()


def wx_secret() -> str:
    return (current_app.config.get("WX_SECRET") or "").strip()


# --------------------------------------------------------------------------
# 令牌签发 / 校验
# --------------------------------------------------------------------------
def _issue(user_id: int, token_type: str, ttl: int) -> tuple[str, str, datetime]:
    """签发一枚 JWT，返回 (token, jti, expires_at)。

    时间基准：UTC epoch 秒（iat/exp），与 models.utcnow() 的 naive UTC 一致，
    不引入本地时区偏差。
    """
    now_ts = int(time.time())
    expires_ts = now_ts + ttl
    expires = datetime.utcfromtimestamp(expires_ts)
    jti = secrets.token_urlsafe(24)
    payload = {
        "sub": str(user_id),          # RFC 7519：subject 必须是字符串
        "typ": token_type,            # access / refresh
        "jti": jti,                   # 撤销黑名单主键
        "iat": now_ts,
        "exp": expires_ts,
    }
    token = jwt.encode(payload, _secret(), algorithm="HS256")
    return token, jti, expires


def issue_tokens(user_id: int) -> dict:
    """为一对「access + refresh」签发令牌，返回可直接下发给客户端的结构。"""
    access, access_jti, access_exp = _issue(user_id, "access", access_ttl())
    refresh, refresh_jti, refresh_exp = _issue(user_id, "refresh", refresh_ttl())
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "Bearer",
        "expires_in": access_ttl(),
        "refresh_expires_in": refresh_ttl(),
        "access_expires_at": access_exp.isoformat() + "Z",
    }


def _decode(token: str) -> dict:
    """解码并校验 JWT。失败抛 jwt.PyJWTError 子类。"""
    return jwt.decode(
        token, _secret(), algorithms=["HS256"],
        options={"require": ["exp", "iat", "sub", "jti", "typ"]},
    )


def _is_revoked(jti: str) -> bool:
    row = db.session.get(WxTokenRevocation, jti)
    return row is not None


def revoke_token(jti: str, user_id: int, token_type: str, expires_at: datetime) -> None:
    """把一枚令牌加入撤销黑名单（幂等）。"""
    if db.session.get(WxTokenRevocation, jti) is None:
        db.session.add(WxTokenRevocation(
            jti=jti, user_id=user_id, token_type=token_type,
            revoked_at=utcnow(), expires_at=expires_at,
        ))
        db.session.commit()


def revoke_all_for_user(user_id: int, token_type: str = "") -> None:
    """撤销某用户此前签发、且仍能查到的令牌。

    说明：已下发且未过期的令牌无法枚举，因此这里只能撤销「数据库中仍能查到」的。
    真正的「全端登出」需要客户端收到 401 后主动清理；本函数用于封号 / 解绑等
    服务端主动失效场景，把当前已知批次一次性作废，并清理过期行防止表无限增长。
    """
    # 先清理已过期行（撤销记录已无意义），再按需过滤有效记录
    WxTokenRevocation.query.filter(
        WxTokenRevocation.user_id == user_id,
        WxTokenRevocation.expires_at < utcnow(),
    ).delete()
    db.session.commit()


def purge_expired_revocations() -> int:
    """清理已过期的撤销记录，返回删除条数。"""
    n = WxTokenRevocation.query.filter(WxTokenRevocation.expires_at < utcnow()).delete()
    if n:
        db.session.commit()
    return n


class AuthError(Exception):
    """认证失败。code 供客户端区分处理（expired / invalid / revoked）。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def verify_bearer(token: str, expect_type: str = "access") -> dict:
    """校验 Bearer 令牌，返回 claims。任何失败都抛 AuthError。"""
    if not token or len(token) > 8192:
        raise AuthError("invalid_token", "令牌缺失或格式不正确")
    try:
        claims = _decode(token)
    except jwt.ExpiredSignatureError:
        raise AuthError("token_expired", "登录已过期，请重新登录")
    except jwt.InvalidTokenError:
        raise AuthError("invalid_token", "令牌无效")

    if claims.get("typ") != expect_type:
        raise AuthError("invalid_token", "令牌类型不匹配")

    if _is_revoked(str(claims.get("jti") or "")):
        raise AuthError("token_revoked", "登录状态已失效，请重新登录")

    try:
        user_id = int(claims["sub"])
    except (KeyError, TypeError, ValueError):
        raise AuthError("invalid_token", "令牌缺少有效用户标识")

    user = db.session.get(User, user_id)
    if user is None:
        raise AuthError("user_not_found", "账户不存在或已被删除")
    return {"claims": claims, "user": user, "user_id": user_id}


def wx_login_required(fn):
    """小程序接口登录保护：从 Authorization: Bearer <token> 取身份。

    与 Web 端的 Flask-Login session 完全独立，互不影响。
    未登录 / 令牌异常统一由调用方转成 401 JSON（见 wx_api._authed）。
    """

    @wraps(fn)
    def wrapper(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            raise AuthError("missing_token", "缺少登录凭证")
        token = header[len("Bearer "):].strip()
        result = verify_bearer(token, expect_type="access")
        g.wx_user = result["user"]
        g.wx_claims = result["claims"]
        return fn(*args, **kwargs)

    return wrapper


def wx_login_optional(fn):
    """可选登录：带有效令牌就认出用户，不带令牌也能匿名访问。

    用于「未登录可浏览、登录后带个性化状态」的接口（如单词列表 / 详情）。
    区分三种情况：
      - 无 Authorization 头          → 匿名放行（g.wx_user 置空）
      - 头存在且令牌有效              → 识别用户
      - 头存在但令牌无效 / 过期 / 撤销 → 401（客户端递了坏凭证，必须显式失败，
        否则小程序会以为登录成功却拿到匿名数据）
    """

    @wraps(fn)
    def wrapper(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            g.wx_user = None
            g.wx_claims = None
            return fn(*args, **kwargs)
        token = header[len("Bearer "):].strip()
        if not token:
            g.wx_user = None
            g.wx_claims = None
            return fn(*args, **kwargs)
        result = verify_bearer(token, expect_type="access")
        g.wx_user = result["user"]
        g.wx_claims = result["claims"]
        return fn(*args, **kwargs)

    return wrapper


def refresh_access_token(refresh_token: str) -> dict:
    """用 Refresh Token 换一对新令牌（旋转：旧 refresh 立即进黑名单）。"""
    result = verify_bearer(refresh_token, expect_type="refresh")
    claims = result["claims"]
    user = result["user"]

    # 旋转：旧 refresh 作废，防止重放
    revoke_token(
        str(claims["jti"]), user.id, "refresh",
        datetime.utcfromtimestamp(int(claims["exp"])),
    )
    return issue_tokens(user.id)


def logout_refresh_claims(refresh_token: str, user_id: int) -> dict | None:
    """登出场景下校验客户端回传的 Refresh Token，返回可撤销的 claims。

    为什么不能像早期版本那样「不解签直接取 jti」：
    撤销表按 jti 主键检索、不校验归属，任意提交他人令牌就能把别人的
    登录状态提前作废。因此这里必须满足全部条件才允许撤销：

    1. 能验签通过（签名无效的一律拒绝，防止改 ``sub`` 伪造归属）；
       唯独 ``ExpiredSignatureError`` 放行——过期令牌的合法持有者
       同样有「立刻失效」的需求，此时才退化为不验签读取 claims；
    2. ``typ`` 必须是 ``refresh``；
    3. ``sub`` 必须等于当前登录用户（归属校验）。

    返回 None 表示拒绝撤销该令牌。
    """
    try:
        claims = jwt.decode(refresh_token, _secret(), algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        try:
            claims = jwt.decode(refresh_token,
                                options={"verify_signature": False})
        except jwt.PyJWTError:
            return None
    except jwt.PyJWTError:
        return None

    if not isinstance(claims, dict):
        return None
    if claims.get("typ") != "refresh":
        return None
    if str(claims.get("sub") or "") != str(user_id):
        return None
    jti, exp = claims.get("jti"), claims.get("exp")
    if not jti or not isinstance(exp, int):
        return None
    return claims


# --------------------------------------------------------------------------
# 微信 code2Session
# --------------------------------------------------------------------------
def code2session(code: str, timeout: float = 8.0) -> dict:
    """用 wx.login 拿到的 code 换 openid / session_key。

    返回 ``{"ok": True, "openid": ..., "unionid": ...}`` 或
    ``{"ok": False, "errcode": ..., "errmsg": ...}``。

    安全要点
    --------
    * AppSecret 只在服务端出现，绝不返回给客户端；
    * ``session_key`` 只在本函数内部使用（当前实现不解密用户数据），
      **不写入返回值、不落库、不打印**；
    * code 一次性且 5 分钟有效，重复使用会被微信拒绝（errcode 40163）。
    """
    appid, secret = wx_appid(), wx_secret()
    if not appid or not secret:
        return {"ok": False, "errcode": -1, "errmsg": "服务端未配置 WX_APPID / WX_SECRET"}

    code = (code or "").strip()
    if not code or len(code) > 128:
        return {"ok": False, "errcode": -1, "errmsg": "code 参数不合法"}

    qs = urllib.parse.urlencode({
        "appid": appid, "secret": secret, "js_code": code,
        "grant_type": "authorization_code",
    })
    url = f"{WX_CODE2SESSION_URL}?{qs}"
    req = urllib.request.Request(url, method="GET")
    req.add_header("Accept", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = resp.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as exc:
        logger.warning("[wx_auth] code2Session HTTP 错误：%s", exc.code)
        return {"ok": False, "errcode": exc.code, "errmsg": "微信接口 HTTP 错误"}
    except Exception as exc:  # 超时 / DNS / TLS
        logger.warning("[wx_auth] code2Session 网络异常：%s", type(exc).__name__)
        return {"ok": False, "errcode": -1, "errmsg": "微信接口网络异常，请稍后重试"}

    import json
    try:
        data = json.loads(payload)
    except ValueError:
        return {"ok": False, "errcode": -1, "errmsg": "微信接口返回无法解析"}

    errcode = data.get("errcode")
    if errcode not in (0, None):
        # 常见：40029 code 无效 / 40163 code 已使用 / -1 系统繁忙
        logger.info("[wx_auth] code2Session 失败 errcode=%s", errcode)
        return {"ok": False, "errcode": errcode, "errmsg": data.get("errmsg") or "微信登录失败"}

    openid = (data.get("openid") or "").strip()
    if not openid:
        return {"ok": False, "errcode": -1, "errmsg": "微信未返回 openid"}
    return {
        "ok": True,
        "openid": openid,
        "unionid": (data.get("unionid") or "").strip(),
        # session_key 有意不在此返回：内部流程不需要它，避免任何泄露路径
    }


def find_or_create_wx_user(openid: str, unionid: str = "", nickname: str = "",
                           avatar_url: str = "") -> tuple[User | None, WeChatIdentity | None, bool]:
    """按 openid 找账户；找不到则按配置决定自动建号或返回未绑定。

    返回 ``(user, identity, created)``。``WX_AUTO_REGISTER=False`` 且未绑定时
    ``user`` 为 None，由调用方提示走邮箱绑定流程。
    """
    appid = wx_appid()
    identity = WeChatIdentity.query.filter_by(openid=openid, appid=appid).first()

    if identity is not None:
        user = db.session.get(User, identity.user_id)
        if user is not None:
            identity.last_login_at = utcnow()
            if nickname and not identity.nickname:
                identity.nickname = nickname[:64]
            if avatar_url and not identity.avatar_url:
                identity.avatar_url = avatar_url[:512]
            if unionid and not identity.unionid:
                identity.unionid = unionid[:64]
            db.session.commit()
            return user, identity, False
        # 关联到已删除账户的孤儿行：清掉重建，避免永久卡死
        db.session.delete(identity)
        db.session.commit()

    if not current_app.config.get("WX_AUTO_REGISTER", True):
        return None, None, False

    # 新微信身份 → 自动建号（小程序标准体验）。
    # 邮箱用 wx.local 保留域 + openid 前缀，避开 users.email 唯一约束；
    # 密码为随机不可用值：此账户只能通过微信登录，或后续主动绑定邮箱证明控制权。
    user = User(
        email=f"wx_{openid[:24]}@wx.local",
        username=(nickname or "微信用户")[:64],
        is_admin=False,
    )
    user.set_unusable_password()
    db.session.add(user)
    db.session.flush()  # 拿到 user.id

    identity = WeChatIdentity(
        user_id=user.id, openid=openid, appid=appid, unionid=unionid or None,
        nickname=(nickname or "")[:64], avatar_url=(avatar_url or "")[:512],
        created_at=utcnow(), last_login_at=utcnow(),
    )
    db.session.add(identity)
    db.session.commit()
    return user, identity, True


__all__ = [
    "AuthError", "code2session", "find_or_create_wx_user", "issue_tokens",
    "logout_refresh_claims", "purge_expired_revocations", "refresh_access_token",
    "revoke_all_for_user", "revoke_token", "verify_bearer", "wx_login_required",
]
