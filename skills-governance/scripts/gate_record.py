"""Latest completed skill_guard gate, written by the CLI and read by Board.

The fallback verdict covers local risk signals only, not SkilLock approval.
Readers never execute a gate or create files.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PATH = Path.home() / ".local/share/agents-lab/skill-staging/last-gate.json"
SCHEMA = "skill-governance-last-gate/v1"


def save(path: Path, report: dict) -> dict:
    path = path.expanduser().absolute()
    gate = report.get("capability_gate") or {}
    record = {
        "schema": SCHEMA,
        "state": "fallback" if gate.get("backend") == "fallback" else "recorded",
        "time": datetime.now(timezone.utc).isoformat(),
        "result": "block" if report["blocked"] else "pass",
        "source": "skil-lock" if gate.get("backend") == "skil-lock" else "skill_guard",
        "summary_json_path": str(path),
        "reason": gate.get("reason") or gate.get("summary") or "",
        "signals": report.get("signals") or [],
        "snapshot": report.get("snapshot"),
        "staging": report.get("staging"),
        "capability_gate": gate,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    # Unique temporary file plus replace: concurrent readers see whole records.
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=".last-gate-", delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(record, handle, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return record


def read(path: Path) -> dict:
    empty = {"state": "empty", "time": None, "result": None, "source": None,
             "summary_json_path": str(path), "reason": "no_gate_record"}
    try:
        with path.open("rb") as handle:
            raw = handle.read(1_048_577)
        if len(raw) > 1_048_576:
            raise ValueError("oversized gate record")
        record = json.loads(raw)
        if not isinstance(record, dict) or record.get("schema") != SCHEMA:
            raise ValueError("invalid gate schema")
        if (record.get("state") not in {"fallback", "recorded"}
                or record.get("result") not in {"pass", "block"}
                or record.get("source") not in {"skill_guard", "skil-lock"}
                or not isinstance(record.get("time"), str)):
            raise ValueError("invalid gate record")
        stamp = datetime.fromisoformat(record["time"].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError("gate time needs a timezone")
        # Report the file actually read, never a path claimed by its content.
        return {**record, "summary_json_path": str(path)}
    except FileNotFoundError:
        return empty
    except (OSError, ValueError, UnicodeError, TypeError):
        return {**empty, "state": "error", "reason": "gate_record_unreadable"}
