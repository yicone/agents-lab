---
name: acns-inbox
description: Use when the user wants to capture ideas, tasks, questions, or info into the ACNS global Inbox for later triage and routing.
metadata:
  owner: agents-lab
  scope: repo
  adapter_targets:
    - <target-project-skill-dir>/acns-inbox
---

# ACNS Inbox

## Overview

`acns-inbox` is the default global capture workflow for the user's ACNS.

Its role is:
1. capture quickly
2. classify lightly
3. preserve enough context for later routing
4. avoid premature system spread

Read these before acting when relevant:
- `pages/Inbox.md`
- `pages/OS-AREA%2F信息管理%2FAI处理Inbox工作流.md`
- `pages/OS-RES%2FAI 信息管家运行规则 v1.md`
- `AGENTS.md`

## Core Principle

Global capture goes to `[[Inbox]]` by default.

Do this when:
- ownership is unclear
- project association is unclear
- the item is not yet confirmed as a real project delivery action
- the user is thinking out loud, collecting ideas, or parking something quickly

Do not use Inbox as a reflex when the item is already a clearly defined project task inside an existing project context.

Rule:
- unclear global capture -> `[[Inbox]]`
- clear project delivery action in an existing project context -> usually `Notion` project task pool, not `Inbox`

## Workflow

1. Receive input
2. Triage lightly
3. Decide whether this is really global capture
4. Classify object type
5. Add metadata
6. Write to `Inbox` only if Inbox is the correct layer
7. Explain the classification briefly

## Lightweight Triage

Before writing, answer:
- Is this an actionable item
- Is this a thought or idea that should incubate
- Is this a question needing later resolution
- Is this reference or info worth retaining
- Is this already clearly a project task instead of a capture item

Do not overdesign at capture time.

## Object Type Mapping

Use the existing Inbox metadata model, interpreted through the current ACNS object model:

- `inbox-type:: task`
  - use for `Action`
- `inbox-type:: idea`
  - use for unvalidated possibilities, hypotheses, future concepts
- `inbox-type:: question`
  - use for unresolved questions
- `inbox-type:: info`
  - use for facts, references, small notes

## When Not to Write to Inbox

Do not write to `Inbox` when:
- the item is already a confirmed project delivery task
- it clearly belongs to an active project in `Notion`
- it is already today's execution slice and should go to `Journals`
- it is purely durable context that already has an owning Logseq page

## Output Location

When Inbox is the right destination, always write to:
- `pages/Inbox.md`
- section: `## 📥 新捕获（AI写入区）`
