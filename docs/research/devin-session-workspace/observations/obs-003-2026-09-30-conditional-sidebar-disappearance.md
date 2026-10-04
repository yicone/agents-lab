# Obs 003：Elder Oasis 条件性批量会话消失

## 1. 调查目标

本观察以开发体验为目标：找出 Elder Oasis Space 中会话从侧边栏批量消失的触发条件、影响边界和可用规避方式。

它不是为 Devin 整理缺陷报告，也不以修改 Devin 数据为目标。优先级高于 `yr` 目录污染和固定 tab 视觉问题，因为它直接破坏会话可达性。

## 2. 当前用户观察

- 批量消失并非已经完全停止；在某些条件下仍会出现。
- 之前观察到的典型表现是：点击某个 Elder Oasis 会话后，先打开目标内容，随后 Space 中多个会话延迟消失。
- 消失不是稳定绑定某一个 Session ID；在不同状态下，`wistful-bowl` 等会话的表现也会改变。
- `resourceToSpace`、sessioninfo、CLI 数据和 transcript 不应因侧边栏消失而假定被删除。

当前还缺少本次最新复现的精确动作序列：冷启动/热状态、是否先滚动、点击的具体会话、Project 分组状态、是否有新建会话或 editor tab 恢复。后续结论必须把这些作为实验变量记录。

## 3. 已有受控证据

### 3.1 Space membership 未随消失删除

历史受控复现中，点击 `accurate-opossum` 后，Space 侧边栏中的多个会话消失；但 CLI 记录、Desktop `sessioninfo`、message-store、Space `resourceToSpace` 和 transcript 仍存在，且 local Devin 会话 `hidden=0`。

因此当前工作定义是：

```text
“会话消失” = 侧边栏/运行时可见性丢失
“会话删除” = 持久化记录、message-store 或 Space membership 不存在
```

目前没有证据把两者等同。

### 3.2 加载风暴与 retention 时序

在已有 Desktop 操作日志
`/Users/tr/Library/Application Support/Devin/logs/20260929T161907/window1/desktop-operations.log`
中，`2026-09-29T14:39:02Z` 起出现以下时序：

| 时间段 | 运行时事件 | 观察意义 |
|---|---|---|
| 14:39:02 | `accurate-opossum`、`garrulous-haddock` 开始 `session_load_local` | 先加载当前/相邻 Devin 会话 |
| 14:39:26–14:39:27 | `foam-yamamomo`、`ember-foxtail`、`lemon-brook`、`lilac-lungfish`、`garrulous-haddock`、`wistful-bowl`、`lumbar-success` 相继开始加载 | Space 资源被批量物化，而不是只加载一个点击目标 |
| 14:39:53–14:39:54 | 9 个 Devin 会话、多个 ACP 资源再次开始加载 | 出现第二轮大规模加载 |
| 14:40:11–14:40:25 | 9 个 Devin 会话反复 `retention_retire`；部分资源还发生 `retention_evict` | 运行时缓存正在主动退休/驱逐刚加载的资源 |

这个时序与批量消失的用户体验高度相关，但日志没有记录一个名为“从 Space 列表删除”的事件。因此它支持“侧边栏投影依赖的运行时对象被 retire/evict 后失去可见性”的假设，尚不能证明精确因果。

### 3.3 固定 tab 不是消失会话的专属触发器

Obs 002 已记录：点击会消失和不会消失的会话，都会恢复 `01a010dd-06c9-7d70-aa94-68715d05eeae` 与 `unequaled-feverfew`。因此这两个 tab 更像 Space 级恢复布局中的固定资源。

当前应区分：

- **共同触发器候选**：激活 Elder Oasis 后的 Space 级资源批量加载；
- **负载放大器候选**：`editorStates` 恢复固定 tab，或恢复更大的历史 tab 集合；
- **失败表现**：Project/sidebar 使用的会话对象被 `retention_retire/evict` 后不再显示。

这仍是工作假设，不是已证明的 Devin 内部实现。

### 3.4 当前只读快照

在本轮检查时，Devin Desktop 未运行，因此没有进行新的点击或滚动操作；只读取了本机状态库：

- Space `1784977093276-9xndoc4fc` 的 `windsurfSpace.resourceToSpace` 仍包含 15 个资源，其中包括 `accurate-opossum`、`garrulous-haddock`、`wistful-bowl`、`lumbar-success`、`unequaled-feverfew` 以及 Cursor/Codex ACP 资源；
- 同一 Space 的 `windsurfSpace.editorStates` 仍包含 `01a010dd-06c9-7d70-aa94-68715d05eeae` 与 `unequaled-feverfew` 两个编辑器资源；
- 因而当前快照继续支持“侧边栏/运行时投影异常”而不是“持久化 Space membership 删除”。

这只是静态快照，不能替代用户所说的最新批量消失动作；下一轮需要在 Desktop 启动后按单变量实验记录点击前后集合。

### 3.5 额外信号：批量加载伴随明显堆内存增长

同一段历史日志还显示，第二轮批量加载期间 `heap_used` 从约 472 MB 上升到约 2.16 GB；在该窗口内记录到的峰值约为 2.34 GB。期间出现一次 `session_load_local` 失败，并在批量加载完成后集中出现 `retention_retire`。

这使“加载风暴导致运行时缓存/列表投影不稳定”比单纯的 Space membership 变更更值得优先验证。但目前只能把内存压力列为相关信号，不能据此断言是根因：日志没有记录 UI 列表 reducer 的具体移除原因，也没有把某次消失与某个 `retention_*` 事件做一一对应。

Obs 004 的新鲜冷启动复现中，批量消失发生在约 731 MB 的堆内存峰值下，且没有 `retention_evict`。因此本节的“内存压力”只能保留为旧样本伴随信号，不能作为当前复现的必要条件。

## 4. 当前最有用的工作假设

```text
Space 激活/点击
    ↓
Space 级批量 session_load_local
    + editorStates 额外恢复固定 tab
    ↓
加载和 replay 队列堆积 / retention 介入
    ↓
部分 session summary 被 retire/evict
    ↓
侧边栏列表失去可见对象
    ≠
持久化 Session 或 Space membership 被删除
```

这可以解释为什么：

1. 批量消失有时发生、有时不发生；
2. 点击不同类型会话可能得到不同结果；
3. 同一批会话过一段时间又可能重新出现；
4. editor tab 与侧边栏集合不一致；
5. Project 数量可能变化，但 `resourceToSpace` 仍保持资源。

## 5. 对当前开发体验的规避建议

在根因取证完成前，优先采用低破坏性的规避：

1. 不把 Elder Oasis Space 当作唯一会话入口；重要会话同时从全局 Sessions/搜索入口或其 provider 原生客户端打开。
2. 需要连续工作时，减少在 Elder Oasis 中反复滚动、切换和新建会话的次数；这些动作可能重复触发 Space 级物化。
3. 发现批量消失后，先不要执行 Ungroup、删除 Space、删除 tab 或修改会话目录；这些会改变证据层，不能恢复运行时缓存。
4. 对正在进行的工作保留会话 ID 和标题，必要时从 provider 入口重新打开；侧边栏消失本身不应被当成数据丢失。
5. 暂不直接删除 Elder Oasis 的 `editorStates`。如果后续验证“清空 Space 布局能显著降低复现率”，再在备份后做可回滚的 A/B 试验。

## 6. 下一轮最小复现实验

每轮只改变一个变量，并在点击前后保存四类快照：

| 快照 | 内容 |
|---|---|
| UI 投影 | Space/Project 计数、可见 Session ID、消失顺序、延迟时长 |
| Space 持久化 | `windsurfSpace.resourceToSpace` 的 Elder Oasis 条目 |
| 编辑器布局 | `windsurfSpace.editorStates` 的资源列表、MRU、tab 数 |
| 运行时 | 时间窗内 `session_load_*`、`cache_hydrate`、`replay_apply`、`retention_*` |

建议的最小矩阵：

| 实验 | 初始状态 | 动作 | 目的 |
|---|---|---|---|
| A | 冷启动、保持 Space 不滚动 | 点击一个稳定会话 | 验证仅激活/点击是否足以触发批量加载 |
| B | 冷启动、先滚动到 Space 底部 | 点击同一会话 | 验证分页物化是否是条件 |
| C | 清空 tab 后不新建会话 | 点击同一会话 | 验证 editor-state 放大器作用 |
| D | 不进入 Elder Oasis，直接从全局 Sessions 打开 | 打开同一会话 | 验证 Space 入口是否是触发器 |
| E | 保持其它 Space 不动 | 点击 ACP 与 Devin 会话各一次 | 区分 provider 是否改变加载/retention 行为 |

## 7. 当前结论边界

### 已确认

- 侧边栏消失不能作为持久化删除证据。
- 历史日志存在 Elder Oasis 多轮批量加载后的大量 `retention_retire`，部分伴随 `retention_evict`。
- 固定两个 tab 的恢复与“会消失/不会消失”类别无关。

### 尚未确认

- 哪个具体 UI 动作构成当前最新复现的必要条件；
- `retention_retire/evict` 是否直接造成 sidebar 消失，还是与其并行发生；
- Project 计数变化是否来自同一个缓存/物化机制；
- 清空或隔离 editor-state 是否能降低复现率。
