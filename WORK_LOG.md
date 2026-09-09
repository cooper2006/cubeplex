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
