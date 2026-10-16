# Obs 005：3.10.48 重开窗口后单击 `wistful-bowl` 的第二轮复现

## 1. 实验目的

验证 Obs 004 的批量消失是否只由 `accurate-opossum` 或某一个“问题会话”触发。
本轮改用此前在用户观察中相对稳定的 Devin 会话 `wistful-bowl`，并继续分离：

- Space 列表的可见投影；
- `resourceToSpace` 持久化 membership；
- editor-state 中的固定 tab；
- session load、replay 和 retention 的运行时相关事件。

本轮没有写入 Devin 数据库、Space membership 或 editor-state。

## 2. 实验条件

- Devin Desktop：`3.10.48`；
- 重新打开 Devin 窗口并进入 Sessions 列表后，先记录 Elder Oasis 的初始列表；
- 未滚动侧边栏；
- 未新建会话；
- 未清空 editor tab；
- 初始 Elder Oasis Space 可见 14 个会话；
- 唯一 UI 动作：点击 `订阅消息通知 & 下拉刷新`（`wistful-bowl`）。

这里将“重开窗口后的新列表”称为第二轮受控样本；本记录不把它强行等同于已证明的应用进程级 cold start。

## 3. UI 结果

点击 `wistful-bowl` 后等待约 5 秒，Elder Oasis Space 的可见列表再次从 14 个缩为 6 个。
仍可见的 6 个会话是：

- 商品信息编辑优化&接入京东联盟
- Sudoku Game Dev
- Lianliankan Game Dev
- New User Onboarding
- feat: 家庭组公告
- 完善禅意游走小游戏体验

本次被点击的 `wistful-bowl` 以及其它 7 个会话从该 Space 的侧边栏可见投影中消失，但点击目标的内容仍在编辑区打开。编辑区同时保留 Space context 和此前恢复出的 `feat: 家庭组公告` tab。

本轮未记录 Project 数字计数，因此不把 `Project 15 → 14 → 7` 的序列外推到本样本；本轮确认的是 Space 内可见列表 `14 → 6`。

## 4. 持久化结果

点击后的只读读取显示：

- `windsurfSpace.resourceToSpace` 中 Elder Oasis Space ID `1784977093276-9xndoc4fc` 仍有 **15 个资源**；
- 该数组仍包含 `wistful-bowl`、`accurate-opossum`、`garrulous-haddock`、`lumbar-success`、`unequaled-feverfew`，以及 3 个 Cursor ACP 和 2 个 Codex ACP 资源；
- `windsurfSpace.editorStates` 仍包含 `01a010dd-06c9-7d70-aa94-68715d05eeae` 和 `unequaled-feverfew` 这两个固定资源；
- 因此，本轮 UI 可见数 `14 → 6` 没有对应的 `resourceToSpace` membership 删除。

## 5. 最新日志结果

本轮对应的是最新日志文件中的 `05:25:03.659Z–05:25:42.344Z` UTC 段，而不是之前使用的旧日志段：

```text
/Users/tr/Library/Application Support/Devin/logs/20260930T132331/window1/desktop-operations.log
```

该时间段记录：

- `cache_hydrate`：2 次 before/completed 对；
- `session_load_local`：22 次 before/completed 对，覆盖 `wistful-bowl`、`accurate-opossum`、`garrulous-haddock`、其它 Devin 会话，以及 Cursor/Codex ACP 资源；
- `replay_apply`：422 次 before/completed 对；
- `retention_retire`：8 次 before/completed 对，其中包括 `wistful-bowl`、`garrulous-haddock`、`lumbar-success` 等会话；
- `retention_evict`：0 次；
- `heap_used` 峰值：671,349,527 bytes，约 640 MB。

日志证明的是批量加载、replay 和 retire 在时间上相关；它没有直接证明某个 retire 动作删除了 Space membership。

## 6. 与 Obs 004 的合并判断

### 当前已直接复现的样本

| 样本 | 点击目标 | UI 现象 | `resourceToSpace` | `retention_evict` |
|---|---|---|---:|---:|
| Obs 004 | `accurate-opossum` | Project/Space 可见集合最终 `15 → 14 → 7`，Space `14 → 6` | 仍为 15 | 0 |
| Obs 005 | `wistful-bowl` | Space 可见集合 `14 → 6` | 仍为 15 | 0 |

这两轮足以否定“只有 `accurate-opossum` 自身损坏才触发”的解释，也说明此前相对稳定的 `wistful-bowl` 在 3.10.48 的这条加载路径上不再稳定。

### 当前工作模型

```text
打开 Elder Oasis 中任一 Devin 会话
  → 触发同一 Space 的批量 session_load_local
  → 大量 replay_apply，并伴随部分 retention_retire
  → Project/Space 侧边栏可见投影收缩
  ≠ resourceToSpace 持久化删除
```

这仍是分层证据支持的工作模型，不是已经定位到具体 reducer、过滤条件或根因的结论。当前样本数为 2，足以改变排查方向，但不足以声称 Elder Oasis 的每一个会话、每一种入口都必然触发相同结果。

## 7. 已完成的入口对照与下一步

已完成全局入口对照：全局 `Sessions` 能看到并搜索 Space 侧边栏已经隐藏的会话；输入 `accurate-opossum` 能得到唯一目标并正常打开。该结果单独记录于 [Obs 006](obs-006-2026-09-30-global-sessions-search-fallback.md)。

下一轮若继续做因果隔离，优先在真正冷启动且尚未预加载会话时比较入口，而不是继续增加任意点击样本：

1. 从全局 `Sessions`/搜索入口直接打开同一 Devin 会话；
2. 记录是否仍发生 Space `14 → 6`；
3. 再从 Elder Oasis Space 内打开另一条会话；
4. 对比两次的 `session_load_local`、`replay_apply`、`retention_*` 和持久化快照。

这能区分“会话本身触发”与“从 Space 投影进入时触发”，比单纯增加点击数量更有信息量。
