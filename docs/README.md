# agents-lab docs

This directory is the durable system of record for repository rules that agents need during work.
The rules here should stay tool-agnostic by default so they remain usable across multiple coding-agent runtimes.

## Structure

- `context/`: durable context the agent should respect across tasks
- `workflows/`: ordered procedures the agent should follow
- `conventions/`: writing and organization conventions
- `../skills-governance/`: local-machine Skills governance package
- `api-gateway-cache-probe/`: reusable notes for testing caching behavior on AI gateways

## Start Here

- Session bootstrap: [workflows/session-start.md](workflows/session-start.md)
- Memory rules: [context/memory.md](context/memory.md)
- Research loop: [workflows/research-loop.md](workflows/research-loop.md)
- Cross-project agent collaboration patterns: [conventions/agent-collaboration-generalizations.md](conventions/agent-collaboration-generalizations.md)
- Agent-facing doc layering: [conventions/agent-facing-doc-layering.md](conventions/agent-facing-doc-layering.md)
- Skills governance package: [../skills-governance/README.md](../skills-governance/README.md)
- Skills governance backlog: [../skills-governance/backlog.md](../skills-governance/backlog.md)
- Skills source and scope policy: [../skills-governance/docs/skills.md](../skills-governance/docs/skills.md)
- Skills versioning: [../skills-governance/docs/skills-versioning.md](../skills-governance/docs/skills-versioning.md)
- Skills governance release checklist: [../skills-governance/docs/release-checklist.md](../skills-governance/docs/release-checklist.md)
- Skills governance case prompt: [../skills-governance/docs/case-prompt.md](../skills-governance/docs/case-prompt.md)
- Multi-repo AGENTS.md governance research: [research/agents-md-multi-repo-governance.md](research/agents-md-multi-repo-governance.md)
- Devin PR review thread evolution: [research/devin-pr-review-thread-timeline.md](research/devin-pr-review-thread-timeline.md)
- Logseq vault skill inventory stub: [../skills-governance/docs/logseq-vault-skill-inventory.md](../skills-governance/docs/logseq-vault-skill-inventory.md)
- Memory writing format: [conventions/memory-writing.md](conventions/memory-writing.md)
- API gateway cache probes: [api-gateway-cache-probe/README.md](api-gateway-cache-probe/README.md)
- Skills governance action index: [../skills-governance/docs/index.md](../skills-governance/docs/index.md)

## Maintenance Rule

If a rule is likely to outlive one task, document it here instead of expanding `AGENTS.md`.
