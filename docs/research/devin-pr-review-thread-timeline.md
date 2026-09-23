# Devin PR Review Thread 演进时间线

本文记录 `devin-pr-review-thread` 从设计、实现到跨 harness 自测的主要方向变化。它记录的是 agents-lab 的 skill/provider 基础设施，不是某个 PR 的审查记录。

## 一、初始目标：固定模型、固定会话、GitHub thread

- 目标是让本机其他 agent harness 通过 Devin CLI 审查 GitHub PR，并使用 `gh` 在 PR 中发布 review thread。
- 模型固定为 `SWE-2 High`，可用期记录为 2026-10-26 前。
- 每个仓库固定一个 Devin session，所有 review 复用该 session。
- session 的主工作目录与仓库主 workspace 绑定；skill 使用软链接暴露到全局 agent skill 目录。
- 初始安全边界确定为：不替换模型、不创建第二 session、不切换 `dangerous` 权限、不把未经验证的输出发布到 GitHub。

## 二、从一次性 review 转向有界 review-round

- 早期失败暴露出 review、修复、复审容易变成无限循环，尤其是 agent reviewer 会持续提出低置信度或重复问题。
- 因此将 `devin-pr-review-thread` 定义为 provider，而不是 review-loop controller。
- `pr-review-fix` 负责 finding 分类、批量修复、验证和下一轮决策；review-round contract 负责记录 `run`、`post-triage`、`fix-and-rereview`、`stop` 等状态。
- round 预算改为有限状态机：第二轮只能处理新发现或修复后仍存在的问题；第三轮及以后必须有明确 exception。
- provider 不负责自动修复、resolve thread、merge PR 或自行决定下一轮。

## 三、引入 host-side provider，隔离 sandbox 与 Devin/ACP/GitHub

- 发现大多数调用方运行在 sandbox，无法稳定访问 GitHub、Devin CLI、ACP、trust store、session lock 和受保护证据目录。
- 方向从“每个 agent 自己操作 Devin/gh”改为 host-side provider：
  - sandbox 只提交最小 review request；
  - host worker 执行 preflight、授权、Devin 调用、结构化校验和 GitHub 发布；
  - 调用方只消费稳定 JSON 状态和 opaque evidence reference。
- provider 只接受 host-issued authorization，调用方不能自行创建或修复授权记录、session registry、lock 或 trust 配置。
- Devin Desktop/ACP 是否打开不再作为调用方前提；运行时问题归 host maintainer 处理。

## 四、运行时加固与失败分类

- 增加只读 `preflight.py`，统一检查：仓库 origin、PR head、精确模型、固定 session、workspace trust、锁、GitHub transport 和运行时证据。
- GitHub 错误从笼统的 `pr_identity_failed` 拆分为：
  - `github_transport_unavailable`
  - `pr_not_found_or_forbidden`
  - `github_response_invalid`
- `host_worker.py --startup-check` 从“一次检查后退出”改为“通过后保持常驻”；`--check-only` 保留为诊断模式。
- provider 失败时保留受保护 evidence，并返回稳定的 `await-user`/`provider-unavailable` 状态；禁止把中间日志或非 JSON 输出当作 review 结果。

## 五、从 root-keyed session 改为 repository-level session binding

- 早期注册表按本地 filesystem root 绑定，无法支持同一仓库的主 workspace 与多个 worktree。
- 改为按规范化 GitHub origin（如 `yicone/yr-monorepo`）绑定一个固定 session；旧 root-keyed registry 必须通过 host-only migration 转换。
- `host_enroll.py` 负责 GitHub origin allowlist；`session_enroll.py` 负责固定 session 注册；两者职责分离。
- worktree parent 采用 wildcard allowlist，避免每新增一个 worktree 就重复做 repository enrollment。
- 后续发现主 workspace 位于 worktree parent 外，因此 host allowlist 同时需要 exact main root 和 parent wildcard。

## 六、处理 Devin session 的 workspace-scoped 可见性

- 实测发现 `devin list --format json` 按当前 workspace 查询，同一个 session 可能只在实际拥有它的 nested worktree 中出现；而且复用同一 session 从不同 cwd 启动时，Devin 会改变其 `working_directory`，因此 session 可能在主 workspace 与不同 worktree 之间往返漂移。
- 初始实现从 registry 的主 workspace 查询，导致合法 session 被误报为 `session_missing`。
- 当前实现改为：
  1. 先从 review request 的 root 查询；
  2. 若主 workspace 看不到 session，则在已登记 worktree parent 下发现所有候选 worktree，不按当前 PR head 排除候选；
  3. 验证发现的实际 session root 仍在注册 root/worktree boundary 内；
  4. provider 始终从 registry 的 canonical main workspace 恢复 Devin，避免下一次 review 再次把 session 迁移到调用方 worktree。
- 这样调用方可以留在任意仓库 worktree，session discovery 与 PR target worktree 解耦，且每次 review 都会把 session 回锚到稳定的 canonical root。
- 后续实测又发现并发 workspace-scoped `devin list` 会返回空投影，且 session 可能在扫描期间被另一个 cwd 的 Devin 调用迁移；preflight 现在使用只读 session database workspace hint，随后顺序扫描所有边界内 worktree，并保留扫描根目录/策略证据，避免并发 CLI 或 head 过滤制造 `session_missing`。

## 七、复现并修复“修改后仍然 session_missing”

- 在一次真实 PR 复测中，旧的常驻 worker 仍返回 `session_missing`，但同一 session 实际存在于另一个已登记 worktree；这证明“registry 存在”不等于“当前 cwd 的 `devin list` 可见”。
- 一次并行化扫描实验进一步证明 Devin CLI 的 workspace projection 不是并发安全的：并发执行多个 `devin list` 会得到空投影，产生新的假阴性。因此并行扫描被撤回，不再作为优化方案。
- 当前实现改为 `db-hinted-sequential-worktrees-v2`：先只读查询 `sessions.db` 的 `working_directory`，再顺序扫描边界内 worktree；不按 PR head 过滤，并在证据中记录 `registered_root`、`observed_root`、`lookup_roots` 和 discovery strategy。
- host 侧使用系统代理对真实 PR 做了启动预检，结果为 `status=ready` / `preflight.status=ok`，固定 session 被发现，模型 `SWE-2 High` 可用。沙盒内直接跑同一预检仍可能因无法连接本机代理而得到 `github_transport_unavailable`；这属于执行边界差异，不是 host 预检失败。
- 长期 worker 必须从 canonical skill path 重启；若 evidence 中没有 `discovery_strategy: db-hinted-sequential-worktrees-v2`，应判定为旧 worker 副本，而不是让调用方自行修复或重试。
- 2026-09-24 的跨项目实测再次暴露短超时：host preflight 的 `devin list` 4 秒预算在较慢但正常返回的 CLI 上误报 `session_output_invalid`。当前修复将每次显式 `cwd` 的 session list 命令预算提高到 30 秒，并加入回归测试；它不改变顺序 discovery、固定 session 或 fail-closed 边界。

## 八、登录 service 后的 queue timeout 与模型目录慢查询

- 自动 LaunchAgent 已正常运行并持有代理环境，但一次真实请求仍被调用方记录为 `queue_response_timeout`。
- queue 中最终产生的 host response/evidence 显示，真实错误是 `model_output_invalid`；进一步复现发现 `devin models list --format json` 在 host 上需要约 30 秒，而 `preflight.py` 仍使用 12 秒默认命令预算，导致可用的 `SWE-2 High` 被误判为模型输出无效。
- 修复为模型目录查询使用独立的 60 秒预算，并增加回归测试；修复后的 host preflight 已返回：`status=ok`、`model.available=true`、`label=SWE-2 High`、`session.list_state=present`。
- 该修复解决的是基础设施误判；此前 round 的 `await-user` 记录仍然有效，是否重新发起 review 必须由 review-round 控制线程产生新的授权决定。

## 九、修复 queue client 丢失规范化 JSON

- 后续复测显示 host response 实际包含 `status`、`failure_code`、`evidence_ref`，但调用方仍报告“queue client 没有返回规范化 JSON”。
- 根因是 queue client 把合法的 `await-user` application status 映射成退出码 2；部分 harness 在非零退出时丢弃 stdout，于是调用方看不到 JSON 字段。
- 现在只要拿到 `devin-host-review/v1` JSON（包括 `await-user`），queue client 都以退出码 0 输出；只有参数错误或无法得到规范化对象才返回非零。
- 已用 host LaunchAgent 实测：无效仓库请求输出完整 JSON，包含 `status=await-user`、`failure_code=repo_identity_failed` 和 `evidence_ref`，退出码为 0。

## 十、修复大 PR patch 获取的 transport 脆弱性

- 一次 `gh pr diff` 复现返回 `TLS handshake timeout`，导致 provider 把完整 review 请求压成 `diff unavailable`。
- host provider 现在优先请求 GitHub REST diff media type，并在必要时回退到 `gh pr diff`；成功取得的 patch 只解析一次，同时用于 changed-line 校验和 Devin prompt。
- host 侧已验证：REST patch 获取成功，约 1.1 MB、2 个文件；因此大补丁不再受原有 GraphQL 路径的单次超时影响。
- 后续真实 round 又复现 `unexpected EOF`；连续 host 探测显示 REST 请求大多成功、偶发断流。provider 现在仅对传输形态错误做每条路径最多三次重试，认证/身份/HTTP 错误不重试，耗尽后仍返回带 evidence 的 `github_transport_unavailable`。
- 紧接着又发现约 1.15 MB patch 被作为单个 Devin argv 传入，触发 `OSError`；同时一次遗留的诊断 worker 与 LaunchAgent 并行消费 queue，产生两份授权并覆盖 response。provider 现改用 `--prompt-file`，worker 增加单消费者锁、原子 request claim，以及带 request identity/evidence 的异常响应；诊断 worker 不能与登录服务并行运行。
- 操作边界补充：`--startup-check` 不带 `--check-only` 会继续服务 queue；诊断必须显式使用 `--check-only`。即使误启动，新的 queue lock 也会让第二个 worker 立即以 `worker_already_running` 退出。

## 十一、避免 Devin 在 auto permission 下触发交互工具调用

- 初次 host review 中，Devin 在 `--permission-mode auto` 下尝试读取本地文件，被 CLI 拒绝并产生非 JSON 输出。
- 设计上没有切换到 `dangerous`，也没有放宽权限或换模型。
- provider 改为先由 host 通过 `gh pr diff --patch` 获取并验证 PR patch，再把完整 patch 作为只读输入交给 Devin。
- Devin prompt 明确禁止工具调用、命令执行、文件编辑、commit、push 和 GitHub 操作。
- 该改变保留了固定模型和固定 session，同时降低 ACP/工具确认对 review 结果的影响。

## 十二、稳定错误字段与双 JSON 协议

- 进一步实测发现 provider 内部有 evidence，但稳定响应没有顶层 `failure_code`，调用方只能看到模糊的 `await-user`。
- 现在 `devin-host-review/v1` response 始终包含 `failure_code`；预检、head mismatch、权限/进程失败和 request rejection 都有明确分类。
- 又发现一种调用误区：调用方把 `pr-review-round/v1` 的控制记录直接作为 `host_provider.py` stdin。
- 当前协议明确分离：
  - `pr-review-round/v1`：只供 review-round validator 使用；
  - `devin-host-review/v1`：只作为 host worker queue request；
  - `host_provider.py` 是 worker 内部组件，调用方不得直接调用。
- 误把控制记录传给 provider 时，返回 `control_record_not_provider_request`，而不是让调用方猜测 `invalid-request` 的原因。

## 十三、当前实现验证结果

- provider、preflight、registry、repository binding 四组测试脚本均通过；另有 Python 编译检查和 `git diff --check` 通过。
- 本轮新增的 host setup、清理策略、架构规范也已加入可重复验证：host setup 组合测试、cleanup 保留/过期授权测试，以及 v1/v2 Archify 结构与浏览器视觉检查。
- 已验证主 workspace 请求可以发现 nested worktree 中的固定 session。
- 历史上已验证 host worker 能从主 workspace 完成一次真实的结构化 Devin review，并返回 `no-findings`；2026-09-24 的跨 harness 实测又完成了真实 `review-published`，证明 session discovery 修复不只停留在 preflight。
- 当前本地 canonical skill 通过软链接暴露到：
  `/Users/tr/.agents/skills/devin-pr-review-thread`
- 最新基础设施提交包括：
  - `a77e8db`：从 review workspace 发现 Devin session
  - `148b1b9`：稳定返回 `failure_code`
  - `45b1252`：从主 workspace 发现固定 session
  - `b4fcbc4`：分离 round record 与 provider request 协议
  - `de40135`：固定 provider 的 canonical 执行根目录
  - `e67053c`：增加 host 预检超时边界和结构化超时结果
  - `3e35a24`：撤回并发 discovery，改用 DB hint + 顺序扫描
  - `6163e1e`：同步 discovery 策略回归测试

## 十四、从基础设施完成到跨 harness 验证

- **2026-09-20 至 2026-09-23：运行时与调用方边界收敛。** LaunchAgent、固定 host transport、fail-closed response、顺序 discovery、safe queue client、host setup、cleanup 和双 JSON 协议陆续完成；对应的 fake-runtime、queue、registry、worker、repository-binding、cleanup 和架构图检查均通过。新 worktree 由 parent wildcard 覆盖，调用方不需要重复 repository enrollment；Devin CLI 的 exact-path trust 仍属于 host 维护动作。
- **2026-09-23：架构与发布记录同步。** v1/v2 Archify 图统一了 worker/provider/GitHub 的职责边界，并在 v2 中补出 Host enrollment → worker/provider/session 的控制路径；skill metadata 升级到 `0.3.0`，变更记录和实施提交保持可审计分组。
- **2026-09-24：从 preflight 修复扩展到真实 review。** 跨项目实测证明，较慢但正常返回的 `devin list` 不应被短预算误判为 provider 故障；将 session-list 预算提高到 30 秒并重启 canonical LaunchAgent 后，固定 session、精确模型和 GitHub transport 均可通过 host preflight，queue client 能完成一次真实 `review-published`。该验证只记录能力边界，不把具体仓库、PR、head、session 或 evidence 标识写入本时间线。
- **当前状态。** P0/P1/P2 的 host 生命周期、transport、授权、证据清理、协议和回归验证已经完成；后续真实 review 仍必须由使用方控制线程记录新的 bounded round 授权，provider 只消费最小 queue request 并返回经过校验的结果。

## 记录边界

本文不替代 `SKILL.md`、review-round contract 或 host runtime recovery 文档；它只记录方向演进、已验证结果和 agents-lab 基础设施状态。具体 PR 的 finding 分类、修复、thread 回复、resolve 和 merge 仍应留在对应 PR 的控制线程中；本节只记录已完成的代表性闭环与可复用边界。
