# Logseq Vault Skill Inventory

This file is a repository-side stub. It does not store the live inventory.

## Machine-Readable Source

The source of truth is:

- `skills-governance/config/skills-inventory.json`

Validate it with `skills-governance/scripts/validate_skills_inventory.py`. The schema is `skills-inventory/v1` in `skills-governance/config/skills-inventory.schema.json`. Source classes remain in `skills-governance/config/source-types.yaml`.

That JSON record is what agents read and write for:

- source class, authority, baseline, adapters, and status
- `review_time`, `baseline_ref`, and `current_version` for drift reports
- local customization, customization mode, compatibility constraints, and conflict status

## Human Mirror

`[[OS-LOG/Skills 当前状态清单]]` stays as the human-facing view. When someone needs that page updated, project from the JSON in one direction. Agents do not write the Logseq page as a second inventory, and they do not write live inventory state into `[[OS-RES/Skills 管理与治理原则]]`.

## Repository Role

Repository docs should keep:

- durable principles
- layout and versioning rules
- canonical-source rules for locally owned skills
- the machine-readable inventory and its validator
