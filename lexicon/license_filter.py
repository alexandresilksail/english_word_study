"""许可证注册表 + 商业授权判定（保守：不确定即 False）。

- 每个 source 的实际许可证以数据源当前公布的 License 为准，记录在
  data/lexicon/license_registry.json。
- 若某 source 的商业授权不确定，注册表一律置 commercial_allowed=False。
- 即便条目显式声明 commercial_allowed=True，也要注册表允许才保留（绝不假设可商用）。
"""
from __future__ import annotations

import json
import os

_REGISTRY_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "data", "lexicon", "license_registry.json")
)
_cache = None


def load_registry() -> dict:
    global _cache
    if _cache is None:
        if os.path.exists(_REGISTRY_PATH):
            with open(_REGISTRY_PATH, "r", encoding="utf-8") as f:
                _cache = json.load(f)
        else:
            _cache = {}
    return _cache


def apply_license(entry: dict) -> dict:
    reg = load_registry()
    src = entry.get("source", "")
    meta = reg.get(src, {}) if src else {}

    if not (entry.get("license") or "").strip() or entry.get("license") == "DEV-PLACEHOLDER":
        entry["license"] = meta.get("license", "")
    if not (entry.get("license_url") or "").strip():
        entry["license_url"] = meta.get("license_url", "")
    if not (entry.get("attribution") or "").strip():
        entry["attribution"] = meta.get("attribution", "")
    if not (entry.get("cefr_source") or "").strip() and meta.get("cefr_source"):
        entry["cefr_source"] = meta["cefr_source"]

    # 商业授权：始终以注册表为准（保守）
    reg_commercial = bool(meta.get("commercial_allowed", False))
    entry["commercial_allowed"] = reg_commercial
    entry["redistribution_allowed"] = bool(meta.get("redistribution_allowed", False))
    return entry
