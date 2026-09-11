# Runtime Adapter Normalization Example

## Goal

Show the canonical batch case for local-skill intake: multiple self-authored skills had runtime-facing directories acting like the source of truth and were normalized back into the canonical repo.

## Case

- `case_classification`: `runtime_adapter_normalization`
- `shared_trigger`: runtime-facing directories in a Logseq vault were being used like long-term source locations
- `batch_members`:
  - `acns-inbox`
  - `acns-route-inbox`
  - `acns-weekly-review`
  - `acns-suggest-archive`
  - `acns-pdf-summary`
- canonical destination:
  - `<skills-canonical-repo>/skills/<skill-name>`
- adapter destination:
  - `<target-project-skill-dir>/<skill-name>`

## Expected Routing

- use `skills-intake-local`
- do not use `skills-cli-reconcile`
- do not use `skills-promote-global`

## Expected Case-Level Result

- `case_classification`: `runtime_adapter_normalization`
- `shared_trigger`: runtime-facing directory acting as source of truth
- `risk_summary`: `source_of_truth_drift` or `adapter_misuse`

## Expected Output Shape

```text
Case Classification: runtime_adapter_normalization
Shared Trigger: runtime-facing directory acting as source of truth
Batch Members:
- acns-inbox
- acns-route-inbox
- acns-weekly-review
- acns-suggest-archive
- acns-pdf-summary
Verification Result: canonical source moved to <skills-canonical-repo>/skills/<skill-name>; target path left as adapter only
Per-Skill Records:
- Skill: acns-inbox
  Ownership: local_custom
  Scope: target_scoped
  Canonical Source: <skills-canonical-repo>/skills/acns/acns-inbox
  Adapter Strategy: target
  Risk Summary: source_of_truth_drift
  Changes Made: moved canonical source into repo and replaced target path with adapter link
  Follow-up: keep target-scoped unless generalized later
```

## What This Example Guards

- directory-level normalization is treated as a first-class governance case
- case-level classification happens before per-skill records
- runtime-facing directories are not left acting as source of truth
