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
- `tools/` 与 `tests/`：修复工具 `repair_session_workspace.py` 及其测试（见下方“修复工具使用流程”）。

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

## 修复工具使用流程

### 1. 只读调查

```bash
python3 docs/research/devin-session-workspace/tools/repair_session_workspace.py inspect \
  --session accurate-opossum \
  --session garrulous-haddock \
  --session wistful-bowl \
  --session lumbar-success
```

`inspect` 不写入任何数据，并同时显示 CLI 来源与 Desktop 镜像。

### 2. 预备修复

先退出 Devin Desktop 和 Devin CLI。修复工具会拒绝修改仍被 `lsof` 持有的数据库。

```bash
python3 docs/research/devin-session-workspace/tools/repair_session_workspace.py repair \
  --session accurate-opossum \
  --session garrulous-haddock \
  --session wistful-bowl \
  --session lumbar-success \
  --expect-cwd /Users/tr/Workspace/elder-oasis \
  --expect-extra-json '["/Users/tr/Workspace/yr"]' \
  --set-extra-json '[]'
```

未加 `--apply` 时只进行校验和打印计划，不写数据库。确认计划无误后，再加 `--apply`。
工具会备份数据库及存在的 `-wal` / `-shm` sidecar，并生成 SHA-256 manifest。

修复后重新启动 Devin，逐一打开目标 Session，确认 Explorer 只显示预期目录；随后再次运行
`inspect` 做存储层回读。

### 3. 回退边界

备份目录是恢复入口。不要在 Devin 运行时直接替换 SQLite 主库或 WAL；如需回退，应先退出
所有 Devin 写入进程，再根据 manifest 选择对应备份恢复，并重新冷启动验证。

## 证据与已知边界

- `workspace_dirs` 是主目录之外的附加目录列表，不是主目录本身。
- `additionalWorkspaceDirs` 是 Desktop 镜像；清理 CLI 来源但保留该字段可能被 Desktop 重新采用。
- Space 的成员目录并集与会话级附加目录是两个概念。本工具不修改 Space 成员关系。
- 当前没有证据可以把某次 `yr` 写入归因到一个确定的 UI 点击；“共享 Space 目录被回写到成员
  Session”是需要后续版本回归或日志证据验证的假设。
