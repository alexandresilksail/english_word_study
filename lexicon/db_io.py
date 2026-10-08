"""数据库 I/O：独立 session（不依赖 Flask app 启动），批量入库。

- 默认每批 250 条 commit 一次，避免一次性写入 7000 条占用过多内存 / 事务。
- dry_run 只统计不写库。
- 出错时回滚当前事务（已提交批次保留为部分进度；首批发失败则整批回滚）。
- 导入完成后调用方负责关闭 session，不常驻内存。
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from extensions import db
from models import LexiconEntry


def make_session(uri: str = "sqlite:///lexicon_dev.db"):
    engine = create_engine(uri, future=True, pool_pre_ping=True)
    db.metadata.create_all(engine)  # 仅建表，不加载数据
    Session = sessionmaker(bind=engine)
    return Session()


def _to_model_kwargs(e: dict) -> dict:
    allowed = {c.name for c in LexiconEntry.__table__.columns} - {"id", "created_at"}
    return {k: e.get(k) for k in allowed if k in e}


def import_entries(session, entries, batch_size: int = 250, dry_run: bool = False):
    if dry_run:
        return {"inserted": 0, "batches": 0, "dry_run": True, "would_insert": len(entries)}

    inserted = 0
    batches = 0
    try:
        for i in range(0, len(entries), batch_size):
            chunk = entries[i:i + batch_size]
            objs = [LexiconEntry(**_to_model_kwargs(e)) for e in chunk]
            session.add_all(objs)
            session.flush()
            session.commit()
            inserted += len(objs)
            batches += 1
        return {"inserted": inserted, "batches": batches, "dry_run": False}
    except Exception:
        session.rollback()
        raise
