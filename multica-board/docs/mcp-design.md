# MCP 板块设计（ACM-370）

> ACM-378 重做后，版式、采集范围、接口和待处理分桶以 `mcp-redesign.md` 为准。本文的事实依据、各 runtime 是否加载、生效优先级和命令表继续有效。

回答三个问题：

1. 每个 workspace 的 MCP 库里有什么，分给了谁？
2. 某个 agent 下一次跑任务时，实际会拿到哪些 MCP server？
3. 哪些分配不会生效（runtime 不加载、被同名配置覆盖、任务会直接失败）？

接口见 `api-contract.md` 的 `GET /api/mcp`（v0.9）。交互沿用 Skills：一览表、待处理栏、右侧详情抽屉、可复制命令。

## 事实依据

以下来自 multica CLI 0.6.5，以及 `multica-ai/multica` 的 `v0.6.5` 标签源码（与 2026-10-23 的 main 相比，相关文件只有 `hermes.go` 有改动，而且不涉及 MCP）。没有在真实 workspace 上做写操作试验，所以「是否加载」这一列都是**源码确认，未实测**。

- **两层来源**。一层是 workspace MCP 库（`workspace mcp *`），条目建好后不属于任何 agent，必须用 `agent mcp add` 显式分配，分配关系自带启用开关。另一层是 agent 自有的 `mcp_config`（`agent update --mcp-config*`）。
- **库条目的配置只写不读**。`workspace mcp list` 和 `agent mcp list` 只返回 `id`、`name`、`transport`、时间戳，库列表多一个 `agent_count`，agent 列表多一个 `enabled`。配置本身对任何人都不返回，owner 也拿不到。所以库条目的 env 和 headers **无法判断有没有设置**，页面写「不可见」，不能写「未设置」。
- **agent 自有配置是读得到的**。`agent list` 和 `agent get` 会对有权查看 agent 密钥的调用者（Atlas 用的是用户本人的 owner profile）返回完整的 `mcp_config`，其他调用者拿到 `null`，同时 `mcp_config_redacted: true`。这是本板块最大的泄露风险，见「密钥处理」。
- **生效优先级**（每次 claim 时由服务端计算）：分配给它且已启用的库条目 < agent 自有 `mcp_config` < 单次任务的 overlay（Composio 等集成）。同名时 agent 自有的配置胜出，库条目对这个 agent 等于不存在。没有分配、也没有自有配置时结果为 `null`，runtime 按它自己的方式继承本机配置。
- **改动下一次 claim 就生效**，不用重启 daemon。
- **写权限**：库的增、删、改只有 workspace admin 或 owner 能做。agent 的分配类操作要求调用者能查看该 agent 的密钥，而且 agent 身份一律 403。所以所有写命令都只能由用户在自己的终端里运行。
- **当前状态**：ACM Lab 的库是 `[]`，23 个 agent 的 `mcp_config` 都是 `null`，`mcp_config_redacted` 都是 `false`。

## 各 runtime 是否加载

判断依据是 `packages/core/agents/mcp-support.ts` 里的 `MCP_SUPPORTED_PROVIDERS`（Multica 只给列表内的 provider 显示 MCP 设置页），再逐个对照 `server/pkg/agent/<provider>.go` 和 `server/internal/daemon/execenv/` 的传递路径。下表先列 issue 点名的五个，后面是本机在线的其他 runtime。

| Runtime（provider） | 加载分配的 MCP | 传递方式 | 本机原有的 MCP 配置 | 备注 |
|---|---|---|---|---|
| Codex（`codex`） | 是 | 写入每个任务独立的 `CODEX_HOME/config.toml` 的 `mcp_servers` | daemon 把 `~/.codex/config.toml` 合并进来做底层，同名以 Multica 为准 | 有托管配置时，`custom_args` 里的 `-c mcp_servers.*` 会被丢掉 |
| Hermes（`hermes`） | 是 | ACP `session/new` 与 `session/resume` 的 `mcpServers` | 未知：daemon 不合并，Hermes 自己会不会再读本机配置，源码里看不出来 | runtime 没有声明 http/sse 能力时，远程条目会被丢掉，只剩 stdio |
| Cursor（`cursor`） | 是 | 在任务目录写 `.cursor/mcp.json`，并用独立的 `CURSOR_DATA_DIR` 预置批准记录 | daemon 把 `~/.cursor/mcp.json` 合并进来做底层 | 需要 OAuth 的 server 依赖 agent 的 custom_env 指定授权来源，否则登录状态未知 |
| Antigravity（`antigravity`） | **否** | 无：不在支持列表里，backend 也不读 `mcp_config` | 不受影响 | 分配了也会被静默忽略 |
| Devin（`mcode`） | 是 | 与 Hermes 相同的 ACP 通道和远程能力过滤 | 未知（原因同 Hermes） | 本机 runtime 名为「Devin」，provider 是 `mcode` |
| Claude（`claude`） | 是 | 临时文件 `--mcp-config`，严格模式 | daemon 合并 `~/.claude.json` 和已启用插件 | |
| Grok（`grok`） | 是 | ACP，过滤规则同 Hermes | 未知 | |
| OpenCode（`opencode`） | 有条件 | 1.x 用 `OPENCODE_CONFIG_CONTENT` | daemon 合并 `opencode.json` | 2.x 上只要有托管 MCP，**任务直接报错失败**；Atlas 不知道 runtime 是哪个大版本 |
| Oh-My-Pi（`omp`） | 是 | 在任务目录写配置 | daemon 不合并，omp 自己按多级配置链读取 | |
| Copilot（`copilot`） | **否** | 无 | 不受影响 | |

Atlas 把这张表做成后端常量（`support.providers`），并带上来源版本。provider 不在表里时一律按「未知」显示，不能推断成「是」。Multica 升级后要重新核对这张表，这条写进实现 issue 的验收清单。

## 位置与路由

- 顶栏加第 6 个一级 Tab「MCP」，快捷键 `6`，地址 `#/mcp`。不并进 Skills，因为 Skills 的数据属于这台机器，MCP 属于 workspace，两者筛选维度不同。地图 L3「能力」的工具列表在 Skills 后面加一项「MCP」。
- 角标是待处理里「会出问题」三个桶的条数之和（下文的 `ignored`、`shadowed`、`may_fail`），0 时隐藏。
- 参数：`ws=<slug>`、`view=matrix|list`（默认 `matrix`）、`issue=<桶代码>`、`archived=1`、`q=`。抽屉不写进 URL，与 Skills 相同。
- 在 MCP 页时，顶栏状态换成「MCP 快照 X 前」。下一行沿用 workspace 健康条，标签显示的是 MCP 采集状态。

## 页面结构

```
┌ Agent Atlas [地图 1][待决事项 2][Issues 3][Agents 4][Skills 5][MCP 6 ②]  ● MCP 快照 4 分钟前 ┐
│ ● ACM Lab  ● 萌芽辅导  ▲ Elder Oasis 分配读取失败  ○ 英语学习 未加载 …                      │
│ 库条目 5 · 已分配 4 · 无人使用 1 · 有自有配置的 agent 2 · 会被忽略 1 · 同名覆盖 1           │
├ [workspace ▾] [矩阵 | 清单] [只看有问题] [显示归档]                         搜索 /      ┤
│ ┌ ACM Lab · 库 3 · 分配 5 ───────────────────────────────────┐ ┌ 待处理 ──────────┐ │
│ │ Agent              Runtime        自有配置  linear sentry fs │ │ 会被忽略    1    │ │
│ │                                             http   http  io  │ │ 同名覆盖    1    │ │
│ │ Builder · Codex    Codex     ✓    —         ●      ·     ○   │ │ 可能失败    0    │ │
│ │ 调研 · Antigravity Antigrav. ✕    —         ⊘      ·     ·   │ │ 配置看不到  1    │ │
│ │ Reviewer · Hermes  Hermes    ✓    2 个      ◐      ●     ·   │ │ 无人使用    1    │ │
│ │ … 其余 20 个 agent 没有任何 MCP  [展开]                      │ │ 只有停用    0    │ │
│ └──────────────────────────────────────────────────────────────┘ └──────────────────┘ │
│ ┌ 萌芽辅导 · 库为空 · 没有 agent 有自有配置 ──────────────────────────── [如何添加] ┐  │
└──────────────────────────────────────────────────────────────────────────────────────┘
```

### 摘要卡

库条目数、已分配（`agent_count > 0`）、无人使用、有自有配置的 agent 数、会被忽略、同名覆盖。点击即筛选。范围跟随 workspace 筛选。

### 矩阵视图（默认）

每个 workspace 一块，因为库按 workspace 隔离，不同 workspace 的同名条目是两个 server。

- **行**是 agent，默认隐藏归档的。没有任何分配、也没有自有配置的 agent 收进一行「其余 N 个 agent 没有任何 MCP」，可以展开。
- **固定列**：
  - Agent 名。
  - Runtime 名加加载标记：`✓` 是，`✕` 否，`!` 有条件，`?` 未知，「未绑定」。悬停显示上表的传递方式和备注。
  - 自有配置：`—` 无，`N 个` 已托管，`托管·空` 是 `{}` 或空 `mcpServers`，`看不到` 是 redacted，`无法解析` 是 JSON 坏了。
- **server 列**：每个库条目一列。列头写名字、传输方式（`stdio` / `http` / `sse` / 原样的其他值）、`启用 N · 停用 M`。点列头打开 server 抽屉。超过 8 列时，这个 workspace 自动退回清单视图，并在块头说明原因。
- **单元格**只用一个符号，与 Skills 保持一致：

  | 符号 | 含义 | 颜色 |
  |---|---|---|
  | `●` | 已分配、已启用，runtime 会加载 | 绿 |
  | `○` | 已分配、已停用 | 灰 |
  | `·` | 未分配 | 浅灰 |
  | `⊘` | 已分配、已启用，但 runtime 不加载或未绑定 | 黄 |
  | `◐` | 已分配、已启用，但被 agent 自有配置里的同名 server 覆盖 | 黄 |
  | `!` | 已分配、已启用，runtime 有条件加载（OpenCode） | 黄 |
  | `?` | 这个 agent 的分配没读到（采集失败或尚未采集） | 灰虚线 |

  点单元格打开「agent × server」抽屉。键盘可以用方向键在格子之间移动，`Enter` 打开。

### 清单视图

扁平表，一行是一个「agent × server」，包括库分配和自有配置里的条目。列：workspace、agent、server、来源（库 / 自有）、启用、传输、runtime 加载、状态说明。适合搜索和窄屏（小于 1200px 时默认用清单）。

### 待处理栏

和 `/api/mcp` 的 `attention` 是同一份数据。空桶写「无」。

| 桶 | 计入角标 | 条件 |
|---|---|---|
| `ignored` 会被忽略 | 是 | 未归档 agent 有已启用的分配或自有配置，但 runtime 加载标记是 `no`，或者没绑 runtime |
| `shadowed` 同名覆盖 | 是 | 已启用的分配与 agent 自有配置里的某个 server 同名 |
| `may_fail` 可能失败 | 是 | agent 有托管 MCP，runtime 是 OpenCode（2.x 会直接报错）。以后有了 runtime 大版本数据再收窄 |
| `hidden` 配置看不到 | 否 | `mcp_config_redacted: true`，判断不了有没有同名覆盖 |
| `unused` 无人使用 | 否 | 库条目的 `agent_count` 是 0 |
| `disabled_only` 只有停用 | 否 | 库条目分配给了人，但全部停用 |

每条记录在各桶中独立计数，同一个 agent 可以同时出现在 `ignored` 和 `shadowed` 里。

### 空状态

真实数据现在就是空的，所以空状态是主路径，不是边角情况。

- 所有 workspace 的库都为空、也没有自有配置：主区域写「还没有 MCP server。库条目建好后不会自动给任何 agent，需要逐个分配」，下面给「新增库条目」的命令模板（见下文），以及「哪些 runtime 会加载」的摘要表。
- 某个 workspace 为空：折成一行块头，带「如何添加」，点开显示同样的模板。
- 后端还没有 `/api/mcp`（返回 404）：写「后端还没有 MCP 采集」，不显示成空库。
- 某个 workspace 的库读取失败：块头标红，写明原因，保留上次的数据并降低不透明度，与 Issues 的「旧数据」处理一致。分配读取失败时，单元格显示 `?`，不能显示成 `·`。

### 抽屉

都是非模态的右侧抽屉，`Esc` 关闭后焦点回到原来的格子或行。

- **agent 抽屉**：runtime 和加载标记（附传递方式、备注）。「下次任务实际生效」列表按优先级算好：库分配（去掉被覆盖的）加上自有配置，每条写来源。列表下方注明两件 Atlas 看不到的事：runtime 本机自己的 MCP 配置，和单次任务的集成 overlay。自有配置的每个 server 只显示名字、传输方式、`env 已设置 3 项` 或 `env 未设置`、`headers 已设置 1 项` 或 `headers 未设置`。`hidden` 时整段写「Multica 不向当前身份返回这份配置」。
- **server 抽屉**：名字、传输方式、创建和更新时间、配置一栏固定写「只写不读：Multica 不向任何人返回库条目的配置，无法判断 env / headers」。下面是已分配的 agent（启用、停用分开列），每个带加载标记。还有未分配、但 runtime 支持的 agent，每个带一条分配命令。
- **agent × server 抽屉**：一句话说明当前状态为什么是这个符号，加上相关命令。

## 操作范围

**v1 不执行任何写操作。** Atlas 的安全模型是只有 GET、CLI 精确白名单、没有 CSRF 面，MCP 配置又是密钥。在页面里加写接口，等于给本机任意网页多开一条可能带密钥的写路径。所以 v1 的「管理」是三件事：把分配关系看清楚，写清楚每个操作的影响，给出可以复制的命令。命令由用户在自己的终端里运行。页面上方固定一行：「页面只读，不代为执行。写命令需要 workspace admin/owner，或者能查看该 agent 密钥的身份；agent 会话里运行会被拒绝。」

CLI 白名单只增加 `workspace mcp list` 和 `agent mcp list` 两条只读命令。

| 操作 | 命令 | v1 | 页面上的确认方式 |
|---|---|---|---|
| 分配给 agent | `multica --workspace-id <ws> agent mcp add <agent> <server>` | 复制命令 | 直接给。runtime 不加载时，命令上方标黄并写明原因，命令照样给 |
| 启用 / 停用 | `agent mcp enable` / `disable <agent> <server>` | 复制命令 | 直接给 |
| 取消分配 | `agent mcp remove <agent> <server>` | 复制命令 | 直接给，并注明库条目和其他 agent 不受影响 |
| 改名 | `workspace mcp update <server> --name <新名>` | 复制命令 | 注明分配按 id 保留。新名和某个 agent 自有配置同名时，提示会产生覆盖 |
| 替换配置 | `workspace mcp update <server> --server-config-file ./<name>.mcp.json` | 复制模板 | 先列出受影响的已启用 agent（共 N 个，下一次任务就生效），再显示命令 |
| 新增库条目 | `workspace mcp add <name> --server-config-file ./<name>.mcp.json` | 复制模板 | 放在空状态和库块头 |
| 删除库条目 | `workspace mcp remove <server>` | 复制命令，**两步** | 先列出所有已分配的 agent，勾选「我知道会同时从这 N 个 agent 上移除」后才显示命令 |
| agent 自有配置 | `agent update <agent> --mcp-config-file ./<agent>.mcp.json`，清除用 `--mcp-config null` | 只给说明和清除命令 | 说明优先级比库条目高、会覆盖同名条目。页面不生成配置内容 |

命令规则：

- 涉及配置内容的命令**只生成 `--*-config-file` 形式**，文件名是占位符。不生成 `--server-config '<json>'` 或 `--mcp-config '<json>'`，也不生成往 stdin 里 echo JSON 的写法。模板下方固定提示：「文件里有密钥：建议 `chmod 600`，用完删除，不要放进 git 仓库。」
- 页面上没有任何配置输入框，也不提供 JSON 编辑器。
- 命令里的 id 全用 UUID，不用名字，避免同名歧义。
- 批量分配不在 v1。server 抽屉里，每个未分配的 agent 各有一条命令。

### 以后再做（不在 v1）

- 页面内执行写操作：需要独立的 POST 接口、每次会话的随机 token、逐条确认和审计日志，另开 issue 讨论。
- runtime 本机的 MCP 配置清单：Multica 有这份脱敏清单（只有名字、传输方式、来源），但 CLI 0.6.5 没有读取它的命令。Atlas 对本机/项目级 MCP 配置文件的读取规则见 `mcp-redesign.md` §3（只读 `config/mcp-sources.json` 声明的路径，fd 安全打开，不跟随符号链接，值只出指纹和状态，不落库、不进 API、不进日志）。
- 给 Steward 的 `/api/mcp/attention?format=text`。
- 按 OpenCode 的大版本收窄 `may_fail`。

## 密钥处理

原则是 Atlas 只保存「有没有」和「几项」，不保存值，不保存 key 名，不保存 URL、command、args。

1. **采集时就降成摘要**。`norm_agent` 在内存里解析 `mcp_config`，只产出摘要：每个 server 的名字、传输方式、env 和 headers 的「是否设置」与项数。原始对象随即丢弃，不写进 SQLite、不写日志、不放进异常信息。JSON 坏了就记成 `state: "invalid"`，错误信息是固定文案，不带原文片段。
2. **传输方式由后端判断**，规则与 Multica 的 `mcpTransportOf` 相同：显式 `type` 先归一（`local`/`stdio` 归为 `stdio`，`remote`/`http`/`streamable-http` 归为 `http`，其他值原样保留），没有 `type` 时有 `command` 就是 `stdio`，有 `url` 就是 `http`，否则 `unknown`。原样保留的值截断到 32 个字符。
3. **不输出**：env 和 headers 的 key 名与值、`url`、`command`、`args`、`cwd`，以及 server 条目里除传输方式以外的任何字段。URL 本身就可能是凭证（会话 URL）。
4. **server 名字**过 `_plain_text`，压空白后截断到 80 个字符，再过一次 `redact()`。
5. **CLI 输出**：`agent list` 的 stdout 不进日志，`-v` 时也只记命令和字节数。错误文本仍按现有规则先归类再脱敏。
6. **API**：`/api/mcp` 只输出契约里列出的字段。`/api/agents` 保持不输出 `mcp_config`。
7. **前端**：只渲染接口字段，没有配置输入框，命令只用 file 形式。
8. **测试必须带哨兵**：假 CLI 返回的 `mcp_config` 在 env 值、env key 名、headers 值、url、args、command 里各放一个唯一哨兵字符串。断言这些哨兵不出现在 SQLite 文件的原始字节、所有 `/api/*` 响应、`-v` 级别的日志，以及 JSON 坏掉时的错误信息中。同时断言项数统计正确。

## 采集

- 每轮对每个 workspace 调一次 `workspace mcp list`，成本是一次 CLI 调用。
- `agent mcp list` 是每个 agent 一次调用。只在下面两种情况调用：库不为空且 `agent_count` 之和大于 0；而且库的指纹（各条目的 `id + updated_at + agent_count`）变了，或者距离上次读取已经过了 `--mcp-every` 轮（默认 5 轮，约 5 分钟）。停用或启用不会改变 `agent_count`，所以要靠这个周期兜底。只读未归档的 agent，并发受现有的 `--max-cli` 限制。
- 库为空时不调用 `agent mcp list`，`bindings_state` 直接记为 `none`，表示「确定没有」，与「没读」区分开。
- 自有配置的摘要来自每轮已经在调用的 `agent list`，不增加调用。
- 撤权（`auth`）时，与其他资源一样清掉该 workspace 的 MCP 缓存。

## mock

- `?mock=mcp`：两个 workspace 有库。覆盖 Antigravity 被忽略、Hermes 同名覆盖、配置看不到、无人使用、OpenCode 可能失败，以及超过 8 列时退回清单。
- `?mock=mcp-empty`：全部为空，就是现在的真实数据。
- `?mock=mcp-partial`：一个 workspace 的库读取失败但保留旧数据，一个 workspace 的分配读取失败（`?`），一个 workspace 从未成功。
- `?mock=mcp-absent`：`/api/mcp` 返回 404。
- 打开时加上 `#/mcp`。fixtures 是虚构数据，里面也不能出现像 token 的字符串。

## 实现验收清单（给 Reviewer）

1. CLI 白名单只增加 `workspace mcp list`、`agent mcp list`；用测试证明 `workspace mcp add/update/remove`、`agent mcp add/enable/disable/remove`、`agent update` 会在启动进程前就被拒绝。
2. 哨兵测试按「密钥处理」第 8 条全部通过。
3. `/api/mcp` 的字段与契约一致，`/api/agents` 仍然没有 `mcp_config`。
4. 生效优先级和同名覆盖的计算与 Multica `ResolveAgentMcpConfig` 一致，至少覆盖这几种情况：只有库、只有自有、两者同名、自有在旧的 `mcp` 容器里、自有为 `{}`。
5. 库为空时不调用 `agent mcp list`；指纹不变且没到周期时也不调用。
6. 四个 mock 场景各有截图；空状态和读取失败不会显示成 0 或 `·`。
7. 生成的命令里没有 `--server-config '`、`--mcp-config '{`、`--server-config-stdin`。
8. `support.providers` 带来源版本，并且与 v0.6.5 的 `MCP_SUPPORTED_PROVIDERS` 一致；不在表里的 provider 显示 `?`。
9. 全程没有对真实 workspace 做写操作，测试只用 `tests/fake_cli.py`。
