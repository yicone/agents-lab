# Multica Board

本地只读、跨 workspace 的 Multica 看板（ACM-161 第一阶段）。默认只监听 `127.0.0.1`，可显式开放受信任网络只读访问。ACM-458 新增默认关闭的单 agent runtime 写入入口，仅允许回环监听。

```text
multica CLI（你本人的登录，--output json）→ SQLite 缓存（~/.multica-board）→ 127.0.0.1 上的 GET-only API + 静态页面
```

- `backend/`：采集、缓存与只读 API（Python ≥ 3.10 标准库，无依赖；需要 `multica` CLI ≥ 0.6.2 在 `PATH` 上）。归 Builder（ACM-162）。
- `web/`：前端静态页面（无构建、无依赖），由后端在 `/` 托管。归前端设计师（ACM-163）。
- `web/fixtures/`：契约样例响应（虚构数据），`generate.py` 重新生成。
- `docs/api-contract.md`：前后端只读 API 契约（v0.5）。改契约先在 ACM-163 说。
- `docs/frontend-design.md`：信息架构与交互设计。
- `docs/design-system.md`：信息结构、版式模式、组件与视觉规范，以及新板块设计检查清单（新板块从这里开始）。

## 模型 / runtime 写入（ACM-458，本地候选，未部署）

页面为 `#/configure/runtime`；顶栏由另一任务负责，本改动不加导航。先交公共管线和单 agent，批量请求一律拒绝。旧 G1 计划页和只读 CLI runner 保持只读，不能把 G1 hash 当成批准。

写入口需要启动参数 `--write-policy /private/path/policy.json`。策略是操作者本人安装的 0600 普通文件，不允许符号链接。无策略时所有 POST 仍为 405；启用后仅接受回环监听、回环来源和与 Host 完全相同的 HTTP Origin，不开放网络写入口。浏览器必须提供独立授权码 `X-Atlas-Write-Token`（不写 URL、日志或 localStorage，离开页面清除）。策略只保存授权码 SHA-256，实际授权码应由操作者用安全随机数生成并妥善保存，不应使用下面 fixture 的固定字符串。

策略字段：

- `profile`：与 serve 的 `--profile` 完全一致。
- `server_identity`：规范化服务 origin；执行时和 `auth status` 复核。
- `actor_id`：用户 UUID；执行时和 `user profile get` 的 `id` 复核。当前版本仅支持本人拥有、未归档且 idle 的 agent，不能借管理员权限改别人的 agent。
- `token_sha256`：独立随机授权码的 64 位小写 SHA-256。
- `targets`：明确的对象数组，每项只含 `workspace_id`、`agent_id`、`audit_issue_id` UUID。审计 issue 必须在目标 workspace；执行前读回校验。没有通配目标。

仅 `POST /api/runtime-actions/{catalog,status,preview,execute,rollback,reconcile}` 接受写协议，Content-Type 必须为 application/json，最大 16KiB，重复键、额外字段、查询串、分块 body 均拒绝。除 `GET /api/runtime-actions` 返回 enabled 外，不提供无授权目标清单。各 POST body：

| 操作 | Body | 结果 |
|---|---|---|
| catalog | `{}` | 授权范围、最近 20 个操作摘要、并发限制 |
| status | `{id}` | 指定已执行操作的本地审计结果，无确认码 |
| preview | `{workspace_id,agent_id,changes}` | 实时 old/new、版本、300 秒期限和一次性确认码 |
| execute | `{id,confirmation}` | 消费确认后执行，读回相等才是 applied |
| rollback | `{id}` | 创建单独回滚预览，不执行；仍需 execute 确认 |
| reconcile | `{workspace_id,agent_id}` | 只读对账 unknown，无法确定则继续锁住目标 |

`changes` 白名单：`runtime_id`、`model`、`thinking_level`、`service_tier`、`custom_model`（只代表 custom_args 的 --model）。省略=保持，null=CLI 显式清除（model / thinking_level / service_tier 映射为空字符串），null 和空字符串在快照里保留区别；runtime_id 不能清除。custom_model 的 null 代表移除 --model，空字符串拒绝。其它 custom_args 不展示、不持久化；包含其它参数时禁止改写 custom_model，防止覆盖秘密或无关参数。独立模型和 custom_model 冲突时拒绝；Codex/OpenClaw 不允许 custom_model。模型/推理/Tier 支持性最终仍由 Multica 和 daemon 校验，本轮未验证真实 provider 组合。

每次执行先复核身份、ownership、workspace、审计落点、agent idle、完整分页任务清单（运行与排队均阻止），复读版本与预览比较；确认单在任何检查前原子消费，过期/冲突/失败都必须重新预览。CLI 写入无重试，回执丢失或读回不符标 unknown，目标锁住直至 reconcile；多进程通过审计文件排它锁拒绝第二写入服务。重启丢弃批准码，保留 executing/unknown 锁和回滚记录。

本地 `<db-stem>-write-audit.jsonl` 是 0600 只追加日志，每次提交前 fsync；保存白名单 old/new、身份、资源、结果和回滚关联，不存授权码、秘密字段、custom_args 原文或 CLI 原始错误。同时在策略绑定 issue 留一条只含字段名和结果的脱敏评论。评论回执未知只记录 audit_unknown，禁止自动重发。日志不可读或身份不符拒绝启动。日志应保留到运维完成对账，不能靠删日志解除未知状态。

**并发限制**：当前 CLI 没有服务端条件更新或活动任务提交栅栏。这里实现的是执行前复读检查，检查和提交之间仍有外部写入/新任务的竞态；不能宣称原子 CAS 或并发绝对安全。批量后置，后续需要平台栅栏能力。配置读回成功只证明配置已写入；下一次运行使用的实际模型保持「未知」，本轮没有推断生效证据。

仅本地 fixture 验证，不会调用真实 CLI：

```sh
python3 multica-board/backend/tests/runtime_write_fixture_server.py --port 8798
# 浏览器访问 http://127.0.0.1:8798/#/configure/runtime
# fixture 授权码：fixture authorization
# --scenario unknown / empty / disabled 可查看隔离失败场景。
```

## 运行

### 用户补采其他九个 workspace 的 agent

只由用户本人在 mini 的普通终端运行，agent 不运行此采集器：

```sh
python3 multica-board/scripts/collect-owner-agents.py
```

脚本固定使用 `--profile desktop-api.multica.ai`，从子进程环境清除全部 `MULTICA_*` 并设置 `GODEBUG=tlsmlkem=0`，在无 `.multica` 的临时目录逐个用完整 UUID 运行只读 `agent list`。只保留 Atlas 需要的字段，instructions 和 skills 沿用现有脱敏；不保存 env、MCP 配置或原始错误输出。某个 workspace 失败不影响其余采集，终端显示各 workspace 的成功/失败和数量；全部成功退出 0，部分失败退出 2。

默认原子写入 `~/.multica-board/board-owner-agents.json`（权限 0600），不修改 SQLite 或 Multica。自定义数据目录用 `--db /path/to/board.sqlite`，快照写在该数据库旁。Atlas 每次读取 `/api/agents` 都读取快照：成功 workspace 的 inventory 替代常规 agent 缓存（包括删除和空列表），失败 workspace 回退常规缓存，不保留上次补采结果。其他 CLI profile 不读取这个快照。

执行后确认九行均为 `ok`，刷新**包含此改动的 Atlas** 的 Agents 页面，打开任意补采 agent 的详情，核对 instructions、Skills 绑定、采集时间和 `user-cli:desktop-api.multica.ai` 来源；页面也显示失败 workspace。`/api/agents` 的 `owner_inventory` 提供逐 workspace 状态。数据是用户手动补采快照，并非实时状态。旧版线上服务不会自动支持该格式，必须在变更合并到 main 后按正常流程部署；本地实现阶段不得部署。

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

`--workspace`（可重复，ID 或 slug）和 `--all-workspaces` 必须二选一。手工运行时在前台按 Ctrl-C 停止。后台运行时先查监听 PID，再用 `ps -p <PID> -o pid=,ppid=,args=` 确认命令是预期的 `python -m multica_board serve`；启动输出只包含地址，不包含 PID。不要 `pkill multica`，那会匹配到 workspace daemon。

本机现用服务由 launchd 管理，端口为 8765；PID 以 launchd 为准。以下命令只读，不会重启服务：

```sh
launchctl print gui/$(id -u)/one.yic.multica-board
lsof -nP -iTCP:8765 -sTCP:LISTEN
ps -p <上面两处一致的PID> -o pid=,ppid=,args=
```

核对 `state = running`、`pid =`、监听 PID 和服务命令一致；若不一致，先排查，不能依据旧 PID 发信号。当前启动链路是 launchd → login-item 壳 → App 启动脚本 → `exec python3 -m multica_board serve`，标准输出/错误写入 `~/Library/Logs/multica-board.log`。Board 不读写 PID 文件；`~/.multica-board/board.pid` 和 `serve.log` 是历史手工运行遗留，不应作为状态或日志来源。启动、安装或打包流程不得生成、复制这些文件，也不要打包整个 `~/.multica-board` 运行目录。遗留文件清理需用户批准，不由服务或测试自动执行。

| 参数 | 默认 | 说明 |
|---|---|---|
| `--profile` | `desktop-api.multica.ai` | CLI profile；CLI 只通过它认证 |
| `--db` | `~/.multica-board/board.sqlite` | 删掉即重置缓存；schema 变化时自动重建 |
| `--refresh` | `60` | 两轮采集之间的秒数，即 `/api/meta` 的 `collect_interval_s`；`0` 只读缓存 |
| `--full-every` | `30` | 每 N 轮做一次 run 历史全量校对，其余轮次增量 |
| `--mcp-every` | `5` | 库指纹不变时，每 N 轮重读一次各 agent 的 MCP 分配（首个含自有配置的轮次例外，强制读一次） |

`/api/mcp`（schema 2，见 `docs/mcp-redesign.md`）另读 `config/mcp-sources.json` 声明的本地 MCP 配置文件；该文件自带逐工具来源说明。
| `--run-days` | `14` | run 缓存窗口（`latest_run=null` 表示窗口内没有 run） |
| `--max-task-pages` | `10` | 每个 agent 每轮最多翻几页（每页 200 条） |
| `--concurrency` / `--max-cli` | `3` / `4` | 并行的 workspace（及 agent）数 / 同时运行的 `multica` 进程上限 |
| `--port` / `--host` | `8787` / `127.0.0.1` | host 为数字 IP，可重复或逗号分隔；多个监听共享端口，IPv6 独立于 IPv4 |
| `--allowed-host` | 对应回环地址及监听端口 | 追加精确 `host[:port]`，可重复或逗号分隔；不填端口则用监听端口，无通配符 |
| `--static-dir` | `../web` | `/` 下托管的前端目录 |
| `--app-url` | `https://multica.ai` | 深链前缀：`{app_url}/{slug}/issues/{issue_uuid}`、`{app_url}/{slug}/agents/{agent_uuid}` |

参考耗时：ACM Lab（144 issue、8 agent）一轮约 20–30 秒，每次 CLI 调用 1–3 秒。

## API

契约见 `docs/api-contract.md`。实现的接口：`/api/meta`、`/api/issues`（每条带 `layer`）、`/api/layers`（ACM Lab 五层计数）、`/api/agents`、`/api/runtimes`、`/api/issues/{workspace_id}/{issue_id}/runs`（缓存里最近 20 次）、`/api/attention`（需处理事项，含未被状态迁移覆盖的 agent 交付，以及未启用 `hermes-async-delegation` 的 Hermes agent；`?format=text` 为不超过 2048 字节的纯文本）、`/api/changes?since=&kind=`、`/api/stats/failures`（按 provider / runtime / agent 汇总 24h 与 7d 失败次数及前 3 个 `failure_reason`）、`/api/skills`、`/api/skills/meta`、`/api/skills/attention`（清单和 drift 合并；`?format=text` 不超过 2048 字节，基线债只计数）。全部 GET，其它方法 `405`，错误统一为 `{"error":{"code","message"}}`。

实现上需要知道的约定：

- `generated_at` 是最近一次采集**完成**的时间，不是响应时间；从未采集过为 `null`。
- `/api/issues` 返回所有未关闭 issue（`status_category` 不是 `done`/`cancelled`），加上 7 天内 run 失败过的已关闭 issue，不分页。
- `project` 为 `{id, title}` 或 `null`。`parent_identifier`、`last_failed_issue_identifier` 只在对应 issue 也在缓存里时有值。`assignee.type=squad` 的 `name` 为 `null`（v0 不采集 squad）。
- `error_kind`：契约枚举加 `process`。匹配顺序：quota → rate_limit → auth → model_unavailable → daemon_restart → timeout → network → cancelled → process → unknown。`error_summary` 已脱敏，截断到 200 字符。
- `collect_state`：从未完整成功为 `never`（`counts=null`）；最近一轮有失败为 `error`；上次成功超过 3 个采集周期为 `stale`；否则 `ok`。
- `error.kind=auth`（403、401、撤权，或 CLI 返回的数据属于别的 workspace）时清空该 workspace 的缓存，`counts=null`。
- `/api/changes`：省略 `since` 只返回当前 cursor。`since` 为 ISO 时间或上次的 cursor，返回此后的 issue 状态/指派变化、新 run、run 失败、runtime 上下线，并附新 cursor。`?kind=status` 只返回状态迁移（别名 `issue_status`；省略则不过滤），翻页时 cursor 和 `kind` 一起带上。每种资源第一次成功快照只建基线。变更保留 90 天；历史不完整时 `reset: true`（仍是 200），客户端应重拉全量。单次最多 500 条。

## 安全边界

- **默认回环，显式开放**：默认只绑定 `127.0.0.1`，只接受 `Host: 127.0.0.1:<port>`；绑 `::1` 时默认为 `[::1]:<port>`。非回环监听默认仍只放行回环 Host，必须用 `--allowed-host` 追加实际访问地址。监听地址不会自动加入白名单；`0.0.0.0` / `::` 不代表任意 Host。Host 不匹配返回 `421`，重复 Host 也拒绝；Origin 若存在，只接受 HTTP(S) 且其地址和有效端口必须匹配同一白名单，畸形、重复或未知 Origin 返回 `403`。没有 Origin 的 GET/HEAD（普通导航、CLI）仍可用。`Sec-Fetch-Site: cross-site` 的 API / 子资源请求继续返回 `403`，保留已有顶层页面导航例外。不发 CORS 头，白名单不会开放跨站 fetch。静态页 CSP：`default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'`。
- **没有写接口**：HTTP 只有 GET/HEAD。CLI 只能执行精确白名单（`workspace list`、`workspace member list`、`issue list`、`project list`、`agent list`、`agent tasks`、`runtime list`、`property list`），白名单外的命令在启动进程前就被拒绝；调用方也不能注入 `--profile` / `--workspace-id` / `--server-url`。没有“立即刷新”接口。`suggest_layers` 另外只允许 `issue list`、`project list`、`property list`，用来出回填建议，不写属性。
- **MCP 配置文件只按声明读**：后端只读 `config/mcp-sources.json` 声明的本机/项目级 MCP 配置文件（相对路径，逐目录 fd 打开、`O_NOFOLLOW` 不跟随符号链接、只读固定白名单字段、值只在内存里解析；不入 SQLite、不进 API、不进日志）。指纹比较用 `<db 目录>/mcp-fingerprint.key`（0600、属主/链接/长度校验，拒绝就停用指纹）；自有配置指纹只在进程内存。不读 CLI 配置或 token。子进程 env 去掉 `MULTICA_TOKEN`、`MULTICA_SERVER_URL`、`MULTICA_TASK_*`、`MULTICA_WORKSPACE_ID`，加 `GODEBUG=tlsmlkem=0`，每次调用都带 `--profile <p> --workspace-id <id>`。子进程 cwd 固定为一个空的私有临时目录：CLI 会从 cwd 向上查找 `.multica` 并覆盖 `--profile`，所以从哪个目录启动看板都不影响采集。不缓存：issue 正文、agent instructions、custom_env/args、mcp_config、runtime_config、launch_header、run 输出与 workdir、成员邮箱。错误文本脱敏（Bearer/PAT/`m??_` token、`sk-`、`ghp_`、JWT、`key=value`、长 hex/base64、home 路径）。缓存目录 `0700`，文件 `0600`。
- **workspace 隔离**：返回行的 `workspace_id` 和请求的不一致时，整批丢弃，该 workspace 标 `collect_state=error`、`kind=auth`（"CLI identity scoped to another workspace"）。一个 workspace 失败不影响其他 workspace，旧缓存保留并标为过期，不会显示成 0 条。

## 不在本期

批量切换 runtime（第二阶段）、run transcript、评论正文、squad。

### 局域网 / Tailscale 访问与切换草稿

**只在受信任的局域网或 tailnet 中使用。** Board 没有登录认证；白名单防止意外 Host / 浏览器跨站访问，不是客户端身份验证。能连到端口的客户端可以自行设置 Host 并读取缓存数据。请用防火墙 / Tailscale ACL 限制客户端，不映射到公网。优先绑定具体网卡 IP；`0.0.0.0` 会开放所有 IPv4 网卡。不要同时绑定 `0.0.0.0` 和具体 IPv4 地址，否则端口冲突会使整个启动失败。

```sh
# 保留回环并直连 LAN / Tailscale IP；替换所有占位地址
python3 -m multica_board serve --all-workspaces --port 8765 \
  --host 127.0.0.1 --host <LAN_IP>,<TAILSCALE_IP> \
  --allowed-host <LAN_IP>,<TAILSCALE_IP> \
  --allowed-host tr-mini-m4.tailc98f73.ts.net:8443
```

白名单条目是地址，不是 URL；IPv6 用 `[IPv6]:端口`。不带端口的条目绑定到服务实际端口（包括 `--port 0` 的临时端口），显式端口适用于 Tailscale Serve 等反代外部入口。Host 精确匹配地址和端口（域名不区分大小写），不匹配子域名；Origin 的 HTTP/HTTPS 默认端口按 80/443 处理。不信任 `X-Forwarded-Host` / `X-Forwarded-For`。

由 Steward 审阅后执行，当前实现交付不会操作 LaunchAgent 或重启 8765：

1. 备份 Board 启动配置、两个转发器配置和 Tailscale Serve 当前配置。核对 `one.yic.multica-board` 的实际启动链：若 LaunchAgent 调用 App / 壳脚本，应在最终 `python -m multica_board serve` 参数处追加以上 `--host` / `--allowed-host`，每个参数独立成 argv，不把整行放入一个 plist 字符串。保留现有 DB、workspace、采集等参数。
2. 将审核通过的本地提交按正常流程纳入部署版本，再由 Steward 重启 Board。先验证回环与手机 LAN / tailnet 直连 `:8765`，未知 Host 应为 421、未知 Origin 应为 403、POST 应为 405。
3. 将既有 Tailscale Serve 的 HTTPS `:8443` 后端从 `127.0.0.1:8766` 改为 `127.0.0.1:8765`，保持 tailnet-only、不开 Funnel。具体命令先按机器上 `tailscale serve --help` 核对；确认代理保留的实际 Host 与白名单一致，不依赖转发器改写。手机验证 HTTPS 页面与 API 加载。
4. 全部通过后，再卸载 `one.yic.atlas-lan-forward` 和 `one.yic.atlas-ts-forward` 的 launchd 作业，禁用对应自动启动配置，保留备份；先不删除 `~/.local/bin/lan-forward.py`。确认 8766 转发监听消失，8765 和 HTTPS 8443 继续可用。
5. 失败时恢复原 Board 启动参数 / 部署版本、两个转发器作业和 Tailscale Serve 的旧后端，再验证原有入口。网卡 IP 变化时需同步更新绑定与白名单。

### 效能指标与流速（ACM-462）

从 `multica-board` 目录运行，只读 Multica；快照写入本地私有文件，不接前端或日报：

```sh
PYTHONPATH=backend python3 -m multica_board metrics-backfill --snapshot metrics-history.json
PYTHONPATH=backend python3 -m multica_board metrics --snapshot metrics-history.json
PYTHONPATH=backend python3 -m multica_board metrics --snapshot metrics-history.json --threshold 3 --wip-floor 2 --rework-ceiling 1
PYTHONPATH=backend python3 -m multica_board metrics --snapshot metrics-history.json --format markdown
PYTHONPATH=backend python3 -m multica_board metrics-reconcile --backfill backfill.json --realtime realtime.json
```

默认回补全部可见 workspace、最近 28 天的 run、issue 生命周期和评论，脱敏事件快照最多保留 90 天。`metrics-backfill --resume` 复用成功读取，只重试失败的 timeline/comment，保留原库存时点和 workspace 权限缺口；要刷新库存或权限变化须重新回补。复用 Board 的 issue/run 分页、scope 校验和 CLI runner；不调用巡检写入、CodexBar 或另一份 usage 采集。常驻 Board profile 不可用的任务环境可显式加 `metrics-backfill --task-identity` 使用当前任务身份，范围仍限该身份可见 workspace；不切换身份或修改配置。`metrics` 是纯本地只读命令，默认快照超过 24 小时即标来源未知；`--now` 冻结基线时点用于复现，不能用于伪装实时读数。

`--ledger events.json` 接受人工核实后的脱敏枚举事件，格式见 `backend/multica_board/metrics_cli.py` 模块说明。结束必须有 `source=delivery_ledger`、`event_type=delivery_completed`、`confidence=complete`、`delivery_evidence` 不透明 ID。完整 attention/rework/cost 通道通过 coverage 事件显式声明 `since/until`；没有事件不证明零次注意力、零返工或零成本。原始评论 ID 用作分类事件 ID（同一操作只归一类）；无分类的历史评论只保留引用，不能猜批准或返工。取消成本单列，额度不分摊、不与 Multica 原始 cost_usd_ticks 相加。未来实时采集沿用相同事件 ID；连续 2 天对账后再确认一致性，本命令不冒充已经完成对账。

输出包含五项指标的已知数/候选数/coverage、周期 P90、每日 UTC 24 小时窗口交付数、滚动 24h、在制品、当前等待分类和返工诊断。缺台账 done 不算交付；分页不完整、workspace 不符、服务端截断和过期快照均显式 unknown。阈值默认不设，示例参数仅演示用法，不代表批准的目标。建议动作只供 Steward 判断，不执行派活或修改 issue。
