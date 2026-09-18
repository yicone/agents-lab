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

The harness must fill the session id and root in the prompt and verify them again after parsing. `line` must be a changed line in the PR diff, `path` must be relative and normalized, and `body` must not contain secrets or executable shell text. Findings should be independent and actionable; omit style-only comments unless they affect correctness, security, reliability, or maintainability.

The GitHub comment body should start with a stable marker:

```text
<!-- devin-pr-review session=<session-id> finding=F-001 -->
**[high] Short defect title**

Actionable explanation...
```

Before POSTing, search existing PR review comments for that marker and skip an already-present finding. Keep model response and posted body separate so a retry cannot publish raw model text.
