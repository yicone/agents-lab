"""Daily pack (read-only data for the LLM analysis) and daily-post."""

from __future__ import annotations

import json
from datetime import timedelta

from . import config
from .cli import CliError
from .facts import iso, parse_ts


def _root_id(issue_id: str, issue_index: dict) -> str:
    seen = set()
    current = issue_id
    while current and current not in seen:
        seen.add(current)
        parent = (issue_index.get(current) or {}).get("parent_issue_id")
        if not parent:
            return current
        current = parent
    return issue_id


def build_pack(state: dict, agent_names: dict, coverage: dict | None, now) -> dict:
    """Deterministic data pack. No decisions, no writes."""
    issue_index = state.get("issue_index") or {}
    alerted = state.get("alerted") or {}
    items = []
    for issue_id, entry in (state.get("cache") or {}).items():
        issue = entry.get("issue") or {}
        if issue_id in config.PERSISTENT_IDS:
            continue
        if issue.get("identifier") in config.SHELVED | config.UNRESOLVED_RULE:
            continue
        for finding in entry.get("findings") or []:
            key = f"{issue_id}|{finding['reason']}|{finding.get('anchor')}"
            rec = alerted.get(key) or {}
            first = parse_ts(rec.get("first_at")) or parse_ts(finding.get("anchor"))
            items.append({
                "issue_id": issue_id,
                "identifier": issue.get("identifier"),
                "status": issue.get("status"),
                "assignee_id": issue.get("assignee_id"),
                "assignee_name": agent_names.get(issue.get("assignee_id")),
                "reason": finding["reason"],
                "anchor": finding.get("anchor"),
                "first_at": rec.get("first_at"),
                "age_s": (now - first).total_seconds() if first else None,
                "escalated": bool(rec.get("escalated_at")),
                "parent_issue_id": issue.get("parent_issue_id"),
                "suggested_owner": agent_names.get(issue.get("assignee_id")),
            })
    groups = {}
    for item in items:
        root = _root_id(item["issue_id"], issue_index)
        groups.setdefault(root, []).append(item)
    big_matters = []
    for root, members in sorted(groups.items()):
        root_issue = issue_index.get(root) or {}
        big_matters.append({
            "root_issue_id": root,
            "root_identifier": root_issue.get("identifier"),
            "members": members,
            "statuses": sorted({m["status"] for m in members if m.get("status")}),
            "assignee_names": sorted({m["assignee_name"] for m in members if m.get("assignee_name")}),
            "reasons": sorted({m["reason"] for m in members}),
        })
    escalated_48 = [i for i in items if i["escalated"]]
    cutoff = now - timedelta(hours=24)
    self_failures = [
        f for f in (state.get("failures") or [])
        if (parse_ts(f.get("at")) or cutoff) >= cutoff
    ]
    unresolved = [
        {"issue_id": e.get("issue", {}).get("id"), "identifier": e.get("issue", {}).get("identifier"),
         "status": e.get("issue", {}).get("status"),
         "note": "需要用户裁定归类为 shelved / persistent / normal"}
        for e in (state.get("cache") or {}).values()
        if (e.get("issue") or {}).get("identifier") in config.UNRESOLVED_RULE
    ]
    shelved = sorted({
        (e.get("issue") or {}).get("identifier")
        for e in (state.get("cache") or {}).values()
        if (e.get("issue") or {}).get("identifier") in config.SHELVED
    } - {None})
    return {
        "generated_at": iso(now),
        "big_matters": big_matters,
        "findings_count": len(items),
        "escalated_48h": escalated_48,
        "self_failures_24h": self_failures,
        "unresolved": unresolved,
        "shelved_count": len(shelved),
        "last_scan_coverage": coverage,
    }


def render_md(pack: dict) -> str:
    lines = ["# Patrol daily pack", f"generated_at: {pack['generated_at']}", ""]
    cov = pack.get("last_scan_coverage") or {}
    lines.append(f"last scan: discovered={cov.get('issues_discovered')} "
                 f"deep={cov.get('deep_checked')} cache={cov.get('from_cache')} "
                 f"errors={len(cov.get('errors') or [])} complete={cov.get('complete')}")
    lines.append("")
    lines.append(f"## Big matters ({len(pack['big_matters'])})")
    for group in pack["big_matters"]:
        lines.append(f"### {group['root_identifier'] or group['root_issue_id']}")
        lines.append(f"- statuses: {', '.join(group['statuses'])}")
        lines.append(f"- assignees: {', '.join(group['assignee_names']) or '(none)'}")
        lines.append(f"- reasons: {', '.join(group['reasons'])}")
        for member in group["members"]:
            age_h = f"{member['age_s'] / 3600:.1f}h" if member.get("age_s") else "?"
            lines.append(f"  - {member['identifier']} `{member['reason']}` age={age_h} "
                         f"owner={member.get('suggested_owner') or 'none'}")
    if pack["escalated_48h"]:
        lines.append("")
        lines.append(f"## Escalated >48h ({len(pack['escalated_48h'])})")
        for item in pack["escalated_48h"]:
            lines.append(f"- {item['identifier']} `{item['reason']}`")
    if pack["self_failures_24h"]:
        lines.append("")
        lines.append(f"## Self failures (24h): {len(pack['self_failures_24h'])}")
        for fail in pack["self_failures_24h"]:
            lines.append(f"- slot {fail.get('slot')} [{fail.get('kind')}] {fail.get('message', '')}")
    if pack["unresolved"]:
        lines.append("")
        lines.append("## 待用户口径")
        for item in pack["unresolved"]:
            lines.append(f"- {item['identifier']} `{item['status']}` {item['note']}")
    lines.append("")
    lines.append(f"shelved: {pack['shelved_count']} (count only)")
    return "\n".join(lines) + "\n"


def validate_daily_body(body: str) -> str | None:
    if not body or not body.strip():
        return "empty body"
    if config.MENTION_RE.search(body):
        return "body contains agent/member mention"
    if len(body) > config.BODY_MAX_CHARS:
        return "body exceeds 20000 chars"
    return None
