# Worktree Branch Governance YR Regression Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct the audit report schema and validator so a YR-style runtime-coupled monorepo does not produce invented governance differences or unsupported deployment claims.

**Architecture:** Keep the report as human-readable Markdown and extend the existing dependency-free Python validator only for deterministic shape checks. Separate task execution constraints from project governance, split fork/runtime/deployment extensions, permit an explicit no-findings result, and use the YR replay as the behavioral RED baseline. Semantic evidence quality remains governed by the skill and regression cases rather than inferred by the parser.

**Tech Stack:** Markdown skill documentation, Python 3 standard library, `unittest`, Git.

**Execution note:** Work directly in `/Users/tr/Workspace/agents-lab`; the user has explicitly said this repository does not need a worktree. Preserve unrelated changes and do not run the behavioral replay against `/Users/tr/Workspace/yr` until the implementation is complete.

---

## File Map

- Modify `skills/worktree-branch-governance-audit/tests/test_validate_report.py`: enumerate the expanded valid and invalid fixture matrix and assert intended diagnostic codes.
- Modify existing files under `skills/worktree-branch-governance-audit/tests/fixtures/`: migrate all fixtures to the new required section and finding shape without masking their original purpose.
- Create focused fixtures under the same directory for no findings, fork-only, runtime-only, missing task constraints, incomplete fork extension, removed deployment confidence, incomplete enforcement gap, and compound sources.
- Modify `skills/worktree-branch-governance-audit/scripts/validate_report.py`: implement the revised section, extension, source, and finding contracts.
- Modify `skills/worktree-branch-governance-audit/SKILL.md`: place the new semantic boundaries in the first-hop instructions.
- Modify `skills/worktree-branch-governance-audit/references/audit-model.md`: document the complete report model and examples.
- Modify `skills/worktree-branch-governance-audit/regression-tests.md`: add YR Case 7 and its observed RED baseline.
- Modify `skills/worktree-branch-governance-audit/references/tooling-evaluation.md`: record what the local validator can now enforce and why `agentslint`/`agnix` are still deferred.

### Task 1: Add the revised contract as failing fixtures

**Files:**
- Modify: `skills/worktree-branch-governance-audit/tests/test_validate_report.py`
- Modify: `skills/worktree-branch-governance-audit/tests/fixtures/valid-core.md`
- Modify: `skills/worktree-branch-governance-audit/tests/fixtures/valid-deployment.md`
- Modify: `skills/worktree-branch-governance-audit/tests/fixtures/invalid-legacy-main-role.md`
- Modify: `skills/worktree-branch-governance-audit/tests/fixtures/invalid-literal-branch-role.md`
- Modify: `skills/worktree-branch-governance-audit/tests/fixtures/invalid-wildcard-source.md`
- Modify: `skills/worktree-branch-governance-audit/tests/fixtures/invalid-unknown-high-confidence.md`
- Modify: `skills/worktree-branch-governance-audit/tests/fixtures/invalid-finding-destination.md`
- Create: `skills/worktree-branch-governance-audit/tests/fixtures/valid-no-findings.md`
- Create: `skills/worktree-branch-governance-audit/tests/fixtures/valid-fork-only.md`
- Create: `skills/worktree-branch-governance-audit/tests/fixtures/valid-runtime-only.md`
- Create: `skills/worktree-branch-governance-audit/tests/fixtures/invalid-missing-task-constraints.md`
- Create: `skills/worktree-branch-governance-audit/tests/fixtures/invalid-incomplete-fork-extension.md`
- Create: `skills/worktree-branch-governance-audit/tests/fixtures/invalid-deployment-binding-confidence.md`
- Create: `skills/worktree-branch-governance-audit/tests/fixtures/invalid-enforcement-gap-fields.md`
- Create: `skills/worktree-branch-governance-audit/tests/fixtures/invalid-compound-source.md`

- [ ] **Step 1: Migrate all otherwise-valid fixture shapes**

Add this required section after `Explicit Project Constraints` in every fixture:

```markdown
## Task Execution Constraints

- Read-only audit; do not create a worktree or contact remotes.
```

Add a non-empty `difference:` field to every existing finding so each old invalid fixture continues to isolate its original diagnostic.

- [ ] **Step 2: Add valid no-findings, fork-only, and runtime-only fixtures**

Use exactly this no-findings body:

```markdown
## Difference Findings

none discovered
```

The fork-only profile contains both `upstream_base_sync` and `local_patch_flow`, but no `runtime_binding`. The runtime-only profile contains `runtime_binding` but neither fork field. The deployment fixture contains only `production_branch`, `production_trigger`, and `preview_behavior`, each followed by its own `confidence`.

- [ ] **Step 3: Add focused invalid fixtures**

Use these defect shapes:

```markdown
# missing required section
# omit: ## Task Execution Constraints

# incomplete fork extension
upstream_base_sync: manual
confidence: high

# removed nested confidence field
deployment_binding_confidence: low
confidence: high

# incomplete enforcement gap
type: enforcement_gap
difference: Direct primary writes are not mechanically rejected.
# omit missing_effective_control and proportionality

# compound source row
| `AGENTS.md` and `apps/api/AGENTS.md` | repository instructions | repository | normative | current | read |
```

- [ ] **Step 4: Expand the test matrix**

Update `test_valid_fixtures` to include:

```python
for name in (
    "valid-core.md",
    "valid-deployment.md",
    "valid-no-findings.md",
    "valid-fork-only.md",
    "valid-runtime-only.md",
):
```

Add expected diagnostics:

```python
"invalid-missing-task-constraints.md": "section.missing",
"invalid-incomplete-fork-extension.md": "profile.incomplete-extension",
"invalid-deployment-binding-confidence.md": "profile.unknown-key",
"invalid-enforcement-gap-fields.md": "finding.missing-field",
"invalid-compound-source.md": "source.compound",
```

- [ ] **Step 5: Run the tests and confirm RED**

Run:

```bash
python3 -m unittest discover -s skills/worktree-branch-governance-audit/tests -v
```

Expected: failures showing that the current validator rejects no-findings and partial fork/runtime profiles, accepts the removed deployment field, does not require task constraints or enforcement-gap fields, and does not detect compound sources.

- [ ] **Step 6: Commit the RED fixtures**

```bash
git add skills/worktree-branch-governance-audit/tests
git commit -m "test: capture YR governance audit schema failures"
```

### Task 2: Implement the revised validator contract

**Files:**
- Modify: `skills/worktree-branch-governance-audit/scripts/validate_report.py`
- Test: `skills/worktree-branch-governance-audit/tests/test_validate_report.py`

- [ ] **Step 1: Add the required task section and split extension constants**

Insert `Task Execution Constraints` after `Explicit Project Constraints` in `REQUIRED_SECTIONS`. Replace `FORK_RUNTIME_FIELDS` with:

```python
FORK_FIELDS = {
    "upstream_base_sync": {"mirror", "periodic-sync", "manual", "unknown"},
    "local_patch_flow": {"none", "upstream-contribution", "persistent-local", "mixed", "unknown"},
}

RUNTIME_FIELDS = {
    "runtime_binding": {"upstream-install", "fork-primary", "patch-branch", "worktree", "unknown"},
}
```

Remove `deployment_binding_confidence` from `DEPLOYMENT_FIELDS` and build `PROFILE_FIELDS` from all four groups.

- [ ] **Step 2: Validate independent extension completeness**

Use:

```python
extension_groups = (
    ("fork", FORK_FIELDS),
    ("runtime", RUNTIME_FIELDS),
    ("deployment", DEPLOYMENT_FIELDS),
)
```

Only groups with two or more fields need an all-or-nothing check; `runtime_binding` is independently optional. Update enum lookup to search the split mappings.

- [ ] **Step 3: Add a narrow compound-source check**

Create a helper that extracts path-like inline-code spans from the source cell. Return `source.compound` when two or more such spans are joined in one source cell. Keep wildcard handling unchanged and avoid rejecting one exact command or one exact memory identifier.

Suggested shape:

```python
def _has_compound_inline_sources(value: str) -> bool:
    spans = re.findall(r"`([^`]+)`", value)
    path_like = [span for span in spans if "/" in span or span.endswith((".md", ".toml", ".yaml", ".yml"))]
    return len(path_like) > 1
```

- [ ] **Step 4: Permit an explicit no-findings result**

Before requiring finding blocks, inspect non-empty prose in the section. Accept only a normalized exact value in `{"none", "none discovered"}` when no blocks exist. Continue returning `finding.missing` for an empty section or unrelated prose.

- [ ] **Step 5: Require explicit difference and enforcement-gap support**

Change the common finding fields to:

```python
required = {
    "type",
    "difference",
    "evidence",
    "confidence",
    "recommended_action",
    "proposed_destination",
}
```

When `type == "enforcement_gap"`, additionally require:

```python
{"missing_effective_control", "proportionality"}
```

Emit the existing `finding.missing-field` diagnostic for missing conditional fields.

- [ ] **Step 6: Run the unit tests and reach GREEN**

Run:

```bash
python3 -m unittest discover -s skills/worktree-branch-governance-audit/tests -v
python3 -m py_compile skills/worktree-branch-governance-audit/scripts/validate_report.py
```

Expected: all tests pass and compilation exits `0`.

- [ ] **Step 7: Commit the validator implementation**

```bash
git add skills/worktree-branch-governance-audit/scripts/validate_report.py
git commit -m "feat: tighten governance audit report validation"
```

### Task 3: Update the skill and full audit model

**Files:**
- Modify: `skills/worktree-branch-governance-audit/SKILL.md`
- Modify: `skills/worktree-branch-governance-audit/references/audit-model.md`

- [ ] **Step 1: Separate constraint layers in the first-hop skill**

Add `Task Execution Constraints` to Required Report Sections. State explicitly that read-only, no-fetch, no-worktree, and temporary-output instructions for one audit are task constraints, not evidence that a repository worktree rule is overbroad.

- [ ] **Step 2: Prevent fabricated findings**

State in both files:

```text
Difference Findings contains only an evidenced incompatible, missing, drifting, misplaced, duplicated, or unverifiable relationship. Positive alignment and correct audit behavior are observations. If no difference is established, write `none discovered`.
```

Require the `difference` field and document the two conditional enforcement-gap fields.

- [ ] **Step 3: Document independent conditional extensions**

Replace every fork/runtime bundled instruction with separate fork and runtime triggers. Remove `deployment_binding_confidence` and explain that each deployment field's adjacent confidence is sufficient.

- [ ] **Step 4: Tighten deployment-role semantics**

Document that “keep this branch deployable” supports `release-ready`. Require actual production binding evidence for `production`; otherwise use `unknown` deployment values at low or medium confidence.

- [ ] **Step 5: Update the report template and source rule**

Add the task section, `difference`, and enforcement-gap conditional fields to the template. State that each Source Inventory row identifies exactly one source rather than one wildcard or compound source expression.

- [ ] **Step 6: Check terminology drift**

Run:

```bash
rg -n "fork/runtime|deployment_binding_confidence|at least one finding|Task Execution Constraints|difference:" skills/worktree-branch-governance-audit/SKILL.md skills/worktree-branch-governance-audit/references/audit-model.md
```

Expected: no active bundled-extension or nested-confidence instruction remains; new terms appear in both files.

- [ ] **Step 7: Commit the documentation contract**

```bash
git add skills/worktree-branch-governance-audit/SKILL.md skills/worktree-branch-governance-audit/references/audit-model.md
git commit -m "docs: separate governance audit evidence layers"
```

### Task 4: Add the YR behavioral regression and tooling disposition

**Files:**
- Modify: `skills/worktree-branch-governance-audit/regression-tests.md`
- Modify: `skills/worktree-branch-governance-audit/references/tooling-evaluation.md`

- [ ] **Step 1: Add Case 7 with the real RED baseline**

Describe YR as a solo-maintained commercial monorepo with repository-scoped worktree rules, active sibling worktrees, worktree-bound backend runtimes, a deployable `main`, and no deployment-system inspection during the audit. Record the ten failures listed in the approved design.

- [ ] **Step 2: Define Case 7 expected behavior**

Require task/project constraint separation, `primary_branch_role: release-ready` absent stronger evidence, runtime-only extension use, unknown deployment values when uninspected, and `none discovered` when no actual source difference exists.

- [ ] **Step 3: Update scoring gates**

For Case 7, cap the score at `3` when the report invents a difference, infers production binding, or couples runtime evidence to fork fields. Keep the no-mutation requirement.

- [ ] **Step 4: Update tooling evaluation**

Record that the local validator now checks independent extension groups, explicit no-findings, finding differences, enforcement-gap completeness, and narrow compound-source rows. Reaffirm that it cannot judge evidence truth and that `agentslint`/`agnix` remain deferred for generated reports.

- [ ] **Step 5: Commit the regression documentation**

```bash
git add skills/worktree-branch-governance-audit/regression-tests.md skills/worktree-branch-governance-audit/references/tooling-evaluation.md
git commit -m "test: add YR governance audit regression case"
```

### Task 5: Run full validation and verify global exposure

**Files:**
- Verify: `skills/worktree-branch-governance-audit/**`
- Verify: `/Users/tr/.agents/skills/worktree-branch-governance-audit`

- [ ] **Step 1: Run the complete test suite**

```bash
python3 -m unittest discover -s skills/worktree-branch-governance-audit/tests -v
python3 -m py_compile skills/worktree-branch-governance-audit/scripts/validate_report.py
```

Expected: all tests pass and compilation exits `0`.

- [ ] **Step 2: Exercise CLI exit behavior**

```bash
python3 skills/worktree-branch-governance-audit/scripts/validate_report.py skills/worktree-branch-governance-audit/tests/fixtures/valid-no-findings.md
python3 skills/worktree-branch-governance-audit/scripts/validate_report.py skills/worktree-branch-governance-audit/tests/fixtures/valid-runtime-only.md
python3 skills/worktree-branch-governance-audit/scripts/validate_report.py skills/worktree-branch-governance-audit/tests/fixtures/invalid-compound-source.md
```

Expected: the first two return `0`; the invalid fixture returns `1` with `source.compound`.

- [ ] **Step 3: Check repository consistency**

```bash
git diff --check
git status --short
realpath /Users/tr/.agents/skills/worktree-branch-governance-audit
shasum -a 256 skills/worktree-branch-governance-audit/SKILL.md /Users/tr/.agents/skills/worktree-branch-governance-audit/SKILL.md
```

Expected: no whitespace errors; the global path resolves to the canonical skill directory; hashes match. Only planned changes, if any remain uncommitted, appear in status.

- [ ] **Step 4: Inspect the implementation commits**

```bash
git log --oneline -6
git show --check --stat HEAD
```

Expected: focused RED, validator, documentation, and regression commits with no unrelated files.

- [ ] **Step 5: Run the behavioral GREEN separately**

After implementation is complete, open a fresh Codex task rooted at `/Users/tr/Workspace/yr`, invoke `worktree-branch-governance-audit`, require a read-only report and validator result, and do not reveal the expected findings. Review that task from this coordinating conversation. Do not mutate YR.

