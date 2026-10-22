# Multica Board 只读 API 契约（v0.6）

状态：v0 已由 ACM-162 后端实现；v0.1 按 ACM-162 审阅把差异统一为后端现状，并新增 `meta.collector`。v0.2 新增 `GET /api/attention`（ACM-165）、`GET /api/changes`（ACM-166）与 `GET /api/stats/failures`（ACM-167）。v0.3（ACM-174）在 `/api/attention` 增加 `missing_hermes_async_delegation`。v0.4（ACM-179）在 `/api/attention` 增加 `agent_updates`。v0.5（ACM-192）在 `/api/issues` 增加 `layer`，新增 `GET /api/layers`，并给 `GET /api/changes` 增加 `?kind=`。v0.6（ACM-182）新增 `GET /api/skills`、`/api/skills/meta`、`/api/skills/attention`。分工：`web/`、`docs/` 归前端（ACM-163），`backend/` 归 Builder（ACM-162）。改契约先在 ACM-163 说明，再改本文件。

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
      },
      "layer": {
        "primary": "L5",
        "involved": ["L3"],
        "governance": false,
        "source": {
          "primary": "own",
          "involved": "inherited:ACM-181",
          "governance": null
        }
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
- `layer`（**v0.5**）：这条 issue 归在哪一层。后端用缓存的 `properties` 和属性定义解析，前端不再判断选项文案。
  - `primary`：`L1` | `L2` | `L3` | `L4` | `L5` | `outside` | `null`。`outside` 是选项「不在地图」。`null` 是未归层（继承之后仍然没有主层，包括只填了涉及层的）。
  - `involved`：`L1`–`L5` 的数组，按 L1→L5 排。已经包含主层的会去掉。主层是 `outside` 时恒为 `[]`。
  - `governance`：`true` | `false` | `null`。`null` 表示这个字段没人填过。只有 `true` 计入治理线。
  - `source.primary` / `source.involved` / `source.governance`：各自独立，`own`、`inherited:<identifier>` 或 `null`。来源 identifier 是沿父 issue 向上最近一个自己填了该字段的祖先。父 issue 不在缓存里时停住，该字段保持空。
  - 选项名以 `L1`–`L5` 加空格（或只有这一个代号）识别；「不在地图」按全名识别。以后改显示文字不影响代号。
  - 没有这三个属性定义时，三个值都是空，`source` 全是 `null`。非 ACM Lab 的 issue 同样带这个对象，通常是空的。

## `GET /api/layers`

ACM Lab（workspace slug `acm-lab`）的地图计数。其它 workspace 不在这里，它们的层信息只在 `/api/issues` 的 `layer` 上。

```json
{
  "generated_at": "2026-10-14T09:00:00Z",
  "workspace": { "id": "uuid", "slug": "acm-lab", "name": "ACM Lab" },
  "ready": true,
  "header": { "total": 8, "assigned": 6, "open": 6, "decide": 4 },
  "layers": [
    {
      "id": "L5",
      "name": "感知与决策",
      "total": 0,
      "open": 0,
      "decide": 0,
      "involved_extra": 0,
      "involved_from": [],
      "governance_open": 0,
      "governance_decide": 0,
      "decide_items": []
    }
  ],
  "outside": {
    "total": 1,
    "open": 1,
    "decide": 0,
    "governance_open": 0,
    "governance_decide": 0,
    "decide_items": []
  },
  "unassigned": {
    "total": 1,
    "open": 1,
    "decide": 0,
    "governance_open": 0,
    "governance_decide": 0,
    "decide_items": []
  }
}
```

- `layers` 固定 5 个，顺序是地图从上到下：L5、L4、L3、L2、L1。`name` 是短名，不含 `L` 编号。
- `ready`：缓存里同时有「主层」（select）、「涉及层」（multi_select）、「治理线」（checkbox）时为 `true`。属性还没建时为 `false`，此时每条未取消的 issue 都算未归层，地图应显示引导而不是一屏 0。
- `workspace`：slug 不是 `acm-lab` 的缓存里没有这个 workspace 时为 `null`，计数全是 0。
- 计数都排除 `cancelled` / `canceled`。一条 issue 只进入一个格子：`layers[].total`、`outside.total` 或 `unassigned.total`。三者之和等于 `header.total`。
- `total`：主层是该层。`open`：其中状态不是 `done` 的（含 backlog）。`decide`：`in_review` 或 `blocked`，是 `open` 的子集；`status` 是这两个值时优先于 `status_category`。
- `header.assigned`：五个层的 `total` 之和（已归层）。`header.open` / `header.decide` 含「不在地图」和未归层。
- `involved_extra`：涉及层包含本层、主层在另外一个 L1–L5、且未取消。不进任何 `total`。`involved_from` 是这些 issue 的主层代号，按 L1→L5。
- `governance_open` / `governance_decide`：治理线为 `true` 且主层是该层（或「不在地图」）。未归层上的治理线不进任何格子。
- `decide_items`：该格里待你决定的前 3 条，等待最久的在前。`waiting_since` 是进入当前状态的时间，取自 `/api/changes` 的 `issue_status`；7 天窗口里没有这条迁移时退回 `last_activity_at`，并把 `waiting_exact` 设为 `false`（前端在时长前加 `≥`）。字段还有 `key`、`id`、`identifier`、`title`、`status`、`governance`、`url`。

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
- `description`：agent 简介，入库前经 `redact()`，没有则为 `null`。
- `instructions`：规范化后的指令全文，入库前经 `redact()`（不截断到错误摘要的 200 字）。`sk-`、`Bearer`、`ghp_`、JWT、长 hex 等被换成 `[redacted]`。没有则为 `null`。
- `instructions_sha256`：规范化原文（NFC、换行统一为 LF、去掉首尾空白）的 SHA-256，在脱敏之前计算。原文不入库。
- `skills`：对象数组，元素是 `{id, name, description, enabled}`。不含 skill 正文。未采集时为 `[]`。
- `disabled_runtime_skills`：被关掉的 runtime skill 名称（或 `{id, name}`）。未采集时为 `[]`。

`custom_env`、`mcp_config`、`runtime_config`、`launch_header` 不采集。某个 workspace 返回 `error.kind=auth` 时，该 workspace 的缓存（含 `workspace_skills`）整表清空。

## `GET /api/bots`

本机 Grok Bot 花名册。只读 sand-client 的 5 个切片，以及 `routes.json` 和 `outbox.json`。不读 transcript、草稿、secret、Cookies、credential 或 Keychain。目录不存在或未登录时：

```json
{"ok": false, "reason": "absent", "items": []}
```

登录后 `ok: true`。`account_slot` 只保留账号标识末 8 位。`items` 是花名册（单人 Bot 与 Room），带分组、`pinned`、`is_group`、`member_ids`。`weekly_usage` 是账号级周额度。`delivery` 是 bridge 的 `shadow` 或 `send`。每个 item 的 `bridge` 含负责的 workspace、webhook `routine`、`delivery`、outbox 的 `pending` / `dead_letter` / `shadow_record`，以及该目的地的断路器。数量以本机 roster 为准。

## `GET /api/skills` 的 `workspace_installs`

每条本地 skill 增加 `workspace_installs`：各 workspace 里同名的 Multica Workspace skill，以及该 workspace 中挂载它的 agent（`id`、`name`、`enabled`）。Multica Workspace skill 来自 `multica skill list`，缓存在 `workspace_skills`（键 `server + workspace_id + id`）。

ACM-349：本机 `local_source.content_hash` 为 SKILL.md 原始字节的 SHA-256；`summary` 保留完整描述。Multica Workspace`workspace_installs[].content_hash` 优先取列表，缺失时通过不带 `--with-content` 的 `skill get` 补采，仅缓存合法 SHA-256，不保存正文；同 workspace、skill ID、非空 updated_at 未变时复用。获取失败可返回 null，鉴权或 workspace 越界仍按原有隔离规则处理。前端优先比较哈希，相同即忽略 mtime；不同只说明内容不同。缺少哈希时才按时间推断并明确提示，时间未知时仅比较完整描述。`summary` 在合并仅 Multica Workspace 条目后重算；未登记安装的 `reconciliation_status` 使用 `unrecorded`，卡片、行和状态筛选统一显示「未登记」，有记录但未核实仍为 `unverified`。

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
      "agent_updates": [
        {
          "identifier": "ACM-129",
          "title": "门禁",
          "status": "in_progress",
          "time": "2026-10-15T08:21:09Z",
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
      ],
      "missing_hermes_async_delegation": [
        {
          "identifier": "agent-uuid",
          "title": "Vega",
          "status": "missing_skill",
          "time": "2026-10-16T00:26:18Z",
          "agent_name": "Vega"
        }
      ]
    }
  ]
}
```

六个桶是 `in_review`、`blocked`、`agent_updates`、`failed_runs`、`offline_runtimes`、`missing_hermes_async_delegation`。空桶不出现，六个桶都空的 workspace 也不出现。

- 每条只含 `identifier`、`title`、`status`、`time`、`agent_name`。`title` 先把连续空白压成一个空格，再截到 80 个字符（不加省略号）。没有的字段为 `null`，不用空字符串。
- `in_review` / `blocked`：`status` 或 `status_category` 为这两个值的 issue（小写比较）。两者不一致时以 `status` 为准，只进一个桶。条目的 `status` 用 issue 自己的 `status`，没有则用桶名。`time` 取 `last_activity_at`，否则 `updated_at`，否则 `created_at`。`agent_name` 只在负责人是 agent 时填名字，成员、squad、未分配都是 `null`。`identifier` 没有时退回 issue id。
- `agent_updates`：负责人是 agent，且当前状态不是 `done` / `cancelled` / `backlog` 的 issue。近 `window_h`（24）小时内，该 agent 有 `status=completed` 的 run，并且 changes 日志里没有一次进入 `in_review` 或 `blocked` 的迁移落在该 run 完成时间前后 15 分钟内。`time` 是这次 run 的 `completed_at`（没有则 `created_at`）。`status` 用 issue 自己的当前状态，所以停在 `in_progress` 或早已处于 `in_review` 的新交付都会出现。`agent_name` 是负责人。判断与状态桥接共用同一函数：桥接以该 agent 的新评论为主信号，没有评论时才用 run 完成兜底，并丢掉纯确认和重复回复；看板缓存没有评论正文，因此这里只呈现 run 兜底。已被同期状态迁移覆盖的交付不进这个桶。issue 仍可同时留在 `in_review` 或 `blocked`，那是当前状态，不与本桶合成一条。
- `failed_runs`：近 `window_h`（24）小时内 `status=failed` 的 run，按 `completed_at`（没有则 `created_at`）计算。之后又成功的也保留。`identifier` 优先用 issue 的 identifier，否则 issue id，否则 task id。`title` 是 issue 标题，issue 不在缓存里为 `null`。`status` 固定 `failed`。`agent_name` 是跑这次 run 的 agent。
- `offline_runtimes`：`status` 为 `offline`（小写比较），且至少有一个未归档 agent 的 `runtime_id` 指向它。归档 agent 不算绑定。`identifier` 是 runtime id，`title` 优先自定义名，`time` 是 `last_seen_at`，`status` 固定 `offline`。多个 agent 的名字按名字排序后用 `, ` 拼进 `agent_name`；没有名字时用 agent id。
- `missing_hermes_async_delegation`：未归档、且 `runtime_id` 指向 `provider` 为 `hermes`（不区分大小写）的 agent，启用中的 workspace skill 名单里没有 `hermes-async-delegation`。`identifier` 是 agent id，`title` 和 `agent_name` 都是 agent 名，`status` 固定 `missing_skill`，`time` 是 `updated_at`。归档 agent、非 Hermes runtime、runtime 不在缓存里、以及已启用该 skill 的 agent 都不出现。采集只缓存启用的 skill 名（`enabled: false` 视为未挂）；skill 正文不入库，`/api/agents` 也不返回这份名单。
- 同一 workspace 内按 `time` 倒序，没有时间的排最后；时间相同按 `identifier` 升序。workspace 按 `name` 不区分大小写，再按 `id`。
- `?format=text` 是紧凑纯文本，`Content-Type: text/plain; charset=utf-8`，目标小于 2KB。超过 2048 字节时从末尾整行丢掉，并在最后一行写 `…`（全文计数仍写在桶标题上）。空桶不出现。列之间用制表符，顺序为 identifier、status、time、agent 名、title。缺失单元格是 `-`。字段里的换行和制表符会先被压成空格，所以一行一条。

```text
generated_at 2026-10-14T09:00:00Z
window_h 24

# acm-lab ACM Lab
in_review 1
ACM-165	in_review	2026-10-15T05:00:04Z	Builder	Multica Board API：需处理事项摘要接口
agent_updates 1
ACM-129	in_progress	2026-10-15T08:21:09Z	Builder	门禁
failed_runs 1
ACM-162	failed	2026-10-14T08:00:00Z	Builder	后端采集
offline_runtimes 1
rt-uuid	offline	2026-10-14T04:00:00Z	Builder	Cursor (tr-mini-m4)
missing_hermes_async_delegation 1
agent-uuid	missing_skill	2026-10-16T00:26:18Z	Vega	Vega
```

## `GET /api/changes`

增量，用来避免每次采集都重拉 `/api/issues`、`/api/runtimes` 和 run 列表。`?since=` 省略时只签发 cursor、不返回历史。`since` 为 ISO 8601 UTC 时间，或上一次响应里的 `cursor`。`?kind=` 可选，省略时仍返回全部类型。

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
- 单次最多 500 条，按发生顺序。`has_more: true` 时把返回的 `cursor` 当作下一次 `since`。带了 `kind` 时，下一页要原样带上同一个 `kind`：cursor 仍是整份日志里的序号，过滤发生在取页时，所以翻页不会再被其它类型占满。
- `kind`（**v0.5**）：`status`（`issue_status` 的别名）、`issue_status`、`assignee`（`issue_assignee` 的别名）、`issue_assignee`、`run`、`run_failed`、`runtime`。省略表示不过滤。未知值、空值或重复的 `kind` 为 `400`。不带 `since` 时 `kind` 不改变「只签发 cursor」的行为。待你决定页用 `?kind=status` 读取进入当前状态的时间。
- 保留 7 天（`retained_days`）。更早的 `since`，或 cursor 前面有被裁掉的序号，返回 `reset: true`，不把残缺窗口伪装成完整增量。
- 每个 workspace、每种资源的**第一次成功快照只建基线，不产生事件**（issues、runtimes，以及每个 agent 的 tasks 各自一份）。基线之后才记录：
  - `issue_status` / `issue_assignee`：已在快照里的 issue，状态或指派变了。新建 issue、标题、优先级、从列表消失都不记。
  - `run`：基线之后新出现的 task。已经是 `failed` 时紧跟一条 `run_failed`。
  - `run_failed`：基线里已有的 run 后来变成 `failed`，或上面这条新的失败 run。`completed` 等其它状态变化不记。
  - `runtime`：是否等于 `online` 发生了变化。新出现且已是 `online` 记上线（`previous_status: null`）。原先 `online`、这次不在快照里，`status: null`。`offline` 与 `connecting` 之间不算。
- 指派和 agent 名在读的时候 join，规则与 `/api/issues` 相同（`squad` 的 `name` 为 `null`）。`error_summary` 已脱敏。
- 撤权清缓存时，该 workspace 的变更和基线一起删掉。下一次成功采集重新建基线，不会把旧快照再放进增量。
- 非法 `since`（不是 UTC 时间也不是 cursor）为 `400`，`error.code=bad_request`。

## `GET /api/stats/failures`

按 provider、runtime、agent 汇总失败的 run，用来判断额度与稳定性。只读，走和其它 `/api/*` 相同的 Host / 跨源校验与安全头。不返回 `error_summary` 或原始错误文本。

```json
{
  "generated_at": "2026-10-14T09:00:00Z",
  "windows": { "h24": 24, "d7": 168 },
  "by_provider": [
    {
      "provider": "cursor",
      "failed": { "h24": 3, "d7": 6 },
      "top_reasons": [
        { "failure_reason": "quota", "h24": 2, "d7": 3 },
        { "failure_reason": "rate_limit", "h24": 1, "d7": 2 },
        { "failure_reason": "timeout", "h24": 0, "d7": 1 }
      ]
    }
  ],
  "by_runtime": [
    {
      "key": "ws-uuid:rt-uuid",
      "workspace_id": "ws-uuid",
      "id": "rt-uuid",
      "name": "Cursor (tr-mini-m4)",
      "provider": "cursor",
      "failed": { "h24": 3, "d7": 6 },
      "top_reasons": [
        { "failure_reason": "quota", "h24": 2, "d7": 3 },
        { "failure_reason": "rate_limit", "h24": 1, "d7": 2 },
        { "failure_reason": "timeout", "h24": 0, "d7": 1 }
      ]
    }
  ],
  "by_agent": [
    {
      "key": "ws-uuid:agent-uuid",
      "workspace_id": "ws-uuid",
      "id": "agent-uuid",
      "name": "Builder",
      "runtime_id": "rt-uuid",
      "failed": { "h24": 2, "d7": 4 },
      "top_reasons": [
        { "failure_reason": "quota", "h24": 2, "d7": 3 },
        { "failure_reason": "process", "h24": 0, "d7": 1 }
      ]
    }
  ]
}
```

- 只统计 `status=failed` 的 run。时间取 `completed_at`，没有则用 `created_at`，和响应时刻比较（与 `/api/agents` 的 `recent` 相同，不是 `generated_at`）。`h24` 是 `d7` 的子集。
- `failure_reason` 就是入库时归类的 `error_kind`（`quota` → … → `unknown`）。CLI 的 task 没有单独的 failure reason 字段，原始 `error` 文本不进入本接口。没有 `error_kind` 的失败记为 `unknown`。
- `failed` 是该组窗口内的全部失败次数。`top_reasons` 最多 3 条，按 `d7`、`h24`、`failure_reason` 排序；被截掉的原因仍算在 `failed` 里。
- `by_provider` 把各 workspace 里同一 `provider` 加总（额度通常按 provider 共享）。runtime 不在缓存、或 `provider` 为空时归入 `"provider": null`。
- `by_runtime` / `by_agent` 按 `workspace_id + id` 分开，不跨 workspace 合并。runtime 的 `name` 规则同 `/api/runtimes`。agent 的 `runtime_id` 是**当前绑定**，不是每条 run 当时的 runtime；run 记在哪个 runtime 上，由 `by_runtime` 和 `by_provider` 决定。
- 7 天内没有失败的组不出现。缓存只保留 `--run-days`（默认 14）天的 run，所以 7 天窗口在默认配置下是完整的。
- 排序：`failed.d7` 降序，其次 `failed.h24`，再按名称；名称为空的排在后面。

## `GET /api/skills`、`/api/skills/meta`、`/api/skills/attention`

Skills 属于这台机器，不放进按 workspace 分组的 `/api/attention`。三个接口共用同一次合并（清单 + drift + 工具根配置 + 未激活暂存区）。`?review_days=` 只接受 `30`、`90`、`180`，省略是 `90`。

- `GET /api/skills`：`items` 是清单和未登记安装按名字合并后的行，带 `tools`（`direct` / `shared` / `missing` / `null`）、`bucket`、`baseline_debt`。`attention` 与下面的 attention 接口相同。`ok: false` 表示清单读不出来，此时 `items` 为空，不把「没检查」显示成「没问题」。
- `skills_schema: 2`：payload 结构版本（含 `ok:false` 的错误 payload）。前端见到缺失或 <2 视为 `legacy_payload`：所有条目按 `legacy`（旧数据）渲染、不计入未扫描，并显示「后端数据版本较旧，共享入口与仓库入口未检查」。schema≥2 时某条 `shared_entry` 缺失/非对象/state 不可识别 ⇒ 客户端 `unknown`/`field_invalid`。
- `shared_root`：共享根探测 `{path, state, reason, detail}`；`state` ∈ `ok | absent | denied | unknown | unscanned`（未配置共享根 → `path:null`/`unscanned`/`no_shared_root`；清单无效也是 `unscanned`，表示探测根本没跑）。`repos` 是 `[{id, label, path, identity_state, reason}]`；`repo_id` 是**本次 payload 的展示分组键，非持久**：`realpath(repo_root, strict)` 派生，重启/移动/改名会变，不是 Multica 项目引用，不得存储或当 join key；realpath 失败 ⇒ 单条 `{id:"unknown", label:"未知仓库", identity_state:"unknown", reason: repo_root_absent|repo_root_denied|repo_root_error}`，`"unknown"` 不表示跨 payload/跨仓库相等。目录移动 ⇒ 新 id；大小写变体不合并。
- 每条 item 带 `shared_entry`：`{state,label,reason,errno,step,path,target,resolved,link_kind,target_scope,detail,root}`，探测 `<共享根>/<name>`。步骤按固定顺序、首个终止态胜出：P0（无系统调用：无共享根→`unscanned/no_shared_root`；名字非法→`unknown/invalid_name`）→ S1 `_stat(根)`（denied/none `root_absent`/unknown `root_loop`/`root_error`/`root_not_dir`）→ S2 `_lstat(入口)`（ENOENT→`none/entry_absent`；EACCES→`denied/entry_denied`；其它含 ENOTDIR/ELOOP→`unknown/entry_error`）→ S3 `_readlink` → S4 `_stat(入口)`（仅链接：ENOENT/ENOTDIR/ELOOP→`broken_link` `target_absent`/`target_enotdir`/`link_loop`；EACCES→`denied/target_denied`）→ S5 `realpath(strict)`（只算 `target_scope`：inside/outside_shared_root，纯信息，不改 state）→ S6 SKILL.md（缺失/悬空链接/非普通文件/空文件→`content_missing`；EACCES/读失败→`denied`/`unknown`）→ S7 `link`/`dir`。`target/resolved/errno/link_kind/target_scope` 为 null 表示"该步未到达/不适用"，绝不表示"没有"。意外异常 ⇒ `unknown/probe_exception`。区别记牢：`unscanned`=后端没跑探测；`none/root_absent`=探测跑了、根不在；配置不可读 ⇒ `inventory_invalid`；字段缺失 ⇒ 客户端 `field_invalid`；旧 payload ⇒ `legacy`。
- `evidence[]`：每条 `{harness, kind:"entry"|"target", attribution:"direct"|"shared"|"missing"|"unknown", state:"present"|"absent"|"broken_link"|"denied"|"unknown", path, lexical, readlink, resolved, root, repo, repo_id, scope, entry_index}`，按 (entry, harness, kind) 一一对应、不合并。`kind=entry` 用词法路径先配根；attribution：present→根 kind，absent/broken_link→`missing`，denied/unknown→`unknown`（权限错误绝不推导成 `missing`）。`kind=target`：入口存在且 realpath≠lexical 且命中另一个根时，该根每个 harness 一条。排序键：payload tools 顺序（未列出的工具按 id 殿后）→ kind（entry<target）→ attribution（direct<shared<missing<unknown）→ lexical → resolved。`item.tools` 仍是 tool→mark|null，mark = 该工具所有证据按 `shared<missing<unknown<direct` 取最大。`entries[]` 保留 `path/present/symlink/target/tools/mark/resolved/location/repo_id` 并加 `scope`；不再带 target_* 字段（证据是唯一载体）；`present` 对 denied/unknown 也为 false，以 `evidence.state` 为准。`entries`/`evidence` 按用词法绝对路径（lexical）去重——同一物理路径经 visible_paths、adapters、canonical_path 多次出现时只产生一条 entry 及其证据，`entry_index` 指向去重后的 entries 下标。
- `scope`：`repo_entry`（命中 `repo:true` 根）、`repo_source`（没命中根但 lexical/resolved 在 repo_root 内 → 带 repo_id、location 为 null）、`user`（命中非 repo 根）、`other`（无根且在 repo 外）。别的仓库同名 `.agents/skills` 不会并入本仓库。`item.repo_id` 已移除，改为 `item.repo_ids`（本条目带 repo_id 的去重排序列表，可空）。抽屉分组：仓库入口（repo_entry）/ 仓库源目录（repo_source）/ 用户级入口（user）/ 其他位置（other）。`project` 工具列在页面显示为「仓库入口」。
- 缓存依赖（`skills_document`/`_install_stamp`）：`_stat_token` 含 `st_mode/st_ctime_ns/st_ino/st_mtime_ns/st_size`（chmod/替换即失效），链接另记链接文本+跟随 token。键还含：共享根 token + realpath、清单每个名字的 `shared/<name>` 与 `shared/<name>/SKILL.md` token（stat 会穿过链接，目标 SKILL.md 删除/chmod/替换都失效）、repo_root token + realpath + 原字符串、tool-roots/清单 mtime、锁文件与暂存区（原有）。任何 token 变化 ⇒ 新 key ⇒ 重建；只以未登记/暂存形态出现的名字由共享根目录树 stamp 覆盖；其余由 `CACHE_TTL_SECONDS=15` 兜底。
- `GET /api/skills/meta`：数据源健康（清单、漂移扫描、SkilLock、暂存区、升级检查、重叠检测）和来源汇总。`absent` / `fallback` 是未接入或回退，不是通过。
- `GET /api/skills/attention`：`?format=json|text`。text 不超过 2048 字节，空桶不出现，基线债只有一行计数、不列条目。桶代码：`missing`、`version_changed`、`unrecorded`、`capability_pending`、`modified`、`upgradable`、`overlap`。`badge` 是这些桶的条数之和，不含 `baseline_debt`。

每个 skill 只进一个桶，优先级是上面的桶代码顺序。`version_changed` 是记录的插件版本目录不在、同级有新版本。`modified` 同时收 `unverified`。升级检查第一期没有数据源，桶在 JSON 里 `connected: false`。

## `GET /api/skills/content`（ACM-336）

- `?name=<skill>&path=<已暴露入口>&file=<相对文件>`，`file` 默认 `SKILL.md`。`path` 必须与同名 skill 的 `entries[].path` 或 `local_source.path`（也接受其绝对形式）匹配；入口为目录或 `SKILL.md`。版本通配入口只使用扫描已解析的 `resolved`，不展开请求中的通配符。已知目录链接的规范目标定义读取边界；扫描后已改目标的入口拒绝。目录内链接一律拒读，包括指向目录内的链接，文件树仍标出链接本身，不输出目标。
- `?name=<skill>&path=<入口>&tree=1` 返回 `{ok:true,state:"ok",files:[{path,type,bytes,readable}],truncated,max_bytes}`。路径相对技能目录，`type=file|directory|symlink|special|unknown`，bytes 为 lstat 字节数；探测失败 bytes 为 null。`readable` 只表示普通文件且未超大小上限，UTF-8 检查仍在读取时执行。目录访问失败附 `state:"unknown"`。最多 2000 项、递归 16 层，触限 `truncated:true`。
- 正文响应 `{ok:true,state:"ok"|"empty",name,path,file,format:"markdown"|"text",bytes,sha256,content,source:"local",collected_at,evidence_state:"observed"}`。UTF-8 原文保留（含 frontmatter、换行、HTML 字面文本），空文件 `content:""`。单文件上限 512 KiB，不预览二进制、非 UTF-8、FIFO 等特殊文件。
- 读取失败仍 HTTP 200：`{ok:false,state:"missing"|"denied"|"too_large"|"unsupported"|"error",name,path,source,collected_at,evidence_state:"unknown",message}`。路径穿越、绝对 `file`、链接及非普通文件为 denied；错误不包含底层异常或额外本机路径。重复/未知查询参数、缺 name/path、非法 tree/source 为 HTTP 400 标准 error 对象。沿用回环、同源与 GET/HEAD 安全门禁。
- `?name=<skill>&source=cloud` 不读取本机目录，返回 `{ok:false,state:"uncollected",source:"cloud",collected_at:null,evidence_state:"unknown",message,...}`。`workspace_installs[].content` 同样明确标注此未采集状态；Multica Workspace 更新时间不等于正文采集时间。

### 动态 Runtime 列

`GET /api/skills` 和 `/api/skills/meta` 的 `tools` 取已配置工具 ∪ `/api/runtimes` 缓存 provider ∪ 安装证据 harness。provider 去空格并转小写后去重，保留任意新 provider；缺失 provider 归 `unknown` 并保留实例引用。`project` 仍是配置中的仓库入口。

每列 `{id,provider,label,sources:[{source,collected_at,evidence_state}],runtime_ids:[{workspace_id,id}],roots_state:"configured"|"unconfigured",roots:[{path,kind}],runtime_loaded:null}`。source 为 `tool_roots`、`runtimes`、`skill_evidence`。配置和磁盘证据时间来自本机扫描，runtime 时间来自缓存文档；null 表示没有采集时间。runtime 名称不用于推断 provider 别名或加载规则。

每条 skill 增加 `runtime_evidence[provider]={mark,state,reason,source:"disk_installations",collected_at,runtime_loaded:null}`；state 为 `observed`（有已知入口标记）、`no_match`（有配置根但无命中）、`unknown`（不可读、无配置根或无本机安装证据）、`not_applicable`（仅 Multica Workspace 、没有本机安装记录）。reason 对应 `skill_roots_unconfigured` / `local_installation_unobserved` / `cloud_only` 或 null。Runtime 单元格只显示符号：不适用为 `∅`，无法确定为 `?`；原因码在提示里转换成中文。Multica Workspace 列的 `—` 专指数据未采集。不把无根列的空白当成没安装，不把磁盘入口当成当前会话实测加载。

`tool_filter_ids` 与 tools 同源。前端路由收到列数据后使用 tools.id 生成筛选白名单；首次加载前仅暂存格式受限的 URL provider，避免 Grok/未来 provider 深链在数据到达前丢失。查看器、共享入口加载规则和无根标记的展示由阶段 2 消费。

### Grok Bot 技能名称来源（ACM-349）

`GET /api/skills` 只读 `instructions-governance/grok-bots-manifest.json`（`grok-bots-manifest/v1`），不读取 Grok 客户端、不调用 Grok API，也不改清单。每个不同技能名称生成一个 `origin/source_class="grok_bot"` 条目，附 `grok_bots:[{id,name,evidence_state,evidence_label}]`；`key` 是独立标识，与本机或 Multica Workspace 的同名行保持分离。`related_skill_name` 只表示存在同名关联。

ACM-298 的 8 个名称在 Agent Steward、EO-PM 上标 `observed`（历史实测），其它 21 个 Bot 标 `inferred`（按共享机制推断、未逐一实测）；新增名称不继承旧实测结论。这不代表当前会话加载。Grok Bot 没有正文、版本或本机安装入口，`content.state` 和所有 `runtime_evidence[].state` 为 `not_applicable`，页面以浅灰 `∅` 展示；不生成正文读取按钮、更新命令或 Workspace 安装记录。

`summary` 含 `grok_bot` 来源卡片，全部计数包含这些独立条目；Bot 名称可搜索，来源可筛选。清单缺失或格式无效通过 `meta.sources[id=grok_bot]` 显式标注，来源卡片显示未采集，不能把读取失败视作 0 个技能。用户文案统一为「Multica Workspace」；历史 API 标识 `cloud` / `multica_cloud` 继续兼容。

## 变更记录

- v0.7（2026-10-20）：ACM-291 增加 `GET /api/bots`。`GET /api/agents` 增加 `description`、`instructions`、`instructions_sha256`、`skills`、`disabled_runtime_skills`。`GET /api/skills` 的每条增加 `workspace_installs`。
- v0.6（2026-10-17）：ACM-182 增加 `GET /api/skills`、`/api/skills/meta`、`/api/skills/attention`。drift 增加 `version_changed`。页面待处理栏和 attention 接口用同一个分桶函数。
- v0.5（2026-10-17）：ACM-192 在 `GET /api/issues` 增加 `layer`（主层、涉及层、治理线，以及各自的 `own` / `inherited:<identifier>` 来源），并新增 `GET /api/layers`（ACM Lab 五层计数、不在地图、未归层、待你决定前 3 条）。同日给 `GET /api/changes` 增加 `?kind=`，`status` 只返回 issue 状态迁移，避免 run 事件占满分页后等待时长全部退回 `≥`。
- v0.4（2026-10-16）：ACM-179 在 `GET /api/attention` 增加 `agent_updates`，列出近 24 小时内负责人 agent 已完成、但没有被同期 `in_review` / `blocked` 迁移覆盖的 run。
- v0.3（2026-10-16）：ACM-174 在 `GET /api/attention` 增加 `missing_hermes_async_delegation`，列出未启用 skill `hermes-async-delegation` 的 Hermes agent。
- v0.2（2026-10-15）：ACM-165 新增 `GET /api/attention`，按 workspace 汇总 in_review、blocked、近 24 小时失败 run、仍绑着 agent 的离线 runtime。`?format=text` 输出不超过 2048 字节的纯文本。
- v0.2（2026-10-15）：新增 `GET /api/changes`（ACM-166）。`since` 为 ISO 时间或上次的 cursor；缓存保留 7 天的 issue 状态/指派、新 run、run 失败、runtime 上下线。
- v0.2（2026-10-15）：新增 `GET /api/stats/failures`（ACM-167）。`failure_reason` 取值即 `error_kind`。
- v0.1（2026-10-15）：按 ACM-162 审阅以后端为准：`error_kind` 加 `process` 并定匹配顺序；`url` 改用 UUID；`generated_at` 定义为最近一次采集完成时间；`project` 定为 `{id, title}`；`/runs` 记录带 `key`，转为第一阶段接口；`parent_identifier`、`last_failed_issue_identifier`、squad `name` 的空值规则；`::1`、421、更严的响应头与 CSP。新增 `meta.collector`。
- v0（2026-10-14）：初稿。

## G1 meta API identity

`GET /api/meta` additionally returns `server_identity`: a best-effort normalized API origin observed from the selected CLI profile's unique `Server:` line in read-only `auth status`. It is distinct from `multica_web_base` and the profile name. Resolution failure returns null; serve still starts and the collector retains its existing profile routing, without pinning an origin via `--server-url`. No actor or on-behalf-of identity is inferred. See `runtime-plan-contract.md` (v1.2 draft) for the plan interfaces and their null/empty-string exception.
