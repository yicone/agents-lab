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
- `config/`: source classes, the machine-readable skills inventory (`skills-inventory.json`), and the customization record schema
- `customizations/`: records of local deltas on non-local Skills (`<skill>/customization.json` plus overlay payloads under `<skill>/delta/`); upstream baselines are never stored here
- `scripts/`: inventory validation, the read-only drift report, the read-only conflict report, the read-only token footprint report, the read-only capability-declaration check, customization record check and overlay apply, local.patch create/rebase for patch-style overlays (`skill_patch.py`), plus snapshot, stage, diff, gate, activate, and rollback commands
- `tests/`: focused tests for the safety commands. Representative conflict cases live in `tests/fixtures/conflicts/`.
- `changelog/`: append-only per-skill JSONL entries written by `activate`; the drift report flags changes it cannot match here
- `backlog.md`: pointer only; Multica issue ACM-122 is the source of truth for backlog status

Ordinary Skills remain under the repository's top-level `skills/<skill-name>/`. The nested `skills/` here is intentionally limited to the governance workflow itself.

## Default Lifecycle

1. Inventory what the runtime can see and identify the source class.
2. Record the source authority and current install shape.
3. Capture a baseline, then place the candidate in staging instead of the live runtime path.
4. Review the snapshot-vs-staging diff and security signals; activate only after the gate passes. The gate calls SkilLock for capability drift when `skil-lock` is on PATH, and records a risk-signal fallback when it is not. The same SkilLock behavior map is compared with the `capabilities` frontmatter; overreach blocks activation and writes no changelog. See [docs/capability-declaration.md](docs/capability-declaration.md). Activation that passes the gate appends a per-skill entry to `changelog/<skill>.jsonl` recording the actual old-to-new change.
5. Estimate context cost with `python3 skills-governance/scripts/token_footprint.py --format text`. The report uses `ceil(utf-8 bytes / 4)`. `token_over_budget` means the eager `SKILL.md` is over 5,000 static tokens; the same threshold applies to every scope. Referenced docs are quantified separately for visibility but never gate the skill. The downgrade suggestion is `references_subdirectory` (on-demand `references/`). `--check` exits 2 when the gate fails. The command does not modify skills or inventory.
6. Check conflicts with `python3 skills-governance/scripts/conflict_report.py --format text`. The report separates name collision, responsibility overlap, behavior divergence, scope shadowing, dependency conflicts, and trigger ambiguity. It does not select which skill runs. Which skill runs is [docs/routing-precedence.md](docs/routing-precedence.md): target adapter, then project, then canonical local, then runtime plugin, then runtime builtin, then global. Same-layer overlap and the stop conditions on that page are Stop-and-Ask.
7. Decide whether to update, customize, fork, quarantine, or keep the current version. Customizing a non-local Skill means choosing one of the five recorded modes and storing a delta record; see [docs/customization.md](docs/customization.md).
8. Update `config/skills-inventory.json` and the Multica backlog (ACM-122). Project the Logseq status page from that file when a human view is needed. Do not mistake runtime visibility for canonical ownership.

## Routing

Precedence and Stop-and-Ask rules: [docs/routing-precedence.md](docs/routing-precedence.md).

- `skills-governance-audit`: read-only inventory, provenance, conflict, naming, drift, and token-footprint scan
- `skills-lifecycle-manager`: reuse/install/fork/build choice across all source classes
- `skills-cli-reconcile`: restore a confirmed third-party Skill to Skills CLI ownership
- `skills-intake-local`: intake a self-authored Skill into canonical Git source
- `skills-promote-global`: evaluate whether a locally owned Skill is suitable for global scope

The current package is a governance control plane, not a replacement for runtime package managers or Skills CLI. It records and reviews changes before they become active where the underlying tool allows that.

See [docs/upgrade-review.md](docs/upgrade-review.md) for the P0 command workflow and community implementation comparison. See [docs/upgrade-diff-review.md](docs/upgrade-diff-review.md) for how to read a diff report and decide whether to activate.

### Adapter doctor

Run `python3 skills-governance/scripts/skill_guard.py doctor` from the repository
for a readable adapter health report. Add `--format json` for the shared doctor
contract (`broken`, unresolved `count`, and `repaired`). Exit status is 1 while
problems remain, otherwise 0. The entry point reuses `doctor.py` and
`broken_links.py` scanning and repair.

Discovery includes user `~/.agents/skills` and `~/.codex/skills`, the canonical
repository, and `.agents/skills` / `.codex/skills` in the current directory and
its ancestors. Inventory-recorded adapter parents also cover external projects.
`--adapter-root` replaces discovery roots; recorded adapters remain included.
Use `--inventory`, `--repo-root`, and an isolated HOME for fixture validation.

`--repair` prefers the current inventory canonical directory with `SKILL.md`;
if unavailable or missing `SKILL.md`, it falls back to an existing recorded adapter target.
Discovered names must match exactly one inventory entry. Broken links belonging
to explicitly retired entries are removed. Only active/staged skills may be relinked; quarantined/unknown entries and orphan
links remain reported for manual review. Directory aliases are resolved before
deduplication, and name matching applies only to top-level adapters. Healthy links
are preserved; an individual repair failure does not stop other repairs.
