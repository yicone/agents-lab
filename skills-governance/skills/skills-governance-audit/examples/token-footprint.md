# Token Footprint Example

## Goal

Show a read-only context-budget audit that quantifies static tokens and recommends a downgrade for an oversized global skill.

## Case

- `audit_scope`: the global skill catalog plus one repository
- `focus`: `footprint`
- known concern:
  - `morphism-mapper` was removed after its body became large enough to dominate a session

## Command

```bash
python3 skills-governance/scripts/token_footprint.py --format text
```

Add `--check` when the audit should fail closed. The command exits 2 when the gate is `fail`.

## Expected Findings

- `estimator`: `bytes_div_4` (`ceil(utf-8 bytes / 4)`)
- `token_over_budget` when `SKILL.md` is over 2,000 static tokens
- `token_over_budget` when a globally injected skill's `SKILL.md` plus referenced docs are over 2,000 static tokens
- `size_over_budget` when `SKILL.md` is over 16,384 bytes
- a repository skill whose supporting text already lives in `references/` stays under the eager token gate

## Expected Downgrade

- `references_subdirectory`: move supporting material out of `SKILL.md` into `references/` and load it on demand
- `target_scoped`: install the skill on the target project instead of injecting it from the global catalog

## Expected Output Shape

```text
Token Footprint
Gate: fail
- skill: morphism-mapper
  scope: machine
  eager_tokens: 4200
  referenced_tokens: 800
  static_tokens: 5000
  warnings: token_over_budget, size_over_budget
  suggestion: references_subdirectory — Move supporting material out of SKILL.md into a references/ subdirectory and load those files only when a task needs them.
  suggestion: target_scoped — Stop injecting this skill from the global catalog. Install it as a target-scoped skill that loads only in the project that needs it.
```

## What This Example Guards

- the footprint report stays read-only
- referenced documents are quantified separately from the eager `SKILL.md`
- an over-budget global skill gets both downgrade paths
