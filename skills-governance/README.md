# Local Skills Governance

This directory is the maintained home for governance of Skills visible on this machine.

It covers the main source classes that may all be visible to an agent runtime:

- self-authored local Skills
- Skills bundled by an agent runtime
- Skills introduced by enabled runtime plugins
- Skills installed or updated through Skills CLI

It also covers locally customized copies of non-local Skills. Visibility in a runtime is not ownership: a runtime or plugin may provide a Skill, while the governance record still needs to identify its source authority, update channel, local customization, and conflict risk.

## Package Layout

- `skills/`: governance-specific agent entrypoints
- `docs/`: durable governance rules, operating guides, and state-note conventions
- `config/`: source classes and the machine-readable skills inventory (`skills-inventory.json`)
- `scripts/`: inventory validation, the read-only drift report, plus snapshot, stage, diff, gate, activate, and rollback commands
- `tests/`: focused tests for the safety commands
- `backlog.md`: pointer only; Multica issue ACM-122 is the source of truth for backlog status

Ordinary Skills remain under the repository's top-level `skills/<skill-name>/`. The nested `skills/` here is intentionally limited to the governance workflow itself.

## Default Lifecycle

1. Inventory what the runtime can see and identify the source class.
2. Record the source authority and current install shape.
3. Capture a baseline, then place the candidate in staging instead of the live runtime path.
4. Review the snapshot-vs-staging diff and security signals; activate only after the gate passes. The gate calls SkilLock for capability drift when `skil-lock` is on PATH, and records a risk-signal fallback when it is not.
5. Check for responsibility overlap and behavior conflicts.
6. Decide whether to update, customize, fork, quarantine, or keep the current version.
7. Update `config/skills-inventory.json` and the Multica backlog (ACM-122). Project the Logseq status page from that file when a human view is needed. Do not mistake runtime visibility for canonical ownership.

## Routing

- `skills-governance-audit`: read-only inventory, provenance, conflict, naming, and drift scan
- `skills-lifecycle-manager`: reuse/install/fork/build choice across all source classes
- `skills-cli-reconcile`: restore a confirmed third-party Skill to Skills CLI ownership
- `skills-intake-local`: intake a self-authored Skill into canonical Git source
- `skills-promote-global`: evaluate whether a locally owned Skill is suitable for global scope

The current package is a governance control plane, not a replacement for runtime package managers or Skills CLI. It records and reviews changes before they become active where the underlying tool allows that.

See [docs/upgrade-review.md](docs/upgrade-review.md) for the P0 command workflow and community implementation comparison.
