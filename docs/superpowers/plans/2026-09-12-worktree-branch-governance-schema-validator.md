# Worktree Branch Governance Schema Validator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the governance audit profile branch-name-independent and add a deterministic validator for report structure without automating semantic governance decisions.

**Architecture:** Keep the concise first-hop boundaries in `SKILL.md`, define the complete core and conditional schemas in `references/audit-model.md`, and implement a dependency-free Python validator beside the skill. Fixtures encode the three observed failure families; standard-library unit tests exercise parser and CLI behavior.

**Tech Stack:** Markdown, Python 3 standard library (`argparse`, `pathlib`, `re`, `unittest`), Git.

**Repository constraint:** Implement directly in `/Users/tr/Workspace/agents-lab`; do not create a worktree.

---

### Task 1: Add failing report-contract fixtures

**Files:**

- Create: `skills/worktree-branch-governance-audit/tests/fixtures/valid-core.md`
- Create: `skills/worktree-branch-governance-audit/tests/fixtures/valid-deployment.md`
- Create: `skills/worktree-branch-governance-audit/tests/fixtures/invalid-legacy-main-role.md`
- Create: `skills/worktree-branch-governance-audit/tests/fixtures/invalid-literal-branch-role.md`
- Create: `skills/worktree-branch-governance-audit/tests/fixtures/invalid-wildcard-source.md`
- Create: `skills/worktree-branch-governance-audit/tests/fixtures/invalid-unknown-high-confidence.md`
- Create: `skills/worktree-branch-governance-audit/tests/fixtures/invalid-finding-destination.md`
- Create: `skills/worktree-branch-governance-audit/tests/test_validate_report.py`

- [ ] **Step 1: Write minimal valid reports**

Include every required heading, a six-column Source Inventory table, all core profile fields in `field: value` plus `confidence: level` pairs, and one complete Difference Finding. The deployment fixture additionally includes all deployment extension fields.

- [ ] **Step 2: Write one-invalidity-per-fixture reports**

Derive each invalid fixture from a valid report and introduce exactly the named failure so diagnostics remain specific.

- [ ] **Step 3: Write failing unit tests**

Import `validate_report` from `scripts/validate_report.py`. Assert valid fixtures produce no diagnostics and each invalid fixture contains its expected diagnostic code:

```python
EXPECTED = {
    "invalid-legacy-main-role.md": "profile.unknown-key",
    "invalid-literal-branch-role.md": "profile.invalid-role",
    "invalid-wildcard-source.md": "source.wildcard",
    "invalid-unknown-high-confidence.md": "profile.unknown-confidence",
    "invalid-finding-destination.md": "finding.missing-field",
}
```

- [ ] **Step 4: Run tests to verify RED**

Run:

```bash
python3 -m unittest discover -s skills/worktree-branch-governance-audit/tests -v
```

Expected: FAIL because `scripts/validate_report.py` does not exist.

### Task 2: Implement the dependency-free validator

**Files:**

- Create: `skills/worktree-branch-governance-audit/scripts/validate_report.py`
- Modify: `skills/worktree-branch-governance-audit/tests/test_validate_report.py`

- [ ] **Step 1: Define schema constants and diagnostics**

Define required headings, Source Inventory columns, core keys, optional extension keys, allowed enum values, and a small immutable diagnostic record with `code`, `message`, and optional `line`.

- [ ] **Step 2: Parse Markdown sections and tables**

Implement focused helpers that:

- recognize ATX headings while ignoring fenced code blocks;
- require each report section exactly once;
- parse the first Markdown table under Source Inventory;
- parse Observed Profile field/confidence pairs;
- split Difference Findings by level-three headings or list item boundaries.

- [ ] **Step 3: Validate source records**

Require normalized columns `source`, `class`, `scope`, `normativity`, `freshness`, and `visibility`. Reject source cells containing glob markers (`*`, `?`, `[...]`) unless escaped or inside an exact command string that is clearly not a source path.

- [ ] **Step 4: Validate profile schema**

Reject legacy or unknown keys. Require every core key exactly once and every profile field to have confidence. Validate enum values, semantic `branch_roles`, and the `unknown` plus high-confidence contradiction. Allow conditional extension groups only when each group is complete.

- [ ] **Step 5: Validate findings**

Require `type`, `evidence`, `confidence`, `recommended action`, and `proposed destination` for each finding. Validate type, confidence, and action enums, but do not assess truth or recommendation quality.

- [ ] **Step 6: Add CLI behavior**

Support:

```bash
python3 scripts/validate_report.py REPORT.md
```

Print `path:line: code: message` diagnostics to stderr, return `0` for valid reports, `1` for contract failures, and `2` for usage or file-read errors.

- [ ] **Step 7: Run tests to verify GREEN**

Run:

```bash
python3 -m unittest discover -s skills/worktree-branch-governance-audit/tests -v
```

Expected: all tests PASS.

### Task 3: Upgrade the audit schema and first-hop guidance

**Files:**

- Modify: `skills/worktree-branch-governance-audit/SKILL.md`
- Modify: `skills/worktree-branch-governance-audit/references/audit-model.md`

- [ ] **Step 1: Replace name-coupled core fields**

Replace `main_role`, `direct_main_changes`, `worktree_mode`, and `branch_families` with the approved `primary_branch`, `primary_branch_role`, `direct_primary_changes`, `worktree_policy`, `worktree_adoption`, `branch_roles`, and `branch_patterns` fields.

- [ ] **Step 2: Stabilize conditional extensions**

Add the fork/runtime and deployment extensions from the approved spec. State that extension groups are conditional and complete when used.

- [ ] **Step 3: Clarify confidence semantics**

State that uninspected or missing evidence produces `unknown` with low confidence; proven absence uses an explicit negative enum with evidence.

- [ ] **Step 4: Add classification and enforcement rules**

Add `git_object_confusion`. Restrict `unverifiable_claim` to unsupported material claims. Require consequence, deterministic feasibility, absence of another control, and proportionality before `enforcement_gap`.

- [ ] **Step 5: Require validation without target mutation**

Tell auditors to validate a temporary report draft with `scripts/validate_report.py` before final output when the script is available. Make clear that the temporary file must remain outside the target repository and validation does not authorize repository writes.

- [ ] **Step 6: Check first-hop/reference alignment**

Use `rg` to confirm no live contract text still requires legacy keys and that the report shape in both files uses the same names.

### Task 4: Add the AICenter regression case and update tooling status

**Files:**

- Modify: `skills/worktree-branch-governance-audit/regression-tests.md`
- Modify: `skills/worktree-branch-governance-audit/references/tooling-evaluation.md`

- [ ] **Step 1: Add the non-`main` production branch case**

Record evidence: `aimaiai` is the documented production/integration target, literal `main` is a reference branch, `.worktrees/` is actively used, upstream changes are selectively adopted, and Vercel's actual binding remains unverified.

- [ ] **Step 2: Encode expected behavior**

Require `primary_branch: aimaiai`, semantic roles separate from literal patterns, conditional deployment fields, remote-evidence limits, proportionate enforcement analysis, and one inventory record per relied-on plan.

- [ ] **Step 3: Record the failing baseline honestly**

Note that the fresh repository audit passed scope and cached-ref rules but produced legacy multi-branch `main_role`, literal `branch_families`, wildcard source aggregation, `unknown` with high confidence, classification errors, and an overbroad enforcement recommendation.

- [ ] **Step 4: Move custom validation to evaluate-now**

Update the tooling evaluation: `agnix` and AGENTS-focused linters remain candidates for source-file checks; the local report validator is now `evaluate-now` because the post-revision independent audit repeated a mechanically detectable report failure.

### Task 5: Verify CLI, fixtures, and skill package

**Files:**

- Verify all files under `skills/worktree-branch-governance-audit/`

- [ ] **Step 1: Run the complete unit suite**

```bash
python3 -m unittest discover -s skills/worktree-branch-governance-audit/tests -v
```

Expected: PASS.

- [ ] **Step 2: Exercise valid CLI fixtures**

```bash
python3 skills/worktree-branch-governance-audit/scripts/validate_report.py \
  skills/worktree-branch-governance-audit/tests/fixtures/valid-core.md
python3 skills/worktree-branch-governance-audit/scripts/validate_report.py \
  skills/worktree-branch-governance-audit/tests/fixtures/valid-deployment.md
```

Expected: exit `0`, no diagnostics.

- [ ] **Step 3: Exercise an invalid CLI fixture**

```bash
python3 skills/worktree-branch-governance-audit/scripts/validate_report.py \
  skills/worktree-branch-governance-audit/tests/fixtures/invalid-wildcard-source.md
```

Expected: exit `1` with `source.wildcard`.

- [ ] **Step 4: Run repository consistency checks**

```bash
git diff --check
rg -n "main_role|direct_main_changes|worktree_mode|branch_families" \
  skills/worktree-branch-governance-audit
```

Expected: no whitespace errors; legacy names appear only in historical failing-baseline prose or invalid fixtures.

- [ ] **Step 5: Verify global adapter resolution**

```bash
realpath ~/.agents/skills/worktree-branch-governance-audit
shasum -a 256 skills/worktree-branch-governance-audit/SKILL.md \
  ~/.agents/skills/worktree-branch-governance-audit/SKILL.md
```

Expected: realpath resolves to the canonical skill directory and hashes match.

- [ ] **Step 6: Review diff and commit**

Confirm the diff is limited to the approved skill, tests, validator, tooling evaluation, and this plan. Commit with:

```bash
git add docs/superpowers/plans/2026-09-12-worktree-branch-governance-schema-validator.md \
  skills/worktree-branch-governance-audit
git commit -m "feat: validate branch governance audit reports"
```

### Task 6: Run the next behavioral GREEN test

**Files:**

- No repository changes during the audit.

- [ ] **Step 1: Start a fresh task in a different project archetype**

Use the commercial monorepo with a test server. Invoke the skill with the exact repository root and read-only constraints, without revealing expected profile values or prior failures.

- [ ] **Step 2: Verify loaded revision**

Confirm the injected skill contains `primary_branch`, `branch_roles`, and the report-validator instruction.

- [ ] **Step 3: Score behavior separately from parser tests**

Check profile semantics, source attribution, confidence, enforcement proportionality, target scope, and report-validator use. Record any feedback before promoting the schema from experimental status.
