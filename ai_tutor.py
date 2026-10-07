"""V5.7 AI Tutor 服务层（顶层模块，勿放 services/ 包）。

.. warning:: **不要**把它移进 ``services/`` 包。工程根目录已有一个顶层
    ``services.py`` 模块，同名的包会在 import 时把它遮蔽掉，导致整站崩溃。

设计要点
--------
* **Provider 适配器**：``mock``（默认）/ ``openai`` / ``anthropic``。
  三者返回统一结构，上层与 UI 不需要知道用的是哪家。
* **零新增依赖**：HTTP 走标准库 ``urllib``，不引入各家 SDK —— 保持 Docker
  镜像轻量，符合 2vCPU-2GB 的部署约束与「不引入重型组件」的边界。
* **诚实原则**：没有配置 ``AI_API_KEY`` 就绝不像真的一样编造回答——
  走 :func:`_mock_*` 给出基于**本地真实数据**（ContentItem）的规则化结果，
  并在返回值里标注 ``"mock": True``，UI 必须如实显示「本地规则模式」。
* **失败不假装成功**：真实 provider 出错时返回 ``ok=False`` 与可读原因，
  不会悄悄退化成 mock 冒充 AI 回答。

安全
----
* API Key 只从环境变量读取，绝不写入代码 / 日志 / 返回值；
* 入参长度截断 + 控制字符清理 + 提示注入标记剥离（"ignore previous..."等）；
* system prompt 由服务端固定，用户文本永远只进 user 角色；
* 网络请求带超时，异常信息里不回显请求头（含 Authorization）。
"""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request

from models import ContentItem

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------
# 配置（全部来自环境变量，代码中不出现任何默认值密钥）
# --------------------------------------------------------------------------
PROVIDER = (os.environ.get("AI_PROVIDER") or "mock").strip().lower()
API_KEY = (os.environ.get("AI_API_KEY") or "").strip()
AI_BASE_URL = (os.environ.get("AI_BASE_URL") or "").strip()
TIMEOUT = int(os.environ.get("AI_TIMEOUT") or "20")
MAX_INPUT_CHARS = int(os.environ.get("AI_MAX_INPUT_CHARS") or "2000")

_DEFAULT_MODELS = {
    "openai": "gpt-4o-mini",
    "anthropic": "claude-3-5-haiku-latest",
    "mock": "rule-based-local",
}
MODEL = (os.environ.get("AI_MODEL") or "").strip() or _DEFAULT_MODELS.get(PROVIDER, "rule-based-local")

#: 六类能力
ACTIONS = ("explain", "example", "conversation", "correct", "translate", "practice")

_BASE_URLS = {
    "openai": AI_BASE_URL or "https://api.openai.com/v1",
    "anthropic": AI_BASE_URL or "https://api.anthropic.com/v1",
}

# 提示注入常见套路（剥离而非报错，避免用户正常句子里含这些词就被拒）
_INJECTION_RE = re.compile(
    r"(ignore\s+(all\s+)?(previous|above|prior)\s+instructions?|"
    r"disregard\s+(all\s+)?(previous|prior)\s+instructions?|"
    r"you\s+are\s+now\s+|system\s*prompt|reveal\s+(your\s+)?(api\s*key|secret))",
    re.I)


# --------------------------------------------------------------------------
# 输入净化
# --------------------------------------------------------------------------
def sanitize(text: str, max_chars: int | None = None) -> str:
    """剥离控制字符、压缩空白、截断长度，并去掉提示注入痕迹。"""
    s = "" if text is None else str(text)
    s = "".join(ch if ch == "\n" or ord(ch) >= 32 else " " for ch in s)
    s = _INJECTION_RE.sub("[filtered]", s)
    s = re.sub(r"[ \t]{3,}", "  ", s).strip()
    limit = max_chars or MAX_INPUT_CHARS
    if len(s) > limit:
        s = s[:limit] + " …[truncated]"
    return s


def status() -> dict:
    """当前 AI 能力状态（UI 用它如实显示「本地规则模式」还是「已接模型」）。

    只在**真正接通**时才汇报真实模型名：若设了 ``AI_MODEL`` 但没配 key，
    不能再把 ``gpt-4o-mini`` 之类名字显示出来——那会让用户以为在用大模型。
    """
    configured = bool(API_KEY) and PROVIDER in ("openai", "anthropic")
    model = MODEL if configured else _DEFAULT_MODELS["mock"]
    return {
        "provider": PROVIDER if configured else "mock",
        "model": model,
        "enabled": configured,      # 是否真的接了模型
        "mock": not configured,     # 是否处于本地规则模式
        "actions": list(ACTIONS),
    }


def is_mock() -> bool:
    return not status()["enabled"]


# --------------------------------------------------------------------------
# Provider 调用
# --------------------------------------------------------------------------
def _tutor(action: str, user_prompt: str, raw_text: str, lang: str) -> dict:
    """统一入口：校验 → 分派 provider / mock。

    :param user_prompt: 拼好的 prompt（可能带 ``"Explain: "`` 之类前缀）
    :param raw_text:    用户**原始**输入。mock 查词必须用它 —— 若拿拼好的
                        prompt 去查，永远匹配不到站内词条。
    """
    if not (raw_text or "").strip():
        # 必须在拼 prompt **之前**拦掉：光看 user_prompt 永远不会为空。
        return {"ok": False, "action": action, "mock": True, "error": "请输入内容"}

    user = sanitize(user_prompt)
    st = status()
    if not st["enabled"]:
        return {"ok": True, "action": action, "model": st["model"],
                "provider": "mock", "mock": True,
                "output": _mock_answer(action, sanitize(raw_text), lang),
                "note": "本地规则模式（未配置 AI_API_KEY），结果由站内数据生成，非大模型回答"}

    system = _SYSTEM[action]
    try:
        if PROVIDER == "openai":
            raw = _call_openai(system, user)
        elif PROVIDER == "anthropic":
            raw = _call_anthropic(system, user)
        else:  # pragma: no cover - status() 已拦截
            return _fail(action, f"不支持的 provider：{PROVIDER}")
    except Exception as exc:
        # 注意：不要把请求体 / 请求头（含 Authorization）回显给用户
        logger.warning("AI provider %s 调用失败（action=%s）：%s",
                       PROVIDER, action, type(exc).__name__)
        return _fail(action, "AI 服务暂时不可用，请稍后再试")

    return {"ok": True, "action": action, "model": st["model"],
            "provider": PROVIDER, "mock": False,
            "output": sanitize(raw, max_chars=4000)}


def _fail(action: str, message: str) -> dict:
    return {"ok": False, "action": action, "error": message,
            "provider": PROVIDER, "mock": True}


def _http_json(url: str, payload: dict, headers: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # 只取状态码，不回显请求内容，避免任何敏感信息外泄
        raise RuntimeError(f"http_{exc.code}") from None


def _openai_text(data: dict) -> str:
    """从 OpenAI 风格响应里取文本；结构不符抛受控 RuntimeError。"""
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("bad_openai_response") from None


def _anthropic_text(data: dict) -> str:
    """从 Anthropic 风格响应里取文本；结构不符抛受控 RuntimeError。"""
    try:
        return data["content"][0]["text"]
    except (KeyError, IndexError, TypeError):
        raise RuntimeError("bad_anthropic_response") from None


def _call_openai(system: str, user: str) -> str:
    headers = {"Content-Type": "application/json",
               "Authorization": f"Bearer {API_KEY}"}
    payload = {"model": MODEL, "temperature": 0.4,
               "messages": [{"role": "system", "content": system},
                            {"role": "user", "content": user}]}
    data = _http_json(f"{_BASE_URLS['openai']}/chat/completions", payload, headers)
    return _openai_text(data)


def _call_anthropic(system: str, user: str) -> str:
    headers = {"Content-Type": "application/json",
               "x-api-key": API_KEY, "anthropic-version": "2023-06-01"}
    payload = {"model": MODEL, "max_tokens": 800, "system": system,
               "messages": [{"role": "user", "content": user}]}
    data = _http_json(f"{_BASE_URLS['anthropic']}/messages", payload, headers)
    return _anthropic_text(data)


# --------------------------------------------------------------------------
# 本地规则模式：基于站内真实数据的确定性回答（不假装来自大模型）
# --------------------------------------------------------------------------
def _lookup(term: str) -> ContentItem | None:
    term = (term or "").strip()
    if not term:
        return None
    item = ContentItem.query.filter(ContentItem.surface.ilike(term)).first()
    if item:
        return item
    first = term.split()[0].strip(",.;!?\"'")
    return ContentItem.query.filter(ContentItem.surface.ilike(first)).first()


def _mock_answer(action: str, text: str, lang: str) -> str:
    en = (lang == "en")
    item = _lookup(text)

    if action == "explain":
        if not item:
            return ("No local entry found for this term yet. You can still open the lesson to learn it."
                    if en else
                    "站内暂未收录该词条。可打开对应课时学习后，这里会给出基于站内数据的解释。")
        parts = [f"**{item.surface}**"]
        if item.phonetic:
            parts.append(f"/{item.phonetic}/")
        meaning = item.meaning_en if en else (item.meaning_cn or item.meaning_en)
        if meaning:
            parts.append(f"— {meaning}")
        if item.example_en:
            parts.append(f"\n\nExample: {item.example_en}")
        tip = ("\n\n(Local rule-based result, not a large-model answer.)" if en
               else "\n\n（本地规则结果，非大模型回答。）")
        return " ".join(parts[:2]) + " ".join(parts[2:]) + tip

    if action == "example":
        if not item or not item.example_en:
            return ("Keep practising — example sentences will appear once this entry has corpus data."
                    if en else "该条目暂无例句数据，继续学习后这里会补充。")
        return item.example_en

    if action == "translate":
        if not item:
            return (f"No local dictionary entry for “{text}”." if en
                    else f"站内暂未收录 “{text}”，暂无译词。")
        return (item.meaning_en or item.meaning_cn or "") if en else (item.meaning_cn or item.meaning_en or "")

    if action == "correct":
        s = text.rstrip()
        fixed = s[0].upper() + s[1:] if s else s
        if fixed and fixed[-1] not in ".!?":
            fixed += "."
        if fixed == text:
            return ("No obvious issues detected (local rule check does not replace a real grader)."
                    if en else "未发现明显问题（本地规则检查不能替代真人批改）。")
        return f"{fixed}"

    if action == "conversation":
        return ("[Practice mode] Reply with one question using your target word, then switch roles."
                if en else "【练习模式】用目标词提一个问题回复，然后互换角色继续。")

    if action == "practice":
        if not item:
            return ("Pick a word from the lesson and try again." if en
                    else "请从课时中选择一个词条再试。")
        meaning = item.meaning_en if en else (item.meaning_cn or item.meaning_en)
        return (f"Recall drill — say the meaning of “{item.surface}”, then compare with: {meaning}"
                if en else f"回忆练习 —— 说出 “{item.surface}” 的释义，再与答案对照：{meaning}")

    return "Unsupported action" if en else "不支持的操作"


# --------------------------------------------------------------------------
# 对外能力
# --------------------------------------------------------------------------
_SYSTEM = {
    "explain": ("You are a patient language tutor. Explain the given word or phrase concisely: "
                "meaning, register, common collocations. Reply in the learner's language."),
    "example": ("You are a language tutor. Produce 2 natural example sentences with a brief gloss each."),
    "conversation": ("You are a friendly conversation partner. Continue the dialogue naturally and "
                     "ask one follow-up question."),
    "correct": ("You are an editor. Correct the learner's sentence and briefly explain each fix."),
    "translate": ("You are a translator. Give the most natural equivalent, noting register differences."),
    "practice": ("You are a drill coach. Generate one short recall question about the given item."),
}


def explain(text: str, lang: str = "zh") -> dict:
    return _tutor("explain", f"Explain: {text}", text, lang)


def example(text: str, lang: str = "zh") -> dict:
    return _tutor("example", f"Give examples for: {text}", text, lang)


def conversation(text: str, lang: str = "zh") -> dict:
    return _tutor("conversation", text, text, lang)


def correct(text: str, lang: str = "zh") -> dict:
    return _tutor("correct", f"Correct this sentence: {text}", text, lang)


def translate(text: str, lang: str = "zh") -> dict:
    return _tutor("translate", f"Translate: {text}", text, lang)


def practice(text: str, lang: str = "zh") -> dict:
    return _tutor("practice", f"Drill on: {text}", text, lang)


DISPATCH = {
    "explain": explain,
    "example": example,
    "conversation": conversation,
    "correct": correct,
    "translate": translate,
    "practice": practice,
}


def run(action: str, text: str, lang: str = "zh") -> dict:
    """按 action 名分派；未知 action 返回结构化错误而不是 KeyError。"""
    key = (action or "").strip().lower()
    fn = DISPATCH.get(key)
    if fn is None:
        return {"ok": False, "action": action, "mock": True,
                "error": f"不支持的操作：{action}（可选：{', '.join(ACTIONS)}）"}
    # 空输入必须在拼 prompt **之前**拦掉：各 wrapper 会把文本拼进
    # "Explain: {text}" 之类模板，光看模板串永远不会为空。
    if not (text or "").strip():
        return {"ok": False, "action": key, "mock": True, "error": "请输入内容"}
    return fn(text, lang)
