# Skills Inventory Changelog

Notes that do not belong in a new inventory field.

## 2026-10-16

- Drift reports are produced by `skills-governance/scripts/drift_report.py`. The command reads `review_time`, `baseline_ref`, and `current_version` and does not modify skills or lock files.
- `skills-governance-audit` baseline hash moved with the audit skill text that runs that report.

- `pr-review-fix` is `local_custom`. It is self-authored and still installed at `~/.agents/skills/pr-review-fix`. It is not in this repository, so `canonical_path` is null.
- `wechatide-skill` stays `third_party_unmanaged`. It may come from the WeChat DevTools app. That origin is not verified.
- `external-qa-triage` and `writing-child-presentations` stay `third_party_unmanaged`.
- `reading-logseq-data` and `writing-to-logseq` are self-authored and are no longer on disk. No inventory rows were added. Their lock entries were removed. The lock had recorded both under `c0ntr0lledcha0s/claude-code-plugin-automations`.
- `find-skills` was removed from `~/.agents/.skill-lock.json`. It is third-party and its install source is forgotten. It is not under `~/.agents/skills`. A separate copy remains at `~/.gemini/antigravity/global_skills/find-skills` and is not an inventory row.
- The lock file was copied to `~/.agents/.skill-lock.json.bak-2026-10-16` before those three entries were deleted.
- `ego-browser` `current_version` is `2.0.0`, copied from the skill's own `metadata.version`. The previous value `0.5.1.13` was the Ego Lite app version.
- `~/.agents/skills/skills-governance-audit` already points at `~/Workspace/agents-lab/skills-governance/skills/skills-governance-audit` on the merged tree. That directory matches the inventory baseline, so the install was not retargeted and no other skill install was changed.
- Removed the dangling symlink `~/.gemini/antigravity/global_skills/find-skills`. It pointed at `~/.agents/skills/find-skills`, which is already gone, and it is not an inventory row.
