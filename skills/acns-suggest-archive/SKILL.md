---
name: acns-suggest-archive
description: Scan project pages and suggest archive candidates using status, recency, and reality checks against journals and current-real-tasks.
metadata:
  owner: agents-lab
  scope: repo
  adapter_targets:
    - /Users/tr/Library/Mobile Documents/iCloud~com~logseq~logseq/Documents/.agents/skills/acns-suggest-archive
---

# ACNS Suggest Archive

## Purpose

Identify project pages that are likely archive candidates and generate a conservative archive recommendation report.

This skill should reduce stale project noise without accidentally archiving still-alive work.

## Trigger

- explicit: `/acns-suggest-archive`
- proactive suggestion when user asks what projects can be archived or cleaned up

## Parameters

```bash
/acns-suggest-archive
/acns-suggest-archive --completed=180
/acns-suggest-archive --paused=90
```

Default thresholds:
- `completed`: 365 days
- `paused`: 180 days
- `canceled` / `cancelled`: immediate suggestion

## Workflow

### Phase 1: Scan

1. Find all `PROJ` files under `pages/`
2. Read key properties near the top of each file
3. Extract `status::`
4. Read file modification time
5. Cross-check whether the project still appears in:
   - recent `Journals`
   - `pages/OS-RES%2F当前真实活任务 v1.md`
6. Only then classify as archive candidate

## Archive Rules

Suggest archive when:
- `status:: completed` and long inactive
- `status:: paused` and long inactive
- `status:: canceled` or `status:: cancelled`

Do not suggest archive merely because file mtime is old if:
- the project is still in `当前真实活任务`
- the project was referenced in recent journals as still alive
- there is explicit current activity elsewhere

## Output

Group candidates into:
- completed candidates
- paused candidates
- canceled candidates
- needs manual review

For each candidate include:
- wikilink
- current status
- last modified date
- days since last modification
- reason for suggestion
- reason not to auto-archive if ambiguity exists

## Archive Action

If the user confirms archiving:
1. update `status::` to `archived` only if appropriate
2. add `archived-date:: YYYY-MM-DD`
3. preserve other properties
4. create an archive operation log if the batch is substantial
