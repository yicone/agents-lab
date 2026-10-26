# Changelog

记录已合入 `main` 的用户可感知变化，采用 Keep a Changelog 的分类方式。无正式版本号，以下日期为 PR 合并日期（UTC），**不是部署或上线日期**。核对基线：`537e5fec07d239504cd86592d4a1caa55a1ff341`（2026-10-24 核对）。以下为合并历史，部署证据未完整核实，不据合并推断已上线；已找到的历史部署记录见文末，未覆盖项仍待补证。纯文档、纯测试、内部重构及已撤回的图标方案不单列。

## Unreleased

### 变更

- 本地候选（未合并、未部署）：Skills 接入本地实时重叠检测，覆盖新安装技能，分别展示检测证据与人工标记；检测缓存并在失败时明确回退。（ACM-357；无 PR）

- 指令目录与当前文档中的两位设计 agent 名称统一为「UI 设计师」，保留历史运行与回滚证据。（[PR #108](https://github.com/yicone/agents-lab/pull/108)；[合并提交 `eb1f9ea`](https://github.com/yicone/agents-lab/commit/eb1f9ea572d706db44a7e7c2daab6fdbb979dacf)；ACM-518；已合并、已部署到 8765，部署事实据 ACM-542 发布前补记指示确认，非本轮部署实测）

- 本地候选（未合并、未部署）：模型配置预览显式展示可用性三态；不可用模型需额外确认，提交时复核预览绑定的账号证据。现有生产 runtime 缺可靠账号绑定来源时明确显示未知。（ACM-513；仅本地提交，无 PR）

- 本地候选（未合并、未部署）：新增策略特性开关，独立写入令牌默认关闭，可显式恢复；只读 API 展示开关状态，审计记录认证模式，其余写入防护保留。（ACM-521；仅本地提交，无 PR）

- 本地候选（未合并、未部署）：导航统一为「收件箱 / 运行监控 / 配置管理 / 地图」，收件箱包含「待批准 / 全部事项」，占位页改名「服务健康」；顶栏红色数字仅统计待批准，保留旧路由与未知态。（ACM-505；无 PR）

- 本分支候选（未合并、未部署）：历史指标按父 issue done 回补代理交付，与台账分列；使用 Atlas profile 读取全部 workspace，输出每日与 70% 示例流速读数。（ACM-470；获批开 PR 后补链接）

- 顶栏按「收件箱、运行监控、配置管理、地图」组织，设置使用齿轮入口；旧书签兼容跳转。处理队列覆盖全部 workspace，缺失数据明确显示未知。（[PR #96](https://github.com/yicone/agents-lab/pull/96)；ACM-459；未合并、未部署）

### 修复

- 本地候选（未合并、未部署）：修复收件箱窄屏横向溢出与标题断行，Agent 表格可横向滚动，runtime 汇总卡片窄屏单列、宽屏网格等宽；只读实例或授权码开关关闭时不显示授权码输入框，开关关闭且支持写入时自动加载配置目录，只读提示为「本实例只读。」。（ACM-542；仅本地提交，无 PR）

- 本地候选（未合并、未部署）：地图工具入口、层侧栏和未接入引导中的三处旧称统一为「全部事项」，路由保持不变。（ACM-505 / ACM-520 返修；无 PR）

- 二级导航与一级导航样式统一并压缩高度，页面统一左右边距并移除标题上方多余空白；模型与 Runtime 页取消 1100px 居中上限、从统一左边距铺满容器，段落与表单控件单独限宽保持可读性。（[PR #104](https://github.com/yicone/agents-lab/pull/104)；ACM-510；未合并、未部署）

- 本分支候选（未合并、未部署）：全部事项的未知态仅按当前 workspace 判断；全局异常摘要明确标注范围。两种 Agent 页面显示可移除的单 Agent 筛选，切换 workspace 自动解除，避免误报空列表。（ACM-498 / ACM-500 返修；仅本地提交）

- 本分支候选（未合并、未部署）：统一顶栏为收件箱 / 运行监控 / 配置管理 / 地图；范围与数量随筛选变化，配置默认进入 Agent 配置；区分状态与配置列，最近运行补齐任务、agent、UTC+8 时间和历史入口。（ACM-498；仅本地提交）

- 本分支候选（未合并、未部署）：模型与 Runtime 编辑区及确认弹窗明确提示账号模型可用性未校验、保存后下一次任务仍可能失败，区分 runtime 在线与模型可用。（ACM-489；仅本地提交，获批开 PR 后补链接）

- MCP 空桶计数使用灰色，部分来源读取失败时保留来源卡数字并标明不确定性。（[PR #91](https://github.com/yicone/agents-lab/pull/91)；ACM-454；已合并，部署待核实）

- 本分支候选（未合并、未部署）：completed run 的最终输出若为明确上游处理错误，单列为交付异常，展示证据及独立计数，不改任务状态、不自动重跑。（ACM-409；仅本地提交，获批开 PR 后补链接）

### 新增

- 本地分支候选（未合并、未部署）：新增只读 `flow-check` 巡检，默认增量读取所有 workspace 的近 48h 变更 timeline，台账与代理分栏，用已确认下限/候选上限比较批准的 4.5 件/天阈值，附提速线索口径和缺数原因；支持 Board 状态观测导出及指定窗口对账。（ACM-486；仅本地提交，无 PR）

- 本分支候选（未合并、未部署）：增加只读效能指标和流速命令、四周历史回补与覆盖率报告，变更事件保留 90 天；缺证据保持未知。（ACM-462；获批开 PR 后补链接）

- 本分支候选（未合并、未部署）：配置管理新增单 Agent 模型与 Runtime 切换，复用预览、一次确认、执行前冲突与活动任务检查、读回、独立确认回滚和脱敏审计；写入口默认关闭，仅限回环，批量后置。（ACM-458；获批开 PR 后补链接）

- 本分支候选（未合并、未部署）：支持多个监听 IP 和精确 Host/Origin 白名单，允许受信任 LAN / tailnet 直连；默认保持回环只读访问。（ACM-424；当前仅本地提交，获批开 PR 后补链接）

## 2026-10-23

### 新增

- 增加只读 MCP 板块，展示 workspace 库、agent 分配、待处理问题及详情。（[PR #73](https://github.com/yicone/agents-lab/pull/73)；[合并提交 `268e4ad`](https://github.com/yicone/agents-lab/commit/268e4ad064fa62ea59b37f9d2ed4fba5126fa13d)；ACM-370）
- Skills 可升级栏接入只读 npm / GitHub 版本检查，失败或不可比较时保留未知状态。（[PR #67](https://github.com/yicone/agents-lab/pull/67)；[合并提交 `b667131`](https://github.com/yicone/agents-lab/commit/b667131f53ccda2709080399da634898431f7f55)；ACM-358）
- Agents 支持用户手动补采其他 workspace 的 agent 清单，并显示快照来源和失败状态。（[PR #69](https://github.com/yicone/agents-lab/pull/69)；[合并提交 `01e8a22`](https://github.com/yicone/agents-lab/commit/01e8a228569c0f3bd9cc9ed068fadb870decafd4)；ACM-332）

### 修复

- 恢复窗口和跨站顶层页面导航不再被误拦截，API、子资源及 Host/Origin 防护保留。（[PR #75](https://github.com/yicone/agents-lab/pull/75)；[合并提交 `d00aabb`](https://github.com/yicone/agents-lab/commit/d00aabbbd686707c895b0e2a89a5102db6525787)；ACM-375）
- Skills 优先比较内容指纹，修正未登记项计数和状态，明确 Multica Workspace 与 Grok Bot 来源。（[PR #71](https://github.com/yicone/agents-lab/pull/71)；[合并提交 `5f60a64`](https://github.com/yicone/agents-lab/commit/5f60a642bd51d082a585d596109d73eecb974be1)；ACM-349）

## 2026-10-22

### 新增

- Skills 支持 Markdown 正文、文件树、有限来源 diff 和动态 runtime 列，修复附件重试与刷新状态丢失。（[PR #65](https://github.com/yicone/agents-lab/pull/65)；[合并提交 `2fae0d2`](https://github.com/yicone/agents-lab/commit/2fae0d2524c94bf4b2909a64e3cd9908a81cfb99)；ACM-337 / ACM-336 / ACM-331）

## 2026-10-21

### 新增

- Skills 增加固定表头、云端安装来源筛选和正文查看器，缺接口时明确报错。（[PR #61](https://github.com/yicone/agents-lab/pull/61)；[合并提交 `0bbcfe2`](https://github.com/yicone/agents-lab/commit/0bbcfe20e338cd83f9fb1a4dd50b5257f2d0f793)；ACM-312）

### 变更

- 统一 runtime 标识，明确地图工具告警与所属列。（[PR #60](https://github.com/yicone/agents-lab/pull/60)；[合并提交 `06de079`](https://github.com/yicone/agents-lab/commit/06de0790ec0615f48ef9ff5c7c7db3d472fb241d)；ACM-311）

### 修复

- 启动采集时避开工作目录中的 queue.py，避免看板无法启动。（[PR #63](https://github.com/yicone/agents-lab/pull/63)；[合并提交 `5dfb305`](https://github.com/yicone/agents-lab/commit/5dfb305248b4e5d58a313f52707d1a673c1a3f55)；ACM-324）
- Skills 保留递归目录与同名条目，避免安装可见性被治理去重吞掉。（[PR #62](https://github.com/yicone/agents-lab/pull/62)；[合并提交 `95408d3`](https://github.com/yicone/agents-lab/commit/95408d33aeeff02bc4524d05d99044493c35d1cc)；ACM-307）

## 2026-10-20

### 新增

- Agents / Bots 抽屉展示 Git 真源状态、逐行 diff 与复制按钮。（[PR #56](https://github.com/yicone/agents-lab/pull/56)；[合并提交 `55e49d9`](https://github.com/yicone/agents-lab/commit/55e49d973b848bf0267185f73da87c7dac428c60)；ACM-294）
- 用量页分列展示 Multica 用量、外部配额和失败信息。（[PR #57](https://github.com/yicone/agents-lab/pull/57)；[合并提交 `69b59f5`](https://github.com/yicone/agents-lab/commit/69b59f5e3f4057e7b861148a208b8689eed7a378)；ACM-299 / ACM-301）
- Skills 页展示 Multica 云端 Skills 与本地跨层漂移。（[PR #55](https://github.com/yicone/agents-lab/pull/55)；[合并提交 `20bbfa1`](https://github.com/yicone/agents-lab/commit/20bbfa1d99eb9802695f6271d9a0c1ce3eaeaff0)；ACM-292）
- Agents 增加 Grok Bots 子视图及脱敏 Instructions / Skills 详情。（[PR #54](https://github.com/yicone/agents-lab/pull/54)；[合并提交 `bf0cf7d`](https://github.com/yicone/agents-lab/commit/bf0cf7d743fab40dd94a1f605a645e88cde1e7e4)；ACM-293；采集后端见 PR #53 / ACM-291）

## 2026-10-19

### 新增

- 增加切换计划列表、审阅页与两个只读 GET，执行操作保持禁用。（[PR #50](https://github.com/yicone/agents-lab/pull/50)；[合并提交 `250023b`](https://github.com/yicone/agents-lab/commit/250023b97c5583f5460596b8ee78e0d1da0ebead)；ACM-285）

### 修复

- 修正计划组装命令的 API origin 读取，runtime 下拉按名称去重并使用各 workspace 对应 UUID。（[PR #52](https://github.com/yicone/agents-lab/pull/52)；[合并提交 `d855930`](https://github.com/yicone/agents-lab/commit/d8559309624a2dc7caf894def30b467013f33904)；历史 ACM 关联未查明；单条例外批准见文末）
- 进入切换计划列表或详情时重新读取数据，并清除多余 null 文本。（[PR #51](https://github.com/yicone/agents-lab/pull/51)；[合并提交 `e0f9530`](https://github.com/yicone/agents-lab/commit/e0f9530afbecf1e22716d06720a58d3bdf94dc08)；ACM-286）

## 2026-10-18

### 变更

- Skills 展示共享目录入口多态与多来源证据，修正 Harness 路径归属。（[PR #48](https://github.com/yicone/agents-lab/pull/48)；[合并提交 `a6dde1f`](https://github.com/yicone/agents-lab/commit/a6dde1f359ce6363ae5757c8dd4cf4b0ad4d0ec1)；ACM-232）

### 修复

- Skills 按词法路径去重仓库条目，修复窄屏溢出和 null 渲染。（[PR #49](https://github.com/yicone/agents-lab/pull/49)；[合并提交 `ae49528`](https://github.com/yicone/agents-lab/commit/ae49528da486343073baa5c8fd9e2acdcf849790)；ACM-232）
- workspace 与页签筛选逐步进入浏览历史，支持逐步回退和窄屏页签。（[PR #47](https://github.com/yicone/agents-lab/pull/47)；[合并提交 `c9591bf`](https://github.com/yicone/agents-lab/commit/c9591bf2720d4f1d4c8ac5ea3f08018530b3182d)；ACM-260）

## 2026-10-17

### 新增

- 增加只读 Skills 清单、来源与待处理信息。（[PR #44](https://github.com/yicone/agents-lab/pull/44)；[合并提交 `6ded569`](https://github.com/yicone/agents-lab/commit/6ded5690e6489b4955c05c0a73925d91a255fa0b)；ACM-182）
- 增加只读用量快照，显示配额、token 用量及采集失败状态。（[PR #32](https://github.com/yicone/agents-lab/pull/32)；[合并提交 `6d7ca36`](https://github.com/yicone/agents-lab/commit/6d7ca36286b9c96df62f3a87641a7e744e1f3d58)；ACM-185 / ACM-187；关联依据：ACM-185 评论 `01a1499a-8316-748e-9e1b-264475347702`）
- 增加 Agent Atlas 地图、层视图及 Issues 层筛选。（[PR #31](https://github.com/yicone/agents-lab/pull/31)；[合并提交 `efb90bf`](https://github.com/yicone/agents-lab/commit/efb90bfaa65228561ac4bc6a77357314f4dd91bc)；ACM-193；层解析后端见 PR #30 / ACM-192）
- 增加 ACM Lab「待批准」队列。（[PR #29](https://github.com/yicone/agents-lab/pull/29)；[合并提交 `e37c7cc`](https://github.com/yicone/agents-lab/commit/e37c7ccd34a789972c7d812704a9233207f98ba9)；ACM-191）

### 修复

- Issues 和 Agents 保留共用 workspace 筛选及清除入口。（[PR #46](https://github.com/yicone/agents-lab/pull/46)；[合并提交 `7b318fd`](https://github.com/yicone/agents-lab/commit/7b318fda6e3e5bf2c4fd21002ddcecc1faf68fd1)；ACM-230；关联依据：ACM-244 评论 `01a14b05-0e00-70e2-bbf2-95921f8dd62b`）
- 自动补齐旧缓存缺少的任务失败原因字段，避免读取失败。（[PR #33](https://github.com/yicone/agents-lab/pull/33)；[合并提交 `dcc5d7a`](https://github.com/yicone/agents-lab/commit/dcc5d7a05536251f015239b8c8cf7b334ca4b9f2)；ACM-197）

## 2026-10-16

### 新增

- 待处理摘要纳入未被状态提醒覆盖的 agent 交付。（[PR #24](https://github.com/yicone/agents-lab/pull/24)；[合并提交 `49ab669`](https://github.com/yicone/agents-lab/commit/49ab669f735f8c3e86035ef5ae5b1edba26b676b)；ACM-179）
- 待处理摘要提示缺少异步委派技能的 Hermes agent。（[PR #19](https://github.com/yicone/agents-lab/pull/19)；[合并提交 `3a9b506`](https://github.com/yicone/agents-lab/commit/3a9b506b9c71dd485d1b37cbda49fa5b7b33159a)；ACM-174）

## 2026-10-15

### 新增

- 增加待处理摘要、增量变更流和失败统计，并采用叠层看板图标 B。（[PR #12](https://github.com/yicone/agents-lab/pull/12)；[合并提交 `b715b0c`](https://github.com/yicone/agents-lab/commit/b715b0c3536c9c04489b5174fd148a177fd25a1e)；ACM-165 / ACM-166 / ACM-167 / ACM-170）
- 提供跨 workspace 只读看板、采集健康状态、Issues / Agents 视图及运行历史抽屉。（[PR #11](https://github.com/yicone/agents-lab/pull/11)；[合并提交 `bda4ed4`](https://github.com/yicone/agents-lab/commit/bda4ed4ad9572392c15432c4f3f986425655d955)；ACM-161 / ACM-162 / ACM-163）

## 历史证据与待补证

- PR #32：ACM-185 评论 `01a1499a-8316-748e-9e1b-264475347702`（2026-10-17 11:23:45 UTC）由用户确认 main `6d7ca36` 已合并且 Atlas 已重启；当时 usage collector launchd 尚未安装，不能据此称其已启用。关联实现 ACM-187。
- PR #46：ACM-244 描述将 PR #46 / 完整 SHA `7b318fda6e3e5bf2c4fd21002ddcecc1faf68fd1` 明确关联 ACM-230；评论 `01a14b05-0e00-70e2-bbf2-95921f8dd62b`（2026-10-17 17:59:44 UTC）记录 main 同步到该 SHA、Board 重启、HTTP 200 和真实 UI 复验。记录中的本地启动时间不替代 UTC 评论时间。
- PR #52：历史 ACM 关联未查明；已核实 PR、完整 merge SHA `d8559309624a2dc7caf894def30b467013f33904` 与 UTC 合并日期 `2026-10-19`。用户在 ACM-381 评论 `01a169f5-3720-7dfd-ae32-9bf96338167f`（2026-10-23 18:10:40 UTC）批准仅此条缺 ACM 关联的例外，其他条目不豁免；该批准不证明当时未开单，也不授权推送、开 PR、合并或部署。
- 其余条目部署日期与结果尚未逐项核实；不得作为完整的已上线清单。以上均为历史记录引用，本轮未重启服务或重做线上验收。
