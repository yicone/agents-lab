# Devin parent-directory session layout

## Approved objective

Support an existing fixed Devin session whose workspace is the immediate parent
of a repository. Keep consuming agents on the existing queue-client contract;
host enrollment, trust, discovery and execution remain provider responsibilities.

## Binding and compatibility

An origin-keyed registry entry separates `repository_root` (the main Git checkout)
from `session_root` (Devin's execution cwd), records `session_layout`, and stores
the resolved Git `repository_common_dir`. Missing layout/root fields on existing
v2 entries mean `exact-root` with `repository_root=session_root`; a missing
common-dir is computed and recorded by the next host enrollment. No mass
migration is required.

`parent-of-repository` requires an explicit repository root whose resolved
immediate parent equals the resolved session root. It never infers the bound
repository from other children, a session title, or the caller's cwd. An optional
worktree parent is an explicit, strict descendant of the session root; it is not
the session root itself. Each requested worktree must also have the same Git
common directory and origin as the registered main repository. A sibling clone,
symlink escaping the boundary, or another repository is rejected.

Each session ID belongs to one normalized origin. Multiple distinct sessions may
live in one parent workspace; sharing one session between origins is rejected.
Enrollment verifies the actual session cwd and rejects an explicit model mismatch
from `devin list`. Some CLI versions omit per-session model metadata; in that
case the host still requires the exact `SWE-2 High` entry from the global model
catalog and keeps the registry pin, rather than treating missing display metadata
as a mismatch. Enrollment writes atomically under its registry lock and refuses
to rotate or silently change an existing root/layout/session binding. A
`--check-only` mode verifies eligibility without changing registry or allowlist
state.

## Runtime behavior

Parent-layout discovery queries `devin list` with explicit `cwd=session_root`.
A read-only database hint or CLI metadata showing a different workspace is a
`session_working_directory_drift` failure, not permission to resume from it.
The repository origin, resolved Git common directory and request boundary are
checked before the database hint or any worktree/CLI discovery. A target and the
registered execution root are trusted independently; both must be trusted before
the provider can run. Existing exact-root worktree discovery remains intact.

Trust must cover both the review target and the execution workspace. Enrollment
does not add trust. The provider runs Devin from the registered session root,
fetches and validates GitHub data against the request repository, and supplies a
verified patch with a prompt explicitly distinguishing both roots. No tools are
requested; the broader session cwd is not a filesystem sandbox.

For a parent-layout run, inspect the fixed session again immediately before
starting Devin and after it exits, before any GitHub publication. Directory drift
or missing/ambiguous session metadata fails closed with protected evidence.
Evidence records repository root, execution root and layout. These checks detect
drift; they cannot prevent a Desktop user changing a session concurrently.

## Delivery and verification

Add temporary-Git enrollment/preflight tests for a non-Git session parent,
nested worktrees, unrelated sibling clones (including same-origin clones),
symlink escapes, duplicate session IDs, missing/unknown layout, trust separation,
metadata drift, invalid registries and unchanged legacy exact-root behavior.
Provider tests verify the actual subprocess cwd, request-root JSON validation,
common-dir binding, and publication blocked by post-run drift. Run all existing
script tests, Python compilation and diff checks.

Live enrollment, LaunchAgent restart, and a fresh review remain host-operator
follow-up actions. They are intentionally not performed by this repository
change; when authorized, report a passing preflight separately from a completed
review.
