# Devin Session-to-Workspace Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a safe, reusable local repair tool and a durable case record for
incorrect Devin session extra working directories.

**Architecture:** Keep evidence, case records, the command-line tool, and its
tests in one documentation-adjacent directory. The tool reads the Devin CLI
SQLite source and the Desktop SQLite mirror; only an explicit, guarded repair
updates both stores after a hash-verified backup.

**Tech Stack:** Python 3 standard library (`argparse`, `sqlite3`, `json`,
`hashlib`, `shutil`, `subprocess`, `unittest`), Markdown.

---

### Task 1: Establish the research and case record

**Files:**
- Create: `docs/research/devin-session-workspace/README.md`
- Create: `docs/research/devin-session-workspace/cases/case-001-elder-oasis-extra-yr.md`

- [ ] **Step 1: Document the data model and operational boundary**

State the primary/extra directory distinction, both local stores, explicit
backup requirements, and the fact that Space-write causality remains an open
hypothesis.

- [ ] **Step 2: Record case 1 with confirmed evidence separated from inference**

Record the four IDs, expected primary directory, observed `yr` extra directory,
the exact protected repair, and post-cold-start verification. Do not include
session transcript content or secrets.

### Task 2: Write failing fixture tests

**Files:**
- Create: `docs/research/devin-session-workspace/tests/test_repair_session_workspace.py`

- [ ] **Step 1: Build temporary CLI and Desktop SQLite fixtures**

Use `tempfile.TemporaryDirectory`; fixture schemas contain only the columns the
tool needs. Seed one target and one unrelated session.

- [ ] **Step 2: Test inspect and refusal paths**

Assert inspect returns both representations without mutation, and repair fails
without `--apply` or when expected cwd/extra data differ.

- [ ] **Step 3: Test a guarded repair**

Assert only the target changes, a backup manifest contains matching hashes, the
CLI extras equal `[]`, and the Desktop mirror key is absent.

- [ ] **Step 4: Run the test file and confirm it fails before implementation**

Run: `python3 -m unittest discover -s docs/research/devin-session-workspace/tests -v`

Expected: failure because the tool module is absent.

### Task 3: Implement the repair tool

**Files:**
- Create: `docs/research/devin-session-workspace/tools/repair_session_workspace.py`

- [ ] **Step 1: Implement explicit path and session validation**

Accept paths as flags for fixture tests and default to documented macOS Devin
paths for production use. Reject empty IDs and malformed JSON arrays.

- [ ] **Step 2: Implement read-only inspect**

Report CLI `working_directory`/`workspace_dirs` and Desktop mirror extras for
each requested ID. Return non-zero if a record is missing or inconsistent.

- [ ] **Step 3: Implement guarded backup and mutation**

Require `--apply`; use exact old cwd and extra-array checks, create a timestamp
backup with SHA-256 manifest, update only named sessions in transactions, and
delete the Desktop mirror key only for desired empty extras.

- [ ] **Step 4: Implement post-write verification**

Read both stores again and fail if any requested session differs from the
requested target values.

### Task 4: Validate and document usage

**Files:**
- Modify: `docs/research/devin-session-workspace/README.md`

- [ ] **Step 1: Run unit tests**

Run: `python3 -m unittest discover -s docs/research/devin-session-workspace/tests -v`

Expected: all tests pass.

- [ ] **Step 2: Run CLI help and a fixture-only inspect**

Run the script with `--help`; do not run `--apply` against real Devin paths.

- [ ] **Step 3: Add copyable commands and manual cold-start verification**

Document inspect, dry-run/plan, guarded apply, rollback from backup, and the
manual Explorer verification that completes a production repair.
