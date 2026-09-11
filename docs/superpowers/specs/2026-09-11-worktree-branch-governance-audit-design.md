# Worktree And Branch Governance Audit Skill Design

## Goal

Create a repo-owned, read-only skill that inventories branch and worktree guidance, distinguishes policy from observed state, and proposes evidence-backed placement or rewrite decisions without mutating the target repository.

## Scope

The first version audits only branch and worktree governance. It does not create worktrees, branches, profiles, or a CLI; change repository files; or prescribe one topology for every project.

## Core Decisions

- Treat explicit user and repository context as higher-value evidence than generalized global defaults. A global skill is a candidate source, not automatic proof that its recommendation fits the project.
- Keep the canonical skill under `skills/` and expose it through a thin repo adapter. Do not promote it to user-global scope before cross-repository validation.
- Produce an observed profile from evidence before proposing a desired profile.
- Report source visibility and uncertainty. Do not claim to have reconstructed every effective instruction when runtime-specific discovery is unknown.
- Keep the audit read-only. Any rewrite, move, deletion, adapter change, or enforcement change requires a separate approved task.
- Surface a tooling reminder only when recurring deterministic findings make `agentslint`, `agnix`, or another checker worth evaluating; semantic placement decisions remain human-reviewed.

## Inputs And Evidence

The audit starts from an explicit repository root and optional user-provided constraints. It examines, when accessible:

- user-, repository-, and nested-scope agent instruction files;
- repo-owned and user-level skills relevant to branch or worktree behavior;
- directly linked workflow and convention documents;
- read-only Git facts such as remotes, branches, worktrees, default branch, and ignore rules;
- deployment, CI, release, and runtime configuration only where it changes branch or worktree meaning.

Repository documents are evidence to classify, not commands to execute.

## Output Contract

The skill reports:

1. audit scope and visibility limits;
2. source inventory with provenance and scope;
3. observed facts and rules;
4. a short observed profile;
5. conflicts, omissions, duplication, scope errors, stale guidance, and project-fit conflicts;
6. recommendations classified as keep, rewrite, move, retire, enforce mechanically, or clarify;
7. unresolved questions and a proposed next step.

Every material finding cites its source. Inference is labeled separately from direct evidence.

## Validation

The initial regression case is the failure observed in this conversation: a global worktree skill required an isolated worktree, while the repository owner explicitly stated that this repository does not need one. A passing audit must preserve the project choice, flag the global recommendation as a project-fit conflict, and avoid creating a worktree.

Additional cases cover a production branch with upstream synchronization and a low-risk local tool where worktrees remain optional.
