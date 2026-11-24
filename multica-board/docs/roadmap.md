# Agent Atlas 路线图

方向与先后以本文为准；执行状态以 Multica 为准；下文已交付标记以 Multica 状态及已合入 main 的 SHA 为准，不代表均已部署或启用。本稿对照 main `e89473a41f3c63bd96cef2e4086c07286f0d3481`（2026-11-24 核实）。依据：ACM-383 中用户 10-24 确认的规则、现有设计与契约、已合入的 ACM-377 设计系统（`docs/design-system.md`）、issue 原文及相关评论；跨 harness 方向同时依据 ACM-415 / ACM-418 的盘点、ACM-420 汇总结论及用户更正。

## 使用规则

- 各板块按「现在 / 下一步 / 以后 / 不做」组织；它们是方向优先级，不是 issue 状态。「现在」包含本轮需要收拢验收或交付的目标，不表示重新实施历史交付。
- 「现在」「下一步」每条至少关联一个 issue；历史 issue 只作来源时明确标注。「以后」允许未开单；进入「下一步」才开对应执行单，不提前堆积 backlog。
- 交付中推迟的事项先加入「以后」，不能只留评论。本文不授予推送、合并、部署或任何真实写操作权限；push、PR、合并、部署须用户点名 issue、动作与 SHA 逐项批准。
- 新页面按设计系统定位数据层、选版式、复用组件。验收需与 Skills 同宽截图对照（1440px、1100px），补深色及 390px、键盘操作；未知、未采集、失败不能画成零或正常。
- 本机、仓库、Multica workspace、agent 四层各自保留身份与证据，不把同名资源、可见性、安装分配、实际加载混为一谈。

## Box 配置同步

采集、对账、快照传输与看板上的只读展示；写回与定时巡检见本节「下一步 / 以后」。统领设计 [ACM-910](mention://issue/01a1afe2-d7cd-73a1-bf00-e0fd73419cf9) 已 done。设计稿 `multica-board/docs/box-config-sync-design.md` 第 2 版 HEAD `482432fce3056acfd1cef5c4666d1510ed3ee7bb` 截至 2026-11-24 仍不在 `origin/main`（当前 `e89473a`）上。已知副本：Box 上 `/workspace/acm928/design.bundle/acm-box-config-sync-design.bundle`；当时 Mini 工作树 `/Users/tr/multica_workspaces_desktop-api.multica.ai/acm-lab-24a04e64d3f5/acm-910-2123792e5454/workdir/agents-lab`，分支 `agent/builder-cursor-opus/e0703a47e3fb`。本稿只注明缺口，未将该文档纳入本仓库。

### 现在
- **mcps 未采集展示口径**：未采集时仍显示 `ok` + 占位条数；先短清单再实现，本单只到本地 commit + bundle，不 push / PR / 合并 / 部署。[ACM-1008](mention://issue/01a207c4-f2bf-7f08-b92e-73423a3448c9)（2026-11-24 已指派 Builder · Cursor 开工，验收另批）。启动 run 随即 TLS 失败（评论 `01a20cc2-f69b`：`Client network socket disconnected before secure TLS connection was established`），尚无实现交付，不能读成正在执行。
- **路线图本文更新**：补本板块、挪走已交付、标注运行指标暂停、补设计稿位置。[ACM-1007](mention://issue/01a20755-9f46-7e71-af57-21da13c884a4)（todo；本稿即该单本地稿，push / PR / 合并另批）
- **快照保活须人工另批**：36 小时过期；在 [ACM-934](mention://issue/01a1b3a2-d459-7b64-84aa-4df670f689ba) 的 launchd / Routine 未获批准加载前不会自动续期。Box 已于 2026-11-24 16:48:19 上海时间发布 `8bf2fd3fe6d6294b31c68f9f453cf7901a6231f4`（`collected_at` 2026-11-24T08:48:19Z），按 36 小时计算过期约为 2026-11-26 04:48:19 上海时间。Mini 拉取待确认，不能当作线上已续期。下一次保活仍须人工另批。

### 下一步
- **第二期写回**：总单 [ACM-896](mention://issue/01a1af71-7e78-7382-8609-abeedc60a81a)；顺序 [ACM-929](mention://issue/01a1b3a2-c7fc-74d0-b90b-c04ca4843558) → [ACM-930](mention://issue/01a1b3a2-cabf-7882-9a2f-80f9009ca84d) → [ACM-931](mention://issue/01a1b3a2-cdac-7137-ad18-32d92ebe1559) → [ACM-932](mention://issue/01a1b3a2-d031-7e97-8c14-adebe4903963)（2026-11-24 均为 todo、未指派）。929 是 `.library-fingerprint` 与宿主写入方式研究单，开工须另批。真实写回（尤其 931 / 932）须用户本人在 Multica 发 `BOXSYNC-APPROVE v1` 凭据（人类 owner 评论，Bot 账号不算）。
- **P3-B 巡检上线**：ACM-934 的 Routine 模板与 fetch-snapshot 样例已合入 main（含于 `c7b4f2d9` 及之后），并随看板部署到 Mini；launchd 未装，定时巡检未上线。装载与线上验收须另批。本地验收单 [ACM-1006](mention://issue/01a20752-34c1-7d15-a4fc-1fe32746dc84) 已于 2026-11-24 关成 done。
- **data-worktree 权限**：`publish-snapshot` 专用 worktree 权限应收紧为 `0700`，由人核对并 `chmod 700`，本仓库不代改。2026-11-24 在 Box 对本机路径做 `stat`：`/home/box/.local/state/box-config-sync/data-worktree` 为 `0755`（父目录为 `0700`）。收紧须另批。

### 以后
- **P3-A 看板全量接入**。[ACM-933](mention://issue/01a1b3a2-d247-7845-9b8a-982db7696a8c)（todo）
- **P3-C Instructions 与云端技能变更包**。[ACM-935](mention://issue/01a1b3a2-d6c3-7bbd-ac2e-6c2a4d1cbd17)（todo）
- **P3-D′ 停用旧导出**：连续 14 天稳定后再做。[ACM-936](mention://issue/01a1b3a2-d8e7-7aff-911e-4d84df6cb68b)（todo）

### 不做
- 不把未采集的 MCP 占位条数显示为已覆盖（口径见 ACM-1005 / ACM-1008）。
- 不在未获 `BOXSYNC-APPROVE v1` 前对真实 Box 做写回。
- 不把本路线图当 launchd 安装、快照保活、权限收紧或部署批准。

### 已交付
- 设计 [ACM-910](mention://issue/01a1afe2-d7cd-73a1-bf00-e0fd73419cf9) 已 done；设计稿尚未进入 origin，缺口见本节导语。
- 第一期已闭环：总单 [ACM-894](mention://issue/01a1af71-77af-7866-8981-12e326506e47)、P1-D 云端 skill 正文只读接入 [ACM-895](mention://issue/01a1af71-7ae5-7849-b0f6-905d879c2703)、P1-E 最小快照传输 [ACM-928](mention://issue/01a1b3a2-c490-70a2-8bef-1c17dcfde190)、现场闭环 [ACM-1002](mention://issue/01a205b9-2f79-7626-8fde-b9b1f451f8b8) 均为 done。传输分支 `box-snapshots`。ACM-895 以假 CLI 与 API/测试交付云端正文只读接入，明确不到真实云端 skill 在线抽屉读取。
- 计数口径 [ACM-1005](mention://issue/01a20751-d69b-7a97-ad80-ddbb5f928538) 已上线：memory / routines / attachments / hooks 为 `not_collected`、计数为 `null`。mcps 不在其实现范围，见 ACM-1008。
- ACM-934 巡检模板已合并未装 launchd；ACM-1006 本地验收已 done。

## Agents（含 bots）

### 现在
- 无未交付的当前焦点。已交付项见本节「已交付」。

### 下一步
- **G1 边界补强（位置待用户定）**：处理矛盾分页快照校验、离线启动鉴权超时、同值字段展示三项；先核对已有修复，避免重复实施。2026-11-24 该单为 backlog、无指派；在用户另批之前保持现状，不擅自删改其产品含义，也不当作本轮执行项。[ACM-330](mention://issue/01a16307-27ba-7db4-8e5b-33d79deed931)

### 以后
- **G2 批量切换执行与条件回滚**（未开单；来源 ACM-231）：依赖跨板块统一写入安全设计（护栏暂按 ACM-458，真实单号待核）及平台能力证明，不把 G1 或设计评审通过当成 apply 授权。
- **Instructions / Skills 同步**（未开单；来源 [ACM-294](mention://issue/01a15854-d965-7efc-960d-dfa151b2dde7)）：只读对账已完成（ACM-828 / ACM-880 / ACM-866）；下一档是受控写回与定期巡检，均未开单，依赖统一写入安全设计（护栏暂按 ACM-458，真实单号待核；不得把已 done 的 ACM-390 当作该设计）。已有 CLI 能力不等于页面写入口获准。
- **创建 / 复制 / 修改 agent**（未开单；用户在聊天中确认，已记录 ACM-383）：逐条预览和确认。复制不复制 `custom_env`、`mcp_config`、`runtime_config`，必须提示并支持一键补齐；补齐同样走独立授权，不回显秘密值。
- **Grok Bot 证据补齐**（未开单；来源 ACM-290 / ACM-349）：区分直接实测与共享机制推断，未来 memory、sessions、hooks 等依赖先研究公开或本机只读来源，再定范围。

### 不做
- 不逆向调用 Grok Bot 私有写 RPC；不把共享 skill 名称清单当成逐 Bot 的绑定或加载证明；不因配额信号自动切账号或 runtime。

### 已交付
- **全景只读与指令补采**：保留 Multica Agents、Grok Bots、Instructions / Skills 抽屉和 Git 真源对照；其余九个 workspace 的指令由用户本人在授权终端补采，任务令牌不绕过 workspace 边界。[ACM-290](mention://issue/01a15798-3e52-732b-8089-3ab66fdb80bf)、[ACM-332](mention://issue/01a16327-c7de-77af-a4ca-83ac73157af2)（均为 done）
- **职责说明只读对账与首轮对齐**：ACM-880 提供 Agents 对账视图与 `/api/instruction-diff`；ACM-866 补录 3 bot、对齐 Steward / Atlas PM Live 并再生来源快照。[ACM-880]、[ACM-866]（均为 done）
- **模型与 Runtime 单 agent 写入代码已合入 main**：ACM-450 讨论稿已 done；ACM-458 已合入 #98 / #101，默认不配置 `--write-policy`，写入口关闭。启用与部署另批；配置写入、运行生效与账号模型可用性分别核实，批量切换继续后置。[ACM-450]、[ACM-458]
- **G1 计划与切换流程**：选择、预检、逐行差异、确认和执行结果；一份计划一个 workspace，`null` 与空字符串保留区别，hash 不是批准。[ACM-231](mention://issue/01a14abd-4777-7469-bb77-fd10f4ccf511)、[ACM-279](mention://issue/01a15244-c6a6-7674-bdb4-9935a7eb0217)（均为 done）

## Skills

里程碑：**Skills v2（治理二期）**包含登记与展示口径、实时重叠检测、只读上游版本检查、runtime 加载证据；SkilLock 接入另有前置。**升级审批**单列后续里程碑，不能混进只读 v2。

### 现在
- 无未交付的当前焦点。已交付项见本节「已交付」。

### 下一步
- 无已开单的执行项。页面消费 S1–S3 字段与只读对齐命令已由 ACM-891 / ACM-893 交付，见本节「已交付」。

### 以后
- **Skill 可编辑登记**（未开单；来源 [ACM-309](mention://issue/01a15a32-00ca-725a-93e1-25bd2fe8b223)）：先设计本地 / Workspace / 第三方的可写边界、保存目标和冲突处理，依赖统一写入安全设计（护栏暂按 ACM-458，真实单号待核）。
- **任意两个 Skill 内容比较**（未开单；来源 ACM-309）：用户选两个版本或入口看正文差异；不能用跨层漂移摘要替代正文比较。
- **Skills 升级审批进入 Atlas**（未开单；来源 [ACM-181](mention://issue/01a143ce-9544-73a1-8071-e3fbf0977801)，用户在聊天中确认，已记录 ACM-383）：保留批准 / 拒绝方向，依赖统一写入安全设计（护栏暂按 ACM-458，真实单号待核）、暂存与 diff、完整扫描覆盖和审计；并非取消或永久排除。
- **SkilLock 真正接入看板**（后续执行单未开；来源 ACM-359）：先修本地投影附件漏测，再评估上游入口链接支持；上游草稿不是本地修复替代品。ACM-359 扫描覆盖处置已 done，本条仍指看板接入，不是重复实施已完成的扫描修复。
- **真实云端 skill 在线抽屉读取仍缺独立核验**（不是整项未开单）：契约 [ACM-336](mention://issue/01a1634b-e405-7b17-ac68-a5b82fbfac7b)、Skills 内容查看 [ACM-309](mention://issue/01a15a32-00ca-725a-93e1-25bd2fe8b223)、Grok 导出 user skill 正文 [ACM-613](mention://issue/01a17924-da31-7e1a-9355-53b7edb0400b)、云端正文只读接入 [ACM-895](mention://issue/01a1af71-7ae5-7849-b0f6-905d879c2703) 均已 done。ACM-895 以假 CLI 与 API/测试交付，明确不到真实云端 skill 在线抽屉读取；现场闭环 ACM-1002 验的是 `/api/box-config` 传输，不覆盖该抽屉。剩余缺口是这一步的上线端到端核验，须另批，不以「未开单」概括整项。保留权限与读取失败边界，不以「正文未采集」冒充空正文。

2026-11-04 Steward 早稿 P0–P2 已作废：大屏一致性看板、roadmap 消歧 UI、仓库切换、额度跳转闭环、Board 直 activate 均不沿用为当前承诺。

### 不做
- 不自动批准能力变化、不自动填复核时间与定制约束、不把 SkilLock 当沙箱；不恢复搁置的多仓库视图。

### 已交付
- **清单口径与登记机制**：统一 Multica Workspace / Grok Bot 来源、内容哈希对账、符号图例和技能根证据；登记机制只自动补基础事实，不自动填写复核结论，定时触发另行批准。[ACM-349](mention://issue/01a164b5-164f-7643-a7ff-a86b744c9eda)、[ACM-350](mention://issue/01a164b5-1b2f-7040-a874-7aea8485ff52)
- **只读升级检查**：按来源查询 npm registry / GitHub API，缓存和限频；网络失败或不可比较明确显示缺口，不说「无更新」。[ACM-358](mention://issue/01a164e3-a4c9-7ddd-b826-3114e6f2772c)
- **S1–S3 只读数据**：S1（ACM-825）提供逐项 id / path / body_sha256 / loaders；S2（ACM-826）提供多副本 `hash_compare`；S3（ACM-827）提供 `upgrade_candidates` / `last_gate`，Board 禁止直接 activate。后端已合入；列表/抽屉展示已由 ACM-891 交付，只读对齐命令已由 ACM-893 交付，不再把整项当作「体验单未开」。[ACM-825]、[ACM-826]、[ACM-827]
- **实时重叠检测与加载探针**：ACM-357 / ACM-360 已合入；磁盘可见仍不等于实际加载，缺证据保持未验证。[ACM-357]、[ACM-360]
- **收尾体验**：[ACM-615](mention://issue/01a1795f-d999-7e22-adce-33e062266dfa)（done）。跨板块深链见「跨板块」已交付的 ACM-390，不是写入安全设计。
- **SkilLock 扫描覆盖处置**：上游草稿与本地投影附件漏测的需求边界已处理；不改 PATH、不把空扫描或部分测量当成门禁通过。[ACM-359](mention://issue/01a164e3-aa68-717e-8f78-190e3c21aa88)（done）
- **Skills 抽屉只读对账与对齐命令**：正文 hash、多副本对照、loaders 与 last_gate；漂移摘要及一键复制只读对齐命令。[ACM-891](mention://issue/01a1af71-709b-78d9-846a-62a33f809a2b)、[ACM-893](mention://issue/01a1af71-7587-7590-84a5-ad24dea72af1)（均为 done）
- **Skill 正文只读来源**：正文与动态数据契约 [ACM-336](mention://issue/01a1634b-e405-7b17-ac68-a5b82fbfac7b)、内容查看 [ACM-309](mention://issue/01a15a32-00ca-725a-93e1-25bd2fe8b223)、Grok 导出 user skill 正文 [ACM-613](mention://issue/01a17924-da31-7e1a-9355-53b7edb0400b)、云端 skill 正文只读接入 [ACM-895](mention://issue/01a1af71-7ae5-7849-b0f6-905d879c2703) 均为 done。895 不到真实云端抽屉在线读取，剩余缺口见本节「以后」。

## MCP

里程碑：**MCP v1 重做**是扩大只读采集并对齐 Skills；**MCP v2**才讨论受控写入。重做不是写入版。

### 现在
- 无未交付的当前焦点。已交付项见本节「已交付」。

### 下一步
- 无已开单的执行项。

### 以后
- **MCP v2 写入**（未开单；来源 ACM-370 / ACM-383）：配置、分配、启停等动作先经过统一写入安全设计（护栏暂按 ACM-458，真实单号待核），逐项授权、确认、审计和回读；不能扩宽现有 GET-only 服务后直接启用。
- **加载实证与持续支持表核对**（未开单；来源 ACM-370 / ACM-374）：源码支持、安装配置、下一次任务实际加载分开，未知 runtime 保持未知。

### 不做
- 不因 workspace 库为空认定本机没有 MCP；不显示秘密取值；不把 MCP 与 Skills 合成同一个板块。

### 已交付
- **v1 补验**：核对线上页面与只读 API、runtime 支持证据、schema 重建连接异常是否复现；未验证项单列，不用源码确认代替实测。[ACM-370](mention://issue/01a167e0-2e77-7aa5-9876-b609e4d783cb)、[ACM-374](mention://issue/01a16912-ea39-75a9-91b7-e74a35fb96c5)（均为 done）
- **MCP v1 重做**：遵循已合入的 ACM-377 设计系统；采集本机各 harness 全局配置、agents-lab 项目配置、workspace 库和 agent 分配，按来源卡片、单表分组、工具列和单一详情抽屉对齐 Skills。同名配置只比指纹，不显示配置取值、env、header、token。[ACM-378](mention://issue/01a16937-2128-7e42-bc9f-116674a36295)（done）

## Issues 与待决事项

### 现在
- 无未交付的当前焦点。已交付项见本节「已交付」。

### 下一步
- 无新增优先项；不把巡检和旁路的执行状态复制进路线图。

### 以后
- **每日清单入口**（未开单；来源地图 L5「每日清单」）：先定义与待决事项、ACM-487 每日分析的分工和数据来源，避免再做一个重复队列。
- **等待用户标签 / 评论信号**（未开单；来源 ACM-162 / ACM-178）：明确真实用户决策和普通进度的区别，再决定是否扩展现有状态队列。

### 不做
- 不把所有 workspace 自动纳入「待决事项」；不把 run completed 当成 issue 完成；不复述巡检台账或自动派活。

### 已交付
- **关注与决策队列的职责分工**：Issues 看跨 workspace 的运行失败、受阻、验收和停滞；待决事项聚焦 ACM Lab 的待验收与受阻，范围始终可见。[ACM-191](mention://issue/01a148c8-dd8e-7b2a-bcc8-d4c2da4ca924)（done）

## 地图

### 现在
- 无未交付的当前焦点。已交付项见本节「已交付」。五层导航与工具信号的口径仍然有效：告警、正常、未建有别；已建工具可导航，未知或失败不强行点绿。

### 下一步
- 无独立执行项；节点清理先随本文审定，不在本轮改页面。

### 已定范围
- 地图先只管 ACM Lab，不扩到其他 workspace；顶栏保持现状，不随新板块调整。

### 以后
- **地图「未建」节点按本文维护**（未开单；来源 ACM-383）：L5「每日清单」、L4「派活与交接」、L3「Spec」是方向占位，不是开工承诺。逐项晋级后才开单和点亮。
- **L5「状态桥接」待删**（未开单；用户在聊天中确认，已记录 ACM-383）：节点已过时，不再按「未建」功能排期；L4「Grok Bots & 桥接」既有入口不随之删除。
- **派活与交接 / Spec 入口**（未开单）：先研究现有平台、issue 链和仓库规范能否复用，不凭地图空位新增一套调度或 spec 系统。

### 不做
- 不以地图节点反向决定产品范围；不复活搁置事项；不靠硬编码全绿表示完成。

### 已交付
- **五层导航与工具信号**：保持资源、执行、能力、协作、感知与决策五层。[ACM-193](mention://issue/01a148e9-9517-72ea-aa10-badcab52dc71)、[ACM-309](mention://issue/01a15a32-00ca-725a-93e1-25bd2fe8b223)（均为 done）

## 运行指标与额度

本节**暂停推进**：按用户已作决定，不作为当前焦点。本稿只改路线图表述，不启、不关、不改这些单。

里程碑（备查）：**运行指标 v1（只读）**——把 run、失败、返工和额度健康度统一成一份只读结果，Atlas 与巡检共用同一口径，调度只依据「当下额度健康」，不依据历史失败。依据：ACM-525（S1–S8）；S4 回放结论用户已于 10-26 通过。

2026-11-24 重核、仍未收口的单（均非本轮焦点）：[ACM-943](mention://issue/01a1d028-1aa5-713b-8289-850312c26d32) in_review、[ACM-944](mention://issue/01a1d028-1df5-742b-8065-44df9d3a84e7) blocked、[ACM-947](mention://issue/01a1d23e-5a57-71db-9ed0-535796a93791) todo、[ACM-958](mention://issue/01a1d32a-e03d-7259-9019-3dd7f9afde0e) in_review；S8 [ACM-574] 为 in_review。

### 现在
- 无当前焦点。本节暂停。

### 下一步
- 无当前焦点。本节暂停。S8 小范围启用、复验与返工推断修正均不在暂停解除前作为执行项。

### 以后
- **失败规则 v0.2**（未开单；来源 S4 抽样）：补 geo / access 限制、broken pipe / EPIPE、runtime recovery、配置或模型不可用、HTTP 503 包装 quota 的识别；需另行审定，不自动落地。
- **provider_unstable 阈值**（未开单；来源 S4 提案）：同 runtime、同签名，滚动 24 小时至少 3 条，排除 quota / capacity / access_denied；用户批准前原型不得产出该类，只给候选与样本数。
- **结构化返工字段**（未开单）：`rework_of_run_id`、`rework_trigger_event_id`、`rework_reason`；仅当返工链覆盖率长期不足才申请，需单独批准。

### 不做
- 不把历史额度失败当永久不可用；不从错误文案猜分类；不因配额信号自动切账号或 runtime；S8 明确启用前不改实际派单逻辑。

### 已交付（备查，不恢复为当前焦点）
- **Atlas 只读运行指标视图（S5）**与**巡检消费统一结果（S6）**、额度采集稳健性。[ACM-571]、[ACM-572]、[ACM-642]（均为 done）
- **影子试运行（S7）**。[ACM-573]（done）
- **分池额度预警**。[ACM-414]、[ACM-586]（均为 done）
- **常驻 run-metrics 采集管线**（脚本 + launchd 模板；安装另批）。[ACM-736](mention://issue/01a188c1-30d6-76e7-89a9-e6efda1cadc1)（done；此前未写入本文）

## Agent 配置与 skills / MCP / hooks

信息架构；口径均为用户 10-27 前后已拍板，skills / MCP / hooks 与 Memory 的口径按用户 10-28 改判。用户 2026-10-29（ACM-715）起，这三页并列，不再用「能力」作总称。依据：ACM-607（术语与 IA）、ACM-613（skill 正文只读来源）、ACM-496（IA 评审）、ACM-576 / ACM-577（Memory 来源调研）。

里程碑：**Agent 配置与 skills / MCP / hooks v1（只读 IA 落地）**——按本节口径落地只读信息架构，不含写入。ACM-660 / ACM-661 / ACM-662 / ACM-664 / ACM-715 已 done；ACM-663 Memory 仍 todo，数据来源未定。
- 已交付执行单：术语 [ACM-660]、运行配置字段组 [ACM-661]、三页并列与来源筛选 [ACM-662]、能力三态 [ACM-664]、移除「能力」总称 [ACM-715]、能力证据清单 [ACM-613]；待做：Memory 标签页 [ACM-663]（todo，数据来源核实后才做）。
- 验收标准：
  1. 全站不再出现「跑法」；跨工具术语只出现在 Agent 配置页和 skills / MCP / hooks 页；顶栏不变。
  2. 运行配置字段组与职责块分区显示。
  3. skills / MCP / hooks 三个页面并列，不含 Memory。
  4. Declared / Reachable / Observed 三态均有真实条目，或明确显示 missing。
  5. 全程只读，不显示 env、header、token。
  6. 每个 PR 带 CHANGELOG 条目；「已上线」以部署 SHA 为证。
  7. 与 Skills 同宽截图对照（1440px、1100px），另附深色与 390px 截图，并补键盘操作。

### 现在
- **主对象是「Agent 配置」**：职责块（指令、技能、MCP 等）与「运行配置」字段组分开显示；「跑法」不是独立对象，只是 Agent 配置上与职责块分开的运行配置（模型、推理、Runtime 等）。
- **skills / MCP / hooks**：三个页面并列为同级条目，各自用项名，不再用「能力」作总称（用户 2026-10-29，ACM-715）；来源（本机、仓库、workspace、agent）是过滤项，不是分类。Memory 不再与这三页同级（用户 10-28 改判，取代 10-27 的四类并列）。hooks 来源只有 Cursor、Codex 用户 hooks、Grok CLI、Hermes 插件事件。
- **能力三态**：Declared（声明了）、Reachable（路径或连接可达）、Observed（运行中被观察到）；缺失显示 missing / unknown，不推断。
- **只读且不显示秘密值**：env、header、token 一律不显示。

### 下一步
- **Memory 来源确认后再推进**：ACM-607 拆出的 660 / 661 / 662 / 664 及后续 715 已 done；ACM-663 仍 todo，先确认数据来源，再做只读元数据页，不把规划当成交付。[ACM-663]

### 以后
- hooks 与抽屉里的 Memory 标签页的写入、跨 harness 统一管理，归「跨板块」的统一写入安全设计（护栏暂按 ACM-458，真实单号待核），本板块只管只读。

### 不做
- 不把 Multica 的产品命名带进 Atlas 界面术语；跨工具通用词汇只落在 Agent 配置页和 skills / MCP / hooks 页，不扩散到别的页；不改顶栏。

## 跨板块

### 现在
- 无未交付的当前焦点。已交付项见本节「已交付」。

### 下一步
- 无已开单的执行项。

### 以后
- **跨 harness 统一管理**（远期方向；来源 [ACM-415](mention://issue/01a16ab2-bc7d-7628-9ae5-b404a91f2974)、[ACM-420](mention://issue/01a16ae1-92e6-7f41-81d4-3760e5a18ba4)，后续执行单未开）：覆盖定时任务（Multica Autopilot、Codex Automation、launchd 等）、Hooks、Plugin、MCP 和 memory，按以下里程碑推进；现有盘点是研究来源，不是写入授权。
  - **只读 inventory**（其中 hooks 与 skills、MCP 并列为同级页面，不再用「能力」作总称；memory 改为 Agent 配置抽屉里的 Memory 标签页，先只显示元数据，数据来源待确认；本条只保留定时任务、Plugin 与其余配置）：先汇总各 harness 的配置与只读 CLI 证据，按对象身份、配置路径和 manifest 去重，分别记录存在、启用、加载与运行；保留触发、last_run、数据源、observed_at 和 confidence，未核实的注入点与瞬时值不提升为确定事实。memory 先索引私有库，不自动合并或写入；配置只记录必要元数据，不读取或展示凭证值。
  - **告警与冲突检测**：再做快照差异、目标缺失、日志陈旧、重复 Stop hook、跨调度器职责重叠与多份 MCP 配置检测；先读停用登记，排除已登记的有意停用，paused 或未加载本身不等于故障。`one.yic.multica-dispatch-sidecar` 按 ACM-354 有意停用，不能作为异常或首个告警样例；Hermes heartbeat 的 POC plist 残留另行登记，不直接恢复。
  - **受控写操作评估**：最后才评估人工确认的 pause / resume，launchd 启停、hook / MCP 配置改写继续后置；依赖统一写入安全设计（护栏暂按 ACM-458，真实单号待核）、独立授权、逐条确认、回读、回滚与审计，不因盘点或告警自动修复。具体数据源、字段和权限边界须经执行单设计确认。
- **统一写入安全设计**（真实单号待核；用户在聊天中确认原则，已记录 ACM-383）：不得把 [ACM-390](mention://issue/01a169f5-4093-72c0-ad14-d1b6c19f3d7c) 当作该设计——390 是 Agents ↔ Skills ↔ MCP 跨板块深链（G18），已 done，明确不改安全/同步入口，见本节「已交付」。ACM-910 定稿评论 `01a1b34e-8450-7cae-8495-5d14694457d9` 写明：roadmap 指向 ACM-390 是错误；护栏暂按 [ACM-458](mention://issue/01a16dce-c4cd-77b0-a66e-4a56964c6a58) 口径，统一写入安全设计的真实单号待核。原则仍有效：MCP v2、instructions / skills 同步、runtime 批量切换执行、Skills 升级审批、创建 / 复制 / 修改 agent 拟共用一份安全设计；所有动作单独授权、逐条确认、留审计；环境变量只能写入、只显示名字，从不显示值。[ACM-458](mention://issue/01a16dce-c4cd-77b0-a66e-4a56964c6a58) 已交付模型/runtime 写入底座且默认关闭写入口，不等于这份跨热点统一设计已完成或已启用。
- **写入门槛与故障处置**（属于上述设计，护栏暂按 ACM-458，真实单号待核）：绑定身份、server 与 workspace 范围，dry-run 逐行差异，过期和冲突拒绝，写后回读、失败即停、未知结果先对账。批量切换另须证明批准原子单次消费、CAS / 活动任务保护和服务端提交栅栏；回滚独立批准，不自动覆盖后续变更。
- **共用组件和文案清理**（未开单；来源 ACM-377 §6–7）：角标、未复核淡化、只读文案、复制失败提示、组件改名先审定，不把设计师建议写成用户决定。Agents ↔ Skills ↔ MCP 深链已由 ACM-390 交付，见本节「已交付」，不再把跳转写进未开单。

### 不做
- 不把确认按钮或本地锁当完整授权 / 并发保护；不记录秘密值供审计；不把本路线图当写入批准。

### 已交付
- **设计系统与统一路线图**：按四层数据和现有版式明确各板块边界，保留待决设计问题；路线图管方向、issue 管状态。[ACM-377](mention://issue/01a16937-1a43-7a15-add9-368e4f5b850f)、[ACM-383](mention://issue/01a169cf-0a69-758b-8a9c-fe558f601e5b)（均为 done）
- **MCP 重做已遵守共用范本**。[ACM-378](mention://issue/01a16937-2128-7e42-bc9f-116674a36295)、[ACM-377](mention://issue/01a16937-1a43-7a15-add9-368e4f5b850f)（均为 done）
- **Agents ↔ Skills ↔ MCP 跨板块深链（G18）**：[ACM-390](mention://issue/01a169f5-4093-72c0-ad14-d1b6c19f3d7c)（done）。只做定位跳转，明确不改安全/同步入口；不能当作统一写入安全设计已交付。

### 配置管理与写入热点（用户 2026-10-24 确认）

- **配置管理信息结构**：将 runtime / model、instructions、skills、MCP、hooks、plugin、memory 归入配置管理子视图；现状、来源、证据、diff、测试和写入边界分开显示。地图保留总览入口，设置只管理看板自身偏好、刷新、数据源和缓存。
- **统一写入安全底座**：所有写入复用权限校验、逐项 diff、一次性显式确认、执行后读回、独立批准回滚和脱敏审计；绑定 workspace / agent / 资源范围，支持 CAS、活动任务保护、失败即停，未知结果先对账。Atlas 本地只追加脱敏日志为主，同时向对应 Multica issue 或 agent 写一条脱敏评论；未来服务端审计接口可迁移。实现口径：护栏暂按 ACM-458；不得把 ACM-390 当作本底座的执行单（390 为跨板块深链）。统一设计真实单号待核。
- **首个写入热点**：模型与 Runtime 切换直接进入实现，不只做底座和预览；支持单 agent 及明确选择的批量切换，默认不跨 workspace，配置已写入与实际生效分开显示。批量无原子语义时逐项执行并失败即停。
- **第二个写入热点**：instructions / skill 修改纳入版本、diff、评估集、逐项确认、读回、回滚和审计。
- **跨板块跳转与文案清理**：执行已决定的 Agents ↔ Skills ↔ MCP、运行状态 ↔ 配置管理跳转；统一红=坏、黄=待处理、灰 `?`=未知，未复核且无其他问题的行淡化；复制失败和只读提示统一。通用组件改名另开清理单，不阻塞写入热点。

## 基建与文档

### 现在
- 无未交付的当前焦点。已交付项见本节「已交付」。
- **9router Gemini 代理故障（位置待用户定）**：修复 Gemini 经 Node 代理 TLS/ECONNRESET。属运维而非 Atlas 产品；2026-11-24 该单为 todo、无指派，自 10-29 起未推进。在用户另批「移入不做」或指定位置之前，保持待定，不擅自移走，也不当作本轮执行项。[ACM-734](mention://issue/01a1889a-41af-7939-91c7-f48edc9efddd)

### 下一步
- 无已开单的执行项。契约与设计口径的后续核对并入本节「以后」的文档一致性清理，不以已完成的 MCP 重做为当前焦点。

### 以后
- **文档一致性清理**（未开单）：同步 frontend-design、api-contract、runtime-plan-contract 和 README 中被新设计取代的文字；只改确有证据的差异，不把旧 fixture 或历史数字当实时事实。
- **扩展额度来源 / 采集通道**（未开单；来源 ACM-160 / ACM-299）：9router、Orca 等未接入来源先研究可用只读接口；规模增大再评估 API adapter / WebSocket，不提前重构。
- **Schema 重建连接异常**（条件性后续，未开单；来源 ACM-374）：若补验复现 `sqlite3.ProgrammingError: Cannot operate on a closed database`，单开缺陷，不把一次恢复认作根因已修。

### 不做
- 不增加 `docs/maintenance.md`，不改根 AGENTS 指针；不在路线图中混入日常运维台账、业务 workspace 工作或 Devin CLI 原 paused project 的范围。

### 已交付
- **CHANGELOG 与专用规则**：只整理已合入 main 的用户可感知变化；Atlas 嵌套 AGENTS 仅写 CHANGELOG 硬规则，配仓库级 changelog skill。以 ACM-381 最新评论替代旧维护文档方案。[ACM-381](mention://issue/01a169bc-eb2c-73c8-beba-5974df2d9352)（done）
- **契约与设计口径曾与 MCP 重做同步核对**的执行单 [ACM-378](mention://issue/01a16937-2128-7e42-bc9f-116674a36295)、[ACM-377](mention://issue/01a16937-1a43-7a15-add9-368e4f5b850f) 均为 done。

### 部署前提
- multica-board 每个 PR 必须在同一 PR 内带 CHANGELOG 条目（见 `multica-board/AGENTS.md`），否则不进入合并；usage-collector 经 PYTHONPATH 直接读 `~/Workspace/agents-lab`，该类代码改动的部署是 fast-forward 同步，不重启采集任务，模板与已安装 plist 不互相覆盖。本文只改 `docs/roadmap.md`，不含代码或部署产物；合并后不触发看板部署。

## 搁置（原题照录，不建议恢复）

本区不排先后、不建议重启，不改变原标签、状态或负责人。

- [ACM-235](mention://issue/01a14acd-77aa-7c6e-8f08-b16451976f3e)：Atlas：仓库与 Multica project 两层抽象及 Skills 多仓库视图
- [ACM-224](mention://issue/01a14a86-8d0d-7420-af13-c3a88cbc6e88)：方案：用 Wakeups 替代副调度转发旁路

其他历史搁置范围：ACM-34–38（Devin CLI）与 ACM-60（存储清理）不属 Atlas 产品路线，保持原项目 / 历史记录，不在本稿建议恢复。
