---
name: colgrep-search
description: Use when a coding agent needs to find code by behavior or meaning, when a text search would return too much to read, or when colgrep --agent is being considered for a coding task and colgrep is on PATH.
metadata:
  owner: agents-lab
  scope: cross-repo
  version: "1.0.0"
---

# ColGrep 普通搜索

用 `colgrep` 做语义检索，在改代码前用少量结果定位实现。这是普通搜索，不是 agent 模式。

试点（ACM-176）：`colgrep -k 5` / `colgrep -c` 约 0.6s，主模型读入大约少 75%–90%。`colgrep --agent` 要 17–54s，不使用。

## 何时用

用普通搜索：

- 知道行为，不知道符号名
- 精确搜索会吐出太多文本
- 当前仓库已有索引，或可以接受一次增量更新

改用 `rg`，不要用 colgrep：

- 精确字符串、符号、正则、文件名
- `colgrep` 不在 PATH、命令失败、索引起不来
- 结果和查询无关，而查询本来就是精确符号

不要做这些：

- `colgrep --agent`，以及 `colgrep settings --hook-mode agent`、`--install-agent`
- `colgrep --install-claude-code`、`--install-codex`、`--install-hermes` 或其他 `--install-*`（那会改 hook）
- 用 colgrep 替换 `rg`，或改 agent hook

## 命令

在要搜索的仓库根目录执行。输出给模型时加 `--color never`。

```bash
colgrep --color never -k 5 "自然语言查询"
colgrep --color never -c -k 5 "需要看函数体时"
```

- 先用不带 `-c` 的 `-k 5`。不够再加 `-c`（函数或类正文，大约最多 50 行）。
- 查询后面写路径，用来限定目录。
- `--include "*.py"` 用来限定文件类型。
- 索引已新、这次不想更新时加 `--no-update`。
- 不要加 `--agent`。`--agent` 会拒绝这些搜索参数。

## 索引

索引按项目增量维护。先看状态，再更新：

```bash
colgrep status
colgrep init
```

`colgrep init` 没有索引就新建，有文件变更就增量更新。普通搜索在需要时也会更新索引。大仓库提示确认时加 `-y`。

不要运行 `colgrep clear`，除非用户明确要求重建。索引用 `colgrep status` 打印的路径，不要猜目录。

## 失败就回退

下面任一情况，停用 colgrep，改跑 `rg`：

- 找不到命令，或搜索 / `init` / `status` 非零退出
- 普通检索明显超过数秒仍无结果。不要因此改跑 `--agent`
- 查询其实是精确符号，语义结果对不上

```bash
rg -n -g '*.py' '符号或模式'
```

## 红旗

- “这是行为问题，所以该用 --agent”
- “先装 agent 模型或 hook 再搜”
- 把 `-k` 调到几十再整段读入

这些都停。继续用 `-k 5`，或者回退到 `rg`。
