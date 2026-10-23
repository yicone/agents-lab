# MCP 板块重做设计（ACM-378）

本文替换 `mcp-design.md` 里的版式、采集范围和接口部分；那份文档里的事实依据（各 runtime 是否加载、生效优先级、Multica 命令表、库条目只写不读）继续有效。设计依据是 `design-system.md`（`937e5ad`）、Skills 现有代码、本单描述里用户 10-23、10-24 的决定，以及用户「批准 1-4」（评论 `01a16a1b-6eda-7a9f-b490-ba267f9b46b1`）。

第 2 版回应 ACM-396 非作者审查（评论 `01a16a24-280c-744f-8bce-6c258353645e`）的五个阻断问题和 M4、M10、M11、M13 及横滚意见，逐项对应见第 16 节。

设计稿和对照图在 `docs/mcp-redesign/`：

- `mock.html`：静态设计稿，直接引用 `web/styles.css`，全部是虚构数据，不连后端。`?drawer=1` 打开抽屉，`?copyfail=1` 演示复制失败，`?state=partial` 部分来源失败，`?state=unknown` 全部来源失败且没有条目，`?state=warn` 只剩黄色待处理，`?scroll=right` 把表格横滚到最右。
- `shoot.py`：从 fixtures 和设计稿重新生成对照图，并打印各视口的表格宽度。只起本地静态服务（端口 8799），不碰真实看板。
- `compare-*.png`、`v1-vs-redesign-1440.png`、`copy-fail-1440.png`、`state-*.png`、`scroll-right-1440.png`、`mock-390-table.png`：见第 13 节。

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

**健康条形态**：「固定范围说明 + 数据源健康条」，和 Skills 相同（已批选择 3）。重做后一行是跨 workspace 的同名 server，和 Skills 一样不按 workspace 切分；§1.3 规定这种情况用固定范围说明。这一改动同时消除 M7。

- 范围说明：「范围：本机各工具全局 MCP 配置、agents-lab 项目级配置，以及全部 Multica Workspace 的库与 agent。workspace 筛选不作用于这一页。」
- 健康条 `meta.sources`：本机配置、仓库项目级、配置指纹、Multica 库、Agent 分配、Agent 自有配置、Runtime 加载表，共 7 个 `.src-chip`。每个标签的 `state` 和 `detail` 按第 5.1 节的来源健康状态给出。
- 顶栏状态：「MCP 快照 X 前 · 本机扫描 Y 前」；读取失败时沿用 `mcpSnapshot()` 的 warn 写法。

**进出跳转**：

- 进：顶栏 `6`；地图 L3 工具行（`MAP_TOOLS` 已有）。`toolSignal("mcp")` 改为「N 个 server · 坏了 X · 待处理 Y」；没有待处理且所有来源都读到时「N 个 server · 无待处理」；有来源没读到时末尾加「· M 个来源没读到」，此时不写「无待处理」；未接上仍是「还没接上」。告警圆点在角标显示数字或 `?` 时出现。
- 板块内：待处理条目调用 `focusMcp(id)`（`id` 见 3.6），做法同 `focusSkill`：清筛选、展开所在分组、滚到行、打开抽屉。
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

**一行是什么**：一个 server 原名（原始身份，见 3.6）。本机文件、项目文件、各 workspace 的库条目和 agent 自有配置里原名完全相同的条目合在一行，和 Skills「一个 skill 名一行，各 workspace 的安装放在 Multica Workspace 列」的做法相同。原名按码位逐字比较：不压空白、不去首尾空白、不做大小写或 Unicode 归一。按工具分组时同一 server 会出现在多组，组标题写「同一 server 可能重复出现」。

**来源卡片**是行的分区，全部可点，点卡片等于设置「来源」下拉，两者写同一个 `source=`：

| 卡片 id | 标签 | 条件 |
|---|---|---|
| `all` | 全部 | 全部行 |
| `both` | 本机 + Multica | 本机或项目文件里有，Multica 也有 |
| `local_only` | 仅本机 | 只在本机或项目文件里 |
| `multica_only` | 仅 Multica | 只在库或 agent 自有配置里 |
| `project`（前端合成的伪来源） | 仓库项目级 · agents-lab | 项目文件里有同名条目，和上面三块重叠，做法同 Skills 的 `CLOUD_SOURCE` |

卡片内容：标签、数字、按一致性的拆分。损坏用 `.bad-n`，不一致用新增的 `.warn-n`，其余 `.muted`。「仅本机」「仅 Multica」依赖「另一侧确定没有」：另一侧有来源没读到时，卡片拆分行最前面加 `.warn-n`「含 N 行待确认」，`title`「有来源没读到，这些行可能在另一侧也有」（规则见 5.1）。本机没扫描或 Multica 从未采集时数字写「—」并说明原因。

**工具栏**：来源、状态（损坏 / 不一致 / 一致 / 仅一处 / 不可比）、工具（各工具 + 仓库入口）、分组（按来源 / 按工具 / 不分组）、复选框「只看已分配给 agent」。没有视图切换，没有 workspace 下拉。

**列的顺序**（身份 → 来源 → 跨层 → 本机工具 → 仓库入口 → 状态）：

| 列 | 内容 | 对应 Skills |
|---|---|---|
| Server | 等宽显示名 + 所在桶的标签（如 `配置不一致`、`条目损坏`，和 Skills 把漂移标签放在名字旁一样）；显示名相同但原名不同时，名字后加灰字「（同显示名 1/2）」，见 3.6 | Skill |
| 来源 / 出现位置 | 第一行来源标签，第二行灰色等宽「本机 4 · 仓库 1 · 库 1 · 自有 1」，`.channel-col` 164px | 来源 / 更新通道 |
| Multica Workspace | `.cloud-cell`：最多 3 个 workspace，每个附已启用 agent 简写（`agentBrief`），自有配置写「（自有）」；没采集写「—」；某个 workspace 没读到写「? <workspace 名> 没读到」；分配没读到写「分配没读到」；全部确定读到了但没有才写「Multica 未配置」 | Multica Workspace |
| 每个工具一列 | 见第 7 节符号表和 5.1 的格子规则。列头写短名（Cursor、Codex、Claude、Hermes、Gemini、Devin、Antigravity、Windsurf、OpenCode），`<small>` 写 `●` 的个数，文件没读到时写 `?`；`title` 写全名、文件路径和读取状态 | 每个工具一列 |
| 仓库入口 | `.repo-entries` + `.entry-chip`：`.mcp.json ●`、`.cursor ●`、`.codex ●`、`.gemini ●`、`opencode.json ●` | 仓库入口 |
| 传输 | 只有四个值：`stdio` / `http` / `sse` / `unknown`；各处不同时写「stdio / http」并标黄，`.version-col` 112px | 版本 |
| 一致性 | `tag`：一致（ok）、不一致 · N 组（warn）、损坏（bad）、仅一处（muted）、不可比（muted）；结论不全时标签后加 `?` | 对账 |

**和 Skills 不同的地方**（逐条理由）：

1. 没有「共享入口」列：MCP 没有跨工具共享的配置目录（Skills 的 `~/.agents/skills` 没有对应物）。`○` 因此不用，保留它在 Skills 的含义。
2. 没有「定制 · 约束」「复核」列：MCP 没有清单基线和人工复核记录。最右一列是状态（一致性），符合「状态在最右」。
3. 待处理栏底部的不计数按钮是「待清理 · 无人使用 / 只有停用」，对应 Skills 的「基线债」；没有复核阈值设置。
4. 列头用短名，全名在 `title` 里。短名只是让列宽更紧，**不保证一致性列初始可见**：按设计稿实测（第 2 版），表格内容约 1434px，1440、1100、390 视口下容器分别约 1042、1066、372px，都要横滚（`shoot.py` 每次出图打印实测值）。一致性列通过 `.table-wrap` 自身的横向滚动到达，页面本身不出现横向滚动条；行的状态已经在 Server 列的桶标签里写出，不滚动也能看到这一行要不要处理。不加左侧冻结列，和 Skills 保持一致。横滚可达性按第 15 节的验收项实测。

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

**声明校验**：`mcp-sources.json` 里每个路径必须是相对路径（全局文件相对 `home`，项目文件相对 `repo_root`），不能以 `/` 开头，不能含 `..`、`.` 段、空段或 NUL。校验失败整份清单无效，`/api/mcp` 返回 `ok: false`。

**受限 YAML 读取器**：后端只用标准库，不新增依赖。Hermes 用一个只认 `mcp_servers:` 块的受限读取器：块状映射、普通和引号标量、块状和行内列表。遇到锚点、别名、标签、多行字符串、合并键、多文档分隔符等就整份文件记为 `unsupported`，Hermes 列显示 `?`，不猜。读取器只把 `mcp_servers:` 块交给后续步骤，其他顶层键不解析值。

### 3.2 安全打开与拒绝规则

每次读取都在一个已打开的文件描述符上做判断，不先 `lstat` 再按路径 `open`，所以不存在「检查后被替换」的窗口。

1. **起点**：`home` 和 `repo_root` 由调用方传入（测试传临时目录），用 `os.open(base, O_RDONLY | O_DIRECTORY | O_CLOEXEC)` 打开一次。这两个起点本身视为可信，允许是符号链接。
2. **逐级打开目录**：从起点开始，每个中间目录用 `os.open(name, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC, dir_fd=上一级)` 打开。中间目录是符号链接时打开失败，不跟随。
3. **打开文件**：最后一级用 `os.open(name, O_RDONLY | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC, dir_fd=所在目录)`。`O_NONBLOCK` 保证遇到 FIFO 时立即返回，不会阻塞等待写端。
4. **只信 `fstat`**：打开后对描述符做 `os.fstat`，不是 `S_ISREG` 就立即关闭，不读一个字节。`st_size` 超过 16 MB 记 `too_large`；读取时最多读 16 MB + 1 字节，读到超出也记 `too_large`（文件在读的过程中变大）。
5. **不写、不改**：对任何声明路径都不调用 `chmod`、`chown`、`utime`、`rename`、`unlink`、写入或创建。配置文件的所有者和权限不检查也不修改：读得到就读，读不到记 `denied`。
6. **平台能力**：启动时检查 `os.open in os.supports_dir_fd`。不支持时本机和项目级读取全部记 `platform_unsupported`，不退回按路径打开。
7. **缓存**：读完后用 `fstat` 的 `(st_dev, st_ino, st_mtime_ns, st_size)` 做 15 秒缓存键，和 Skills 的 `CACHE_TTL_SECONDS` 一样。缓存命中也要先完成第 2–4 步拿到 `fstat` 再比较，不按路径 `stat`。

**读取状态**（`files[].state`，只有这些值）：

| state | 触发 | 格子 |
|---|---|---|
| `ok` | 读取并解析成功 | 按条目 |
| `absent` | 文件或某级目录 `ENOENT` | 确定没有：空白，列头 0 |
| `denied` | `EACCES` / `EPERM` | `?` |
| `symlink` | 文件或中间目录是符号链接（`ELOOP`，部分平台为 `EMLINK`） | `?` |
| `not_regular` | 不是普通文件（目录、FIFO、socket、设备），或中间一级不是目录（`ENOTDIR`） | `?` |
| `too_large` | 超过 16 MB | `?` |
| `parse_error` | JSON / JSONC / TOML 解析失败，或容器不是对象 | `?` |
| `unsupported` | YAML 超出受限读取器能力 | `?` |
| `io_error` | 其他 `OSError` | `?` |
| `platform_unsupported` | 第 6 条 | `?` |

健康标签和格子 `title` 用固定中文文案，例如 `symlink` 写「是符号链接，未跟随读取」，`not_regular` 写「不是普通文件，未读取」。错误只记上面的固定代码，不用异常原文：`json`、`tomllib` 和 YAML 的错误信息可能带出原文片段，`OSError` 的 `filename` 可能带出链接目标，一律不进日志、接口和 SQLite。

解析只在内存里进行，取出容器后立刻降成摘要（3.3），原始对象丢弃。本机部分不写 SQLite，按请求计算。

### 3.3 条目摘要、传输枚举和损坏原因

摘要只有：显示名和 `id`（3.6）、`state`（`ok` / `broken`）、`broken_reason`、`disabled`、`transport`、`env {set, count}`、`headers {set, count}`、指纹组字母。

**传输枚举**：本机、项目、agent 自有配置和 Multica 库条目统一用一个函数 `transport_enum`，输出只有 `stdio`、`http`、`sse`、`unknown` 四个值，任何情况下都不输出原字段内容。现有 `mcp_summary.py` 的 `_norm_explicit` 会原样返回无法识别的 `type`（`transport_of({"type": "SENTINEL-…"})` 原样回显），重做时删除这条分支；`norm_library_transport` 也改走 `transport_enum`。

| 输入 | 结果 |
|---|---|
| 显式 `type` 或 `transport` 是字符串，去首尾空白、转小写后 ≤ 32 字符，且在别名表里：`stdio` `local` → `stdio`；`http` `streamable-http` `streamable_http` `remote` → `http`；`sse` → `sse` | 对应枚举 |
| 显式值是字符串但不在别名表 | `unknown`，条目 `broken`，`broken_reason: "unknown_transport"` |
| 显式值不是字符串（数字、布尔、列表、对象、null 以外的任何类型） | `unknown`，`broken`，`bad_field_type` |
| 没有显式值：`command` 是非空字符串，或 OpenCode 的非空字符串数组 | `stdio` |
| 没有显式值：`url` / `serverUrl` / `httpUrl` 是非空字符串 | `http`；Gemini 的 `url` 为 `sse`，`httpUrl` 为 `http` |
| 既没有显式值，也没有 command 和 url | `unknown`，`broken`，`no_target` |
| 显式为 `stdio` 但没有 command，或显式为 `http` / `sse` 但没有 url | 对应枚举，`broken`，`missing_target` |
| 已知字段类型不对：`command` 不是字符串或字符串数组，`args` 不是字符串数组，`url` 类字段不是字符串，`env` / `headers`（及 Codex `env_vars`、`http_headers`、`env_http_headers`，OpenCode `environment`）不是对象或约定的列表，`disabled` / `enabled` 不是布尔 | `broken`，`bad_field_type`；该项 `env` / `headers` 写 `{set: null, count: null}` |
| 条目本身不是对象 | `unknown`，`broken`，`not_object` |

`broken_reason` 只有 `unknown_transport`、`bad_field_type`、`no_target`、`missing_target`、`not_object` 五个值，前端按固定文案显示（如「类型无法识别」「字段类型不对」「缺 command 和 url」），不显示字段名以外的任何内容。`broken` 条目不算指纹，`group` 为 `null`。

已有 SQLite 里 v1 写入的 `mcp_own_json` 和 `mcp_servers.transport` 可能含原样回显的值。schema 2 读取时，枚举以外的任何字符串一律当作 `unknown` 输出；collector 之后写入的只会是枚举值。

### 3.4 指纹和密钥

指纹分四份，都用 HMAC-SHA256，输入前缀带算法版本 `mcp-fp-v1\0<分项>\0`：

| 指纹 | 输入（规范化后的 JSON，键排序） |
|---|---|
| `launch` | 传输枚举 + `command` + `args` + `cwd`，或者 `url` |
| `env` | env 的键值对；Codex 的 `env_vars`（只有名字）按键、值为 null 计入；OpenCode 的 `environment` |
| `headers` | headers 的键值对；Codex 的 `http_headers`、`env_http_headers`、`bearer_token_env_var` |
| `all` | 上面三份合在一起 |

不参与比对：启用开关、超时、工具白名单、trust 等。抽屉里写明这一点。

**密钥文件位置**：`<数据目录>/mcp-fingerprint.key`，数据目录是 `--db` 所在目录（默认 `~/.multica-board/`），和 `board-owner-agents.json` 放在一起。第 1 版写的 `~/.local/share/agents-lab/` 改掉：看板自己的数据目录已经是 `0700`，不必在用户 home 里再新建目录。密钥只是看板自己的缓存文件，不改任何 harness、agent、runtime 或全局配置。

**打开已有密钥**（按 3.2 的方式逐级打开，再加以下检查，任何一条不满足都拒绝使用）：

| 检查 | 不满足时的代码 |
|---|---|
| 数据目录是真实目录（不是符号链接），所有者是当前有效用户，`mode & 0o022 == 0` | `key_dir_unsafe` |
| 密钥路径不是符号链接 | `key_symlink` |
| `S_ISREG` | `key_not_regular` |
| `st_uid == os.geteuid()` | `key_owner` |
| `st_mode & 0o077 == 0` | `key_mode` |
| `st_nlink == 1` | `key_links` |
| `st_size == 32`，并且恰好读到 32 字节 | `key_length` |

拒绝时不 `chmod`、不覆盖、不删除、不重新生成，也不跟随链接读取目标。「配置指纹」健康标签变红，`detail` 写固定文案（如「密钥文件权限过宽，未比对」），一致性全部显示「不可比」，`drift` 桶 `connected: false`，正文「— 指纹密钥不可用，未比对」。用户修好之后下一次请求自动恢复。

**新建密钥**：只在打开结果是 `ENOENT` 时新建。用 `secrets.token_bytes(32)` 生成，写入同目录的临时文件 `mcp-fingerprint.key.tmp-<随机>`（`O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW`，模式 `0o600`），`fsync` 后用 `os.link(临时, 正式, src_dir_fd, dst_dir_fd)` 放到正式位置，再删掉临时文件。`os.link` 在目标已存在时失败，所以不会覆盖任何已有文件；遇到 `EEXIST`（并发创建）就丢弃自己的临时文件，回到「打开已有密钥」再做一次。新建后按上表复核一次。

**指纹不出后端**：比对在后端完成，接口只输出组字母（A、B、C…）和三项分项结论（一致 / 不同），不输出任何哈希值。组字母按成员数从多到少、再按工具顺序分配，每次请求重新算，不落盘。用加盐哈希而不是裸 SHA-256，是为了让知道候选值的人无法用指纹验证猜测。

### 3.5 agent 自有配置的指纹：只在内存里

**问题**：本机文件每次请求现读，但 agent 自有配置只有 collector 采集时能看到原文（`collector.py` 的 `norm_agent`），之后原文就丢弃，SQLite 里只有 `mcp_own_json` 摘要。指纹又不能进 SQLite。

**做法**：

- `mcp_own_json` 只保存没有秘密的摘要：显示名、`exact`（3.6）、传输枚举、`broken_reason`、env / headers 的项数。不保存任何指纹、HMAC、哈希、组字母、行 `id`，也不保存原名。实现时加一条断言：写入前检查摘要只含白名单键。
- serve 进程里新增一个进程内对象 `OwnFingerprints`。collector 线程在 `norm_agent` 里拿到原始 `mcp_config` 时，用当前密钥算出每个 server 的四份指纹和行 `id`，写进这个对象，然后丢弃原文。结构：

```
OwnFingerprints（只在 serve 进程内存里）
  key_gen: int                                  # 密钥代号，进程内计数
  agents[(workspace_id, agent_id)] = {
    agent_updated_at, collected_at,
    servers: { <行 id>: { launch, env, headers, all } }
  }
  library[(workspace_id, server_id)] = { id: <行 id>, collected_at }   # 库条目只有身份，没有指纹
```

- **密钥版本**：每次请求按 3.4 打开密钥，比较 `fstat` 的 `(st_dev, st_ino, st_mtime_ns, st_size)`；和上次不同就 `key_gen += 1`。只有 `key_gen` 与当前一致的记录才参与比对；本机文件的指纹每次请求用当前密钥现算。算法版本固定写在 HMAC 输入里，升级算法时一起改 `mcp-fp-v1`。
- **记录何时有效**：同时满足才有效：`key_gen` 一致；记录的 `agent_updated_at` 等于 SQLite 当前那一行的 `updated_at`；记录里的行 `id` 集合和 SQLite 摘要里的 server 数一致。agent 从 SQLite 消失或被归档时，下一轮 collector 删掉它的记录。
- **不可比的情况**：serve 刚启动、collector 线程还没跑完第一轮、用 `serve` 但关闭了采集、只跑过独立的 `python -m multica_board collect`（另一个进程，内存记录随它退出）、密钥换过、agent 更新后还没重新采集。这些情况下该 agent 的自有条目 `fp_state: "pending"`，`group: null`，不计入组，不当作一致，也不当作不一致；所在行 `consistency.partial: true`，`title`「agent 自有配置本次启动后尚未采集，未参与比对」。健康标签「配置指纹」写「自有配置待采集 N 个」（黄）。
- **首轮补采**：serve 进程启动后的第一轮 collector 必须读一次 agent 列表和 MCP 分配，不受 `--mcp-every` 和 `mcp_state.fingerprint` 未变的跳过逻辑影响（判断条件加上「`OwnFingerprints` 里还没有这个 workspace」）。
- **日志**：只记数量（如「own fingerprints: 12 agents」），不记指纹、行 `id`、盐或密钥。

### 3.6 原始身份与显示名

现有 `plain_text` 会压空白、截到 80 字符、`redact()`，`plain_text("a  b") == plain_text("a b")`。它只能用于显示，不能用于合并、比较或生成 `id`。

- **原始身份**：配置里的 server 名字符串原样（按码位）。空字符串和全空白也是合法的、彼此不同的身份。合并成一行、判断同名覆盖、算指纹分组都只看原始身份。原始身份只在内存里出现，不进接口、SQLite 和日志。
- **行 `id`**：`"m" + base32(HMAC-SHA256(进程盐, "mcp-name-v1\0" + 原名 UTF-8 字节))` 取前 20 个字符（100 位）。进程盐是 serve 启动时生成的 32 字节随机数，只在内存里，不写盘，不进日志，和指纹密钥无关，所以密钥不可用时页面导航照常。`id` 不能反推原名，也不能用来验证猜测；在一个 serve 进程内稳定，重启后会变。同一次响应里两个不同原名得到相同 `id`（概率可忽略）时，后端按原名排序给后一个加 `-2`，并记一条只含数量的告警。
- **显示名** `display_name`：`plain_text(原名)`；结果为空时写「（空名字）」。不提供完整原名，也不在 `title` 里放截断前的原文。
- **显示名相同**：同一次响应里多行的 `display_name` 相同时，这些行都带 `display_dup: {index, total}`，按 `id` 排序编号；前端在名字后加灰字「（同显示名 1/2）」，`title`「显示名相同，原名不同（空白、长度或脱敏后相同）」。
- **SQLite 里的名字**：库条目（`mcp_servers`）和自有配置摘要（`mcp_own_json`）保存显示名和 `exact`（显示名是否等于原名），不保存原名或任何名字哈希。collector 采集时把库条目和自有条目的行 `id` 写进 `OwnFingerprints`。没有内存记录时（重启后、首轮之前）：`exact: true` 的条目用显示名算 `id`（此时显示名就是原名，结果相同）；`exact: false` 的条目不和任何行合并，单独成一行，`tags` 带 `name_unverified`，抽屉写「名字含被清洗的字符，本次启动后尚未重新采集，暂不合并」，一致性「不可比」，同名覆盖不判断（见 5.1）。
- **抽屉定位**：行 DOM id `mcp-row-<id>`，`state.drawer.id`、`focusMcp(id)`、待处理条目都用 `id`，不用名字。定时刷新后 `id` 仍在就保持抽屉；`id` 消失（重启后）就关闭抽屉，焦点回到表格顶部，不报错。
- **搜索**只匹配显示名和第 9 节列出的其他字段，不匹配 `id`。

### 3.7 永远不输出

env 和 headers 的键名与值、`url`、`command`、`args`、`cwd`、条目里的其他字段、显式 `type` / `transport` 的原值、类型不对的字段内容、与显示名不同的原名、指纹值、指纹密钥、进程盐、文件原文片段、解析错误原文、`OSError` 的文件名和链接目标。适用于 `/api/*` 响应、SQLite、日志（含 `-v`）、异常信息和 fixtures。行 `id` 只出现在 `/api/mcp` 响应和前端 DOM，不进 SQLite 和日志。

文件路径可以显示（`~` 开头，用 `redact` 的 home 路径规则），它是证据，不是取值；只显示声明路径，不显示链接目标。

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
  "unknown": {
    "local_files": [], "project_files": [], "library": [], "bindings": [],
    "own_hidden": 0, "own_invalid": 0, "own_pending": 0, "key": "ok", "multica": "collected"
  },
  "items": [
    {
      "id": "m7k2q9x4w1c8v5b3n6z0",
      "display_name": "github",
      "display_dup": null,
      "source_class": "both",
      "source_uncertain": false,
      "source_label": "本机 + Multica",
      "in_project": false,
      "presence": { "local": 4, "project": 0, "library": 1, "own": 0 },
      "transports": ["stdio"],
      "tools": { "cursor": "direct", "codex": "direct", "claude": "direct", "hermes": "direct", "gemini": null },
      "repo_cells": { ".mcp.json": null, ".cursor": null },
      "entries": [
        { "scope": "global", "tool": "cursor", "path": "~/.cursor/mcp.json", "state": "ok", "broken_reason": null,
          "disabled": false, "transport": "stdio", "group": "A",
          "env": { "set": true, "count": 2 }, "headers": { "set": false, "count": 0 } }
      ],
      "workspace_entries": [
        { "kind": "library", "workspace_id": "<uuid>", "workspace_name": "ACM Lab", "server_id": "<uuid>",
          "transport": "stdio", "agent_count": 2, "updated_at": "2026-10-20T08:00:00Z", "stale": false,
          "bindings_known": true,
          "agents": [ { "id": "<uuid>", "name": "Builder · Codex", "enabled": true, "shadowed": false,
                        "provider": "codex", "runtime_loads": "yes" } ] }
      ],
      "consistency": { "state": "differs", "partial": false, "pending": 0,
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
    { "id": "all", "label": "全部", "count": 13, "uncertain": 0,
      "statuses": { "broken": 1, "differs": 2, "same": 4, "single": 3, "incomparable": 3 } }
  ],
  "attention": {
    "badge": 6,
    "tone": "bad",
    "partial": false,
    "counts": { "broken": 1, "ignored": 1, "shadowed": 1, "may_fail": 1, "drift": 2 },
    "buckets": {
      "drift": { "connected": true, "partial": false, "unknown_sources": [], "reason": "",
        "items": [ { "id": "m7k2q9x4w1c8v5b3n6z0", "title": "Cursor、Claude Code 与 Codex、Hermes Agent 的 env 不同",
                     "status": "drift", "time": "2026-10-24T02:01:00Z", "source_class": "both", "tags": [] } ] }
    },
    "tidy": { "count": 2, "partial": false, "unknown_sources": [] }
  },
  "meta": {
    "sources": [
      { "id": "local_files", "label": "本机配置", "state": "ok", "detail": "8 已读 · 1 不存在" },
      { "id": "multica_own_config", "label": "Agent 自有配置", "state": "ok", "detail": "全部可见" }
    ]
  }
}
```

字段说明：

- `items[].id`：行 `id`（3.6）。`display_name` 是清洗后的显示名，`display_dup` 为 `{index, total}` 或 `null`。接口里没有 `name` 和 `key` 字段，前端也不得用显示名做键。
- `tools.<id>` 和 `repo_cells.<location>` 格子值：`direct`（有条目）、`off`（有条目，配置里显式停用）、`missing`（条目损坏）、`unknown`（文件没读到）、`not_applicable`（这一行确定只在 Multica）、`null`（已读文件，没有同名条目）。取值按 5.1 的顺序判定。
- `source_uncertain`：`true` 表示「仅本机」或「仅 Multica」的判断依赖了没读到的来源。
- `entries[].group`：所属指纹组字母；`broken`、`pending` 或不可比时为 `null`。项目文件的条目 `scope: "project"`，`tool` 为 `null`，`location` 写 `.cursor` 等。
- `workspace_entries[].kind`：`library` 或 `own`（agent 自有）。`own` 带 `agent_id`、`agent_name`、`provider`、`runtime_loads`、`group`、`fp_state`（`ready` / `pending`）、`env`、`headers`。`bindings_known: false` 时 `agents` 为 `null`，前端写「分配没读到」，不写成没有分配。
- `workspace_entries[].stale`：这个 workspace 本轮库读取失败、显示的是上次数据。
- `consistency.pending`：没有参与比对的自有条目数（3.5）。
- `unknown`：没读到的来源汇总，只含工具 id、项目文件 location、workspace id 和计数，前端用它写异常提示条、全空引导和卡片的「待确认」。`multica` 为 `collected` / `never` / `stale`。
- `summary`：后端给 `all`、`both`、`local_only`、`multica_only` 四块，每块带 `uncertain`（`source_uncertain` 的行数；`all` 和 `both` 恒为 0，前端只在「仅本机」「仅 Multica」卡上显示）；`project` 块由前端按 `in_project` 合成。
- `attention.buckets`：五个桶都出现，空桶 `items: []`；`connected: false` 时写原因；`partial: true` 时 `unknown_sources` 列出没读到的来源标签（固定文案）。`attention.tone` 为 `bad` / `warn` / `unknown` / `none`。
- `ok: false` + `error`：整份数据无效（如 `mcp-sources.json` 读不出来或声明校验失败），前端显示 `.atlas-guide`。
- `/api/agents` 仍然不输出 `mcp_config`。

## 5. 待处理栏和角标（第 9 条）

分桶在后端完成，顺序固定，一个对象只进一个桶，取第一个命中的，其余命中变成行上的 `tags`：

| 顺序 | 代码 | 标题 | 条件 | 类别 |
|---|---|---|---|---|
| 1 | `broken` | 条目损坏 | 任一本机、项目或自有条目 `broken` | 坏了（红） |
| 2 | `ignored` | 会被忽略 | 未归档 agent 已启用这个 server（库分配或自有配置），但 runtime 不加载 MCP 或没绑 runtime | 坏了（红） |
| 3 | `shadowed` | 同名覆盖 | 已启用的库分配被同一 agent 自有配置里原名相同的 server 覆盖 | 坏了（红） |
| 4 | `may_fail` | 可能失败 | 已启用给 provider 为 `opencode` 的 agent | 坏了（红） |
| 5 | `drift` | 配置不一致 | `consistency.state` 为 `differs` | 等用户处理（黄） |

- 不计入角标：`tidy`（库条目无人使用，或分配了但全部停用），作为底部虚线按钮「待清理 · 无人使用 / 只有停用」，点击切换 `tidy=1` 筛选，做法同基线债（已批选择 2）。
- v1 的 `hidden`（agent 自有配置不可见）不再是桶。它是采集本身的限制，放到异常提示条和「Agent 自有配置」健康标签（黄）（已批选择 2）。
- 空桶写「无」，只在该桶 `connected: true` 且 `partial: false` 时写。`connected: false` 时计数写「—」，正文「— 原因」；`partial: true` 时照常列出已发现的条目，计数后加 `?`，桶底加一行灰字「另有来源没读到：<来源>，可能还有」。
- 桶标题只是标题，不做筛选按钮。条目点击调用 `focusMcp(id)`。计数在红类桶用 `.bad-n`，黄类桶用 `.warn-n`，0 用 `.muted`。
- 小于 1200px 时渲染 `.skills-compact`：「条目损坏 1 · 会被忽略 1 · 同名覆盖 1 · 可能失败 1 · 配置不一致 2 · 待清理 2」，`partial` 的桶数字后同样加 `?`。
- `reason` 不超过 80 字，例如「调研 · Antigravity 的 runtime 不加载 MCP（ACM Lab）」；涉及多个 agent 时写第一个加「等 N 个」。

**角标**（执行用户 10-24 的决定和已批选择 1）：数字 = 五个桶里已发现条目的条数之和。颜色由后端给的 `attention.tone` 决定：

| tone | 条件 | 显示 |
|---|---|---|
| `bad` | 红类桶有任何一条 | 红色数字，`.tab-badge.tone-bad` |
| `warn` | 红类桶都为 0，`drift` 有条目 | 黄色数字，`.tab-badge` 默认样式 |
| `unknown` | 五个桶都没有已发现条目，但至少一个桶 `partial` 或 `connected: false` | 灰色 `?`，新增 `.tab-badge.tone-unknown`；不是红黄，不改变已批的红黄划分 |
| `none` | 五个桶都为 0，且都 `connected`、不 `partial` | 隐藏 |

颜色不是唯一线索：`title` 和 `aria-label` 写「MCP 待处理 6 条：坏了 4 · 待你处理 2」；`attention.partial` 为真时末尾加「（有 N 个来源没读到，可能不全）」；`unknown` 时写「MCP 有 N 个来源没读到，是否有待处理未确定」。

判定理由：前四个桶的配置不会按预期生效或会让任务失败，属于「坏了」；配置不一致需要用户决定以哪一份为准，属于「等用户处理」。这一划分用户已批准。

### 5.1 未知优先：来源健康状态到格子、卡片、分桶和全空引导

总原则：**看到的是证据，没读到的不是「没有」**。存在可以由任何一个读到的来源证明；不存在必须由所有相关来源都读到才能证明。

**来源是否已知**：

| 来源 | 已知 | 未知 |
|---|---|---|
| 本机 / 项目文件 | `ok`、`absent` | 3.2 表里其余状态 |
| workspace 库 | 本轮 `library_state: ok` | 读取失败（含 `stale` 旧数据）、从未采集 |
| workspace 分配 | 本轮 `bindings_state` 为 `ok` 或 `none` | 其他 |
| agent 自有配置 | 摘要 `state` 为 `managed` 或 `none` | `hidden`、`invalid` |
| 自有配置指纹 | `fp_state: ready` | `pending` |
| 指纹密钥 | 可用 | 3.4 的拒绝代码 |

`stale` 的库数据可以证明「有」（行照常显示，格内加「旧数据」标签），不能证明「没有」。

**格子**（每个工具列和每个仓库入口格，按顺序取第一个成立的）：

1. 这个文件里有这一行的条目 → `direct` / `off` / `missing`。
2. 这个文件没读到 → `unknown`（`?`）。无论这一行是不是只在 Multica。
3. 所有本机和项目文件都已知，且都没有这一行，且 Multica 里有 → `not_applicable`（`∅`）。
4. 其余 → `null`（空白：这个文件已读，没有同名条目）。

所以只要有任何一个本机或项目文件没读到，表里就不出现 `∅`：那一列是 `?`，其余列是空白。

**行的来源类别**：本机有、Multica 也有 → `both`（看到即证明，确定）。只在本机看到 → `local_only`；所有 workspace 库、分配和自有配置都已知时 `source_uncertain: false`，否则为 `true`。只在 Multica 看到 → `multica_only`；所有本机和项目文件都已知时为 `false`，否则为 `true`。来源卡按 `summary[].uncertain` 显示「含 N 行待确认」。

**Multica Workspace 列**：只有所有 workspace 的库已知、分配已知、自有配置已知时，没有条目才写「Multica 未配置」；有 workspace 未知时，写已知部分，再写「? <workspace 名> 没读到」；Multica 从未采集写「—」。

**一致性**：参与比对的只有 `ok` 条目和 `ready` 的自有条目。有任何相关文件未知或自有条目 `pending` 时 `partial: true`，标签后加 `?`。`partial` 不改变 `state` 的取值，只表示结论仅覆盖已读部分：`partial` 的「一致」「仅一处」不能当作确定结论，标签 `title` 写「部分来源未读到或待采集，结论只覆盖已读的」。

**分桶**：

| 桶 | 依赖的来源 | `connected: false` | `partial: true` |
|---|---|---|---|
| `broken` | 本机 / 项目文件、自有配置 | 本机未扫描且 Multica 从未采集 | 任一文件未知，或任一自有配置 `hidden` / `invalid` |
| `ignored` | 分配、自有配置、加载表 | Multica 从未采集 | 任一 workspace 分配未知，或任一自有配置未知 |
| `shadowed` | 分配、自有配置、原始身份 | Multica 从未采集 | 同上，或有 `name_unverified` 的条目 |
| `may_fail` | 分配、自有配置 | Multica 从未采集 | 任一 workspace 分配未知，或任一自有配置未知 |
| `drift` | 指纹 | 指纹密钥不可用，或本机未扫描且没有 `ready` 的自有指纹 | 任一文件未知，或任一自有条目 `pending` |
| `tidy` | 库、分配 | Multica 从未采集 | 任一 workspace 库或分配未知 |

**分配没读到不得判无人使用**：一个库条目只有在它所在 workspace 本轮分配已知时，才可能进 `tidy`。分配未知的 workspace 里的库条目一律不进 `tidy`，格内写「分配没读到」，`tidy.unknown_sources` 列出该 workspace；同理不进 `ignored`、`may_fail`。

**健康标签**：本机配置和仓库项目级按文件统计，`detail` 如「7 已读 · 1 不存在 · 1 读不出」；有读不出时 `warn`，全部读不出时 `bad`。Multica 库、Agent 分配按 workspace 统计，同样规则。Agent 自有配置有不可见或无效时 `warn`。配置指纹：密钥不可用 `bad`，有 `pending` 时 `warn`。

**异常提示条**：每个未知来源一行，固定文案，如「本机 Hermes：~/.hermes/config.yaml 解析失败，Hermes 列显示 ?，这一列的条目没有参与判断」「英语学习：agent 分配没读到，这个 workspace 的库条目不判断是否无人使用」。

**全空**（`items` 为空）：

| 条件 | 主区 `.atlas-guide` |
|---|---|
| 所有来源都已知 | `h2`「还没有 MCP server」，正文「本机各工具的全局配置和 agents-lab 项目级文件里都没有 MCP 条目，Multica 库也为空。库条目建好后不会自动给任何 agent，需要逐个分配。」，下面是只读提示和各 workspace 的「新增库条目」命令（放进 `<details>`） |
| 部分来源未知 | `h2`「没有读到 MCP server」，正文「已读的来源里没有 MCP 条目。以下来源没读到，不能确定没有：」+ 来源列表和各自的修复提示；不显示「新增库条目」命令 |
| 所有来源都未知 | `h2`「所有来源都没读到」，正文「本机配置、项目级文件和 Multica 都没读到，无法判断有没有 MCP server。」+ 各来源的修复提示 |

后两种情况来源卡数字写「—」，角标 `tone: unknown`。

## 6. 详情抽屉（第 10 条）

一种对象一种抽屉，`state.drawer.kind = "mcp"`，`state.drawer.id` 为行 `id`。非模态，`Esc` 或 × 关闭，焦点回到原来的行（`mcp-row-<id>`）。

抽屉头：等宽显示名 + 传输方式标签 + 关闭；`h2.drawer-title` 写来源标签。各节按「是什么 → 什么状态 → 证据 → 怎么处理」：

1. **来源与范围**：名称（显示名；`display_dup` 时加「显示名相同，原名不同，这是第 1/2 个」；`name_unverified` 时写 3.6 的说明）、来源（`source_uncertain` 时加「有来源没读到，可能不止这里」）、传输、本机工具列表、仓库项目级、Multica（库条目数 · 自有数）。
2. **一致性**：状态标签；每个指纹组一行（成员）；启动方式、env、headers 三项各写一致或不同，env 和 headers 附各组项数；`pending` 的自有条目单列「未参与比对：<agent>（本次启动后尚未采集）」；有 `?` 来源时写「<工具> 没读到，结论只覆盖已读的」；固定说明「只比较配置指纹，不显示取值」和「Multica 库条目只写不读，不参与比对。停用、超时、工具白名单等字段不参与比对。」
3. **本机工具配置**：每个条目一行 `.skill-entry`：符号、等宽路径、工具名、组字母、`env 已设置 N 项` 或 `env 未设置`、headers 同理、停用时写「已停用」、损坏时写 `broken_reason` 的固定文案。没读到的文件也列一行：`?` + 路径 + 固定原因。末尾「路径证据，未验证 runtime 当前会话实际加载。」
4. **仓库项目级**：同上。条目 env 已设置时加一句「项目级文件在仓库工作区里；env 有值时确认它没有被提交。」
5. **Multica Workspace（跨 Workspace）**：先写只读提示（第 8 节），再每个 workspace 一块 `.cloud-install`：库条目（传输、更新时间、固定说明「配置只写不读」），已启用和已停用的 agent（各附加载结论标签：会加载 / 不加载 / 有条件 / 未知 / 未绑定），各自的停用 / 启用 / 取消分配命令；分配没读到时写「分配没读到，无法列出 agent，也不判断是否无人使用」；自有配置条目（agent、组字母或「未参与比对」、env/headers 项数、清除自有配置命令）。替换配置和删除库条目放进 `<details>`，展开后先列影响范围，删除沿用 v1 的勾选两步。命令沿用 v1 `commandsFor` 的表，全部带 `--workspace-id <uuid>`，配置内容只用 `--*-config-file` 形式，下方固定提示「文件里有密钥：建议 chmod 600，用完删除，不要放进 git 仓库。」
6. **在终端处理**：只读提示 + 「改本机文件后，下一次扫描生效。」；每个相关本机或项目文件一条「打开 X 配置」命令 `"${EDITOR:-vi}" <声明路径>`；固定提示「文件里有密钥：保持 chmod 600，不要放进 git 仓库，共享屏幕时不要打开。」页面不生成任何带取值的命令，也不提供 diff 命令（diff 会把取值打到终端）。`symlink` 状态的文件不给「打开」命令，改写「这个路径是符号链接，看板没有跟随；请自己确认链接目标」。

**agent 分配信息在哪里看**（v1 按 agent 汇总视图按已批选择 4 删除）：分配数据照常采集，并在这些地方可查：每行抽屉 Multica 节列出各 workspace 已启用和已停用的 agent 及加载结论；Multica Workspace 列的 agent 简写；「只看已分配给 agent」复选框；搜索匹配 agent 名；「会被忽略」「同名覆盖」「可能失败」三个桶的 `reason` 写出 agent 名。按 agent 汇总的列表属于 Agents 板块或跨板块跳转（ACM-390），本单不补。

## 7. 组件和视觉（第 11–15 条）

**复用**（不改类名，G16 改名归 ACM-389）：`.source-grid` `.source-card` `.source-recon`、`.toolbar` `.filter` `.check`、`.skills-anomaly`、`.skills-legend` `.legend-group`、`.skills-layout` `.skills-main`、`.table-wrap.sticky-head`、`table.skills-table`、`.skill-group` `.skill-more` `.skill-row`、`.channel-col` `.version-col` `.cell-ellipsis`、`.tool-col` `.tool-mark` 及 `.missing` `.unknown` `.not-applicable`、`.repo-entries` `.repo-group` `.repo-name` `.entry-chip`、`.cloud-cell` `.cloud-ws` `.cloud-install`、`.skills-queue` `.skills-compact` `.skill-bucket` `.queue-item` `.debt`、`#drawer` 及抽屉各类、`.command`、`.skill-entry`、`.tag.*`、`.bad-n`、`.atlas-guide`、`.empty`、`.is-stale`、`.scope-note`、`.src-chip`。`section()`、`line()` 在 `mcp.js` 里保留现有一份，不和 `skills.js` 合并（G15 归 ACM-389）。

**新增 CSS**（四处，都放在 skills 段后面）：

| 类 | 写法 | 现有的为什么不够 |
|---|---|---|
| `.tab-badge.tone-bad` | `background: var(--bad); color: var(--bg);` | 红色角标现在只有 `.tab-badge.skills`，带页面名，而且写死 `#fff`；MCP 需要按 `tone` 切换 |
| `.tab-badge.tone-unknown` | `background: var(--panel-2); color: var(--muted); border: 1px dashed var(--faint);` | 「没读到，不确定」既不是红也不是黄，不能借用已批的两种颜色 |
| `.warn-n` | `color: var(--warn); font-weight: 600;` | 只有 `.ok-n` 和 `.bad-n`，「等用户处理」的黄色计数没有对应类 |
| `.tool-mark.off`（含 `sup`） | `color: var(--muted)`；`sup` 9px | 停用要和 `●` 区分，又不能借 `○`（Skills 里是「经共享目录」） |

**删除**：`styles.css` 里全部 `.mcp-*` 选择器（`.mcp-summary` 到 `.mcp-queue`），以及 `mcp.js` 的矩阵、清单、三种抽屉代码。

**格子符号**（`○` 不用）：

| 符号 | 本板块含义 | 样式 | 与设计系统 §2.1（Skills）的对应 |
|---|---|---|---|
| `●` | 该工具的全局配置里有同名条目 | 正文色 | 相同：有直接入口 |
| `●`<sup>停</sup> | 有条目，配置里显式停用 | `.tool-mark.off` 灰 | 新增；Skills 没有停用 |
| `✕` | 条目损坏：有这个名字的条目，但不能用（`broken_reason` 五种之一） | `.missing` 红 | **领域映射**：Skills 是「入口或版本文件不在」，即登记了但实物缺失；MCP 没有「实物文件」，对应的是「登记了但内容不能用」。两者共同点是「声明了，按声明用不了」，所以沿用 `✕` 和红色；含义不是逐字相同 |
| `?` | 无法确定：文件没读到（3.2 的九种状态） | `.unknown` 黄 | 相同：无法确定 |
| `∅` | 不适用：这一行确定只在 Multica | `.not-applicable` 灰 | 相同：只存在于 Multica Workspace |
| 空白 | 已读该文件，没有同名条目；仍可能来自该工具的其他配置层 | — | 相同 |

**与父单「●○✕ 表示有无」的对应**：父单要求照搬 Skills 用符号表示有无。最终图例里，「有」是 `●`（停用为 `●停`），「有但不能用」是 `✕`，「确定没有」是空白，「不确定」是 `?`，「不适用」是 `∅`。`○` 在 Skills 里表示「经共享目录可见」，MCP 没有共享目录，所以不出现；不把它挪作他用，避免 M4 的含义冲突。已批选择 1–4 不受影响。

格子 `title` 写含义、文件路径、指纹组字母，以及「路径证据，未验证 runtime 实际加载」。图例按列分组：本机工具、仓库入口、Multica Workspace、一致性，原文见 `mock.html`。设计系统 §2.1 的符号表在实现阶段文档同步时加一行脚注说明 MCP 的 `✕` 映射（第 11 节第 4 条）。

**颜色**：只用 §4 的变量。损坏、会被忽略、同名覆盖、可能失败用 bad；配置不一致、无法确定、待确认、旧数据用 warn；一致、会加载用 ok；其余 muted。每个颜色都同时有文字或符号。

**四种情况**（§4.5），细则见 5.1：

| 情况 | 卡片 / 计数 | 格子 / 列 | 页面 |
|---|---|---|---|
| 有 | 数字 | 符号 | 正常 |
| 确定没有 | 0、空桶「无」 | 空白；文件不存在时列头 0，`title`「文件不存在：确定没有全局 MCP」 | 全空且所有来源已知时「还没有 MCP server」 |
| 没采集 | 「—」+ 原因 | Multica 列「—」 | 桶「— 原因」 |
| 读取失败 | 保留上次数据；依赖它的卡片加「含 N 行待确认」 | 文件没读到的列整列 `?`，表里不出现 `∅`；库读取失败的 workspace 在格内加「旧数据」标签；分配没读到写「分配没读到」 | 异常提示条 + 健康标签变黄或红；桶 `partial` 加 `?` 和「另有来源没读到」；整份接口失败时表格加 `.is-stale`，提示条写「本轮读取失败，显示 X 前的数据」 |

接口 404：`.atlas-guide`「后端还没有 MCP 采集」。`ok: false`：`.atlas-guide`「MCP 数据无效」+ 原因类别 + 怎么修。

## 8. 只读提示、命令和复制

- 只读提示用 Skills 原句：「页面只读，不代为执行。复制命令后在自己的终端运行。」只出现在抽屉的命令区上方（Multica 节和「在终端处理」节），页面顶部不再放。后面可跟一句影响说明：Multica 节是「写命令需要 workspace admin/owner；agent 会话里运行会被拒绝。」，本机节是「改本机文件后，下一次扫描生效。」
- 复制按钮成功写「已复制」，失败写「复制失败」，并做回退（三条路径）：
  1. 先用 `navigator.clipboard.writeText`。
  2. 失败或不可用时，用隐藏 `textarea` + `document.execCommand("copy")`，**检查返回值**。
  3. 仍失败时，按钮写「复制失败」，把这条命令的 `<code>` 内容选中（`Range.selectNodeContents`），并在命令下方插入一行灰字「已选中命令，按 ⌘C 复制」。
- 实现为 `mcp.js` 导出的 `copyWithFallback(text, env)`，返回 `true/false`，`env` 可注入剪贴板和 `document` 以便测试。不改 `app.js` 的 `copyText`（它吞掉了失败，所以 v1 的「复制失败」实际不会出现）和 `skills.js` 的 `copyCommand`，统一归 ACM-389。

## 9. 路由、搜索和键盘

- hash 参数：`#/mcp?source=both&status=differs&tool=cursor&group=tool&assigned=1&tidy=1&q=…`。`source`、`tool` 复用 route 现有字段，按 MCP 的取值校验；新增 `mcpStatus`、`mcpGroup`、`mcpAssigned`、`mcpTidy`，在 `parseHash` / `formatHash` / `applyPatch` 登记，离开板块时清掉。删除 `mcpView`、`issue`；MCP 不再读 `archived` 和 `ws`（`ws` 仍按 §1.4 跨板块保留）。旧链接里的 `view=list`、`issue=`、`archived=1` 在规范化时丢弃。行 `id` 不进 hash（重启后会变）。
- `filterChrome("mcp")` 从 `shared` 改为与 Skills 相同的范围说明形态，`renderChrome` 按当前板块取 `state.mcp.meta.sources`。`mcpWorkspaceChip` 删除。
- 搜索匹配：显示名、来源标签、出现的工具名、文件路径、workspace 名和 slug、agent 名。不匹配组字母和行 `id`。placeholder「搜索 server / 工具 / workspace / agent」。
- 键盘：`6` 进入，`/` 搜索，`Tab` 到行，`Enter` 打开抽屉，`Esc` 关闭并回到原行。v1 格子间的方向键导航随矩阵一起删除。横滚靠 `.table-wrap` 自身的滚动（触控板、Shift + 滚轮、滚动条），不劫持方向键。

## 10. §6.1 M1–M13 逐项映射

| 编号 | v1 问题 | 重做后的做法 | 验收看哪里 |
|---|---|---|---|
| M1 | 指标卡不是分区，3 张不可点 | 来源卡是行的分区，全部可点，和「来源」下拉写同一个 `source=`；`.mcp-card` 删除；依赖未知来源的卡片写「含 N 行待确认」 | `compare-1440.png` 顶部；`state-partial-1440.png`；点每张卡，下拉同步 |
| M2 | 每个 workspace 一块、各自一张表 | 一张表 + 分组行，一个限高滚动区 | `v1-vs-redesign-1440.png` |
| M3 | 列是动态库条目，超 8 列退回清单 | 列是 `mcp-sources.json` 里的固定工具 + 仓库入口 | `compare-1440.png` 表头 |
| M4 | 另起一套符号，`○` 含义冲突，没有图例 | `●` `✕` `?` `∅` 空白按第 7 节对应表使用，`✕` 是记录在案的领域映射（不是逐字同义），`●停` 为新增；`○` 不用；页面有图例，列头 `title` 写证据 | 图例行、格子 `title`、第 7 节对应表 |
| M5 | 三种抽屉 | 一种抽屉，`kind = "mcp"`，按行 `id` 定位 | `compare-1440-drawer.png` |
| M6 | 矩阵 / 清单切换 | 删除，只有「分组」下拉 | 工具栏 |
| M7 | 工具栏 workspace 下拉和顶栏标签写同一个 `ws` | 两者都去掉，改为固定范围说明 + 健康条 | 顶栏第三行 |
| M8 | 只读提示在页面顶部 | 只在抽屉命令区上方，用 Skills 原句 | `copy-fail-1440.png` |
| M9 | 桶标题是筛选按钮，条目开三种抽屉，窄屏只剩空框 | 桶标题只是标题；条目走 `focusMcp(id)`；渲染 `.skills-compact` | `compare-1100.png` 侧栏上移成一行计数 |
| M10 | 一条记录可进多个桶 | 一个对象一个桶，其余变 `tags`；桶在后端算；来源未知时桶 `partial`，不得用已读子集给出「无」 | 接口测试 + 待处理栏 + `state-partial-1440.png` |
| M11 | 失败借 `.decide-note`，全空塞进 `.empty.big` | 失败用 `.skills-anomaly` + `.is-stale` + 5.1 的未知优先规则；全空分三种（全已知 / 部分未知 / 全未知），404、无效用 `.atlas-guide` | `state-partial-1440.png`、`state-unknown-1440.png`；实现后 `mcp-partial`、`mcp-empty`、`mcp-unknown`、`mcp-absent`、`mcp-invalid` 截图 |
| M12 | 行不能点 | 整行可点，`tabindex="0"`，`Enter` 打开，选中加 `.selected` | 键盘走查 |
| M13 | 角标默认黄 | 按 `tone`：坏了红、只有待处理时黄、只有未知时灰 `?` | `compare-1440.png` 顶栏（红）；`state-warn-1440.png`（黄）、`state-unknown-1440.png`（灰 `?`）；实现后 `mcp`、`mcp-warn`、`mcp-unknown` 截图 |

## 11. 实现拆分建议

1. 后端：`mcp-sources.json`（含声明校验）；`mcp_local.py`（3.2 安全打开、各格式读取、受限 YAML 读取器、3.3 摘要和 `transport_enum`、HMAC 指纹、3.4 密钥文件）；`mcp_summary.py`（删除原样回显分支，改用 `transport_enum`，摘要加 `exact` 和 `broken_reason`；`plain_text` 只用于显示名）；`collector.py`（`norm_agent` 和库条目采集时写 `OwnFingerprints`，首轮强制读 MCP，`mcp_servers` 加 `name_exact` 列）；`mcp_view.py` 改为 schema 2（按原始身份合并、行 `id`、`display_dup`、一致性、5.1 未知优先、分桶、`tone`、`summary`、`unknown`、`meta.sources`）。
2. 前端：`mcp.js` 按 `skills.js` 的结构重写（`renderMcp`、`renderMcpDrawer`、`filteredItems`、`groupedItems`、`queueModel`、`copyWithFallback`）；`app.js`（抽屉分派、`focusMcp(id)`、角标 tone 四态、`toolSignal`、`mcpCtx`）；`route.js`；`filter-bar.js`；`styles.css`。
3. fixtures：`write-mcp.js` 生成 `mcp`、`mcp-empty`、`mcp-partial`、`mcp-unknown`、`mcp-absent`（沿用 app.js 的 404 模拟）、`mcp-invalid`、`mcp-warn`，登记进 `scenarios.json`。`mcp` 场景里包含一对「同显示名」行和一个 `name_unverified` 行。
4. 文档同步：`api-contract.md`（v0.10，`/api/mcp` schema 2）；`mcp-design.md`（头部已指向本文；删掉「不允许 Atlas 读 `~/.cursor/mcp.json`」那句，改为指向第 3 节）；`design-system.md` §1.2 MCP 行和 §1.3 形态、§2.1 符号表加 MCP `✕` 映射脚注、§3.3 删 `.mcp-card`、§6.1 标注已由 ACM-378 解决；`frontend-design.md` MCP 一节；**`README.md` 安全边界**：把「后端不读 CLI 配置」改为准确的范围——只读 `mcp-sources.json` 声明的本机和项目级 MCP 配置文件，不跟随符号链接，只在内存里解析，取值不进 SQLite、接口和日志；新增指纹密钥文件（数据目录下 `0600`）和进程内指纹的说明；仍不读 CLI token。

## 12. 已批准的设计选择

用户评论 `01a16a1b-6eda-7a9f-b490-ba267f9b46b1`「批准 1-4」，按编号提案执行，本版不再申请：

1. 红黄划分：条目损坏、会被忽略、同名覆盖、可能失败算「坏了」（红），配置不一致算「等用户处理」（黄）。本版新增的灰色 `?` 只用于「什么都没发现但有来源没读到」，不改变这条划分。
2. 「无人使用 / 只有停用」不计入角标，做成「待清理」虚线按钮；「自有配置不可见」从桶改为异常提示。
3. 健康条从 workspace 标签改为固定范围说明，页面不再按 workspace 筛选（搜索仍能匹配 workspace 名）。
4. v1 的 agent 视角汇总不保留；agent 分配数据仍采集，可查位置见第 6 节末尾。

读取本机配置在本单原范围内，不另行申请；`README.md` 安全边界必须同步（第 11 节第 4 条）。本版把指纹密钥从 `~/.local/share/agents-lab/` 移到看板自己的数据目录，是缩小落盘范围的细节调整，不涉及任何 harness、agent、runtime 或全局配置。

## 13. 对照图和设计稿状态图

全部由 `shoot.py` 生成。对照图左边是 `web/?mock=1#/skills`（或 v1 的 `?mock=mcp#/mcp`），右边是 `mock.html`：

| 文件 | 视口 | 内容 |
|---|---|---|
| `compare-1440.png` | 1440 × 900 浅色 | 顶部卡片、工具栏、图例、表格密度、待处理栏、红色角标、同显示名两行（正常场景所有来源都读到，没有异常提示条） |
| `compare-1440-drawer.png` | 1440 × 900 | 抽屉头、节标题、字段行 |
| `compare-1100.png` | 1100 × 900 | 待处理栏上移为一行计数（M9） |
| `compare-390.png` | 390 × 844 | 顶栏换行、卡片两列、工具栏换行（首屏） |
| `compare-dark-1440.png` | 1440 × 900 深色 | 变量在深色下的表现 |
| `v1-vs-redesign-1440.png` | 1440 × 900 | v1 MCP 与重做设计稿（M1–M13 前后） |
| `copy-fail-1440.png` | 1440 × 900 | 复制前 / 复制失败 + 选中回退（预设的静态状态） |
| `state-partial-1440.png` | 1440 × 900 | 只有设计稿：Hermes 文件解析失败、英语学习分配没读到；异常提示条、`?` 列、没有 `∅`、「含 N 行待确认」、桶 `?` 和「另有来源没读到」 |
| `state-unknown-1440.png` | 1440 × 900 | 只有设计稿：所有来源都没读到、没有条目、灰色 `?` 角标 |
| `state-warn-1440.png` | 1440 × 900 | 只有设计稿：只剩配置不一致、黄色角标 |
| `scroll-right-1440.png` | 1440 × 900 | 只有设计稿：表格横滚到最右，一致性列可见 |
| `mock-390-table.png` | 390 × 844 | 只有设计稿：滚到表格区域 |

这些都是静态图，只说明外观约定，**不是交互实测**：点击、键盘、真实剪贴板失败、横滚操作、390 宽下的抽屉、黄色和灰色角标的真实数据驱动，都留到实现验收（第 15 节）。

## 14. 测试要求：fake 数据、临时目录、泄密哨兵

**原则**：测试、fixtures、截图都不读真实的本机 MCP 配置，也不连真实看板。截图用 `?mock=`；本单出图时发现 8765 端口上跑着用户的真实看板，`shoot.py` 因此固定用 8799 并只起静态服务。

**后端**（新增 `tests/test_mcp_local.py`，扩充 `tests/test_mcp.py`）：

1. **隔离**：所有读取函数接收 `home`、`repo_root`、`data_dir` 参数；测试一律用 `tempfile.TemporaryDirectory()` 造假 HOME、假仓库和假数据目录，并设置 `HOME` 环境变量指向临时目录。对 `os.open`、`open`、`Path.read_bytes`、`os.chmod`、`os.chown`、`os.rename`、`os.replace`、`os.unlink` 打桩记录：测试期间没有打开真实 home 下的任何路径，没有对临时目录外的路径做任何写操作。
2. **九工具 / 五项目路径**：九种全局文件（第 3.1 节表）和五种项目文件各写一份 fake 配置，逐一断言读到的容器和条目数正确；`mcp-sources.json` 的 `evidence` 字段非空。声明校验：绝对路径、`..`、空段各一条，返回 `ok: false`。
3. **受限解析器**：Hermes YAML 的块状映射、引号标量、块状和行内列表能读；锚点、别名、标签、多行字符串、合并键、多文档各一份，得到 `unsupported`，且 `mcp_servers` 以外的顶层键里放的哨兵不出现在任何输出。JSONC 注释和尾逗号（Gemini、OpenCode）能读。
4. **安全打开**（每条都断言：状态正确；链接目标或特殊文件的内容没有被读取——打桩显示没有对目标路径的 `open`，且目标里的哨兵不出现在任何输出；目标的字节、模式、`mtime` 在测试前后不变）：
   - 配置文件是指向临时目录里「目标文件」的符号链接 → `symlink`。
   - 中间目录是符号链接（如假 HOME 里 `.cursor` → 另一个目录） → `symlink`。
   - 配置路径是 FIFO（没有写端） → `not_regular`；扫描放在线程里，2 秒内必须返回，否则判失败。
   - 配置路径是目录、socket → `not_regular`；中间一级是普通文件 → `not_regular`。
   - 检查后替换：打桩在打开最后一级前一刻把文件换成指向目标的链接 → `symlink`；在打开后、读取前换掉 → 读到的是原文件内容，绝不是目标内容。
   - 超过 16 MB、读取中变大 → `too_large`；无权限 → `denied`；`os.supports_dir_fd` 打桩为不支持 → 全部 `platform_unsupported`，没有任何按路径的 `open`。
5. **密钥文件**（目标文件和目录的字节、模式、`mtime` 测试前后不变；任何拒绝都不 `chmod`、不覆盖、不删除、不新建）：不存在时新建为 `0600`、32 字节；是符号链接 → `key_symlink`；是 FIFO → `key_not_regular` 且 2 秒内返回；模式 `0644` → `key_mode`，之后仍是 `0644`；31 / 33 字节 → `key_length`，内容不变；硬链接数 2 → `key_links`；所有者不符（把 `geteuid` 注入为别的值）→ `key_owner`；数据目录 `0777` 或是符号链接 → `key_dir_unsafe`，目录模式不变；新建时打桩制造 `EEXIST` → 读取已有密钥，不覆盖。所有拒绝都让 `drift` 桶 `connected: false`、一致性全部「不可比」，健康标签为固定文案。
6. **传输枚举和字段哨兵**：`type`、`transport` 放未知字符串哨兵 → `unknown` + `unknown_transport`；`type` 为数字、列表（含哨兵）、对象（键和值含哨兵）→ `bad_field_type`；`command` 为对象、`args` 为字符串、`env` 为列表、`headers` 为字符串、`url` 为数字，各自含哨兵 → `bad_field_type`；条目不是对象 → `not_object`。本机、项目、自有配置、库条目（`fake_cli.py` 返回哨兵 transport）四条路径都测。预置一行 v1 格式的 SQLite（`mcp_own_json` 和 `mcp_servers.transport` 含哨兵）→ 接口输出 `unknown`，哨兵不出现。
7. **泄密哨兵**：每份 fake 文件在 env 值、env 键名、headers 值、headers 键名、url（主机、路径、查询串）、command、args、cwd、未知额外字段、`type`、`transport`、类型不对的字段里各放一个唯一哨兵串，形如 `SENTINEL-<文件>-<字段>-7f3a`；再各写一份故意损坏的文件，哨兵放在出错的那一行；符号链接目标和 FIFO 路径名里也放哨兵。断言哨兵不出现在：`/api/mcp`、`/api/agents`、`/api/skills` 及其余全部 `/api/*` 的响应；SQLite 文件原始字节（含 WAL）；`-v` 级别日志（`caplog` 和 stderr）；所有异常信息和 `meta.sources[].detail`；指纹密钥文件之外的任何临时文件。
8. **指纹**：同一份配置在两个工具里 → 同组；只改 env 值 → `env` 不同、`launch` 相同；换密钥文件 → 组划分不变但内部哈希全变；内部哈希不等于对规范化 JSON 做的裸 SHA-256。把测试里算出的全部内部指纹（完整值、hex 和 base64 形式、任意 8 位以上的前缀）和进程盐拿去搜全部 `/api/*` 响应、SQLite 原始字节和 `-v` 日志，都搜不到；行 `id` 不出现在 SQLite 和日志。
9. **内存指纹的生命周期**：collector 线程采集后，自有条目 `fp_state: ready` 并参与分组；`mcp_own_json` 只含白名单键。模拟重启（同一个 SQLite，新建 `Views` 和空的 `OwnFingerprints`）→ 自有条目全部 `pending`，所在行 `partial: true`，不因此出现 `same` 或 `differs`，「配置指纹」标签为黄；跑一轮 collector 后恢复。只跑独立 `collect` 进程再起不带采集的 `serve` → 一直 `pending`。换密钥 → 自有条目 `pending`，本机条目立即按新密钥分组。agent `updated_at` 变化但未重新采集 → 该 agent `pending`。首轮补采：`mcp_state.fingerprint` 未变、`rounds` 未到 `--mcp-every` 时，serve 首轮仍读取 MCP。
10. **原始身份**：`"a  b"` 和 `"a b"`、`" github"` 和 `"github"`、`"a\tb"` 和 `"a b"`、空字符串和 `"   "`、大小写不同、NFC 和 NFD、前 80 字符相同的两个长名、两个脱敏后相同的名字（如都含 `ghp_` 样式的不同串）→ 各自是不同的行，`id` 不同，`display_dup` 编号正确，指纹分组和同名覆盖不会跨行。`exact: false` 的自有条目在重启后不合并、带 `name_unverified`，`shadowed` 桶 `partial`。原名里放可被 `redact` 识别的哨兵 → 不出现在任何输出。
11. **未知优先**：混合失败（Hermes `parse_error` + 一个 workspace 分配失败）→ Hermes 列全部 `?`，表里没有 `∅`，「仅 Multica」卡 `uncertain` 正确，该 workspace 的库条目不进 `tidy`、`ignored`、`may_fail`，相关桶 `partial`；一个同名条目可能在失败文件里 → 一致性 `partial`；全部来源失败且 `items=[]` → 不出现「还没有 MCP server」，`tone: unknown`，各卡「—」；部分失败且 `items=[]` → 「没有读到 MCP server」；全部已知且为空 → 「还没有 MCP server」；`stale` 库数据能证明有、不能证明没有。
12. **分桶**：一个对象一个桶、顺序、`tags`、`tone` 四态（只有 drift 时为 `warn`，只有未知时为 `unknown`）、`tidy` 不计入角标、`connected: false` 的各种原因、`partial` 的来源列表。
13. 生效判断沿用 v1 的用例（只有库、只有自有、两者同名、自有在旧 `mcp` 容器、自有为 `{}`），同名比较改为按原始身份。
14. CLI 白名单不变；全程不对真实 workspace 做写操作，只用 `tests/fake_cli.py`。

**前端**（`web/mcp.test.js` 重写）：`filteredItems`、`groupedItems`、`queueModel`（含 `partial` 和 `?`）、格子符号映射（含 `unknown` 优先于 `not_applicable`）、角标 tone 四态和 `title`、`display_dup` 显示、按 `id` 打开和保持抽屉（刷新后 `id` 消失时关闭）、`copyWithFallback` 的三条路径（剪贴板成功；剪贴板失败、`execCommand` 返回 `true`；两者都失败时返回 `false`、按钮「复制失败」、命令被选中、出现提示行）、route 的 parse / format / 离开清理和旧参数丢弃。

**fixtures**：全部虚构名字。加一条测试扫描 `web/fixtures/mcp*/`：不得出现 `ghp_`、`sk-`、`xox`、`Bearer `、32 位以上的 hex 或 base64 串、`@` 邮箱。

## 15. 验收清单

设计系统 §5 逐条（验收写「是 / 否 + 说明」）：

| # | 本板块的答案 |
|---|---|
| 1 | 问题和数据层见第 1 节 |
| 2 | 顶栏板块，理由见第 1 节 |
| 3 | 固定范围说明 + 健康条 |
| 4 | 地图 L3 工具行 + `toolSignal` 新文案；板块内 `focusMcp(id)`；不出板块 |
| 5 | 版式 A，差异 4 条见第 2 节 |
| 6 | 一行一个 server 原名；按工具分组时组标题注明可能重复 |
| 7 | 卡片是分区、全部可点、写 `source=`，与下拉同参 |
| 8 | 参数和搜索字段见第 9 节 |
| 9 | 五个桶后端算、顺序固定、一对象一桶、空桶「无」、未接上「—」+ 原因、部分未知 `?`；角标 = 五桶已发现条数之和，`tone` 四态 |
| 10 | 一种抽屉，节顺序见第 6 节，`Esc` 回到原行 |
| 11 | 复用和新增见第 7 节 |
| 12 | 符号按第 7 节对应表，`✕` 领域映射已记录，`○` 不用；图例和列头 `title` 齐全 |
| 13 | 只用变量，无十六进制颜色 |
| 14 | 四种情况见第 7 节表格和 5.1 |
| 15 | 只读提示用 Skills 原句；命令带 `--workspace-id <uuid>`；只用 file 形式；密钥只写「有没有、几项」 |
| 16 | 场景：`mcp`、`mcp-empty`、`mcp-partial`、`mcp-unknown`、`mcp-absent`、`mcp-invalid`、`mcp-warn` |
| 17 | 与 Skills 对照截图：1440 和 1100，逐项对照卡片、工具栏、图例、表格密度、待处理栏、抽屉 |
| 18 | 深色 1440 和 390 宽各一张 |
| 19 | 键盘走查：`6`、`/`、`Tab`、`Enter`、`Esc` 回原行 |

本单额外要求（实现验收时逐项实测，静态设计稿不能替代）：

- M1–M13 每项按第 10 节的「验收看哪里」逐项核对，附截图。
- 红色角标（`mcp`）、黄色角标（`mcp-warn`）、灰色 `?` 角标（`mcp-unknown`）各一张截图，`title` 文案正确。
- 只读提示逐字等于「页面只读，不代为执行。复制命令后在自己的终端运行。」，页面顶部没有只读提示。
- 复制三条路径：在禁用剪贴板的浏览器上下文里点复制，按钮显示「复制失败」，命令被选中，下方出现「已选中命令，按 ⌘C 复制」；另验证剪贴板成功和 `execCommand` 回退成功各一次。
- 横滚可达性：在 1440、1100、390 三个视口，页面 `document.documentElement.scrollWidth` 不超过视口宽度；把 `.table-wrap` 横滚到底，一致性列完整出现在容器可视范围内；纵向滚动时表头保持吸顶；横滚后点行仍打开正确的抽屉。附实测数字。
- 390 宽：滚到表格区域和打开抽屉各一张截图，抽屉可关闭、焦点回到原行。
- `mcp-partial`、`mcp-unknown`、`mcp-empty`、`mcp-absent`、`mcp-invalid` 各一张截图，文案与 5.1、第 7 节一致；`mcp-partial` 里没有 `∅`。
- 第 14 节的全部测试通过（安全打开、密钥、传输枚举、哨兵、内存指纹与重启、原始身份、未知优先、分桶、路由、复制三路径），并附命令和结果。
- 生成的命令里没有 `--server-config '`、`--mcp-config '{`、`--server-config-stdin`，也没有带取值的本机命令。
- `styles.css` 里没有残留的 `.mcp-*` 选择器；没有改 `skills.js`、`agents-view.js`、`plan.js`、`view-atlas.js` 的复制和只读提示代码。
- agent 分配信息：删除 v1 汇总视图后，第 6 节末尾列出的可查位置逐一走查；分配没读到时写「分配没读到」，不显示为未分配。
- `README.md` 安全边界已按第 11 节第 4 条更新。
- MCP 页与 Skills 页的截图对比，「版式、组件、交互与设计系统一致」逐项给结论。
- 不涉及 ACM-389（跨页面 G 项）和 ACM-390（跨板块跳转）的改动。

## 16. 第 2 版修订记录（回应 ACM-396 审查）

| 审查意见 | 修复位置 |
|---|---|
| 1 允许路径边界未封闭：符号链接、非普通文件、检查后替换、已有密钥的链接 / 所有者 / 长度 | 3.1 声明校验；3.2 安全打开与读取状态表；3.4 密钥打开、拒绝代码和新建规则；14 第 1、4、5 条 |
| 2 agent 指纹跨采集 / 请求的数据流矛盾 | 3.5 全节（`mcp_own_json` 白名单、`OwnFingerprints`、`key_gen`、有效条件、不可比情况、首轮补采、日志）；4 `fp_state` `pending`；14 第 8、9 条 |
| 3 原名合并与展示清洗冲突 | 2「一行是什么」；3.6 全节（原始身份、行 `id`、显示名、同显示名、SQLite `exact`、抽屉定位）；4 `id` / `display_name` / `display_dup`；6 抽屉定位；9；14 第 10 条 |
| 4 `transport_of` 原样回显未知 `type` | 3.3 传输枚举表和 `broken_reason`；3.7；14 第 6、7 条 |
| 5 未知与不适用优先级不明 | 5.1 全节（来源已知表、格子顺序、来源类别、Workspace 列、一致性、分桶依赖表、分配未读不判无人使用、健康标签、全空三种）；5 角标 `tone` 四态；7 四种情况表；14 第 11 条 |
| M4 `✕` 领域映射；父单「●○✕ 表示有无」与最终图例 | 7 符号对应表和「与父单的对应」；10 M4 |
| M10、M11 未知状态规则 | 5.1；10 M10、M11；`state-partial-1440.png`、`state-unknown-1440.png` |
| M13 黄色角标 | 5 角标表；`state-warn-1440.png`（静态）；实现后 `mcp-warn` 截图 |
| 一致性列初始可见的承诺不成立 | 2 第 4 条改写；9 键盘；15 横滚可达性；`scroll-right-1440.png`；`shoot.py` 打印实测宽度 |
| 390 只有首屏；静态图不是交互实测 | 13 末段；15 的 390 表格和抽屉截图要求；`mock-390-table.png`（静态） |
