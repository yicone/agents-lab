---
name: acns-route-inbox
description: Use when reviewing Inbox items and deciding whether they should stay in Inbox or route into Notion, GitHub, reminder tools, Journals, or Logseq context pages.
metadata:
  owner: agents-lab
  scope: repo
  adapter_targets:
    - <target-project-skill-dir>/acns-route-inbox
---

# ACNS Route Inbox

## Overview

`acns-route-inbox` is the second-stage workflow after global capture.

The goal is:
1. read Inbox items
2. identify their real object type
3. recommend the narrowest useful destination
4. avoid duplicate systems
5. close the loop from capture into the real execution system

Read these before acting:
- `pages/Inbox.md`
- `pages/OS-AREA%2F信息管理%2FAI处理Inbox工作流.md`
- `pages/OS-RES%2FAI 信息管家运行规则 v1.md`
- `pages/OS-RES%2F当前真实活任务 v1.md`
- `AGENTS.md`

## Core Principle

Do not route Inbox items just because another system exists.

Route only when doing so improves one of these:
- execution clarity
- project coordination
- reminder reliability
- reality calibration

If the best destination is still ambiguous, keep the item in `[[Inbox]]` and make the ambiguity explicit.

## Decision Order

For each item, answer in this order:
1. Is it really an `Action`
2. If yes, is it already a clear project delivery action
3. If yes, does it belong in GitHub or Notion
4. If not a project task, does it need reminder behavior
5. If not a completion-oriented action, does it actually belong in Logseq context instead
6. If it is today's real focus, should it also appear in `Journals`

## Default Destinations

### Keep in Inbox

Use when:
- the item is still ambiguous
- the item is incubating
- the item may become `Project`, `Area`, or `Resource` but is not ready
- the item would require creating a new long-term structure

Expected metadata:
- `inbox-status:: raw` or `incubating`
- `ai-note::` explains uncertainty

### Route to Notion Tasks

Use when:
- the item is a real action
- it belongs to an existing project or an already confirmed project container
- it benefits from status tracking, grouping, or project-level coordination
- it is a project delivery action rather than pure context

Important:
- once an item is confirmed as a project delivery action, it should not remain only in `当前真实活任务` or `Inbox`
- write it back into `Notion` project task pool

### Route to GitHub Issues

Use when:
- the item is directly about code delivery
- completion should be visible in code, PRs, CI, release flow, or repo history
- it is a bug, feature, tech debt, refactor, or engineering blocker

### Route to Reminder Layer

Use when:
- the item needs a date or time trigger
- the user is likely to act on it from mobile
- the action is short and self-explanatory
- it can be executed without opening heavy context

### Route to Journals

Use when:
- the item has become part of today's execution slice
- the item is one of today's real priorities
- the item should appear in the daily cockpit rather than only in a long-term system

### Route to Logseq

Use when:
- the item is mainly research, evaluation, learning, decision, or process
- it needs explanation beyond 2 sentences
- it has multiple steps or branches
- its main value is context, not completion
- it has become a discussion page disguised as a task

## Relationship to 当前真实活任务

`当前真实活任务` is a short-lived reality-calibration layer.

Use it to:
- identify what is truly alive now
- filter stale history out of decision-making

Do not use it as the final long-term task home.

Rule:
- reality calibrated action -> if project delivery, write to `Notion`
- reality calibrated daily focus -> also write to `Journals`

## Output Format

When reviewing a batch, group results into:
- `留在 Inbox`
- `进入 Notion Tasks`
- `进入 GitHub Issues`
- `进入提醒层`
- `进入 Journals`
- `回到 Logseq`
- `待人工确认`

For each item, provide:
- original item label
- inferred object type
- recommended destination
- short reason
- whether confirmation is required
