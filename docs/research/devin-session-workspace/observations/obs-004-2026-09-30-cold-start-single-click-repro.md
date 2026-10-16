# Obs 004：3.10.48 冷启动后单击 Elder Oasis 会话的直接复现

## 1. 实验目的

验证“Elder Oasis 会话批量从侧边栏消失”是否仍能在当前 Devin Desktop 版本中复现，并区分：

- UI/Project/Space 投影变化；
- Space 资源持久化变化；
- editor-state 变化；
- 当前运行时的加载、replay 和 retention 事件。

本实验只做一次 UI 点击，不修改 Devin 数据库、Space membership 或 editor-state。

## 2. 实验条件

- Devin Desktop：`3.10.48`，由 `/Applications/Devin.app/Contents/Info.plist` 读回；
- 冷启动后等待初始列表稳定；
- 未滚动侧边栏；
- 未新建会话；
- 未清空 editor tab；
- Explorer 初始只显示 `/Users/tr/Workspace/elder-oasis`，没有 `/Users/tr/Workspace/yr`；
- 初始 Project `elder-oasis` 显示数量 `15`，其 Elder Oasis Space 列表可见 14 个会话；
- 唯一 UI 动作：点击 `Elder Oasis: Taro Init & Admin Form`。

## 3. UI 结果

点击后观察到两个阶段：

1. 约 4 秒后的增量状态中，Project `elder-oasis` 从 `15` 变为 `14`；
2. 随后的完整无差分状态中，Project `elder-oasis` 变为 `7`，Elder Oasis Space 中可见会话由 14 个变为 6 个。

最终仍可见的 6 个 Space 会话是：

- 商品信息编辑优化&接入京东联盟
- Sudoku Game Dev
- Lianliankan Game Dev
- New User Onboarding
- feat: 家庭组公告
- 完善禅意游走小游戏体验

被点击的会话以及另外 7 个 Elder Oasis 会话从侧边栏投影中消失。被点击会话的内容仍然打开，编辑区出现 3 个 tab：Space context、`feat: 家庭组公告` 和 `Elder Oasis: Taro Init & Admin Form`。

## 4. 持久化结果

点击后的只读快照显示：

- `windsurfSpace.resourceToSpace` 中 Elder Oasis Space 仍有 15 个资源；
- `accurate-opossum`、`garrulous-haddock`、`wistful-bowl`、`lumbar-success`、`unequaled-feverfew` 等资源仍在映射中；
- `windsurfSpace.editorStates` 仍包含 `01a010dd-06c9-7d70-aa94-68715d05eeae` 和 `unequaled-feverfew`；
- 因此 Project/Space 侧边栏数量下降没有对应的持久化 resource membership 删除。

## 5. 最新日志结果

本次实验对应的最新日志是：

```text
/Users/tr/Library/Application Support/Devin/logs/20260930T132331/window1/desktop-operations.log
```

日志时间为 UTC `05:25:03` 起，即本地时间约 `13:25:03`。点击后短时间内出现一轮 Space 级批量加载：

- `session_load_local` 同时覆盖 `01a010dd...`、`unequaled-feverfew`、`accurate-opossum`、多个其它 Devin 会话以及 3 个 Cursor ACP 和另一个 Codex ACP；
- 随后出现大量 `replay_apply`，当前日志共 428 次 before/completed 对；
- 出现 8 次 `retention_retire`，但没有 `retention_evict`；
- `heap_used` 峰值约 731 MB，而不是旧日志中约 2 GB 的高峰。

## 6. 结论边界

### 本样本直接确认

- 当前 `3.10.48` 仍可在“冷启动后不滚动、只点击一次”的条件下复现批量消失；
- 触发动作会引起 Space 级资源加载和大量 replay，而不是只加载被点击会话；
- UI 从 Project/Space 列表中移除资源时，`resourceToSpace` 仍保留 15 个资源；
- 高内存峰值和 `retention_evict` 不是本次复现的必要条件。

### 仍未确认

- 是哪一个列表过滤、hydration 失败或 UI projection reducer 导致 8 个对象暂时不可见；
- `retention_retire` 是原因、结果，还是与 UI 消失并行的清理动作；
- 为什么这 8 个对象被选中，而另外 6 个会话仍保留；
- 点击其它 Elder Oasis 会话是否得到同样的 `15 → 14 → 7` 序列。

因此，旧样本中的“加载风暴叠加内存压力”应降级为伴随信号；当前更稳妥的描述是：

```text
Elder Oasis 单次会话打开
  → Space 级 session_load_local + replay_apply
  → Project/Space 侧边栏投影批量收缩
  ≠ resourceToSpace 持久化删除
```

## 7. 回退入口说明

- 本次建议的全局入口首先指 Devin Desktop 的 `Sessions` 页面或搜索，不是 Cursor CLI。
- Devin CLI 实际支持 `devin -r <SESSION_ID>` 恢复会话，也支持 `devin list` 查看当前目录下的会话；这是本地 Devin 会话的可用回退路径。
- Codex CLI 有 `codex resume`，但当前没有把 Devin Desktop 中的 Codex ACP ID 与 Codex CLI 可恢复 ID 做过一一映射，不能直接假定两者互通。
- Cursor 当前只有 ACP Server 能被 Devin Desktop 使用；其 Desktop 不加载这些 ACP 会话，因此不能把 Cursor Desktop 当作本实验的回退入口。
