"""本地化层（Localization Layer）：三语 UI + 数据字段本地化。

解决的问题
-----------
旧实现把中英文案写成 ``("中文", "English")`` 二元组并用 CSS 同时显示，
模板里也大量 ``{{ obj.title_en }} · {{ obj.title_zh }}`` 硬编码双输出 ——
这导致**切到 English 版面时页面上仍然有中文**。

本模块确立一条硬约束：

    **English 版面（lang == "en"）绝不输出中文。**

UI 文案走 :data:`BUNDLES`（``app/translations/{zh,en,yue}.json``）；
业务数据字段走 :func:`pick` 系列函数，按当前 UI 语言选择对应字段，
英文缺失时向 English 降级，**绝不向中文降级**。

为何保留 ``"both"`` 模式
-----------------------
``lang == "both"`` 保留为**中文站默认**（英文在前 / 中文在后），但这只在
``zh`` 语境下成立；一旦用户选择 ``en``，所有辅助函数只返回英文。
"""
from __future__ import annotations

import json
import os
import re
import threading

from flask import request
from flask_login import current_user
from markupsafe import Markup, escape

# ==========================================================================
# 支持的语言
# ==========================================================================
UI_LANGS = ("zh", "en", "yue")

#: 界面语言 -> 显示名（用于语言切换器自身：它必须自身语种在前）
LANG_NAMES = {
    "zh": ("中文", "Chinese", "中文"),
    "en": ("English", "English", "English"),
    "yue": ("粵語", "Cantonese", "粵語"),
}
LANG_FLAGS = {"zh": "🇨🇳", "en": "🇬🇧", "yue": "🇭🇰"}

_TRANSLATIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "app", "translations")

#: 双语结构里的中文段（用于 English 版面在服务端剥离）
_BI_ZH_RE = re.compile(r'<span class="bi-zh">.*?</span>', re.DOTALL)

BUNDLES: dict[str, dict[str, str]] = {}
_lock = threading.Lock()


def load_bundles(force: bool = False) -> dict[str, dict[str, str]]:
    """加载 ``app/translations/{zh,en,yue}.json``（幂等，结果缓存在模块级）。"""
    global BUNDLES
    with _lock:
        if BUNDLES and not force:
            return BUNDLES
        loaded: dict[str, dict[str, str]] = {}
        for lang in UI_LANGS:
            path = os.path.join(_TRANSLATIONS_DIR, f"{lang}.json")
            try:
                with open(path, encoding="utf-8") as f:
                    loaded[lang] = json.load(f)
            except FileNotFoundError:  # pragma: no cover
                loaded[lang] = {}
        BUNDLES = loaded
        return loaded


def bundle(lang: str) -> dict[str, str]:
    if not BUNDLES:
        load_bundles()
    return BUNDLES.get(lang) or BUNDLES.get("en", {}) or BUNDLES.get("zh", {}) or {}


# ==========================================================================
# 语言解析
# ==========================================================================
def resolve_lang() -> str:
    """解析当前 UI 语言，优先级：显式指定 > Cookie > 用户偏好 > 默认 zh。

    返回值 ∈ ``{"zh", "en", "yue", "both"}``。

    ``"both"`` 是中文站的「双语同显」模式（英文在前、中文在后）；
    **English 版面永远是纯 ``"en"``，不会退化为 both**。
    """
    try:
        cookie = (request.cookies.get("ui_lang") or "").strip()
    except Exception:  # 无请求上下文（测试/CLI）
        cookie = ""
    if cookie in UI_LANGS or cookie == "both":
        return cookie

    try:
        if current_user and current_user.is_authenticated:
            pref = getattr(current_user, "preferred_lang", None)
            if pref in UI_LANGS or pref == "both":
                return pref
    except Exception:  # pragma: no cover
        pass
    return "zh"


def is_english() -> bool:
    """当前是否为 English 版面（该模式下禁止任何中文输出）。"""
    return resolve_lang() == "en"


# ==========================================================================
# UI 文案
# ==========================================================================
def translate(key: str, lang: str = "zh", default: str | None = None) -> str:
    """按语言取 UI 文案，单语输出。

    降级顺序：目标语言 -> en -> zh -> key 本身。
    注意 **不会** 在英文版面降级到中文（除 en 也缺失时必然无中文可选）。
    """
    b = bundle(lang)
    if key in b and b[key]:
        return b[key]
    for fallback in ("en", "zh"):
        fb = bundle(fallback)
        if key in fb and fb[key]:
            return fb[key]
    return default or key


#: 模板里的短别名
def t_ui(key: str, lang: str | None = None, default: str | None = None) -> str:
    return translate(key, lang or resolve_lang(), default)


# ==========================================================================
# 数据字段本地化 —— 「English 版面零中文」的保证点
# ==========================================================================
def pick(en_value: str = "", zh_value: str = "", yue_value: str = "",
         lang: str | None = "", sep: str = " · ") -> str:
    """按 UI 语言选出**单一**语言的数据字段值。

    - ``en``  -> 英文；英文缺失则返回空串（**绝不返回中文**）
    - ``yue`` -> 粤语 -> 中文 -> 英文
    - ``zh``  -> 中文 -> 英文
    - ``both``-> ``英文 · 中文``（中文站的双语同显）

    这是模板里所有旧式 ``{{ a_en }} · {{ a_zh }}`` 的替代品。
    """
    lang = lang or resolve_lang()
    en_value = (en_value or "").strip()
    zh_value = (zh_value or "").strip()
    yue_value = (yue_value or "").strip()

    if lang == "en":
        return en_value                      # 硬约束：English 版面不出现中文
    if lang == "both":
        if en_value and zh_value:
            return f"{en_value}{sep}{zh_value}"
        return en_value or zh_value
    if lang == "yue":
        return yue_value or zh_value or en_value
    return zh_value or en_value              # zh（默认）


def pick_pair(en_value: str = "", zh_value: str = "", lang: str | None = "",
              sep: str = " · ") -> Markup:
    """双语版 :func:`pick` —— English 版面只输出英文，其余语言双语同显/单语。

    返回 :class:`Markup`，可直接 ``{{ pick_pair(...) }}`` 输出。
    """
    lang = lang or resolve_lang()
    if lang == "en":
        return Markup(escape((en_value or "").strip()))
    if lang == "both":
        a, b = (en_value or "").strip(), (zh_value or "").strip()
        if a and b:
            return Markup(f'<span class="bi"><span class="bi-en">{escape(a)}</span>'
                          f'<span class="bi-zh">{escape(b)}</span></span>')
        return Markup(escape(a or b))
    return Markup(escape(pick(en_value, zh_value, lang=lang)))


# --- 下面是针对具体模型的便捷封装 -----------------------------------------
def title_of(obj, lang: str | None = "") -> str:
    """Course / Unit / Lesson：按语言取标题。"""
    if obj is None:
        return ""
    return pick(getattr(obj, "title_en", "") or "",
                getattr(obj, "title_zh", "") or "",
                getattr(obj, "title_yue", "") or "", lang)


def desc_of(obj, lang: str | None = "") -> str:
    """Course / Unit / Lesson：按语言取描述。"""
    if obj is None:
        return ""
    return pick(getattr(obj, "description_en", "") or getattr(obj, "desc_en", "") or "",
                getattr(obj, "description_zh", "") or getattr(obj, "desc_zh", "") or "",
                getattr(obj, "desc_yue", "") or "", lang)


def meaning_of(item, lang: str | None = "") -> str:
    """ContentItem：释义。

    English 版面返回 ``meaning_en``；缺失则返回空串。
    """
    if item is None:
        return ""
    en_v = getattr(item, "meaning_en", "") or ""
    zh_v = getattr(item, "meaning_cn", "") or ""
    yue_v = getattr(item, "meaning_yue", "") or ""
    return pick(en_v, zh_v, yue_v, lang)


def example_of(item, lang: str | None = "") -> str:
    """ContentItem：例句。"""
    if item is None:
        return ""
    return pick(getattr(item, "example_en", "") or "",
                getattr(item, "example_cn", "") or "",
                getattr(item, "example_yue", "") or "", lang)


def strip_other_language(html: str, lang: str) -> str:
    """服务端剥离「另一种语言」的节点，让 English 版面**源码里就没有中文**。

    为什么需要这一步
    ----------------
    旧实现把中英文写在同一个 ``<span class="bi">`` 里，靠 CSS
    ``html[data-lang="en"] .bi-zh { display:none }`` 隐藏中文 ——
    用户看不见，但中文依然存在于 HTML 源码、可复制、可被搜索引擎抓取。

    此处在响应层直接删除非目标语言的 span，做到**真正的单语输出**。
    CSS 规则保留作为兜底（例如 `:lang` 选择器场景）。

    仅对结构简单的 ``<span class="bi-xx">…</span>`` 生效，不做通用 HTML 解析，
    避免引入额外依赖与性能开销。
    """
    if not html or lang != "en":
        return html
    return _BI_ZH_RE.sub("", html)


def lang_label(lang: str, ui: str | None = None) -> str:
    """语言自身的显示名（切换器用）。

    **English 版面下语言名也必须是英文**（Chinese / English / Cantonese），
    否则切换器自己就会在英文界面里出现 «中文»。
    """
    ui = ui or resolve_lang()
    zh, en, yue = LANG_NAMES.get(lang, (lang, lang, lang))
    if ui == "en":
        return en
    if ui == "yue":
        return yue
    if ui == "both":
        return zh
    return zh


def flash_l(zh: str, en: str, category: str = "info", yue: str | None = None) -> None:
    """语言感知的 flash：English 版面只显示英文提示，绝不出现中文。

    用法等同 ``flash()``，但多传一个英文文案：``flash_l("已保存", "Saved", "success")``。
    """
    from flask import flash
    flash(pick(en, zh, yue or ""), category)


def t_flash(msg: str) -> str:
    """渲染 flash 文案：English 版面只显示「中文 / English」串里的英文半边。

    对于已经写成 ``"中文 / English"`` 形式的双语 flash（如校验失败提示），
    English 版面提取 ``/`` 之后的英文部分；纯中文 flash 必须由 ``flash_l``
    提供英文文案，否则这里无法翻译，原样返回。
    """
    if resolve_lang() == "en" and msg:
        for sep in (" / ", "／", " /", "/ "):
            if sep in msg:
                return msg.rsplit(sep, 1)[-1].strip()
    return msg


# ==========================================================================
# 词性标签（Part of Speech）—— English 版面返回英文，绝不输出中文
# ==========================================================================
POS_LABELS = {"n": "名词", "v": "动词", "vt": "及物动词", "vi": "不及物动词",
              "adj": "形容词", "adv": "副词", "prep": "介词", "pron": "代词",
              "conj": "连词", "num": "数词", "art": "冠词", "int": "感叹词"}
POS_LABELS_EN = {"n": "noun", "v": "verb", "vt": "transitive verb", "vi": "intransitive verb",
                 "adj": "adjective", "adv": "adverb", "prep": "preposition", "pron": "pronoun",
                 "conj": "conjunction", "num": "numeral", "art": "article", "int": "interjection"}


def pos_label(pos: str, lang: str | None = "") -> str:
    """按 UI 语言返回词性标签；English 版面只返回英文，绝不输出中文。"""
    lang = lang or resolve_lang()
    key = (pos or "").lower()
    if lang == "en":
        return POS_LABELS_EN.get(key, "")
    return POS_LABELS.get(key, "")
