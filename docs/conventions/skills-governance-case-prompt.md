# Skills Governance Case Prompt

Copy this prompt into another agent session when you want to use a real historical case to validate or improve the skills-governance workflow.

## Prompt Template

```text
Use the existing skills-governance workflow to review this real case.

Goal:
- classify what kind of governance case this is
- decide which governance skill should handle it
- produce a structured result
- note whether the current governance skill or principle docs need improvement

Relevant governance references:
- OS-RES/Skills 管理与治理原则
- OS-LOG/Skills 当前状态清单
- skills-cli-reconcile
- skills-intake-local
- skills-promote-global

Case evidence:
- This is a real case from another session, not a hypothetical example.
- The attached screenshot / transcript / note should be treated as valid case material if it shows:
  - a concrete object
  - a clear trigger
  - an actual action
  - a verifiable result
  - a reusable lesson

Requested output:
1. Case classification
2. Which governance skill should own it
3. Structured result in that skill's output format
4. Any governance-document or skill-boundary changes suggested by the case

If the case affects multiple skills at once:
- first produce a case-level classification
- explicitly name the shared trigger
- then provide multiple per-skill records

Case content:
[Paste screenshot text / transcript / summary here]
```

## Example Use

The prompt works well for cases like:

- a third-party skill being reinstalled through `npx skills`
- a local skill being adopted into the canonical repo
- a directory-level normalization event affecting multiple local skills at once
- a local skill being considered for global promotion
- a workflow repair where a skill's output did not match Logseq writing expectations
