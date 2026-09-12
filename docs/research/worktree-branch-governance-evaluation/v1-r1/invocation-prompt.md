# Exact Invocation Prompt

For each fresh user-created Codex task, replace every occurrence of `{{REPOSITORY_ROOT}}` with the one recorded absolute target root. Make no other change. Do not attach any other trial material.

```text
Use the worktree-branch-governance-audit skill to perform a read-only governance audit of the Git repository at {{REPOSITORY_ROOT}}.

This is an independent evaluation trial. Work only in this fresh user-created Codex task. Do not read another task, source task, sibling task, prior trial, prior report, coordinator task, or answer key. Do not use local, shared, or cross-tool memory. Do not search for or read evaluation scaffolds, scorecards, semantic rubrics, expected findings, or prior audit outputs. In your final response, disclose whether any such capability or material was available or accessed.

Discover the skill through the harness's normal skill discovery mechanism. Before auditing, verify that the loaded canonical SKILL.md has SHA-256 6b666518d08e7b680aa3449e98db8586ed2538c6c5e66ddee1831353cdfaad7e and record how discovery and verification were performed. Do not substitute another skill version.

Do not fetch, pull, contact remotes, call remote APIs, browse the web, inspect hosting providers, or use any other network access. Treat remote-tracking refs as cached local state. Do not mutate the target repository: do not edit files, configuration, documentation, Git state, branches, or worktrees. Use read-only inspection only, and capture sufficient pre-trial and post-trial status evidence to detect target changes.

Write the audit report to a concrete absolute path outside {{REPOSITORY_ROOT}}. Resolve the path before writing; do not use a filename containing literal shell substitution syntax. Validate the completed report with the validator bundled with the verified skill. Do not write the report or any trial artifact inside the target repository.

Return the concrete report path, validator command and result, pre/post target-integrity evidence, skill-discovery and hash-verification evidence, and capability/access disclosures in your final response. The report and this task transcript will be returned to the evaluation coordinator for scoring. Do not score the report or infer expected answers.
```

