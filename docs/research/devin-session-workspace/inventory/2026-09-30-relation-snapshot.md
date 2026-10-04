# 2026-09-30 Devin Desktop 会话—目录关系快照

> 这是只读快照，不代表所有会话都应归入 Elder Oasis，也不代替逐会话核验。

## CLI `sessions.db`

- 总行数：213。
- canonical cwd 为 `/Users/tr/Workspace/elder-oasis`：12 行。
- canonical cwd 为 `/Users/tr/Workspace/yr`：25 行。
- `workspace_dirs` 非空：10 行，其中 3 行包含 `yr`。
- `meadow-quiet` 不在非空 `workspace_dirs` 集合中；它是 canonical cwd 创建错误。

## Desktop `state.vscdb`

- 可读到 `devin-cli` sessioninfo：56 个资源。
- cwd 为 `/Users/tr/Workspace/yr`：23 个。
- cwd 为 `/Users/tr/Workspace/elder-oasis`：12 个。
- 可读到 `additionalWorkspaceDirs`：4 个 devin-cli 资源，其中 3 个包含 `yr`，1 个包含 `agent-storage-manager`。
- Desktop sessioninfo 与 CLI `sessions.db` 不是一一对应的完整镜像，不能单独作为清单来源。

## Space / Project 观察

- Elder Oasis 当前 Space ID：`1784977093276-9xndoc4fc`。
- 本次新观察中，Elder Oasis `resourceToSpace` 有 15 个资源。
- Project 分组与 Space 分组不是同一集合；Project 计数还可能受到 cwd、worktree、远端 repo 元数据、provider materialization 和 retention 影响。
- 另一组 `yr` Project 观察曾出现冷启动 `20`，滚动后 `25 → 30 → 39 → 49 → 50`；这是独立的 Project 懒加载/投影观察。

## 分类结论

当前至少存在四类关系问题：

1. canonical cwd 创建错误；
2. 额外 workspace 持久化污染；
3. Space 级目录聚合继承；
4. Project/sidebar/editor-state 运行时投影异常。

各类不能用同一个修复动作处理。
