---
name: skills-governance-audit
description: Use when scanning a project, vault, or skill directory for ownership drift, provenance gaps, conflict signals, naming-taxonomy inconsistencies, or unclear routing between existing skills. Also use when measuring a skill's static token footprint or file size against its context budget.
metadata:
  owner: agents-lab
  scope: repo
---

# Skills Governance Audit

## Purpose

Use this skill when the goal is to audit a directory, runtime, plugin surface, or Skill set before deciding which governance action should run next.

This skill is a **read-only governance scanner**.

It helps answer:

- what skills exist in the scanned scope
- which ones are local, runtime-bundled, plugin-provided, Skills CLI-managed, customized, or unclear
- whether there are conflict signals between skills
- whether naming conventions look coherent
- which follow-up workflow should handle each finding
- how many static tokens each skill and its referenced docs would cost

## Do Not Use This Skill For

- intaking a local skill into the canonical repo
- reinstalling a third-party skill through `skills CLI`
- promoting a skill to global scope
- silently renaming or moving skills during the audit itself

Use:

- `skills-intake-local` for local canonical-source intake
- `skills-cli-reconcile` for third-party takeover
- `skills-promote-global` for global-promotion review
- `skills-lifecycle-manager` for reuse/install/fork/build decisions across source classes

## Expected Inputs

Minimum:

- `audit_scope`
  - one directory, one project, or one named skill set

Helpful optional context:

- `known_targets`
- `known_local_skills`
- `known_third_party_skills`
- `known_runtime_skills`
- `known_plugin_skills`
- `known_cli_sources`
- `focus`
  - `ownership`
  - `provenance`
  - `conflicts`
  - `naming`
  - `footprint`
  - `full`

## Output Contract

Always report:

- `audit_scope`
- `scope_summary`
- `source_classifications`
- `local_skills`
- `third_party_skills`
- `unknown_skills`
- `source_authority_findings`
- `upgrade_drift_findings`
- `conflict_findings`
- `naming_findings`
- `token_footprint`
- `routing_recommendations`
- `state_update_suggestions`
- `followups`

Suggested conflict types, matching `skills-governance/scripts/conflict_report.py`:

- `name_collision`
- `responsibility_overlap`
- `behavior_divergence`
- `scope_shadowing`
- `dependency_conflict`
- `trigger_ambiguity`

Suggested naming findings:

- `consistent`
- `taxonomy_mixed_but_explained`
- `naming_taxonomy_drift`
- `rename_candidate`

## Related Skills

- `skills-intake-local`
  - use when the audit finds local skills that should move under canonical source control
- `skills-cli-reconcile`
  - use when the audit finds third-party skills that should return to Skills CLI ownership
- `skills-promote-global`
  - use when the audit finds a local skill that may need global-promotion review

## Workflow

### 1. Define Audit Scope

Decide whether the audit is about:

- one skill
- one runtime-facing directory
- one repository's canonical skills set
- one target project or vault

Keep the scope explicit in the output.

### 2. Inventory What Exists

Scan the requested scope and list:

- canonical source entries under `skills/`
- governance source entries under `skills-governance/skills/`
- runtime-facing adapters under `.agents/skills/` or `.codex/skills/`
- runtime-bundled and plugin-provided entries when the runtime exposes them
- Skills CLI install records and lock metadata when available
- externally adapted targets when they are part of the case

Do not assume every visible entry is a true source directory.

### 3. Classify Source Authority At A High Level

For each relevant skill, classify whether it looks:

- `local_custom`
- `runtime_builtin`
- `runtime_plugin`
- `skills_cli_managed`
- `third_party_unmanaged`
- `third_party_customized`
- `unknown`

Runtime visibility is not proof of ownership. Record the source authority and update channel separately from the visible path.

### 4. Check Upgrade Drift And Customization

For the full machine inventory, run the read-only drift report first:

```bash
python3 skills-governance/scripts/drift_report.py --format text
```

The command reads `skills-governance/config/skills-inventory.json` and reconciles `review_time`, `baseline_ref`, and `current_version` with the recorded path or lock source. It does not modify skills, inventory, or lock files. A directory or lock row with the same name is not provenance.

Each inventory row gets one reconciliation status:

- `missing` — the recorded source path or lock entry is absent
- `modified` — the observed hash, version, or symlink target differs from the baseline
- `stale` — the observed source matches the baseline, and `review_time` is missing or older than the lock update
- `unverified` — there is no baseline, or the only link is a shared name
- `aligned` — baseline, version, and review time agree with the observed source

Installs with no inventory path are listed as unrecorded: status `missing`, provenance `unverified`. Do not attach them to a row just because the name matches.

When a baseline or upstream version is available, also compare the current copy for:

- version or commit change
- changed trigger and scope text
- new scripts, binaries, network calls, or secret handling
- local edits that would be lost by replacement

If no baseline can be captured, report `unverified` rather than implying that the Skill is current.

Also run the capability declaration check. It compares `capabilities` in `SKILL.md` frontmatter with the SkilLock behavior map. It does not add a second scanner.

```bash
python3 skills-governance/scripts/capability_contract.py --format text
```

The pinned fork binary is selected by default (see README); `--skil-lock /absolute/path` overrides its path with the same version/commit check. Missing or mismatched binaries leave behavior `unmeasured` with a recorded fallback reason. `--check` exits 2 when a declaration is invalid, measured behavior exceeds the declaration, or a high-risk skill has no `review_time` and is neither `quarantined` nor `policy_exclusion`.

Do not promote a skill that fails `--promote <name> --check`. Quarantine and policy exclusion are the existing isolation states; do not invent another one. See `skills-governance/docs/capability-declaration.md`.

### 5. Estimate Token Footprint

Run the read-only report before judging context cost. `focus: footprint` still runs this step; a `full` audit runs it too.

```bash
python3 skills-governance/scripts/token_footprint.py --format text
```

The command does not modify skills, inventory, or lock files. Static tokens use `bytes_div_4`: `ceil(utf-8 bytes / 4)`. The report lists each skill's eager `SKILL.md` and the text files it references, including everything under `references/`; each referenced file row carries `linked` (`true` when SKILL.md links or names it, `false` when it was only found by scanning `references/`).

Thresholds:

- `token_over_budget`: eager `SKILL.md` is over 5,000 static tokens, at every scope. Referenced docs are quantified for visibility but never gate the skill.

For each warning, recommend a downgrade and stop there. Do not rewrite the skill inside the audit:

- `references_subdirectory`: move supporting material into `references/` and load it only when a task needs it

A repository skill that is already split into `references/` can stay under the eager token gate while its referenced docs are still quantified. `--check` exits 2 when the gate fails. Do not promote a warned skill to global scope.

### 6. Detect Conflict Signals

Run the read-only report before classifying by hand:

```bash
python3 skills-governance/scripts/conflict_report.py --format text
```

The command does not write `conflict_status` and does not select which skill runs. Apply `skills-governance/docs/routing-precedence.md` before naming a winner. Specific context wins: target adapter, then project, then canonical local, then runtime plugin, then runtime builtin, then global. Same-layer overlap, behavior divergence, unresolved name collision, dependency conflict, trigger ambiguity without a boundary fact, and an `unknown` source are Stop-and-Ask. Do not use runtime load order or `SCOPE_RANK` as the winner. Use its type names as the finding types:

- `name_collision`: one skill name, two different `SKILL.md` hashes
- `responsibility_overlap`: two skills claim the same responsibility phrase
- `behavior_divergence`: related skills disagree, or the same name has different content
- `scope_shadowing`: a broader scope installs a different copy under the same directory name
- `dependency_conflict`: a skill depends on a missing, retired, quarantined, or cyclic skill, or pins two versions
- `trigger_ambiguity`: two `Use when` clauses can match the same request

Keep `trigger_ambiguity` as its own type. Do not fold it into `responsibility_overlap`.

A phrase claimed by three or more skills is a family marker. The report does not emit a pair for that phrase alone. An explicit handoff that names the other skill is `boundary_stated`; it is not behavior divergence.

### 7. Check Naming Taxonomy

Audit whether current names follow a coherent taxonomy:

- prefix families match actual responsibility boundaries
- policy-layer skills are distinguishable from transport-layer skills
- user-facing workflow skills are distinguishable from governance or infrastructure skills

Do not demand one prefix for everything.

Mixed prefixes are acceptable when they reflect a real taxonomy.

Flag a naming issue only when:

- similar skills use different names for no clear semantic reason
- one family mixes policy and transport without distinction
- a rename would materially reduce routing confusion

### 8. Route Findings To The Right Workflow

For a finding about which skill should run, apply `skills-governance/docs/routing-precedence.md`. Put `stop-and-ask` in `Recommended Winner Or Boundary` when that page says to stop. Do not invent a winner.

For each finding, recommend the next step:

- `skills-intake-local`
- `skills-cli-reconcile`
- `skills-promote-global`
- `manual clarification only`
- `runtime/plugin review`

Do not perform the follow-up action inside the audit.

### 9. Suggest State Updates

If a state note or backlog should change, suggest:

- inventory updates
- backlog entries
- conflict notes
- naming-taxonomy follow-ups

Default state destinations:

- `skills-governance/config/skills-inventory.json` for inventory updates. Validate with `skills-governance/scripts/validate_skills_inventory.py --check-paths`.
- `[[OS-LOG/Skills 治理待办与未决事项]]` for backlog notes only when a human page is still in use; backlog status itself lives in Multica ACM-122.

`[[OS-LOG/Skills 当前状态清单]]` is a human mirror. Project it one way from the JSON when a person needs the view. Do not write live inventory or backlog state into:

- `[[OS-RES/Skills 管理与治理原则]]`

## Automatic Execution Boundary

It is usually safe to run the audit automatically because it is read-only.

Stop and ask before continuing when:

- the user expects the audit to mutate files or directories immediately
- the audit depends on uncertain external targets that are not accessible
- a naming change would break active adapters and the user has not asked for renames yet

Hard stop conditions:

- the only way to answer is to mutate active skill layouts
- audit scope is too vague to distinguish source from adapter layers
- a high-risk skill would be promoted while `capability_contract.py --promote <name> --check` exits 2

## Output Template

Use this summary format:

```text
Audit Scope: <path / project / skill set>
Scope Summary:
- ...
- ...
Source Classifications:
- <skill> -> <local_custom / runtime_builtin / runtime_plugin / skills_cli_managed / third_party_unmanaged / third_party_customized / unknown>
Source Authority Findings:
- <skill> -> <provider / repository / plugin / upstream / unknown>
Drift Report:
- Command: `python3 skills-governance/scripts/drift_report.py --format text`
- <skill> -> review_time=<...> baseline_ref=<...> current_version=<...> status=<missing / modified / stale / unverified / aligned>
- Unrecorded: <path> -> missing record, provenance unverified
Upgrade Drift Findings:
- <skill> -> <baseline / changed scope / changed behavior / unavailable>
Local Skills:
- <skill>
Third-Party Skills:
- <skill>
Unknown Skills:
- <skill>
Conflict Findings:
- Command: `python3 skills-governance/scripts/conflict_report.py --format text`
- Type: <name_collision / responsibility_overlap / behavior_divergence / scope_shadowing / dependency_conflict / trigger_ambiguity>
  Skills: <skill-a>, <skill-b>
  Why It Matters: <...>
  Recommended Winner Or Boundary: <skill / stated boundary / stop-and-ask>
Naming Findings:
- Status: <consistent / taxonomy_mixed_but_explained / naming_taxonomy_drift / rename_candidate>
  Evidence: <...>
Token Footprint:
- Command: `python3 skills-governance/scripts/token_footprint.py --format text`
- Estimator: bytes_div_4
- <skill> -> eager_tokens=<...> referenced_tokens=<...> package_tokens=<...> warnings=<none / token_over_budget>
- suggestion: <references_subdirectory> — <move supporting text into references/>
Capability Contract:
- Command: `python3 skills-governance/scripts/capability_contract.py --format text`
- Observation: <skil-lock / unmeasured>
- <skill> -> high_risk=<true / false> isolation=<ok / required> promotion=<allowed / blocked>
- signals: <network_behavior / secret_access when unmeasured and capabilities is absent>
- violation: <SkilLock behavior field that exceeds capabilities.network / capabilities.secrets / capabilities.filesystem>
Routing Recommendations:
- <skill or finding> -> <skills-intake-local / skills-cli-reconcile / skills-promote-global / runtime/plugin review / manual clarification only>
State Update Suggestions:
- <...>
Follow-up:
- <...>
```

## Example

- See `examples/repo-scan-with-conflicts.md` for a repository-scope audit that surfaces responsibility overlap and naming-taxonomy questions without mutating any skill.
- See `examples/token-footprint.md` for a context-budget audit that reports static tokens and downgrade suggestions without mutating any skill.
- See `examples/capability-contract.md` for a declaration check that blocks promotion of an unreviewed high-risk skill.

## Success Criteria

This skill has succeeded when:

- the current skill set is easier to reason about than before
- ownership, conflict, and naming problems are separated clearly
- follow-up work is routed to the right governance workflow
- over-budget skills are quantified and given a downgrade suggestion
- high-risk skills without review are held at quarantine or policy exclusion and are not promoted
- no accidental mutation happens during the audit
