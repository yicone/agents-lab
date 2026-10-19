# Multica Board 前端设计（第一阶段）

页面显示名是 **Agent Atlas**（`<title>`、顶栏、favicon 的 `alt` / `<title>`）。目录和包名仍是 `multica-board`，改名收尾不在这一版。

目标：一个本地页面回答两个问题。

1. 哪些 issue 现在需要我看？（待验收、受阻、运行失败、停滞）
2. 每个 agent 跑在哪个 runtime / 模型上，哪里在出错？

接口见 `api-contract.md`（v0.1）。

## 技术取舍

- 无构建、无依赖：`index.html` + `styles.css` + 原生 ES module。不引入框架、CDN、字体、图标库。
- 所有数据渲染走 `textContent` / `createElement`，不对接口数据用 `innerHTML`（issue 标题可能含任意文本）。
- 不在浏览器持久化业务数据。`localStorage` 只存停滞阈值。筛选状态放在 URL hash，便于收藏。
- 外链统一 `target="_blank" rel="noopener noreferrer"`，页面 `referrer=no-referrer`。

## 页面结构

```
┌ Agent Atlas  [地图 1] [待你决定 2] [Issues 3] [Agents 4] [Skills 5]  ● 快照     [搜索 /] ┐
│ ┌ 采集失败：鉴权 ──────────────────────────────────────────────────────┐ │  ← 仅在全局采集失败时出现
│ │ 401 unauthorized …                                                   │ │
│ │ CLI 认证失败。在你自己的终端确认 multica 已登录 …                     │ │
│ │ 最近尝试 刚刚 · 最近成功 40 分钟前 · 页面显示的是 40 分钟前的旧数据    │ │
│ └──────────────────────────────────────────────────────────────────────┘ │
│ ● ACM Lab  ● 萌芽辅导  ● Elder Oasis 超时·18 分钟前  ○ 英语学习 未加载 …     │  ← workspace 健康条，点击=只看该 workspace
├ 停滞阈值 [3 天▾]   其他打开 issue 7 条 ──────────────────────────────────────┤
│ 运行失败 4    │ 受阻 2        │ 待验收 6      │ 停滞 3                        │
│ ┌──────────┐ │               │               │                    ┌───────┐ │
│ │ACM ACM-162│ │               │               │                    │运行历史│ │  ← 抽屉
│ │看板后端…   │ │               │               │                    │ 失败 … │ │
│ │项目 · ↑父  │ │               │               │                    │ 完成 … │ │
│ │✗ 进程失败… │ │               │               │                    └───────┘ │
│ │Builder 运行记录  3h│        │               │                              │
│ └──────────┘ │               │               │                              │
└──────────────┴───────────────┴───────────────┴──────────────────────────────┘
```

### 顶栏状态

顶栏的状态标记只显示一种状态，按以下顺序取第一个命中的：

| 条件 | 颜色 | 文案 |
|---|---|---|
| 前端请求 `/api/*` 失败 | 红 | 后端不可达（有旧数据时附“显示 X 前的数据”） |
| `meta.collector.error` 非空 | 红 | 采集失败 · 数据停在 X 前 / 尚无数据 |
| `generated_at == null` | 灰 | 尚未采集（`collect_interval_s > 0` 时附“首轮进行中”） |
| 所有 workspace 都是 `error` / `never`（至少一个 `error`） | 红 | 全部 workspace 采集失败 |
| `collect_interval_s == 0` | 灰 | 只读缓存 · 快照 X 前（不判断过期） |
| 快照年龄超过 3 个采集周期 | 黄 | 快照 X 前（过期） |
| 其它 | 绿 | 快照 X 前 |

全局采集失败、或全部 workspace 失败时，顶栏下出现红色提示条，内容依次为：原因类别、脱敏后的错误原文、按 `error_kind` 给出的处理建议、时间信息。单个 workspace 的失败只显示在它的健康条标签上，悬停可看到错误原文。

处理建议：

- `auth`：检查 CLI 登录与 `--profile`。
- `process`：检查 CLI 是否已安装，或用 `--multica-bin` 指定路径。
- `network` / `timeout`：检查网络、调大 `--timeout`。
- `rate_limit` / `quota`：等待下一轮自动重试。
- 其它：查看后端终端输出（加 `-v`）。

没有任何数据时，主区域显示占位说明而不是空看板：

- 采集从未成功过：提示原因见上方提示条。
- `--refresh 0` 且缓存为空：提示先运行 `collect`。
- 首轮采集进行中：提示完成后会自动显示。

### Issues 关注

- 四栏：运行失败 → 受阻 → 待验收 → 停滞。每个 issue 只进一栏，按此优先级取第一个命中的栏，其余状态作为卡片上的小标签显示（例如“待验收”栏里的卡片带“曾失败·已恢复”）。
  - 运行失败：`latest_run.status == failed`。
  - 受阻：`status == blocked`。
  - 待验收：`status == in_review`。
  - 停滞：`status` 为 `todo` / `in_progress`，没有运行中或排队中的 run，且 `last_activity_at` 早于阈值（默认 3 天，可选 1/3/7/14）。
- 卡片内容：
  - workspace 色标、identifier（等宽）、状态。
  - 标题（两行截断），点击在 Multica 打开原 issue；`url` 为 `null` 时不带链接。
  - 项目名和父 issue（`↑ ACM-161`），没有则不显示。
  - 最近一次失败的 `error_kind` 与摘要。
  - 负责人（squad 没有名字时显示“Squad”）、“运行记录”按钮、最后活动的相对时间。
  - `latest_run == null` 时不显示按钮，改为“14 天无 run”，因为后端只缓存 14 天。
- 每栏内按最后活动时间倒序；运行失败栏按失败时间倒序；停滞栏按最久未活动在前。
- 每栏默认只显示前 30 张卡片，底部按钮“显示其余 N 条”展开、“收起到 30 条”收起，收起时滚回栏头。真实数据里“待验收”一栏有 120 多条，不分页会把整页拉得很长。
- 工具栏显示不在四栏里的打开 issue 数量，并按状态拆分（例如“其余 132 条打开 issue 不在关注范围（Todo 68 · Backlog 60 · 进行中 4）”）。已关闭的 issue 不计入。
- 采集失败或过期的 workspace：其卡片降低不透明度并带“旧数据”标签。从未成功的 workspace 不显示卡片，健康条上显示“未加载”。
- `--refresh 0` 时后端仍会按年龄把 workspace 标成 `stale`，但不采集时“过期”没有意义，所以前端此时把 `stale` 当作正常显示，不淡化卡片；顶栏已经显示“只读缓存 · 快照 X 前”。

### 运行历史抽屉

- 点击卡片上的“运行记录”，从右侧滑出抽屉，内容来自 `/api/issues/{ws}/{id}/runs`。
- 每行显示：状态、agent、结束（或开始）的相对时间、耗时，失败时附 `error_kind` 与摘要。
- 接口返回 404 或列表为空时显示“14 天内没有 run”。
- 抽屉不是模态的，看板仍可操作。`Esc` 或 × 关闭，关闭后焦点回到原按钮。

### Agent 矩阵

- 顶部 provider 汇总条：每个 provider 一块，显示 agent 数、跨几个 workspace、在线 runtime 数、24h 失败数，以及其中额度/限流的次数。点击即按 provider 筛选。这是第二阶段“批量切 runtime”的决策入口。
- 表格，分组切换：按 workspace（默认）/ 按 provider。列：

  | Agent | 状态 | Runtime | 模型 | 推理 | Tier | 24h | 最近失败 |
  |---|---|---|---|---|---|---|---|

  - 状态：`working` 时显示运行数和排队数。
  - Runtime：名称加在线圆点，未绑定、离线分别标注。
  - 模型、推理、Tier 为 `null` 时显示灰色“默认”。
  - 24h：`✓5 ✗1`。
  - 最近失败：`error_kind`、相对时间、issue identifier（不在缓存时不显示 identifier）。
- 筛选：只看有失败、只看离线或未绑定的 runtime、显示已归档（默认关）。

### 切换计划

Agents 页的子视图，不加第 6 个一级 Tab。顶栏仍高亮 Agents。入口是工具栏的「规划切换…」。

- `#/agents?plan=1&sel=`：勾选 agent。选择写在 URL 里，筛选变化不改 `sel`。底部条显示「其中 N 个不在当前筛选中」，N 为 0 时写「全部可见」。归档行的勾选框禁用。provider 块在这个模式下可以「全选本 provider」，只含当前可见、未归档的行。`Esc` 或再点入口会退出并清空选择。
- `#/agents/plan/new`：范围卡、目标 runtime，以及模型 / 推理 / Tier 的不改、清除为默认、设为。命令区只有复制。参数名还没定稿，页面用 fixture 拼一条预览命令，不发送、不运行。跨多个 workspace 时范围卡标黄。目标 runtime 不是 Codex 时，Tier 的「设为」禁用。
- `#/agents/plans`：计划列表。过期、hash 不符、无法解析都留在表里。目录为空时只显示占位，不画空表。顶栏状态换成「计划目录 · 最近一份 X 前」。
- `#/agents/plans/<id>`：吸顶范围卡、本机时钟倒计时、门槛条、四组行表。hash 不符或无法解析时不渲染行表。G2 三项显示未接入，并有一行「apply 关闭」。这一行不是按钮。四组计数之和等于总行数。`null` 和 `""` 都显示为「默认」，后面用小号等宽标记区分；同一列两种都有时，列头加黄点。
- 行详情是非模态右侧抽屉。`Esc` 关闭后焦点回到原行。批准区不渲染。
- 契约定稿前这些页面只读 `web/fixtures/plan-data.js`，不请求 `/api/plans`。没有批准、执行、回滚入口。

## 交互细节

- 快捷键按顶栏位置编号，不再按层号：`1` 地图、`2` 待你决定、`3` Issues、`4` Agents、`5` Skills。`[` / `]` 只在某一层的层视图里切到上一层 / 下一层（往 L5 / 往 L1，L1 与 L5 循环）。`/` 聚焦搜索，`Esc` 先关抽屉，再清空搜索。v1 的 `1` = Issues、`2` = Agents 已废弃。
- 搜索匹配 identifier、标题、负责人、agent 名、项目、父 issue、模型、runtime、workspace。
- 每 15 秒轮询一次 `/api/meta`，`generated_at` 变化时才重新拉列表；顶栏状态每 5 秒重算一次。
- 前端请求失败时保留上一次已渲染的数据，不清空页面。
- 不认识的 `error_kind` 按“未知错误”显示。
- 深浅色跟随系统。

## 开发

```sh
python3 -m http.server --bind 127.0.0.1 8765 -d multica-board/web
# 打开 http://127.0.0.1:8765/?mock=1
```

| 地址 | 场景 |
|---|---|
| `?mock=1` | 正常数据：5 个 workspace，其中一个超时、一个从未成功 |
| `?mock=cli-down` | 首次启动，CLI 找不到，没有任何数据 |
| `?mock=cli-stale` | 曾经成功过，现在 CLI 鉴权失败，显示 40 分钟前的旧数据 |
| `?mock=atlas` | 五层地图：自设、继承、跨层、治理线、未归层、不在地图。打开 `#/map` |
| `?mock=atlas-empty` | issue 上没有 `layer`。地图是引导，不是五层全 0 |
| `?mock=atlas-unfilled` | `layer` 在，但主层全空。同样是引导 |
| `?mock=atlas-quiet` | 已归层，待你决定为 0，角标隐藏 |
| `?mock=atlas-gap` | 和 atlas 同一批 issue，状态迁移 `reset`，等待时长带 `≥` |

真实地图读 `GET /api/layers`。`ready` 为 false（三个属性还没建）时显示引导，不画五层全 0。属性在、但已归层为 0（主层还没回填）时也是引导。已经回填的部分按接口计数，没回填的落在未归层。`?mock=` 没有这份响应，仍用 fixture 里的 `issue.layer` 在浏览器汇总。Issues 页在层数据就绪后增加「层」和「按主层 / 按涉及层」，卡片头部加五格条；非 ACM Lab 的卡片不画条，选了层之后这些卡片被滤掉。Agents 页内容不变。Skills 页读 `GET /api/skills`（见下方）。角标是待处理条数，基线债不计入，0 时隐藏。只看治理线时，未完成和待你决定用接口的 `governance_open` / `governance_decide`；接口没有单独的治理线总数，所以这一档的「总数」等于未完成。

旧地址用 `location.replace` 跳到新地址：`#/l5/attention` → `#/issues`，`#/l2/agents` → `#/agents`，`#/l3/skills` → `#/skills`，`#/lN` 与 `#/lN/issues` → `#/map/lN`（保留 `scope`），其他 `#/lN/…` → `#/map/lN`，`#/gov` → `#/map/all?gov=1`，空地址或不认识的地址 → `#/map`。`#/issues`、`#/agents`、`#/skills` 保持原样。刷新停在替换后的地址上，后退不会回到旧地址。

- mock 模式以 `meta.collector.last_attempt_at`（没有则用 `generated_at`）作为“现在”，所以相对时间保持稳定。
- 各场景覆盖哪些响应，记录在 `fixtures/scenarios.json`。
- `runs.json` 不对应任何接口，是 mock 模式下 `/runs` 的数据来源。
- fixtures 是虚构数据。主场景修改后运行 `python3 multica-board/web/fixtures/generate.py`。地图场景修改 `web/fixtures/atlas-data.js` 后运行 `node multica-board/web/fixtures/write-atlas.js`。

## 待你决定（ACM-191）

回答「现在有哪些事等我拍板」。范围只有 ACM Lab（`slug=acm-lab`，否则名称正好是 `ACM Lab`）里 `in_review` 和 `blocked` 的 issue，等待最久的在最前。其他 workspace 的同类 issue 只在页脚计数，并链到 Issues。

- 路由 `#/decide`，参数 `type=review|blocked`、`layer`、`match=primary|involved`、`gov=1`、`group=layer`。顶栏第 2 项是「待你决定」，黄色角标等于这一页未筛选的条数，0 时隐藏。层数据还没就绪时，层筛选保持灰色占位。
- 已等待取 `/api/changes?kind=status` 里进入当前状态的最近一次 `issue_status`。`kind=status` 只返回状态迁移，run 不再占用这 500 条分页。日志里没有这条迁移、或者这次拉取 `reset` / 失败 / 没有读完时，改用 `last_activity_at`（再退 `updated_at`、`created_at`），数字前加 `≥`。超过 7 天标黄，超过 14 天标红；刚好 7 天、刚好 14 天不算超过。状态迁移仍最多翻 16 页；读不完就整页退回，不用读到一半的迁移。
- 页头：合计、待验收、受阻、超过 7 天、最久等待。层分布、层筛选、按涉及层、只看治理线、按主层分组在层属性还没建时是灰色占位，不发请求。
- 点行打开现有运行记录抽屉，点标题在 Multica 打开。最近动静用 `latest_run` 的状态；没有 run 时写「最后活动」。评论类型不在现有接口里。
- mock：`?mock=decide` 正常队列，`?mock=decide-gap` 状态迁移被重置，`?mock=decide-down` 状态迁移 404。打开时加上 `#/decide`。

## Skills（ACM-182）

回答「这些 skill 现在装在哪、哪些要处理」。数据是这台机器的清单和 drift，不跟 workspace 健康条混在一起。页面只读，没有批准 / 拒绝。

- 路由 `#/skills`。参数：`source`、`status`、`tool`、`custom=1`、`debt=1`、`group=source|tool|flat`。搜索框匹配名字、路径、来源、版本和发现代码。
- 顶栏状态换成清单写入时间和 Git 短 SHA，加上漂移扫描年龄。下一行是数据源健康条：没接入或 SkilLock 回退用虚线灰点。
- 来源汇总可点。一览表默认按来源分组，组内有待处理的在前，未复核且没有别的问题的行降低不透明度。每组先显示 8 行。工具列：`●` 直接入口，`○` 经共享目录可见，`✕` 入口不在。
- 待处理栏和 `/api/skills/attention` 是同一份分桶。空桶写「无」。升级检查写「—」和原因。基线债在最底下，只计数，点一下在表里筛出这些行。复核阈值 30 / 90 / 180 存在 `localStorage` 键 `aa.skillReviewDays`，默认 90；真实接口会带上 `review_days`。
- 点行打开右侧详情。`Esc` 关闭后焦点回到原来的行。详情里给可复制的命令，不在页面里改 skill。
- 屏宽小于 1200px 时，待处理栏移到表格上方，收成一行桶计数。
- mock：默认 `fixtures/skills.json` 是设计稿里的分布（版本已变 3、未登记 1、基线债 76）。`?mock=skills-missing`、`skills-drift`、`skills-gate`、`skills-fallback`、`skills-invalid` 覆盖这一份。打开时加上 `#/skills`。生成：`node multica-board/web/fixtures/write-skills.js`。
- 切换计划用 fixture，不经过 `generate.py`。在任意 `?mock=` 下打开 `#/agents/plans`。单份计划：`hist`（16 行，阻断 13、跳过 3）、`mixed`、`expired`、`mismatch`、`broken`、`blanks`、`cross`、`drift`。空目录是 `#/agents/plans?fix=empty`。`hist` 的分布对齐验收场景，不是 10-17 的实时快照。
