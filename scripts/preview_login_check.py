"""对运行中的预览服务做 HTTP 登录闭环校验。

验证点：
1. GET /login 可提取 CSRF token；
2. POST /login 成功并建立会话；
3. 登录后的 /dashboard 页面中，导航主题图片（favorites/wrong/admin/logout/profile）
   以 <img class="feat-img"> 形式真实输出；
4. 退出登录为 POST + CSRF 表单（安全加固保留）。
"""
from __future__ import annotations

import http.cookiejar
import os
import re
import urllib.error
import urllib.parse
import urllib.request

BASE = os.getenv("PREVIEW_BASE_URL", "http://127.0.0.1:5000")
EMAIL = os.getenv("PREVIEW_EMAIL", "preview@example.com")
PASSWORD = os.getenv("PREVIEW_PASSWORD", "change-me-in-env")

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def get(url: str) -> tuple[int, str]:
    try:
        r = opener.open(url, timeout=30)
        return r.status, r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:  # noqa: F821
        return e.code, e.read().decode("utf-8", "ignore")


def csrf(html: str) -> str:
    m = re.search(r'name="csrf_token"[^>]*value="([^"]+)"', html)
    return m.group(1) if m else ""


# 1) 打开登录页
st, html = get(f"{BASE}/login")
print(f"[1] GET /login -> {st}, csrf={'yes' if csrf(html) else 'NO'}")
if st != 200:
    raise SystemExit("登录页不可达，请确认预览服务已启动")

token = csrf(html)

# 2) 提交登录
data = urllib.parse.urlencode(
    {"csrf_token": token, "email": EMAIL, "password": PASSWORD}
).encode()
req = urllib.request.Request(f"{BASE}/login", data=data, method="POST")
try:
    r = opener.open(req, timeout=30)
    body = r.read().decode("utf-8", "ignore")
    print(f"[2] POST /login -> {r.status}, final_url={r.geturl()}")
except urllib.error.HTTPError as e:
    body = e.read().decode("utf-8", "ignore")
    print(f"[2] POST /login -> {e.code}")
if "登录" in body and "/login" in r.geturl() if "r" in dir() else False:
    print("    (可能仍在登录页，继续检查仪表盘会话)")

# 3) 访问仪表盘并检查导航图片
st, dash = get(f"{BASE}/dashboard")
print(f"[3] GET /dashboard -> {st}")
if st != 200:
    raise SystemExit("未建立会话，登录可能失败")

imgs = re.findall(r'<img[^>]*class="feat-img"[^>]*>', dash)
srcs = [re.search(r'src="([^"]+)"', i).group(1) for i in imgs if re.search(r'src="([^"]+)"', i)]
print(f"    feat-img 数量: {len(imgs)}")

for key in ("favorites", "wrong", "admin", "logout", "profile"):
    hit = [s for s in srcs if f"/themes/{key}.png" in s]
    print(f"    导航 {key:9s}: {'OK  ' if hit else 'MISS'} {len(hit)}")

alt_missing = [i for i in imgs if "alt=" not in i]
print(f"    缺少 alt 的图片: {len(alt_missing)}")

nav_labels = len(re.findall(r'class="nav-ico-text"', dash))
print(f"    导航文字标签保留: {nav_labels}")

logout_form = 'action="/logout"' in dash and 'method="post"' in dash.lower()
print(f"    退出登录为 POST 表单: {logout_form}")
