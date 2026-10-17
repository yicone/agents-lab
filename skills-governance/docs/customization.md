# 非本地 Skill 定制（ACM-131）

本页说明如何对一个不属于自己的 Skill 做本地定制：五种模式怎么选、记录写在哪里、升级时怎么重放。原则是基线与 delta 分离：上游基线永不入库（它在上游或 `skill_guard` 快照里），本仓库只保存经过审查的本地 delta，作为数据存放在 `customizations/<skill>/`。

## 五种模式对比

| 模式 | 是什么 | 基线在哪 | delta 在哪 | 升级成本 | 库存字段 | 必要证据 |
| --- | --- | --- | --- | --- | --- | --- |
| `policy_exclusion` | 不加载/排除某 Skill 的策略决定，不改内容 | 上游，不动 | 无内容 delta，仅记录 `policy` | 零；上游更新无感 | `local_customization` 可为 `none`；`customization_mode` `policy_exclusion`；`compatibility_constraints` 需有 `kind: policy` 项 | `customization.json` 记录理由 |
| `wrapper` | 新建一个本地 Skill 调用或先于它运行，不碰上游文件 | 上游 + 本地包装目录 | 仓库内 `path` 指向的含 SKILL.md 目录 | 低；上游更新通常不影响包装 | `local_customization` `local_delta`；`source_class` 可用 `third_party_customized` | 记录 + 包装 Skill 入库审查 |
| `overlay` | 对上游树做少量逐文件增/改/删，仍跟踪上游 | 上游（快照或锁坐标） | `customizations/<skill>/delta/` 载荷 + `ops` 清单 | 中；每次升级跑 `apply` 检查冲突 | `local_customization` `local_delta`；记录 `baseline_ref` | `ops` 每条含 sha256，replace/delete 含 `base_sha256` |
| `fork` | 分叉后广泛偏离，自己维护，不再跟踪上游 | 分叉点记录于 `baseline_ref` | 仓库内 `path` 指向的含 SKILL.md 目录 | 高；等同维护自建 Skill | `local_customization` `local_delta`；`source_class` `third_party_customized` | 记录 + 分叉树按本地 Skill 治理 |
| `local_replacement` | 上游失效/不安全/不合用，整体重写替代 | 无上游跟踪（`baseline.kind` 可为 `none`） | 仓库内 `path` 指向的含 SKILL.md 目录 | 与本地 Skill 相同 | 同上 | 记录 + 替代 Skill 入库审查 |

## 模式选择顺序

按顺序回答，取第一个成立的：

1. 默认保持外部管理（`customization_mode` `none`），不动它？
2. 需求能否用“不加载/排除”满足？→ `policy_exclusion`
3. 需求能否用一个新本地 Skill 调用它或先于它运行来满足，完全不改上游文件？→ `wrapper`
4. 只需少量逐文件修改，且希望继续跟踪上游更新？→ `overlay`
5. 需要大范围偏离、愿意自己维护，且上游不再跟踪或难以重放？→ `fork`
6. 上游已失效、不安全或根本不合用？→ `local_replacement`

调整既有模式的触发条件：

- overlay 在连续 2 次上游更新中 `apply` 都出现冲突 → 升级为 `fork`；
- 上游吸收了本地 delta（新版已含等效改动）→ 降回 `none`，删除定制记录；
- 包装逻辑上游已原生支持 → `wrapper` 降回 `none`；
- 上游停止维护或来源不再可信 → `fork`/`overlay` 升级为 `local_replacement`。

禁止在没有记录的情况下原地修改第三方安装目录。任何落地到 runtime 生效路径的非本地 Skill 内容，都须先有对应记录。

## 基线与 delta 分开审查

- 审查 delta = 审查 `customizations/<skill>/` 下的记录和载荷文件；
- 审查上游 = `skill_guard` 对快照与暂存树的 diff；
- 两者分开审。`apply` 产出的已套用树只是一个暂存候选（staging candidate），不因为它的 delta 已审过就跳过上游 diff/gate。

## 如何记录

1. 更新 `config/skills-inventory.json` 中该 Skill：`customization_mode`、适用时 `source_class` 改为 `third_party_customized`、`local_customization` 改为 `local_delta`、确认 `baseline_ref` 为 delta 审查时对应的基线；
2. 写 `customizations/<skill>/customization.json`（格式 `skills-customization/v1`，见 `config/skills-customization.schema.json`），overlay 同时把载荷放进 `delta/`；
3. 运行：

       python3 skills-governance/scripts/customization_check.py check
       python3 skills-governance/scripts/validate_skills_inventory.py --check-paths

## 升级流程

定制 Skill 升级时在标准流程（见 [upgrade-review.md](upgrade-review.md)）的 `stage` 之前多一步 `apply`：

    # 1. 对旧的上游树做快照（同标准流程）
    python3 skills-governance/scripts/skill_guard.py snapshot \
      --skill-root <live-skill-root> --source-class third_party_customized \
      --output <snapshot-dir>

    # 2. 获取上游候选树（Skills CLI / 下载解包到独立目录）

    # 3. 把本地 delta 套到上游候选上
    python3 skills-governance/scripts/customization_check.py apply \
      --skill <name> --upstream <upstream-candidate-dir> \
      --output <applied-output-dir>

    # 4. 套用结果作为候选进入标准审查
    python3 skills-governance/scripts/skill_guard.py stage \
      --skill-root <applied-output-dir> --live-root <live-skill-root> \
      --snapshot <snapshot-dir> --output <staging-dir>
    python3 skills-governance/scripts/skill_guard.py diff \
      --snapshot <snapshot-dir> --staging <staging-dir> --gate
    python3 skills-governance/scripts/skill_guard.py activate --staging <staging-dir>

`apply` 退出码：

- `0`：无冲突，已生成 `<output>/<name>/`；
- `2`：上游已漂移，`base_sha256` 不再匹配或文件缺失/已存在 → 输出 `conflicts` 列表，什么都不写。这是“需要三方合并/重新审查”的信号：逐条审查冲突，调整 delta 后更新 `baseline_ref` 与 `reviewed_at`；
- `1`：非 overlay 模式、记录无效或输出目录已存在（可用 `--force` 覆盖）。

ACM-156 将在 `apply` 之上提供自动重放与三方合并（base=基线时的上游文件、ours=delta 载荷、theirs=新上游），并在成功重放后自动更新 `baseline_ref` 与 `reviewed_at`。当前版本只做冲突检测，不合并。

## overlay 记录示例

`customizations/example-skill/customization.json`：

```json
{
  "schema": "skills-customization/v1",
  "skill": "example-skill",
  "mode": "overlay",
  "reason": "关闭上游默认开启的遥测示例",
  "baseline_ref": "github:owner/example-skill",
  "reviewed_at": "2026-10-20T09:30:00Z",
  "retire_when": "上游提供关闭遥测的配置项",
  "ops": [
    {
      "op": "replace",
      "path": "SKILL.md",
      "source": "SKILL.md",
      "sha256": "<64 hex of delta/SKILL.md>",
      "base_sha256": "<64 hex of upstream SKILL.md at baseline_ref>"
    },
    {
      "op": "add",
      "path": "references/local-notes.md",
      "source": "references/local-notes.md",
      "sha256": "<64 hex>"
    },
    {
      "op": "delete",
      "path": "scripts/telemetry.sh",
      "base_sha256": "<64 hex>"
    }
  ]
}
```
