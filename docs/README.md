# agents-lab docs

This directory is the durable system of record for repository rules that agents need during work.
The rules here should stay tool-agnostic by default so they remain usable across multiple coding-agent runtimes.

## Structure

- `context/`: durable context the agent should respect across tasks
- `workflows/`: ordered procedures the agent should follow
- `conventions/`: writing and organization conventions
- `api-gateway-cache-probe/`: reusable notes for testing caching behavior on AI gateways

## Start Here

- Session bootstrap: [workflows/session-start.md](workflows/session-start.md)
- Memory rules: [context/memory.md](context/memory.md)
- Research loop: [workflows/research-loop.md](workflows/research-loop.md)
- Cross-project agent collaboration patterns: [conventions/agent-collaboration-generalizations.md](conventions/agent-collaboration-generalizations.md)
- Skills policy: [conventions/skills.md](conventions/skills.md)
- Agent-facing doc layering: [conventions/agent-facing-doc-layering.md](conventions/agent-facing-doc-layering.md)
- Skills versioning: [conventions/skills-versioning.md](conventions/skills-versioning.md)
- Skills governance release checklist: [conventions/skills-governance-release-checklist.md](conventions/skills-governance-release-checklist.md)
- Skills governance case prompt: [conventions/skills-governance-case-prompt.md](conventions/skills-governance-case-prompt.md)
- Logseq vault skill inventory stub: [conventions/logseq-vault-skill-inventory.md](conventions/logseq-vault-skill-inventory.md)
- Memory writing format: [conventions/memory-writing.md](conventions/memory-writing.md)
- API gateway cache probes: [api-gateway-cache-probe/README.md](api-gateway-cache-probe/README.md)
- Skills governance action index: [../skills/skills-governance-index.md](../skills/skills-governance-index.md)

## Maintenance Rule

If a rule is likely to outlive one task, document it here instead of expanding `AGENTS.md`.
