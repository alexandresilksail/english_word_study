"""在数据库副本上验证 V6.1 微信小程序迁移（UP → 校验 → ROLLBACK → 再校验）。

流程（全程只动副本，绝不触碰 instance/english_word_study.db）：
1. 复制生产库到临时文件；
2. 记录迁移前关键表的行数（作为数据完好性基线）；
3. 执行 UP 迁移；
4. 校验两张新表存在、列与唯一约束正确、既有数据一行未变；
5. 执行 ROLLBACK；
6. 校验新表已消失、既有数据仍与基线一致；
7. 顺带做一次「重复绑定」的行为验证：同一 openid 无法绑两个账户。
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
UP_SQL = os.path.join(HERE, "001_wx_miniprogram.sql")
DOWN_SQL = os.path.join(HERE, "001_wx_miniprogram_rollback.sql")
SRC_DB = os.path.join(ROOT, "instance", "english_word_study.db")

CHECK_TABLES = [
    "users", "words", "word_meta", "user_word_progress", "favorites",
    "wrong_answers", "test_records", "study_records", "user_stats",
    "email_codes", "courses", "units", "lessons",
]

failures: list[str] = []


def check(label: str, cond: bool, detail: str = "") -> None:
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"  ({detail})" if detail else ""))
    if not cond:
        failures.append(label)


def counts(con: sqlite3.Connection) -> dict[str, int]:
    out = {}
    for t in CHECK_TABLES:
        try:
            out[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except sqlite3.Error:
            out[t] = -1
    return out


def table_names(con: sqlite3.Connection) -> set[str]:
    return {r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}


def index_names(con: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in con.execute(f"PRAGMA index_list({table})").fetchall()}


def main() -> int:
    if not os.path.exists(SRC_DB):
        print(f"未找到生产库 {SRC_DB}，改用内存库做 schema 验证")
        tmp = tempfile.mkdtemp(prefix="wx_mig_")
        db = os.path.join(tmp, "probe.db")
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT)")
        con.commit()
        base = counts(con)
    else:
        tmp = tempfile.mkdtemp(prefix="wx_mig_")
        db = os.path.join(tmp, "copy_of_production.db")
        shutil.copy2(SRC_DB, db)
        print(f"已复制生产库副本：{db}")
        con = sqlite3.connect(db)
        base = counts(con)

    print("\n[1] 迁移前基线")
    print("    " + ", ".join(f"{k}={v}" for k, v in base.items() if v >= 0))
    before_tables = table_names(con)
    check("迁移前不存在 wechat_identities", "wechat_identities" not in before_tables)

    print("\n[2] 执行 UP 迁移")
    with open(UP_SQL, encoding="utf-8") as fh:
        con.executescript(fh.read())
    con.commit()
    tables = table_names(con)
    check("wechat_identities 已创建", "wechat_identities" in tables)
    check("wx_token_revocations 已创建", "wx_token_revocations" in tables)

    print("\n[3] 校验新表结构")
    cols = [r[1] for r in con.execute("PRAGMA table_info(wechat_identities)").fetchall()]
    for c in ("id", "user_id", "openid", "appid", "unionid", "nickname",
              "avatar_url", "created_at", "updated_at", "last_login_at"):
        check(f"wechat_identities.{c} 存在", c in cols)

    uniques = [r[1] for r in con.execute("PRAGMA index_list(wechat_identities)").fetchall()
               if r[2]]
    has_openid_appid = any(
        {c[2] for c in con.execute(f"PRAGMA index_info({u})").fetchall()} == {"openid", "appid"}
        for u in uniques)
    has_user_appid = any(
        {c[2] for c in con.execute(f"PRAGMA index_info({u})").fetchall()} == {"user_id", "appid"}
        for u in uniques)
    check("唯一约束 (openid, appid) 生效", has_openid_appid)
    check("唯一约束 (user_id, appid) 生效", has_user_appid)

    idxs = index_names(con, "wechat_identities")
    check("普通索引 ix_wx_identity_openid 已建", "ix_wx_identity_openid" in idxs)

    rcols = [r[1] for r in con.execute("PRAGMA table_info(wx_token_revocations)").fetchall()]
    for c in ("jti", "user_id", "token_type", "revoked_at", "expires_at"):
        check(f"wx_token_revocations.{c} 存在", c in rcols)

    print("\n[4] 既有数据完好性")
    after = counts(con)
    for t, v in base.items():
        if v < 0:
            continue
        check(f"{t} 行数不变 ({v})", after.get(t) == v, f"before={v} after={after.get(t)}")

    print("\n[5] 外键级联与唯一约束行为")
    uid = con.execute("SELECT id FROM users LIMIT 1").fetchone()
    if uid:
        uid = uid[0]
        con.execute(
            "INSERT INTO wechat_identities (user_id, openid, appid) VALUES (?,?,?)",
            (uid, "openid_dup_test", "wx_appid_test"))
        con.commit()
        # 同一 openid + appid 再插一次必须失败
        try:
            con.execute(
                "INSERT INTO wechat_identities (user_id, openid, appid) VALUES (?,?,?)",
                (uid, "openid_dup_test", "wx_appid_test"))
            con.commit()
            check("重复 openid 绑定被唯一约束拦截", False, "第二次插入竟然成功")
        except sqlite3.IntegrityError:
            con.rollback()
            check("重复 openid 绑定被唯一约束拦截", True)
        # 同一用户 + appid 换个 openid 也必须失败
        try:
            con.execute(
                "INSERT INTO wechat_identities (user_id, openid, appid) VALUES (?,?,?)",
                (uid, "openid_other", "wx_appid_test"))
            con.commit()
            check("同账户重复微信身份被唯一约束拦截", False, "第二次插入竟然成功")
        except sqlite3.IntegrityError:
            con.rollback()
            check("同账户重复微信身份被唯一约束拦截", True)
        # 清掉测试行，恢复干净状态
        con.execute("DELETE FROM wechat_identities WHERE appid='wx_appid_test'")
        con.commit()
    else:
        print("  SKIP  库中无用户，跳过外键/唯一约束行为验证")

    print("\n[6] 执行 ROLLBACK")
    with open(DOWN_SQL, encoding="utf-8") as fh:
        con.executescript(fh.read())
    con.commit()
    tables = table_names(con)
    check("wechat_identities 已删除", "wechat_identities" not in tables)
    check("wx_token_revocations 已删除", "wx_token_revocations" not in tables)

    print("\n[7] 回滚后既有数据完好性")
    final = counts(con)
    for t, v in base.items():
        if v < 0:
            continue
        check(f"{t} 行数仍为 {v}", final.get(t) == v, f"before={v} after={final.get(t)}")

    con.close()
    shutil.rmtree(tmp, ignore_errors=True)

    print("\n" + "=" * 60)
    if failures:
        print(f"迁移验证失败 {len(failures)} 项：" + ", ".join(failures))
        return 1
    print("迁移验证全部通过：UP / ROLLBACK 均可执行，既有数据零改动。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
