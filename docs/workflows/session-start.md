# Session Start Workflow

按需使用，非强制。This page is optional context reference, not a required conversation bootstrap. For research tasks that need startup or memory steps, use [skills/agent-research](../../skills/agent-research/SKILL.md) as the workflow entrypoint. Consult only the sections relevant to the task.

## Step 1: Read Repository Entry Point

Read [AGENTS.md](../../AGENTS.md).

## Step 2: Load Project Seeds

When project seed context is needed and the current runtime exposes Shared Memory MCP, load:

- `taxonomy_v1`
- `products_scope_v1`
- `workflow_weekly_v1`
- `eval_rubric_v1`

Use the project tags:

- `proj/agents-lab`
- `seed/agents-lab/2026-02-27`

## Step 3: Verify Tooling Before Falling Back

When the task needs Shared Memory and the runtime is expected to provide it but it appears unavailable:

1. Run `python3 mcp_shared_memory/smoke_test_mcp.py`
2. Inspect `/MCP` output or the relevant logs
3. Only then report that the tool is unavailable

## Step 4: Read Daily Working Memory

When recent priorities or context are needed and a Working Memory briefing is available, read it.

## Step 5: Check Skills Governance Backlog When Relevant

If the task resumes or extends skills-governance work, use ACM-122 as the sole source of backlog status, acceptance criteria, and priority. [skills-governance/backlog.md](../../skills-governance/backlog.md) is a pointer to that issue.

## Outcome

When these references are needed for the task, they help establish:

- the project namespace
- the current research taxonomy
- the default workflow for weekly and ad hoc research
- whether memory tooling is functioning, unsupported in the current runtime, or genuinely blocked
