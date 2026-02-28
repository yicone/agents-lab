# Agent Operating Rules (agents-lab)

## Shared Memory MCP (跨会话记忆)

本仓库默认启用一个 Shared Memory MCP Server（SQLite 落盘），用于跨会话复用研究框架与结论。

### 每次新对话的“开场必做”

在回答/规划之前，先从 Memory 拉取种子记忆（taxonomy/products/workflow/rubric）。

重要：在 Codex 里，MCP 工具名通常是“**Fully qualified**”的：`<server>.<tool>`（例如 `shared_memory.memory_search`），而不是裸的 `memory_search`。
如果你只让 agent 调 `memory_search`/`memory_stats`，它可能会误把它当 shell 命令或声称“工具不可用”。
遇到这种情况，先让它改用 fully qualified 名称；仍不可用则优先定位 shared_memory MCP 启动/握手问题（而不是“兜底输出”）。

- 如果 Codex/CLI 提示 MCP 启动失败（常见错误包括 `timed out`、`Transport closed`、`initialize response` 等）：
  - 先跑一次快速自检（验证 stdio 协议与初始化握手）：`python3 /Users/tr/Workspace/agents-lab/mcp_shared_memory/smoke_test_mcp.py`
  - 自检通过但对话仍不暴露工具：优先收集 `/MCP` 输出与 Codex 日志再定位（不要凭空猜测）

- 若对话中能调用 MCP 工具：
  - `shared_memory.memory_search(query="taxonomy_v1", tags=["seed/2026-02-27"], limit=20)`
  - `shared_memory.memory_search(query="products_scope_v1", tags=["seed/2026-02-27"], limit=20)`
  - `shared_memory.memory_search(query="workflow_weekly_v1", tags=["seed/2026-02-27"], limit=20)`
  - `shared_memory.memory_search(query="eval_rubric_v1", tags=["seed/2026-02-27"], limit=20)`
  - 统计：`shared_memory.memory_stats()`
  
若你认为工具不可用，必须给出可验证证据（至少其一）：
- `/MCP` 的截图/文本（server 列表）
- 或 `smoke_test_mcp.py` 的输出/报错

### 任务进行中的“按需检索”

当用户提到某个产品/项目/轴（coding-agent / orchestrator / memory / skills / platform / prompts）时：

- 先用 `memory_search` 用关键字检索（必要时加上对应 `axis/*` 或 `product/*` tags 过滤）
- 找到历史结论则复用，只更新变化（delta）

### 任务结束时的“可复用信息落库”

当产生**未来会反复用到**的信息（稳定偏好、研究口径、分类法更新、结论/踩坑/对比结果）时：

- 调用 `memory_add` 写入简短、可检索的条目
- tags 至少包含：`axis/*`（必要时加 `product/*`、`topic/*`）以及合适的 `decision/*` 或 `workflow/*`
