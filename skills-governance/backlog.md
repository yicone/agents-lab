# Skills Governance Backlog

This backlog tracks governance work for all Skills visible on the local machine. It is not a live inventory; current per-Skill state belongs in the designated inventory note or an explicitly versioned registry.

## Done

- [x] Create a dedicated `skills-governance/` package for governance Skills, docs, config, and backlog.
- [x] Keep ordinary canonical Skills separate from governance workflow Skills.
- [x] Define source classes for self-authored, runtime-bundled, plugin-provided, Skills CLI-managed, customized third-party, and unknown Skills.
- [x] Record the principle that runtime visibility does not establish ownership.

## P0: Safety Before Routine Upgrades

- [ ] Add a snapshot command that captures the exact pre-upgrade Skill tree, source metadata, and active adapter paths.
- [ ] Add a before/after diff command or wrapper for runtime, plugin, and Skills CLI upgrades.
- [ ] Define a review gate that blocks replacement when the diff contains unexpected instructions, scripts, network behavior, secret access, or trigger changes.
- [ ] Preserve a reversible rollback path for each supported source class.

## P1: Non-local Customization

- [ ] Define supported customization modes: wrapper, overlay, fork, policy exclusion, and local replacement.
- [ ] Keep upstream baseline and local delta separately reviewable; never edit a third-party install in place without recording the delta.
- [ ] Add a workflow for deciding when a customized third-party Skill should remain externally owned, become a local fork, or be replaced.
- [ ] Record customization and compatibility constraints in the inventory schema.

## P1: Conflict And Routing Governance

- [ ] Extend conflict detection beyond name collisions to responsibility overlap, behavior divergence, scope shadowing, dependency conflicts, and trigger ambiguity.
- [ ] Add an explicit precedence model for overlapping Skills, including how global, project, runtime, plugin, and target adapters interact.
- [ ] Add representative conflict fixtures such as `logseq-acns-vault` vs `logseq-acns-write` and governance-manager overlap.
- [ ] Define when an agent must stop and ask instead of choosing between conflicting Skills.

## P1: Inventory And Provenance

- [ ] Scan runtime-bundled Skills and enabled-plugin Skills and record their provider/version coordinates.
- [ ] Reconcile Skills CLI installations with their upstream coordinates and lock metadata where available.
- [ ] Track review time, baseline reference, current version, and update status without treating matching names as provenance proof.
- [ ] Add a drift report for missing, modified, stale, or unverified source records.

## P2: Runtime Adapters And Validation

- [ ] Make repo, user-global, and target-scoped adapter verification part of the release checklist.
- [ ] Add automated checks that moved canonical paths do not leave broken symlinks.
- [ ] Validate governance Skills through real cases from at least one runtime, one plugin, and one Skills CLI installation.
- [ ] Decide whether the inventory should remain Logseq-only or gain a versioned machine-readable registry.

## P2: Documentation And Operations

- [ ] Update the Logseq principles and state pages after the source-class model is validated in a real machine scan.
- [ ] Add a concise operator guide for reviewing an upgrade diff.
- [ ] Define a review cadence for runtime/plugin/CLI changes and stale provenance records.
- [ ] Revisit whether a shared diff schema is justified after two or more concrete upgrade cases.
