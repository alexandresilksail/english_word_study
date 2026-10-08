"""Podcast / 音频学习 —— 架构预览页。

本阶段**不实现**完整 Podcast 系统，只做两件事：
1. 把数据模型（Channel / Episode / Transcript / Vocabulary / Grammar / Quiz）
   与转换链路讲清楚，让后续开发有明确落点；
2. 若数据库里已有内容（未来导入后），页面会真实渲染 —— 现在为空则显示空状态。

音频一律经 :mod:`media_service` 解析，不写死路径，
将来迁到对象存储 + CDN 只改环境变量。
"""
from __future__ import annotations

from flask import Blueprint, render_template

from media_service import audio_url, media_base_url, provider
from models import PodcastChannel, PodcastEpisode

podcast_bp = Blueprint("podcast", __name__, url_prefix="/podcast")

# 未来的转换链路（展示用，说明一条音频如何变成一套学习材料）
# 每项：(key, en, zh, desc_en, desc_zh, ico) —— 模板用 pick() 按 UI 语言取，
# 保证 English 版面零中文。
PIPELINE = [
    ("audio", "Audio", "音频", "Raw podcast audio, stored on object storage + CDN", "原始播客音频，存对象存储 + CDN", "headphones"),
    ("transcript", "Transcript", "文稿", "Speech-to-text, segmented by timeline", "语音转写，按时间轴切段", "article"),
    ("vocabulary", "Vocabulary", "生词", "High-frequency words drawn from the transcript, linked to the main bank", "从文稿抽取高频生词，关联主词库", "book"),
    ("grammar", "Grammar", "语法", "Grammar points extracted from the transcript with example sentences", "从文稿抽取语法点并配例句", "puzzle"),
    ("listening", "Listening", "听力", "Timeline-based intensive listening & fill-in-the-blank", "基于时间轴的精听与填空", "mic"),
    ("quiz", "Quiz", "测验", "Auto-generated comprehension / vocabulary / grammar questions", "自动生成理解 / 词汇 / 语法题", "quiz"),
]


@podcast_bp.route("/")
def index():
    channels = PodcastChannel.query.order_by(PodcastChannel.id).all()
    episodes = PodcastEpisode.query.order_by(PodcastEpisode.id.desc()).limit(12).all()
    items = [{
        "id": e.id, "title": e.title, "summary": e.summary,
        "level": e.level, "duration_sec": e.duration_sec,
        "audio_url": audio_url(_key(e.media_asset_id), kind="podcast_audio"),
        "channel": e.channel.title if e.channel else "",
    } for e in episodes]

    return render_template("podcast.html", channels=channels, episodes=items,
                           pipeline=PIPELINE,
                           storage={"provider": provider(),
                                    "base": media_base_url() or "(not set - using in-site static files)"})


def _key(asset_id):
    if not asset_id:
        return ""
    from models import MediaAsset
    a = MediaAsset.query.get(asset_id)
    return (a.key or "") if a else ""
