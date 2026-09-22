# Agent Operating Rules (agents-lab)

This file is the entry point for agents working in this repository.

Read this first, then follow the linked docs instead of expanding this file into a large manual.

## Purpose

- This repo studies coding agents, orchestration, memory, skills, and related workflows.
- This repo may be accessed by multiple agent runtimes, including Codex, Windsurf, Claude Code, Antigravity, OpenCode, and GitHub Copilot.
- The default project namespace for reusable memory is `proj/agents-lab`.
- Repository-specific operating rules should stay repo-scoped and should not silently leak into other projects.

## Mandatory Start

At the start of each new conversation:

1. Read [docs/workflows/session-start.md](docs/workflows/session-start.md).
2. Load the project seed memories described there.
3. If Shared Memory MCP appears broken, run the documented smoke test before claiming the tool is unavailable.

## Working Rules

- Keep durable repository rules tool-agnostic unless a step is inherently runtime-specific.
- Default to Shared Memory reads and writes scoped to `proj/agents-lab`.
- Reuse prior conclusions when they exist; update only the delta.
- When a result is likely to be reused, store a short memory entry with stable tags.
- Keep repo-specific skills under repo scope unless they are explicitly generalized.

## Documentation Map

- [docs/README.md](docs/README.md): documentation index
- [docs/context/memory.md](docs/context/memory.md): Shared Memory conventions
- [docs/workflows/session-start.md](docs/workflows/session-start.md): required session bootstrap
- [docs/workflows/research-loop.md](docs/workflows/research-loop.md): how to run research and distill reusable output
- [skills-governance/README.md](skills-governance/README.md): local-machine Skills governance entrypoint
- [skills-governance/docs/skills.md](skills-governance/docs/skills.md): Skills source and scope guidance
- [docs/conventions/memory-writing.md](docs/conventions/memory-writing.md): how to write reusable memories

## Change Policy

- Keep this file short.
- Put durable details in `docs/`.
- If a rule needs examples, troubleshooting, or step-by-step instructions, add or update a doc page and link it here.
