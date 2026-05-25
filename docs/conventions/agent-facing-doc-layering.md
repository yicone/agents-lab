# Agent-Facing Document Layering

This document defines how agent-facing skills and docs in `agents-lab` should reference each other.

The goal is not perfect elegance. The goal is reliable execution under current agent limits.

## Why This Exists

As of 2026-05, agents are reasonably reliable at:

- reading the directly triggered skill or entrypoint doc
- following one explicit next hop
- using short routing guidance

They are less reliable at:

- traversing deep doc chains
- discovering that a second- or third-hop doc contains mandatory rules
- preserving all critical constraints across long reference paths

So agent-facing information architecture should optimize for shallow, explicit, and self-sufficient entrypoints.

## Core Rules

### 1. Keep Mandatory Rules In The First Hop

If an agent can make a damaging mistake by not reading a rule, that rule must not live only in a second- or third-hop document.

Allowed:

- `AGENTS.md` -> `session-start.md`
- a skill -> one directly referenced companion doc

Not acceptable:

- entrypoint doc -> secondary doc -> tertiary doc
- where the tertiary doc is the first place that states a mandatory execution rule

### 2. Treat One Hop As Normal, Two Hops As Risky

Use this mental model:

- first hop: normal
- second hop: acceptable only for supplementary detail
- third hop or deeper: avoid for mandatory behavior

If a second-hop reference is required for correct execution, move a compressed version of the critical rule up into the first hop.

### 3. Keep Entrypoints Self-Sufficient

An entrypoint skill or doc should contain enough information to:

- identify whether it applies
- state what it owns
- state what it does not own
- state which sibling skill or doc to use next when needed

Do not make the entrypoint depend on deep reference chasing just to discover its basic boundary.

### 4. Use Deep References For Reference Material, Not Control Logic

Second-hop or deeper references are best for:

- examples
- heavy reference
- scripts
- background explanation
- low-frequency edge cases

They are not the right place for:

- the only definition of scope
- the only stop condition
- the only routing rule
- the only conflict boundary

### 5. Prefer Sibling Routing Over Dependency Chains

When several skills cooperate, prefer:

- skill A states its boundary
- skill A points to sibling skill B
- skill B states its own boundary

Avoid long dependency chains such as:

- skill A must fully load B
- B must fully load C
- only then can A be used correctly

This repo should favor coordination boundaries over deep chained dependencies.

### 6. Separate Principles, State, And Execution

Do not mix:

- stable principles
- current state
- executable workflow

Use:

- principle docs for stable rules
- state notes for current inventory / backlog / risk
- skills for repeatable execution workflows

Cross-reference them, but do not duplicate live state or hide mandatory execution rules in state pages.

## Recommended Layering Patterns

### Pattern A: Entry Doc -> One Required Workflow Doc

Good for startup:

- `AGENTS.md` -> `docs/workflows/session-start.md`

### Pattern B: Skill -> Example Or Reference

Good for reuse:

- `skills-*.SKILL.md` -> `examples/*.md`
- `SKILL.md` -> `scripts/*`

### Pattern C: Skill -> Sibling Skill

Good for coordination:

- `logseq-acns-vault` -> `logseq-acns-write`
- `logseq-acns-write` -> `logseq-http-transport`

This works only if each skill already explains its own role.

## Anti-Patterns

- A mandatory rule appears only in a deep doc linked from another linked doc.
- A skill's true boundary is only discoverable by reading two more files.
- A state page becomes the only place that explains how to execute a workflow.
- One “manager” skill grows until it overlaps all other governance skills.
- The same critical rule is rewritten independently in multiple places.

## Review Checklist

When adding or editing an agent-facing doc or skill, ask:

1. If the agent reads only this file, can it avoid the biggest mistake?
2. Is any mandatory rule hidden deeper than one hop?
3. Is a linked file truly required, or only helpful?
4. Should the linked file be a sibling route instead of a dependency?
5. Does this change duplicate state that should live elsewhere?

## Current Repo Guidance

Apply this especially to:

- `AGENTS.md`
- `docs/workflows/session-start.md`
- governance skills under `skills/skills-*`
- Logseq write-stack skills such as `logseq-acns-vault`, `logseq-acns-write`, and `logseq-http-transport`

When in doubt, move the minimal critical boundary upward and leave detail below.
