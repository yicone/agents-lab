"""UTC parsing and window bounds for run-metrics.v0.1."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

WINDOWS = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "14d": timedelta(days=14),
}
DEFAULT_WINDOW = "14d"


def parse_ts(value: str | None) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def in_window(value: datetime | None, start: datetime, end: datetime) -> bool:
    return value is not None and start <= value <= end


def window_bounds(now: datetime, name: str) -> tuple[datetime, datetime]:
    if name not in WINDOWS:
        raise KeyError(name)
    end = now.astimezone(timezone.utc)
    return end - WINDOWS[name], end
