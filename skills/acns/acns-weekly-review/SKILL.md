---
name: acns-weekly-review
description: Generate a weekly review for the user's ACNS by analyzing Journals, current-real-tasks, project progress, and task-system reality across Logseq and Notion.
metadata:
  owner: agents-lab
  scope: repo
  adapter_targets:
    - <target-project-skill-dir>/acns-weekly-review
---

# ACNS Weekly Review

## Overview

Generate a weekly review that reflects the user's current three-core system instead of treating Logseq alone as the task source.

The review should answer:
- what actually moved this week
- what stayed alive but did not move
- where system drift still exists
- what should become next week's realistic focus

## Usage

User input:
- `/acns-weekly-review`
- `/acns-weekly-review [date]`

## Workflow

1. Determine the time range
   - default: past 7 days
   - specified date: use that week's Monday to Sunday
2. Collect sources
   - `journals/YYYY_MM_DD.md`
   - **Multica** active + demoted (e.g. ACM-105..110) — primary actionable inventory
   - Apple Reminders hard-deadline items when available
   - `pages/OS-RES%2F当前真实活任务 v1.md` (calibration; may be superseded — see banner)
   - relevant `pages/*-PROJ%2F*.md` and `pages/*-LOG%2F*.md`
   - `pages/Inbox.md` — expect **near-empty**; residual raw/incubating = debt only
   - archive context: `[[I-LOG/Inbox清理归档-2026-10-02]]`
   - when available, current `Notion` project/task state for active projects
3. Analyze across layers
   - daily execution signals from `Journals`
   - actionable movement from Multica + Reminders + Notion
   - reality calibration from `当前真实活任务` (if still maintained)
   - project movement from `Notion` and project pages
   - system drift: tasks alive in conversation but missing from Multica/Reminders/Notion
   - Inbox should stay near-empty; volume there is debt, not progress
4. Generate the review
   - write to current Journal or a dedicated weekly review page

## Review Structure

Include:
- 本周现实推进
- 本周完成事项
- 仍然活着但未推进的事项
- 系统偏差与噪声
- 值得沉淀的洞察
- 下周最现实的 1-3 个重点

## Output Location

Default:
- append to current day's Journal under `## 📅 周回顾`

Optional:
- create a dedicated page such as `pages/I-LOG%2F周回顾-YYYY-WXX.md`

## Important Rules

- do not treat `Inbox` volume as weekly progress — near-empty is healthy
- collect Multica active/demoted as the process-task reality check
- do not confuse project context notes with real task movement
- use `Notion` task state when it is the actual project task pool
- use `Journals` to understand what the user really touched this week
