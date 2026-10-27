"""Per-account Codex window snapshots from CodexBar's on-disk cache.

Reads ~/Library/Application Support/CodexBar/codex-account-snapshots.json only.
Never invokes `codexbar usage` / promote / OAuth refresh.
Emails become a 2-letter alias; tokens and full emails are not persisted.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("multica_board.usage")

APPLE_EPOCH_UNIX = 978307200  # 2001-01-01 UTC
DEFAULT_SNAPSHOTS = Path.home() / "Library/Application Support/CodexBar/codex-account-snapshots.json"
HISTORY_NAME = "codex-accounts-history.jsonl"
DEFAULT_RETENTION_DAYS = 90

_WINDOW_LABELS = {
    300: "5h",
    10080: "weekly",
    43200: "monthly",
}


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _scrub(obj: Any) -> Any:
    """Drop obvious secret-looking keys; keep structure otherwise."""
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            lk = str(key).lower()
            if any(s in lk for s in ("token", "cookie", "secret", "password", "authorization", "authfingerprint")):
                continue
            if "email" in lk and isinstance(value, str) and "@" in value:
                continue
            out[key] = _scrub(value)
        return out
    if isinstance(obj, list):
        return [_scrub(item) for item in obj]
    return obj


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _alias(email: str | None) -> str | None:
    if not isinstance(email, str) or not email.strip():
        return None
    local = email.strip().split("@", 1)[0]
    return local[:2] if local else None


def _apple_to_iso(value: Any) -> str | None:
    """Convert Apple CFAbsoluteTime (or already-ISO string) to UTC ISO-8601."""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if "T" in text or text.endswith("Z"):
            return text
        try:
            value = float(text)
        except ValueError:
            return text
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    # Apple absolute times for current years are ~8e8; Unix is ~1.7e9.
    unix = num + APPLE_EPOCH_UNIX if num < 2_000_000_000 else num
    return datetime.fromtimestamp(unix, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _window_entry(raw: dict[str, Any] | None, fallback_key: str) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    used = _num(raw.get("usedPercent"))
    if used is None:
        return None
    minutes = _num(raw.get("windowMinutes"))
    label = _WINDOW_LABELS.get(int(minutes)) if minutes is not None else None
    return {
        "key": fallback_key,
        "label": label or fallback_key,
        "window_minutes": minutes,
        "used_percent": used,
        "resets_at": _apple_to_iso(raw.get("resetsAt")),
        "reset_description": _text(raw.get("resetDescription")),
    }


def _reset_cards(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        return {"available_count": 0, "soonest_expires_at": None, "cards": []}
    cards_out: list[dict[str, Any]] = []
    soonest: str | None = None
    for item in raw.get("credits") or []:
        if not isinstance(item, dict):
            continue
        status = _text(item.get("status")) or "unknown"
        expires = _apple_to_iso(item.get("expires_at"))
        cards_out.append({
            "status": status,
            "reset_type": _text(item.get("reset_type")),
            "title": _text(item.get("title")),
            "expires_at": expires,
            "granted_at": _apple_to_iso(item.get("granted_at")),
        })
        if status == "available" and expires and (soonest is None or expires < soonest):
            soonest = expires
    count = raw.get("availableCount")
    try:
        available_count = int(count) if count is not None else sum(
            1 for c in cards_out if c.get("status") == "available"
        )
    except (TypeError, ValueError):
        available_count = sum(1 for c in cards_out if c.get("status") == "available")
    return {
        "available_count": available_count,
        "soonest_expires_at": soonest,
        "cards": cards_out,
    }


def _is_system(identity: dict[str, Any] | None) -> bool:
    if not isinstance(identity, dict):
        return False
    src = identity.get("selectionSource")
    return isinstance(src, dict) and src.get("kind") == "liveSystem"


def _account_key(identity: dict[str, Any] | None, record_id: Any) -> str | None:
    if isinstance(identity, dict):
        sid = identity.get("storedAccountID")
        if isinstance(sid, str) and sid.strip():
            return sid.strip()[:8]
    if isinstance(record_id, str) and record_id.strip():
        return "e:" + hashlib.sha256(record_id.strip().lower().encode()).hexdigest()[:8]
    return None


def normalize_record(
    rec: dict[str, Any],
    *,
    sampled_at: datetime,
    source_mtime: datetime | None,
) -> dict[str, Any] | None:
    snap = rec.get("snapshot") if isinstance(rec.get("snapshot"), dict) else {}
    identity = rec.get("accountIdentity") if isinstance(rec.get("accountIdentity"), dict) else {}
    email = _text(snap.get("accountEmail")) or _text(identity.get("normalizedEmail"))
    alias = _alias(email)
    if not alias:
        return None

    plan = _text(snap.get("loginMethod"))
    ident = snap.get("identity") if isinstance(snap.get("identity"), dict) else None
    if not plan and ident:
        plan = _text(ident.get("loginMethod"))

    windows: list[dict[str, Any]] = []
    for key in ("primary", "secondary", "tertiary"):
        entry = _window_entry(snap.get(key) if isinstance(snap.get(key), dict) else None, key)
        if entry:
            windows.append(entry)

    credits_raw = rec.get("credits") if isinstance(rec.get("credits"), dict) else {}
    credits_remaining = _num(credits_raw.get("remaining"))
    if credits_remaining is None:
        cost = snap.get("providerCost") if isinstance(snap.get("providerCost"), dict) else {}
        credits_remaining = _num(cost.get("balance"))

    source_updated = _apple_to_iso(snap.get("updatedAt"))
    if not source_updated and source_mtime is not None:
        source_updated = source_mtime.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    return _scrub({
        "alias": alias,
        "plan": plan,
        "is_system_account": _is_system(identity),
        "account_key": _account_key(identity, rec.get("id")),
        "data_confidence": _text(snap.get("dataConfidence")),
        "windows": windows,
        "credits_remaining": credits_remaining,
        "reset_cards": _reset_cards(snap.get("codexResetCredits")),
        "source_updated_at": source_updated,
        "sampled_at": sampled_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "codexbar-disk",
        "source_path": "CodexBar/codex-account-snapshots.json",
    })


def load_codex_account_records(
    snapshots_path: Path | None = None,
    *,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    path = Path(snapshots_path) if snapshots_path else Path(
        os.environ.get("CODEXBAR_ACCOUNT_SNAPSHOTS", str(DEFAULT_SNAPSHOTS))
    )
    meta: dict[str, Any] = {
        "status": "error",
        "source": "codexbar-disk",
        "path": str(path),
        "error": None,
        "account_count": 0,
        "file_mtime": None,
    }
    if not path.is_file():
        meta["error"] = "snapshots file missing"
        return [], meta
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        meta["file_mtime"] = mtime.strftime("%Y-%m-%dT%H:%M:%SZ")
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        meta["error"] = f"{type(exc).__name__}: read/parse failed"
        log.warning("codex account snapshots read failed: %s", type(exc).__name__)
        return [], meta

    records = payload.get("records") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        meta["error"] = "snapshots payload missing records[]"
        return [], meta

    accounts: list[dict[str, Any]] = []
    for rec in records:
        if not isinstance(rec, dict):
            continue
        norm = normalize_record(rec, sampled_at=now, source_mtime=mtime)
        if norm:
            accounts.append(norm)

    accounts.sort(key=lambda a: a.get("alias") or "")
    meta["status"] = "ok" if accounts else "empty"
    meta["account_count"] = len(accounts)
    meta["error"] = None if accounts else "no usable account records"
    return accounts, meta


def write_accounts_history(
    path: Path,
    accounts: list[dict[str, Any]],
    meta: dict[str, Any],
    *,
    now: datetime,
    retention_days: int = DEFAULT_RETENTION_DAYS,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = _scrub({
        "at": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "codexbar-disk",
        "meta": {k: meta.get(k) for k in ("status", "account_count", "file_mtime", "error")},
        "accounts": accounts,
    })
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass

    cutoff = now.astimezone(timezone.utc) - timedelta(days=max(1, retention_days))
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    kept: list[str] = []
    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            obj = json.loads(raw)
            at = obj.get("at")
            if isinstance(at, str):
                ts = datetime.fromisoformat(at.replace("Z", "+00:00"))
                if ts < cutoff:
                    continue
        except Exception:
            pass
        kept.append(raw)
    if len(kept) != len([ln for ln in lines if ln.strip()]):
        _atomic_write(path, ("\n".join(kept) + ("\n" if kept else "")).encode("utf-8"))
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def collect_codex_accounts(
    *,
    data_dir: Path,
    snapshots_path: Path | None = None,
    now: datetime | None = None,
    retention_days: int = DEFAULT_RETENTION_DAYS,
) -> dict[str, Any]:
    """Read disk snapshots, append history, return payload for usage-snapshot.json."""
    now = now or datetime.now(timezone.utc)
    accounts, meta = load_codex_account_records(snapshots_path, now=now)
    hist = Path(data_dir) / HISTORY_NAME
    write_accounts_history(hist, accounts, meta, now=now, retention_days=retention_days)
    return _scrub({
        "status": meta["status"],
        "source": "codexbar-disk",
        "sampled_at": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "file_mtime": meta.get("file_mtime"),
        "account_count": meta.get("account_count"),
        "error": meta.get("error"),
        "accounts": accounts,
    })
