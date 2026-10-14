# Multica Board 只读 API 契约（v0 草案）

状态：前端提需求的草案，供 ACM-162（Builder）对齐。字段名、枚举可以改，改动请在 ACM-163 回复，前端同步更新 `web/fixtures/`。

`web/fixtures/*.json` 是本契约的样例响应，前端以 `?mock=1` 直接读取它们开发。后端实现后，同一份前端不改代码切到真实接口。

## 通用约定

- 监听 `127.0.0.1`（不是 `0.0.0.0`，不是 `localhost` 解析）。同一进程在 `/` 下托管 `multica-board/web/` 静态文件，在 `/api/` 下提供 JSON。
- 只接受 `GET`、`HEAD`。其它方法一律 `405`。没有任何写 Multica 的路径，也没有“立即刷新”这类触发采集的接口；采集由后端定时器驱动。
- 校验 `Host` 头只能是 `127.0.0.1:<port>`，否则 `421`/`403`，防 DNS rebinding。不返回任何 CORS 头。
- 响应头：`Content-Type: application/json; charset=utf-8`、`Cache-Control: no-store`、`X-Content-Type-Options: nosniff`。静态页面加 CSP：`default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'`。前端不发任何外部请求（不加载头像、字体、CDN）。
- 时间一律 ISO 8601 UTC 字符串；缺失为 `null`，不用空字符串。
- 每个列表响应带 `generated_at`（本次响应所依据的缓存快照时间）。
- 每条记录带 `key = "<workspace_id>:<resource_id>"`，前端用它去重和做 DOM key。
- 错误：`{"error": {"code": "not_found", "message": "..."}}`，配合 4xx/5xx。
- **脱敏白名单**：只输出本文列出的字段。不输出 token、`instructions`、`custom_env*`、`custom_args`、`mcp_config`、`runtime_config`、`metadata`、avatar URL、评论正文、run transcript。错误信息先归类为 `error_kind`，`error_summary` 截断到 200 字符并去掉疑似 token/路径/env 值。

### 枚举

`error_kind`：`quota` | `rate_limit` | `auth` | `network` | `timeout` | `model_unavailable` | `daemon_restart` | `cancelled` | `unknown`

`run.status`：沿用 CLI 原值（`queued` | `dispatched` | `running` | `completed` | `failed` | `cancelled` …），前端只特判 `failed`、`running`、`queued`/`dispatched`。

`collect_state`（workspace 采集状态）：`ok` | `stale` | `error` | `never`

## `GET /api/meta`

页面顶栏与健康状态。前端每 15 秒轮询一次，`generated_at` 变化才重新拉列表。

```json
{
  "api_version": "0",
  "generated_at": "2026-10-14T09:00:00Z",
  "collect_interval_s": 60,
  "multica_web_base": "https://multica.ai",
  "workspaces": [
    {
      "id": "uuid",
      "slug": "acm-lab",
      "name": "ACM Lab",
      "collect_state": "ok",
      "last_success_at": "2026-10-14T08:59:40Z",
      "last_attempt_at": "2026-10-14T08:59:40Z",
      "error": null,
      "counts": { "open_issues": 42, "agents": 8, "runtimes": 8 }
    },
    {
      "id": "uuid",
      "slug": "elder-oasis",
      "name": "Elder Oasis",
      "collect_state": "error",
      "last_success_at": "2026-10-14T08:41:10Z",
      "last_attempt_at": "2026-10-14T08:59:42Z",
      "error": { "kind": "timeout", "message": "issue list timed out after 20s" },
      "counts": { "open_issues": 17, "agents": 3, "runtimes": 2 }
    }
  ]
}
```

- `collect_state=stale`：上次成功时间超过 3 个采集周期。`error`：最近一次失败（仍可能有旧缓存）。`never`：从未成功，`counts` 为 `null`，前端显示“未加载”而不是 0。
- 403/权限撤销：`error.kind="auth"`，后端应清掉该 workspace 缓存，`counts=null`。

## `GET /api/issues`

返回所有 workspace 的“关注范围”issue，一次全量（预计几百条，不分页）。前端负责分栏、筛选与停滞判断。

关注范围 = `status_category` 不是 `done`/`cancelled` 的 issue，加上最近 7 天内 run 失败过的已关闭 issue（可选，没有也行）。

```json
{
  "generated_at": "2026-10-14T09:00:00Z",
  "items": [
    {
      "key": "ws-uuid:issue-uuid",
      "workspace_id": "ws-uuid",
      "id": "issue-uuid",
      "identifier": "ACM-163",
      "title": "看板前端：信息架构、设计与实现",
      "status": "in_progress",
      "status_category": "in_progress",
      "priority": "none",
      "assignee": { "type": "agent", "id": "uuid", "name": "前端设计师" },
      "project": null,
      "parent_identifier": "ACM-161",
      "due_date": null,
      "created_at": "...",
      "last_activity_at": "...",
      "url": "https://multica.ai/acm-lab/issues/ACM-163",
      "latest_run": {
        "task_id": "uuid",
        "status": "failed",
        "agent_name": "Builder",
        "runtime_id": "uuid",
        "started_at": "...",
        "completed_at": "...",
        "error_kind": "daemon_restart",
        "error_summary": "daemon restarted while task was in flight"
      },
      "last_failed_run": {
        "task_id": "uuid",
        "completed_at": "...",
        "error_kind": "daemon_restart"
      }
    }
  ]
}
```

- `assignee`：`type` 为 `agent` | `member` | `squad`；名字由后端 join 好，未分配为 `null`。
- `latest_run`：该 issue 最近一次 run（按 `created_at`），没有 run 为 `null`。
- `last_failed_run`：最近 7 天内最近一次失败的 run，即使之后成功也保留，前端用它显示“曾失败、已恢复”。没有为 `null`。
- `url`：后端拼接，前端只当 `<a href>` 用。不确定格式时给 `null`，前端退化为显示 identifier 不带链接。

## `GET /api/agents`

agent 矩阵，一次全量。默认包含已归档 agent（`archived=true`），前端默认隐藏。

```json
{
  "generated_at": "2026-10-14T09:00:00Z",
  "recent_window_h": 24,
  "items": [
    {
      "key": "ws-uuid:agent-uuid",
      "workspace_id": "ws-uuid",
      "id": "agent-uuid",
      "name": "Builder",
      "status": "working",
      "archived": false,
      "runtime_id": "rt-uuid",
      "model": null,
      "thinking_level": null,
      "service_tier": null,
      "max_concurrent_tasks": 6,
      "tasks": { "running": 1, "queued": 0 },
      "recent": {
        "completed": 5,
        "failed": 1,
        "last_failed_at": "...",
        "last_error_kind": "daemon_restart",
        "last_failed_issue_identifier": "ACM-162"
      },
      "url": "https://multica.ai/acm-lab/agents/agent-uuid"
    }
  ]
}
```

- `model` / `thinking_level` / `service_tier`：CLI 返回空字符串时转成 `null`，前端显示“继承默认”。
- `runtime_id` 为 `null` 表示未绑定。runtime 详情查 `/api/runtimes`，前端自己 join（同一 workspace 内）。
- `recent`：`recent_window_h` 小时内该 agent 的 run 统计，来自 `agent tasks`。
- `url`：同 issues，不确定格式时给 `null`。

## `GET /api/runtimes`

```json
{
  "generated_at": "2026-10-14T09:00:00Z",
  "items": [
    {
      "key": "ws-uuid:rt-uuid",
      "workspace_id": "ws-uuid",
      "id": "rt-uuid",
      "name": "Cursor (tr-mini-m4)",
      "provider": "cursor",
      "runtime_mode": "local",
      "status": "online",
      "last_seen_at": "...",
      "device_info": "tr-mini-m4 · 2026.10 (Cursor)"
    }
  ]
}
```

同一台机器在不同 workspace 注册为不同 runtime 记录（不同 `id`），前端按 `provider` 聚合做跨 workspace 汇总，按 `workspace_id + id` 做精确 join。

## 可选（第一阶段可不做）

`GET /api/issues/{workspace_id}/{issue_id}/runs`：最近 20 次 run 元数据（字段同 `latest_run`），用于详情抽屉。前端在该接口返回 404 时隐藏“运行历史”按钮。
