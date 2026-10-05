"""游戏化服务层：XP / 等级 / 连续学习天数 / 每日目标。

设计原则
--------
1. **纯函数 + 单一写入口**：等级公式只存在于本文件，前端与 API 都调用
   :func:`level_info`，避免公式散落在多处后不一致。
2. **增量安全**：所有统计落在新建的 user_stats / skill_stats 表，
   不改动 users 表，老用户首次访问时自动补一行，历史数据零影响。
3. **跨端一致**：未来小程序 / Android / macOS 调用同一 REST API，
   拿到的 XP 与等级必然一致（因为都走这里）。
"""
from __future__ import annotations

from datetime import date

from extensions import db
from models import GameRecord, GameStat, SkillStat, UserStat, utcnow
from services import today_start

# --------------------------------------------------------------------------
# 等级曲线
# --------------------------------------------------------------------------
# 第 N 级所需累计 XP：120 * N^1.35（取整到 10），前期升级快、后期稳定，
# 保证新手几分钟就能升 2 级获得正反馈，又不会几个月满级。
LEVEL_BASE = 120
LEVEL_EXP = 1.35


def xp_for_level(level: int) -> int:
    """升到指定等级所需的**累计** XP（level 1 为 0）。"""
    if level <= 1:
        return 0
    return int(round(LEVEL_BASE * ((level - 1) ** LEVEL_EXP) / 10.0) * 10)


def level_info(xp: int) -> dict:
    """由累计 XP 推导等级、升级进度。

    返回结构固定，供 HTML 与 REST API 共用：::

        {"level": 3, "xp": 340, "xp_into_level": 60,
         "xp_for_next": 160, "level_percent": 37,
         "xp_to_next": 100, "title": "Explorer"}
    """
    xp = max(0, int(xp or 0))
    level = 1
    while level < 999 and xp >= xp_for_level(level + 1):
        level += 1
    base = xp_for_level(level)
    nxt = xp_for_level(level + 1)
    span = max(1, nxt - base)
    into = xp - base
    return {
        "level": level,
        "xp": xp,
        "xp_into_level": into,
        "xp_for_next": nxt,
        "xp_to_next": max(0, nxt - xp),
        "level_percent": min(100, round(into * 100 / span)),
        "title": level_title(level),
    }


LEVEL_TITLES = [
    (1, "新手上路", "Starter"), (3, "进阶学徒", "Apprentice"),
    (6, "词汇探索者", "Explorer"), (10, "流利旅人", "Traveller"),
    (15, "表达达人", "Speaker"), (22, "双语玩家", "Bilingual"),
    (30, "语言大师", "Master"), (45, "传奇学者", "Legend"),
]


def level_title(level: int) -> str:
    name = LEVEL_TITLES[0][1]
    for min_lv, zh, _en in LEVEL_TITLES:
        if level >= min_lv:
            name = zh
    return name


# --------------------------------------------------------------------------
# 读写
# --------------------------------------------------------------------------
def get_stat(user_id: int) -> UserStat:
    """取用户统计行，不存在则创建（首次访问的老用户自动补齐）。"""
    st = UserStat.query.filter_by(user_id=user_id).first()
    if not st:
        st = UserStat(user_id=user_id)
        db.session.add(st)
        db.session.commit()
    return st


def _today_str() -> str:
    return today_start().strftime("%Y-%m-%d")


def _roll_day(st: UserStat, active: bool = False) -> None:
    """跨天滚动：重置今日进度；（有学习活动时才）结算连续天数。

    ``active=False``（仅读取总览）只滚动日计数器，不会凭空制造 Streak ——
    否则用户一打开 Dashboard 就"连续学习 1 天"是不诚实的。
    """
    today = _today_str()
    if st.today_date == today:
        return

    prev = st.last_active_date
    gap = None
    if prev:
        try:
            gap = (today_start().date() - date.fromisoformat(prev)).days
        except ValueError:
            gap = None

    # 只有真的产生了学习活动，才推进 Streak
    if active:
        if gap == 1:
            st.streak_days = (st.streak_days or 0) + 1
        elif gap is None or gap > 1 or gap <= 0 and not st.streak_days:
            st.streak_days = 1
        st.best_streak = max(st.best_streak or 0, st.streak_days or 0)
        st.last_active_date = today

    # gap == 0 说明今天已有活动记录、只是日计数器还没滚动，此时不能清零进度
    if prev != today:
        st.today_done = 0
    st.today_date = today


def add_xp(user_id: int, xp: int, skill: str | None = None,
           items: int = 0, minutes: int = 0) -> dict:
    """增加 XP 并同步等级 / 连续天数 / 今日目标 / 技能统计。

    返回本次的等级信息（含是否升级），便于前端放一个小动画。
    """
    xp = max(0, int(xp or 0))
    st = get_stat(user_id)
    before = st.level

    _roll_day(st, active=xp > 0 or items > 0)
    st.xp = (st.xp or 0) + xp
    st.today_done = (st.today_done or 0) + max(1, items or (1 if xp else 0))
    st.last_active_date = _today_str()
    st.updated_at = utcnow()

    info = level_info(st.xp)
    st.level = info["level"]

    if skill:
        bump_skill(user_id, skill, xp=xp, items=items, minutes=minutes)

    db.session.commit()
    info["leveled_up"] = st.level > before
    info["streak"] = st.streak_days or 0
    return info


def bump_skill(user_id: int, skill: str, xp: int = 0, items: int = 0,
               minutes: int = 0) -> SkillStat:
    row = SkillStat.query.filter_by(user_id=user_id, skill=skill).first()
    if not row:
        row = SkillStat(user_id=user_id, skill=skill)
        db.session.add(row)
    row.xp = (row.xp or 0) + max(0, int(xp))
    row.items = (row.items or 0) + max(0, int(items))
    row.minutes = (row.minutes or 0) + max(0, int(minutes))
    row.last_at = utcnow()
    return row


def skill_stats(user_id: int) -> dict[str, SkillStat]:
    return {r.skill: r for r in SkillStat.query.filter_by(user_id=user_id).all()}


def overview(user_id: int) -> dict:
    """Dashboard / API 共用的游戏化总览。"""
    st = get_stat(user_id)
    _roll_day(st)
    db.session.commit()
    info = level_info(st.xp)
    info.update({
        "streak": st.streak_days or 0,
        "best_streak": st.best_streak or 0,
        "daily_goal": st.daily_goal or 20,
        "today_done": st.today_done or 0,
        "goal_percent": st.today_goal_percent,
        "total_games": st.total_games or 0,
    })
    return info


def set_goal(user_id: int, goal: int) -> int:
    st = get_stat(user_id)
    st.daily_goal = max(5, min(200, int(goal or 20)))
    db.session.commit()
    return st.daily_goal


# --------------------------------------------------------------------------
# 小游戏成绩
# --------------------------------------------------------------------------
def record_game(user_id: int, game_key: str, score: int, correct: int,
                total: int, max_combo: int = 0, duration_sec: int = 0,
                xp: int = 0, skill: str = "vocabulary") -> dict:
    """写入一次游戏成绩，并更新累计统计 + XP。"""
    rec = GameRecord(user_id=user_id, game_key=game_key, score=int(score),
                     xp_earned=int(xp), correct=int(correct), total=int(total),
                     max_combo=int(max_combo), duration_sec=int(duration_sec))
    db.session.add(rec)

    g = GameStat.query.filter_by(user_id=user_id, game_key=game_key).first()
    if not g:
        g = GameStat(user_id=user_id, game_key=game_key)
        db.session.add(g)
    g.plays = (g.plays or 0) + 1
    g.best_score = max(g.best_score or 0, int(score))
    g.total_xp = (g.total_xp or 0) + int(xp)
    g.total_correct = (g.total_correct or 0) + int(correct)
    g.total_questions = (g.total_questions or 0) + int(total)
    g.best_combo = max(g.best_combo or 0, int(max_combo))
    g.last_played_at = utcnow()

    st = get_stat(user_id)
    st.total_games = (st.total_games or 0) + 1
    db.session.commit()

    info = add_xp(user_id, xp, skill=skill, items=max(correct, 1))
    return {
        "record_id": rec.id,
        "score": rec.score,
        "xp_earned": rec.xp_earned,
        "accuracy": rec.accuracy,
        "max_combo": rec.max_combo,
        "best_score": g.best_score,
        "plays": g.plays,
        "is_new_best": rec.score >= (g.best_score or 0),
        "level_info": info,
    }


def xp_for_game(game_key: str, result: dict) -> int:
    """小游戏 XP 规则（集中一处，Web / API / 未来客户端共用）。

    得分越高 XP 越多；一题没答对只给象征性的参与分，避免空刷。
    """
    correct = int(result.get("correct") or 0)
    if correct <= 0:
        return 2
    base = {"word_match": 20, "speed_quiz": 25,
            "listening_challenge": 20, "word_builder": 25}.get(game_key, 15)
    acc = int(result.get("accuracy") or 0)
    bonus = 10 if acc >= 80 else (5 if acc >= 60 else 0)
    return base + bonus


def game_stats(user_id: int) -> dict[str, GameStat]:
    return {r.game_key: r for r in GameStat.query.filter_by(user_id=user_id).all()}


def recent_games(user_id: int, limit: int = 5) -> list[GameRecord]:
    return (GameRecord.query.filter_by(user_id=user_id)
            .order_by(GameRecord.played_at.desc()).limit(limit).all())


def leaderboard_game(user_id: int, game_key: str) -> dict:
    """个人最佳榜：本人最佳 + 全站排名（不暴露他人邮箱，只显示名次与分数）。"""
    g = GameStat.query.filter_by(user_id=user_id, game_key=game_key).first()
    my_best = g.best_score if g else 0
    rank = None
    total_players = GameStat.query.filter_by(game_key=game_key).count()
    if my_best > 0:
        rank = (GameStat.query.filter(GameStat.game_key == game_key,
                                      GameStat.best_score > my_best).count() + 1)
    return {"my_best": my_best, "rank": rank, "players": total_players}


def active_days(user_id: int, days: int = 7) -> list[str]:
    """最近 N 天是否有学习（用于 Streak 火焰条）。"""
    from services import daily_trend
    return [d["day"] for d in daily_trend(user_id, days) if d["total"] > 0]
