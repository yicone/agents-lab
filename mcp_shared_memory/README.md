# MCP Shared Memory (SQLite)

一个本地 MCP Server：提供跨会话共享的“记忆库”，默认落盘到 SQLite，支持全文检索（SQLite FTS5 可用时）。

## 能解决什么

- 你在 Codex desktop app 里做研究/开发时，把“结论/偏好/约束/项目背景”写入一个可长期保存的 Memory Store
- 下次会话里，Agent 可以通过 MCP 工具检索并取回这些记忆

## 启动

```bash
python3 /Users/tr/Workspace/agents-lab/mcp_shared_memory/server.py
```

可选：指定数据库位置（方便备份/同步）

```bash
MCP_MEMORY_DB_PATH="$HOME/.codex/mcp/shared-memory/memory.sqlite3" \
  python3 /Users/tr/Workspace/agents-lab/mcp_shared_memory/server.py
```

## 在 Codex 里接入（stdio）

在 MCP Servers 里添加一个“stdio server”，命令指向上面的 `server.py` 即可。

如果 UI 允许设置环境变量，把 `MCP_MEMORY_DB_PATH` 也填上，这样可以稳定使用同一个 DB 文件。

建议把启动命令设置为：

```bash
env PYTHONUNBUFFERED=1 python3 /Users/tr/Workspace/agents-lab/mcp_shared_memory/server.py
```

说明：本 server 会自动兼容两种 stdio 传输格式（按首条消息自动探测并用同种格式响应）：

- JSONL（每行一条 JSON-RPC 消息）
- LSP-style framing（`Content-Length` headers + JSON payload）

## 提供的工具

- `memory_add`：写入记忆（text + tags + source）
- `memory_search`：检索记忆（query + tags + limit）
- `memory_get`：按 id 获取
- `memory_list`：列最近 N 条
- `memory_update`：更新 text/tags/source
- `memory_forget`：删除
- `memory_stats`：统计

## 建议的“记忆写入”口径

把“对未来决策有用、且跨会话会反复用到”的内容写进去，例如：

- 你的研究目标、边界、偏好（例如：优先本地优先/隐私优先/可脚本化）
- 某项目的架构约束、目录结构、常见坑
- 你的术语表/分类标准（方便后续持续积累）

如果你同时在多个项目里使用同一个 shared DB（默认就是这样），建议用 **project namespace tag** 做逻辑隔离：

- 例如本仓库：`proj/agents-lab`
- 以及项目级 seed tag：`seed/agents-lab/2026-02-27`（避免不同项目 seed 日期相同导致误命中）
- 写入时：所有本项目相关记忆都带上该 tag
- 检索时：默认 `tags` 里包含该 tag，避免跨项目误命中

## 开发自测

```bash
python3 /Users/tr/Workspace/agents-lab/mcp_shared_memory/smoke_test_mcp.py
```

## 写入种子记忆（可选）

```bash
python3 /Users/tr/Workspace/agents-lab/mcp_shared_memory/seed_memories.py
```
