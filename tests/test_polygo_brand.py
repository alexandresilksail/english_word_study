"""PolyGo 品牌统一回归测试（V1.1 部署前验收）。

纯静态文件断言，不建数据库、不起服务。双重作用：
1. 复现：在品牌统一前的代码上，相关断言失败（旧名残留 / 新名缺失）；
2. 防回归：任何后续改动把产品名改回旧名或清掉新名，都会被这里拦下。

同时把「不得擅动」的技术标识（AppID / API 路由 / 数据库表名）固化为断言，
防止有人为统一品牌误改这些关键标识。
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
MP = os.path.join(ROOT, "miniprogram")

PASS = FAIL = 0


def check(name: str, ok, detail=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def read(relpath: str) -> str:
    with open(os.path.join(ROOT, relpath), encoding="utf-8") as f:
        return f.read()


OLD_NAMES = ("英语单词学习", "English Word Study", "english-word-study")


def main():
    print("=" * 70)
    print("PolyGo 品牌统一 + 技术标识防误改回归")
    print("=" * 70)

    # ---- 1. 小程序各展示面必须出现 PolyGo ----
    print("\n[1] 展示面品牌名")
    app = json.loads(read("miniprogram/app.json"))
    nav = app.get("window", {}).get("navigationBarTitleText", "")
    check("app.json 全局导航标题为 PolyGo", nav == "PolyGo", nav)

    for rel in ("miniprogram/pages/login/login.wxml",
                "miniprogram/pages/home/home.wxml",
                "miniprogram/pages/profile/profile.wxml",
                "miniprogram/pages/legal/legal.wxml",
                "miniprogram/README.md",
                "README.md"):
        content = read(rel)
        check(f"{rel} 含 PolyGo", "PolyGo" in content)

    # ---- 2. 隐私政策 / 用户协议中的小程序名 ----
    print("\n[2] 法律文档中的小程序名")
    privacy = read("app/templates/privacy.html")
    terms = read("app/templates/terms.html")
    check("privacy.html 以 PolyGo 指称小程序", "PolyGo" in privacy)
    check("terms.html 以 PolyGo 指称小程序", "PolyGo" in terms)

    # ---- 3. 小程序源码内不得残留旧品牌名 ----
    print("\n[3] 旧品牌名零残留（miniprogram/ 全量扫描）")
    leftovers = []
    for ext in ("wxml", "wxss", "js", "json"):
        for path in glob.glob(os.path.join(MP, "**", f"*.{ext}"), recursive=True):
            with open(path, encoding="utf-8") as f:
                text = f.read()
            for old in OLD_NAMES:
                if old in text:
                    leftovers.append((os.path.relpath(path, ROOT), old))
    check("miniprogram/ 无旧品牌名", not leftovers, leftovers)

    # ---- 4. 技术标识不得被品牌统一误改 ----
    print("\n[4] 技术标识防误改")
    pc = json.loads(read("miniprogram/project.config.json"))
    check("AppID 保持 touristappid（未擅改）",
          pc.get("appid") == "touristappid", pc.get("appid"))
    check("API 路由前缀 /api/wx/v1 未变",
          'url_prefix="/api/wx/v1"' in read("wx_api.py"))
    models = read("models.py")
    check("数据库表名 wechat_identities 未变",
          "wechat_identities" in models)
    check("数据库表名 wx_token_revocations 未变",
          "wx_token_revocations" in models)
    check("wx_auth 登出仍始终验签（无 verify_signature=False 产品调用）",
          not re.search(r'jwt\.decode\([^)]*verify_signature["\']?\s*:\s*False',
                        read("wx_auth.py")))

    # ---- 5. 构建完整性：11 个页面四件套 + JSON 合法 ----
    print("\n[5] 小程序构建完整性")
    missing = []
    for page in app["pages"]:
        for ext in ("json", "wxml", "wxss", "js"):
            p = os.path.join(MP, page + "." + ext)
            if not os.path.exists(p):
                missing.append(page + "." + ext)
    check("app.json 声明页面四件套齐全（11 页）", not missing, missing)
    for jf in glob.glob(os.path.join(MP, "**", "*.json"), recursive=True):
        try:
            with open(jf, encoding="utf-8") as f:
                json.load(f)
        except Exception as exc:  # noqa: BLE001
            check(f"JSON 合法：{os.path.relpath(jf, ROOT)}", False, exc)
            break
    else:
        check("miniprogram/ 全部 JSON 合法", True)

    print("\n" + "=" * 70)
    print(f"品牌回归汇总：通过 {PASS} / 失败 {FAIL}")
    print("=" * 70)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
