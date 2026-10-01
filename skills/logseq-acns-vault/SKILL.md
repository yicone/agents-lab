---
name: logseq-acns-vault
description: Use when operating on the user's ACNS Logseq vault as the context brain, especially when deciding page ownership, namespace choice, structured properties, Inbox/Journal boundaries, and how Logseq relates to Notion and the current-real-tasks layer.
metadata:
  owner: agents-lab
  scope: repo
---

# Logseq ACNS Semantic Guide

## Overview

This skill defines **how Logseq should be used inside the user's current ACNS / AI information-housekeeper system**.

It is not a generic Logseq skill and not a tool-execution manual.

Use it to decide:
- what belongs in Logseq (stable conclusions)
- what should stay out of Logseq (process → Multica; hard-deadline light actions → Apple Reminders)
- how pages should be named
- what structured properties are expected
- when content belongs in short-TTL `Inbox`, `Journals`, `AREA`, `PROJ`, `RES`, `LOG`, Multica, Reminders, or Notion

Core principle:
- optimize for machine-readable context, durable structure, and low maintenance cost
- do not optimize for grand system-building or pretty prose

## Current System Role

Agent-era layering v0.1 (post 2026-10-02 Inbox cleanup):

| Layer | Role |
|---|---|
| `Logseq` vault | **Stable conclusions only** — context, decisions, logs, knowledge, areas |
| `Multica` | Actionable / process / half-baked / tool-trial work home (e.g. ACM-105..110 demotions) |
| `Apple Reminders` | Hard-deadline light actions (after threshold) |
| `Notion` | Project control tower and structured project task pool |
| `GitHub` | Code-delivery surface |
| `Journals` | Daily execution and review layer |
| `Inbox` | **Short-TTL unclear capture only** (shell: queries + empty 新捕获; not a task pool) |
| `当前真实活任务` | Short-lived reality-calibration (superseded inventory → Multica + archive) |

This means:
- Logseq is the durable home for **conclusions**, not a task park
- Actionable process work → Multica; hard-deadline light actions → Apple Reminders
- Inbox near-empty is healthy; archive at `[[I-LOG/Inbox清理归档-2026-10-02]]`
- Logseq is **not** the default home for every project task
- Logseq is **not** the only execution surface

## When to Use

Use this skill when:
- creating or renaming pages in the main vault
- deciding whether content belongs in `AREA`, `PROJ`, `RES`, `LOG`, short-TTL `Inbox`, `Journals`, Multica, or Reminders
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

Vault-local agents also load vault `AGENTS.md` first for file-path / Edit / 禁止事项 / ops. This skill must not contradict AGENTS on shared semantic rules; file `%2F` disk ops are AGENTS-owned.

Vault ops / skills-runtime details live in vault `AGENTS-ops.md` / `[[OS-RES/Vault Agent 运维附录]]`, not in the AGENTS notes core.

Use `logseq-acns-vault` to decide:
- whether content belongs in the vault at all
- which page owns it
- which namespace and page type fit
- whether the destination should be short-TTL `Inbox`, `Journals`, Multica, Reminders, `AREA`, `PROJ`, `RES`, or `LOG`

If destination ownership is already known and the content is a one-liner, use **Fast Path** below — skip remaining vault decisions and hand off to `logseq-acns-write` (`fast-append` / `inbox`).

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

If the content is an unclear short-TTL capture, prefer `[[Inbox]]` briefly — then Multica / promote / discard.
If it is process / half-baked / tool-trial work, prefer **Multica** (do not park long in Inbox).
If it is a hard-deadline light action, prefer **Apple Reminders**.

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

### 4. Inbox vs Journal vs Durable Page vs Multica / Reminders

Use `[[Inbox]]` when (short TTL only):
- the item is newly captured and ownership is unclear
- you need a brief window before Multica / promote / discard
- do **not** use Inbox as a task park or long incubating shelf

Use **Multica** when:
- process-state / half-baked / tool-trial / demote-worthy actionable work

Use **Apple Reminders** when:
- hard-deadline light actions that need reliable close-out

Use `Journals` when:
- the item is about today
- it is part of today's execution slice
- it is part of today's review or AI summary

Use a durable Logseq page when:
- the content is a **stable conclusion** with repeat-reference value
- it belongs to a stable domain, project, resource, or decision trail
- longform: `pages/视频转长文%2F…` / `pages/X-digests%2F…` (not removed `00-Inbox/notes/`)

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

Inbox field schema lives in vault `AGENTS.md` + `acns-inbox` (do not duplicate enums here).

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

## Write exits (decided 2026-09-14)

Long-term **dual exits**, choose by scenario:
- Logseq HTTP API available → `logseq-acns-write` → `logseq-http-transport`
- API unavailable, or editing pages in an IDE → vault disk path per `AGENTS.md` (`pages/%2F` + precise Edit)

Do not invent a third write path. Do not force-merge into a single playbook. Semantics (this skill) stay shared across both exits.

## Fast Path (known ownership, one-liner)

Use this when friction would hurt more than ceremony helps.

**Eligible when all are true:**
- destination page (or layer) is already known: existing Logseq page title, today's `Journals`, Multica, Reminders, or clear short-TTL `[[Inbox]]` capture
- content is one short bullet / one sentence (single block; no multi-section body)
- not creating a new long-term `AREA` / `PROJ` structure
- not a confirmed project delivery action that belongs in Notion

**Then:**
1. Skip the rest of vault semantic deliberation (do not re-run bare-vs-namespace / full Write Workflow).
2. Hand off directly to `logseq-acns-write` with `intent_type: fast-append` (or `inbox` if parking in Inbox).
3. Execute via `logseq-http-transport` (or AGENTS file Edit if already on the disk path).

**Still forbidden on the fast path:**
- dumping transient tasks into AREA hubs
- stuffing multiple headings/lists into one block
- inventing third-party namespaces (`Claude Notes/`, etc.)
- leaving confirmed Notion delivery actions only in Logseq

If ownership is unclear, content needs structure, or a new durable page might be required → leave the fast path and use the full vault → write → transport stack.

## Write Workflow

1. Classify the content:
   - capture
   - daily execution
   - durable area/project/resource/log update
   - project delivery task
   - architectural or governance decision
2. Decide the system layer:
   - short-TTL `Inbox` (capture only)
   - Multica (process / actionable)
   - Apple Reminders (hard deadline)
   - `Journals`
   - durable Logseq conclusion page
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
- move process / temporary actionable work to **Multica**, hard-deadline light actions to **Apple Reminders**, today's slice to `Journals`, project delivery to `Notion`, short-TTL unclear capture to `Inbox`, or a LOG page if the main value is reasoning — **do not dump temporary work into Inbox as a long-term park**

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
- if it is short-TTL unclear capture, put it in `Inbox` briefly
- if it is process / half-baked work, put it in Multica
- if it is a hard-deadline light action, put it in Apple Reminders
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
- route temporary actionable work to Multica / Apple Reminders / `Journals` / `Notion`; use `[[Inbox]]` only for short-TTL unclear capture

## Final Reminder

`OS` / "personal information operating system" is now a worldview label, not an instruction to keep expanding the system.

When in doubt:
- favor low-maintenance operation
- favor machine-readable context
- favor reality over historical residue
- favor clear routing over universal accumulation
