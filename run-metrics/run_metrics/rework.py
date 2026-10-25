"""Read-only rework inference with explicit / inferred / unknown coverage.

A point rework rate is withheld whenever any candidate issue stays unknown.
No coverage threshold was approved, so any unknown forces a lower bound.
"""

from __future__ import annotations

import re
from collections import defaultdict

from .timeutil import in_window, parse_ts

_ACCEPTANCE_TO = frozenset({"todo", "in_progress", "backlog"})
_ACCEPTANCE_TEXT = re.compile(
    r"(?i)验收不通过|验收退回|验收未通过|退回修改|要求返工|需要返工|请返工|"
    r"acceptance return|changes requested|sent back for rework"
)


def _anchor(run: dict):
    return parse_ts(run.get("anchor_at"))


def _next_run(runs: list[dict], after, start, end) -> dict | None:
    chosen = None
    chosen_at = None
    for run in runs:
        moment = _anchor(run)
        if moment is None or moment <= after or not in_window(moment, start, end):
            continue
        if chosen_at is None or moment < chosen_at:
            chosen = run
            chosen_at = moment
    return chosen


def _previous_run(runs: list[dict], at) -> dict | None:
    chosen = None
    chosen_at = None
    for run in runs:
        moment = _anchor(run)
        if moment is None or moment > at:
            continue
        if chosen_at is None or moment > chosen_at:
            chosen = run
            chosen_at = moment
    return chosen


def _chain(
    issue: dict,
    *,
    trigger_event_id: str | None,
    source_run_id: str | None,
    rework_run_id: str | None,
    reason: str,
    confidence: str,
    evidence_ids: list[str],
    occurred_at: str | None,
    note: str | None = None,
) -> dict:
    return {
        "rework_id": "rw:{issue}:{confidence}:{trigger}:{run}".format(
            issue=issue.get("id"),
            confidence=confidence,
            trigger=trigger_event_id or "none",
            run=rework_run_id or "none",
        ),
        "issue_id": issue.get("id"),
        "issue_identifier": issue.get("identifier"),
        "trigger_event_id": trigger_event_id,
        "source_run_id": source_run_id,
        "rework_run_id": rework_run_id,
        "reason": reason,
        "confidence": confidence,
        "evidence_ids": evidence_ids,
        "occurred_at": occurred_at,
        "note": note,
    }


def _dedup(chains: list[dict]) -> list[dict]:
    """One chain per rework run. explicit outranks inferred; earliest trigger wins."""
    rank = {"explicit": 2, "inferred": 1, "unknown": 0}
    best: dict[str, dict] = {}
    order: list[str] = []
    for chain in chains:
        key = chain.get("rework_run_id") or chain["rework_id"]
        current = best.get(key)
        if current is None:
            best[key] = chain
            order.append(key)
            continue
        if rank[chain["confidence"]] > rank[current["confidence"]]:
            best[key] = chain
        elif rank[chain["confidence"]] == rank[current["confidence"]]:
            if (chain.get("occurred_at") or "") < (current.get("occurred_at") or ""):
                best[key] = chain
    return [best[key] for key in order]


def infer_rework(
    issues: list[dict],
    runs: list[dict],
    status_changes: list[dict],
    comments: list[dict],
    *,
    start,
    end,
    timeline_truncated_ids: set[str] | None = None,
    timeline_missing_ids: set[str] | None = None,
) -> dict:
    truncated = timeline_truncated_ids or set()
    missing = timeline_missing_ids or set()
    runs_by_issue: dict[str, list[dict]] = defaultdict(list)
    changes_by_issue: dict[str, list[dict]] = defaultdict(list)
    comments_by_issue: dict[str, list[dict]] = defaultdict(list)
    for run in runs:
        if run.get("issue_id"):
            runs_by_issue[run["issue_id"]].append(run)
    for change in status_changes:
        if change.get("issue_id"):
            changes_by_issue[change["issue_id"]].append(change)
    for comment in comments:
        if comment.get("issue_id"):
            comments_by_issue[comment["issue_id"]].append(comment)

    chains: list[dict] = []
    confidence_by_issue: dict[str, str] = {}
    for issue in issues:
        issue_id = issue.get("id")
        if not issue_id:
            continue
        issue_runs = runs_by_issue.get(issue_id, [])
        window_runs = [run for run in issue_runs if in_window(_anchor(run), start, end)]
        proven: list[dict] = []
        unknown_notes: list[str] = []

        for run in window_runs:
            parent = run.get("parent_run_id")
            if parent:
                proven.append(_chain(
                    issue,
                    trigger_event_id=parent,
                    source_run_id=parent,
                    rework_run_id=run.get("run_id"),
                    reason="unknown",
                    confidence="explicit",
                    evidence_ids=[item for item in (parent, run.get("run_id")) if item],
                    occurred_at=run.get("anchor_at"),
                    note="平台 parent/previous/retried_from run 关系",
                ))
            elif (run.get("attempt_no") or 0) > 1:
                unknown_notes.append("attempt>1 但没有 parent_run_id，不猜测重试因果")

        for change in changes_by_issue.get(issue_id, []):
            moment = parse_ts(change.get("created_at"))
            if not in_window(moment, start, end):
                continue
            source = change.get("from")
            target = change.get("to")
            if not source or not target:
                if len(window_runs) >= 2:
                    unknown_notes.append("status_changed 缺少 from/to，不能证明阻塞或退回")
                continue
            reason = None
            if target == "blocked":
                reason = "blocked"
            elif source == "in_review" and target in _ACCEPTANCE_TO:
                reason = "acceptance_return"
            if reason is None:
                continue
            rework_run = _next_run(issue_runs, moment, start, end)
            if rework_run is None:
                continue
            previous = _previous_run(issue_runs, moment)
            proven.append(_chain(
                issue,
                trigger_event_id=change.get("id"),
                source_run_id=previous.get("run_id") if previous else None,
                rework_run_id=rework_run.get("run_id"),
                reason=reason,
                confidence="inferred",
                evidence_ids=[
                    item for item in (
                        change.get("id"),
                        previous.get("run_id") if previous else None,
                        rework_run.get("run_id"),
                    ) if item
                ],
                occurred_at=rework_run.get("anchor_at"),
                note="状态时间链；from/to 来自 timeline details",
            ))

        for comment in comments_by_issue.get(issue_id, []):
            moment = parse_ts(comment.get("created_at"))
            if not in_window(moment, start, end):
                continue
            content = comment.get("content") or ""
            if not _ACCEPTANCE_TEXT.search(content):
                continue
            rework_run = _next_run(issue_runs, moment, start, end)
            if rework_run is None:
                continue
            previous = _previous_run(issue_runs, moment)
            proven.append(_chain(
                issue,
                trigger_event_id=comment.get("id"),
                source_run_id=previous.get("run_id") if previous else None,
                rework_run_id=rework_run.get("run_id"),
                reason="acceptance_return",
                confidence="inferred",
                evidence_ids=[
                    item for item in (
                        comment.get("id"),
                        previous.get("run_id") if previous else None,
                        rework_run.get("run_id"),
                    ) if item
                ],
                occurred_at=rework_run.get("anchor_at"),
                note="评论预览含验收退回用语，随后出现新 run",
            ))

        proven = _dedup(proven)
        has_explicit = any(chain["confidence"] == "explicit" for chain in proven)
        has_inferred = any(chain["confidence"] == "inferred" for chain in proven)
        if not proven and len(window_runs) >= 2 and issue_id in truncated:
            unknown_notes.append("timeline 被截断，不能排除窗口外的状态链")
        if not proven and len(window_runs) >= 2 and issue_id in missing:
            unknown_notes.append("timeline 读取失败，不能建立因果链")

        if has_explicit:
            confidence_by_issue[issue_id] = "explicit"
            chains.extend(proven)
        elif has_inferred:
            confidence_by_issue[issue_id] = "inferred"
            chains.extend(proven)
        elif unknown_notes:
            confidence_by_issue[issue_id] = "unknown"
            chains.append(_chain(
                issue,
                trigger_event_id=None,
                source_run_id=None,
                rework_run_id=None,
                reason="unknown",
                confidence="unknown",
                evidence_ids=[],
                occurred_at=None,
                note="；".join(dict.fromkeys(unknown_notes)),
            ))
        else:
            confidence_by_issue[issue_id] = "not_candidate"

    return {"chains": chains, "issue_confidence": confidence_by_issue}
