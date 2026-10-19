"""Independent G1 read-only planner. Never consumes the Board SQLite cache."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from .cli_runner import CliError, CliRunner
from .plan_contract import (FIELDS, G1, canonical, counts, decide, envelope,
                            exact, server_identity, strict_json, text, utc, uuid)

DEFAULT_PLAN_DIR = Path.home() / ".multica-board" / "plans"
MAX_BYTES = 2 * 1024 * 1024


def operation(value):
    if value == "omit":
        return ("omit", None)
    if value == "clear":
        return ("clear", None)
    if value.startswith("set:") and value[4:] and value[4:] == value[4:].strip():
        text(value[4:])
        return ("set", value[4:])
    raise ValueError("operation must be omit, clear, or set:<nonblank trimmed text>")


def selection(workspaces, agents):
    ws = [uuid(w) for w in workspaces]
    pairs = []
    for spec in agents:
        parts = spec.split(":")
        if len(parts) != 2:
            raise ValueError("agent must be WORKSPACE_UUID:AGENT_UUID")
        pairs.append(tuple(uuid(p) for p in parts))
    if len(ws) != 1:
        raise ValueError("schema 1 requires exactly one workspace")
    if not 1 <= len(pairs) <= 1000 or len(set(pairs)) != len(pairs):
        raise ValueError("explicit nonduplicate allow-list and 1..1000 unique agents required")
    if any(w not in ws for w, _ in pairs):
        raise ValueError("selected agent outside workspace allow-list")
    return sorted(ws), sorted(pairs)


def iso(t):
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def source_time(value):
    """CLI RFC3339 timestamps become the schema's UTC-second display timestamps."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return iso(parsed) if parsed.tzinfo else None
    except ValueError:
        return None


def rows_by(items, key):
    if not isinstance(items, list):
        raise ValueError("expected resource array")
    out = {}
    for item in items:
        k = tuple(item[x] for x in key)
        if k in out:
            raise ValueError("duplicate resource identity")
        out[k] = item
    return out


def read_live(runner, origin, workspaces, selected, *, now=None):
    """One bounded task page: no supported snapshot/exhaustion contract => UNKNOWN.

    Project only allow-listed fields before returning. Failures do not mean idle.
    """
    snapshot = {"server_identity": origin, "observed_at": iso(now or datetime.now(timezone.utc)),
                "workspaces": [], "agents": [], "runtimes": [], "tasks": []}
    def read(args, ws=None):
        try:
            result = runner.run(args, workspace_id=ws).data
            return result if isinstance(result, list) and all(isinstance(r, dict) for r in result) else []
        except CliError:
            return []
    visible = read(["workspace", "list"])
    for ws in workspaces:
        matches = [w for w in visible if w.get("id") == ws]
        snapshot["workspaces"].append({"id": ws, "name": matches[0].get("name") if len(matches) == 1 else None})
        agents = read(["agent", "list"], ws)
        runtimes = read(["runtime", "list"], ws)
        # If a response includes another workspace, do not use any of that batch.
        if any(a.get("workspace_id") != ws for a in agents):
            agents = []
        if any(r.get("workspace_id") != ws for r in runtimes):
            runtimes = []
        for runtime in runtimes:
            minimal = {k: runtime[k] for k in ("workspace_id", "id", "name", "provider", "status") if k in runtime}
            minimal["last_seen_at"] = source_time(runtime.get("last_seen_at"))
            snapshot["runtimes"].append(minimal)
        for w, agent_id in selected:
            if w != ws:
                continue
            found = [a for a in agents if a.get("id") == agent_id]
            if len(found) == 1:
                raw = found[0]
                row = {k: raw[k] for k in ("workspace_id", "id", "name", *FIELDS) if k in raw}
                if type(raw.get("archived")) is bool:
                    row["archived"] = raw["archived"]
                elif "archived_at" in raw and raw["archived_at"] is None:
                    row["archived"] = False
                elif isinstance(raw.get("archived_at"), str) and source_time(raw["archived_at"]):
                    row["archived"] = True
                else:
                    row["archived"] = None
                snapshot["agents"].append(row)
            try:
                response = runner.run(["agent", "tasks", agent_id, "--limit", "200"], workspace_id=ws)
                data = response.data
                # Unknown envelope/scope is not accepted as an empty successful page.
                valid = isinstance(data, list) and len(data) <= 200 and all(
                    isinstance(t, dict) and t.get("workspace_id") == ws and t.get("agent_id") == agent_id for t in data)
                runs = [{k: t[k] for k in ("id", "status", "created_at") if k in t} for t in data] if valid else []
                for run in runs:
                    run["created_at"] = source_time(run.get("created_at"))
                pages = 1 if valid else 0
            except CliError:
                pages, runs = 0, []
            snapshot["tasks"].append({"workspace_id": ws, "agent_id": agent_id, "pages_read": pages, "runs": runs})
    return snapshot


def build_plan(snapshot, *, origin, actor, on_behalf_of, workspaces, selected, target,
               operations, snapshot_kind="historical", ttl=600, now=None, plan_id=None):
    now = (now or datetime.now(timezone.utc)).replace(microsecond=0)
    if type(ttl) is not int or not 1 <= ttl <= 600:
        raise ValueError("TTL must be 1..600 seconds")
    origin = server_identity(origin)
    uuid(target)
    exact(operations, ("model", "thinking_level", "service_tier"))
    ops = {f: operation(v) for f, v in operations.items()}
    if snapshot.get("server_identity") != origin:
        raise ValueError("snapshot server identity mismatch")
    observed = snapshot["observed_at"]
    utc(observed)
    if utc(observed) > now:
        raise ValueError("snapshot observation is in the future")
    ws_map = rows_by(snapshot["workspaces"], ("id",))
    agents = rows_by(snapshot["agents"], ("workspace_id", "id"))
    runtimes = rows_by(snapshot["runtimes"], ("workspace_id", "id"))
    tasks = rows_by(snapshot["tasks"], ("workspace_id", "agent_id"))
    rows = []
    for ws, agent_id in selected:
        a = agents.get((ws, agent_id), {})
        fields = {}
        for f in FIELDS:
            old = a.get(f)
            op, value = ("set", target) if f == "runtime_id" else ops[f]
            fields[f] = {"op": op, "old": old, "new": old if op == "omit" else value,
                         "old_known": f in a}
        task = tasks.get((ws, agent_id), {})
        unique = {}
        for run in task.get("runs", []):
            rid = uuid(run["id"])
            minimal = {"status": run["status"], "created_at": run.get("created_at")}
            if rid in unique and unique[rid] != minimal:
                raise ValueError("conflicting duplicate task")
            unique[rid] = minimal
        runs = list(unique.values())
        checks = {k: {"status": "UNKNOWN", "source": "not_proven_by_supported_read_contract",
                      "observed_at": observed} for k in G1}
        runtime = runtimes.get((ws, target), {})
        def runtime_summary(runtime_id):
            r = runtimes.get((ws, runtime_id))
            if not r:
                return None
            return {k: r.get(k) for k in ("id", "name", "provider", "status", "last_seen_at")}
        health = checks["health"]
        if runtime.get("status") == "offline":
            health["status"] = "FAIL"
        elif runtime.get("status") == "online" and runtime.get("last_seen_at"):
            try:
                age = (now - utc(runtime["last_seen_at"])).total_seconds()
                health["status"] = "PASS" if 0 <= age <= 60 else "FAIL"
            except ValueError:
                pass
        health["source"] = "runtime_list_status_and_heartbeat"
        if snapshot_kind == "historical":
            health.update(status="UNKNOWN", source="historical_snapshot")
        if not runtime:
            checks["capability"]["source"] = "target_registration_not_confirmed"
        row = {"workspace_id": ws, "agent_id": agent_id, "agent_name": a.get("name"),
               "archived": a.get("archived"), "fields": fields, "source_version": None,
               "source_fingerprint": hashlib.sha256(canonical({f: {"value": d["old"], "known": d["old_known"]}
                                                               for f, d in fields.items()})).hexdigest(),
               "observed_at": observed, "source_runtime": runtime_summary(a.get("runtime_id")),
               "target_runtime": runtime_summary(target),
               "activity": {"pages_read": task.get("pages_read", 0), "tasks_seen": len(runs),
                            "completeness": "UNKNOWN", "source": "unproven_cli_snapshot_pagination",
                            "observed_at": observed, "runs": runs},
               "checks": checks, "decision": None, "reasons": [],
               "impact": ["Session/workdir continuity is unverified; no migration or rerun is performed.",
                          "Source fingerprint is not CAS. Plan hash is not approval. Apply is disabled."]}
        row["decision"], row["reasons"] = decide(row, snapshot_kind)
        rows.append(row)
    payload = {"schema_version": 1, "plan_id": plan_id or str(uuid4()), "mode": "review_only",
               "server_identity": origin, "actor": actor, "on_behalf_of": on_behalf_of,
               "workspace_allow_list": [{"id": w, "name": ws_map.get((w,), {}).get("name")} for w in workspaces],
               "selected_agents": [{"workspace_id": w, "agent_id": a} for w, a in selected],
               "generated_at": iso(now), "expires_at": iso(now + timedelta(seconds=ttl)),
               "policy_version": "g1-v1", "snapshot_kind": snapshot_kind,
               "selection_reason": "Explicit UUID selection; no filter-derived scope.",
               "allowed_fields": list(FIELDS), "rows": rows, "counts": counts(rows)}
    return envelope(payload)


def main(argv=None):
    p = argparse.ArgumentParser(description="G1 read-only runtime plan; never applies changes")
    p.add_argument("--server", required=True)
    p.add_argument("--actor", required=True, help="member:UUID or agent:UUID (unverified declaration)")
    p.add_argument("--on-behalf-of", required=True)
    p.add_argument("--workspace-allow", action="append", required=True)
    p.add_argument("--agent", action="append", required=True, help="WORKSPACE_UUID:AGENT_UUID")
    p.add_argument("--target-runtime", required=True)
    for f in ("model", "effort", "tier"):
        p.add_argument("--" + f, default="omit")
    p.add_argument("--snapshot", type=Path)
    p.add_argument("--plan-dir", type=Path, default=DEFAULT_PLAN_DIR)
    p.add_argument("--ttl-seconds", type=int, default=600)
    a = p.parse_args(argv)
    try:
        ws, selected = selection(a.workspace_allow, a.agent)
        origin = server_identity(a.server)
        actor_type, actor_id = a.actor.split(":")
        if actor_type not in {"member", "agent"}:
            raise ValueError("invalid actor type")
        actor = {"type": actor_type, "id": uuid(actor_id), "verified": False}
        uuid(a.on_behalf_of)
        uuid(a.target_runtime)
        operations = {"model": a.model, "thinking_level": a.effort, "service_tier": a.tier}
        for v in operations.values():
            operation(v)
        if not 1 <= a.ttl_seconds <= 600:
            raise ValueError("TTL must be 1..600 seconds")
        if a.snapshot:
            with a.snapshot.open("rb") as stream:
                raw = stream.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError("snapshot exceeds 2 MiB")
            snapshot = strict_json(raw)
        else:
            if "MULTICA_TOKEN" in os.environ or any(k.startswith("MULTICA_TASK_") for k in os.environ):
                raise ValueError("live planner must run in the user's independently authorized terminal")
            # Normal human profile only; task credentials are stripped by CliRunner.
            snapshot = read_live(CliRunner(profile="desktop-api.multica.ai", server_url=origin), origin, ws, selected)
        plan = build_plan(snapshot, origin=origin, actor=actor, on_behalf_of=a.on_behalf_of,
                          workspaces=ws, selected=selected, target=a.target_runtime, operations=operations,
                          snapshot_kind="historical" if a.snapshot else "live_cli", ttl=a.ttl_seconds)
        from .plans import write_plan
        write_plan(a.plan_dir, plan)
        print(json.dumps({"plan_id": plan["payload"]["plan_id"], "plan_hash": plan["plan_hash"],
                          "mode": "review_only", "counts": plan["payload"]["counts"], "apply_enabled": False}))
        return 0
    except (ValueError, KeyError, TypeError, OSError):
        # No raw CLI, paths, supplied model text or credentials in errors.
        print("plan rejected: invalid scope, snapshot, operation, or private plan directory", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
