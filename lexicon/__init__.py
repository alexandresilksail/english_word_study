"""V5.1 Master Lexicon 引擎。

设计原则（见规格）：
- 轻量：仅用 SQLite + SQLAlchemy，不引入 PG / Redis / ES / Celery / ML / embedding / 常驻内存服务。
- 数据可分批导入（默认 250/批），不在应用启动时加载或导入。
- 许可证保守：不确定商业授权时 commercial_allowed=False。
- 去重键为 language_code + normalized + kind + pos，绝不按 surface。
"""
from .normalize import normalize
from .dedup import dedup_key, dedup_entries
from .cefr import CEFR_ORDER, compute_difficulty, is_valid_cefr
from .license_filter import apply_license, load_registry
from .validate import validate_entry
from .sources import get_source
from .pipeline import build_entry, run_pipeline
from .db_io import make_session, import_entries
from .to_content import convert_to_content

__all__ = [
    "normalize", "dedup_key", "dedup_entries", "CEFR_ORDER", "compute_difficulty",
    "is_valid_cefr", "apply_license", "load_registry", "validate_entry", "get_source",
    "build_entry", "run_pipeline", "make_session", "import_entries", "convert_to_content",
]
