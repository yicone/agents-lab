# agents-lab docs

This directory indexes explanations, contracts, and historical material. Repository-wide constraints live in [AGENTS.md](../AGENTS.md), subproject constraints in nested `AGENTS.md` files, and task workflows in repository skills (`skills/` sources with `.agents/skills/` symlink entrypoints). Agent instructions define roles, permissions, and delivery.

## Structure

- `context/`: durable project context and memory reference material
- `workflows/`: workflow reference material, used when relevant
- `conventions/`: writing and organization conventions
- `../skills-governance/`: local-machine Skills governance package
- `api-gateway-cache-probe/`: reusable notes for testing caching behavior on AI gateways

## Reference Index

- Optional session context: [workflows/session-start.md](workflows/session-start.md)
- Shared Memory reference: [context/memory.md](context/memory.md)
- Research loop reference (execution entrypoint: [agent-research](../skills/agent-research/SKILL.md)): [workflows/research-loop.md](workflows/research-loop.md)
- Cross-project agent collaboration patterns: [conventions/agent-collaboration-generalizations.md](conventions/agent-collaboration-generalizations.md)
- Agent-facing doc layering: [conventions/agent-facing-doc-layering.md](conventions/agent-facing-doc-layering.md)
- Skills governance package: [../skills-governance/README.md](../skills-governance/README.md)
- Skills governance backlog: [../skills-governance/backlog.md](../skills-governance/backlog.md)
- Skills source and scope policy: [../skills-governance/docs/skills.md](../skills-governance/docs/skills.md)
- Skills versioning: [../skills-governance/docs/skills-versioning.md](../skills-governance/docs/skills-versioning.md)
- Skills governance release checklist: [../skills-governance/docs/release-checklist.md](../skills-governance/docs/release-checklist.md)
- Skills upgrade diff review: [../skills-governance/docs/upgrade-diff-review.md](../skills-governance/docs/upgrade-diff-review.md)
- Skills governance case prompt: [../skills-governance/docs/case-prompt.md](../skills-governance/docs/case-prompt.md)
- Multi-repo AGENTS.md governance research: [research/agents-md-multi-repo-governance.md](research/agents-md-multi-repo-governance.md)
- CLI-Anything adaptation design and accepted decisions: [research/cli-anything-agent-native-cli-design.md](research/cli-anything-agent-native-cli-design.md)
- Devin PR review thread evolution: [research/devin-pr-review-thread-timeline.md](research/devin-pr-review-thread-timeline.md)
- Devin Desktop session ↔ workspace archive: [research/devin-session-workspace/README.md](research/devin-session-workspace/README.md)
- Logseq vault skill inventory stub: [../skills-governance/docs/logseq-vault-skill-inventory.md](../skills-governance/docs/logseq-vault-skill-inventory.md)
- Memory writing format: [conventions/memory-writing.md](conventions/memory-writing.md)
- API gateway cache probes: [api-gateway-cache-probe/README.md](api-gateway-cache-probe/README.md)
- Skills governance action index: [../skills-governance/docs/index.md](../skills-governance/docs/index.md)

## Maintenance Rule

Keep explanations, contracts, and historical evidence here. Put short repository-wide constraints in the root `AGENTS.md`, subproject constraints in the corresponding nested `AGENTS.md`, and multi-step task workflows in skills. Do not make this index an additional normative entrypoint.
