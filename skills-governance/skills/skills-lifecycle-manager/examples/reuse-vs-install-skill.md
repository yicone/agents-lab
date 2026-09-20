# Reuse Vs Install Example

## Goal

Show the smallest valid case where the user first needs a lifecycle decision rather than an immediate governance action.

## Case

- `task_or_domain`: `skill governance for an existing repo`
- known context:
  - the repo may already contain local governance skills
  - there may also be third-party installer-managed skills available globally
  - the immediate question is whether to reuse, install, adapt, or build

## Expected Routing

- use `skills-lifecycle-manager`
- do not start with `skills-governance-audit` unless the user first asks for a read-only scan of the existing skill set
- do not start with `skills-cli-reconcile`
- do not start with `skills-intake-local`
- do not start with `skills-promote-global`

## Expected Decision Shape

- `recommended_path`: `reuse_local` or `route_to_governance_workflow`
- likely follow-up:
  - reuse an existing local governance skill
  - or route to `skills-governance-audit` if the real issue is ownership/conflict clarity

## Expected Output Shape

```text
Task Or Domain: skill governance for an existing repo
Candidate Sources:
- local: skills-governance-audit
- local: skills-cli-reconcile
- local: skills-intake-local
- build-new option
Existing Fit Assessment:
- skills-governance-audit -> fits when the first need is read-only scanning
- skills-cli-reconcile -> fits only for known third-party takeover
- skills-intake-local -> fits only for self-authored canonical-source intake
Security Screening Summary:
- local governance skills -> low additional install risk
Recommended Path: reuse_local
Why Not The Other Paths:
- build_new -> existing local governance skills already cover the likely need
- install_third_party -> not needed before checking local coverage
Follow-up:
- if the first unresolved question is ownership/conflicts, invoke skills-governance-audit
```

## What This Example Guards

- lifecycle discovery is not confused with audit
- install/build decisions happen after checking local coverage
- repo-specific documentation sync is not treated as a default responsibility of this skill
