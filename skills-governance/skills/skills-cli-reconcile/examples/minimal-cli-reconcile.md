# Minimal CLI Reconcile Example

## Goal

Show the smallest valid case for bringing a third-party skill back under `skills CLI` ownership.

## Case

- `skill_name`: `analyze`
- `skill_path`: `<target-project-skill-dir>/analyze`
- `known_provenance`: `AllenAI2014/ai-investment-advisor@analyze`
- current situation:
  - the skill already exists locally
  - it is third-party, not self-authored
  - it has already been reinstalled via `npx skills add ... --skill analyze`
  - prior installation reported elevated risk

## Expected Routing

- use `skills-cli-reconcile`
- do not use `skills-intake-local`
- do not use `skills-promote-global`

## Expected Classification

- `classification`: `third_party_cli_managed`
- `scope`: `third_party`
- `current_install_shape`: `real directory`
- `action_taken`: `none` or `already CLI-managed`

## Expected Output Shape

```text
Skill: analyze
Classification: third_party_cli_managed
Provenance: AllenAI2014/ai-investment-advisor@analyze
Scope: third_party
Current Shape: real directory
Action Taken: already CLI-managed
Risk Summary: Gen=Critical Risk, Socket=0 alerts, Snyk=Med Risk
Required Changes:
- none for ownership
- keep risk review pending
Follow-up:
- review scripts, dependencies, and network behavior
- keep risk status in [[OS-LOG/Skills 当前状态清单]]
```

## What This Example Guards

- a locally installed third-party skill is not mistaken for `local_custom`
- provenance is surfaced before reinstall logic
- `CLI-managed` is not confused with `risk-reviewed`
