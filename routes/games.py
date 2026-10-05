"""小游戏页面与局内接口（Word Match / Speed Quiz / Listening / Word Builder）。

路由只负责三件事：渲染页面、把 Service 生成的状态放进 session、
把提交转交给 Service 判分。**任何判分逻辑都不写在这里**，
也不写进前端 JS —— 保证 Web / 小程序 / Android 拿到同样的结果。
"""
from __future__ import annotations

from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from gamification import game_stats, overview, recent_games
from game_service import GAME_REGISTRY, start_game, submit_game
from media_service import audio_url
from models import GAMES

games_bp = Blueprint("games", __name__, url_prefix="/games")

GAME_META = {g["key"]: g for g in GAMES}

# 中文名与说明（与 models.GAMES 的 key 一一对应，展示层文案集中在这里）
GAME_TEXT = {
    "word_match": {"zh": "单词配对", "en": "Word Match",
                   "desc_zh": "把英文单词和中文释义连起来",
                   "desc_en": "Match each word with its meaning",
                   "how_zh": "点击左侧单词，再点击右侧释义即可配对；配错会断开重来。",
                   "how_en": "Tap a word, then tap its meaning to link them."},
    "speed_quiz": {"zh": "限时抢答", "en": "Speed Quiz",
                   "desc_zh": "60 秒内能答对多少题",
                   "desc_en": "Answer as many as you can in 60 seconds",
                   "how_zh": "每题 4 选 1，连对有额外加成；剩余秒数也会加分。",
                   "how_en": "Four options each; combos and remaining time add bonus."},
    "listening_challenge": {"zh": "听音挑战", "en": "Listening Challenge",
                            "desc_zh": "听英式发音选出正确单词",
                            "desc_en": "Listen and pick the right word",
                            "how_zh": "点击喇叭播放发音，从 4 个选项里选出听到的单词。",
                            "how_en": "Play the audio, then choose the word you heard."},
    "word_builder": {"zh": "字母拼词", "en": "Word Builder",
                     "desc_zh": "打乱的字母拼回正确单词",
                     "desc_en": "Unscramble the letters into a word",
                     "how_zh": "根据中文释义和音标，把打乱的字母拼成正确单词。",
                     "how_en": "Use the meaning and phonetic to rebuild the word."},
}


def _game_cards():
    stats = game_stats(current_user.id)
    cards = []
    for g in GAMES:
        s = stats.get(g["key"])
        txt = GAME_TEXT.get(g["key"], {})
        cards.append({
            **g, **txt,
            "url": url_for("games.play", key=g["key"]),
            "best": s.best_score if s else 0,
            "plays": s.plays if s else 0,
            "accuracy": s.accuracy if s else 0,
        })
    return cards


@games_bp.route("/")
@login_required
def index():
    return render_template("games.html",
                           games=_game_cards(),
                           recent=recent_games(current_user.id, 5),
                           gamification=overview(current_user.id))


@games_bp.route("/<key>")
@login_required
def play(key: str):
    if key not in GAME_REGISTRY:
        return redirect(url_for("games.index"))
    meta = GAME_META.get(key, {})
    return render_template("game_play.html",
                           key=key, meta=meta, text=GAME_TEXT.get(key, {}),
                           gamification=overview(current_user.id))


# --------------------------------------------------------------------------
# 局内接口（页面 JS 调用；判分逻辑全在 game_service）
# --------------------------------------------------------------------------
@games_bp.post("/api/<key>/start")
@login_required
def api_start(key: str):
    if key not in GAME_REGISTRY:
        return jsonify(ok=False, error="未知游戏"), 404
    body = request.get_json(silent=True) or {}
    res = start_game(key, body.get("seed"))
    if not res.get("ok"):
        return jsonify(ok=False, error=res.get("error") or "开局失败"), 500
    session["game"] = res["state"]          # 保存判题状态
    session.modified = True
    view = dict(res["view"])
    # 音频地址一律经 media_service 解析（将来切 CDN 不改前端）
    probe = audio_url("__probe__.mp3")
    view["audio_base"] = probe.rsplit("/", 1)[0] if "/" in probe else "/static/audio"
    return jsonify(ok=True, q=view)


@games_bp.post("/api/<key>/submit")
@login_required
def api_submit(key: str):
    if key not in GAME_REGISTRY:
        return jsonify(ok=False, error="未知游戏"), 404
    state = session.get("game") or {}
    if state.get("game") != key:
        return jsonify(ok=False, error="游戏状态已失效，请重新开始"), 409

    body = request.get_json(silent=True) or {}
    res = submit_game(key, state, body)
    if not res.get("ok"):
        return jsonify(ok=False, error=res.get("error") or "判分失败"), 400

    from gamification import record_game, xp_for_game

    xp = xp_for_game(key, res)
    saved = record_game(
        current_user.id, key, score=res.get("score", 0),
        correct=res.get("correct", 0), total=res.get("total", 0) or 1,
        max_combo=res.get("max_combo", 0),
        duration_sec=int(body.get("elapsed_sec") or 0), xp=xp,
        skill="listening" if key == "listening_challenge" else "vocabulary",
    )
    session.pop("game", None)
    session.modified = True
    return jsonify(ok=True, result={**res, **saved})
