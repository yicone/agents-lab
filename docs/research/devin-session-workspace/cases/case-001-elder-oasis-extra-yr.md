# Case 1：Elder Oasis 会话被附加 `yr` 工作目录污染

## 时间与范围

- 调查与修复：2026-09-15
- 产品：Devin Desktop 3.10.23 / local Devin CLI
- 目标主目录：`/Users/tr/Workspace/elder-oasis`
- 异常附加目录：`/Users/tr/Workspace/yr`

## 目标 Session

| Session ID | Session Name |
|---|---|
| `accurate-opossum` | Elder Oasis: Taro Init & Admin Form |
| `garrulous-haddock` | Elder端游戏大厅 & 禅意游走 & 反诈模拟 & 文案优化 |
| `wistful-bowl` | 订阅消息通知 & 下拉刷新 |
| `lumbar-success` | 数据库运维 |

## 用户观察

1. Agent's working directories 显示 `elder-oasis` 和 `yr`。
2. 打开目标 Session 后，Explorer 的 Workspace 自动包含两个目录。
3. 在 Explorer 中 Remove Folder from Workspace 后，`yr` 立即重新出现。
4. 这些 Session 历史上曾位于同一个 Space；用户后来执行了 `Ungroup`。因此当前分离的
   Space 不能被当作原始状态。

## 已确认事实

调查读取了 Devin CLI 会话库和 Desktop 状态库：

```text
~/.local/share/devin/cli/sessions.db
~/Library/Application Support/Devin/User/globalStorage/state.vscdb
```

四条 CLI 记录均为：

```text
working_directory = /Users/tr/Workspace/elder-oasis
workspace_dirs    = ["/Users/tr/Workspace/yr"]
```

Desktop 对应记录的 `info._meta["cognition.ai/additionalWorkspaceDirs"]` 也为 `yr`。
应用内的 `sessionWorkingSetFolders` 逻辑将主目录与附加目录去重合并，正好解释了 UI 中的
两个目录。

## 修复动作

在 Devin Desktop 和 Devin CLI 均退出后：

1. 备份了 CLI 数据库、CLI WAL、Desktop 状态库和当时的派生 Workspace 配置。
2. 对四个明确 Session ID 使用主目录和旧附加目录双重条件保护。
3. 将 CLI `workspace_dirs` 改为 `[]`。
4. 删除 Desktop 镜像中的 `cognition.ai/additionalWorkspaceDirs` 字段。
5. 冷启动 Devin，逐一打开四个 Session。

## 验证结果

- 四个 Session 的主目录仍为 `elder-oasis`。
- 四个 CLI `workspace_dirs` 均为 `[]`。
- 四个 Desktop 镜像字段均不存在。
- 四次 Explorer 检查均只显示 `elder-oasis`，没有重新加入 `yr`。
- 修复没有修改项目文件、Session transcript 或 Space 分组。

## 根因结论与未决问题

已证实的直接原因是四个 Session 的持久化附加目录字段被写成了 `yr`。尚未证实的是最初
写入的具体事件。用户关于“另一个 `workspace_dirs` 为其他目录的 Session 混入同一 Space 后，
共享目录状态扩散到成员 Session”的推测与现象相符，但目前仍应标记为待验证假设，而非确定根因。

这暴露出至少一个设计缺陷：Space 目录并集、当前 Explorer 视图和 Session 持久化附加目录之间
缺少清晰的作用域提示；Remove Folder from Workspace 也没有显式说明它是否会持久化到 Session。

## 2026-09-19 复发与扩展修复

`accurate-opossum` 再次在 Explorer 中显示 `yr`，且 Space 中的会话列表出现短暂消失。复查表明，
最初四个目标会话仍保持干净；同一 Elder Oasis Space 内另有五个本地 Devin 会话携带相同的持久化
附加目录，Space 的工作区聚合逻辑遂把 `yr` 提供给该 Space 的全部成员：

```text
foam-yamamomo
ember-foxtail
dear-reader
lemon-brook
lilac-lungfish
```

在 Desktop 与 Zed 均退出后，修复工具以 `working_directory == elder-oasis` 和
`workspace_dirs == [yr]` 为前置条件，清除了这五条 CLI 记录和五条 Desktop 镜像记录。备份位于：

```text
~/.local/share/devin/cli/repair-backup-20260919T203938+0800
```

修复后，对上述五条及原四条记录的双库复核均显示：CLI `workspace_dirs == []`，且 Desktop
`cognition.ai/additionalWorkspaceDirs` 字段不存在。

同时修复了工具自身的一个 TOCTOU 误报：Python 的 SQLite 连接上下文管理器只管理事务、不会关闭连接；
因此早期 `--apply` 在检查文件句柄时可能将自身误判为外部数据库占用。读取函数现在在检查前显式
`close()` 连接，回归测试通过。这个问题与 Devin Desktop 自动重启或重新写入数据库无关。

## 2026-09-19：工作目录已修复，但 Space 列表继续隐藏会话

Desktop 重启后的 UI 证据显示，`yr` 没有重新出现；但打开上述历史会话后，它们会从 Elder Oasis 的
左侧列表逐渐消失。此时不是持久化的 Space 成员关系被删除：

- `windsurfSpace.resourceToSpace["1784977093276-9xndoc4fc"]` 仍包含原四条与五条后续修复会话，
  以及同一 Space 的 ACP 会话资源。
- CLI `sessions.hidden` 对九条均为 `0`；`devin list --format json`（在 elder-oasis 目录）仍返回这九条。
- Desktop 的 `windsurf.acp.sessioninfo.session.acp/devin-cli/<id>` 记录仍在，且没有
  `cognition.ai/isArchived`、hidden 或 status 标记。

### Desktop 中存在但尚未证实触发本案的筛选机制

Desktop 3.10.23 的内置 `windsurf-chat-client` 在构建 Space 面板列表时，过滤条件等价于：

```ts
connectorIsAvailable(session.providerId) &&
!(session.arenaId && session.isArenaRep !== true)
```

同一 bundle 的 reducer 会对本地 Devin provider 中同一个 `arenaId` 的会话，只将
`sessionId` 字典序最小者标记为 `isArenaRep = true`。这里的 `isArenaRep` 是 Arena
对比组的 UI 保留项，不是 Space 的“代表会话”；Space 本身没有代表会话概念。

该代码路径只能说明一种可能的隐藏机制，不能证明它触发了本案。2026-09-19 的运行时只读检查进一步
排除了此前的过度推断：

- 新启动的 `devin acp` 通过 `session/list` 返回九条目标会话，`cwd` 都是
  `/Users/tr/Workspace/elder-oasis`，但响应中没有 `arenaId` 或 `isArenaRep`。
- 会话从 Space 面板消失后，Desktop 暴露的 `globalThis.__acpStore` 中，
  `acp.sessions` 为 221 条，但按九个原始 Session ID 筛选结果为空。
- `wistful-bowl` 只出现在三条 `acp.agentRequests.*.params.sessionId` 请求记录中；这些父对象的
  `arenaId`、`isArenaRep` 和 `providerId` 均为空。
- 同一时刻 UI 显示 `Unable to connect to Devin. Some AI features may not work.`。

因此当前不能给九条会话列出任何实际 `arenaId`，也不能声称其中某条是 Arena representative。
静态 Arena 筛选仍保留为待验证代码路径；更贴近当前证据的方向是本地 Devin connector
断连或重载后，从 `acp.sessions` 移除了这些 local Devin summary，而 Space 面板对持久化资源与
运行时 session summary 的降级处理不一致。为什么 `wistful-bowl` 与四条 ACP 会话仍可见，尚待
在“刚启动、尚未点击任何目标会话”的窗口内抓取一次 store 前后差异。

### 当前结论

这已与 `workspace_dirs` 污染分离：前者是已修复的持久化目录错误；后者是尚未定位写入源或稳定
复现边界的 Desktop 运行时可见性错误。没有在运行中的 Desktop 数据库上猜测性写入。下一步应在
冷启动后、点击前和点击后各导出一次 `__acpStore`，比较九条 local Devin session summary 及
connector 状态的增删，而不是修改并不存在的持久化 `arenaId`。

## 2026-09-19：冷启动后静置对照

用户在冷启动 Devin Desktop 后、未点击任何问题会话时连续截图：刚启动和数秒后的
`Elder Oasis` 列表都完整包含九条本地 Devin 会话及四条 ACP 会话。随后对同一窗口进行只读观察：

- 未点击任何会话时，继续静置 12 秒，十三条会话仍全部可见，列表没有收缩。
- Explorer 只加载 `/Users/tr/Workspace/elder-oasis`，没有重新出现 `yr`。
- 当前冷启动窗口没有复现此前可见的 `Unable to connect to Devin` 提示。

这组对照排除了“Desktop 冷启动后仅因等待数秒便自动隐藏这些会话”这一解释。更精确的复现边界是：
冷启动和初始列表水合可以保留全部成员；隐藏现象需要之后的某个事件触发。根据此前现象，首要候选是
打开历史 Devin 会话时触发的 session activation / connector reconciliation，但在取得点击前后
`__acpStore` 差分前，这仍是待验证推断。下一次受控实验应固定一条目标会话，分别在点击前及点击后
0、1、3、10 秒导出 `acp.sessions`、`agentRequests` 和 provider 可用状态，同时确认持久化
`resourceToSpace` 未变化。

## 2026-09-20：点击触发的受控复现

在不修改数据库、项目文件或 Space 配置的前提下，使用冷启动后的同一 Devin 窗口完成一次受控点击：

- 点击前：`Elder Oasis` 仍显示九条本地 Devin 会话和四条 ACP 会话，Explorer 只有
  `/Users/tr/Workspace/elder-oasis`。
- 点击前的 DevTools 运行时查询显示：`__acpStore.acp.sessions` 共 221 条，但按九个目标
  Session ID 匹配结果为 `[]`。因此 Space 初始列表并非直接由这份 runtime summary 生成。
- 点击 `accurate-opossum` 后，该会话先从 Space 列表消失；约数秒后，其余受影响的本地 Devin
  会话也批量消失。
- 最终列表保留 `订阅消息通知 & 下拉刷新` 和四条 ACP 会话；Explorer 仍没有 `yr`。

这次复现将触发链进一步限定为：打开历史本地 Devin 会话会触发 Chat Client 的 session
activation / reconciliation；在该过程中，Space 面板从仍可显示的持久化资源关系切换到需要
runtime summary 的视图，而九条本地 Devin summary 当前缺失，于是它们被过滤。点击不是删除
这九条 ACP summary 的根因，因为点击前它们已经不在 `acp.sessions`；点击只是触发了错误状态
被 UI 暴露出来。`resourceToSpace`、CLI session 记录和 transcript 是否在这次点击后保持不变，
仍需在 DevTools 关闭后用只读数据库/API 复核。

### 点击后的持久化复核

2026-09-20 在关闭 DevTools 后以只读方式复核，结果如下：

- CLI `sessions.db` 仍包含全部九条记录；每条 `working_directory` 都是
  `/Users/tr/Workspace/elder-oasis`，`workspace_dirs` 都是 `[]`，`hidden` 都是 `0`。
- Desktop `state.vscdb` 仍包含九条 `windsurf.acp.sessioninfo.session.acp/devin-cli/<id>`
  记录、九条 message-store 记录以及 summaryState 记录；记录中的 `cwd` 仍是
  `/Users/tr/Workspace/elder-oasis`。
- `windsurfSpace.resourceToSpace["1784977093276-9xndoc4fc"]` 仍包含全部九个目标资源：
  `accurate-opossum`、`garrulous-haddock`、`wistful-bowl`、`lumbar-success`、
  `foam-yamamomo`、`ember-foxtail`、`dear-reader`、`lemon-brook`、`lilac-lungfish`。
- 状态库中没有匹配到 `additionalWorkspaceDirs`，也没有发现目标会话被归档或隐藏。

因此“从 Space 面板消失”没有伴随持久化删除、归档、隐藏或 Space resource 移除；它是
Desktop runtime 的列表投影/筛选错误。当前最小根因表述应为：持久化 Space membership 和
Session 数据仍完整，但本地 Devin 会话缺失于 `acp.sessions`，而历史会话激活触发了面板从
持久化投影切换到 runtime-dependent 投影。

## Workaround 与 bug report 草稿

### 当前可逆 workaround

由于持久化数据完整，恢复 Space 列表不需要修改数据库：

1. 退出并重新启动 Devin Desktop；冷启动后的初始 Space 列表会重新显示完整成员。
2. 在同一运行周期内不要逐个点击受影响的本地 Devin 会话；点击其中一条可能触发其余本地
   Devin 会话批量隐藏。
3. 必须继续处理某个会话时，优先使用 Devin CLI，或先保存当前 Space 列表截图和只读状态，
   再进行受控点击。

该 workaround 只恢复 UI 投影，不修复 Desktop runtime；重启后再次点击仍可能复现。

### 可提交给 Devin 的 bug report 草稿

**标题**：Local Devin ACP sessions disappear from a Space after opening one historical session

**环境**：Devin Desktop 3.10.23，macOS；provider `devin-cli`，local workspace
`/Users/tr/Workspace/elder-oasis`。

**复现步骤**：

1. 冷启动 Devin Desktop，打开包含 9 个本地 Devin 会话和 4 个 ACP 会话的 `Elder Oasis` Space。
2. 等待初始加载完成；不点击任何会话时，全部成员保持可见。
3. 点击 `Elder Oasis: Taro Init & Admin Form`（Session ID `accurate-opossum`）。
4. 观察 Space 列表数秒。

**实际结果**：被点击的会话先消失，随后其余本地 Devin 会话也从 Space 列表消失；
`订阅消息通知 & 下拉刷新` 和 4 个 ACP 会话仍可见。Explorer 仍只显示
`/Users/tr/Workspace/elder-oasis`。

**预期结果**：打开会话不应改变 Space membership，也不应隐藏仍存在的本地 Devin 会话。

**持久化排除证据**：点击后 CLI `sessions.db` 仍保留 9 条记录，`workspace_dirs=[]`、
`hidden=0`；Desktop `sessioninfo`、message-store 和 Space `resourceToSpace` 均仍保留。
因此问题发生在 Desktop runtime session summary 与 Space 列表投影之间，而不是数据删除或归档。

**附加 runtime 证据**：点击前 `__acpStore.acp.sessions` 共 221 条，但 9 个目标 Session ID
均不匹配；初始 Space 列表仍能显示它们。打开历史会话后触发 reconciliation，随后列表按缺失
的 runtime summary 过滤这些持久化资源。
