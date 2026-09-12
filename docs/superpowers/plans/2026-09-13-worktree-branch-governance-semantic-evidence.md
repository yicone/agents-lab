# Worktree Branch Governance Semantic Evidence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `writing-skills` while changing the skill package, plus `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the approved component-aware report contract, semantic evidence safeguards, and measurable v1 evaluation protocol for the read-only branch/worktree governance audit.

**Architecture:** Keep the user-facing workflow in `SKILL.md`, the complete semantic contract in `references/audit-model.md`, and deterministic syntax checks in the dependency-free Python validator. Encode each newly rejected shape as a focused fixture before changing the validator. Keep behavioral scoring in a separate evaluation protocol because evidence truth and project fit cannot be established by parsing Markdown.

**Tech Stack:** Markdown, Python 3 standard library (`argparse`, `dataclasses`, `pathlib`, `re`, `unittest`), Git.

**Repository constraint:** Implement directly in `/Users/tr/Workspace/agents-lab`; do not create a worktree. Do not run an audit against or mutate any target repository as part of deterministic implementation tasks.

**Approved design:** `docs/superpowers/specs/2026-09-13-worktree-branch-governance-semantic-evidence-design.md`

---

## File Map

- Modify `.codex/skills/worktree-branch-governance-audit/SKILL.md`: first-hop execution and report rules.
- Modify `.codex/skills/worktree-branch-governance-audit/references/audit-model.md`: canonical profile, finding, evidence, and placement semantics.
- Modify `.codex/skills/worktree-branch-governance-audit/scripts/validate_report.py`: deterministic report-contract enforcement only.
- Modify `.codex/skills/worktree-branch-governance-audit/tests/test_validate_report.py`: fixture and CLI expectations.
- Add focused files under `.codex/skills/worktree-branch-governance-audit/tests/fixtures/`: one valid or invalid contract behavior per fixture.
- Modify `.codex/skills/worktree-branch-governance-audit/regression-tests.md`: preserve the second YR semantic RED baseline and its corrected expectations.
- Create `.codex/skills/worktree-branch-governance-audit/references/evaluation-protocol.md`: reference configuration, trial validity, scoring matrix, release gate, and stop rule.

### Task 1: Add RED fixtures for the revised deployment contract

**Files:**

- Modify: `.codex/skills/worktree-branch-governance-audit/tests/test_validate_report.py`
- Replace: `.codex/skills/worktree-branch-governance-audit/tests/fixtures/valid-deployment.md`
- Create: `.codex/skills/worktree-branch-governance-audit/tests/fixtures/valid-component-deployment.md`
- Create: `.codex/skills/worktree-branch-governance-audit/tests/fixtures/invalid-legacy-deployment-fields.md`
- Create: `.codex/skills/worktree-branch-governance-audit/tests/fixtures/invalid-deployment-component-mismatch.md`
- Create: `.codex/skills/worktree-branch-governance-audit/tests/fixtures/invalid-deployment-binding.md`
- Create: `.codex/skills/worktree-branch-governance-audit/tests/fixtures/invalid-deployment-trigger.md`

- [ ] **Step 1: Rewrite the valid single-source deployment fixture**

Use the complete new extension with adjacent confidence after every field:

```text
deployment_topology: single-source
confidence: high
deployment_bindings: [site=branch:trunk]
confidence: high
deployment_triggers: [site=merge]
confidence: high
preview_behavior: pull-request-preview
confidence: high
```

- [ ] **Step 2: Add a valid component-specific fixture**

Use matching component sets, for example `api`, `admin`, and `miniapp`, and include a `branch-pattern:release/miniapp-*` binding to prove that pattern text inside a list is accepted.

- [ ] **Step 3: Add isolated invalid fixtures**

Each fixture starts from a valid report and introduces one defect only: a legacy scalar field, mismatched component sets, a binding without an allowed `branch:`, `branch-pattern:`, or `tag-pattern:` prefix, or a trigger outside `merge|push|tag|manual|external|unknown`.

- [ ] **Step 4: Register exact expected diagnostics**

Extend the fixture table with:

```python
"invalid-legacy-deployment-fields.md": "profile.unknown-key",
"invalid-deployment-component-mismatch.md": "profile.deployment-components",
"invalid-deployment-binding.md": "profile.invalid-deployment-binding",
"invalid-deployment-trigger.md": "profile.invalid-deployment-trigger",
```

- [ ] **Step 5: Run the fixture suite and verify RED**

Run:

```bash
python3 -m unittest discover -s .codex/skills/worktree-branch-governance-audit/tests -v
```

Expected: new valid fixtures fail on unknown keys and new invalid fixtures do not yet produce their expected diagnostics.

- [ ] **Step 6: Commit the RED fixtures**

```bash
git add .codex/skills/worktree-branch-governance-audit/tests
git commit -m "test: define component deployment report contract"
```

### Task 2: Implement component-aware deployment validation

**Files:**

- Modify: `.codex/skills/worktree-branch-governance-audit/scripts/validate_report.py`
- Test: `.codex/skills/worktree-branch-governance-audit/tests/test_validate_report.py`

- [ ] **Step 1: Replace the deployment constants**

Define the new complete extension and allowed item vocabularies:

```python
DEPLOYMENT_FIELDS = {
    "deployment_topology": {"single-source", "component-specific", "external", "unknown"},
    "deployment_bindings": None,
    "deployment_triggers": None,
    "preview_behavior": {
        "none", "branch-preview", "pull-request-preview",
        "environment-preview", "mixed", "unknown",
    },
}
DEPLOYMENT_BINDING_KINDS = {"branch", "branch-pattern", "tag-pattern"}
DEPLOYMENT_TRIGGERS = {"merge", "push", "tag", "manual", "external", "unknown"}
```

- [ ] **Step 2: Parse keyed deployment items without interpreting repository truth**

Add a helper that accepts `<component>=<kind>:<literal>` bindings and `<component>=<trigger>` triggers, rejects empty or duplicate component keys, and returns the component set. Keep this parser independent of Git or deployment-provider access.

- [ ] **Step 3: Enforce list shape and matching component sets**

Require square-bracket list syntax for both fields. Emit line-oriented diagnostics for malformed items and emit `profile.deployment-components` when the binding and trigger key sets differ.

- [ ] **Step 4: Run the focused and complete tests**

```bash
python3 -m unittest discover -s .codex/skills/worktree-branch-governance-audit/tests -v
```

Expected: all deployment fixtures and all pre-existing fixtures PASS.

- [ ] **Step 5: Commit the validator migration**

```bash
git add .codex/skills/worktree-branch-governance-audit/scripts/validate_report.py
git commit -m "feat: validate component deployment bindings"
```

### Task 3: Add RED fixtures for evidence-bearing findings and source precision

**Files:**

- Modify: `.codex/skills/worktree-branch-governance-audit/tests/test_validate_report.py`
- Modify all valid fixtures containing findings under `.codex/skills/worktree-branch-governance-audit/tests/fixtures/`
- Create: `.codex/skills/worktree-branch-governance-audit/tests/fixtures/invalid-finding-baseline.md`
- Create: `.codex/skills/worktree-branch-governance-audit/tests/fixtures/invalid-finding-deviation.md`
- Create: `.codex/skills/worktree-branch-governance-audit/tests/fixtures/invalid-finding-keep.md`
- Create: `.codex/skills/worktree-branch-governance-audit/tests/fixtures/invalid-compound-command-source.md`
- Create: `.codex/skills/worktree-branch-governance-audit/tests/fixtures/invalid-task-constraint-leak.md`

- [ ] **Step 1: Upgrade valid findings**

Every valid finding must use:

```text
type:
baseline:
deviation:
difference:
evidence:
confidence:
recommended_action:
proposed_destination:
```

Use distinct, non-empty baseline and deviation text. Keep `none discovered` fixtures unchanged.

- [ ] **Step 2: Add one-invalidity-per-fixture reports**

Cover missing `baseline`, missing `deviation`, removed action `keep`, two inline Git commands joined in one Source Inventory source cell, and known run-local wording such as “this audit is read-only” under Explicit Project Constraints.

- [ ] **Step 3: Register exact expected diagnostics**

Use `finding.missing-field`, `finding.invalid-action`, `source.compound`, and `constraint.task-leak` as appropriate.

- [ ] **Step 4: Run tests and verify RED**

Expected: the revised valid findings fail until required fields are implemented; the new invalid fixtures lack their intended diagnostics.

- [ ] **Step 5: Commit the RED fixtures**

```bash
git add .codex/skills/worktree-branch-governance-audit/tests
git commit -m "test: require evidence-bearing governance findings"
```

### Task 4: Implement finding, constraint, and source checks

**Files:**

- Modify: `.codex/skills/worktree-branch-governance-audit/scripts/validate_report.py`
- Test: `.codex/skills/worktree-branch-governance-audit/tests/test_validate_report.py`

- [ ] **Step 1: Require baseline and deviation**

Add both fields to the required finding set. Reject blank values and values where normalized `baseline`, `deviation`, and `difference` collapse to generic placeholders or baseline equals deviation. Keep these checks deliberately narrow so the validator does not claim semantic truth.

- [ ] **Step 2: Remove `keep` from finding actions**

Delete `keep` from `FINDING_ACTIONS`. Positive alignment remains valid only in Observed Facts And Rules, not as a Difference Finding.

- [ ] **Step 3: Detect compound inline command sources**

Extend `_has_compound_inline_sources` to classify multiple command-like inline spans such as `` `git remote -v` and `git config --get ...` `` while continuing to allow ordinary prose with one inline source and the word “and.”

- [ ] **Step 4: Add narrow task-mechanics leakage checks**

Inspect only the Explicit Project Constraints section for a small approved phrase set covering audit-local read-only, no-fetch, no-worktree, and temporary-report instructions. Emit `constraint.task-leak`; do not attempt general natural-language policy classification.

- [ ] **Step 5: Run the complete suite**

```bash
python3 -m unittest discover -s .codex/skills/worktree-branch-governance-audit/tests -v
```

Expected: all valid and invalid fixture tests PASS.

- [ ] **Step 6: Commit the checks**

```bash
git add .codex/skills/worktree-branch-governance-audit/scripts/validate_report.py
git commit -m "feat: enforce audit evidence shape"
```

### Task 5: Align first-hop skill and canonical audit model

**Files:**

- Modify: `.codex/skills/worktree-branch-governance-audit/SKILL.md`
- Modify: `.codex/skills/worktree-branch-governance-audit/references/audit-model.md`

- [ ] **Step 1: Replace all legacy deployment contract text**

Use `deployment_topology`, keyed `deployment_bindings`, keyed `deployment_triggers`, and the revised `preview_behavior` enum. State that release-source policy does not prove a live deployment.

- [ ] **Step 2: Add the upstream evidence gate**

Permit fork fields only from an applicable rule, configured upstream source, or authoritative project document. State explicitly that one `origin`, third-party dependencies, generic contribution prose, and suggestive branch names are insufficient.

- [ ] **Step 3: Preserve repository policy strength**

State that current audit constraints cannot weaken `worktree_policy`, branch policy, or repository constraints. Keep `Task Execution Constraints` separate.

- [ ] **Step 4: Update the finding contract**

Require `baseline` and `deviation`, remove `keep`, and explain that correct cached-ref discipline and unregistered sibling directories are observations unless an evidenced disagreement exists.

- [ ] **Step 5: Replace temporary-file guidance**

Require a resolved `mktemp` path captured before report writing. Prohibit literal shell substitutions in patch paths or filenames and require that every later validation or final reference use the resolved path.

- [ ] **Step 6: Check contract synchronization**

Run:

```bash
rg -n "production_branch|production_trigger|recommended.*keep|baseline:|deviation:|deployment_(topology|bindings|triggers)" \
  .codex/skills/worktree-branch-governance-audit
```

Expected: legacy deployment names and `keep` appear only in explicit historical or invalid-fixture contexts; current contract text is consistent.

- [ ] **Step 7: Run the complete suite and commit**

```bash
python3 -m unittest discover -s .codex/skills/worktree-branch-governance-audit/tests -v
git add .codex/skills/worktree-branch-governance-audit/SKILL.md \
  .codex/skills/worktree-branch-governance-audit/references/audit-model.md
git commit -m "docs: align governance audit semantic contract"
```

### Task 6: Operationalize the YR regression and v1 evaluation protocol

**Files:**

- Modify: `.codex/skills/worktree-branch-governance-audit/regression-tests.md`
- Create: `.codex/skills/worktree-branch-governance-audit/references/evaluation-protocol.md`
- Modify: `.codex/skills/worktree-branch-governance-audit/SKILL.md`

- [ ] **Step 1: Extend Case 7 with the second YR RED baseline**

Record the unsupported upstream extension, weakened required worktree policy, component branches concatenated into a scalar, positive-alignment findings, unregistered-directory overclassification, compound source row, and literal temporary filename.

- [ ] **Step 2: Define a valid trial record**

The protocol must require: repository archetype and root, model identifier, reasoning effort, harness/version, skill commit or hash, exact prompt, memory/remote/cross-task permissions, target pre/post status evidence, report path, validator result, semantic score, and contamination check.

- [ ] **Step 3: Define the five-archetype matrix and thresholds**

Specify three independent trials per archetype, 15 total; critical properties at 15/15; semantic acceptance at 13/15 overall and at least 2/3 per archetype. Make validator success necessary but insufficient.

- [ ] **Step 4: Define Codex App independence rules**

Prefer a fresh user-created task. Exclude coordinator-created runs that can read a source or prior task unless cross-task access was prohibited and the transcript proves no prior report was consumed. Classify exclusions as harness-protocol failures, not semantic passes or failures.

- [ ] **Step 5: Define release and stopping conditions**

After the 15-trial gate, require two held-out repositories with no core schema change; then mark v1 stable and freeze the core. Route later critical defects, new archetypes, isolated model deviations, and harness-specific failures to the destinations in the approved design.

- [ ] **Step 6: Link the protocol from the skill**

Add a short Validation reference without expanding `SKILL.md` into the full evaluation manual.

- [ ] **Step 7: Commit the protocol**

```bash
git add .codex/skills/worktree-branch-governance-audit/SKILL.md \
  .codex/skills/worktree-branch-governance-audit/regression-tests.md \
  .codex/skills/worktree-branch-governance-audit/references/evaluation-protocol.md
git commit -m "docs: define governance audit evaluation protocol"
```

### Task 7: Verify the canonical package and global adapter

**Files:**

- Verify: `.codex/skills/worktree-branch-governance-audit/`
- Verify: `~/.agents/skills/worktree-branch-governance-audit`

- [ ] **Step 1: Run all deterministic tests**

```bash
python3 -m unittest discover -s .codex/skills/worktree-branch-governance-audit/tests -v
```

Expected: all tests PASS.

- [ ] **Step 2: Exercise validator CLI exit behavior**

Run one valid component deployment fixture and one invalid mismatch fixture directly. Expected: valid exits `0` without diagnostics; invalid exits `1` with `profile.deployment-components` and a line number.

- [ ] **Step 3: Verify package text and formatting**

```bash
git diff --check
rg -n "production_branch|production_trigger" .codex/skills/worktree-branch-governance-audit
```

Expected: no whitespace errors; legacy names occur only in historical explanation or deliberately invalid fixtures.

- [ ] **Step 4: Verify the global adapter resolves to the tested package**

```bash
realpath ~/.agents/skills/worktree-branch-governance-audit
shasum -a 256 \
  .codex/skills/worktree-branch-governance-audit/SKILL.md \
  ~/.agents/skills/worktree-branch-governance-audit/SKILL.md
```

Expected: `realpath` resolves inside `/Users/tr/Workspace/agents-lab/.codex/skills/` and both hashes match. If the link is absent or points elsewhere, stop and request explicit approval before changing global state.

- [ ] **Step 5: Review the complete diff**

Confirm changes are limited to the approved plan and skill package. Confirm the validator never reads Git, contacts remotes, or writes target repositories.

- [ ] **Step 6: Commit final verification-only adjustments if needed**

```bash
git add .codex/skills/worktree-branch-governance-audit \
  docs/superpowers/plans/2026-09-13-worktree-branch-governance-semantic-evidence.md
git commit -m "feat: complete governance audit semantic evidence"
```

Skip this commit when verification required no tracked changes.

### Task 8: Execute the behavioral acceptance campaign separately

**Files:**

- Do not modify audited repositories.
- Store trial records only in a separately approved evaluation destination; do not silently add target-specific reports to this repository.

- [ ] **Step 1: Freeze and record the reference configuration**

Record the exact model, reasoning effort, Codex App version, skill commit/hash, invocation prompt, and permission surface before the first scored run.

- [ ] **Step 2: Run three blinded independent trials for each archetype**

Use explicit absolute target repository paths and the same invocation prompt. Do not expose expected profile values, prior reports, or regression answers to the auditing task.

- [ ] **Step 3: Validate and score every eligible report**

Run the deterministic validator, review transcript evidence for critical properties and contamination, then apply the semantic rubric. Exclude invalidly contaminated trials and replace them; do not count them toward 15.

- [ ] **Step 4: Apply the acceptance gate**

Require all four 15/15 critical/structural conditions, at least 13/15 semantic passes, and at least 2/3 passes per archetype. Any critical failure blocks v1 regardless of aggregate score.

- [ ] **Step 5: Run two held-out repository trials**

Proceed only after the matrix passes. If either requires a core schema change, return to design review; otherwise mark the core schema frozen and the skill v1/stable in a separately approved change.

- [ ] **Step 6: Test one second model or harness for portability**

Do this only after the reference gate passes. Treat portability findings as adapter/protocol work unless they expose a critical core-contract defect.

- [ ] **Step 7: Re-evaluate external lint tooling at the defined trigger**

At schema freeze, assess `agentslint`, `agnix`, and current alternatives for batch link/frontmatter/schema checks. Do not delegate semantic project-fit judgments to them.
