# Skills 升级审查与回滚

本页说明如何在升级运行时内置 Skills、插件引入的 Skills，或通过 Skills CLI 安装的第三方 Skills 前后，保留可审查、可回滚的证据。

## 目标与边界

`skill_guard.py` 是本地薄控制层，不负责安装或升级 Skill。它负责：

- 在变更前保存指定 Skill 根目录及适配器目录的快照；
- 把候选新版本复制到暂存目录，且不写入 runtime 正在加载的生效路径；
- 对比快照与暂存树的文件、权限、哈希、符号链接和关键风险信号；
- 在发现新增脚本、网络行为、秘密访问、触发条件变化或适配器路径变化时提供保守门禁；
- 仅在审查通过后把暂存内容激活到生效目录；
- 在确认快照可用后恢复被捕获的目录。

真正执行下载或解包的仍是对应的来源管理器，例如 Skills CLI、运行时升级器或插件管理器。它们应把新版本落到暂存目录，而不是先覆盖 runtime 可见路径。对于具有结构化能力分析和锁文件的环境，优先叠加 SkilLock 等工具；`skill_guard` 负责跨来源的快照、来源元数据和本地适配器保护。

## 标准流程

升级必须先进入暂存目录，完成 snapshot 与 diff/gate 审查后才激活。禁止把新版先写进 runtime 正在加载的目录。

### 1. 升级前创建快照

在执行任何升级前，先确定本次变更的来源、真实安装目录和运行时适配器目录。以 Skills CLI 管理的第三方 Skill 为例：

    python3 skills-governance/scripts/skill_guard.py snapshot \
      --skill-root ~/.agents/skills \
      --adapter-root .agents/skills \
      --source-class skills_cli_managed \
      --source-authority upstream_repository \
      --metadata '{"package":"owner/repo","ref":"main"}' \
      --output ~/.local/share/agents-lab/skill-snapshots/2026-10-01-before-update

参数说明：

- `--skill-root` 可以重复使用，按顺序记录多个真实 Skill 根目录；
- `--adapter-root` 可以重复使用，记录项目或目标运行时使用的适配器目录；
- `--source-class` 建议使用 `local_custom`、`runtime_builtin`、`runtime_plugin`、`skills_cli_managed`、`third_party_unmanaged` 或 `third_party_customized`；
- `--source-authority` 记录实际来源，例如仓库、运行时发行版、插件或安装器；
- `--metadata` 记录包名、仓库、版本或 ref 等可追溯信息，不要写入秘密；
- `--output` 是快照目录。目录已存在时，必须显式加 `--force` 才会覆盖。

运行时内置或插件引入的 Skill 使用同一流程，只需替换来源元数据。例如：

    python3 skills-governance/scripts/skill_guard.py snapshot \
      --skill-root ~/.codex/skills \
      --source-class runtime_plugin \
      --source-authority enabled_plugin \
      --metadata '{"plugin":"example-plugin","version":"1.2.3"}' \
      --output ~/.local/share/agents-lab/skill-snapshots/2026-10-01-before-plugin-update

快照保留普通文件、目录、权限、符号链接目标、文件大小和 SHA-256。它不是运行时沙箱，也不是对 Skill 语义安全性的完整证明。

### 2. 把候选新版本放入暂存目录

来源管理器应把下载或解包结果写到暂存位置，而不是生效目录。若已经有一份未激活的候选树，用 `stage` 复制进去，并记录稍后要替换的 live 路径：

    python3 skills-governance/scripts/skill_guard.py stage \
      --skill-root ~/.local/share/agents-lab/skill-candidates/example \
      --live-root ~/.agents/skills \
      --snapshot ~/.local/share/agents-lab/skill-snapshots/2026-10-01-before-update \
      --output ~/.local/share/agents-lab/skill-staging/2026-10-01-example

规则：

- `--skill-root` / `--adapter-root` 是候选树，必须与 `--live-root` 不是同一路径；
- `stage` 只写入 `--output`，不会改 live 目录，因此 runtime 此时仍加载快照时的旧版；
- `--live-root` 按与候选根目录相同的顺序记录激活目标；
- `--snapshot` 可选，绑定后 `activate` 默认用它做门禁。

不要对生效目录直接运行会原地覆盖的升级命令。若来源管理器只能原地安装，先让它输出到独立目录，再 `stage`。

对已记录定制的非本地 Skill（见 [customization.md](customization.md)），候选树不是裸的上游下载：先用 `python3 skills-governance/scripts/customization_check.py apply --skill <name> --upstream <候选目录> --output <输出>` 把本地 delta 套到上游候选上，再把套用结果作为 `--skill-root` 传给 `stage`。`apply` 退出码为 `2` 表示上游已漂移、需要重新审查 delta，不应继续 `stage`。`patch` 式记录改用 `python3 skills-governance/scripts/skill_patch.py rebase --skill <name> --old-baseline <旧基线> --upstream <候选目录> --output <输出>`，同样以退出码 `2` 表示存在合并冲突，且只产出 `<name>.conflicts/` 树供人工解决，不产生可 stage 的 `<name>/`。

### 3. 对比快照与暂存树并运行门禁

审查暂存内容。此时 live 树应仍是快照中的旧版，runtime 尚未加载候选版本：

    python3 skills-governance/scripts/skill_guard.py diff \
      --snapshot ~/.local/share/agents-lab/skill-snapshots/2026-10-01-before-update \
      --staging ~/.local/share/agents-lab/skill-staging/2026-10-01-example \
      --format json \
      --gate

也可以使用文本格式人工审查：

    python3 skills-governance/scripts/skill_guard.py diff \
      --snapshot ~/.local/share/agents-lab/skill-snapshots/2026-10-01-before-update \
      --staging ~/.local/share/agents-lab/skill-staging/2026-10-01-example \
      --format text

`--staging` 与 `--current-root` 不能同时使用。只有在明确审查一份已经存在的树（例如回滚后复核）时，才把 `--current-root` 指到 live 路径。

退出码含义：

- `0`：未发现当前门禁配置识别的风险信号；
- `2`：门禁阻断，必须先审查结果，不应直接激活或发布；
- `1`：参数、快照或文件系统错误，不代表升级安全。

重点审查 `unexpected_instructions`、`unexpected_files`、`scripts`、`network_behavior`、`secret_access`、`trigger_changes`、`adapter_paths` 和 `conflict_markers`。逐条判定和放行决定见 [upgrade-diff-review.md](upgrade-diff-review.md)。门禁是保守信号，不是自动判定恶意；即使退出码为 `0`，也不能替代对重要 Skill 的人工审查。

### 能力漂移后端

`diff` 和未加 `--force` 的 `activate` 会同时询问 SkilLock。快照与候选的 skill 根目录被投影到临时目录的 `.codex/skills/<name>/`，先对快照执行 `skil-lock init --baseline`，再在候选目录写入 `mode: block` 的 `.skil-lock.yaml` 并执行 `skil-lock ci`。适配器根目录仍由本仓库的路径信号负责，不送进 SkilLock。

报告里的 `capability_gate` 记录实际后端：

- `backend: skil-lock` 且 `blocked: true`：能力面相对快照发生未批准漂移，信号为 `capability_drift`，`--gate` 退出码为 `2`。
- `backend: fallback`：本机没有可执行的 `skil-lock`，或显式传入 `--skil-lock fallback`。`fallback` 字段固定为 `skill_guard risk signals`，`reason` 写明原因。这时仍用上面的风险信号做决定，缺二进制不等于审查通过。
- `error: true`：指定的二进制无法执行，或 `init` / `ci` 失败、超时。失败关闭，信号为 `capability_gate_error`，不激活。

`--skil-lock <path>` 指向一个具体二进制。默认从 `PATH` 查找 `skil-lock`。

如果门禁阻断，先保存 JSON 结果并检查具体文件。确认变化符合预期后，再用 `--force` 激活；如果不符合预期，丢弃暂存目录，live 树无需回滚。若采用 SkilLock，应同时审查其 `skills.lock` 和能力差异结果，将它作为更细粒度的行为门禁。

### 4. 审查通过后再激活

    python3 skills-governance/scripts/skill_guard.py activate \
      --staging ~/.local/share/agents-lab/skill-staging/2026-10-01-example

`activate` 默认对绑定的快照再跑一次门禁：阻断时退出码为 `2`，并且不改 live 目录。只有审查确认后才加 `--force`。没有快照时也必须显式 `--force`，避免未审查就替换生效树。

#### 激活变更日志

每次成功激活后，`activate` 会为发生变化的每个 Skill 追加一条记录到 `skills-governance/changelog/<skill>.jsonl`（文件名中的 Skill 名经过字符清洗，非法字符替换为 `_`）。每条记录是一行 JSON（`schema: skill-governance-changelog/v1`），包含：

- `date` / `skill` / `staging` / `snapshot`：本次激活的时间、Skill 名和来源目录；
- `old_hash` / `new_hash`：激活前后 `SKILL.md` 的 `sha256:` 摘要（缺失时为 `null`）；
- `old_tree_hash` / `new_tree_hash` / `old_version` / `new_version`：激活前后的整树哈希和 `metadata.version`；
- `files`：相对 Skill 目录的 `added` / `removed` / `modified` 文件清单（各最多 50 条）；
- `signals`：本次变化文件的风险信号并集；
- `summary`：变更摘要；`forced`：是否走了 `--force`；`new_ref`：上游 ref 或锁文件哈希。

可用 `--changelog-dir <dir>` 改写到其他目录，用 `--change-summary <text>` 记录人工审查结论（自动摘要会保留在 `auto_summary`），用 `--new-ref <ref>` 记录上游版本。整树哈希未变化的 Skill 不写记录；门禁阻断（退出码 `2`）时不写任何记录。

`drift_report.py` 默认读取同一目录。若某个 Skill 出现 `content_hash_mismatch`、`lock_hash_mismatch` 或 `version_mismatch`，而 changelog 中没有 `new_hash` / `new_ref` / `new_version` 与观测值匹配的条目，报告会增加 `change_unrecorded`（有变更未记录）这一 finding，提示升级发生了但没有留档。可用 `--changelog-dir` 指定其他目录。

### 5. 回滚

只有在确认快照包含正确目录并且希望恢复时，才执行：

    python3 skills-governance/scripts/skill_guard.py rollback \
      --snapshot ~/.local/share/agents-lab/skill-snapshots/2026-10-01-before-update \
      --current-root ~/.agents/skills \
      --force

`rollback` 要求显式提供 `--force`，并恢复快照中捕获的目录和符号链接。它不会替来源管理器修复包索引、锁文件、插件注册状态或运行时元数据；这些状态必须使用对应来源管理器的恢复、重新安装或锁文件流程处理。

## 多目录与适配器注意事项

如果快照时传入多个 `--skill-root`，后续 `stage` / `diff --staging` / `activate` 的根目录数量和顺序必须与快照一致；使用 `--current-root` 或 `--target` 时也一样。适配器目录只在创建快照时作为受保护的活动路径记录；恢复时，应确认适配器仍指向预期的 canonical source，避免把运行时入口误当成源码。

对本地自建 Skill，先保证 canonical Git 仓库是唯一源码，再快照项目级或全局适配器。对第三方 Skill，不要因为目录形态或同名搜索就推断来源；应在元数据中记录来源管理器、仓库和版本/ref，并把本地定制标记为 `third_party_customized`。

## 社区工具的分工

当前没有一个社区工具同时覆盖来源安装、升级前后差异、跨运行时适配器保护、行为能力门禁和回滚。建议按职责组合：

- [Vercel Skills CLI](https://github.com/vercel-labs/skills)：负责发现、安装、列举和升级，并维护其支持的来源锁定信息；
- [GitHub CLI Skill Update](https://cli.github.com/manual/gh_skill_update)：适合 GitHub 来源的远端 SHA 检查、dry-run 和更新；
- [SkilLock](https://github.com/skills-lock/skil-lock)：适合记录和比较 shell、URL、路径、脚本等结构化能力，并在 CI 中阻断策略违规；
- [skillshare](https://github.com/runkids/skillshare)：适合建立跨运行时的统一来源和同步层；
- 本仓库的 `skill_guard.py`：补足跨来源快照、暂存、来源分类、适配器路径记录、激活前审查以及没有专用能力门禁时的保守回滚保护。`skil-lock` 在 PATH 上时，它是能力漂移 gate 的后端；不在时，报告明确回退到风险信号。

这些工具不能互相替代。升级应先进入暂存，差异审查通过后再激活；对于第三方来源的实际版本和语义变化，仍以来源仓库、锁文件和人工审查为准。
