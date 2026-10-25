"""Versioned failure-class rules.

First match wins. ``provider_unstable`` is intentionally absent: single 5xx,
connection, or generic provider errors are candidate signals only. v0.2 adds
geo/access, config, and runtime-recovery rules, and extends reason vocabularies.
Candidate counts stay inclusive of already classified runs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from . import RULE_VERSION
from .redact import redact

# Classes this prototype is allowed to emit. provider_unstable is not one of them.
FAILURE_CLASSES = (
    "quota",
    "capacity",
    "access_denied",
    "config_failed",
    "runtime_recovery",
    "timeout",
    "process_failed",
    "cancelled",
    "unknown",
)

_QUOTA_REASONS = frozenset({
    "agent_error.provider_quota_limit",
    "provider_quota_limit",
    "quota",
    "usage_limit",
})
_CAPACITY_REASONS = frozenset({
    "provider_capacity",
    "rate_limit",
    "agent_error.provider_capacity_or_rate_limit",
    "agent_error.model_not_found_or_unavailable",
})
_ACCESS_REASONS = frozenset({
    "access_denied",
    "unauthorized",
    "forbidden",
    "authentication_failed",
    "agent_error.access_denied",
    "agent_error.provider_auth_or_access",
})
_CONFIG_REASONS = frozenset({"api_invalid_request"})
_RUNTIME_RECOVERY_REASONS = frozenset({"runtime_recovery"})
_TIMEOUT_REASONS = frozenset({"timeout", "timed_out", "deadline_exceeded"})
_PROCESS_REASONS = frozenset({
    "process_failed",
    "process_exited",
    "agent_error.process_failed",
    "agent_error.process_failure",
})
_CANCEL_REASONS = frozenset({"cancelled", "canceled"})

_QUOTA_TEXT = re.compile(
    r"(?i)(\busage limit\b|provider[_ ]quota|insufficient[_ ]quota|\bquota\b|"
    r"insufficient[_ ]?(?:credit|balance|funds)|credit balance|exceeded your current|"
    r"\bbilling\b|额度|配额|余额不足)"
)
_CAPACITY_TEXT = re.compile(
    r"(?i)(\bat\s+capacity\b|\b429\b|rate[ _-]?limit|too many requests|"
    r"resource_exhausted|\boverloaded\b|concurrency limit|queue (?:is )?full|"
    r"容量不足|并发(?:已满|限制))"
)
_ACCESS_TEXT = re.compile(
    r"(?i)(\b(?:401|403)\b|unauthori[sz]ed|unauthenticated|\bforbidden\b|"
    r"authentication|permission denied|access denied|invalid (?:api )?key|"
    r"not logged in|拒绝访问|未授权|无权限)"
)
_GEO_TEXT = re.compile(
    r"(?i)(user location is not supported|location is not supported for the api)"
)
_CONFIG_TEXT = re.compile(
    r"(?i)(from --model flag is not available|"
    r"model [^\n]{0,80}? is not available|"
    r"model [^\n]{0,80}? is not supported|"
    r"not supported when using)"
)
_RUNTIME_RECOVERY_TEXT = re.compile(
    r"(?i)daemon restarted while task was in flight"
)
_TIMEOUT_TEXT = re.compile(r"(?i)(timed?[ _-]?out|deadline exceeded|\btimeout\b|超时)")
_PROCESS_TEXT = re.compile(
    r"(?i)(\bCLI not found\b|command not found|executable file not found|"
    r"process exited|exit(?:ed)? (?:code|status)|initiali[sz]e failed|"
    r"\bcrash(?:ed)?\b|\bpanic\b|\bkilled\b|进程退出|崩溃)"
)
_CANCEL_TEXT = re.compile(r"(?i)(\bcancell?ed\b|\bcancellation\b|已取消|被取消|任务取消)")

_CANDIDATE_SPECS = (
    (
        "PC-001-HTTP-5XX",
        "HTTP 5xx 或网关错误文本。不是 failure_class。",
        re.compile(
            r"(?i)(\b50[0234]\b|internal server error|bad gateway|"
            r"service unavailable|gateway time-?out)"
        ),
    ),
    (
        "PC-002-CONNECTION",
        "连接中断、DNS、TLS、不可达、broken pipe 或 EPIPE。不是 failure_class。已分类的 run 仍计入候选，不因阈值方案而剔除。",
        re.compile(
            r"(?i)(connection (?:refused|reset|closed)|econnreset|econnrefused|"
            r"\beconn\b|tls handshake|network is unreachable|\bunreachable\b|"
            r"\bdns\b|connection error|broken pipe|\bepipe\b)"
        ),
    ),
    (
        "PC-003-GENERIC-PROVIDER",
        "供应商通用处理失败文本（不含 quota/capacity 词）。不是 failure_class。",
        re.compile(r"(?i)an error occurred while processing your request"),
    ),
    (
        "PC-004-PROVIDER-SERVER-REASON",
        "文本或 failure_reason 含 provider_server_error。不是 failure_class，也不能升格为 provider_unstable。",
        re.compile(r"(?i)provider_server_error"),
    ),
)


@dataclass(frozen=True)
class Rule:
    id: str
    failure_class: str
    condition: str
    recoverable: bool | str
    kind: str

    def __post_init__(self) -> None:
        if self.failure_class not in FAILURE_CLASSES:
            raise ValueError(f"rule {self.id} emits forbidden class {self.failure_class}")


RULES: tuple[Rule, ...] = (
    Rule(
        "FR-001-CANCELLED-STATUS",
        "cancelled",
        "raw status 为 cancelled 或 canceled（平台终态，优先于错误文本）",
        False,
        "cancelled_status",
    ),
    Rule(
        "FR-002-CANCELLED-REASON",
        "cancelled",
        "failure_reason 精确等于 cancelled 或 canceled",
        False,
        "cancelled_reason",
    ),
    Rule(
        "FR-003-QUOTA-REASON",
        "quota",
        "failure_reason 精确等于 agent_error.provider_quota_limit、provider_quota_limit、quota、usage_limit",
        True,
        "quota_reason",
    ),
    Rule(
        "FR-004-QUOTA-TEXT",
        "quota",
        "error 或 failure_reason 匹配 usage limit / quota / provider_quota / insufficient credit / billing / 额度 / 配额 / 余额不足",
        True,
        "quota_text",
    ),
    Rule(
        "FR-005-CAPACITY-REASON",
        "capacity",
        "failure_reason 精确等于 provider_capacity、rate_limit、agent_error.provider_capacity_or_rate_limit、agent_error.model_not_found_or_unavailable。后者在 S4 窗口里的文本都是 at capacity，命中后不再看配置类文本",
        "unknown",
        "capacity_reason",
    ),
    Rule(
        "FR-006-CAPACITY-TEXT",
        "capacity",
        "error 或 failure_reason 匹配 at capacity / 429 / rate limit / overloaded / resource_exhausted / 容量不足 / 并发限制",
        "unknown",
        "capacity_text",
    ),
    Rule(
        "FR-007-ACCESS-REASON",
        "access_denied",
        "failure_reason 精确等于 access_denied、unauthorized、forbidden、authentication_failed、agent_error.access_denied、agent_error.provider_auth_or_access",
        False,
        "access_reason",
    ),
    Rule(
        "FR-008-ACCESS-TEXT",
        "access_denied",
        "error 或 failure_reason 匹配 401/403、unauthorized、forbidden、permission denied、access denied、拒绝访问、未授权、无权限",
        False,
        "access_text",
    ),
    Rule(
        "FR-012-GEO-ACCESS",
        "access_denied",
        "error 或 failure_reason 匹配 user location is not supported / location is not supported for the API。归入 access_denied，不单开 geo 类",
        False,
        "geo_text",
    ),
    Rule(
        "FR-009-TIMEOUT",
        "timeout",
        "failure_reason 精确等于 timeout、timed_out、deadline_exceeded，或文本匹配 timeout / timed out / deadline exceeded / 超时",
        "unknown",
        "timeout",
    ),
    Rule(
        "FR-013-CONFIG-REASON",
        "config_failed",
        "failure_reason 精确等于 api_invalid_request。确定性配置失败，类为 config_failed",
        False,
        "config_reason",
    ),
    Rule(
        "FR-014-CONFIG-TEXT",
        "config_failed",
        "error 或 failure_reason 匹配 from --model flag is not available、model … is not available、model … is not supported、not supported when using。先于进程规则，避免 exit status 把模型不可用记成 process_failed",
        False,
        "config_text",
    ),
    Rule(
        "FR-015-RUNTIME-RECOVERY",
        "runtime_recovery",
        "failure_reason 精确等于 runtime_recovery，或文本匹配 daemon restarted while task was in flight。单独成类，不并入 process_failed",
        "unknown",
        "runtime_recovery",
    ),
    Rule(
        "FR-010-PROCESS",
        "process_failed",
        "failure_reason 精确等于 process_failed、process_exited、agent_error.process_failed、agent_error.process_failure，或文本匹配 process exited / exit code / crash / panic / CLI not found / 进程退出 / 崩溃",
        "unknown",
        "process",
    ),
    Rule(
        "FR-011-CANCELLED-TEXT",
        "cancelled",
        "文本匹配 cancelled / cancellation / 已取消 / 被取消 / 任务取消",
        False,
        "cancelled_text",
    ),
    Rule(
        "FR-090-UNMATCHED",
        "unknown",
        "失败或 blocked 的 run 未命中以上规则。禁止猜测，也不升格为 provider_unstable",
        "unknown",
        "fallback",
    ),
)

_RECOVERY_AT = re.compile(
    r"(?i)(?:resets?(?: at)?|refresh(?:es)?(?: at)?|retry after|available at)"
    r"\s*[:=]?\s*(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)"
)

CANDIDATE_SIGNALS = tuple(
    {"id": sid, "condition": condition, "produces_failure_class": False}
    for sid, condition, _pattern in _CANDIDATE_SPECS
)


def _reason(value: str | None) -> str:
    return value.strip() if isinstance(value, str) else ""


def _texts(error: str | None, failure_reason: str | None) -> list[tuple[str, str]]:
    rows = []
    if isinstance(error, str) and error.strip():
        rows.append(("error", error))
    if isinstance(failure_reason, str) and failure_reason.strip():
        rows.append(("failure_reason", failure_reason))
    return rows


def _excerpt(text: str, pattern: re.Pattern[str] | None) -> str:
    if pattern is not None:
        matched = pattern.search(text)
        if matched:
            start = max(0, matched.start() - 40)
            end = min(len(text), matched.end() + 40)
            return redact(text[start:end], limit=180) or ""
    return redact(text, limit=180) or ""


def candidate_ids(error: str | None, failure_reason: str | None) -> list[str]:
    blob = "\n".join(text for _field, text in _texts(error, failure_reason))
    found = []
    for sid, _condition, pattern in _CANDIDATE_SPECS:
        if blob and pattern.search(blob):
            found.append(sid)
    return found


def _evidence(rule: Rule, field: str, text: str, pattern: re.Pattern[str] | None) -> dict:
    return {
        "rule_id": rule.id,
        "rule_version": RULE_VERSION,
        "source_field": field,
        "condition": rule.condition,
        "excerpt": _excerpt(text, pattern),
    }


def _hit(rule: Rule, field: str, text: str, pattern: re.Pattern[str] | None = None) -> dict:
    return {
        "failure_class": rule.failure_class,
        "failure_rule_id": rule.id,
        "failure_recoverable": rule.recoverable,
        "failure_evidence": _evidence(rule, field, text, pattern),
    }


def classify_failure(
    mapped_status: str,
    raw_status: str | None,
    error: str | None,
    failure_reason: str | None,
) -> dict | None:
    """Return a classification for failed, blocked, or cancelled runs.

    Other statuses are not failures. Candidate signals are computed separately
    so a success or an unknown failure can still carry them.
    """
    if mapped_status not in {"failed", "blocked", "cancelled"}:
        return None
    reason = _reason(failure_reason)
    texts = _texts(error, failure_reason)
    for rule in RULES:
        if rule.kind == "cancelled_status" and mapped_status == "cancelled":
            return _hit(rule, "status", raw_status or mapped_status)
        if rule.kind == "cancelled_reason" and reason in _CANCEL_REASONS:
            return _hit(rule, "failure_reason", reason)
        if rule.kind == "quota_reason" and reason in _QUOTA_REASONS:
            return _hit(rule, "failure_reason", reason)
        if rule.kind == "quota_text":
            for field, text in texts:
                if _QUOTA_TEXT.search(text):
                    return _hit(rule, field, text, _QUOTA_TEXT)
        if rule.kind == "capacity_reason" and reason in _CAPACITY_REASONS:
            return _hit(rule, "failure_reason", reason)
        if rule.kind == "capacity_text":
            for field, text in texts:
                if _CAPACITY_TEXT.search(text):
                    return _hit(rule, field, text, _CAPACITY_TEXT)
        if rule.kind == "access_reason" and reason in _ACCESS_REASONS:
            return _hit(rule, "failure_reason", reason)
        if rule.kind == "access_text":
            for field, text in texts:
                if _ACCESS_TEXT.search(text):
                    return _hit(rule, field, text, _ACCESS_TEXT)
        if rule.kind == "geo_text":
            for field, text in texts:
                if _GEO_TEXT.search(text):
                    return _hit(rule, field, text, _GEO_TEXT)
        if rule.kind == "timeout":
            if reason in _TIMEOUT_REASONS:
                return _hit(rule, "failure_reason", reason)
            for field, text in texts:
                if _TIMEOUT_TEXT.search(text):
                    return _hit(rule, field, text, _TIMEOUT_TEXT)
        if rule.kind == "config_reason" and reason in _CONFIG_REASONS:
            return _hit(rule, "failure_reason", reason)
        if rule.kind == "config_text":
            for field, text in texts:
                if _CONFIG_TEXT.search(text):
                    return _hit(rule, field, text, _CONFIG_TEXT)
        if rule.kind == "runtime_recovery":
            for field, text in texts:
                if _RUNTIME_RECOVERY_TEXT.search(text):
                    return _hit(rule, field, text, _RUNTIME_RECOVERY_TEXT)
            if reason in _RUNTIME_RECOVERY_REASONS:
                return _hit(rule, "failure_reason", reason)
        if rule.kind == "process":
            for field, text in texts:
                if _PROCESS_TEXT.search(text):
                    return _hit(rule, field, text, _PROCESS_TEXT)
            if reason in _PROCESS_REASONS:
                return _hit(rule, "failure_reason", reason)
        if rule.kind == "cancelled_text":
            for field, text in texts:
                if _CANCEL_TEXT.search(text):
                    return _hit(rule, field, text, _CANCEL_TEXT)
        if rule.kind == "fallback" and mapped_status in {"failed", "blocked"}:
            blob = reason or (texts[0][1] if texts else "")
            field = "failure_reason" if reason else ("error" if texts else "status")
            return _hit(rule, field, blob or mapped_status)
    return None


def recovery_at_from_text(failure_class: str | None, error: str | None, failure_reason: str | None) -> str | None:
    if failure_class != "quota":
        return None
    blob = "\n".join(text for _field, text in _texts(error, failure_reason))
    matched = _RECOVERY_AT.search(blob)
    if not matched:
        return None
    return matched.group(1)


def public_rules() -> list[dict]:
    return [
        {
            "id": rule.id,
            "rule_version": RULE_VERSION,
            "failure_class": rule.failure_class,
            "condition": rule.condition,
            "recoverable": rule.recoverable,
        }
        for rule in RULES
    ]
