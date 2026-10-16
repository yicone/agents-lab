---
name: acns-route-inbox
description: Use when reviewing Inbox short-TTL capture and routing into Multica, Notion, GitHub, Apple Reminders, Journals, or Logseq conclusion pages. Placement alone is not success — apply the Content Quality Gate before routing; process-state/half-baked items demote to Multica, not long Inbox parking.
metadata:
  owner: agents-lab
  scope: repo
  adapter_targets:
    - <target-project-skill-dir>/acns-route-inbox
---

# ACNS Route Inbox

## Overview

`acns-route-inbox` is the second-stage workflow after short-TTL global capture (Agent-era layering v0.1, post 2026-10-02 cleanup).

**Inbox is a capture shell** (`pages/Inbox.md` = queries + empty 新捕获), **not a task pool**. History was archived at `[[I-LOG/Inbox清理归档-2026-10-02]]`. Demoted process items live in Multica ACM-105..110 — do not dump them back into Inbox.

The goal is:
1. read residual Inbox items (near-empty is healthy)
2. apply the Content Quality Gate (thought density)
3. identify their real object type
4. recommend the narrowest useful destination (Multica / Notion / GitHub / Apple Reminders / Journals / Logseq conclusions)
5. avoid duplicate systems
6. close the loop from capture into the real execution system

Routing improves **placement**. It does not by itself raise **content quality**. Do not treat a successful move as archival quality.

Read these before acting (priority order):
1. `pages/Inbox.md` (live capture shell + Agent-era v0.1)
2. `AGENTS.md`
3. Only if still needed after those: `pages/OS-AREA%2F信息管理%2FAI处理Inbox工作流.md`, `pages/OS-RES%2FAI 信息管家运行规则 v1.md`, `pages/OS-RES%2F当前真实活任务 v1.md` — treat OS-* Inbox workflow pages as **deprecated-until-updated**; prefer Inbox.md + this skill.

## Core Principle

Do not route Inbox items just because another system exists.

Route only when doing so improves one of these:
- execution clarity
- project coordination
- reminder reliability
- reality calibration

If the best destination is still ambiguous: **do not default to a long stay in Inbox**. Prefer short-TTL incubating with an explicit timeout → Multica (process / half-baked / tool-trial), promote when ready, or discard. Make the ambiguity explicit in `ai-note::`.


## Content Quality Gate

Run this **before** destination choice. Placement quality ≠ content quality.

An item is **route-ready** only if it has enough thought density to be useful at the destination without the agent inventing missing meaning.

### Minimum bar (need at least 2 of 3)

1. **Retrievable summary** — one clear sentence stating what this is (not a vague fragment like "看看这个" / "TODO later")
2. **Anchor** — at least one of: `related-to::` / wikilink to an existing page, project name, or explicit Notion/GitHub target
3. **Why / next** — one short clause: why it matters, or the concrete next step

### Failures → short-TTL incubating (not long-term Inbox park)

If the bar fails:
- do **not** route to Notion / GitHub / Logseq durable conclusion pages / Journals just to clear Inbox
- set `inbox-status:: incubating` (or keep `raw` if still unclassified) with **short TTL**
- add `ai-note::` naming what is missing (summary / anchor / why-next)
- on timeout: demote to **Multica** (process-state / half-baked / tool-trial), promote when density improves, or discard — do not keep stacking in Inbox
- optionally ask the user one sharp clarifying question; do not pad the note with invented detail

### Soft exceptions (may route with a thinner body)

- **Apple Reminders** (hard-deadline layer): short self-explanatory action + clear time trigger (still needs an unambiguous verb phrase)
- **Journals today-slice**: already-known today's focus phrase the user just confirmed in conversation
- Still record `ai-note::` if anchor is missing

### Pass → continue Decision Order

Only after the gate passes (or a soft exception applies) proceed to Action / Notion / GitHub / etc.

## Decision Order

For each item, answer in this order:
0. Does it pass the Content Quality Gate? If no → short-TTL Inbox (`incubating`); on timeout → Multica / promote / discard — stop
1. Is it **process-state / half-baked / tool-trial / demote-worthy work**? If yes → **Multica** (not Inbox parking; ACM Lab etc.)
2. Is it really an `Action`
3. If yes, is it already a clear project delivery action
4. If yes, does it belong in GitHub or Notion
5. If not a project task, does it need **hard-deadline** reminder behavior → **Apple Reminders**
6. If not a completion-oriented action, does it belong in Logseq as a **stable conclusion** (namespaced page), not as a task
7. If it is today's real focus, should it also appear in `Journals`

## Default Destinations

### Keep in Inbox (short-TTL capture only)

Use when:
- ownership is still unclear **and** the item is fresh (short TTL)
- the item fails the Content Quality Gate (thin / no anchor / no why-next) **temporarily**
- you need a brief incubating window before Multica / promote / discard

Do **not** use Inbox for:
- long-term parking of incubating / ambiguous items
- process-state / half-baked / tool-trial work (→ Multica)
- hard-deadline light actions (→ Apple Reminders)
- stable conclusions (→ namespaced Logseq pages)
- demoted backlog (already in Multica ACM-105..110; do not dump back)

Expected metadata:
- `inbox-status:: raw` or `incubating`
- `ai-note::` explains uncertainty **and** intended next hop / TTL

### Route to Multica

Use when:
- the item is process-state, half-baked, tool-trial, or demote-worthy work
- it needs an actionable home but is not yet a stable vault conclusion
- it would otherwise clutter Inbox as long-lived incubating

Important:
- Multica is the **process / actionable work home** (e.g. ACM Lab; demoted set ACM-105..110)
- do not invent issue URLs; name Multica + issue ids when known
- do not dump Multica demotions back into Inbox

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

### Route to Apple Reminders (hard-deadline layer)

Use when:
- the item has a **hard deadline** or must be closed by a person on time
- the user is likely to act on it from mobile
- the action is short and self-explanatory
- it can be executed without opening heavy context

Note: Apple Reminders is the hard-deadline / light-action layer — not a project task pool and not a substitute for Multica process work.

### Route to Journals

Use when:
- the item has become part of today's execution slice
- the item is one of today's real priorities
- the item should appear in the daily cockpit rather than only in a long-term system

### Route to Logseq (stable conclusions only)

Use when:
- the item has become a **stable conclusion** worth re-reading (decision, durable reference, longform note)
- it needs explanation beyond 2 sentences and belongs on a namespaced page
- longform notes land at `pages/视频转长文%2F…` or `pages/X-digests%2F…` (not under removed `00-Inbox/notes/`)

Do **not** use Logseq durable pages as a task park. Process / half-baked work → Multica.

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
- `留在 Inbox`（仅 short-TTL / 质量门槛未过；近空=健康）
- `进入 Multica`（过程态 / 半成品 / 工具试用 / demote）
- `进入 Notion Tasks`
- `进入 GitHub Issues`
- `进入 Apple Reminders`（硬 deadline 轻动作）
- `进入 Journals`
- `回到 Logseq`（稳定结论页）
- `待人工确认`

For each item, provide:
- original item label
- content-quality: `pass` | `fail` | `soft-exception` (and which bar items were missing)
- inferred object type
- recommended destination
- short reason
- whether confirmation is required
