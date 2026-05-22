# agents-lab docs

This directory is the durable system of record for repository rules that agents need during work.
The rules here should stay tool-agnostic by default so they remain usable across multiple coding-agent runtimes.

## Structure

- `context/`: durable context the agent should respect across tasks
- `workflows/`: ordered procedures the agent should follow
- `conventions/`: writing and organization conventions
- `api-gateway-cache-probe/`: reusable notes for testing caching behavior on AI gateways

## Start Here

- Session bootstrap: [workflows/session-start.md](/Users/tr/Workspace/agents-lab/docs/workflows/session-start.md)
- Memory rules: [context/memory.md](/Users/tr/Workspace/agents-lab/docs/context/memory.md)
- Research loop: [workflows/research-loop.md](/Users/tr/Workspace/agents-lab/docs/workflows/research-loop.md)
- Skills policy: [conventions/skills.md](/Users/tr/Workspace/agents-lab/docs/conventions/skills.md)
- Skills versioning: [conventions/skills-versioning.md](/Users/tr/Workspace/agents-lab/docs/conventions/skills-versioning.md)
- Logseq vault skill inventory stub: [conventions/logseq-vault-skill-inventory.md](/Users/tr/Workspace/agents-lab/docs/conventions/logseq-vault-skill-inventory.md)
- Memory writing format: [conventions/memory-writing.md](/Users/tr/Workspace/agents-lab/docs/conventions/memory-writing.md)
- API gateway cache probes: [api-gateway-cache-probe/README.md](/Users/tr/Workspace/agents-lab/docs/api-gateway-cache-probe/README.md)
- Skills governance action index: [../skills/skills-governance-index.md](/Users/tr/Workspace/agents-lab/skills/skills-governance-index.md)

## Maintenance Rule

If a rule is likely to outlive one task, document it here instead of expanding `AGENTS.md`.
