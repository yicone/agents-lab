# CHANGELOG 维护

- 凡合入 `main` 的 Atlas 改动，作者都要在同一个 PR 中写入 `CHANGELOG.md` 的 `Unreleased`，合并后不另补提交。
- 把 `Unreleased` 改成日期，放到下一个改到 CHANGELOG 的 PR 中顺手完成，并保留新的空 `Unreleased`；未部署内容不得标为已上线，部署目录永远不改。
- 具体步骤与条目粒度遵循 [atlas-changelog](../.agents/skills/atlas-changelog/SKILL.md)。

## Atlas 产品约束

- 本地服务使用 `127.0.0.1`，只读优先；已批准的写入范围和安全边界以 `docs/roadmap.md` 为准，不把只读优先解释为永久禁止写入。
- 信息密度高但清晰，不引入不必要的依赖。
- Atlas 新板块的需求范围与验收必须对照 `docs/design-system.md`；涉及页面的验收材料附截图对比。
- 上述约束与 `docs/roadmap.md`、`docs/design-system.md` 冲突时，以这两份文档为准。
