# Devin Desktop Session ↔ Workspace 调查档案

本目录是 Devin Desktop / Devin CLI / ACP 会话与工作目录关系的调查、修复和回归观察档案。
它把“会话本身的持久化字段”“Space 成员关系”“Project/侧边栏投影”和“编辑器 tab 恢复”分开记录，避免用单一 UI 现象推导其它层的状态。

## 当前入口

- [规范台账](ledger.md)：当前会话登记、关系字段、处理状态和事件索引。
- [Case 001：Elder Oasis / `yr` 额外目录](cases/case-001-elder-oasis-extra-yr.md)：已执行修复、备份和验证。
- [观察 002：3.10.48 Elder Oasis 投影分裂](observations/obs-002-2026-09-30-elder-oasis-projection.md)：Project 计数、Space 成员和固定 tab 恢复的回归观察。
- [观察 005：第二轮稳定会话复现](observations/obs-005-2026-09-30-stable-session-second-repro.md)：点击 `wistful-bowl` 后 Space 可见列表再次 `14 → 6`，但持久化资源仍为 15。
- [观察 006：全局 Sessions 搜索回退](observations/obs-006-2026-09-30-global-sessions-search-fallback.md)：可用标题或 Desktop 的 Session ID 找回并打开已从 Space 侧边栏消失的会话。
- [2026-09-30 关系快照](inventory/2026-09-30-relation-snapshot.md)：CLI、Desktop、ACP 和 Space 的只读盘点。
- `tools/` 与 `tests/`：修复工具及其测试的预留位置；本次重构只整理文档和证据，不改变修复脚本。

## 目录约定

```text
devin-session-workspace/
├── README.md
├── ledger.md                 # 规范台账与当前状态
├── cases/                    # 已执行特别处理或受控修复案例
├── observations/             # 回归、复现和运行时投影观察
├── inventory/                # 某一时点的只读关系快照
├── tools/                    # 只读检查与经授权的精确修复工具
└── tests/                    # 修复工具测试
```

## 证据层级

每条记录必须标记来源：

| 来源 | 含义 | 允许得出的结论 |
|---|---|---|
| 用户观察 | Desktop UI 行为、点击顺序、计数或截图 | 记录现象和复现条件；不能单独证明数据库变更 |
| 本机持久化读取 | `sessions.db`、`state.vscdb`、ACP `meta.json` | 证明字段或映射在读取时的实际值 |
| 运行时日志 | `session_load_*`、`cache_hydrate`、`retention_*`、`replay_apply` | 证明操作时序；没有直接因果链时标为相关性 |
| 推断 | 跨层解释或根因假设 | 必须写明依据、反证条件和待验证动作 |

## 关系模型

调查时分别记录以下关系，不做隐式合并：

```text
provider/storage
      │
      ├── canonical cwd / working_directory
      ├── extra workspace fields
      └── sessioninfo / transcript

Space ── resource membership ── session resource
Project / sidebar ── runtime projection ── session resource
Space ── editorStates / serializedGrid ── editor tabs
```

## 维护规则

1. 新样本先进入规范台账的“登记”状态，不自动迁移 canonical cwd。
2. 修复前必须核对 Session ID、标题、provider、canonical cwd、额外目录字段和 Space membership。
3. 只删除精确匹配的额外目录字段；不以修复工作区污染为由修改 Space membership。
4. Project 计数、Space 数量、侧边栏可见性和 editor tab 数量必须分别记录。
5. 任何“消失”都先标为运行时投影现象，直到持久化删除或 membership 变化被直接读到。
6. 案例文档记录事实和处理；观察文档记录事件与假设；快照文档记录某一时刻的原始盘点。
