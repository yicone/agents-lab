import assert from "node:assert/strict";
import test from "node:test";
import {
  BAD_AFTER_MS,
  DAY_MS,
  WARN_AFTER_MS,
  changesSince,
  compareWait,
  decideQueue,
  filterDecide,
  readStatusChanges,
  formatSince,
  formatWait,
  latestStatusEntry,
  waitFor,
  waitLevel,
} from "./decide.js";

const NOW = Date.parse("2026-10-14T09:00:00Z");
const ACM = { id: "ws-acm", slug: "acm-lab", name: "ACM Lab" };
const OTHER = { id: "ws-other", slug: "beta", name: "Beta 辅导" };

function issue(partial) {
  return {
    workspace_id: ACM.id,
    id: partial.identifier,
    status: "in_review",
    last_activity_at: new Date(NOW - DAY_MS).toISOString().replace(".000Z", "Z"),
    ...partial,
  };
}

function entry(id, daysAgo, status, previous = "in_progress") {
  return {
    kind: "issue_status",
    at: new Date(NOW - daysAgo * DAY_MS).toISOString().replace(".000Z", "Z"),
    workspace_id: ACM.id,
    id,
    status,
    previous_status: previous,
  };
}

test("color thresholds are strict: 7 days stays plain, past 14 days turns red", () => {
  assert.equal(waitLevel(WARN_AFTER_MS), "ok");
  assert.equal(waitLevel(WARN_AFTER_MS + 1), "warn");
  assert.equal(waitLevel(BAD_AFTER_MS), "warn");
  assert.equal(waitLevel(BAD_AFTER_MS + 1), "bad");
  assert.equal(waitLevel(null), "none");
  assert.equal(waitLevel(0), "ok");
});

test("wait uses the latest transition into the current status", () => {
  const it = issue({ identifier: "ACM-1", status: "in_review", last_activity_at: new Date(NOW - DAY_MS).toISOString() });
  const changes = [
    entry("ACM-1", 10, "in_review"),
    entry("ACM-1", 2, "in_review", "blocked"),
    entry("ACM-1", 1, "blocked", "in_review"),
    entry("ACM-OTHER", 20, "in_review"),
    { ...entry("ACM-1", 0.5, "in_review"), previous_status: "in_review" },
  ];
  const wait = waitFor(it, changes, NOW);
  assert.equal(wait.approximate, false);
  assert.equal(wait.ms, 2 * DAY_MS);
  assert.equal(latestStatusEntry(it, changes).iso, changes[1].at);
});

test("missing transition falls back to last activity and marks ≥", () => {
  const it = issue({
    identifier: "ACM-2",
    status: "blocked",
    last_activity_at: "2026-09-28T09:00:00Z",
    updated_at: "2026-10-01T09:00:00Z",
  });
  const wait = waitFor(it, [entry("ACM-2", 1, "in_progress")], NOW);
  assert.equal(wait.approximate, true);
  assert.equal(wait.iso, "2026-09-28T09:00:00Z");
  assert.equal(wait.ms, 16 * DAY_MS);
  assert.equal(wait.level, "bad");
  assert.equal(formatWait(wait.ms, wait.approximate), "≥16 天");
});

test("an incomplete change log ignores transitions that did arrive", () => {
  const it = issue({ identifier: "ACM-3", last_activity_at: "2026-10-13T09:00:00Z" });
  const wait = waitFor(it, [entry("ACM-3", 12, "in_review")], NOW, true);
  assert.equal(wait.approximate, true);
  assert.equal(wait.ms, DAY_MS);
  assert.equal(formatWait(wait.ms, true), "≥1 天");
});

test("queue keeps ACM Lab in_review and blocked, longest wait first", () => {
  const issues = [
    issue({ identifier: "ACM-B", status: "blocked", last_activity_at: "2026-10-04T09:00:00Z" }),
    issue({ identifier: "ACM-A", status: "in_review", last_activity_at: "2026-10-01T09:00:00Z" }),
    issue({ identifier: "ACM-A2", status: "in_review", last_activity_at: "2026-10-01T09:00:00Z" }),
    issue({ identifier: "ACM-NEW", status: "in_review" }),
    issue({ identifier: "ACM-DONE", status: "done", status_category: "in_review" }),
    issue({ identifier: "ACM-WORK", status: "in_progress", status_category: "in_review" }),
    issue({ identifier: "BET-1", workspace_id: OTHER.id, status: "in_review" }),
    issue({ identifier: "BET-2", workspace_id: OTHER.id, status: "blocked" }),
    issue({ identifier: "BET-3", workspace_id: OTHER.id, status: "todo" }),
  ];
  const changes = [entry("ACM-NEW", 1, "in_review")];
  const model = decideQueue({
    issues,
    workspaces: [OTHER, { id: ACM.id, slug: "other", name: "ACM Lab" }],
    changes,
    nowMs: NOW,
  });
  assert.equal(model.acm.id, ACM.id);
  assert.deepEqual(
    model.rows.map((row) => row.issue.identifier),
    ["ACM-A", "ACM-A2", "ACM-B", "ACM-NEW"],
  );
  assert.equal(model.rows[0].wait.ms, model.rows[1].wait.ms);
  assert.equal(model.outside, 2);
  assert.equal(model.summary.total, 4);
  assert.equal(model.summary.review, 3);
  assert.equal(model.summary.blocked, 1);
  assert.equal(model.summary.over7, 3);
  assert.equal(model.summary.total, model.rows.length);
  assert.ok(compareWait(model.rows[0], model.rows[2]) < 0);
});

test("slug acm-lab wins when the display name differs", () => {
  const named = { id: "ws-name", slug: "lab", name: "ACM Lab" };
  const slug = { id: "ws-slug", slug: "acm-lab", name: "实验室" };
  const model = decideQueue({
    issues: [issue({ identifier: "ACM-1", workspace_id: slug.id })],
    workspaces: [named, slug],
    changes: [],
    nowMs: NOW,
  });
  assert.equal(model.acm.id, slug.id);
  assert.equal(model.summary.total, 1);
});

test("no ACM Lab workspace leaves the queue empty and counts the others", () => {
  const model = decideQueue({
    issues: [issue({ identifier: "BET-1", workspace_id: OTHER.id, status: "IN_REVIEW" })],
    workspaces: [OTHER],
    changes: [],
    nowMs: NOW,
  });
  assert.equal(model.acm, null);
  assert.equal(model.summary.total, 0);
  assert.equal(model.outside, 1);
});

test("type filter and search narrow the list without changing the badge total", () => {
  const model = decideQueue({
    issues: [
      issue({ identifier: "ACM-1", title: "桥接", status: "in_review" }),
      issue({ identifier: "ACM-2", title: "额度", status: "blocked" }),
    ],
    workspaces: [ACM],
    changes: [],
    nowMs: NOW,
  });
  const rows = model.rows.map((row) => ({ ...row, assignee: "Builder" }));
  assert.equal(filterDecide(rows, { type: "blocked" }).length, 1);
  assert.equal(filterDecide(rows, { query: "桥接" })[0].issue.identifier, "ACM-1");
  assert.equal(filterDecide(rows, { query: "builder" }).length, 2);
  assert.equal(model.summary.total, 2);
});

test("since label uses the Shanghai calendar day", () => {
  assert.equal(formatSince("2026-10-14T06:00:00Z", NOW), "今天 14:00 起");
  assert.equal(formatSince("2026-10-12T09:00:00Z", NOW), "10-12 起");
  assert.equal(formatWait(3 * 3600 * 1000, false), "3 小时");
  assert.equal(formatWait(15 * DAY_MS, false), "15 天");
});

test("changes since stays inside the seven-day horizon", () => {
  const since = Date.parse(changesSince(NOW));
  assert.ok(since > NOW - 7 * DAY_MS);
  assert.ok(since < NOW - 6 * DAY_MS);
  assert.match(changesSince(NOW), /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$/);
});

test("status changes are requested with kind=status and runs do not fill the page", async () => {
  const calls = [];
  const status = entry("ACM-1", 2, "in_review");
  const payload = await readStatusChanges(async (path) => {
    calls.push(path);
    const since = new URL(path, "http://board.local").searchParams.get("since");
    assert.equal(new URL(path, "http://board.local").searchParams.get("kind"), "status");
    if (since === changesSince(NOW)) {
      return {
        items: [{ kind: "run", at: status.at }, status],
        cursor: "v1:2",
        has_more: true,
        reset: false,
      };
    }
    return { items: [entry("ACM-2", 1, "blocked")], cursor: "v1:3", has_more: false, reset: false };
  }, NOW);
  assert.equal(calls.length, 2);
  assert.equal(payload.incomplete, false);
  assert.deepEqual(payload.items.map((item) => item.id), ["ACM-1", "ACM-2"]);
});
