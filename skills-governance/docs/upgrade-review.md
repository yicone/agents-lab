# Skills 升级审查与回滚

本页说明如何在升级运行时内置 Skills、插件引入的 Skills，或通过 Skills CLI 安装的第三方 Skills 前后，保留可审查、可回滚的证据。

## 目标与边界

`skill_guard.py` 是本地薄控制层，不负责安装或升级 Skill。它负责：

- 在变更前保存指定 Skill 根目录及适配器目录的快照；
- 对比升级前后的文件、权限、哈希、符号链接和关键风险信号；
- 在发现新增脚本、网络行为、秘密访问、触发条件变化或适配器路径变化时提供保守门禁；
- 在确认快照可用后恢复被捕获的目录。

真正执行升级的仍是对应的来源管理器，例如 Skills CLI、运行时升级器或插件管理器。对于具有结构化能力分析和锁文件的环境，优先叠加 SkilLock 等工具；`skill_guard` 负责跨来源的快照、来源元数据和本地适配器保护。

## 标准流程

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

### 2. 使用来源管理器执行升级

快照成功后，再调用对应的来源管理器，例如：

    npx skills update

或者执行运行时、插件自己的升级命令。`skill_guard.py` 不替代这些命令，也不会自动激活升级。

### 3. 查看升级差异并运行门禁

升级后先查看机器可读结果：

    python3 skills-governance/scripts/skill_guard.py diff \
      --snapshot ~/.local/share/agents-lab/skill-snapshots/2026-10-01-before-update \
      --current-root ~/.agents/skills \
      --format json \
      --gate

也可以使用文本格式人工审查：

    python3 skills-governance/scripts/skill_guard.py diff \
      --snapshot ~/.local/share/agents-lab/skill-snapshots/2026-10-01-before-update \
      --current-root ~/.agents/skills \
      --format text

退出码含义：

- `0`：未发现当前门禁配置识别的风险信号；
- `2`：门禁阻断，必须先审查结果，不应直接激活或发布；
- `1`：参数、快照或文件系统错误，不代表升级安全。

重点审查 `unexpected_instructions`、`unexpected_files`、`scripts`、`network_behavior`、`secret_access`、`trigger_changes` 和 `adapter_paths`。门禁是保守信号，不是自动判定恶意；即使退出码为 `0`，也不能替代对重要 Skill 的人工审查。

如果门禁阻断，先保存 JSON 结果并检查具体文件。确认变化符合预期后，再由来源管理器完成激活或发布；如果不符合预期，则回滚或放弃本次升级。若采用 SkilLock，应同时审查其 `skills.lock` 和能力差异结果，将它作为更细粒度的行为门禁。

### 4. 回滚

只有在确认快照包含正确目录并且希望恢复时，才执行：

    python3 skills-governance/scripts/skill_guard.py rollback \
      --snapshot ~/.local/share/agents-lab/skill-snapshots/2026-10-01-before-update \
      --current-root ~/.agents/skills \
      --force

`rollback` 要求显式提供 `--force`，并恢复快照中捕获的目录和符号链接。它不会替来源管理器修复包索引、锁文件、插件注册状态或运行时元数据；这些状态必须使用对应来源管理器的恢复、重新安装或锁文件流程处理。

## 多目录与适配器注意事项

如果快照时传入多个 `--skill-root`，后续 `diff` 或 `rollback` 需要按相同顺序提供相同数量的 `--current-root`。适配器目录只在创建快照时作为受保护的活动路径记录；恢复时，应确认适配器仍指向预期的 canonical source，避免把运行时入口误当成源码。

对本地自建 Skill，先保证 canonical Git 仓库是唯一源码，再快照项目级或全局适配器。对第三方 Skill，不要因为目录形态或同名搜索就推断来源；应在元数据中记录来源管理器、仓库和版本/ref，并把本地定制标记为 `third_party_customized`。

## 社区工具的分工

当前没有一个社区工具同时覆盖来源安装、升级前后差异、跨运行时适配器保护、行为能力门禁和回滚。建议按职责组合：

- [Vercel Skills CLI](https://github.com/vercel-labs/skills)：负责发现、安装、列举和升级，并维护其支持的来源锁定信息；
- [GitHub CLI Skill Update](https://cli.github.com/manual/gh_skill_update)：适合 GitHub 来源的远端 SHA 检查、dry-run 和更新；
- [SkilLock](https://github.com/skills-lock/skil-lock)：适合记录和比较 shell、URL、路径、脚本等结构化能力，并在 CI 中阻断策略违规；
- [skillshare](https://github.com/runkids/skillshare)：适合建立跨运行时的统一来源和同步层；
- 本仓库的 `skill_guard.py`：补足跨来源快照、来源分类、适配器路径记录以及没有专用能力门禁时的保守回滚保护。

这些工具不能互相替代。升级前仍应先做快照，差异审查通过后再激活；对于第三方来源的实际版本和语义变化，仍以来源仓库、锁文件和人工审查为准。
