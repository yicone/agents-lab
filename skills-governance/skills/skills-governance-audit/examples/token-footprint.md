# Token Footprint Example

## Goal

Show a read-only context-budget audit that quantifies static tokens and recommends a downgrade for an oversized eager `SKILL.md`.

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
- `token_over_budget` when the eager `SKILL.md` is over 5,000 static tokens, at any scope
- referenced docs are quantified separately (`referenced_tokens`, per-file `linked` flag) and never gate the skill
- a repository skill whose supporting text already lives in `references/` stays under the eager token gate

## Expected Downgrade

- `references_subdirectory`: move supporting material out of `SKILL.md` into `references/` and load it on demand

## Expected Output Shape

```text
Token Footprint
Gate: fail
- skill: morphism-mapper
  scope: machine
  eager_tokens: 5500
  referenced_tokens: 800
  package_tokens: 6300
  warnings: token_over_budget
  suggestion: references_subdirectory — Move supporting material out of SKILL.md into a references/ subdirectory and load those files only when a task needs them.
```

## What This Example Guards

- the footprint report stays read-only
- referenced documents are quantified separately from the eager `SKILL.md`
- an over-budget skill gets the references/ downgrade path regardless of scope
