# MCP 板块重做设计（ACM-378）

本文替换 `mcp-design.md` 里的版式、采集范围和接口部分；那份文档里的事实依据（各 runtime 是否加载、生效优先级、Multica 命令表、库条目只写不读）继续有效。设计依据是 `design-system.md`（`937e5ad`）、Skills 现有代码和本单描述里用户 10-23、10-24 的决定。

设计稿和对照图在 `docs/mcp-redesign/`：

- `mock.html`：静态设计稿，直接引用 `web/styles.css`，全部是虚构数据，不连后端。`?drawer=1` 打开抽屉，`?copyfail=1` 演示复制失败。
- `shoot.py`：从 fixtures 和设计稿重新生成对照图。只起本地静态服务（端口 8799），不碰真实看板。
- `compare-*.png`、`v1-vs-redesign-1440.png`、`copy-fail-1440.png`：对照图，见第 13 节。

本单只做设计。跨页面的 G 项归 ACM-389，跨板块跳转归 ACM-390，这里不做，也不借本单改 Skills 或其他板块的代码。

## 1. 定位（设计系统 §5 第 1–4 条）

**回答的问题**：本机各工具、agents-lab 项目和各 Multica Workspace 里配了哪些 MCP server，同名 server 在不同地方配得是否一样，哪些不会按预期生效。

**数据层**（§1.1）：

| 页面元素 | 数据层 |
|---|---|
| 每个工具一列（Cursor … OpenCode） | 本机：各工具的全局 MCP 配置文件 |
| 仓库入口列 | 仓库：agents-lab 工作区里的项目级配置文件 |
| 工具列表和文件位置 | 仓库：`multica-board/config/mcp-sources.json`（新增，前端不硬编码） |
| Multica Workspace 列 | Multica workspace 的库 + agent 的分配和自有配置 |
| 一致性列 | 本机 + 仓库 + agent 自有配置的指纹比对；库条目不可比 |
| runtime 是否加载 | 仓库：`mcp_support.py` 的加载表 |

**为什么是顶栏板块**：沿用用户已定的「独立 MCP 板块，不与 Skills 合并」。问题和数据形状与 Skills 平行（一类资源装在哪、哪些要处理），所以照搬版式 A，但对象不同，不做成 Skills 的子视图。

**健康条形态**：改成「固定范围说明 + 数据源健康条」，和 Skills 相同。理由：重做后一行是跨 workspace 的同名 server，和 Skills 一样不按 workspace 切分；§1.3 规定这种情况用固定范围说明，不显示一排点了没反应的 workspace 标签。这一改动同时消除 M7。

- 范围说明：「范围：本机各工具全局 MCP 配置、agents-lab 项目级配置，以及全部 Multica Workspace 的库与 agent。workspace 筛选不作用于这一页。」
- 健康条 `meta.sources`：本机配置、仓库项目级、配置指纹、Multica 库、Agent 分配、Agent 自有配置、Runtime 加载表，共 7 个 `.src-chip`。
- 顶栏状态：「MCP 快照 X 前 · 本机扫描 Y 前」；读取失败时沿用 `mcpSnapshot()` 的 warn 写法。

**进出跳转**：

- 进：顶栏 `6`；地图 L3 工具行（`MAP_TOOLS` 已有）。`toolSignal("mcp")` 改为「N 个 server · 坏了 X · 待处理 Y」，没有待处理时「N 个 server · 无待处理」，未接上仍是「还没接上」。告警圆点条件不变（角标大于 0）。
- 板块内：待处理条目调用 `focusMcp(name)`，做法同 `focusSkill`：清筛选、展开所在分组、滚到行、打开抽屉。
- 出：没有。抽屉里的 agent 名只是文字；跳到 Agents 或 Skills 归 ACM-390。

## 2. 版式（第 5–8 条）

沿用版式 A，从上到下与 Skills 一一对应：

```
┌ 来源卡片 .source-grid（全部 · 本机 + Multica · 仅本机 · 仅 Multica · 仓库项目级）┐
├ 工具栏 .toolbar：来源▾ 状态▾ 工具▾ 分组▾ □只看已分配给 agent                  ┤
├ 异常提示条 .skills-anomaly（只在采集本身有问题时出现）                           ┤
├ 图例 .skills-legend                                                              ┤
│ ┌ 一览表 .table-wrap.sticky-head ─────────────────────┐ ┌ 待处理 .skills-queue 352px ┐ │
│ │ ▸ 分组行 本机 + Multica · 3                          │ │ 条目损坏 1                 │ │
│ │   Server 来源 Workspace ● ● ● … 仓库入口 传输 一致性 │ │ 会被忽略 1 … 配置不一致 2  │ │
│ └──────────────────────────────────────────────────────┘ │ 待清理 · 无人使用/只有停用 │ │
│                                                          └────────────────────────────┘ │
└ 右侧详情抽屉 #drawer（点行打开）                                                      ┘
```

**一行是什么**：一个 server 名。本机文件、项目文件、各 workspace 的库条目和 agent 自有配置里同名的条目合在一行，和 Skills「一个 skill 名一行，各 workspace 的安装放在 Multica Workspace 列」的做法相同。按工具分组时同一 server 会出现在多组，组标题写「同一 server 可能重复出现」。名字按原样比较，不做大小写归一。

**来源卡片**是行的分区，全部可点，点卡片等于设置「来源」下拉，两者写同一个 `source=`：

| 卡片 id | 标签 | 条件 |
|---|---|---|
| `all` | 全部 | 全部行 |
| `both` | 本机 + Multica | 本机或项目文件里有，Multica 也有 |
| `local_only` | 仅本机 | 只在本机或项目文件里 |
| `multica_only` | 仅 Multica | 只在库或 agent 自有配置里 |
| `project`（前端合成的伪来源） | 仓库项目级 · agents-lab | 项目文件里有同名条目，和上面三块重叠，做法同 Skills 的 `CLOUD_SOURCE` |

卡片内容：标签、数字、按一致性的拆分。损坏用 `.bad-n`，不一致用新增的 `.warn-n`，其余 `.muted`。本机没扫描或 Multica 从未采集时数字写「—」并说明原因。

**工具栏**：来源、状态（损坏 / 不一致 / 一致 / 仅一处 / 不可比）、工具（各工具 + 仓库入口）、分组（按来源 / 按工具 / 不分组）、复选框「只看已分配给 agent」。没有视图切换，没有 workspace 下拉。

**列的顺序**（身份 → 来源 → 跨层 → 本机工具 → 仓库入口 → 状态）：

| 列 | 内容 | 对应 Skills |
|---|---|---|
| Server | 等宽名字 + 所在桶的标签（如 `配置不一致`、`条目损坏`，和 Skills 把漂移标签放在名字旁一样） | Skill |
| 来源 / 出现位置 | 第一行来源标签，第二行灰色等宽「本机 4 · 仓库 1 · 库 1 · 自有 1」，`.channel-col` 164px | 来源 / 更新通道 |
| Multica Workspace | `.cloud-cell`：最多 3 个 workspace，每个附已启用 agent 简写（`agentBrief`），自有配置写「（自有）」；没采集写「—」；全部采集了但没有写「Multica 未配置」 | Multica Workspace |
| 每个工具一列 | 见第 7 节符号表。列头写短名（Cursor、Codex、Claude、Hermes、Gemini、Devin、Antigravity、Windsurf、OpenCode），`<small>` 写 `●` 的个数，`title` 写全名、文件路径和读取状态 | 每个工具一列 |
| 仓库入口 | `.repo-entries` + `.entry-chip`：`.mcp.json ●`、`.cursor ●`、`.codex ●`、`.gemini ●`、`opencode.json ●` | 仓库入口 |
| 传输 | `stdio` / `http` / `sse`；各处不同时写「stdio / http」并标黄，`.version-col` 112px | 版本 |
| 一致性 | `tag`：一致（ok）、不一致 · N 组（warn）、损坏（bad）、仅一处（muted）、不可比（muted） | 对账 |

**和 Skills 不同的地方**（逐条理由）：

1. 没有「共享入口」列：MCP 没有跨工具共享的配置目录（Skills 的 `~/.agents/skills` 没有对应物）。`○` 因此不用，保留它在 Skills 的含义。
2. 没有「定制 · 约束」「复核」列：MCP 没有清单基线和人工复核记录。最右一列是状态（一致性），符合「状态在最右」。
3. 待处理栏底部的不计数按钮是「待清理 · 无人使用 / 只有停用」，对应 Skills 的「基线债」；没有复核阈值设置。
4. 列头用短名：9 个工具加全名时 1440px 下一致性列会被挤出视口（见第 13 节）。全名在 `title` 里。

## 3. 采集范围与保密边界

### 3.1 文件清单（`multica-board/config/mcp-sources.json`）

文件位置写在仓库声明里，后端只读这里列出的路径，不做通配、不跟随配置里的 include。新增工具只改这份文件。

| 工具 id | 短名 | 全局文件 | 读取的容器 | 格式 |
|---|---|---|---|---|
| `cursor` | Cursor | `~/.cursor/mcp.json` | `mcpServers` | JSON |
| `codex` | Codex | `~/.codex/config.toml` | `mcp_servers` | TOML（`tomllib`） |
| `claude` | Claude | `~/.claude.json` | 顶层 `mcpServers`，其他键一概不读 | JSON |
| `hermes` | Hermes | `~/.hermes/config.yaml` | `mcp_servers` | YAML 子集（见下） |
| `gemini` | Gemini | `~/.gemini/settings.json` | `mcpServers` | JSON / JSONC |
| `mcode` | Devin | `~/.config/devin/mcp_config.json` | `mcpServers` | JSON |
| `antigravity` | Antigravity | `~/.gemini/antigravity/mcp_config.json` | `mcpServers` | JSON |
| `windsurf` | Windsurf | `~/.codeium/windsurf/mcp_config.json` | `mcpServers` | JSON |
| `opencode` | OpenCode | `~/.config/opencode/opencode.json` | `mcp` | JSON / JSONC |

项目级（agents-lab 工作区根目录，即后端的 `REPO_ROOT`）：`.mcp.json`、`.cursor/mcp.json`、`.codex/config.toml`、`.gemini/settings.json`、`opencode.json`。工具 id 与 `tool-roots.json` 保持一致（Devin 用 `mcode`）。实现前请对每个工具按其当前文档再核对一次路径和容器名，核对结果写进该文件的 `evidence` 字段。

YAML：后端只用标准库，不新增依赖。Hermes 用一个只认 `mcp_servers:` 块的受限读取器：块状映射、普通和引号标量、块状和行内列表。遇到锚点、标签、多行字符串等就整份文件记为 `unsupported`，Hermes 列显示 `?`，不猜。

### 3.2 读取规则

- 每个文件先 `lstat`：不存在记 `absent`（确定没有，格子留空，列头数字 0）；无权限记 `denied`；超过 16 MB 记 `too_large`；解析失败记 `parse_error`；格式超出读取器能力记 `unsupported`。后四种该列的格子显示 `?`。
- 错误只记固定代码，不用异常原文。`json`、`tomllib` 和 YAML 的错误信息可能带出原文片段，一律不进日志、接口和 SQLite。
- 解析只在内存里进行，取出容器后立刻降成摘要（3.3），原始对象丢弃。本机部分不写 SQLite，按请求计算，用文件的 `(mtime, size, inode)` 做 15 秒缓存，和 Skills 的 `CACHE_TTL_SECONDS` 一样。
- agent 自有配置沿用 v1 的 `summarize_mcp_config`，加上同一套指纹计算。CLI 白名单不变，不新增命令。
- server 名过 `plain_text`（压空白、截到 80 字符、`redact()`），与 v1 相同。

### 3.3 每个条目的摘要和指纹

摘要只有：名字、`state`（`ok` / `broken`）、`disabled`、`transport`、`env {set, count}`、`headers {set, count}`、指纹组字母。

传输方式沿用 `transport_of`，补充各工具的写法：`serverUrl`、`httpUrl` 归为 `http`，Gemini 的 `url` 归为 `sse`，OpenCode 的 `type: local/remote` 和数组形式的 `command`。既没有 command 也没有 url，或类型无法识别，记为 `broken`。

指纹分四份，都用 HMAC-SHA256：

| 指纹 | 输入（规范化后的 JSON，键排序） |
|---|---|
| `launch` | 传输方式 + `command` + `args` + `cwd`，或者 `url` |
| `env` | env 的键值对；Codex 的 `env_vars`（只有名字）按键、值为 null 计入；OpenCode 的 `environment` |
| `headers` | headers 的键值对；Codex 的 `http_headers`、`env_http_headers`、`bearer_token_env_var` |
| `all` | 上面三份合在一起 |

不参与比对：启用开关、超时、工具白名单、trust 等。抽屉里写明这一点。

**密钥**：首次使用时生成 32 字节随机数，存 `~/.local/share/agents-lab/mcp-fingerprint.key`，权限 `0600`。权限比 `0600` 宽时拒绝使用，「配置指纹」健康标签变黄，一致性全部显示「不可比」，`drift` 桶显示「— 指纹密钥不可用」。用加盐哈希而不是裸 SHA-256，是为了让知道候选值的人无法用指纹验证猜测。

**指纹不出后端**：比对在后端完成，接口只输出组字母（A、B、C…）和三项分项结论（一致 / 不同），不输出任何哈希值。组字母按成员数从多到少、再按工具顺序分配。

**一致性结论**（行级 `consistency.state`）：

| state | 条件 | 显示 |
|---|---|---|
| `broken` | 至少一个条目 `broken` | 损坏（红） |
| `differs` | 可比条目至少 2 个且分成 2 组以上 | 不一致 · N 组（黄） |
| `same` | 可比条目至少 2 个且只有 1 组 | 一致（绿） |
| `single` | 只有 1 个可比条目 | 仅一处（灰） |
| `incomparable` | 没有可比条目（只有库条目） | 不可比（灰），`title` 写「Multica 库条目只写不读」 |

有格子是 `?` 时加 `partial: true`，标签 `title` 写「部分工具未读到，结论只覆盖已读的」。

### 3.4 永远不输出

env 和 headers 的键名与值、`url`、`command`、`args`、`cwd`、条目里的其他字段、指纹值、指纹密钥、文件原文片段、解析错误原文。适用于 `/api/*` 响应、SQLite、日志（含 `-v`）、异常信息和 fixtures。

文件路径可以显示（`~` 开头，用 `redact` 的 home 路径规则），它是证据，不是取值。

## 4. 数据契约：`GET /api/mcp`（`mcp_schema: 2`）

形状照抄 Skills（§2.1「数据契约」）。没有查询参数，带参数返回 400，与 v1 相同。示例为虚构数据，和设计稿一致：

```json
{
  "ok": true,
  "error": null,
  "mcp_schema": 2,
  "generated_at": "2026-10-24T02:01:00Z",
  "multica_collected_at": "2026-10-24T01:58:00Z",
  "tools": [
    { "id": "cursor", "label": "Cursor", "short_label": "Cursor",
      "files": [ { "path": "~/.cursor/mcp.json", "scope": "global", "state": "ok", "entry_count": 6 } ] },
    { "id": "opencode", "label": "OpenCode", "short_label": "OpenCode",
      "files": [ { "path": "~/.config/opencode/opencode.json", "scope": "global", "state": "absent", "entry_count": 0 } ] }
  ],
  "repos": [
    { "id": "agents-lab", "label": "agents-lab",
      "files": [ { "path": ".mcp.json", "location": ".mcp.json", "state": "ok", "entry_count": 1 },
                 { "path": ".codex/config.toml", "location": ".codex", "state": "absent", "entry_count": 0 } ] }
  ],
  "workspaces": [
    { "id": "<uuid>", "slug": "acm-lab", "name": "ACM Lab", "library_state": "ok",
      "library_error": null, "bindings_state": "ok", "collected_at": "2026-10-24T01:58:00Z" }
  ],
  "support": { "source": "multica v0.6.5 packages/core/agents/mcp-support.ts", "providers": { "codex": { "loads": "yes" } } },
  "items": [
    {
      "key": "github",
      "name": "github",
      "source_class": "both",
      "source_label": "本机 + Multica",
      "in_project": false,
      "presence": { "local": 4, "project": 0, "library": 1, "own": 0 },
      "transports": ["stdio"],
      "tools": { "cursor": "direct", "codex": "direct", "claude": "direct", "hermes": "direct", "gemini": null },
      "entries": [
        { "scope": "global", "tool": "cursor", "path": "~/.cursor/mcp.json", "state": "ok", "disabled": false,
          "transport": "stdio", "group": "A", "env": { "set": true, "count": 2 }, "headers": { "set": false, "count": 0 } }
      ],
      "workspace_entries": [
        { "kind": "library", "workspace_id": "<uuid>", "workspace_name": "ACM Lab", "server_id": "<uuid>",
          "transport": "stdio", "agent_count": 2, "updated_at": "2026-10-20T08:00:00Z", "stale": false,
          "agents": [ { "id": "<uuid>", "name": "Builder · Codex", "enabled": true, "shadowed": false,
                        "provider": "codex", "runtime_loads": "yes" } ] }
      ],
      "consistency": { "state": "differs", "partial": false,
        "groups": [ { "id": "A", "members": ["cursor", "claude"] }, { "id": "B", "members": ["codex", "hermes"] } ],
        "parts": { "launch": "same", "env": "differs", "headers": "same" } },
      "tidy": null,
      "bucket": "drift",
      "tags": [],
      "reason": "Cursor、Claude Code 与 Codex、Hermes Agent 的 env 不同",
      "quiet": false,
      "command": "\"${EDITOR:-vi}\" ~/.codex/config.toml"
    }
  ],
  "summary": [
    { "id": "all", "label": "全部", "count": 11,
      "statuses": { "broken": 1, "differs": 2, "same": 4, "single": 1, "incomparable": 3 } }
  ],
  "attention": {
    "badge": 6,
    "tone": "bad",
    "counts": { "broken": 1, "ignored": 1, "shadowed": 1, "may_fail": 1, "drift": 2 },
    "buckets": {
      "drift": { "connected": true, "reason": "",
        "items": [ { "identifier": "github", "title": "Cursor、Claude Code 与 Codex、Hermes Agent 的 env 不同",
                     "status": "drift", "time": "2026-10-24T02:01:00Z", "source_class": "both", "tags": [] } ] }
    },
    "tidy": 2
  },
  "meta": {
    "sources": [
      { "id": "local_files", "label": "本机配置", "state": "ok", "detail": "8 已读 · 1 不存在" },
      { "id": "multica_own_config", "label": "Agent 自有配置", "state": "warn", "detail": "1 个不可见" }
    ]
  }
}
```

字段说明：

- `tools.<id>` 格子值：`direct`（有条目）、`off`（有条目，配置里显式停用）、`missing`（条目损坏）、`unknown`（文件读不出来）、`not_applicable`（这一行只在 Multica）、`null`（已读文件，没有同名条目）。
- `entries[].group`：所属指纹组字母；`broken` 或不可比时为 `null`。项目文件的条目 `scope: "project"`，`tool` 为 `null`，`location` 写 `.cursor` 等。
- `workspace_entries[].kind`：`library` 或 `own`（agent 自有）。`own` 带 `agent_id`、`agent_name`、`provider`、`runtime_loads`、`group`、`env`、`headers`。agent 的 `bindings_state` 不是 `ok` 时，`agents` 为 `null`，前端写「分配没读到」，不写成没有分配。
- `workspace_entries[].stale`：这个 workspace 本轮库读取失败、显示的是上次数据。
- `summary`：后端给 `all`、`both`、`local_only`、`multica_only` 四块；`project` 块由前端按 `in_project` 合成。
- `attention.buckets`：五个桶都出现，空桶 `items: []`；`connected: false` 时写原因。
- `ok: false` + `error`：整份数据无效（如 `mcp-sources.json` 读不出来），前端显示 `.atlas-guide`。
- `/api/agents` 仍然不输出 `mcp_config`。

## 5. 待处理栏和角标（第 9 条）

分桶在后端完成，顺序固定，一个对象只进一个桶，取第一个命中的，其余命中变成行上的 `tags`：

| 顺序 | 代码 | 标题 | 条件 | 类别 |
|---|---|---|---|---|
| 1 | `broken` | 条目损坏 | 任一本机、项目或自有条目 `broken` | 坏了（红） |
| 2 | `ignored` | 会被忽略 | 未归档 agent 已启用这个 server（库分配或自有配置），但 runtime 不加载 MCP 或没绑 runtime | 坏了（红） |
| 3 | `shadowed` | 同名覆盖 | 已启用的库分配被同一 agent 自有配置里的同名 server 覆盖 | 坏了（红） |
| 4 | `may_fail` | 可能失败 | 已启用给 provider 为 `opencode` 的 agent | 坏了（红） |
| 5 | `drift` | 配置不一致 | `consistency.state` 为 `differs` | 等用户处理（黄） |

- 不计入角标：`tidy`（库条目无人使用，或分配了但全部停用），作为底部虚线按钮「待清理 · 无人使用 / 只有停用」，点击切换 `tidy=1` 筛选，做法同基线债。
- v1 的 `hidden`（agent 自有配置不可见）不再是桶。它是采集本身的限制，放到异常提示条和「Agent 自有配置」健康标签（黄）。
- 空桶写「无」。`connected: false` 时计数写「—」，正文写「— 原因」：Multica 从未采集时，`ignored`、`shadowed`、`may_fail` 写「— Multica MCP 尚未采集」；指纹密钥不可用时，`drift` 写「— 指纹密钥不可用，未比对」；本机没扫描时，`broken`、`drift` 写「— 本机配置未扫描」。
- 桶标题只是标题，不做筛选按钮。条目点击调用 `focusMcp`。计数在红类桶用 `.bad-n`，黄类桶用 `.warn-n`，0 用 `.muted`。
- 小于 1200px 时渲染 `.skills-compact`：「条目损坏 1 · 会被忽略 1 · 同名覆盖 1 · 可能失败 1 · 配置不一致 2 · 待清理 2」。
- `reason` 不超过 80 字，例如「调研 · Antigravity 的 runtime 不加载 MCP（ACM Lab）」；涉及多个 agent 时写第一个加「等 N 个」。

**角标**（执行用户 10-24 的决定）：数字 = 五个桶的条数之和。颜色由后端给的 `attention.tone` 决定：红类桶有任何一条时为 `bad`（红），否则 `drift` 有条目时为 `warn`（黄），都为 0 时隐藏。颜色不是唯一线索：`title` 和 `aria-label` 写「MCP 待处理 6 条：坏了 4 · 待你处理 2」。红色用新增的 `.tab-badge.tone-bad`，黄色用 `.tab-badge` 的默认样式。

判定理由：前四个桶的配置不会按预期生效或会让任务失败，属于「坏了」；配置不一致需要用户决定以哪一份为准，属于「等用户处理」。这个划分是本设计的判断，在第 12 节列为待用户确认项。

## 6. 详情抽屉（第 10 条）

一种对象一种抽屉，`state.drawer.kind = "mcp"`。非模态，`Esc` 或 × 关闭，焦点回到原来的行（`rowId` 为 `mcp-<encodeURIComponent(name)>`）。

抽屉头：等宽名字 + 传输方式标签 + 关闭；`h2.drawer-title` 写来源标签。各节按「是什么 → 什么状态 → 证据 → 怎么处理」：

1. **来源与范围**：名称、来源、传输、本机工具列表、仓库项目级、Multica（库条目数 · 自有数）。
2. **一致性**：状态标签；每个指纹组一行（成员）；启动方式、env、headers 三项各写一致或不同，env 和 headers 附各组项数；固定说明「只比较配置指纹，不显示取值」和「Multica 库条目只写不读，不参与比对。停用、超时、工具白名单等字段不参与比对。」
3. **本机工具配置**：每个条目一行 `.skill-entry`：符号、等宽路径、工具名、组字母、`env 已设置 N 项` 或 `env 未设置`、headers 同理、停用时写「已停用」。末尾「路径证据，未验证 runtime 当前会话实际加载。」
4. **仓库项目级**：同上。条目 env 已设置时加一句「项目级文件在仓库工作区里；env 有值时确认它没有被提交。」
5. **Multica Workspace（跨 Workspace）**：先写只读提示（第 8 节），再每个 workspace 一块 `.cloud-install`：库条目（传输、更新时间、固定说明「配置只写不读」），已启用和已停用的 agent（各附加载结论标签：会加载 / 不加载 / 有条件 / 未知 / 未绑定），各自的停用 / 启用 / 取消分配命令；自有配置条目（agent、组字母、env/headers 项数、清除自有配置命令）。替换配置和删除库条目放进 `<details>`，展开后先列影响范围，删除沿用 v1 的勾选两步。命令沿用 v1 `commandsFor` 的表，全部带 `--workspace-id <uuid>`，配置内容只用 `--*-config-file` 形式，下方固定提示「文件里有密钥：建议 chmod 600，用完删除，不要放进 git 仓库。」
6. **在终端处理**：只读提示 + 「改本机文件后，下一次扫描生效。」；每个相关本机或项目文件一条「打开 X 配置」命令 `"${EDITOR:-vi}" <路径>`；固定提示「文件里有密钥：保持 chmod 600，不要放进 git 仓库，共享屏幕时不要打开。」页面不生成任何带取值的命令，也不提供 diff 命令（diff 会把取值打到终端）。

v1 的「agent 视角」（某个 agent 下次任务实际拿到哪些 server）在新版式里没有单独入口：每行抽屉的 Multica 节写了各 agent 的加载结论，但没有按 agent 汇总的列表。按 agent 看属于 Agents 板块或跨板块跳转（ACM-390），本单不补。

## 7. 组件和视觉（第 11–15 条）

**复用**（不改类名，G16 改名归 ACM-389）：`.source-grid` `.source-card` `.source-recon`、`.toolbar` `.filter` `.check`、`.skills-anomaly`、`.skills-legend` `.legend-group`、`.skills-layout` `.skills-main`、`.table-wrap.sticky-head`、`table.skills-table`、`.skill-group` `.skill-more` `.skill-row`、`.channel-col` `.version-col` `.cell-ellipsis`、`.tool-col` `.tool-mark` 及 `.missing` `.unknown` `.not-applicable`、`.repo-entries` `.repo-group` `.repo-name` `.entry-chip`、`.cloud-cell` `.cloud-ws` `.cloud-install`、`.skills-queue` `.skills-compact` `.skill-bucket` `.queue-item` `.debt`、`#drawer` 及抽屉各类、`.command`、`.skill-entry`、`.tag.*`、`.bad-n`、`.atlas-guide`、`.empty`、`.is-stale`、`.scope-note`、`.src-chip`。`section()`、`line()` 在 `mcp.js` 里保留现有一份，不和 `skills.js` 合并（G15 归 ACM-389）。

**新增 CSS**（只有三处，都放在 skills 段后面）：

| 类 | 写法 | 现有的为什么不够 |
|---|---|---|
| `.tab-badge.tone-bad` | `background: var(--bad); color: var(--bg);` | 红色角标现在只有 `.tab-badge.skills`，带页面名，而且写死 `#fff`；MCP 需要按 `tone` 切换 |
| `.warn-n` | `color: var(--warn); font-weight: 600;` | 只有 `.ok-n` 和 `.bad-n`，「等用户处理」的黄色计数没有对应类 |
| `.tool-mark.off`（含 `sup`） | `color: var(--muted)`；`sup` 9px | 停用要和 `●` 区分，又不能借 `○`（Skills 里是「经共享目录」） |

**删除**：`styles.css` 里全部 `.mcp-*` 选择器（`.mcp-summary` 到 `.mcp-queue`），以及 `mcp.js` 的矩阵、清单、三种抽屉代码。

**格子符号**（只用 Skills 已有的含义，`○` 不用）：

| 符号 | 含义 | 样式 |
|---|---|---|
| `●` | 该工具的全局配置里有同名条目 | 正文色 |
| `●`<sup>停</sup> | 有条目，配置里显式停用 | `.tool-mark.off` 灰 |
| `✕` | 条目损坏（缺 command 和 url，或类型无法识别） | `.missing` 红 |
| `?` | 无法确定（文件无权限、解析失败、格式超出读取范围、超大） | `.unknown` 黄 |
| `∅` | 不适用：这一行只在 Multica | `.not-applicable` 灰 |
| 空白 | 已读该文件，没有同名条目；仍可能来自该工具的其他配置层 | — |

格子 `title` 写含义、文件路径、指纹组字母，以及「路径证据，未验证 runtime 实际加载」。图例按列分组：本机工具、仓库入口、Multica Workspace、一致性，原文见 `mock.html`。

**颜色**：只用 §4 的变量。损坏、会被忽略、同名覆盖、可能失败用 bad；配置不一致、无法确定、旧数据用 warn；一致、会加载用 ok；其余 muted。每个颜色都同时有文字或符号。

**四种情况**（§4.5）：

| 情况 | 卡片 / 计数 | 格子 / 列 | 页面 |
|---|---|---|---|
| 有 | 数字 | 符号 | 正常 |
| 确定没有 | 0、空桶「无」 | 空白；文件不存在时列头 0，`title`「文件不存在：确定没有全局 MCP」 | 全空时主区显示 `.atlas-guide`（见下） |
| 没采集 | 「—」+ 原因 | Multica 列「—」；有 workspace 未采集时不写「Multica 未配置」 | 桶「— 原因」 |
| 读取失败 | 保留上次数据 | 文件读不出来的列整列 `?`；库读取失败的 workspace 在格内加「旧数据」标签 | 异常提示条 + 健康标签变红或黄；整份接口失败时表格加 `.is-stale`，提示条写「本轮读取失败，显示 X 前的数据」 |

全空：主区 `.atlas-guide`，`h2`「还没有 MCP server」，正文「本机各工具的全局配置和 agents-lab 项目级文件里都没有 MCP 条目，Multica 库也为空。库条目建好后不会自动给任何 agent，需要逐个分配。」，下面是只读提示和各 workspace 的「新增库条目」命令（放进 `<details>`）。不再把命令和 runtime 支持表放进 `.empty.big`。

接口 404：`.atlas-guide`「后端还没有 MCP 采集」。`ok: false`：`.atlas-guide`「MCP 数据无效」+ 原因类别 + 怎么修。

## 8. 只读提示、命令和复制

- 只读提示用 Skills 原句：「页面只读，不代为执行。复制命令后在自己的终端运行。」只出现在抽屉的命令区上方（Multica 节和「在终端处理」节），页面顶部不再放。后面可跟一句影响说明：Multica 节是「写命令需要 workspace admin/owner；agent 会话里运行会被拒绝。」，本机节是「改本机文件后，下一次扫描生效。」
- 复制按钮成功写「已复制」，失败写「复制失败」，并做回退：
  1. 先用 `navigator.clipboard.writeText`。
  2. 失败或不可用时，用隐藏 `textarea` + `document.execCommand("copy")`，**检查返回值**。
  3. 仍失败时，按钮写「复制失败」，把这条命令的 `<code>` 内容选中（`Range.selectNodeContents`），并在命令下方插入一行灰字「已选中命令，按 ⌘C 复制」。
- 实现为 `mcp.js` 导出的 `copyWithFallback(text, env)`，返回 `true/false`，`env` 可注入剪贴板和 `document` 以便测试。不改 `app.js` 的 `copyText`（它吞掉了失败，所以 v1 的「复制失败」实际不会出现）和 `skills.js` 的 `copyCommand`，统一归 ACM-389。

## 9. 路由、搜索和键盘

- hash 参数：`#/mcp?source=both&status=differs&tool=cursor&group=tool&assigned=1&tidy=1&q=…`。`source`、`tool` 复用 route 现有字段，按 MCP 的取值校验；新增 `mcpStatus`、`mcpGroup`、`mcpAssigned`、`mcpTidy`，在 `parseHash` / `formatHash` / `applyPatch` 登记，离开板块时清掉。删除 `mcpView`、`issue`；MCP 不再读 `archived` 和 `ws`（`ws` 仍按 §1.4 跨板块保留）。旧链接里的 `view=list`、`issue=`、`archived=1` 在规范化时丢弃。
- `filterChrome("mcp")` 从 `shared` 改为与 Skills 相同的范围说明形态，`renderChrome` 按当前板块取 `state.mcp.meta.sources`。`mcpWorkspaceChip` 删除。
- 搜索匹配：server 名、来源标签、出现的工具名、文件路径、workspace 名和 slug、agent 名。不匹配组字母。placeholder「搜索 server / 工具 / workspace / agent」。
- 键盘：`6` 进入，`/` 搜索，`Tab` 到行，`Enter` 打开抽屉，`Esc` 关闭并回到原行。v1 格子间的方向键导航随矩阵一起删除。

## 10. §6.1 M1–M13 逐项映射

| 编号 | v1 问题 | 重做后的做法 | 验收看哪里 |
|---|---|---|---|
| M1 | 指标卡不是分区，3 张不可点 | 来源卡是行的分区，全部可点，和「来源」下拉写同一个 `source=`；`.mcp-card` 删除 | `compare-1440.png` 顶部；点每张卡，下拉同步 |
| M2 | 每个 workspace 一块、各自一张表 | 一张表 + 分组行，一个限高滚动区 | `v1-vs-redesign-1440.png` |
| M3 | 列是动态库条目，超 8 列退回清单 | 列是 `mcp-sources.json` 里的固定工具 + 仓库入口 | `compare-1440.png` 表头 |
| M4 | 另起一套符号，`○` 含义冲突，没有图例 | 只用 `● ✕ ? ∅ 空白`，`●` 正文色，新增的停用用 `●停` 上标；`○` 不用；页面有图例，列头 `title` 写证据 | 图例行、格子 `title` |
| M5 | 三种抽屉 | 一种抽屉，`kind = "mcp"` | `compare-1440-drawer.png` |
| M6 | 矩阵 / 清单切换 | 删除，只有「分组」下拉 | 工具栏 |
| M7 | 工具栏 workspace 下拉和顶栏标签写同一个 `ws` | 两者都去掉，改为固定范围说明 + 健康条 | 顶栏第三行 |
| M8 | 只读提示在页面顶部 | 只在抽屉命令区上方，用 Skills 原句 | `copy-fail-1440.png` |
| M9 | 桶标题是筛选按钮，条目开三种抽屉，窄屏只剩空框 | 桶标题只是标题；条目走 `focusMcp`；渲染 `.skills-compact` | `compare-1100.png` 侧栏上移成一行计数 |
| M10 | 一条记录可进多个桶 | 一个对象一个桶，其余变 `tags`；桶在后端算 | 接口测试 + 待处理栏 |
| M11 | 失败借 `.decide-note`，全空塞进 `.empty.big` | 失败用 `.skills-anomaly` + `.is-stale`；全空、404、无效用 `.atlas-guide` | `mcp-partial`、`mcp-empty`、`mcp-absent`、`mcp-invalid` 截图 |
| M12 | 行不能点 | 整行可点，`tabindex="0"`，`Enter` 打开，选中加 `.selected` | 键盘走查 |
| M13 | 角标默认黄 | 按 `tone`：坏了红、只有待处理时黄 | `compare-1440.png` 顶栏（红）；`mcp-warn` 截图（黄） |

## 11. 实现拆分建议

1. 后端：`mcp-sources.json`；`mcp_local.py`（各格式读取、摘要、HMAC 指纹、密钥文件）；`mcp_view.py` 改为 schema 2（行合并、一致性、分桶、`tone`、`summary`、`meta.sources`）；`collector` 的自有配置摘要加指纹。
2. 前端：`mcp.js` 按 `skills.js` 的结构重写（`renderMcp`、`renderMcpDrawer`、`filteredItems`、`groupedItems`、`queueModel`、`copyWithFallback`）；`app.js`（抽屉分派、`focusMcp`、角标 tone、`toolSignal`、`mcpCtx`）；`route.js`；`filter-bar.js`；`styles.css`。
3. fixtures：`write-mcp.js` 生成 `mcp`、`mcp-empty`、`mcp-partial`、`mcp-absent`（沿用 app.js 的 404 模拟）、`mcp-invalid`、`mcp-warn`，登记进 `scenarios.json`。
4. 文档同步：`api-contract.md`（v0.10，`/api/mcp` schema 2）、`mcp-design.md`（头部已指向本文；删掉「不允许 Atlas 读 `~/.cursor/mcp.json`」那句，改为指向第 3 节）、`design-system.md` §1.2 MCP 行和 §1.3 形态、§3.3 删 `.mcp-card`、§6.1 标注已由 ACM-378 解决、`frontend-design.md` MCP 一节、`README.md` 安全边界（新增本机 MCP 配置的只读范围和指纹密钥文件）。

## 12. 待用户确认（本设计的选择，不是已批准事项）

1. 红黄划分：条目损坏、会被忽略、同名覆盖、可能失败算「坏了」（红），配置不一致算「等用户处理」（黄）。
2. 「无人使用 / 只有停用」不计入角标，做成「待清理」虚线按钮；「自有配置不可见」从桶改为异常提示。
3. 健康条从 workspace 标签改为固定范围说明，页面不再按 workspace 筛选（搜索仍能匹配 workspace 名）。
4. v1 的 agent 视角汇总不保留（第 6 节末尾）。
5. 读取本机配置会改变 `README.md` 的安全边界（现在写的是「后端不读 CLI 配置」）。用户 10-23 已定采集范围；第 3 节的读取规则和指纹密钥文件是本设计补的细节。

## 13. 对照图

全部由 `shoot.py` 生成。左边是 `web/?mock=1#/skills`（或 v1 的 `?mock=mcp#/mcp`），右边是 `mock.html`：

| 文件 | 视口 | 对照内容 |
|---|---|---|
| `compare-1440.png` | 1440 × 900 浅色 | 顶部卡片、工具栏、异常提示条、图例、表格密度、待处理栏、红色角标 |
| `compare-1440-drawer.png` | 1440 × 900 | 抽屉头、节标题、字段行 |
| `compare-1100.png` | 1100 × 900 | 待处理栏上移为一行计数（M9） |
| `compare-390.png` | 390 × 844 | 顶栏换行、卡片两列、工具栏换行 |
| `compare-dark-1440.png` | 1440 × 900 深色 | 变量在深色下的表现 |
| `v1-vs-redesign-1440.png` | 1440 × 900 | v1 MCP 与重做设计稿（M1–M13 前后） |
| `copy-fail-1440.png` | 1440 × 900 | 复制前 / 复制失败 + 选中回退 |

设计稿是静态的，只有正常场景。空、部分失败、接口缺失、无效、只有黄色角标五个场景由实现按第 7 节的写法做出，在验收时截图。

## 14. 测试要求：fake 数据、临时目录、泄密哨兵

**原则**：测试、fixtures、截图都不读真实的本机 MCP 配置，也不连真实看板。截图用 `?mock=`；本单出图时发现 8765 端口上跑着用户的真实看板，`shoot.py` 因此固定用 8799 并只起静态服务。

**后端**（新增 `tests/test_mcp_local.py`，扩充 `tests/test_mcp.py`）：

1. 所有读取函数接收 `home` 和 `repo_root` 参数；测试一律用 `tempfile.TemporaryDirectory()` 造假 HOME 和假仓库，并设置 `HOME` 环境变量指向临时目录。加一条断言：测试期间没有打开真实 home 下的任何声明路径（对 `open` / `Path.read_bytes` 打桩记录路径）。
2. 九种全局文件和五种项目文件各写一份 fake 配置。每份在 env 值、env 键名、headers 值、headers 键名、url（主机、路径、查询串）、command、args、cwd、未知额外字段里各放一个唯一哨兵串，形如 `SENTINEL-<文件>-<字段>-7f3a`。再各写一份故意损坏的文件，哨兵放在出错的那一行。
3. 断言哨兵不出现在：`/api/mcp`、`/api/agents`、`/api/skills` 及其余 `/api/*` 的响应；SQLite 文件原始字节；`-v` 级别日志（`caplog` 和 stderr）；所有异常信息和 `meta.sources[].detail`；指纹密钥文件之外的任何临时文件。
4. 指纹：同一份配置在两个工具里 → 同组；只改 env 值 → `env` 不同、`launch` 相同；换密钥文件 → 组划分不变但内部哈希全变；把测试里算出的全部内部指纹（完整值和任意 8 位以上的前缀）拿去搜各接口响应和 SQLite，都搜不到；内部哈希不等于对规范化 JSON 做的裸 SHA-256。
5. 密钥文件：不存在时创建为 `0600`；权限过宽时拒绝并让 `drift` 桶 `connected: false`。
6. 读取状态：不存在、无权限、超大、解析失败、YAML 超出子集，各自得到正确的 `state` 和格子值（不存在为空白，其余为 `?`），错误信息是固定文案。
7. 分桶：一个对象一个桶、顺序、`tags`、`tone`（只有 drift 时为 `warn`）、`tidy` 不计入角标、`connected: false` 的三种原因。
8. 生效判断沿用 v1 的用例（只有库、只有自有、两者同名、自有在旧 `mcp` 容器、自有为 `{}`）。
9. CLI 白名单不变；全程不对真实 workspace 做写操作，只用 `tests/fake_cli.py`。

**前端**（`web/mcp.test.js` 重写）：`filteredItems`、`groupedItems`、`queueModel`、格子符号映射、角标 tone、`copyWithFallback` 的三条路径、route 的 parse / format / 离开清理。

**fixtures**：全部虚构名字。加一条测试扫描 `web/fixtures/mcp*/`：不得出现 `ghp_`、`sk-`、`xox`、`Bearer `、32 位以上的 hex 或 base64 串、`@` 邮箱。

## 15. 验收清单

设计系统 §5 逐条（验收写「是 / 否 + 说明」）：

| # | 本板块的答案 |
|---|---|
| 1 | 问题和数据层见第 1 节 |
| 2 | 顶栏板块，理由见第 1 节 |
| 3 | 固定范围说明 + 健康条 |
| 4 | 地图 L3 工具行 + `toolSignal` 新文案；板块内 `focusMcp`；不出板块 |
| 5 | 版式 A，差异 4 条见第 2 节 |
| 6 | 一行一个 server 名；按工具分组时组标题注明可能重复 |
| 7 | 卡片是分区、全部可点、写 `source=`，与下拉同参 |
| 8 | 参数和搜索字段见第 9 节 |
| 9 | 五个桶后端算、顺序固定、一对象一桶、空桶「无」、未接上「—」+ 原因；角标 = 五桶之和，红黄按 `tone` |
| 10 | 一种抽屉，节顺序见第 6 节，`Esc` 回到原行 |
| 11 | 复用和新增见第 7 节 |
| 12 | 符号沿用 Skills 含义，`○` 不用；图例和列头 `title` 齐全 |
| 13 | 只用变量，无十六进制颜色 |
| 14 | 四种情况见第 7 节表格 |
| 15 | 只读提示用 Skills 原句；命令带 `--workspace-id <uuid>`；只用 file 形式；密钥只写「有没有、几项」 |
| 16 | 场景：`mcp`、`mcp-empty`、`mcp-partial`、`mcp-absent`、`mcp-invalid`、`mcp-warn` |
| 17 | 与 Skills 对照截图：1440 和 1100，逐项对照卡片、工具栏、图例、表格密度、待处理栏、抽屉 |
| 18 | 深色 1440 和 390 宽各一张 |
| 19 | 键盘走查：`6`、`/`、`Tab`、`Enter`、`Esc` 回原行 |

本单额外要求：

- M1–M13 每项按第 10 节的「验收看哪里」逐项核对，附截图。
- 红色角标（`mcp`）和黄色角标（`mcp-warn`）各一张截图，`title` 文案正确。
- 只读提示逐字等于「页面只读，不代为执行。复制命令后在自己的终端运行。」，页面顶部没有只读提示。
- 复制失败：在禁用剪贴板的浏览器上下文里点复制，按钮显示「复制失败」，命令被选中，下方出现「已选中命令，按 ⌘C 复制」。
- 第 14 节的哨兵测试全部通过，并附命令和结果。
- 生成的命令里没有 `--server-config '`、`--mcp-config '{`、`--server-config-stdin`，也没有带取值的本机命令。
- `styles.css` 里没有残留的 `.mcp-*` 选择器；没有改 `skills.js`、`agents-view.js`、`plan.js`、`view-atlas.js` 的复制和只读提示代码。
- MCP 页与 Skills 页的截图对比，「版式、组件、交互与设计系统一致」逐项给结论。
