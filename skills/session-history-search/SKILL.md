---
name: session-history-search
description: Use when locating prior local agent sessions by id, date, title, cwd or path, or searching an Agent Sessions read-only index and rollout session_meta.
capabilities:
  network: false
  secrets: false
  filesystem: write
---

# 历史会话检索

跨仓 skill；规范源在 agents-lab 的 `skills/session-history-search/`。只检索当前任务所需的最小脱敏材料；唯一允许的写入是当前 issue workdir 中的索引一致性副本与必要脱敏结果。安装和库存登记另行批准。

## 硬边界

- 禁止 `nmem threads sync/save --apply` 和 `--all-projects`。
- 不读不写 `~/.codex`。
- 不全量复制或导入会话。
- 确需整会话导入时停下来，回 ACM-216 请求用户单独批准。

不得以 rollout 位于默认目录为由绕过上述限制；没有获准的非 `~/.codex` 来源时报告证据缺口。不要读取配置、状态、凭证或与任务无关的正文。metadata 中 `base_instructions` 等不可复制。

## 先定位，再检索

1. ACM-242 附件保存伞目录说明与 session_meta 清单。只按 id、日期、标题、cwd、文件路径定位；近期标签不代表 issue 状态。Multica 没有原生历史会话检索 API，附件用 authenticated `multica attachment` CLI 获取，不直接访问资源 URL。
2. Nowledge Mem 默认只搜索已有内容或 preview；使用已有 memory/thread 搜索 skill 控制范围，不执行导入或同步写入。
3. 需要本机 Agent Sessions FTS 时，先从任务已授权的非受限位置确认 `index.db` 路径。通过 SQLite 只读连接和 backup API，在当前 issue workdir 生成一致性副本；不要直接复制活跃主 DB 而遗漏 WAL：

   ```python
   import sqlite3
   from contextlib import closing
   from pathlib import Path

   source = Path(approved_index_path).resolve()
   codex = (Path.home() / ".codex").resolve()
   if source == codex or codex in source.parents:
       raise ValueError("restricted source")
   workdir = Path(issue_workdir).resolve()
   if workdir == codex or codex in workdir.parents:
       raise ValueError("restricted destination")
   destination = workdir / "session-index-copy.db"
   if destination.exists() or destination.is_symlink():
       raise FileExistsError(destination)
   with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as src:
       with closing(sqlite3.connect(destination)) as dst:
           src.backup(dst)
           dst.execute("PRAGMA journal_mode=DELETE")
   ```

4. 只对副本建立只读连接。先查 `sqlite_master` 中 FTS 虚表及 `PRAGMA table_info`，确认实际 schema；再用参数绑定的 `MATCH ?`，加入任务的 id/时间/cwd 范围和小 `LIMIT`。优先取定位字段与短 snippet，不 `SELECT *`，不扫出全部正文；没有 FTS 时说明缺口，不重建源索引。
5. 原始 rollout 仅查看已定位、已授权的指定文件首条 `session_meta`，先排除 `~/.codex` 下路径和指向该目录的 symlink。普通文件用 `head -n 1`，压缩文件用 `zstd -dc` 配合 `head` / `jq`；只投影字段，禁止打印整个 metadata：

   ```sh
   approved_rollout=$(python3 - "$approved_rollout" <<'PY_PATH'
   import sys
   from pathlib import Path
   p = Path(sys.argv[1]).resolve()
   restricted = (Path.home() / ".codex").resolve()
   if p == restricted or restricted in p.parents:
       sys.exit("restricted rollout")
   print(p)
   PY_PATH
   ) && zstd -dc -- "$approved_rollout" | head -n 1 | jq 'select(.type == "session_meta") | {id: .payload.id, timestamp: .payload.timestamp, cwd: .payload.cwd}'
   ```

   解压管道可能因 `head` 提前关闭而出现 SIGPIPE；确认输出是完整首条 JSON，不能因此转而输出全文件。首条不是 session_meta 时报告缺口，不遍历后续内容补找。
6. 第 5 步的原始 rollout 元数据定位仅限首条；只有用户明确需要正文时，才可另行读取已授权、非 `~/.codex` 来源的已定位必要脱敏片段，仍受硬边界限制。交付记录来源 id、筛选范围、短摘录和缺口，不把原始 session_meta、指令或整会话写入记忆或 issue。
