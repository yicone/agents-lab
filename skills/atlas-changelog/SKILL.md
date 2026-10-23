---
name: atlas-changelog
description: 维护 Agent Atlas 的 multica-board/CHANGELOG.md；用于 Atlas 改动交付前核对条目、从已合并提交和 PR 补历史，以及在下一个改到 CHANGELOG 的 PR 中归档日期。
metadata:
  owner: agents-lab
  scope: repo
---

# Atlas CHANGELOG

只在开发分支的 PR 中维护 `multica-board/CHANGELOG.md`，部署目录永远不改。此 skill 不授权推送、开 PR、合并或部署；本地交付、独立验收和用户批准仍按当前任务执行。

## 改动交付前

1. 阅读 `multica-board/AGENTS.md` 和现有 CHANGELOG。逐项检查本次 Atlas 改动，由作者在实现改动的同一个 PR 中写入 Unreleased，合并后不再另外补提交。尚未合并的 PR 内条目是本次拟合入内容，不冒称已在 main 或已上线；补历史时仍只收已合并事实。
2. 条目按「新增 / 变更 / 修复」分组，一句人话说明用户看到什么变化，附 PR 链接和 ACM 单号。PR 号尚未产生时先在同一分支准备条目，获批开 PR 后在该 PR 合并前补齐真实链接，不猜编号、不等合并后补交；本 skill 不授权开 PR。可附已有完整提交链接增强追溯，未来 merge SHA 不预填。合并提交不是产品版本号。
3. 展示粒度为用户可感知变化，不列纯重构、测试或只影响开发过程的提交。每项改动都要检查维护义务；若纯内部改动无可展示内容，在交付评论记下排除理由，不向用户清单凑条目。
4. 核对当前 PR、关联 ACM、实际 diff 和撤回记录；同一能力的前后端可合并描述，修复不能被笼统的新功能条目掩盖。Unreleased 中当前 PR 的候选内容不等于已合并或已上线；不把计划、草案或 mock 能力写成真实已完成能力。

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

## 部署与后续日期归档

1. 获批部署时只从已合并的 main 部署，记录实际部署 SHA、结果、时间与时区；部署目录永远不改，不在部署目录编辑 CHANGELOG，也不为合并或部署单独补一笔 CHANGELOG 提交。
2. 把 Unreleased 改成日期的操作，放到下一个改到 CHANGELOG 的 PR 中顺手做，由该 PR 作者在开发分支完成并随同 PR 审核、合并；不要在部署现场改文件。无正式版本号时使用实际部署日期 `YYYY-MM-DD`，注明时区、部署 SHA 与依据，不用后续 PR 的日期冒充部署日期。
3. 只归档已有部署结果证据覆盖的条目，保留「新增 / 变更 / 修复」分类并重建空 `## Unreleased`；若部分尚未部署，将其保留在 Unreleased。新 PR 自身的未部署内容也留在 Unreleased。
4. 若部署未确认，先保留 Unreleased，不编造已上线日期。历史待证区只在取得对应证据后随该 PR 更新，不随新部署自动重写全部历史。

## 交付检查

运行 `git diff --check`；逐条读回日期、分类、PR URL、合并 SHA 与 ACM 关联，确认顶部保留 Unreleased。报告实际检查与缺证项；不以格式检查代替来源核对。文档改动无须触碰服务或真实 workspace 配置。
