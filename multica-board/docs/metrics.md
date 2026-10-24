审核后批准推送

# Agent 系统效能指标定义

状态：只读指标命令已实现；历史代理交付口径按 ACM-470 更新。第一版只采集五项：北极星、交付周期中位数、等待占比、一次通过率、每件交付花费。基线从第一条完整采集事件起算，满 14×24 小时；目标值另行拍板，实施不预设阈值。默认覆盖全部 workspace。

## 1. 共同口径

### 1.1 统计对象：交付件

- 一件交付固定按一个**父 issue**计，不拆分；正式交付须有交付台账证据；历史回补另列 `evidence=proxy` 代理交付。
- 子 issue 只作为父 issue 的执行证据，不单独计入交付件；子 issue 的 run、等待和花费归入父 issue。
- 没有父 issue 的独立 issue 自己就是交付件。
- 父 issue 有多个独立交付结果时仍算一件，不使用 `delivery_unit_id` 拆分。
- Steward 验收后关单（`done`）表示结论被采纳；周期和一次通过率仍必须有统一交付台账的结束证据。没有台账证据的 `done` 不进入正式台账指标；历史回补可按下述代理规则进入独立代理列。
- `cancelled` / `canceled` 不算交付，也不进入交付周期、一次通过率分母；若取消前已发生花费，花费进入“未交付成本”单独报告，不摊入每件交付花费。
- `backlog`、`todo`、`in_progress`、`blocked`、`in_review` 都不是交付完成。
- 跨 workspace 的 issue 各自保留 `workspace_id`。默认覆盖全部 workspace，不跨 workspace 合并同名 issue；汇总必须保留来源 workspace。
- 父子关系来自 issue 的 `parent_issue_id` / `parent_identifier`。父 issue 不在采集窗口或跨 workspace 无法确认时，标记 `parent_unknown`，不把子 issue 自动当成独立交付。

### 1.1a 历史代理口径（ACM-470）

- 父 issue 当前以 `done` 关闭可作为历史代理交付；无父的独立 issue 等同父 issue。子 issue 不单计。
- 排除 `cancelled/canceled`、验收单、复验单，以及纯推送/合并/部署单。采集时按标题识别 issue 类型，只保留 `proxy_exclusion` 原因枚举，不保存标题或正文。无法识别类型的旧快照须重新采集，不按零交付处理。
- 代理结束取完整 timeline 最后一次 `done`；重新打开后关闭仍只计一件。时间线不全或当前未关闭均不能推断完成；代理行标记 `evidence=proxy`。90 天事件裁剪前保存完整 timeline 的最后关闭时间作为最小库存摘要，避免旧单在每个新窗口误入未知候选；裁剪丢失开始证据时周期/等待仍 unknown。
- 代理周期与状态等待可计算；注意力、一次通过率、每件成本仍需原有分类、coverage 与成本台账，缺少则 unknown，不能从 done 推断一次通过。
- 台账从 PR #97 合并当天 **2026-10-24 UTC** 起正式记录。历史代理与正式台账各有分母、覆盖率和每日件数，同一件可能在两列出现，不能相加；本次不补造历史台账，也不启用实时采集或修改 Multica。
- 报告按每个 workspace 分别输出每日代理/台账件数、五项指标及覆盖率、滚动 24h 与四周日均 × 70% 的示例阈值。70% 只是读数示例，不是已批准告警策略。完整读数未知时另列已观察 24h 下界和样本日均 × 70%，不据此断言触发。无法访问的 workspace 保留 unknown。
- 使用 Atlas 后端同一只读 profile（`desktop-api.multica.ai`），不使用任务令牌；通过 Board runner 清理任务及 agent 身份变量，并在私有中立 cwd 调用 CLI，不改 profile 配置或输出 token。

### 1.2 时间边界

- 所有时间存 ISO 8601 UTC；展示可转本地时区，但计算使用 UTC。
- 交付开始 = 第一次进入 `in_progress` 的时间；没有该状态迁移时回退到父 issue `created_at`。
- 交付结束 = 统一交付台账记录的结束时间。正式台账缺证据的 `done` 记为 `completion_time_unknown`；历史代理使用完整 timeline 最后一次 `done` 时间。两者都不能用 `updated_at` 冒充结束时间。
- 当前统计窗口按结束时间归属；窗口左闭右开。窗口外开始、窗口内结束的交付仍纳入窗口，但报告标记为跨窗口。

### 1.3 数据可信度

每个结果带 `coverage`：

- `complete`：必需事件和时间齐全。
- `partial`：能计算，但存在已知缺口，例如 run 缓存只有 14 天。
- `unknown`：缺少关键事件，不能计算；显示“未知”，不显示 0。

采集失败、缓存过期、分页未完成、workspace 身份不匹配都不能转成“没有事件”。

## 2. 五项第一版指标

### M1 北极星：每件交付平均占用用户几次注意力

**定义**：

`attention_per_delivery = (approval_card_count + user_reply_count + manual_handling_count) / complete_deliveries`

分子按事件去重：同一条评论只能归一个事件；同一次用户操作不能因状态桥接、转发或巡检重复计数。

- `approval_card_count`：用户明确批准/确认某个执行范围、计划或交付结果的次数。普通“收到”“继续”只有在明确推进某个交付时才算。
- `user_reply_count`：用户针对该交付提出问题、补充约束、要求修改、确认事实或回答待决问题的次数。自动 agent 评论不算。
- `manual_handling_count`：用户在 Multica 外手动处理该交付的次数，例如手动补派、手动重跑、手动回滚、手动验收；只统计台账里有 ID 的动作。没有记录的动作标为 `unknown`，不从“用户是负责人”推断。
- 只统计能绑定到父 issue 的事件；无法绑定的用户操作放入 `unattributed_attention`，不硬分摊。
- 分母只含 `complete` 的交付件；取消件不算分母。

**来源与状态**：

- Multica CLI：`issue comment list` 可读取评论作者、时间、线程和正文，用于人工分类；**现在就能取**，但需要新建分类与去重采集。
- Multica CLI/API：issue 的 `id`、`parent_issue_id`、`workspace_id`、`status`、`created_at`、`updated_at`；**现在就能取**。
- `/api/changes` 的 `issue_status` / `issue_assignee`：只提供状态和负责人变化，不提供用户意图；**现在就能取**，只能作辅助证据。
- 巡检/转发台账：手动处理和重复转发的事实；**需要新采集**，且需统一事件 schema。
- 评论正文的稳定分类、批准卡片定义和用户身份映射：当前 Board API 白名单不输出正文和成员邮箱；**需要新采集**，建议在受限采集层只保存事件类别、actor type、issue/thread ID，不保存正文。

**计算与频率**：按事件写入后增量重算，每小时刷新；日报冻结前一日数据。历史回补后报告均值、中位数、P90，并按 workspace、agent、交付类型切片。

**易刷点与配对指标**：少问用户会降低本指标，但可能增加返工。必须和一次通过率、返工轮数、交付周期一起看；少记手动处理会虚降，故保留 `unknown/unattributed` 和采集覆盖率。

### M2 交付周期中位数

**定义**：每件完整交付的 `completion_time - start_time`，按父 issue 交付件统计；报告中位数和 P90，单位小时。

- 等待、运行、评审、返工全部计入总周期。
- 父 issue 的子 issue 时间不相加；父 issue 只有一个生命周期。
- `completion_time_unknown` 不进分布，单独报告缺失数和缺失率。
- 取消件不进分布；若需要观察取消耗时，另列“取消前时长”，不混入交付周期。
- 跨 workspace 按来源分组；跨窗口交付按结束日归属并标记。

**来源与状态**：

- issue `created_at`、`parent_issue_id`、workspace、状态、负责人：Multica CLI/API，**现在就能取**。
- `/api/changes` 的 `issue_status` 迁移和 `at`：**现在就能取**，可补首次 `in_progress`，但只保留 7 天，历史窗口不足。
- 统一交付台账的结束时间和证据：**需要新采集**。Steward 验收后 `done` 可证明结论被采纳，但没有台账结束证据不能作为正式周期结束时间；历史代理周期单列。

**计算与频率**：每小时增量更新，日报和周报按结束时间重算；`/api/changes` 和事件台账保留 90 天，避免历史断层。

**易刷点与配对指标**：晚建 issue、提前标完成会缩短周期；和北极星、一次通过率、交付完成证据覆盖率配对。没有开始/结束证据不计入，不填 0。

### M3 等待占比

**定义**：

`waiting_share = waiting_duration / delivery_cycle_duration`

其中 `waiting_duration` 只计算父 issue 在 `blocked` 或 `in_review` 的时间，并拆成：

- `waiting_user`：等待用户决定、回复、批准或手动操作；
- `waiting_quota`：等待额度恢复或 usage limit 复查；
- `waiting_agent`：等待 agent、runtime、daemon、任务队列或执行资源。

- `blocked` 按明确原因拆分；`in_review` 只有挂着“等用户批准”的请求时算 `waiting_user`，其余算 `waiting_agent`（等验收）。无法确认原因时进入 `waiting_unclassified`，不能猜成等用户。一个时间段只能归一个桶；状态变化重叠时按事件时间线去重，未知间隔不计入分子但报告缺口。

- 父 issue 统计；子 issue 的等待不重复加入父 issue。
- 交付周期未知或没有状态迁移时，指标为 unknown。
- 取消件不进第一版分母；等待时长可进入取消分析附表。
- 跨 workspace 分组，不跨 workspace 拼接状态事件。

**来源与状态**：

- `/api/changes` `issue_status` 的 `at`、前后状态、issue ID：**现在就能取**，但当前只保留 7 天；实现需扩展到 90 天。
- issue 当前 `status` / `status_category`：Multica CLI/API，**现在就能取**，只能确定当前状态，不能恢复历史时长。
- 巡检台账、quota wait ledger、Wakeup 的 `reason`、目标 agent 和复查时间：**需要新采集**。现有台账已有 quota 等待信息，但未形成稳定 Board 指标接口。
- 用户/额度/agent 等待原因：当前 issue 状态字段不含原因；**需要新采集**。替代方案是要求状态变更事件携带有限枚举 `wait_reason`，未知就保留 `unclassified`。

**计算与频率**：事件到达时更新；每小时汇总。正式基线保留 90 天状态事件，daily snapshot 只能作校验，不能替代事件日志。

**易刷点与配对指标**：把 issue 从 `blocked` 改成 `in_progress` 可压低等待占比；和总周期、运行失败率、一次通过率配对，并记录状态覆盖率。禁止把缺失事件当成无等待。

### M4 一次通过率

**定义**：

`first_pass_rate = deliveries_without_rework / complete_deliveries`

一次通过 = 第一次进入 `in_review` 后，在最终交付前没有发生用户要求修改、返工子 issue、失败后重跑导致的功能返工，且最终交付证据成立。

- `in_review` 后用户只做无内容确认、格式修正或文档链接补充，不算返工；需按事件分类记录。
- 用户提出实质修改、验收失败、重新打开、追加实现范围，算一次返工。
- agent 失败后自动恢复、没有改变交付内容，不算返工；在可靠性指标中单独标为自动恢复警告。
- 同一父 issue 多次 `in_review`：第一次之后发生实质修改即非一次通过；重复评论不能重复计数。
- 取消件不进分母；没有交付台账证据的 `done` 不进正式分母，历史代理独立统计；“没有返工记录”不等于一次通过，缺采集标记 unknown。
- 子 issue 只要是为同一交付件的返工，归父 issue；并行实现子 issue 不算返工。

**来源与状态**：

- issue 状态迁移、父子关系：Multica CLI/API 与 `/api/changes`，**现在就能取**，但历史窗口不足。
- 评论和用户验收/返工分类：Multica CLI `issue comment list`，**现在就能取**；结构化分类和去重逻辑**需要新采集**。
- run 状态、失败原因、重跑链：`agent tasks`；Board 当前 run 缓存最近 14 天，**现在就能取**但需补采集窗口与关联规则。
- 统一交付台账的结束证据：**需要新采集**，作为正式周期和一次通过率的结束证据；历史代理以完整 timeline 最后一次 `done` 结束，不能替代返工分类与覆盖声明。

**计算与频率**：每小时增量计算，日报冻结；首次只报比例、分子、分母、unknown 数和覆盖率，不设目标。

**易刷点与配对指标**：把问题归为“新需求”、不提交验收失败、拆成新父 issue 都会虚高；和北极星、周期 P90、取消率、交付件身份/父子覆盖率配对。

### M5 每件交付花费

**定义**：

`cost_per_delivery = attributable_cost / complete_deliveries`

第一版同时展示两种不能相加的成本口径：

1. `multica_cost`：Multica run 的付费 token / `cost_usd_ticks`，按 run 关联父 issue 后求和；
2. `quota_consumption`：外部订阅额度消耗比例或额度单位，按 provider / runtime 展示；若无法归因到交付件，只展示窗口总量和“不可分摊”，不伪造每件成本。

- 取消件成本进入 `cost_of_cancelled`，不进入每件交付分母。
- 失败 run 若属于交付链，成本归该父 issue；同一 run 重复记录只计一次。
- 子 issue 成本归父 issue；无父关系的 run 记为 `unattributed_run_cost`。
- 跨 workspace 不混合；跨 agent / runtime 保留明细维度。
- `multica_cost` 与 `quota_consumption` 永久分两栏，不相加。价格表放在仓库，由 Steward 维护；订阅固定费用、免费额度和付费 token 不擅自换算成同一货币。

**来源与状态**：

- `/api/usage` 的 `multica_daily.by_runtime`、运行/完成/失败/取消、token 计数、`cost_usd_ticks`：**现在就能取**；其文档明确该值保持原单位，不换算美元。
- `/api/usage` 的 `external_quotas` / `signals`：**现在就能取**，有 provider、`used_percent`、`resets_at`、`source`、状态和采集时间；它是窗口级，不等于单件花费。
- CodexBar：草案要求额度一律先用 `codexbar` 查；当前 Atlas 文档只证明有 CodexBar 额度快照来源，未证明有稳定 CLI/API 字段可按 issue 归因；**现在就能取**窗口快照，**需要新采集**逐 run / issue 归因。失败或过期时标 unknown。
- 9router：Observability 请求日志（`enableObservability` 开关）和本地数据库，**需要新采集**；只有 Observability 开启时才有数据。关闭时标为 unknown，不估算。Orca：Usage 文档标为“未接入”；**取不到**，列 provider 未接入和未分摊总量。
- run 与父 issue 关联：`agent tasks` 的 `issue_id` 和 run 状态/时间，**现在就能取**；token 字段是否每个 run 完整，需以实际采集结果核验，缺失不补估。

**计算与频率**：运行层每小时更新，按交付结束日报冻结；外部额度在每次成功快照更新。报告同时给 `attributable`、`unattributed`、`unknown`，禁止把不可归因额度平均摊给所有交付。

**易刷点与配对指标**：少跑验证、少记失败可压低成本但可能降低一次通过率和提高回归；和一次通过率、返工率、失败率、每件周期配对。低成本不代表高效。

## 3. 流速驱动

### 3.1 读数与触发

目的：用流速尺驱动 Steward 加速整体交付，不把处置动作自动化或写死。

- **主读数**：滚动 24 小时交付件数，严格按本文件交付件口径；正式列只计有交付台账结束证据的完整交付；历史代理列独立展示。
- **诊断读数**：在制品数（当前正在推进的父 issue 数）；等待占比，拆成 `waiting_user`、`waiting_quota`、`waiting_agent`；另报返工和覆盖率，防止用少交付或少记录刷主读数。
- **触发条件**：主读数低于阈值。阈值由历史回补基线决定，例如过去 4 周日均值的 70%；用户已于 2026-10-24 批准每天 **4.5 件**（代理基线日均 6.43 的 70%）；默认配置 `backend/multica_board/flow_config.json` 保存阈值、出处和 3 小时缓存有效期，可用 `--config` 指定替代配置。台账、代理各自比较，禁止相加。
- **运行频率**：白天每 2–3 小时由 Steward 例行检查读取一次，日报记录是否触发、诊断结果、采取动作和结果；不在本文授权自动派活、换 agent 或改 issue。

### 3.2 处置映射

| 诊断结果 | Steward 处置建议 |
|---|---|
| 在制品太少 | 从 backlog 和 roadmap 补派；先核对批准、负责人、活动 run、额度和权限。 |
| 卡在等用户 | 把待批事项合并成一张编号批准，减少用户注意力占用。 |
| 卡在等额度或 agent | 先用 `codexbar` 核对额度，换到已有且合适的 agent，或拆小并行；不改绑定、凭据或模型配置。 |
| 返工多 | 修验收标准和 issue 描述，保留返工原因和改动证据。 |

处置必须遵守既有授权、容量、workspace 边界和安全规则；读数触发不等于动作授权。

### 3.3 数据来源

- 交付件、父子关系、状态和负责人：Multica CLI 的 issue / workspace 读取，或 Board `/api/issues`；**现在就能取**。
- 滚动 24 小时交付件：交付台账事件；历史回补阶段用已有 issue `done` 与可确认结束事件，符合代理规则的 `done` 在独立代理列统计；实时阶段由 `delivery_completed` 事件更新。
- 在制品：Multica CLI issue 状态，或 Board `/api/issues` 的 `status` / `status_category` / `parent_identifier`；**现在就能取**，按父 issue 去重。
- 等待占比：Board `/api/changes` 的 `issue_status` 事件，加巡检、quota wait、Wakeup ledger 的 `wait_reason`；状态流转可取，原因需新采集，缺失标 `unknown`。
- 历史基线：Multica 现有 issue 状态流转、运行记录和评论，尽量最近 4 周；没有历史来源的台账、额度归因、`wait_reason` 标 `unknown` 并单报 coverage。

### 3.4 对账

实时采集接入后连续运行 2 天，按相同父 issue、事件类型、时间窗口和去重 ID 对比历史回补结果。日报列出匹配数、仅回补、仅实时、时间差和口径差异；差异不得静默覆盖，未解释差异保留 `unknown`。

## 4. 数据采集与实现边界

### 4.1 现有可复用接口

- Multica CLI：issue、comment、agent tasks、workspace、runtime；具体字段以 CLI `--output json` 实际返回为准。CLI 是读取事实的来源，不假设存在未文档化的成本或交付接口。
- Atlas 只读 API：`/api/issues`、`/api/issues/{workspace_id}/{issue_id}/runs`、`/api/changes`、`/api/usage`。当前契约规定只读，只有 GET/HEAD，没有立即刷新或写入指标接口。
- `/api/changes` 当前保留 7 天；`/api/issues` run 缓存默认 14 天。实现需让 `/api/changes` 和事件台账保留 90 天；扩展前的历史缺口不可回补。
- 巡检、quota wait、Wakeup ledger：可作为原因和人工处理的证据，但必须先定义脱敏、ID、时间、幂等键和保留期。
- CodexBar：只读额度快照；不得读取 auth 文件、token、cookie 或密钥。

### 4.2 必须新增的最小事件

建议 Builder 在采集层新增统一事件，不把正文或秘密写进 Board：

`delivery_id, parent_issue_id, workspace_id, event_id, event_type, actor_type, actor_id_hash, at, source, confidence, related_run_id, wait_reason, delivery_evidence, schema_version`

- `event_type`：`delivery_started`、`delivery_completed`、`delivery_adopted`、`approval`、`user_reply`、`manual_handling`、`rework`、`wait_open`、`wait_close`、`cost_observed`。
- `event_id` 做幂等去重；`source` 区分 CLI、API、patrol ledger、CodexBar、9router。
- `actor_id_hash` 只用于区分用户/agent，不存邮箱或正文。
- 事件未知、冲突、来源过期时保留记录并置 `confidence=unknown`，不补猜。

## 4. 给 Builder 的第一版实施拆分建议

只写建议，不创建 issue；顺序可调整，但每步先有读回和覆盖率验证：

1. **历史回补交付件归并层**：读取全部 workspace 的最近 4 周 issue、父子关系、状态流转、运行记录和评论；生成稳定 `delivery_id`，处理取消、跨 workspace、缺父 issue；缺台账时可用独立代理列；没有历史来源的分类、额度归因、`wait_reason` 标 `unknown`，输出覆盖率，不改变 Multica 数据。
2. **补齐交付事件**：从 issue 状态、评论线程和可用交付证据采集开始、采纳、批准、回复、返工事件；统一结束仍以交付台账为准，只存脱敏枚举和 ID，为人工分类保留可复核引用，不存正文。
3. **建立等待事件**：接入 `/api/changes` 与巡检/quota ledger，形成 `wait_reason` 的 user/quota/agent/unclassified 四类；处理事件重叠、历史回补、实时增量和幂等。
4. **建立运行与成本归因**：关联 `agent tasks` run 到父 delivery，接入 `/api/usage` 的 token / `cost_usd_ticks`、CodexBar 快照和 9router Observability（仅在 `enableObservability` 开启时采集）；逐项标记 attributable / unattributed / unknown；Orca 保持未接入。
5. **实现流速驱动读数**：计算滚动 24 小时交付件数、在制品数和等待/返工诊断；读取接口复用 `/api/issues`、`/api/changes`、`/api/usage`、CLI 和台账。用历史回补基线提供阈值参数，不写死阈值；支持白天每 2–3 小时检查和日报记录，处置仍由 Steward 执行。
6. **计算五项指标**：实现中位数/P90、等待分桶、一次通过判定、注意力分子和成本分层；先跑历史回补，再接入实时采集运行 2 天对账，输出匹配数、仅回补、仅实时、时间差、口径差异、分子、分母、unknown、覆盖率及按 workspace/agent/provider 的切片。
7. **加反刷校验与验收样本**：覆盖父子 issue、取消单、全部 workspace、缺失台账结束证据、重复评论、重复 run、quota wait、失败重跑、9router Observability 开关关闭、历史回补与实时对账、缓存过期；任何采集失败不得渲染为 0 或“无”。

## 5. 已决定

1. 一件交付固定按父 issue 算，不拆分。
2. 开始时间取第一次进入 `in_progress` 的时间，没有就用 `created_at`。
3. 结论是否被采纳，以 Steward 验收后关单（`done`）为准；不要求用户另外确认。
4. 手动处理只统计台账里有 ID 的动作，没有记录的标为 `unknown`。
5. `in_review` 只有挂着“等用户批准”的请求时才算 `waiting_user`，其余算 `waiting_agent`（等验收）。
6. 正式结束证据以交付台账为准；历史回补按父 issue `done` 作代理，`evidence=proxy`，与正式台账分列，不能相加。
7. 失败后自动恢复的运行不算返工，但在可靠性那组里单独标出警告。
8. `multica_cost` 和 `quota_consumption` 永久分两栏，不相加。价格表放在仓库里，由 Steward 维护。
9. 基线用历史数据回补：从 Multica 现有历史读取 issue 状态流转、运行记录和评论，尽量取最近 4 周。没有历史来源的台账、额度归因、`wait_reason` 等标为 `unknown`，单独列覆盖率。接入实时采集后，再运行 2 天对账，核对回补和实时采集口径；不一致处单独说明。
10. 默认覆盖全部 workspace。
11. 2026-10-24 用户已批准流速阈值 4.5 件/天；其它目标值仍待单独批准。
12. `/api/changes` 和事件台账保留 90 天，不是 30 天。


## 6. ACM-486 轻量巡检与两天实时对账

### 6.1 白天每 2–3 小时读取

从 `multica-board` 目录运行：

```sh
PYTHONPATH=backend python3 -m multica_board flow-check --json
# 已有完整历史快照和正式台账时：
PYTHONPATH=backend python3 -m multica_board flow-check --json --snapshot baseline.json --ledger ledger.json
```

默认读取 `~/.multica-board/board.sqlite` 的一致性只读事务（SQLite mode=ro），不创建 Store、不迁移、不刷新、不调用 CLI/网络。SQL 运行时间预算 45 秒；最终实跑 8.702 秒（首次约 9 秒）。只计算 24h，不展开四周/逐 workspace 报表。`--db`、`--profile` 选择既有缓存；缺文件不会创建数据库。JSON `null` 统一表示 unknown，缺数时 `below_threshold` 也为 null。

台账/代理复用已有交付、父子归并、取消及代理类型排除规则。Board 缓存只有轮询观测时间，不具备完整 timeline，不能用最后观测 done 或 updated_at 冒充代理完成时间；没有新鲜完整快照时交付数保持 unknown。完整快照输入可给出确定值，超过配置时效仍为 unknown。

提速线索按父 issue 计：`in_progress_issues` 是推进中 issue 数，`queued_issues` 是 todo 数，均不是活动 run 数。`backlog_issues` 只是库存；尚无结构化授权/依赖/容量/可派证明，`dispatchable_backlog_issues` 保持 unknown，不能把全部 backlog 宣称可派。明确 `wait_open(waiting_user)` 只证明等用户（另列 `waiting_user_issues`），不能区分问答、手工处理和待审批；未分类的 blocked/in_review 或有用户等待但无审批分类时，待审批总数 unknown。返工按过去 24h 有完整 rework coverage 的父交付去重；无 coverage 时不把无记录算作零。

本轮缓存实跑（2026-10-24T15:30:54.694130Z）：台账交付 unknown、代理交付 unknown、两栏是否低于 4.5 均 unknown；推进中 13、todo 88、backlog 26；待审批、可派 backlog、24h 返工均 unknown。命令完成只代表读取成功，不表示数据完整。

### 6.2 已启动的观测窗口

- 窗口：**2026-10-24T15:15:04.892402Z — 2026-10-26T15:15:04.892402Z**（SGT：10 月 24 日 23:15 至 26 日 23:15），左闭右开。
- 已创建一次性平台 wakeup `01a16e7d-63f5-7ef7-85d0-6aa402d7ad73`，2026-10-26T15:16:00Z（SGT 23:16）唤醒本 issue 复核，避免本轮退出后遗留任务进程。
- 复用已运行 Board 的 90 天 `issue_status` 变更日志，不启动隐藏后台进程、不改运行服务或 collector 配置。起点已导出 392 条最近 48h 脱敏状态观测，inventory_complete=true，来源状态 partial。起点文件只作审计锚点，不当作窗口内事件。
- 本 issue workdir 保存 `reconcile-window.json`、`reconcile-start.json`、`reconcile-backfill-start.json`；这些是本地私有工作材料，不是交付链接，不提交原始数据。
- **这是状态观测的两天对账启动，不是正式台账实时接入或两天验收通过。** 正式交付/返工/审批台账未接入；Board 初次基线不产事件，轮询可能漏掉两次采集间的往返迁移。Board `board-status:<seq>` 与 timeline 原生事件 ID 不同，现有 reconcile 将报告 only/unknown，不凭时间相近自动合并或伪造匹配。

两天后，在同一专属 worktree，从 `multica-board` 运行（文件位置可换为自己的私有目录）：

```sh
PYTHONPATH=backend python3 -m multica_board metrics-realtime --snapshot ../../reconcile-end.json \
  --since 2026-10-24T15:15:04.892402Z --until 2026-10-26T15:15:04.892402Z
PYTHONPATH=backend python3 -m multica_board metrics-backfill --snapshot ../../reconcile-backfill-end.json --workers 8
PYTHONPATH=backend python3 -m multica_board metrics-reconcile \
  --backfill ../../reconcile-backfill-end.json --realtime ../../reconcile-end.json --event-type status \
  --since 2026-10-24T15:15:04.892402Z --until 2026-10-26T15:15:04.892402Z
```

`metrics-realtime` 只读既有 Board 并导出近 48h 脱敏观测；复核明确传入 `--since/--until`，不受默认 48h 滑动窗口影响（日志仍须在 90 天保留期内）。全量 backfill 只在起点/终点各跑一次，不纳入频繁巡检。以回补 `status` 子集和实时状态事件对账，评论等非共同类型另列“未接入”，不要把它们误判为状态采集丢失。

复核时把以下实际结果追加本节：matched、backfill_only、realtime_only、conflicting_ids、differences 的时间差/字段差、workspace/父子覆盖率、源新鲜度/采集中断、不可比较事件类型数、未接入正式台账。ID 不同、未知时间或未解释差异继续 unknown；在缺少证据的情况下不作“对账通过”判断。未来需要真实事件身份映射与正式 ledger 采集才能完成全口径验收。


起点实测结果（2026-10-24）：全量回补完成，`complete=true`、845 issues、2,544 runs、5,617 events、errors=0。完整采集不等于完整结束证据：24h 代理候选 56 件中 45 件有证据，11 件缺结束证据，因此代理总数及触发判断仍 unknown，不能把观察到的 45 当作完整总数；正式台账未接入，同样 unknown。

已用现有 `metrics-reconcile --event-type status` 对起点之前 48h（2026-10-22T15:15:04.892402Z 至 2026-10-24T15:15:04.892402Z）做一次基线对账：matched=0、backfill_only=629、realtime_only=392、conflicting_ids=0、invalid_timestamps=0、differences=[]、status=unknown。两端没有共同 ID，故没有可比较的时间差，不能把 differences=[] 解读成一致；这组数是起点校验，不是两天窗口终点结果。终点仍按上文固定窗口执行。

验证：`PYTHONPATH=backend python3 -m unittest discover -s backend/tests` 全量通过；`git diff --check` 通过。仅 CLI/后端和文档改动，无 Web 操作路径变更。
