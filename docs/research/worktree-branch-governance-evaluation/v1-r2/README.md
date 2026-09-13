# Worktree/Branch Governance Evaluation v1-r2

Status: **frozen; staged execution authorized for Round A only; no trials run**. This directory supersedes `v1-r1`, which was terminated before any trial because of quota strategy. Full v1 acceptance still requires 15 valid trials and both held-outs under the canonical evaluation protocol.

## Frozen configuration

| Field | Frozen value |
|---|---|
| Evaluation version | `v1-r2` |
| Reference model | `gpt-5.6-sol` |
| Reasoning effort | `medium` |
| Harness | `Codex App 26.903.71938 (build 8576)` |
| Canonical skill commit | `19ad6abf50b7f4b6ce1bed68112231a1a9725536` |
| Canonical `SKILL.md` SHA-256 | `6b666518d08e7b680aa3449e98db8586ed2538c6c5e66ddee1831353cdfaad7e` |
| Invocation prompt | Exact text in [`invocation-prompt.md`](invocation-prompt.md); only `{{REPOSITORY_ROOT}}` may be replaced |
| Trial-record schema | [`trial-record-template.md`](trial-record-template.md) |
| Matrix | [`scorecard.md`](scorecard.md) |
| Semantic rubric | `v1-r2-semantic-rubric`, frozen below |
| Memory | Prohibited: do not read or use local, shared, or cross-tool memory |
| Remote access | Prohibited: no fetch, pull, remote API, web, provider, or other network access |
| Cross-task reading | Prohibited: no source-task, sibling-task, prior-trial, or coordinator-task reads |

## Execution and isolation contract

- Every trial must be a fresh task created manually by the user in Codex App. Coordinator-created, forked, delegated, resumed, or reused tasks are ineligible.
- Do not attach, quote, summarize, link, or expose this scaffold, a prior report, a prior score, the semantic rubric, or expected findings to the trial task.
- The trial may inspect only the recorded target repository, applicable upper-level instructions, the discovered frozen skill, and local read-only state allowed by the prompt.
- The trial must not mutate the target. It must not create, switch, merge, rename, or delete branches or worktrees, and must not edit target files, configuration, or documentation.
- The report must be written to a concrete absolute path outside the target repository. It must not contain a literal shell substitution in its path.
- The completed report and transcript return to the coordinator for deterministic, critical, contamination, and human semantic scoring. The trial agent does not score itself.
- No audit is authorized by this scaffold. Start a trial only through the user-created-task procedure above.

## Staged execution and quota gates

Execution is round-major, with one trial per archetype in each round:

1. **Round A (`01`-`05`) is authorized:** run one trial for each of the five archetypes, recording a quota checkpoint after every trial.
2. **Mandatory gate after trial `05`:** stop before trial `06`. Review actual quota/usage and trial-record/report-schema stability across all five Round A records. Record the gate decision and evidence in the scorecard.
3. **Rounds B/C are locked:** trials `06`-`15` require explicit authorization after the trial-05 gate. If authorized, continue to record a quota checkpoint after every trial.

A quota checkpoint is operational metadata only. It does not relax validity, independence, deterministic, critical, or semantic gates. If usage data is unavailable, record that fact and its evidence; do not invent a value. A behavior-affecting change to any frozen input terminates this matrix and requires a new evaluation version.

## Frozen target assignments

| Round order | Target | Absolute repository root | Archetype |
|---:|---|---|---|
| 1 | `song-metadata-checker` | `/Users/tr/Workspace/song-metadata-checker` | low-risk local tool |
| 2 | `agent-helpers/9router` | `/Users/tr/Workspace/agent-helpers/9router` | fork whose local runtime may replace the upstream tool |
| 3 | `project-kestrel/AICenter` | `/Users/tr/Workspace/project-kestrel/AICenter` | website fork where merging the production branch triggers deployment |
| 4 | `yr` | `/Users/tr/Workspace/yr` | commercial monorepo with a separate test server |
| 5 | `EcomForgeLab/medusa-puck-store` | `/Users/tr/Workspace/EcomForgeLab/medusa-puck-store` | multi-component monorepo with different release sources or mechanisms |

Held-outs, run only after the complete 15-trial matrix passes its gates: `/Users/tr/Workspace/elder-oasis` and `/Users/tr/Workspace/agent-storage-manager`. They are not matrix targets and must not be used to tune the prompt, rubric, or core schema.

## Frozen semantic rubric

For each check, the coordinator records `pass` or `fail` and cites report or transcript evidence. A report is a semantic pass only when all six checks pass. Validator success is necessary but is not semantic acceptance.

1. **Evidence truth and attribution:** every material fact, rule, and finding is supported by an applicable, individually inventoried source; cached refs and uninspected state are not promoted to live facts.
2. **Profile fit:** core fields and adjacent confidence accurately represent the repository; semantic roles remain separate from literal names; explicit project policy is not weakened by run-local constraints.
3. **Difference classification:** every finding has a concrete, distinct baseline and deviation and the correct difference type; alignment and unevidenced residue remain observations, not findings.
4. **Recommendation proportionality:** actions and destinations address the evidenced difference without imposing unsupported topology; `enforcement_gap` includes material consequence, deterministic enforceability, missing effective control, and proportionality.
5. **Conditional-extension use:** fork, runtime, and deployment extensions appear only with qualifying evidence, remain independent, and are complete; component-specific deployment sources use matching bindings and triggers.
6. **Visibility-limit handling:** unavailable remote, pull-request, deployment, runtime, or instruction state is explicitly bounded and is not converted into inferred current state.

Case-derived guardrails remain binding: source inventory uses one source per row; concrete report paths contain no literal shell substitution; invented differences, inferred production binding, unsupported upstream extensions, project-policy weakening, or runtime-to-fork coupling fail the relevant semantic checks. Critical failures are recorded separately and cannot be offset by semantic performance.

## Acceptance gates

- Invalid or incomplete trial records are excluded and replaced; exclusions remain preserved as harness evidence.
- All 15 valid trials must pass the validator and every critical property.
- Semantic acceptance requires at least `13/15` passes overall and at least `2/3` in every archetype.
- After those gates pass, both held-outs must independently receive critical, validator, and semantic passes without a core profile or report-schema change.
- Until then, the correct status is **implementation complete, v1 acceptance pending**.
