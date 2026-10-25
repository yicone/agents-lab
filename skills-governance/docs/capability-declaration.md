# Skill 能力声明（ACM-158）

高危 Skill 的权限以 `SKILL.md` frontmatter 里的 `capabilities` 为契约，以 SkilLock 写入 `skills.lock` 的 `behavior` 为观测。越权是这两者的差，不是另一套关键字扫描。

SkilLock 已经是 `skill_guard` 的能力漂移后端（ACM-128）。本页只增加声明与那份 behavior 的对照，以及未复核高危 Skill 的隔离。shell、URL、路径仍由 `skil-lock init` 提取。

## Frontmatter

```yaml
---
name: example-skill
capabilities:
  network: false
  secrets: false
  filesystem: none
---
```

三个键都对齐 SkilLock 的 behavior 字段：

| 声明 | 取值 | 对照的 SkilLock 字段 |
| --- | --- | --- |
| `capabilities.network` | `false`、`true`，或 host glob 列表 | `network_urls`；另外 `shell_commands` 里的 `curl`、`wget`、`fetch`、`nc`、`ssh`、`scp`、`sftp`、`httpie`、`aria2c` |
| `capabilities.secrets` | `true` 或 `false` | `file_reads` / `file_writes` 是否命中 SkilLock 默认 `protected_paths`（`.env`、`.env.*`、`.netrc`、`.ssh/**`、`*.pem`、`*.key`、`secrets/**`、`*secret*`、`*credentials*`、`id_rsa`、`id_ed25519`） |
| `capabilities.filesystem` | `none`、`read` 或 `write` | `file_reads`、`file_writes`，以及 `allowed_tools` 里的 `Read` / `Write` / `Edit` / `NotebookEdit` |

`network` 列表的写法和 SkilLock `allowed_domains` 一样：精确 host，或 `*.example.com` 这种后缀 glob。`true` 才表示任意网络。host 列表不授予网络类 shell 动词。

`filesystem: write` 包含读。`bundled_scripts` 仍由 ACM-128 的漂移门禁负责，不映射成第四个声明键。

省略整个 `capabilities` 时，网络和密钥按拒绝计，文件系统写也按拒绝计，只读不单独构成越权。一旦写了 `capabilities:`，缺省的子键是 `network: false`、`secrets: false`、`filesystem: none`。未知键或非法取值是声明无效，升级门禁直接阻断。

## 越权

`skill_guard diff --gate` 和未加 `--force` 的 `activate` 在 SkilLock 漂移检查之外，再读候选树的 `skil-lock init` 产物。观测超出声明时信号为 `capability_overreach`，退出码 `2`，不激活。`capability_gate.blocked` 仍只表示相对快照的漂移或调用失败；声明结果在 `capability_gate.declaration`。

没有可用的 `skills.lock` behavior 时，升级门禁把声明对照记为 `unmeasured`，不把缺二进制当成通过，也不把正文正则命中记成 `capability_overreach`。二进制已经写出 lock、但这份 lock 读不下来（不是 behavior map，或再读就会丢掉观测）时，门禁是 `capability_gate_error`，`diff --gate` 和未加 `--force` 的 `activate` 都阻断。这和本机没有 `skil-lock` 的 fallback 不是一回事。

库存审计多一条：无法测量且 `SKILL.md` 没有 `capabilities` 时，用 `skill_guard` 既有的 `network_behavior` / `secret_access` 模式扫描该 skill 的 `SKILL.md` 和 `scripts/`。命中则记为高危。这仍不是越权。已经写了 `capabilities`（包括显式拒绝）的 skill 不走这条路径。误报用声明收口，不另加例外词表。

审计命令：

```bash
python3 skills-governance/scripts/capability_contract.py --format text
python3 skills-governance/scripts/capability_contract.py --check
python3 skills-governance/scripts/capability_contract.py --check
```

`--check` 在越权、声明无效，或下面的隔离条件成立时退出码为 `2`。命令不修改 Skill、库存或 lock。

## 隔离与禁止提升

高危是下面任一情况：

- 声明了网络（`true` 或非空 host 列表）、`secrets: true` 或 `filesystem: write`
- 已测到的 SkilLock behavior 出现网络、受保护路径或文件写
- 无法测量，且没有 `capabilities`，但 `SKILL.md` 或 `scripts/` 命中既有的 `network_behavior` / `secret_access` 模式

人工复核完成是库存字段 `review_time` 有时间戳。高危且 `review_time` 为空时，必须已经是下面两种既有状态之一，不能另起状态名：

- `status: quarantined`
- `customization_mode: policy_exclusion`（仍要按 [customization.md](customization.md) 写 `kind: policy` 约束）

这两种状态都禁止提升为全局可用。已复核的高危 Skill 可以保持 `active`，再按 `skills-promote-global` 决定是否提升。

```bash
python3 skills-governance/scripts/capability_contract.py --promote <skill> --check
```

`--promote` 在该 Skill 被隔离、越权，或高危且未复核时退出码为 `2`。只把隔离状态写进库存、不带 `--promote` 时，`--check` 不会因为“已经隔离”而失败。

默认使用 README 指定的 fork 提交目录，`SKIL_LOCK_BINARY` 或 `--skil-lock /absolute/path` 可覆盖路径，但不能绕过版本/提交校验。`--skil-lock fallback` 显式保留未测量模式。JSON 的 `backend.reason` 和文本 Backend 行记录回退原因。
