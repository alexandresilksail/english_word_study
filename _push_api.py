"""通过 GitHub REST API 推送当前工作区改动（git 协议被屏蔽时的兜底方案）。

做法（Git Database API）：
1. 读取 Desktop 上的 token（不打印）。
2. 取远端 main 当前 commit（base_sha）。
3. 取 base commit 的 tree（base_tree）。
4. 枚举 `git status --porcelain` 中的改动文件：
   - 修改 / 新增 -> 读取内容建 blob，更新 tree 条目
   - 删除 -> 条目置 sha=null（基于 base_tree 删除）
   其余文件沿用 base_tree，无需重传。
5. 基于 base_tree + 变更条目建新 tree，再建新 commit（parent = base_sha），
   最后 PATCH 远端 ref 为新的 commit（fast-forward，无需 force）。

只推送改动文件，避免重传整个仓库（音频 / 数据库 / .env 均不在此列）。
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import urllib.request

SAAS_DIR = os.path.dirname(os.path.abspath(__file__))
TOKEN_FILE = r"C:/Users/Administrator/Desktop/新建文本文档.txt"
REPO = "alexandresilksail/english_word_study"
BRANCH = "main"
API = "https://api.github.com"


def run(cmd):
    return subprocess.run(cmd, cwd=SAAS_DIR, capture_output=True, text=True)


def api(method, path, token, body=None):
    url = API + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/json",
        "User-Agent": "workbuddy-push",
    })
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status, json.load(r)


def main():
    token = open(TOKEN_FILE, "r", encoding="utf-8").read().strip().splitlines()[0].strip()
    if token.lower().startswith("token "):
        token = token[6:].strip()
    if not token:
        print("NO_TOKEN"); sys.exit(2)

    # 1) base ref
    st, ref = api("GET", f"/repos/{REPO}/git/ref/heads/{BRANCH}", token)
    if st != 200:
        print("GET_REF_FAIL", st, ref); sys.exit(3)
    base_sha = ref["object"]["sha"]
    print("base_sha", base_sha[:12])

    # 2) base tree (recursive)
    st, tree = api("GET", f"/repos/{REPO}/git/trees/{base_sha}?recursive=1", token)
    if st != 200:
        print("GET_TREE_FAIL", st, tree); sys.exit(4)
    print("base_tree_entries", len(tree.get("tree", [])))

    # 3) 枚举本地改动
    out = run(["git", "status", "--porcelain"])
    entries = []          # 发送到 GitHub 的变更条目
    n_add = n_del = 0
    for line in out.stdout.splitlines():
        if not line.strip():
            continue
        code = line[:2]
        path = line[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if "D" in code:
            # 删除：sha=null（配合 base_tree）
            entries.append({"path": path, "mode": "100644",
                            "type": "blob", "sha": None})
            n_del += 1
            continue
        fpath = os.path.join(SAAS_DIR, path)
        if not os.path.isfile(fpath):
            continue
        with open(fpath, "rb") as f:
            content = f.read()
        b64 = base64.b64encode(content).decode("ascii")
        st2, blob = api("POST", f"/repos/{REPO}/git/blobs",
                        token, {"content": b64, "encoding": "base64"})
        if st2 != 201:
            print("BLOB_FAIL", st2, blob, path); sys.exit(5)
        entries.append({"path": path, "mode": "100644",
                        "type": "blob", "sha": blob["sha"]})
        n_add += 1

    print(f"变更条目：新增/修改 {n_add}，删除 {n_del}")

    # 4) 新 tree（基于 base_tree 合并）
    st3, new_tree = api("POST", f"/repos/{REPO}/git/trees",
                        token, {"base_tree": base_sha, "tree": entries})
    if st3 != 201:
        print("TREE_FAIL", st3, new_tree); sys.exit(6)
    new_tree_sha = new_tree["sha"]
    print("new_tree", new_tree_sha[:12])

    # 5) 新 commit
    message = ("V3.0 完成：小游戏端到端 / REST API v1 / 页面渲染测试套件；"
               "API 未登录统一返回 401 JSON")
    st4, commit = api("POST", f"/repos/{REPO}/git/commits", token, {
        "message": message,
        "tree": new_tree_sha,
        "parents": [base_sha],
    })
    if st4 != 201:
        print("COMMIT_FAIL", st4, commit); sys.exit(7)
    new_commit_sha = commit["sha"]
    print("new_commit", new_commit_sha[:12])

    # 6) 更新 ref（fast-forward）
    st5, upd = api("PATCH", f"/repos/{REPO}/git/refs/heads/{BRANCH}",
                   token, {"sha": new_commit_sha, "force": False})
    if st5 not in (200, 202):
        print("REF_FAIL", st5, upd); sys.exit(8)
    print("PUSH_OK", new_commit_sha[:12])


if __name__ == "__main__":
    main()
