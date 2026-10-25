"""Compare a live runtime list with the S1 inventory claim."""

from __future__ import annotations

S1_COMMENT_ID = "01a173e7-4d1b-7c12-9e40-a94ee2c675a9"
STEWARD_ATLAS_COMMENT_ID = "01a173ef-44df-738b-b354-b74523090400"
S1_RUNTIME_COUNT = 12
S1_ALL_ONLINE = True


def _is_gemini(runtime: dict) -> bool:
    provider = str(runtime.get("provider") or "").lower()
    name = str(runtime.get("name") or "").lower()
    return "gemini" in provider or "gemini" in name


def reconcile_runtimes(
    runtimes: list[dict],
    usage_probes: list[dict],
    *,
    checked_at: str,
) -> dict:
    observed = []
    for runtime in runtimes:
        observed.append({
            "runtime_id": runtime.get("id"),
            "name": runtime.get("name"),
            "provider": runtime.get("provider"),
            "status": runtime.get("status"),
            "last_seen_at": runtime.get("last_seen_at"),
        })
    online = [row for row in observed if row["status"] == "online"]
    offline = [row for row in observed if row["status"] != "online"]
    gemini = [row for row in observed if _is_gemini(row)]
    discrepancies = []
    unverified = [
        f"未打开 Atlas UI，不能复核评论 {STEWARD_ATLAS_COMMENT_ID} 里的 gemini 0/1 显示。"
    ]
    if len(observed) != S1_RUNTIME_COUNT:
        discrepancies.append(
            f"S1（评论 {S1_COMMENT_ID}）记录 12 个本地 runtime 且均为 online；"
            f"本次 `multica runtime list` 于 {checked_at} 返回 {len(observed)} 个，"
            f"其中 online {len(online)}、非 online {len(offline)}。"
            "S1 未列出 runtime id，因此不能点名少掉的是哪几个。"
        )
    elif offline:
        names = ", ".join(f"{row['name']}={row['status']}" for row in offline)
        discrepancies.append(f"S1 记录全部 online；本次非 online：{names}。")
    if not gemini:
        discrepancies.append(
            "本次 CLI 列表没有 provider 或 name 含 gemini 的 runtime。"
            "这和 S1 的 12 个 online runtime 不能逐个对齐。"
        )
    else:
        gemini_offline = [row for row in gemini if row["status"] != "online"]
        if gemini_offline:
            discrepancies.append(
                "CLI 中的 gemini runtime 并非全部 online："
                + ", ".join(f"{row['name']}={row['status']}" for row in gemini_offline)
                + "。这与 S1「全部 online」不一致。"
            )
    quota_fields_found = any(probe.get("quota_remaining_present") for probe in usage_probes)
    return {
        "checked_at": checked_at,
        "source": "multica runtime list",
        "s1_claim": {
            "runtime_count": S1_RUNTIME_COUNT,
            "all_online": S1_ALL_ONLINE,
            "source_comment_id": S1_COMMENT_ID,
        },
        "observed_count": len(observed),
        "online_count": len(online),
        "offline_count": len(offline),
        "runtimes": observed,
        "gemini_runtimes": gemini,
        "atlas_gemini_display": {
            "claimed_by": "Steward",
            "source_comment_id": STEWARD_ATLAS_COMMENT_ID,
            "claim": "gemini 0/1 online",
            "rechecked_in_atlas_ui": False,
        },
        "discrepancies": discrepancies,
        "unverified": unverified,
        "usage_probes": usage_probes,
        "quota_remaining_present": quota_fields_found,
    }


def scheduling_view(
    runtimes: list[dict],
    usage_probes: list[dict],
    historical_quota_agent_ids: list[str],
    *,
    checked_at: str,
) -> dict:
    probes = {probe.get("runtime_id"): probe for probe in usage_probes}
    signals = []
    for runtime in runtimes:
        probe = probes.get(runtime.get("id")) or {}
        signals.append({
            "subject_type": "runtime",
            "subject_id": runtime.get("id"),
            "provider": runtime.get("provider"),
            "name": runtime.get("name"),
            "connection_status": runtime.get("status"),
            "health": "unknown",
            "quota_remaining": None,
            "recovery_at": None,
            "capacity_used": None,
            "capacity_limit": None,
            "hard_disable": False,
            "checked_at": checked_at,
            "ttl_seconds": None,
            "source": "multica runtime list 只有连接状态；runtime usage 未提供剩余额度",
            "usage_probe": {
                "days": probe.get("days"),
                "record_count": probe.get("record_count"),
                "quota_remaining_present": probe.get("quota_remaining_present", False),
            },
        })
    return {
        "policy": "current_quota_health_only",
        "unknown_hard_disable": False,
        "historical_quota_excludes_agent": False,
        "excluded_agent_ids": [],
        "historical_quota_agent_ids": historical_quota_agent_ids,
        "historical_quota_note": "这些 agent 在默认窗口有 quota 分类，仅作历史记录，不产生排除名单。",
        "signals": signals,
    }
