# G1 runtime 计划器契约 v1.2 草案（ACM-279）

状态：v1.2 待调度安排独立审阅；替代未定稿的 v1.1。依据 ACM-231 v2 §3–6/9–10、UI v1.1 §7。仅 G1；本地实现与测试不表示契约已确认，不以前端接入为完成条件。

## 实现计划与边界

1. 独立 `runtime_plan` 命令逐个读取显式选择的 agent，使用现有 `CliRunner`（argv、隔离 cwd、剥离 task credentials、只读白名单）；不用 Board 缓存，不切 workspace/profile，不启动模型探测。
2. `plan_contract` 定义 schema 1、原始字段 diff、保守决策与 JCS hash；`plans` 负责私有文件读写和两个 GET。HTTP 不触发采集或生成文件。
3. 验证 null/空字符串、作用域、活动/未知、完整性、hash 篡改、重复 JSON 键、权限/符号链接/超限及 HTTP 写方法拒绝，再运行现有后端测试。

实际 CLI 没有已证明的身份/修改权/runtime 使用权/模型目录/兼容检查接口，也没有已证实的 tasks 快照分页协议。因此 schema 1 只生成 `review_only`，actor 为声明且 `verified=false`；活动完整性固定 UNKNOWN，权限/能力/兼容 UNKNOWN，无 APPROVABLE 行。四桶保留 APPROVABLE、BLOCKED、SKIP_ACTIVE、NO_CHANGE，但不能以客户端自报 PASS 解锁。已有活动优先 SKIP_ACTIVE，其余按所有缺失证据 BLOCKED；阻断优先于无变化。未来补证须新 policy/schema 审阅，不能自动升级旧计划。

## 命令

从 `multica-board/backend` 运行（在用户自己的已授权普通终端；不在任务环境借用宿主凭证）：

```sh
python3 -m multica_board.runtime_plan \
  --server https://api.multica.ai \
  --actor member:11111111-1111-4111-8111-111111111111 \
  --on-behalf-of 11111111-1111-4111-8111-111111111111 \
  --workspace-allow 22222222-2222-4222-8222-222222222222 \
  --agent 22222222-2222-4222-8222-222222222222:33333333-3333-4333-8333-333333333333 \
  --target-runtime 44444444-4444-4444-8444-444444444444 \
  --model omit --effort clear --tier set:default
```

- schema 1 每份计划恰好一个 workspace。`--workspace-allow UUID` 必须且只能提供一次（即便重复相同 UUID 也拒绝）；仅 `--agent WORKSPACE_UUID:AGENT_UUID` 可重复；无 all-workspaces、slug、隐式筛选或通配符；重复/越界/非法 UUID 拒绝。每行选中范围必须存在于 allow-list；UUID 小写。最多 1000 行。
- `--target-runtime UUID` 必填，同一个具体注册 UUID；跨 workspace 不自动映射同名 runtime。跨 workspace 选择由组装页拆成多份单 workspace 计划、各一条命令，不在同一份计划映射多个目标。目标查不到/不匹配时阻断。
- `--model/--effort/--tier` 默认 `omit`，可为 `omit|clear|set:<非空且无首尾空白的值>`。`clear` 精确定义为新值 `null`；`omit` 原样保留旧值；不自动沿用、降级或补默认。
- `--server` 必填，HTTPS origin（HTTP 仅 IP loopback），去默认端口、host 小写、无凭证/query/path。直接注入 CliRunner 的固定 server 参数；不信任本机配置 URL。`--actor type:UUID`、`--on-behalf-of UUID` 必填，但 G1 不将声明当已认证事实。
- `--snapshot FILE` 可选：离线读取下述最小 snapshot，强制 historical，绝不冒充 live。未提供则读取 CLI。`--plan-dir DIR` 默认 `~/.multica-board/plans`；`--ttl-seconds` 默认 600、范围 1..600。无任意 CLI 参数透传、无 profile 参数、无 POST/apply/approve/rollback。
- CLI 只允许 workspace list、agent list、runtime list、agent tasks。每个选中 agent 读一次 tasks `--limit 200`，不以短页/裸数组/缺 next_cursor 推断穷尽，不盲翻页；pages_read/tasks_seen/runs 留下实际观察。重复任务 UUID 去重；未知状态、读取失败/403/404 均保守 UNKNOWN；从不将失败解释成空闲。
- snapshot 形状：`{server_identity, observed_at, workspaces:[{id,name}], agents:[{workspace_id,id,name,archived,runtime_id,model,thinking_level,service_tier}], runtimes:[{workspace_id,id,name,provider,status,last_seen_at}], tasks:[{workspace_id,agent_id,pages_read,runs:[{id,status,created_at}]}]}`。缺旧字段用 `old_known=false`；不等价于显式 null。忽略非白名单原文，不输出 secrets、配置、任务内容或 stderr。

## 文件与 hash

目录 0700，计划 `<plan_id>.json` 0600、当前 UID 所有，禁止符号链接、硬链接多引用、非普通文件；单文件最大 2 MiB。原子发布、不覆盖既有计划。读取用目录/文件描述符与 NOFOLLOW；GET 不创建目录、文件或修权限。没有自动删除/保留策略。

文件 envelope：`{payload, hash_algo:"sha256-jcs-schema1", plan_hash:<64小写hex>}`。hash = SHA-256(JCS(payload))；响应附 `canonical_payload_b64`（精确 canonical UTF-8 bytes 的 Base64）。schema 1 是 [RFC 8785 §3.2](https://www.rfc-editor.org/rfc/rfc8785) 的受限输入域：只允许字符串、null、boolean、±(2^53−1) 内整数、数组与对象；拒绝浮点数、NaN/Infinity、孤立 surrogate、重复键、未知字段/schema、重复 agent。对象键按 UTF-16 排序；无 Unicode 归一化。不是使用普通 Python sort_keys 冒充 JCS。

payload 字段（全部入 hash，无显示值拼接）：

| 字段 | 形状/语义 |
|---|---|
| schema_version / policy_version / mode | `1` / `g1-v1` / `review_only` |
| plan_id | UUID，与文件名一致 |
| server_identity | 规范化 origin |
| actor / on_behalf_of | `{type:member或agent,id:UUID,verified:false}` / 人类 UUID |
| workspace_allow_list | `[{id:UUID,name:string或null}]`，长度必须恰好为 1；非数组、空数组及多项均拒绝 |
| selected_agents | `[{workspace_id,agent_id}]`，按二元 UUID 排序且唯一，与 rows 完全相同 |
| generated_at / expires_at | UTC 秒精度 `YYYY-MM-DDTHH:mm:ssZ`，TTL ≤600 秒 |
| snapshot_kind / selection_reason | `live_cli|historical` / 固定无敏感摘要 |
| allowed_fields | `[runtime_id,model,thinking_level,service_tier]` |
| rows / counts | 下表 / `{APPROVABLE,BLOCKED,SKIP_ACTIVE,NO_CHANGE}`，四数之和等于 rows 长度 |

每行：`workspace_id,agent_id,agent_name,archived,observed_at,source_version,source_fingerprint,source_runtime,target_runtime,fields,activity,checks,decision,reasons,impact`。`source_runtime/target_runtime` 为 null（未知）或 `{id,name,provider,status,last_seen_at}`，只含计划时同 workspace 对应注册的元数据，不从当前 Board 快照填补。

- `archived` 为 `boolean | null`。只有观察到布尔字段或明确的 archived_at 才确定归档状态；agent 缺失、读取失败、字段未返回均为 null，前端显示“未知”，不得隐含为 false。
- `fields` 四字段均为 `{op:omit|set|clear,old:string或null,new:string或null,old_known:boolean}`。**计划接口豁免 api-contract.md 的空字符串转 null 约定**，old/new 的 `""` 与 `null` 原样区分；未知旧值用 `old_known=false` 表示。clear 新值 null，不推断实际继承值。
- 逐字段变化判定为 `old_known && old !== new`（本 schema 仅 string/null，严格区分 null 与空字符串）；未知旧值显示“未知”，不能算已知无变化。已知旧值相等即无变化，即使 op=set/clear；op 本身不代表值已改变。只有四字段都 old_known 且 old===new 才是全字段无变化；阻断优先，不能以无变化解除缺失预检。
- `source_version=null`（当前无受支持版本契约）；fingerprint 是四字段 `{字段:{value:old,known:old_known}}` 的 JCS/SHA-256，仅指纹，**不是 CAS**。
- `checks` 五项 `activity,permission,capability,compatibility,health`，每项 `{status:PASS|FAIL|UNKNOWN,source,observed_at}`。来源为固定无敏感协议说明，不拷贝 CLI 原文。
- `activity={pages_read,tasks_seen,completeness:"UNKNOWN",source,observed_at,runs:[{status,created_at}]}`。仅状态/时间，无任务内容；任务 ID 仅内部去重。`pages_read=0` 表示任务读取失败、被拒或响应不可用，不表示没有任务。live_cli 的健康只依据明确 online 及 ≤60s heartbeat，仍不证明认证/额度。historical 的 health 固定 `status=UNKNOWN,source=historical_snapshot`，无论原状态和 heartbeat 时间如何；validate_payload 强制校验，历史证据不转成当前 PASS 或 FAIL。
- `impact` 固定风险文字数组；不暴露 session/workdir 路径或配置。
- 历史与当前快照不混合；GET 仅返回文件中快照，不重读源值。Atlas 当前缓存差异仅前端提示，不能进入 hash 或执行判断。过期也保留，`expired` 是响应时钟提示，hash 不证明真实性或授权。

## meta 的 API origin

`GET /api/meta` 新增 `server_identity: string | null`：serve 启动时尽力从选中 profile 的隔离只读 CLI `auth status` 输出中读取唯一 `Server:` 行，并规范化 API origin。它是启动时的观察值，不是持续验证的采集路由证明；不从 profile 名、web app 地址或浏览器 origin 猜测，不返回用户行、不读凭证文件、不修改 profile。命令失败、输出格式变化、缺失/重复/非法 Server 行时返回 null，serve 照常启动并显示缓存/原有采集错误。采集器保持现有 profile 路由行为，无论 origin 是否确认都不为采集请求新增固定 `--server-url`。前端在 null 时使用 `<server>` 占位并标黄，不能猜地址。直接构造未配置 Views 时该字段也为 null。`--actor`/`--on-behalf-of` 仍必填，meta 不提供或推定它们。独立计划器的显式 `--server` 参数不受此展示规则影响。

## 两个 GET

`serve --plan-dir DIR` 指定读取目录；默认与命令一致。沿用现有 Host/Origin/loopback、no-store、CSP；只 GET/HEAD，其余 405。

`GET /api/plans` 无参数，全量，不静默分页；响应 `{generated_at:null,items,complete,directory_error}`。generated_at 的通用采集轮次语义在此不适用；每份计划有自己的 generated_at。目录不存在返回空列表且 complete=true；权限/读取失败返回 complete=false 和固定错误码。上限 1000 个 UUID 文件；超过时先按 UUID 字典序截取前 1000 份，再按生成时间展示，不是最新的 1000 份。返回 complete=false、directory_error=directory_limit，明确不是全量，可能缺少最新计划。

每个 item：`plan_id,generated_at,expires_at,mode,schema_version,server_identity,snapshot_kind,observed_at,workspace_allow_list,counts,agent_count,plan_hash,hash_verified,parse_error,expired`。snapshot_kind 来自 payload，observed_at 为各行共有的快照观察时间（校验器要求各行相同），不是计划生成时间或响应时间。不可读/未知 schema/解析失败也列出 UUID 条目，除 plan_id 和 parse_error 外字段 null，hash_verified=false。hash 不符但 schema 合法可列摘要，parse_error=null、hash_verified=false；不得渲染行表。默认 generated_at 倒序，同时间按 ID；无时间排最后。读取时消失保留 read_error 项。

`GET /api/plans/{plan_id}` 无参数；成功 `{generated_at:null,plan,hash_verified,hash_algo,canonical_payload_b64,gates,apply_enabled:false,expired}`。`plan` 是完整 envelope。`gates={g1:{activity,permission,capability,compatibility,health},g2:{approval_claim,commit_fence,cas_activity_guard,operation_attribution}}`，G1 值 PASS/FAIL/UNKNOWN 按行聚合（FAIL 优先，全部 PASS 才 PASS），G2 固定 `absent`。即便输入试图自报 G2 PASS 也不接受未知字段。schema 合法时，无论 hash 是否一致都返回 canonical_payload_b64。前端用 WebCrypto 对 Base64 解码后的精确字节做 SHA-256；只有前端复算结果等于 `plan.plan_hash` 且 `hash_verified=true` 才渲染行表，任何不一致都按 hash 不符处理。hash 不符返回 200 + false，供审阅页阻止渲染及复制原始 JSON；不可解析/超限/权限异常返回 422 `plan_unreadable`（message 为固定下表代码）。不存在 404、非法 UUID/查询参数 400。GET 绝不写计划，也不提供生成接口。

文件错误码：`permissions`（目录/文件 owner、mode、类型、链接异常）、`too_large`、`invalid_json`（重复键/非有限/Unicode）、`invalid_schema`（字段/版本/UUID/UTC/scope/count/决策/hash envelope 格式异常）、`read_error`。错误不含路径/原始异常。未知 schema 不做尽力解析。

## 决策/原因枚举

| decision 或 reason | 中文 |
|---|---|
| APPROVABLE | 可批准（当前 policy 不产生） |
| BLOCKED | 阻断 |
| SKIP_ACTIVE | 跳过·活动 / 存在活动任务 |
| NO_CHANGE | 无变化（当前缺预检证据时仍阻断） |
| BLOCKED_SOURCE | 源字段不完整 |
| BLOCKED_ARCHIVED | 已归档 |
| BLOCKED_TARGET | 目标不可确认 |
| BLOCKED_ACTIVITY_COMPLETENESS | 活动未穷尽 |
| BLOCKED_PERMISSION | 权限未证实 |
| BLOCKED_CAPABILITY | 能力未证实 |
| BLOCKED_CONFIG_COMPATIBILITY | 配置兼容未证实 |
| BLOCKED_HEALTH | 目标健康未证实 |
| BLOCKED_HISTORICAL | 历史快照仅供审阅 |

未来未知码前端原样展示，不自动映射成通过。G2 未批准/未实现，批准消费、提交栅栏、CAS/活动事务保护、归因/未知终态均未验证；apply 始终关闭。CLI 读取成功不是写权限证明；计划 hash 不是批准。
