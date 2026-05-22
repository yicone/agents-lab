# Regression Tests

This file captures a lightweight dry-run test round for `logseq-acns-vault`.

Goal: verify that the skill improves page ownership, namespace choice, and page-boundary decisions for ambiguous Logseq write requests.

## Test Method

For each case:
- Run A: generic agent, no `logseq-acns-vault`
- Run B: agent explicitly loading `logseq-acns-vault`
- Do not write to the live vault
- Ask for a dry-run decision only:
  - chosen page(s)
  - split vs append
  - naming / namespace choice
  - minimal properties if creating a page
  - short justification

## Scoring Rubric

Score each run on 5 binary checks:
- Page ownership correct
- Namespace correct
- No mixed unrelated concerns
- Structured properties used when creating a durable page
- Linking / placement strategy is reasonable

Interpretation:
- `0-2`: poor
- `3`: partial
- `4-5`: good

## Case 1

Prompt:

```text
把“lsq 这个 repo 为什么被选来扩展 query，而不是别的 GitHub 上的 Logseq CLI repo”这段结论，先顺手记到现有的 Logseq CLI Query 研究页面里；另外把这次 PR #72 的 review 流程经验也一起补进去，免得分散。
```

### Without skill
- Chosen page(s): `OS-LOG/Logseq CLI Query 选型与架构调研`
- Decision: append to one page; do not split
- Namespace: existing `OS-LOG/`
- Properties: none
- Score: `3/5`

Why this fails:
- It accepts the user's "免得分散" pressure at face value.
- It mixes query research with PR/review process lessons on one page.

### With skill
- Chosen page(s):
  - `[[OS-LOG/Logseq CLI Query 选型与架构调研]]`
  - `[[OS-LOG/PR Review 与 Fork 分支治理经验]]`
- Decision: split by domain, append to both existing pages
- Namespace: `OS-LOG/` for both
- Properties: none needed because both pages already exist
- Score: `5/5`

Improvement:
- Correctly recognizes an existing validated failure mode: research and process governance do not belong together.
- Chooses the correct existing owner page for each domain.

## Case 2

Prompt:

```text
我刚想到一个长期要持续维护的主题：信息管理里的 Logseq CLI / MCP / plugin 协作方式。先帮我建个页面记下来，再把“下次记得不要再让 bot review 无限循环”这条提醒也放进去。
```

### Without skill
- Chosen page(s): `Logseq CLI / MCP / plugin 协作方式`
- Decision: create one page and append the reminder there too
- Namespace: none; bare title
- Properties: none, or only `title::`
- Score: `1/5`

Why this fails:
- Uses a durable bare title instead of a namespace.
- Mixes a durable knowledge page with a transient process reminder.
- Omits the minimal structured property block.

### With skill
- Chosen page(s):
  - `[[OS-RES/Logseq CLI MCP plugin 协作方式]]`
  - `[[Inbox]]`
- Decision: split
- Namespace: `OS-RES/` for the durable topic; `Inbox` for the reminder
- Minimal properties:

```markdown
title:: Logseq CLI / MCP / plugin 协作方式
type:: RES
status:: active
related:: [[OS-AREA/信息管理]]
```

- Score: `5/5`

Improvement:
- Correctly treats the durable topic as maintained reference material.
- Keeps the transient reminder out of the durable page.
- Applies the minimal property block.

## Case 3

Prompt:

```text
把“{{query ...}} macro stripping 应该先于本地 file backend 做”记下来。先放到最合适的页面；如果没有页面就新建。顺便把今天为了赶 PR 临时决定的分支清理步骤也记录进去。
```

### Without skill
- Chosen page(s):
  - `[[lsq/query]]`
  - `[[2026_03_27]]`
- Decision: split, but into an ad hoc repo/topic page and the daily journal
- Namespace: repo-style topic page; no Context OS namespace
- Properties: none
- Score: `3/5`

Why this only partially succeeds:
- It does split durable reasoning from temporary operations.
- But it invents a temporary repo/topic page instead of using the Context OS naming system and existing owner pages.

### With skill
- Chosen page(s):
  - `[[OS-LOG/Logseq CLI Query 选型与架构调研]]`
  - `[[OS-LOG/PR Review 与 Fork 分支治理经验]]`
- Decision: split and append to the existing owner pages
- Namespace: `OS-LOG/`
- Properties: none needed because both pages already exist
- Score: `5/5`

Improvement:
- Correctly identifies the durable architecture decision as part of the query research log.
- Correctly routes branch cleanup/governance process content to the process/governance log.
- Avoids creating a temporary or implementation-shaped page title.

## Summary

Observed pattern without the skill:
- too willing to append to a roughly related page
- too willing to create a durable page with a bare or ad hoc title
- inconsistent separation between durable knowledge and transient process/task content

Observed pattern with the skill:
- consistent split of research vs governance/process
- consistent use of `OS-*` namespaces or `Inbox`
- better reuse of existing owner pages
- better minimal property discipline for new durable pages

## What This Test Round Validates

`logseq-acns-vault` is effective as a vault-specific write-classification skill.

Its strongest contribution is not prose quality; it is decision discipline:
- page ownership
- namespace choice
- durable vs transient placement
- split vs append judgment

## Suggested Reuse

Reuse these three cases whenever the skill changes materially, especially if you edit:
- namespace rules
- page-boundary rules
- minimal property conventions
- pressure scenarios
- validated failure modes

A future round can add one more case specifically targeting AREA-page misuse.
