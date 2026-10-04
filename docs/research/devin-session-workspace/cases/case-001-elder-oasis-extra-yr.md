# Case 001：Elder Oasis / `yr` 额外工作目录

## 摘要

这是本档案的首个特别处理案例：Elder Oasis 相关 Devin local 会话的 canonical cwd 是 `/Users/tr/Workspace/elder-oasis`，但 CLI 与 Desktop 镜像中出现了 `/Users/tr/Workspace/yr` 作为额外工作目录。修复目标是清理额外字段，不迁移会话、不修改 Space membership。

## 处理对象

首批四个：`accurate-opossum`、`garrulous-haddock`、`wistful-bowl`、`lumbar-success`。

同一 Space 后续确认的五个污染源：`foam-yamamomo`、`ember-foxtail`、`dear-reader`、`lemon-brook`、`lilac-lungfish`。

后续追加：`unequaled-feverfew`，其 canonical cwd 正确但独立出现同样的 `yr` 额外目录字段。

## 修复内容

在 Desktop 与 CLI 完全退出后，按 Session ID、canonical cwd、provider 和精确旧值做保护性更新：

```text
CLI sessions.workspace_dirs:
  ["/Users/tr/Workspace/yr"] → []

Desktop info._meta["cognition.ai/additionalWorkspaceDirs"]:
  删除 /Users/tr/Workspace/yr
```

未修改：

- canonical `working_directory` / Desktop `cwd`；
- title、model、provider、hidden；
- Space `resourceToSpace` membership；
- ACP 会话本地存储。

## 证据与验证

- 首批修复后冷启动，Explorer 只显示 `elder-oasis`，不再显示 `yr`。
- `unequaled-feverfew` 修复前后 CLI 与 Desktop 数据库完整性检查均为 `ok`，字段读回符合预期。
- `unequaled-feverfew` 修复备份目录：

  ```text
  ~/.local/share/devin/cli/repair-backup-20260930T021200+0800-unequaled-feverfew
  ```

## 与运行时问题的边界

修复持久化额外目录后，Elder Oasis 仍出现过侧边栏会话消失、Project 数量变化和 editor tab 恢复。这些现象不等价于 `yr` 字段再次写回，另行记录在 [观察 002](../observations/obs-002-2026-09-30-elder-oasis-projection.md)。

特别是，`unequaled-feverfew` 在本次修复时曾被 `resourceToSpace` 读为独立 Space；后续当前快照显示它已出现在 Elder Oasis，属于独立的 Space 归属变化线索，不能直接归因于本次额外目录修复。
