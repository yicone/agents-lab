# Shared Memory Conventions

This repository uses Shared Memory MCP for cross-session reuse of research framing, conclusions, and workflow knowledge.

## Namespace

The default namespace is `proj/agents-lab`.

- Writes: every reusable memory related to this repo must include `proj/agents-lab`
- Reads: search within `proj/agents-lab` by default
- Cross-project reuse: only search broader tags when there is a clear reason

## Seed Memories

At conversation start, load these seed memories when the MCP tool is available:

- `taxonomy_v1`
- `products_scope_v1`
- `workflow_weekly_v1`
- `eval_rubric_v1`

Recommended tags:

- `proj/agents-lab`
- `seed/agents-lab/2026-02-27`

## During Tasks

When the user mentions a product, topic, or axis such as `coding-agent`, `orchestrator`, `memory`, `skills`, `platform`, or `prompts`:

1. Search Shared Memory first with `tags=["proj/agents-lab"]`
2. Add narrower tags when helpful, such as `axis/*` or `product/*`
3. Reuse prior conclusions and update only what changed

## MCP Failure Rule

Do not claim Shared Memory is unavailable without evidence.

Acceptable evidence:

- `/MCP` output showing the server state
- The output from `python3 mcp_shared_memory/smoke_test_mcp.py`

If tool calls fail with handshake-style errors such as `timed out`, `Transport closed`, or `initialize response`, run the smoke test first.
