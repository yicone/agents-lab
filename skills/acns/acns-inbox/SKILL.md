---
name: acns-inbox
description: Use when ownership is unclear and a short-TTL capture into the ACNS Inbox shell is needed before routing to Multica / Reminders / conclusion pages. Inbox is not a task park. If ownership is already known, prefer Multica, Apple Reminders, Notion, or Logseq fast-append instead of Inbox.
metadata:
  owner: agents-lab
  scope: repo
  adapter_targets:
    - <target-project-skill-dir>/acns-inbox
---

# ACNS Inbox

## Overview

`acns-inbox` is the **short-TTL capture shell** workflow for the user's ACNS (Agent-era layering v0.1).

`pages/Inbox.md` = queries + empty 「新捕获」. **Inbox is not a task pool.** History archived at `[[I-LOG/Inbox清理归档-2026-10-02]]`. Demoted items live in Multica ACM-105..110 — do not dump them back here.

Its role is:
1. capture quickly when ownership is unclear
2. classify lightly
3. preserve enough context for later routing (short TTL)
4. avoid premature system spread — and avoid long-term Inbox parking

Read when ownership is unclear or routing is non-trivial (priority):
1. `pages/Inbox.md`
2. `AGENTS.md`
3. OS-* Inbox workflow pages only if needed — treat as **deprecated-until-updated** (`pages/OS-AREA%2F信息管理%2FAI处理Inbox工作流.md`, `pages/OS-RES%2FAI 信息管家运行规则 v1.md`)

For a known-ownership one-liner, skip these reads and use Fast Path / Multica / Reminders / Notion as appropriate.

## Core Principle

Unclear short-TTL capture may go to `[[Inbox]]`. Actionable process work does **not** park there.

Do this when:
- ownership is unclear **and** you need a brief capture window
- project association is unclear for a moment
- the user is thinking out loud and a one-line capture buys time before Multica / promote / discard

Do **not** use Inbox as a reflex when:
- the item is process-state / half-baked / tool-trial → **Multica**
- the item is a hard-deadline light action → **Apple Reminders**
- the item is already a clear project delivery task → **Notion**
- the item is a stable conclusion → namespaced Logseq page

Rule:
- unclear short-TTL capture -> `[[Inbox]]` (timeout → Multica / promote / discard)
- process / demote work -> Multica (not Inbox stack)
- hard-deadline light action -> Apple Reminders
- clear project delivery -> Notion
- vault = stable conclusions only


## Fast Path (skip Inbox ceremony)

If the user already names an existing Logseq page / today's Journal / a clear Notion delivery task, **do not** force Inbox.

| Known destination | Action |
|---|---|
| Existing Logseq page + one sentence | `logseq-acns-write` `fast-append` → transport (skip vault deliberation) |
| Today's focus / execution slice | append to `Journals` via `fast-append` |
| Confirmed project delivery | Notion task pool — not Inbox, not Logseq task dump |
| Process / half-baked / tool-trial | **Multica** — not Inbox |
| Hard-deadline light action | **Apple Reminders** — not Inbox |
| Ownership still unclear (short TTL) | stay on this Inbox skill (minimal metadata below) |

For unclear one-liners into Inbox: write one block with light metadata only — do not over-read reference pages or invent structure.

Capture may be thin; **routing** must not pretend thin items are done — `acns-route-inbox` Content Quality Gate keeps low-density items `incubating` until summary/anchor/why-next improve.

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
- the item is process-state / half-baked / tool-trial → write to **Multica**
- it is a hard-deadline light action → **Apple Reminders**
- the item is already a confirmed project delivery task → **Notion**
- it clearly belongs to an active project in `Notion`
- it is already today's execution slice and should go to `Journals`
- it is a stable conclusion / durable context → namespaced Logseq page (`pages/视频转长文%2F…`, `pages/X-digests%2F…`, AREA/PROJ/RES/LOG)
- it was already demoted (Multica ACM-105..110) — do not dump back into Inbox

## Output Location

When Inbox is the right destination (short-TTL only), always write to:
- `pages/Inbox.md`
- section: `## 📥 新捕获（AI写入区）`

Near-empty 新捕获 = healthy. Archive: `[[I-LOG/Inbox清理归档-2026-10-02]]`. Routing: `acns-route-inbox`.
