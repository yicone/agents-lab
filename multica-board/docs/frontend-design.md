# Multica Board 前端设计（第一阶段）

目标：一个本地页面回答两个问题。

1. 哪些 issue 现在需要我看？（待验收、受阻、运行失败、停滞）
2. 每个 agent 跑在哪个 runtime / 模型上，哪里在出错？

## 技术取舍

- 无构建、无依赖：`index.html` + `styles.css` + 原生 ES module。不引入框架、CDN、字体、图标库。
- 所有数据渲染走 `textContent` / `createElement`，不对接口数据用 `innerHTML`（issue 标题可能含任意文本）。
- 不在浏览器持久化业务数据。`localStorage` 只存 UI 偏好（停滞阈值、分组方式）。筛选状态放在 URL hash，便于收藏。
- 外链统一 `target="_blank" rel="noopener noreferrer"`。

## 页面结构

```
┌ Multica Board ── 快照 42 秒前 ── [搜索 /] ───────────────────────────────┐
│ ● ACM Lab  ● 萌芽辅导  ▲ Elder Oasis(超时, 18 分钟前)  ○ 英语学习(未加载) …   │  ← workspace 健康条，点击=只看该 workspace
├ [Issues 关注 1]  [Agent 矩阵 2] ──────────────────────────────────────────┤
│ 待验收 6 │ 受阻 2 │ 运行失败 3 │ 停滞 4 (阈值 3 天 ▾) │ 运行中 5            │  ← 计数即筛选
├─────────────┬─────────────┬─────────────┬──────────────────────────────┤
│ 运行失败     │ 受阻         │ 待验收       │ 停滞                           │
│ ┌─────────┐ │ ┌─────────┐ │             │                                │
│ │ACM·ACM-162│ │             │             │                                │
│ │看板后端…  │ │             │             │                                │
│ │Builder·2h │ │             │             │                                │
│ │✗ daemon…  │ │             │             │                                │
│ └─────────┘ │             │             │                                │
└─────────────┴─────────────┴─────────────┴──────────────────────────────┘
```

### Issues 关注

- 四栏：运行失败 → 受阻 → 待验收 → 停滞。每个 issue 只进一栏，按此优先级取第一个命中的栏，其余状态作为卡片上的小标签显示（例如“待验收”栏里的卡片带“曾失败·已恢复”）。
  - 运行失败：`latest_run.status == failed`。
  - 受阻：`status == blocked`。
  - 待验收：`status == in_review`。
  - 停滞：`status` 为 `todo`/`in_progress`，无运行中/排队 run，`last_activity_at` 早于阈值（默认 3 天，可选 1/3/7/14）。
- 卡片：workspace 色标 + 名称、identifier（等宽）、标题（两行截断）、负责人、最后活动相对时间、最近 run 状态与 `error_kind`。整张卡片是链接，打开 Multica 原页面。
- 每栏内按最后活动时间倒序；运行失败栏按失败时间倒序。
- 采集失败的 workspace：其卡片降低不透明度并带“旧数据”标签；从未成功的 workspace 不显示卡片，健康条上显示“未加载”。

### Agent 矩阵

- 顶部 provider 汇总条：每个 provider 一块，显示 agent 数、跨几个 workspace、在线 runtime 数、24h 失败数（其中 quota/rate_limit 几次）。点击即按 provider 筛选。这是第二阶段“批量切 runtime”的决策入口。
- 表格，分组切换：按 workspace（默认）/ 按 provider。列：

  | Agent | 状态 | Runtime | 模型 | 推理 | Tier | 24h | 最近失败 |
  |---|---|---|---|---|---|---|---|

  - 状态：`working` 显示运行/排队数。
  - Runtime：名称 + 在线圆点；未绑定、离线分别标注。
  - 模型/推理/Tier 为 `null` 时显示灰色“默认”。
  - 24h：`✓5 ✗1`。
  - 最近失败：相对时间 + `error_kind` + issue identifier。
- 筛选：只看有失败、只看离线 runtime、显示已归档（默认关）。

## 交互细节

- 快捷键：`/` 聚焦搜索，`1` / `2` 切换视图，`Esc` 清空搜索。
- 搜索匹配 identifier、标题、负责人、agent 名、模型。
- 轮询 `/api/meta` 每 15 秒；`generated_at` 变化才重新拉列表。快照年龄超过 3 个采集周期时顶栏变黄。
- 接口失败：顶栏显示红色“后端不可达”，保留上一次已渲染的数据，不清空页面。
- 深浅色跟随系统。

## 开发

```sh
python3 -m http.server --bind 127.0.0.1 8765 -d multica-board/web
# 打开 http://127.0.0.1:8765/?mock=1
```

`?mock=1` 读取 `web/fixtures/*.json`，并以 `meta.generated_at` 作为“现在”，相对时间保持稳定。fixtures 是虚构数据，不含真实 issue 内容。
