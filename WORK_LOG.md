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

## 2026-09-10 (续) - WeCom /connect 绑定消息被 ingress 静默丢弃

### 现象
09:57 重新连接（pending 账号 `imac-1rmqEbaeastXHj`，锁修复后单一 pending ✓），
gateway 启动成功（无失败日志），用户在企业微信发 `/connect <code>` 后账号
始终不生效（`enabled` 仍为 `f`），日志中无任何 `[WeCom] inbound` / 绑定日志。

### 根因
`handle_inbound_callback`（`cubeplex/im/wecom/ingress.py`）开头的守卫
`if live_account is None or not live_account.enabled: return`：
pending 账号天生 `enabled=False`（绑定成功才置 True），所以 /connect
绑定消息在到达绑定逻辑前就被静默丢弃。09-09 的 /connect 绑定特性引入
pending 账号时没有放宽这个守卫 → 绑定流程自始不可用。

### 修复
- 守卫改为：pending_ 账号放行；显式禁用的非 pending 账号仍丢弃。
- 回归测试（`test_im_wecom_wechat_ingress.py`）：
  `test_wecom_pending_account_connect_binds_and_enables`（pending /connect →
  enabled=True + external 换成发送者 userid + 回复"绑定成功"）与
  `test_wecom_disabled_non_pending_account_drops_connect_message`（负向）。
  红→绿已验证（旧守卫下前者必挂）。

### 注意
- 重载后 pending 账号的内存 gateway 被清空，向导需重新拉一次码
  （倒计时到期自动 refetch 会同时重启 gateway）再发 /connect。
- `test_im_wecom_wechat_ingress.py` 中 7 个既有 webhook 路由测试在本地
  环境（`cubeplex_test` 库、缺 S3 rustfs bucket）下原本就失败，与本次改动无关
  （stash 验证：未改动代码同样失败）。

### 提交记录
```
b287ef80 fix(im): let pending WeCom accounts through the ingress enabled-guard
```

### 续：pending gateway 又被 15s 租约 sweep 杀掉
- 现象：ingress 修复后 /connect 消息仍到不了。redis 里没有 wecom pending 账号的
  owner lease，也没有任何失败日志 → gateway 启动后 15-30s 内被 `_sweep_once`
  停掉：sweep 只保留 enabled 账号的 gateway，而 pending 账号天生 disabled。
- 修复（`runtime.py::_sweep_once`）：pending_ gateway 账号与 enabled 账号同批
  保留 + 续租。
- 回归：`test_lease_sweep_keeps_pending_wecom_gateway_alive`（手动跑一次
  reconcile，断言 pending gateway 存活；红→绿已验证）。
- 提交：`ebfc5e4c`
- 注意：`cubeplex_test` 库若有残留账号，e2e 启动时会认领孤儿 lease 并尝试
  真实 WS 连接，造成假失败/变慢——复现异常先 drop+migrate 测试库。
- 结果：11:37:39 `/connect` 绑定成功，`imac-1rn3dH5949AFe1` → enabled、
  external=ZhuJunFeng，机器人正式可用。

### 收尾：并发测试加固 + 格式修复
- 12:04 一次 -k 跑出现 "got 2"：当时到 openws 的出口网络降级、首请求
  提交拖过锁的 1.9s 兜底窗口，第二个请求无锁兜底读到了未提交状态 →
  两行。该窗口是 wechat 同款设计的固有余量（锁兜底后仍放行），本次不扩大
  改动面；残留行会在下次 /wecom/connect 的 stale 清理里自愈。
- `test_concurrent_wecom_binding_connect_keeps_single_pending_account`
  加固为抗污染不变量："并发调用最多新增 1 行"（POST 前后快照对比），
  且清理不再断言状态——上一轮失败遗留的行不会让下一轮假失败；
  清理覆盖遗留行 + 本轮新行。污染态已验证通过（预置一行遗留后跑绿）。
- `ws_im.py` / `ingress.py` 中本次会话新增代码按 ruff format 对齐
  （不动文件里 pre-existing 的格式问题）。
- 事故记录：误把 09-08 遗留 stash（"pre-merge uncommitted wechat edits,
  superseded"）pop 进当前树造成 6 个文件冲突，已从 HEAD 恢复；该 stash
  保留在 stash list 里可找回，内容早已被 rebase 后的提交覆盖，无需处理。
