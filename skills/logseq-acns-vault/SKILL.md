---
name: logseq-acns-vault
description: Use when operating on the user's ACNS Logseq vault as the context brain, especially when deciding page ownership, namespace choice, structured properties, Inbox/Journal boundaries, and how Logseq relates to Notion and the current-real-tasks layer.
---

# Logseq ACNS Semantic Guide

## Overview

This skill defines **how Logseq should be used inside the user's current ACNS / AI information-housekeeper system**.

It is not a generic Logseq skill and not a tool-execution manual.

Use it to decide:
- what belongs in Logseq
- what should stay out of Logseq
- how pages should be named
- what structured properties are expected
- when content belongs in `Inbox`, `Journals`, `AREA`, `PROJ`, `RES`, `LOG`, or should instead live in `Notion`

Core principle:
- optimize for machine-readable context, durable structure, and low maintenance cost
- do not optimize for grand system-building or pretty prose

## Current System Role

In the current three-core system:

- `Logseq` = context brain
- `Notion` = project control tower and project task pool
- `GitHub` = code-delivery surface
- `Journals` = daily execution and review layer
- `Inbox` = default global capture entry
- `当前真实活任务` = short-lived reality-calibration layer

This means:
- Logseq is the durable home for context, decisions, logs, knowledge, areas, and capture triage
- Logseq is **not** the default home for every project task
- Logseq is **not** the only execution surface

## When to Use

Use this skill when:
- creating or renaming pages in the main vault
- deciding whether content belongs in `AREA`, `PROJ`, `RES`, `LOG`, `Inbox`, or `Journals`
- deciding whether a page should use a bare name or a namespace
- adding structured properties such as `title::`, `type::`, `status::`, `related::`
- deciding whether content should stay in Logseq or be written back to `Notion`
- cleaning up pages that mix unrelated concerns
- deciding whether something is durable context, project structure, daily execution, or temporary capture

Do not use this skill as the main decision layer when:
- the task is purely about repo-local docs outside the personal vault
- the task is purely about editing Logseq or Obsidian config directories
- the task is purely about API/tool debugging with no vault semantic decision

## Coordination Boundary

Use `logseq-acns-vault` to decide:
- whether content belongs in the vault at all
- which page owns it
- which namespace and page type fit
- whether the destination should be `Inbox`, `Journals`, `AREA`, `PROJ`, `RES`, or `LOG`

If the task already clearly belongs in the vault and the next problem is building an ACNS-compliant write payload, pair or hand off to:
- `logseq-acns-write`

If the task is only about HTTP execution, token loading, or API response handling, do not stay in this skill. Pair or hand off to:
- `logseq-http-transport`

## Core Decisions

### 1. Bare Name vs Namespace

Ask:
- is this page about an external thing itself
- or is it the user's own structured content

Use bare names for external entities:
- `[[Logseq]]`
- `[[Git]]`
- `[[Claude Code]]`
- `[[Notion]]`

Use namespaces for the user's own structured content:
- `[[I-AREA/信息管理]]`
- `[[P-PROJ/charamerch]]`
- `[[OS-RES/AI 信息管家运行规则 v1]]`
- `[[I-LOG/开发环境工具链治理]]`

### 2. Which Namespace

Use:
- `X-AREA/` for long-lived domains with no fixed end
- `X-PROJ/` for projects with an end state
- `X-RES/` for maintained reference material, operating rules, or outputs
- `X-LOG/` for decisions, evaluations, explorations, change records, and time-bound reasoning

If the content is a temporary thought, capture, question, or unclassified task, prefer `[[Inbox]]`.

If the content is "today-only" execution or review context, prefer `Journals`, not a durable page.

### 3. What Belongs in Logseq vs Notion

Use Logseq for:
- context
- decisions
- logs
- research
- durable references
- area hubs
- page navigation
- capture triage
- project background and reasoning

Use Notion for:
- project control-tower records
- structured project task pools
- task status tracking
- boards, timelines, and cross-project views

Rule:
- project delivery action -> usually belongs in `Notion`
- project governance, task triage, reasoning, or context -> usually belongs in `Logseq`

### 4. Inbox vs Journal vs Durable Page

Use `[[Inbox]]` when:
- the item is newly captured
- ownership is unclear
- it is too early to decide whether it belongs in Logseq, Notion, GitHub, or reminder tools

Use `Journals` when:
- the item is about today
- it is part of today's execution slice
- it is part of today's review or AI summary

Use a durable page when:
- the content has repeat-reference value
- it belongs to a stable domain, project, resource, or decision trail

### 5. Current Real Tasks Layer

`当前真实活任务` is not a historical task archive and not a long-term project task pool.

Its role is:
- pull reality back out of stale systems
- identify what is truly alive in the current few days or week
- provide a short calibration layer before daily execution

Once a task is clearly a project delivery action:
- it should be written back into `Notion` project task pool

Once a task becomes today's real focus:
- it should appear in `Journals`

## Required Conventions

### Minimal Properties for New Structured Pages

For most new namespaced pages, start with:

```markdown
title:: 页面标题
type:: AREA | PROJ | RES | LOG
status:: active
related:: [[相关页面]]
```

Add `created::` when direct file creation is used.

### Linking Rules

- use wikilinks only: `[[页面名]]`
- verify the page exists before relying on a link
- avoid ghost links
- prefer cross-references over repeated prose

### Language Rules

- Chinese first for titles and content
- keep proper nouns in English where natural
- keep commands, code, paths, and API names in English

### Formatting Rules

- use Logseq property syntax, never YAML frontmatter
- preserve Logseq-native syntax such as queries and block references
- prefer bullets and compact structure over long essays
- start headings from `##` when headings are needed
- a single Logseq block must not contain multiple unordered lists or multiple headings
- if content needs multiple sections, split them into separate sibling or child blocks instead of concatenating them into one block string
- keep page properties in the page property area; do not embed properties after normal list text
- headings must begin as their own blocks, not appear after list text inside the same block

## Page Ownership Rules

### AREA pages

AREA pages are stable hubs.

They should hold:
- domain definition
- stable navigation
- recurring principles
- useful queries
- knowledge structure

They should not hold:
- transient tasks
- execution detail
- raw discussion
- temporary capture

### PROJ pages

Project pages in Logseq are for:
- project background
- reasoning
- decisions
- links to related resources
- links to Notion / GitHub / docs / journals

They are not required to be the primary task list.

### RES pages

Use RES for:
- durable guides
- reference material
- operating rules
- synthesized outputs

Examples:
- `[[OS-RES/AI 信息管家运行规则 v1]]`
- `[[OS-RES/当前真实活任务 v1]]`

### LOG pages

Use LOG for:
- decision trails
- evaluations
- explorations
- change reasoning
- time-bound conclusions

If the main value is "why we decided this", it likely belongs in a LOG.

## Write Workflow

1. Classify the content:
   - capture
   - daily execution
   - durable area/project/resource/log update
   - project delivery task
   - architectural or governance decision
2. Decide the system layer:
   - `Inbox`
   - `Journals`
   - durable Logseq page
   - `Notion`
3. If durable Logseq content is needed:
   - choose existing page first
   - create a new page only if the topic deserves a durable home
4. Choose naming style:
   - bare external entity
   - namespaced personal content
5. Add or normalize the minimal property block
6. Write concise bullets with strong links and structured fields
7. Re-check page boundary:
   - remove off-topic process detail
   - split if two domains are being forced together
   - move project delivery tasks out of Logseq if they really belong in `Notion`

## Common Mistakes

### Treating Logseq as the only task system

Bad:
- leaving clear project delivery actions only in Logseq notes even after they are confirmed real

Fix:
- keep reasoning and context in Logseq
- write project delivery actions back to `Notion`

### Using OS-prefixed namespaces by reflex

Bad:
- forcing all durable pages into `OS-*` because the vault once used more OS-centered language

Fix:
- follow the real page ownership and current namespace pattern
- use `I-/P-/F-/A-/E-` and `OS-*` where they genuinely fit current vault practice

### Putting today's execution into durable pages

Bad:
- storing today's action slice only in durable AREA/PROJ pages

Fix:
- keep "today" in `Journals`
- keep long-lived context in durable pages

### Putting transient tasks into AREA pages

Bad:
- adding temporary TODOs or execution detail into an AREA hub

Fix:
- move temporary work to `Inbox`, `Journals`, a project task in `Notion`, or a LOG page if the main value is reasoning

### Writing multi-section markdown into one block

Bad:
- sending one large block string that contains list text, then a heading, then another unordered list

Why it fails:
- Logseq renders this poorly and may show: `Full content is not displayed, Logseq doesn't support multiple unordered lists or headings in a block.`

Fix:
- create one block per heading or list section
- use sibling or child blocks to represent structure instead of stuffing multiple sections into one block

## Pressure Scenarios

### Scenario 1: "Just append it here for now"

Pressure:
- the user asks for speed
- there is already a roughly related page
- adding one more block would be easier than splitting

Correct response:
- test page ownership first
- if the content is mainly daily execution, put it in `Journals`
- if it is mainly task capture, put it in `Inbox`
- if it is a confirmed project task, consider `Notion`

### Scenario 2: "This feels like a task, so Logseq should own it"

Pressure:
- the content appears in a Logseq conversation or journal
- the page context is already open

Correct response:
- ask whether this is:
  - a capture item
  - a project delivery action
  - durable context
  - today's execution slice
- do not assume task location from capture location

### Scenario 3: "The AREA page is the obvious hub"

Pressure:
- the user mentions a topic associated with an AREA page
- it feels convenient to drop tasks or execution details there

Correct response:
- AREA pages are stable hubs, not task dumps
- route temporary tasks to `[[Inbox]]`, `Journals`, or `Notion`

## Final Reminder

`OS` / "personal information operating system" is now a worldview label, not an instruction to keep expanding the system.

When in doubt:
- favor low-maintenance operation
- favor machine-readable context
- favor reality over historical residue
- favor clear routing over universal accumulation
