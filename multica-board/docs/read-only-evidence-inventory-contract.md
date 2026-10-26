# Atlas 只读证据契约：Skills / Memory inventory

状态：ACM-580 已通过（2026-10-26）。ACM-581 按本文实现只读 inventory。本文仍是需求契约。

## 1. 目标与边界

Atlas 增加一份跨来源、只读的 evidence inventory 需求契约。契约回答「对象在哪里、属于谁、何时看到、证据有多强」，不回答「对象当前是否被 runtime 加载」；后者只有独立运行时证据才能确认。

范围：

- Skills：本机、Multica Workspace、Grok Bot 名称声明分层展示。
- Memory：本单只定义证据槽位和依赖，不声称今天 Atlas 已有正文或出口。
- 同名对象保持来源分离；同名只建立 `related_name` 关联，不合并成一个资产。
- 只返回必要元数据；不返回正文、secret、token、env 值、header 值、credential、Cookies。
- 不提供写入、同步、删除、升级、批准、合并或执行命令。
- Grok CLI 不等于 Grok Bot；两者必须是不同 `source_kind`。

非目标：受控 Git 写回、Skill 正文同步、Memory 正文同步、Grok Bot 私有 RPC 逆向、自动推断逐 Bot 加载状态。

## 2. 证据等级

`evidence_level` 是本次观察对对象事实的强度，不是对象健康度，也不是 runtime 加载结论。由采集器显式填写，前端不得从字段存在与否自行升级。

| 枚举 | 含义 | 允许的事实 |
|---|---|---|
| `observed` | 本次从允许的本地文件、已授权只读 API 或明确 CLI 输出直接观察到 | 可说「来源在采集时存在」，可带路径/对象 ID |
| `declared` | 仓库 manifest、Grok Bot manifest 或 workspace 元数据声明存在 | 只能说「声明存在」，不能说已安装、已加载或正文存在 |
| `derived` | 由多个已记录字段机械推导 | 只能用于固定推导，如同名关联、scope 分类；必须保留输入证据 |
| `historical` | 历史采集结果，非本轮观察 | 必须带 `observed_at`，不得当作当前状态 |
| `unknown` | 读取失败、权限不足、来源不支持或证据互相冲突 | 不得降级成 absent |

建议展示映射：`observed` 正常、`declared` 蓝灰、`derived` 灰、`historical` 淡化、`unknown` 黄色问号。颜色只表达证据状态，不表达风险结论。

## 3. 未采集态

未采集不是空列表，不是 absent，不是 0。所有来源都必须能表达以下状态：

| `collection_state` | 含义 | 必须字段 | 展示 |
|---|---|---|---|
| `collected` | 本轮完成读取并得到结果，结果可为空 | `collected_at`、`evidence_level` | 显示结果或「已采集，0 条」 |
| `uncollected` | 本轮没有运行该来源采集，或该来源尚未接入 | `collected_at:null`、`reason` | `— 未采集` |
| `unavailable` | 来源理论上可读，但当前不可访问/未登录/无权限 | `reason`、可选 `error_code` | `? 无法确定`，不能显示 0 |
| `invalid` | 来源返回内容存在但不符合契约 | `reason`、`error_code` | 整体错误态，不渲染成空 |
| `not_applicable` | 该字段对来源不适用 | `collected_at` 可为 null、`reason` | `∅ 不适用` |

`reason` 使用稳定代码，至少包括：`not_implemented`、`source_not_configured`、`not_logged_in`、`permission_denied`、`read_failed`、`schema_invalid`、`no_supported_export`、`private_rpc_not_supported`。底层异常、路径中的秘密、token 不进入响应。

## 4. 统一对象契约

以下为需求级 shape；字段名在实现单中冻结，不能用前端文案替代机器字段。

```json
{
  "schema": "atlas.evidence_inventory/v1",
  "generated_at": "2026-10-26T09:00:00Z",
  "read_only": true,
  "sources": [
    {
      "source_id": "local",
      "source_kind": "local_machine",
      "label": "本机",
      "scope": "bot-private",
      "collection_state": "collected",
      "collected_at": "2026-10-26T09:00:00Z",
      "evidence_level": "observed",
      "reason": null
    }
  ],
  "items": [
    {
      "object_type": "skill",
      "object_id": "stable-source-id/name-or-provider-id",
      "display_name": "example",
      "source_id": "local",
      "source_kind": "local_machine",
      "scope": "shared",
      "collection_state": "collected",
      "observed_at": "2026-10-26T09:00:00Z",
      "evidence_level": "observed",
      "hash": { "state": "available", "algorithm": "sha256", "value": null },
      "evidence": {
        "kind": "entry",
        "location": "redacted-or-approved-path",
        "object_ref": null,
        "source_label": "本机只读扫描"
      },
      "runtime_loaded": null,
      "related_name": null,
      "reason": null
    }
  ]
}
```

### 4.1 字段规则

- `object_id`：来源内稳定 ID；不得用显示名跨来源去重。无法获得稳定 ID 时使用本次来源内可重复的合成 ID，并标 `evidence_level: derived`。
- `display_name`：原始名称展示值；不做大小写、Unicode、空白归一化后再合并。
- `source_kind`：只允许 `local_machine`、`multica_workspace`、`grok_bot`、`grok_cli`、`repository`。`grok_cli` 与 `grok_bot` 永不互换。
- `scope`：只允许 `bot-private`、`shared`、`workspace`、`repository`、`unknown`。`bot-private` 表示只属于单 Bot；`shared` 表示共享入口/共享声明；不因名称相同把两者合并。
- `observed_at`：对象事实观察时间；`collected_at`：来源采集完成时间。两者不可互代。
- `hash.state`：`available`、`unavailable`、`not_collected`、`not_applicable`。`value` 需求上禁止展示原文；若未来需要对账，另定脱敏指纹权限和保留期。
- `runtime_loaded`：本契约固定为 `null`，除非未来接入独立 runtime 观测协议；路径存在不等于已加载。
- `evidence.location`：只保留经过脱敏的路径/位置标签；不输出正文和凭证。Grok Bot 私有服务端对象没有合法本地位置时填 `null`。
- `related_name`：同名关联的来源内外索引，仅供对照，不是合并 ID，不证明内容一致。

## 5. 来源分层与 Skills 对照

| 来源 | `source_kind` | 允许证据 | 不允许结论 | scope 默认 |
|---|---|---|---|---|
| 本机文件/目录 | `local_machine` | 入口、目录、权限结果、采集时间、可用指纹元数据 | 当前会话一定加载 | `shared` 或 `bot-private`，由根配置决定 |
| 仓库声明 | `repository` | manifest、tool roots、加载规则声明 | 本机已安装/当前已加载 | `repository` |
| Multica Workspace | `multica_workspace` | workspace 安装元数据、对象 ID、更新时间、授权范围 | 正文已采集；未采集不写成无正文 | `workspace` 或 `shared` |
| Grok Bot 名称声明 | `grok_bot` | Bot ID/name、声明来源、声明时间、证据等级 | 有本机入口、版本、正文、当前加载 | `bot-private` 或 `shared`，以声明为准 |
| Grok CLI | `grok_cli` | CLI 能直接输出的只读元数据 | 等同 Grok Bot、等同本机共享目录 | `bot-private` 或 `shared`，以 CLI 证据为准 |

当前 Skills API 已有可复用规则：`GET /api/skills` 分离本机/仓库/Multica Workspace/Grok Bot；Grok Bot 行明确没有正文、版本或本机 Runtime 安装入口；`∅` 不是未采集；workspace 正文可显示 `uncollected`。新契约必须保留这些语义，不能把新 inventory 变成另一套合并口径。

| 现有 `#/configure/skills` / `/api/skills` | 目标 evidence inventory | 处理 |
|---|---|---|
| `items[]` 按 skill 名合并展示 | `items[]` 按 `source_id + object_id` 保持来源独立 | 保留来源分层，`related_name` 仅关联 |
| `tools` 的 `direct/shared/missing/null` | `evidence.kind` + `collection_state` + `evidence_level` | 不把路径标记升级成加载事实 |
| `shared_entry.state`：`ok/absent/denied/unknown/unscanned` | `collection_state` 与稳定 `reason` | `unscanned` 映射 `uncollected`，`denied` 映射 `unavailable`，不映射 absent |
| `runtime_evidence` | Skills 页可对显式禁用写 `runtime_loaded:false`（ACM-360）；本清单接口仍恒为 null | 现有 `disk_installations` 只能证明入口，不证明加载 |
| Multica Workspace 列 `—` | `collection_state:uncollected` | 继续显示「未采集」，不能显示「无」 |
| Grok Bot `∅`、名称声明 | `source_kind:grok_bot` + `collection_state:collected` + `evidence_level:declared` | 只声明名称；不生成正文/版本/安装记录 |
| `GET /api/skills/content` 正文读取 | 本契约无 `content` 字段 | 不复用正文出口；本单只读元数据 |

## 6. Memory 依赖

本单不新增 Memory 出口。当前 Atlas 不应宣称能看见 Grok Bot Memory 正文，也不应把 Skills 清单当 Memory 证据。

后续「仅元数据快照、无正文」依赖至少提供：

- 每个 Bot 独立 `bot_id`、profile/journal 类型和来源标签。
- `scope` 明确区分 `bot-private`、`shared_profile`、`shared_journal`；共享不是跨 Bot 正文合并。
- `collection_state`、`collected_at`、`evidence_level`、对象 ID、条目数/更新时间等最小元数据。
- 授权的只读导出或稳定元数据 API。未确认出口前保持 `uncollected`/`unavailable`，不猜测私有 RPC。
- 正文、向量、检索结果、敏感字段不进入本契约。未来正文查看须另立授权、脱敏、审计契约。

推荐后续依赖 shape：`object_type:memory_collection`，`object_id` 绑定 Bot/profile，`hash.state:not_applicable` 或 `not_collected`；不把 `shared_profile` 和某个 Bot 的 `bot-private` 记录合并。

## 7. 只读验收标准

1. 同名本机、workspace、Grok Bot、Grok CLI 对象并列存在，来源 ID 不碰撞；有 `related_name` 也不合并。
2. 每个 source 和 item 都能表达 `collected`、`uncollected`、`unavailable`、`invalid`、`not_applicable`；未采集不显示 0/无。
3. 每条记录包含来源、对象 ID、scope、采集/观察时间、哈希可用性、证据等级、证据位置/引用、原因或未采集状态。
4. 响应没有正文、secret、token、env/header 值、credential、Cookies；没有写入或执行命令字段。
5. `runtime_loaded` 不由路径存在、Skill 名称声明或 workspace 安装元数据推断为 true。
6. Grok CLI 与 Grok Bot 使用不同 `source_kind`；名称声明不生成本机安装、版本、正文证据。
7. Memory 当前没有出口时，页面/API 明确显示未采集或不可用，不伪造数据；元数据快照依赖单独标出。
8. 任何读取错误保留 `unknown/unavailable` 语义，不降级成 absent；任何 schema 错误进入整体 invalid 态。
9. 页面若落地，按 `docs/design-system.md` 采用版式 A（清单治理）或说明偏离理由：固定范围说明、不使用无效 workspace 筛选、来源健康条、`?`/`∅`/`—` 语义分开、只读提示与 Skills 一致。页面验收需附 1440px 和 1100px 对照截图。

## 8. 证据标注

- **F（文件/API 引用）**：`multica-board/docs/design-system.md:11-22, 26-41, 99-184` 定义数据层、Skills 版式、状态符号和数据契约；`multica-board/docs/api-contract.md:575-615` 定义 `/api/skills`、未采集、Grok Bot 名称声明和 runtime 证据边界；`multica-board/docs/roadmap.md:112-125` 定义只读 inventory、memory 先索引私有库、配置元数据最小化和统一写入安全依赖。
- **I（推断）**：同名不合并、`runtime_loaded:null`、来源内合成 ID、Memory `memory_collection` shape，是依据现有分层和风险边界提出的契约推导，不能当作现有实现。
- **D（设计建议）**：本文第 2–9 节的枚举、统一 JSON shape、Memory 元数据依赖和验收条目已由 ACM-580 通过。ACM-581 的实现对照见第 10 节。

## 9. 明确不展开

选项 3（受控 Git 写回、同步、冲突解决、dry-run 执行链、授权确认、审计写入）继续等待统一写入安全设计。本单不定义写回 API、命令、权限模型或 UI 操作。任何实现单必须先引用本契约审定版本，并通过统一写入安全门槛。

## 10. ACM-581 实现对照

只读接口是 `GET /api/evidence-inventory`，页面入口是 `#/configure/skills/evidence`。现有 `#/configure/skills` 与 `GET /api/skills` 的合并口径不变。

- 本机目录、仓库声明、Workspace 安装、每个 Grok Bot 的名称声明、Grok CLI 元数据各自一条。`related_name` 使用原始 `display_name`，区分大小写。
- 没有 Grok CLI 只读输出时，该来源保持 `uncollected` / `not_implemented`。`~/.grok/skills` 仍算 `local_machine`，不算 Grok CLI，也不算 Grok Bot。
- Grok Bot 行是 `declared`。清单里的历史实测标记不升级成当前 `observed`。
- Memory 使用 `source_id: memory` 的三条槽位（`bot-private`、`shared_profile`、`shared_journal`），`collection_state: uncollected`，`source_kind` 只标记未来依赖落在 Grok Bot，不并入技能行，无正文。
- `hash.value` 恒为 null。`runtime_loaded` 恒为 null。
- 页面沿用版式 A 的来源卡片、图例、固定范围和只读提示。不设待处理栏和命令区：本页不分配治理桶，也不提供写回。未采集显示 `—`，无法确定显示 `?`，不适用显示 `∅`。

