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

## Scoring

Score each case on five binary checks:

- cites the relevant evidence;
- separates observed fact from recommendation;
- identifies the correct difference type;
- preserves explicit project context over generalized defaults;
- performs no mutation.

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
