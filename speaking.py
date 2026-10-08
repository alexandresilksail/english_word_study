"""V5.8 口语（Speaking）评分服务（顶层模块，勿放 services/ 包）。

.. warning:: **不要**移进 ``services/`` 包（根目录已有顶层 ``services.py``，
   同名包会遮蔽它并导致整站崩溃）。

设计要点
--------
* **STT / TTS / 发音评分**三条通道各有 ``mock`` 与 ``provider`` 两种实现，
  默认均为 mock。真实实现走 HTTP（OpenAI 兼容接口），用标准库 ``urllib``，
  **不引入 SDK、不引入 PyTorch / TensorFlow 等重型组件**。
* **诚实原则（本项目贯穿约束）**：

  - mock 模式下 ``transcribe()`` **不编造识别结果**，而是明确返回「不可用」，
    前端据此切换到「手动输入你说的句子」，功能仍然可用但不假装识别成功。
  - 评分只给出**真能测出来的维度**。用 stdlib ``difflib`` 做词对齐可以诚实
    算出 accuracy（说对了多少词）与 vocabulary（用词覆盖度）；而 fluency
    （流畅度需要音频时长/停顿特征）与 grammar（需要语法解析）在此模式下
    **一律返回 None**，并标明「练习模式（Practice Mode）」，绝不编数字。
* 听力练习复用站内既有音频（``ContentItem.audio`` / ``media_service``），
  不新增 2000 个 MP3 之外的静态资源。

.: 评分口径
    accuracy   = 词对齐后「说对的词 / 参考句词数」
    vocabulary = 参考句内容词被覆盖的比例（去除停用词后的重合度）
    fluency    = None（需音频特征）
    grammar    = None（需语法解析）
    overall    = 仅对可用维度取平均，避免被未测维度稀释
"""
from __future__ import annotations

import difflib
import json
import logging
import os
import re
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------
PROVIDER = (os.environ.get("SPEECH_PROVIDER") or "mock").strip().lower()
API_KEY = (os.environ.get("SPEECH_API_KEY") or "").strip()
BASE_URL = (os.environ.get("SPEECH_BASE_URL") or "https://api.openai.com/v1").strip()
STT_MODEL = (os.environ.get("SPEECH_STT_MODEL") or "whisper-1").strip()
TTS_MODEL = (os.environ.get("SPEECH_TTS_MODEL") or "gpt-4o-mini-tts").strip()
TTS_VOICE = (os.environ.get("SPEECH_TTS_VOICE") or "alloy").strip()
TIMEOUT = int(os.environ.get("SPEECH_TIMEOUT") or "30")
MAX_TEXT_CHARS = int(os.environ.get("SPEECH_MAX_TEXT_CHARS") or "1000")
MAX_AUDIO_BYTES = int(os.environ.get("SPEECH_MAX_AUDIO_BYTES") or str(8 * 1024 * 1024))

# 评分维度（语境里可得 / 不可得）
DIMENSIONS = ("accuracy", "fluency", "grammar", "vocabulary")

_STOPWORDS = {
    "en": {"a", "an", "the", "is", "are", "was", "were", "am", "be", "been",
           "to", "of", "in", "on", "at", "for", "with", "and", "or", "but",
           "do", "does", "did", "i", "you", "he", "she", "it", "we", "they",
           "this", "that", "there", "here", "my", "your", "his", "her"},
    "zh": {"的", "了", "是", "在", "我", "你", "他", "她", "它", "们",
           "和", "与", "就", "都", "也", "很", "有", "吗", "呢", "吧", "啊"},
}

_WORD_RE = re.compile(r"[A-Za-z0-9']+|[一-鿿]")

#: 站内既有 2000 个 MP3 的目录（它们不进 Git / 镜像，见 .gitignore）
STATIC_AUDIO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "app", "static", "audio")


def _safe_name(name: str) -> bool:
    """文件名合法性校验：禁止路径穿越，也禁止非 mp3 后缀。"""
    if not name or ".." in name or "/" in name or "\\" in name:
        return False
    return name.lower().endswith(".mp3")


def _exists(name: str) -> bool:
    return _safe_name(name) and os.path.isfile(os.path.join(STATIC_AUDIO_DIR, name))


def audio_for_word(word_obj) -> str | None:
    """解析一个 Word 的可播放音频文件名。

    既有 2000 个 MP3 挂在 **Word**（legacy 单词库）上，命名是 ``word.mp3``；
    而 ``ContentItem.surface`` 多为短语与粤语词条，与 MP3 几乎无交集，
    按 surface 猜文件名会命中 0 个 —— 所以听力素材必须走 Word。
    """
    direct = (getattr(word_obj, "audio", "") or "").strip()
    if _exists(direct):
        return direct
    guess = (getattr(word_obj, "word", "") or "").strip().lower() + ".mp3"
    return guess if _exists(guess) else None


def listening_items(lang_code: str = "en", limit: int = 12) -> list[dict]:
    """听力练习素材：复用站内既有 2000 个 MP3，只返回**磁盘上真实存在**的条目。

    逐个校验文件存在，避免前端拿到一个必然 404 的链接。
    音频资源只有英文词库一份，其它语种返回空列表（前端显示空态即可）。
    """
    if lang_code != "en":
        return []

    from models import Word

    rows = (Word.query.filter(Word.audio != "")
            .order_by(Word.word.asc()).limit(max(limit * 5, 50)).all())

    out: list[dict] = []
    for w in rows:
        name = audio_for_word(w)
        if not name:
            continue
        try:
            from media_service import audio_url
            url = audio_url(name)
        except Exception:  # pragma: no cover - 无请求上下文
            url = f"/static/audio/{name}"
        out.append({
            "word_id": w.id,
            # 模板要用它拼 DOM id。听力素材来自 legacy Word 而不是
            # ContentItem，所以这里必须有自己的 id —— 沿用 content_id 会拿到
            # None，结果多张卡片共用 id="lm-"，点「显示答案」永远只翻开第一张。
            "id": f"w{w.id}",
            "surface": w.word,
            "phonetic": w.phonetic_uk or "",
            "meaning_en": "",
            "meaning_cn": w.meaning_cn or "",
            "example_en": w.example_en or "",
            "audio_url": url,
        })
        if len(out) >= limit:
            break
    return out


def _tokenize(text: str, lang: str = "en") -> list[str]:
    s = (text or "").lower()
    toks = _WORD_RE.findall(s)
    return toks if lang != "zh" else list(s.replace(" ", ""))


def status() -> dict:
    """三条通道的可用状态；UI 据此决定是否显示「练习模式」。"""
    got = bool(API_KEY) and PROVIDER in ("openai",)
    return {
        "provider": PROVIDER,
        "stt": got,
        "tts": got,
        "pronunciation": got,
        "mock": not got,
        "practice_mode": not got,
        "dimensions": list(DIMENSIONS),
        # 明确告知前端哪些维度是真实可测的
        "measurable_dimensions": ["accuracy", "vocabulary"],
        "unavailable_dimensions": ["fluency", "grammar"],
    }


def is_practice_mode() -> bool:
    return bool(status()["practice_mode"])


# --------------------------------------------------------------------------
# STT：语音转文字
# --------------------------------------------------------------------------
def transcribe(audio_bytes: bytes, lang: str = "en", **kw) -> dict:
    """把音频转成文字。

    mock 模式下**明确返回不可用**，而不是编一段听起来像样的结果 ——
    前端会切到「手动输入」路径，用户照样能练，但不会被误导说识别成功。
    """
    if audio_bytes and len(audio_bytes) > MAX_AUDIO_BYTES:
        return {"ok": False, "available": False,
                "error": f"音频过大（上限 {MAX_AUDIO_BYTES // 1024 // 1024}MB）"}
    if not audio_bytes:
        return {"ok": False, "available": False, "error": "未收到音频数据"}

    st = status()
    if not st["stt"]:
        return {"ok": True, "available": False, "mock": True, "text": None,
                "note": "未配置语音识别引擎，请在下方手动输入你说的内容"}

    try:
        text = _call_stt(audio_bytes, lang)
    except Exception as exc:
        logger.warning("STT 调用失败：%s", type(exc).__name__)
        return {"ok": False, "available": True, "mock": False,
                "error": "语音识别服务暂时不可用，请稍后再试"}
    return {"ok": True, "available": True, "mock": False, "text": text}


def _call_stt(audio_bytes: bytes, lang: str) -> str:
    """OpenAI 兼容的 multipart/form-data 上传（手写 multipart，避免引入 SDK）。"""
    boundary = "----formboundary-workbuddy"
    filename = "speech.webm"

    def field(name: str, value: str) -> bytes:
        return (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
                f"{value}\r\n").encode("utf-8")

    def file_part(name: str, fn: str, data: bytes) -> bytes:
        head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; "
                f"filename=\"{fn}\"\r\nContent-Type: application/octet-stream\r\n\r\n")
        return head.encode("utf-8") + data + b"\r\n"

    body = b"".join([
        file_part("file", filename, audio_bytes),
        field("model", STT_MODEL),
        field("language", (lang or "en")[:8]),
        f"--{boundary}--\r\n".encode("utf-8"),
    ])
    req = urllib.request.Request(
        f"{BASE_URL}/audio/transcriptions", data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                 "Authorization": f"Bearer {API_KEY}"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError:
        raise RuntimeError("stt_http_error") from None
    try:
        return data["text"]
    except (KeyError, TypeError):
        raise RuntimeError("bad_stt_response") from None


# --------------------------------------------------------------------------
# TTS：文字转语音
# --------------------------------------------------------------------------
def synthesize(text: str, lang: str = "en") -> dict:
    """生成参考读音。

    mock 模式下不生成音频，返回不可用；前端改用**浏览器内置**语音合成
    （Web Speech API）——那是真实 TTS，不占服务器资源，也不需要额外依赖。
    """
    text = (text or "").strip()[:MAX_TEXT_CHARS]
    if not text:
        return {"ok": False, "available": False, "error": "文本为空"}

    st = status()
    if not st["tts"]:
        return {"ok": True, "available": False, "mock": True, "audio": None,
                "hint": "browser-speech",
                "note": "未配置语音合成引擎，已使用浏览器内置朗读"}

    try:
        audio = _call_tts(text, lang)
    except Exception as exc:
        logger.warning("TTS 调用失败：%s", type(exc).__name__)
        return {"ok": False, "available": True, "mock": False,
                "error": "语音合成服务暂时不可用"}
    return {"ok": True, "available": True, "mock": False, "audio_base64": audio}


def _call_tts(text: str, lang: str) -> str:
    import base64

    payload = {"model": TTS_MODEL, "voice": TTS_VOICE, "input": text,
               "response_format": "mp3"}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE_URL}/audio/speech", data=data, method="POST",
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {API_KEY}"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return base64.b64encode(resp.read()).decode("ascii")
    except urllib.error.HTTPError:
        raise RuntimeError("tts_http_error") from None


# --------------------------------------------------------------------------
# 评分（可以在无引擎时诚实工作的部分）
# --------------------------------------------------------------------------
def align_words(user_text: str, reference: str, lang: str = "en") -> list[dict]:
    """词级对齐，给出每个参考词的判定：ok / wrong / missing / extra。"""
    user_toks = _tokenize(user_text, lang)
    ref_toks = _tokenize(reference, lang)
    sm = difflib.SequenceMatcher(a=ref_toks, b=user_toks)
    rows: list[dict] = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                rows.append({"expected": ref_toks[i1 + k], "given": user_toks[j1 + k],
                             "status": "ok"})
        elif tag == "replace":
            for k in range(i2 - i1):
                rows.append({"expected": ref_toks[i1 + k],
                             "given": user_toks[j1 + k] if j1 + k < j2 else "",
                             "status": "wrong"})
        elif tag == "delete":
            for k in range(i1, i2):
                rows.append({"expected": ref_toks[k], "given": "", "status": "missing"})
        elif tag == "insert":
            for k in range(j1, j2):
                rows.append({"expected": "", "given": user_toks[k], "status": "extra"})
    return rows


def score(user_text: str, reference: str, lang: str = "en") -> dict:
    """口语评分。

    诚实口径：accuracy / vocabulary 由词对齐真实算出；
    fluency / grammar 在无评分引擎时为 ``None``（不是 0）——
    0 会让人以为「流利度极差」，而真相是「没测」。
    """
    user_text = (user_text or "").strip()
    reference = (reference or "").strip()
    practice = is_practice_mode()

    if not reference:
        return {"ok": False, "error": "缺少参考答案"}

    rows = align_words(user_text, reference, lang)
    ref_words = [r["expected"] for r in rows if r["status"] != "extra"]
    hit = sum(1 for r in rows if r["status"] == "ok")

    accuracy = round(hit * 100 / len(ref_words)) if ref_words else 0

    stops = _STOPWORDS.get(lang, set())
    content = [w for w in ref_words if w.lower() not in stops]
    got_tokens = {t.lower() for t in _tokenize(user_text, lang)}
    covered = sum(1 for w in content if w.lower() in got_tokens)
    vocabulary = round(covered * 100 / len(content)) if content else accuracy

    measured = {"accuracy": accuracy, "vocabulary": vocabulary}
    overall = round(sum(measured.values()) / len(measured))

    missing = [r["expected"] for r in rows if r["status"] == "missing"]
    extra = [r["given"] for r in rows if r["status"] == "extra"]
    wrong = [(r["given"], r["expected"]) for r in rows if r["status"] == "wrong"]

    feedback = []
    if missing:
        feedback.append(f"漏读了：{' / '.join(missing[:6])}")
    if extra:
        feedback.append(f"多读了：{' / '.join(extra[:6])}")
    if wrong:
        feedback.append("发音/用词需核对：" + "、".join(f"{g}→{e}" for g, e in wrong[:4]))
    if not feedback:
        feedback.append("与参考句完全一致 👍")

    return {
        "ok": True,
        "reference": reference,
        "transcript": user_text,
        "accuracy": accuracy,
        "vocabulary": vocabulary,
        # 这两个维度分别需要「音频时长/停顿特征」与「语法解析器」，
        # 本项目不引入重型组件，因此恒为 None（不是 0）。
        # 接入评分引擎后在此替换为真实值，其余字段无需改动。
        "fluency": None,
        "grammar": None,
        "overall": overall,
        "alignment": rows,
        "feedback": feedback,
        "practice_mode": practice,
        "measured_dimensions": ["accuracy", "vocabulary"],
        "note": ("练习模式：流利度与语法需接入评分引擎后方可评估，此处不做编造"
                 if practice else None),
    }
