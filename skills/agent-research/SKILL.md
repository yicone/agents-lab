---
name: agent-research
description: "Workflow for researching AI Agents (especially coding agents): classify projects, keep an evidence log, and write durable cross-session memories via an MCP memory server."
---

# Agent Research (Coding Agents)

## What this skill is for

Use this skill when the user is researching:

- Coding Agents (Windsurf/Codex/Claude Code/OpenCode/Amp/Cursor…)
- Orchestrators / multi-agent workflows (Agent Teams, agent orchestrators, plugins)
- Memory / context layers (MCP memory servers, long-term memory)
- Skills / context engineering (skills catalogs, prompt corpora)

## Default workflow (fast + repeatable)

1) **Restate the question as a decision**  
   Example: “Do we invest time in X this week, or just monitor it?”

2) **Pull prior context (if MCP memory is available)**  
   - Search: query = project name + axis + “pitfalls/benchmarks”
   - Repo scope (agents-lab): always filter by `proj/agents-lab` to avoid cross-project leakage
   - If there’s a prior conclusion, reuse it and only update deltas

3) **Classify the item (one primary, one secondary)**
   - Primary axes: `coding-agent`, `orchestrator`, `memory`, `skills`, `platform/host`, `prompts`
   - Secondary: `open-source` vs `closed`, `cli` vs `ide`, `single-agent` vs `multi-agent`

4) **Capture evidence, not vibes**
   - What is it? (1 sentence)
   - What problem does it solve? (1 sentence)
   - What is novel? (1–3 bullets)
   - What would convince us it’s worth deeper time? (clear criteria)

5) **Write a durable memory (only if it will be reused)**
   Save: taxonomy decisions, recurring constraints, “we already tried X and it failed because Y”.

## Suggested memory tags

- `proj/agents-lab` (project namespace; always include when writing/searching in this repo)
- `seed/agents-lab/2026-02-27` (repo-scoped seed tag for this project’s taxonomy/products/workflow/rubric)
- `axis/coding-agent` `axis/orchestrator` `axis/memory` `axis/skills`
- `product/windsurf` `product/codex` `product/claude-code` `product/opencode` `product/amp` `product/cursor`
- `topic/mcp` `topic/evals` `topic/benchmarks` `topic/workflow` `topic/context-engineering`

## If the user asks “what skill should I install?”

Use the Skills CLI search flow:

- Run `npx skills find <keywords>` (examples: `mcp`, `github`, `research`, `playwright`, `repo review`)
- Present 2–5 options with install commands and what each covers

If no skill fits, propose creating a micro-skill: one clear workflow + 1–2 scripts max.
