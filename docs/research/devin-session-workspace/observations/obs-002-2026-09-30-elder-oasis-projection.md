# Obs 002：3.10.48 Elder Oasis 投影分裂与固定 tab 恢复

## 1. 观察范围

- Devin Desktop：`3.10.48`
- Space：`Elder Oasis`
- Space ID：`1784977093276-9xndoc4fc`
- canonical workspace：`/Users/tr/Workspace/elder-oasis`

## 2. 证据来源分工

| 来源 | 本记录使用的内容 |
|---|---|
| 用户人工观察 | 点击会消失/不会消失的两类会话、Project 数量变化、清 tab 后再次恢复、此前 14-tab 现象 |
| 本机只读取证 | `resourceToSpace` 的 15 个资源、两个 `sessioninfo` 的 provider/cwd、Elder Oasis `editorStates` 中的两个 tab |
| Desktop 日志 | 两个资源相邻的 `session_load_local`、`cache_hydrate` 和 `replay_apply` 时序 |
| 研究判断 | 对 Project/sidebar 与 Space/editor-state 分层，以及待验证的恢复/计数假设 |

## 3. 用户观察

### 3.1 旧批量消失行为的变化

此前点击会触发批量隐藏消失的会话，当前不再触发批量消失；但点击其中一个后，Project 会话数量减少 1。

### 3.2 固定 tab 恢复

点击会话时，编辑器会自动打开：

- `01a010dd-06c9-7d70-aa94-68715d05eeae`
- `unequaled-feverfew`

手动清空编辑器 tab 后，下一次点击仍会重新打开这两个会话。

### 3.3 新增对照观察

补充观察显示：点击那些原本不会消失的会话，也会联动打开同样的两个会话。

这条对照证据很关键：两个固定 tab 不再像是“批量消失会话集合”的副作用，也不像只由被点击的某个会话触发；它们更符合 Space 级 editor-state / layout 恢复集合。

### 3.4 关联的先前观察

在同一版本的较早观察中，Elder Oasis 内新建会话曾自动打开约 14 个 tab，集合看起来覆盖此前 Space 中的多个 Devin 会话、ACP 会话，以及原本没有随批量消失的 `lemon-brook`。这条记录与本次“无论点击哪类会话都恢复两个固定 tab”共同说明：侧边栏可见集合和 editor tab 恢复集合不是同一个集合。

## 4. 本机直接证据

### 4.1 Space membership

只读读取 `windsurfSpace.resourceToSpace`：Elder Oasis 当前有 15 个资源，其中包含：

```text
vscode-cascade-editor:///cascade-acp/codex-acp/01a010dd-06c9-7d70-aa94-68715d05eeae
vscode-cascade-editor:///cascade-acp/devin-cli/unequaled-feverfew
```

### 4.2 Session info

| 资源 | provider | cwd | 当前信息 |
|---|---|---|---|
| `01a010dd-06c9-7d70-aa94-68715d05eeae` | `codex-acp` | `/Users/tr/Workspace/elder-oasis` | Desktop sessioninfo 可读；标题为“补建缺失的云数据库索引” |
| `unequaled-feverfew` | `devin-cli` | `/Users/tr/Workspace/elder-oasis` | Desktop sessioninfo 可读；标题为 `feat: 家庭组公告` |

### 4.3 Editor state

只读读取 `windsurfSpace.editorStates`：Space `1784977093276-9xndoc4fc` 的 `serializedGrid` 中，两个资源都作为 `workbench.input.cascadeEditor` 保存；因此 UI 清 tab 后再次恢复与持久化布局重放相符。

### 4.4 Runtime log

最新操作日志记录两个资源在相邻时间被加载：

- `01a010dd...`：`session_load_local` 于 `04:12:32Z` 开始；
- `unequaled-feverfew`：同一时间窗开始 `session_load_local`；
- 随后分别出现 `cache_hydrate` 和 `replay_apply`。

这证明它们被运行时联动加载，但尚未定位具体 reducer、恢复入口或布局选择器。

## 5. 分层判断

### 已确认

1. Project 计数减少 1 不等于 Space membership 删除；两个资源在当前 `resourceToSpace` 中仍存在。
2. 两个固定 tab 在 Space 级 editor state 中有持久化记录。
3. “会消失”和“不会消失”的会话都会触发这两个 tab 恢复，因此恢复集合是更高层级的 Space/layout 行为。
4. `unequaled-feverfew` 的 Space 归属出现历史快照与当前快照不一致：修复时曾是独立 Space，当前已在 Elder Oasis。

### 高度疑似

- 3.10.48 将旧的批量侧边栏异常转化为更窄的 Project 侧投影漂移：点击后当前 Project 可见/已物化集合减少，但 Space membership 未被同步删除。
- editor-state 在进入或激活 Elder Oasis 时重放固定资源集合，手动关闭 tab 不会改变该集合，或会被随后一次布局保存/恢复重新写回。

### 尚未证明

- Project 数量减少 1 的具体 reducer、分页游标、retention 或缓存原因；
- 点击后是 Project 列表移除、session summary retire/evict，还是仅前端过滤变化；
- `unequaled-feverfew` 何时、由哪个操作从独立 Space 进入 Elder Oasis；
- editor state 是用户清 tab 后未落盘，还是 Desktop 在后续交互中主动恢复旧布局。

## 6. 当前处置

本观察期间不修改：

- `windsurfSpace.resourceToSpace`；
- `windsurfSpace.editorStates`；
- Project/sidebar 运行时缓存；
- 任一会话的 cwd、额外 workspace 或 hidden 字段。

下一次受控复现必须在一次点击前后同时保存：

1. Project 可见集合与计数；
2. Space `resourceToSpace`；
3. Elder Oasis `editorStates`；
4. 时间窗内的 `session_load_*`、`cache_hydrate`、`retention_*`、`replay_apply`。
