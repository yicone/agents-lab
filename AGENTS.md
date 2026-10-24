# Agent Operating Rules (agents-lab)

本仓研究 coding agents、编排、记忆与 skills。以下约束适用于全仓。

## 规则归属

- 全仓短约束在本文件；子项目约束在对应目录 AGENTS.md，不重复根规则。
- 多步任务流程正文在 `skills/` 或 `skills-governance/skills/`，通过 `.agents/skills/<name>/SKILL.md` 的 symlink 入口发现，保持单源。
- agent instructions 只定义角色、权限边界和交付；docs 保留说明、契约和历史材料，不另作规范入口。
- 仓库特定规则与 skills 不得未经审定推广到其它项目；通用流程也不得隐式覆盖本仓约束。
- 规则保持 runtime 无关；关键停止条件直接写在适用入口，不藏在深层引用中。

## 执行边界

- 先核对目标仓库、分支和工作树；不覆盖已有改动或未跟踪文件，不删除用户文件。
- 只有任务要求或用户明确要求时才 commit；push、开 PR、merge 各需明确批准，READY 不代表授权。
- 不 force push、不改 git config、不跳过 hooks；部署仅从已合并 main，且须获得对应授权。
- 不输出或提交密钥、凭证；配置、runtime、daemon、桥接和 skills 的变更不得超出本任务明确授权。
- 改行为跑现有相关测试；Web 改动走受影响操作。交付写实际命令、结果和未验证项。
- 区分建议、批准、实现、合并和部署事实，不把历史记录当本轮实测。
- 使用 Shared Memory 保存本仓结论时限定 proj/agents-lab；按任务需要读取上下文，不跨项目泄露信息。
