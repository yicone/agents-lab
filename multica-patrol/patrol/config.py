"""Shared constants and thresholds for the patrol machine layer."""

from __future__ import annotations

import re
from datetime import timedelta

WORKSPACE_ID = "8ebc87ea-27f2-42c4-90f6-24a04e64d3f5"
LEDGER_ISSUE_ID = "01a14aba-2fd1-75d1-a49f-b8323cc9ebbf"  # ACM-229
INBOX_ISSUE_ID = "01a14a4e-0c6b-745f-a168-948eade9f5f4"  # ACM-216
DISPATCHER_AGENT_ID = "9bfe8713-ce7e-499b-830e-423223a64480"  # 副调度
PATROL_AUTOPILOT_ID = "2d84a985-978e-43ca-9b87-bd7b71fa2e0c"
LEDGER_IDENTIFIER = "ACM-229"
INBOX_IDENTIFIER = "ACM-216"

SGT_OFFSET = timedelta(hours=8)

SCAN_STATUSES = ("in_progress", "in_review", "blocked", "todo", "backlog")
LIGHT_STATUSES = ("todo", "backlog")
ACTIVE_RUN_STATUSES = ("queued", "dispatched", "running", "waiting_local_directory")
TERMINAL_RUN_STATUSES = ("completed", "failed", "cancelled")

PROGRESS_STALE_S = 2 * 3600
REVIEW_STALE_S = 24 * 3600
BLOCKED_STALE_S = 48 * 3600
APPROVAL_WAIT_S = 3600
NO_ASSIGNEE_GRACE_S = 30 * 60
FAILED_RUN_GRACE_S = 10 * 60
FAILED_RUN_MIN_STREAK = 2
DEDUPE_WINDOW_S = 24 * 3600
ESCALATE_AFTER_S = 48 * 3600
SELF_ESCALATE_MIN_STREAK = 2
SELF_ESCALATE_REPEAT_S = 24 * 3600
INFLIGHT_RESEND_AGE_S = 10 * 60
DUP_LOOKBACK_S = 26 * 3600
DAILY_DUP_LOOKBACK_S = 24 * 3600
COMMENTS_WINDOW_S = 14 * 86400
DEEP_CHECK_BUDGET_S = 480
DEEP_CHECK_WORKERS = 4
PAGE_LIMIT = 100
BODY_MAX_CHARS = 20000
FULL_SCAN_MAX_AGE_S = 24 * 3600
FULL_SCAN_SLOT_HOUR = 23
LIGHT_RECHECK_S = 3600

# Shelving is a LABEL, not an identifier list: labelled issues are dropped at
# fetch time, cost no CLI calls, and appear nowhere (not even as counts).
SHELVED_LABEL_ID = "6ddf2d23-8de1-4be1-975d-b112c9c91a44"
SHELVED_LABEL_NAME = "搁置"
UNRESOLVED_RULE = {"ACM-215"}
PERSISTENT_IDS = frozenset({LEDGER_ISSUE_ID, INBOX_ISSUE_ID})
APPROVED_LABELS = {"可开工"}
APPROVAL_WAIT_PATTERNS = [
    re.compile(r"^\s*待用户批准"),
    re.compile(r"^\s*待(用户)?(批准|决定)"),
    re.compile(r"^\s*待决定"),
]
SELF_COMMENT_PREFIXES = ("[patrol-batch:", "[dispatch-batch:")
ESCALATION_PREFIX = "[patrol-escalate:"
DAILY_PREFIX = "[patrol-daily:"
ERROR_RUN_WINDOW_S = 6 * 3600
RETRY_NO_RUN_ESCALATE_S = 2 * 3600
MAX_RETRIES_PER_RUN = 3
RETRY_PREFIX = "[patrol-retry:"
UNWOKEN_GRACE_S = 15 * 60
UNWOKEN_DEDUPE_S = 24 * 3600
MAX_UNWOKEN_PER_RUN = 3
UNWOKEN_PREFIX = "[patrol-unwoken:"
NOWAKE_PREFIX = "[patrol-nowake:"
# Rule 10 dispatch-class heuristic: dispatcher comments whose first line is
# not an explicit handoff marker still count when the body uses dispatch
# wording. Plain status notes must not match.
DISPATCH_CLASS_RE = re.compile(
    r"派活|开工|批准后|批准执行|返工|复验|重验|重新验收|补改|请现在|按.*(?:执行|开工)")
UUID_MENTION_RE = re.compile(
    r"mention://(agent|member)/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
FIRST_LINE_NEED = "需要你做："
FIRST_LINE_NEXT = "不需要你做，下一步由"
MENTION_RE = re.compile(r"mention://(agent|member)/")

AGENT_MENTION_RE = re.compile(
    r"mention://agent/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})")
NEXT_TARGET_RE = re.compile(r"^不需要你做，下一步由\s*(?P<name>[^\s：:]+)\s*做")
