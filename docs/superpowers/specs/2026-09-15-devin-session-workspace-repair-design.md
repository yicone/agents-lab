# Devin Session-to-Workspace Repair Design

## Goal

Provide a narrowly scoped, auditable procedure for investigating and repairing
incorrect extra working directories on local Devin CLI sessions, without
changing project files, session transcripts, or unrelated sessions.

## Scope and boundaries

- Target only local Devin CLI sessions stored in the macOS paths documented in
  the case records.
- Treat `sessions.working_directory` as the primary directory and
  `sessions.workspace_dirs` as its extra-directory list.
- Keep the Desktop mirror
  `info._meta["cognition.ai/additionalWorkspaceDirs"]` consistent with the
  CLI source.
- Do not infer or modify Space membership, shared multi-root
  `workspace.json`, project content, or non-targeted session records.
- Refuse mutation while a Devin writer holds either database.

## Layout

`docs/research/devin-session-workspace/` is the self-contained evidence and
tooling directory:

- `README.md`: data model, supported platform, investigation workflow, and
  recovery boundary.
- `cases/case-001-elder-oasis-extra-yr.md`: factual record of the four-session
  incident, including confirmed facts and open hypotheses.
- `tools/repair_session_workspace.py`: Python standard-library command-line
  tool. It has an inspect mode and an explicitly gated repair mode.
- `tests/test_repair_session_workspace.py`: temporary SQLite fixture tests;
  never open a real Devin database.

## Command model

`inspect` takes one or more session IDs and reports the CLI source and Desktop
mirror side by side. It changes nothing.

`repair` requires all of the following:

1. one or more explicit `--session` IDs;
2. `--expect-cwd` and an exact expected old extra-directory JSON value;
3. `--set-extra-json`, which may be `[]` but never defaults to clearing;
4. `--apply` to opt into mutation.

Before a repair, the tool checks that known Devin database files are not open,
copies the source database/WAL and Desktop state database to a timestamped
backup directory, and writes SHA-256 checksums. It uses guarded SQLite
transactions for the target rows only, then reads both stores back.

## Repair semantics

For a session, Devin derives agent working directories by de-duplicating
`working_directory` followed by configured extra directories. The repair
therefore changes only the extras.

The Desktop mirror has a higher read priority than the CLI `workspace_dirs`
field. When the desired extra list is empty, the tool removes the mirror key;
when it is non-empty, it writes the exact JSON array. This prevents stale
Desktop state from overriding the corrected CLI source.

## Verification

Automated tests cover inspect output, refusal without `--apply`, exact old
value guards, source-and-mirror update, and backup manifest generation. A
production run additionally requires a cold restart of Devin Desktop and a
manual Explorer check for every affected session.
