/** Fictional ACM Lab layer scenarios. `node fixtures/write-atlas.js` writes the JSON the page fetches. */

export const ACM = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
export const BETA = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
export const GAMMA = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
export const NOW = "2026-10-14T09:00:00Z";

function layer(primary, involved = [], governance = false, source = {}) {
  return {
    primary,
    involved,
    governance,
    source: {
      primary: source.primary ?? (primary ? "own" : null),
      involved: source.involved ?? (involved.length ? "own" : null),
      governance: source.governance ?? (governance ? "own" : null),
    },
  };
}

function issue(fields) {
  const ws = fields.workspace_id || ACM;
  return {
    key: `${ws}:${fields.id}`,
    workspace_id: ws,
    id: fields.id,
    identifier: fields.identifier,
    title: fields.title,
    status: fields.status,
    status_category: fields.status_category || fields.status,
    priority: "none",
    assignee: fields.assignee || { type: "agent", id: "agent-builder", name: "Builder" },
    project: { id: "proj-atlas", title: "Agent Atlas" },
    parent_identifier: fields.parent || null,
    due_date: null,
    created_at: "2026-09-01T09:00:00Z",
    last_activity_at: fields.at || "2026-10-13T09:00:00Z",
    url: `https://multica.example/acm-lab/issues/${fields.id}`,
    latest_run: fields.latest_run ?? null,
    last_failed_run: null,
    ...(fields.layer === undefined ? {} : { layer: fields.layer }),
  };
}

const vega = { type: "agent", id: "agent-vega", name: "Vega" };
const designer = { type: "agent", id: "agent-design", name: "前端设计师" };

export function atlasIssues() {
  return [
    issue({ id: "acm-510", identifier: "ACM-510", title: "监督页的空态说明", status: "done", layer: layer("l5"), assignee: designer }),
    issue({ id: "acm-511", identifier: "ACM-511", title: "每日清单只读预览", status: "in_progress", layer: layer("l5"), assignee: designer }),
    issue({
      id: "acm-514",
      identifier: "ACM-514",
      title: "状态桥接改成持久 outbox",
      status: "in_review",
      at: "2026-10-12T09:00:00Z",
      layer: layer("l5", [], true),
      assignee: vega,
    }),
    issue({
      id: "acm-515",
      identifier: "ACM-515",
      title: "评估：状态桥接改为脚本直发",
      status: "blocked",
      at: "2026-09-20T09:00:00Z",
      parent: "ACM-500",
      layer: layer("l5", ["l3"], false, { primary: "inherited:ACM-500", involved: "inherited:ACM-500" }),
      assignee: vega,
    }),
    issue({ id: "acm-520", identifier: "ACM-520", title: "派活交接草稿", status: "done", layer: layer("l4") }),
    issue({
      id: "acm-521",
      identifier: "ACM-521",
      title: "跨 workspace 全量迁移",
      status: "todo",
      layer: layer("l4", ["l3", "l5"]),
    }),
    issue({
      id: "acm-522",
      identifier: "ACM-522",
      title: "Backlog triage：每周三条",
      status: "blocked",
      at: "2026-10-10T09:00:00Z",
      layer: layer("l4", [], true),
    }),
    issue({ id: "acm-530", identifier: "ACM-530", title: "skills 抽屉信息架构", status: "done", layer: layer("l3"), assignee: designer }),
    issue({
      id: "acm-531",
      identifier: "ACM-531",
      title: "把 ColGrep 写成 Coding agent skill",
      status: "in_progress",
      layer: layer("l3", ["l1"]),
    }),
    issue({
      id: "acm-532",
      identifier: "ACM-532",
      title: "冲突检测扩到职责重叠",
      status: "in_review",
      at: "2026-10-06T09:00:00Z",
      parent: "ACM-122",
      assignee: designer,
      layer: layer("l3", [], true, { primary: "inherited:ACM-122", governance: "inherited:ACM-122" }),
      latest_run: {
        task_id: "task-532",
        status: "completed",
        agent_name: "前端设计师",
        runtime_id: "rt-mini",
        started_at: "2026-10-13T08:00:00Z",
        completed_at: "2026-10-13T09:00:00Z",
        error_kind: null,
        error_summary: null,
      },
    }),
    issue({ id: "acm-533", identifier: "ACM-533", title: "Instructions 草稿不进层视图工具栏", status: "todo", layer: layer("l3") }),
    issue({ id: "acm-540", identifier: "ACM-540", title: "daemon 在线点", status: "done", layer: layer("l2") }),
    issue({ id: "acm-541", identifier: "ACM-541", title: "Agent 矩阵保留失败列", status: "in_progress", layer: layer("l2", [], true) }),
    issue({
      id: "acm-542",
      identifier: "ACM-542",
      title: "Orca 本机存储占用",
      status: "in_review",
      at: "2026-10-13T09:00:00Z",
      layer: layer("l2"),
      assignee: vega,
    }),
    issue({ id: "acm-550", identifier: "ACM-550", title: "额度信号借用矩阵", status: "todo", layer: layer("l1") }),
    issue({ id: "acm-551", identifier: "ACM-551", title: "用量采集调查", status: "done", layer: layer("l1") }),
    issue({ id: "acm-560", identifier: "ACM-560", title: "存储清理", status: "done", layer: layer("outside") }),
    issue({ id: "acm-561", identifier: "ACM-561", title: "英语学习打卡", status: "todo", layer: layer("outside") }),
    issue({
      id: "acm-562",
      identifier: "ACM-562",
      title: "Clip 收藏整理",
      status: "in_review",
      at: "2026-10-13T21:00:00Z",
      layer: layer("outside"),
    }),
    issue({ id: "acm-570", identifier: "ACM-570", title: "还没标层的根 issue", status: "todo", layer: layer(null) }),
    issue({
      id: "acm-571",
      identifier: "ACM-571",
      title: "子 issue 只填了涉及层",
      status: "in_review",
      at: "2026-09-01T09:00:00Z",
      parent: "ACM-570",
      layer: layer(null, ["l2"], false, { primary: null, involved: "own", governance: null }),
    }),
    issue({ id: "acm-580", identifier: "ACM-580", title: "已取消的能力层试验", status: "cancelled", status_category: "cancelled", layer: layer("l3") }),
    issue({
      id: "bet-1",
      identifier: "BET-1",
      title: "别的 workspace 待验收",
      status: "in_review",
      workspace_id: BETA,
      layer: layer("l4"),
    }),
  ];
}

export function meta() {
  return {
    api_version: "0",
    generated_at: NOW,
    collect_interval_s: 60,
    multica_web_base: "https://multica.example",
    collector: { last_attempt_at: NOW, last_success_at: NOW, error: null },
    workspaces: [
      {
        id: ACM,
        slug: "acm-lab",
        name: "ACM Lab",
        collect_state: "ok",
        last_success_at: NOW,
        last_attempt_at: NOW,
        error: null,
        counts: { open_issues: 15, agents: 2, runtimes: 2 },
      },
      {
        id: BETA,
        slug: "beta-coach",
        name: "Beta 辅导",
        collect_state: "ok",
        last_success_at: NOW,
        last_attempt_at: NOW,
        error: null,
        counts: { open_issues: 1, agents: 0, runtimes: 0 },
      },
      {
        id: GAMMA,
        slug: "gamma-oasis",
        name: "Gamma Oasis",
        collect_state: "error",
        last_success_at: "2026-10-14T08:41:00Z",
        last_attempt_at: NOW,
        error: { kind: "timeout", message: "issue list timed out after 20s" },
        counts: { open_issues: 4, agents: 1, runtimes: 1 },
      },
    ],
  };
}

function change(id, identifier, at, status, previous) {
  return {
    kind: "issue_status",
    at,
    key: `${ACM}:${id}`,
    workspace_id: ACM,
    id,
    identifier,
    status,
    status_category: status,
    previous_status: previous,
    previous_status_category: previous,
  };
}

export function atlasChanges() {
  return {
    generated_at: NOW,
    cursor: "v1:7",
    reset: false,
    retained_days: 7,
    has_more: false,
    items: [
      change("acm-514", "ACM-514", "2026-10-12T09:00:00Z", "in_review", "in_progress"),
      change("acm-515", "ACM-515", "2026-09-20T09:00:00Z", "blocked", "in_progress"),
      change("acm-522", "ACM-522", "2026-10-10T09:00:00Z", "blocked", "todo"),
      change("acm-532", "ACM-532", "2026-10-06T09:00:00Z", "in_review", "in_progress"),
      change("acm-542", "ACM-542", "2026-10-13T09:00:00Z", "in_review", "in_progress"),
      change("acm-562", "ACM-562", "2026-10-13T21:00:00Z", "in_review", "todo"),
      change("acm-571", "ACM-571", "2026-09-01T09:00:00Z", "in_review", "in_progress"),
    ],
  };
}

export function agents() {
  return {
    generated_at: NOW,
    recent_window_h: 24,
    items: [
      {
        key: `${ACM}:agent-builder`,
        workspace_id: ACM,
        id: "agent-builder",
        name: "Builder",
        status: "idle",
        archived: false,
        runtime_id: "rt-mini",
        model: null,
        thinking_level: null,
        service_tier: null,
        max_concurrent_tasks: 4,
        tasks: { running: 0, queued: 0 },
        recent: {
          completed: 3,
          failed: 1,
          last_failed_at: "2026-10-14T06:00:00Z",
          last_error_kind: "quota",
          last_failed_issue_identifier: "ACM-542",
        },
        url: "https://multica.example/acm-lab/agents/agent-builder",
      },
      {
        key: `${ACM}:agent-vega`,
        workspace_id: ACM,
        id: "agent-vega",
        name: "Vega",
        status: "working",
        archived: false,
        runtime_id: "rt-mini",
        model: "gpt-5.5-medium",
        thinking_level: "medium",
        service_tier: null,
        max_concurrent_tasks: 2,
        tasks: { running: 1, queued: 0 },
        recent: {
          completed: 2,
          failed: 0,
          last_failed_at: null,
          last_error_kind: null,
          last_failed_issue_identifier: null,
        },
        url: "https://multica.example/acm-lab/agents/agent-vega",
      },
    ],
  };
}

export function runtimes() {
  return {
    generated_at: NOW,
    items: [
      {
        key: `${ACM}:rt-mini`,
        workspace_id: ACM,
        id: "rt-mini",
        name: "mini",
        provider: "codex",
        runtime_mode: "local",
        status: "online",
        last_seen_at: NOW,
        device_info: "dev-mini",
      },
      {
        key: `${ACM}:rt-air`,
        workspace_id: ACM,
        id: "rt-air",
        name: "air",
        provider: "claude",
        runtime_mode: "local",
        status: "offline",
        last_seen_at: "2026-10-13T09:00:00Z",
        device_info: "macbook-air",
      },
    ],
  };
}

export function runs() {
  return {
    generated_at: NOW,
    by_issue: {
      [`${ACM}:acm-532`]: [
        {
          key: `${ACM}:task-532`,
          task_id: "task-532",
          status: "completed",
          agent_name: "前端设计师",
          runtime_id: "rt-mini",
          started_at: "2026-10-13T08:00:00Z",
          completed_at: "2026-10-13T09:00:00Z",
          error_kind: null,
          error_summary: null,
        },
      ],
    },
  };
}

function stripLayer(items) {
  return items.map((item) => {
    const copy = { ...item };
    delete copy.layer;
    return copy;
  });
}

function unfilled(items) {
  return items.map((item) => ({
    ...item,
    layer: { primary: null, involved: [], governance: false, source: { primary: null, involved: null, governance: null } },
  }));
}

function quiet(items) {
  return items.map((item) => {
    if (item.status !== "in_review" && item.status !== "blocked") return item;
    return { ...item, status: "in_progress", status_category: "in_progress" };
  });
}

export function scenarioFiles(name) {
  const issues = atlasIssues();
  const files = {
    meta: meta(),
    agents: agents(),
    runtimes: runtimes(),
    changes: atlasChanges(),
    runs: runs(),
  };
  if (name === "atlas-empty") files.issues = { generated_at: NOW, items: stripLayer(issues) };
  else if (name === "atlas-unfilled") files.issues = { generated_at: NOW, items: unfilled(issues) };
  else if (name === "atlas-quiet") {
    files.issues = { generated_at: NOW, items: quiet(issues) };
    files.changes = { ...atlasChanges(), items: [] };
  } else if (name === "atlas-gap") {
    files.issues = { generated_at: NOW, items: issues };
    files.changes = { generated_at: NOW, cursor: null, reset: true, retained_days: 7, has_more: false, items: [] };
  } else files.issues = { generated_at: NOW, items: issues };
  return files;
}
