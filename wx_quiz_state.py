"""小程序测试中心的**无状态**出题状态传递。

为什么需要它
------------
Web 端测试中心把出题状态存在 Flask session Cookie 里（见 ``routes/quiz.py``），
这依赖 Cookie 与会话。小程序走 Bearer Token、且要支持多 worker / 断线重连，
不能再依赖服务端会话。

做法
----
把出题状态（词 id 列表、模式、seed、每题正确选项下标）用 **itsdangerous 的
HMAC 签名**序列化成一枚不透明字符串，随题目一起返回客户端；客户端在后续
「取题 / 判题 / 交卷」请求里原样带回来，服务端验签后还原。

重要命名边界
------------
**这是「签名状态令牌」（URLSafeSerializer），不是 JWT。**
它与 :mod:`wx_auth` 里的标准 JWT 是两套独立机制，用途不同、不可互相替代、
也不应互相称呼：JWT 用于「你是谁」，本模块用于「这一轮测试进行到哪了」。
之所以用 itsdangerous 而不是 JWT，是因为 payload 由服务端全量生成、
原样回传，不需要 claims 语义，序列化 + HMAC 更直接也更难被构造。

安全性质
--------
* 签名密钥独立于 JWT（``WX_QUIZ_STATE_SECRET``，缺省回落到 ``WX_TOKEN_SECRET``），
  分开轮换不影响另一套；
* 客户端无法在不知道密钥的情况下篡改词表或正确答案下标 —— 改一个字节验签即失败；
* 状态里**不包含**用户身份，用户身份始终来自 Authorization 头里的 Access Token；
* 设 2 小时过期，超时需重新开始测试（与 Access Token 同步，避免长期悬空）。
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

from itsdangerous import BadSignature, SignatureExpired, URLSafeSerializer

from extensions import db
from models import Word
from services import build_question

SALT = "wx-quiz-state-v1"
STATE_TTL_SECONDS = 2 * 60 * 60      # 与 Access Token 对齐：2 小时


class QuizStateError(Exception):
    """状态令牌缺失 / 被篡改 / 已过期。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _serializer():
    """签名序列化器。密钥优先取专用变量，缺省回落到 JWT 密钥。

    回落是安全的：两者都由同一个 ``WX_TOKEN_SECRET`` 派生信任，且 salt 不同，
    签名域互相隔离，不会串用。
    """
    from flask import current_app

    secret = (current_app.config.get("WX_QUIZ_STATE_SECRET") or "").strip()
    if not secret:
        secret = (current_app.config.get("WX_TOKEN_SECRET") or "").strip()
    if not secret:
        if current_app.config.get("TESTING") or current_app.config.get("DEBUG"):
            secret = "dev-only-insecure-quiz-state-secret"
        else:
            raise RuntimeError("WX_QUIZ_STATE_SECRET / WX_TOKEN_SECRET 均未配置，拒绝签发测试状态")
    return URLSafeSerializer(secret, salt=SALT)


def create_state(mode: str, word_ids: list[int], seed: int | None = None,
                 answers: dict | None = None, started_at: int | None = None,
                 answered: int = 0) -> str:
    """生成一枚签名状态令牌。

    ``answers`` 保存「出题时确定的正确选项下标」，判题时直接读取，
    绝不重新生成随机选项 —— 否则前端提交的下标会指向另一个选项
    （这是 Web 端已经踩过的坑，见 services._question_rng 的注释）。

    ``started_at`` 由首题签发时确定，后续重新签发必须原样带回，
    否则交卷时算出的用时会被不断重置。
    """
    if not word_ids:
        raise QuizStateError("empty", "没有可用于出题的单词")
    payload = {
        "v": 1,
        "mode": mode,
        "ids": [int(i) for i in word_ids],
        "total": len(word_ids),
        "answered": int(answered or 0),
        "seed": int(seed if seed is not None else random.randrange(1, 2 ** 31)),
        "answers": {str(k): int(v) for k, v in (answers or {}).items()},
        "started_at": int(started_at if started_at is not None
                          else datetime.utcnow().timestamp()),
    }
    return _serializer().dumps(payload)


def load_state(token: str) -> dict:
    """验签并还原状态。失败抛 QuizStateError。"""
    if not token or len(token) > 16384:
        raise QuizStateError("missing_state", "测试状态缺失，请重新开始")
    try:
        data = _serializer().loads(token, max_age=STATE_TTL_SECONDS)
    except SignatureExpired:
        raise QuizStateError("state_expired", "测试已超时，请重新开始")
    except BadSignature:
        raise QuizStateError("invalid_state", "测试状态无效，请重新开始")

    if not isinstance(data, dict) or not data.get("ids"):
        raise QuizStateError("invalid_state", "测试状态无效，请重新开始")
    return data


def build_item(state: dict, idx: int) -> dict:
    """取第 idx 题，并把「正确选项下标」写回状态（随令牌回传）。

    返回可直接下发客户端的题目（**已剔除 answer 字段**）。
    """
    ids = state.get("ids") or []
    if idx < 0 or idx >= len(ids):
        raise QuizStateError("out_of_range", "题目序号超出范围")

    word = db.session.get(Word, int(ids[idx]))
    if word is None:
        raise QuizStateError("bad_data", "题目数据异常")

    mode = state.get("mode") or "choice"
    q = build_question(word, mode, state.get("seed"))
    q["i"] = idx
    q["total"] = state.get("total") or len(ids)

    opts = list(q.get("options") or [])
    answers = dict(state.get("answers") or {})
    if opts and q.get("answer") in opts:
        answers[str(idx)] = opts.index(q["answer"])
    else:
        # spell 模式没有选项；异常情况标 -1，判题时退化为文本比对
        answers[str(idx)] = -1
    state["answers"] = answers

    q.pop("answer", None)   # 绝不把正确答案下发给客户端
    return q


def expected_text(word: Word, mode: str) -> str:
    """该模式下本题的正确答案文本（不依赖选项，word 表直接可得）。"""
    return word.meaning_cn if mode == "choice" else word.word


def expires_at() -> datetime:
    """状态令牌的理论到期时间（用于日志 / 排查）。"""
    return datetime.utcnow() + timedelta(seconds=STATE_TTL_SECONDS)
