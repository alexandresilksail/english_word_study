"""归一化：把 surface 变成用于去重 / 查询的稳定键。

规则：
- Unicode NFC 归一化（粤语繁体、英文撇号等保持一致）
- 去除首尾空白与标点（内部标点保留，如 don't 的 '）
- 折叠多余空白
- 英文小写；粤语中文无大小写，仅拉丁部分受影响
"""
from __future__ import annotations

import re
import unicodedata

_EDGE = " \t\r\n\"'`,.;:!?()[]{}<>/\\|-_=+*&^%@#$~`"

_SPACE_RE = re.compile(r"\s+")


def normalize(language_code: str, surface: str) -> str:
    if surface is None:
        return ""
    s = unicodedata.normalize("NFC", str(surface)).strip()
    s = s.strip(_EDGE)
    s = _SPACE_RE.sub(" ", s)
    s = s.lower()
    return s
