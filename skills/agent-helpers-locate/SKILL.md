---
name: agent-helpers-locate
description: Use when locating a repository or determining worktree ownership under the agent-helpers umbrella, including CodexBar.worktrees and codeg.
capabilities:
  network: false
  secrets: false
  filesystem: read
---

# agent-helpers 仓库定位

跨仓 skill；规范源在 agents-lab 的 `skills/agent-helpers-locate/`。用于只读定位，不授予修改、提交或推送权限。适配器和全局安装须另行批准。

## 边界

`/Users/tr/Workspace/agent-helpers` 是伞目录，不是 git 根。8 个用户独立 git 根：

- 9router
- CodexBar
- agent-cli-governor
- agent-sessions
- codex-lb
- codex-multi-auth
- multi-codex
- quotio

`codeg` 的 origin 是 `https://github.com/xintaofei/codeg.git`，属于第三方，只作事实登记/只读，不作为用户仓库管理，不改动。

## 定位步骤

1. 从用户指定的子目录开始，先检查目标是否存在；不要在伞目录执行提交。
2. 对目标执行以下只读命令，确认 git 根、remote、分支、已有改动及 worktree 列表：

   ```sh
   git -C <target> rev-parse --show-toplevel
   git -C <target> rev-parse --path-format=absolute --git-common-dir
   git -C <target> status --short --branch
   git -C <target> worktree list --porcelain
   ```

   remote 用 `git remote -v` 确认，但不要直接回显：通过捕获 stdout 的本地程序读取，在内存中移除 URL userinfo、query 及 fragment，并遮蔽 SCP 风格地址的用户名后再报告；不要将原始输出写到日志或文件。

3. 对候选主仓运行同样的 common-dir 查询，比较规范化后的绝对路径，再以 `worktree list` 验证成员关系。路径或 remote 与预期不符时先报告事实，不凭目录名推断，也不自动改 remote。
4. `CodexBar.worktrees/`、`agent-sessions.worktrees/`、`agent-cli-governor/.worktrees/` 等是所属仓库的 worktree 容器，不是额外独立仓库；每个具体 worktree 的归属仍以 Git 查询为准。
5. 记录目标 repo、remote（隐藏可能嵌入的凭证）、分支、worktree 和 common-dir 归属，读取目标仓库规则后再按当前任务授权行动。

不要覆盖未提交或未跟踪文件，不在用户现有工作副本直接修改。Multica issue 使用目标仓库专属 worktree；分支、提交和推送按子仓库分别处理，不跨仓混合。
