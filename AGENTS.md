# Agent Operating Rules (agents-lab)

This file is the entry point for agents working in this repository.

Read this first, then follow the linked docs instead of expanding this file into a large manual.

## Purpose

- This repo studies coding agents, orchestration, memory, skills, and related workflows.
- The default project namespace for reusable memory is `proj/agents-lab`.
- Repository-specific operating rules should stay repo-scoped and should not silently leak into other projects.

## Mandatory Start

At the start of each new conversation:

1. Read [docs/workflows/session-start.md](/Users/tr/Workspace/agents-lab/docs/workflows/session-start.md).
2. Load the project seed memories described there.
3. If Shared Memory MCP appears broken, run the documented smoke test before claiming the tool is unavailable.

## Working Rules

- Default to Shared Memory reads and writes scoped to `proj/agents-lab`.
- Reuse prior conclusions when they exist; update only the delta.
- When a result is likely to be reused, store a short memory entry with stable tags.
- Keep repo-specific skills under repo scope unless they are explicitly generalized.

## Documentation Map

- [docs/README.md](/Users/tr/Workspace/agents-lab/docs/README.md): documentation index
- [docs/context/memory.md](/Users/tr/Workspace/agents-lab/docs/context/memory.md): Shared Memory conventions
- [docs/workflows/session-start.md](/Users/tr/Workspace/agents-lab/docs/workflows/session-start.md): required session bootstrap
- [docs/workflows/research-loop.md](/Users/tr/Workspace/agents-lab/docs/workflows/research-loop.md): how to run research and distill reusable output
- [docs/conventions/skills.md](/Users/tr/Workspace/agents-lab/docs/conventions/skills.md): repo-scoped skill guidance
- [docs/conventions/memory-writing.md](/Users/tr/Workspace/agents-lab/docs/conventions/memory-writing.md): how to write reusable memories

## Change Policy

- Keep this file short.
- Put durable details in `docs/`.
- If a rule needs examples, troubleshooting, or step-by-step instructions, add or update a doc page and link it here.
