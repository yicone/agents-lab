#!/usr/bin/env python3
"""Plan or apply retention-based cleanup of host review artifacts."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import tempfile
from typing import Any


SCHEMA = "devin-cleanup/v1"


def load_json(path: pathlib.Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def collect_refs(value: Any, refs: set[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "evidence_ref" and isinstance(child, str) and child:
                refs.add(child)
            collect_refs(child, refs)
    elif isinstance(value, list):
        for child in value:
            collect_refs(child, refs)


def parse_time(value: Any) -> dt.datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def old(path: pathlib.Path, cutoff: float) -> bool:
    try:
        return path.stat().st_mtime < cutoff
    except OSError:
        return False


def plan_cleanup(evidence_dir: pathlib.Path, queue_dir: pathlib.Path,
                 auth_path: pathlib.Path, ledger_path: pathlib.Path,
                 retention_days: int, expire_unused_authorizations: bool,
                 now: dt.datetime | None = None) -> dict[str, Any]:
    current = now or dt.datetime.now(dt.timezone.utc)
    cutoff = (current - dt.timedelta(days=retention_days)).timestamp()
    protected_refs: set[str] = set()
    collect_refs(load_json(ledger_path), protected_refs)
    for response in queue_dir.glob("*.response.json"):
        collect_refs(load_json(response), protected_refs)

    evidence_candidates: list[str] = []
    evidence_protected: list[str] = []
    for path in sorted(evidence_dir.glob("*.json")):
        if not old(path, cutoff):
            evidence_protected.append(str(path))
        elif path.stem in protected_refs:
            evidence_protected.append(str(path))
        else:
            evidence_candidates.append(str(path))

    response_candidates: list[str] = []
    response_protected: list[str] = []
    for path in sorted(queue_dir.glob("*.response.json")):
        request = path.with_name(path.name.replace(".response.json", ".request.json"))
        if not old(path, cutoff) or request.exists():
            response_protected.append(str(path))
        else:
            response_candidates.append(str(path))
    for path in sorted(queue_dir.glob(".*.response.json.*")):
        if old(path, cutoff):
            response_candidates.append(str(path))
        else:
            response_protected.append(str(path))

    auth_data = load_json(auth_path)
    auth_data = auth_data if isinstance(auth_data, dict) else {}
    auth_candidates: list[str] = []
    auth_protected: list[str] = []
    for key, record in auth_data.items():
        issued = parse_time(record.get("issued_at")) if isinstance(record, dict) else None
        is_expired = issued is not None and issued.timestamp() < cutoff
        unused = isinstance(record, dict) and not record.get("used", False)
        if is_expired and (not unused or expire_unused_authorizations):
            auth_candidates.append(key)
        else:
            auth_protected.append(key)

    return {
        "schema": SCHEMA,
        "retention_days": retention_days,
        "cutoff": dt.datetime.fromtimestamp(cutoff, dt.timezone.utc).isoformat(),
        "candidates": {
            "evidence": evidence_candidates,
            "queue_artifacts": response_candidates,
            "authorization_ids": auth_candidates,
        },
        "protected": {
            "evidence": evidence_protected,
            "queue_artifacts": response_protected,
            "authorization_ids": auth_protected,
            "referenced_evidence": sorted(protected_refs),
        },
        "paths": {
            "evidence_dir": str(evidence_dir),
            "queue_dir": str(queue_dir),
            "authorizations": str(auth_path),
            "ledger": str(ledger_path),
        },
    }


def write_atomic(path: pathlib.Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def apply_plan(plan: dict[str, Any], auth_path: pathlib.Path) -> dict[str, Any]:
    deleted = {"evidence": [], "queue_artifacts": [], "authorization_ids": []}
    for category in ("evidence", "queue_artifacts"):
        for raw in plan["candidates"][category]:
            path = pathlib.Path(raw)
            try:
                path.unlink()
                deleted[category].append(raw)
            except FileNotFoundError:
                pass
    if plan["candidates"]["authorization_ids"]:
        data = load_json(auth_path)
        if isinstance(data, dict):
            for key in plan["candidates"]["authorization_ids"]:
                if key in data:
                    del data[key]
                    deleted["authorization_ids"].append(key)
            write_atomic(auth_path, data)
    return deleted


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir", default="~/.local/share/devin/host-provider/evidence")
    parser.add_argument("--queue", default="/private/tmp/devin-host-review")
    parser.add_argument("--authorizations", default="~/.config/devin/round-authorizations.json")
    parser.add_argument("--ledger", default="~/.local/share/devin/host-provider/ledger.json")
    parser.add_argument("--audit-dir", default="~/.local/share/devin/host-provider/cleanup-audit")
    parser.add_argument("--retention-days", type=int, default=30)
    parser.add_argument("--expire-unused-authorizations", action="store_true")
    parser.add_argument("--apply", action="store_true", help="delete the planned expired artifacts")
    args = parser.parse_args()
    if args.retention_days < 1:
        parser.error("--retention-days must be positive")
    evidence_dir = pathlib.Path(args.evidence_dir).expanduser()
    queue_dir = pathlib.Path(args.queue).expanduser()
    auth_path = pathlib.Path(args.authorizations).expanduser()
    ledger_path = pathlib.Path(args.ledger).expanduser()
    plan = plan_cleanup(evidence_dir, queue_dir, auth_path, ledger_path, args.retention_days, args.expire_unused_authorizations)
    audit_dir = pathlib.Path(args.audit_dir).expanduser()
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    audit_path = audit_dir / f"cleanup-{stamp}.json"
    audit = {"schema": SCHEMA, "mode": "apply" if args.apply else "dry-run", "planned_at": dt.datetime.now(dt.timezone.utc).isoformat(), "plan": plan}
    write_atomic(audit_path, audit)
    if args.apply:
        audit["deleted"] = apply_plan(plan, auth_path)
        audit["applied_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
        write_atomic(audit_path, audit)
    print(json.dumps({"schema": SCHEMA, "status": "applied" if args.apply else "dry-run", "audit_path": str(audit_path), "candidates": plan["candidates"], "protected": plan["protected"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
