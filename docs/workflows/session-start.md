# Session Start Workflow

Follow this sequence at the start of each new conversation in this repository.

## Step 1: Read Repository Entry Point

Read [AGENTS.md](../../AGENTS.md).

## Step 2: Load Project Seeds

If the current runtime exposes Shared Memory MCP, load:

- `taxonomy_v1`
- `products_scope_v1`
- `workflow_weekly_v1`
- `eval_rubric_v1`

Use the project tags:

- `proj/agents-lab`
- `seed/agents-lab/2026-02-27`

## Step 3: Verify Tooling Before Falling Back

If the runtime is expected to provide Shared Memory MCP but it appears unavailable:

1. Run `python3 mcp_shared_memory/smoke_test_mcp.py`
2. Inspect `/MCP` output or the relevant logs
3. Only then report that the tool is unavailable

## Step 4: Read Daily Working Memory

When available, read the current Working Memory briefing to recover recent priorities and context.

## Outcome

Before answering substantively, the agent should know:

- the project namespace
- the current research taxonomy
- the default workflow for weekly and ad hoc research
- whether memory tooling is functioning, unsupported in the current runtime, or genuinely blocked
