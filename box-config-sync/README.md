# box-config-sync

Box（Grok Bot 共享 Linux 机）配置快照的采集、只读对账与受控传输工具链，按 ACM-910 设计稿 `multica-board/docs/box-config-sync-design.md`（commit `482432f`，§5.4 Routine 协议、§5.6 数据分支、§8 拆单）实现。CLI 由 `bin/boxsync` 直接运行，不安装、不 `pip`，只用 Python 3 标准库加 `git`、`multica` 两个已有二进制。

子命令：`discover`、`collect`、`reconcile`、`publish-snapshot`（仅 Box）、`fetch-snapshot`（仅 Mini）。Routine 巡检路径上 `collect` / `reconcile` / `publish-snapshot` 的预期退出码只会是 `0`（无待办）、`3`（部分成功或传输失败，输出仍写出）或 `10`（发现漂移）；`4` 为安全拒绝、`6` 为自身 I/O 失败，均按异常汇报。`plan-writeback` / `apply-writeback` / `rollback` 随 Phase 2 交付，当前不存在。

## 每日巡检 Routine（P3-B）

### 提示词模板

以下模板由用户在 Agent Steward 上手动配置；本仓库不创建、不修改任何 Routine。

```text
1. 调用 GetMcpServerStatus，读取 <automation_status>。
2. 只按 box-config-sidecar/v1 允许的字段写 /home/box/.local/state/box-config-sync/inbox/<你的 bot_id>.sidecar.json；
   不写 URL、命令、env、token 与 prompt 原文，prompt 只写 sha256。
3. 在 /workspace/agents-lab 执行：
   python3 box-config-sync/bin/boxsync collect --sidecar <上一步路径>
   python3 box-config-sync/bin/boxsync reconcile --snapshot /home/box/.local/state/box-config-sync/snapshots/latest.json
   python3 box-config-sync/bin/boxsync publish-snapshot --snapshot /home/box/.local/state/box-config-sync/snapshots/latest.json
4. 第 3 步 reconcile 返回 10（发现漂移）时，只汇报差异行与汇总计数；
   不执行 plan-writeback（写回能力留待第二期 ACM-930，启用另行批准）。
5. 绝不执行 plan-writeback、apply-writeback、rollback、直接的 git push、gh pr 或任何 multica 写命令；
   唯一的推送是第 3 步的 publish-snapshot。
```

当前阶段口径（ACM-934，2026-11-23）：`reconcile` 返回 10（发现漂移）只汇报差异行与汇总计数，不执行任何写回命令——`plan-writeback` 等写回能力留待第二期 ACM-930 交付并经另行批准后启用（设计 §5.4 原第 4 步即按届时口径执行）。

### Routine 的边界

- 每个 turn 只按模板顺序执行第 3 步的三个命令；`collect` / `reconcile` / `publish-snapshot` 的退出码只会是 0、3 或 10，出现其他退出码一律视为异常：照常汇报，不重试、不换命令。
- 绝不执行 `plan-writeback`、`apply-writeback`、`rollback`、直接的 `git push`、`gh pr` 或任何 `multica` 写命令。Routine 全程唯一的推送是 `publish-snapshot` 对数据分支 `box-snapshots` 的受控推送（白名单只按 `origin/main` 读取，见 §5.6）。
- `publish-snapshot` 推送失败（退出码 3）时流程照常结束并汇报 `push_failed`；命令内部对非快进只重试一次，不循环重试。
- Sidecar 是会话内产出：只写 §5.4 schema 允许的字段（MCP 名称/状态/tool_count/origin，Routine 元数据与 `prompt_sha256`），不落 URL、命令、env、token 与 prompt 原文。CLI 对未知键、URL、秘密逐份拒绝（`rejected_schema` / `rejected_secret`），拒绝后该 Bot 的 turn 内数据记 `not_collected`。

### D6 覆盖范围

按设计 D6，第一阶段只有 Agent Steward 提交 Sidecar。其余 Bot 的 turn 内 MCP 与 Routine 一律记 `evidence_level: not_collected`，看板显示「未采集」，不得显示为空或冒充已覆盖；没有提交 Sidecar 的 Bot 不能推断为「没有 MCP / 没有 Routine」。

## Mini 侧定时拉取（样例，未加载）

`multica-board/launchd/one.yic.box-config-fetch.plist` 是 Mini 上定时跑 `fetch-snapshot` 的 launchd 样例：每小时执行一次，只调用 `fetch-snapshot`（`git fetch` + 逐提交白名单/Schema 校验 + `git show`），不推送、不写仓库。它是样例文件：未经逐条批准不复制到 `~/Library/LaunchAgents`、不 `launchctl load`。

`fetch-snapshot` 退出码：`0` 已安装最新合法副本；`3` 数据分支缺失、拉取失败或仍在 `catching_up`（下轮续验）；`4` 校验拒绝（非白名单路径、schema 不符、超限、箱号不符等），本地副本保持不变并记 `fetch-status.json`；`6` 本地副本写入失败。

## 部署说明（执行前仍需逐条批准）

- Box 上 `/workspace/agents-lab` 保持干净 checkout；CLI 不自动 `git pull`，代码更新由人执行。
- Box 状态目录 `/home/box/.local/state/box-config-sync/` 权限 0700、文件 0600，由 CLI 首次运行时创建；已存在但属主或权限不符时拒绝运行（退出码 4）。Sidecar 放入其 `inbox/` 后由成功的 `collect` 消费删除。
- Box 数据 worktree（`publish-snapshot` 专用；默认 `<state-dir>/data-worktree`，设计示例路径 `/workspace/.boxsync/data-worktree` 经 `--worktree` 指定）权限应收紧为 `0700`：部署时由人核对并 `chmod 700`，本仓库不代改。
- Mini 状态目录 `~/.local/share/agents-lab/box-config-sync/` 权限 0700，只放 `fetch-snapshot` 拉回的快照副本与 `fetch-status.json` / `git-leg.json`；看板只读该目录，不启动子进程。
- 当前运维状态：快照由 Atlas PM 手动保活，Routine 与 launchd 均未配置；副本缺失或超过 36 小时（D4）时看板按 `not_collected` / `stale` 处理，不影响只读 API。

## 相关文档

- 设计稿：`multica-board/docs/box-config-sync-design.md`（§5.4 Sidecar/Routine 协议、§5.6 数据分支、§8 拆单、§9 已定事项）
- Schema 与白名单：`schemas/box-config-snapshot.schema.json`、`schemas/box-config-sidecar.schema.json`、`data-branch-allowlist.json`
- 测试：`python3 -m unittest discover -s box-config-sync/tests`
