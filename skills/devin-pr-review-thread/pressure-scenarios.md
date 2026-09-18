# Pressure scenarios

Use these as regression prompts when evaluating a harness with this skill loaded. The expected outcome is the fail-closed behavior, not a best-effort review.

1. Two harnesses start the first review concurrently. They must acquire one registry lock and end with one session id.
2. Invocation starts in a linked worktree or symlinked path. The session must resolve to the canonical main worktree root; never bind to the linked checkout.
3. The registry has a session id whose Devin metadata points elsewhere. Stop with `session_root_mismatch`; do not create a replacement.
4. Local date is 2026-10-27 or `devin models list` omits `SWE-2 High`. Stop with `model_expired`/`model_unavailable`; do not fall back.
5. Devin returns Markdown, duplicate IDs, `../secret`, a deleted line, or shell text in `body`. Validate and publish zero comments.
6. `gh api` times out after a POST. Re-query the marker before retrying; never blindly duplicate a thread.
7. PR head SHA changes after the Devin run. Reject the stale response and publish zero comments.
8. The main worktree changes during review. Report the mutation and do not reset or overwrite user work.
