# CubePlex 工作日志

## 2026-09-09

### WeCom 重复 pending 账号 bug 修复

**问题**：用户输入 bot_id/secret 后出现两个 pending 账号，都显示"已禁用"。

**根因**：`_connect_wecom_binding` 函数查询 pending 账号时缺少排序和去重逻辑，导致每次触发 `/wecom/connect` 都会追加新账号。

**修复内容**（`backend/cubeplex/api/routes/v1/ws_im.py`）：
1. 查询时按 `created_at DESC` 排序，确保取最新创建的
2. 保留最新的 pending 账号，删除多余的 stale 账号
3. 清理时同步停止对应的 gateway 实例
4. 与 WeChat 实现保持一致

**前端修复**（`frontend/packages/web/components/im/ImConnectWizard/platforms/wecom.ts`）：
1. 添加缺失的 `StepCredentials` import
2. 调整字段顺序：`bot_id` → `secret` → `bot_name`

**状态**：
- ✅ 代码已修复并提交
- ✅ 数据库已清理（删除 2 个重复 pending 账号）
- ✅ 后端运行正常（端口 8000）
- ✅ 前端运行正常（端口 3000）

### 前端代理问题修复

**问题**：前端访问 http://localhost:3000 返回 502。

**根因**：环境变量中有 `http_proxy=http://127.0.0.1:53061`，导致 Next.js 代理请求走代理服务器。

**修复**：创建 `frontend/packages/web/.env.local`，设置 `CUBEPLEX_API_URL=http://127.0.0.1:8000`

**状态**：✅ API 代理正常，可访问 http://localhost:3000

### 代码提交

- **Commit**: `b2db49a2`
- **Message**: fix(wecom): dedupe stale pending accounts; fix(wecom): add missing StepCredentials import and reorder credential fields
- **推送到**: https://github.com/cooper2006/cubeplex (personal remote)

## 2026-09-09 (续) - WeCom 绑定测试成功

### 测试通过
- ✅ WeCom 绑定流程正常
- ✅ `/connect <code>` 消息能被成功消费
- ✅ 账号正常启用

### 新增修复
- **Commit**: `2ad5c12a`
- **Message**: fix(wecom): start gateway for pending account to receive /connect messages
- **文件**: `backend/cubeplex/api/routes/v1/ws_im.py`
- **说明**: 在绑定成功后启动 gateway，确保机器人能接收 `/connect` 消息

### 最终提交记录
```
2ad5c12a fix(wecom): start gateway for pending account to receive /connect messages
d217127d docs: 更新工作日志
b2db49a2 fix(wecom): dedupe stale pending accounts; fix(wecom): add missing StepCredentials import and reorder credential fields
1f164a4c feat(wecom): 采用 /connect <code> 绑定方式替代企业微信端验证
```

**推送状态**: ✅ 已推送到 https://github.com/cooper2006/cubeplex

**服务状态**:
- ✅ 后端运行中（端口 8000）
- ✅ 前端运行中（端口 3000）
- ✅ API 代理正常

## 2026-09-09 (续) - 合并上游 cubeplexai/cubeplex 并刷新文档

### 合并内容（origin/main → main）

拉取上游 3 个提交：

```
a54442cf chore(release): bump version to 0.7.2
ba632124 feat(im): deliver WeCom images and files both ways
b932776c fix(im): stack WeCom credential fields in the connect wizard
```

**冲突处理**（WeCom 连接向导，本地 /connect 绑定改造 vs 上游 UI 布局优化）：
1. `wecom.ts`：保留本地的 `binding` 步骤（`StepWeComBinding`）与 `skipSubmit: true`，
   同时采用上游的 `fullWidth` 全宽字段布局与 `botNameHint` 提示；
   删除因字段重排产生的重复 `bot_name` 字段。
2. `StepCredentials.tsx`：采用上游响应式网格（`grid-cols-1 gap-4 sm:grid-cols-2 sm:gap-3`），
   配合 `fullWidth` 后 WeCom 三个凭证字段仍各占一整行。

### 验证

- ✅ 后端 WeCom 单元 + 集成测试：`tests/unit/im/wecom/` + `test_artifact_delivery.py` 共 65 项通过
- ✅ 前端 `tsc --noEmit`（packages/web）通过
- ✅ 文档站 `pnpm check`（Docusaurus en + zh-Hans 构建、typecheck、URL 规范检查、Worker 检查）通过
- ✅ i18n 镜像（`docs/site/i18n/zh-Hans/.../current`）与英文文档结构一致（42 文件）

### 提交记录

```
7d6449d9 merge: sync upstream main — WeCom media delivery, stacked wizard fields, v0.7.2
36562179 docs: update work log for WeCom binding fix
2ad5c12a fix(wecom): start gateway for pending account to receive /connect messages
```

**推送状态**: ✅ 已推送到 https://github.com/cooper2006/cubeplex

## 2026-09-10 - WeCom 双 pending 复现（并发竞态）修复

### 问题
连接 WeCom 时又出现两个 pending 账号（`imac-1rmdNDaWH7i0cR` / `imac-1rmdNDKPfTnc9p`，同分钟创建）。

### 根因
09-09 的修复只覆盖"顺序重连"（读时去重）。这次是两个**并发**的 `/wecom/connect` 请求
（dev 下 StrictMode 双挂载向导步骤 effect，两次请求都在对方插入前读到空 pending 表），
各自建了一行 → 双 pending。

### 修复
1. **后端竞态锁**（`ws_im.py::_connect_wecom_binding`）：与 WeChat 流程对齐，在
   "去重 + 创建/复用"临界段外加 `{prefix}:wecom:connect-lock:{workspace}` Redis 锁
   （nx + 3 次重试 + 兜底 sleep），并发的两次调用串行化，后者读到前者的 pending 行。
2. **前端防双发**（`StepWeComBinding` / `StepWechatQR`）：绑定 fetch 加 in-flight
   守卫，StrictMode 双挂载 / 标签页重挂载不再触发两个并发请求。
3. **E2E 回归**（`test_im_routes.py::test_concurrent_wecom_binding_connect_keeps_single_pending_account`）：
   两个并发 /wecom/connect 必须收敛到恰好 1 个 pending 行（已做红→绿验证：
   临时禁用锁 → 测试失败；恢复 → 通过）。

### 清理
- 数据库删除 2 条残留 pending 行（`im_connector_accounts`）
- 后端热重载已加载新代码，旧 pending 账号的内存 gateway 随 worker 重启清空

### 验证
- ✅ `tests/e2e/test_im_routes.py` 9 项通过（含新并发测试）
- ✅ `tests/unit/im/wecom/` 56 项通过
- ✅ 前端 `tsc --noEmit` 通过
