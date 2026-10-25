# 升级 diff 审阅

本页只讲怎么读 `skill_guard.py diff` 的报告并做放行决定。快照、暂存、激活和回滚命令见 [upgrade-review.md](upgrade-review.md)。本地 delta 与上游 diff 要分开审，见 [customization.md](customization.md)。

审的是快照里的旧树和暂存里的候选树。正常升级用 `--staging`，此时 runtime 仍加载旧版。`--current-root` 只用于复核一份已经存在的树（例如回滚之后），并且不能和 `--staging` 同时使用。

## 读报告

```bash
python3 skills-governance/scripts/skill_guard.py diff \
  --snapshot <snapshot-dir> \
  --staging <staging-dir> \
  --format text \
  --gate
```

留档时把 `--format text` 换成 `--format json`。JSON 的 `schema` 是 `skill-governance-diff/v1`。

文本报告依次是：

- `Snapshot:` 本次对比的快照目录
- `Changes:` 有差异的路径条数
- `Decision: ALLOW` 或 `Decision: BLOCK`
- `Capability gate:` 能力门禁后端（没有这一行时，看 JSON 里的 `capability_gate`）
- `Signals:` 仅在阻断时出现，是整份报告的信号汇总
- 每条变更：`added` / `removed` / `modified`，路径，以及方括号里的信号；没有风险信号时显示 `no-risk-signal`

JSON 里对应 `blocked`、`signals`、`changes[]`（`root`、`kind`、`path`、`signals`、`diff`）和 `capability_gate`。每条 `diff` 最多 80 行。看到截断就打开暂存树里的原文件，不要凭半段 diff 放行。`no-risk-signal` 的变更也要扫一眼，门禁没标不等于可以不看。

带 `--gate` 时的退出码：

| 退出码 | 含义 |
| --- | --- |
| `0` | 当前门禁没有标出风险信号。这不是语义安全证明。 |
| `2` | 阻断。先逐条看完信号，不要激活。 |
| `1` | 参数、快照或文件系统错误。`activate` 在生效目录已经替换、但 changelog 写入失败时也退出 `1`。这不是通过，也不表示 live 树没变。 |

## 信号

| 信号 | 报告在说什么 | 放行前要能回答 |
| --- | --- | --- |
| `unexpected_files` | 候选里多了文件 | 这个文件属于本次上游版本，还是多出来的 |
| `unexpected_instructions` | `SKILL.md` 变了，或其他 `.md` / `.yaml` / `.yml` / `.json` / `.py` / `.sh` / `.ts` / `.js` 被修改 | 指令变化和发行说明一致 |
| `trigger_changes` | `SKILL.md` 的 frontmatter 变了，或变更行里出现 `trigger`、`activation`、`when to use`、`use when`。`name:` 和 `description:` 只在行首才算 | 新的触发条件会不会抢别的 Skill |
| `scripts` | 路径在 `scripts/` 下 | 新脚本做什么，谁会执行它 |
| `network_behavior` | 候选正文新出现 URL、`curl` / `wget`、`requests` / `urllib` / `socket`、`fetch(`、`npm install` / `npm exec`、`pip install` | 外联目标是声明过的来源，还是新的 |
| `secret_access` | 路径或新增正文命中 `.env`、api key、`access token`（含 `access_token` / `access-token`）、secret、password、credential、private key、keychain、`ssh/`。单独出现的 token 不算 | 只是文档用词，还是把秘密写进了树 |
| `adapter_paths` | 变化落在适配器根目录 | 只是适配器指针变了，还是把 runtime 入口当成源码改了 |
| `conflict_markers` | 暂存正文同时出现行首 `<<<<<<<` 和行首 `>>>>>>>`，且旧文件没有行首 `<<<<<<<` | `skill_patch.py rebase` 冲突未解决。回去处理 `<skill>.conflicts/`，不要 `--force` |
| `capability_drift` | SkilLock 判定能力面相对快照有未批准漂移 | `skills.lock` 和能力差异已经审过，并且接受这次漂移 |
| `capability_gate_error` | 版本校验通过后，`init` / `ci` 无法执行、失败或超时，或写出的 `skills.lock` 不能读成 behavior map | 先修好门禁。失败即关闭，不激活。这不是「没装 skil-lock」的 fallback |
| `capability_overreach` | 候选的 SkilLock `behavior` 超出 `SKILL.md` 的 `capabilities` 声明，或声明本身无效 | 收紧 behavior，或先改声明并完成复核。这条信号只来自已读到的 lock behavior，不是正文正则命中。未测到 behavior 时不会出现 |

`Capability gate: fallback` 表示固定 fork 二进制缺失或版本/提交校验不符（或显式 `--skil-lock fallback`）。这时用上表的风险信号做决定。缺二进制不等于通过。`Capability gate: skil-lock BLOCK` 与 `capability_drift` 相同，按能力漂移审。`Capability gate: error` 与 `capability_gate_error` 相同，不激活。

`Capability gate:` 之后还有一行声明结论：

- `Capability declaration: BLOCK`，下一行起缩进写出每个 skill 的越权或声明无效原因
- `Capability declaration: within declaration`：已经读到 behavior，且没有越权
- `Capability declaration: unmeasured`：没有可用的 lock behavior，升级报告不做声明对照。这时不会把正则命中记成 `capability_overreach`。库存审计在「没有声明」时另用既有的 `network_behavior` / `secret_access` 模式，见 [capability-declaration.md](capability-declaration.md)

## 决定

三选一。决定激活时，用 `--change-summary` 把人工结论写入 `skills-governance/changelog/<skill>.jsonl` 的 `summary`（自动摘要留在 `auto_summary`），需要上游坐标时加 `--new-ref`。不激活或门禁阻断不会写这条记录。字段见 [upgrade-review.md](upgrade-review.md) 的「激活变更日志」。

1. **不激活。** 信号对不上预期的发行说明，截断 diff 看不懂，或出现 `conflict_markers`、`capability_overreach`。冲突标记要回到 `<skill>.conflicts/` 解开，不要 `--force`。越权要先改声明或收紧 behavior。丢掉暂存目录即可，live 树不用回滚。
2. **记录理由后激活。** 每条阻断信号都能对上预期变更，且没有 `conflict_markers`。`activate` 不加 `--force` 会再跑一次门禁，阻断时退出码 `2` 并且不改 live 目录。审查确认后才加 `--force`，并带上 `--change-summary`。
3. **停。** 退出码是 `1`，或出现 `capability_gate_error`。先修命令或门禁，不要把错误当成通过。`activate` 若已替换生效目录、只是 changelog 写入失败，退出码同样是 `1`，stderr 含 `activation already applied; changelog write failed`。这时 live 树已经是新版本。

`drift_report.py` 在发现 `content_hash_mismatch`、`lock_hash_mismatch` 或 `version_mismatch`，且 changelog 里没有 `new_hash` / `new_ref` / `new_version` 对上观测值的条目时，会标 `change_unrecorded`。

`Decision: ALLOW` 且退出码 `0` 时，重要 Skill 仍要扫变更列表。门禁是保守信号，不判定恶意，也不代替人工阅读。
