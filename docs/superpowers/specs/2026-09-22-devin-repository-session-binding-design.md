# Devin repository-level session binding

## Problem

`devin-pr-review-thread` currently binds a Devin session to one exact canonical
worktree root. That prevents a repository's main workspace and nested linked
worktrees from sharing the requested single fixed session. `host_enroll.py`
only binds filesystem roots to a GitHub origin and must remain separate from
Devin session registration.

## Decision

Bind the fixed Devin session by canonical GitHub origin (`owner/repository`),
while keeping the main workspace as the session's Devin working directory.
Every review request still carries its concrete worktree root. The provider
resolves that root's Git origin, requires an exact match with the registered
repository binding, and uses the fixed session for that repository.

## Registry and migration

Keep the registry host-owned and non-secret, but make its key an exact GitHub
origin rather than a filesystem wildcard:

```json
{
  "yicone/yr-monorepo": {
    "repository": "yicone/yr-monorepo",
    "session_id": "succinct-avenue",
    "session_root": "/Users/tr/Workspace/yr",
    "model": "SWE-2 High",
    "created_at": "2026-09-20T00:00:00Z",
    "last_verified_at": "2026-09-20T00:00:00Z"
  }
}
```

The provider must verify that the session exists, has the exact configured
model, and is rooted at `session_root`. Registry version 2 is keyed by exact
normalized origin; the old root-keyed format is read-only legacy input and
must be migrated by the host-only enrollment tool before review. The provider
must not silently create a second session or accept a root mismatch as a new
binding.

Origin normalization accepts SSH and GitHub HTTPS forms, removes one trailing
`.git`, rejects URL credentials, ports, enterprise hosts, fragments, query
strings, and ambiguous/multiple `remote.origin.url` values, and returns the
case-preserving `owner/repository` path only when it has exactly two non-empty
segments. A registry entry is rejected if the key and its `repository` field
normalize differently. Duplicate origin entries or one session ID referenced
by multiple origins are hard failures; the tool must not choose one.

## Request flow

1. Host preflight canonicalizes the requested worktree root.
2. It resolves `remote.origin.url` and normalizes it to `owner/repository`.
3. It loads the exact origin entry from the session registry.
4. It verifies the Devin session against the registered `session_root`.
5. It checks the requested root against the host allowlist and its actual
   origin. A worktree is eligible only when it is a Git worktree of that
   origin and is either the registered session root or an enrolled worktree
   descendant; unrelated paths are rejected.
6. The provider resumes the fixed session from the registered canonical main
   workspace and includes the requested worktree root only as the verified
   review target in the prompt. The session's registered root remains the trust
   anchor; the target cannot be outside the enrolled repository/worktree
   boundary. GitHub remains the external source and publication target.

Nested worktrees are supported because their individual roots are not used as
registry keys. They are still independently checked for origin and allowlist
membership. A worktree from another repository is rejected.

## Tools and ownership

- `host_enroll.py`: GitHub origin allowlist only; no Devin session mutation.
- `session_enroll.py` (new host-only tool): create or register exactly one
  repository session under an exclusive lock, then verify it with `devin list`.
- `host_worker.py`: host-side queue supervisor and one-shot authorization;
  it does not create sessions and may run persistently outside the sandbox.
- `preflight.py`: read-only repository-level session verification.
- `host_provider.py`: review execution, validation, and GitHub publication.

## Failure behavior

- Missing origin entry: `session_registry_missing` → `await-user`.
- Missing or mismatched Devin session: `session_missing` or
  `session_root_mismatch` → `await-user`.
- Different worktree origin or target outside the enrolled boundary:
  `invalid-request` (the request is not eligible for this repository session).
- Missing allowlist or inconsistent host enrollment: `await-user` with
  protected evidence (host state must be repaired).
- No GitHub access or missing head SHA: `await-user` with protected evidence.

No consuming agent edits the registry, trust store, session locks, or Devin
Desktop state.

## Verification

Add tests for:

- main workspace and nested worktree resolving to one origin entry;
- sibling and nested worktrees from a different origin being rejected;
- a worktree outside the registered session-root parent being rejected;
- exact session-root verification;
- SSH/HTTPS origin normalization and ambiguous remote rejection;
- duplicate origin/session references failing closed;
- registry entries with missing, duplicate, or mismatched sessions;
- `host_enroll.py` continuing to modify only the origin allowlist;
- `host_worker.py` continuing to issue authorization without session creation.
