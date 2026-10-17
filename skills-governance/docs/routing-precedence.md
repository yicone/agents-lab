# Skill Routing Precedence And Stop-and-Ask

This page decides which visible skill an agent runs when more than one could apply. It does not decide ownership, and it does not install, promote, fork, or replace a skill.

`python3 skills-governance/scripts/conflict_report.py` reports name collision, responsibility overlap, behavior divergence, scope shadowing, dependency conflicts, and trigger ambiguity. The report does not select which skill runs. `SCOPE_RANK` in that script measures breadth for shadowing detection. It is not this precedence order. Runtime load order is not a layer and is not a winner.

Apply the first matching rule in [Decision order](#decision-order). Stop when it matches.

## Layers

Most specific first. A broader layer stays visible. It does not override a more specific layer that applies to this task.

1. target adapter
2. project
3. canonical local
4. runtime plugin
5. runtime builtin
6. global

| Layer | What counts | Inventory signal |
| --- | --- | --- |
| target adapter | A path bound to the current vault, app, or named environment | `adapters[].kind` of `declared_target`, or a visible path the task names as its target |
| project | Adapters inside the current checkout | `.agents/skills/<name>` and `.codex/skills/<name>` in this workspace, usually `kind: symlink` |
| canonical local | The repo-owned skill for this repository | `source_class` `local_custom` with a `canonical_path` in the repo that owns the task. `scope` is `repo` or `cross-repo` |
| runtime plugin | A skill introduced by a plugin enabled on the current host | `source_class` `runtime_plugin`, `scope` `plugin-managed` |
| runtime builtin | A skill shipped with the runtime or app | `source_class` `runtime_builtin`, `scope` `vendor-managed` or `app-bundled` |
| global | A user-global install visible to every project | `scope` `machine`, including `~/.agents/skills`. Typical classes are `skills_cli_managed`, `third_party_unmanaged`, and `third_party_customized` |

A `local_custom` skill with no `canonical_path`, installed only under a machine path, is global. It is not canonical local.

`unknown` never wins by layer rank.

## How adapters interact

An adapter exposes a skill. It does not create a second source.

- A project or machine symlink whose target is the canonical directory is the same skill as canonical local. Those layers collapse to one candidate.
- A recorded adapter of the same inventory row, or the same `SKILL.md` digest, is not a second name. The drift report keeps hash differences for that row.
- A target adapter wins only for a task inside that target. It does not follow the agent into an unrelated workspace.
- A project adapter wins over canonical local only when it exposes a different skill for this checkout. A symlink back to canonical local is not a different skill.
- A runtime plugin does not override a target adapter, a project adapter, or canonical local.
- A runtime builtin does not override a plugin, a project adapter, a target adapter, or canonical local.
- Global does not override a more specific layer when the candidates have different names and no stop condition applies.
- The same directory name with different `SKILL.md` bytes is not settled by layer rank. Use the same-name rule in the decision order. That includes a user-global copy and a runtime builtin of the same name when no customization is recorded.

## Decision order

1. The user named a skill in this turn. Use that skill.
2. A stated boundary matches facts already in the task. Follow the boundary. `boundary_stated: yes` counts, as does a handoff that names the other skill and says which first question it owns.
3. A stated boundary covers only some situations, and this task is outside those situations. Stop and ask. Do not fall through to layer rank.
4. Same name, different bytes:
   - one recorded adapter of one inventory row, or the same digest: one skill
   - `customization_mode` is `wrapper`, `overlay`, `fork`, or `local_replacement`, recorded against that upstream: the recorded delta wins
   - otherwise stop and ask
5. Different names, no stated boundary, the task context identifies a layer, and no stop condition below applies. Use the most specific applicable layer.
6. Otherwise stop and ask.

## Stop and ask

Stop before running either skill when any of these remain true:

- two or more candidates are still on the same layer
- the finding is `behavior_divergence`
- the finding is `name_collision` and no recorded customization names the override
- the finding is `dependency_conflict` (missing, retired, quarantined, cyclic, or two pinned versions)
- the finding is `trigger_ambiguity` and the request matches more than one trigger with no boundary fact
- the candidate that would win is `source_class` `unknown`
- the task does not identify a target or a project, so the first three layers cannot be applied, and more than one plugin, runtime, or global skill still matches
- the only way to resolve the overlap is to install, promote, fork, or replace a skill
- three or more skills share a family marker. The conflict report does not emit a pair for that phrase. The marker is not a ranking
- `scope_shadowing` shows different bytes, and the narrower copy is not the applicable recorded skill for this task

When `scope_shadowing` shows different bytes and the narrower copy is the applicable winner, use the narrower copy. Say that a broader different copy exists. Do not follow whichever path the runtime loaded.

### How to ask

- Name the candidates, the finding type, the layer, and the single missing fact.
- Ask one question.
- Do not invent a rank, blend the two skills, or keep an earlier unconfirmed guess.
- An explicit skill name from the user ends the ask.

## Recorded boundaries

These are stated boundaries. They are not layer-rank winners.

- `logseq-acns-vault` and `logseq-acns-write` are the same layer (canonical local) and `boundary_stated` is yes. Load both when the task needs Logseq semantic placement and an ACNS write plan. Prefer `logseq-acns-vault` while page ownership or namespace choice is still open. Prefer `logseq-acns-write` when the destination is known and the next step is a write plan. If that fact is missing, stop and ask.
- `skills-lifecycle-manager` and `skills-governance-audit` name each other. Use `skills-lifecycle-manager` when the first question is whether to reuse, install, fork, or build. Use `skills-governance-audit` when the first question is what is going on in this skill set. If the first question is missing, stop and ask.
- `agent-browser` and `ego-browser` are responsibility overlap, not behavior divergence. Use `ego-browser` only when the task needs a shared person-and-agent browser session, or multiple Profiles and Spaces. That note does not choose a winner for every other browser task. Those remaining tasks stop and ask.

Representative fixtures live in `skills-governance/tests/fixtures/conflicts/`.

## What this page does not change

Open `conflict_status` values stay as recorded. Applying this page to a pair does not mark that pair resolved. Pair-specific boundaries beyond the three above are still stop-and-ask until a human states one.
