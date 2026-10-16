# Skills Inventory Changelog

Notes that do not belong in a new inventory field.

## 2026-10-16

- Every skill record now stores `customization_mode` and `compatibility_constraints` (ACM-134). Current rows are `none` and `[]`: this machine has no recorded content delta. `policy_exclusion` is allowed when `local_customization` is `none`. A content delta must name `wrapper`, `overlay`, `fork`, `local_replacement`, or `unknown`. Constraint kinds are `upstream_version`, `runtime`, `adapter`, `policy`, and `unknown`.
- The mode-selection workflow stays outside this schema.
- Runtime bundles and enabled plugins that ship skills now have provider/version coordinates. A vendor bundle stays one summary record. `baseline_ref` is `runtime:<provider>` or `plugin:<host>/<id>`, `baseline.subject` is the version file, and `current_version` is the version read from that file. `review_time` stays null. The host is part of the plugin coordinate because Codex and Claude can enable the same plugin id at different versions.
- `cursor-builtin-skills` is `runtime:cursor` at Cursor `CFBundleShortVersionString`. `codex-builtin-skills` is `runtime:codex` from `~/.codex/packages/standalone/current/codex-package.json`. `hermes-builtin-skills` is `runtime:hermes` from `install-stamp.json` as `baseVersion+commit[:10]`, not `displayVersion`.
- Enabled Codex plugins are the `enabled = true` tables in `~/.codex/config.toml`. Cursor plugins are the installed cache under `~/.cursor/plugins/cache`, not marketplace clones. Claude plugins are `~/.claude/plugins/installed_plugins.json`. Each plugin with at least one `SKILL.md` is one `runtime_plugin` summary. Plugins with no skills are scanned and not given a row: Codex `chrome`, `computer-use`, `browser`, `codex-app-tools`, `unified-computer-use`, `code-review`; Cursor `github`; Hermes `homeassistant` (no `SKILL.md`).
- Plugin rows do not replace Skills CLI rows. `understand-*` and Nowledge Mem skills installed under `~/.agents/skills` stay `skills_cli_managed`.

- Skills CLI rows in the inventory now follow the lock at `~/.agents/.skill-lock.json`. `baseline_ref` is `github:<source>`, `baseline.subject` is `skillPath`, and `current_version` is `skillFolderHash`. The lock file was not modified. `review_time` stays null until a content review. The drift report treats a `SKILL.md` subject that differs from `skillPath` as unverified.
- `agent-browser` has an empty lock `skillFolderHash`, so `current_version` stays null. Its upstream coordinate is `github:vercel-labs/agent-browser` and its path is `skills/agent-browser/SKILL.md`.
- `writing-plans` lock hash moved from `be89d032af0d3621f689e8bd6083e4010971e5fa` to `cc97fa00eb2ccd4f283e34eb2ba06a48e1e679f7` (`updatedAt` 2026-10-15T08:15:44.460Z). The inventory now records the lock hash. The other 35 CLI hashes were already equal to the lock and were kept.
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
