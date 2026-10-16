# Devin Desktop Session ↔ Workspace 规范台账

> 维护入口：本文件只放规范化登记、关系摘要和事件索引。详细证据进入 `cases/`、`observations/` 和 `inventory/`。

## 1. 范围与状态定义

本台账覆盖：

- Devin Desktop 中的 local `devin-cli` 会话；
- 出现在 Devin Desktop Space/resource 映射中的 Cursor/Codex ACP 或 Devin Cloud 资源；
- 已读取、修复或受控复现过 cwd、额外 workspace、Space membership、侧边栏或 editor-state 问题的对象。

状态含义：

| 状态 | 含义 |
|---|---|
| 已登记 | 已有用户输入或本机证据，但没有执行迁移/修复 |
| 已修复 | 精确修改已执行，并有备份、完整性或读回证据 |
| 观察中 | 持久化字段已核对，但运行时行为仍需复现或分层解释 |
| 待授权 | 需要修改 canonical cwd、provider 侧会话或 Space membership |

## 2. 会话登记

canonical workspace：`/Users/tr/Workspace/elder-oasis`

| Session ID | 名称 | provider / 存储源 | Desktop 版本 | canonical cwd | 额外目录状态 | Space / 预期 | 状态 |
|---|---|---|---|---|---|---|---|
| `accurate-opossum` | Elder Oasis: Taro Init & Admin Form | devin-cli / sessions.db + state.vscdb | 3.10.23；3.10.48 观察 | elder-oasis | 已清理 `yr` | Elder Oasis | 已修复；运行时投影观察中 |
| `garrulous-haddock` | Elder端游戏大厅 & 禅意游走 & 反诈模拟 & 文案优化 | devin-cli | 3.10.23 | elder-oasis | 已清理 `yr` | Elder Oasis | 已修复；运行时投影观察中 |
| `wistful-bowl` | 订阅消息通知 & 下拉刷新 | devin-cli | 3.10.23；3.10.48 | elder-oasis | 已清理 `yr` | Elder Oasis | 已修复；3.10.48 行为已改变 |
| `lumbar-success` | 数据库运维 | devin-cli | 3.10.23 | elder-oasis | 已清理 `yr` | Elder Oasis | 已修复；运行时投影观察中 |
| `foam-yamamomo` | Elder 端留存提升：7天签到日历与锦鲤转盘开发 | devin-cli | 3.10.23 | elder-oasis | 已清理 `yr` | Elder Oasis | 已修复；曾为污染源 |
| `ember-foxtail` | 可视化数据看板 & 防骗答题交互 | devin-cli | 3.10.23 | elder-oasis | 已清理 `yr` | Elder Oasis | 已修复；曾为污染源 |
| `dear-reader` | 答题重构 & DART 保健品防诈故事生成 | devin-cli | 3.10.23 | elder-oasis | 已清理 `yr` | Elder Oasis | 已修复；曾为污染源 |
| `lemon-brook` | 商品信息编辑优化&接入京东联盟 | devin-cli | 3.10.23；3.10.48 | elder-oasis | 已清理 `yr` | Elder Oasis | 已修复；tab 集合对照 |
| `lilac-lungfish` | 仓库内 Agent 配置 | devin-cli | 3.10.23 | elder-oasis | 已清理 `yr` | Elder Oasis | 已修复；曾为污染源 |
| `unequaled-feverfew` | feat: 家庭组公告 | devin-cli | 3.10.48 | elder-oasis | `workspace_dirs=[]`；Desktop 额外目录已清理 | 修复时独立；当前映射为 Elder Oasis | 已修复；Space 归属变化观察中 |
| `meadow-quiet` | 老人每日牛奶打卡功能设计 | devin-cli | 3.10.48 | `/Users/tr/Workspace/yr` | `workspace_dirs=[]` | 预期 elder-oasis | 已登记；待授权迁移 |
| `e31f1232-be30-447c-881b-bee806a15ccc` | Elderly Access Code | Cursor ACP / `~/.cursor/acp-sessions` | 3.10.48 | `/Users/tr/Workspace/project-sprout` | 不适用 Devin CLI 字段 | 预期 elder-oasis | 已登记；待 ACP 侧迁移/重建决定 |

## 3. 问题分类

| 分类 | 识别条件 | 样本 | 处理边界 |
|---|---|---|---|
| canonical cwd 创建错误 | 主 `working_directory` / Desktop `cwd` 就是错误项目 | `meadow-quiet`、Cursor ACP `e31f...` | 不清空额外字段冒充迁移；需 provider 侧迁移或重建授权 |
| 额外目录持久化污染 | canonical cwd 正确，但 CLI `workspace_dirs` 或 Desktop `additionalWorkspaceDirs` 含错误目录 | Case 001 九个会话、`unequaled-feverfew` | 备份后按精确旧值清理两层额外字段 |
| Space 聚合继承 | 会话自身字段干净，但同 Space 资源把目录并入 Explorer/工作目录投影 | `accurate-opossum` 的 3.10.23 复现 | 调查同 Space 污染源；不 Ungroup 或改 membership |
| Project/侧边栏投影异常 | Project 数量、列表可见性或懒加载与持久化资源不一致 | Elder Oasis、yr Project | 先做前后快照；不把 UI 消失写成删除 |
| editor-state/tab 投影异常 | 清空 tab 后固定资源再次恢复，或新建会话打开历史资源集合 | Elder Oasis 的两个固定资源、14-tab 现象 | 先读取 `windsurfSpace.editorStates`；暂不直接删除布局状态 |

## 4. 事件索引

| ID | 日期 / 版本 | 事件 | 文档 | 结论状态 |
|---|---|---|---|---|
| Case 001 | 2026-09-19 至 2026-09-30；3.10.23/3.10.48 | Elder Oasis 会话出现 `yr` 额外目录，完成精确清理 | [case-001](cases/case-001-elder-oasis-extra-yr.md) | 修复完成；运行时问题分离 |
| Obs 001–002 | 2026-09-29 至 2026-09-30；3.10.48 | `wistful-bowl` 加入批量隐藏；新建会话打开 14 个 tab；消失与不消失会话都会打开同两个固定 tab；Project 点击后计数 -1 | [Elder Oasis 投影观察](observations/obs-002-2026-09-30-elder-oasis-projection.md) | Space 级 editor-state 恢复假设增强；Project 计数因果待验证 |
| Obs 003 | 2026-09-30；3.10.48 | 批量消失在某些条件下仍会出现；重点转向加载风暴、retention 与侧边栏可见性 | [条件性批量消失](observations/obs-003-2026-09-30-conditional-sidebar-disappearance.md) | 当前最高优先级；规避和触发矩阵待验证 |
| Obs 004 | 2026-09-30；3.10.48 | 冷启动、不滚动、单击 `accurate-opossum` 后，Project/Space 可见集合出现 `15 → 14 → 7`；最新日志显示批量加载与 replay，但无 `retention_evict`；持久化资源仍为 15 | [冷启动单击直接复现](observations/obs-004-2026-09-30-cold-start-single-click-repro.md) | 已直接复现；内存压力降级为伴随信号；具体投影过滤原因待查 |
| Obs 005 | 2026-09-30；3.10.48 | 重开窗口、不滚动、单击此前相对稳定的 `wistful-bowl` 后，Elder Oasis Space 可见列表再次 `14 → 6`；最新日志为 422 次 replay 对、8 次 `retention_retire`、0 次 `retention_evict`；持久化资源仍为 15 | [第二轮稳定会话复现](observations/obs-005-2026-09-30-stable-session-second-repro.md) | 已复现第二个点击目标；问题上升为 Space 级加载/投影路径；入口对照待做 |
| Obs 006 | 2026-09-30；3.10.48 | 全局 Sessions 可显示 Space 侧边栏已隐藏的 Elder Oasis 会话；标题搜索和 Session ID `accurate-opossum` 搜索均得到目标并可打开；打开后未新增批量加载日志 | [全局 Sessions 搜索回退](observations/obs-006-2026-09-30-global-sessions-search-fallback.md) | 回退入口可行；不修复 Space 投影或 membership |
| Snapshot 001 | 2026-09-30 | CLI/Desktop/ACP/Space 关系盘点 | [snapshot](inventory/2026-09-30-relation-snapshot.md) | 只读快照 |

## 5. 已知修复与备份

Case 001 的 `unequaled-feverfew` 修复备份：

```text
~/.local/share/devin/cli/repair-backup-20260930T021200+0800-unequaled-feverfew
```

修复原则：

- Devin Desktop 和 CLI 必须完全退出；
- 先备份 SQLite 主库及 WAL/SHM sidecar；
- 以 Session ID、provider、canonical cwd 和精确旧值做 fail-closed guard；
- 不修改 `working_directory`、标题、model、provider、`hidden` 或 Space membership；
- 修改后做 SQLite integrity check 和字段读回。

## 6. 后续新增输入格式

新增样本至少记录：

```text
session_id:
title:
provider / storage:
desktop_version:
canonical cwd:
CLI workspace_dirs:
Desktop additionalWorkspaceDirs:
Space ID / resource URI:
Project/sidebar observation:
editor-state observation:
evidence source:
requested action:
```
