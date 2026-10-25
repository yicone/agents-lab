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
- `scripts/`: inventory validation, the inventory sync (`sync_skills_inventory.py`, dry-run unless `--apply`), the read-only drift report, the read-only conflict report, the read-only token footprint report, the read-only capability-declaration check, customization record check and overlay apply, local.patch create/rebase for patch-style overlays (`skill_patch.py`), plus snapshot, stage, diff, gate, activate, and rollback commands
- `tests/`: focused tests for the safety commands. Representative conflict cases live in `tests/fixtures/conflicts/`.
- `changelog/`: append-only per-skill JSONL entries written by `activate`; the drift report flags changes it cannot match here
- `backlog.md`: pointer only; Multica issue ACM-122 is the source of truth for backlog status

Ordinary Skills remain under the repository's top-level `skills/<skill-name>/`. The nested `skills/` here is intentionally limited to the governance workflow itself.

## Default Lifecycle

1. Inventory what the runtime can see and identify the source class.
2. Record the source authority and current install shape.
3. Capture a baseline, then place the candidate in staging instead of the live runtime path.
4. Review the snapshot-vs-staging diff and security signals; activate only after the gate passes. The gate calls SkilLock for capability drift when the pinned fork binary passes its version check, and records a risk-signal fallback when it is not. The same SkilLock behavior map is compared with the `capabilities` frontmatter; overreach blocks activation and writes no changelog. See [docs/capability-declaration.md](docs/capability-declaration.md). Activation that passes the gate appends a per-skill entry to `changelog/<skill>.jsonl` recording the actual old-to-new change.
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

### Inventory sync

`python3 skills-governance/scripts/sync_skills_inventory.py` prints a dry-run
summary. `--apply` writes `config/skills-inventory.json` only when that result
still validates. New installs record a name, path, source, and hash or
`baseline_ref`. The command does not set `review_time`, and it does not change
`customization_mode` or `compatibility_constraints`. A hash or provider-version
change stays on the recorded baseline and is reported as needs-review. A
missing install is marked `status: unknown` and kept. This command does not
install a schedule.

### Pinned SkilLock fork (ACM-540)

Source: `https://github.com/yicone/skil-lock`, commit
`2fe41285ab69b347f1a3d53ac003c6cff33b3a62` (currently local, not pushed).
The Go module still identifies as `github.com/skills-lock/skil-lock`; that alone
is not evidence of an upstream build. Build from an isolated local clone:

```sh
git clone --local ~/Workspace/agent-helpers/skil-lock ./skil-lock-build
git -C ./skil-lock-build checkout --detach 2fe41285ab69b347f1a3d53ac003c6cff33b3a62
make -C ./skil-lock-build build VERSION=fork-2fe4128 COMMIT=2fe41285ab69b347f1a3d53ac003c6cff33b3a62 BIN_DIR="$HOME/.local/share/skil-lock/2fe41285ab69b347f1a3d53ac003c6cff33b3a62"
```

Do not overwrite an existing commit directory; verify it before reuse.
Selection: absolute `--skil-lock` > `SKIL_LOCK_BINARY` >
`~/.local/share/skil-lock/<expected-full-commit>/skil-lock`. No PATH lookup.
Every selected binary must report `fork-2fe4128` and the full expected commit
via `version` within five seconds. These embedded strings are provenance
assertions, not signatures. Missing, non-executable, timed-out or mismatched
binaries produce `backend: fallback` and an explicit `reason`; the capability
contract remains `unmeasured`. `--skil-lock fallback` explicitly disables it.
Fallback still applies skill_guard risk signals and declaration/isolation rules;
it does not establish measured capability safety. Once verified, scanner
init/ci failures retain the existing blocking gate behavior.

Upgrade in a separate fork branch/worktree: fetch upstream, rebase the fork
changes onto the chosen upstream revision, resolve and test, then obtain
independent acceptance. Build the resulting exact commit in a new directory;
update both expected constants in `scripts/skill_layout.py` and tests together.
Push, PR, merge and deployment each require separate authorization. This local
switch does not deploy agents-lab. Roll back by reverting this dependency change
locally (commit only with authorization), or select `--skil-lock fallback` for
an explicit degraded run; retain old commit directories. Environment overrides
cannot select a different commit without changing the expected pin.

`--follow-skill-symlinks` remains disabled. The current projection uses symlinks
for files inside real skill directories; enabling entry-link traversal does not
fix bundled attachment/hash coverage. ACM-359 requires a separate design for
safe physical copies (root containment, escapes/cycles, file types and limits),
then attachment/hash regression tests before considering the flag.

Dependency inventory: `scripts/skill_layout.py` selects and verifies the binary;
`capability_gate.py` invokes init/ci; `skill_guard.py` consumes that gate;
`capability_contract.py` invokes init for declaration observations. Tests in
`test_skill_guard.py`, `test_capability_contract.py`, `test_skill_layout.py`,
`test_skill_patch.py` and `test_customization_check.py` cover these paths.
Documentation lives here, in capability-declaration, upgrade-review,
upgrade-diff-review, skills-inventory-changelog and the governance-audit skill.
`multica-board/backend/multica_board/skills_view.py` only probes PATH for a
status badge; it never invokes SkilLock. Its badge can disagree with this pinned
backend; board code, tests and web fixtures are unchanged in this task.
Local LaunchAgents/LaunchDaemons, repository scripts and standard shell startup
files had no SkilLock references. `~/go/bin/skil-lock` reports dev/commit none;
`go version -m` identifies upstream module v0.2.5. It is outside current PATH and
is retained unchanged. This audit did not read credentials or modify services.
