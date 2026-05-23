# Repository Audit Example

## Goal

Show a read-only audit over the repository's current skills set, focusing on conflict signals and naming coherence.

## Case

- `audit_scope`: `agents-lab skills/ and repo-scoped adapters`
- `focus`: `full`
- known concern:
  - `logseq-acns-vault` and `logseq-acns-write` may create routing hesitation because both are relevant to Logseq-writing requests

## Expected Routing

- use `skills-governance-audit`
- do not immediately use `skills-intake-local`
- do not immediately use `skills-cli-reconcile`
- do not immediately use `skills-promote-global`

## Expected Findings

- `conflict_findings` should include:
  - `responsibility_overlap`
  - skills: `logseq-acns-vault`, `logseq-acns-write`
  - explanation: one owns vault semantics and page-ownership policy, while the other owns write-plan construction; without explicit sequencing, an agent may hesitate about whether one or both should load
- `naming_findings` should likely be:
  - `taxonomy_mixed_but_explained`
  - evidence:
    - `acns-*` names are user-facing ACNS workflows
    - `logseq-acns-*` names are Logseq-specific policy-layer helpers
    - `logseq-http-transport` is a transport-layer helper and intentionally not named as a user-facing ACNS workflow

## Expected Output Shape

```text
Audit Scope: agents-lab skills/ and repo-scoped adapters
Scope Summary:
- canonical source lives under skills/
- repo-scoped adapters exist under .agents/skills/ and .codex/skills/
Local Skills:
- acns-inbox
- acns-route-inbox
- acns-weekly-review
- acns-skills-manager
- acns-suggest-archive
- acns-pdf-summary
- logseq-acns-vault
- logseq-acns-write
- logseq-http-transport
Conflict Findings:
- Type: responsibility_overlap
  Skills: logseq-acns-vault, logseq-acns-write
  Why It Matters: both can appear relevant for Logseq write requests unless the semantic-planning boundary is explicit
  Recommended Winner Or Boundary: load both when the task needs Logseq semantic classification plus ACNS write-plan generation; otherwise prefer logseq-acns-vault for semantic placement questions and logseq-acns-write for write-plan generation
Naming Findings:
- Status: taxonomy_mixed_but_explained
  Evidence: acns-* = user-facing ACNS workflows; logseq-acns-* = Logseq policy helpers; logseq-http-transport = transport layer
Routing Recommendations:
- responsibility overlap finding -> manual clarification only
State Update Suggestions:
- add a short coordination note or cross-reference between logseq-acns-vault and logseq-acns-write
Follow-up:
- tighten trigger language if future cases show repeated hesitation
```

## What This Example Guards

- audit remains read-only
- responsibility overlap is treated as a first-class conflict type
- mixed naming families are not treated as wrong by default
