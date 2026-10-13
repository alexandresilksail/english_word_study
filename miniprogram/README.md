# PolyGo 微信小程序导入运行教程

`miniprogram/` 是 **PolyGo** 的原生微信小程序（WXML / WXSS / JS，无框架依赖）。
本教程带你从零把它跑起来，并说明发布前必须完成的配置。

## 1. 环境准备

| 项 | 要求 |
|---|---|
| 微信开发者工具 | 稳定版（[官网下载](https://developers.weixin.qq.com/miniprogram/dev/devtools/download.html)） |
| Node | 不强制（本项目无 npm 依赖、无构建步骤） |
| 后端服务 | 本地或服务器上跑起 Flask 后端（见第 4 节） |

## 2. 导入项目

1. 打开微信开发者工具 → **导入项目** → 选择本仓库的 `miniprogram/` 目录。
2. AppID 选择：
   - 有正式 AppID：填你的（需在 [mp.weixin.qq.com](https://mp.weixin.qq.com) 注册）；
   - 没有：选**测试号**（`project.config.json` 已预置 `touristappid`，可直接导入预览，
     但无法使用真机微信登录，只能用「体验评分」等游客能力）。
3. 项目配置已就绪：`project.config.json` 里关闭了域名校验
   （`urlCheck:false`，开发期可用 http），并忽略了 `images/optimize.py`。

## 3. 目录结构

```
miniprogram/
├── app.js / app.json / app.wxss     入口 / 路由与 tabBar / 全局设计系统
├── project.config.json              开发者工具项目配置
├── sitemap.json                     索引配置
├── images/                          11 张本地插画 PNG（192×192，透明背景，共约 508KB）
│   └── optimize.py                  图片校验脚本（非运行时文件）
├── utils/
│   ├── api.js                       请求封装：Bearer 附加 / 401 单飞刷新 / 错误映射
│   ├── auth.js                      Token 本地存取（wx.Storage）+ 过期判断
│   └── util.js                      时间格式化 / 防重复点击 / 空状态配置
└── pages/
    ├── login/                       微信一键登录 + 绑定已有账户
    ├── home/                        学习概览 + 功能入口
    ├── words/                       词库（搜索 + A-Z 索引 + 分页）
    ├── word-detail/                 单词详情（真实音频播放 + 收藏）
    ├── quiz/                        测试中心（4 模式 × 6 范围）
    ├── quiz-result/                 测试结果与学习建议
    ├── favorites/                   收藏夹
    ├── wrong/                       错题本
    ├── progress/                    进度统计（14 天趋势 / 徽章 / 技能）
    └── profile/                     个人中心（目标 / 绑定 / 解绑 / 登出）
```

## 4. 启动后端（开发期）

小程序所有数据都来自真实后端，**没有假数据**。开发期最简方式：

```bash
# 1. 安装依赖（含 PyJWT）
pip install -r requirements.txt

# 2. 配置环境变量（至少给一套开发密钥，生产必须用强随机值）
export WX_TOKEN_SECRET="dev-only-secret"        # JWT 签名密钥
export WX_QUIZ_STATE_SECRET="dev-only-quiz"     # 出题状态签名密钥
export WX_APPID="你的小程序AppID"
export WX_SECRET="你的小程序AppSecret"

# 3. 启动（开发配置默认 sqlite + 自动播种）
python app.py
```

后端默认监听 `http://127.0.0.1:5000`，与 `app.js` 里的 `globalData.baseUrl` 一致。
手机真机调试时把 `baseUrl` 改成你电脑的局域网 IP（如 `http://192.168.1.5:5000`），
并在开发者工具里勾选「不校验合法域名」。

**关于微信登录**：真机上 `wx.login()` 的 code 必须在服务端用真实
`WX_APPID` / `WX_SECRET` 调 `code2Session` 才能换到 openid。
本地开发可以临时 mock（参考 `tests/test_wx_api.py` 的 `_FakeWx`），
或先用「绑定已有账户」流程登录（该路径不依赖微信侧）。

## 5. 页面功能对照

| 页面 | 调用的接口 |
|---|---|
| login | `POST /auth/wx-login`、`POST /auth/send-code`、`POST /auth/bind-email` |
| home | `GET /home` |
| words | `GET /words?q=&page=&initial=` |
| word-detail | `GET /words/<id>`、`POST /words/<id>/favorite` |
| quiz | `POST /quiz/start` → `GET /quiz/item` → `POST /quiz/answer` → `POST /quiz/finish` |
| quiz-result | （成绩由 quiz 页 redirect 透传） |
| favorites | `GET /favorites`、`POST /words/<id>/favorite` |
| wrong | `GET /wrong`、`POST /wrong/<id>/remove` |
| progress | `GET /progress` |
| profile | `GET /auth/me`、`POST /me/goal`、`POST /auth/bind-email`、`POST /auth/unbind`、`POST /auth/logout` |

完整接口文档见 [`../docs/api-wx-v1.md`](../docs/api-wx-v1.md)。

## 6. 常见问题

**Q：登录返回「微信登录失败」？**
A：检查 `WX_APPID` / `WX_SECRET` 是否与小程序后台一致；code 只能使用一次且 5 分钟过期，
重复提交会报 40029。

**Q：请求报「不在合法域名列表」？**
A：开发期在开发者工具详情页勾选「不校验合法域名」（`project.config.json` 已设
`urlCheck:false`）；上线前必须在 [mp.weixin.qq.com](https://mp.weixin.qq.com) →
开发管理 → 服务器域名里添加 HTTPS 域名。

**Q：Token 过期后页面没反应？**
A：`utils/api.js` 会自动用 refresh 换新并重放原请求一次；refresh 也失效时
才清Token 回登录页。手动复现：等 2 小时，或在后端把该用户令牌撤销。

**Q：答题时提示「测试状态失效」？**
A：出题状态令牌 2 小时过期（与 Access Token 对齐），超时需重新开始测试。

**Q：图片在哪里可以改？**
A：`images/` 下 11 张 PNG，192×192 透明背景。修改后跑
`python images/optimize.py` 做体积与透明度校验。

## 7. 发布前检查清单

- [ ] 已注册小程序并通过微信认证（个人订阅号无法使用自定义菜单等能力，
      但 `wx.login` + 后端 API 模式不受影响）
- [ ] 生产环境配置强随机 `WX_TOKEN_SECRET` / `WX_QUIZ_STATE_SECRET`（≥32 字节）
- [ ] `WX_APPID` / `WX_SECRET` 配置为生产值，且服务端可访问
      `api.weixin.qq.com`
- [ ] 后端域名已备案，并在小程序后台加入 request 合法域名（HTTPS）
- [ ] `app.js` 的 `baseUrl` 改为生产 HTTPS 地址
- [ ] 生产环境配置 SMTP（验证码邮件），否则 `send-code` 会下发 `dev_code`
- [ ] 执行 `migrations/001_wx_miniprogram.sql`（先在副本上验证，见
      `migrations/README.md`）
- [ ] 跑一遍 `tests/run_all.py` 与 `tests/integration_miniprogram.py`
- [ ] 开发者工具里真机预览 / 体验版全流程走查
