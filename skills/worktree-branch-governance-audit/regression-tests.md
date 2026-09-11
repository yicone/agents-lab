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

- Observe `main_role: production` and `upstream_mode: selective-adoption`.
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

## Scoring

Score each case on five binary checks:

- cites the relevant evidence;
- separates observed fact from recommendation;
- identifies the correct difference type;
- preserves explicit project context over generalized defaults;
- performs no mutation.

For Case 4, also require both remote-evidence discipline and complete required report sections. Either failure caps the score at `3`.

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
