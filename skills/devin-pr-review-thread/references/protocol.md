# Review protocol

The prompt sent to Devin must require one JSON document and no Markdown wrapper:

```json
{
  "schema": "devin-pr-review/v1",
  "session_id": "<filled by harness>",
  "repository_root": "/absolute/path",
  "pr_number": 123,
  "head_sha": "<40-hex-sha>",
  "findings": [
    {
      "id": "F-001",
      "severity": "blocker|high|medium|low",
      "confidence": 0.0,
      "path": "relative/path.ext",
      "line": 42,
      "side": "RIGHT",
      "title": "Short defect title",
      "body": "Actionable explanation and a concrete fix suggestion."
    }
  ]
}
```

The provider fills the fixed session id and review request root in the prompt and verifies them again after parsing. The request root identifies the repository/PR; the provider resumes Devin from the registered canonical session root so a session cannot drift between worktrees. `line` must be a changed line in the PR diff, `path` must be relative and normalized, and `body` must not contain secrets or executable shell text. Findings should be independent and actionable; omit style-only comments unless they affect correctness, security, reliability, or maintainability.

The GitHub comment body should start with a stable marker. Include the reviewed head SHA so a fixed per-repository session can reuse finding ids across review rounds without suppressing a new finding on a later head:

```text
<!-- devin-pr-review session=<session-id> head=<40-hex-sha> finding=F-001 -->
**[high] Short defect title**

Actionable explanation...
```

Before POSTing, search existing PR review comments for the complete marker and skip an already-present finding only when session, head, and finding all match. Keep model response and posted body separate so a retry cannot publish raw model text.
