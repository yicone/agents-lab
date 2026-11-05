"""Read-only three-way instruction reconciliation for ``GET /api/instruction-diff``.

Compares the Git truth (``instructions-governance``), the deployed board export
(the ``instruction-sources.json`` this server hands out), and the live caches
(board-collected agents, the local Grok roster). In-process ``matrix()`` reuse;
the endpoint never spawns processes, opens sockets, or writes files.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[3]
INSTRUCTION_SCRIPTS = REPO_ROOT / "instructions-governance" / "scripts"
if str(INSTRUCTION_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(INSTRUCTION_SCRIPTS))

DEFAULT_INSTRUCTIONS_ROOT = REPO_ROOT / "instructions-governance"
MAX_AGE_SECONDS = 300

_DRIFTS = ("aligned", "modified", "stale", "split", "withheld")
_DRIFT_RANK = {"split": 0, "modified": 1, "stale": 2, "withheld": 3}
_MOUNT_HINT = "需随看板一起检出或单独挂载（只读），由用户另批；本接口不会改写部署目录。"


class GitSourceUnreadable(Exception):
    """The Git truth root or a file its index names cannot be read."""


def _iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def _display(root: Path, rel: str) -> str:
    """Repo-relative when possible; otherwise the canonical layout prefix."""
    try:
        return str((root / rel).resolve().relative_to(REPO_ROOT))
    except (OSError, ValueError):
        return f"instructions-governance/{rel}"


def _check_git_source(root: Path) -> dict[str, Any]:
    """Pre-flight the Git truth before matrix(); index.json itself is reused for names."""
    try:
        is_dir = root.is_dir()
    except OSError:
        is_dir = False
    if not is_dir:
        raise GitSourceUnreadable(f"部署目录缺少 instructions-governance/，{_MOUNT_HINT}")
    index_rel = "sources/index.json"
    try:
        index = json.loads((root / index_rel).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        index = None
    if not isinstance(index, dict):
        raise GitSourceUnreadable(f"Git 真源索引缺失或不可读：{_display(root, index_rel)}，{_MOUNT_HINT}")
    for kind in ("agents", "bots"):
        entries = index.get(kind)
        if entries is not None and not isinstance(entries, list):
            raise GitSourceUnreadable(f"Git 真源索引缺失或不可读：{_display(root, index_rel)}，{_MOUNT_HINT}")
        for item in entries or []:
            if not isinstance(item, dict):
                continue
            rel = item.get("path")
            if not isinstance(rel, str) or not rel or item.get("withheld"):
                continue
            try:
                ok = (root / "sources" / rel).is_file()
            except OSError:
                ok = False
            if not ok:
                raise GitSourceUnreadable(f"Git 真源文件缺失：{_display(root, f'sources/{rel}')}，{_MOUNT_HINT}")
    return index


def _load_board(board_path: Path | None, now: datetime, max_age_seconds: int) -> tuple[dict[str, Any], str, bool]:
    """Board export plus mtime freshness, same validation as query_instructions.main()."""
    board: Any = None
    stamp: datetime | None = None
    if board_path is not None:
        try:
            board = json.loads(board_path.read_text(encoding="utf-8"))
            stamp = datetime.fromtimestamp(board_path.stat().st_mtime, timezone.utc)
        except (OSError, ValueError):
            board = None
    if (not isinstance(board, dict) or board.get("schema") != "instruction-sources/v1"
            or any(not isinstance(board.get(k), dict) for k in ("agents", "bots"))):
        return {}, "stale", True
    age = (now - stamp).total_seconds()
    return board, "fresh" if 0 <= age <= max_age_seconds else "stale", False


def _fresh(observed_at: datetime | None, now: datetime, max_age_seconds: int) -> str:
    if observed_at is None:
        return "stale"
    age = (now - observed_at).total_seconds()
    return "fresh" if 0 <= age <= max_age_seconds else "stale"


def _git_names(index: dict[str, Any]) -> dict[tuple[Any, ...], Any]:
    names: dict[tuple[Any, ...], Any] = {}
    for item in index.get("agents") or []:
        if isinstance(item, dict):
            names[("agent", item.get("workspace_id"), item.get("id"))] = item.get("name")
    for item in index.get("bots") or []:
        if isinstance(item, dict):
            names[("bot", None, item.get("id"))] = item.get("name")
    return names


def _live_names(agents: list[dict[str, Any]], bots: list[dict[str, Any]]) -> dict[tuple[Any, ...], Any]:
    names: dict[tuple[Any, ...], Any] = {}
    for row in agents:
        names[("agent", row.get("workspace_id"), row.get("id"))] = row.get("name")
    for row in bots:
        names[("bot", None, row.get("id"))] = row.get("name")
    return names


def build_instruction_diff(*, root: Path, board_path: Path | None, agents: list[dict[str, Any]],
                           agents_observed_at: datetime | None, bots: list[dict[str, Any]] | None,
                           bots_observed_at: datetime | None, bot_error: bool, now: datetime,
                           max_age_seconds: int = MAX_AGE_SECONDS) -> dict[str, Any]:
    """Pure read of ``root`` and ``board_path``; raises GitSourceUnreadable, never degrades."""
    index = _check_git_source(root)
    source_errors: dict[str, str] = {}
    board, board_freshness, board_error = _load_board(board_path, now, max_age_seconds)
    if board_error:
        source_errors["board"] = "unavailable_or_invalid"
    live_bots = list(bots or [])
    if bot_error:
        source_errors["bot"] = "unavailable_or_invalid"
    if not agents and agents_observed_at is None:
        source_errors["agent"] = "unavailable_or_invalid"
    freshness = {
        "board": board_freshness,
        "agent": _fresh(agents_observed_at, now, max_age_seconds),
        "bot": _fresh(bots_observed_at, now, max_age_seconds),
    }
    observed = {"agent": _iso(agents_observed_at), "bot": _iso(bots_observed_at)}
    # Redacted bodies must not feed the hash fallback; only the pre-redaction
    # digests are trustworthy.
    live_agents = [{"workspace_id": a.get("workspace_id"), "id": a.get("id"),
                    "name": a.get("name"), "instructions_sha256": a.get("instructions_sha256")}
                   for a in agents]
    live_bots = [{"id": b.get("id"), "name": b.get("name"),
                  "description_sha256": b.get("description_sha256")} for b in live_bots]
    try:
        import query_instructions  # noqa: E402  (sys.path prepared at module top)

        report = query_instructions.matrix(root, board, live_agents, live_bots,
                                           observed_at=observed, freshness=freshness)
    except ImportError as exc:
        raise GitSourceUnreadable(
            f"对账脚本缺失：instructions-governance/scripts/query_instructions.py，{_MOUNT_HINT}"
        ) from exc
    except (OSError, ValueError, SystemExit) as exc:
        raise GitSourceUnreadable(
            f"Git 真源读取失败：instructions-governance/（{type(exc).__name__}），{_MOUNT_HINT}"
        ) from exc
    git_names = _git_names(index)
    live_names = _live_names(live_agents, live_bots)
    items = []
    for row in report["items"]:
        if row["drift"] == "aligned":
            continue
        key = (row.get("kind"), row.get("workspace_id"), row.get("id"))
        items.append({**row, "name": live_names.get(key) or git_names.get(key) or row.get("id")})
    items.sort(key=lambda r: (_DRIFT_RANK.get(r["drift"], 9), 0 if r["kind"] == "agent" else 1,
                              str(r.get("name") or ""), str(r.get("id") or "")))
    counts = report["summary"]
    summary = {d: counts.get(d, 0) for d in _DRIFTS}
    return {
        "schema": "instruction-diff/v1",
        "generated_at": _iso(now),
        "summary": {"total": sum(summary.values()), **summary},
        "freshness": freshness,
        "live_sources": {"agent": "board-cache", "bot": "local-roster-cache"},
        "source_errors": source_errors,
        "inaccessible_workspaces": [],
        "items": items,
    }
