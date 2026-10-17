# Multica Board

本地只读、跨 workspace 的 Multica 看板（ACM-161 第一阶段）。只监听 `127.0.0.1`，没有任何写接口。

```text
multica CLI（你本人的登录，--output json）→ SQLite 缓存（~/.multica-board）→ 127.0.0.1 上的 GET-only API + 静态页面
```

- `backend/`：采集、缓存与只读 API（Python ≥ 3.10 标准库，无依赖；需要 `multica` CLI ≥ 0.6.2 在 `PATH` 上）。归 Builder（ACM-162）。
- `web/`：前端静态页面（无构建、无依赖），由后端在 `/` 托管。归前端设计师（ACM-163）。
- `web/fixtures/`：契约样例响应（虚构数据），`generate.py` 重新生成。
- `docs/api-contract.md`：前后端只读 API 契约（v0.5）。改契约先在 ACM-163 说。
- `docs/frontend-design.md`：信息架构与交互设计。

## 运行

必须在**你自己的普通终端**里运行。Multica agent 任务环境里 CLI 被钉在当前任务的 workspace 上；后端会剥掉 `MULTICA_TOKEN`、`MULTICA_SERVER_URL`、`MULTICA_TASK_*`，所以在 agent shell 里采集会直接报 “No server configured”。

```sh
cd multica-board/backend

# 1. 看 CLI 身份能访问哪些 workspace
python3 -m multica_board workspaces

# 2. 跑一轮采集（打印每个 workspace 的结果；任一失败则退出码 2）
python3 -m multica_board collect --all-workspaces
python3 -m multica_board collect --workspace acm-lab --workspace <uuid>   # 显式白名单

# 3. 启动看板：API + 前端，每 60 秒采集一次
python3 -m multica_board serve --all-workspaces
#   → 打开 http://127.0.0.1:8787/   （必须用 127.0.0.1；localhost 会返回 421）

# 只读缓存、不调用 CLI
python3 -m multica_board serve --all-workspaces --refresh 0

# 测试
python3 -m unittest discover -s tests -v

# 层回填建议（只读，不写属性）
python3 -m multica_board.suggest_layers --output layer-backfill.md
```

只看前端（mock 数据，不需要后端）：

```sh
python3 -m http.server --bind 127.0.0.1 8765 -d multica-board/web
# 打开 http://127.0.0.1:8765/?mock=1
```

`--workspace`（可重复，ID 或 slug）和 `--all-workspaces` 必须二选一。停止：在前台按 Ctrl-C；后台启动的话，kill 它打印的 PID（或查 `lsof -nP -iTCP:8787 -sTCP:LISTEN`）。不要 `pkill multica`，那会匹配到 workspace daemon。

| 参数 | 默认 | 说明 |
|---|---|---|
| `--profile` | `desktop-api.multica.ai` | CLI profile；CLI 只通过它认证 |
| `--db` | `~/.multica-board/board.sqlite` | 删掉即重置缓存；schema 变化时自动重建 |
| `--refresh` | `60` | 两轮采集之间的秒数，即 `/api/meta` 的 `collect_interval_s`；`0` 只读缓存 |
| `--full-every` | `30` | 每 N 轮做一次 run 历史全量校对，其余轮次增量 |
| `--run-days` | `14` | run 缓存窗口（`latest_run=null` 表示窗口内没有 run） |
| `--max-task-pages` | `10` | 每个 agent 每轮最多翻几页（每页 200 条） |
| `--concurrency` / `--max-cli` | `3` / `4` | 并行的 workspace（及 agent）数 / 同时运行的 `multica` 进程上限 |
| `--port` / `--host` | `8787` / `127.0.0.1` | host 只接受 `127.0.0.1` 或 `::1` |
| `--static-dir` | `../web` | `/` 下托管的前端目录 |
| `--app-url` | `https://multica.ai` | 深链前缀：`{app_url}/{slug}/issues/{issue_uuid}`、`{app_url}/{slug}/agents/{agent_uuid}` |

参考耗时：ACM Lab（144 issue、8 agent）一轮约 20–30 秒，每次 CLI 调用 1–3 秒。

## API

契约见 `docs/api-contract.md`。实现的接口：`/api/meta`、`/api/issues`（每条带 `layer`）、`/api/layers`（ACM Lab 五层计数）、`/api/agents`、`/api/runtimes`、`/api/issues/{workspace_id}/{issue_id}/runs`（缓存里最近 20 次）、`/api/attention`（需处理事项，含未被状态迁移覆盖的 agent 交付，以及未启用 `hermes-async-delegation` 的 Hermes agent；`?format=text` 为不超过 2048 字节的纯文本）、`/api/changes?since=&kind=`、`/api/stats/failures`（按 provider / runtime / agent 汇总 24h 与 7d 失败次数及前 3 个 `failure_reason`）。全部 GET，其它方法 `405`，错误统一为 `{"error":{"code","message"}}`。

实现上需要知道的约定：

- `generated_at` 是最近一次采集**完成**的时间，不是响应时间；从未采集过为 `null`。
- `/api/issues` 返回所有未关闭 issue（`status_category` 不是 `done`/`cancelled`），加上 7 天内 run 失败过的已关闭 issue，不分页。
- `project` 为 `{id, title}` 或 `null`。`parent_identifier`、`last_failed_issue_identifier` 只在对应 issue 也在缓存里时有值。`assignee.type=squad` 的 `name` 为 `null`（v0 不采集 squad）。
- `error_kind`：契约枚举加 `process`。匹配顺序：quota → rate_limit → auth → model_unavailable → daemon_restart → timeout → network → cancelled → process → unknown。`error_summary` 已脱敏，截断到 200 字符。
- `collect_state`：从未完整成功为 `never`（`counts=null`）；最近一轮有失败为 `error`；上次成功超过 3 个采集周期为 `stale`；否则 `ok`。
- `error.kind=auth`（403、401、撤权，或 CLI 返回的数据属于别的 workspace）时清空该 workspace 的缓存，`counts=null`。
- `/api/changes`：省略 `since` 只返回当前 cursor。`since` 为 ISO 时间或上次的 cursor，返回此后的 issue 状态/指派变化、新 run、run 失败、runtime 上下线，并附新 cursor。`?kind=status` 只返回状态迁移（别名 `issue_status`；省略则不过滤），翻页时 cursor 和 `kind` 一起带上。每种资源第一次成功快照只建基线。变更保留 7 天；历史不完整时 `reset: true`（仍是 200），客户端应重拉全量。单次最多 500 条。

## 安全边界

- **只监听回环地址**：只能绑定 `127.0.0.1` / `::1`。`Host` 只接受 `127.0.0.1:<port>`（绑 `::1` 时为 `[::1]:<port>`），其它返回 `421`。跨源 `Origin` 或 `Sec-Fetch-Site: cross-site` 返回 `403`。不发 CORS 头。静态页 CSP：`default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'`。
- **没有写接口**：HTTP 只有 GET/HEAD。CLI 只能执行精确白名单（`workspace list`、`workspace member list`、`issue list`、`project list`、`agent list`、`agent tasks`、`runtime list`、`property list`），白名单外的命令在启动进程前就被拒绝；调用方也不能注入 `--profile` / `--workspace-id` / `--server-url`。没有“立即刷新”接口。`suggest_layers` 另外只允许 `issue list`、`project list`、`property list`，用来出回填建议，不写属性。
- **token 不外泄**：后端不读 CLI 配置或 token。子进程 env 去掉 `MULTICA_TOKEN`、`MULTICA_SERVER_URL`、`MULTICA_TASK_*`、`MULTICA_WORKSPACE_ID`，加 `GODEBUG=tlsmlkem=0`，每次调用都带 `--profile <p> --workspace-id <id>`。子进程 cwd 固定为一个空的私有临时目录：CLI 会从 cwd 向上查找 `.multica` 并覆盖 `--profile`，所以从哪个目录启动看板都不影响采集。不缓存：issue 正文、agent instructions、custom_env/args、mcp_config、runtime_config、launch_header、run 输出与 workdir、成员邮箱。错误文本脱敏（Bearer/PAT/`m??_` token、`sk-`、`ghp_`、JWT、`key=value`、长 hex/base64、home 路径）。缓存目录 `0700`，文件 `0600`。
- **workspace 隔离**：返回行的 `workspace_id` 和请求的不一致时，整批丢弃，该 workspace 标 `collect_state=error`、`kind=auth`（"CLI identity scoped to another workspace"）。一个 workspace 失败不影响其他 workspace，旧缓存保留并标为过期，不会显示成 0 条。

## 不在本期

批量切换 runtime（第二阶段）、run transcript、评论正文、squad。
