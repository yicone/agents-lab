# Skills Governance Release Checklist

Use this checklist before treating the skills-governance workflow set as ready for routine reuse.

## Scope

Applies to:

- `skills-cli-reconcile`
- `skills-intake-local`
- `skills-promote-global`
- `skills-governance-audit`
- `skills-lifecycle-manager`
- `skills-governance/docs/index.md`
- `skills-governance/config/source-types.yaml`
- `skills-governance/config/skills-inventory.json`
- `skills-governance/config/skills-inventory.schema.json`
- `skills-governance/scripts/validate_skills_inventory.py`
- `skills-governance/backlog.md`

## Checklist

- Each skill has a narrow and non-overlapping responsibility
- Each skill explicitly says what it does **not** handle
- Output fields are structurally aligned across the governance skills where they intentionally overlap
- Each skill points to the other related governance skills
- Each skill points inventory updates to `skills-governance/config/skills-inventory.json`; `[[OS-LOG/Skills 当前状态清单]]` is a human mirror, not a write target
- Each skill explicitly avoids writing live state back into `[[OS-RES/Skills 管理与治理原则]]`
- Each skill has explicit stop conditions
- The governance index gives a short routing rule
- The package README identifies source authority separately from runtime visibility
- The audit skill stays read-only and routes findings instead of mutating layouts
- `conflict_report.py` stays read-only and reports name collision separately from responsibility overlap, behavior divergence, scope shadowing, dependency conflicts, and trigger ambiguity
- The principle page defines stable rules only
- The state page carries current status only
- Source class and update authority are explicit for every reviewed Skill
- Non-local upgrades have a baseline and diff review when technically possible
- Local customization is recorded instead of silently overwriting upstream content
- Run `python3 skills-governance/scripts/doctor.py` and require exit code 0. It checks recorded repo/user-global adapters and scans every adapter parent directory for orphan broken symlinks. `declared_target` entries are checked only when a concrete target is bound; null targets are reported as unchecked placeholders, not validated target-scoped installations. Before release, bind and verify any target-scoped installation required for that release. `skill_guard.py doctor` additionally discovers user/project adapter directories. With `--repair`, active/staged skills prefer an existing canonical directory containing `SKILL.md`, falling back to an existing recorded target. Retired broken links are removed; quarantined/unknown skills and orphan links remain unresolved. Per-link repair failures are reported without stopping the scan; remaining failures return nonzero. Use `--format text` for readable output.

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
