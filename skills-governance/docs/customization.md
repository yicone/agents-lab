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

高危且 `review_time` 为空的 Skill 不新增状态名：要么把库存 `status` 设为 `quarantined`，要么选择 `policy_exclusion`。两种状态都不能被提升为全局可用。判定和 `--promote` 检查见 [capability-declaration.md](capability-declaration.md)。

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

`apply` 退出码（仅适用于 `ops` 式记录；`patch` 式记录用 `skill_patch.py rebase`，见下节）：

- `0`：无冲突，已生成 `<output>/<name>/`；
- `2`：上游已漂移，`base_sha256` 不再匹配或文件缺失/已存在 → 输出 `conflicts` 列表，什么都不写。这是“需要重新审查 delta”的信号：逐条审查冲突，调整 delta 后更新 `baseline_ref` 与 `reviewed_at`；
- `1`：非 overlay 模式、`patch` 式记录（报 `patch-style record: use skill_patch.py rebase`）、记录无效或输出目录已存在（可用 `--force` 覆盖）。

## local.patch 规范与补丁重放（ACM-156）

overlay 有两种 delta 形态，记录里 `ops` 与 `patch` 二选一：

- `ops`：少量整文件增/改/删，载荷在 `delta/`。上游变化只检测冲突，不合并；
- `patch`：对上游文件的行级修改，delta 是整个树的 unified diff，存在 `customizations/<skill>/local.patch`。升级时对它做真正的三方合并，能自动合并的就不惊动人。改动是行级、且应与上游演进自动合并时，选 `patch`；改动接近整文件替换时，选 `ops` 更直观。

### local.patch 格式

`local.patch` 是对“基线上游树 → 本地实现树”跑 `git diff --no-index --binary --full-index` 得到的补丁，路径带 `a/` `b/` 前缀，支持二进制。它只能由 `skill_patch.py create` 生成，绝不手工编辑——记录的 `patch.sha256` 与文件内容绑定，手改即校验失败。

记录在 `patch` 字段：

```json
"patch": {
  "sha256": "<64 hex of customizations/<skill>/local.patch>",
  "files": {
    "SKILL.md": "<64 hex of upstream SKILL.md at baseline_ref>",
    "notes.md": null,
    "old.txt": "<64 hex>"
  }
}
```

`files` 列出补丁触及的每个文件：修改或删除 → 基线时上游文件的 sha256；补丁新增 → `null`。校验仍走 `customization_check.py check`（`patch.sha256` 必须等于 local.patch 的实际哈希，`files` 键必须是安全相对路径、非空）。

### 生成与重放

    # 首次登记：基线上游树 + 本地实现树 → local.patch + 记录
    python3 skills-governance/scripts/skill_patch.py create \
      --skill <name> --baseline <baseline-dir> --local <local-dir> \
      --baseline-ref <ref> --reason "<why>"

    # 升级：把 local.patch 重放到新上游树上（真正的三方合并）
    python3 skills-governance/scripts/skill_patch.py rebase \
      --skill <name> --old-baseline <baseline-dir> \
      --upstream <new-upstream-dir> --output <output-dir>

`create` 对已存在的记录只更新 `patch`（`--baseline-ref` / `--reviewed-at` 给才更新）；记录含 `ops` 时报错，要求人工转换。两棵树都必须含 SKILL.md，且不允许符号链接或非普通文件。

`rebase` 对每个清单路径做三方合并（base=旧基线、ours=套完补丁的树、theirs=新上游）：上游没动 → 取本地（`replayed`）；上游改成了和本地一样 → 取上游（`absorbed`）；两边都改但能干净合并 → `merged`；合并不净 → 带 diff3 标记的 `content` 冲突；一边改一边删 → `modify_delete`；含 NUL 的二进制 → `binary`。

退出码：

- `0`：无冲突，写入 `<output>/<name>/`，可直接进 `stage`；
- `2`：有冲突，写 `<output>/<name>.conflicts/`（完整树，冲突文件含标记），且不写 `<output>/<name>/`；
- `1`：记录缺失/无效、local.patch 哈希不符、旧基线与清单不符、补丁打不上、输出已存在（`--force` 覆盖）等。

### 冲突标记与激活阻断

`content` 冲突在冲突树里长这样：

```
<<<<<<< local
本地版本
||||||| base
基线版本
=======
上游版本
>>>>>>> upstream
```

阻断是双重的：`rebase` 冲突时不产出 `<output>/<name>/`，`stage` 没有干净候选可暂存；即便有人把含标记的树直接塞进 `stage`，`skill_guard` 的 `conflict_markers` 门禁信号也会让 `diff --gate` 和未 `--force` 的 `activate` 以退出码 2 阻断——本地定制不会被含冲突标记的内容静默覆盖。

### 冲突解决流程

1. 复制 `<output>/<name>.conflicts/` 为工作树，手工解掉每个 `<<<<<<<`/`>>>>>>>` 标记；
2. 刷新补丁与记录：

       python3 skills-governance/scripts/skill_patch.py create \
         --skill <name> --baseline <new-upstream-dir> --local <resolved-tree> \
         --baseline-ref <new-ref> --reviewed-at <ts>

3. 按既有流程更新库存 `baseline_ref`，再跑 `customization_check.py check` 与 snapshot → `stage` → `diff --gate` → `activate`。

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
