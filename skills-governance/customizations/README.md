# Customizations

本目录存放非本地 Skill 的定制记录。每个定制一个目录：

- `<skill-name>/customization.json`：定制记录，格式 `skills-customization/v1`
- `<skill-name>/delta/`：`ops` 式 overlay 的载荷文件（上游基线不入库，仅存本地 delta）
- `<skill-name>/local.patch`：`patch` 式 overlay 的载荷，仅由 `skill_patch.py create` 生成，勿手改

校验：`python3 skills-governance/scripts/customization_check.py check`

工作流与模式选择见 [../docs/customization.md](../docs/customization.md)。
