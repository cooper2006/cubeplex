# Work Log

## 2026-09-08 — WeChat/WeCom IM Connector Fixes

### 问题诊断
从日志分析发现四个核心问题：

1. **HTTP 500 on render** — `renderer.py:36` 使用 `state.time.monotonic()`（RenderState 无 time 属性）；`renderer.py:43` 使用 `self._connector._openid`（WeChatConnector 无 _openid 属性）
2. **QR 绑定后 Credential 丢失** — `_update_account_token` 用新 dict 替换整个 credential，丢失了 `qrcode_login_enabled` 标志
3. **QR 不匹配** — 用户刷新 QR 页面后生成新 QR，但 gateway 仍在轮询旧 QR，binding code 验证时无法同步
4. **Binding state 存储了 URL 而非原始 QR 码** — `_fetch_wechat_qrcode()` 返回 `(qrcode_string, qrcode_url)`，但 `create_binding_state` 调用时传入了 `qrcode_url`（完整URL），导致 gateway 无法用 URL 去轮询 iLink API
5. **`send_to_chat` 不存在** — `WeChatConnector` 没有此方法，renderer 调用时 AttributeError
6. **WeCom 表单布局过紧** — `grid-cols-2` 三字段放不下

### 修复内容

**backend/cubeplex/im/wechat/renderer.py**
- `state.time.monotonic()` → `time.monotonic()`
- `self._connector._openid` → `self._connector._chat_id`

**backend/cubeplex/im/wechat/connector.py**
- 添加 `gateway` 参数到 `__init__`
- 添加 `send_to_chat(chat_id, context_token, text)` 方法，委托给 gateway

**backend/cubeplex/im/wechat/_platform.py**
- `build_tailer` 中把 `gw` 传给 `WeChatConnector(gateway=gw, ...)`

**backend/cubeplex/im/wechat/gateway.py**
- `_update_account_token`: 先读取现有 credential，再 merge 新 bot_token，保留 `qrcode_login_enabled`
- `_check_binding_code`: 验证 binding code 时同步 gateway 的 auth_state QR
- `DEFAULT_QRCODE_POLL_TIMEOUT`: 180 → 600

**backend/cubeplex/api/routes/v1/ws_im.py**
- 从 URL 中提取原始 QR 码字符串
- `create_binding_state` 使用原始 QR 码而非 URL
- QR 刷新时同步 binding state 到 gateway

**frontend/.../StepCredentials.tsx**
- WeCom 表单改为 `grid-cols-1` 单列布局

### 当前状态（08:59）
- 代码修复已完成，6个文件已修改
- **后端尚未重启，仍运行旧代码**
- 建议操作：重启后端 → 删除重复 pending 账号 → 重新测试连接

### 验证结果
- `uv run python -m pytest tests/unit/im/wecom/test_connector.py` — **15 passed**
- 导入验证通过

---

### Push / PR / Codex 状态（10:5x）

在 08:59 修复基础上，本机进一步完成并验证（详见 commit `934b15af`，分支 `fix/2026-09-08-wechat-reconnect`）：

- 去重 pending 账号（`POST /wechat/connect` 只复用唯一一个 pending 账号，删除重复行）
- `send_to_chat` 对齐规范签名 `(chat_id, reply_to_id, text)`，修复运行期 `TypeError`（机器人不回消息的根因）
- 重连强制刷新 QR + mint 新 binding code 并作废旧的
- gateway 绑定成功写入 `bot_open_id`，状态翻「已连接」
- 新增 `test_wechat_renderer.py`、XML 测试换 iLink 版本

**已推送**：`git push -u personal fix/2026-09-08-wechat-reconnect`（personal = `cooper2006/cubeplex`）
**已开 PR**：https://github.com/cooper2006/cubeplex/pull/6 （base: cooper2006/cubeplex:main）

**Codex 阻塞**：codex 未监听 `cooper2006/cubeplex`（开 PR 及 re-tag 后均约 2 分钟无 👀 反应、无评审评论，只有 CodeQL 的 `github-advanced-security[bot]`）。
→ 用户决定：保留 fork PR #6，由用户在 `cooper2006/cubeplex` 上自行启用/安装 codex 连接器。
→ 待用户启用后，需**重新发一条 @codex 评论**触发评审（安装前的旧 re-tag 评论不会被响应），随后跑完整 review 循环（poll → fix → reply → re-tag 直到干净）。

**验证**：后端 `mypy`（改动文件）0 错、im 单测 263 passed、`ruff` 改动文件干净；前端 `tsc --noEmit` 0 错、`prettier --check` 干净。
**注**：`make check-ci` 还会报 3 个 pre-existing 格式/lint 问题（`im_ingress.py`、`schemas/im_connector.py`、`services/im_connector.py`），与本次改动无关，未顺手改。

### 已合并到本地 main（11:0x）
- 主仓库 `main` 之前有 7 个未提交的微信相关文件改动（即本 fix 改进前的版本）。先用 `git stash` 安全暂存（message: "pre-merge uncommitted wechat edits..."，保存在 stash 中可恢复，**未删除**），再 `git merge --ff-only fix/2026-09-08-wechat-reconnect`。
- 结果：`main` 已 fast-forward 至 `934b15af`，tracked 工作树干净，fix 分支内容全部进入 main。
- 未推送 main 到远端（用户只要求本地合并）。PR #6（cooper2006/cubeplex:main）的 diff 仍是 `934b15af`，一致。
- 待办：用户需在 `cooper2006/cubeplex` 启用 codex 后，由我重新发 @codex 评论触发 review 循环（安装前的旧 re-tag 评论不会被响应）。

### Codex 已安装 + 验证 PR（11:38）
- 用户已在 GitHub 手动安装 Codex App（slug: `chatgpt-codex-connector`，installation_id `159933272`）到 `cooper2006/cubeplex`。
  之前 PR #6 无 👀 反应正是因 app 未装在该 fork；安装落地页回调到 `chat.openai.com`（国内不通）只是成功提示页，GitHub 侧安装已生效。
- 为实跑确认 codex 现在会监听 fork，开最小验证 PR（分支 `feat/2026-09-08-codex-verify` → `cooper2006/cubeplex:main`），看 codex 是否在 ~30s 内对 PR 正文点 👀 并自动评审。
