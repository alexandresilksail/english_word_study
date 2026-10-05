"""小游戏测试共享工具。

提供两套「推导正确答案」的方法：

1. ``state_answers(key, state)`` —— 直接读 service 出题时保存的判题状态
   （solution / answers）。用于**纯 service 层**测试，完全不依赖数据库，
   最稳：验证「判分用保存的状态、不重新随机」这一核心不变量。

2. ``view_answers(key, view)`` —— 只凭 HTTP 开局返回的 view 推导正确答案。
   因为前端本来就拿不到答案，这里模拟真实客户端：从 view 反查数据库得到
   正确选项下标 / 单词，用来驱动端到端（route / REST API）测试。
"""
from __future__ import annotations

from models import Word


def state_answers(key: str, state: dict) -> list:
    """从保存的判题状态推导正确提交（service 层单测用）。"""
    if key == "word_match":
        sol = (state or {}).get("solution") or {}
        return [[int(i), j] for i, j in sol.items()]
    if key in ("speed_quiz", "listening_challenge", "word_builder"):
        ans = (state or {}).get("answers") or {}
        return [[int(i), v] for i, v in ans.items()]
    return []


def view_answers(key: str, view: dict) -> list:
    """从开局 view 推导正确提交（route / API 端到端用）。"""
    if key == "word_match":
        left, right = view["left"], view["right"]
        out = []
        for it in left:
            j = next((r["j"] for r in right if r["id"] == it["id"]), None)
            out.append([it["i"], j])
        return out

    if key == "speed_quiz":
        out = []
        for q in view["questions"]:
            w = Word.query.filter_by(word=q["word"]).first()
            out.append([q["i"], q["options"].index(w.meaning_cn)])
        return out

    if key == "listening_challenge":
        out = []
        for q in view["questions"]:
            w = Word.query.filter_by(audio=q["audio"]).first()
            out.append([q["i"], q["options"].index(w.word)])
        return out

    if key == "word_builder":
        out = []
        for q in view["questions"]:
            w = Word.query.filter_by(audio=q["audio"]).first()
            out.append([q["i"], w.word])
        return out

    return []
