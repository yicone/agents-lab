# Skills Governance Release Checklist

Use this checklist before treating the skills-governance workflow set as ready for routine reuse.

## Scope

Applies to:

- `skills-cli-reconcile`
- `skills-intake-local`
- `skills-promote-global`
- `skills-governance-index.md`

## Checklist

- Each skill has a narrow and non-overlapping responsibility
- Each skill explicitly says what it does **not** handle
- Output fields are structurally aligned across the three skills
- Each skill points to the other related governance skills
- Each skill points state updates to `[[OS-LOG/Skills 当前状态清单]]`
- Each skill explicitly avoids writing live state back into `[[OS-RES/Skills 管理与治理原则]]`
- Each skill has explicit stop conditions
- The governance index gives a short routing rule
- The principle page defines stable rules only
- The state page carries current status only

## Current Release Notes

- The workflow set has been exercised against:
  - `analyze` for CLI reconcile
  - `acns-pdf-summary` for local intake
  - `acns-pdf-summary` for global-promotion evaluation
- The exercises were useful for finding and fixing inventory drift in the state page.

## Next Validation Trigger

Run another pass when:

- a new third-party skill needs CLI takeover
- a new local skill needs intake into the canonical repo
- a user explicitly requests promotion of a local skill to global scope
