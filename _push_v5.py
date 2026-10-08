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
    message = ("V5.9 收口 + V6.0.1-4 AI 个性化闭环（审核推送）\n\n"
               "V5.9 遗留项收口：每日配额 + 运维 CLI + 录音降级与麦克风释放 + 听力卡片缺陷修复\n"
               "  + 安全 IP 限流接入（auth before_request，429 JSON / flash）+ 性能 N+1 修复。\n"
               "V6.0.1 AI Assessment：六维（词汇/语法/阅读/听力/口语/写作）能力评估，产出 Skill Profile。\n"
               "V6.0.2 Learner Model：LearnerProfile（六维等级 + 母语/目标语 + 弱项 + 偏好）与 Assessment 快照。\n"
               "V6.0.3 AI Learning Path：基于 LearnerProfile 的规则引擎，产出个性化每日计划\n"
               "  （弱项专项 / 当前 CEFR 单元 / 间隔复习），复用既有 /path 路由与数据脊柱。\n"
               "V6.0.4 Adaptive Review：综合弱项优先级 + 逾期程度重排到期复习队列，复用 /review。\n\n"
               "诚实原则（贯穿全项目）：未配 API Key 一律本地规则 / 练习模式并标注 mock=true；\n"
               "  无法评估的维度返回 None 而非 0（0 会被误读成「很差」，真相是「没测」）；\n"
               "  真实 provider 出错显式失败，绝不悄悄退化冒充 AI。\n\n"
               "关键修复：IP 限流改为显式 IP_RATELIMIT_ENABLED 开关，测试配置统一关闭以免误拦\n"
               "  共享 127.0.0.1 登录；测试改用显式 config 类，消除 os.environ 与导入顺序的隐式依赖。\n\n"
               "技术栈未变：Flask / SQLAlchemy / SQLite / Docker / Gunicorn / Nginx，2vCPU-2GB 可承载；\n"
               "未引入 PostgreSQL / Redis / Celery / 大型模型 / 任何 SDK。\n\n"
               "测试：pytest 全量 192 passed，python -m compileall 通过，工作区干净。\n"
               "文档：docs/V5_AUDIT_REPORT.md、docs/V6.0_ASSESSMENT.md、README.md。\n"
               "V6.0.5-11（AI Tutor 2.0 / Conversation / Speaking Coach / Content Engine /\n"
               "  Multilingual / Dashboard 2.0 / Subscription）列为下一阶段路线，未在本推送范围。")
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
