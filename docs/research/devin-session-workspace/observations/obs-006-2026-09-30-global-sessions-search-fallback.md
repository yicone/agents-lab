# Obs 006：全局 Sessions 搜索可找回已从 Space 侧边栏消失的会话

## 1. 实验目的

验证“重要会话优先从全局 Sessions 搜索入口打开”是否可作为 Elder Oasis 批量消失期间的实际规避路径，并确认用户从 Desktop 的 “Copy Session ID” 得到的短 ID 是否可直接搜索。

## 2. 实验条件

- Devin Desktop：`3.10.48`；
- Elder Oasis Space 当时的侧边栏可见列表已处于异常后的 6 条状态；
- 全局 `Sessions` 页面仍可访问；
- 没有修改 Space、会话、membership 或 editor-state。

## 3. UI 验证结果

### 3.1 全局 Sessions 能显示被 Space 侧边栏隐藏的会话

打开全局 `Sessions` 页面后，Elder Oasis 项目分组中仍能看到此前从 Space 侧边栏消失的 Devin 会话，包括：

- Elder Oasis: Taro Init & Admin Form
- Elder端游戏大厅 & 禅意游走 & 反诈模拟 & 文案优化
- 订阅消息通知 & 下拉刷新
- 数据库运维
- 答题重构 & DART 保健品防诈故事生成
- 仓库内 Agent 配置
- Elder 端留存提升：7天签到日历与锦鲤转盘开发
- 可视化数据看板 & 防骗答题交互

这与 Space 侧边栏当时仅显示 6 条形成对照：全局 Sessions 的项目分组仍保留更完整的会话可达性。

### 3.2 标题搜索可用

在全局搜索框输入：

```text
Elder Oasis: Taro Init & Admin Form
```

结果中出现目标会话，并可点击打开。

### 3.3 Session ID 搜索可用

清空标题搜索后输入：

```text
accurate-opossum
```

搜索结果为唯一会话：

```text
Elder Oasis: Taro Init & Admin Form
```

点击该结果后，目标会话内容正常打开。此验证直接覆盖用户从 Devin Desktop “Copy Session ID” 得到的标识使用方式。

## 4. 对 Space 异常的影响

从全局搜索打开目标会话后：

- Elder Oasis Space 侧边栏仍保持异常后的 6 条；
- 目标会话不会因为全局打开而自动重新出现在 Space 侧边栏；
- 目标内容仍然可继续查看和操作；
- 没有观察到新的批量侧边栏收缩。

因此，全局入口解决的是“会话可达性”，不是“Space membership 或侧边栏投影修复”。

## 5. 日志对照

点击全局搜索结果后重新读取最新日志：

```text
/Users/tr/Library/Application Support/Devin/logs/20260930T132331/window1/desktop-operations.log
```

日志末尾仍为周期性的 `ingest_flush`；在该点击之后没有新增：

- `session_load_local`；
- `replay_apply`；
- `retention_retire`；
- `retention_evict`；
- `cache_hydrate`。

这说明本次打开没有再次触发一轮可观测的 Space 级批量加载。不过目标会话此前已经在 Obs 004/005 的批量加载中物化，因此不能据此证明“全局入口在完全冷启动、未预加载条件下永远不会触发批量加载”。

## 6. 实际建议

### 可以采用

对重要会话，尤其是已经从 Space 侧边栏消失的会话，优先使用：

1. 左侧 `Sessions`；
2. 搜索框输入 Desktop 的 Session ID；
3. 确认唯一结果的标题；
4. 点击打开并继续工作。

### 不应误解为

- 它不会修复 Space 的可见列表；
- 它不会把会话重新加入 Space；
- 它不会清理错误的 workspace 目录；
- 它不能证明 Space 内点击路径已经安全。

当前结论是：全局 Sessions 搜索是可靠的回退入口和工作规避路径，Space 侧边栏仍需单独排查和修复。
