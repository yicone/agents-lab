# Agent Atlas 信息结构与设计系统（ACM-377）

本文是 Atlas 新板块设计和验收的入口。内容按 2026-10-23 `main`（`268e4ad`）的代码整理，以代码为准；和 `frontend-design.md`、`mcp-design.md` 冲突的地方在第 6 节列出。各板块的需求细节仍在 `frontend-design.md`，接口在 `api-contract.md`。

读法：设计新板块时先看第 1 节定位，再按第 2 节选版式，用第 3、4 节的组件和视觉规范，最后逐条过第 5 节清单。第 6 节是现有不一致的清理待办，本单不修。

标记约定：「现状」是代码里已经这样做的；「规则」是新板块必须遵守的，均来自现有 Skills 做法；「暂定」是现有代码互相矛盾、本文先选了一种，等用户在第 7 节拍板。

## 1. 信息结构

### 1.1 数据分层

Atlas 的每个数字都来自下面四层之一。板块设计稿必须写明每一列、每个计数属于哪一层，因为它决定了筛选方式、刷新节奏和「没数据」的含义。

| 层 | 是什么 | 例子 | 刷新 |
|---|---|---|---|
| 本机 | 这台 Mac 上的文件和进程 | 磁盘上的 skill 入口、工具技能根扫描、Grok Bot 花名册（sand-client）、CodexBar 额度快照、SQLite 缓存 | 每次请求或用户手动采集 |
| 仓库 | `agents-lab` Git 里的声明 | `skills-inventory.json`、`tool-roots.json`、`instruction-sources.json`（instructions 的 Git 真源）、`mcp_support.py` 的 runtime 加载表 | 随提交变化 |
| Multica workspace | 通过 `multica` CLI 采集的 workspace 资源 | issue、project、层属性、runtime、workspace 已安装 skill、MCP 库 | 每 60 秒一轮 |
| agent | 单个 agent 的配置 | instructions、skill 绑定、MCP 分配和自有 `mcp_config`、runtime / 模型 / 推理 / Tier | 随 workspace 一起采集 |

规则：一个页面混用多层时，层与层之间不相加、不合并成一个状态。例子有 Skills 的「Multica Workspace」列和本机工具列分开，Usage 的 Multica token 和 CodexBar 额度分栏。

### 1.2 顶栏板块

| 顶栏 | 地址 | 回答的问题 | 数据层 | workspace 筛选 |
|---|---|---|---|---|
| 地图 `1` | `#/map`、`#/map/lN` | ACM Lab 的 issue 落在五层的哪里，各层有哪些工具、有没有告警 | Multica workspace（只有 ACM Lab）+ 各板块的信号 | 不显示 |
| 待你决定 `2` | `#/decide` | 现在有哪些事等我拍板 | Multica workspace（只有 ACM Lab）+ 状态迁移 | 固定范围说明 |
| Issues `3` | `#/issues` | 哪些 issue 现在需要我看 | Multica workspace（全部） | 共享筛选条 |
| Agents `4` | `#/agents` | 每个 agent 跑在哪个 runtime / 模型上，哪里在出错 | agent + Multica workspace 的 runtime；Grok Bots 子视图是本机；instructions 对照仓库 | 共享筛选条 |
| Skills `5` | `#/skills` | 这些 skill 装在哪，哪些要处理 | 本机 + 仓库，加上 Multica workspace 安装和 Grok Bot 名称声明 | 固定范围说明 |
| MCP `6` | `#/mcp` | 同名 server 在本机工具、仓库项目级和各 workspace 的配置是否一致、哪些不会生效 | 本机声明文件 + 仓库项目级 + Multica workspace 的库与 agent；加载表来自仓库 | 固定范围说明 + 数据源健康条（`meta.sources`） |
| 用量（非顶栏） | `#/usage` | Multica 运行、外部额度、失败各是多少 | 本机快照 + Multica run 缓存 | 不显示 |

现状：

- Agents 有两个子视图，不占顶栏：Grok Bots（`?kind=bots`）和切换计划（`?plan=1`、`#/agents/plan/new`、`#/agents/plans[/<id>]`），由工具栏的 `kindSwitch` 切换。用量只从地图 L1 进入。
- 顶栏三个角标：待你决定（ACM Lab 待验收 + 受阻）、Skills（`attention.badge`，基线债不计入）、MCP（`attention.badge`：五桶条数之和，待清理桶不计入；`attention.tone=unknown` 时显示灰色 `?` 不计数，见 `mcp-redesign.md` §5）。

规则：新板块加不加顶栏，看它是不是一个独立的问题、数据层和筛选维度是否不同。MCP 单独成栏的理由是「Skills 属于本机，MCP 属于 workspace」（`mcp-design.md`），新板块照这个标准写理由。只是某个板块的另一种看法时，做成子视图（参考 Agents 的 `kindSwitch`），或者只从地图进入（参考用量）。

### 1.3 顶栏三行

`index.html` 的 `.topbar` 固定为三行，所有板块共用，内容由 `app.js` 的 `renderChrome()` 填写。

1. **标签行**：品牌、标签、`#snapshot` 状态、搜索框。状态默认是全局采集状态（`health()`），按 `frontend-design.md`「顶栏状态」表取第一个命中的。Skills、MCP、计划目录换成本页数据源的时间（`skillsSnapshot()`、`mcpSnapshot()`、`planSnapshot()`）。
2. **`#banner`**：只在全局采集失败或全部 workspace 失败时出现。单个 workspace 的问题不上这一行。
3. **`#health`**：由 `filter-bar.js` 的 `filterChrome(view)` 决定形态：
   - `shared`（Issues、Agents）：「当前：X」+「清除」+ 每个 workspace 一个 `.ws-chip`，点击即写 `ws=<slug>`。
   - `decide` / `skills` / `mcp`：一行 `.scope-note` 写死范围，说明 workspace 筛选不作用于本页。Skills 和 MCP 后面再跟数据源健康条（`meta.sources` 里的每一项一个 `.src-chip`，MCP 的七个来源见 `mcp-redesign.md` §5.1）。
   - `none`（地图、用量）：隐藏。

规则：新板块必须选这三种之一。数据不分 workspace 时用固定范围说明，不要显示一排点了没反应的 workspace 标签。

### 1.4 全局搜索和 workspace 筛选

- 搜索框只有一个，值写在 hash 的 `q=`，切换板块时保留（`applyPatch` 不清 `q`）。每个板块自己决定匹配哪些字段，并在 `render()` 里换 placeholder。`/` 聚焦，`Esc` 先关抽屉再清空。
- `ws=` 同样跨板块保留；只有 `shared` 形态的板块使用它，其他板块不显示也不读。
- 历史记录（`route.js` 的 `historyWrite`）：换板块、换 workspace、进出计划子页用 push，其他筛选和搜索用 replace，相同地址不写。
- 抽屉不进 URL。刷新页面后抽屉关闭。

规则：新板块的筛选参数要进 hash（在 `parseHash` / `formatHash` / `applyPatch` 里登记，离开板块时清掉），并写明搜索匹配哪些字段。

### 1.5 板块之间的跳转

现状里的跳转：

| 从 | 到 | 方式 |
|---|---|---|
| 地图 | 各板块 | 每层的工具行（`layers.js` 的 `MAP_TOOLS`），带一句信号（`app.js` 的 `toolSignal`）和告警圆点 |
| 地图 / 层视图 | 待你决定、Issues | 「查看全部 N 条」→ `#/decide?layer=`；层视图侧栏 → `#/issues?layer=`、`#/decide?layer=` |
| 地图预览、待你决定、Issues、层视图 | 运行记录抽屉 | 点行或卡片 |
| 待你决定 | Issues | 页脚「在 Issues 中查看」 |
| 用量 | 地图 L1 | 「返回 L1 地图」 |
| 板块内 | 同板块的行 | Skills 待处理条目调用 `focusSkill`：清筛选、展开分组、滚到行、打开抽屉 |
| 任意 issue / agent | Multica | 新窗口外链（`extLink`） |

缺口：Agents 抽屉的「Skills 绑定」只列名字，跳不到 Skills；Skills 抽屉列出已启用的 agent，跳不到 Agents；MCP 不链接 Agents 或 Skills。是否补跨板块跳转见第 7 节。

规则：新板块必须在地图上有一个工具行入口（`MAP_TOOLS` + `toolSignal`），并在设计稿里列出它进出的跳转。

## 2. 页面版式模式

Atlas 现在有七种版式。新板块先选一种，偏离的地方逐条写理由。

| 版式 | 范本 | 适合的问题 |
|---|---|---|
| A 清单治理 | Skills | 一类资源装在哪、分布在哪些工具或 workspace、哪些要处理 |
| B 关注栏 | Issues | 按紧急程度分栏的事件 |
| C 等待队列 | 待你决定 | 单一队列，按等待时长排序 |
| D 配置矩阵 | Agents | 每个实体一行，看配置和健康 |
| E 地图与层视图 | 地图 | 导航和汇总，不做处理 |
| F 分栏对照 | 用量 | 口径不同、不能相加的几组数 |
| G 流程子页 | 切换计划 | 多步生成一份产物 |

选择方法：问题是「东西在哪、哪些不对」，用 A；是「哪些事要我处理」，用 B 或 C；是「每个实体配成什么样」，用 D。MCP 这类「workspace 资源 × agent × runtime」的治理问题属于 A。

### 2.1 版式 A：清单治理（Skills 范本）

代码：`web/skills.js`（`renderSkills`、`renderSkillDrawer`），后端 `backend/multica_board/skills_view.py`（`build_payload`、`assign_buckets`、`_attention`、`_summary`）、`skill_upgrade.py`，CSS 在 `styles.css` 的 `/* skills */` 段。

从上到下：

```
┌ 来源卡片 .source-grid（全部 · Skills CLI · 自建 · … · Multica Workspace · 已安装）┐
├ 工具栏 .toolbar：来源▾ 状态▾ 工具▾ 分组▾ □只看定制                              ┤
├ 异常提示条 .skills-anomaly（只在采集本身有问题时出现）                           ┤
├ 图例 .skills-legend                                                              ┤
│ ┌ 一览表 .table-wrap.sticky-head ───────────────────┐ ┌ 待处理 .skills-queue 352px ┐ │
│ │ ▸ 分组行 自建 · 12                                │ │ Multica Workspace 待核对 1 │ │
│ │   name  来源/通道  Workspace  共享  ● ○ ✕ …  版本  │ │ 缺失 / 坏链 0  无          │ │
│ │   … 显示其余 4 条                                 │ │ 版本已变 3                 │ │
│ └───────────────────────────────────────────────────┘ │ … 基线债 · 从未复核 76     │ │
│                                                         └───────────────────────────┘ │
└ 右侧详情抽屉 #drawer（点行打开）                                                     ┘
```

**来源卡片**（`sourceCard`、`sourceBlocks`）

- 卡片是一览表行的**分区**：「全部」加上每个 `source_class` 一张，后端 `_summary()` 给出每块的数量和按对账状态的拆分。末尾前端合成一张「Multica Workspace · 已安装」，它是伪来源（`CLOUD_SOURCE`），按「有没有 workspace 安装」筛选。
- 卡片内容：标签、大号数字、状态拆分（有问题的状态用 `.bad-n`，未复核和一致用 `.muted`）。数据没采集时数字写「—」并说明原因，不写 0。
- 点卡片等于设置「来源」下拉，再点一次取消。卡片和下拉写同一个 `source=` 参数。

**工具栏**：`.toolbar` 里放 `.filter`（标签 + `select`）和 `.check`（复选框）。每个控件对应一个 hash 参数。

**异常提示条**（`anomalyBanner`）：只说采集或扫描本身的问题，例如共享根不存在、无权限、后端数据版本较旧。单个 skill 的问题不上这里，进待处理栏。没有异常时不渲染。

**图例**（`legend`、`RUNTIME_LEGEND`）：页面上每一种符号和状态词都要在图例里有解释，按列分组（共享入口、Harness、仓库入口、Multica Workspace）。列头 `title` 再放一遍图例和该列的证据来源（`columnEvidence`）。

**一览表**（`table`、`row`、`groupedItems`）

- 一张表。分组用 `.skill-group` 表头行，默认按来源，可切按工具或不分组（`group=source|tool|flat`）。按工具分组时同一 skill 会出现在多组，组标题要写「同一 skill 可能重复出现」。
- 组内排序：有待处理的在前，再按名字（`byAttention`）。每组先显示 8 行（`GROUP_LIMIT`），其余放在「显示其余 N 条」里。
- 一行一个对象，整行可点（`tabindex="0"`，`Enter` 也能打开），选中行加 `.selected`。
- 列的顺序：身份列（等宽名字 + 范围标签 + 漂移标签）→ 来源 / 更新通道 → 跨层的 Multica Workspace 列 → 共享入口 → 每个工具一列 → 仓库入口 → 版本 → 对账 → 定制 · 约束 → 复核。规则：身份在最左，状态和复核时间在最右，跨层的列和本机的列分开。
- 长文本列定宽加省略号（`.channel-col` 164px、`.version-col` 112px、`.cell-ellipsis`），全文放 `title`。

**每个工具一列的符号**（`toolCell`、`MARK`、`markTitle`）

| 符号 | 含义 | 样式 |
|---|---|---|
| `●` | 有直接入口 | 正文色 |
| `○` | 经共享目录可见 | `.shared` 灰 |
| `✕` | 入口或版本文件不在；MCP 里表示「条目损坏」（有同名条目但不能用：缺启动目标、类型无法识别、字段类型不对） | `.missing` 红 |
| `?` | 无法确定（权限、读取失败、未配置技能根） | `.unknown` 黄 |
| `∅` | 不适用（只存在于 Multica Workspace，或者是 Grok Bot 名称声明） | `.not-applicable` 灰 |
| 空白 | 已配置的根里没找到，仍可能从别处加载 | — |
| `●链`（上标） | 只有链接目标在该工具的根里 | `.via-target` 蓝 |

列头写工具名，下面用 `<small>` 写可见数。格子的 `title` 写证据路径和「路径推导，未验证 runtime 实际加载」。规则：一格只放一个符号；「不适用」「无法确定」「没有」三者必须分开显示。

**cloud 区块**：跨层数据的统一做法。Skills 里是「Multica Workspace」这一层：

- 列（`cloudCell`）：最多列 3 个 workspace（`CLOUD_PREVIEW`），每个附已启用 agent 的简写（`agentBrief`，两个名字 + `+N`），其余写「另有 N 个 Workspace」。没采集写「—」，采集了但没装写「Multica Workspace 未安装」。和本机不一致的 workspace 用 `.cloud-ws.stale` 标黄。
- 漂移判断（`cloudDrift`）：先比内容哈希，不同时只说「内容不同」，不推断新旧；没有哈希才按时间推断，并注明依据。
- 待处理栏最上面一桶「Multica Workspace 待核对」。
- 抽屉里一节「Multica Workspace（跨 Workspace）」，每个安装一块 `.cloud-install`，带该 workspace 的命令。

**待处理栏**（`queue`、`bucketBlock`、`queueModel`）

- 分桶在后端完成（`assign_buckets`），前端只显示。桶的顺序固定（`BUCKETS`）。一个对象只进一个桶，取顺序里第一个命中的；其他命中变成行上的标签（`tags`）。角标 = 各桶条数之和。
- 每桶：标题 + 计数。空桶写「无」。数据源没接上（`connected: false`）时计数写「—」，正文写「— 原因」，不写「无」。例子：升级检查请求失败或没查完时，不能显示成「没有更新」。
- 点条目调用 `focusSkill`：清筛选、展开所在分组、滚到这一行并打开抽屉。
- 最底下是不计入角标的「基线债」（虚线按钮，点击切换 `debt=1` 筛选），再下面是本栏的设置（复核阈值 30 / 90 / 180 天，存 `localStorage`）。
- 屏宽小于 1200px 时，整栏移到表格上方，只剩一行桶计数（`.skills-compact`）。

**单一详情抽屉**（`renderSkillDrawer`）

- 一个对象一种抽屉。抽屉头：等宽名字 + 范围标签 + 关闭 ×；下面 `h2.drawer-title`。例外：Grok Bot 名称声明行用的是同一个抽屉函数，但节的组成不同（`skills.js:839–844`），依次是证据范围、使用此技能的 Bot、正文 / 版本 / Runtime（都标「不适用」）、同名关联。
- 正文按节（`section()` → `h3.drawer-sub`），顺序是：来源与权责 → 基线与版本 → 共享入口 → 各类入口 → 对账发现 → 定制与兼容约束 → 正文 → 跨层（Multica Workspace）→ 在终端处理。规则：先讲「它是什么、谁管」，再讲「现在什么状态、证据是什么」，最后才是「怎么处理」。
- 字段一行一个（`line()`：灰色标签 + 等宽值，没有值写「—」）。
- 大块内容按需加载：正文点「查看正文」才请求，原文、文件树、diff 放在 `<details>` 里，重绘时保留展开状态（`data-skill-detail`）。
- 非模态。`Esc` 或 × 关闭，焦点回到原来的行（`closeDrawer` 先找 `rowId`，再退回 `opener`）。

**可复制命令**（`.command`）：`<p class="command">` 里依次放灰色说明、`<code>` 命令、「复制」按钮。按钮成功后改成「已复制」。命令区上方写只读提示（见 4.6）。页面不执行命令，也没有任何输入框。

**数据契约**（后端 → 前端，新板块照抄这个形状）

- `items[]`：一行一个对象，带 `bucket`、`tags`、`reason`（≤80 字）、`quiet`、`command`。
- `summary[]`：来源卡片用的分区块 `{id, label, count, statuses}`。
- `attention`：`{badge, counts, buckets: {<code>: {connected, reason, items}}, baseline_debt}`，另有 `?format=text`（不超过 2048 字节，给 Steward）。
- `meta.sources[]`：健康条用的 `{id, label, state: ok|bad|warn|absent|fallback, detail}`。
- `ok: false` + `error`：整份数据无效时，页面显示 `.atlas-guide`（「Skills 清单无效」+ 原因 + 怎么修），不把没检查当成没问题。

### 2.2 其他版式（摘要）

- **B 关注栏**（`renderIssues`、`attention.js`）：`.lanes` 四栏，每栏 `.lane` + 顶边状态色，`.card` 卡片。一个 issue 只进一栏，其余状态做标签。每栏 30 张，「显示其余 N 条」/「收起到 30 条」。点卡片打开运行记录抽屉。
- **C 等待队列**（`renderDecide`）：页头 `h2` + 范围说明 + `.decide-stat` 统计块；`.decide-table`，等待最久在前，超过 7 天黄、超过 14 天红；近似值前加 `≥`；页脚写口径和范围外的计数。
- **D 配置矩阵**（`renderAgents`）：`.providers` 汇总块（点击筛选）+ `.toolbar` + `table.matrix`，每组一个 `<tbody>` 和 `.group-row`。整行可点，打开带分段（`.seg.drawer-tabs`）的抽屉。`null` 显示灰色斜体「默认」。
- **E 地图**（`view-atlas.js`）：`.map-table` 网格行，数据没就绪时显示 `.atlas-guide`，不画全 0。
- **F 分栏对照**（`usage.js`）：`.usage-columns` 三栏，口径说明 `.usage-split` 放在最上面，缺口写在 `.usage-gap`。
- **G 流程子页**（`plan.js`）：`.plan-card`、吸顶 `.plan-scope`、底部吸附 `.plan-bar`。

## 3. 组件清单

页面用 `app.js` 的 `h(tag, attrs, ...children)` 拼 DOM，渲染函数接收 `ctx`（`h`、`route`、`setRoute`、`ago`、`absTime` 等）。接口数据只走 `textContent`，不用 `innerHTML`。可测试的逻辑写成不碰 DOM 的导出函数（如 `filteredItems`、`groupedItems`、`queueModel`、`cellSymbol`），由同名 `.test.js` 覆盖。

### 3.1 页面骨架和布局

| 类 / 函数 | 用途 | 用在 |
|---|---|---|
| `.main` | 主区域，`12px 16px 32px` 内边距 | 全部 |
| `.toolbar` | 一行筛选控件，`gap 14px`，12px 字号 | 全部 |
| `.skills-layout` | 主区 + 352px 侧栏，<1200px 变单栏、侧栏在上 | Skills、MCP |
| `.layer-layout` | 主区 + 240px 侧栏 | 层视图 |
| `.atlas-guide` | 整页引导卡（数据没就绪、清单无效） | 地图、Skills、用量 |
| `.empty` / `.empty.big` | 灰色居中短句；`.big` 是整页占位 | 全部 |

### 3.2 筛选和切换

| 类 / 函数 | 用途 | 说明 |
|---|---|---|
| `.filter` / `.field` | 标签 + 下拉 | 两个类做同一件事，见第 6 节 |
| `.check` | 复选框 + 文字 | |
| `.seg` | 分段按钮，`.active` 为当前项 | 视图、分组、子视图（`kindSwitch`） |
| `.ws-chip`、`.ws-clear`、`.scope-current`、`.scope-note` | workspace 筛选条和固定范围说明 | 只由 `filter-bar.js` + `renderChrome` 生成 |
| `.src-chip` | 数据源健康标签，状态 `ok/bad/warn/absent/fallback` | Skills 健康条 |
| `.is-pending` | 前置数据没就绪的控件，灰色、禁用 | 层筛选 |

### 3.3 摘要和计数

| 类 | 用途 | 交互 |
|---|---|---|
| `.source-card`（`.source-grid` 网格） | 行的分区 + 状态拆分 | 点击切换筛选，`aria-pressed` |
| `.provider`（`.providers`） | 按 provider 汇总 | 点击切换筛选 |
| `.decide-stat` | 只读统计块，`.warn` / `.bad` | 不可点 |
| `.bot-stat`、`.foot-bucket`、`.plan-counts button`、`.trio` | 各页专用 | |
| `.tab-badge` | 顶栏角标，默认黄，`.skills` 红 | |

规则：新板块的顶部卡片默认用 `.source-card` 的做法：卡片是行的分区，全部可点，点击等于设置对应筛选。只读统计用 `.decide-stat`。

### 3.4 表格

| 类 | 用途 |
|---|---|
| `.table-wrap` / `.table-wrap.sticky-head` | 表格外框；`sticky-head` 限高并吸顶表头，滚动位置在重绘时恢复（`SKILLS_LIST_SCROLL`） |
| `table.skills-table` | 版式 A 的一览表，`width: max-content`，12px，表头 `--panel-2` |
| `.skill-group` / `.skill-more` | 分组表头行 / 「显示其余」行 |
| `.skill-row`（`.selected`、`.quiet`） | 可点行 |
| `.tool-col` / `.tool-mark` | 工具列头 / 符号格 |
| `.channel-col`、`.version-col`、`.cell-ellipsis` | 定宽省略列 |
| `.entry-chip` | 小块入口标签（`● ✕ ?`） |
| `.cloud-cell`、`.cloud-ws` | 跨层列 |
| `table.matrix` / `.group-row` | 版式 D 的矩阵，13px；`td.inherit` 是灰色「默认」，`td.num` 等宽数字 |
| `.decide-table`、`.layer-table`、`.plan-table`、`.mcp-support` | 各页专用表 |

### 3.5 标签、状态和符号

| 类 | 用途 |
|---|---|
| `.tag` + `.ok/.bad/.warn/.info/.run/.muted/.prio/.quota/.review-pending/.room` | 行内小标签，11px，圆角 3px |
| `.ws-tag` | workspace 色标，色相由 slug 哈希得出（`hueOf`） |
| `.runtime-mark` | runtime 身份徽记（`runtime-mark.js`），中性色；在线状态用单独的圆点 |
| `.dot`（在 `.snapshot`、`.ws-chip`、`.src-chip`、`.rt`、`.tool` 里） | 7px 状态圆点 |
| `.err-kind`（`.quota`） | 失败类别，额度 / 限流反白 |
| `.ok-n` / `.bad-n` | 绿色 / 红色加粗数字 |
| `.not-applicable` | `∅` 不适用 |
| `.layer-bar` | 五格层条，实心主层、空心涉及层 |
| `.is-stale` | 旧数据整块降低不透明度，配「旧数据」标签 |

### 3.6 待处理栏

| 类 | 用途 |
|---|---|
| `aside.skills-queue` | 侧栏外框，`h2` 写「待处理」 |
| `.skill-bucket` | 一个桶，`header` 放标题和计数 |
| `.queue-item` | 桶内条目按钮：等宽标识 / 说明 / 灰色时间 |
| `.skills-queue .debt` | 不计入角标的计数，虚线按钮，`.active` 时实线 |
| `.skills-compact` | <1200px 时的一行桶计数 |

这些类名带 `skills` 前缀，实际已经被 MCP 复用，是通用组件。改名见第 6 节。

### 3.7 抽屉和命令

| 类 / 函数 | 用途 |
|---|---|
| `#drawer.drawer` | 唯一的右侧抽屉，`min(420px, 100vw)`，非模态；`renderDrawer()` 按 `state.drawer.kind` 分派 |
| `.drawer-head`、`.drawer-close`、`.drawer-title`、`.drawer-link`、`.drawer-sub` | 抽屉头、关闭、标题、外链、节标题 |
| `section()`、`line()` | 节和「标签 值」行（`skills.js`、`mcp.js` 各有一份） |
| `.seg.drawer-tabs` | 抽屉内分段（Agents） |
| `.command` | 可复制命令 |
| `.drawer-actions` | 抽屉内按钮行（复制全文等） |
| `.instr-body`、`.diff-block`、`.diff-add/.diff-del` | 长文本和 diff |
| `.skill-markdown` | 只读 Markdown 渲染（`skill-viewer.js`） |

### 3.8 提示条

| 类 | 用途 | 颜色 |
|---|---|---|
| `#banner.banner` | 全局采集失败 | 红边红底 |
| `.skills-anomaly` | 本页采集或扫描异常 | 黄边黄底黄字 |
| `.decide-note` | 本页数据不完整（状态迁移 reset 等） | 黄边黄底正文色 |
| `.usage-split` / `.usage-gap` | 口径说明 / 数据缺口 | 灰底 / 灰虚线 |

## 4. 视觉规范

### 4.1 色彩

全部颜色走 `:root` 变量，深色模式在 `prefers-color-scheme: dark` 里覆盖。新代码不写十六进制颜色。

| 变量 | 浅色 | 深色 | 用途 |
|---|---|---|---|
| `--bg` | `#f6f7f9` | `#111317` | 页面底色、组行底色 |
| `--panel` | `#ffffff` | `#181b20` | 卡片、表格、抽屉 |
| `--panel-2` | `#f0f2f5` | `#1f232a` | 表头、悬停、选中、分段按钮当前项 |
| `--line` | `#e2e5ea` | `#2a2f37` | 所有 1px 边线 |
| `--text` / `--muted` / `--faint` | `#1c2026` / `#6b7280` / `#9aa1ab` | `#e4e7ec` / `#9099a6` / `#6a7280` | 正文 / 次要信息 / 占位和空状态 |
| `--accent` | `#2f6fde` | `#6b9cff` | 链接、焦点环、选中边框 |

### 4.2 状态色

| 状态 | 前景 / 底色 | 含义 | 例子 |
|---|---|---|---|
| ok | `--ok` | 一致、在线、完成、会生效 | 快照圆点、`✓`、对账「一致」 |
| bad | `--bad` / `--bad-bg` | 坏了、缺失、失败、离线 | `✕`、运行失败、缺失 / 坏链 |
| warn | `--warn` / `--warn-bg` | 需要注意、过期、推断、近似、受阻 | `?`、旧数据、等待超过 7 天、Workspace 较旧 |
| info | `--info` / `--info-bg` | 待验收、未核实、未登记、选中底色 | 待验收栏、`.tag.info` |
| run | `--run` / `--run-bg` | 运行中、治理线、mock 标记 | `.tag.run`、地图治理线 |
| muted / faint | `--muted` / `--faint` | 无、未启用、不适用、未接入 | `○`、`∅`、「无」 |

`--info` 和 `--accent` 是同一个蓝。规则：状态只用这六类，颜色不能是唯一线索，必须同时有符号或文字（例如 `✕` 加红色，「旧数据」标签加降低不透明度）。

### 4.3 字号和字重

基准是 13px / 1.45，系统字体栈（含 PingFang SC），等宽用 `--mono`。

| 字号 | 用途 |
|---|---|
| 10px | mock 标记、`.runtime-mark`、`.repo-name`、`.gap-label` |
| 11px | 标签、表头、卡片头、说明小字、图例 |
| 12px | 表格正文（Skills、层、计划）、工具栏、`.mono`、命令 |
| 13px | 基准正文、`table.matrix`、抽屉正文 |
| 14px | 品牌、抽屉标题、侧栏 `h2`、整页占位 |
| 16px | 页面 `h2`、`.mcp-card` 数字 |
| 18px | 卡片大数字（`.source-card`、`.decide-stat`、`.foot-bucket`） |

字重：550 用于标题和名字，600 用于表头和当前项，650 用于页面标题和强调数字，700 只用于角标和严重告警。标识（identifier、skill 名、路径、命令、计数）一律等宽。

### 4.4 间距、圆角和尺寸

- 新代码的间距只取 2 / 4 / 6 / 8 / 10 / 12 / 14 / 16 px（现有代码里还有零星的 3、5、7、9）。表格单元格 `6px 8px`（版式 A）或 `5px 10px`（矩阵）；卡片和侧栏 `8px`–`12px`；区块之间 `10px`–`12px`。
- 圆角：标签 3px，下拉 4px，按钮和小卡 `--radius`（6px），面板、表格外框和侧栏 8px，标签胶囊 999px。
- 断点：1200px（版式 A 侧栏上移），1100px（关注栏两列、用量单栏），980px（地图单栏），900px（待你决定页头竖排），760px（顶栏标签换行），620px（关注栏单列），420px（抽屉全宽）。
- 宽度：抽屉 420px，版式 A 侧栏 352px，层视图侧栏 240px。

### 4.5 空状态、加载和错误

核心原则：**没检查过不等于没有问题**。每个数字和格子至少区分四种情况：

| 情况 | 写法 | 例子 |
|---|---|---|
| 有 | 数字或符号 | `3`、`●` |
| 确定没有 | 「无」、`0`，或者把没有的那部分折成一行 | 空桶「无」；「其余 20 个 agent 没有任何 MCP」 |
| 没采集 / 没接上 / 数据源缺失 | 「—」加原因，或者引导卡 | 升级检查「— 原因」；地图未回填时显示引导，不画全 0；`/api/mcp` 404 写「后端还没有 MCP 采集」 |
| 读取失败 | 保留上次数据并降低不透明度，加「旧数据」标签或提示条；从未成功写「未加载」 | 采集失败的 workspace 卡片；`?` 不能显示成 `·` |

文案规则（来自现有代码）：

- 加载中：「正在读取 X…」或「加载中…」。
- 空列表要说清是哪一种空：「没有符合筛选的 X」（有筛选）、「X 现在没有 Y」（真的没有）、「X 数据未采集，无法筛选」（没采集）。
- 错误原文只在提示条和 `title` 里出现，且已经脱敏；正文写原因类别和处理建议（`COLLECTOR_HINT`）。
- 不认识的枚举按「未知」显示，不把英文代码直接漏到页面上（`findingText` 的做法）。
- 「不适用」用 `∅` 加 `title` 说明，不用「—」。

### 4.6 只读提示和命令

规则：

- 页面没有写接口、没有配置输入框、不代为执行。所有修改都给可复制的命令，由用户在自己的终端运行。
- 命令区上方固定一句只读提示。**暂定**统一为「页面只读，不代为执行。复制命令后在自己的终端运行。」（Skills 现在用的这一句），后面可以再跟一句这条命令的影响，例如「下一轮采集后生效」「只影响这个 agent」。
- 涉及 Multica 资源的命令一律带 `multica --workspace-id <uuid>`，id 用 UUID，不用名字。
- 有破坏性的命令先列出影响范围，确认后才显示（MCP 删除库条目的两步做法）。
- 复制成功写「已复制」；**暂定**失败统一写「复制失败」，并用 `app.js` 的 `copyText`（带 textarea 回退）。

### 4.7 密钥

规则（来自 `README.md` 安全边界和 `mcp-design.md`「密钥处理」）：

- 页面只显示「有没有」和「几项」，例如「env 已设置 3 项」「headers 未设置」。不显示值、key 名、URL、command、args。
- 读不到时写「不可见」或「Multica 不向当前身份返回这份配置」，不能写「未设置」。
- 涉及配置内容的命令只生成 `--*-config-file ./<name>.json` 形式，下方固定提示「文件里有密钥：建议 chmod 600，用完删除，不要放进 git 仓库。」不生成内联 JSON 或 stdin 写法。
- 不显示邮箱、账号和认证信息（用量页的 `safeLabel` 会过滤邮箱）。UUID 只出现在命令和 `title` 里。
- fixture 是虚构数据，里面不能出现像 token 的字符串。

## 5. 新板块设计检查清单

设计稿和验收各过一遍。每条写「是 / 否 + 说明」。

**定位**

1. 写出这个板块回答的一个问题，以及它属于哪些数据层（1.1）。
2. 说明为什么是顶栏板块、子视图，还是只从地图进入（1.2）。
3. 选定健康条形态：共享筛选条、固定范围说明、不显示（1.3）。
4. 列出进出跳转，包括地图工具行入口和 `toolSignal` 文案（1.5）。

**版式**

5. 写明沿用第 2 节哪个版式；和范本不同的地方逐条写理由。
6. 写明一行是什么对象。一个对象只占一行；分组导致重复时，组标题要说明。
7. 顶部卡片是不是行的分区、能不能点、点了写哪个参数，和下拉是否是同一个参数。
8. 视图、分组、筛选都进 hash；离开板块时清掉；搜索匹配哪些字段、placeholder 写什么（1.4）。
9. 待处理栏：桶在后端算，顺序固定，一个对象一个桶，空桶写「无」，没接上写「—」加原因；角标怎么算、什么颜色。
10. 详情只有一种抽屉；节的顺序是「是什么 → 什么状态 → 证据 → 怎么处理」；`Esc` 关闭后焦点回到原来的行。

**组件和视觉**

11. 列出复用的组件（第 3 节类名）。每个新增 CSS 类都写明现有的为什么不够。
12. 每个符号都沿用现有含义，不给已有符号新含义（例如 `○`）；页面内有图例，列头 `title` 写证据来源。
13. 只用第 4 节的变量、状态色、字号和间距，不写十六进制颜色。
14. 每个计数和格子都能区分有、确定没有、没采集、读取失败（4.5）。
15. 只读提示、命令格式、`--workspace-id`、密钥写法符合 4.6 和 4.7。

**验收材料**

16. mock 场景至少覆盖：正常、全空、部分失败（保留旧数据）、接口缺失。场景写进 `fixtures/scenarios.json`。
17. 和 Skills 对照截图：同一窗口宽度（1440px 和 1100px），左边 `?mock=1#/skills`，右边新板块的正常场景，分别对照顶部卡片、工具栏、图例、表格密度、待处理栏、抽屉。差异要么改掉，要么写进第 5 条的理由。
18. 深色模式和 390px 宽各截一张。
19. 键盘走一遍：数字键进入、`/` 搜索、`Tab` 到行、`Enter` 打开抽屉、`Esc` 关闭并回到原行。

## 6. 现有不一致（清理待办，本单不修）

编号供后续清理单引用。「推断」表示只看代码得出，没有在浏览器里复现。

### 6.1 v1 MCP 页（`web/mcp.js`）和 Skills 范本的差异

**ACM-378 / ACM-403 已解决**：MCP 页按 `mcp-redesign.md` 重写为一张分组表 + Skills 结构的来源卡、图例、待处理栏和单抽屉，下面 M1–M13 全部关闭，本文只留作历史记录。

- **M1** 顶部是指标卡 `.mcp-card`（库条目、已分配、无人使用、有自有配置、会被忽略、同名覆盖），不是行的分区，其中库条目、已分配、有自有配置这 3 张禁用不可点。Skills 的来源卡全部可点。
- **M2** 每个 workspace 一块 `.mcp-ws`，各自一张表、各自一个限高滚动区。Skills 是一张表加分组行。
- **M3** 列是动态的库条目（每个 server 一列，超过 8 列整块退回清单视图）。Skills 的列是固定的工具。
- **M4** 符号另起一套：`● ○ · ⊘ ◐ ! ?`。`○` 在 Skills 表示「经共享目录可见」，在 MCP 表示「已停用」；`●` 在 MCP 是绿色，在 Skills 是正文色。页面上没有图例，含义只在 `title` 和 `aria-label` 里。
- **M5** 三种抽屉（agent、server、agent × server）。Skills 一种对象一种抽屉。
- **M6** 有「矩阵 | 清单」视图切换，清单视图又是另一套列。Skills 只切分组方式。
- **M7** 工具栏有 workspace 下拉，和顶栏筛选条写同一个 `ws`，同一参数两个控件。
- **M8** 只读提示放在页面顶部；Skills 放在抽屉的命令区。
- **M9** 待处理栏的桶标题做成筛选按钮，条目打开三种不同的抽屉。没有渲染 `.skills-compact`，而它复用的 `.skill-bucket` 样式在小于 1200px 时会隐藏标题和内容，所以窄屏下这一栏只剩一个空框（推断）。
- **M10** 桶计数规则不同：MCP 一条记录可以同时进多个桶（`mcp-design.md`），Skills 一个对象只进一个桶。
- **M11** 读取失败提示借用 `.decide-note`；全空状态把命令和 runtime 支持表放进 `.empty.big`（居中灰字的占位样式），Skills 用 `.atlas-guide`。
- **M12** 行本身不能点，只有 agent 名按钮和格子能点；Skills 整行可点。
- **M13** 角标用默认黄色，Skills 角标是红色（见 G6）。

### 6.2 跨页面

- **G1** 表格有六套样式：`.matrix`、`.skills-table`、`.decide-table`、`.layer-table`、`.plan-table`、`.mcp-support`。表头底色（`--panel` 或 `--panel-2`）、单元格内边距（5/10、6/8、7/10、8/10）、分组行底色（`--panel-2` 或 `--bg`）各不相同。
- **G2** 摘要卡有七套：`.provider`、`.source-card`、`.decide-stat`、`.mcp-card`、`.bot-stat`、`.foot-bucket`、`.plan-counts button`。选中态有三种写法（边框加内阴影、边框加 `--info-bg`、只换边框）。
- **G3** 下拉筛选有两个类：`.field`（Issues、待你决定）和 `.filter`（Skills、MCP）。
- **G4** 提示条有四种做法：`.banner`、`.skills-anomaly`（黄字）、`.decide-note`（正文色）、`.usage-gap`。
- **G5** 「显示其余 N 条」：Issues 能收起、每栏 30 条，Skills 展开后不能收起、每组 8 条。
- **G6** 顶栏角标颜色：待你决定和 MCP 黄色，Skills 红色（`.tab-badge.skills`）。
- **G7** 直接调用剪贴板的有 3 处：`skills.js` 的 `copyCommand`（没有回退）、`app.js` 的 `copyText`（有 textarea 回退）、`view-atlas.js` 的 `drawerCommands`（内联）。另有 3 个包装，都调用 `copyText`：`agents-view.js` 的 `copyButton`、`plan.js` 的 `copyButton`，以及 `app.js` 里 `mcpCtx()` 的 `copyCommand`（MCP 用它）。失败文案有「复制失败」和「请手抄」两种。
- **G8** 命令格式：Skills 的 workspace 命令和 MCP 命令带 `--workspace-id <uuid>`；Agents 抽屉的 `agent update`、`agent skills add/set` 不带；改层命令用 identifier，也不带 workspace。
- **G9** 只读提示有五种说法：Skills「页面只读，不代为执行。复制命令后在自己的终端运行。」和「页面只读，不替代命令行。」，Agents「页面只读。复制后在自己的终端执行。」，issue 抽屉「页面只读。复制命令改属性，下一轮采集后生效。」，MCP 顶部的 `READONLY_LINE`。
- **G10** 旧数据的不透明度有三个值：`.is-stale` 0.62、`.mcp-ws.is-stale` 0.65、`.plan-expired` / `.is-dim` 0.55。
- **G11** `frontend-design.md` 写「未复核且没有别的问题的行降低不透明度」，但 `styles.css` 里 `.skill-row.quiet { opacity: 1; }` 把它取消了（`e8582b7` 引入）。文档和代码不一致，见第 7 节。
- **G12** CSS 重复定义：`.tag.bad`（237、473 行）、`.tag.info`（239、809 行）、`.table-wrap`（345 行 `overflow-x`、758 行 `overflow`）、`.card`（282、723 行）。
- **G13** 页面标题：地图、层视图、待你决定、用量有 `h2` 页头；Issues、Agents、Skills、MCP 没有。
- **G14** 写死的颜色：`.usage-col` 顶边 `#6b7280`，角标文字 `#fff`、`#1c1408`，层颜色写在 `layers.js`。
- **G15** `section()`、`line()` 在 `skills.js` 和 `mcp.js` 各有一份；`lineDiff` 在 `agents-view.js` 和 `skill-viewer.js` 各有一份，diff 的 DOM 结构也不同。
- **G16** 通用组件带页面前缀：`skills-layout`、`skills-queue`、`skill-bucket`、`queue-item` 已经被 MCP 复用；MCP 的旧数据提示用的是 `decide-note`。
- **G17** `filter-bar.js` 顶部注释写「Issues and Agents use route.ws」，实际 MCP 也用。
- **G18** 跨板块跳转缺口（1.5）：Agents、Skills、MCP 互相提到对方的对象，但不能跳转。

## 7. 已决定（用户 2026-10-24 确认）

1. **未复核的行淡化**（G11）：接受 `frontend-design.md` 规则。未复核且没有其他问题的行降低不透明度；缺失、失败、漂移、冲突行不淡化。修复文档与 `.skill-row.quiet` 的冲突。
2. **顶栏角标颜色**（G6、M13）：红色表示坏，黄色表示等用户处理，灰色 `?` 表示未知或未采集，确定为 0 时隐藏。因此 Skills 待处理角标改黄色；损坏桶仍用红色。
3. **只读提示和复制失败文案**（G7、G9）：统一为「页面只读，不代为执行。复制命令后在自己的终端运行。」和「复制失败，请手抄」。写入功能上线后改为明确动作状态文案。
4. **跨板块跳转**（G18）：补 Agents ↔ Skills ↔ MCP，以及运行状态 ↔ 配置台跳转。使用稳定内部 row ID 定位、滚动并打开抽屉；抽屉不进 URL；没有目标证据时显示降级文案。
5. **通用组件改名**（G16）：单独开清理单处理，不阻塞 MCP v2 或首个写入热点。先用测试保护行为，再改为资源无关名称。

上述决定替代本节原「待用户决定」状态；执行仍须通过对应 issue 审定。
