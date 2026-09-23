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

## 七、避免 Devin 在 auto permission 下触发交互工具调用

- 初次 host review 中，Devin 在 `--permission-mode auto` 下尝试读取本地文件，被 CLI 拒绝并产生非 JSON 输出。
- 设计上没有切换到 `dangerous`，也没有放宽权限或换模型。
- provider 改为先由 host 通过 `gh pr diff --patch` 获取并验证 PR patch，再把完整 patch 作为只读输入交给 Devin。
- Devin prompt 明确禁止工具调用、命令执行、文件编辑、commit、push 和 GitHub 操作。
- 该改变保留了固定模型和固定 session，同时降低 ACP/工具确认对 review 结果的影响。

## 八、稳定错误字段与双 JSON 协议

- 进一步实测发现 provider 内部有 evidence，但稳定响应没有顶层 `failure_code`，调用方只能看到模糊的 `await-user`。
- 现在 `devin-host-review/v1` response 始终包含 `failure_code`；预检、head mismatch、权限/进程失败和 request rejection 都有明确分类。
- 又发现一种调用误区：调用方把 `pr-review-round/v1` 的控制记录直接作为 `host_provider.py` stdin。
- 当前协议明确分离：
  - `pr-review-round/v1`：只供 review-round validator 使用；
  - `devin-host-review/v1`：只作为 host worker queue request；
  - `host_provider.py` 是 worker 内部组件，调用方不得直接调用。
- 误把控制记录传给 provider 时，返回 `control_record_not_provider_request`，而不是让调用方猜测 `invalid-request` 的原因。

## 九、当前实现验证结果

- provider、preflight、registry、repository binding 相关测试：`12/12 passed`。
- 已验证主 workspace 请求可以发现 nested worktree 中的固定 session。
- 已验证 host worker 能从主 workspace 完成一次真实的结构化 Devin review，并返回 `no-findings`。
- 当前本地 canonical skill 通过软链接暴露到：
  `/Users/tr/.agents/skills/devin-pr-review-thread`
- 最新基础设施提交包括：
  - `a77e8db`：从 review workspace 发现 Devin session
  - `148b1b9`：稳定返回 `failure_code`
  - `45b1252`：从主 workspace 发现固定 session
  - `b4fcbc4`：分离 round record 与 provider request 协议

## 十、项目级待办（不包含 PR #232 的 GitHub 处理）

以下事项属于 skill/provider 基础设施；PR #232 的 GH 状态、thread resolve、merge 和使用方 review-loop 不在本清单内。

### 待确认

- 是否将 host worker 固化为登录后自动启动的 host service，还是继续由 host maintainer 手动保持常驻。
- 是否接受“新 worktree 的 Devin CLI trust 仍可能需要 exact-path 登记”，或另行设计显式授权的 host-side trust manager。
- 是否将当前本地 12 个 skill/provider 提交推送到远端，或先以独立 PR 发布。
- 是否需要将 `devin-pr-review-thread` metadata version 从 `0.2.0` 升级，并建立变更/发布记录。

### 待执行

- 为 queue request 增加一个面向调用方的安全提交辅助入口，避免 agent 自己处理 request filename、原子写入和 response polling。
- 为“主 workspace → 已登记 worktree 集合 → session discovery”增加自动化测试，而不只依赖本机实测。
- 增加 session working directory 漂移回归测试：同一 session 在主 workspace 与不同 PR worktree 间往返后，下一轮仍能发现并从 canonical root 执行。
- 更新 `docs/superpowers/specs/` 中仍描述旧协议或 exact session root 的设计文档，使其与当前 provider/worker 行为一致。
- 建立 evidence、stale response 和未使用 authorization 的保留期限与清理工具；清理前必须保留审计摘要。
- 将主 workspace exact allowlist 与 worktree-parent wildcard 的 host enrollment 组合固化为可重复的 host setup 流程。
- 在其他 agent harness 上验证“只提交最小 queue request、绝不直接调用 provider”的跨 runtime 兼容性。

## 记录边界

本文不替代 `SKILL.md`、review-round contract 或 host runtime recovery 文档；它只记录方向演进、已验证结果和 agents-lab 基础设施待办。任何具体 PR 的 finding 分类、修复、thread 回复、resolve 和 merge，应留在对应 PR 的控制线程中。
