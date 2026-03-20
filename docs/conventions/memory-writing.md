# Writing Reusable Memories

Only store information that is likely to save time in future sessions.

## Good Candidates

- stable preferences
- taxonomy updates
- evaluation conclusions
- repeated failure modes
- integration decisions
- comparison results with evidence

## Required Tags

Every repo-related memory must include:

- `proj/agents-lab`

It should also include:

- at least one `axis/*`
- relevant `product/*` or `topic/*` tags when applicable
- a `decision/*` or `workflow/*` tag when it represents a conclusion or repeatable procedure

## Writing Style

- Keep entries short
- State the conclusion first
- Record concrete evidence or source pointers when relevant
- Prefer reusable abstractions over task-local narration

## Minimal Template

```text
title: short_stable_name
source_type: article | repo | workflow | experiment

Core conclusion: one or two sentences.

Signals:
- fact or observation
- fact or observation

Why it matters:
- future decision or workflow impact
```
