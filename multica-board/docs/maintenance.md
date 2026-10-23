# Agent Atlas 维护规范

适用范围为本仓库 `multica-board/`。改动前先读根目录 `AGENTS.md` 及其会话启动规则，再读本页、`../README.md`、本次涉及的契约与实现；当前 issue、批准范围和已有评审结论共同约束交付。不要把旧文档或本地实现当作已验收、已合并或已部署的证据。

## 目录与契约

以下路径相对于 `multica-board/`：

| 路径 | 内容与改动前必读项 |
| --- | --- |
| `backend/` | Python 标准库采集、SQLite 缓存、只读 HTTP；实现位于 `multica_board/`，测试位于 `tests/` |
| `web/` | 无构建、无依赖的原生 ES modules、CSS、静态页面；`fixtures/` 为虚构数据；`*.test.js` 为 Node 测试 |
| `docs/api-contract.md` | 只读 API、字段、错误与安全边界；标题仍为 v0.6，但正文已含 ACM-370 v0.9，不可只看标题判断版本 |
| `docs/frontend-design.md` | 信息架构、路由、筛选、状态与交互；历史版本引用须与当前实现对照 |
| `docs/mcp-design.md` | MCP 来源、分配、生效证据、脱敏和板块设计；runtime 支持表是有版本的源码证据，不是永久保证 |
| `docs/runtime-plan-contract.md` | G1 计划器 schema、只读计划 GET、权限证据与文件/hash 边界；当前标题明确为 v1.2 草案，不得据此开启执行 |
| `launchd/` | 当前仅有 `one.yic.multica-usage-collector.plist` 模板；未经批准不得安装或加载 |
| `scripts/` | 当前为 `collect-owner-agents.py` 用户手动只读补采脚本；README 明确仅用户普通终端执行，agent 不运行 |

本页以 `537e5fec` 的 main 为核对基线。契约变更先说明影响、协调后端与前端，再同步契约、样例和测试；不得静默扩大既有权限或把草案能力变成可执行操作。

## 页面与板块

新增或改动页面先对照 `docs/design-system.md`（ACM-377，**以合并版本为准**）。本基线该文件尚未合入，ACM-377 状态为 in_review；不可把分支文档称为生效规范。实现前重新核实 main，若仍未合并，明确记录依赖和适用版本。

沿用 Skills 页布局与交互模式：先通读 `web/skills.js`、`web/styles.css`、`backend/multica_board/skills_view.py`、`skill_upgrade.py` 及相关测试，再确定来源卡片、分组一览、工具列状态、cloud 区块、待处理栏、单一详情抽屉、可复制命令的复用方式。新板块走「设计 → 实现 → 截图对比验收」，提交与 Skills 同尺度的桌面和窄屏对照，核实筛选、路由回退、抽屉、空状态、错误状态与只读提示。MCP 现有实现与 Skills 的差异不能作为另立视觉规范的依据。

接口内容使用 `textContent` / `createElement` 渲染，不插入未经处理的 HTML；保持无外部 CDN、字体或业务数据持久化的既有边界。

## 数据与安全红线

- 只读采集，不新增写 Multica 的 HTTP 路径；HTTP 仅 GET/HEAD，CLI 保持精确只读白名单，不开放任意命令或参数透传。
- 不显示、记录或提交任何 token、密钥、环境变量值、headers 值或原始敏感配置；MCP 只保留允许的名称、transport、设置标志和计数。脱敏在缓存与返回之前完成，错误输出同样脱敏。
- 不借用宿主凭证，不改 agent、runtime、skills、daemon、桥接或全局配置来绕过任务权限。保持 CLI 隔离 cwd、任务凭证剥离、workspace 身份核验、私有文件权限和失败可见性；失败不得伪装为空清单。
- `backend/multica_board/server.py` 的 `_guard()` 不得放宽：Host 精确允许列表，Origin 按现有 netloc 校验，跨站 API/子资源拒绝，不增加 CORS。ACM-375 已合并的例外只允许非 API 的 GET/HEAD 顶层导航（navigate、document，缺 Dest 默认 document）；不扩大例外。README 和 api-contract 的概括仍写跨站一律拒绝，核对时以实现和 `tests/test_board.py` 回归为证，不自行改旧契约。
- 只绑定回环地址；`localhost` 不等于允许的 `127.0.0.1` Host。不打包整个 `~/.multica-board`，不生成或复制历史 `board.pid`、`serve.log`。

## 检查与测试

从仓库根目录分别运行现有全量测试，无 npm install 或构建步骤：

```sh
cd multica-board/backend
python3 -m unittest discover -s tests -v
```

```sh
cd multica-board/web
node --test
```

改动必须全绿；根目录运行 `git diff --check`。说明实际命令、结果和未验证部分。Web 行为变动还需实际走受影响操作及截图对照；可用 README 的回环 mock 服务与 `?mock=1`，MCP 另有 `mcp`、`mcp-empty`、`mcp-partial`、`mcp-absent` 场景。mock 和单元测试不能替代真实数据或上线验收。仅文档变动核对路径、命令、引用和 Git 范围，不冒称完成浏览器验收。

## 交付与部署职责

1. 在自己的任务分支完成小范围改动与本地 commit；交付评论列出分支、commit、文件、证据、测试与未决项。
2. 副调度安排非作者验收；作者不能自行验收本人交付，也不自行派评审。READY 后由调度发起用户编号批准。
3. 作者仅在用户明确批准相应动作后推送自己的分支、开 PR；不得推送 main。合并和部署由运维部署执行，作者不承担这两个动作。
4. 运维只从已合并的 `main` 部署，核实实际部署 SHA、检查与回滚安排。PR、READY、本地 commit 均不等于上线。
5. 每个合入 main 的用户可感知改动在 `CHANGELOG.md` 的 `Unreleased` 加一条，附已合并 PR、提交和 ACM 单号；纯内部改动不强凑条目。部署时将本次实际部署覆盖的条目移至当天日期节，附部署 SHA/记录；若全部覆盖，可把 Unreleased 改为当天日期并重建空 Unreleased。未部署条目留在 Unreleased，不因日期到来自动标上线；历史合并日期节保留其「部署待补证」性质，证据不足直说。

## launchd 与运维核对

Board 的服务名是 `one.yic.multica-board`（服务名依据 README；无 PID 文件的生命周期另由 `backend/tests/test_pid_lifecycle.py` 覆盖）；它不是仓库 `launchd/` 中的 usage collector。Board 默认开发端口 8787，本机现用服务端口在 README 中为 8765；不要因此修改服务。只读检查方式：

```sh
launchctl print gui/$(id -u)/one.yic.multica-board
lsof -nP -iTCP:8765 -sTCP:LISTEN
ps -p <已核对的PID> -o pid=,ppid=,args=
```

先比对 launchd 的 running/pid、监听 PID 和命令。日志按 README 为 `~/Library/Logs/multica-board.log`，历史 PID 文件不是权威。不得按进程名终止 multica；任何启停、安装、清理或部署均需独立授权并交运维执行。本页核对的是仓库证据，未以这些命令验证当前主机服务状态。
