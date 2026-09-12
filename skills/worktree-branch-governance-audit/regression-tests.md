# Behavioral Regression Cases

These dry-run cases test decision quality. They must never mutate the target repository.

## Case 1: Global Worktree Default Does Not Fit The Repository

### Evidence

- A global skill says feature implementation should use an isolated worktree.
- The repository has no worktree directory convention.
- The repository owner explicitly says this repository does not need worktrees.

### Expected Behavior

- Treat the owner's repository-specific decision as authoritative for this audit.
- Report `project_fit_conflict` against the generalized global recommendation.
- Recommend keeping the global rule conditional or narrowing its trigger.
- Do not create a worktree or propose adding `.worktrees/` to `.gitignore`.

### Failure Signals

- The agent asks where to create a worktree after the project decision is known.
- The agent treats the global skill as higher authority than explicit project context.
- The agent converts this one case into a universal rule that no repository needs worktrees.

## Case 2: Production Main And Periodic Upstream Adoption

### Evidence

- A forked website deploys production automatically from `main`.
- Non-production branches receive previews.
- The repository periodically reviews upstream changes and adopts only selected changes.
- Existing guidance says only “merge upstream regularly.”

### Expected Behavior

- Observe `primary_branch: main`, a separate `primary_branch_role: production`, and `upstream_mode: selective-adoption`.
- Report an omission around isolated upstream review/adoption.
- Recommend separating upstream review from the branch that can reach production.
- Do not prescribe a permanent `develop` branch without project evidence.

## Case 3: Low-Risk Local Tool

### Evidence

- The tool runs only on the owner's machine.
- There is no deployment automation or upstream remote.
- Only one task is active.
- Existing AGENTS.md requires a worktree for every change.

### Expected Behavior

- Observe low merge and deployment risk.
- Report `project_fit_conflict` for the unconditional worktree requirement.
- Recommend optional worktrees for risky, concurrent, or agent-intensive changes.
- Preserve any explicit repository requirement if the owner confirms it has a non-obvious reason.

## Case 4: Cached Remote Ref And Open Pull Request

### Evidence

- A fork has local remote-tracking refs for `origin/main` and `upstream/main`.
- No fetch or remote API query was performed during the audit.
- A task branch has a pull request, but its current lifecycle is not visible from local Git state.
- A later authoritative remote check would show that upstream advanced and the pull request remains open.

### Expected Behavior

- Describe remote-tracking refs as cached local state and report remote freshness as `unverified`.
- Do not call the task branch or pull request stale, merged, closed, abandoned, or historical.
- Classify unsupported lifecycle language as `unverifiable_claim`, not `stale_guidance`.
- Include every required report section. Source Inventory includes class, scope, normativity, freshness, and visibility; Observed Profile includes confidence; Visibility Limits is explicit.
- Keep audit instructions and report mechanics out of Explicit Project Constraints.
- Keep runtime bindings or worktree roles as repository observations; do not add them to the universal profile schema from this case alone.

### Failure Signals

- The agent equates `upstream/main` with the current remote head.
- The agent infers pull-request lifecycle from commit reachability or branch age alone.
- The report omits required sections because the audit is described as compact.
- Source Inventory drops `normativity`, or Explicit Project Constraints contains the audit skill or report method instead of project evidence.
- The agent creates a universal profile field based only on this repository.

## Case 5: Local Runtime Fork Inside A Multi-Repository Workspace

### Evidence

- The Codex project root is a non-Git directory containing several independent repositories.
- The requested target is one fork subdirectory with `origin` and `upstream` remotes.
- The fork can be built and used locally in place of the upstream-installed application.
- An empty `.worktrees/` directory exists, but no applicable repository rule designates it and prior worktrees used an external path.
- A visible global worktree skill requires ignored project-local worktree directories, but the audit skill itself does not.
- Prior task history contains verified lifecycle evidence and temporary preservation constraints.

### Expected Behavior

- Name the workspace root, repository root, and audit scope separately; inspect only applicable repository and upper-level sources.
- Inventory every relied-on source with all required fields, including the global skill and prior task history.
- Put only normative user/repository boundaries in Explicit Project Constraints; put Git topology and prior lifecycle state in Observed Facts.
- Do not attribute the global skill's ignore rule to the audit skill or apply it without a project-fit finding.
- Report `worktree_location: unspecified`; an empty directory alone does not establish policy or an enforcement gap.
- Keep literal branch prefixes and tags in Observed Facts; normalize only evidenced semantic roles into `branch_roles`.
- Record local runtime binding and upstream/local patch flow as conditional-extension candidates, not core profile fields.

### Failure Signals

- The audit treats the non-Git workspace root as the repository or mixes sibling repositories into the source inventory.
- Source Inventory uses shorthand entries that omit required fields or excludes a source used in a finding.
- Explicit Project Constraints contains audit mechanics, repository facts, or temporary state.
- The agent claims the audit skill requires `.worktrees/` to be ignored, or infers adoption from directory existence.
- Literal prefixes or release tags are reported as semantic `branch_roles`.

## Case 6: AICenter Uses A Non-`main` Primary Branch

### Evidence

- The repository documents `aimaiai` as the production/integration target.
- Literal `main` is a reference branch, not the primary production branch.
- `.worktrees/` is actively used.
- Upstream changes are selectively adopted.
- The deployment provider's actual production binding remains unverified.
- One specific plan is relied on by a finding and must have its own Source Inventory record.

### Expected Behavior

- Report `primary_branch: aimaiai` and assign a separate semantic `primary_branch_role`.
- Keep literal branch names and prefixes in `branch_patterns`, not `branch_roles`.
- Emit deployment fields only as a complete conditional extension when deployment evidence is present, and preserve the provider-binding visibility limit.
- Treat cached or absent remote evidence as unverified rather than claiming current lifecycle.
- Require proportionate evidence before reporting `enforcement_gap`.
- List each relied-on plan as its own Source Inventory record; do not aggregate it under a wildcard.

### Failing Baseline

The fresh AICenter audit passed scope and cached-ref rules but produced the legacy multi-branch `main_role`, literal `branch_families`, wildcard Source Inventory aggregation, `unknown` with high confidence, classification errors, and an overbroad enforcement recommendation. This case is the regression baseline for the deterministic report validator and the revised schema.

## Scoring

Score each case on five binary checks:

- cites the relevant evidence;
- separates observed fact from recommendation;
- identifies the correct difference type;
- preserves explicit project context over generalized defaults;
- performs no mutation.

For Cases 4 and 5, also require complete Source Inventory records and correct source attribution. Either failure caps the score at `3`.

## Case 7: YR runtime-coupled monorepo

The YR replay is a solo-maintained commercial monorepo with repository-scoped worktree rules, active sibling worktrees, and worktree-bound backend runtimes. The audit was read-only, did not contact remotes, and did not inspect deployment-system bindings. The RED baseline incorrectly treated the task-local no-worktree constraint as a project conflict, targeted a repository rule with `narrow_global_default`, labeled correct cached-ref discipline as `git_object_confusion`, emitted positive worktree alignment as `omission`, coupled runtime evidence to irrelevant upstream fields, promoted deployable-state language to production binding, invented deployment values despite visibility limits, added a second deployment confidence field, combined multiple Git sources in one row, and omitted the effective-control and proportionality fields for `enforcement_gap`.

The corrected report keeps task constraints separate, uses `primary_branch_role: release-ready` absent production evidence, uses a runtime-only extension when appropriate, leaves uninspected deployment values unknown, and writes `none discovered` when no source difference is established. Any invented difference, inferred production binding, or runtime-to-fork coupling caps the score at `3`; mutation of the audited repository remains an automatic failure.

Interpretation:

- `0-2`: poor
- `3`: partial
- `4-5`: good

## Initial Manual Replay

Date: `2026-09-11`

Case 1 was replayed against `agents-lab` after the repository owner rejected the generalized worktree default.

Observed evidence:

- the current user instruction says this repository does not need a worktree;
- the visible global `using-git-worktrees` skill requires worktree setup before implementation plans;
- `docs/conventions/skills.md` already warns that global skills can contaminate projects when they carry repository-specific assumptions;
- `git worktree list --porcelain` showed only the main working tree;
- no worktree was created.

Result: `5/5` by self-dry-run. The finding is `project_fit_conflict` with recommended action `narrow_global_default`; it is not evidence of an independent forward test. A future authorized agent evaluation should rerun the case without revealing the expected answer.

Case 4 originated from a real 9router audit that treated cached remote-tracking refs as current remote evidence and described an open pull request as historical or stale. That output is the failing baseline.

After the evidence-discipline rules were moved into the first-hop skill, a self-dry-run of Case 4 produced the intended remote-evidence result: remote freshness and pull-request lifecycle remain `unverified`; the unsupported stale claim is `unverifiable_claim`; and no repository-specific profile fields are promoted globally.

A later paired replay in the original 9router conversation passed those evidence checks but omitted Source Inventory `normativity` and listed the audit skill as an explicit project constraint. The first-hop contract itself had omitted `normativity`, despite the reference model requiring it. Treat the earlier `5/5` self-score as superseded: the paired replay exposed report-contract drift and was not an independent test.

The first-hop contract and Case 4 now require `normativity` and separate project constraints from audit mechanics. These documentation corrections have not yet received an independent forward test; use the next repository in a fresh conversation rather than replaying 9router again.

Case 5 originated from a CodexBar audit in an existing long-running repository conversation. The audit correctly scoped the target subrepository and respected cached-remote evidence limits, but it again omitted explicit Source Inventory fields, placed audit mechanics and repository state under Explicit Project Constraints, attributed a global skill's ignore rule to the audit skill, and treated an empty `.worktrees/` directory as evidence for an enforcement gap.

This is a real second-repository failing baseline, but not an independent fresh-conversation test because prior task context was available. The next project should test Case 5's revised rules in a new conversation without revealing expected findings.
