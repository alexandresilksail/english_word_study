"""小游戏服务层（Word Match / Speed Quiz / Listening Challenge / Word Builder）。

设计原则（与 Quiz 判分修复完全一致）
-----------------------------------
**出题时把用于判分的状态保存下来，判分时读取它，绝不重新随机生成。**

每个游戏都有两个动作：

1. ``start_xxx()``   —— 生成题目并**保存判题状态**（正确答案下标 / 单词 id / 配对表）
2. ``submit_xxx()``  —— 用保存的状态判分，绝不重新抽词、重新洗牌

状态保存在 session["game"] 中（与 quiz 同一套机制），不信任前端回传的答案。
所有随机都走 :func:`services._question_rng` 的确定性随机源，保证刷新页面
（重新 GET）时同一局题目不变。

跨端说明：本模块不依赖 Flask 的 request/session，输入是显式的 user_id 与
state 字典，因此将来 REST API、小程序、Android 都能复用同一套逻辑。
"""
from __future__ import annotations

import random
from datetime import datetime, timezone

from extensions import db
from models import Word
from services import _question_rng  # 确定性随机源（与出题判分共用同一实现）

# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------
WORD_MATCH_PAIRS = 6          # 配对题对数
SPEED_QUIZ_QUESTIONS = 30     # 60 秒内的题池（答完提前结束）
LISTENING_QUESTIONS = 10
BUILDER_QUESTIONS = 8

# 计分
SCORE_CORRECT = 100
SCORE_COMBO_STEP = 20         # 每层连击额外加分
SPEED_TIME_BONUS = 3          # 抢答：剩余秒数 × 该系数


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def _pick_words(size: int, seed: int, user_id: int | None = None,
                min_len: int = 3, max_len: int = 9) -> list[Word]:
    """按 seed 稳定抽词（同一 seed 结果完全一致，刷新不换题）。

    优先挑长度适中的词：太短拼写题没难度，太长在手机上排版会溢出。
    """
    q = Word.query.filter(Word.word.isnot(None))
    total = q.count()
    if total == 0:
        return []
    rng = random.Random(int(seed) * 7919 + 13)
    pool_size = min(total, 400)
    offset = rng.randrange(0, max(1, total - pool_size + 1))
    rows = q.order_by(Word.id).offset(offset).limit(pool_size).all()
    rows = [w for w in rows if min_len <= len(w.word or "") <= max_len] or rows
    rng.shuffle(rows)
    return rows[:size]


def _word_payload(w: Word) -> dict:
    """给前端的最小字段集（不含任何答案）。"""
    return {
        "id": w.id,
        "word": w.word,
        "meaning": w.meaning_cn,
        "phonetic": w.phonetic_uk,
        "audio": w.audio,
        "pos": w.pos,
    }


# ==========================================================================
# 1) Word Match —— 单词 ↔ 中文释义配对
# ==========================================================================
def start_word_match(seed: int | None = None) -> dict:
    """生成配对盘面，并把「左列 i ↔ 右列 j」的正确映射保存进 state。"""
    seed = int(seed or random.randrange(1, 2 ** 31))
    words = _pick_words(WORD_MATCH_PAIRS, seed, min_len=3, max_len=10)
    if len(words) < 2:
        return {"ok": False, "error": "词库不足，无法开始游戏"}

    rng = _question_rng(0, seed)
    left = [_word_payload(w) for w in words]
    right = [_word_payload(w) for w in words]
    rng.shuffle(right)
    # 正确映射：左列第 i 项对应的单词 id，在右列中的下标
    right_ids = [r["id"] for r in right]
    solution = {str(i): right_ids.index(left[i]["id"]) for i in range(len(left))}

    state = {"game": "word_match", "seed": seed, "solution": solution,
             "started_at": _now_ts()}
    view = {"game": "word_match", "seed": seed,
            "left": [{"i": i, "text": l["word"], "id": l["id"]} for i, l in enumerate(left)],
            "right": [{"j": j, "text": r["meaning"], "id": r["id"]} for j, r in enumerate(right)],
            "limit_sec": 90}
    return {"ok": True, "view": view, "state": state}


def submit_word_match(state: dict, pairs: list) -> dict:
    """判分：pairs 为 [[i, j], ...]；用保存的 solution 判定，不重新抽词。"""
    solution = (state or {}).get("solution") or {}
    if not solution:
        return {"ok": False, "error": "游戏状态丢失，请重新开始"}

    correct = 0
    detail = []
    for pair in pairs or []:
        try:
            i, j = int(pair[0]), int(pair[1])
        except (TypeError, ValueError, IndexError):
            continue
        right = solution.get(str(i))
        ok = right is not None and right == j
        if ok:
            correct += 1
        detail.append({"i": i, "j": j, "correct": bool(ok)})

    total = max(1, len(solution))
    score = correct * SCORE_CORRECT
    return {"ok": True, "correct": correct, "total": total,
            "score": score, "max_combo": correct, "detail": detail,
            "accuracy": round(correct * 100 / total)}


# ==========================================================================
# 2) Speed Quiz —— 60 秒快速答题
# ==========================================================================
def start_speed_quiz(seed: int | None = None) -> dict:
    seed = int(seed or random.randrange(1, 2 ** 31))
    words = _pick_words(SPEED_QUIZ_QUESTIONS, seed, min_len=3, max_len=10)
    if not words:
        return {"ok": False, "error": "词库不足，无法开始游戏"}

    rng = _question_rng(0, seed)
    questions, answers = [], {}
    for idx, w in enumerate(words):
        # 干扰项：从同批次里取，避免额外查库
        others = [x.meaning_cn for x in words if x.id != w.id]
        rng.shuffle(others)
        opts = others[:3] + [w.meaning_cn]
        rng.shuffle(opts)
        answers[str(idx)] = opts.index(w.meaning_cn)
        questions.append({
            "i": idx, "word": w.word, "phonetic": w.phonetic_uk,
            "options": opts, "audio": w.audio,
        })

    state = {"game": "speed_quiz", "seed": seed, "answers": answers,
             "started_at": _now_ts()}
    view = {"game": "speed_quiz", "seed": seed, "limit_sec": 60,
            "questions": questions}
    return {"ok": True, "view": view, "state": state}


def submit_speed_quiz(state: dict, answers: list, elapsed_sec: float = 60.0) -> dict:
    """answers: [[i, option_index], ...]。用保存的 answers 判分。"""
    saved = (state or {}).get("answers") or {}
    if not saved:
        return {"ok": False, "error": "游戏状态丢失，请重新开始"}

    correct = 0
    combo = best = 0
    for item in answers or []:
        try:
            i, pick = int(item[0]), int(item[1])
        except (TypeError, ValueError, IndexError):
            continue
        ok = saved.get(str(i)) is not None and saved[str(i)] == pick
        if ok:
            correct += 1
            combo += 1
            best = max(best, combo)
        else:
            combo = 0

    total = len(answers or [])
    time_left = max(0.0, 60.0 - float(elapsed_sec or 0))
    score = correct * SCORE_CORRECT + best * SCORE_COMBO_STEP + int(time_left * SPEED_TIME_BONUS)
    return {"ok": True, "correct": correct, "total": total, "score": score,
            "max_combo": best, "time_left": round(time_left, 1),
            "accuracy": round(correct * 100 / total) if total else 0}


# ==========================================================================
# 3) Listening Challenge —— 听音选词
# ==========================================================================
def start_listening(seed: int | None = None) -> dict:
    seed = int(seed or random.randrange(1, 2 ** 31))
    words = _pick_words(LISTENING_QUESTIONS, seed, min_len=3, max_len=9)
    if len(words) < 4:
        return {"ok": False, "error": "词库不足，无法开始游戏"}

    rng = _question_rng(0, seed)
    questions, answers = [], {}
    for idx, w in enumerate(words):
        others = [x.word for x in words if x.id != w.id]
        rng.shuffle(others)
        opts = others[:3] + [w.word]
        rng.shuffle(opts)
        answers[str(idx)] = opts.index(w.word)
        questions.append({"i": idx, "audio": w.audio, "options": opts,
                          "phonetic": w.phonetic_uk})

    state = {"game": "listening_challenge", "seed": seed, "answers": answers,
             "started_at": _now_ts()}
    view = {"game": "listening_challenge", "seed": seed, "limit_sec": 120,
            "questions": questions}
    return {"ok": True, "view": view, "state": state}


def submit_listening(state: dict, answers: list) -> dict:
    saved = (state or {}).get("answers") or {}
    if not saved:
        return {"ok": False, "error": "游戏状态丢失，请重新开始"}
    correct = combo = best = 0
    for item in answers or []:
        try:
            i, pick = int(item[0]), int(item[1])
        except (TypeError, ValueError, IndexError):
            continue
        ok = saved.get(str(i)) is not None and saved[str(i)] == pick
        if ok:
            correct += 1
            combo += 1
            best = max(best, combo)
        else:
            combo = 0
    total = len(answers or [])
    score = correct * SCORE_CORRECT + best * SCORE_COMBO_STEP
    return {"ok": True, "correct": correct, "total": total, "score": score,
            "max_combo": best,
            "accuracy": round(correct * 100 / total) if total else 0}


# ==========================================================================
# 4) Word Builder —— 打乱字母拼词
# ==========================================================================
def start_word_builder(seed: int | None = None) -> dict:
    seed = int(seed or random.randrange(1, 2 ** 31))
    words = _pick_words(BUILDER_QUESTIONS, seed, min_len=4, max_len=8)
    if not words:
        return {"ok": False, "error": "词库不足，无法开始游戏"}

    rng = _question_rng(0, seed)
    questions, answers = [], {}
    for idx, w in enumerate(words):
        letters = list(w.word)
        # 打乱时避免恰好与原词一致（否则白送一题）
        for _ in range(6):
            rng.shuffle(letters)
            if "".join(letters) != w.word:
                break
        answers[str(idx)] = w.word.lower()
        questions.append({
            "i": idx, "letters": letters, "meaning": w.meaning_cn,
            "phonetic": w.phonetic_uk, "length": len(w.word), "audio": w.audio,
        })

    state = {"game": "word_builder", "seed": seed, "answers": answers,
             "started_at": _now_ts()}
    view = {"game": "word_builder", "seed": seed, "limit_sec": 120,
            "questions": questions}
    return {"ok": True, "view": view, "state": state}


def submit_word_builder(state: dict, answers: list) -> dict:
    saved = (state or {}).get("answers") or {}
    if not saved:
        return {"ok": False, "error": "游戏状态丢失，请重新开始"}
    correct = combo = best = 0
    detail = []
    for item in answers or []:
        try:
            i = int(item[0])
            typed = str(item[1]).strip().lower()
        except (TypeError, ValueError, IndexError):
            continue
        want = str(saved.get(str(i)) or "").lower()
        ok = bool(want) and typed == want
        if ok:
            correct += 1
            combo += 1
            best = max(best, combo)
        else:
            combo = 0
        detail.append({"i": i, "typed": typed, "expected": want, "correct": ok})
    total = len(answers or [])
    score = correct * SCORE_CORRECT + best * SCORE_COMBO_STEP
    return {"ok": True, "correct": correct, "total": total, "score": score,
            "max_combo": best, "detail": detail,
            "accuracy": round(correct * 100 / total) if total else 0}


# --------------------------------------------------------------------------
# 统一入口（Route / API 都走这里，避免逻辑重复）
# --------------------------------------------------------------------------
GAME_REGISTRY = {
    "word_match": (start_word_match, submit_word_match),
    "speed_quiz": (start_speed_quiz, submit_speed_quiz),
    "listening_challenge": (start_listening, submit_listening),
    "word_builder": (start_word_builder, submit_word_builder),
}


def start_game(key: str, seed: int | None = None) -> dict:
    if key not in GAME_REGISTRY:
        return {"ok": False, "error": f"未知游戏：{key}"}
    try:
        return GAME_REGISTRY[key][0](seed)
    except Exception as exc:  # pragma: no cover - 保护性兜底
        db.session.rollback()
        return {"ok": False, "error": f"出题失败：{exc}"}


def submit_game(key: str, state: dict, payload: dict) -> dict:
    if key not in GAME_REGISTRY:
        return {"ok": False, "error": f"未知游戏：{key}"}
    fn = GAME_REGISTRY[key][1]
    try:
        if key == "word_match":
            return fn(state, payload.get("pairs") or [])
        if key == "speed_quiz":
            return fn(state, payload.get("answers") or [],
                      payload.get("elapsed_sec") or 60.0)
        return fn(state, payload.get("answers") or [])
    except Exception as exc:  # pragma: no cover
        return {"ok": False, "error": f"判分失败：{exc}"}
