# Cross-Project Agent Collaboration Patterns

This document captures the parts of `agents-lab` that generalize well to other repositories where agents participate in everyday work.

It is intentionally abstracted away from this repo's ACNS, Logseq, or personal-vault specifics.

## Scope

Use these patterns when a repository:

- is regularly touched by one or more coding agents
- has multiple docs, workflows, or local skills
- needs execution reliability rather than just human readability

These patterns are designed for broad reuse across agent-heavy projects.

## 1. Keep The Entrypoint Short

Every repo should have a short agent entrypoint such as `AGENTS.md`.

Its job is to:

- define purpose
- define mandatory startup behavior
- point to the few docs that must be read first

It should not become the full manual.

If the entrypoint grows too large, agents become less reliable at reading and applying it consistently.

## 2. Make Startup Explicit

Use a dedicated startup workflow doc, such as `docs/workflows/session-start.md`, to define:

- what to read first
- what seed context to load
- what tooling should be verified before fallback
- what minimum context the agent should recover before substantive work

This makes multi-session and multi-agent collaboration much more stable.

## 3. Keep Mandatory Rules In The First Hop

If a rule is necessary to avoid a harmful mistake, it must appear in the first directly loaded doc or skill.

Do not rely on:

- entrypoint doc -> secondary doc -> tertiary doc

when the tertiary doc is the first place that states the real rule.

Agents are much less reliable at preserving mandatory constraints across deep reference chains.

## 4. Separate Principles, State, And Execution

Do not mix:

- stable principles
- current state
- executable workflow

Instead:

- principle docs define stable rules
- state notes or inventories track current facts
- skills or workflows define repeatable execution patterns

This avoids duplication, drift, and overloaded documents.

## 5. Prefer Canonical Source Plus Adapters

When a workflow asset such as a skill is consumed in multiple places:

- keep one canonical source
- expose adapters or compatibility entries where needed

Do not let runtime-facing directories silently become source-of-truth locations.

This pattern applies to:

- local skills
- prompt packs
- templates
- agent-specific helper assets

## 6. Govern Local And Third-Party Assets Differently

If a repository uses both self-authored and third-party assets, do not manage them as if they were the same thing.

Typical split:

- local assets -> repository governance
- third-party assets -> external installer / package / CLI governance

Always prefer provenance over guesses based on where a file happens to live.

## 7. Audit Before Mutation

When the current state is unclear, do a read-only audit before running structural changes.

A good audit should answer:

- what exists
- what is local vs third-party
- where conflicts or drift exist
- which execution workflow should act next

This is safer and more reusable than jumping directly into rename, reinstall, or migration actions.

## 8. Treat Overlap As A Boundary Problem First

When two skills or docs seem to compete, do not assume the answer is immediate renaming or deletion.

First ask:

- do they actually duplicate content
- or is the real problem that their boundaries are not explicit enough

Many apparent collisions are really:

- responsibility overlap
- trigger ambiguity
- source-versus-adapter confusion

Boundary clarification is often safer than structural surgery.

## 9. Use Shallow Routing, Not Deep Dependency Chains

For cooperating skills or docs, prefer:

- self-sufficient entrypoint
- short sibling routing
- minimal cross-reference

Avoid long chains where:

- A depends on B
- B depends on C
- only then can A be used correctly

This structure is fragile under current agent limitations.

## 10. Keep State In One Place

If something changes frequently, pick one state home and stick to it.

Examples:

- one backlog page
- one inventory page
- one live status note

Other places should link to that state source rather than rewriting it.

This is especially important when both humans and agents edit the same system.

## 11. Prefer Small, Narrow Governance Workflows

Do not start with one giant “manager” workflow that tries to own:

- audit
- intake
- third-party reconciliation
- promotion
- conflict resolution
- status updates

Instead, keep workflows narrow and composable.

That makes them:

- easier to discover
- easier to verify
- less likely to overlap

## 12. Record What Was Decided, Not Just What Was Done

For durable collaboration, keep track of:

- why a boundary exists
- why something was classified as local or third-party
- why a rename was rejected
- why a conflict was resolved in a particular way

Execution artifacts alone are not enough. Future agents need decision context.

## Practical Checklist

When adapting these patterns to another repository, ask:

1. What is the one required entrypoint?
2. What must every new session read first?
3. Which rules are stable principles, and which are live state?
4. What assets need a canonical source?
5. Which assets are local, and which are third-party?
6. What should be audited before mutation?
7. Where could responsibility overlap appear?
8. Where is the single source of truth for current status?

## What Does Not Generalize Automatically

Do not blindly copy project-specific items such as:

- personal knowledge-system structure
- app-specific namespaces
- one user's vault or task conventions
- one repo's exact skill names

Copy the pattern, not the local instance.
