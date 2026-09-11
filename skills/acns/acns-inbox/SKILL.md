---
name: acns-inbox
description: Use when the user wants to capture ideas, tasks, questions, or info into the ACNS global Inbox for later triage and routing. If ownership is already known, prefer the Logseq fast-append path (logseq-acns-vault Fast Path) instead of Inbox.
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

Read when ownership is unclear or routing is non-trivial:
- `pages/Inbox.md`
- `AGENTS.md`
- (optional deeper) `pages/OS-AREA%2F信息管理%2FAI处理Inbox工作流.md`, `pages/OS-RES%2FAI 信息管家运行规则 v1.md`

For a known-ownership one-liner, skip these reads and use Fast Path above.

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


## Fast Path (skip Inbox ceremony)

If the user already names an existing Logseq page / today's Journal / a clear Notion delivery task, **do not** force Inbox.

| Known destination | Action |
|---|---|
| Existing Logseq page + one sentence | `logseq-acns-write` `fast-append` → transport (skip vault deliberation) |
| Today's focus / execution slice | append to `Journals` via `fast-append` |
| Confirmed project delivery | Notion task pool — not Inbox, not Logseq task dump |
| Ownership still unclear | stay on this Inbox skill (minimal metadata below) |

For unclear one-liners into Inbox: write one block with light metadata only — do not over-read reference pages or invent structure.

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
