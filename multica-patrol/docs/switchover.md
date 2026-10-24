## ACM-342 切换与回滚材料

本文件只是变更草案，供审查。**未对任何生产配置做改动**：autopilot、playbook、ACM-227、ACM-229 当前内容保持原样。切换必须在代码合并 main、用户另行批准后执行（部署只能从已合并的 main）。

### 1. 机器层怎样不经过大模型

| 环节 | 谁执行 | 是否经过大模型 |
| --- | --- | --- |
| 阈值判断、分页发现、缓存、去重、升级、自报、台账渲染、发送与读回 | `python3 -m patrol run`（确定性脚本） | 否，没有任何模型调用 |
| 每个整点把脚本跑起来 | autopilot `2d84a985`（Multica 调度）→ 副调度 agent 作为**启动器**，提示词只允许运行一条命令并原样回传 | 仅启动，不做判断 |
| 每日分析 | 独立 autopilot → 副调度 | 是，只在这一层 |

为什么还要经过 agent 启动：台账必须以副调度 agent 身份发。已核实 CLI 没有“以某个 agent 身份发言”的开关或凭证导出；agent 身份只来自 daemon 在 agent 任务里注入的任务凭证（`MULTICA_TASK_ID`/`MULTICA_AGENT_ID`/`MULTICA_TOKEN`），本机 launchd 用的是用户登录态，写出来会是用户名义（这正是旧台账 11 条以用户名义发出的原因）。因此：

- 判断全在脚本里，启动器 agent 不决定内容，只消耗启动一次 run 的最小成本。
- 脚本写入前校验任务环境，发后读回校验作者；不满足一律拒绝（退出码 3），不使用用户登录态、不复制或读取凭证。
- 纯只读演练（`--dry-run`）不需要 agent 身份，可在 launchd 或终端运行，不发任何评论。
- 已知限制：如果启动器 run 本身没起来（模型容量、runtime 离线），脚本不会执行；下一次成功运行会把缺失的 slot 记为 `missed_slot` 自报，连续 2 个 slot 缺失/失败升级到 ACM-487。要做到“启动失败也即时发现”需要另起不依赖 agent 的告警通道，不在本次范围。

### 2. 变更清单（逐项）

| # | 对象 | 变更 | 材料 |
| --- | --- | --- | --- |
| 1 | autopilot `2d84a985` | 描述改为启动器版本；调度 `0 * * * *` / Asia/Singapore、执行模式 run_only、assignee 副调度、project 都不变；触发器 label 建议同步改为“全天逐时，机器层” | `autopilot-hourly.description.md` |
| 2 | 新 autopilot（每日分析） | 新建：副调度、run_only、同 project，cron `0 9 * * *` Asia/Singapore | `autopilot-daily.description.md` |
| 3 | playbook `~/multica-handoff/fu-diaodu/playbook.md` | §13/§14 的最小修改 | `playbook.patch`（基于 `rollback/playbook.base.md`，sha256 `6a22fe23…b6dca`；`patch -p1` 已对当前 playbook 干跑通过） |
| 4 | ACM-227 描述 | 替换为 v2 规则说明 | `acm-227.description.md` |
| 5 | ACM-229 描述 | 替换为 v2 台账说明 | `acm-229.description.md` |

回滚快照（切换前内容原样）在 `rollback/`：autopilot 旧描述与元数据、ACM-227 与 ACM-229 旧描述。

### 3. 切换步骤（合并 main、用户批准后，由获授权执行者按序做）

1. 部署：`cd ~/Workspace/agents-lab && git checkout main && git pull --ff-only`；`cd multica-patrol && /opt/homebrew/bin/python3 -m unittest discover -s tests -v` 必须全绿。
2. 只读冒烟（不写评论）：`cd ~/Workspace/agents-lab/multica-patrol && PATROL_STATE_DIR=$(mktemp -d) /opt/homebrew/bin/python3 -m patrol run --dry-run --manual smoke`，核对 `PATROL_RESULT` 中 `writes_attempted:0`、coverage 完整。
3. 备份当前状态（已在 `rollback/`；若切换时 autopilot 描述又变过，先重新导出：`multica autopilot get 2d84a985-978e-43ca-9b87-bd7b71fa2e0c --output json`）。
4. 更新 playbook：在 `~/multica-handoff/fu-diaodu/` 先 `cp playbook.md playbook.md.bak-<日期>`，再 `patch -p1 < <repo>/multica-patrol/docs/playbook.patch`。live playbook 可能在切换前又被改过：应用前必须先 `patch --dry-run -p1 playbook.md < playbook.patch`，失败就按 `rollback/playbook.base.md` 重新生成 patch，不可直接强打。
5. 更新 ACM-227 / ACM-229 描述：`multica issue update <id> --description-file <文件>`（文件放在执行者自己的工作目录内）。
6. 更新每小时 autopilot 描述：`multica autopilot update 2d84a985-978e-43ca-9b87-bd7b71fa2e0c --description "$(cat multica-patrol/docs/autopilot-hourly.description.md)"`，读回 `autopilot get` 核对描述与触发器未变。
7. 新建每日分析 autopilot：`multica autopilot create --title "ACM Lab 每日卡点分析（09:00 SGT）" --agent 9bfe8713-ce7e-499b-830e-423223a64480 --mode run_only --project ad48897b-bd8e-4268-8ee7-e1ede97e48d8 --description "$(cat multica-patrol/docs/autopilot-daily.description.md)"`，再 `multica autopilot trigger-add <新id> --kind schedule --cron "0 9 * * *" --timezone Asia/Singapore --label "每日 09:00 SGT"`。autopilot 写入按发起用户授权，操作者必须是用户本人或获用户授权的 owner/admin。
8. 不要为“测试”手动 trigger 生产 autopilot；如必须手动触发，运行必须带 manual 标记且不计入观察。
9. 观察（不能用单测替代）：由非作者 Reviewer · Hermes 观察至少 2 个自然整点的台账与 1 次每日分析，核对：无重复评论、无误报、作者是副调度 agent、`scheduled` 标记正确、ACM-290 类“失败 run 无重试”能被抓到、带「搁置」标签的 issue 不出现在台账和每日汇总（取数阶段已排除，可用 `--dry-run` 输出和 `grep` 核对）、provider 报错（completed + `[Error]`）首次重试一次且不重复 @、重试后再错升级 ACM-487、ACM-487 评论第一行是大白话下一步。观察期内保留 Grok routine，不由巡检建议停旧 routine 之外的动作。

### 4. 回滚

1. 暂停每日分析：`multica autopilot update <新id> --status paused`。
2. 恢复每小时 autopilot 旧描述：`multica autopilot update 2d84a985-978e-43ca-9b87-bd7b71fa2e0c --description "$(cat multica-patrol/docs/rollback/autopilot-2d84a985.description.before.md)"`。
3. 恢复 ACM-227 / ACM-229 描述：对应 `rollback/*.description.before.md`，`multica issue update --description-file`。
4. 恢复 playbook：`mv playbook.md.bak-<日期> playbook.md`（或 `patch -R -p1 < playbook.patch`）。
5. 代码回滚：`git revert` 合并提交，或直接不再被引用（脚本只被两个 autopilot 描述调用，本身不装 launchd、不常驻）。
6. 台账 ACM-229 与收件箱 ACM-216 的历史评论保留；本地状态目录 `~/.local/state/multica-patrol/` 可保留供复盘，不需删除。

### 5. 配置与运行位置

- 状态目录：`~/.local/state/multica-patrol/`（0700；环境变量 `PATROL_STATE_DIR` 可覆盖，测试用）。
- 没有 launchd 任务、没有常驻进程；本次改动不要求重启任何服务。
- 脚本只用 Python 标准库；每轮目标 <10 分钟（全量 + 4 线程深查，超 8 分钟预算会标覆盖不完整而不是静默放行）。

### 6. 需要用户决定

1. **ACM-215 口径**：现状是 in_progress、副调度、无活动 run、自 10/17 无进展，历史台账里一会儿报一会儿豁免。当前没有找到用户的明确裁定，所以脚本既不报也不静默豁免，每 24h 单列“待用户口径”。请裁定为：搁置 / 持续入口 / 普通事项。裁定为搁置只需给它打「搁置」标签；持续入口或普通事项需改 `patrol/config.py` 的集合并合并。
2. **旧版 approved_unassigned 夜间派活例外是否退役**：机器层现在只对带「可开工」标签的候选只读上报，派活由每日分析按 playbook 第 3 节做；夜间（00–08 SGT）不再有任何派活动作。若要保留夜间自动派活需另行批准。
3. **历史搁置名单待确认**：ACM-80、82、92、83、55、118 在历史记录里被列为搁置，但本轮未获用户确认，没有打标签，也没有纳入排除，会按普通事项被巡检。需要用户确认后再打「搁置」标签。
4. **启动失败的即时告警**：见第 1 节已知限制，是否需要另做不依赖 agent 的告警通道。
5. **provider 报错重试会写源 issue 评论；规则 9/10 补唤醒只创建一次性 wakeup，不写源 issue 评论。** 交付/派活评论 id、时间、判定依据和下一步放在 `--instruction-file`；目标仍由 `DISPATCHER_AGENT_ID` 指定。两类补唤醒共用每 issue 24h 去重和每轮 3 次成功上限；不改状态、指派和 agent instructions。旧 `pending` / `wakeup_pending` 记录直接恢复 wakeup，不重发旧评论。

   指令文件在当前运行目录的私有临时目录创建并在调用结束后清理，不能直接把共享状态目录的文件传给 CLI。创建前持久化 intent；超时后下一轮先按 marker、源评论和 intent 时间读回去重。只有返回有效 wakeup id 才记 `sent`；原始创建结果保存在 `wakeup_result`，错误保存在 `last_error` / `last_attempt_at` / `attempts`。台账和 `PATROL_RESULT.unwoken_actions` 同时记录源评论 id、wakeup id、动作和错误，便于日志追溯。日志只带 issue id 等必要字段，不复制 issue 描述；provider 重试台账也用 agent id，避免带出历史点名。

   **ACM-379 事故（2026-10-24 01:02 SGT）**：源交付评论 `01a169a5-dd89-72a2-b90d-89537c72afe9`，旧巡检评论 `01a169b6-8df6-7857-b848-d95b10f0604f`。autopilot run `01a169b4-c165-7b9a-b440-5566b7c95dee` 的结果为 `writes_attempted=3`、`unwoken_sent=0`；本地记录停在 `wakeup_pending`，台账记 `wakeup_uncertain`，规则列表为空，issue 无后续接力 run。结合当轮代码顺序可确认已进入创建调用，但旧代码丢弃 stderr，无法取回当次原始错误。使用同一个指令文件、对不存在的 issue UUID 做无副作用验证，CLI 在请求前返回：`--instruction-file ... resolves outside the current working directory`。这是可复现的创建前文件校验失败，未建成规则，并非规则触发后丢失；修复 run 内文件位置与错误留痕。此修复不部署、不补发生产 wakeup，待本地提交审核批准。
6. **「已批准未派出」的派活从巡检挪走**：§13 旧文写由巡检全天派执行人；机器层只上报，派活挪到 §14 每日分析（需用户确认）。
7. **推送、开 PR、合并、切换**各需单独批准；本次一律未执行。

### 7. 已知限制

- provider 报错检测依赖评论读取，只在深查时执行；已缓存且 run 未变化的 issue 上 `[Error]` 评论被原地编辑，要等下一次深查才能看到。超过 6 小时的旧错误只上报不重试，超过 14 天评论窗口的旧错误看不到。
- 检测判据是“最新 run completed，且该 run 所属 agent 在 [开始, 结束+120 秒] 内最新的 `[Error]` 评论”；这是用 ACM-335 的真实数据校准的，换 provider 的报错格式不以 `[Error]` 开头则不会被识别。
- 台账 ACM-229 是机器记录，首行保留批次标记，不套用“第一行大白话”格式；该格式只用于 ACM-487 的升级、每日汇总和源 issue 的重试评论。
- 自然语言“用户已批准”的判断不在机器层；机器层只认「可开工」标签，其余交每日分析。
- 业务进展的判定排除副调度自己的评论（视为派活/确认）。副调度若在某 issue 上做了真实交付，机器层会低估其进展，宁可多报不漏报。
- 时间锚点：时间线被服务端截断时，用 `updated_at` 作保守下界并标“证据不全”，不捏造更早的时间。
- 手动运行与自然整点由启动器按触发上下文判别；判别不了按 schedule 处理，Reviewer 观察时以 run 触发来源为准，不以台账标记为准。
- slot 取整点（≥55 分钟视为下一个整点）；延迟 ≥55 分钟的 run 会被记到下一个 slot。
- 单元测试用离线 fixture，只证明规则与协议；不是自然整点实测。


### ACM-438 巡检判断与额度等待（本地实现，未部署）

已指派 todo/backlog 深查任务与交付：15 分钟无任务记录标为 assigned_no_task；已有 queued/dispatched 但未启动标为 queued_unclaimed；completed 且时间窗内有正常 agent 交付评论标为 delivery_status_not_updated，只核对状态，不补派。单次终止失败超过 10 分钟且无后继任务即可报告，保留现有失败分类，不用 attempt/max_attempts 猜平台会重试。

规则 10 支持带空格的名称及“在 ACM-411 复验”。写入和 dry-run 都读取目标 issue 的负责人、状态与任务；目标未知只记 unknown_target，不回退源单负责人。多目标/多单引用无法唯一定位时保守标未知。待唤醒旧记录也重读，已被新交付替代就不再创建。

quota/usage limit 复用既有 classify_error、ledger 和一次性 at Wakeup（唤醒调度作判断，不直接重跑）。记录绑定原失败 run、目标 agent、复查时间；同一原失败 run 永不按 24h 再挂等待。优先使用 reset_at，Codex “try again at h:mm AM/PM” 按本机 SGT 解释、跨午夜取下一日；提示已过期或不能解析时 1 分钟后交调度核实，提示绝不作为额度恢复证明。指令要求到点读新鲜额度、最新交付/负责人/活动任务，只在全部满足且有原授权时恢复一次，读回关联新 run；缺证据由调度裁定。未新增额度采集旁路、账号切换或自动 rerun。

健康输出分别列 coverage 与 scheduling。Wakeup 创建失败/结果不确定/读取失败/目标未知计入 scheduling.errors；真实运行返回非零并保留 self-report，不能用 coverage.complete 或 exit0 代表接力成功。dry-run 仅验证读路径及拟定动作，不验证真实 Wakeup 触发或恢复。

离线回放：在 multica-patrol 下运行 python3 -m unittest tests.test_recovery -v。374/409/422/378/405 为调查证据重建的历史 fixture，非当前线上状态、非自然整点验收。线上接力和真实额度读取/一次恢复须批准部署后独立验证。
