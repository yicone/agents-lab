审核后批准推送

# Agent 系统效能指标定义

状态：指标口径草案，等待用户拍板后实现。第一版只采集五项：北极星、交付周期中位数、等待占比、一次通过率、每件交付花费。先积累两周基线，再定目标值；本文件不预设目标阈值。

## 1. 共同口径

### 1.1 统计对象：交付件

- 一件交付 = 一个**父 issue**完成一次可识别的最终交付：最终合并或上线，或结论被用户/负责人明确采纳。
- 子 issue 只作为父 issue 的执行证据，不单独计入交付件；子 issue 的 run、等待和花费归入父 issue。
- 没有父 issue 的独立 issue 自己就是交付件。
- 父 issue 有多个独立交付结果时，仍算一件；若用户明确拆成多个验收目标，需在采集台账中登记 `delivery_unit_id`，再按验收目标计件。没有该登记时不擅自拆分。
- `done` 不是交付证明。必须有合并/上线记录，或用户/负责人采纳结论的记录。
- `cancelled` / `canceled` 不算交付，也不进入交付周期、一次通过率分母；若取消前已发生花费，花费进入“未交付成本”单独报告，不摊入每件交付花费。
- `backlog`、`todo`、`in_progress`、`blocked`、`in_review` 都不是交付完成。
- 跨 workspace 的 issue 各自保留 `workspace_id`。默认按 workspace 分组，不跨 workspace 合并同名 issue；跨 workspace 汇总只在明确选择“全部 workspace”时进行，并保留来源 workspace。
- 父子关系来自 issue 的 `parent_issue_id` / `parent_identifier`。父 issue 不在采集窗口或跨 workspace 无法确认时，标记 `parent_unknown`，不把子 issue 自动当成独立交付。

### 1.2 时间边界

- 所有时间存 ISO 8601 UTC；展示可转本地时区，但计算使用 UTC。
- 交付开始 = 父 issue `created_at`，除非后续采集到明确的首次进入 `in_progress` 时间；两者都存在时保留两者，默认用首次进入 `in_progress`，没有状态迁移证据才回退到 `created_at`。
- 交付结束 = 合并/上线时间，或采纳事件时间。仅有 `done` 状态时，暂记为 `completion_time_unknown`，不纳入周期分布，不能用 `updated_at` 冒充结束时间。
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
- `manual_handling_count`：用户在 Multica 外手动处理该交付的次数，例如手动补派、手动重跑、手动回滚、手动验收；必须由巡检/交接台账记录，不从“用户是负责人”推断。
- 只统计能绑定到父 issue / `delivery_unit_id` 的事件；无法绑定的用户操作放入 `unattributed_attention`，不硬分摊。
- 分母只含 `complete` 的交付件；取消件不算分母。

**来源与状态**：

- Multica CLI：`issue comment list` 可读取评论作者、时间、线程和正文，用于人工分类；**现在就能取**，但需要新建分类与去重采集。
- Multica CLI/API：issue 的 `id`、`parent_issue_id`、`workspace_id`、`status`、`created_at`、`updated_at`；**现在就能取**。
- `/api/changes` 的 `issue_status` / `issue_assignee`：只提供状态和负责人变化，不提供用户意图；**现在就能取**，只能作辅助证据。
- 巡检/转发台账：手动处理和重复转发的事实；**需要新采集**，且需统一事件 schema。
- 评论正文的稳定分类、批准卡片定义和用户身份映射：当前 Board API 白名单不输出正文和成员邮箱；**需要新采集**，建议在受限采集层只保存事件类别、actor type、issue/thread ID，不保存正文。

**计算与频率**：按事件写入后增量重算，每小时刷新；日报冻结前一日数据。两周后报告均值、中位数、P90，并按 workspace、agent、交付类型切片。

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
- 合并/上线时间：当前 issue/API 契约没有统一字段；**需要新采集**。替代方案是要求交付评论或台账写入 `delivery_completed_at`，缺失则明确 unknown，不用 `done.updated_at` 代替。
- 结论采纳时间：评论可读但无结构化采纳字段；**需要新采集**，用受限事件分类替代正文落库。

**计算与频率**：每小时增量更新，日报和周报按结束时间重算；窗口至少保留 30 天，避免 `/api/changes` 7 天保留造成历史断层。

**易刷点与配对指标**：晚建 issue、提前标完成会缩短周期；和北极星、一次通过率、交付完成证据覆盖率配对。没有开始/结束证据不计入，不填 0。

### M3 等待占比

**定义**：

`waiting_share = waiting_duration / delivery_cycle_duration`

其中 `waiting_duration` 只计算父 issue 在 `blocked` 或 `in_review` 的时间，并拆成：

- `waiting_user`：等待用户决定、回复、批准或手动操作；
- `waiting_quota`：等待额度恢复或 usage limit 复查；
- `waiting_agent`：等待 agent、runtime、daemon、任务队列或执行资源。

`blocked` / `in_review` 没有明确原因时进入 `waiting_unclassified`，不能猜成等用户。一个时间段只能归一个桶；状态变化重叠时按事件时间线去重，未知间隔不计入分子但报告缺口。

- 父 issue 统计；子 issue 的等待不重复加入父 issue。
- 交付周期未知或没有状态迁移时，指标为 unknown。
- 取消件不进第一版分母；等待时长可进入取消分析附表。
- 跨 workspace 分组，不跨 workspace 拼接状态事件。

**来源与状态**：

- `/api/changes` `issue_status` 的 `at`、前后状态、issue ID：**现在就能取**，但只保留 7 天。
- issue 当前 `status` / `status_category`：Multica CLI/API，**现在就能取**，只能确定当前状态，不能恢复历史时长。
- 巡检台账、quota wait ledger、Wakeup 的 `reason`、目标 agent 和复查时间：**需要新采集**。现有台账已有 quota 等待信息，但未形成稳定 Board 指标接口。
- 用户/额度/agent 等待原因：当前 issue 状态字段不含原因；**需要新采集**。替代方案是要求状态变更事件携带有限枚举 `wait_reason`，未知就保留 `unclassified`。

**计算与频率**：事件到达时更新；每小时汇总。正式基线至少保留 30 天状态事件，daily snapshot 只能作校验，不能替代事件日志。

**易刷点与配对指标**：把 issue 从 `blocked` 改成 `in_progress` 可压低等待占比；和总周期、运行失败率、一次通过率配对，并记录状态覆盖率。禁止把缺失事件当成无等待。

### M4 一次通过率

**定义**：

`first_pass_rate = deliveries_without_rework / complete_deliveries`

一次通过 = 第一次进入 `in_review` 后，在最终交付前没有发生用户要求修改、返工子 issue、失败后重跑导致的功能返工，且最终交付证据成立。

- `in_review` 后用户只做无内容确认、格式修正或文档链接补充，不算返工；需按事件分类记录。
- 用户提出实质修改、验收失败、重新打开、追加实现范围，算一次返工。
- agent 失败但自动恢复、没有改变交付内容，不算返工，但进入可靠性/运行失败统计。
- 同一父 issue 多次 `in_review`：第一次之后发生实质修改即非一次通过；重复评论不能重复计数。
- 取消件和 `unknown` 交付证据不进分母；“没有返工记录”不等于一次通过，缺采集标记 unknown。
- 子 issue 只要是为同一交付件的返工，归父 issue；并行实现子 issue 不算返工。

**来源与状态**：

- issue 状态迁移、父子关系：Multica CLI/API 与 `/api/changes`，**现在就能取**，但历史窗口不足。
- 评论和用户验收/返工分类：Multica CLI `issue comment list`，**现在就能取**；结构化分类和去重逻辑**需要新采集**。
- run 状态、失败原因、重跑链：`agent tasks`；Board 当前 run 缓存最近 14 天，**现在就能取**但需补采集窗口与关联规则。
- 最终合并/上线证据：当前没有统一字段，**需要新采集**；替代方案是交付台账中的结构化事件。

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
- 订阅固定费用、免费额度和付费 token 不换算成同一货币；若未来要货币化，需另行批准价格表和汇率口径。

**来源与状态**：

- `/api/usage` 的 `multica_daily.by_runtime`、运行/完成/失败/取消、token 计数、`cost_usd_ticks`：**现在就能取**；其文档明确该值保持原单位，不换算美元。
- `/api/usage` 的 `external_quotas` / `signals`：**现在就能取**，有 provider、`used_percent`、`resets_at`、`source`、状态和采集时间；它是窗口级，不等于单件花费。
- CodexBar：草案要求额度一律先用 `codexbar` 查；当前 Atlas 文档只证明有 CodexBar 额度快照来源，未证明有稳定 CLI/API 字段可按 issue 归因；**现在就能取**窗口快照，**需要新采集**逐 run / issue 归因。失败或过期时标 unknown。
- 9router / Orca：Usage 文档标为“未接入”；**取不到**。替代方案是列 provider 未接入和未分摊总量，不估算。
- run 与父 issue 关联：`agent tasks` 的 `issue_id` 和 run 状态/时间，**现在就能取**；token 字段是否每个 run 完整，需以实际采集结果核验，缺失不补估。

**计算与频率**：运行层每小时更新，按交付结束日报冻结；外部额度在每次成功快照更新。报告同时给 `attributable`、`unattributed`、`unknown`，禁止把不可归因额度平均摊给所有交付。

**易刷点与配对指标**：少跑验证、少记失败可压低成本但可能降低一次通过率和提高回归；和一次通过率、返工率、失败率、每件周期配对。低成本不代表高效。

## 3. 数据采集与实现边界

### 3.1 现有可复用接口

- Multica CLI：issue、comment、agent tasks、workspace、runtime；具体字段以 CLI `--output json` 实际返回为准。CLI 是读取事实的来源，不假设存在未文档化的成本或交付接口。
- Atlas 只读 API：`/api/issues`、`/api/issues/{workspace_id}/{issue_id}/runs`、`/api/changes`、`/api/usage`。当前契约规定只读，只有 GET/HEAD，没有立即刷新或写入指标接口。
- `/api/changes` 当前保留 7 天；`/api/issues` run 缓存默认 14 天。两周基线若要连续无缺口，必须扩展事件/快照保留，不可回补不存在的历史。
- 巡检、quota wait、Wakeup ledger：可作为原因和人工处理的证据，但必须先定义脱敏、ID、时间、幂等键和保留期。
- CodexBar：只读额度快照；不得读取 auth 文件、token、cookie 或密钥。

### 3.2 必须新增的最小事件

建议 Builder 在采集层新增统一事件，不把正文或秘密写进 Board：

`delivery_id, parent_issue_id, workspace_id, event_id, event_type, actor_type, actor_id_hash, at, source, confidence, related_run_id, wait_reason, delivery_evidence, schema_version`

- `event_type`：`delivery_started`、`delivery_completed`、`delivery_adopted`、`approval`、`user_reply`、`manual_handling`、`rework`、`wait_open`、`wait_close`、`cost_observed`。
- `event_id` 做幂等去重；`source` 区分 CLI、API、patrol ledger、CodexBar、9router。
- `actor_id_hash` 只用于区分用户/agent，不存邮箱或正文。
- 事件未知、冲突、来源过期时保留记录并置 `confidence=unknown`，不补猜。

## 4. 给 Builder 的第一版实施拆分建议

只写建议，不创建 issue；顺序可调整，但每步先有读回和覆盖率验证：

1. **建立交付件归并层**：读取所有 workspace issue、父子关系和状态迁移；生成稳定 `delivery_id`，处理取消、跨 workspace、缺父 issue；输出 coverage 和 unknown，不改变 Multica 数据。
2. **补齐交付事件**：从 issue 状态、评论线程、合并/上线台账采集开始/结束、采纳、批准、回复、返工事件；只存脱敏枚举和 ID。为人工分类提供可复核的原始引用 ID，不存正文。
3. **建立等待事件**：接入 `/api/changes` 与巡检/quota ledger，形成 `wait_reason` 的 user/quota/agent/unclassified 四类；处理事件重叠、回补窗口和幂等。
4. **建立运行与成本归因**：关联 `agent tasks` run 到父 delivery，接入 `/api/usage` 的 token / `cost_usd_ticks` 和 CodexBar 快照；逐项标记 attributable / unattributed / unknown；9router、Orca 保持未接入。
5. **计算五项指标**：实现中位数/P90、等待分桶、一次通过判定、注意力分子和成本分层；每小时增量、日报冻结；输出分子、分母、unknown、覆盖率及按 workspace/agent/provider 的切片。
6. **加反刷校验与验收样本**：覆盖父子 issue、取消单、跨 workspace、缺失完成证据、重复评论、重复 run、quota wait、失败重跑、9router 未接入、缓存过期；任何采集失败不得渲染为 0 或“无”。

## 5. 需要用户拍板

1. “一件交付”是否固定按父 issue 计，还是允许用户登记多个 `delivery_unit_id` 拆分一个父 issue？
2. `delivery_started` 默认用首次 `in_progress`，没有时回退 `created_at`，是否批准？
3. 哪些状态/评论词算“用户明确采纳结论”？是否由用户在台账中确认，避免模型自行猜测？
4. 手动处理是否只统计巡检台账中有 ID 的动作；台账没有记录时是否一律 `unknown`？
5. `in_review` 是否统一视为“等待用户”，还是必须要求 `wait_reason` 才归入 `waiting_user`？
6. 合并、上线、结论采纳是否由统一交付台账作为唯一结束证据？没有证据的 `done` 是否一律排除周期和一次通过率？
7. 一次通过率中，自动恢复的运行失败是否始终不算返工，但是否单独设质量警告？
8. 成本是否永久保持 `multica_cost` 与 `quota_consumption` 两栏不相加？若要换算金额，价格表由谁维护、何时批准？
9. 两周基线是否按自然日冻结，还是从第一条完整采集事件起算 14×24 小时？
10. 指标是否默认覆盖全部 workspace，还是默认 ACM Lab、用户切换后才看跨 workspace？
11. 指标目标值是否等两周基线完成后再单独拍板，不在 Builder 实施中预置阈值？
12. 是否批准扩展 `/api/changes` 与事件台账保留至少 30 天，以支持周期和等待历史？
