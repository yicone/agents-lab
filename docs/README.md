# agents-lab docs

This directory is the durable system of record for repository rules that agents need during work.

## Structure

- `context/`: durable context the agent should respect across tasks
- `workflows/`: ordered procedures the agent should follow
- `conventions/`: writing and organization conventions

## Start Here

- Session bootstrap: [workflows/session-start.md](/Users/tr/Workspace/agents-lab/docs/workflows/session-start.md)
- Memory rules: [context/memory.md](/Users/tr/Workspace/agents-lab/docs/context/memory.md)
- Research loop: [workflows/research-loop.md](/Users/tr/Workspace/agents-lab/docs/workflows/research-loop.md)
- Skills policy: [conventions/skills.md](/Users/tr/Workspace/agents-lab/docs/conventions/skills.md)
- Memory writing format: [conventions/memory-writing.md](/Users/tr/Workspace/agents-lab/docs/conventions/memory-writing.md)

## Maintenance Rule

If a rule is likely to outlive one task, document it here instead of expanding `AGENTS.md`.
