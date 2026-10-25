# Capability Contract Example

## Goal

Show a read-only check that a high-risk skill is isolated before anyone promotes it, using the existing quarantine and policy-exclusion states.

## Case

- skill declares `capabilities.network: true`, `capabilities.secrets: true`, and `capabilities.filesystem: write`
- `review_time` is null
- `status` is `active`

## Command

```bash
python3 skills-governance/scripts/capability_contract.py --format text --check
python3 skills-governance/scripts/capability_contract.py --promote <skill> --check
```

Use the pinned fork binary by default; an absolute `--skil-lock` override must pass the same version/commit check. The check does not invent a second scanner when the map is missing.

## Expected Findings

- `isolation: required` while the skill is active and unreviewed
- after `status` is `quarantined`, or `customization_mode` is `policy_exclusion`, isolation is `ok`
- `--promote` still exits 2 for both isolation states
- a measured `network_urls` hit while `capabilities.network` is false is `capability_overreach` on `skill_guard --gate`

## Expected Output Shape

```text
Capability contract
Observation: unmeasured
Gate: fail
- skill: demo
  isolation: required
  promotion: blocked
```
