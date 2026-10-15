# Multica Board 只读 API 契约（v0.2）

状态：v0 已由 ACM-162 后端实现；v0.1 按 ACM-162 审阅把差异统一为后端现状，并新增 `meta.collector`。v0.2 新增 `GET /api/attention`（ACM-165）与 `GET /api/changes`（ACM-166）。分工：`web/`、`docs/` 归前端（ACM-163），`backend/` 归 Builder（ACM-162）。改契约先在 ACM-163 说明，再改本文件。

`web/fixtures/` 是本契约的样例响应（虚构数据），前端以 `?mock=1` 读取；`?mock=cli-down` 是“CLI 整体不可用”场景。后端实现后，同一份前端不改代码切到真实接口。

## 通用约定

- 默认监听 `127.0.0.1`，可选 `::1`，其它地址拒绝启动。同一进程在 `/` 下托管 `multica-board/web/`，在 `/api/` 下提供 JSON。
- `Host` 只接受 `127.0.0.1:<port>`（绑 `::1` 时为 `[::1]:<port>`），`localhost:<port>` 等一律 `421`。跨源 `Origin`（含 `null`）或 `Sec-Fetch-Site: cross-site` 返回 `403`。不返回 CORS 头。请用 `http://127.0.0.1:<port>/` 访问。
- 只接受 `GET`、`HEAD`，其它方法 `405`。没有任何写 Multica 的路径，也没有“立即刷新”接口；采集由后端定时器驱动。
- 响应头：`Content-Type: application/json; charset=utf-8`、`Cache-Control: no-store`、`X-Content-Type-Options: nosniff`、`X-Frame-Options: DENY`、`Referrer-Policy: no-referrer`、`Cross-Origin-Resource-Policy: same-origin`。静态页 CSP：`default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'`。前端不发任何外部请求（不加载头像、字体、CDN）。
- 时间一律 ISO 8601 UTC 字符串；缺失为 `null`，不用空字符串。CLI 返回的空字符串由后端转成 `null`。
- 每个响应带 `generated_at` = **最近一次采集轮次完成的时间**（不是响应时间）；从未完成过采集为 `null`。前端按“`generated_at` 变了才重拉列表”工作。
- 每条记录带 `key = "<workspace_id>:<resource_id>"`，前端用它去重和做 DOM key。
- 错误：`{"error": {"code": "not_found", "message": "..."}}`，配合 4xx/5xx。
- **脱敏白名单**：只输出本文列出的字段。不输出 token、`instructions`、`custom_env*`、`custom_args`、`mcp_config`、`runtime_config`、`launch_header`、`metadata`、avatar URL、成员邮箱、issue/评论正文、run 输出与 transcript。错误文本先归类为 `error_kind`，再脱敏（token、`key=value`、长 hex/base64、home 路径）并截断到 200 字符。

### 枚举

`error_kind`（后端按此顺序匹配，第一个命中即为结果）：`quota` → `rate_limit` → `auth` → `model_unavailable` → `daemon_restart` → `timeout` → `network` → `cancelled` → `process`（进程退出、初始化失败、崩溃、找不到 CLI）→ `unknown`。前端遇到不认识的值按 `unknown` 显示。

`run.status`：沿用 CLI 原值（`queued` | `dispatched` | `waiting_local_directory` | `running` | `completed` | `failed` | `cancelled` …）。前端特判 `failed`、`running`，以及排队类 `queued` / `dispatched` / `waiting_local_directory`。

`collect_state`（workspace 采集状态）：`ok` | `stale`（上次成功超过 3 个采集周期）| `error`（最近一次失败，可能仍有旧缓存）| `never`（从未成功）

## `GET /api/meta`

顶栏与健康状态。前端每 15 秒轮询一次。

```json
{
  "api_version": "0",
  "generated_at": "2026-10-14T09:00:00Z",
  "collect_interval_s": 60,
  "multica_web_base": "https://multica.ai",
  "collector": {
    "last_attempt_at": "2026-10-14T08:59:40Z",
    "last_success_at": "2026-10-14T08:59:40Z",
    "error": null
  },
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

- `collect_interval_s`：等于 `serve --refresh`。`0` 表示只读缓存、不采集，前端不再按周期判断快照过期。
- `collector`（**v0.1 新增，必改项**）：全局采集状态，来自 `sync_status` 的 `*` / `workspaces` 行，即每轮第一步 `workspace list` 的结果。
  - `last_attempt_at` / `last_success_at`：该步骤最近一次尝试 / 成功的时间，没有为 `null`。
  - `error`：最近一次尝试失败时为 `{"kind", "message"}`，成功后清为 `null`。典型情况：没登录或 profile 写错（`auth`）、找不到 CLI（`process`，例如 `multica CLI not found: <path>`）、网络/超时。`message` 必须先过 `redact`（会去掉 home 路径），再截断到 200 字符。
  - 这一步失败时后端不会更新 `generated_at` 和各 workspace 状态，所以前端以 `collector.error` 为准显示全局错误，而不是等各 workspace 变成 `stale`。
  - 进程刚启动、第一轮还没跑完：三个字段都为 `null`。
  - 后端未实现该字段时前端视为缺省，不报错。
- `workspaces[]`：
  - `error.kind="auth"`（403/权限撤销）时后端清掉该 workspace 缓存，`counts=null`。返回数据的 `workspace_id` 与请求不一致时，丢弃整批数据并同样标 `error` / `auth`。
  - `never` 时 `counts=null`，前端显示“未加载”而不是 0。
  - 只要轮次跑完 `generated_at` 就会更新，即使所有 workspace 都失败；前端以各 workspace 的 `collect_state` 判断。

## `GET /api/issues`

所有 workspace 的“关注范围”issue，一次全量，不分页（ACM Lab 约 150 条；有 workspace 超过 500 条时再讨论分页）。前端负责分栏、筛选与停滞判断。

关注范围 = `status_category` 不是 `done` / `cancelled` 的 issue，加上 7 天内 run 失败过的已关闭 issue。

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
      "project": { "id": "uuid", "title": "Multica Board" },
      "parent_identifier": "ACM-161",
      "due_date": null,
      "created_at": "...",
      "last_activity_at": "...",
      "url": "https://multica.ai/acm-lab/issues/issue-uuid",
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

- `assignee`：`type` 为 `agent` | `member` | `squad`，名字由后端 join。`squad` 的 `name` 在 v0 为 `null`（第一阶段不采集 squad）。未分配为 `null`。
- `project`：`{id, title}`；`title` 在项目不在缓存时为 `null`。没有项目为 `null`。
- `parent_identifier`：只有父 issue 也在缓存里时才有值，否则为 `null`（例如父 issue 已关闭）。
- `latest_run`：按 `agent tasks` 的 `issue_id` 关联得到的最近一次 run。后端只缓存最近 14 天（`--run-days`）的 run，所以 `null` 表示“14 天内没有 run”，不代表从未运行。
- `last_failed_run`：7 天内最近一次失败的 run，之后成功也保留，前端用它显示“曾失败、已恢复”。没有为 `null`。
- `url`：`{multica_web_base}/{slug}/issues/{issue_uuid}`（路径用 UUID，不是 identifier）。无法拼接时为 `null`，前端退化为不带链接。

## `GET /api/issues/{workspace_id}/{issue_id}/runs`

单个 issue 最近 20 次 run（缓存窗口内），按 `created_at` 倒序，用于前端“运行历史”抽屉。issue 不在缓存时 `404`。

```json
{
  "generated_at": "2026-10-14T09:00:00Z",
  "items": [
    {
      "key": "ws-uuid:task-uuid",
      "task_id": "task-uuid",
      "status": "failed",
      "agent_name": "Builder",
      "runtime_id": "uuid",
      "started_at": "...",
      "completed_at": "...",
      "error_kind": "process",
      "error_summary": "process exited with code 1"
    }
  ]
}
```

## `GET /api/agents`

agent 矩阵，一次全量。包含已归档 agent（`archived=true`），前端默认隐藏。

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

- `model` / `thinking_level` / `service_tier`：未设置为 `null`，前端显示“默认”。
- `runtime_id` 为 `null` 表示未绑定。runtime 详情查 `/api/runtimes`，前端按 `workspace_id + id` join。
- `tasks.queued` 包含 `queued` / `dispatched` / `waiting_local_directory`。
- `recent`：`recent_window_h` 小时内的 run 统计。`last_failed_issue_identifier` 只有该 issue 在缓存里时才有值。
- `url`：`{multica_web_base}/{slug}/agents/{agent_uuid}`，无法拼接时为 `null`。

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

`name` 优先用 runtime 的自定义名。同一台机器在不同 workspace 注册为不同 runtime 记录（不同 `id`），前端按 `provider` 聚合做跨 workspace 汇总，按 `workspace_id + id` 精确 join。

## `GET /api/attention`

给 Steward 的一次请求摘要。看板页面不调用这个接口。只读缓存，字段是白名单，不带 URL、正文、错误原文、runtime 设备信息。

查询参数 `format`：缺省或 `json` 返回 JSON；`text` 返回纯文本。大小写不敏感，前后空白忽略。其它值 `400`，`{"error":{"code":"bad_request","message":"format must be json or text"}}`。

```json
{
  "generated_at": "2026-10-14T09:00:00Z",
  "window_h": 24,
  "workspaces": [
    {
      "id": "ws-uuid",
      "slug": "acm-lab",
      "name": "ACM Lab",
      "in_review": [
        {
          "identifier": "ACM-165",
          "title": "Multica Board API：需处理事项摘要接口",
          "status": "in_review",
          "time": "2026-10-15T05:00:04Z",
          "agent_name": "Builder"
        }
      ],
      "failed_runs": [
        {
          "identifier": "ACM-162",
          "title": "后端采集",
          "status": "failed",
          "time": "2026-10-14T08:00:00Z",
          "agent_name": "Builder"
        }
      ],
      "offline_runtimes": [
        {
          "identifier": "rt-uuid",
          "title": "Cursor (tr-mini-m4)",
          "status": "offline",
          "time": "2026-10-14T04:00:00Z",
          "agent_name": "Builder"
        }
      ]
    }
  ]
}
```

四个桶是 `in_review`、`blocked`、`failed_runs`、`offline_runtimes`。空桶不出现，四个桶都空的 workspace 也不出现。

- 每条只含 `identifier`、`title`、`status`、`time`、`agent_name`。`title` 先把连续空白压成一个空格，再截到 80 个字符（不加省略号）。没有的字段为 `null`，不用空字符串。
- `in_review` / `blocked`：`status` 或 `status_category` 为这两个值的 issue（小写比较）。两者不一致时以 `status` 为准，只进一个桶。条目的 `status` 用 issue 自己的 `status`，没有则用桶名。`time` 取 `last_activity_at`，否则 `updated_at`，否则 `created_at`。`agent_name` 只在负责人是 agent 时填名字，成员、squad、未分配都是 `null`。`identifier` 没有时退回 issue id。
- `failed_runs`：近 `window_h`（24）小时内 `status=failed` 的 run，按 `completed_at`（没有则 `created_at`）计算。之后又成功的也保留。`identifier` 优先用 issue 的 identifier，否则 issue id，否则 task id。`title` 是 issue 标题，issue 不在缓存里为 `null`。`status` 固定 `failed`。`agent_name` 是跑这次 run 的 agent。
- `offline_runtimes`：`status` 为 `offline`（小写比较），且至少有一个未归档 agent 的 `runtime_id` 指向它。归档 agent 不算绑定。`identifier` 是 runtime id，`title` 优先自定义名，`time` 是 `last_seen_at`，`status` 固定 `offline`。多个 agent 的名字按名字排序后用 `, ` 拼进 `agent_name`；没有名字时用 agent id。
- 同一 workspace 内按 `time` 倒序，没有时间的排最后；时间相同按 `identifier` 升序。workspace 按 `name` 不区分大小写，再按 `id`。
- `?format=text` 是紧凑纯文本，`Content-Type: text/plain; charset=utf-8`，目标小于 2KB。超过 2048 字节时从末尾整行丢掉，并在最后一行写 `…`（全文计数仍写在桶标题上）。空桶不出现。列之间用制表符，顺序为 identifier、status、time、agent 名、title。缺失单元格是 `-`。字段里的换行和制表符会先被压成空格，所以一行一条。

```text
generated_at 2026-10-14T09:00:00Z
window_h 24

# acm-lab ACM Lab
in_review 1
ACM-165	in_review	2026-10-15T05:00:04Z	Builder	Multica Board API：需处理事项摘要接口
failed_runs 1
ACM-162	failed	2026-10-14T08:00:00Z	Builder	后端采集
offline_runtimes 1
rt-uuid	offline	2026-10-14T04:00:00Z	Builder	Cursor (tr-mini-m4)
```

## `GET /api/changes`

增量，用来避免每次采集都重拉 `/api/issues`、`/api/runtimes` 和 run 列表。`?since=` 省略时只签发 cursor、不返回历史。`since` 为 ISO 8601 UTC 时间，或上一次响应里的 `cursor`。

```json
{
  "generated_at": "2026-10-15T05:10:00Z",
  "cursor": "v1:6",
  "reset": false,
  "retained_days": 7,
  "has_more": false,
  "items": [
    {
      "kind": "issue_status",
      "at": "2026-10-15T05:10:00Z",
      "key": "ws-uuid:issue-uuid",
      "workspace_id": "ws-uuid",
      "id": "issue-uuid",
      "identifier": "ACM-166",
      "status": "in_review",
      "status_category": "in_review",
      "previous_status": "in_progress",
      "previous_status_category": "in_progress"
    },
    {
      "kind": "issue_assignee",
      "at": "2026-10-15T05:10:00Z",
      "key": "ws-uuid:issue-uuid",
      "workspace_id": "ws-uuid",
      "id": "issue-uuid",
      "identifier": "ACM-166",
      "assignee": { "type": "agent", "id": "uuid", "name": "Builder" },
      "previous_assignee": { "type": "member", "id": "uuid", "name": "Yi" }
    },
    {
      "kind": "run",
      "at": "2026-10-15T05:10:00Z",
      "key": "ws-uuid:task-uuid",
      "workspace_id": "ws-uuid",
      "task_id": "task-uuid",
      "issue_id": "issue-uuid",
      "identifier": "ACM-166",
      "status": "running",
      "agent_name": "Builder",
      "runtime_id": "rt-uuid",
      "started_at": "2026-10-15T05:09:00Z",
      "completed_at": null,
      "error_kind": null,
      "error_summary": null
    },
    {
      "kind": "run_failed",
      "at": "2026-10-15T05:10:00Z",
      "key": "ws-uuid:task-uuid",
      "workspace_id": "ws-uuid",
      "task_id": "task-uuid",
      "issue_id": "issue-uuid",
      "identifier": "ACM-166",
      "status": "failed",
      "agent_name": "Builder",
      "runtime_id": "rt-uuid",
      "started_at": "2026-10-15T05:09:00Z",
      "completed_at": "2026-10-15T05:10:00Z",
      "error_kind": "process",
      "error_summary": "process exited with code 1"
    },
    {
      "kind": "runtime",
      "at": "2026-10-15T05:10:00Z",
      "key": "ws-uuid:rt-uuid",
      "workspace_id": "ws-uuid",
      "id": "rt-uuid",
      "name": "Cursor (tr-mini-m4)",
      "status": "offline",
      "previous_status": "online"
    }
  ]
}
```

历史不完整时仍是 `200`，`reset: true`、`items: []`，`cursor` 停在当前末端。客户端应重拉全量，再从这个 cursor 继续：

```json
{
  "generated_at": "2026-10-15T05:10:00Z",
  "cursor": "v1:128",
  "reset": true,
  "retained_days": 7,
  "has_more": false,
  "items": []
}
```

- `at` 是采集器**观察到**变化的时间（秒），不是 issue 的 `updated_at`。比较按秒截断，`since` 为开区间（`at > since`）。同一秒里的事件用返回的 cursor 续拉，不要用事件自己的 `at`。
- `cursor` 形如 `v1:<序号>`，不透明，只回传响应里的值。`v1:0` 表示从日志开头重放；若开头已被裁掉则 `reset: true`。序号比当前末端还大（例如换过缓存库）同样 `reset`。
- 不带 `since`：`items` 为空，`cursor` 为当前末端。接入时先拉全量，再立刻取一次 cursor，之后只传 cursor。
- 单次最多 500 条，按发生顺序。`has_more: true` 时把返回的 `cursor` 当作下一次 `since`。
- 保留 7 天（`retained_days`）。更早的 `since`，或 cursor 前面有被裁掉的序号，返回 `reset: true`，不把残缺窗口伪装成完整增量。
- 每个 workspace、每种资源的**第一次成功快照只建基线，不产生事件**（issues、runtimes，以及每个 agent 的 tasks 各自一份）。基线之后才记录：
  - `issue_status` / `issue_assignee`：已在快照里的 issue，状态或指派变了。新建 issue、标题、优先级、从列表消失都不记。
  - `run`：基线之后新出现的 task。已经是 `failed` 时紧跟一条 `run_failed`。
  - `run_failed`：基线里已有的 run 后来变成 `failed`，或上面这条新的失败 run。`completed` 等其它状态变化不记。
  - `runtime`：是否等于 `online` 发生了变化。新出现且已是 `online` 记上线（`previous_status: null`）。原先 `online`、这次不在快照里，`status: null`。`offline` 与 `connecting` 之间不算。
- 指派和 agent 名在读的时候 join，规则与 `/api/issues` 相同（`squad` 的 `name` 为 `null`）。`error_summary` 已脱敏。
- 撤权清缓存时，该 workspace 的变更和基线一起删掉。下一次成功采集重新建基线，不会把旧快照再放进增量。
- 非法 `since`（不是 UTC 时间也不是 cursor）为 `400`，`error.code=bad_request`。

## 变更记录

- v0.2（2026-10-15）：ACM-165 新增 `GET /api/attention`，按 workspace 汇总 in_review、blocked、近 24 小时失败 run、仍绑着 agent 的离线 runtime。`?format=text` 输出不超过 2048 字节的纯文本。
- v0.2（2026-10-15）：新增 `GET /api/changes`（ACM-166）。`since` 为 ISO 时间或上次的 cursor；缓存保留 7 天的 issue 状态/指派、新 run、run 失败、runtime 上下线。
- v0.1（2026-10-15）：按 ACM-162 审阅以后端为准：`error_kind` 加 `process` 并定匹配顺序；`url` 改用 UUID；`generated_at` 定义为最近一次采集完成时间；`project` 定为 `{id, title}`；`/runs` 记录带 `key`，转为第一阶段接口；`parent_identifier`、`last_failed_issue_identifier`、squad `name` 的空值规则；`::1`、421、更严的响应头与 CSP。新增 `meta.collector`。
- v0（2026-10-14）：初稿。
