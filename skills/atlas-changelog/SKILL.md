---
name: atlas-changelog
description: 维护 Agent Atlas 的 multica-board/CHANGELOG.md；用于 Atlas 改动交付前核对条目、从已合并提交和 PR 补历史，以及获批部署时归档日期。
metadata:
  owner: agents-lab
  scope: repo
---

# Atlas CHANGELOG

只维护 `multica-board/CHANGELOG.md`。此 skill 不授权推送、开 PR、合并或部署；本地交付、独立验收和用户批准仍按当前任务执行。

## 改动交付前

1. 阅读 `multica-board/AGENTS.md` 和现有 CHANGELOG。逐项检查本次 Atlas 改动，保证每个合入 main 的改动都有 Unreleased 维护记录；PR 尚未合并时，在交付评论准备候选文案，不把分支实现写成 main 事实。合并后由获授权的维护者落入 Unreleased，并补真实 PR/提交链接。
2. 条目按「新增 / 变更 / 修复」分组，一句人话说明用户看到什么变化，附 PR 链接和 ACM 单号；可附完整提交链接增强追溯。合并提交不是产品版本号。
3. 展示粒度为用户可感知变化，不列纯重构、测试或只影响开发过程的提交。每项改动都要检查维护义务；若纯内部改动无可展示内容，在交付评论记下排除理由，不向用户清单凑条目。
4. 核对当前 PR、关联 ACM、实际 diff 和撤回记录；同一能力的前后端可合并描述，修复不能被笼统的新功能条目掩盖。不把计划、草案、未合并代码或 mock 能力写成完成。

## 补历史

从仓库根目录读取最新 main（先确保本地工作安全），例如：

```sh
git fetch origin main
git log origin/main --date=iso-strict --format='%H %ad %s' -- multica-board
gh pr list --repo yicone/agents-lab --state merged --limit 100 --json number,title,url,mergedAt,mergeCommit,body
```

若超出返回范围，继续按 CLI 支持的筛选/分页查询，不能因 limit 截断就称完整。对候选 PR 读取详情及实际 diff，并执行 `git merge-base --is-ancestor <完整合并SHA> origin/main`。合并 PR 中的其它目录可能有 Atlas 用户可见影响，也要检查。剔除撤回变化，合并重复描述。

PR 未写 ACM 时继续查提交、PR 评论和 Multica issue/相关部署与验收评论，通过 `multica` CLI 核实关联；不能凭相似标题猜编号。没有证据就单列「待补证」并说明缺的是哪条关联，不伪造编号或声称已满足完整追溯。

历史日期必须注明含义与时区。UTC 合并日期只能称合并历史；要称已上线，必须关联记录中的部署 SHA、时间/时区与结果。历史评论是历史证据，不是本轮实测。无法证明部署的内容保留待证性质，不用合并日期冒充上线日期。

## 获批部署时

1. 仅在明确获授权的部署流程中，由部署执行者核对已合并 main 的实际部署 SHA、部署记录与日期/时区；不能从未合并分支部署。
2. 将本次实际部署覆盖的 Unreleased 改成当天日期节（无正式版本号用 `YYYY-MM-DD`），注明部署 SHA、时区与证据，保留分类，再新建空 `## Unreleased`。若只覆盖部分条目，只移动覆盖的内容；其它保留 Unreleased，不提前标上线。
3. 若是部署尝试但结果未确认，不归档为已上线。历史待证区只在获得对应证据后补状态；不随一次新部署自动重写所有历史日期。

## 交付检查

运行 `git diff --check`；逐条读回日期、分类、PR URL、合并 SHA 与 ACM 关联，确认顶部保留 Unreleased。报告实际检查与缺证项；不以格式检查代替来源核对。文档改动无须触碰服务或真实 workspace 配置。
