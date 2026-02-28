# Agent Operating Rules (agents-lab)

## Shared Memory MCP (跨会话记忆)

本仓库默认启用一个 Shared Memory MCP Server（SQLite 落盘），用于跨会话复用研究框架与结论。

### 逻辑隔离（跨项目不串）

本仓库约定使用项目命名空间 tag：`proj/agents-lab`。

- **写入**：任何与本仓库相关、可能被复用的 memory，都必须带 `proj/agents-lab`
- **检索**：默认只检索 `proj/agents-lab` 范围内的 memory；只有你明确要跨项目复用时，才额外检索其它 tag（例如 `global/*`）

### 每次新对话的“开场必做”

在回答/规划之前，先从 Memory 拉取种子记忆（taxonomy/products/workflow/rubric）。

重要：在 Codex 里，MCP 工具名通常是“**Fully qualified**”的：`<server>.<tool>`（例如 `shared_memory.memory_search`），而不是裸的 `memory_search`。
如果你只让 agent 调 `memory_search`/`memory_stats`，它可能会误把它当 shell 命令或声称“工具不可用”。
遇到这种情况，先让它改用 fully qualified 名称；仍不可用则优先定位 shared_memory MCP 启动/握手问题（而不是“兜底输出”）。

- 如果 Codex/CLI 提示 MCP 启动失败（常见错误包括 `timed out`、`Transport closed`、`initialize response` 等）：
  - 先跑一次快速自检（验证 stdio 协议与初始化握手）：`python3 mcp_shared_memory/smoke_test_mcp.py`
  - 自检通过但对话仍不暴露工具：优先收集 `/MCP` 输出与 Codex 日志再定位（不要凭空猜测）

- 若对话中能调用 MCP 工具：
  - `shared_memory.memory_search(query="taxonomy_v1", tags=["proj/agents-lab","seed/agents-lab/2026-02-27"], limit=20)`
  - `shared_memory.memory_search(query="products_scope_v1", tags=["proj/agents-lab","seed/agents-lab/2026-02-27"], limit=20)`
  - `shared_memory.memory_search(query="workflow_weekly_v1", tags=["proj/agents-lab","seed/agents-lab/2026-02-27"], limit=20)`
  - `shared_memory.memory_search(query="eval_rubric_v1", tags=["proj/agents-lab","seed/agents-lab/2026-02-27"], limit=20)`
  - 统计：`shared_memory.memory_stats()`
  
若你认为工具不可用，必须给出可验证证据（至少其一）：

- `/MCP` 的截图/文本（server 列表）
- 或 `smoke_test_mcp.py` 的输出/报错

### 任务进行中的“按需检索”

当用户提到某个产品/项目/轴（coding-agent / orchestrator / memory / skills / platform / prompts）时：

- 先用 `shared_memory.memory_search` 用关键字检索，并默认加 `tags=["proj/agents-lab"]`（必要时再加 `axis/*` 或 `product/*` tags 过滤）
- 找到历史结论则复用，只更新变化（delta）

### 任务结束时的“可复用信息落库”

当产生**未来会反复用到**的信息（稳定偏好、研究口径、分类法更新、结论/踩坑/对比结果）时：

- 调用 `shared_memory.memory_add` 写入简短、可检索的条目（tags 必须包含 `proj/agents-lab`）
- tags 至少包含：`axis/*`（必要时加 `product/*`、`topic/*`）以及合适的 `decision/*` 或 `workflow/*`

## Skills（避免跨项目串）

如果某个 skill 的内容与本仓库强绑定（例如默认使用 `proj/agents-lab`、引用本仓库脚本/路径），**不建议**把它软链接到全局 `~/.codex/skills`：

- 软链接到全局后，它会在其它项目里也可用，容易把错误的项目 tag/流程带过去
- 推荐做法：放到本仓库的 `.codex/skills/` 下（repo-scope），需要复用源文件时再从 `.codex/skills/<name>/SKILL.md` 软链接到 `skills/<name>/SKILL.md`（需要时再单独做一个“全局版”skill，内容必须是 `proj/<repo>` 这种参数化写法）
