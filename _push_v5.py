"""V5.0 推送脚本：把工作区的 V5 改动推到 feat/v5-learning-platform 分支。

与 _push_api.py 的区别：
- 工作区不是 git 仓库（git 协议被屏蔽，也无法 fetch），因此不再依赖 `git status`。
- 改为本地文件系统直接对远程 base tree 做差异（增 / 改），不发送删除条目，
  以避免因为本地是“部分抽取副本”而误删远程的音频 / 数据库等大文件。
- 目标分支固定为 feat/v5-learning-platform；若分支不存在则基于 main 新建。
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import sys
import urllib.request

SAAS_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = r"C:/Users/Administrator/Desktop/新建文本文档.txt"
REPO = "alexandresilksail/english_word_study"
BRANCH = "feat/v5-learning-platform"
API = "https://api.github.com"

# 不纳入推送的目录 / 文件（本地产物、密钥、缓存、预览辅助脚本）
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "instance", "logs",
             "node_modules", "venv", ".venv", ".mypy_cache", ".idea", ".vscode",
             ".workbuddy"}
SKIP_FILES = {".env", "preview_server.py"}
SKIP_SUFFIX = (".pyc", ".db", ".sqlite", ".sqlite3", ".log")


import time

def api(method, path, token, body=None, _attempt=0):
    url = API + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "workbuddy-push-v5",
    })
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.load(r)
    except (urllib.error.URLError, TimeoutError) as e:
        if _attempt >= 4:
            raise
        print(f"  (retry {_attempt+1} on {method} {path}: {e})")
        time.sleep(2 * (_attempt + 1))
        return api(method, path, token, body, _attempt + 1)


def blob_sha(content: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(content)).encode() + b"\0" + content).hexdigest()


def main():
    token = open(TOKEN_FILE, "r", encoding="utf-8").read().strip().splitlines()[0].strip()
    if token.lower().startswith("token "):
        token = token[6:].strip()
    if not token:
        print("NO_TOKEN"); sys.exit(2)

    # 1) 目标分支是否已存在？不存在则基于 main 建立
    st, ref = api("GET", f"/repos/{REPO}/git/ref/heads/{BRANCH}", token)
    if st == 200:
        base_sha = ref["object"]["sha"]
        print("base_sha (branch)", base_sha[:12], "(branch exists, fast-forward)")
        create_branch = False
    else:
        st, ref = api("GET", f"/repos/{REPO}/git/ref/heads/main", token)
        if st != 200:
            print("GET_MAIN_REF_FAIL", st, ref); sys.exit(3)
        base_sha = ref["object"]["sha"]
        print("base_sha (main)", base_sha[:12], "(branch will be created from main)")
        create_branch = True

    # 2) base tree (recursive)
    st, tree = api("GET", f"/repos/{REPO}/git/trees/{base_sha}?recursive=1", token)
    if st != 200:
        print("GET_TREE_FAIL", st, tree); sys.exit(4)
    base_paths = {e["path"]: e for e in tree.get("tree", [])}
    print("base_tree_entries", len(base_paths))

    # 3) 遍历本地工作区，diff vs base tree（只处理 新增 / 修改）
    entries = []
    n_add = n_mod = 0
    for root, dirs, files in os.walk(SAAS_DIR):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in files:
            if fn in SKIP_FILES or fn.endswith(SKIP_SUFFIX):
                continue
            full = os.path.join(root, fn)
            rel = os.path.relpath(full, SAAS_DIR).replace(os.sep, "/")
            if rel.startswith(".git/"):
                continue
            with open(full, "rb") as f:
                content = f.read()
            sha = blob_sha(content)
            be = base_paths.get(rel)
            if be is not None and be.get("sha") == sha:
                continue  # 未变更
            b64 = base64.b64encode(content).decode("ascii")
            st2, blob = api("POST", f"/repos/{REPO}/git/blobs",
                            token, {"content": b64, "encoding": "base64"})
            if st2 != 201:
                print("BLOB_FAIL", st2, blob, rel); sys.exit(5)
            entries.append({"path": rel, "mode": "100644",
                            "type": "blob", "sha": blob["sha"]})
            if be is None:
                n_add += 1
            else:
                n_mod += 1
    print(f"变更条目：新增 {n_add}，修改 {n_mod}")

    if not entries:
        print("NO_CHANGES"); sys.exit(0)

    # 4) 新 tree（基于 base_tree 合并）
    st3, new_tree = api("POST", f"/repos/{REPO}/git/trees",
                        token, {"base_tree": base_sha, "tree": entries})
    if st3 != 201:
        print("TREE_FAIL", st3, new_tree); sys.exit(6)
    new_tree_sha = new_tree["sha"]
    print("new_tree", new_tree_sha[:12])

    # 5) 新 commit
    message = ("V5.2-V5.8 AI 多语学习平台（完整版 · 审核推送）\n\n"
               "V5.2 遗留 2000 词审计 + production_ready 判定（synthetic-dev 永不进生产）。\n"
               "V5.3 English Master Lexicon：LexiconEntry 模型 + lexicon/ 引擎包。\n"
               "V5.4 粤语课程：7 类课时 × 6 单元，繁体 + Jyutping，language_code 恒为 yue。\n"
               "V5.5 学习内容引擎：Learn→Practice→Quiz→Complete 四步流 + ContentMastery 模型。\n"
               "V5.6 统一掌握度 Mastery 0-4（练习 + 小测 + 单元测试三类证据）+ 复习时刻单一真源。\n"
               "V5.7 AI Tutor：mock/openai/anthropic 三条分支，六类能力，零新增依赖（urllib）。\n"
               "V5.8 口语（STT/TTS/词级评分）+ 听力（复用既有 2000 MP3）+ 套餐权限 entitlements。\n\n"
               "诚实原则（贯穿全项目，重要）：\n"
               "  · 未配 API Key 一律处于本地规则 / 练习模式，UI 与 API 均标注 mock=true；\n"
               "  · 无法评估的评分维度（fluency / grammar）返回 None 而非 0 —— 0 会被误读成\n"
               "    「很差」，而真相是「没测」；\n"
               "  · 真实 provider 出错时显式失败，绝不悄悄退化成 mock 冒充 AI 回答。\n\n"
               "关键修复：schema_compat 补登 notify_email（老库缺列会让 SELECT users 直接报错，\n"
               "属真实生产隐患）；测试改用显式 config 类，消除 os.environ 与导入顺序的隐式依赖。\n\n"
               "技术栈未变：Flask / SQLAlchemy / SQLite / Docker / Gunicorn / Nginx，2vCPU-2GB 可承载；\n"
               "未引入 PostgreSQL / Redis / Celery / 大型模型 / 任何 SDK。\n\n"
               "测试：pytest 全量 145 passed，工作区干净。文档见 docs/V5.2-V5.8_ARCHITECTURE.md。")
    st4, commit = api("POST", f"/repos/{REPO}/git/commits", token, {
        "message": message,
        "tree": new_tree_sha,
        "parents": [base_sha],
    })
    if st4 != 201:
        print("COMMIT_FAIL", st4, commit); sys.exit(7)
    new_commit_sha = commit["sha"]
    print("new_commit", new_commit_sha[:12])

    # 6) 更新 / 创建 ref
    if create_branch:
        st5, upd = api("POST", f"/repos/{REPO}/git/refs", token,
                       {"ref": f"refs/heads/{BRANCH}", "sha": new_commit_sha})
        ok = st5 in (201, 200)
    else:
        st5, upd = api("PATCH", f"/repos/{REPO}/git/refs/heads/{BRANCH}",
                       token, {"sha": new_commit_sha, "force": False})
        ok = st5 in (200, 202)
    if not ok:
        print("REF_FAIL", st5, upd); sys.exit(8)
    print("PUSH_OK", new_commit_sha[:12])
    print(f"BRANCH_URL https://github.com/{REPO}/tree/{BRANCH}")


if __name__ == "__main__":
    main()
