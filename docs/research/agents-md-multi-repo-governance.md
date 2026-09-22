# 多仓库场景下的 AGENTS.md 治理

## 结论摘要

维护仓库数量增长后，真正的痛点通常不是“不会写 AGENTS.md”，而是以下四件事同时发生：

1. 每个仓库都开始复制一份越来越长的规则。
2. `AGENTS.md`、`CLAUDE.md`、Cursor Rules、Copilot instructions、技能文件之间出现重复和漂移。
3. 仓库结构、命令、依赖和发布流程变化后，agent-facing 文档仍然描述旧世界。
4. 团队把“知识、规则、工作流、当前状态、工具配置”全部塞进常驻上下文，导致 agent 既耗 token 又更容易误解。

截至 2026-09，比较稳妥的共同实践可以归纳为一句话：

> 把 `AGENTS.md` 当作短小、可验证的入口和路由表；把详细知识放在有明确 owner 的仓库文档中，把低频流程放进按需加载的 skill，把能机械判定的规则交给代码工具和 CI。

这不是“只用 AGENTS.md”或“只用 skills”。Vercel 的公开评测显示，在需要持续、被动提供的框架知识上，压缩后的 `AGENTS.md` 索引优于按需 skill；但 Vercel 自己的产品设计实践又把复杂的判断、引用资料、范例和评测放进 skill。更准确的分工是：

| 内容 | 默认载体 | 是否常驻 | 适合的粒度 |
|---|---|---:|---|
| 任务前必须知道的边界、入口、危险操作 | 根目录 `AGENTS.md` | 是 | 短、可执行 |
| 子项目/目录特有的规则 | 受控的嵌套 `AGENTS.md` 或 path-specific rules | 仅相关路径 | 局部 |
| 复杂、低频、多步骤流程 | `.agents/skills/<name>/SKILL.md` | 否 | 一个任务族 |
| 架构、领域知识、设计决策 | `docs/`、ADR、设计文档 | 按需读取 | 事实和理由 |
| 生成、格式、命名、静态安全约束 | formatter、linter、schema、CI | 机械执行 | 不要重复写给 agent |
| 当前 backlog、实验状态、临时决定 | issue、状态页、研究记录 | 不常驻 | 明确有效期 |

## 1. 研究范围和证据等级

本报告比较了：

- `AGENTS.md` 的开放约定及 Codex 的实现语义；
- Claude Code、Cursor、Gemini CLI、GitHub Copilot、Windsurf 的官方上下文机制；
- OpenAI、Vercel、Temporal、Kubernetes 等真实仓库的公开实践；
- 关于上下文文件、配置异味和 Agent Skills 的近期经验研究；
- 可用于多仓库治理的 lint、referential-integrity、skill validation 和 CI 工具。

证据优先级为：官方文档/源码 > 真实仓库和官方工程文章 > 学术预印本 > 社区工具和经验文章。学术结果仍需注意样本、模型和任务边界，不能直接当成所有 agent 的普遍定律。

## 2. 生态现状：格式正在趋同，加载语义没有完全趋同

### 2.1 `AGENTS.md` 是开放约定，不是强 schema

[AGENTS.md 官方站点](https://agents.md/) 将它描述为面向 coding agent 的 README：没有必需字段，使用普通 Markdown，常见内容包括项目结构、安装/构建/测试命令、代码规范和安全注意事项。该约定已经被多种 agent 使用，并且站点建议在大型 monorepo 中使用嵌套文件。

这带来两个好处：

- 一个简洁的仓库入口可以跨多个 agent 复用；
- 不需要为每个工具发明一套完全独立的项目规则。

但它也带来两个限制：

- 没有统一的字段、继承语法、优先级声明或验证协议；
- “支持 AGENTS.md”只说明文件可能被读取，不保证不同工具的路径发现、覆盖和导入语义一致。

因此不要把“文件名标准化”误认为“行为标准化”。多工具团队仍需要一个明确的 canonical source 和一组适配层。

### 2.2 主要 agent 的加载模型

| 工具 | 主要机制 | 对多仓库治理的含义 |
|---|---|---|
| Codex | 按项目根到当前工作目录收集 `AGENTS.md`，按层拼接；支持全局 instructions、`AGENTS.override.md` 和 fallback filename。实现源码还显示项目根由 marker 决定。 | 根规则与子目录规则可以分层，但要测试实际 cwd、嵌套 Git、submodule 和 worktree 边界。 |
| Claude Code | 原生读取 `CLAUDE.md`；可让 `CLAUDE.md` 使用 `@AGENTS.md`，或建立 symlink。祖先目录规则按顺序合并，子树中的规则在读取该子树时才加入；支持 import。 | 用 `AGENTS.md` 做跨工具源，`CLAUDE.md` 做薄适配器；不要维护两份正文。 |
| Cursor | 推荐 `.cursor/rules/*.mdc`，可用 glob、always/auto/manual 等激活方式，支持嵌套 rules；`AGENTS.md` 是简单替代方案。 | 需要路径匹配和激活模式时用 Cursor Rules；需要跨工具共享时保留短 `AGENTS.md`。 |
| Gemini CLI | 从全局、workspace/祖先目录和 JIT 子目录读取 `GEMINI.md`；支持 `@file.md` import，也可在配置中把 `AGENTS.md` 设为 context filename。 | 可配置为读取共享格式，但 import 和边界是 Gemini 语义，不应假设其它工具照做。 |
| GitHub Copilot | `.github/copilot-instructions.md` 做 repo-wide；`.github/instructions/**/*.instructions.md` 做路径规则；agent instructions 可使用最近的 `AGENTS.md`。技能用于 task-specific workflow。 | 把 Copilot 专属规则留在 `.github/`，把跨工具常驻规则放 `AGENTS.md`，把流程放 skill。 |
| Windsurf | global/workspace/system rules，以及任意目录的 `AGENTS.md`；根部常驻，子目录按目录自动作用；自动 memory 与团队共享规则是不同层。 | 要区分个人本地 memory、workspace rules、仓库共享规则，不能把自动 memory 当 canonical source。 |

主要官方依据： [Codex AGENTS.md implementation](https://github.com/openai/codex/blob/main/codex-rs/core/src/agents_md.rs)、[Claude Code memory](https://code.claude.com/docs/en/memory)、[Cursor Rules](https://docs.cursor.com/context/rules-for-ai)、[Gemini context files](https://geminicli.com/docs/cli/gemini-md/)、[Copilot customization](https://docs.github.com/en/copilot/concepts/agents/code-review)、[Windsurf memories and rules](https://docs.windsurf.com/windsurf/cascade/memories)。

### 2.3 设计上的共同风险

多个工具都把规则放入模型上下文，而且很多工具会自动合并文件。因此：

- “最近文件覆盖一切”不是所有工具的真实行为；有的工具是拼接而不是覆盖；
- 同一条命令在 `AGENTS.md` 和 `CLAUDE.md` 中写成不同版本时，agent 可能不会报冲突，而是随机选择一条；
- 根目录文件引用子目录文档时，裸路径并不总能触发 agent 读取，必须说明“何时读、为什么读、读完要做什么”；
- 目录中一个意外的 `.git`、submodule 或自定义 project-root marker，可能改变规则发现边界。

## 3. 真实项目中的优秀实践

### 3.1 OpenAI：从“大手册”转向“目录页”

OpenAI 的 [Harness engineering](https://openai.com/index/harness-engineering/) 明确记录了“一份很大的 `AGENTS.md`”失败的原因：它挤占上下文、让所有内容都显得同等重要、快速腐烂且难以机械验证。后来采用的模型是：短的 `AGENTS.md` 作为 table of contents，结构化的 `docs/` 作为 system of record。

这个案例对多仓库最有价值的不是“100 行”这个数字，而是职责分离：

- `AGENTS.md` 只回答“从哪里开始、什么不能错、到哪里找详情”；
- 事实、架构、执行计划和设计决策分别有自己的目录和 owner；
- lint、结构测试和边界约束负责强制不变量，而不是让自然语言承担全部约束。

### 3.2 Vercel/Next.js：常驻索引胜过依赖 agent 自己决定是否加载

Vercel 对 Next.js 16 API 做了公开评测：[8KB 压缩文档索引放入 `AGENTS.md` 达到 100% pass rate；默认 skill 为 53%，加显式触发指令后为 79%](https://vercel.com/blog/agents-md-outperforms-skills-in-our-agent-evals)。实验中 skill 在 56% 的 case 没有被调用。Vercel 的解释是：常驻索引没有“是否要加载”的决策点，也没有“先探索还是先加载 skill”的排序问题。

这不是“skill 无用”的证明，而是一个分工信号：

- 对每次相关任务都需要的版本匹配知识，提供短索引、明确路径和 retrieval-first 指示；
- 不要把完整文档塞进常驻文件，索引指向可按需读取的版本化文档；
- skill 的触发条件必须写在 agent 已经会看到的入口中，并且要单独评测“是否触发”和“触发后是否遵守”。

Vercel 的 [product-design 实践](https://vercel.com/blog/teaching-agents-product-design-at-vercel)进一步给出了成熟的 skill 结构：仓库根 `AGENTS.md` 负责触发，skill 内的 `AGENTS.md` 负责加载顺序、校验和治理，`SKILL.md` 负责运行流程，`references/` 保存判断依据，`exemplars/` 保存正反例，evals 检查行为。它还强调把 routing、rules、evidence 分开，并给规则稳定 ID、来源、例外和 coverage gaps。

### 3.3 Temporal：短的根文件承载高频操作

[Temporal Java SDK 的 `AGENTS.md`](https://github.com/temporalio/sdk-java/blob/main/AGENTS.md) 只有约 59 行，包含模块布局、Java 版本、格式化、测试、构建、PR 信息和 review checklist。它没有试图复制完整的贡献文档，而是把关键入口和命令集中起来。

[Temporal 主仓库的 `AGENTS.md`](https://github.com/temporalio/temporal/blob/main/AGENTS.md) 则把根级行为准则、项目结构和常用 make targets 放在一个仍可扫描的文件里。它把“先读周边代码和测试”“不要假设依赖存在”等难以由 formatter/linter 保证的行为写出来。

可复用的模式是：

- 命令要指向仓库真正使用的统一入口，如 `make` 或 wrapper script；
- 只写 agent 不容易自行推断的事实；
- 高风险或跨服务约束要明确，但不要把每个边角案例都升级成根规则。

### 3.4 Kubernetes agent-sandbox：根规则、skills 和 human source of truth 分开

[Kubernetes agent-sandbox 的 `AGENTS.md`](https://github.com/kubernetes-sigs/agent-sandbox/blob/main/AGENTS.md) 明确告诉 agent：人类贡献者还要阅读 `CONTRIBUTING.md`、开发和测试文档，这些才是 source of truth；根文件负责项目摘要、目录 ownership、`.agents/skills/` 入口、标准 make targets 和生成文件边界。

这是多仓库治理很值得采用的写法：

- agent-facing 文件不取代人类文档；
- 生成文件、可编辑文件、owner 目录直接标出来；
- skill 目录和根入口互相链接，但不把所有 skill 正文复制到根文件。

### 3.5 Next.js 的 skill authoring guide：入口、触发和详情分层

[Next.js 仓库的 skills README](https://github.com/vercel/next.js/blob/canary/.agents/skills/README.md) 给出了非常清楚的判断标准：

- `AGENTS.md` 放每次都要知道的一行式 guardrail；
- skill 放复杂模板、多步骤流程、诊断过程和验证步骤；
- skill 不能只是无动作的知识堆，要有 “Use this skill when…”，步骤、示例和验证命令；
- 复杂 skill 用一个短的 `SKILL.md` 加 `workflow.md`、`references/` 或 examples；
- description 是自动触发的关键字段，要包含任务场景、文件名和概念关键词。

## 4. 研究证据：为什么“越多上下文越好”是错的

### 4.1 正向证据：可以减少运行成本

[On the Impact of AGENTS.md Files on the Efficiency of AI Coding Agents](https://arxiv.org/abs/2601.20404) 在 10 个仓库、124 个 PR 上比较有无根 `AGENTS.md` 的 Codex 执行，报告中位运行时间下降约 28.64%，中位输出 token 下降约 16.58%。这是一个小样本、单 agent 研究，不能外推到所有模型，但支持这样一个弱结论：准确的仓库入口可能减少盲目探索。

### 4.2 反向证据：冗余和不必要要求会降低成功率

[Evaluating AGENTS.md](https://arxiv.org/abs/2602.11988) 用 CTXbench 和 SWE-bench 评估上下文文件，报告总体上上下文文件没有稳定提升任务成功率，并使推理成本增加超过 20%；开发者编写的文件平均优于自动生成文件约 7%，但作者最终建议只加入 README 中不存在、且 agent 确实需要的最小要求。

这与 OpenAI/Vercel 的工程实践并不矛盾：他们使用的是经过维护、结构化、能指向真实资料的上下文；研究警告的是自动生成的冗余和额外规定，不是所有仓库文档都无效。

### 4.3 配置异味已经可以被系统分类

[Configuration Smells in AGENTS.md Files](https://arxiv.org/html/2606.15828) 分析 100 个流行开源项目，发现 91 个至少有一种异味。六类异味是：

| 异味 | 典型表现 | 处理方式 |
|---|---|---|
| Context Bloat | 根文件过长、塞入大量低频细节 | 拆到 docs/skills；根文件只保留入口和高风险规则 |
| Skill Leakage | 只对少数任务有用的流程写进常驻文件 | 移到按需 skill，并在根文件写清触发条件 |
| Lint Leakage | 重复 formatter、lint、schema 已能检查的规则 | 让工具强制，AGENTS 只写如何运行和为什么重要 |
| Blind References | 只写一个路径/链接，没有说明作用 | 写“何时读、解决什么问题、读后要验证什么” |
| Init Fossilization | `/init` 生成后长期不复核 | 把文档变更纳入 PR 和定期漂移检查 |
| Conflicting Instructions | 文件内部或跨工具文件相互矛盾 | canonical source、薄适配器、冲突 lint、明确 owner |

该研究的检测结果中，Lint Leakage 62%、Context Bloat 42%、Skill Leakage 35%；但 LLM 检测冲突的 precision 只有 57%，所以自动工具应优先报候选和上下文，不应把语义判定全部自动化。

### 4.4 Agent Skills 也不是自动有效

[SWE-Skills-Bench](https://arxiv.org/abs/2603.15401) 在 49 个公开 SWE skill、约 565 个任务上做 paired evaluation，报告 39/49 个 skill 没有 pass-rate 提升，平均提升只有 +1.2%；少数高度专门化 skill 有明显收益，也有版本不匹配的 skill 使结果下降。

因此 skill 的最低治理要求应是：

- 触发条件可测试；
- 目标项目/版本边界写清楚；
- 有 before/after 或固定任务回归；
- 能证明它比没有 skill 更好，而不是仅仅“内容更长”。

## 5. 反模式清单

### 5.1 一份覆盖所有仓库的超级 AGENTS.md

问题：把个人偏好、公司规范、某个仓库的发布步骤、另一个仓库的数据库禁令放到同一份全局文件。结果是跨项目污染、冲突和上下文浪费。

建议：全局层只放真正跨项目的安全和交互偏好；仓库事实放仓库；低频流程放 skill；发布和生产操作放经过权限控制的工具或 workflow。

### 5.2 复制多份 `AGENTS.md` / `CLAUDE.md` / `copilot-instructions.md`

问题：最初只是为了兼容工具，最后变成四个独立真源。

建议：采用以下优先顺序：

1. 一份仓库 canonical `AGENTS.md`；
2. `CLAUDE.md` 用 `@AGENTS.md` 或 symlink；
3. Copilot 文件只放 Copilot 特有内容；
4. Cursor/Gemini/Windsurf 文件只放不能用共享格式表达的适配内容；
5. CI 检查适配器没有复制核心命令和约束。

### 5.3 把当前状态和长期规则混在一起

例如把“本周正在迁移的分支”“临时测试账号”“尚未决定的方案”写进根规则。状态很快过时，agent 仍会把它当长期指令。

建议把状态放 issue、研究记录、release note 或明确带日期/状态的文档；`AGENTS.md` 只保留指向状态源的入口。

### 5.4 裸链接和深链路

只写“详见 `docs/foo.md`”通常不够。应该说明：

- 什么任务必须读；
- 该文档是架构事实、命令清单还是流程；
- 读完后要运行什么验证；
- 如果与代码冲突，以什么为准。

避免 `AGENTS.md -> index -> guide -> reference -> example` 五层链路承载关键规则。第一跳必须足够阻止最大的错误。

### 5.5 把确定性规则写成自然语言，把判断性规则交给脚本

例如“所有文件必须 2 空格缩进”应由 formatter 负责；“不要修改 generated client，修改来源 schema 后重新生成”才是适合写进 agent 文档的边界。

### 5.6 让 `/init` 生成结果直接成为永久规范

自动生成适合启动，不适合直接合并。必须由维护者删除：

- 可以从 package manifest 推断出的废话；
- 与任务无关的产品介绍；
- 已被 linter/CI 强制的重复规则；
- 不可验证或没有 owner 的主观偏好。

### 5.7 把 `AGENTS.md` 当作安全边界

它是模型上下文，不是权限系统。仓库、issue、依赖、skill 和外部文档都可能包含 prompt injection。安全边界应该来自 sandbox、工具权限、审批、网络隔离、secret 管理和 CI policy。[Anthropic 对项目配置和 trust boundary 的说明](https://www.anthropic.com/engineering/how-we-contain-claude)明确提醒：外部资源同时带来代码执行风险和 prompt injection 风险；VS Code 也建议对不可信项目使用 Workspace Trust、sandbox 或 dev container。

## 6. 推荐的多仓库治理架构

### 6.1 四层模型

```text
个人/组织层
  只放跨项目的安全底线、语言偏好、工具使用偏好

仓库层
  repo/AGENTS.md
  只放该仓库的入口、边界、命令、验证和低频资料路由

子项目层
  repo/apps/foo/AGENTS.md
  repo/services/bar/AGENTS.md
  只放该目录独有的运行时、测试和 ownership 规则

按需能力层
  repo/.agents/skills/<skill>/SKILL.md
  放可复用流程、模板、诊断、迁移和专项 review
```

关键原则：每一层都要有不同的责任，不能只是把同一份文本复制到不同目录。

### 6.2 根目录 `AGENTS.md` 建议结构

```markdown
# AGENTS.md

## Scope
- This file applies to the repository root and descendants.
- Deeper instructions may add local rules; they must not silently remove root safety boundaries.

## Start here
- Read `docs/README.md` for the documentation map.
- Read `docs/architecture/...` only for architecture-affecting changes.
- Read `.agents/skills/<name>/SKILL.md` for the listed task families.

## Repository map
- `apps/`: deployable applications
- `packages/`: shared libraries
- `generated/`: generated output; edit the source and regenerate

## Hard-to-infer rules
- Do not edit generated files directly.
- Do not run production mutations from a local development task.
- Preserve the public API contract in `docs/...` and add a regression test for changes.

## Commands
- Install: `...`
- Fast validation: `...`
- Full validation: `...`
- Targeted test: `...`

## Task routing
- UI changes: load `.agents/skills/product-design/SKILL.md`.
- Release changes: load `.agents/skills/release/SKILL.md`.

## Source of truth
- Human contribution guide: `CONTRIBUTING.md`.
- Architecture decisions: `docs/adr/`.
- Current project state: issue tracker / `docs/status/`, not this file.
```

这个结构不要求所有仓库字面一致；它的价值在于稳定地回答六个问题：作用域、入口、目录、不能做什么、怎么验证、何时加载哪个专项流程。

### 6.3 多仓库集中维护方式

推荐维护一个独立的 `agent-governance` 仓库或目录，存放：

- 模板和示例；
- `agnix` / `agentslint` 配置；
- 适配器生成脚本；
- 固定的 smoke tasks 和评测脚本；
- 版本化的组织级安全基线；
- 仓库 inventory：每个仓库的 owner、agent 支持范围、规则源、最近验证时间。

但不要把每个仓库的 `AGENTS.md` 都变成中央仓库的 symlink。仓库规则应和代码同仓库演进，这样 code change 与 agent guidance 可以在同一个 PR 中审查。中央层只提供模板、检查和生成能力。

### 6.4 规则和文档的 owner 模型

每条高价值规则最好能回答：

| 字段 | 示例 |
|---|---|
| Owner | `apps/payments` maintainers |
| Scope | `apps/payments/**` |
| Source of truth | `docs/adr/012-payment-boundary.md` |
| Enforcement | `make check-architecture` |
| Trigger | 修改 payment API、schema 或 reconciliation |
| Freshness | 随相关代码 PR 更新 |
| Exception | 允许的 migration window 或明确 issue |

不一定要把这些字段做成 frontmatter；在跨工具场景中，普通 Markdown headings 更兼容。重点是 owner 和验证路径不能只存在于维护者脑中。

## 7. 工具和 agent skills 选择

### 7.1 工具

| 工具 | 适合解决什么 | 成熟度/注意点 |
|---|---|---|
| [`agentslint`](https://pypi.org/project/agentslint/) | 校验路径、内部链接、npm/Make/Python/Go 命令和 scope ambiguity；只读，不执行 AGENTS 中的命令 | 适合作为低风险 CI 基线；Python 工具，先在少数仓库试点 |
| [`agents-lint`](https://github.com/giacomo/agents-lint) | 检查 stale paths、npm scripts、依赖和多仓库脚本，支持 JSON/CI | 功能方向相近，适合 Node 生态；需比较维护活跃度和规则误报 |
| [`agnix`](https://github.com/agent-sh/agnix) | 跨 Claude、Cursor、Copilot、Codex、MCP、skills 等配置的规则检查，带 autofix 和 GitHub Action | 覆盖面最广，但规则集合变化快；固定版本并 review autofix |
| [`agents-md-kit`](https://github.com/reaatech/agents-md-kit) | AGENTS/SKILL 的 scaffold、schema validation、lint 和报告 | 更像工具包/脚手架，不应把其 schema 当开放格式标准 |
| [`skills-ref`](https://github.com/agentskills/agentskills/tree/main/skills-ref) | 校验 Agent Skills 的 frontmatter 和目录结构 | 官方生态参考实现，但项目明确说不适合作为生产 SDK |
| Next.js `agents-md` codemod | 根据 Next.js 版本生成匹配文档和压缩索引 | 框架特定工具；体现“版本化 docs index”模式，不是通用仓库治理器 |
| `gh skill` / `skills` CLI | 发现和安装 skills | 解决分发，不解决规则真源、漂移和安全审查 |

初始落地建议：先用一个 referential-integrity checker + 一个跨工具 config linter；不要一开始同时部署四个相似 lint 工具。

### 7.2 skills 的推荐职责

技能适合以下任务：

- 发布/迁移/诊断/安全 review 等低频、步骤多的工作；
- 需要脚本、模板、references 和 examples 的流程；
- 需要隔离上下文或专门验证器的任务；
- 需要按任务或路径选择资料，而不是每次都加载的知识。

技能不适合承载：

- 每次 agent session 都必须遵守的安全边界；
- 只有一行的 repo 规则；
- 仅仅为了避开根文件长度而把核心规则藏起来。

在当前 `agents-lab` 仓库中，`skills-governance/skills/` 下的 `skills-governance-audit`、`skills-cli-reconcile`、`skills-intake-local`、`skills-promote-global` 和 `skills-lifecycle-manager` 可以作为多仓库治理的参考形态：前者覆盖只读审计、canonical source/adapter reconciliation、本地 Skill intake、global suitability judgment 和生命周期选择。它们是治理包内的 repo-scoped Skills，不应未经参数化就直接提升为所有项目的 global Skill；`agent-research` 则继续负责证据记录与研究沉淀。

## 8. 建议的验证体系

### 8.1 静态验证

每个仓库的 CI 至少检查：

- 所有 instruction files 和 skills 是否可解析；
- 所有相对链接、文档路径、脚本路径是否存在；
- 文档中引用的 package script / Make target 是否存在；
- `CLAUDE.md`、Copilot、Cursor 等适配器是否与 canonical source 漂移；
- 是否出现本机绝对路径、secret、不可移植用户名或环境变量；
- 是否超过本组织约定的 context budget；
- 是否有明显的空泛语句、重复规则和未说明用途的引用。

### 8.2 行为评测

仅有 lint 不够，因为“文件存在”不等于 agent 会正确执行。每个仓库选择 3–8 个固定 smoke tasks：

- 一个普通 bug fix；
- 一个跨模块改动；
- 一个需要读取架构文档的任务；
- 一个必须运行特定测试的任务；
- 一个应拒绝或要求确认的危险操作；
- 如果有 skill，再加一个“应该触发”和一个“明确不应触发”的任务。

比较规则版本变更前后的：

- 任务成功率和测试通过率；
- 修改文件数、无关改动数；
- 工具调用次数和 token 成本；
- 是否读了预期文档/skill；
- 是否执行了不应执行的命令；
- agent 是否明确报告未验证事项。

要把“skill 没有触发”和“触发后没有遵守”分开测；Vercel 的实践明确指出这两个失败原因不同。

### 8.3 运行时可观察性

对真实使用至少记录：

- agent 使用的 cwd 和识别出的 project root；
- 实际加载了哪些 instruction files；
- 使用了哪些 skills；
- 哪些命令因文档不存在而失败；
- PR review 中哪些错误反复出现。

如果工具没有显示加载清单，优先增加一个 debug/status 命令或 wrapper；不要只根据 agent 自己说“我读过了”来判断。

## 9. 分阶段迁移计划

### 阶段 A：盘点

扫描所有仓库和用户级配置，建立 inventory：

- 文件路径、工具、scope、owner；
- 字数/行数；
- 命令和路径引用；
- 与其它配置的重复/冲突；
- 是否有最近维护记录；
- 是否绑定某个 repo、vault、账号或机器路径。

先只读，不批量重写。

### 阶段 B：定义 canonical source

每个仓库选择唯一的共享正文源。通常是根 `AGENTS.md`；Claude 用 import/symlink，Copilot/Cursor 等保留最小工具适配内容。

明确三类内容的去处：

- stable rules；
- task workflows；
- current state。

### 阶段 C：试点三个差异明显的仓库

选择：一个单体应用、一个 monorepo、一个强运维/发布仓库。为每个仓库建立短根文件、一个局部文件和一个 skill，接入静态检查和 3–8 个 smoke tasks。

### 阶段 D：迁移和删除重复

把重复正文合并进 canonical source；适配器只保留 tool-specific 语法。任何删除都要先跑静态检查和行为 smoke tasks，避免“看起来更干净”却失去关键边界。

### 阶段 E：持续治理

- 规则文件随代码/命令变更一起 review；
- 每月或每个大版本运行 stale-reference scan；
- 每季度重跑固定 smoke tasks；
- 记录一条规则导致的真实错误或节省的探索；没有证据的规则进入候选删除清单；
- 对 skill 做版本、provenance、依赖和触发回归。

## 10. 最终判断

如果目标是降低多仓库维护痛点，最值得先做的不是“设计一份完美的 AGENTS.md 模板”，而是建立以下最小系统：

1. 每个仓库一个短的 canonical `AGENTS.md`；
2. 只在目录边界增加少量局部文件；
3. `CLAUDE.md` 等工具文件只做薄适配器；
4. 详细事实进入有 owner 的 `docs/`，低频流程进入 skills；
5. path/script/link/重复/冲突进入 CI 检查；
6. 用固定任务评测规则改动是否真的改善 agent 行为；
7. 把全局层限制在真正跨仓库的偏好和安全策略，不让 repo-specific 规则泄漏到所有项目；
8. 把任何来自不可信仓库或外部内容的指令视为数据，靠权限和 sandbox 保证安全。

其中最重要的工程决策是“canonical source + generated/adapted targets”，而不是“每个工具一份手写文档”。这与现有 `agents-lab` 的文档分层原则一致：入口负责路由，深层文档负责参考，skill 负责执行，状态页负责当前状态，机械工具负责可判定约束。

## Sources

1. [AGENTS.md official site](https://agents.md/)
2. [OpenAI — Harness engineering: leveraging Codex in an agent-first world](https://openai.com/index/harness-engineering/)
3. [OpenAI Codex — AGENTS.md discovery implementation](https://github.com/openai/codex/blob/main/codex-rs/core/src/agents_md.rs)
4. [Anthropic — Claude Code memory](https://code.claude.com/docs/en/memory)
5. [Cursor — Rules for AI](https://docs.cursor.com/context/rules-for-ai)
6. [Gemini CLI — Provide context with GEMINI.md files](https://geminicli.com/docs/cli/gemini-md/)
7. [GitHub — About Copilot code review customization](https://docs.github.com/en/copilot/concepts/agents/code-review)
8. [Windsurf — Cascade memories and rules](https://docs.windsurf.com/windsurf/cascade/memories)
9. [Vercel — AGENTS.md outperforms skills in our agent evals](https://vercel.com/blog/agents-md-outperforms-skills-in-our-agent-evals)
10. [Vercel — Teaching agents product design at Vercel](https://vercel.com/blog/teaching-agents-product-design-at-vercel)
11. [Temporal Java SDK — AGENTS.md](https://github.com/temporalio/sdk-java/blob/main/AGENTS.md)
12. [Temporal — AGENTS.md](https://github.com/temporalio/temporal/blob/main/AGENTS.md)
13. [Kubernetes agent-sandbox — AGENTS.md](https://github.com/kubernetes-sigs/agent-sandbox/blob/main/AGENTS.md)
14. [Next.js — Skills authoring guide](https://github.com/vercel/next.js/blob/canary/.agents/skills/README.md)
15. [Lulla et al. — On the Impact of AGENTS.md Files on the Efficiency of AI Coding Agents](https://arxiv.org/abs/2601.20404)
16. [Gloaguen et al. — Evaluating AGENTS.md](https://arxiv.org/abs/2602.11988)
17. [dos Santos et al. — Configuration Smells in AGENTS.md Files](https://arxiv.org/html/2606.15828)
18. [Han et al. — SWE-Skills-Bench](https://arxiv.org/abs/2603.15401)
19. [Agent Skills specification](https://github.com/agentskills/agentskills/blob/main/docs/specification.mdx)
20. [agentslint](https://pypi.org/project/agentslint/)
21. [agnix](https://github.com/agent-sh/agnix)
22. [agents-md-kit](https://github.com/reaatech/agents-md-kit)
23. [skills-ref](https://github.com/agentskills/agentskills/tree/main/skills-ref)
24. [Anthropic — How we contain Claude](https://www.anthropic.com/engineering/how-we-contain-claude)
25. [VS Code — Secure AI-assisted development](https://code.visualstudio.com/docs/agents/run/security)
