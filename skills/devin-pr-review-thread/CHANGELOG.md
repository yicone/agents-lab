# Changelog

## 0.3.0 — 2026-09-24

- Added host-only `host_setup.py` to enroll the exact main workspace, a future-worktree parent wildcard, and one fixed Devin session as an idempotent setup.
- Added host-only `cleanup.py` with a 30-day dry-run default, protected evidence/reference handling, explicit apply mode, and mode-0600 audit summaries.
- Hardened large review dispatch with `--prompt-file`, single-consumer queue locking, atomic request claims, stable failure evidence, and transport-aware GitHub error codes.
- Synchronized the queue/provider/session-binding protocol documentation and refreshed the v1/v2 Archify architecture artifacts.
- Added regression coverage for setup composition and retention/authorization safety.

## 0.2.0

- Fixed-session Devin PR review adapter with bounded review-round handoff, host-side preflight, structured responses, and fail-closed GitHub thread publication.
