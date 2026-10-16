# Agent-Native CLI 与 Skill 契约设计稿

状态：用户已接受试点顺序与门禁力度，文档 PR 待合并；对应 ACM-177，承接 ACM-175「仅借鉴、不安装」。

交付边界：只改 agents-lab 的 docs/；不安装 CLI-Anything、cli-hub 或第三方 harness，不改宿主机配置，不激活 Skill，不修改产品代码。用户已授权提交并推送任务分支，对 `yicone/agents-lab` 的 `main` 开 docs-only PR；合并由 Agent Steward 负责。

## 结论

吸收三件事：先识别真实后端再设计命令；共享状态显式并发控制；CLI 与 Skill 作为同一发布契约校验。不要照搬默认 REPL、动态全局安装、或 `_locked_save_json` 原码。

建议先用 agents-lab 已有 `skill_guard.py` 做只读契约试点，再接 agent-storage-manager 的只读检查与安全计划，最后覆盖 DevDesk。原因：本仓有可核对代码和测试，两个产品尚未在本任务检出，不能把建议目录说成现有实现。

## 证据与勘误

上游固定版本：`HKUDS/CLI-Anything@34f519533bc175d2fe287ab8316b0dd99bb9cc43`。本仓检查基线：`62616b3604e164ba5d55c2fcddc20bc83944c376`。

| 来源 | 实际行为 | 对设计的影响 |
| --- | --- | --- |
| [HARNESS.md](https://github.com/HKUDS/CLI-Anything/blob/34f519533bc175d2fe287ab8316b0dd99bb9cc43/cli-anything-plugin/HARNESS.md#L12-L73) | Phase 1–3：识别后端、数据模型、现有 CLI；子命令与 REPL；JSON 输出；先探测再修改 | 保留方法，使用产品现有领域服务，不另造平行实现 |
| [测试与 Skill 阶段](https://github.com/HKUDS/CLI-Anything/blob/34f519533bc175d2fe287ab8316b0dd99bb9cc43/cli-anything-plugin/HARNESS.md#L111-L294) | 先写测试计划，再做单元/真实后端/子进程测试、核对产物；Phase 6.5 生成 Skill | 测试计划与执行证据分开；产物回读成为发布门槛 |
| [锁指南](https://github.com/HKUDS/CLI-Anything/blob/34f519533bc175d2fe287ab8316b0dd99bb9cc43/cli-anything-plugin/guides/session-locking.md)、[LibreOffice 实现](https://github.com/HKUDS/CLI-Anything/blob/34f519533bc175d2fe287ab8316b0dd99bb9cc43/libreoffice/agent-harness/cli_anything/libreoffice/core/session.py#L10-L33) | `r+` 打开，`flock(LOCK_EX)` 后 truncate、dump、flush；不存在则 `w`；锁失败继续无锁 | 借鉴锁意图，但不是崩溃安全事务，不能用于高风险状态原样复用 |
| [同步脚本](https://github.com/HKUDS/CLI-Anything/blob/34f519533bc175d2fe287ab8316b0dd99bb9cc43/.github/scripts/sync_root_skills.py#L58-L85) | 深层 harness Skill 发现后重写 name，生成根目录镜像 | 实际生成方向是深层到根层；不要因 HARNESS 称根目录 canonical 就假设根层为编辑源 |
| [镜像校验](https://github.com/HKUDS/CLI-Anything/blob/34f519533bc175d2fe287ab8316b0dd99bb9cc43/.github/scripts/validate_root_skills.py#L37-L110)、[CI](https://github.com/HKUDS/CLI-Anything/blob/34f519533bc175d2fe287ab8316b0dd99bb9cc43/.github/workflows/check-root-skills.yml) | 对源 Skill 与根镜像做文本一致性检查；镜像独有内容给覆盖风险提示；PR、main push、手动触发 | 这是 Skill 副本同步，不等于运行 CLI 校验命令/参数，更不等于行为兼容性证明 |

ACM-175 的「7 阶段」是概括；所读版本另有 Phase 6.5。根目录没有 `HARNESS.md`，实际入口在 `cli-anything-plugin/HARNESS.md`。本稿不沿用上轮未经本任务复核的 star 数、覆盖数量或兼容性承诺。

本地取证：完整 clone 超时，改为 GitHub API/raw 固定 SHA 读取所需文本；未执行上游代码。

## 适用性裁剪

| 做法 | 采用方式 | 不采用部分 |
| --- | --- | --- |
| GUI 操作映射真实后端 | API/领域服务优先，原生 CLI 次之，桌面 UI 自动化仅作受控补充；CLI、GUI 复用同一写入边界 | 用文件拼装绕过产品业务校验，或重写产品引擎 |
| 人读/机读双输出 | 新 CLI 统一机器契约；既有 CLI 保留现有旗标，适配后比较 | 为统一风格强改已有 `--format json` 为 `--json` |
| 状态化会话 | 显式 workspace/session ID，跨命令状态可检查 | 无参数默认 REPL，让 Agent/CI 挂起；隐式「当前项目」 |
| 测试前置、真实后端回读 | 单元、进程、真实后端三层；必需环境缺失明确失败/标未验证 | 把 mock 成功当集成成功；把完整历史测试日志塞入 Skill |
| 自动生成 Skill | 只生成命令参考块；人维护权限、风险和工作流说明 | 自动生成高权限指令后直接激活，或双向同步 |
| 软件包分发 | 沿用现有包体系、固定版本与来源审查 | cli-hub、全局插件安装、自动下载/执行第三方包装器 |

## CLI 契约：提议 v1

以下名称和字段均是拟议接口，不是产品现有命令。

- 无参数：输出帮助并退出；显式 `repl` 才能交互。非 TTY 不进入提示、浏览器登录或无限等待。
- 新工具提供 `--json`；stdout 单个 JSON 文档，诊断走 stderr，不混横幅、颜色、进度。已有工具通过清单声明 JSON 参数，避免破坏兼容性。
- JSON envelope：`schema_version`、`ok`、`data`、`error`、`warnings`；成功时 `error: null`，失败时 `data: null`，error 含稳定 `code` 与可读 `message`。不得回显令牌、密钥或含密参数。
- 提议退出码：0 成功，1 运行失败，2 参数/契约错误，3 版本冲突，4 锁超时，5 权限/策略拒绝。既有工具声明自己的映射，不能追溯套用。例如 `skill_guard.py diff --gate` 当前 2 表示阻断，不得解释成参数错误。
- 每条命令声明只读/写入、资源范围、网络访问、敏感输出、可否重试。只读命令也必须审查网络和敏感数据读取，不能因「只读」默认允许。
- 变更先 `plan`，列出目标、预期 revision、内容摘要及不可逆风险；`apply` 必须绑定计划 ID/摘要和目标，应用前复核。`--yes` 不能绕过策略/来源审查。
- 可重试变更接受幂等键；记录键、请求摘要和结果。相同键不同请求拒绝；外部调用超时后先回读结果，不盲目重试。
- 真实效果回读：存储检查实际目录、文件摘要及权限；DevDesk 检查实际资源状态；Skill 激活检查部署树及 adapter。退出码与回显不足以证明成功。
- 不要求所有工具增加持久会话；只读无状态命令保持无状态。撤销仅覆盖确实可逆的范围，删除/外部副作用不能承诺通用 undo。

## 状态文件：锁只是起点

### 上游模式的明确缺口

`r+` 解决已有文件在加锁前被截断的问题，但锁只覆盖写入。若两个进程先读旧值再分别保存，仍有 lost update。`FileNotFoundError` 后的 `open("w")` 存在首次创建竞争。truncate 后崩溃可留下空文件/半截 JSON；flush 也不是 fsync。锁导入或获取失败被吞掉属于 fail-open。advisory lock 不能约束不遵守协议的 GUI/其他进程。

### 小型本机 JSON 的拟议协议

1. 状态根目录限定于授权 workspace，目录私有；校验 owner、路径和软链接，不允许利用状态路径写出授权边界。
2. 每资源固定侧车锁文件 `state.json.lock`；以不截断方式打开，有限等待取得独占锁。锁不可用或超时返回明确错误，绝不降级无锁。释放 fd 即释放锁，持有期间禁止删除/替换侧车锁文件。
3. 在锁内读最新 JSON、校验 schema/revision、计算修改。新建也在同一把锁内完成；旧版本请求返回 conflict，禁止覆盖。
4. 同目录创建唯一临时文件，权限限制；完整序列化后 flush、fsync，再用原子 replace 替换状态文件；按平台能力同步父目录。所有协作读写均锁同一侧车文件，不能锁会被 replace 的旧 inode。
5. 崩溃遗留临时文件仅在持锁下、确认属于本工具时清理。坏 JSON 保留取证并报错，不能静默初始化。输出不含私密内容。

适用边界：macOS/Linux 本机文件系统、少量合作进程、单资源元数据。Windows 需要经验证的跨平台锁实现；网络文件系统/跨主机不做安全承诺。若产品已使用 SQLite/数据库或单写 daemon，复用事务、revision 与唯一约束，不再叠 JSON 状态副本。

`skill_guard.py` 的 `write_json()` 当前直接写 manifest，`activate()`/`rollback()` 涉及目录复制；单文件原子写不等于整个激活事务。未来应另加按目标排序的资源锁、应用 journal、内容复核和失败恢复；先明确多目录恢复协议，再考虑自动化激活。此处是风险与后续设计，不是本次修复。

### 必须覆盖的状态测试

- 两进程首次创建、并发读改写：成功更新均保留，或旧 revision 明确冲突；无静默丢失。
- writer 持锁时另一 writer 有限超时；模拟锁不可用必须拒写。
- 序列化失败、replace 前进程中断：旧状态有效；replace 后状态完整；锁可重新取得。
- 坏 JSON、跨版本 schema、只读目录、软链越界、权限错误：拒写且不破坏原数据。
- 幂等键重复不产生二次副作用；同键不同内容拒绝；跨资源操作失败能报告部分完成并恢复。

## agents-lab 落点：与现有治理兼容

现有事实：`skills-governance/README.md` 已定义 snapshot → stage → diff/gate → activate/rollback；`scripts/skill_guard.py` 提供上述命令；`scripts/drift_report.py` 只读检查来源与版本漂移；`scripts/validate_skills_inventory.py` 和 inventory schema 都拒绝未知字段。本次检出没有 `.github/` 工作流目录。

因此：不替换现有治理，不给 `skills-inventory/v1` 私塞 CLI 字段，不把根 `skills/` 变成上游式生成镜像。

拟议增加独立 `cli-skill-contracts/v1` 注册表，通过 inventory 的 skill name 和 canonical_path 连接；先限定本仓拥有/已审计的 Skill，runtime bundled、plugin、Skills CLI 来源保持原更新权威。

单条记录拟含：

| 字段 | 约束 |
| --- | --- |
| `skill_name`, `skill_path` | 唯一匹配既有 inventory；不创建第二套 provenance |
| `cli_source` | 产品仓标识、固定 ref、发布版本；本仓 CLI 允许 repo-relative 源路径 |
| `argv`, `json_args`, `contract_version` | 参数数组而非 shell 字符串；静态检查绝不执行这些字段 |
| `contract_fixture`, `contract_sha256` | 产品 CI 导出的规范化命令契约；摘要用于内容一致性，不作为来源签名 |
| `generated_block`, `minimum_cli_version` | 生成块标记唯一；安装版本不兼容则拒绝推荐写命令 |
| `capabilities`, `safe_examples` | 命令路径、选项类型/必需性/默认值、输出 schema、退出码、权限/副作用、人工审查的安全用例 |

单一事实源分层：命令结构来自产品 CLI 注册树/原生 introspection；安全和工作流说明由产品维护者审查；agents-lab 的 canonical Skill 是治理记录确认的源，生成器只更新其中标记块。所有 runtime adapter、打包副本单向派生，不允许回写源。不能通过抓 `--help` 文本就宣称完整契约同步。

跨仓同步：产品 CI 生成契约 fixture 和 CLI 包内 Skill；agents-lab 固定产品版本/ref，人工引入 fixture 更新与 Skill 变更。未下载真实二进制的 agents-lab CI 只证明 fixture/Skill 一致，不证明已安装 CLI 一致；产品发布 CI 必须对实际构建包重复检查。禁止自动跟随 latest 或 PR 提供的任意远程命令。

### CI 分层（提案，不在本次启用）

1. 静态门禁：校验现有 inventory，再校验契约注册表与本地路径、唯一名称、固定 ref；检查 Skill 生成块与 fixture 的差异、缺失/孤立副本、非法生成块编辑。只读失败，不自动覆盖、不自动提交。
2. 契约门禁：受信构建环境对实际 CLI 生成规范化元数据，与 fixture 比较。删除/更名命令、选项或输出字段、改变副作用/退出码均须显式兼容性审查与版本更新；不能只比较两份 SKILL.md 文本。
3. 行为门禁：隔离 HOME/workspace，用安装后的 CLI 在无关 cwd 跑帮助、JSON 成功/失败、超时、不挂起、只读示例和进程级状态测试。fixtures 覆盖 flag 移除、镜像独改、重复命名、锁失败等反例。
4. 真后端门禁：产品专用 runner 验证实际效果、权限、并发和恢复。需要 GUI/软件时单独运行；环境缺失报告未验证，不把 skip 当发布通过。

拟议 workflow：普通 PR 可运行无凭据静态校验与隔离单元测试；禁止 `pull_request_target` 带密钥执行 PR 代码。实际产品/桌面集成由受信 runner 人工批准，在沙箱 fixture 中运行，不碰个人真实数据。路径触发至少覆盖 `skills/**`、`skills-governance/**`、契约 fixture、对应 CLI 源、生成器、测试与 workflow 自身；另提供手动全量检查。

## 具体改动清单（全部待后续批准）

以下新增路径均为建议，尚不存在；现有路径已在本仓核对。

| 顺序 | 位置 | 改动与验收 |
| --- | --- | --- |
| P0 | 新增 `docs/conventions/agent-native-cli.md`、`docs/templates/cli-harness-review.md` | 把本稿收敛为轻量规范与取证/测试清单；不要求 Click、Python、REPL 或统一包管理器 |
| P0 | 新增 `skills-governance/config/cli-skill-contracts.json` 及同名 `.schema.json` | 独立契约注册表；关联 inventory，旧 inventory validator 不变 |
| P0 | 新增 `skills-governance/scripts/validate_cli_skill_contracts.py`、`render_cli_skill_reference.py` | 前者只读、离线比较；后者显式本地生成标记块并展示 diff，拒绝覆盖块外内容 |
| P0 | 新增 `skills-governance/tests/test_cli_skill_contracts.py` 和 fixtures | 正反例覆盖命令/flag 漂移、镜像独改、路径越界、重复记录；不执行 fixture 任意命令 |
| P0 | 现有 `skills-governance/scripts/skill_guard.py` 和 `tests/test_skill_guard.py` | 只读导出其真实 argparse 命令树，锁定现有退出码与 JSON 形状；写命令协议另开兼容性评审，不直接更改 |
| P1 | 新增 `.github/workflows/check-cli-skill-contracts.yml`；更新 `skills-governance/README.md`、`docs/release-checklist.md`（相对 skills-governance） | 静态、契约、行为检查分层，说明证据边界；release checklist 增加 fixture/Skill/实际包版本同批审核 |
| P1 | 现有 `skills-governance/scripts/drift_report.py`、其测试 | 后续增加可选 CLI 契约漂移报告，保持只读；缺少 CLI 时标未验证，不自动安装 |
| P2 | 产品仓现有后端服务、CLI 入口、测试与发布 CI（路径待检出确认） | agent-storage-manager 优先 inventory/inspect/plan；DevDesk 优先资源 list/status，后接有授权范围的变更；不在 agents-lab 复制产品后端 |
| P2 | 现有 `skills-governance/scripts/skill_guard.py` 写路径及测试 | 另行评审事务/锁/journal；确认恢复协议后补 manifest 原子写、多目标并发与失败恢复，不以文件锁冒充目录事务 |

DevDesk 与 agent-storage-manager 的命令名、仓内路径、数据存储均未在本任务验证；后续先读各仓 AGENTS/manifest，定位 GUI/CLI 共用服务、写入点和现有测试，再把上表转换为精确文件改动。这里不声称产品缺少 CLI 或一定需要 JSON 状态文件。

### 推荐试点验收

- 单一治理工具从真实解析器导出契约，Skill 命令参考可重现；人为移除一个 flag 后校验必须失败。
- 人为编辑派生副本必须失败并指回源；不得静默覆盖手工内容。
- 发布包在隔离工作区用自身 CLI 成功读取 fixture，机器输出可解析；失败输出与退出码按既有契约验证。
- 开放写功能前，状态竞争、锁失效、崩溃恢复与真实回读测试全部通过；未满足时维持只读。
- snapshot/stage/gate 的原流程不被旁路；不新增自动安装、自动激活、常驻服务或记忆系统写入。

## 决议（用户已接受）

决议依据：ACM-177 用户确认评论 `01a142c3-f38f-7e03-81f6-d570b8f3f609`。

1. **试点顺序**：采用「skill_guard 只读契约 → agent-storage-manager → DevDesk」。先验证机制，再进入产品试点。
2. **门禁力度**：静态/fixture 一致性先设为阻断；产品真实后端测试作为开放写权限前的必需验收。环境缺失或未验证时，不开放写权限。

以上为已确认设计决议，不表示门禁已经实现或启用。其余为设计建议，不构成安装、产品修改或发布授权；本次只提交文档，合并由 Agent Steward 负责。

## 本次交付与验证范围

已交付设计稿和 docs 索引入口；按用户授权通过任务分支 PR 交付。检查项：Markdown 本地链接、固定 SHA 来源、两条决议、变更范围及 whitespace。前轮运行现有 skills-governance 单元回归以确认基线，不代表拟议 CI/状态协议已实现或产品集成已验证。

实际检查结果：`git diff --check`、本地链接、两条决议与 docs-only 范围检查通过。`python3 -m unittest discover -s skills-governance/tests -v` 共 37 项，36 通过，1 失败：`test_repo_inventory_covers_canonical_skills` 检出既有 inventory 的 `skills[28].visible_paths[0]` 不存在，即 `cursor-plugin-x` 对应 `~/.cursor/plugins/cache/cursor-public/x/850d7f2e9f5e55dff7eb00a0a479337fe79eb22f/skills`。已确认 inventory 与 HEAD 完全一致；属于当前机器路径缺失，本次不安装插件、不修改清单来消除失败。

知识路由：决议随设计稿保留仓内，本次不另写 Logseq/Nowledge Mem；规范落地后再按既有知识路由补充链接与短结论，避免把已接受设计误写成已启用规则。
