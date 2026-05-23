# Keep Local Promotion Example

## Goal

Show the minimal case where a useful local skill is still too coupled to be promoted globally.

## Case

- `skill_name`: `acns-pdf-summary`
- `skill_path`: `/Users/tr/Workspace/agents-lab/skills/acns-pdf-summary`
- `current_scope`: `target_scoped`
- current usage:
  - adapted into the Logseq vault only
  - depends on ACNS naming and Logseq highlight/page conventions

## Expected Routing

- use `skills-promote-global`
- do not use `skills-intake-local` unless canonical source is still missing
- do not use `skills-cli-reconcile`

## Expected Decision

- `promotion_decision`: `keep_local`
- `current_scope`: `target_scoped`
- `naming_risk`: `medium`
- `conflict_risk`: `low`
- `risk_summary`: `coupling`

## Expected Output Shape

```text
Skill: acns-pdf-summary
Promotion Decision: keep_local
Provenance: local
Current Scope: target_scoped
Coupling Findings:
- depends on ACNS-specific naming
- depends on Logseq PDF highlight page structure
- depends on vault-specific ownership and routing assumptions
Naming Risk: medium
Conflict Risk: low
Risk Summary: coupling
Required Changes:
- extract a generic PDF summary core
- remove ACNS-specific naming or create a generalized alias
- replace vault-specific routing assumptions with parameters
Recommended Next Step: keep this skill target-scoped until a generalization pass is complete
```

## What This Example Guards

- “useful twice” is not treated as enough reason to globalize
- project- or vault-specific coupling is surfaced before any move into a global directory
